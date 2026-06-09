# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
BLIP image-captioning utility plugin.

Wraps Salesforce/blip-image-captioning-base via HuggingFace transformers.
Inference runs in the tenstorrent venv python via subprocess so the GTK
process stays clean.

Graceful degradation: if the venv python or transformers are missing,
is_available() returns False and caption_image() raises RuntimeError.

In-process usage by the right-click transform menu and remix engine:
    from plugins.blip.plugin import caption_image, is_available
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

_VENV_PYTHON = Path.home() / ".tenstorrent-venv" / "bin" / "python3"
_PYTHON = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable

_HF_REPO = "Salesforce/blip-image-captioning-base"

_available: bool | None = None


def is_available() -> bool:
    """Return True if torch and transformers are importable."""
    global _available
    if _available is not None:
        return _available
    result = subprocess.run(
        [_PYTHON, "-c",
         "import torch; "
         "from transformers import BlipProcessor, BlipForConditionalGeneration"],
        capture_output=True, timeout=15,
    )
    _available = result.returncode == 0
    return _available


def caption_image(
    src: str,
    prompt: str = "",
    max_new_tokens: int = 50,
) -> str:
    """Generate a natural-language caption for *src*.

    Args:
        src:            Path to the input image.
        prompt:         Optional text prefix (e.g. "a photograph of").
        max_new_tokens: Maximum caption length in tokens.

    Returns:
        Caption string, stripped of leading/trailing whitespace.

    Raises:
        RuntimeError:      If required packages are not installed.
        FileNotFoundError: If *src* does not exist.
    """
    if not is_available():
        raise RuntimeError(
            "blip plugin requires torch and transformers in the tenstorrent venv."
        )

    src_path = Path(src)
    if not src_path.exists():
        raise FileNotFoundError(src)

    script = f"""
import json, torch
from PIL import Image
from transformers import BlipProcessor, BlipForConditionalGeneration

processor = BlipProcessor.from_pretrained({_HF_REPO!r})
model = BlipForConditionalGeneration.from_pretrained({_HF_REPO!r})
model.eval()

image = Image.open({str(src_path)!r}).convert("RGB")
prompt = {prompt!r}
if prompt:
    inputs = processor(image, prompt, return_tensors="pt")
else:
    inputs = processor(image, return_tensors="pt")

with torch.no_grad():
    out = model.generate(**inputs, max_new_tokens={max_new_tokens!r})

caption = processor.decode(out[0], skip_special_tokens=True).strip()
print(json.dumps(caption))
"""

    result = subprocess.run(
        [_PYTHON, "-c", script],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"blip inference failed:\n{result.stderr[-800:]}")

    return json.loads(result.stdout.strip())
