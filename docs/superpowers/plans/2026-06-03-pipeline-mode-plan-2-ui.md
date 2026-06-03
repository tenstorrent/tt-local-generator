# Pipeline Mode — Plan 2: UI Layer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Pipeline as a 5th source tab with a full GTK4 UI: batch job template table, phase progress grid, Configure/History left pane tabs, and detail pane wiring — all backed by the Plan 1 `PipelineRunner`/`PipelineStore` foundation.

**Architecture:** `PipelinePanel` (new file) is a `Gtk.Box` that replaces the left control pane when the Pipeline source tab is active. `PhaseGridWidget` (new file) lives in the center pane gallery stack. `MainWindow._on_source_change` gains a `"pipeline"` branch that hides the ControlPanel and shows `PipelinePanel` + `PhaseGridWidget`. The right `DetailPanel` is reused as-is — clicking a grid cell calls the existing `_on_card_selected` path.

**Tech Stack:** Python 3.12, GTK4 via PyGObject, `Gtk.ColumnView` for the job table, `Gtk.Grid` for the phase grid, `Gtk.Stack`/`Gtk.StackSwitcher` for Configure/History tabs. No new dependencies.

---

## File structure

| File | Role |
|---|---|
| `app/pipeline_panel.py` | `PipelinePanel` — left pane: spec picker, template+job table, Configure/History tabs, run controls |
| `app/phase_grid_widget.py` | `PhaseGridWidget` — center pane: live phase progress grid with clickable cells |
| `app/main_window.py` | Add Pipeline tab to toolbar, `_set_source("pipeline")` branch, gallery stack entry, detail wiring |
| `tests/test_pipeline_panel.py` | Unit tests for PipelinePanel state logic (no display needed) |
| `tests/test_phase_grid_widget.py` | Unit tests for PhaseGridWidget cell state updates |

---

## Task 1: CSS for Pipeline tab and phase grid

**Files:**
- Modify: `app/main_window.py` (CSS block, ~line 460)

Add CSS for the new widgets alongside existing source-btn, servers-menu-btn styles.

- [ ] **Step 1: Locate CSS block**

```bash
grep -n "source-btn\|servers-menu-btn\|workflow-btn" app/main_window.py | head -5
```

- [ ] **Step 2: Add CSS after the existing `/* -- Workflow button */` block**

```python
        css += """

/* -- Pipeline tab ---------------------------------------------------------- */
button.pipeline-source-btn {
    background-color: @tt_bg_dark;
    color: @tt_accent;
    border: 1px solid @tt_accent;
    border-radius: 4px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
}
button.pipeline-source-btn:hover {
    background-color: @tt_bg_medium;
}

/* -- Phase grid ------------------------------------------------------------ */
.phase-grid-header {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: @tt_text_muted;
    padding: 2px 4px;
}
.phase-cell-pending   { background-color: @tt_bg_dark;    border: 1px solid rgba(79,209,197,.1);  border-radius: 4px; }
.phase-cell-running   { background-color: #1a2a3a;        border: 2px solid @tt_accent;           border-radius: 4px; }
.phase-cell-done      { background-color: #1a3a20;        border: 2px solid @tt_success;          border-radius: 4px; }
.phase-cell-failed    { background-color: #3a1a1a;        border: 2px solid @tt_error;            border-radius: 4px; cursor: pointer; }
.phase-cell-skipped   { background-color: #2a2010;        border: 1px solid rgba(244,196,113,.4); border-radius: 4px; }
.phase-cell-selected  { outline: 2px solid @tt_accent; outline-offset: 1px; }
.phase-job-label      { font-size: 10px; font-weight: 700; color: @tt_text; }
.phase-job-sublabel   { font-size: 9px;  color: @tt_text_muted; }
"""
```

- [ ] **Step 3: Run tests — no regressions**

```bash
/usr/bin/python3 -m pytest tests/ -q --tb=no 2>/dev/null | tail -2
```

- [ ] **Step 4: Commit**

```bash
git add app/main_window.py
git commit -m "feat: CSS for Pipeline source tab and phase grid cells"
```

---

## Task 2: PhaseGridWidget

**Files:**
- Create: `app/phase_grid_widget.py`
- Create: `tests/test_phase_grid_widget.py`

`PhaseGridWidget` renders the N-jobs × M-phases grid. It is **not** a live GTK display in tests — test the state logic directly without instantiating widgets.

- [ ] **Step 1: Write failing tests**

Create `tests/test_phase_grid_widget.py`:

