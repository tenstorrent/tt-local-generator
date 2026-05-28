#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
AnimateDiff MCP plugin — Blackhole-accelerated GIF generation.

Delegates all implementation to app/artgen/generators/animatediff.py so the
MCP and GUI paths share one canonical implementation. The artgen module owns
the subprocess invocation, logging, hardware check, and path resolution.

MCP schema defined in mcp.json alongside this file.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the app package is on sys.path (plugin_loader sets cwd to repo root,
# but app/ is not always on sys.path when loaded as a plugin).
_APP_DIR = Path(__file__).resolve().parent.parent.parent / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from artgen.generators.animatediff import (
    check_hardware,
    run_subprocess,
    make_gif_thumbnail,
)


def generate_artifact(
    prompt: str,
    out_path: Path,
    *,
    negative_prompt: str = "blurry, low quality",
    frames: int = 16,
    steps: int = 20,
    seed: int = 42,
    temporal_alpha: float = 0.5,
    on_progress=None,
) -> tuple[bool, str]:
    """MCP entry point — delegates to run_subprocess in artgen/generators/animatediff.py."""
    ok, msg = check_hardware()
    if not ok:
        return False, f"AnimateDiff requires Blackhole hardware: {msg}"

    return run_subprocess(
        prompt=prompt,
        out_path=Path(out_path),
        frames=frames,
        steps=steps,
        seed=seed,
        negative_prompt=negative_prompt,
        temporal_alpha=temporal_alpha,
        on_progress=on_progress,
    )
