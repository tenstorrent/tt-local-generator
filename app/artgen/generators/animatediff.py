#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
AnimateDiff generator — Blackhole-accelerated GIF generation.

Registered as generator name "animatediff". Unlike LLM-based generators,
this one skips the build_prompt/call_llm pipeline entirely; artgen_panel.py
routes "animatediff" to _run_animatediff() which runs the unified generate.py
as a subprocess on the tt-metal Python env.

Modes (--mode):
  blackhole  — TTNN UNet on Blackhole hardware (Phase 2.5 cross-frame temporal
               attention; Phase 3 with --motion-adapter). Default.
  cpu        — Diffusers AnimateDiff pipeline on CPU/CUDA. Supports Lightning
               distilled weights (--lightning).
  sim        — Software simulator (libttsim_bh.so). For development only.

Hardware requirement for blackhole mode: Blackhole device (P100/P150/P300c/QB2).

Script resolution order:
  1. vendor/tt-animatediff/examples/generate.py   (git submodule, v0.9.0+)
  2. ~/code/tt-animatediff/examples/generate.py   (developer checkout)
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Callable


from artgen import ArtGenerator, register

# Structured log for every animatediff run — written alongside generated GIFs
# so failures are self-contained and don't require a running GUI to diagnose.
# Log level: DEBUG captures all subprocess output; INFO captures run summaries.
_LOG_DIR = Path.home() / ".local" / "share" / "tt-local-generator" / "logs" / "animatediff"

_log = logging.getLogger("animatediff")
_log.setLevel(logging.DEBUG)


def _ensure_log_handler() -> None:
    """Attach a FileHandler the first time a log entry is actually emitted.

    Called at the top of check_hardware() and run_subprocess() — never at
    module import time — so importing this module is always safe for test
    collection, CLI --help, and .deb installs where $HOME may be unusual.
    """
    if _log.handlers:
        return
    log_dir = _LOG_DIR
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_dir = Path("/tmp/tt-local-generator/logs/animatediff")
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            _log.addHandler(logging.NullHandler())
            return
    try:
        _fh = logging.FileHandler(log_dir / "animatediff.log")
        _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
        _log.addHandler(_fh)
    except OSError:
        _log.addHandler(logging.NullHandler())

_TT_METAL = Path.home() / "tt-metal"
_PYTHON = _TT_METAL / "python_env" / "bin" / "python"

