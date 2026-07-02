# SPDX-License-Identifier: Apache-2.0
#
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# Hotpatch: adds TTWan22AnimateRunner + SkyReels log-map entries, Mochi
# TT_DIT_CACHE_DIR env, Flux trace_region_size from settings (not hardcoded),
# and Motif P300X2 fixes (traced=False inference, (2,2) config added upstream).

import asyncio
import base64
import io
import os
from abc import abstractmethod

import ttnn
from config.constants import (
    WAN22_NUM_FRAMES,
    ModelRunners,
    ModelServices,
    SupportedModels,
    is_large_mesh,
    wan22_target_resolution,
)
from config.settings import get_settings
from domain.image_generate_request import ImageGenerateRequest
from domain.video_generate_request import VideoGenerateRequest
try:
    from domain.video_i2v_generate_request import ImagePromptEntry, VideoI2VGenerateRequest
except ImportError:
    # 0.9.0 image (Motif) does not ship this module — I2V runners won't work
    # but image runners (FLUX, Motif, Z-Image-Turbo) are unaffected.
    ImagePromptEntry = None  # type: ignore[assignment,misc]
    VideoI2VGenerateRequest = None  # type: ignore[assignment,misc]
from models.common.utility_functions import is_blackhole
try:
    # 0.17.0 image: models.tt_dit (promoted out of experimental)
    from models.tt_dit.pipelines.flux1.pipeline_flux1 import Flux1Pipeline
    from models.tt_dit.pipelines.mochi.pipeline_mochi import MochiPipeline
    from models.tt_dit.pipelines.motif.pipeline_motif import MotifPipeline
    from models.tt_dit.pipelines.stable_diffusion_35_large.pipeline_stable_diffusion_35_large import (
        StableDiffusion3Pipeline,
    )
    from models.tt_dit.pipelines.wan.pipeline_wan import WanPipeline
except ImportError:
    # 0.9.0 image (Motif): models.experimental.tt_dit
    from models.experimental.tt_dit.pipelines.flux1.pipeline_flux1 import Flux1Pipeline  # type: ignore[no-redef]
    from models.experimental.tt_dit.pipelines.mochi.pipeline_mochi import MochiPipeline  # type: ignore[no-redef]
    from models.experimental.tt_dit.pipelines.motif.pipeline_motif import MotifPipeline  # type: ignore[no-redef]
    from models.experimental.tt_dit.pipelines.stable_diffusion_35_large.pipeline_stable_diffusion_35_large import (  # type: ignore[no-redef]
        StableDiffusion3Pipeline,
    )
    from models.experimental.tt_dit.pipelines.wan.pipeline_wan import WanPipeline  # type: ignore[no-redef]
try:
    # 0.17.0 only — not present in 0.9.0 Motif image
    from models.tt_dit.pipelines.qwenimage.pipeline_qwenimage import (
        QwenImagePipeline,
    )
except ImportError:
    QwenImagePipeline = None  # type: ignore[assignment,misc]
try:
    from models.tt_dit.pipelines.wan.pipeline_wan_i2v import (
        ImagePrompt,
        WanPipelineI2V,
    )
except ImportError:
    ImagePrompt = None  # type: ignore[assignment,misc]
    WanPipelineI2V = None  # type: ignore[assignment,misc]
from PIL import Image
from telemetry.telemetry_client import TelemetryEvent
from tt_model_runners.base_metal_device_runner import BaseMetalDeviceRunner
from utils.decorators import log_execution_time
from utils.image_manager import ImageManager
try:
    from utils.logger import log_exception_chain
except ImportError:
    import sys as _sys, traceback as _tb
    # 0.9.0 image — format_tb gives stack frames without touching str(e)/repr(e),
    # which avoids pybind11 ABI issues where __str__/__repr__ itself raises.
    def log_exception_chain(logger, device_id, msg, e):  # type: ignore[misc]
        etype, _eval, etb = _sys.exc_info()
        ename = getattr(etype, "__name__", repr(etype)) if etype else type(e).__name__
        try:
            frames = "".join(_tb.format_tb(etb))
        except Exception:
            frames = "(frames unavailable)"
        logger.error(f"Device {device_id}: {msg}: [{ename}]\n{frames}")

dit_runner_log_map = {
    ModelRunners.TT_SD3_5.value: "SD35",
    ModelRunners.TT_FLUX_1_DEV.value: "FLUX.1-dev",
    ModelRunners.TT_FLUX_1_SCHNELL.value: "FLUX.1-schnell",
    ModelRunners.TT_MOTIF_IMAGE_6B_PREVIEW.value: "Motif-Image-6B-Preview",
    ModelRunners.TT_MOCHI_1.value: "Mochi1",
    ModelRunners.TT_WAN_2_2.value: "Wan22",
    ModelRunners.TT_WAN_2_2_I2V.value: "Wan22-I2V",
    ModelRunners.TT_WAN_2_2_I2V_PRODIA.value: "Wan22-I2V-Prodia",
    ModelRunners.TT_WAN_2_2_I2V_ANISORA.value: "Wan22-I2V-AniSora",
    ModelRunners.TT_WAN_2_2_I2V_DISTILL.value: "Wan22-I2V-Distill",
    ModelRunners.TT_WAN_2_2_I2V_LORA.value: "Wan22-I2V-LoRA",
    ModelRunners.TT_QWEN_IMAGE.value: "Qwen-Image",
    ModelRunners.TT_QWEN_IMAGE_2512.value: "Qwen-Image-2512",
    ModelRunners.SP_RUNNER.value: "SP-Runner",
    ModelRunners.TT_WAN_2_2_ANIMATE.value: "Wan22-Animate",
    ModelRunners.TT_SKYREELS_V2.value: "SkyReels-V2",
    ModelRunners.TT_SKYREELS_V2_I2V.value: "SkyReels-V2-I2V",
}

