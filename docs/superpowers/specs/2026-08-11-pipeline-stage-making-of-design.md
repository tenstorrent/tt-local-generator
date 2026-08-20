# Pipeline "Stage" — the live making-of (Slice 1) — Design

**Date:** 2026-08-11
**Branch:** `feat/pipeline-editor`
**Status:** Approved (direction), pending spec review → implementation plan

## Problem / goal

The pipeline live run (`LiveRunView`) is the worst offender on the "show the
work as it progresses" axis: it's a **text ticker** — a spinner + a raw phase
string + an elapsed timer per step, with the real hardware story (board resets,
server starts) hidden in a collapsed "Details" log, no per-step artifact ever
shown, no real progress bar, no cancel, and "Step N of M" counting *started* not
done. (Critical review evidence: `pipeline_studio.py:3393` step rows never
preview output; `:3237` the log is collapsed by default; `pipeline_progress.py:68`
counts started; `:3754` back doesn't cancel and dead-ends for Muse-launched
runs.)

**Goal (Slice 1 of the approved "Stage" direction — see
[[project_stage_pipeline_direction]]):** turn the pipeline live run into a
**making-of** — you *watch the machine build your thing*. Each step surfaces its
output as it lands, the "incredible machine" is ambient and honest (the
**tensix-viz** activity + the board-reset/server story in view, not buried), a
real progress spine counts *done*, there's a **Stop**, and leaving is intentional
(no dead-end). This is the project's own canon made literal — *one machine,
everything is a Run*; *see the result the instant it's done*.

**Two things we must KEEP (Taylor, 2026-08-11):**
- The **per-chip AnimateDiff incremental progress** we built (`CreateResultPanel`'s
  `chipN:` live rows, `_pending_chip_box`) — it must appear in the pipeline
  making-of too, not just Create.
- The **tensix-viz** "👁 Watch" hardware viz (`ActivityVizWidget`) — worked into
  the Stage as the ambient machine element, not a separate toggle you have to
  find.

## Scope

**In (Slice 1 — `LiveRunView` only):**
1. **The making-of layout** — the recipe **spine** (a left→right row of step
   tiles: done / active / upcoming), replacing the flat status-row list. The
   active tile is enlarged.
2. **Per-step output preview as it lands** — a finished step's tile shows a
   thumbnail of what it produced; the active step shows its output the instant
   it's available. Reuse the app's existing rendering (`artgen_render` for artgen
   kinds; the gallery's `DetailPanel`/`AnimatedGifWidget` path for raster/video/
   gif) rather than a new renderer.
3. **Per-chip AnimateDiff rows, preserved** — when the active step is multi-chip
   AnimateDiff, the tile shows one live row per chip (the `chipN:` breakdown),
   the same information Create shows today.
4. **tensix-viz as the ambient machine** — embed `ActivityVizWidget`, driven by
   the *active step's* medium/mode (`set_mode`) + `set_running`, so the chips
   visibly pulse with the real work of the step that's running. The board-reset /
   server-start LOG lines become a first-class ambient status, not a hidden
   Details expander.
5. **Honest, real progress + control** — "Step N of M" counts **done**; a real
   progress bar where the runner emits percent; a **Stop** that cancels the
   runner; and a fixed leave/back path (drop the run into the live-context tray /
   return to the correct place — never a blank/stale Open page).

**Out (later slices, explicitly not this pass):**
- Per-step **drill-in inspector** on *completed* runs (Slice 2).
- Progress polish in the **Create** surface (Slice 3 — Create stays as-is now;
  its `CreateResultPanel` already resolves result-in-place).
- The blank-canvas **recipe-sentence compose** front door (later).
- Redesigning **Create / Discover & curate / Watch** — they stay roughly as
  today. Discovering completed pipeline *projects* is a big future bet, not now.

## Design