```python
"""Tests for PhaseGridWidget state logic — no GTK display required."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def make_grid_state():
    """Return a fresh grid state dict — the pure-data model for PhaseGridWidget."""
    from phase_grid_widget import GridState
    return GridState(
        jobs=["1964-ny", "1939-ny"],
        phases=[
            {"id": "1", "label": "Seed",  "model": "FLUX"},
            {"id": "4", "label": "Video", "model": "SkyReels"},
            {"id": "5", "label": "Poem",  "model": "Llama"},
        ]
    )


def test_initial_cells_are_pending(make_grid_state=make_grid_state):
    gs = make_grid_state()
    assert gs.cell("1964-ny", "1")["status"] == "pending"
    assert gs.cell("1939-ny", "4")["status"] == "pending"


def test_update_cell_to_running(make_grid_state=make_grid_state):
    gs = make_grid_state()
    gs.update("1964-ny", "1", "running", "FLUX.1-schnell")
    assert gs.cell("1964-ny", "1")["status"] == "running"
    assert gs.cell("1964-ny", "1")["detail"] == "FLUX.1-schnell"


def test_update_cell_to_done(make_grid_state=make_grid_state):
    gs = make_grid_state()
    gs.update("1964-ny", "1", "done", "/tmp/node1.png")
    c = gs.cell("1964-ny", "1")
    assert c["status"] == "done"
    assert c["detail"] == "/tmp/node1.png"


def test_update_cell_to_failed(make_grid_state=make_grid_state):
    gs = make_grid_state()
    gs.update("1964-ny", "4", "failed", "SkyReels OOM")
    assert gs.cell("1964-ny", "4")["status"] == "failed"


def test_update_cell_to_skipped(make_grid_state=make_grid_state):
    gs = make_grid_state()
    gs.update("1970-osaka", "2", "skipped", "fog/exterior")
    # Job not in initial list — update creates it
    assert gs.cell("1970-osaka", "2")["status"] == "skipped"


def test_cells_for_job(make_grid_state=make_grid_state):
    gs = make_grid_state()
    gs.update("1964-ny", "1", "done", "/tmp/a.png")
    gs.update("1964-ny", "4", "running", "SkyReels")
    cells = gs.cells_for_job("1964-ny")
    assert cells["1"]["status"] == "done"
    assert cells["4"]["status"] == "running"
    assert cells["5"]["status"] == "pending"


def test_health_cell_is_special(make_grid_state=make_grid_state):
    gs = make_grid_state()
    gs.update("__health__", "__chips__", "degraded", "AC power cycle recommended")
    # Health signals are stored separately, not as a regular cell
    assert gs.health_status() == "degraded"
    assert gs.health_detail() == "AC power cycle recommended"


def test_load_from_store_record(make_grid_state=make_grid_state):
    """GridState can be populated from a PipelineStore run record."""
    from phase_grid_widget import GridState
    run_record = {
        "jobs": [{"name": "1964-ny"}, {"name": "1939-ny"}],
        "job_states": {
            "1964-ny": {
                "1": {"status": "done",    "detail": "/tmp/a.png", "elapsed_s": 3.1},
                "4": {"status": "running", "detail": "SkyReels",   "elapsed_s": 0.0},
            },
            "1939-ny": {
                "1": {"status": "done",    "detail": "/tmp/b.png", "elapsed_s": 3.0},
            }
        }
    }
    phases = [{"id": "1", "label": "Seed", "model": "FLUX"},
              {"id": "4", "label": "Video", "model": "SkyReels"}]
    gs = GridState.from_run_record(run_record, phases)
    assert gs.cell("1964-ny", "1")["status"] == "done"
    assert gs.cell("1964-ny", "4")["status"] == "running"
    assert gs.cell("1939-ny", "4")["status"] == "pending"  # not in job_states
```

- [ ] **Step 2: Run — expect ModuleNotFoundError**

```bash
/usr/bin/python3 -m pytest tests/test_phase_grid_widget.py -v 2>&1 | head -5
```

- [ ] **Step 3: Implement `app/phase_grid_widget.py`**

