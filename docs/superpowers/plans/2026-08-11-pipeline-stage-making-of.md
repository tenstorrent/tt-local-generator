# Pipeline "Stage" — the live making-of (Slice 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the pipeline `LiveRunView` (`app/pipeline_studio.py`) from a text ticker into a live "making-of" — a recipe spine whose active step shows its output as it lands, per-chip AnimateDiff rows preserved, the tensix-viz as the ambient machine, honest done-counting progress, a Stop, and no dead-end back.

**Architecture:** Pure display over the *same* `PipelineRunner` signals — `collect()`/run output stay byte-identical. Reuse everything already built: `ProgressState` (pure reducer, already has `done_count`), `pipeline_view_model._resolve_artifact*` (filesystem-glob, resolvable mid-run), `artgen_render` + the gallery's `AnimatedGifWidget` for real rendering, `ActivityVizWidget` (tensix-viz, already has `set_mode`/`set_running`), and `PipelineRunner.cancel()` (already exists). The one engine change: stream AnimateDiff's per-chip lines into the pipeline stdout so they reach `LiveRunView.on_log`.

**Tech Stack:** Python 3 (system `/usr/bin/python3`), GTK4/PyGObject, WebKit (tensix-viz, guarded). Tests via `xvfb-run --auto-servernum /usr/bin/python3 -m pytest`.

## Global Constraints

Every task implicitly includes this section.

- **KEEP, never regress:** the per-chip AnimateDiff progress (the `chipN:` live rows) and the tensix-viz "👁 Watch" viz — this slice *incorporates* them into the Stage.
- **Palette = the app's MAIN scheme:** teal `#4FD1C5` on deep blue-gray `#0F2A35` (panels `#1A3C47`/`#2D5566`, ink `#E8F0F2`, dim `#A9C1C6`/`#607D8B`, accent-light `#81E6D9`, green `#27AE60`, gold `#F6BC42`, red `#FF6B6B`) — the tt-vscode-toolkit editor variant used throughout the generator. NOT the docs-site forest-teal. New CSS reuses the existing `ps-*` class family in `pipeline_studio.py`'s `_CSS`.
- **`_CSS` / `b"""..."""` byte literals ASCII-only** (non-ASCII → SyntaxError). Glyphs (✓, ⟳, ●, →) live only in Python `str` labels, never inside a bytes CSS literal.
- **GTK single-threaded:** all runner/telemetry callbacks touch widgets only on the main thread. `PipelineRunner` already dispatches via `_idle_add`; the viz telemetry is already a daemon thread posting via `GLib.idle_add`.
- **tensix-viz built LAZILY + fail-soft + WebKit-free at construction:** eager WebKit segfaults the bwrap sandbox in CI; mirror `CreateView._ensure_activity_viz` (build on first need, try/except, no-WebKit → inert). A regression test must assert the Stage constructs WebKit-free.
- **`collect()` / run-spec byte-identical:** the making-of is pure display over `PipelineRunner`'s existing `NODE:`/`LOG:` signals; a run produces identical output whether the new or old UI rendered it.
- **Fragile QB2 chip** (`reference_qb2_card924055_fragility`): this slice adds NO backend switching — it only *visualizes* what the runner already does; the runner's confirm-before-switch is untouched.
- **Reuse, don't rebuild** — no new renderer, no new progress reducer, no new cancel mechanism.
- **Local commits only.** VERSION minor bump in the finalize task (do not bump per task).
- **Known-flake deselects** for full-suite runs (Task 8):
  `tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`,
  `tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`,
  `tests/test_role_zone_panel.py::test_prompt_field_hidden_but_still_collected_for_artgen`.

## Confirmed values (from the live code; no guessing)

