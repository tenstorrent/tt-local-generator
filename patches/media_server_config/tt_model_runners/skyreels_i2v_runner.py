# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
#
# TTSkyReelsI2VRunner — server runner for SkyReels-V2-I2V-14B-540P on
# Tenstorrent Blackhole.
#
# This file is a hotpatch delivered via patches/media_server_config/tt_model_runners/.
# It is bind-mounted into the container at:
#   ~/tt-metal/server/tt_model_runners/skyreels_i2v_runner.py
#
# Design notes
# ============
# SkyReels-V2-I2V-14B-540P is a WAN 2.1-derived image-to-video model.  Unlike
# the 1.3B DF variant, its checkpoint is in raw WAN 2.1 format (not diffusers).
# The TTNN acceleration uses the same WanTransformer3DModel backbone as the
# WAN 2.2 T2V pipeline, configured with model_type="i2v" (in_channels=36).
#
# This runner is intentionally a standalone file (not inside dit_runners.py)
# to avoid triggering the dit_runner_log_map dict lookup at import time.
#
# Configurable inference parameters (via VideoGenerateRequest):
#   prompt              — text description of the video
#   negative_prompt     — negative guidance text (optional)
#   num_inference_steps — denoising steps (default 20; 8 for speed)
#   seed                — random seed
#   num_frames          — output frames; (N-1) % 4 == 0  → 9, 13, 17, … 97, …
#   width               — output width in pixels (default 960)
#   height              — output height in pixels (default 544)
#   guidance_scale      — CFG scale (default 5.0; SkyReels I2V recommended 5-7)
#   image               — base64-encoded conditioning image (PNG/JPEG) or URL

import asyncio
import base64
import io
import os

import ttnn
from config.constants import ModelRunners, ModelServices, SupportedModels
from config.settings import get_settings
from domain.video_generate_request import VideoGenerateRequest
from tt_model_runners.base_metal_device_runner import BaseMetalDeviceRunner
from utils.logger import log_exception_chain