```python
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
PhaseGridWidget — live phase progress grid for Pipeline mode.

Two parts:
  GridState — pure Python data model for cell states; no GTK dependency.
              Testable without a display. Updated by PipelineRunner callbacks.
  PhaseGridWidget — GTK4 widget that renders GridState as a scrollable grid.
                    Job names are sticky on the left column.
                    Each completed cell is clickable → loads artifact in detail pane.
"""
from __future__ import annotations

from typing import Callable, Optional

# ── GridState (pure data, no GTK) ────────────────────────────────────────────

_TERMINAL = {"done", "failed", "skipped"}
_PENDING = {"status": "pending", "detail": ""}


class GridState:
    """Mutable state model for the phase grid.

    jobs:   list of job names (row identifiers)
    phases: list of {id, label, model} dicts (column identifiers, in display order)
    """

    def __init__(self, jobs: list[str], phases: list[dict]) -> None:
        self.jobs = list(jobs)
        self.phases = list(phases)
        self._cells: dict[str, dict[str, dict]] = {}  # job_name → node_id → {status, detail}
        self._health_status: Optional[str] = None
        self._health_detail: str = ""
        # Initialise all cells to pending
        for job in jobs:
            self._cells[job] = {}

    def update(self, job_name: str, node_id: str, status: str, detail: str = "") -> None:
        """Update a cell. Creates the job row if it doesn't exist yet."""
        if job_name == "__health__":
            self._health_status = status
            self._health_detail = detail
            return
        if job_name not in self._cells:
            self._cells[job_name] = {}
        self._cells[job_name][node_id] = {"status": status, "detail": detail}

    def cell(self, job_name: str, node_id: str) -> dict:
        """Return cell state dict. Returns _PENDING for unknown cells."""
        return self._cells.get(job_name, {}).get(node_id, dict(_PENDING))

    def cells_for_job(self, job_name: str) -> dict[str, dict]:
        """Return all cells for a job, filling in pending for unknown phase IDs."""
        phase_ids = {p["id"] for p in self.phases}
        row = self._cells.get(job_name, {})
        return {pid: row.get(pid, dict(_PENDING)) for pid in phase_ids}

    def health_status(self) -> Optional[str]:
        return self._health_status

    def health_detail(self) -> str:
        return self._health_detail

    @classmethod
    def from_run_record(cls, run: dict, phases: list[dict]) -> "GridState":
        """Populate from a PipelineStore run record (for history display)."""
        jobs = [j["name"] for j in run.get("jobs", [])]
        gs = cls(jobs=jobs, phases=phases)
        for job_name, node_states in run.get("job_states", {}).items():
            for node_id, state in node_states.items():
                gs.update(job_name, node_id,
                          state.get("status", "pending"),
                          state.get("detail", ""))
        return gs


# ── PhaseGridWidget (GTK) ─────────────────────────────────────────────────────

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
    _GTK_AVAILABLE = True
except ImportError:
    _GTK_AVAILABLE = False


if _GTK_AVAILABLE:
    class PhaseGridWidget(Gtk.Box):
        """
        Scrollable phase progress grid.

        Layout:
            [sticky job col] [phase1] [phase2] ... [phaseN] [↺ retry]

        Cell states drive CSS classes: phase-cell-{pending,running,done,failed,skipped}.
        Clicking a done/failed cell calls on_cell_click(job_name, node_id, detail).
        Clicking a failed cell also shows an inline retry affordance.
        """

        def __init__(
            self,
            state: GridState,
            on_cell_click: Optional[Callable[[str, str, str], None]] = None,
            on_retry_node: Optional[Callable[[str, str], None]] = None,
            on_retry_job: Optional[Callable[[str], None]] = None,
        ) -> None:
            super().__init__(orientation=Gtk.Orientation.VERTICAL)
            self._state = state
            self._on_cell_click = on_cell_click
            self._on_retry_node = on_retry_node
            self._on_retry_job = on_retry_job
            self._cell_widgets: dict[tuple[str, str], Gtk.Widget] = {}
            self._build()

        def _build(self) -> None:
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
            scroll.set_vexpand(True)
            self.append(scroll)

            grid = Gtk.Grid()
            grid.set_column_spacing(3)
            grid.set_row_spacing(3)
            grid.set_margin_start(12)
            grid.set_margin_end(12)
            grid.set_margin_top(10)
            grid.set_margin_bottom(8)
            scroll.set_child(grid)

            # Column headers
            job_hdr = Gtk.Label(label="Job")
            job_hdr.add_css_class("phase-grid-header")
            job_hdr.set_xalign(0)
            job_hdr.set_size_request(110, -1)
            grid.attach(job_hdr, 0, 0, 1, 1)

            for col, phase in enumerate(self._state.phases, start=1):
                hdr = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
                name_lbl = Gtk.Label(label=phase["label"])
                name_lbl.add_css_class("phase-grid-header")
                model_lbl = Gtk.Label(label=phase.get("model", ""))
                model_lbl.add_css_class("phase-grid-header")
                model_lbl.set_markup(
                    f'<span foreground="#4fd1c5" size="xx-small">{phase.get("model","")}</span>'
                )
                hdr.append(name_lbl)
                hdr.append(model_lbl)
                hdr.set_size_request(52, -1)
                grid.attach(hdr, col, 0, 1, 1)

            # Retry-all column header
            grid.attach(Gtk.Label(label=""), len(self._state.phases) + 1, 0, 1, 1)

            # Job rows
            for row_idx, job_name in enumerate(self._state.jobs, start=1):
                job_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
                job_box.set_size_request(110, -1)
                job_lbl = Gtk.Label(label=job_name)
                job_lbl.set_xalign(0)
                job_lbl.add_css_class("phase-job-label")
                job_lbl.set_ellipsize(3)
                job_box.append(job_lbl)
                grid.attach(job_box, 0, row_idx, 1, 1)

                for col, phase in enumerate(self._state.phases, start=1):
                    cell_state = self._state.cell(job_name, phase["id"])
                    cell = self._make_cell(job_name, phase["id"], cell_state)
                    self._cell_widgets[(job_name, phase["id"])] = cell
                    grid.attach(cell, col, row_idx, 1, 1)

                # Per-job retry button
                retry_btn = Gtk.Button(label="↺")
                retry_btn.set_tooltip_text("Retry job from first failure")
                retry_btn.add_css_class("flat")
                retry_btn.set_sensitive(False)
                retry_btn.job_name = job_name  # type: ignore[attr-defined]
                retry_btn.connect("clicked", self._on_retry_job_clicked)
                grid.attach(retry_btn, len(self._state.phases) + 1, row_idx, 1, 1)

        def _make_cell(self, job_name: str, node_id: str, state: dict) -> Gtk.Box:
            status = state.get("status", "pending")
            detail = state.get("detail", "")
            cell = Gtk.Box()
            cell.set_size_request(52, 28)
            cell.set_halign(Gtk.Align.CENTER)
            cell.set_valign(Gtk.Align.CENTER)
            cell.add_css_class(f"phase-cell-{status}")

            label_text = {
                "pending":  "",
                "running":  "⏳",
                "done":     "✓",
                "failed":   "✗",
                "skipped":  "skip",
            }.get(status, "")
            lbl = Gtk.Label(label=label_text)
            lbl.set_halign(Gtk.Align.CENTER)
            cell.append(lbl)

            if status in ("done", "failed"):
                gesture = Gtk.GestureClick()
                gesture.connect(
                    "pressed",
                    lambda g, n, x, y, jn=job_name, nid=node_id, det=detail:
                        self._on_cell_clicked(jn, nid, det)
                )
                cell.add_controller(gesture)

            return cell

        def update_cell(self, job_name: str, node_id: str, status: str, detail: str) -> None:
            """Called on GTK main thread by PipelineRunner callback. Updates state + widget."""
            self._state.update(job_name, node_id, status, detail)
            key = (job_name, node_id)
            if key not in self._cell_widgets:
                return
            old_cell = self._cell_widgets[key]
            parent = old_cell.get_parent()
            if parent is None:
                return
            # Rebuild the cell widget in place
            new_cell = self._make_cell(job_name, node_id, {"status": status, "detail": detail})
            # GTK4: replace child in Grid by position
            # Find the grid and column/row of this cell
            grid = parent
            # We rebuild by removing old and attaching new at same position
            # Simpler: just update CSS class and label in-place
            for css in ["phase-cell-pending", "phase-cell-running", "phase-cell-done",
                        "phase-cell-failed", "phase-cell-skipped"]:
                old_cell.remove_css_class(css)
            old_cell.add_css_class(f"phase-cell-{status}")
            child = old_cell.get_first_child()
            if isinstance(child, Gtk.Label):
                child.set_label({"done": "✓", "failed": "✗", "running": "⏳",
                                  "skipped": "skip", "pending": ""}.get(status, ""))
            # Wire click handler for done/failed
            if status in ("done", "failed"):
                gesture = Gtk.GestureClick()
                gesture.connect(
                    "pressed",
                    lambda g, n, x, y, jn=job_name, nid=node_id, det=detail:
                        self._on_cell_clicked(jn, nid, det)
                )
                old_cell.add_controller(gesture)

        def _on_cell_clicked(self, job_name: str, node_id: str, detail: str) -> None:
            if self._on_cell_click:
                self._on_cell_click(job_name, node_id, detail)

        def _on_retry_job_clicked(self, btn: Gtk.Button) -> None:
            if self._on_retry_job:
                self._on_retry_job(btn.job_name)  # type: ignore[attr-defined]
```

- [ ] **Step 4: Run tests — all 9 must pass**

```bash
/usr/bin/python3 -m pytest tests/test_phase_grid_widget.py -v
```

- [ ] **Step 5: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ -q --tb=short 2>/dev/null | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add app/phase_grid_widget.py tests/test_phase_grid_widget.py
git commit -m "feat: PhaseGridWidget + GridState data model for Pipeline phase grid"
```

---

## Task 3: PipelinePanel

**Files:**
- Create: `app/pipeline_panel.py`
- Create: `tests/test_pipeline_panel.py`

`PipelinePanel` is the left control pane for Pipeline mode. It has two tabs: Configure (spec picker + template + job table + run button) and History (past runs list). Both are wired to `PipelineRunner` and `PipelineStore`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_pipeline_panel.py`:

