#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
AnimateDiff generator — Blackhole-accelerated GIF generation.

Registered as generator name "animatediff". Unlike LLM-based generators,
this one skips the build_prompt/call_llm pipeline entirely; artgen_panel.py
routes "animatediff" to _run_animatediff() which runs generate_blackhole.py
as a subprocess on the tt-metal Python env.

Hardware requirement: Blackhole device (P100/P300c). No CPU fallback.
Script location: ~/tt-scratchpad/tt-animatediff/examples/generate_blackhole.py
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable


from artgen import ArtGenerator, register

_TT_METAL = Path.home() / "tt-metal"
_SCRIPT_DIR = Path.home() / "tt-scratchpad" / "tt-animatediff"
_PYTHON = _TT_METAL / "python_env" / "bin" / "python"


@register
class AnimateDiffGenerator(ArtGenerator):
    name = "animatediff"
    description = "Blackhole-accelerated animated GIF via TTNN UNet (Phase 2)"
    output_ext = ".gif"

    def build_prompt(self, args) -> str:
        # Not used — animatediff bypasses the LLM pipeline entirely.
        raise RuntimeError("AnimateDiff does not use build_prompt; route to _run_animatediff()")

    def default_output(self) -> Path:
        return Path("animatediff.gif")


def check_hardware() -> tuple[bool, str]:
    """Check whether a Blackhole device is available.

    Returns (ok, message). ok=True means at least one Blackhole device detected.
    Uses tt-smi -s (snapshot mode) to avoid launching the TUI.
    """
    import json
    try:
        result = subprocess.run(
            ["tt-smi", "-s"], capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout)
        devices = data.get("device_info", [])
        for dev in devices:
            arch = dev.get("board_info", {}).get("board_type", "").lower()
            if "blackhole" in arch or "p100" in arch or "p300" in arch or "p150" in arch:
                return True, arch
        if devices:
            # Hardware present but not Blackhole
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
    on_progress: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Run generate_blackhole.py as a subprocess.

    Streams stdout line-by-line, calling on_progress(line) for each line that
    contains "Frame" (e.g. "  Frame 3/8 done").

    Returns (success, error_message). error_message is "" on success.
    """
    script = _SCRIPT_DIR / "examples" / "generate_blackhole.py"
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

    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(_PYTHON),
        str(script),
        "--prompt", prompt,
        "--negative-prompt", negative_prompt,
        "--frames", str(frames),
        "--steps", str(steps),
        "--seed", str(seed),
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
            if on_progress and ("Frame" in line or "Generating" in line or "Loading" in line):
                on_progress(line.strip())
        proc.wait()
    except Exception as e:
        return False, f"Subprocess error: {e}"

    if proc.returncode != 0:
        return False, f"generate_blackhole.py exited with rc={proc.returncode}"

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
