# Multi-chip AnimateDiff modes (Off / Remix / Coherent) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the accidental 4-chip AnimateDiff "glitch" into three controllable modes — Off (single chip), Remix (deterministic per-chip variation, stitched), Coherent (continuous N×-longer) — with a UI, resolving issue #21.

**Architecture:** All orchestration/UI/tests live in `app/artgen/generators/animatediff.py` and `app/artgen_panel.py`. A per-chip plan (`list[ChipParams]`) replaces the "same params to every chip" launch. Pure plan-builders and the GIF stitcher are unit-tested; subprocess orchestration is exercised via a pure cmd-builder; the LLM auto-vary is tested with a mocked call. `tt-animatediff` is touched only if Task 1 (a QB2 investigation) approves parallel-Coherent.

**Tech Stack:** Python 3.12 (system `/usr/bin/python3`), pytest, PyGObject/GTK4, PIL (Pillow), the vendored `tt-animatediff` `generate.py` (Blackhole TTNN). Hardware: QB2 = 4× P300c.

## Global Constraints

- Edit only `app/artgen/generators/animatediff.py` and `app/artgen_panel.py` (app side). Any `tt-animatediff` change is Task 1-gated and mirrored into `vendor/tt-animatediff` via the vendor snapshot.
- System python for tests: `/usr/bin/python3 -m pytest`. Non-GTK tests run headless; GTK-widget tests run under `xvfb-run --auto-servernum` and self-skip when no display (repo convention).
- Modes are exactly `off` | `remix` | `coherent`.
- Concurrency cap / frame rule: multi-chip requires `frames % num_chips == 0`; else fall back to single chip with a user note (existing behavior — preserve).
- Never execute generated code; never call GTK from a worker thread — post via `GLib.idle_add`.
- Seed spread formula: `chips[i].seed = base_seed + i * spread` (spread default 1; 0 = identical).
- Coherent default implementation = **sequential latent-chaining** via existing `generate.py --chain-save`/`--chain-from` (Task 1 may upgrade to parallel; see Task 1 gate).
- Auto-vary must fall back to `[base]*n` on any LLM error/shortfall; never block generation.
- Version bump is a **minor** bump (new user-visible feature); Task 9 sets it.

## File Structure

- `app/artgen/generators/animatediff.py` — add `ChipParams` dataclass; `build_remix_plan()`, `build_coherent_segments()`, `_autovary_prompts()` (pure/testable); `_multichip_cmds()` (pure cmd-builder); refactor `_run_multi_chip()` to consume a plan; add `_run_coherent_chain()`; add mode routing in `run_subprocess()`; fix `_stitch_gifs()`.
- `app/artgen_panel.py` — the "🎛 Multi-chip" expander in `_build_controls_page` (animatediff branch); plan collection in `_build_args`; Remix reveal + Auto-vary wiring.
- `tests/test_animatediff_multichip.py` — units for plan-builders, cmd-builder, autovary, stitch, mode routing.
- `tests/test_animatediff_panel_multichip.py` — GTK panel wiring (xvfb, display-guarded).

---

### Task 1: Build Step 0 — QB2 empirical check + parallel-Coherent decision

**Files:**
- Create: `docs/superpowers/notes/2026-07-09-multichip-step0-findings.md` (findings + decision)
- Create (scratch, not committed): a throwaway run harness under the scratchpad

**Interfaces:**
- Produces: a recorded **decision** — `COHERENT_IMPL = "sequential-chain" | "parallel-window"` — that Task 6 consumes. Default is `sequential-chain` unless this task explicitly justifies parallel.

This is an investigation task (hardware + code reading), not TDD.

- [ ] **Step 1: Reset/confirm hardware**

Run: `tt-smi -s` (if it errors, `tt-smi -r` then retry). Expected: 4 healthy `p300c` devices, no ARC-dead sentinels.

- [ ] **Step 2: Run the current 4-chip path, preserving shards**

Write a scratch harness that calls the existing `run_subprocess(..., mode="blackhole", frames=16, steps=4, lightning=True, num_chips=4, device_id=None)` but points the temp shard dir somewhere inspectable (or copy `tmp_dir` before stitch). Minimal, fast settings. Run it on QB2.

- [ ] **Step 3: Compare the 4 shards**

Compare shard GIFs frame-by-frame (e.g. PIL pixel diff or file hash per frame). Record: are chip shards **identical** (glitch is pure stitch/quantization) or **different** (per-device drift)? This is issue #21's definitive check.

- [ ] **Step 4: Read the temporal pipeline for parallel-Coherent feasibility**

Read `~/code/tt-animatediff/animatediff_ttnn/temporal_module.py`, `temporal_attention.py`, `ttnn_motion_pipeline.py`. Judge whether a context-window/frame-offset scheme could let chips render continuous non-overlapping slices. Note the entry (`generation_helpers.load_sd14_ttnn`) and where the frame window / temporal attention is applied.

- [ ] **Step 5: Record findings + decision**

