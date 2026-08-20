# Pipeline UX Overhaul — Design

**Date:** 2026-08-05
**Branch:** `feat/pipeline-editor`
**Status:** Approved (brainstorm), pending implementation plan

## Problem

The Pipeline surface (`app/pipeline_studio.py`, a 5-page `Gtk.Stack`: Discover /
Muse / Remix / LiveRun / Open) is functional but reads as underbaked. Four
concrete UX gaps, each verified in code:

1. **Steps don't explain themselves.** A step card (`RemixView._build_step_card`,
   `pipeline_studio.py:1768-1816`) shows only the intent label
   `f"{intent.verb} {intent.noun}"` plus, *sometimes*, a quiet `model_label`
   line. There is no plain-language "takes X → makes Y" and no "why this step."
   The `Intent.input_kind`/`output_kind`/`outputs` metadata exists
   (`intent_vocab.py:27-61`) but is never surfaced.

2. **No model picker.** There is no model dropdown anywhere in the pipeline UI.
   `spec_remix.editable_params` (`spec_remix.py:107-137`) surfaces a `model`
   literal only as a **free-text `Gtk.Entry`** (`_build_field_widget`,
   `pipeline_studio.py:2153-2181`, has no "choice" widget). `TTLGAnimateDiff`
   (`intent_vocab.py:203-213`) has no `model` field at all. At run time the
   engine fuzzy-matches the free-text string to a backend by substring
   (`pipeline_engine._backend_for`, `pipeline_engine.py:1145-1191`). So "animate
   into a loop — with what model?" cannot be answered in the UI.

3. **Progress is opaque.** `LiveRunView` (`pipeline_studio.py:2618-2884`) shows a
   static per-step glyph (`_STATUS_GLYPH` •/⟳/✓/✕) beside a raw scrolling log
   tail. `on_node_update` **ignores the `detail`** the engine emits
   (`pipeline_studio.py:2775`, real nodes drop it), so the running phase/node is
   invisible; there is no spinner, elapsed timer, or "step N of M".

4. **The final result is hard to find.** `OpenView._build_step_row`
   (`pipeline_studio.py:1172-1275`) renders **every step identically** — no
   "final result" hero. `pipeline_view_model` computes a `hero_path`
   (`pipeline_view_model.py:380-381`) that `OpenView` never uses; `StepView`
   (`pipeline_view_model.py:94-121`) has no `is_final` flag. Pipeline outputs
   live only under the run's `output_dir` (`pipeline_store.py`), **not** in the
   main Library galleries — so a finished result is siloed in Pipelines.

## Goal

Make the Pipeline surface legible end to end: every step says what it does and
what runs it, you pick the model the same way you do in Create, a run shows real
live progress, and a finished pipeline leads with "here's what you made" — which
is also findable in the Library like any Create output.

Driving journey: *palette → 🔀 Remix → "a looping animation" → see a step that
clearly says "Animate a prompt → looping GIF, runs on AnimateDiff", pick/confirm
the model, Run with a live spinner + phase + elapsed, land on a hero of the
finished GIF that's also in the Library.*

## Decisions (locked in brainstorm)

- **Full overhaul, one spec**, built in sequenced slices (below).
- **Per-step model picker reuses Create's model system** — `ModelStatusService`
  + `server_manager` friendly names/benefit taglines + live ●/◐/◌ dots, scoped
  to the models that can perform each step. Single-engine steps (AnimateDiff
  loop) show their one engine auto-selected with its tagline, never a blank box.
- **Final result: hero on the run page AND registered into the Library.** The
  run/detail page leads with the deliverable; the final artifact is also added to
  the main galleries (image/video/artgen) with pipeline provenance.
- **Library placement (resolved default):** pipeline finals appear in the SAME
  Discover galleries as Create outputs, tagged with pipeline provenance in their
  record `params` (and a small "from a pipeline" affordance), identifiable but
  not siloed.

## Global Constraints

- GTK is single-threaded: all run-progress and post-run UI updates marshal to the
  main thread via `GLib.idle_add` (the existing `pipeline_runner` callback
  pattern already does this).
- The model dropdown writes a normal **scalar `model` literal** into a node's
  `inputs` — so `spec_remix.editable_params`/`apply_edits` round-trip is
  unchanged (a wire is still never editable; a literal stays a literal). No
  change to the spec contract (`{node_id:{class_type,inputs}}`, wires
  `[src,out]`).
- The picker only offers models `pipeline_engine._backend_for` can actually route
  — extend `_backend_for` in lockstep where Create knows a backend the engine
  doesn't (so the dropdown never offers a dead choice).
- Reuse, don't fork: the model picker reuses Create's status/benefit seam; the
  Library registration reuses Create's `history_store.GenerationRecord` /
  `media_store.MediaRecord` construction patterns (see `_create_generate_artgen`
  / forge-transform paths).
- `_CSS`/`b"""..."""` byte literals stay ASCII-only; glyphs live in Python str
  labels.
