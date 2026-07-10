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
  blackhole  — TTNN UNet on Blackhole hardware (Phase 2.5 temporal attention).
               Multi-chip: N parallel processes, one per chip, frame slices
               concatenated. The SD demo UNet loads weights with to_torch()
               (no mesh_composer), so ShardTensorToMesh is not viable; N
               separate single-chip processes is the correct parallelism model.
               Phase 3 with --motion-adapter (single-chip only).
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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


from artgen import ArtGenerator, register


@dataclass
class ChipParams:
    """Per-chip generation parameters for a multi-chip run."""
    prompt: str
    seed: int
    temporal_alpha: float
    motion_adapter_alpha: float


def _linspace(lo: float, hi: float, n: int) -> "list[float]":
    """n evenly spaced values from lo to hi inclusive (n>=1)."""
    if n <= 1:
        return [lo]
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def build_remix_plan(
    *,
    base_prompt: str,
    base_seed: int,
    base_temporal_alpha: float,
    base_motion_alpha: float,
    num_chips: int,
    per_chip_prompts: "list[str] | None" = None,
    seed_spread: int = 1,
    ramp: str = "none",
    ramp_lo: float = 0.0,
    ramp_hi: float = 1.0,
) -> "list[ChipParams]":
    """Build a per-chip plan for Remix mode. See module docstring / spec."""
    prompts = list(per_chip_prompts or [])
    temporal = _linspace(ramp_lo, ramp_hi, num_chips) if ramp == "temporal" \
        else [base_temporal_alpha] * num_chips
    motion = _linspace(ramp_lo, ramp_hi, num_chips) if ramp == "motion" \
        else [base_motion_alpha] * num_chips
    plan: list[ChipParams] = []
    for i in range(num_chips):
        p = prompts[i] if i < len(prompts) and prompts[i] else base_prompt
        plan.append(ChipParams(
            prompt=p,
            seed=base_seed + i * seed_spread,
            temporal_alpha=temporal[i],
            motion_adapter_alpha=motion[i],
        ))
    return plan


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


def check_hardware() -> tuple[bool, str, int]:
    """Check whether a Blackhole device is available and log per-chip health.

    Returns (ok, message, num_chips).
      ok=True means at least one Blackhole device detected.
      num_chips is the count of healthy Blackhole chips (0 on failure).
    Uses tt-smi -s (snapshot mode) to avoid launching the TUI.

    Also logs per-chip temperature and power so ARC hangs (sentinel 65536°C /
    4294W) are visible in the log before a run starts.
    """
    _ensure_log_handler()
    import json
    tt_smi = _find_tt_smi()
    if tt_smi is None:
        _log.warning("tt-smi not found")
        return False, "tt-smi not found (expected at ~/.tenstorrent-venv/bin/tt-smi)", 0
    try:
        result = subprocess.run(
            [tt_smi, "-s"], capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout)
        devices = data.get("device_info", [])
        blackhole_ids: list[int] = []
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
                continue  # exclude dead chips from the usable count
            if "blackhole" in arch or "p100" in arch or "p300" in arch or "p150" in arch:
                blackhole_ids.append(i)
                arch_str = arch
        if blackhole_ids:
            num = len(blackhole_ids)
            _log.info("found %d healthy Blackhole chip(s): %s", num, blackhole_ids)
            return True, f"{arch_str} ×{num}", num
        if devices:
            arch = devices[0].get("board_info", {}).get("board_type", "unknown")
            _log.warning("No Blackhole device (detected: %s)", arch)
            return False, f"No Blackhole device found (detected: {arch})", 0
        _log.warning("No TT hardware detected by tt-smi")
        return False, "No TT hardware detected", 0
    except Exception as e:
        _log.exception("tt-smi check failed")
        return False, f"tt-smi check failed: {e}", 0


def _build_cmd(
    script: Path,
    out_path: Path,
    mode: str,
    prompt: str,
    negative_prompt: str,
    frames: int,
    steps: int,
    seed: int,
    temporal_alpha: float,
    lightning: bool,
    lightning_steps: int,
    device_id: int | None,
    chain_from: str | None,
    chain_save: str | None,
    chain_alpha: float,
    motion_adapter: str | None,
    motion_adapter_alpha: float,
    motion_adapter_skip: list[str] | None,
) -> list[str]:
    """Assemble the generate.py command list for a single-chip invocation."""
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
        if motion_adapter:
            cmd += ["--motion-adapter", motion_adapter]
        else:
            cmd.append("--motion-adapter")
        cmd += ["--motion-adapter-alpha", str(motion_adapter_alpha)]
        if motion_adapter_skip:
            cmd += ["--motion-adapter-skip"] + list(motion_adapter_skip)
    return cmd