```python
"""Tests for PipelinePanel state logic."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def test_parse_template_variables():
    """Template parser extracts {variable} tokens."""
    from pipeline_panel import parse_template_variables
    assert parse_template_variables("{era} World's Fair, {subject}, {style}") == ["era", "subject", "style"]
    assert parse_template_variables("no variables here") == []
    assert parse_template_variables("{a} {a} {b}") == ["a", "b"]  # deduped, order preserved


def test_resolve_prompt_from_row():
    """Variable row + template → resolved prompt string."""
    from pipeline_panel import resolve_prompt
    template = "{era} World's Fair, {subject}"
    row = {"name": "1964", "era": "1964", "subject": "IBM Wall"}
    assert resolve_prompt(template, row) == "1964 World's Fair, IBM Wall"


def test_resolve_prompt_missing_variable():
    """Missing variable left as-is in resolved prompt."""
    from pipeline_panel import resolve_prompt
    assert resolve_prompt("{era} test {missing}", {"era": "1964"}) == "1964 test {missing}"


def test_resolve_prompt_custom_override():
    """Row with __custom__ flag uses prompt field directly."""
    from pipeline_panel import resolve_prompt
    row = {"name": "test", "__custom__": True, "prompt": "a custom prompt here"}
    assert resolve_prompt("{era} template", row) == "a custom prompt here"


def test_jobs_to_runner_format():
    """Job rows → list[dict] suitable for PipelineRunner.start()."""
    from pipeline_panel import jobs_to_runner_format
    template = "{era} World's Fair"
    rows = [
        {"name": "1964 NY", "era": "1964"},
        {"name": "custom", "__custom__": True, "prompt": "a direct prompt"},
    ]
    result = jobs_to_runner_format(template, rows)
    assert result[0] == {"name": "1964 NY", "prompt": "1964 World's Fair"}
    assert result[1] == {"name": "custom", "prompt": "a direct prompt"}


def test_jobs_to_runner_format_skips_disabled():
    """Disabled rows (enabled=False) are excluded."""
    from pipeline_panel import jobs_to_runner_format
    rows = [
        {"name": "a", "era": "1964", "enabled": True},
        {"name": "b", "era": "1939", "enabled": False},
    ]
    result = jobs_to_runner_format("{era}", rows)
    assert len(result) == 1
    assert result[0]["name"] == "a"


def test_phases_from_spec():
    """Workflow JSON spec → phase list for PhaseGridWidget."""
    from pipeline_panel import phases_from_spec
    import json, tempfile
    spec = {
        "_description": "test",
        "1": {"class_type": "TTLGTextToImage", "_comment": "seed", "inputs": {}, "outputs": ["image_path"]},
        "4": {"class_type": "TTLGImageToVideo", "_comment": "video", "inputs": {}, "outputs": ["video_path"]},
        "9": {"class_type": "TTLGAddToPlaylist", "_comment": "save", "inputs": {}, "outputs": []},
    }
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(spec, f)
        path = f.name
    phases = phases_from_spec(path)
    assert len(phases) == 3
    assert phases[0]["id"] == "1"
    assert phases[0]["label"] == "Seed"  # from _comment, capitalised
    assert phases[1]["id"] == "4"
```

- [ ] **Step 2: Run — expect ModuleNotFoundError**

```bash
/usr/bin/python3 -m pytest tests/test_pipeline_panel.py -v 2>&1 | head -5
```

- [ ] **Step 3: Implement pure-Python helpers in `app/pipeline_panel.py`**

