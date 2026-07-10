# Multi-chip AnimateDiff modes: Off / Remix / Coherent (design)

Date: 2026-07-09
Status: approved (design), pending implementation plan
Related: issue #21 (multi-chip may dupe frames / stitch bugs)

## Summary

Today's 4-chip AnimateDiff path launches N `generate.py` processes with the
**identical** prompt, seed, and frame count (only `--device-id` differs).
`generate.py` has no frame-offset knob, so every chip renders the same clip and
`_stitch_gifs` concatenates them — issue #21's "duped frames." The *glitchy,
blended* result users love is an emergent artifact: the shared seed gives a
common base composition while concurrent execution on the shared Blackhole mesh
drifts each chip's output, and the shards are concatenated. It is currently
**non-reproducible**.

This feature turns that accident into a real, controllable capability with three
multi-chip modes:

- **Off** — single chip, no stitch (existing behavior).
- **Remix** — the glitch as a *feature*: deterministic per-chip variation
  (prompts, seed spread, parameter ramps, LLM auto-variations), stitched into a
  morphing N-frame animation. Reproducible and dialable.
- **Coherent** — one continuous N×-longer animation. Implementation
  (parallel via a pipeline change vs. sequential latent-chaining) is **decided by
  Build Step 0** (see below).

Also fixes issue #21's stitch bugs and its `_stitch_gifs` test gap.

## Goals / non-goals

Goals:
- Make the multi-chip glitch reproducible and exploitable (Remix).
- Offer a continuous long-animation mode (Coherent).
- A clear UI (Option A) to choose mode and drive per-chip variation.
- Resolve issue #21 (intentional behavior + stitch fixes + tests).

Non-goals:
- No change to single-chip / cpu / sim generation behavior.
- No promise of *parallel* Coherent until Step 0 proves it feasible (fallback is
  sequential-chain).
- No new video model; this is orchestration + UI + a possible pipeline knob.

## Repos and convergence

- **tt-local-generator** (app): all orchestration, UI, stitch fixes, tests.
- **tt-animatediff** (`~/code/tt-animatediff`, v0.9.0, vendored into the app):
  changed only if Build Step 0 shows a pipeline knob is warranted (parallel
  Coherent, or a determinism fix). Any change is mirrored into the app's
  `vendor/tt-animatediff` snapshot so the two converge.

## Build Step 0 — empirical check + parallel-Coherent investigation (GATES the rest)

Runs on the QB2 (4× P300c confirmed). Two outputs:

1. **Reproduce/confirm the glitch.** Run the current 4-chip path (16 frames / 4
   chips / `--lightning` / low steps), preserve the 4 shard GIFs, and compare
   them (frames 0–7 vs 8–15 vs …). Confirms whether the effect is per-device
   *drift* (shards differ) or identical clips (effect is purely in the stitch).
   Establishes the baseline Remix must reproduce deterministically.
2. **Probe parallel-Coherent feasibility.** Inspect the temporal-attention /
   context structure in `animatediff_ttnn/temporal_module.py`,
   `temporal_attention.py`, and `ttnn_motion_pipeline.py` to judge whether a
   context-window / frame-offset scheme could let chips render *different
   temporal slices that stay continuous* (parallel Coherent). AnimateDiff's
   motion module attends across the whole frame window, so independent per-chip
   slices are not trivially continuous — the investigation decides if a windowed
   approach is worth a pipeline change.

**Decision gate (recorded in the plan):**
- If parallel-Coherent is feasible at acceptable risk → implement it via a
  tt-animatediff pipeline change (e.g. a `--frame-window`/offset arg) + app
  orchestration.
- Otherwise → **Coherent = sequential latent-chaining** (below), app-only.

Either way the *user-facing* Coherent mode and UI are identical; only the
internal execution differs.

## Core data model — per-chip plan

`_run_multi_chip` currently passes identical params to every chip. Introduce a
**per-chip plan**: `chips: list[ChipParams]`, where `ChipParams` carries
`prompt`, `seed`, `temporal_alpha`, `motion_adapter_alpha` (and any future
per-chip lever). `_run_multi_chip` launches chip *i* by feeding
`chips[i]` to the existing `_build_cmd` (which already accepts all of these as
per-call args). This single refactor enables every Remix lever with no pipeline
change. `ChipParams` is a plain dataclass in `app/artgen/generators/animatediff.py`.

## Modes

### Off
Single chip (existing path). Chosen by Mode=Off in the UI (maps to the current
single-chip / device-pin behavior).

### Remix (parallel)
Build `chips` from base params + variation controls, launch in parallel, stitch
in chip order.

- **Per-chip prompts** — explicit override per chip; empty → inherit base prompt.
- **Seed spread** — `chips[i].seed = base_seed + i * spread` (spread default 1;
  0 = identical seeds = maximal blend/drift).
- **Ramp** (optional, one at a time) — linearly ramp `temporal_alpha` OR
  `motion_adapter_alpha` from a low to a high value across chips.