# Raised from 6000 to 14400 (4h) — Z-Image-Turbo first-run TTNN kernel
# compilation on P150X4 takes 90+ min; the default timed out during weight load.
DIT_WEIGHTS_DISTRIBUTION_TIMEOUT_SECONDS = 14400


class TTDiTRunner(BaseMetalDeviceRunner):
    def __init__(self, device_id: str):
        super().__init__(device_id)
        self.pipeline = None

    def _configure_fabric(self, updated_device_params):
        # 0.9.0 image (Motif): FabricRouterConfig was added in 0.17.0.
        # set_fabric_config still exists but takes 6 args (no router config).
        # We must call it — skipping leaves fabric uninitialized, which crashes
        # all_gather_async inside the CLIP encoder.
        if not hasattr(ttnn, "FabricRouterConfig"):
            fabric_config = updated_device_params.pop(
                "fabric_config", ttnn.FabricConfig.FABRIC_1D
            )
            fabric_tensix_config = updated_device_params.pop(
                "fabric_tensix_config", ttnn.FabricTensixConfig.DISABLED
            )
            reliability_mode = updated_device_params.pop(
                "reliability_mode", ttnn.FabricReliabilityMode.STRICT_INIT
            )
            updated_device_params.pop("fabric_router_config", None)
            try:
                ttnn.set_fabric_config(
                    fabric_config,
                    reliability_mode,
                    None,
                    fabric_tensix_config,
                    ttnn.FabricUDMMode.DISABLED,
                    ttnn.FabricManagerMode.DEFAULT,
                )
            except Exception as e:
                self.logger.warning(
                    f"Device {self.device_id}: 0.9.0 fabric init warning: {type(e).__name__}"
                )
            return fabric_config
        try:
            fabric_config = updated_device_params.pop(
                "fabric_config", ttnn.FabricConfig.FABRIC_1D
            )
            fabric_tensix_config = updated_device_params.pop(
                "fabric_tensix_config", ttnn.FabricTensixConfig.DISABLED
            )
            reliability_mode = updated_device_params.pop(
                "reliability_mode", ttnn.FabricReliabilityMode.STRICT_INIT
            )
            fabric_router_config = updated_device_params.pop(
                "fabric_router_config", ttnn.FabricRouterConfig()
            )
            ttnn.set_fabric_config(
                fabric_config,
                reliability_mode,
                None,
                fabric_tensix_config,
                ttnn.FabricUDMMode.DISABLED,
                ttnn.FabricManagerMode.DEFAULT,
                fabric_router_config,
            )
            return fabric_config
        except Exception as e:
            log_exception_chain(
                self.logger,
                self.device_id,
                "Fabric configuration failed",
                e,
            )
            raise RuntimeError(f"Fabric configuration failed: {str(e)}") from e

    @abstractmethod
    def create_pipeline(self):
        """Create a pipeline for the model"""

    @abstractmethod
    def get_pipeline_device_params(self):
        """Get the device parameters for the pipeline"""

    @log_execution_time(
        f"{dit_runner_log_map[get_settings().model_runner]} warmup",
        TelemetryEvent.DEVICE_WARMUP,
        os.environ.get("TT_VISIBLE_DEVICES"),
    )
    def load_weights(self):
        return True  # weights will be loaded upon pipeline creation

    async def warmup(self) -> bool:
        self.logger.info(f"Device {self.device_id}: Loading model...")

        def distribute_block():
            self.pipeline = self.create_pipeline()

        weights_distribution_timeout = max(
            getattr(self.settings, "weights_distribution_timeout_seconds",
                    DIT_WEIGHTS_DISTRIBUTION_TIMEOUT_SECONDS),
            DIT_WEIGHTS_DISTRIBUTION_TIMEOUT_SECONDS,
        )
        try:
            await asyncio.wait_for(
                asyncio.to_thread(distribute_block),
                timeout=weights_distribution_timeout,
            )
        except asyncio.TimeoutError:
            self.logger.error(
                f"Device {self.device_id}: ttnn.distribute block timed out after {weights_distribution_timeout} seconds"
            )
            raise
        except Exception as e:
            log_exception_chain(
                self.logger,
                self.device_id,
                "Exception during model loading",
                e,
            )
            raise

        self.logger.info(f"Device {self.device_id}: Model loaded successfully")

        # we use model_construct to create the request without validation
        # (warmup uses 2 inference steps which is below the normal minimum)
        if self.settings.model_service == ModelServices.IMAGE.value:
            self.run(
                [
                    ImageGenerateRequest.model_construct(
                        prompt="Sunrise on a beach",
                        negative_prompt="",
                        num_inference_steps=2,
                    )
                ],
            )
        elif self.settings.model_service == ModelServices.VIDEO.value:
            self.run([self._build_warmup_video_request()])

        self.logger.info(f"Device {self.device_id}: Model warmup completed")

        return True

    def _build_warmup_video_request(self) -> VideoGenerateRequest:
        """
        Build the throwaway request used to trigger compile/trace on warmup.
        """
        return VideoGenerateRequest.model_construct(
            prompt="Sunrise on a beach",
            negative_prompt="",
            num_inference_steps=2,
        )

    @log_execution_time(
        f"{dit_runner_log_map[get_settings().model_runner]} inference",
        TelemetryEvent.MODEL_INFERENCE,
        os.environ.get("TT_VISIBLE_DEVICES"),
    )
    def run(self, requests: list[ImageGenerateRequest]):
        self.logger.debug(f"Device {self.device_id}: Running inference")
        request = requests[0]
        image = self.pipeline.run_single_prompt(
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            num_inference_steps=request.num_inference_steps,
            seed=int(request.seed or 0),
        )
        self.logger.debug(f"Device {self.device_id}: Inference completed")
        return image