```python
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
PipelinePanel — left control pane for Pipeline source mode.

Pure-Python helpers (parse_template_variables, resolve_prompt,
jobs_to_runner_format, phases_from_spec) are at the top level and
imported by tests without needing a display.

PipelinePanel (GTK class) is defined below; it is only instantiated when
GTK is available.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional


# ── Pure Python helpers (testable without GTK) ────────────────────────────────

def parse_template_variables(template: str) -> list[str]:
    """Return ordered unique list of {variable} names in template."""
    seen: list[str] = []
    for m in re.finditer(r'\{(\w+)\}', template):
        name = m.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def resolve_prompt(template: str, row: dict) -> str:
    """Substitute row variable values into template.

    If row has __custom__=True, returns row['prompt'] directly.
    Missing variables are left as {variable} in the output.
    """
    if row.get("__custom__"):
        return row.get("prompt", "")
    result = template
    for key, value in row.items():
        if key not in ("name", "enabled", "__custom__", "prompt"):
            result = result.replace(f"{{{key}}}", str(value))
    return result


def jobs_to_runner_format(template: str, rows: list[dict]) -> list[dict]:
    """Convert job table rows to PipelineRunner.start() jobs format.

    Skips rows with enabled=False (default enabled=True).
    Returns list[{"name": str, "prompt": str}].
    """
    result = []
    for row in rows:
        if not row.get("enabled", True):
            continue
        result.append({
            "name": row["name"],
            "prompt": resolve_prompt(template, row),
        })
    return result


def phases_from_spec(spec_path: str) -> list[dict]:
    """Parse a workflow JSON spec and return phase list for PhaseGridWidget.

    Returns list[{"id": str, "label": str, "model": str}] in node-ID order.
    Label is derived from node _comment field (capitalised first word).
    Model is derived from class_type (TTLGTextToImage → FLUX, etc.)
    """
    _CLASS_TO_MODEL = {
        "TTLGTextToImage":      "FLUX",
        "TTLGImageToVideo":     "SkyReels",
        "TTLGGenerateText":     "Llama",
        "TTLGCaptionImage":     "BLIP",
        "TTLGRemoveBackground": "RMBG",
        "TTLGEstimateDepth":    "GLPN",
        "TTLGPromptCompose":    "compose",
        "TTLGAddToPlaylist":    "save",
        "TTLGComposite":        "composite",
        "TTLGSVGRender":        "svg",
    }
    try:
        data = json.loads(Path(spec_path).read_text())
    except Exception:
        return []

    phases = []
    for node_id in sorted(data.keys(), key=lambda k: int(k) if k.isdigit() else 999):
        if node_id.startswith("_"):
            continue
        node = data[node_id]
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        comment = node.get("_comment", "")
        # Label: first word of _comment, capitalised; fallback to class_type suffix
        if comment:
            label = comment.split()[0].rstrip(",:").capitalize()
        else:
            label = class_type.replace("TTLG", "")
        model = _CLASS_TO_MODEL.get(class_type, class_type.replace("TTLG", ""))
        phases.append({"id": node_id, "label": label, "model": model})
    return phases


# ── GTK PipelinePanel ─────────────────────────────────────────────────────────

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
    _GTK_AVAILABLE = True
except ImportError:
    _GTK_AVAILABLE = False


if _GTK_AVAILABLE:
    import threading
    from pipeline_runner import PipelineRunner
    from pipeline_store import PipelineStore
    from phase_grid_widget import GridState

    class PipelinePanel(Gtk.Box):
        """
        Left control pane for Pipeline source mode.

        Sections (via Gtk.Stack + Gtk.StackSwitcher):
          Configure tab — spec picker, template entry, job table, param overrides, Run button
          History tab   — scrollable list of past runs; clicking a run loads its
                          job config into Configure AND its grid state via on_load_run callback

        Signals out:
          on_run(jobs, spec_path, param_overrides)  — user clicked Run
          on_cancel()                               — user clicked Cancel
          on_load_run(run_id)                       — user selected a history entry
        """

        def __init__(
            self,
            on_run: Optional[Callable] = None,
            on_cancel: Optional[Callable] = None,
            on_load_run: Optional[Callable] = None,
        ) -> None:
            super().__init__(orientation=Gtk.Orientation.VERTICAL)
            self._on_run = on_run
            self._on_cancel = on_cancel
            self._on_load_run = on_load_run
            self._store = PipelineStore()
            self._runner: Optional[PipelineRunner] = None
            self._spec_path: Optional[str] = None
            self._running = False

            # Mutable job table state
            self._template: str = ""
            self._rows: list[dict] = []  # [{name, enabled, ...variables...}]

            self._build()

        # ── Build ─────────────────────────────────────────────────────────────

        def _build(self) -> None:
            self.set_size_request(310, -1)

            # Tab switcher
            switcher = Gtk.StackSwitcher()
            switcher.set_margin_start(8)
            switcher.set_margin_end(8)
            switcher.set_margin_top(6)
            switcher.set_margin_bottom(4)
            self.append(switcher)

            self._stack = Gtk.Stack()
            self._stack.set_vexpand(True)
            self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
            self._stack.set_transition_duration(120)
            switcher.set_stack(self._stack)
            self.append(self._stack)

            self._stack.add_titled(self._build_configure_tab(), "configure", "Configure")
            self._stack.add_titled(self._build_history_tab(),   "history",   "History")

        def _build_configure_tab(self) -> Gtk.Box:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.set_margin_start(10)
            box.set_margin_end(10)
            box.set_margin_top(8)
            box.set_margin_bottom(8)

            # Spec picker
            spec_lbl = Gtk.Label(label="WORKFLOW SPEC")
            spec_lbl.set_xalign(0)
            spec_lbl.add_css_class("section-label")
            box.append(spec_lbl)

            self._spec_dd = Gtk.DropDown()
            self._spec_dd.set_hexpand(True)
            self._spec_dd.connect("notify::selected", self._on_spec_changed)
            box.append(self._spec_dd)

            # Template
            tmpl_lbl = Gtk.Label(label="PROMPT TEMPLATE")
            tmpl_lbl.set_xalign(0)
            tmpl_lbl.add_css_class("section-label")
            tmpl_lbl.set_margin_top(4)
            box.append(tmpl_lbl)

            self._template_entry = Gtk.Entry()
            self._template_entry.set_placeholder_text("{era} World's Fair, {subject}, {style}, cinematic")
            self._template_entry.set_hexpand(True)
            self._template_entry.connect("changed", self._on_template_changed)
            box.append(self._template_entry)

            self._var_hint = Gtk.Label(label="")
            self._var_hint.set_xalign(0)
            self._var_hint.add_css_class("muted")
            box.append(self._var_hint)

            # Job table
            jobs_lbl = Gtk.Label(label="BATCH JOBS")
            jobs_lbl.set_xalign(0)
            jobs_lbl.add_css_class("section-label")
            jobs_lbl.set_margin_top(4)
            box.append(jobs_lbl)

            self._job_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_min_content_height(100)
            scroll.set_max_content_height(220)
            scroll.set_child(self._job_list_box)
            box.append(scroll)

            add_btn = Gtk.Button(label="+ Add job")
            add_btn.add_css_class("flat")
            add_btn.connect("clicked", lambda _: self._add_row())
            box.append(add_btn)

            # Param overrides
            params_lbl = Gtk.Label(label="PARAMETERS")
            params_lbl.set_xalign(0)
            params_lbl.add_css_class("section-label")
            params_lbl.set_margin_top(4)
            box.append(params_lbl)

            params_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            for label, placeholder, attr in [("num_frames", "33", "_frames_entry"),
                                              ("seed",       "auto", "_seed_entry")]:
                col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                lbl = Gtk.Label(label=label)
                lbl.set_xalign(0)
                lbl.add_css_class("muted")
                entry = Gtk.Entry()
                entry.set_placeholder_text(placeholder)
                entry.set_hexpand(True)
                col.append(lbl)
                col.append(entry)
                setattr(self, attr, entry)
                params_row.append(col)
            box.append(params_row)

            # Spacer
            spacer = Gtk.Box()
            spacer.set_vexpand(True)
            box.append(spacer)

            # Run / Cancel button
            self._run_btn = Gtk.Button(label="▶  Run Pipeline")
            self._run_btn.add_css_class("suggested-action")
            self._run_btn.connect("clicked", self._on_run_clicked)
            box.append(self._run_btn)

            self._status_lbl = Gtk.Label(label="")
            self._status_lbl.set_xalign(0)
            self._status_lbl.add_css_class("muted")
            self._status_lbl.set_wrap(True)
            self._status_lbl.set_max_width_chars(36)
            box.append(self._status_lbl)

            self._populate_specs()
            return box

        def _build_history_tab(self) -> Gtk.Box:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            box.set_margin_start(10)
            box.set_margin_end(10)
            box.set_margin_top(8)
            box.set_margin_bottom(8)

            hdr = Gtk.Label(label="RECENT RUNS")
            hdr.set_xalign(0)
            hdr.add_css_class("section-label")
            box.append(hdr)

            self._history_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_vexpand(True)
            scroll.set_child(self._history_list)
            box.append(scroll)

            self._refresh_history()
            return box

        # ── Spec management ───────────────────────────────────────────────────

        def _populate_specs(self) -> None:
            from workflow_popover import _discover_specs
            self._specs = _discover_specs()
            sl = Gtk.StringList()
            for s in self._specs:
                sl.append(f"{s['name']}  ({s['nodes']} steps)")
            self._spec_dd.set_model(sl)
            if self._specs:
                self._spec_dd.set_selected(0)
                self._spec_path = self._specs[0]["path"]

        def _on_spec_changed(self, dd: Gtk.DropDown, _pspec) -> None:
            idx = dd.get_selected()
            if idx < len(self._specs):
                self._spec_path = self._specs[idx]["path"]

        # ── Template management ───────────────────────────────────────────────

        def _on_template_changed(self, entry: Gtk.Entry) -> None:
            self._template = entry.get_text()
            vars_ = parse_template_variables(self._template)
            if vars_:
                self._var_hint.set_label(f"Variables: {' · '.join(vars_)}")
            else:
                self._var_hint.set_label("")
            self._rebuild_job_rows()

        # ── Job row management ────────────────────────────────────────────────

        def _rebuild_job_rows(self) -> None:
            for child in list(self._job_list_box):
                self._job_list_box.remove(child)
            for row in self._rows:
                self._job_list_box.append(self._make_job_row_widget(row))

        def _make_job_row_widget(self, row: dict) -> Gtk.Box:
            vars_ = parse_template_variables(self._template)
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            box.set_margin_top(1)

            # Enable toggle
            chk = Gtk.CheckButton()
            chk.set_active(row.get("enabled", True))
            chk.connect("toggled", lambda b, r=row: r.update({"enabled": b.get_active()}))
            box.append(chk)

            # Name entry
            name_entry = Gtk.Entry()
            name_entry.set_text(row.get("name", ""))
            name_entry.set_size_request(70, -1)
            name_entry.connect("changed", lambda e, r=row: r.update({"name": e.get_text()}))
            box.append(name_entry)

            if row.get("__custom__"):
                prompt_entry = Gtk.Entry()
                prompt_entry.set_text(row.get("prompt", ""))
                prompt_entry.set_hexpand(True)
                prompt_entry.set_placeholder_text("custom prompt…")
                prompt_entry.add_css_class("pipeline-custom-prompt")
                prompt_entry.connect("changed", lambda e, r=row: r.update({"prompt": e.get_text()}))
                box.append(prompt_entry)
            else:
                for var in vars_:
                    var_entry = Gtk.Entry()
                    var_entry.set_text(str(row.get(var, "")))
                    var_entry.set_size_request(55, -1)
                    var_entry.set_placeholder_text(var)
                    var_entry.connect("changed",
                                      lambda e, r=row, v=var: r.update({v: e.get_text()}))
                    box.append(var_entry)

            # Delete button
            del_btn = Gtk.Button(label="×")
            del_btn.add_css_class("flat")
            del_btn.connect("clicked", lambda _, r=row: self._remove_row(r))
            box.append(del_btn)
            return box

        def _add_row(self) -> None:
            vars_ = parse_template_variables(self._template)
            self._rows.append({"name": f"job {len(self._rows)+1}", "enabled": True,
                                **{v: "" for v in vars_}})
            self._rebuild_job_rows()

        def _remove_row(self, row: dict) -> None:
            self._rows = [r for r in self._rows if r is not row]
            self._rebuild_job_rows()

        # ── Run / Cancel ──────────────────────────────────────────────────────

        def _on_run_clicked(self, _btn) -> None:
            if self._running:
                self._cancel()
                return
            if not self._spec_path:
                return
            jobs = jobs_to_runner_format(self._template, self._rows)
            if not jobs:
                self._status_lbl.set_label("Add at least one job to run.")
                return
            overrides = {}
            if self._frames_entry.get_text().strip().isdigit():
                overrides["num_frames"] = int(self._frames_entry.get_text().strip())
            self._running = True
            self._run_btn.set_label("■  Cancel")
            self._status_lbl.set_label("Starting…")
            if self._on_run:
                self._on_run(jobs, self._spec_path, overrides)

        def _cancel(self) -> None:
            self._running = False
            self._run_btn.set_label("▶  Run Pipeline")
            self._status_lbl.set_label("Cancelled.")
            if self._on_cancel:
                self._on_cancel()

        def set_running(self, running: bool, status: str = "") -> None:
            """Called by MainWindow when runner state changes."""
            self._running = running
            self._run_btn.set_label("■  Cancel" if running else "▶  Run Pipeline")
            if status:
                self._status_lbl.set_label(status)

        # ── History ───────────────────────────────────────────────────────────

        def _refresh_history(self) -> None:
            for child in list(self._history_list):
                self._history_list.remove(child)
            runs = self._store.list_runs(limit=20)
            if not runs:
                lbl = Gtk.Label(label="No runs yet.")
                lbl.add_css_class("muted")
                lbl.set_xalign(0)
                self._history_list.append(lbl)
                return
            for run in runs:
                row = self._make_history_row(run)
                self._history_list.append(row)

        def _make_history_row(self, run: dict) -> Gtk.Box:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.set_margin_top(2)

            try:
                from datetime import datetime
                dt = datetime.fromisoformat(run["started_at"])
                date_str = dt.strftime("%b %d %H:%M")
            except Exception:
                date_str = "—"
            date_lbl = Gtk.Label(label=date_str)
            date_lbl.add_css_class("muted")
            date_lbl.set_size_request(90, -1)
            box.append(date_lbl)

            status_icon = {"done": "✅", "failed": "❌", "running": "⏳",
                            "interrupted": "⚠️"}.get(run.get("status", ""), "⏳")
            box.append(Gtk.Label(label=status_icon))

            n_jobs = len(run.get("jobs", []))
            info_lbl = Gtk.Label(label=f"{n_jobs} jobs · {run.get('spec_name','')[:20]}")
            info_lbl.set_hexpand(True)
            info_lbl.set_xalign(0)
            info_lbl.add_css_class("muted")
            box.append(info_lbl)

            load_btn = Gtk.Button(label="→ Load")
            load_btn.add_css_class("flat")
            load_btn.connect("clicked",
                              lambda _, rid=run["id"]: self._load_run(rid))
            box.append(load_btn)
            return box

        def _load_run(self, run_id: str) -> None:
            run = self._store.get_run(run_id)
            if not run:
                return
            # Restore job table
            jobs = run.get("jobs", [])
            self._rows = []
            for job in jobs:
                row = dict(job)  # copy; has "name" and variable keys
                row.setdefault("enabled", True)
                self._rows.append(row)

            # Restore template from param_overrides if stored, else leave as-is
            stored_template = run.get("param_overrides", {}).get("_template", "")
            if stored_template:
                self._template_entry.set_text(stored_template)

            self._rebuild_job_rows()
            # Switch to Configure tab
            self._stack.set_visible_child_name("configure")

            if self._on_load_run:
                self._on_load_run(run_id)
```