Write `docs/superpowers/notes/2026-07-09-multichip-step0-findings.md` with: shard-comparison result, the drift/stitch conclusion, the pipeline assessment, and an explicit `COHERENT_IMPL = ...` decision with a one-paragraph justification. If `parallel-window`, sketch the exact `generate.py`/pipeline arg needed.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/notes/2026-07-09-multichip-step0-findings.md
git commit -m "docs(animatediff): multi-chip Step-0 findings + Coherent impl decision"
```

---

### Task 2: ChipParams + Remix plan builder

**Files:**
- Modify: `app/artgen/generators/animatediff.py` (add dataclass + `build_remix_plan`)
- Test: `tests/test_animatediff_multichip.py`

**Interfaces:**
- Produces:
  - `@dataclass ChipParams: prompt: str; seed: int; temporal_alpha: float; motion_adapter_alpha: float`
  - `build_remix_plan(*, base_prompt, base_seed, base_temporal_alpha, base_motion_alpha, num_chips, per_chip_prompts=None, seed_spread=1, ramp="none", ramp_lo=0.0, ramp_hi=1.0) -> list[ChipParams]`
    - `per_chip_prompts[i]` empty/None → inherit `base_prompt`.
    - `chips[i].seed = base_seed + i*seed_spread`.
    - `ramp="temporal"` → linearly interpolate `temporal_alpha` from `ramp_lo`→`ramp_hi` across chips; `ramp="motion"` → same for `motion_adapter_alpha`; `ramp="none"` → base values.

- [ ] **Step 1: Write the failing test**

Create `tests/test_animatediff_multichip.py`:

```python
"""Unit tests for multi-chip AnimateDiff plan builders, cmd-builder, autovary,
stitch, and mode routing. Pure logic — no hardware, no GTK."""
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
_AD = Path(__file__).parent.parent / "app" / "artgen" / "generators" / "animatediff.py"