class TTSD35Runner(TTDiTRunner):
    def __init__(self, device_id: str):
        super().__init__(device_id)

    def create_pipeline(self):
        try:
            return StableDiffusion3Pipeline.create_pipeline(
                mesh_device=self.ttnn_device,
                checkpoint_name=SupportedModels.STABLE_DIFFUSION_3_5_LARGE.value,
            )
        except Exception as e:
            log_exception_chain(
                self.logger,
                self.device_id,
                "SD3.5 pipeline creation failed",
                e,
            )
            raise

    def get_pipeline_device_params(self):
        return {"l1_small_size": 32768, "trace_region_size": 25000000}


# Runner for Flux.1 dev and schnell. Model weights from settings.model_weights_path determine the exact model variant.
class TTFlux1Runner(TTDiTRunner):
    def __init__(self, device_id: str):
        super().__init__(device_id)

    def create_pipeline(self):
        try:
            return Flux1Pipeline.create_pipeline(
                checkpoint_name=self.settings.model_weights_path,
                mesh_device=self.ttnn_device,
            )
        except Exception as e:
            log_exception_chain(
                self.logger,
                self.device_id,
                "Flux1 pipeline creation failed",
                e,
            )
            raise

    def get_pipeline_device_params(self):
        # Flux needs ~50.6 MB of trace buffers on P300X2; the settings default
        # (~33 MB) is too small.  64 MB gives comfortable headroom.
        return {"l1_small_size": 32768, "trace_region_size": 64_000_000}


class TTMotifImage6BPreviewRunner(TTDiTRunner):
    def __init__(self, device_id: str):
        super().__init__(device_id)

    def create_pipeline(self):
        try:
            kwargs = {
                "mesh_device": self.ttnn_device,
                "checkpoint_name": SupportedModels.MOTIF_IMAGE_6B_PREVIEW.value,
            }
            if tuple(self.ttnn_device.shape) == (2, 2):
                # P300X2 has 4 chips in a (2,2) mesh.  Motif only validates (2,4)
                # and (4,8).  tp=2 produces random noise; tp=4 via (1,4) reshape
                # produces banded noise (chip routing mismatch).  Fall back to a
                # single-chip (1,1) submesh with no parallelism — slower but correct.
                # t5_enabled=False: diagnostic — T5 forward patch might still be wrong
                # for tp=1 (wrong return shape from o_proj).  CLIP-only determines
                # whether T5 is the noise source.
                single_chip = self.ttnn_device.create_submeshes(ttnn.MeshShape(1, 1))[0]
                kwargs["mesh_device"] = single_chip
                kwargs.update({
                    "dit_cfg": (1, 0),
                    "dit_sp": (1, 0),
                    "dit_tp": (1, 1),
                    "encoder_tp": (1, 1),
                    "vae_tp": (1, 1),
                    "num_links": 1,
                    "use_torch_t5_text_encoder": True,  # diagnostic: use CPU T5 to bypass TT-Metal T5 issues on tp=1
                })
            return MotifPipeline.create_pipeline(**kwargs)
        except Exception as e:
            log_exception_chain(
                self.logger,
                self.device_id,
                "Motif pipeline creation failed",
                e,
            )
            raise

    def run(self, requests: list[ImageGenerateRequest]):
        # Keep traced=False for now: the trace corruption warning ("Allocating device
        # buffers is unsafe due to the existence of an active trace") from the second
        # warmup pass appears even with the (1,4) reshape.  Traced=False is ~20%
        # slower but produces correct output once the tp=4 config is in place.
        request = requests[0]
        image = self.pipeline.run_single_prompt(
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            num_inference_steps=request.num_inference_steps,
            seed=int(request.seed or 0),
            traced=False,
        )
        return image

    def get_pipeline_device_params(self):
        return {"l1_small_size": 32768, "trace_region_size": 31000000}


# Runner for Qwen-Image and Qwen-Image-2512. Model weights from settings.model_weights_path determine the exact model variant.
class TTQwenImageRunner(TTDiTRunner):
    def __init__(self, device_id: str):
        super().__init__(device_id)

    def create_pipeline(self):
        try:
            return QwenImagePipeline.create_pipeline(
                mesh_device=self.ttnn_device,
                checkpoint_name=self.settings.model_weights_path,
            )
        except Exception as e:
            log_exception_chain(
                self.logger,
                self.device_id,
                "Qwen-Image pipeline creation failed",
                e,
            )
            raise

    def get_pipeline_device_params(self):
        return {"trace_region_size": 47000000}


class TTMochi1Runner(TTDiTRunner):
    def __init__(self, device_id: str):
        super().__init__(device_id)
        os.environ["TT_DIT_CACHE_DIR"] = "/tmp/TT_DIT_CACHE"

    def create_pipeline(self):
        try:
            return MochiPipeline.create_pipeline(
                mesh_device=self.ttnn_device,
                checkpoint_name=SupportedModels.MOCHI_1.value,
            )
        except Exception as e:
            log_exception_chain(
                self.logger,
                self.device_id,
                "Mochi pipeline creation failed",
                e,
            )
            raise

    @log_execution_time(
        f"{dit_runner_log_map[get_settings().model_runner]} inference",
        TelemetryEvent.MODEL_INFERENCE,
        os.environ.get("TT_VISIBLE_DEVICES"),
    )
    def run(self, requests: list[VideoGenerateRequest]):
        self.logger.debug(f"Device {self.device_id}: Running inference")
        request = requests[0]
        frames = self.pipeline(
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            num_inference_steps=request.num_inference_steps,
            guidance_scale=3.5,
            num_frames=168,  # TODO: Parameterize output dimensions.
            height=480,
            width=848,
            output_type="np",
            seed=int(request.seed or 0),
        )
        self.logger.debug(f"Device {self.device_id}: Inference completed")
        return frames

    def get_pipeline_device_params(self):
        return {}