- [ ] **Step 4: Run tests — all 7 must pass**

```bash
/usr/bin/python3 -m pytest tests/test_pipeline_panel.py -v
```

- [ ] **Step 5: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ -q --tb=short 2>/dev/null | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add app/pipeline_panel.py tests/test_pipeline_panel.py
git commit -m "feat: PipelinePanel — spec picker, template+job table, Configure/History tabs, run controls"
```

---

## Task 4: Wire Pipeline tab into MainWindow

**Files:**
- Modify: `app/main_window.py` — add tab, `_set_source("pipeline")`, gallery stack entry, runner callbacks

This task connects everything. It's the most complex.

- [ ] **Step 1: Add `_src_pipeline_btn` to `ControlPanel._build()` (around line 3845)**

After `src_row.append(self._src_art_btn)`, add:

```python
        self._src_pipeline_btn = Gtk.ToggleButton(label="⚙ Pipeline")
        self._src_pipeline_btn.add_css_class("source-btn")
        self._src_pipeline_btn.add_css_class("source-btn-right-extra")
        self._src_pipeline_btn.add_css_class("pipeline-source-btn")
        self._src_pipeline_btn.set_tooltip_text(
            "Pipeline mode — batch multi-step generation\n"
            "Template + variable table → phase grid → playlists"
        )
        self._src_pipeline_btn.set_group(self._src_video_btn)
        self._src_pipeline_btn.connect("toggled",
            lambda b: b.get_active() and self._set_source("pipeline"))
        src_row.append(self._src_pipeline_btn)