- Intent LANGUAGE stays tool-agnostic (verb/noun, never `TTLG`/class_type) —
  `intent_vocab.label`'s contract is preserved.
- System `/usr/bin/python3`; GTK tests under `xvfb`; version bump + changelog.

## Components

### Slice 1 — Self-explaining step cards

- **`intent_vocab.py`:** add an optional `Intent.summary: str | None = None` (a
  short, tool-agnostic "why" blurb) and a pure helper
  `flow_line(intent) -> str` that renders "Takes {input} → makes {output}" from
  `input_kind`/`output_kind` using human nouns (a small kind→noun map:
  text→"text", image→"an image", gif→"a looping GIF", video→"a video",
  palette→"a color palette"). A source-style node (`input_kind is None`) renders
  "Makes {output}".
- **`RemixView._build_step_card`** (`pipeline_studio.py:1768-1816`): under the
  intent label, add the flow line (always) and the `summary` (when present),
  styled as quiet secondary text. Keep the existing brief/direction/control
  zoning + marker tooltips unchanged.
- Also surfaced identically on the `OpenView` step rows and `LiveRunView` step
  rows so the vocabulary is consistent across compose/run/detail.

### Slice 2 — Per-step model picker (reuse Create's system)

- **Intent → capability map** (`intent_vocab` or a small helper): TextToImage →
  `image`; ImageToVideo → `video`; AnimateDiff → single engine `animatediff`
  (gif); GenerateText/ArtgenGenerate → chat/`artgen` (text). Intents with no
  model dimension (Describe, Cut out, Split, Compose, PaletteToPrompt) → no
  picker.
- **A shared model-picker widget** built from Create's seam: given a capability
  it lists the models via `server_manager` (`display_name_for`/`benefit_for`) +
  live status via the injected `ModelStatusService` (3-state ●/◐/◌). Factor the
  reusable core out of `create_view` so both surfaces share ONE implementation
  (no forked picker).
- **RemixView** receives the `ModelStatusService` (threaded from
  `MainWindow`/`PipelineStudio`, as CreateView already is via `status_service=`).
  In `_build_step_card`, a model-bearing intent renders the picker in the "Runs
  on" slot; selecting a model writes the canonical `model` string into that
  node's `inputs` (via the same edit path as other fields). Single-engine steps
  render the one engine + tagline, auto-selected, informational.
- **`pipeline_engine._backend_for`** (`pipeline_engine.py:1145-1191`): extend the
  model→backend mapping so every model the picker can offer resolves (add the
  image backends Create knows — e.g. motif/z-image — and any missing video
  keys). The picker's option list is derived from the SAME source, so offer and
  routing stay in sync.
- **Invariant:** with no model change, the node's `inputs` are byte-identical to
  before (the picker pre-selects the current/default and only writes on change).

### Slice 3 — Live run progress

- **`LiveRunView`** (`pipeline_studio.py:2618-2884`): the running step shows a
  real `Gtk.Spinner` + a **live phase sub-label** fed by the `detail` string that
  `on_node_update` currently drops (`:2775`) — wire `detail` through to the step
  row. Add a **per-step elapsed timer** (`GLib.timeout_add`, cancel on
  done/failed) and an overall **"Step N of M"** + total elapsed in the header.
- Demote the raw log to a collapsible **"Details ▸"** expander (kept, not
  primary). Board-switch lines keep their distinct style inside it.
- `on_finished` resolves the view straight into the result hero (Slice 4) rather
  than leaving a flat step list.
- Pure-testable seam: the per-step status/phase/elapsed computation is a small
  pure reducer over `(node_id, status, detail)` events so it's unit-tested
  without GTK; the widget just renders the reduced state.

### Slice 4 — Final-result hero on the run/detail page

- **`pipeline_view_model`:** add `RunView.final: StepView | None` (or a
  `hero`/`is_final` marker on the deliverable) — the last step's primary artifact
  of a hero kind (reuse the existing `hero_path` logic, promote it from unused).
- **`OpenView`** (and the finished `LiveRunView`): lead with a **hero block** —
  large preview of the final deliverable, title "Here's what you made", actions
  **⛶ Fullscreen · ⤓ Save · ↪ In Library · 🔀 Remix**. The step list moves under a
  secondary **"How it was made ▸"** section, each row tagged **artifact** /
  **info (text)** / **no output** (the `has_content` split already exists at
  `pipeline_studio.py:1188` — make the labels explicit).
- Text-output-only pipelines (no visual deliverable) show the final text as the
  hero. A failed run shows the failure + the last good step, no hero.

### Slice 5 — Register the final deliverable into the Library + retire legacy

- On `run-done`, register the final deliverable into the Library using Create's
  patterns: raster/video → `history_store.GenerationRecord`; artgen kinds
  (gif/svg/ansi/palette/verse/…) → `media_store.MediaRecord`
  (`generator_type="pipeline"`, `params` carrying `_pipeline_run_id` + recipe/
  goal for provenance). Refresh the owning gallery exactly as
  `_on_create_artgen_done` / `_on_finished` do. Dedup-safe (register once per
  run, keyed by run id).