class TTSkyReelsI2VRunner(BaseMetalDeviceRunner):
    """
    Runner for SkyReels-V2-I2V-14B-540P on TT Blackhole hardware.

    Uses SkyReelsI2VPipeline (hotpatch at
    models/tt_dit/pipelines/skyreels_v2/pipeline_skyreels_i2v.py) which wraps
    a TTNN-accelerated WanTransformer3DModel in I2V mode with a diffusers
    SkyReelsV2ImageToVideoPipeline front-end.

    Input contract (VideoGenerateRequest fields used):
      prompt              — text description of the video to generate
      negative_prompt     — negative text guidance (optional)
      num_inference_steps — denoising steps (default 20; 8 recommended for speed)
      seed                — random seed for reproducibility
      num_frames          — output frame count; (N-1) % 4 == 0
      width               — output width in pixels
      height              — output height in pixels
      guidance_scale      — CFG scale
      image               — conditioning image as base64-encoded PNG/JPEG

    Output: numpy array (1, T, H, W, C) in [0, 1], encoded to MP4 by the
    video service layer.
    """

    # SkyReels-V2-I2V-14B-540P default resolution and inference settings.
    # The I2V model targets 540P widescreen (960×544).
    # Valid frame counts: (N-1) % 4 == 0  →  9, 13, 17, …, 93, 97, …
    DEFAULT_WIDTH           = 960
    DEFAULT_HEIGHT          = 544
    DEFAULT_NUM_FRAMES      = 97    # ≈4 seconds @ 24fps
    DEFAULT_GUIDANCE_SCALE  = 6.5  # SkyReels I2V recommended: 5-7; 6.5 balances
                                    # prompt adherence vs naturalness given that
                                    # CLIP cross-attn is dropped in this pipeline.

    # Default negative prompt applied when the request doesn't supply one.
    # Without CLIP cross-attention the model needs stronger CFG guidance to
    # stay on-prompt; an explicit negative prompt helps steer away from the
    # most common failure modes (blur, static frames, anatomical distortion).
    DEFAULT_NEGATIVE_PROMPT = (
        "blurry, low quality, low resolution, static, motionless, frozen, "
        "distorted, disfigured, deformed, ugly, overexposed, washed out, "
        "JPEG artifacts, watermark, text, subtitles, bad anatomy, "
        "disconnected limbs, extra fingers, poorly drawn hands, "
        "flickering, jittery, noise, grain"
    )

    def __init__(self, device_id: str):
        super().__init__(device_id)

    def _configure_fabric(self, updated_device_params):
        """
        Pop fabric_config/fabric_tensix_config from device_params and apply via
        ttnn.set_fabric_config().  Must be called before open_mesh_device().

        Mirrors TTSkyReelsRunner._configure_fabric() exactly.
        """
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
            ttnn.set_fabric_config(fabric_config, reliability_mode, None, fabric_tensix_config)
            return fabric_config
        except Exception as e:
            log_exception_chain(
                self.logger,
                self.device_id,
                "SkyReels I2V fabric configuration failed",
                e,
            )
            raise RuntimeError(f"Fabric configuration failed: {str(e)}") from e

    def create_pipeline(self):
        """
        Create the SkyReels I2V TTNN pipeline using the already-opened mesh device.

        Weight loading (14B params across 4 Blackhole chips via PCIe) takes
        30-60 minutes on first cold start.  The 5400s warmup timeout covers this.
        """
        try:
            from models.tt_dit.pipelines.skyreels_v2.pipeline_skyreels_i2v import (
                SkyReelsI2VPipeline,
            )
        except ImportError as exc:
            raise RuntimeError(
                "SkyReelsI2VPipeline not found in the container.  Ensure that "
                "patches/tt_dit/pipelines/skyreels_v2/pipeline_skyreels_i2v.py "
                "exists and the server was started with --dev-mode."
            ) from exc

        checkpoint_name = os.environ.get(
            "MODEL_WEIGHTS_DIR",
            SupportedModels.SKYREELS_V2_I2V_14B_540P.value,
        )
        self.logger.info(
            f"Device {self.device_id}: Loading SkyReels I2V pipeline from {checkpoint_name}"
        )
        try:
            return SkyReelsI2VPipeline.create_pipeline(
                mesh_device=self.ttnn_device,
                checkpoint_name=checkpoint_name,
            )
        except Exception as e:
            log_exception_chain(
                self.logger,
                self.device_id,
                "SkyReels I2V pipeline creation failed",
                e,
            )
            raise

    async def warmup(self) -> bool:
        """
        Load model weights and run a 2-step warmup pass.

        Uses a 90-minute timeout: the I2V-14B checkpoint (14B params, 14 shards)
        takes significantly longer to load than the 1.3B model.
        """
        self.logger.info(f"Device {self.device_id}: Loading SkyReels I2V model...")

        def distribute_block():
            self.pipeline = self.create_pipeline()

        weights_distribution_timeout = 5400  # 90 minutes
        try:
            await asyncio.wait_for(
                asyncio.to_thread(distribute_block),
                timeout=weights_distribution_timeout,
            )
        except asyncio.TimeoutError:
            self.logger.error(
                f"Device {self.device_id}: SkyReels I2V model loading timed out after "
                f"{weights_distribution_timeout} seconds"
            )
            raise
        except Exception as e:
            log_exception_chain(
                self.logger,
                self.device_id,
                "Exception during SkyReels I2V model loading",
                e,
            )
            raise

        self.logger.info(f"Device {self.device_id}: SkyReels I2V model loaded successfully")

        # 2-step warmup pass (compiles TTNN kernels; no image required for warmup)
        self.run(
            [
                VideoGenerateRequest.model_construct(
                    prompt="A lake at sunrise",
                    negative_prompt="",
                    num_inference_steps=2,
                )
            ]
        )

        self.logger.info(f"Device {self.device_id}: SkyReels I2V warmup completed")
        return True

    def load_weights(self):
        # Weights are loaded inside create_pipeline (TTNN model's load_i2v_weights).
        return False

    def run(self, requests: list[VideoGenerateRequest]):
        """
        Run one I2V denoising pass and return video frames.

        Returns numpy array (1, T, H, W, C) with pixel values in [0, 1], matching
        the format returned by WanPipeline so the video service layer can encode
        it as MP4.

        Image input handling:
          - If request.image is a base64-encoded string, decode it to bytes then PIL.
          - If request.image is a URL, the pipeline will open it.
          - If None (no image provided or warmup), a black conditioning frame is used.
        """
        self.logger.debug(f"Device {self.device_id}: Running SkyReels I2V inference")
        request = requests[0]

        # Parse configurable parameters with safe fallbacks
        num_frames     = int(getattr(request, "num_frames",    None) or self.DEFAULT_NUM_FRAMES)
        width          = int(getattr(request, "width",         None) or self.DEFAULT_WIDTH)
        height         = int(getattr(request, "height",        None) or self.DEFAULT_HEIGHT)
        guidance_scale = float(getattr(request, "guidance_scale", None) or self.DEFAULT_GUIDANCE_SCALE)
        # Use the request negative prompt if provided; fall back to our default.
        # The default steers away from common failure modes (blur, static, distortion)
        # that become more likely when CLIP cross-attention is not active.
        negative_prompt = request.negative_prompt or self.DEFAULT_NEGATIVE_PROMPT

        # Decode the conditioning image.
        # The I2V endpoint populates image_prompts[0].image (not request.image),
        # so check both fields and prefer image_prompts when present.
        raw_image = getattr(request, "image", None)
        if not raw_image:
            image_prompts = getattr(request, "image_prompts", None)
            if image_prompts:
                raw_image = image_prompts[0].image
        image = _decode_image(raw_image, self.logger)
        if image is not None:
            self.logger.info(
                f"Device {self.device_id}: I2V conditioning image decoded — "
                f"{image.size[0]}x{image.size[1]} px"
            )
        else:
            self.logger.warning(
                f"Device {self.device_id}: No conditioning image available — "
                f"using black frame (warmup or decode failure)"
            )

        frames = self.pipeline(
            image=image,
            prompt=request.prompt or "",
            negative_prompt=negative_prompt,
            height=height,
            width=width,
            num_frames=num_frames,
            num_inference_steps=request.num_inference_steps,
            guidance_scale=guidance_scale,
            seed=int(request.seed or 0),
        )
        self.logger.debug(f"Device {self.device_id}: SkyReels I2V inference completed")
        return frames

    def get_pipeline_device_params(self):
        """
        Device parameters for Blackhole hardware.

        Matches TTSkyReelsRunner.get_pipeline_device_params() exactly —
        both models target the same Blackhole mesh topology.
        """
        device_params = {
            "fabric_config": ttnn.FabricConfig.FABRIC_1D,
        }
        if ttnn.device.is_blackhole():
            device_params["fabric_tensix_config"] = ttnn.FabricTensixConfig.MUX
            device_params["dispatch_core_axis"] = ttnn.device.DispatchCoreAxis.ROW
        return device_params