### A. The Stage spine (replaces the flat step list)
`LiveRunView.begin(run)` builds a horizontal `spine` of step tiles instead of the
current vertical status rows. Tile states, driven by the existing
`ProgressState` reducer (`pipeline_progress.py`):
- **done** — filled, ✓ badge, a thumbnail of the step's produced artifact.
- **active** — enlarged, accent-bordered, showing (i) the landing output preview,
  (ii) the phase sub-label (the runner's `detail`), (iii) a real progress bar,
  and (iv) per-chip rows when applicable.
- **upcoming** — ghosted/dashed, intent label only.
`ProgressState` gains a `done_count`/`completed(node_id)` read so the header reads
"Step {done} of {M} · {active verb}" — counting completed, resolving the "counts
started" bug (`pipeline_progress.py:68`).

### B. Per-step output preview (the "see it as it lands" core)
- A finished step's artifact path comes from the same resolution
  `pipeline_view_model` already does for the hero (`_resolve_artifact*`).
- Rendering routes through the app's existing widgets — NO new renderer:
  artgen kinds → `artgen_render`; raster/video/gif → the gallery's real playback
  (`AnimatedGifWidget` for gif, the DetailPanel video path for mp4). This also
  retires the Studio's static-degrade (video→poster, gif→one-frame) *for the
  active/preview tiles*, closing the "Studio is a visible playback downgrade"
  flag — but only where it's cheap; a full drill-in viewer is Slice 2.
- **Open item (confirm at plan time):** the pipeline runner emits `NODE:<id>:
  <status>:<detail>` + raw `on_log` lines; an intermediate step's artifact path
  must be resolvable *as it finishes* (from the NODE detail or the run spec's
  output path convention) so the tile can preview before the whole run ends.

### C. Per-chip AnimateDiff — extract-and-share
The per-chip row logic currently lives inside `CreateResultPanel`
(`create_view.py:2988` `_CHIP_LINE_RE`, `_pending_chip_box`, `_upsert_chip_row`,
`_chip_status`). Extract it into a small, GTK-optional shared widget (e.g.
`app/chip_progress.py::ChipProgressRows`) that BOTH `CreateResultPanel` and the
Stage's active tile embed — one implementation, no divergence.
- **Open item (confirm at plan time — load-bearing):** the `chipN:` lines reach
  `CreateResultPanel` via the Create worker's `on_progress`. In a pipeline run,
  AnimateDiff runs under `run_workflow.sh` → `on_log` raw lines. Confirm the
  `chipN:` lines are actually teed into the pipeline subprocess stdout (so
  `LiveRunView.on_log` sees them); if not, thread them through (have the
  AnimateDiff step forward per-chip progress into the pipeline stream). Without
  this, per-chip rows can't render in the pipeline Stage — this is the one seam
  that could turn into real engine work, so it's confirmed first.

### D. tensix-viz as the ambient machine
Embed the existing `ActivityVizWidget` (`app/activity_viz.py`) in the Stage,
driven by the run:
- `set_mode(medium)` keyed to the **active step's** output kind
  (`mode_for_medium`: image→diffusion, video/animate→video, animatediff→diffusion,
  artgen→thinking) so the animation matches what's cooking; `set_running(True)`
  for the run's duration.
- The board-reset / server-start `LOG` lines (`pipeline_studio.py:3350`) stay
  first-class next to the viz — the honest "the machine is switching backends"
  story, no longer only inside a collapsed Details panel.
- **Constraints (from CLAUDE.md, non-negotiable):** the viz is built **lazily**
  (WebKit is heavy and eager construction segfaults the bwrap sandbox in CI —
  the `CreateResultPanel` precedent), fail-soft (no WebKit → inert stub; no chips
  → idle), telemetry on its own daemon thread posting via `GLib.idle_add`, and
  its `unrealize`→`_stop_telemetry` teardown intact (review I4).

### E. Control + no dead-ends
- A **Stop** button cancels the `PipelineRunner` subprocess (the runner already
  owns the process; add a cancel path) and marks remaining steps cancelled.
- **Leave** drops the run into the live-context tray (the resumable-runs tray
  from [[project_one_machine_runs]]) and returns to Library/Discover; the run
  keeps going and its results land where you can watch them. The current
  `← Back → _on_back_to_open` blank/stale dead-end for Muse-launched runs
  (`pipeline_studio.py:3766`, `_current_run_view` None) is fixed by routing back
  to Discover (or the tray), never a blank Open.

## Reuse map (this is surfacing, not new power)
- `pipeline_progress.ProgressState` — extend with `done_count`/`completed`.
- `pipeline_view_model._resolve_artifact*` — per-step artifact resolution.
- `artgen_render` + gallery `AnimatedGifWidget`/DetailPanel — real rendering.
- `CreateResultPanel` per-chip rows → extracted `ChipProgressRows` (shared).
- `ActivityVizWidget` — embedded, driven by the run (already has `set_mode`/
  `set_running`/`mode_for_medium`).
- `PipelineRunner` — add a cancel path; confirm intermediate-artifact + per-chip
  line availability on its callbacks.

## Testing (pure/GTK-optional where possible)
- `ProgressState.done_count`/`completed` reducer (pure) — counts done not started.
- `ChipProgressRows` parse/upsert (the `chipN:` regex + row state) — pure logic,
  shared-widget contract identical to today's Create behavior (pin with the
  existing per-chip Create tests + a new shared-widget test).