WAN22_BH_RING_MESH_SHAPES = frozenset({(1, 4)})

WAN22_GALAXY_BH_TRACE_REGION_BYTES = 125_000_000
WAN22_GALAXY_ROUTER_MAX_PAYLOAD_BYTES = 8192

# The LightX2V 4-step distill, with the fast-VAE-encode + on-device conditioning
# optimizations enabled, captures a larger trace than the shared 125MB default
# (the fully-optimized 4x32 traced run needs ~200MB; 125MB hits the TT_FATAL
# `trace_buffers_size <= trace_region_size` during warmup). Distill-only so the
# other Wan2.2 runners keep the shared default.
WAN22_DISTILL_BH_TRACE_REGION_BYTES = 200_000_000

# Fast-image-encode optimizations for the LightX2V distill pipeline. Enabling all
# three takes the traced 4x32 pipeline from ~6-7s to ~4s with no quality loss
# (validated via per-frame PCC + visual checks against the full-encode baseline):
#   - WAN_DISTILL_FAST_VAE_ENCODER: rebuild the VAE encoder at the real resolution
#     so it keys the swept conv3d blockings (encoder compute 1.44s -> 0.21s).
#   - WAN_DISTILL_ENCODER_T_OUT_1: cap conv3d T_out_block at 1, which removes the
#     temporal-blocking "duplicate subject" artifact the swept encoder otherwise
#     introduces in the 4-step distill. MUST accompany FAST_VAE_ENCODER.
#   - WAN_DISTILL_ONDEVICE_COND: assemble the (mostly-zero) conditioning video on
#     device instead of transferring it from host (prepare_latents 2.99s -> 0.38s).
# Set via setdefault so a deployment can still pin any flag to "0" to disable.
WAN_DISTILL_FAST_ENCODE_FLAGS = {
    "WAN_DISTILL_FAST_VAE_ENCODER": "1",
    "WAN_DISTILL_ENCODER_T_OUT_1": "1",
    "WAN_DISTILL_ONDEVICE_COND": "1",
}

# AniSora V3.2 reuses the same fast-image-encode optimizations (via the shared
# FastImageEncodeMixin) behind AniSora-scoped flags. All three enabled takes the
# traced 8-step 4x32 pipeline image-encode from ~7.5s to ~0.35s (total ~16s ->
# ~9.3s) with quality matching the full-encode baseline. ENCODER_T_OUT_1 MUST
# accompany FAST_VAE_ENCODER to avoid the temporal-blocking artifact.
WAN_ANISORA_FAST_ENCODE_FLAGS = {
    "WAN_ANISORA_FAST_VAE_ENCODER": "1",
    "WAN_ANISORA_ENCODER_T_OUT_1": "1",
    "WAN_ANISORA_ONDEVICE_COND": "1",
}

# AniSora runs real CFG (guidance 3.5 on both experts) and its fully-optimized
# 8-step trace needs the same ~200MB region as the distill (the shared 125MB
# default OOMs during warmup).
WAN22_ANISORA_BH_TRACE_REGION_BYTES = 200_000_000
WAN22_ANISORA_GUIDANCE_SCALE = 3.5
# Fixed step count (mirrors the distill forcing 4): AniSora always runs 8 steps,
# the validated good-quality / low-latency point (~9.3s traced). The client's
# num_inference_steps is ignored, same as the distill runner.
WAN22_ANISORA_NUM_STEPS = 8


def _wan22_needs_ring_fabric(mesh_shape: tuple) -> bool:
    """Return True when Wan2.2 must advertise FABRIC_1D_RING for ``mesh_shape``."""
    if is_large_mesh(mesh_shape):
        return True
    return is_blackhole() and tuple(mesh_shape) in WAN22_BH_RING_MESH_SHAPES


def _wan22_galaxy_router_config():
    """Build the FabricRouterConfig used by Galaxy-class BH meshes."""
    config = ttnn.FabricRouterConfig()
    config.max_packet_payload_size_bytes = WAN22_GALAXY_ROUTER_MAX_PAYLOAD_BYTES
    return config


def _wan22_dit_device_params(mesh_shape: tuple) -> dict:
    """Resolve fabric / trace-region defaults shared by Wan2.2 T2V and I2V runners."""
    fabric_config = (
        ttnn.FabricConfig.FABRIC_1D_RING
        if _wan22_needs_ring_fabric(mesh_shape)
        else ttnn.FabricConfig.FABRIC_1D
    )
    device_params: dict = {"fabric_config": fabric_config}

    if is_blackhole():
        device_params["reliability_mode"] = ttnn.FabricReliabilityMode.RELAXED_INIT

    if is_large_mesh(mesh_shape) and is_blackhole():
        device_params["trace_region_size"] = WAN22_GALAXY_BH_TRACE_REGION_BYTES
        device_params["fabric_router_config"] = _wan22_galaxy_router_config()

    return device_params