- The Library card for a pipeline final carries a small "from a pipeline"
  affordance whose action opens that run's Open page (round-trip to provenance).
- **Retire `app/pipeline_portfolio_view.py`** (the worlds-fair-specific
  fixed-narrative surface, already unwired from the finished-run flow) — remove
  it, or if anything still imports it, reduce to the hero+breakdown OpenView.

## Data flow (driving journey)

```
palette → 🔀 Remix → Muse "a looping animation"
      → RemixView: step card reads "🕺 Animate a prompt · Takes your prompt →
        makes a looping GIF · Runs on [AnimateDiff — Blackhole GIF ●]"
      → Run → LiveRunView: ⟳ spinner + "sampling 12/25" + 0:07 elapsed + Step 2/2
      → run-done → hero "Here's what you made" [big GIF]  ⛶ ⤓ ↪ 🔀
                 → final GIF also registered into the artgen Library gallery
```

## Error handling

- **Model picker with no server up:** shows the models greyed with ◌ dots (like
  Create); selecting one is allowed (the run's readiness gate / engine
  `_backend_for` starts it), never blocks compose.
- **Engine can't route a chosen model:** cannot happen for offered options
  (offer list = `_backend_for`-routable set); a stale spec with an unknown model
  string still falls back to the engine's existing default (unchanged behavior).
- **Run fails mid-pipeline:** no hero; show the failure, the failing step, and
  the last successful step's output; nothing is registered into the Library.
- **Final artifact missing on disk at run-done:** skip Library registration
  (fail-soft, like `_resolve_artgen_media_seed`'s existence guard); the hero
  falls back to a placeholder + "output not found".
- Library registration failure never breaks the run-done view (wrapped, logged).

## Testing

Pure logic headless; GTK-widget tests under `xvfb`.

- **Slice 1:** `flow_line(intent)` for each kind pairing + source node; a GTK
  test that a step card renders the flow line + summary.
- **Slice 2:** intent→capability map; the model-picker option list derives from
  `server_manager` and only contains `_backend_for`-routable models (a guard test
  cross-checking the two lists); selecting a model writes the canonical `model`
  literal into the node inputs; NO change → inputs byte-identical (collect-style
  invariant); single-engine step auto-selects its engine.
- **Slice 3:** the pure progress reducer over `(node,status,detail)` events →
  correct per-step phase/elapsed/"N of M"; a GTK test that a running step shows a
  spinner + phase sub-label and the log is in a collapsed expander.
- **Slice 4:** view-model `final`/hero resolves the last hero-kind artifact;
  `OpenView` renders a hero + a secondary breakdown; text-only pipeline → text
  hero; failed run → no hero.
- **Slice 5:** run-done registers exactly one Library record with pipeline
  provenance of the correct type (GenerationRecord vs MediaRecord by kind);
  missing-file → no record, no crash; the gallery refresh is invoked.
- Full suite green with the three documented flake deselects.

## Out of scope (YAGNI)

- Changing the pipeline SPEC contract, `seed_spec`, or `editable_params`/
  `apply_edits` semantics (the picker is a widget over an existing literal).
- Editing pipeline WIRES in the UI (still literal-only).
- New engine backends beyond wiring `_backend_for` to the models Create already
  knows.
- A node-graph canvas / drag-rewire editor — the linear step list stays.
- Per-step "swap the engine class_type" (e.g. AnimateDiff→SkyReels) — the picker
  chooses the model WITHIN a step's intent, not the intent itself.

## Critical files

- `app/intent_vocab.py` — `Intent.summary`, `flow_line`, intent→capability map.
- `app/pipeline_studio.py` — `RemixView` step cards + model picker slot +
  `ModelStatusService` injection; `LiveRunView` spinner/phase/elapsed + log
  demotion; `OpenView` hero + secondary breakdown.
- `app/create_view.py` — extract the shared model-picker core (used by both).
- `app/pipeline_engine.py` — `_backend_for` model→backend parity.
- `app/pipeline_view_model.py` — `final`/hero on the run view; `is_final`.
- `app/pipeline_runner.py` — ensure `detail` reaches `on_node_update` (it emits
  it; the drop is in the view).
- `app/main_window.py` — thread `ModelStatusService` into `PipelineStudio`;
  register pipeline finals into the Library on run-done; gallery refresh.
- `app/pipeline_store.py` — persist the designated final artifact / provenance.
- `app/pipeline_portfolio_view.py` — retired.

## Build order

1 (step-card clarity) → 2 (model picker) → 3 (live progress) → 4 (result hero) →
5 (Library registration + retire legacy). 1-2 are the compose surface; 3-5 are
the run/results surface. Each slice ends green and independently demoable.