def _load():
    spec = importlib.util.spec_from_file_location("ad_gen", _AD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ad = _load()


class TestRemixPlan:
    def test_seed_spread_and_prompt_inheritance(self):
        plan = ad.build_remix_plan(
            base_prompt="koi pond", base_seed=42,
            base_temporal_alpha=0.35, base_motion_alpha=1.0,
            num_chips=4, per_chip_prompts=["koi dawn", "", None, "koi storm"],
            seed_spread=1, ramp="none",
        )
        assert [c.seed for c in plan] == [42, 43, 44, 45]
        assert [c.prompt for c in plan] == ["koi dawn", "koi pond", "koi pond", "koi storm"]
        assert all(c.temporal_alpha == 0.35 for c in plan)

    def test_seed_spread_zero_gives_identical_seeds(self):
        plan = ad.build_remix_plan(
            base_prompt="x", base_seed=7, base_temporal_alpha=0.3,
            base_motion_alpha=1.0, num_chips=3, seed_spread=0, ramp="none",
        )
        assert [c.seed for c in plan] == [7, 7, 7]

    def test_temporal_ramp_interpolates_across_chips(self):
        plan = ad.build_remix_plan(
            base_prompt="x", base_seed=1, base_temporal_alpha=0.3,
            base_motion_alpha=1.0, num_chips=4, ramp="temporal",
            ramp_lo=0.0, ramp_hi=0.9,
        )
        vals = [round(c.temporal_alpha, 3) for c in plan]
        assert vals == [0.0, 0.3, 0.6, 0.9]        # linspace(0,0.9,4)
        assert all(c.motion_adapter_alpha == 1.0 for c in plan)  # untouched

    def test_motion_ramp_targets_motion_alpha(self):
        plan = ad.build_remix_plan(
            base_prompt="x", base_seed=1, base_temporal_alpha=0.3,
            base_motion_alpha=1.0, num_chips=3, ramp="motion",
            ramp_lo=0.2, ramp_hi=1.0,
        )
        assert [round(c.motion_adapter_alpha, 3) for c in plan] == [0.2, 0.6, 1.0]
        assert all(c.temporal_alpha == 0.3 for c in plan)
```

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_animatediff_multichip.py::TestRemixPlan -q`
Expected: FAIL — `build_remix_plan`/`ChipParams` don't exist.

- [ ] **Step 3: Implement**

In `app/artgen/generators/animatediff.py`, near the top (after imports, before `check_hardware`), add:

```python
from dataclasses import dataclass


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
```

- [ ] **Step 4: Run to verify pass**

Run: `/usr/bin/python3 -m pytest tests/test_animatediff_multichip.py::TestRemixPlan -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/artgen/generators/animatediff.py tests/test_animatediff_multichip.py
git commit -m "feat(animatediff): ChipParams + build_remix_plan (per-chip variation)"
```

---

### Task 3: Fix `_stitch_gifs` (issue #21) + test

**Files:**
- Modify: `app/artgen/generators/animatediff.py` (`_stitch_gifs`)
- Test: `tests/test_animatediff_multichip.py` (add `TestStitch`)

**Interfaces:**
- Consumes: nothing new. Produces: same `_stitch_gifs(shard_paths, out_path) -> bool` signature; now preserves per-frame `duration` and uses a shared palette.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_animatediff_multichip.py`:

```python
class TestStitch:
    def _make_gif(self, path, n, color, duration=80):
        from PIL import Image
        frames = [Image.new("RGB", (8, 8), color) for _ in range(n)]
        frames[0].save(path, save_all=True, append_images=frames[1:],
                       duration=duration, loop=0, format="GIF")

    def test_concatenates_in_order_and_preserves_duration(self, tmp_path):
        a = tmp_path / "a.gif"; b = tmp_path / "b.gif"; out = tmp_path / "out.gif"
        self._make_gif(a, 3, (200, 0, 0), duration=80)
        self._make_gif(b, 2, (0, 0, 200), duration=80)
        assert ad._stitch_gifs([a, b], out) is True
        from PIL import Image
        with Image.open(out) as img:
            assert img.n_frames == 5                       # 3 + 2, concatenated
            img.seek(0)
            assert img.info.get("duration") == 80          # duration preserved
```

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_animatediff_multichip.py::TestStitch -q`
Expected: FAIL — current `_stitch_gifs` drops `duration` (default 100ms), so the duration assert fails.

- [ ] **Step 3: Implement**

Replace the body of `_stitch_gifs` in `app/artgen/generators/animatediff.py` with:

```python
def _stitch_gifs(shard_paths: list[Path], out_path: Path) -> bool:
    """Concatenate GIF frames from multiple chip shards, in chip order.

    Preserves per-frame duration from the source shards and quantizes with a
    single shared palette (avoids per-frame re-quantization banding).
    Returns True on success; leaves out_path untouched on failure.
    """
    try:
        from PIL import Image

        frames: list[Image.Image] = []
        durations: list[int] = []
        for p in shard_paths:
            with Image.open(p) as img:
                default_dur = int(img.info.get("duration", 100) or 100)
                for i in range(getattr(img, "n_frames", 1)):
                    img.seek(i)
                    frames.append(img.copy().convert("RGB"))
                    durations.append(int(img.info.get("duration", default_dur) or default_dur))
        if not frames:
            return False

        # Shared palette: quantize the first frame, apply it to all.
        palette_src = frames[0].quantize(colors=256)
        quantized = [f.quantize(palette=palette_src) for f in frames]

        out_path.parent.mkdir(parents=True, exist_ok=True)
        quantized[0].save(
            out_path,
            save_all=True,
            append_images=quantized[1:],
            duration=durations,
            loop=0,
            format="GIF",
        )
        return True
    except Exception:
        _log.exception("GIF stitch failed")
        return False
```

- [ ] **Step 4: Run to verify pass**

Run: `/usr/bin/python3 -m pytest tests/test_animatediff_multichip.py::TestStitch -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/artgen/generators/animatediff.py tests/test_animatediff_multichip.py
git commit -m "fix(animatediff): _stitch_gifs preserves duration + shared palette (issue #21)"
```

---

### Task 4: Per-chip cmd-builder + `_run_multi_chip` consumes the plan

**Files:**
- Modify: `app/artgen/generators/animatediff.py` (add `_multichip_cmds`, refactor `_run_multi_chip` signature/loop)
- Test: `tests/test_animatediff_multichip.py` (add `TestMultichipCmds`)

**Interfaces:**
- Consumes: `ChipParams` (Task 2), existing `_build_cmd`.
- Produces:
  - `_multichip_cmds(*, script, shard_paths, mode, negative_prompt, frames_per_chip, steps, lightning, lightning_steps, motion_adapter, motion_adapter_skip, chips) -> list[list[str]]` — one `generate.py` argv per chip, each with `chips[i]`'s prompt/seed/temporal_alpha/motion_adapter_alpha and `--device-id i`.
  - `_run_multi_chip(..., chips: list[ChipParams], ...)` — now takes the plan instead of a single prompt/seed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_animatediff_multichip.py`:

```python
class TestMultichipCmds:
    def test_each_chip_gets_its_own_prompt_seed_and_device(self, tmp_path):
        chips = [
            ad.ChipParams(prompt="dawn", seed=42, temporal_alpha=0.1, motion_adapter_alpha=1.0),
            ad.ChipParams(prompt="storm", seed=43, temporal_alpha=0.9, motion_adapter_alpha=0.5),
        ]
        shards = [tmp_path / "s0.gif", tmp_path / "s1.gif"]
        cmds = ad._multichip_cmds(
            script=Path("generate.py"), shard_paths=shards, mode="blackhole",
            negative_prompt="blurry", frames_per_chip=8, steps=4,
            lightning=True, lightning_steps=4, motion_adapter=None,
            motion_adapter_skip=None, chips=chips,
        )
        assert len(cmds) == 2
        def val(cmd, flag):
            return cmd[cmd.index(flag) + 1]
        assert val(cmds[0], "--prompt") == "dawn"
        assert val(cmds[1], "--prompt") == "storm"
        assert val(cmds[0], "--seed") == "42"
        assert val(cmds[1], "--seed") == "43"
        assert val(cmds[0], "--device-id") == "0"
        assert val(cmds[1], "--device-id") == "1"
        assert val(cmds[0], "--temporal-alpha") == "0.1"
        assert val(cmds[1], "--temporal-alpha") == "0.9"
```

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_animatediff_multichip.py::TestMultichipCmds -q`
Expected: FAIL — `_multichip_cmds` does not exist.

- [ ] **Step 3: Implement the cmd-builder**

Add to `app/artgen/generators/animatediff.py` (near `_build_cmd`):

```python
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
    """Build one generate.py argv per chip from the per-chip plan."""
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
```

- [ ] **Step 4: Refactor `_run_multi_chip` to consume `chips`**

Change `_run_multi_chip`'s signature: replace the `prompt`, `seed`, `temporal_alpha`, `motion_adapter_alpha` params with `chips: list[ChipParams]`. Replace its per-chip `_build_cmd(...)` call inside the launch loop with a precomputed list:

```python
    frames_per_chip = frames // num_chips
    tmp_dir = Path(tempfile.mkdtemp(prefix="tt_ad_multi_"))
    shard_paths = [tmp_dir / f"shard_{i}.gif" for i in range(num_chips)]
    cmds = _multichip_cmds(
        script=script, shard_paths=shard_paths, mode=mode,
        negative_prompt=negative_prompt, frames_per_chip=frames_per_chip,
        steps=steps, lightning=lightning, lightning_steps=lightning_steps,
        motion_adapter=motion_adapter, motion_adapter_skip=motion_adapter_skip,
        chips=chips,
    )
    ...
    for chip_idx in range(num_chips):
        cmd = cmds[chip_idx]
        # (rest of the existing launch/drain loop unchanged)
```

Update `run_subprocess`'s call site to build a plan and pass `chips=` (see Task 7). Keep the shared-deadline join and stitch call as-is (the deadline fix from 0.11.0 stays).

- [ ] **Step 5: Run to verify pass**

Run: `/usr/bin/python3 -m pytest tests/test_animatediff_multichip.py::TestMultichipCmds -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/artgen/generators/animatediff.py tests/test_animatediff_multichip.py
git commit -m "feat(animatediff): per-chip cmd-builder; _run_multi_chip consumes ChipParams plan"
```

---

### Task 5: LLM auto-vary prompts

**Files:**
- Modify: `app/artgen/generators/animatediff.py` (add `_autovary_prompts`)
- Test: `tests/test_animatediff_multichip.py` (add `TestAutovary`)

**Interfaces:**
- Produces: `_autovary_prompts(base: str, n: int, call_fn) -> list[str]` — returns exactly `n` prompts. `call_fn(prompt, system=None, max_tokens=None) -> str` (same shape as artgen's call_fn). Parses up to `n` non-empty lines; pads with `base` if short; on any exception returns `[base]*n`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_animatediff_multichip.py`:

```python
class TestAutovary:
    def test_parses_n_lines(self):
        call_fn = lambda *a, **k: "koi at dawn\nkoi in a storm\nkoi at night\nkoi in fog\n"
        out = ad._autovary_prompts("koi pond", 4, call_fn)
        assert out == ["koi at dawn", "koi in a storm", "koi at night", "koi in fog"]

    def test_pads_when_model_returns_too_few(self):
        call_fn = lambda *a, **k: "only one line"
        out = ad._autovary_prompts("base", 3, call_fn)
        assert out == ["only one line", "base", "base"]

    def test_falls_back_to_base_on_error(self):
        def call_fn(*a, **k):
            raise RuntimeError("no LLM")
        assert ad._autovary_prompts("base", 4, call_fn) == ["base"] * 4
```

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_animatediff_multichip.py::TestAutovary -q`
Expected: FAIL — `_autovary_prompts` missing.

- [ ] **Step 3: Implement**

Add to `app/artgen/generators/animatediff.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `/usr/bin/python3 -m pytest tests/test_animatediff_multichip.py::TestAutovary -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/artgen/generators/animatediff.py tests/test_animatediff_multichip.py
git commit -m "feat(animatediff): _autovary_prompts (LLM per-chip prompt variations)"
```

---

### Task 6: Coherent mode (sequential latent-chaining) + segment planner

**Files:**
- Modify: `app/artgen/generators/animatediff.py` (`build_coherent_segments`, `_run_coherent_chain`)
- Test: `tests/test_animatediff_multichip.py` (add `TestCoherent`)

**Note:** Implement per Task 1's `COHERENT_IMPL` decision. Default (this task's code) = **sequential-chain**. If Task 1 chose `parallel-window`, the controller substitutes the pipeline-backed orchestrator but keeps the same `build_coherent_segments` planning + tests.

**Interfaces:**
- Produces:
  - `build_coherent_segments(*, num_segments, frames_per_segment, base_seed) -> list[dict]` — each `{"index", "frames", "seed", "chain_from": bool, "chain_save": bool}`; segment 0 has `chain_from=False, chain_save=True`; middles `chain_from=True, chain_save=True`; last `chain_from=True, chain_save=False`. All share `base_seed`.
  - `_run_coherent_chain(...)` — runs the segments sequentially with `--chain-save`/`--chain-from` temp latents, concatenates via `_stitch_gifs`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_animatediff_multichip.py`:

```python
class TestCoherent:
    def test_segment_chain_flags(self):
        segs = ad.build_coherent_segments(num_segments=4, frames_per_segment=8, base_seed=42)
        assert [s["index"] for s in segs] == [0, 1, 2, 3]
        assert all(s["frames"] == 8 and s["seed"] == 42 for s in segs)
        assert segs[0]["chain_from"] is False and segs[0]["chain_save"] is True
        assert segs[1]["chain_from"] is True and segs[1]["chain_save"] is True
        assert segs[-1]["chain_from"] is True and segs[-1]["chain_save"] is False

    def test_single_segment_has_no_chaining(self):
        segs = ad.build_coherent_segments(num_segments=1, frames_per_segment=8, base_seed=1)
        assert segs[0]["chain_from"] is False and segs[0]["chain_save"] is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_animatediff_multichip.py::TestCoherent -q`
Expected: FAIL — `build_coherent_segments` missing.

- [ ] **Step 3: Implement the planner**

Add to `app/artgen/generators/animatediff.py`:

```python
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
```

- [ ] **Step 4: Implement `_run_coherent_chain`**

Add the orchestrator (sequential; reuses `_build_cmd` with chain args and `_stitch_gifs`):

```python
def _run_coherent_chain(
    *, script, out_path, mode, prompt, negative_prompt, frames, steps, seed,
    temporal_alpha, lightning, lightning_steps, num_segments,
    motion_adapter, motion_adapter_alpha, motion_adapter_skip,
    on_progress, timeout, run_id, env,
) -> "tuple[bool, str]":
    """Run num_segments generate.py passes sequentially, chaining latents, then
    concatenate the segment GIFs into one continuous animation."""
    import tempfile
    frames_per_seg = frames // num_segments
    tmp_dir = Path(tempfile.mkdtemp(prefix="tt_ad_coherent_"))
    segs = build_coherent_segments(num_segments=num_segments,
                                   frames_per_segment=frames_per_seg, base_seed=seed)
    seg_paths: list[Path] = []
    prev_latent: "Path | None" = None
    for s in segs:
        seg_out = tmp_dir / f"seg_{s['index']}.gif"
        latent_out = tmp_dir / f"seg_{s['index']}.pt"
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
        rc, err = _run_one(cmd, timeout=timeout, env=env, run_id=f"{run_id}_seg{s['index']}",
                           on_progress=on_progress)
        if not rc:
            return False, f"coherent segment {s['index']} failed: {err}"
        seg_paths.append(seg_out)
        if s["chain_save"]:
            prev_latent = latent_out
    if not _stitch_gifs(seg_paths, out_path):
        return False, "coherent stitch failed"
    return True, ""
```

Extract the single-process run+drain from the existing single-chip path into a reusable `_run_one(cmd, *, timeout, env, run_id, on_progress) -> tuple[bool, str]` and have both the single-chip path and `_run_coherent_chain` call it (DRY — don't duplicate the Popen/drain block).

- [ ] **Step 5: Run to verify pass**

Run: `/usr/bin/python3 -m pytest tests/test_animatediff_multichip.py::TestCoherent -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/artgen/generators/animatediff.py tests/test_animatediff_multichip.py
git commit -m "feat(animatediff): Coherent mode via sequential latent-chaining"
```

---

### Task 7: Mode routing in `run_subprocess`

**Files:**
- Modify: `app/artgen/generators/animatediff.py` (`run_subprocess` signature + dispatch)
- Test: `tests/test_animatediff_multichip.py` (add `TestModeRouting`)

**Interfaces:**
- Consumes: Tasks 2/4/6.
- Produces: `run_subprocess(..., multichip_mode: str = "off", per_chip_prompts=None, seed_spread=1, ramp="none", ramp_lo=0.0, ramp_hi=1.0, motion_adapter_alpha=1.0, ...)`. Dispatch: `off` → single-chip; `remix` (blackhole, device_id None, num_chips>1, frames divisible) → build plan via `build_remix_plan` then `_run_multi_chip(chips=...)`; `coherent` (same guards) → `_run_coherent_chain(num_segments=num_chips, ...)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_animatediff_multichip.py`:

```python
class TestModeRouting:
    def _common(self, monkeypatch):
        calls = {}
        monkeypatch.setattr(ad, "_run_multi_chip", lambda **k: calls.setdefault("remix", k) or (True, ""))
        monkeypatch.setattr(ad, "_run_coherent_chain", lambda **k: calls.setdefault("coherent", k) or (True, ""))
        monkeypatch.setattr(ad, "check_hardware", lambda: (True, "bh", 4))
        return calls

    def test_remix_routes_to_multi_chip_with_plan(self, monkeypatch, tmp_path):
        calls = self._common(monkeypatch)
        ad.run_subprocess(
            script=Path("g.py"), out_path=tmp_path / "o.gif", mode="blackhole",
            prompt="koi", negative_prompt="", frames=16, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=4, device_id=None,
            multichip_mode="remix", per_chip_prompts=["a", "b", "", ""], seed_spread=1,
        )
        assert "remix" in calls
        chips = calls["remix"]["chips"]
        assert [c.prompt for c in chips] == ["a", "b", "koi", "koi"]
        assert [c.seed for c in chips] == [42, 43, 44, 45]

    def test_coherent_routes_to_chain(self, monkeypatch, tmp_path):
        calls = self._common(monkeypatch)
        ad.run_subprocess(
            script=Path("g.py"), out_path=tmp_path / "o.gif", mode="blackhole",
            prompt="koi", negative_prompt="", frames=16, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=4, device_id=None, multichip_mode="coherent",
        )
        assert "coherent" in calls
        assert calls["coherent"]["num_segments"] == 4

    def test_off_does_not_route_multichip(self, monkeypatch, tmp_path):
        calls = self._common(monkeypatch)
        # single-chip path will try to Popen; patch _run_one to avoid real exec
        monkeypatch.setattr(ad, "_run_one", lambda *a, **k: (True, ""))
        ad.run_subprocess(
            script=Path("g.py"), out_path=tmp_path / "o.gif", mode="blackhole",
            prompt="koi", negative_prompt="", frames=16, steps=4, seed=42,
            temporal_alpha=0.35, num_chips=4, device_id=None, multichip_mode="off",
        )
        assert "remix" not in calls and "coherent" not in calls
```

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_animatediff_multichip.py::TestModeRouting -q`
Expected: FAIL — `run_subprocess` has no `multichip_mode` and doesn't route.

- [ ] **Step 3: Implement dispatch**

Add the new keyword params to `run_subprocess` (defaults keep old callers working) and, in the multi-chip decision block, branch on `multichip_mode`:

```python
    # Replaces the old `use_multi` gate. Multi-chip requires blackhole + no pin
    # + >1 chip + divisible frames; the MODE then chooses remix vs coherent.
    _multi_ok = (
        mode == "blackhole" and device_id is None
        and effective_chips > 1 and frames % effective_chips == 0
        and chain_from is None and chain_save is None
    )
    if _multi_ok and multichip_mode == "remix":
        chips = build_remix_plan(
            base_prompt=prompt, base_seed=seed,
            base_temporal_alpha=temporal_alpha, base_motion_alpha=motion_adapter_alpha,
            num_chips=effective_chips, per_chip_prompts=per_chip_prompts,
            seed_spread=seed_spread, ramp=ramp, ramp_lo=ramp_lo, ramp_hi=ramp_hi,
        )
        return _run_multi_chip(
            script=script, out_path=out_path, mode=mode,
            negative_prompt=negative_prompt, frames=frames, steps=steps,
            lightning=lightning, lightning_steps=lightning_steps,
            num_chips=effective_chips, chips=chips,
            motion_adapter=motion_adapter, motion_adapter_skip=motion_adapter_skip,
            on_progress=on_progress, timeout=timeout, run_id=run_id, env=env,
        )
    if _multi_ok and multichip_mode == "coherent":
        return _run_coherent_chain(
            script=script, out_path=out_path, mode=mode, prompt=prompt,
            negative_prompt=negative_prompt, frames=frames, steps=steps, seed=seed,
            temporal_alpha=temporal_alpha, lightning=lightning,
            lightning_steps=lightning_steps, num_segments=effective_chips,
            motion_adapter=motion_adapter, motion_adapter_alpha=motion_adapter_alpha,
            motion_adapter_skip=motion_adapter_skip,
            on_progress=on_progress, timeout=timeout, run_id=run_id, env=env,
        )
    # else: fall through to single-chip path (off, or guards not met).
```

Ensure the `frames % effective_chips != 0` warning still fires for remix/coherent. Remove the old `use_multi`/`_run_multi_chip(prompt=..., seed=...)` call (superseded).

- [ ] **Step 4: Run to verify pass**

Run: `/usr/bin/python3 -m pytest tests/test_animatediff_multichip.py::TestModeRouting -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/artgen/generators/animatediff.py tests/test_animatediff_multichip.py
git commit -m "feat(animatediff): route run_subprocess by multichip_mode (off/remix/coherent)"
```

---

### Task 8: UI — 🎛 Multi-chip expander (Option A) + args wiring

**Files:**
- Modify: `app/artgen_panel.py` (`_build_controls_page` animatediff branch; `_build_args`; add `_on_ad_autovary`)
- Test: `tests/test_animatediff_panel_multichip.py` (xvfb, display-guarded)

**Interfaces:**
- Consumes: `run_subprocess`'s new kwargs (Task 7).
- Produces: widgets `self._ad_mc_mode` (dropdown Off/Remix/Coherent), `self._ad_mc_prompt_entries` (list, len = detected chips, cap 4 for the UI), `self._ad_mc_seed_spread` (spin), `self._ad_mc_ramp` (dropdown none/temporal/motion), `self._ad_mc_autovary_btn`; a `self._ad_mc_remix_box` revealed only in Remix. `_build_args("animatediff")` adds: `multichip_mode`, `per_chip_prompts`, `seed_spread`, `ramp`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_animatediff_panel_multichip.py`:

```python
import sys
from pathlib import Path
import pytest

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()
except Exception:  # pragma: no cover
    pytest.skip("no GTK display", allow_module_level=True)

import artgen_panel


def _panel():
    return artgen_panel.ArtgenPanel.__new__(artgen_panel.ArtgenPanel)


def test_multichip_widgets_built_and_args_read():
    p = _panel()
    p._build_controls_page("animatediff")
    # default mode is Off
    assert artgen_panel._dd_val(p._ad_mc_mode) == "Off"
    # set Remix + per-chip prompt 0 + seed spread
    p._set_dd(p._ad_mc_mode, "Remix")
    p._ad_mc_prompt_entries[0].set_text("koi at dawn")
    p._ad_mc_seed_spread.set_value(2)
    args = p._build_args("animatediff")
    assert args.multichip_mode == "remix"
    assert args.per_chip_prompts[0] == "koi at dawn"
    assert args.seed_spread == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_animatediff_panel_multichip.py -q`
Expected: FAIL — no `_ad_mc_*` widgets / args.

- [ ] **Step 3: Build the expander**

In `_build_controls_page`, animatediff branch, replace the "Device ID (−1=all)" row's role by adding a "🎛 Multi-chip" expander (keep Device ID inside it as an advanced control). Use the detected chip count for the number of per-chip prompt rows (cap the UI at 4; `check_hardware()` gives N):

```python
            # ── Multi-chip expander (Option A) ────────────────────────────────
            mc_exp = Gtk.Expander(label="🎛 Multi-chip")
            mc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            self._ad_mc_mode = _dd(["Off", "Remix", "Coherent"], "Off")
            mc_box.append(_row("Mode", self._ad_mc_mode))

            # Remix reveal
            self._ad_mc_remix_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            self._ad_mc_remix_box.append(_section_lbl("Per-chip prompt"))
            try:
                _ok, _msg, _n = check_hardware()
            except Exception:
                _n = 4
            n_chips = max(1, min(4, _n or 4))
            self._ad_mc_prompt_entries = []
            for ci in range(n_chips):
                e = Gtk.Entry(); e.set_placeholder_text(f"chip {ci} — inherits base…")
                self._ad_mc_prompt_entries.append(e)
                self._ad_mc_remix_box.append(_row(f"chip {ci}", e))
            self._ad_mc_seed_spread = _spin(0, 16, 1, 1)
            self._ad_mc_ramp = _dd(["none", "temporal", "motion"], "none")
            self._ad_mc_autovary_btn = Gtk.Button(label="✦ Auto-vary from base")
            self._ad_mc_autovary_btn.connect("clicked", lambda _b: self._on_ad_autovary())
            self._ad_mc_remix_box.append(_row("Seed spread", self._ad_mc_seed_spread))
            self._ad_mc_remix_box.append(_row("Ramp", self._ad_mc_ramp))
            self._ad_mc_remix_box.append(self._ad_mc_autovary_btn)
            mc_box.append(self._ad_mc_remix_box)

            def _mc_mode_changed(*_a):
                self._ad_mc_remix_box.set_visible(_dd_val(self._ad_mc_mode) == "Remix")
            self._ad_mc_mode.connect("notify::selected", _mc_mode_changed)
            _mc_mode_changed()
            mc_exp.set_child(mc_box)
            box.append(mc_exp)
```

- [ ] **Step 4: Add `_on_ad_autovary` (threaded LLM call)**

```python
    def _on_ad_autovary(self) -> None:
        """Fill per-chip prompt entries with LLM variations of the base prompt."""
        import threading
        base = self._ad_prompt.get_text().strip() or "a mysterious vision"
        n = len(self._ad_mc_prompt_entries)

        def _bg():
            import artgen
            try:
                base_url, model_id = artgen.detect_artgen_endpoint()
                if not model_id:
                    variations = [base] * n
                else:
                    def call_fn(prompt, system=None, max_tokens=None):
                        text, _ = artgen.call_llm(prompt, model_id, base_url + "/v1",
                                                  max_tokens=max_tokens or 256, system=system)
                        return text
                    from artgen.generators.animatediff import _autovary_prompts
                    variations = _autovary_prompts(base, n, call_fn)
            except Exception:
                variations = [base] * n
            def _apply():
                for e, v in zip(self._ad_mc_prompt_entries, variations):
                    e.set_text(v)
                return False
            GLib.idle_add(_apply)
        threading.Thread(target=_bg, daemon=True).start()
```

- [ ] **Step 5: Wire `_build_args`**

In `_build_args`, animatediff branch, add:

```python
            args.multichip_mode = _dd_val(self._ad_mc_mode).lower()   # off/remix/coherent
            args.per_chip_prompts = [e.get_text() for e in self._ad_mc_prompt_entries]
            args.seed_spread = int(self._ad_mc_seed_spread.get_value())
            args.ramp = _dd_val(self._ad_mc_ramp)
```

And ensure the animatediff worker call (`_run_animatediff` / wherever `run_subprocess` is invoked) forwards these to `run_subprocess(...)`.

- [ ] **Step 6: Run to verify pass**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_animatediff_panel_multichip.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/artgen_panel.py tests/test_animatediff_panel_multichip.py
git commit -m "feat(animatediff): Multi-chip expander UI (Off/Remix/Coherent) + args wiring"
```

---

### Task 9: QB2 hardware smoke, version bump, changelog, PR

**Files:**
- Modify: `VERSION`, `debian/changelog`

- [ ] **Step 1: Full suite (headless-safe + xvfb)**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q`
Expected: all pass except the known 1 environment skip; the new multichip tests included.

- [ ] **Step 2: QB2 smoke — Remix**

Launch a Remix render: 16 frames / 4 chips, per-chip prompts set (2 distinct + 2 inherited), seed spread 1, lightning. Confirm it completes and the output is a stitched 16-frame morph (visibly varied across the four quarters). Record in the report.

- [ ] **Step 3: QB2 smoke — Coherent**

Launch a Coherent render: 16 frames / 4 segments, lightning. Confirm it completes sequentially and plays as one continuous animation. Record.

- [ ] **Step 4: Bump VERSION**

Set `VERSION` to the next minor (from current `0.11.0` → `0.12.0`).

- [ ] **Step 5: Prepend changelog stanza**

```
tt-local-generator (0.12.0) noble; urgency=medium

  * animatediff: multi-chip modes — Off / Remix / Coherent. Remix turns the
    4-chip effect into a controllable feature (per-chip prompts, seed spread,
    temporal/motion ramps, ✦ LLM auto-vary) stitched into a morphing animation;
    Coherent renders a continuous N×-longer clip via sequential latent-chaining.
    New "🎛 Multi-chip" panel expander.
  * animatediff: fix _stitch_gifs — preserve per-frame duration + shared palette
    (resolves issue #21 stitch bugs); add _stitch_gifs / plan-builder tests.

 -- Taylor Singletary <tsingletary@tenstorrent.com>  Thu, 09 Jul 2026 00:00:00 +0000

```

- [ ] **Step 6: Commit, push, open PR**

```bash
git add VERSION debian/changelog
git commit -m "chore: release 0.12.0 — multi-chip AnimateDiff modes"
git push -u origin feat/multichip-animatediff-modes
GH_TOKEN="" gh api repos/tenstorrent/tt-local-generator/pulls -X POST \
  -F title="Multi-chip AnimateDiff modes: Off / Remix / Coherent (v0.12.0)" \
  -F head="feat/multichip-animatediff-modes" -F base="main" -F body=@<pr-body-file>
```
(Use the `gh api` PATCH/POST form — `gh pr create`/`edit` fails on this repo's Projects-classic deprecation. `GH_TOKEN=""` forces keyring auth.)

---

## Self-Review

**Spec coverage:**
- Build Step 0 (empirical + parallel-Coherent decision) → Task 1. ✓
- Per-chip plan data model → Task 2 (`ChipParams`, `build_remix_plan`). ✓
- Remix levers (per-chip prompts, seed spread, ramp, auto-vary) → Tasks 2 (plan), 5 (auto-vary), 8 (UI). ✓
- Stitch fixes (issue #21: duration, palette, no-op alias, test) → Task 3. ✓
- `_run_multi_chip` consumes plan → Task 4. ✓
- Coherent (sequential-chain default; Task 1 may upgrade) → Task 6. ✓
- Mode routing off/remix/coherent → Task 7. ✓
- UI Option A + args + threaded auto-vary (GLib.idle_add) → Task 8. ✓
- Testing: pure units (2,4,5,6,7), stitch (3), GTK wiring (8), hardware smoke (9). ✓
- Frame-divisibility fallback preserved → Task 7 Step 3. ✓
- tt-animatediff change contingent + mirrored to vendor → Task 1 gate + Task 6 note. ✓
- Version minor bump → Task 9. ✓

**Placeholder scan:** No TBD/TODO. Task 1 is a genuine investigation with a concrete deliverable (findings doc + `COHERENT_IMPL`); Task 6 ships the committed default (sequential-chain) as complete code with a controller note for the parallel upgrade. Task 9's PR body references a file the controller supplies at run time (not a code placeholder). Hardware smoke steps (2/3/9) describe exact runs.

**Type consistency:** `ChipParams(prompt, seed, temporal_alpha, motion_adapter_alpha)` used identically in Tasks 2/4/7. `build_remix_plan(...)` kwargs match between Task 2 def and Task 7 call. `_multichip_cmds(...)`/`_run_multi_chip(chips=...)` consistent Tasks 4/7. `build_coherent_segments`/`_run_coherent_chain` consistent Tasks 6/7. `run_subprocess` new kwargs (`multichip_mode`, `per_chip_prompts`, `seed_spread`, `ramp`, `ramp_lo`, `ramp_hi`, `motion_adapter_alpha`) match UI `_build_args` outputs (Task 8) and routing (Task 7). Widget names `_ad_mc_*` consistent Task 8 ↔ its test.
```