- `ProgressState` (`app/pipeline_progress.py`) already has `done_count` (property, counts `status=="done"`), `current_index` (counts `_started_order`), `running_node`, `phase(node_id)`, `status(node_id)`. It has NO `completed(node_id)` predicate yet.
- `LiveRunView._update_step_count_label` (`pipeline_studio.py:3579`) renders `current_index` (started) — the "counts started not done" bug; `done_count` exists but is unused there.
- `PipelineRunner.cancel()` **already exists** (`pipeline_runner.py:208`): sets `self._cancelled=True` + `self._proc.terminate()`. `_watch_stdout` forwards **every** raw stdout line to `on_log` verbatim (`:416`), then `_parse_line` handles `LOG:`/`NODE:`/`PLAYLIST:`.
- **The per-chip seam:** AnimateDiff's `chipN:` lines are produced by `animatediff._make_drain`'s `on_progress(f"chip{i}: {line}")` (`:1159`). In a pipeline run, `pipeline_engine._h_animatediff` (`:1014`) shells to `tt-ctl artgen animatediff` via `_run_tt_ctl` (`:926`) which uses `subprocess.run(..., capture_output=True)` — **the child stdout (incl. `chipN:`) is captured and discarded**, never reaching the pipeline stdout. Engine handlers emit downstream via `ctx.emit(...)` → `print(s, flush=True)` (`:1427`) → `run_workflow.sh` tee → `PipelineRunner._watch_stdout` → `on_log`.
- `pipeline_view_model._resolve_artifact(output_dir, node_id, intent)` (`:194`) globs `node{nid}_*` / `node{nid}.*` (top level + one dir down, newest mtime) — resolvable the moment a step's file lands, mid-run. `StepView` has `node_id, intent, status, artifact_path, text_content, artifact_paths`. `Intent` has `verb, noun, icon, output_kind, model_label`.
- `ActivityVizWidget(arch="blackhole")` (`activity_viz.py:288`): `set_mode(medium)`, `set_running(bool)`, `set_active(medium)`, `set_idle()`, `mode_for_medium(medium)` (pure: image→diffusion, video/animate→video, animatediff→diffusion, artgen→thinking, else inference), `on_close` callable, `_WEBKIT_OK` guard, `unrealize`→`_stop_telemetry`. Lazy build pattern: `CreateView._ensure_activity_viz` (`create_view.py:2757`).
- CreateResultPanel per-chip subsystem owns exactly `self._chip_status` (`{idx:text}`), `self._chip_row_labels` (`{idx:Gtk.Label}`), `self._pending_chip_box`, module `_CHIP_LINE_RE = re.compile(r"chip(\d+):\s*(.*)", re.IGNORECASE)` (`:2988`), `_upsert_chip_row` (`:3419`), and the `chipN:` branch of `show_progress` (`:3451`).

---

## Task 1: `ProgressState.completed()` + header counts DONE

**Files:**
- Modify: `app/pipeline_progress.py` (add `completed`), `app/pipeline_studio.py` (`_update_step_count_label`)
- Test: `tests/test_live_run_progress.py`

