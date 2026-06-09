# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
RMBG background-removal utility plugin.

Wraps briaai/RMBG-1.4 via HuggingFace transformers. (RMBG-2.0 is gated; 1.4 is public.)  Inference runs in the
tenstorrent venv python (which has torch + transformers) via subprocess so
the GTK process stays clean.

Falls back gracefully: if the venv python or required packages are missing,
is_available() returns False and remove_background() raises RuntimeError with
an install hint.  plugin_loader never imports plugin.py for utility plugins,
so missing deps do not prevent the app from starting.

In-process usage by the right-click transform menu and remix engine:
    from plugins.rmbg.plugin import remove_background, is_available
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

# The tenstorrent venv has torch, torchvision, transformers, Pillow.
# Fall back to system python if the venv doesn't exist (CI / minimal installs).
_VENV_PYTHON = Path.home() / ".tenstorrent-venv" / "bin" / "python3"
_PYTHON = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable

_HF_REPO = "briaai/RMBG-1.4"  # 2.0 is gated; 1.4 is public and nearly identical quality

_available: bool | None = None  # None = not yet probed


def is_available() -> bool:
    """Return True if torch, torchvision, and transformers are importable."""
    global _available
    if _available is not None:
        return _available
    result = subprocess.run(
        [_PYTHON, "-c",
         "import torch, torchvision; "
         "from transformers import AutoModelForImageSegmentation"],
        capture_output=True, timeout=15,
    )
    _available = result.returncode == 0
    return _available


def remove_background(src: str, dest: str | None = None) -> str:
    """Remove the background from *src* and write a PNG with transparency to *dest*.

    Args:
        src:  Path to the input image (any PIL-readable format).
        dest: Output PNG path.  A temp file is created when omitted.

    Returns:
        Absolute path of the saved output PNG.

    Raises:
        RuntimeError:      If required packages are not installed.
        FileNotFoundError: If *src* does not exist.
    """
    if not is_available():
        raise RuntimeError(
            "rmbg plugin requires torch, torchvision, and transformers in the "
            "tenstorrent venv.  Run: pip install torch torchvision transformers"
        )

    src_path = Path(src)
    if not src_path.exists():
        raise FileNotFoundError(src)

    if dest is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        dest = tmp.name

    script = f"""
import torch
from PIL import Image
from torchvision import transforms
from transformers import AutoModelForImageSegmentation

model = AutoModelForImageSegmentation.from_pretrained(
    {_HF_REPO!r}, trust_remote_code=True, torch_dtype=torch.float32
)
model.eval()

transform = transforms.Compose([
    transforms.Resize((1024, 1024)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
])

image = Image.open({str(src_path)!r}).convert("RGB")
tensor = transform(image).unsqueeze(0)

with torch.no_grad():
    outputs = model(tensor)

# RMBG-1.4: outputs is tuple(list_of_masks, list_of_features).
# outputs[0][0] is already a probability mask in [0, 1] — do NOT sigmoid again.
mask_tensor = outputs[0][0].squeeze()
mask_pil = transforms.ToPILImage()(mask_tensor.to(torch.float32))
mask_pil = mask_pil.resize(image.size)

result = image.copy()
result.putalpha(mask_pil)
result.save({str(dest)!r}, format="PNG")
print({str(dest)!r})
"""

    result = subprocess.run(
        [_PYTHON, "-c", script],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"rmbg inference failed:\n{result.stderr[-800:]}"
        )
    return str(Path(dest).resolve())