def _multichip_cmds(
    *,
    script: Path,
    shard_paths: "list[Path]",
    mode: str,
    negative_prompt: str,
    frames_per_chip: int,
    steps: int,
    lightning: bool,
    lightning_steps: int,
    motion_adapter: "str | None",
    motion_adapter_skip: "list[str] | None",
    chips: "list[ChipParams]",
) -> "list[list[str]]":
    """Build one generate.py argv per chip from the per-chip plan.

    Each chip gets its own prompt/seed/temporal_alpha/motion_adapter_alpha
    (from `chips[i]`) and `--device-id i`; every other flag (mode, negative
    prompt, frame count, steps, lightning, motion adapter name/skip list) is
    shared across all chips.
    """
    cmds: list[list[str]] = []
    for i, cp in enumerate(chips):
        cmds.append(_build_cmd(
            script=script, out_path=shard_paths[i], mode=mode,
            prompt=cp.prompt, negative_prompt=negative_prompt,
            frames=frames_per_chip, steps=steps, seed=cp.seed,
            temporal_alpha=cp.temporal_alpha,
            lightning=lightning, lightning_steps=lightning_steps,
            device_id=i,
            chain_from=None, chain_save=None, chain_alpha=0.6,
            motion_adapter=motion_adapter,
            motion_adapter_alpha=cp.motion_adapter_alpha,
            motion_adapter_skip=motion_adapter_skip,
        ))
    return cmds


def _autovary_prompts(base: str, n: int, call_fn) -> "list[str]":
    """Ask the LLM for n themed one-line variations of *base*.

    Returns exactly n prompts. On any error or shortfall, unfilled slots use
    *base* — never raises, never blocks generation.
    """
    try:
        system = (
            "You write short, vivid image/animation prompt variations. "
            "Given a base prompt, output exactly N variations, one per line, "
            "no numbering, no commentary — each a single line."
        )
        user = f"Base prompt: {base}\nWrite {n} variations, one per line."
        raw = call_fn(user, system=system, max_tokens=256) or ""
        lines = [ln.strip(" -\t") for ln in raw.splitlines() if ln.strip()]
        out = lines[:n]
    except Exception:
        _log.exception("auto-vary failed; falling back to base prompt")
        out = []
    while len(out) < n:
        out.append(base)
    return out