**Interfaces:**
- Produces: `ProgressState.completed(node_id) -> bool` (True iff that node's status is `"done"`).

- [ ] **Step 1: Write the failing test** (append to `tests/test_live_run_progress.py`)

```python
def test_completed_predicate_and_done_count():
    st = pp.ProgressState(total=3)
    st.update("1", "running", ""); st.update("1", "done", "")
    st.update("2", "running", "")
    assert st.completed("1") is True
    assert st.completed("2") is False   # running, not done
    assert st.completed("3") is False   # never seen
    assert st.done_count == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_live_run_progress.py::test_completed_predicate_and_done_count -v`
Expected: FAIL — `ProgressState` has no `completed`.

- [ ] **Step 3: Add `completed` to `ProgressState`** (after `status()`, `app/pipeline_progress.py:51`)

```python
    def completed(self, node_id: str) -> bool:
        """True iff this node has finished successfully (status == 'done')."""
        return self._status.get(node_id) == "done"
```

- [ ] **Step 4: Header counts done** — in `app/pipeline_studio.py:3579`, `_update_step_count_label`, replace `self._progress.current_index` in the label with `self._progress.done_count`, and gate the blank-state on total (not index):

```python
    def _update_step_count_label(self) -> None:
        if self._progress is None:
            self._step_count_label.set_label("")
            return
        # Count DONE, not started (review: "Step N of M" over-reported before).
        self._step_count_label.set_label(
            f"Step {self._progress.done_count} of {self._progress.total}"
        )
```

- [ ] **Step 5: Update/confirm the LiveRunView header widget test** — in `tests/test_pipeline_studio.py`, if a test asserts the "Step N of M" text, update it to the done-count semantics (a step shows in the count only once `done`). Add/adjust:

```python
def test_live_run_header_counts_done_not_started():
    view = LiveRunView(); view.begin(_make_live_run())
    view.on_node_update("job", "1", "running", "")
    assert "Step 0 of 3" in view._step_count_label.get_label()  # running != done
    view.on_node_update("job", "1", "done", "")
    assert "Step 1 of 3" in view._step_count_label.get_label()
```

- [ ] **Step 6: Run to verify pass**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_live_run_progress.py tests/test_pipeline_studio.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/pipeline_progress.py app/pipeline_studio.py tests/test_live_run_progress.py tests/test_pipeline_studio.py
git commit -m "feat(pipeline): Stage progress counts DONE steps + ProgressState.completed()"
```

---

## Task 2: Engine seam — stream AnimateDiff per-chip lines into the pipeline

**Files:**
- Modify: `app/pipeline_engine.py` (`_run_tt_ctl`, and `_h_animatediff`'s call to it)
- Test: `tests/test_pipeline_engine.py` (or a new `tests/test_pipeline_animatediff_progress.py`)

**Interfaces:**
- Produces: AnimateDiff step's `chipN:` progress lines are forwarded to the pipeline stdout as `LOG:` lines (so `PipelineRunner._watch_stdout` → `on_log` → `LiveRunView.on_log` sees them). No change to `PipelineRunner`.

**Context:** `_run_tt_ctl` (`pipeline_engine.py:926`) captures+discards the child stdout. Change it to **stream** the child stdout line-by-line and forward each line to an optional `emit` callback, so `_h_animatediff` can tee the child's `chipN:` lines into `ctx.emit` as `LOG:` lines. Non-AnimateDiff callers keep today's behavior (default `emit=None` = capture-and-return, unchanged).

- [ ] **Step 1: Write the failing test**

`tests/test_pipeline_animatediff_progress.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pipeline_engine as pe
from unittest.mock import patch, MagicMock

class _FakeProc:
    def __init__(self, lines):
        self.stdout = iter(lines)
        self.returncode = 0
    def wait(self): return 0

def test_run_tt_ctl_streams_lines_to_emit():
    """With an emit callback, _run_tt_ctl forwards each child stdout line
    (so a pipeline AnimateDiff step's chipN: lines reach the run stream)."""
    seen = []
    lines = ["Starting AnimateDiff on 2 chips\n", "  chip0: Step 5/25\n", "  chip1: Step 6/25\n"]
    with patch.object(pe.subprocess, "Popen", return_value=_FakeProc(lines)):
        pe._run_tt_ctl(["artgen", "animatediff"], timeout=10, emit=lambda s: seen.append(s))
    assert any("chip0: Step 5/25" in s for s in seen)
    assert any("chip1: Step 6/25" in s for s in seen)

def test_run_tt_ctl_without_emit_is_unchanged():
    """Default (emit=None) keeps the capture-and-return behavior other callers rely on."""
    with patch.object(pe.subprocess, "run",
                      return_value=MagicMock(returncode=0, stdout="ok", stderr="")):
        out = pe._run_tt_ctl(["servers"], timeout=5)
        assert out.returncode == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_pipeline_animatediff_progress.py -v`
Expected: FAIL — `_run_tt_ctl` has no `emit` param.

- [ ] **Step 3: Add streaming to `_run_tt_ctl`** (`app/pipeline_engine.py:926`)

Add an optional `emit: "Callable[[str], None] | None" = None`. When provided, `Popen` + drain stdout line-by-line, forwarding each stripped line to `emit`; on nonzero exit raise the same `RuntimeError` as today. When `emit is None`, keep the exact existing `subprocess.run(..., capture_output=True)` path (byte-for-byte).

```python
def _run_tt_ctl(argv, timeout=1800, emit=None):
    cmd = [str(TT_CTL), *argv]
    if emit is None:
        # unchanged: capture-and-return (every existing caller relies on this)
        result = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(f"tt-ctl {' '.join(argv)} failed:\n{result.stdout}\n{result.stderr}")
        return result
    # streaming: forward each child stdout line to emit (used by AnimateDiff so
    # per-chip progress reaches the pipeline run stream) — see plan Task 2.
    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True,
    )
    try:
        for line in proc.stdout:
            emit(line.rstrip("\n"))
    finally:
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"tt-ctl {' '.join(argv)} failed (exit {rc})")
    return None
