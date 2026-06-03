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
        """Return all cells for a job, filling in pending for unknown phase IDs.

        Uses a list (not a set) so dict keys preserve the phase display order.
        """
        phase_ids = [p["id"] for p in self.phases]
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
            # Tracks cells that already have a terminal GestureClick attached,
            # so repeated update_cell calls cannot accumulate duplicate controllers.
            self._terminal_cells: set[tuple[str, str]] = set()
            # Maps job_name → per-job retry button so update_cell can toggle sensitivity.
            self._retry_buttons: dict[str, "Gtk.Button"] = {}
            self._build()

        def _build(self) -> None:
            # Clear any existing children
            while child := self.get_first_child():
                self.remove(child)
            self._cell_widgets.clear()
            self._terminal_cells = set()  # reset per-cell gesture tracking
            self._retry_buttons = {}  # reset per-job retry button refs

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
            self._grid = grid

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
                model_lbl = Gtk.Label()
                model_lbl.set_markup(
                    f'<span foreground="#4fd1c5" size="xx-small">{phase.get("model","")}</span>'
                )
                hdr.append(name_lbl)
                hdr.append(model_lbl)
                hdr.set_size_request(52, -1)
                grid.attach(hdr, col, 0, 1, 1)

            # Retry-all column header (empty)
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
                self._retry_buttons[job_name] = retry_btn  # store ref for sensitivity updates

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
                # New job appeared after initial build — rebuild grid to include it.
                self._build()
                return
            old_cell = self._cell_widgets[key]
            # Update CSS class in-place (cheaper than rebuilding the whole grid)
            for css in ["phase-cell-pending", "phase-cell-running", "phase-cell-done",
                        "phase-cell-failed", "phase-cell-skipped"]:
                old_cell.remove_css_class(css)
            old_cell.add_css_class(f"phase-cell-{status}")
            # Update label
            child = old_cell.get_first_child()
            if isinstance(child, Gtk.Label):
                child.set_label({"done": "✓", "failed": "✗", "running": "⏳",
                                  "skipped": "skip", "pending": ""}.get(status, ""))
            # Wire click handler for newly-terminal cells — guard prevents
            # duplicate controllers when update_cell is called more than once
            # for the same cell (e.g. status refresh after "done").
            if status in ("done", "failed") and key not in self._terminal_cells:
                self._terminal_cells.add(key)
                gesture = Gtk.GestureClick()
                gesture.connect(
                    "pressed",
                    lambda g, n, x, y, jn=job_name, nid=node_id, det=detail:
                        self._on_cell_clicked(jn, nid, det)
                )
                old_cell.add_controller(gesture)

            # Enable/disable the per-job retry button based on whether any
            # cell in this job's row is in the "failed" state.
            job_has_failure = any(
                self._state.cell(job_name, p["id"]).get("status") == "failed"
                for p in self._state.phases
            )
            retry_btn = self._retry_buttons.get(job_name)
            if retry_btn:
                retry_btn.set_sensitive(job_has_failure)

        def _on_cell_clicked(self, job_name: str, node_id: str, detail: str) -> None:
            if self._on_cell_click:
                self._on_cell_click(job_name, node_id, detail)

        def _on_retry_job_clicked(self, btn: Gtk.Button) -> None:
            if self._on_retry_job:
                self._on_retry_job(btn.job_name)  # type: ignore[attr-defined]
