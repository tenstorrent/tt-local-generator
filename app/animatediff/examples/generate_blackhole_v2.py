#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC

"""Shim — delegates to generate.py --mode blackhole.

Phase 2.5 (TTNN UNet + cross-frame temporal attention) is now the default
blackhole mode in generate.py. Kept for backward compatibility.
"""

import sys
from pathlib import Path

sys.argv = [sys.argv[0], "--mode", "blackhole"] + sys.argv[1:]
exec(compile(open(Path(__file__).parent / "generate.py").read(), "generate.py", "exec"))