def _wan22_pipeline_args(
    request,
    resolution=None,
    image_prompt=None,
):
    """Build the kwargs dict shared by Wan2.2 T2V and I2V ``__call__`` sites."""
    seed = int(request.seed) if request.seed is not None else 0
    pipeline_args = {
        "prompts": [request.prompt],
        "num_inference_steps": request.num_inference_steps,
        "guidance_scale": 4.0,
        "guidance_scale_2": 3.0,
        "seed": seed,
        "traced": True,
    }
    if image_prompt is not None:
        pipeline_args["image_prompt"] = image_prompt
    if bool(request.negative_prompt):
        pipeline_args["negative_prompts"] = [request.negative_prompt]
    return pipeline_args


class TTWan22Runner(TTDiTRunner):
    def __init__(self, device_id: str):
        super().__init__(device_id)
        self.resolution = wan22_target_resolution(self.settings.device_mesh_shape)

    def create_pipeline(self):
        try:
            return WanPipeline.create_pipeline(
                mesh_device=self.ttnn_device,
                checkpoint_name=self.settings.model_weights_path,
                height=self.resolution.height,
                width=self.resolution.width,
                num_frames=WAN22_NUM_FRAMES,
            )
        except Exception as e:
            log_exception_chain(
                self.logger,
                self.device_id,
                "Wan pipeline creation failed",
                e,
            )
            raise

    def load_weights(self):
        return False

    @log_execution_time(
        f"{dit_runner_log_map[get_settings().model_runner]} inference",
        TelemetryEvent.MODEL_INFERENCE,
        os.environ.get("TT_VISIBLE_DEVICES"),
    )
    def run(self, requests: list[VideoGenerateRequest]):
        self.logger.debug(f"Device {self.device_id}: Running inference")
        frames = self.pipeline(**_wan22_pipeline_args(requests[0], self.resolution))
        self.logger.debug(f"Device {self.device_id}: Inference completed")
        return frames

    def get_pipeline_device_params(self):
        return _wan22_dit_device_params(self.settings.device_mesh_shape)


class TTWan22I2VProdiaRunner(TTDiTRunner):
    """Wan2.2 I2V runner using the Prodia distilled pipeline.
    Single-image conditioning only — when the broadcast carries
    ``image_prompts`` with multiple entries, the prompt with the lowest
    ``frame_pos`` is selected and the rest are dropped (the distilled pipeline
    does not accept multi-frame conditioning).
    """

    def __init__(self, device_id: str):
        super().__init__(device_id)
        self.image_manager = ImageManager("img")
        # Export MP4 inside the device worker by default to avoid pickling the
        # raw frame array (~226MB at 720p×81 frames) over IPC.
        self.export_in_runner = True

    def _build_warmup_video_request(self) -> VideoI2VGenerateRequest:
        """Synthetic 64x64 PIL warmup — same approach as TTWan22I2VRunner.

        The Prodia pipeline resizes to (height, width) before VAE encoding,
        so the input resolution is irrelevant; a small black frame exercises
        the same kernels as a real photo without paying the JPEG encode cost.
        """
        dummy = Image.new("RGB", (64, 64), color=0)
        buf = io.BytesIO()
        dummy.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return VideoI2VGenerateRequest.model_construct(
            prompt="Sunrise on a beach",
            negative_prompt="",
            num_inference_steps=2,
            image_prompts=[ImagePromptEntry(image=b64, frame_pos=0)],
        )

    def load_weights(self):
        return False

    def get_pipeline_device_params(self):
        # The 4x8 LoudBox trace binary needs ~30.6MB; the default 30MB region
        # rejects it and warmup OOMs. Both 4x8 (32 chips) and 4x32 (128 chips)
        # Blackhole meshes get the bumped trace region.
        device_params = {"fabric_config": ttnn.FabricConfig.FABRIC_1D_RING}
        mesh_size = (
            self.settings.device_mesh_shape[0] * self.settings.device_mesh_shape[1]
        )
        if mesh_size >= 32 and is_blackhole():
            device_params["trace_region_size"] = 120_000_000
            config = ttnn.FabricRouterConfig()
            config.max_packet_payload_size_bytes = 8192
            device_params["fabric_router_config"] = config
        return device_params

    def create_pipeline(self):
        try:
            from models.tt_dit.prodia.pipelines.pipeline_i2v import (
                create_i2v_pipeline,
            )

            resolution = wan22_target_resolution(self.settings.device_mesh_shape)
            return create_i2v_pipeline(
                self.ttnn_device,
                weights_dir=self.settings.model_weights_path,
                height=resolution.height,
                width=resolution.width,
                num_frames=WAN22_NUM_FRAMES,
            )
        except Exception as e:
            log_exception_chain(
                self.logger,
                self.device_id,
                "Prodia I2V pipeline creation failed",
                e,
            )
            raise

    def _build_image_prompt(
        self, request: VideoI2VGenerateRequest, target_size: tuple[int, int]
    ) -> list:
        """Decode ``image_prompts`` into the (PIL, frame_pos) tuple list the
        Prodia pipeline expects for multi-frame conditioning.
        """
        return [
            (
                self.image_manager.base64_to_pil_image(
                    entry.image, target_size=target_size, target_mode="RGB"
                ),
                entry.frame_pos,
            )
            for entry in request.image_prompts
        ]

    @log_execution_time(
        f"{dit_runner_log_map[get_settings().model_runner]} inference",
        TelemetryEvent.MODEL_INFERENCE,
        os.environ.get("TT_VISIBLE_DEVICES"),
    )
    def run(self, requests: list[VideoI2VGenerateRequest]):
        self.logger.debug(f"Device {self.device_id}: Running inference")
        request = requests[0]
        resolution = wan22_target_resolution(self.settings.device_mesh_shape)
        image_prompt = self._build_image_prompt(
            request, target_size=(resolution.width, resolution.height)
        )
        frames = self.pipeline(
            prompt=request.prompt,
            image=image_prompt,
            height=resolution.height,
            width=resolution.width,
            num_frames=WAN22_NUM_FRAMES,
            seed=int(request.seed or 0),
            traced=True,
        )
        self.logger.debug(f"Device {self.device_id}: Inference completed")
        if self.export_in_runner:
            from utils.video_manager import VideoManager

            return [VideoManager().export_to_mp4(frames)]
        return frames


