#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
AnimateDiff generator — Blackhole-accelerated GIF generation.

Registered as generator name "animatediff". Unlike LLM-based generators,
this one skips the build_prompt/call_llm pipeline entirely; artgen_panel.py
routes "animatediff" to _run_animatediff() which runs generate_blackhole_v2.py
as a subprocess on the tt-metal Python env.

Phase 2.5 architecture (generate_blackhole_v2.py):
  - TTNN UNet denoising on Blackhole (SD 1.4 spatial denoising, ~15 s/frame P300C)
  - Cross-frame self-attention applied to stacked noise predictions at each step
    (gives genuine temporal coherence without the MotionAdapter TemporalTransformer)
  - temporal_alpha blends pure shared-noise (0.0) towards full cross-frame attention (1.0)
  - VAE decode on CPU (TTNN VAE conv_out OOMs on Blackhole due to L1 grid mismatch)

Hardware requirement: Blackhole device (P100/P150/P300c/QB2). No CPU fallback.
Script location: ~/tt-scratchpad/tt-animatediff/examples/generate_blackhole_v2.py
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable


from artgen import ArtGenerator, register

_TT_METAL = Path.home() / "tt-metal"
_PYTHON = _TT_METAL / "python_env" / "bin" / "python"

# Prefer the copy bundled inside this repo (app/animatediff/).  Fall back to
# the developer scratchpad path so local dev machines that have built
# tt-animatediff from source still work without change.
_BUNDLED_DIR = Path(__file__).resolve().parent.parent.parent / "animatediff"
_SCRATCHPAD_DIR = Path.home() / "tt-scratchpad" / "tt-animatediff"
_SCRIPT_DIR = (
    _BUNDLED_DIR
    if (_BUNDLED_DIR / "examples" / "generate_blackhole_v2.py").exists()
    else _SCRATCHPAD_DIR
)


@register
class AnimateDiffGenerator(ArtGenerator):
    name = "animatediff"
    description = "Blackhole-accelerated animated GIF via TTNN UNet with cross-frame temporal attention"
    output_ext = ".gif"

    def build_prompt(self, args) -> str:
        # Not used — animatediff bypasses the LLM pipeline entirely.
        raise RuntimeError("AnimateDiff does not use build_prompt; route to _run_animatediff()")

    def add_args(self, p) -> None:
        p.add_argument("--prompt", default=None,
                       help="Prompt text (auto-generated via prompt engine if omitted)")
        p.add_argument("--negative-prompt", default="blurry, low quality",
                       dest="negative_prompt", metavar="TEXT")
        p.add_argument("--frames", type=int, default=8,
                       help="Frames to generate (default: 8)")
        p.add_argument("--steps", type=int, default=25,
                       help="Denoising steps (default: 25)")
        p.add_argument("--seed", type=int, default=42,
                       help="Random seed (default: 42; incremented per --count)")
        p.add_argument("--temporal-alpha", type=float, default=0.35,
                       dest="temporal_alpha",
                       help="Cross-frame attention blend 0–1 (default: 0.35)")
        p.add_argument("--count", type=int, default=1,
                       help="Number of GIFs to generate in sequence (default: 1)")

    def default_output(self) -> Path:
        return Path("animatediff.gif")


_TT_SMI_SEARCH = [
    Path.home() / ".tenstorrent-venv" / "bin" / "tt-smi",
    Path("/usr/local/bin/tt-smi"),
    Path("/usr/bin/tt-smi"),
]


def _find_tt_smi() -> str | None:
    """Return the absolute path to tt-smi, checking known venv locations first."""
    import shutil
    for candidate in _TT_SMI_SEARCH:
        if candidate.exists():
            return str(candidate)
    return shutil.which("tt-smi")


def check_hardware() -> tuple[bool, str]:
    """Check whether a Blackhole device is available.

    Returns (ok, message). ok=True means at least one Blackhole device detected.
    Uses tt-smi -s (snapshot mode) to avoid launching the TUI.
    """
    import json
    tt_smi = _find_tt_smi()
    if tt_smi is None:
        return False, "tt-smi not found (expected at ~/.tenstorrent-venv/bin/tt-smi)"
    try:
        result = subprocess.run(
            [tt_smi, "-s"], capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout)
        devices = data.get("device_info", [])
        for dev in devices:
            arch = dev.get("board_info", {}).get("board_type", "").lower()
            if "blackhole" in arch or "p100" in arch or "p300" in arch or "p150" in arch:
                return True, arch
        if devices:
            arch = devices[0].get("board_info", {}).get("board_type", "unknown")
            return False, f"No Blackhole device found (detected: {arch})"
        return False, "No TT hardware detected"
    except Exception as e:
        return False, f"tt-smi check failed: {e}"


def run_subprocess(
    prompt: str,
    out_path: Path,
    frames: int = 8,
    steps: int = 25,
    seed: int = 42,
    negative_prompt: str = "blurry, low quality",
    temporal_alpha: float = 0.35,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Run generate_blackhole_v2.py as a subprocess.

    Streams stdout line-by-line, calling on_progress(line) for each line that
    contains "Frame", "Step", "Generating", or "Loading".

    generate_blackhole_v2.py emits step progress with \\r (carriage return) and
    frame-decode progress with \\n. PYTHONUNBUFFERED=1 ensures both come through
    in real-time; Python's universal-newlines mode (text=True) normalises \\r → \\n.

    Returns (success, error_message). error_message is "" on success.
    """
    script = _SCRIPT_DIR / "examples" / "generate_blackhole_v2.py"
    if not script.exists():
        return False, (
            f"AnimateDiff script not found: {script}\n"
            "Run the AnimateDiff lesson in the Tenstorrent walkthrough to install it."
        )

    if not _PYTHON.exists():
        return False, (
            f"tt-metal Python env not found: {_PYTHON}\n"
            "Run: cd ~/tt-metal && ./build_metal.sh"
        )

    import os
    env = os.environ.copy()
    env["TT_METAL_ARCH_NAME"] = "blackhole"
    env["TT_METAL_HOME"] = str(_TT_METAL)
    # Disable Python output buffering so \r-terminated step lines stream immediately
    env["PYTHONUNBUFFERED"] = "1"

    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(_PYTHON),
        str(script),
        "--prompt", prompt,
        "--negative-prompt", negative_prompt,
        "--frames", str(frames),
        "--steps", str(steps),
        "--seed", str(seed),
        "--temporal-alpha", str(temporal_alpha),
        "--output", str(out_path),
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=str(_SCRIPT_DIR),
        )
        for line in proc.stdout:
            line = line.rstrip()
            if on_progress and ("Frame" in line or "Step" in line or "Generating" in line or "Loading" in line):
                on_progress(line.strip())
        proc.wait()
    except Exception as e:
        return False, f"Subprocess error: {e}"

    if proc.returncode != 0:
        return False, f"generate_blackhole_v2.py exited with rc={proc.returncode}"


    if not out_path.exists():
        return False, "Script exited 0 but no output file was produced"

    return True, ""


def make_gif_thumbnail(gif_path: Path, thumb_path: Path) -> bool:
    """Extract first frame of a GIF as a PNG thumbnail. Returns True on success."""
    try:
        from PIL import Image
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(gif_path) as img:
            img.seek(0)
            frame = img.convert("RGB")
            frame.thumbnail((320, 240))
            frame.save(str(thumb_path), "PNG")
        return True
    except Exception:
        return False
