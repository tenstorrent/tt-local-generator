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
    Uses a single-pass regex so longer variable names like {style_hint}
    are never corrupted by shorter overlapping names like {style}.
    """
    if row.get("__custom__"):
        return row.get("prompt", "")
    _skip = {"name", "enabled", "__custom__", "prompt"}
    substitutions = {k: str(v) for k, v in row.items() if k not in _skip}

    def _replace(m: re.Match) -> str:
        key = m.group(1)
        return substitutions.get(key, m.group(0))  # leave {missing} as-is

    return re.sub(r'\{(\w+)\}', _replace, template)


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
            self._specs: list[dict] = []

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

            self._preflight_lbl = Gtk.Label(label="")
            self._preflight_lbl.set_xalign(0)
            self._preflight_lbl.set_wrap(True)
            self._preflight_lbl.set_max_width_chars(36)
            self._preflight_lbl.add_css_class("muted")
            box.append(self._preflight_lbl)

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
                self._run_preflight()

        def _on_spec_changed(self, dd: Gtk.DropDown, _pspec) -> None:
            idx = dd.get_selected()
            if idx < len(self._specs):
                self._spec_path = self._specs[idx]["path"]
                self._run_preflight()

        def _run_preflight(self) -> None:
            """Validate the selected spec and update the preflight warning label."""
            if not self._spec_path or not hasattr(self, "_preflight_lbl"):
                return
            from workflow_compat import validate_spec
            result = validate_spec(self._spec_path)
            if result.blocking:
                self._preflight_lbl.set_label(f"❌ {result.blocking[0]}")
                self._preflight_lbl.remove_css_class("muted")
                self._preflight_lbl.add_css_class("error")
                if hasattr(self, "_run_btn"):
                    self._run_btn.set_sensitive(False)
            elif result.warnings:
                self._preflight_lbl.set_label(
                    f"⚠️ {len(result.warnings)} node(s) will be skipped"
                )
                self._preflight_lbl.remove_css_class("error")
                self._preflight_lbl.add_css_class("muted")
                if hasattr(self, "_run_btn"):
                    self._run_btn.set_sensitive(True)
            else:
                self._preflight_lbl.set_label("")
                self._preflight_lbl.remove_css_class("error")
                if hasattr(self, "_run_btn"):
                    self._run_btn.set_sensitive(True)

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
                row = dict(job)  # copy
                row.setdefault("enabled", True)
                self._rows.append(row)

            # Restore template from param_overrides if stored
            stored_template = run.get("param_overrides", {}).get("_template", "")
            if stored_template:
                self._template_entry.set_text(stored_template)

            self._rebuild_job_rows()
            # Switch to Configure tab
            self._stack.set_visible_child_name("configure")

            if self._on_load_run:
                self._on_load_run(run_id)