class TTWan22I2VRunner(TTDiTRunner):
    """
    Wan2.2 image-to-video runner.
    """

    def __init__(self, device_id: str):
        super().__init__(device_id)
        self.resolution = wan22_target_resolution(self.settings.device_mesh_shape)
        self.image_manager = ImageManager()

    def create_pipeline(self):
        try:
            return WanPipelineI2V.create_pipeline(
                mesh_device=self.ttnn_device,
                height=self.resolution.height,
                width=self.resolution.width,
                num_frames=WAN22_NUM_FRAMES,
            )
        except Exception as e:
            log_exception_chain(
                self.logger,
                self.device_id,
                "Wan I2V pipeline creation failed",
                e,
            )
            raise

    def load_weights(self):
        return False

    def _build_image_prompt(self, request: VideoI2VGenerateRequest) -> list:
        """Decode base64 images into ``List[ImagePrompt]`` for the pipeline."""
        return [
            ImagePrompt(
                image=self.image_manager.base64_to_pil_image(entry.image),
                frame_pos=entry.frame_pos,
            )
            for entry in request.image_prompts
        ]

    @log_execution_time(
        f"{dit_runner_log_map[get_settings().model_runner]} inference",
        TelemetryEvent.MODEL_INFERENCE,
        os.environ.get("TT_VISIBLE_DEVICES"),
    )
    def run(self, requests: list[VideoI2VGenerateRequest]):
        self.logger.debug(f"Device {self.device_id}: Running inference")
        request = requests[0]
        pipeline_args = _wan22_pipeline_args(
            request,
            self.resolution,
            image_prompt=self._build_image_prompt(request),
        )
        frames = self.pipeline(**pipeline_args)
        self.logger.debug(f"Device {self.device_id}: Inference completed")
        return frames

    def get_pipeline_device_params(self):
        return _wan22_dit_device_params(self.settings.device_mesh_shape)

    def _build_warmup_video_request(self) -> VideoI2VGenerateRequest:
        """Warmup request with a synthetic 64x64 PIL so the VAE encoder has
        input to process.

        The I2V pipeline resizes the image to the target (height, width)
        before VAE encoding, so the input resolution is irrelevant — a
        small black frame exercises the same kernels as a real photo.
        """
        dummy = Image.new("RGB", (64, 64), color=0)
        buf = io.BytesIO()
        dummy.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        return VideoI2VGenerateRequest.model_construct(
            prompt="Sunrise on a beach",
            negative_prompt="",
            num_inference_steps=2,
            image_prompts=[ImagePromptEntry(image=b64, frame_pos=0)],
        )


# ---------------------------------------------------------------------------
# Wan2.2 I2V experimental variants: AniSora, Distill (LightX2V), LoRA
# ---------------------------------------------------------------------------


def _wan22_i2v_warmup_request(prompt: str = "Sunrise on a beach"):
    """Shared warmup request builder for I2V experimental runners."""
    dummy = Image.new("RGB", (64, 64), color=0)
    buf = io.BytesIO()
    dummy.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return VideoI2VGenerateRequest.model_construct(
        prompt=prompt,
        negative_prompt="",
        num_inference_steps=2,
        image_prompts=[ImagePromptEntry(image=b64, frame_pos=0)],
    )


