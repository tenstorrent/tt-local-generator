# SPDX-License-Identifier: Apache-2.0
#
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# Hotpatch: extends VideoGenerateRequest with fields needed by SkyReels I2V and Animate runners.

from typing import Optional

from domain.base_request import BaseRequest
from pydantic import Field


class VideoGenerateRequest(BaseRequest):
    # Required fields
    prompt: str

    # Optional fields
    negative_prompt: Optional[str] = None
    num_inference_steps: Optional[int] = Field(default=20, ge=1, le=200)
    seed: Optional[int] = None
    num_frames: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    # SkyReels I2V: conditioning image as base64 data-URI or raw base64
    image: Optional[str] = None
    # Animate: character image and motion reference video as base64
    reference_image_b64: Optional[str] = None
    reference_video_b64: Optional[str] = None