```

(Confirm the real `_run_tt_ctl` signature/body first; preserve any timeout handling — if the current version passes `timeout=` to `subprocess.run`, keep that on the non-emit branch. The streaming branch's timeout is a plan open item: if a wall-clock bound is needed, wrap the drain with a deadline; the AnimateDiff generator already enforces its own subprocess timeout, so a hard bound here is optional — note the decision in your report.)

- [ ] **Step 4: Forward per-chip lines from `_h_animatediff`** (`app/pipeline_engine.py:1043`)

Change the `_run_tt_ctl(argv, timeout=1800)` call in `_h_animatediff` to pass an `emit` that re-emits only `chipN:` (and other progress) lines into the pipeline stream as `LOG:` lines via the handler's `ctx.emit`:

```python
    _run_tt_ctl(
        argv, timeout=1800,
        emit=lambda s: ctx.emit(f"LOG:{s}") if s.strip() else None,
    )
```

(Confirm `_h_animatediff`'s signature exposes `ctx` with `emit` — the handler is `_h_animatediff(nid, inp, ctx)` and other handlers call `ctx.emit("LOG: ...")`. `LiveRunView.on_log` receives the raw line after `run_workflow.sh` tees it; the `chip(\d+):` regex in Task 3 matches `chip0: …` within it.)

- [ ] **Step 5: Run to verify pass + no regression**

Run: `/usr/bin/python3 -m pytest tests/test_pipeline_animatediff_progress.py tests/test_pipeline_engine.py -q --deselect tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`
Expected: PASS (existing `_run_tt_ctl` callers unaffected — they use the default `emit=None` path).

- [ ] **Step 6: Commit**

```bash
git add app/pipeline_engine.py tests/test_pipeline_animatediff_progress.py
git commit -m "feat(pipeline): stream AnimateDiff per-chip lines into the run (LOG:), for the Stage"
```

---

## Task 3: Extract `ChipProgressRows` + adopt in `CreateResultPanel`

**Files:**
- Create: `app/chip_progress.py`, `tests/test_chip_progress.py`
- Modify: `app/create_view.py` (`CreateResultPanel` uses the shared widget)

**Interfaces:**
- Produces: `chip_progress.CHIP_LINE_RE` (the compiled regex) and `class ChipProgressRows(Gtk.Box)` with:
  - `feed(message: str) -> bool` — if `message` matches `chip(\d+):`, upsert that chip's row (reveals the box) and return `True`; else return `False`.
  - `reset() -> None` — drop all rows/state (fresh job).
  - `snapshot() -> dict[int,str]` / `restore(state: dict[int,str])` — for return-to-pending re-render.

**Context:** Move the per-chip subsystem verbatim (behavior-identical). The existing Create tests (`tests/test_create_result_panel.py:247-295`) are the behavior contract and must still pass after adoption.

- [ ] **Step 1: Write the failing test**

`tests/test_chip_progress.py` (GTK-guarded like `test_create_result_panel.py`):

```python
import sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pytest
try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()
except Exception:
    pytest.skip("GTK4 not available", allow_module_level=True)
import chip_progress as cp

def test_feed_matches_chip_line_and_upserts():
    w = cp.ChipProgressRows()
    assert w.feed("Starting on 4 chips") is False   # not a chip line
    assert w.feed("chip0: Step 2/25") is True
    assert w.feed("chip1: Step 3/25") is True
    assert w.feed("chip1: Step 9/25") is True        # update in place
    assert set(w._chip_row_labels) == {0, 1}
    assert w._chip_row_labels[1].get_label() == "chip 1: Step 9/25"
    assert w.get_visible() is True

def test_reset_and_restore():
    w = cp.ChipProgressRows()
    w.feed("chip0: a"); snap = w.snapshot()
    w.reset(); assert w._chip_row_labels == {}
    w.restore(snap); assert w._chip_row_labels[0].get_label() == "chip 0: a"
```

- [ ] **Step 2: Run to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_chip_progress.py -v`
Expected: FAIL — no `chip_progress` module.

- [ ] **Step 3: Write `app/chip_progress.py`** — a `Gtk.Box` (VERTICAL) owning `_chip_status`/`_chip_row_labels`, the `CHIP_LINE_RE`, and the `_upsert_chip_row` logic lifted verbatim from `create_view.py:3419-3435`. `feed` runs the regex match; `snapshot`/`restore`/`reset` handle the persisted state. Keep the CSS classes (`create-result-chip-box`, `create-result-chip-row`) so styling is unchanged.

