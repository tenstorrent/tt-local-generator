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
import threading
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
        # Multi-chip (run_subprocess already accepts these; flags were missing)
        p.add_argument("--multichip-mode", default="off", dest="multichip_mode",
                       choices=["off", "remix", "coherent"],
                       help="Multi-chip strategy: off (single chip), remix "
                            "(independent per-chip clips, stitched), coherent "
                            "(sequential latent-chained segments). Default: off")
        p.add_argument("--per-chip-prompt", action="append", default=None,
                       dest="per_chip_prompts", metavar="TEXT",
                       help="Per-chip prompt override for --multichip-mode remix "
                            "(repeatable, one per chip in order; falls back to "
                            "--prompt for chips without an override)")
        p.add_argument("--seed-spread", type=int, default=1, dest="seed_spread",
                       help="Per-chip seed increment for remix mode (default: 1)")
        p.add_argument("--ramp", default="none", choices=["none", "temporal", "motion"],
                       help="Interpolate a parameter across chips in remix mode: "
                            "temporal (temporal-alpha) or motion (motion-adapter-alpha). "
                            "Default: none")
        p.add_argument("--ramp-lo", type=float, default=0.0, dest="ramp_lo",
                       help="Ramp low endpoint (default: 0.0)")
        p.add_argument("--ramp-hi", type=float, default=1.0, dest="ramp_hi",
                       help="Ramp high endpoint (default: 1.0)")
        p.add_argument("--stitch-order", default="interleave", dest="stitch_order",
                       choices=["interleave", "concatenate"],
                       help="How remix-mode per-chip clips are combined into the "
                            "final GIF (default: interleave)")
        # Prompt travel / looping (flags accepted here; behavior lands in later tasks)
        p.add_argument("--prompt-schedule", action="append", default=None,
                       dest="prompt_schedule", metavar="FRAME:PROMPT",
                       help="Keyframe a prompt change at a given frame index "
                            "(repeatable, e.g. --prompt-schedule 0:'spring meadow' "
                            "--prompt-schedule 16:'snowfall'). Prompt travel between "
                            "keyframes is implemented in a later task; this flag is "
                            "accepted and forwarded now.")
        p.add_argument("--loop", default="none", choices=["none", "seamless"],
                       help="Post-process the stitched GIF into a seamless loop "
                            "(default: none). Crossfade implementation lands in a "
                            "later task; this flag is accepted and forwarded now.")

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
    """Combine GIF frames from multiple chip/segment shards into a single output GIF.

    Shards are combined in one of two orderings — this makes no assumption
    about temporal frame-slicing. Each shard is an independent render (its
    own chip's clip in Remix mode, or its own segment in Coherent mode), not
    a contiguous slice of one longer clip:
      - interleave=False (default): shards are concatenated in the given
        order — shard 0's frames in full, then shard 1's, etc. Used by
        Coherent mode (segments play back-to-back to form one continuous
        clip) and by Remix mode when `stitch_order="concatenate"`.
      - interleave=True: frames are round-robined across shards (shard0[0],
        shard1[0], shard2[0], ..., shard0[1], shard1[1], ...), skipping shards
        once they run out of frames. This produces the "glitchy" look Remix
        mode is named for (`stitch_order="interleave"`, the default there) —
        each output frame hops between distinct per-chip renders.

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


def _run_one(
    cmd: "list[str]",
    *,
    timeout: int,
    env: dict,
    run_id: str,
    on_progress: Callable[[str], None] | None,
) -> "tuple[bool, str]":
    """Run one generate.py subprocess to completion, draining its stdout.

    This is the single-process run+drain+timeout block shared by the
    single-chip path in run_subprocess() and each per-segment invocation in
    _run_coherent_chain(). Extracted so there is exactly one Popen/drain
    implementation instead of duplicating it per caller.

    Writes a run log to `_LOG_DIR / f"run_{run_id}.log"` (callers that need
    the traditional `run_{run_id}_{out_path.stem}.log` naming — the
    single-chip path — pass a run_id that already has the stem folded in).
    Streams matching lines to on_progress the same way the original
    single-chip path did (Frame/Step/Generating/Loading/chain/adapter/
    lightning/Error/Traceback/fatal/ARC).

    Does NOT check whether the expected output file was produced — that is
    caller-specific (single-chip checks out_path; the coherent chain relies
    on _stitch_gifs failing if a segment's GIF is missing) so it stays out
    of this shared helper.

    Returns (success, error_message). error_message is "" on success.
    """
    run_log_path = _LOG_DIR / f"run_{run_id}.log"
    _log.info("cmd run_id=%s: %s", run_id, " ".join(str(c) for c in cmd))

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

    return True, ""


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
    script: Path | None = None,
    multichip_mode: str = "off",
    per_chip_prompts: list[str] | None = None,
    seed_spread: int = 1,
    ramp: str = "none",
    ramp_lo: float = 0.0,
    ramp_hi: float = 1.0,
    stitch_order: str = "interleave",
    prompt_schedule: list | None = None,
    loop: str = "none",
) -> tuple[bool, str]:
    """Run the unified generate.py, optionally spreading work across Blackhole chips.

    Multi-chip strategy: N separate processes, one per chip (--device-id 0..N-1),
    coordinated per `multichip_mode` — there is no single "consecutive frame
    slice" model. Off runs one chip; Remix has each chip render its OWN
    independent clip (different seed/prompt/alpha) which are then stitched
    together (interleaved or concatenated); Coherent runs chips sequentially,
    each rendering the full frame count and latent-chaining from the previous
    segment, producing one continuous animation N segments longer. See the
    `multichip_mode` breakdown below for details. In every case this is
    process-level parallelism/sequencing — each chip runs a full independent
    denoising run, never tensor-parallelism within a single UNet call.

    Background: the SD demo UNet (wormhole) calls ttnn.to_torch() without a
    mesh_composer in its weight-loading path, so ShardTensorToMesh across a
    multi-chip MeshDevice crashes at model-load time.  The correct multi-chip
    approach is N independent 1×1 MeshDevice processes.  TTNN is also not
    thread-safe, so even the create_submeshes path must run chips sequentially.
    Separate processes are the only way to get true concurrent chip utilisation.

    When mode=="blackhole", device_id is None, and num_chips > 1, `multichip_mode`
    picks WHAT the chips do (guards below must all hold; otherwise this always
    falls back to single-chip regardless of `multichip_mode`):
      - "off"      — ignore num_chips; run the classic single-chip path.
      - "remix"    — each chip renders an independent clip from its own plan
                     entry (see `build_remix_plan`: per_chip_prompts, seed_spread,
                     ramp/ramp_lo/ramp_hi control how chips diverge), then the
                     shard GIFs are combined per `stitch_order` ("interleave"
                     round-robins frames for the classic glitch look;
                     "concatenate" plays each chip's clip back-to-back).
      - "coherent" — chips are NOT used in parallel; instead num_chips becomes
                     the segment count for `_run_coherent_chain`, which runs
                     segments sequentially on one chip, latent-chaining each
                     from the previous for visual continuity. Each segment
                     renders the FULL `frames` count (not frames/num_segments),
                     so the total output is `num_segments * frames` — Coherent
                     produces a continuous animation N× LONGER than a single
                     run, not the same length split into pieces.
    Guards (required for either mode to engage; same as the old `use_multi`
    gate, except the divisibility requirement is remix-only — see below):
      - "remix" additionally requires frames to be divisible by num_chips,
        since remix splits/stitches shard frames sized frames/num_chips each
        (falls back to single-chip if not divisible; coherent has no such
        requirement since every segment renders the full `frames` count)
      - chain_from/chain_save are single-chip only (fall back to single-chip)

    `script`, if given, overrides the auto-resolved generate.py path and skips
    the existence check below (caller's responsibility) — used by tests to
    inject a fake path without touching the filesystem.

    For single-chip runs, cpu/sim modes, or explicit device_id pins: runs a
    single subprocess as before.

    timeout applies to the slowest chip (all processes must finish within it).

    `prompt_schedule` (list of (frame_index, prompt) tuples) and `loop`
    ("none"|"seamless") are accepted here for CLI parity but are inert
    pass-throughs — prompt travel and seamless-loop crossfade are implemented
    in later tasks (6b/6c). They are not yet forwarded to generate.py or any
    post-processing step.

    Returns (success, error_message). error_message is "" on success.
    """
    _ensure_log_handler()
    import tempfile, datetime as _dt

    if script is None:
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

    # ── Decide: multi-chip (remix/coherent) or single-chip ────────────────────
    # Replaces the old `use_multi` gate. Multi-chip requires blackhole + no pin
    # + >1 chip + no chain continuity; `multichip_mode` then chooses remix
    # (independent per-chip clips, stitched) vs coherent (sequential
    # latent-chained segments) vs off (ignore num_chips entirely).
    #
    # The frames-divisible-by-chips requirement is REMIX-ONLY: remix splits
    # `frames` into `frames // num_chips`-sized shards, one per chip, so an
    # uneven split would silently drop frames. Coherent has no such
    # constraint — every segment renders the FULL `frames` count (see
    # `_run_coherent_chain`), so it routes regardless of divisibility.
    effective_chips = num_chips if (num_chips and num_chips > 1) else 1
    _base_ok = (
        mode == "blackhole"
        and device_id is None
        and effective_chips > 1
        # chain continuity only makes sense on a single chip for now
        and chain_from is None
        and chain_save is None
    )
    _remix_divisible = frames % effective_chips == 0
    _multi_ok = _base_ok and (multichip_mode != "remix" or _remix_divisible)

    if _base_ok and multichip_mode == "remix" and not _remix_divisible:
        _log.warning(
            "frames=%d is not divisible by num_chips=%d — falling back to single chip. "
            "Choose a frame count divisible by %d for multi-chip remix: %s",
            frames, effective_chips, effective_chips,
            [effective_chips * k for k in range(1, 9)],
        )
        if on_progress:
            on_progress(
                f"Note: {frames} frames not divisible by {effective_chips} chips — "
                f"running on chip 0 only. Use a multiple of {effective_chips} for full parallelism."
            )

    if _multi_ok and multichip_mode == "remix":
        chips = build_remix_plan(
            base_prompt=prompt,
            base_seed=seed,
            base_temporal_alpha=temporal_alpha,
            base_motion_alpha=motion_adapter_alpha,
            num_chips=effective_chips,
            per_chip_prompts=per_chip_prompts,
            seed_spread=seed_spread,
            ramp=ramp,
            ramp_lo=ramp_lo,
            ramp_hi=ramp_hi,
        )
        return _run_multi_chip(
            script=script,
            out_path=out_path,
            mode=mode,
            chips=chips,
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
            interleave=(stitch_order == "interleave"),
        )

    if _multi_ok and multichip_mode == "coherent":
        return _run_coherent_chain(
            script=script,
            out_path=out_path,
            mode=mode,
            prompt=prompt,
            negative_prompt=negative_prompt,
            frames=frames,
            steps=steps,
            seed=seed,
            temporal_alpha=temporal_alpha,
            lightning=lightning,
            lightning_steps=lightning_steps,
            num_segments=effective_chips,
            motion_adapter=motion_adapter,
            motion_adapter_alpha=motion_adapter_alpha,
            motion_adapter_skip=motion_adapter_skip,
            on_progress=on_progress,
            timeout=timeout,
            run_id=run_id,
            env=env,
        )

    # else: fall through to single-chip path (multichip_mode=="off", or the
    # _multi_ok guards weren't met — e.g. non-divisible frames, a device_id
    # pin, cpu/sim mode, or an active chain_from/chain_save).

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

    # run_id passed to _run_one folds in out_path.stem so the per-run log file
    # keeps its traditional name (run_<run_id>_<stem>.log) — _run_one itself
    # only knows the run_id string handed to it, not out_path.
    single_run_id = f"{run_id}_{out_path.stem}"
    run_log_path = _LOG_DIR / f"run_{single_run_id}.log"

    ok, err = _run_one(
        cmd, timeout=timeout, env=env, run_id=single_run_id, on_progress=on_progress,
    )
    if not ok:
        return False, err

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
    if len(chips) != num_chips:
        raise ValueError(f"chips/num_chips mismatch: {len(chips)} vs {num_chips}")

    import tempfile

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


def build_coherent_segments(*, num_segments: int, frames_per_segment: int, base_seed: int) -> "list[dict]":
    """Plan sequential latent-chained segments for Coherent mode.

    Segment 0 saves latents; each later segment chains from the previous and
    (unless last) saves for the next. All segments share base_seed.
    """
    segs: list[dict] = []
    for i in range(num_segments):
        segs.append({
            "index": i,
            "frames": frames_per_segment,
            "seed": base_seed,
            "chain_from": i > 0,
            "chain_save": i < num_segments - 1,
        })
    return segs


def _run_coherent_chain(
    *,
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
    num_segments: int,
    motion_adapter: str | None,
    motion_adapter_alpha: float,
    motion_adapter_skip: list[str] | None,
    on_progress: Callable[[str], None] | None,
    timeout: int,
    run_id: str,
    env: dict,
) -> "tuple[bool, str]":
    """Run num_segments generate.py passes sequentially, chaining latents via
    --chain-save/--chain-from for visual continuity, then concatenate the
    per-segment GIFs into one continuous animation via _stitch_gifs.

    Unlike the Remix multi-chip path (independent chips, each rendering its
    own unrelated clip), Coherent mode is inherently sequential: segment N
    needs segment N-1's saved latents before it can start, so segments run
    one after another on a single chip rather than in parallel processes.

    Each segment renders the FULL `frames` count — Coherent is a continuous
    animation N segments LONGER than a single run, not the same length
    divided into pieces. Total output frame count is `num_segments * frames`.
    """
    import tempfile

    tmp_dir = Path(tempfile.mkdtemp(prefix="tt_ad_coherent_"))
    segs = build_coherent_segments(num_segments=num_segments,
                                   frames_per_segment=frames, base_seed=seed)
    # Precompute every segment's GIF/latent path up front (mirrors
    # _run_multi_chip's shard_paths) so the finally-block cleanup below can
    # remove them all regardless of which segment (if any) fails partway
    # through the loop — not just the ones that finished successfully.
    seg_paths = [tmp_dir / f"seg_{s['index']}.gif" for s in segs]
    latent_paths = [tmp_dir / f"seg_{s['index']}.pt" for s in segs]

    total_frames = frames * num_segments
    _log.info(
        "coherent run_id=%s segments=%d frames=%d/segment (%d total) tmp=%s",
        run_id, num_segments, frames, total_frames, tmp_dir,
    )

    try:
        prev_latent: "Path | None" = None
        for s in segs:
            seg_out = seg_paths[s["index"]]
            latent_out = latent_paths[s["index"]]
            cmd = _build_cmd(
                script=script, out_path=seg_out, mode=mode, prompt=prompt,
                negative_prompt=negative_prompt, frames=s["frames"], steps=steps,
                seed=s["seed"], temporal_alpha=temporal_alpha,
                lightning=lightning, lightning_steps=lightning_steps, device_id=0,
                chain_from=str(prev_latent) if s["chain_from"] and prev_latent else None,
                chain_save=str(latent_out) if s["chain_save"] else None,
                chain_alpha=0.6,
                motion_adapter=motion_adapter, motion_adapter_alpha=motion_adapter_alpha,
                motion_adapter_skip=motion_adapter_skip,
            )
            if on_progress:
                on_progress(f"Coherent segment {s['index']+1}/{num_segments}…")
            ok, err = _run_one(
                cmd, timeout=timeout, env=env, run_id=f"{run_id}_seg{s['index']}",
                on_progress=on_progress,
            )
            if not ok:
                return False, f"coherent segment {s['index']} failed: {err}"
            if s["chain_save"]:
                prev_latent = latent_out

        if on_progress:
            on_progress(f"All {num_segments} segments done — stitching {total_frames} frames…")

        if not _stitch_gifs(seg_paths, out_path):
            return False, "coherent stitch failed"

        if not out_path.exists():
            return False, "Stitch reported success but output file missing"

        _log.info("coherent run_success run_id=%s segments=%d total_frames=%d out=%s",
                  run_id, num_segments, total_frames, out_path)
        return True, ""
    finally:
        # Best-effort cleanup of per-segment shard GIFs and chained latent
        # (.pt) files, mirroring _run_multi_chip's shard cleanup. This is a
        # finally (not post-loop code) so it runs on BOTH the success path
        # and every early `return False, ...` above — previously this temp
        # dir and its contents were never removed on any path, leaking a
        # `tt_ad_coherent_*` directory of segment GIFs + latents per run.
        for p in seg_paths:
            try: p.unlink(missing_ok=True)
            except Exception: pass
        for p in latent_paths:
            try: p.unlink(missing_ok=True)
            except Exception: pass
        try: tmp_dir.rmdir()
        except Exception: pass


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
