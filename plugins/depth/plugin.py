# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Depth estimation utility plugin.

Wraps vinvino02/glpn-kitti (GLPN) via HuggingFace transformers.  Produces a
grayscale PNG depth map — brighter = closer to camera.

Inference runs in the tenstorrent venv python via subprocess so the GTK
process stays clean.

Graceful degradation: if venv or packages are missing, is_available() returns
False and estimate_depth() raises RuntimeError.

In-process usage by the right-click transform menu and remix engine:
    from plugins.depth.plugin import estimate_depth, is_available
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

_VENV_PYTHON = Path.home() / ".tenstorrent-venv" / "bin" / "python3"
_PYTHON = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable

_HF_REPO = "vinvino02/glpn-kitti"

_available: bool | None = None


def is_available() -> bool:
    """Return True if torch, transformers, and numpy are importable."""
    global _available
    if _available is not None:
        return _available
    result = subprocess.run(
        [_PYTHON, "-c",
         "import torch, numpy; "
         "from transformers import GLPNImageProcessor, GLPNForDepthEstimation"],
        capture_output=True, timeout=15,
    )
    _available = result.returncode == 0
    return _available


def estimate_depth(src: str, dest: str | None = None) -> str:
    """Estimate per-pixel depth for *src* and save a grayscale depth map PNG.

    Brighter pixels are closer to the camera; darker pixels are farther away.
    The output is normalised to the full [0, 255] range.

    Args:
        src:  Path to the input image.
        dest: Output PNG path.  A temp file is created when omitted.

    Returns:
        Absolute path of the saved depth map PNG.

    Raises:
        RuntimeError:      If required packages are not installed.
        FileNotFoundError: If *src* does not exist.
    """
    if not is_available():
        raise RuntimeError(
            "depth plugin requires torch, transformers, and numpy in the "
            "tenstorrent venv."
        )

    src_path = Path(src)
    if not src_path.exists():
        raise FileNotFoundError(src)

    if dest is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        dest = tmp.name

    script = f"""
import torch, numpy as np
from PIL import Image
from transformers import GLPNImageProcessor, GLPNForDepthEstimation

processor = GLPNImageProcessor.from_pretrained({_HF_REPO!r})
model = GLPNForDepthEstimation.from_pretrained({_HF_REPO!r})
model.eval()

image = Image.open({str(src_path)!r}).convert("RGB")
inputs = processor(images=image, return_tensors="pt")

with torch.no_grad():
    outputs = model(**inputs)
    depth = outputs.predicted_depth.squeeze().cpu().numpy()

d_min, d_max = depth.min(), depth.max()
if d_max > d_min:
    depth_norm = (depth - d_min) / (d_max - d_min)
else:
    depth_norm = np.zeros_like(depth)

# Invert so closer = brighter (more intuitive for compositing)
depth_uint8 = (255 - depth_norm * 255).astype(np.uint8)
depth_pil = Image.fromarray(depth_uint8, mode="L")
depth_pil = depth_pil.resize(image.size, Image.LANCZOS)
depth_pil.save({str(dest)!r}, format="PNG")
print({str(dest)!r})
"""

    result = subprocess.run(
        [_PYTHON, "-c", script],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"depth inference failed:\n{result.stderr[-800:]}")

    return str(Path(dest).resolve())