- [ ] **Step 4: Run to verify pass**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_chip_progress.py -v`
Expected: PASS.

- [ ] **Step 5: Adopt in `CreateResultPanel`** — replace the inline `_pending_chip_box`/`_upsert_chip_row`/`_chip_status`/`_chip_row_labels` with an embedded `ChipProgressRows`. In `show_pending`, build/`reset()` it and append into `_current_box`; in `show_progress`'s `chipN:` branch (`create_view.py:3451`), delegate to `self._chip_rows.feed(message)` (keep the persisted `_chip_status` behavior via `snapshot`/`restore` for the return-to-pending case). The panel keeps `_pending_status_lbl`/`_pending_last_status`/`_state` handling.

- [ ] **Step 6: Run the Create per-chip contract tests (must still pass, behavior-identical)**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_result_panel.py -q`
Expected: PASS (the multichip/single-chip/restore/clear tests at 247-295 unchanged in behavior). If a test reads `panel._chip_row_labels`/`panel._pending_chip_box` directly, re-point it at `panel._chip_rows._chip_row_labels` etc., or expose thin pass-through properties — do NOT weaken the assertions.

- [ ] **Step 7: Commit**

```bash
git add app/chip_progress.py app/create_view.py tests/test_chip_progress.py tests/test_create_result_panel.py
git commit -m "refactor(create): extract shared ChipProgressRows (reused by Create + the Stage)"
```

---

## Task 4: LiveRunView — the spine + tiles

**Files:**
- Modify: `app/pipeline_studio.py` (`LiveRunView.begin`, `_build_step_row` → tile builder, the steps container, `_CSS`)
- Test: `tests/test_pipeline_studio.py`

**Interfaces:**
- Consumes: `ProgressState`, `StepView`. Produces: a horizontal `spine` of tile widgets; per-node registries keyed as today (`_step_status_labels` etc.) plus a `_step_tiles: dict[str, Gtk.Widget]` and a `_step_preview: dict[str, Gtk.Widget]` slot for Task 5.

**Context:** Restructure the flat vertical status-row list into a horizontal spine of tiles (done/active/upcoming). Keep the existing per-node widget refs so `on_node_update`/spinner/phase/elapsed keep working; add the tile shell + a preview slot (filled in Task 5). Do NOT add output preview here — this task is the structural redesign only.

- [ ] **Step 1: Write the failing test**

```python
def test_live_run_builds_a_tile_per_step_with_states():
    view = LiveRunView(); view.begin(_make_live_run())
    assert len(view._step_tiles) == 3
    # pending tiles carry the upcoming/ghost class
    assert "ps-tile-upcoming" in view._step_tiles["1"].get_css_classes()
    view.on_node_update("job", "1", "running", "")
    assert "ps-tile-active" in view._step_tiles["1"].get_css_classes()
    view.on_node_update("job", "1", "done", "")
    assert "ps-tile-done" in view._step_tiles["1"].get_css_classes()
```

- [ ] **Step 2: Run to verify it fails** — `KeyError: _step_tiles` / class absent.

- [ ] **Step 3: Build the spine.** Replace `self._steps_box` (VERTICAL) with a horizontal, scrollable `spine` (`Gtk.Box` HORIZONTAL inside a horizontal `Gtk.ScrolledWindow`). Rewrite `_build_step_row` → `_build_step_tile(index, step)` returning the tile + the same widget refs, laid out as a card (number/verb-noun header, status glyph/spinner, phase+elapsed meta, and an empty `preview` slot). Add `self._step_tiles` (node_id→tile) and set the tile's state class. Add a `_set_tile_state(node_id, status)` that swaps `ps-tile-upcoming`/`ps-tile-active`/`ps-tile-done`/`ps-tile-failed`, called from `on_node_update`/`on_finished`. Arrows (→) between tiles are plain `str` labels.

- [ ] **Step 4: CSS** — add `.ps-tile`, `.ps-tile-upcoming` (dashed, dim), `.ps-tile-active` (accent `#4FD1C5` border + subtle glow), `.ps-tile-done` (green `#27AE60` edge), `.ps-tile-failed` (red `#FF6B6B`) to `pipeline_studio.py`'s `_CSS` **bytes literal — ASCII only** (colors as hex, no glyphs inside the bytes).

