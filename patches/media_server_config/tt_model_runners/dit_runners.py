# SPDX-License-Identifier: Apache-2.0
#
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC

import asyncio
import os
from abc import abstractmethod

import ttnn
from config.constants import ModelRunners, ModelServices, SupportedModels
from config.settings import get_settings
from domain.image_generate_request import ImageGenerateRequest
from domain.video_generate_request import VideoGenerateRequest
from models.tt_dit.pipelines.flux1.pipeline_flux1 import Flux1Pipeline
from models.tt_dit.pipelines.mochi.pipeline_mochi import MochiPipeline
from models.tt_dit.pipelines.motif.pipeline_motif import MotifPipeline
from models.tt_dit.pipelines.qwenimage.pipeline_qwenimage import (
    QwenImagePipeline,
)
from models.tt_dit.pipelines.stable_diffusion_35_large.pipeline_stable_diffusion_35_large import (
    StableDiffusion3Pipeline,
)
from models.tt_dit.pipelines.wan.pipeline_wan import WanPipeline
from telemetry.telemetry_client import TelemetryEvent
from tt_model_runners.base_metal_device_runner import BaseMetalDeviceRunner
from utils.decorators import log_execution_time
from utils.logger import log_exception_chain

dit_runner_log_map = {
    ModelRunners.TT_SD3_5.value: "SD35",
    ModelRunners.TT_FLUX_1_DEV.value: "FLUX.1-dev",
    ModelRunners.TT_FLUX_1_SCHNELL.value: "FLUX.1-schnell",
    ModelRunners.TT_MOTIF_IMAGE_6B_PREVIEW.value: "Motif-Image-6B-Preview",
    ModelRunners.TT_MOCHI_1.value: "Mochi1",
    ModelRunners.TT_WAN_2_2.value: "Wan22",
    ModelRunners.TT_WAN_2_2_ANIMATE.value: "Wan22-Animate",
    ModelRunners.TT_QWEN_IMAGE.value: "Qwen-Image",
    ModelRunners.TT_QWEN_IMAGE_2512.value: "Qwen-Image-2512",
    ModelRunners.SP_RUNNER.value: "SP-Runner",
}


