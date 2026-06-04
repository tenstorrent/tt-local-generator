#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC

"""Shim — delegates to generate.py --mode sim.

Defaults to 2 frames × 4 steps (manageable first run on the simulator).
Kept for backward compatibility with docs that reference this script by name.
Use generate.py directly for new workflows:
    python examples/generate.py --mode sim --frames 2 --steps 4
"""

import sys
from pathlib import Path

# Inject --mode sim and sim-appropriate defaults when the user didn't pass them
_extra = ["--mode", "sim"]
_argv = sys.argv[1:]
if "--frames" not in _argv and not any(a.startswith("--frames=") for a in _argv):
    _extra += ["--frames", "2"]
if "--steps" not in _argv and not any(a.startswith("--steps=") for a in _argv):
    _extra += ["--steps", "4"]

sys.argv = [sys.argv[0]] + _extra + _argv
exec(compile(open(Path(__file__).parent / "generate.py").read(), "generate.py", "exec"))
