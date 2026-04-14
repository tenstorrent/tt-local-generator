# SPDX-License-Identifier: Apache-2.0
#
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
#
# Hotpatch delivered via patches/media_server_config/domain/
# Extends VideoGenerateRequest with additional fields for configurable video
# generation parameters, image-to-video support, and Animate-14B reference inputs.
#
# Bound-mounted into the container at:
#   ~/tt-metal/server/domain/video_generate_request.py
#
# CHANGES from upstream:
#   • num_frames: Optional[int] — variable video length for SkyReels/WAN runners
#   • width, height: Optional[int] — output resolution override (64–2048)
#   • guidance_scale: Optional[float] — CFG scale override (0.0–30.0)
#   • image: Optional[str] — base64-encoded or URL image for I2V runners
#   • num_inference_steps: relaxed bounds (ge=1, le=200) to support all runners
#   • reference_image_b64: Optional[str] — character image for Animate-14B
#   • reference_video_b64: Optional[str] — motion video for Animate-14B

from typing import Optional

from domain.base_request import BaseRequest
from pydantic import Field


class VideoGenerateRequest(BaseRequest):
    # Required fields
    prompt: str

    # Optional fields
    negative_prompt: Optional[str] = None
    # Relaxed from upstream (ge=12, le=50) to support all runners including fast/slow variants.
    num_inference_steps: Optional[int] = Field(default=20, ge=1, le=200)
    seed: Optional[int] = None
    # Number of output video frames; None means the runner uses its default.
    # Valid frame counts for SkyReels/WAN: (N-1) % 4 == 0  →  9, 13, 17, 21, 25, 29, 33, …
    num_frames: Optional[int] = None
    # Output resolution override; None means the runner uses its default (e.g. 960×544).
    width: Optional[int] = Field(default=None, ge=64, le=2048)
    height: Optional[int] = Field(default=None, ge=64, le=2048)
    # Classifier-free guidance scale; None means the runner uses its default.
    guidance_scale: Optional[float] = Field(default=None, ge=0.0, le=30.0)
    # Input image for image-to-video runners.  Accepts base64-encoded image data
    # (data URI or raw base64) or an HTTP/HTTPS URL.  None for text-to-video runners.
    image: Optional[str] = None
    # Animate-14B character image (base64-encoded JPEG/PNG).  Sent by api_client
    # as reference_image_b64.  None means the runner uses a gray placeholder.
    reference_image_b64: Optional[str] = None
    # Animate-14B motion video (base64-encoded MP4).  Sent by api_client as
    # reference_video_b64.  None means the runner ignores motion transfer.
    reference_video_b64: Optional[str] = None