```

Also change `_src_art_btn` CSS class from `source-btn-right` to `source-btn-mid` since it's no longer the last button.

- [ ] **Step 2: Add `"pipeline"` branch to `_set_source()` (around line 4957)**

```python
        if source == "pipeline":
            self._title_lbl.set_label("TT Local Generator")
            self._source_desc_lbl.set_label(
                "pipeline mode  ·  batch generation  ·  phase grid  ·  playlists"
            )
            self._on_source_change(source)
            return
```

- [ ] **Step 3: Add `_pipeline_panel` and `_phase_grid` to `MainWindow._build()` (around line 7269)**

After the artgen panel add_named call:

```python
        from pipeline_panel import PipelinePanel
        from phase_grid_widget import PhaseGridWidget, GridState
        self._pipeline_panel = PipelinePanel(
            on_run=self._on_pipeline_run,
            on_cancel=self._on_pipeline_cancel,
            on_load_run=self._on_pipeline_load_run,
        )
        self._phase_grid = PhaseGridWidget(
            state=GridState(jobs=[], phases=[]),
            on_cell_click=self._on_pipeline_cell_click,
            on_retry_node=self._on_pipeline_retry_node,
            on_retry_job=self._on_pipeline_retry_job,
        )
        self._gallery_stack.add_named(self._phase_grid, "pipeline")
```

- [ ] **Step 4: Update `_on_source_change()` for pipeline mode**

```python
    def _on_source_change(self, source: str) -> None:
        self._gallery_stack.set_visible_child_name(source)
        is_artgen = source == "artgen"
        is_pipeline = source == "pipeline"
        # In pipeline mode, swap the left pane to PipelinePanel
        if is_pipeline:
            self._ctrl_wrapper.set_child(self._pipeline_panel)
        else:
            self._ctrl_wrapper.set_child(self._ctrl_scroll_inner)
        self._ctrl_wrapper.set_visible(not is_artgen)
        self._detail_wrap.set_visible(not is_artgen)
        self._rebuild_context_menu(source)
        toggle_act = self.lookup_action("toggle-detail")
        if toggle_act:
            toggle_act.set_enabled(source not in ("artgen", "pipeline"))