- [ ] **Step 5: Run to verify pass** (whole `test_pipeline_studio.py`, since the existing LiveRunView tests must still pass with the tile refs).

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_pipeline_studio.py -q`

- [ ] **Step 6: Commit**

```bash
git add app/pipeline_studio.py tests/test_pipeline_studio.py
git commit -m "feat(pipeline): Stage spine — horizontal step tiles with done/active/upcoming states"
```

---

## Task 5: Per-step output preview as it lands + per-chip rows in the active tile

**Files:**
- Modify: `app/pipeline_studio.py` (`LiveRunView`: track `output_dir` from `LOG:`, resolve+render per-step artifact, embed `ChipProgressRows` in the active tile)
- Test: `tests/test_pipeline_studio.py`

**Interfaces:**
- Consumes: `pipeline_view_model._resolve_artifact`, `chip_progress.ChipProgressRows`, `artgen_render` + gallery `AnimatedGifWidget`.

**Context:** This is the making-of core — "see it as it lands." `LiveRunView.on_log` already receives the `LOG:<path>` line; derive `output_dir` from it (same logic the runner uses). When a step goes `done` (or its file appears), resolve its artifact via `_resolve_artifact(output_dir, node_id, step.intent)` and render a thumbnail into that tile's preview slot. Route rendering through existing widgets (artgen kinds → `artgen_render`; gif → `AnimatedGifWidget`; raster/video → a static frame is acceptable here — full playback is the Slice-2 drill-in). Feed `chipN:` lines (now arriving via Task 2) into a `ChipProgressRows` shown in the active tile.

- [ ] **Step 1: Write the failing test**

```python
def test_step_done_renders_a_preview_thumbnail(tmp_path):
    view = LiveRunView(); run = _make_live_run(); view.begin(run)
    # simulate the runner announcing the output dir + a produced artifact
    (tmp_path / "node1.png").write_bytes(b"\x89PNG\r\n\x1a\n")  # a file _resolve_artifact will find
    view.on_log(f"LOG:{tmp_path}/run.log\n")   # LiveRunView derives output_dir
    view.on_node_update("job", "1", "done", "")
    assert view._step_preview.get("1") is not None   # a preview widget was placed

def test_active_tile_shows_per_chip_rows():
    view = LiveRunView(); view.begin(_make_live_run())
    view.on_node_update("job", "1", "running", "")
    view.on_log("  chip0: Step 5/25\n")
    view.on_log("  chip1: Step 6/25\n")
    rows = view._chip_rows_for("1")
    assert rows is not None and set(rows._chip_row_labels) == {0, 1}
```

(Confirm how the runner derives `output_dir` from the `LOG:` path — `pipeline_runner._parse_line` does it at `:73`; mirror that derivation in `LiveRunView` or, cleaner, have the runner also pass the resolved `output_dir` — decide + note in report. The test above assumes `LiveRunView` derives it from the `LOG:` line it already receives.)

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Track `output_dir`** — in `LiveRunView.on_log`, when the line starts `LOG:`, derive+store `self._output_dir` (mirror `pipeline_runner._parse_line`'s derivation). Keep appending the raw line to the log tail as today.

- [ ] **Step 4: Render preview on done** — add `_render_step_preview(node_id)`: look up the `StepView` (store `run.steps` by node_id in `begin`), `path = pipeline_view_model._resolve_artifact(self._output_dir, node_id, step.intent)`; if found, build a small thumbnail (artgen kinds via `artgen_render`; gif via `AnimatedGifWidget`; raster/video via a static `Gtk.Picture`/poster — full playback is Slice 2) and place it in the tile's preview slot (`self._step_preview[node_id]`). Call it from `on_node_update` when `status=="done"` and from `on_finished`'s resolve loop. Fail-soft: any render error → no preview, never crash the run view.

- [ ] **Step 5: Per-chip rows in the active tile** — add a `ChipProgressRows` per running step (`self._chip_rows: dict[str, ChipProgressRows]`, lazily created and embedded in the active tile). In `on_log`, feed every raw line to the running step's rows: `self._chip_rows_for(running_node).feed(text)` (it returns False for non-chip lines — harmless). Add `_chip_rows_for(node_id)`.

- [ ] **Step 6: Run to verify pass.**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_pipeline_studio.py -q`

- [ ] **Step 7: Commit**

```bash
git add app/pipeline_studio.py tests/test_pipeline_studio.py
git commit -m "feat(pipeline): Stage previews each step's output as it lands + per-chip rows in the active tile"
```

---

## Task 6: tensix-viz as the ambient machine

**Files:**
- Modify: `app/pipeline_studio.py` (`LiveRunView`: lazily embed + drive `ActivityVizWidget`; promote board-switch lines out of the collapsed log)
- Test: `tests/test_pipeline_studio.py` + a WebKit-free construction guard

**Interfaces:**
- Consumes: `activity_viz.ActivityVizWidget`, `mode_for_medium`.