- `mode_for_medium` mapping for each active-step kind (pure).
- Widget glue (spine build, tile-state transitions, viz embed) — xvfb widget
  tests; a regression guard that the Stage stays **WebKit-free at construction**
  (mirrors `test_activity_viz`'s CreateResultPanel guard) so CI (no WebKit)
  collects.
- `collect()` / run-spec are UNTOUCHED — the making-of is pure display over the
  same `PipelineRunner` signals; a run produces byte-identical output whether the
  new UI or the old one rendered it.

## Hard constraints
- **Keep** the per-chip AnimateDiff progress and the tensix-viz — this slice
  *incorporates* them, never regresses them.
- **Palette: the app's main scheme** — teal `#4FD1C5` on deep blue-gray `#0F2A35`
  (panels `#1A3C47`/`#2D5566`, ink `#E8F0F2`), the tt-vscode-toolkit editor
  variant used throughout the generator — NOT the docs-site forest-teal (that's
  showcase-output only). `_CSS` byte literals ASCII-only.
- GTK single-threaded (`GLib.idle_add` from the runner/telemetry threads); viz
  lazy + fail-soft; no eager WebKit at construction (CI/bwrap).
- Fragile QB2 chip: this slice adds no backend switching; it only *visualizes*
  what the runner already does (the runner's confirm-before-switch stays).
- Version discipline: bump `VERSION` (minor — a user-visible UI change).

## Open items for the plan (confirm, don't guess)
- **Per-chip lines in the pipeline stream** (§C) — the single seam that may
  require engine work; verify/thread first.
- **Intermediate-step artifact availability** on runner callbacks (§B) — can a
  step's output be previewed the moment it finishes, before run-done?
- Whether the embedded viz should be always-on during a run or a toggle within
  the Stage (default: on, honest to the "visible machine" principle, with the
  existing `✕`/Watch-toggle behavior preserved).
- Exact `PipelineRunner` cancel mechanism (process-group kill vs. a stop flag).

## Critical files
- `app/pipeline_studio.py` — `LiveRunView` (the making-of), the spine, tile
  rendering, viz embed, Stop/leave.
- `app/pipeline_progress.py` — `done_count`/`completed`.
- `app/chip_progress.py` — NEW, extracted per-chip rows (shared).
- `app/create_view.py` — `CreateResultPanel` adopts the extracted `ChipProgressRows`
  (behavior-identical).
- `app/activity_viz.py` — embed/drive (likely no change; confirm the run-driven
  `set_mode`/`set_running` path).
- `app/pipeline_runner.py` — cancel path; per-chip / intermediate-artifact
  forwarding if needed.
- `app/pipeline_view_model.py` — reuse per-step artifact resolution.
- Tests: `tests/test_pipeline_progress.py`, `tests/test_chip_progress.py` (new),
  `tests/test_live_run_progress.py`, activity-viz WebKit-free guard.
