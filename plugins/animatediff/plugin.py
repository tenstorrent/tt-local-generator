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
from artgen import ArtGenerator


class AnimateDiffGenerator(ArtGenerator):
    """MCP-accessible AnimateDiff plugin.

    plugin_loader picks up this class and uses generate_artifact() for
    MCP tools/call. build_prompt() is not used (AnimateDiff uses a subprocess
    pipeline, not an LLM); call_fn is accepted but ignored.
    """

    name = "animatediff"
    description = "Animated GIF generation via TTNN on Blackhole hardware"
    output_ext = ".gif"
    # This is the class artgen's plugin registry actually instantiates for
    # "animatediff" (plugin_loader._load_local_generator loads THIS file, not
    # app/artgen/generators/animatediff.py directly — see that module's own
    # `uses_llm = False`, which this mirrors for the same reason: AnimateDiff
    # generates via a subprocess pipeline, never the chat LLM, so
    # create_mediums.default_mediums()'s `artgen.get("animatediff").uses_llm`
    # lookup must see False here to thread the fact into the Create surface).
    uses_llm = False

    def add_args(self, parser) -> None:
        parser.add_argument("--prompt", default="a candle flame flickering")
        parser.add_argument("--negative-prompt", default="blurry, low quality",
                            dest="negative_prompt")
        parser.add_argument("--frames", type=int, default=8)
        parser.add_argument("--steps", type=int, default=25)
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--temporal-alpha", type=float, default=0.35,
                            dest="temporal_alpha")

    def build_prompt(self, args) -> str:
        raise RuntimeError(
            "AnimateDiffGenerator does not use build_prompt — "
            "generation is handled by generate_artifact() via subprocess."
        )

    def generate_artifact(self, args, call_fn) -> str:  # call_fn unused
        """Run the AnimateDiff subprocess and return the path to the output GIF."""
        ok, msg = check_hardware()
        if not ok:
            raise RuntimeError(f"AnimateDiff requires Blackhole hardware: {msg}")

        out_path = self.default_output()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        success, err = run_subprocess(
            prompt=getattr(args, "prompt", "a candle flame flickering"),
            out_path=out_path,
            frames=getattr(args, "frames", 8),
            steps=getattr(args, "steps", 25),
            seed=getattr(args, "seed", 42),
            negative_prompt=getattr(args, "negative_prompt", "blurry, low quality"),
            temporal_alpha=getattr(args, "temporal_alpha", 0.35),
        )
        if not success:
            raise RuntimeError(err)
        return str(out_path)