**Context:** Embed the tensix-viz as the run's ambient machine, driven by the active step. Mirror `CreateView._ensure_activity_viz` (lazy, try/except, fail-soft). Drive `set_mode` keyed to the active step's output kind and `set_running(True)` for the run; `set_idle()` on done. Promote the board-switch `LOG` lines (`_SWITCH_MARKERS`) so they show as a first-class ambient status next to the viz, not only inside the collapsed "Details" expander.

- [ ] **Step 1: Write the failing test** (WebKit-free — mirror `test_activity_viz.py:227`)

```python
def test_live_run_construction_is_webkit_free():
    view = LiveRunView()
    assert getattr(view, "_activity_viz", None) is None   # not built at construction

def test_live_run_drives_viz_mode_and_running(monkeypatch):
    view = LiveRunView(); view.begin(_make_live_run())
    calls = []
    class _FakeViz:
        def set_mode(self, m=None): calls.append(("mode", getattr(m,"output_kind",m)))
        def set_running(self, b): calls.append(("run", b))
        def set_idle(self): calls.append(("idle", None))
    view._activity_viz = _FakeViz()          # inject (bypass lazy WebKit build)
    view.on_node_update("job", "1", "running", "")
    assert ("run", True) in calls
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Lazily embed the viz** — add `self._activity_viz = None` in `__init__` (do NOT construct it). Add `_ensure_activity_viz()` mirroring `create_view.py:2757` (try/except import+build `ActivityVizWidget`, fail-soft, place it in a corner of the Stage; wire `on_close`). Call it from `begin()` (first run). Drive it: on a step going `running`, `self._activity_viz.set_running(True)` + `set_mode(<medium/kind for that step>)`; on `run-done`, `set_idle()`. Guard every call `if self._activity_viz is not None`.

- [ ] **Step 4: Map active step → viz mode.** The viz's `mode_for_medium` takes a `Medium`; the pipeline has an `Intent.output_kind` instead. Add a tiny `_viz_mode_for_intent(intent) -> str` OR pass a lightweight object exposing `.id`/`.source` so `mode_for_medium` returns the right mode (image→diffusion, video/gif→video, artgen→thinking). Keep it pure + unit-tested.

- [ ] **Step 5: Promote board-switch lines** — when `on_log` sees a `_is_switch_line` line, ALSO surface it as a first-class ambient status label near the viz (not only appended into the collapsed `_log_box`). Keep the raw log tail too.

- [ ] **Step 6: Run to verify pass** (incl. the WebKit-free guard — the Stage must construct without building a WebView, so CI/bwrap holds).

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_pipeline_studio.py -q`

- [ ] **Step 7: Commit**

```bash
git add app/pipeline_studio.py tests/test_pipeline_studio.py
git commit -m "feat(pipeline): tensix-viz as the Stage's ambient machine (lazy, driven by the active step)"
```

---

## Task 7: Stop + no dead-end

**Files:**
- Modify: `app/pipeline_studio.py` (add a Stop control; fix the run-page back destination)
- Test: `tests/test_pipeline_studio.py`

**Interfaces:**
- Consumes: `PipelineRunner.cancel()` (already exists).

**Context:** The run page's `← Back` routes to the `"open"` stack page unconditionally (`pipeline_studio.py:3766`), which is blank/stale for a Muse-launched run (`_current_run_view is None`). Fix: route back to `"discover"` (the run keeps running; its result lands in Discover). Add a **Stop** button that calls the runner's `cancel()` and marks still-running/pending steps as cancelled in the Stage.

- [ ] **Step 1: Write the failing test**

```python
def test_stop_cancels_runner_and_marks_remaining(monkeypatch):
    studio = _make_studio(monkeypatch)   # existing helper / or construct PipelineStudio
    runner = MagicMock()
    studio._runner = runner
    studio._on_stop_run(None)
    runner.cancel.assert_called_once()

def test_run_back_goes_to_discover_not_blank_open(monkeypatch):
    studio = _make_studio(monkeypatch)
    studio._current_run_view = None      # Muse-launched: no opened run
    studio._on_run_back(None)
    assert studio.stack.get_visible_child_name() == "discover"
```