class TTWan22I2VAniSoraRunner(TTDiTRunner):
    """Wan2.2 I2V with AniSora V3.2 anime fine-tune weights."""

    def __init__(self, device_id: str):
        super().__init__(device_id)
        self.resolution = wan22_target_resolution(self.settings.device_mesh_shape)
        self.image_manager = ImageManager()

    def create_pipeline(self):
        try:
            from models.tt_dit.experimental.pipelines.pipeline_anisora import (
                AniSoraPipeline,
            )

            # Enable the fast-image-encode path before the pipeline reads these
            # flags at build time. setdefault keeps any deployment-provided value.
            for flag, value in WAN_ANISORA_FAST_ENCODE_FLAGS.items():
                os.environ.setdefault(flag, value)
            self.logger.info(
                "AniSora fast-encode flags: "
                + ", ".join(
                    f"{flag}={os.environ.get(flag)}"
                    for flag in WAN_ANISORA_FAST_ENCODE_FLAGS
                )
            )

            return AniSoraPipeline.create_pipeline(
                mesh_device=self.ttnn_device,
                height=self.resolution.height,
                width=self.resolution.width,
                num_frames=WAN22_NUM_FRAMES,
            )
        except Exception as e:
            log_exception_chain(
                self.logger, self.device_id, "AniSora I2V pipeline creation failed", e
            )
            raise

    def load_weights(self):
        return False

    def _build_image_prompt(self, request: VideoI2VGenerateRequest) -> list:
        return [
            ImagePrompt(
                image=self.image_manager.base64_to_pil_image(entry.image),
                frame_pos=entry.frame_pos,
            )
            for entry in request.image_prompts
        ]

    @log_execution_time(
        f"{dit_runner_log_map.get(get_settings().model_runner, 'AniSora')} inference",
        TelemetryEvent.MODEL_INFERENCE,
        os.environ.get("TT_VISIBLE_DEVICES"),
    )
    def run(self, requests: list[VideoI2VGenerateRequest]):
        self.logger.debug(f"Device {self.device_id}: Running AniSora inference")
        request = requests[0]
        pipeline_args = _wan22_pipeline_args(
            request,
            self.resolution,
            image_prompt=self._build_image_prompt(request),
        )
        # AniSora-specific: force 8 steps (ignore the client's num_inference_steps,
        # same as the distill forces 4) and use the model's real CFG (3.5 on both
        # experts) rather than the shared 4.0/3.0 default.
        pipeline_args["num_inference_steps"] = WAN22_ANISORA_NUM_STEPS
        pipeline_args["guidance_scale"] = WAN22_ANISORA_GUIDANCE_SCALE
        pipeline_args["guidance_scale_2"] = WAN22_ANISORA_GUIDANCE_SCALE
        frames = self.pipeline(**pipeline_args)
        self.logger.debug(f"Device {self.device_id}: AniSora inference completed")
        return frames

    def get_pipeline_device_params(self):
        # Start from the shared Wan2.2 fabric/trace defaults, then bump the trace
        # region for AniSora's fully-optimized 8-step trace (see constant above).
        device_params = _wan22_dit_device_params(self.settings.device_mesh_shape)
        if is_large_mesh(self.settings.device_mesh_shape) and is_blackhole():
            device_params["trace_region_size"] = WAN22_ANISORA_BH_TRACE_REGION_BYTES
        return device_params

    def _build_warmup_video_request(self) -> VideoI2VGenerateRequest:
        return _wan22_i2v_warmup_request("An anime girl smiling, soft lighting")


class TTWan22I2VDistillRunner(TTDiTRunner):
    """Wan2.2 I2V with LightX2V 4-step distilled weights.

    Distill bakes in classifier-free guidance, so ``guidance_scale`` is forced
    to 1.0 and ``num_inference_steps`` defaults to 4.
    """

    def __init__(self, device_id: str):
        super().__init__(device_id)
        self.resolution = wan22_target_resolution(self.settings.device_mesh_shape)
        self.image_manager = ImageManager()

    def create_pipeline(self):
        try:
            from models.tt_dit.experimental.pipelines.pipeline_wan_distill import (
                WanDistillPipelineI2V,
            )

            # Enable the fast-image-encode path before the pipeline reads these
            # flags at build time. setdefault keeps any deployment-provided value.
            for flag, value in WAN_DISTILL_FAST_ENCODE_FLAGS.items():
                os.environ.setdefault(flag, value)
            self.logger.info(
                "Distill fast-encode flags: "
                + ", ".join(
                    f"{flag}={os.environ.get(flag)}"
                    for flag in WAN_DISTILL_FAST_ENCODE_FLAGS
                )
            )

            return WanDistillPipelineI2V.create_pipeline(
                mesh_device=self.ttnn_device,
                height=self.resolution.height,
                width=self.resolution.width,
                num_frames=WAN22_NUM_FRAMES,
            )
        except Exception as e:
            log_exception_chain(
                self.logger, self.device_id, "Distill I2V pipeline creation failed", e
            )
            raise

    def load_weights(self):
        return False

    def _build_image_prompt(self, request: VideoI2VGenerateRequest) -> list:
        return [
            ImagePrompt(
                image=self.image_manager.base64_to_pil_image(entry.image),
                frame_pos=entry.frame_pos,
            )
            for entry in request.image_prompts
        ]

    @log_execution_time(
        f"{dit_runner_log_map.get(get_settings().model_runner, 'Distill')} inference",
        TelemetryEvent.MODEL_INFERENCE,
        os.environ.get("TT_VISIBLE_DEVICES"),
    )
    def run(self, requests: list[VideoI2VGenerateRequest]):
        self.logger.debug(f"Device {self.device_id}: Running Distill inference")
        request = requests[0]
        seed = int(request.seed) if request.seed is not None else 0
        pipeline_args = {
            "prompts": [request.prompt],
            "num_inference_steps": 4,
            "guidance_scale": 1.0,
            "guidance_scale_2": 1.0,
            "seed": seed,
            "traced": True,
            "image_prompt": self._build_image_prompt(request),
        }
        frames = self.pipeline(**pipeline_args)
        self.logger.debug(f"Device {self.device_id}: Distill inference completed")
        return frames

    def get_pipeline_device_params(self):
        # Start from the shared Wan2.2 fabric/trace defaults, then bump the trace
        # region for the distill's fully-optimized trace (see constant above).
        device_params = _wan22_dit_device_params(self.settings.device_mesh_shape)
        if is_large_mesh(self.settings.device_mesh_shape) and is_blackhole():
            device_params["trace_region_size"] = WAN22_DISTILL_BH_TRACE_REGION_BYTES
        return device_params

    def _build_warmup_video_request(self) -> VideoI2VGenerateRequest:
        return _wan22_i2v_warmup_request()