class TTDiTRunner(BaseMetalDeviceRunner):
    def __init__(self, device_id: str):
        super().__init__(device_id)
        self.pipeline = None

    def _configure_fabric(self, updated_device_params):
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
            ttnn.set_fabric_config(
                fabric_config, reliability_mode, None, fabric_tensix_config
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

        # 20 minutes to distribute the model on device
        weights_distribution_timeout = 1200
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
            self.run(
                [
                    VideoGenerateRequest.model_construct(
                        prompt="Sunrise on a beach",
                        negative_prompt="",
                        num_inference_steps=2,
                    )
                ],
            )

        self.logger.info(f"Device {self.device_id}: Model warmup completed")

        return True

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
        return {"l1_small_size": 32768, "trace_region_size": 50000000}


class TTMotifImage6BPreviewRunner(TTDiTRunner):
    def __init__(self, device_id: str):
        super().__init__(device_id)

    def create_pipeline(self):
        try:
            return MotifPipeline.create_pipeline(
                mesh_device=self.ttnn_device,
                checkpoint_name=SupportedModels.MOTIF_IMAGE_6B_PREVIEW.value,
            )
        except Exception as e:
            log_exception_chain(
                self.logger,
                self.device_id,
                "Motif pipeline creation failed",
                e,
            )
            raise

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
        # setup environment for Mochi runner
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

    @log_execution_time(f"{dit_runner_log_map[get_settings().model_runner]} inference")
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


class TTWan22Runner(TTDiTRunner):
    def __init__(self, device_id: str):
        super().__init__(device_id)

    def create_pipeline(self):
        try:
            # Use locally mounted weights (MODEL_WEIGHTS_DIR) when available so
            # that from_pretrained never hits the network during the timed
            # ttnn.distribute block.  Fall back to the HF repo ID so the code
            # still works in environments that rely on HF caching.
            checkpoint_name = os.environ.get(
                "MODEL_WEIGHTS_DIR", "Wan-AI/Wan2.2-T2V-A14B-Diffusers"
            )
            self.logger.info(
                f"Device {self.device_id}: Loading Wan pipeline from {checkpoint_name}"
            )
            return WanPipeline.create_pipeline(
                mesh_device=self.ttnn_device,
                checkpoint_name=checkpoint_name,
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

    @log_execution_time(f"{dit_runner_log_map[get_settings().model_runner]} inference")
    def run(self, requests: list[VideoGenerateRequest]):
        self.logger.debug(f"Device {self.device_id}: Running inference")
        request = requests[0]
        # TODO: Move parameterization outside of runner class.
        if tuple(self.pipeline.mesh_device.shape) == (4, 8):
            width = 1280
            height = 720
        else:
            width = 832
            height = 480
        # Use request.num_frames when provided (sent by the app's duration picker);
        # fall back to 81 (3.4 s at 24 fps).  Valid counts are 4k+1: 33, 49, 65, 81, 97…
        num_frames = getattr(request, "num_frames", None) or 81
        frames = self.pipeline(
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            height=height,
            width=width,
            num_frames=num_frames,
            num_inference_steps=request.num_inference_steps,
            guidance_scale=3.0,
            guidance_scale_2=4.0,
            seed=int(request.seed or 0),
        )
        self.logger.debug(f"Device {self.device_id}: Inference completed")
        return frames

    def get_pipeline_device_params(self):
        device_params = {
            "fabric_config": ttnn.FabricConfig.FABRIC_1D,
        }
        if ttnn.device.is_blackhole():
            device_params["fabric_tensix_config"] = ttnn.FabricTensixConfig.MUX
            device_params["dispatch_core_axis"] = ttnn.device.DispatchCoreAxis.ROW
        elif tuple(self.settings.device_mesh_shape) == (4, 8):
            device_params["fabric_config"] = ttnn.FabricConfig.FABRIC_1D_RING
        return device_params


class TTWan22AnimateRunner(TTDiTRunner):
    """
    Runner for Wan2.2-Animate-14B on TT hardware.

    Uses WanPipelineAnimate (a thin subclass of WanPipelineI2V) which must be
    present as a hotpatch file at:
        patches/tt_dit/pipelines/wan/pipeline_wan_animate.py
    and bind-mounted into the container via the tt_dit hotpatch mechanism when
    the server is started with --dev-mode.

    The character image is fed as the I2V conditioning reference frame (frame_pos=0).

    Motion video is NOT used in the TT hardware path.  The upstream Diffusers
    WanPipelineAnimate.__call__ requires pre-extracted pose_video and face_video
    streams (DWPose skeleton + face-tracking frames), which are encoded by
    motion_encoder.* and face_encoder.* modules inside the transformer.  Those
    modules are Animate-14B-specific and are not ported to the TTNN
    WanTransformer3DModel — they are dropped via strict=False during weight load.
    Without the motion_encoder and the per-block attn2.add_k_proj/v_proj
    cross-attention projections on chip, there is no pathway to feed motion
    conditioning into the hardware transformer.

    Until the TTNN model implements these modules, the output motion comes
    entirely from the fine-tuned weight distribution of the Animate-14B checkpoint,
    not from an explicit reference video.

    Input contract (VideoGenerateRequest fields used):
      prompt              — optional style guidance (can be empty string)
      reference_image_b64 — base64-encoded JPEG/PNG of the character to animate
      reference_video_b64 — received but not forwarded to the pipeline (see above)
      num_inference_steps — denoising steps (default 20)
      seed                — random seed

    Output: numpy array of video frames encoded to MP4 by the video service.
    """

    def __init__(self, device_id: str):
        super().__init__(device_id)

    def create_pipeline(self):
        # Lazy import: WanPipelineAnimate lives in a hotpatch file that is
        # bind-mounted at container startup via the tt_dit hotpatch mechanism.
        # It is not present in the installed image, so importing at module level
        # would fail when the module is loaded for other model runners.
        try:
            from models.tt_dit.pipelines.wan.pipeline_wan_animate import (
                WanPipelineAnimate,
            )
        except ImportError as exc:
            raise RuntimeError(
                "WanPipelineAnimate not found in the container.  Ensure that "
                "patches/tt_dit/pipelines/wan/pipeline_wan_animate.py exists "
                "and the server was started with --dev-mode so the tt_dit "
                "hotpatch mechanism bind-mounts it."
            ) from exc

        checkpoint_name = os.environ.get(
            "MODEL_WEIGHTS_DIR", "Wan-AI/Wan2.2-Animate-14B-Diffusers"
        )
        self.logger.info(
            f"Device {self.device_id}: Loading WanPipelineAnimate from {checkpoint_name}"
        )
        try:
            return WanPipelineAnimate.create_pipeline(
                mesh_device=self.ttnn_device,
                checkpoint_name=checkpoint_name,
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

    @log_execution_time(f"{dit_runner_log_map[get_settings().model_runner]} inference")
    def run(self, requests: list[VideoGenerateRequest]):
        import base64
        from io import BytesIO

        from PIL import Image

        self.logger.info(f"Device {self.device_id}: Running Animate inference")
        request = requests[0]

        # Decode the character image.  reference_image_b64 is a declared field on
        # VideoGenerateRequest; getattr() is used defensively so warmup requests
        # built via model_construct() (which skips field defaults) also work.
        reference_image_b64 = getattr(request, "reference_image_b64", None)
        if reference_image_b64:
            char_pil = Image.open(
                BytesIO(base64.b64decode(reference_image_b64))
            ).convert("RGB")
            self.logger.info(
                f"Device {self.device_id}: character image decoded — {char_pil.size[0]}×{char_pil.size[1]}"
            )
        else:
            self.logger.info(
                f"Device {self.device_id}: no reference_image_b64 — using grey dummy (warmup)"
            )
            char_pil = Image.new("RGB", (832, 480), color=(128, 128, 128))

        # Match resolution to the mesh topology, same convention as TTWan22Runner.
        if tuple(self.pipeline.mesh_device.shape) == (4, 8):
            width, height = 1280, 720
        else:
            width, height = 832, 480

        # Use request.num_frames when provided; fall back to 81 (3.4 s at 24 fps).
        num_frames = getattr(request, "num_frames", None) or 81
        frames = self.pipeline(
            character_image=char_pil,
            prompt=request.prompt or "",
            negative_prompt=request.negative_prompt or "",
            height=height,
            width=width,
            num_frames=num_frames,
            num_inference_steps=request.num_inference_steps,
            guidance_scale=3.0,
            # guidance_scale_2 intentionally omitted: Animate uses boundary_ratio=None
            # (single-transformer mode), and check_inputs raises ValueError if
            # guidance_scale_2 is set without a boundary_ratio.
            seed=int(request.seed or 0),
        )
        self.logger.debug(f"Device {self.device_id}: Animate inference completed")
        return frames

    def get_pipeline_device_params(self):
        # Same fabric configuration as TTWan22Runner.
        device_params = {
            "fabric_config": ttnn.FabricConfig.FABRIC_1D,
        }
        if ttnn.device.is_blackhole():
            device_params["fabric_tensix_config"] = ttnn.FabricTensixConfig.MUX
            device_params["dispatch_core_axis"] = ttnn.device.DispatchCoreAxis.ROW
        elif tuple(self.settings.device_mesh_shape) == (4, 8):
            device_params["fabric_config"] = ttnn.FabricConfig.FABRIC_1D_RING
        return device_params