(Use/extend whatever `PipelineStudio` construction helper the file's tests already use; if none, construct `PipelineStudio(...)` with the same fakes its existing tests use.)

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Add a Stop control** to the run page (near the back bar): a `◼ Stop` button → `_on_stop_run` → `self._runner.cancel()` (guard `if self._runner is not None`) + mark remaining steps cancelled in `live_run` (a `LiveRunView.mark_cancelled()` that resolves pending/running tiles to a `cancelled` state). Confirm `PipelineStudio` holds the active runner (it starts `PipelineRunner` in `_on_run_remix`, `:3859` region) — store it as `self._runner` if not already.

- [ ] **Step 4: Fix the back destination** — replace the run page's `_on_back_to_open` binding with `_on_run_back` that routes to `"discover"` (never a blank Open). Keep the run running (do NOT cancel on back — leaving is intentional; only the Stop button cancels).

- [ ] **Step 5: Run to verify pass.**

- [ ] **Step 6: Commit**

```bash
git add app/pipeline_studio.py tests/test_pipeline_studio.py
git commit -m "feat(pipeline): Stage Stop (runner.cancel) + fix the back dead-end (route to Discover)"
```

---

## Task 8: Finalize — version, changelog, docs, full suite, manual check

**Files:**
- Modify: `VERSION`, `debian/changelog`, `CLAUDE.md`

- [ ] **Step 1: Bump `VERSION`** — minor bump from the current value (read `VERSION`; e.g. `0.78.0` → `0.79.0`).

- [ ] **Step 2: Prepend a `debian/changelog` stanza** — summarize: pipeline live run is now a "making-of" — recipe spine with done/active/upcoming tiles, per-step output preview as it lands, per-chip AnimateDiff rows (now streamed into the pipeline via the engine `LOG:` seam + shared `ChipProgressRows`), tensix-viz as the ambient machine, done-counting progress, Stop, and the back dead-end fixed. Match the existing stanza trailer format.

- [ ] **Step 3: Update `CLAUDE.md`** — a "Pipeline Stage — live making-of" note: `LiveRunView` spine/tiles + per-step preview; the `_run_tt_ctl(emit=...)` streaming seam that carries AnimateDiff `chipN:` lines into the pipeline as `LOG:` lines; the extracted `app/chip_progress.py::ChipProgressRows` shared by Create + the Stage; the lazily-built tensix-viz embed; the palette (main scheme, `#4FD1C5`/`#0F2A35`). Note this is Slice 1 of [[project_stage_pipeline_direction]]; drill-in + Create polish + compose front-door are later slices.

- [ ] **Step 4: Full suite (documented deselects)**

```bash
xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q \
  --deselect tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module \
  --deselect tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes \
  --deselect tests/test_role_zone_panel.py::test_prompt_field_hidden_but_still_collected_for_artgen
```

Expected: green. Fix any real regression before committing.

- [ ] **Step 5: Commit**

```bash
git add VERSION debian/changelog CLAUDE.md
git commit -m "chore: VERSION + changelog + docs for the pipeline Stage making-of (Slice 1)"
```

- [ ] **Step 6: Manual check (user, on the real display — NOT automated)**

Document as the acceptance pass: run a multi-step pipeline (ideally one ending in AnimateDiff) and confirm — the spine fills left→right; each step shows its output as it lands; the active AnimateDiff step shows per-chip rows; the tensix-viz pulses with the active step; "Step N of M" counts done; Stop cancels; leaving (back) goes to Discover and the run keeps going, its result landing there. Watch for the fragile chip during any AnimateDiff run.

---

## Self-Review (plan author)

**Spec coverage:** §A spine/done-count → T1, T4. §B per-step preview → T5 (+ `_resolve_artifact` reuse). §C per-chip preserved → T2 (engine seam, the confirmed non-UI part) + T3 (shared widget) + T5 (embed). §D tensix-viz ambient → T6. §E Stop + no dead-end → T7. Testing/palette/lazy-WebKit/collect-untouched → global constraints + per-task tests. ✓

**Placeholder scan:** The three "confirm at plan time" notes (streaming timeout in T2, `output_dir` derivation in T5, runner-handle location in T7) are concrete verify-then-implement instructions with a named fallback + a report requirement — not vague TBDs. All code blocks are real, from the extraction.

**Type/name consistency:** `ChipProgressRows.feed/reset/snapshot/restore` used consistently across T3/T5. `ProgressState.completed`/`done_count` across T1. `_run_tt_ctl(emit=)` across T2. `_step_tiles`/`_step_preview`/`_chip_rows`/`_activity_viz`/`_output_dir` introduced in T4-T6 and reused consistently. `PipelineRunner.cancel()` (existing) in T7.