def _stitch_gifs(shard_paths: list[Path], out_path: Path, interleave: bool = False) -> bool:
    """Combine GIF frames from multiple chip shards into a single output GIF.

    Each shard contains consecutive frames from one chip (chip 0 → frames 0..K-1,
    chip 1 → frames K..2K-1, etc.).

    Ordering:
      - interleave=False (default): frames are concatenated in chip order —
        shard 0's frames, then shard 1's, etc. This is correct when every chip
        renders the *same* seed (deterministic chips produce identical frames
        for identical seeds, so chip order == temporal order of the full clip).
      - interleave=True: frames are round-robined across shards (shard0[0],
        shard1[0], shard2[0], ..., shard0[1], shard1[1], ...), skipping shards
        once they run out of frames. This reproduces the "glitchy" look from
        the original (buggy) stitcher when chips are seeded differently on
        purpose — each output frame hops between distinct per-chip renders.

    Frames are preserved as RGB (not palette-native) so all shards can share a
    single output palette (quantized from a composite sample of every frame),
    avoiding banding from re-quantizing every frame independently. Per-frame
    `duration` is preserved from each source frame, in its position in the
    final ordering.

    Returns True on success. Leaves out_path untouched on failure.
    """
    try:
        from PIL import Image

        # Collect each shard's frames + durations separately so both orderings
        # (concatenate vs interleave) can be built from the same data.
        per_shard_frames: list[list[Image.Image]] = []
        per_shard_durs: list[list[int]] = []
        for p in shard_paths:
            shard_frames: list[Image.Image] = []
            shard_durs: list[int] = []
            with Image.open(p) as img:
                default_dur = int(img.info.get("duration", 100) or 100)
                for i in range(getattr(img, "n_frames", 1)):
                    img.seek(i)
                    shard_frames.append(img.copy().convert("RGB"))
                    shard_durs.append(int(img.info.get("duration", default_dur) or default_dur))
            per_shard_frames.append(shard_frames)
            per_shard_durs.append(shard_durs)

        if interleave:
            import itertools

            # Pair each shard's frames with their own durations up front, so
            # zip_longest can walk both in lockstep without any index lookups
            # back into the per_shard_* lists.
            per_shard_pairs = [
                list(zip(frames, durs))
                for frames, durs in zip(per_shard_frames, per_shard_durs)
            ]
            ordered: list[Image.Image] = []
            ordered_durs: list[int] = []
            for tup in itertools.zip_longest(*per_shard_pairs):
                for pair in tup:
                    if pair is not None:
                        fr, dur = pair
                        ordered.append(fr)
                        ordered_durs.append(dur)
        else:
            ordered = [f for shard in per_shard_frames for f in shard]
            ordered_durs = [d for shard in per_shard_durs for d in shard]

        if not ordered:
            return False

        # Shared palette: quantize a composite sampled from EVERY frame, then
        # apply that one palette to all frames. This keeps colors consistent
        # across shard boundaries (avoids per-frame re-quantization banding)
        # without crushing later frames' colors. Building the palette from
        # frame 0 alone (as a naive "shared palette" might) is a real trap:
        # if frame 0 is a narrow-gamut chip render (e.g. mostly one hue),
        # its quantized palette only contains colors it happened to use, and
        # every subsequent frame gets remapped to its NEAREST available
        # color — silently crushing distinct colors from other chips/frames
        # onto whatever frame 0 contained. Sampling all frames avoids that.
        thumb = (64, 64)
        composite = Image.new("RGB", (thumb[0], thumb[1] * len(ordered)))
        for i, fr in enumerate(ordered):
            composite.paste(fr.resize(thumb), (0, i * thumb[1]))
        palette_src = composite.quantize(colors=256)
        quantized = [f.quantize(palette=palette_src) for f in ordered]

        out_path.parent.mkdir(parents=True, exist_ok=True)
        quantized[0].save(
            out_path,
            save_all=True,
            append_images=quantized[1:],
            duration=ordered_durs,
            loop=0,
            format="GIF",
        )
        return True
    except Exception:
        _log.exception("GIF stitch failed")
        return False


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
    num_chips: int | None = None,
) -> tuple[bool, str]:
    """Run the unified generate.py, using all available Blackhole chips in parallel.

    Multi-chip strategy: N separate processes, one per chip (--device-id 0..N-1).
    Each process generates frames//N consecutive frames from the same prompt and
    seed, then their GIF shards are concatenated in order.  This is frame-level
    data parallelism — each chip runs a full independent denoising run on its
    slice, not tensor-parallelism within a single UNet call.

    Background: the SD demo UNet (wormhole) calls ttnn.to_torch() without a
    mesh_composer in its weight-loading path, so ShardTensorToMesh across a
    multi-chip MeshDevice crashes at model-load time.  The correct multi-chip
    approach is N independent 1×1 MeshDevice processes.  TTNN is also not
    thread-safe, so even the create_submeshes path must run chips sequentially.
    Separate processes are the only way to get true concurrent chip utilisation.

    When mode=="blackhole", device_id is None, and num_chips > 1:
      - frames must be divisible by num_chips (falls back to single-chip if not)
      - chain_from/chain_save are single-chip only (fall back to single-chip)
      - All processes run concurrently; wall-clock time ≈ single-chip time.

    For single-chip runs, cpu/sim modes, or explicit device_id pins: runs a
    single subprocess as before.

    timeout applies to the slowest chip (all processes must finish within it).

    Returns (success, error_message). error_message is "" on success.
    """
    _ensure_log_handler()
    import threading, tempfile, datetime as _dt

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

    run_id = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    _log.info("run_start run_id=%s mode=%s out=%s prompt=%r frames=%d steps=%d seed=%d",
              run_id, mode, out_path, prompt, frames, steps, seed)

    # ── Decide: multi-chip parallel or single-chip ────────────────────────────
    effective_chips = num_chips if (num_chips and num_chips > 1) else 1
    use_multi = (
        mode == "blackhole"
        and device_id is None
        and effective_chips > 1
        and frames % effective_chips == 0
        # chain continuity only makes sense on a single chip for now
        and chain_from is None
        and chain_save is None
    )

    if mode == "blackhole" and device_id is None and effective_chips > 1 and frames % effective_chips != 0:
        _log.warning(
            "frames=%d is not divisible by num_chips=%d — falling back to single chip. "
            "Choose a frame count divisible by %d for multi-chip: %s",
            frames, effective_chips, effective_chips,
            [effective_chips * k for k in range(1, 9)],
        )
        if on_progress:
            on_progress(
                f"Note: {frames} frames not divisible by {effective_chips} chips — "
                f"running on chip 0 only. Use a multiple of {effective_chips} for full parallelism."
            )

    if use_multi:
        # TODO(Task 7): replace this inline single-seed plan with real mode
        # routing (Remix mode will build per-chip prompts/seeds/ramps here).
        # For now, preserve existing single-seed multi-chip behavior: every
        # chip gets the same prompt/temporal_alpha/motion_alpha and a seed
        # spread of 1 (base_seed + chip index), no ramp.
        plan = build_remix_plan(
            base_prompt=prompt,
            base_seed=seed,
            base_temporal_alpha=temporal_alpha,
            base_motion_alpha=motion_adapter_alpha,
            num_chips=effective_chips,
            seed_spread=1,
            ramp="none",
        )
        return _run_multi_chip(
            script=script,
            out_path=out_path,
            mode=mode,
            chips=plan,
            negative_prompt=negative_prompt,
            frames=frames,
            steps=steps,
            lightning=lightning,
            lightning_steps=lightning_steps,
            num_chips=effective_chips,
            motion_adapter=motion_adapter,
            motion_adapter_skip=motion_adapter_skip,
            on_progress=on_progress,
            timeout=timeout,
            run_id=run_id,
            env=env,
            interleave=False,
        )

    # ── Single-chip path ──────────────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = _build_cmd(
        script=script, out_path=out_path, mode=mode,
        prompt=prompt, negative_prompt=negative_prompt,
        frames=frames, steps=steps, seed=seed, temporal_alpha=temporal_alpha,
        lightning=lightning, lightning_steps=lightning_steps,
        device_id=device_id,
        chain_from=chain_from, chain_save=chain_save, chain_alpha=chain_alpha,
        motion_adapter=motion_adapter, motion_adapter_alpha=motion_adapter_alpha,
        motion_adapter_skip=motion_adapter_skip,
    )

    run_log_path = _LOG_DIR / f"run_{run_id}_{out_path.stem}.log"
    _log.info("single-chip cmd: %s", " ".join(str(c) for c in cmd))

    all_output: list[str] = []

    def _drain(proc):
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
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, cwd=str(_SCRIPT_DIR),
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