class TTWan22I2VLoRARunner(TTDiTRunner):
    """Wan2.2 I2V with LoRA adapter fusion (camera control, style, etc.).

    LoRA weights are resolved from ``LORA_HIGH_PATH`` / ``LORA_LOW_PATH``
    environment variables by the pipeline's ``__init__``.
    """

    def __init__(self, device_id: str):
        super().__init__(device_id)
        self.resolution = wan22_target_resolution(self.settings.device_mesh_shape)
        self.image_manager = ImageManager()

    def create_pipeline(self):
        try:
            from models.tt_dit.experimental.pipelines.pipeline_wan_lora import (
                WanPipelineI2VLora,
            )

            lora_high = os.environ.get("LORA_HIGH_PATH")
            lora_low = os.environ.get("LORA_LOW_PATH")

            return WanPipelineI2VLora.create_pipeline(
                mesh_device=self.ttnn_device,
                height=self.resolution.height,
                width=self.resolution.width,
                num_frames=WAN22_NUM_FRAMES,
                lora_high=lora_high,
                lora_low=lora_low,
            )
        except Exception as e:
            log_exception_chain(
                self.logger, self.device_id, "LoRA I2V pipeline creation failed", e
            )
            raise

    def load_weights(self):
        return False

    def _build_image_prompt(self, request: VideoI2VGenerateRequest) -> list:
        return [
            ImagePrompt(
                image=self.image_manager.base64_to_pil_image(entry.image),
                frame_pos=entry.frame_pos,
            )
            for entry in request.image_prompts
        ]

    @log_execution_time(
        f"{dit_runner_log_map.get(get_settings().model_runner, 'LoRA')} inference",
        TelemetryEvent.MODEL_INFERENCE,
        os.environ.get("TT_VISIBLE_DEVICES"),
    )
    def run(self, requests: list[VideoI2VGenerateRequest]):
        self.logger.debug(f"Device {self.device_id}: Running LoRA inference")
        request = requests[0]
        pipeline_args = _wan22_pipeline_args(
            request,
            self.resolution,
            image_prompt=self._build_image_prompt(request),
        )
        frames = self.pipeline(**pipeline_args)
        self.logger.debug(f"Device {self.device_id}: LoRA inference completed")
        return frames

    def get_pipeline_device_params(self):
        return _wan22_dit_device_params(self.settings.device_mesh_shape)

    def _build_warmup_video_request(self) -> VideoI2VGenerateRequest:
        return _wan22_i2v_warmup_request("A golden retriever running on a sandy beach")


# ---------------------------------------------------------------------------
# Wan2.2-Animate-14B runner — Blackhole hotpatch
# ---------------------------------------------------------------------------


class TTWan22AnimateRunner(TTDiTRunner):
    """
    Runner for Wan2.2-Animate-14B on TT hardware.

    Uses WanPipelineAnimate (a thin subclass of WanPipelineI2V) which must be
    present as a hotpatch file at:
        patches/tt_dit/pipelines/wan/pipeline_wan_animate.py
    and bind-mounted into the container via the tt_dit hotpatch mechanism when
    the server is started with --dev-mode.

    The character image is fed as the I2V conditioning reference frame.
    Motion video is not used in the TT hardware path (motion_encoder modules
    are not ported to the TTNN WanTransformer3DModel).
    """

    def __init__(self, device_id: str):
        super().__init__(device_id)
        self.resolution = wan22_target_resolution(self.settings.device_mesh_shape)

    def create_pipeline(self):
        try:
            from models.tt_dit.pipelines.wan.pipeline_wan_animate import (
                WanPipelineAnimate,
            )
        except ImportError as exc:
            raise RuntimeError(
                "WanPipelineAnimate not found.  Ensure patches/tt_dit/pipelines/wan/"
                "pipeline_wan_animate.py exists and the server was started with --dev-mode."
            ) from exc

        self.logger.info(
            f"Device {self.device_id}: Loading WanPipelineAnimate "
            f"(mesh {self.settings.device_mesh_shape})"
        )
        try:
            return WanPipelineAnimate.create_pipeline(
                mesh_device=self.ttnn_device,
            )
        except Exception as e:
            log_exception_chain(
                self.logger,
                self.device_id,
                "WanPipelineAnimate creation failed",
                e,
            )
            raise

    def load_weights(self):
        return False

    @log_execution_time(
        f"{dit_runner_log_map[get_settings().model_runner]} inference",
        TelemetryEvent.MODEL_INFERENCE,
        os.environ.get("TT_VISIBLE_DEVICES"),
    )
    def run(self, requests: list[VideoGenerateRequest]):
        import base64
        from io import BytesIO

        from PIL import Image

        self.logger.info(f"Device {self.device_id}: Running Animate inference")
        request = requests[0]

        reference_image_b64 = getattr(request, "reference_image_b64", None)
        if reference_image_b64:
            char_pil = Image.open(
                BytesIO(base64.b64decode(reference_image_b64))
            ).convert("RGB")
            self.logger.info(
                f"Device {self.device_id}: character image "
                f"{char_pil.size[0]}x{char_pil.size[1]}"
            )
        else:
            self.logger.info(
                f"Device {self.device_id}: no reference_image_b64 — grey dummy (warmup)"
            )
            char_pil = Image.new("RGB", (832, 480), color=(128, 128, 128))

        num_frames = getattr(request, "num_frames", None) or WAN22_NUM_FRAMES
        frames = self.pipeline(
            character_image=char_pil,
            prompt=request.prompt or "",
            negative_prompt=request.negative_prompt or "",
            height=self.resolution.height,
            width=self.resolution.width,
            num_frames=num_frames,
            num_inference_steps=request.num_inference_steps,
            guidance_scale=3.0,
            seed=int(request.seed or 0),
        )
        self.logger.debug(f"Device {self.device_id}: Animate inference completed")
        return frames

    def get_pipeline_device_params(self):
        return _wan22_dit_device_params(self.settings.device_mesh_shape)

    def _build_warmup_video_request(self) -> VideoGenerateRequest:
        return VideoGenerateRequest.model_construct(
            prompt="Character animation warmup",
            negative_prompt="",
            num_inference_steps=2,
        )