```

Note: `_ctrl_scroll_inner` is the existing ctrl_scroll — you need to capture a reference to it before replacing. Add `self._ctrl_scroll_inner = ctrl_scroll` in `_build()` right after `ctrl_scroll = Gtk.ScrolledWindow()`.

- [ ] **Step 5: Add Pipeline runner callbacks to MainWindow**

```python
    def _on_pipeline_run(self, jobs, spec_path, param_overrides):
        from pipeline_runner import PipelineRunner
        from phase_grid_widget import GridState
        from pipeline_panel import phases_from_spec
        phases = phases_from_spec(spec_path)
        state = GridState(jobs=[j["name"] for j in jobs], phases=phases)
        self._phase_grid._state = state
        self._phase_grid._build()
        self._pipeline_runner = PipelineRunner(idle_add=GLib.idle_add)
        self._pipeline_runner.start(
            spec_path=spec_path,
            jobs=jobs,
            param_overrides=param_overrides,
            on_node_update=self._on_pipeline_node_update,
            on_run_finished=self._on_pipeline_run_finished,
        )

    def _on_pipeline_cancel(self):
        if hasattr(self, "_pipeline_runner") and self._pipeline_runner:
            self._pipeline_runner.cancel()

    def _on_pipeline_node_update(self, job_name, node_id, status, detail):
        """Called on GTK main thread via GLib.idle_add."""
        self._phase_grid.update_cell(job_name, node_id, status, detail)
        if job_name == "__health__" and status == "degraded":
            self._hw_statusbar.update_server(False, f"⚠️ Chips degraded: {detail}")

    def _on_pipeline_run_finished(self, success):
        """Called on GTK main thread when the run subprocess exits."""
        if hasattr(self, "_pipeline_panel"):
            self._pipeline_panel.set_running(
                False,
                "✅ Pipeline complete" if success else "❌ Pipeline failed"
            )

    def _on_pipeline_load_run(self, run_id):
        """Load a past run's grid state into the phase grid."""
        from pipeline_store import PipelineStore
        from phase_grid_widget import GridState
        from pipeline_panel import phases_from_spec
        store = PipelineStore()
        run = store.get_run(run_id)
        if not run:
            return
        phases = phases_from_spec(run.get("spec_path", ""))
        state = GridState.from_run_record(run, phases)
        self._phase_grid._state = state
        self._phase_grid._build()

    def _on_pipeline_cell_click(self, job_name, node_id, detail):
        """Load the clicked artifact into the detail pane."""
        from history_store import GenerationRecord, IMAGES_DIR, VIDEOS_DIR, THUMBNAILS_DIR
        import uuid
        from datetime import datetime, timezone
        path = detail
        if not path or not Path(path).exists():
            return
        suffix = Path(path).suffix.lower()
        is_video = suffix in (".mp4", ".webm", ".mov")
        rec = GenerationRecord(
            id=str(uuid.uuid4()),
            prompt=f"Pipeline: {job_name} node {node_id}",
            negative_prompt="",
            num_inference_steps=0,
            seed=-1,
            video_path=path if is_video else "",
            thumbnail_path="",
            created_at=datetime.now(timezone.utc).isoformat(),
            media_type="video" if is_video else "image",
            image_path=path if not is_video else "",
            model="pipeline",
        )
        self._detail.show_record(rec, self._dispatch_remix)

    def _on_pipeline_retry_node(self, job_name, node_id):
        if hasattr(self, "_pipeline_runner") and self._pipeline_runner:
            try:
                self._pipeline_runner.retry_node(job_name, node_id)
            except NotImplementedError:
                pass  # Plan 2 stub — no-op for now

    def _on_pipeline_retry_job(self, job_name):
        if hasattr(self, "_pipeline_runner") and self._pipeline_runner:
            try:
                self._pipeline_runner.retry_job(job_name)
            except NotImplementedError:
                pass
```

- [ ] **Step 6: Handle app restart — load most recent run on Pipeline tab init**

In `MainWindow._build()`, after setting up `_pipeline_panel`, add:

```python
        # On startup, restore the most recent pipeline run into the phase grid
        GLib.idle_add(self._restore_pipeline_run)
```

And the method:

```python
    def _restore_pipeline_run(self):
        from pipeline_store import PipelineStore
        store = PipelineStore()
        runs = store.list_runs(limit=1)
        if runs:
            self._on_pipeline_load_run(runs[0]["id"])
        return GLib.SOURCE_REMOVE
```

- [ ] **Step 7: Run full test suite**

```bash
/usr/bin/python3 -m pytest tests/ -q --tb=short 2>/dev/null | tail -3
```

- [ ] **Step 8: Smoke test the app launches without error**

```bash
timeout 5 /usr/bin/python3 app/main.py 2>&1 | grep -iE "error|traceback|exception" | head -5 || echo "no errors in 5s startup"
```

- [ ] **Step 9: Commit**

```bash
git add app/main_window.py
git commit -m "feat: wire Pipeline as 5th source tab — PipelinePanel + PhaseGridWidget + runner callbacks"
```

---

## Task 5: Restart recovery — reattach on app open

**Files:**
- Modify: `app/main_window.py` — check for interrupted runs on startup and offer reattach

- [ ] **Step 1: In `_restore_pipeline_run()`, check for live interrupted runs**

```python
    def _restore_pipeline_run(self):
        from pipeline_store import PipelineStore
        from pipeline_runner import PipelineRunner
        store = PipelineStore()

        # Check for a run that was alive when we last closed the app
        interrupted = store.find_interrupted_runs()
        for run in interrupted:
            store.mark_interrupted(run["id"])

        # Check for a run that is STILL running (app crashed, process survived)
        running_runs = [r for r in store.list_runs(limit=5) if r.get("status") == "running"]
        for run in running_runs:
            runner = PipelineRunner(idle_add=GLib.idle_add)
            runner._store = store
            reattached = runner.reattach(
                run["id"],
                on_node_update=self._on_pipeline_node_update,
                on_run_finished=self._on_pipeline_run_finished,
            )
            if reattached:
                # Load this run's grid and switch to Pipeline tab
                self._on_pipeline_load_run(run["id"])
                if hasattr(self, "_src_pipeline_btn"):
                    self._src_pipeline_btn.set_active(True)
                self._pipeline_runner = runner
                self._pipeline_panel.set_running(True, "Reconnected to running pipeline…")
                return GLib.SOURCE_REMOVE

        # No live run — just restore most recent completed run for reference
        runs = store.list_runs(limit=1)
        if runs:
            self._on_pipeline_load_run(runs[0]["id"])
        return GLib.SOURCE_REMOVE
```

- [ ] **Step 2: Run full test suite**

```bash
/usr/bin/python3 -m pytest tests/ -q --tb=short 2>/dev/null | tail -3
```

- [ ] **Step 3: Commit**

```bash
git add app/main_window.py
git commit -m "feat: Pipeline restart recovery — reattach to live run on app open, restore latest run grid"
```

---

## Self-review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| 5th source tab in toolbar | Task 4, Step 1 |
| `_set_source("pipeline")` branch | Task 4, Step 2 |
| Template + variable table (Configure tab) | Task 3 |
| Configure / History tabs (left pane) | Task 3 |
| History loads job table + grid | Task 3 (`_load_run`) + Task 4 (`_on_pipeline_load_run`) |
| Phase progress grid, all cell states | Task 2 |
| Cell click → detail pane | Task 4 (`_on_pipeline_cell_click`) |
| Per-job ↺ retry | Task 4 (`_on_pipeline_retry_job`) |
| Health check warning in status bar | Task 4 (`_on_pipeline_node_update`) |
| App restart recovers live run | Task 5 |
| App restart shows last run's grid | Task 4 Step 6 + Task 5 |
| All existing tests pass | Every task step 5/6/7 |

**Not in Plan 2 scope (Plan 3):**
- `retry_node` / `retry_job` full implementation (stubs raise NotImplementedError)
- Multi-job parallel batching (single-job per run_workflow.sh invocation)
- `workflow_compat.py` preflight compatibility validator