def _run_multi_chip(
    script: Path,
    out_path: Path,
    mode: str,
    chips: "list[ChipParams]",
    negative_prompt: str,
    frames: int,
    steps: int,
    lightning: bool,
    lightning_steps: int,
    num_chips: int,
    motion_adapter: str | None,
    motion_adapter_skip: list[str] | None,
    on_progress: Callable[[str], None] | None,
    timeout: int,
    run_id: str,
    env: dict,
    interleave: bool = False,
) -> tuple[bool, str]:
    """Spawn one generate.py process per chip in parallel, then stitch results.

    Each chip renders `frames_per_chip` frames from its OWN ChipParams entry
    in `chips` (its own prompt, seed, temporal_alpha, motion_adapter_alpha) —
    chips are NOT temporal slices of one longer clip. `generate.py` has no
    frame-offset concept and every chip is deterministic (same seed → identical
    frames), so this is per-chip variation, not frame-range partitioning.
    The resulting shard GIFs are combined by `_stitch_gifs()`, either
    concatenated in chip order (interleave=False) or round-robined across
    shards (interleave=True) — see `_stitch_gifs` docstring for when each
    ordering is appropriate.

    Each chip gets its own temp output path and log file. All processes are
    launched simultaneously and joined with the shared timeout.
    """
    import threading, tempfile

    frames_per_chip = frames // num_chips
    tmp_dir = Path(tempfile.mkdtemp(prefix="tt_ad_multi_"))
    shard_paths = [tmp_dir / f"shard_{i}.gif" for i in range(num_chips)]

    _log.info(
        "multi-chip run_id=%s chips=%d frames=%d (%d/chip) tmp=%s",
        run_id, num_chips, frames, frames_per_chip, tmp_dir,
    )
    if on_progress:
        on_progress(f"Starting AnimateDiff on {num_chips} chips in parallel ({frames_per_chip} frames each)…")

    procs: list[subprocess.Popen] = []
    drain_threads: list[threading.Thread] = []
    chip_outputs: list[list[str]] = [[] for _ in range(num_chips)]

    cmds = _multichip_cmds(
        script=script, shard_paths=shard_paths, mode=mode,
        negative_prompt=negative_prompt, frames_per_chip=frames_per_chip,
        steps=steps, lightning=lightning, lightning_steps=lightning_steps,
        motion_adapter=motion_adapter, motion_adapter_skip=motion_adapter_skip,
        chips=chips,
    )

    for chip_idx in range(num_chips):
        chip_log = _LOG_DIR / f"run_{run_id}_chip{chip_idx}.log"
        cmd = cmds[chip_idx]
        _log.info("chip%d cmd: %s", chip_idx, " ".join(str(c) for c in cmd))

        output_buf = chip_outputs[chip_idx]

        def _make_drain(proc, buf, chip_i, log_path, cmd_ref):
            def _drain():
                with open(log_path, "w") as lf:
                    lf.write(f"# chip {chip_i} run {run_id}\n# cmd: {' '.join(str(c) for c in cmd_ref)}\n\n")
                    for raw_line in proc.stdout:
                        line = raw_line.rstrip()
                        buf.append(line)
                        lf.write(line + "\n")
                        lf.flush()
                        _log.debug("[chip%d] %s", chip_i, line)
                        if on_progress and (
                            "Frame" in line or "Step" in line
                            or "Generating" in line or "Loading" in line
                            or "Error" in line or "Traceback" in line
                            or "fatal" in line.lower() or "ARC" in line
                        ):
                            on_progress(f"chip{chip_i}: {line.strip()}")
            return _drain

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=env, cwd=str(_SCRIPT_DIR),
            )
        except Exception as e:
            # Kill any already-launched procs before returning
            for p in procs:
                try: p.kill()
                except Exception: pass
            _log.exception("chip%d launch failed", chip_idx)
            return False, f"chip{chip_idx} launch error: {e}"

        procs.append(proc)
        t = threading.Thread(
            target=_make_drain(proc, output_buf, chip_idx, chip_log, cmd),
            daemon=True,
        )
        t.start()
        drain_threads.append(t)

    # Wait for all chips concurrently against a single shared deadline, so the
    # total wall-clock wait is bounded by `timeout` regardless of chip count
    # (joining each with the full `timeout` would compound to timeout*N when
    # multiple chips hang).
    _deadline = time.monotonic() + timeout
    for t in drain_threads:
        t.join(timeout=max(0.0, _deadline - time.monotonic()))

    # Collect exit codes; kill any stragglers.
    failed_chips: list[int] = []
    for chip_idx, (proc, t) in enumerate(zip(procs, drain_threads)):
        if t.is_alive():
            proc.kill()
            proc.wait()
            t.join(timeout=5)
            _log.error("chip%d timed out after %ds", chip_idx, timeout)
            failed_chips.append(chip_idx)
            continue
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        if proc.returncode != 0:
            _log.error("chip%d rc=%d", chip_idx, proc.returncode)
            failed_chips.append(chip_idx)

    if failed_chips:
        minutes = timeout // 60
        msgs = []
        for ci in failed_chips:
            tail = "\n".join(chip_outputs[ci][-10:]) if chip_outputs[ci] else "(no output)"
            msgs.append(f"chip{ci}:\n{tail}")
        return False, (
            f"Multi-chip run failed on chip(s) {failed_chips} "
            f"(timeout={minutes}min):\n\n" + "\n\n".join(msgs)
        )

    # Stitch shard GIFs into final output
    if on_progress:
        on_progress(f"All {num_chips} chips done — stitching {frames} frames…")

    ok = _stitch_gifs(shard_paths, out_path, interleave=interleave)

    # Clean up temp shard files
    for p in shard_paths:
        try: p.unlink(missing_ok=True)
        except Exception: pass
    try: tmp_dir.rmdir()
    except Exception: pass

    if not ok:
        return False, f"GIF stitch failed — shard logs at {_LOG_DIR}"

    if not out_path.exists():
        return False, "Stitch reported success but output file missing"

    _log.info("multi-chip run_success run_id=%s chips=%d out=%s", run_id, num_chips, out_path)
    if on_progress:
        on_progress(f"Done — {frames} frames from {num_chips} chips → {out_path.name}")
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