- **Auto-vary** — `_autovary_prompts(base, n) -> list[str]`: calls the artgen LLM
  via `artgen.detect_artgen_endpoint()` + `artgen.call_llm()` with a prompt that
  asks for *n* themed one-line variations of the base; parses n lines; fills the
  per-chip prompt fields. Falls back to `[base]*n` on any LLM error/shortfall.

Frame constraint: `frames % num_chips == 0` (existing single-chip fallback with a
warning applies otherwise).

### Coherent
One continuous N×-longer animation. Execution per Step 0's gate:
- **Sequential-chain (fallback / MVP):** N segments; segment 0 renders K frames
  and `--chain-save`s its final latents; segment *i+1* `--chain-from`s the
  previous segment (existing generate.py mechanism), renders K frames;
  concatenate. Continuity via latent hand-off. Serial (no multi-chip speedup) —
  documented as continuity-over-speed. App-only; a new
  `_run_coherent_chain(...)` orchestrator.
- **Parallel (if Step 0 approves):** a tt-animatediff pipeline change lets chip
  *i* render its temporal window with shared context; app launches them in
  parallel and stitches. Same UI.

## UI (Option A — approved mockup)

In the AnimateDiff controls (`_build_controls_page`, `name == "animatediff"`),
replace the bare "Device ID (−1 = all)" control with a **"🎛 Multi-chip"
expander**:

- **Mode** — segmented/dropdown control: Off · Remix · Coherent.
- **Remix reveal** (visible only when Mode=Remix):
  - Per-chip prompt rows, one per detected chip (N from `check_hardware()`; the
    field shows "↳ inherits base…" when empty).
  - **Seed spread** — spin/slider (0–N).
  - **Ramp** — dropdown: none · temporal α · motion α.
  - **✦ Auto-vary N prompts from base** — button; runs `_autovary_prompts` in a
    background thread, fills the rows via `GLib.idle_add`.
- **Coherent reveal** — a length readout ("4 chips → 32 frames, continuous").
- Device-ID pinning stays available (advanced) for Off.

Settings persist via the existing settings mechanism. Widget/label vocabulary
matches the surrounding expanders (`_row`, `_dd`, `_spin`, `_check`,
`_section_lbl`). GTK-thread rule: all widget updates from the auto-vary thread go
through `GLib.idle_add`.

## Data flow

Panel → `_build_args("animatediff")` collects `multichip_mode` + the per-chip
plan inputs → `run_subprocess(...)` dispatches:
- Off → single-chip path.
- Remix → `_run_multi_chip(chips=[...])` → parallel shards → `_stitch_gifs` →
  MediaRecord.
- Coherent → `_run_coherent_chain(...)` (or parallel-coherent) → segments →
  concatenate → MediaRecord.

The record's `params` capture the mode and the resolved per-chip plan for
reproducibility.

## Stitch fixes (issue #21)

In `_stitch_gifs`: preserve each source frame's `info["duration"]` (fall back to
a sane default) so playback timing survives; quantize to a single shared palette
instead of per-frame RGBA re-quantization; remove the misleading
`interleaved = all_frames` no-op alias. Add the missing unit test with two
synthetic multi-frame shard GIFs.

## Error handling

- `frames % num_chips != 0` → existing single-chip fallback + user note.
- Auto-vary LLM unavailable/short → fall back to base prompt for the unfilled
  chips; never block generation.
- Coherent sequential-chain: a segment failure aborts with which segment failed;
  partial segments are discarded (no half-stitched output).
- Per-chip launch failure in Remix: existing multi-chip behavior (kill siblings,
  report failed chips) is preserved.

## Testing

- **Pure logic units** (`tests/test_animatediff_multichip.py`): plan building —
  seed spread, ramp interpolation, prompt inheritance (empty → base), frame
  divisibility; `_autovary_prompts` with a mocked `call_llm` (n lines, fallback);
  mode routing (Off/Remix/Coherent select the right orchestrator).
- **Stitch unit**: `_stitch_gifs` over two synthetic shard GIFs — frame count,
  order, and duration preserved (the issue #21 gap).
- **GTK panel wiring** (xvfb, display-guarded): `_build_controls_page("animatediff")`
  creates the multi-chip widgets; `_build_args` reads them into the plan; Remix
  reveal toggles with Mode.
- **Hardware smoke (QB2, verification, not CI):** Step-0 baseline run; one Remix
  render (per-chip prompts + seed spread) confirming a stitched morph; one
  Coherent render confirming continuity. Recorded in the build report.

## Sequencing (single plan, all three modes)

1. Build Step 0 (empirical + parallel-Coherent decision) — gates 5.
2. Per-chip plan refactor + `_stitch_gifs` fixes + their units.
3. Remix orchestration + variation levers + `_autovary_prompts` + units.
4. UI (Option A) + panel-wiring test.
5. Coherent orchestration (impl per Step-0 gate) + units; any tt-animatediff
   change mirrored into `vendor/`.
6. Hardware smoke on QB2; version bump; changelog; PR.

## Open decision (recorded, resolved in Build Step 0)

Coherent execution = parallel (pipeline change) vs sequential-chain (app-only).
Default assumption for planning: **sequential-chain**, upgraded to parallel only
if Step 0 explicitly approves.
