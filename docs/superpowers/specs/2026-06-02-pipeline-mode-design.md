# Pipeline Mode — Design Spec

**Date:** 2026-06-02  
**Status:** Approved for implementation planning

---

## Goal

Add **Pipeline** as a fifth creation mode in tt-local-generator, sitting alongside Video / Animate / Image / Generative Art in the toolbar. Pipeline lets a user define N jobs (each a set of variable values), pick a workflow spec, and run all N jobs through the full multi-phase pipeline in one operation — with a live phase-grid view, per-cell artifact preview, and per-node retry.

This replaces the current shell-script workflow for batch runs (e.g. the 5-fair World's Fair run) with a first-class UI experience.

---

## Architecture

### Placement

Pipeline is a **5th source tab** in `ControlPanel._toolbar_box`, sitting after "Generative Art":

```
[Video] [Animate] [Image] [Art] [⚙ Pipeline]
```

Selecting it calls `_set_source("pipeline")`, which hides the existing prompt/chips/generate controls and shows a new `PipelinePanel` in the left control pane. The center gallery and right detail pane remain — they're used for artifact preview.

The existing `⚙ Workflow` toolbar `MenuButton` is removed (its functionality is superseded by the Pipeline tab).

---

## Files to create / modify

| File | Change |
|---|---|
| `app/pipeline_panel.py` | New — `PipelinePanel` widget (left control pane) |
| `app/pipeline_runner.py` | New — `PipelineRunner` (subprocess management, phase parsing, retry logic) |
| `app/pipeline_store.py` | New — persistence for run records, job states |
| `app/main_window.py` | Add Pipeline source tab, `_set_source("pipeline")`, wire `PipelinePanel` |
| `app/workflow_popover.py` | Remove (functionality moved into Pipeline tab) |
| `bin/run_workflow.sh` | Add structured `NODE:<id>:<status>` signal lines alongside existing `log_step` calls |

---

## Component Design

### 1. `PipelinePanel` (left pane)

Replaces the prompt/chips/generate controls when Pipeline source is active. Three stacked sections:

**Section 1 — Spec picker**  
`Gtk.DropDown` listing all workflow JSON specs from `docs/examples/workflows/` and `~/.local/share/tt-local-generator/workflows/`. Below it: spec description label and a node-count badge.

**Section 2 — Batch job table**  
The core input widget. Structure:

- **Template row** (above the table): a single `Gtk.Entry` where the user writes the prompt template with `{variable}` placeholders. Example: `{era} World's Fair, {subject}, {style}, cinematic slow push-in`. The widget parses the template in real-time and derives the column set from the `{variable}` tokens found.

- **Variable table** (`Gtk.ColumnView` or custom grid): one row per job, columns:
  - Enabled toggle (checkbox)
  - Name (editable label)
  - One column per detected `{variable}` (editable `Gtk.Entry` cells)
  - Override column: a `✏` icon that, when clicked, replaces the variable cells for that row with a single free-text prompt entry (shown in amber, `color: var(--gold)`)
  - Delete button (×)
  
- **Add row** button below the table.

- **Preview row**: below the table, shows the resolved prompt for the currently-selected row in italic muted text. Updates live as variables are edited.

Template is optional — if the user clears it, all rows switch to free-text mode automatically.

**Section 3 — Run controls**  
- **▶ Run Pipeline** button (`suggested-action` CSS class)  
- While running: **■ Cancel** + live status line ("⏳ Phase 3: SkyReels loading — 2/5 complete")
- Spec parameter overrides (collapsed by default, same as current WorkflowPopover zone 2): allows overriding leaf inputs like `seed`, `steps`, `num_frames` that apply to all jobs

---

### 2. Phase Progress Grid (center pane, replaces gallery during active run)

When a run is started, the center pane switches from `GalleryWidget` to a `PhaseGridWidget`. On run completion the gallery re-appears (or persists alongside via a tab switcher).

**Grid layout:**

```
         Seed   Depth  Video  Poem   PoemImg  Save
1964 NY   ✓      ✓      ✓      ✓       ✓       ✓
1939 NY   ✓      ✓      ⏳4m   —       —       —
1893 Chi  ✓      ✓      q      —       —       —
1970 Osa  ✓      skip   q      —       —       —
1967 Mon  ✗      —      —      —       —       —
```

Column headers show the node name and model (e.g. "Seed · FLUX"). Row labels show the job name. 

**Cell states:**
- `pending` — empty, dim border
- `running` — teal border, pulsing, shows elapsed time ("⏳ 4m")
- `completed` — green fill, checkmark, timing ("✓ 3s")  
- `skipped` — amber border, "skip" label (e.g. depth for fog scenes)
- `failed` — red fill, "✗ retry" label, clickable
- `queued` — dim, "queued" label

**Cell click behavior:**
- `completed` cell → loads the artifact into the right detail pane (same `DetailPanel` used for Video/Image records). The user can play video, star, export, remix.
- `failed` cell → shows a small inline popover with the error message excerpt and a **↺ Retry node** button. Clicking retry re-submits just that node for that job (requires the previous nodes' outputs to still exist in the run's `output_dir`).

**Per-job retry:**  
Each row has a `↺` button at the far right, visible when any cell in that row is `failed`. Clicking it re-runs the job from its first failed node forward.

**Status bar** below the grid: scrolling log tail, same `LOG:` path parsing as current popover.

---

### 3. `PipelineRunner` (no GTK imports)

Manages the lifecycle of a batch pipeline run. Pure Python, no GTK — all UI updates posted via `GLib.idle_add`.

**Interface:**
```python
class PipelineRunner:
    def start(self, spec_path, jobs: list[dict], param_overrides: dict,
              on_node_update: Callable[[str, str, str, Any], None],
              on_run_finished: Callable[[bool], None]) -> None
    def cancel(self) -> None
    def retry_node(self, job_id: str, node_id: str) -> None
    def retry_job(self, job_id: str) -> None
```

`on_node_update(job_id, node_id, status, artifact_path_or_none)` — called whenever a node changes state.

**Implementation:**  
Launches `run_workflow.sh` once per active model phase (matching the existing phase structure). Parses stdout for the new structured signals (see §4). Manages the per-job `results.json` files in `~/.local/share/tt-local-generator/workflow-runs/<run_id>/<job_id>/`.

For batch runs, the runner dispatches all jobs for the current phase in parallel (matching the `run_worlds_fair_parallel.sh` pattern), then moves to the next phase.

---

### 4. New structured signals in `run_workflow.sh`

Add to `run_workflow.sh` alongside existing `log_step` calls:

```bash
# Emit at node start:
echo "NODE:${node_id}:running:${job_label}"
# Emit at node complete (with output path):
echo "NODE:${node_id}:done:${output_path}"
# Emit at node skip:
echo "NODE:${node_id}:skipped:${reason}"
# Emit at node failure:
echo "NODE:${node_id}:failed:${error_summary}"
```

The `PipelineRunner` parses these alongside the existing `LOG:`, `PLAYLIST:`, `⏳`, `⚠️` signals. Existing `WorkflowPopover` parsing is removed when the popover is removed.

---

### 5. `PipelineStore` (persistence)

Thin wrapper over `~/.local/share/tt-local-generator/workflow-runs/index.json` (already exists). Adds:

- `run.jobs: list[dict]` — the job table (name, variable values, resolved prompt, enabled)
- `run.job_states: dict[job_id, dict[node_id, {status, path, timing}]]` — per-cell state
- `run.spec_path`, `run.param_overrides`

On app restart, the Pipeline tab shows the last run's grid in its final state (no live re-connection — just a static view of results).

---

## Data Flow

```
User fills table + clicks Run
  → PipelinePanel._on_run_clicked()
  → PipelineRunner.start(spec, jobs, overrides, on_node_update, on_run_finished)
  → PipelineRunner launches run_workflow.sh per phase
  → NODE: signals parsed → on_node_update(job_id, node_id, status, path)
  → GLib.idle_add(PhaseGridWidget.update_cell, ...)
  → Cell click → GLib.idle_add(DetailPanel.show_record, record)
  → ✗ cell click → retry popover → PipelineRunner.retry_node(job_id, node_id)
  → Run finishes → playlist auto-created → gallery shows playlist
```

---

## Error Handling

- **Node failure:** Cell turns red with "✗ retry". Run continues to next job/phase rather than aborting. Same `_run_node` guard pattern as `run_workflow.sh`.
- **Server not ready:** Phase start checks `tt-health-check.sh --quiet` before launching. If degraded, shows a warning banner in the status bar with "AC power cycle recommended" — does not block the run (user's choice).
- **Cancelled run:** `PipelineRunner.cancel()` sends SIGTERM to active subprocesses, marks all `running` cells as `cancelled`, leaves `completed` cells intact.
- **App restart mid-run:** Run is marked `interrupted` in `PipelineStore`. The grid shows the partial state. No auto-resume — user can manually retry failed/incomplete jobs.

---

## Interaction with existing gallery

Completed pipeline artifacts are imported into `media.db` via the existing `_import_playlist.py` pattern. Each job produces one playlist. The gallery's "By Model" section skips `model_id == "workflow"` entries (already implemented). Playlists appear in the playlist sidebar as normal.

When a pipeline run completes, the center pane offers a **"→ View Playlist"** button that switches back to the gallery and opens the first job's playlist in TT-TV.

---

## What is NOT in scope for v1

- Re-ordering jobs while a run is active
- Saving/loading batch job tables as named presets (future: add to workflow JSON spec as `_batch_defaults`)
- Progress within a single node (e.g. denoising step count) — only node-level granularity
- Running multiple pipeline runs simultaneously
- Dragging artifacts between pipeline runs

---

## Success criteria

1. User can define 5 jobs with a template + variable table, pick a spec, click Run, and watch the phase grid fill in — no terminal required.
2. Clicking a completed video cell plays the video in the detail pane.
3. A failed cell shows the error and a one-click retry that re-runs just that node.
4. Per-job ↺ retry re-runs from first failure.
5. Run completion auto-creates playlists and offers "→ View Playlist".
6. `tt-health-check.sh` runs before each phase and surfaces chip degradation in the status bar.
7. Existing Video/Animate/Image/Art tabs are completely unaffected.
8. All existing tests pass; new `tests/test_pipeline_*.py` suite covers `PipelineRunner`, `PipelineStore`, and node signal parsing.
