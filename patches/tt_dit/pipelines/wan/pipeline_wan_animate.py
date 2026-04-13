# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
# WanPipelineAnimate — TT-hardware pipeline for Wan2.2-Animate-14B.
#
# Architecture notes
# ------------------
# Animate-14B is a fine-tune of I2V-A14B. The core transformer and VAE
# architectures are identical; only the checkpoint weights differ. The
# fine-tuning teaches the model to transfer motion onto a supplied character
# image (reference frame).
#
# Differences from I2V-A14B checkpoint:
#   - No transformer_2 subfolder (handled by pipeline_wan.py conditional patch)
#   - Extra state dict keys: face_encoder.*, motion_encoder.*,
#     added_kv_proj.*, image_embedder.* — not present in TTNN model,
#     dropped via strict=False in _prepare_transformer1.
#
# TT hardware strategy
# --------------------
# Treat the character image as the I2V conditioning reference frame (frame_pos=0).
# Delegate all TT-specific encoding and inference to WanPipelineI2V unchanged.
# The reference video parameter is accepted for API compatibility but not used —
# motion is encoded in the fine-tuned weights.

import os

from loguru import logger

from .pipeline_wan_i2v import ImagePrompt, WanPipelineI2V


# Keys present in Animate-14B state dict but absent from TTNN WanTransformer3DModel.
# These are dropped via strict=False and logged for visibility.
# face_adapter.* / face_encoder.* / motion_encoder.*: Animate-specific conditioning modules
# condition_embedder.image_embedder.*: CLIP image feature embedder (not in I2V TTNN model)
# pose_patch_embedding.*: pose conditioning patch embedding
_ANIMATE_ONLY_KEY_PREFIXES = (
    "face_encoder.",
    "face_adapter.",
    "motion_encoder.",
    "added_kv_proj.",
    "image_embedder.",
    "condition_embedder.image_embedder.",
    "pose_patch_embedding.",
)


class WanPipelineAnimate(WanPipelineI2V):
    """
    TT-hardware Animate-14B pipeline.

    Thin subclass of WanPipelineI2V that:
      1. Defaults to the Animate-14B checkpoint.
      2. Loads transformer weights with strict=False (Animate has extra keys
         the TTNN model doesn't implement).
      3. Wraps character_image as ImagePrompt(frame_pos=0) for inference.

    Args (via __call__):
        character_image (PIL.Image): the character to animate.
        reference_video_frames (list[PIL.Image] | None): motion source frames.
            Accepted for API compatibility; not used in v1 (motion is implicit
            in the fine-tuned Animate weights).
        **kwargs: forwarded to WanPipelineI2V.__call__
            (prompt, num_frames, height, width, num_inference_steps, seed, …).
    """

    CHECKPOINT = "Wan-AI/Wan2.2-Animate-14B-Diffusers"

    def __init__(self, *args, **kwargs):
        if "checkpoint_name" not in kwargs:
            kwargs["checkpoint_name"] = os.environ.get(
                "MODEL_WEIGHTS_DIR", self.CHECKPOINT
            )
        super().__init__(*args, **kwargs)

    @staticmethod
    def create_pipeline(*args, **kwargs):
        """Factory: mirrors WanPipelineI2V.create_pipeline but uses Animate checkpoint."""
        from .pipeline_wan import WanPipeline

        if "checkpoint_name" not in kwargs:
            kwargs["checkpoint_name"] = os.environ.get(
                "MODEL_WEIGHTS_DIR", WanPipelineAnimate.CHECKPOINT
            )
        # boundary_ratio=None disables the dual-transformer timestep switch.
        # Animate has no transformer_2, so the default 0.875 threshold would
        # crash with AttributeError on 'NoneType' during the last ~12.5% of steps.
        kwargs.setdefault("boundary_ratio", None)
        return WanPipeline.create_pipeline(
            *args, pipeline_class=WanPipelineAnimate, **kwargs
        )

    def _prepare_transformer1(self):
        """
        Override: load Animate-14B transformer weights with strict=False.

        The Animate checkpoint state dict contains extra keys not present in the
        TTNN WanTransformer3DModel (face_encoder.*, motion_encoder.*, etc.).
        Using strict=False drops these silently; we log counts for visibility.

        Note: bypasses cache.load_model intentionally — the cache layer does not
        support strict=False. This is acceptable for bring-up; a follow-up can
        add strict support to cache.load_model.
        """
        logger.info("Loading Animate-14B transformer weights (strict=False) ...")
        state = self.torch_transformer.state_dict()
        result = self.transformer.load_torch_state_dict(state, strict=False)

        animate_unexpected = [
            k for k in result.unexpected_keys
            if any(k.startswith(p) for p in _ANIMATE_ONLY_KEY_PREFIXES)
        ]
        other_unexpected = [
            k for k in result.unexpected_keys
            if k not in animate_unexpected
        ]

        logger.info(
            f"Transformer loaded: "
            f"{len(result.missing_keys)} missing, "
            f"{len(animate_unexpected)} Animate-only (dropped), "
            f"{len(other_unexpected)} other unexpected"
        )
        if result.missing_keys:
            logger.warning(f"Missing keys (first 5): {result.missing_keys[:5]}")
        if other_unexpected:
            logger.warning(
                f"Unexpected non-Animate keys (first 5): {other_unexpected[:5]}"
            )

    def __call__(self, character_image, reference_video_frames=None, **kwargs):
        """
        Run Animate inference on TT hardware.

        Args:
            character_image: PIL.Image — character to animate.
            reference_video_frames: list[PIL.Image] | None — motion source frames.
                Not used in v1.
            **kwargs: forwarded to WanPipelineI2V.__call__.
        """
        image_prompt = ImagePrompt(image=character_image, frame_pos=0)
        return super().__call__(image_prompt=image_prompt, **kwargs)