# ---------------------------------------------------------------------------
# Internal helper — image decoding
# ---------------------------------------------------------------------------

def _decode_image(image_field, logger):
    """
    Decode the image field from VideoGenerateRequest into a PIL Image.

    Accepts:
      - None / empty string  →  returns None (pipeline uses black conditioning frame)
      - base64-encoded PNG/JPEG string  →  decoded PIL Image
      - http/https URL string  →  PIL Image fetched from URL
      - PIL Image              →  returned as-is
    """
    if not image_field:
        return None

    if hasattr(image_field, "size"):
        # Already a PIL Image (or numpy array-like with .size attr)
        return image_field

    if isinstance(image_field, str):
        if image_field.startswith(("http://", "https://")):
            try:
                import requests as req
                from PIL import Image
                resp = req.get(image_field, timeout=10)
                resp.raise_for_status()
                return Image.open(io.BytesIO(resp.content)).convert("RGB")
            except Exception as e:
                logger.warning(f"Failed to fetch image URL: {e}. Using black frame.")
                return None

        # Assume base64
        try:
            from PIL import Image
            # Strip optional data-URI prefix (e.g. "data:image/png;base64,...")
            if "," in image_field:
                image_field = image_field.split(",", 1)[1]
            image_bytes = base64.b64decode(image_field)
            return Image.open(io.BytesIO(image_bytes)).convert("RGB")
        except Exception as e:
            logger.warning(f"Failed to decode base64 image: {e}. Using black frame.")
            return None

    return None