# Prefer the copy bundled inside this repo (app/animatediff/ — synced from
# ~/code/tt-animatediff, the canonical source).  Fall back to the canonical
# Resolution order (first match wins):
#   1. vendor/tt-animatediff/  — git submodule pinned to official release tag
#   2. ~/code/tt-animatediff   — developer checkout (canonical source)
# The old app/animatediff/ bundle has been removed in favour of the submodule.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SUBMODULE_DIR = _REPO_ROOT / "vendor" / "tt-animatediff"
_CANONICAL_DIR = Path.home() / "code" / "tt-animatediff"
_SCRIPT_DIR = (
    _SUBMODULE_DIR
    if (_SUBMODULE_DIR / "examples" / "generate.py").exists()
    else _CANONICAL_DIR
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
        p.add_argument("--mode", default="blackhole",
                       choices=["blackhole", "cpu", "sim"],
                       help="Execution backend (default: blackhole)")
        p.add_argument("--frames", type=int, default=8,
                       help="Frames to generate (default: 8)")
        p.add_argument("--steps", type=int, default=25,
                       help="Denoising steps (default: 25)")
        p.add_argument("--seed", type=int, default=42,
                       help="Random seed (default: 42; incremented per --count)")
        p.add_argument("--temporal-alpha", type=float, default=0.35,
                       dest="temporal_alpha",
                       help="Cross-frame attention blend 0–1 (default: 0.35)")
        # Performance / scheduler
        p.add_argument("--lightning", action="store_true",
                       help="Use Euler scheduler (cpu: loads distilled weights; blackhole: solver only)")
        p.add_argument("--lightning-steps", type=int, default=4, choices=[2, 4, 8],
                       dest="lightning_steps",
                       help="Distillation step count for cpu Lightning mode (default: 4)")
        p.add_argument("--device-id", type=int, default=None,
                       dest="device_id",
                       help="Blackhole chip index to pin this run to (default: all chips)")
        # Chain continuity
        p.add_argument("--chain-from", default=None, dest="chain_from", metavar="PATH",
                       help="Load latents from a previous --chain-save run for visual continuity")
        p.add_argument("--chain-save", default=None, dest="chain_save", metavar="PATH",
                       help="Save this run's final latents for use by --chain-from")
        p.add_argument("--chain-alpha", type=float, default=0.6, dest="chain_alpha",
                       help="Chain blend weight 0–1 (default: 0.6)")
        # Phase 3 MotionAdapter
        p.add_argument("--motion-adapter", default=None, dest="motion_adapter",
                       nargs="?", const="guoyww/animatediff-motion-adapter-v1-5-2",
                       metavar="PATH",
                       help="Enable Phase 3 MotionAdapter (blackhole only). "
                            "PATH defaults to HuggingFace cache if omitted.")
        p.add_argument("--motion-adapter-alpha", type=float, default=1.0,
                       dest="motion_adapter_alpha",
                       help="MotionAdapter injection blend 0–1 (default: 1.0, 0=bypass)")
        p.add_argument("--motion-adapter-skip", nargs="*", default=None,
                       dest="motion_adapter_skip",
                       metavar="KEY",
                       help="Injection-point keys to skip (down0..down2 mid up0..up2). "
                            "Skipping up1 up2 is fastest with minimal quality loss.")
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
    """Check whether a Blackhole device is available and log per-chip health.

    Returns (ok, message). ok=True means at least one Blackhole device detected.
    Uses tt-smi -s (snapshot mode) to avoid launching the TUI.

    Also logs per-chip temperature and power so ARC hangs (sentinel 65536°C /
    4294W) are visible in the log before a run starts.
    """
    _ensure_log_handler()
    import json
    tt_smi = _find_tt_smi()
    if tt_smi is None:
        _log.warning("tt-smi not found")
        return False, "tt-smi not found (expected at ~/.tenstorrent-venv/bin/tt-smi)"
    try:
        result = subprocess.run(
            [tt_smi, "-s"], capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout)
        devices = data.get("device_info", [])
        found_blackhole = False
        arch_str = "unknown"
        for i, dev in enumerate(devices):
            bi = dev.get("board_info", {})
            telem = dev.get("telemetry", {})
            arch = bi.get("board_type", "").lower()
            temp = telem.get("asic_temperature", "?")
            power = telem.get("power", "?")
            bus = bi.get("bus_id", "?")
            # Sentinel values indicate ARC firmware hang (see bug-report-arc-hang-chip3.md)
            temp_val = float(temp) if temp not in ("?", None) else 0
            arc_dead = temp_val > 1000
            _log.info("chip%d %s: temp=%s°C power=%sW%s",
                      i, bus, temp, power, " *** ARC DEAD (sentinel values) ***" if arc_dead else "")
            if arc_dead:
                _log.warning("chip%d ARC appears hung — sentinel temp/power values. "
                             "AC power cycle required to recover.", i)
            if "blackhole" in arch or "p100" in arch or "p300" in arch or "p150" in arch:
                found_blackhole = True
                arch_str = arch
        if found_blackhole:
            return True, arch_str
        if devices:
            arch = devices[0].get("board_info", {}).get("board_type", "unknown")
            _log.warning("No Blackhole device (detected: %s)", arch)
            return False, f"No Blackhole device found (detected: {arch})"
        _log.warning("No TT hardware detected by tt-smi")
        return False, "No TT hardware detected"
    except Exception as e:
        _log.exception("tt-smi check failed")
        return False, f"tt-smi check failed: {e}"


def run_subprocess(
    prompt: str,
    out_path: Path,
    mode: str = "blackhole",
    frames: int = 8,
    steps: int = 25,
    seed: int = 42,
    negative_prompt: str = "blurry, low quality",
    temporal_alpha: float = 0.35,
    lightning: bool = False,
    lightning_steps: int = 4,
    device_id: int | None = None,
    chain_from: str | None = None,
    chain_save: str | None = None,
    chain_alpha: float = 0.6,
    motion_adapter: str | None = None,
    motion_adapter_alpha: float = 1.0,
    motion_adapter_skip: list[str] | None = None,
    on_progress: Callable[[str], None] | None = None,
    timeout: int = 1800,
) -> tuple[bool, str]:
    """Run the unified generate.py as a subprocess.

    Streams stdout line-by-line, calling on_progress(line) for each progress
    line. generate.py emits \\r-terminated step progress and \\n-terminated
    frame/load events; PYTHONUNBUFFERED=1 ensures real-time streaming.

    timeout: maximum wall-clock seconds (default 1800 = 30 min).

    Returns (success, error_message). error_message is "" on success.
    """
    _ensure_log_handler()
    import threading

    script = _SCRIPT_DIR / "examples" / "generate.py"
    if not script.exists():
        return False, (
            f"AnimateDiff generate.py not found: {script}\n"
            "Ensure the vendor/tt-animatediff submodule is initialised (git submodule update --init)."
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
    env["PYTHONUNBUFFERED"] = "1"

    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(_PYTHON),
        str(script),
        "--mode", mode,
        "--prompt", prompt,
        "--negative-prompt", negative_prompt,
        "--frames", str(frames),
        "--steps", str(steps),
        "--seed", str(seed),
        "--temporal-alpha", str(temporal_alpha),
        "--output", str(out_path),
    ]

    if lightning:
        cmd.append("--lightning")
        # --lightning-steps only matters for cpu mode (distilled weights)
        if mode == "cpu":
            cmd += ["--lightning-steps", str(lightning_steps)]

    if device_id is not None:
        cmd += ["--device-id", str(device_id)]

    if chain_from:
        cmd += ["--chain-from", chain_from]
    if chain_save:
        cmd += ["--chain-save", chain_save]
    if chain_from or chain_save:
        cmd += ["--chain-alpha", str(chain_alpha)]

    if motion_adapter is not None:
        # Pass as flag + optional path; empty string means use HF default
        if motion_adapter:
            cmd += ["--motion-adapter", motion_adapter]
        else:
            cmd.append("--motion-adapter")
        cmd += ["--motion-adapter-alpha", str(motion_adapter_alpha)]
        if motion_adapter_skip:
            cmd += ["--motion-adapter-skip"] + list(motion_adapter_skip)

    import datetime as _dt
    run_id = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_log_path = _LOG_DIR / f"run_{run_id}_{out_path.stem}.log"
    _log.info("run_start run_id=%s mode=%s out=%s prompt=%r frames=%d steps=%d seed=%d",
              run_id, mode, out_path, prompt, frames, steps, seed)
    _log.info("cmd: %s", " ".join(str(c) for c in cmd))

    all_output: list[str] = []

    def _drain(proc):
        """Read stdout+stderr; log every line, forward progress lines to caller."""
        with open(run_log_path, "w") as run_log:
            run_log.write(f"# animatediff run {run_id}\n# cmd: {' '.join(str(c) for c in cmd)}\n\n")
            for raw_line in proc.stdout:
                line = raw_line.rstrip()
                all_output.append(line)
                run_log.write(line + "\n")
                run_log.flush()
                _log.debug("[subprocess] %s", line)
                if on_progress and (
                    "Frame" in line or "Step" in line
                    or "Generating" in line or "Loading" in line
                    or "chain" in line.lower() or "adapter" in line.lower()
                    or "lightning" in line.lower()
                    or "Error" in line or "Traceback" in line
                    or "fatal" in line.lower() or "ARC" in line
                ):
                    on_progress(line.strip())

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=str(_SCRIPT_DIR),
        )
    except Exception as e:
        _log.exception("Subprocess launch failed")
        return False, f"Subprocess error: {e}"

    drain_thread = threading.Thread(target=_drain, args=(proc,), daemon=True)
    drain_thread.start()
    drain_thread.join(timeout=timeout)

    if drain_thread.is_alive():
        proc.kill()
        proc.wait()
        drain_thread.join(timeout=5)
        minutes = timeout // 60
        _log.error("run_timeout run_id=%s after %ds", run_id, timeout)
        return False, f"AnimateDiff timed out after {minutes} minutes and was stopped"

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    rc = proc.returncode
    if rc != 0:
        tail = "\n".join(all_output[-20:]) if all_output else "(no output captured)"
        msg = f"generate.py exited with rc={rc}\n\nLast output:\n{tail}\n\nFull log: {run_log_path}"
        _log.error("run_failed run_id=%s rc=%d log=%s", run_id, rc, run_log_path)
        return False, msg

    if not out_path.exists():
        msg = f"Script exited 0 but no output file was produced (log: {run_log_path})"
        _log.error("run_no_output run_id=%s", run_id)
        return False, msg

    _log.info("run_success run_id=%s out=%s", run_id, out_path)
    return True, ""


def make_gif_thumbnail(gif_path: Path, thumb_path: Path) -> bool:
    """Extract first frame of a GIF as a JPEG thumbnail. Returns True on success."""
    try:
        from PIL import Image
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(gif_path) as img:
            img.seek(0)
            frame = img.convert("RGB")
            frame.thumbnail((320, 240))
            frame.save(str(thumb_path), "JPEG", quality=85)
        return True
    except Exception:
        return False
