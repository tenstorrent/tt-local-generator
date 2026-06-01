# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
WorkflowPopover — toolbar popover for browsing, parameterizing, and running
workflow JSON specs.

Structure mirrors RemixPopover: three zones stacked vertically inside a
Gtk.Popover anchored to a Gtk.MenuButton in the main toolbar.

  ┌─ WORKFLOW ────────────────────┐
  │ [spec dropdown            ▾] │
  │ <description line>           │
  ├───────────────────────────────┤
  │ PARAMETERS                   │
  │ prompt  [………………………………………]   │
  │ seed    [   1964          ]  │
  ├───────────────────────────────┤
  │ RECENT RUNS                  │
  │ Jun 01  ✅ 5 art [→ Watch]  │
  │ May 28  ✅ 5 art [→ Watch]  │
  │               [▶ Run]        │
  └──────────────────────────────┘

Threading:
  Run launches run_workflow.sh via subprocess.Popen (non-blocking).
  stdout is watched with GLib.io_add_watch — the active run row updates live.
  GTK is never touched from the worker thread.

Persistence:
  Completed run records are written to
  ~/.local/share/tt-local-generator/workflow-runs/index.json
  Each record: {id, spec_path, spec_name, started_at, finished_at,
                status, playlist_id, params_override, output_dir}.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

# ── Paths ─────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILTIN_WORKFLOW_DIR = _REPO_ROOT / "docs" / "examples" / "workflows"
_USER_WORKFLOW_DIR = Path.home() / ".local" / "share" / "tt-local-generator" / "workflows"
_RUN_INDEX = Path.home() / ".local" / "share" / "tt-local-generator" / "workflow-runs" / "index.json"
_RUN_SHELL = _REPO_ROOT / "bin" / "run_workflow.sh"

# Inputs that can be overridden from the popover (others are inter-node wires).
_OVERRIDABLE_KEYS = {"prompt", "negative_prompt", "seed", "model", "width", "height",
                     "num_inference_steps", "steps", "guidance_scale"}

_MAX_HISTORY_ROWS = 6


# ── Run index (persistence) ───────────────────────────────────────────────────

class WorkflowRunIndex:
    """JSON-backed list of WorkflowRunRecord dicts, newest first."""

    def __init__(self) -> None:
        _RUN_INDEX.parent.mkdir(parents=True, exist_ok=True)
        self._path = _RUN_INDEX

    def load(self) -> list[dict]:
        try:
            return json.loads(self._path.read_text())
        except Exception:
            return []

    def save(self, records: list[dict]) -> None:
        try:
            self._path.write_text(json.dumps(records, indent=2))
        except Exception:
            pass

    def add(self, record: dict) -> None:
        records = self.load()
        records.insert(0, record)
        self.save(records)

    def update(self, run_id: str, **kwargs) -> None:
        records = self.load()
        for r in records:
            if r.get("id") == run_id:
                r.update(kwargs)
                break
        self.save(records)

    def for_spec(self, spec_path: str, limit: int = _MAX_HISTORY_ROWS) -> list[dict]:
        return [r for r in self.load() if r.get("spec_path") == spec_path][:limit]


_run_index = WorkflowRunIndex()


# ── Spec discovery ────────────────────────────────────────────────────────────

def _discover_specs() -> list[dict]:
    """Scan builtin + user workflow dirs; return list of {path, name, description, nodes}."""
    specs: list[dict] = []
    for d in (_BUILTIN_WORKFLOW_DIR, _USER_WORKFLOW_DIR):
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                name = data.get("_description", "").split("→")[0].strip() or f.stem
                node_count = sum(1 for k in data if not k.startswith("_"))
                specs.append({
                    "path": str(f),
                    "name": name[:60],
                    "description": data.get("_description", ""),
                    "nodes": node_count,
                })
            except Exception:
                continue
    return specs


def _parse_overridable_inputs(spec_path: str) -> list[dict]:
    """Return list of {node_id, key, value, type} for overridable leaf inputs."""
    try:
        data = json.loads(Path(spec_path).read_text())
    except Exception:
        return []
    results = []
    for node_id, node in data.items():
        if node_id.startswith("_") or not isinstance(node, dict):
            continue
        for key, val in node.get("inputs", {}).items():
            if key not in _OVERRIDABLE_KEYS:
                continue
            if isinstance(val, list):  # inter-node wire — skip
                continue
            results.append({
                "node_id": node_id,
                "key": key,
                "value": val,
                "type": "int" if isinstance(val, int) else "float" if isinstance(val, float) else "str",
            })
    return results


def _apply_overrides(spec_path: str, overrides: dict[tuple, object]) -> str:
    """Write a temp JSON with param overrides patched in. Returns the temp file path."""
    data = json.loads(Path(spec_path).read_text())
    for (node_id, key), value in overrides.items():
        if node_id in data and "inputs" in data[node_id]:
            data[node_id]["inputs"][key] = value
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    json.dump(data, tmp, indent=2)
    tmp.close()
    return tmp.name


# ── WorkflowPopover ───────────────────────────────────────────────────────────

class WorkflowPopover(Gtk.Popover):
    """
    Toolbar popover for browsing, parameterizing, and running workflows.

    Args:
        on_watch_playlist: callable(playlist_id: str) — opens TT-TV for a playlist
    """

    def __init__(self, on_watch_playlist: Callable[[str], None]) -> None:
        super().__init__()
        self._on_watch_playlist = on_watch_playlist
        self._specs: list[dict] = []
        self._param_inputs: list[dict] = []    # current overridable inputs
        self._param_widgets: dict[tuple, Gtk.Widget] = {}  # (node_id, key) → widget
        self._active_run_id: Optional[str] = None
        self._active_run_proc: Optional[subprocess.Popen] = None
        self._history_rows: list[Gtk.Widget] = []

        self.set_position(Gtk.PositionType.BOTTOM)
        self.set_autohide(True)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        outer.set_margin_start(12)
        outer.set_margin_end(12)
        outer.set_size_request(320, -1)
        self.set_child(outer)

        # ── Zone 1: spec selector ─────────────────────────────────────────────
        hdr1 = Gtk.Label(label="WORKFLOW")
        hdr1.set_xalign(0)
        hdr1.add_css_class("section-label")
        outer.append(hdr1)

        self._spec_dd = Gtk.DropDown()
        self._spec_dd.set_hexpand(True)
        self._spec_dd.connect("notify::selected", self._on_spec_changed)
        outer.append(self._spec_dd)

        self._desc_lbl = Gtk.Label(label="")
        self._desc_lbl.set_xalign(0)
        self._desc_lbl.set_wrap(True)
        self._desc_lbl.set_max_width_chars(40)
        self._desc_lbl.add_css_class("muted")
        self._desc_lbl.set_margin_top(2)
        self._desc_lbl.set_margin_bottom(6)
        outer.append(self._desc_lbl)

        outer.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Zone 2: parameters ────────────────────────────────────────────────
        hdr2 = Gtk.Label(label="PARAMETERS")
        hdr2.set_xalign(0)
        hdr2.add_css_class("section-label")
        hdr2.set_margin_top(6)
        outer.append(hdr2)

        self._params_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._params_box.set_margin_bottom(6)
        outer.append(self._params_box)

        outer.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Zone 3: run history + run button ──────────────────────────────────
        hdr3 = Gtk.Label(label="RECENT RUNS")
        hdr3.set_xalign(0)
        hdr3.add_css_class("section-label")
        hdr3.set_margin_top(6)
        outer.append(hdr3)

        self._history_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._history_box.set_margin_bottom(8)
        outer.append(self._history_box)

        self._run_btn = Gtk.Button(label="▶  Run Workflow")
        self._run_btn.add_css_class("suggested-action")
        self._run_btn.connect("clicked", self._on_run_clicked)
        outer.append(self._run_btn)

        # Populate on first show
        self.connect("show", lambda *_: self._refresh())

    # ── Refresh ───────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        """Reload specs and history whenever the popover is shown."""
        self._specs = _discover_specs()

        string_list = Gtk.StringList()
        for s in self._specs:
            label = f"{s['name']}  ({s['nodes']} steps)"
            string_list.append(label)
        self._spec_dd.set_model(string_list)

        if self._specs:
            self._spec_dd.set_selected(0)
            self._load_spec(0)

    def _load_spec(self, idx: int) -> None:
        if idx >= len(self._specs):
            return
        spec = self._specs[idx]
        self._desc_lbl.set_label(spec["description"][:120] or spec["name"])

        # Rebuild param widgets
        for w in list(self._params_box):
            self._params_box.remove(w)
        self._param_widgets.clear()
        self._param_inputs = _parse_overridable_inputs(spec["path"])

        for inp in self._param_inputs:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label=inp["key"])
            lbl.set_xalign(0)
            lbl.set_size_request(80, -1)
            lbl.add_css_class("muted")
            row.append(lbl)

            if inp["type"] == "int":
                widget = Gtk.SpinButton.new_with_range(0, 100_000, 1)
                widget.set_value(int(inp["value"]))
                widget.set_hexpand(True)
            elif inp["type"] == "float":
                widget = Gtk.SpinButton.new_with_range(0, 20, 0.5)
                widget.set_digits(1)
                widget.set_value(float(inp["value"]))
                widget.set_hexpand(True)
            else:
                widget = Gtk.Entry()
                widget.set_text(str(inp["value"]))
                widget.set_hexpand(True)
                widget.set_max_width_chars(30)

            row.append(widget)
            self._params_box.append(row)
            self._param_widgets[(inp["node_id"], inp["key"])] = widget

        # Rebuild history
        self._rebuild_history(spec["path"])

    def _rebuild_history(self, spec_path: str) -> None:
        for w in list(self._history_box):
            self._history_box.remove(w)
        self._history_rows.clear()

        runs = _run_index.for_spec(spec_path)
        if not runs:
            empty = Gtk.Label(label="No runs yet")
            empty.add_css_class("muted")
            empty.set_xalign(0)
            self._history_box.append(empty)
            return

        for run in runs:
            row = self._make_history_row(run)
            self._history_box.append(row)
            self._history_rows.append((run["id"], row))

    def _make_history_row(self, run: dict) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_margin_top(2)
        row.set_name(f"wf-run-{run['id']}")

        # Date
        try:
            dt = datetime.fromisoformat(run["started_at"])
            date_str = dt.strftime("%b %d")
        except Exception:
            date_str = "—"
        date_lbl = Gtk.Label(label=date_str)
        date_lbl.set_xalign(0)
        date_lbl.add_css_class("muted")
        date_lbl.set_size_request(46, -1)
        row.append(date_lbl)

        # Status + live progress
        status = run.get("status", "running")
        icon = {"done": "✅", "failed": "❌", "running": "⏳"}.get(status, "⏳")
        status_lbl = Gtk.Label(label=icon)
        row.append(status_lbl)

        # Progress / artifact count label
        if status == "running":
            prog = Gtk.Label(label=run.get("progress", "starting…"))
        else:
            artifacts = run.get("artifact_count", 0)
            prog = Gtk.Label(label=f"{artifacts} art" if artifacts else "")
        prog.set_hexpand(True)
        prog.set_xalign(0)
        prog.add_css_class("muted")
        row.append(prog)
        run["_progress_lbl"] = prog  # stash for live updates

        # Watch button (only when done + playlist)
        playlist_id = run.get("playlist_id")
        if playlist_id and status == "done":
            watch_btn = Gtk.Button(label="→ Watch")
            watch_btn.add_css_class("flat")
            watch_btn.connect("clicked", lambda _, pid=playlist_id: self._on_watch(pid))
            row.append(watch_btn)

        return row

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_spec_changed(self, dd: Gtk.DropDown, _pspec) -> None:
        idx = dd.get_selected()
        if idx < len(self._specs):
            self._load_spec(idx)

    def _on_watch(self, playlist_id: str) -> None:
        self.popdown()
        self._on_watch_playlist(playlist_id)

    def _on_run_clicked(self, _btn) -> None:
        idx = self._spec_dd.get_selected()
        if idx >= len(self._specs):
            return
        spec = self._specs[idx]

        # Collect param overrides
        overrides: dict[tuple, object] = {}
        for (node_id, key), widget in self._param_widgets.items():
            if isinstance(widget, Gtk.SpinButton):
                # Find original type
                orig = next((i for i in self._param_inputs if i["node_id"] == node_id and i["key"] == key), None)
                val = int(widget.get_value()) if (orig and orig["type"] == "int") else widget.get_value()
            else:
                val = widget.get_text()
            overrides[(node_id, key)] = val

        # Write overridden spec to temp file
        spec_path = _apply_overrides(spec["path"], overrides) if overrides else spec["path"]

        # Build run record
        run_id = str(uuid.uuid4())
        run_dir = str(Path.home() / ".local" / "share" / "tt-local-generator" / "workflow-runs" / run_id[:8])
        run = {
            "id": run_id,
            "spec_path": spec["path"],
            "spec_name": spec["name"],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "status": "running",
            "playlist_id": None,
            "artifact_count": 0,
            "progress": "starting…",
            "params_override": {f"{k[0]}.{k[1]}": v for k, v in overrides.items()},
            "output_dir": run_dir,
        }
        _run_index.add(run)
        self._active_run_id = run_id

        # Add live row at top of history
        live_row = self._make_history_row(run)
        self._history_box.prepend(live_row)

        # Disable run button
        self._run_btn.set_label("⟳  Running…")
        self._run_btn.set_sensitive(False)

        # Launch subprocess
        env = {**os.environ, "WORKFLOW_RUN_ID": run_id}
        try:
            proc = subprocess.Popen(
                ["bash", str(_RUN_SHELL), spec_path],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=env,
            )
            self._active_run_proc = proc
            GLib.io_add_watch(proc.stdout, GLib.IO_IN | GLib.IO_HUP,
                              self._on_run_stdout, run, run.get("_progress_lbl"))
        except Exception as e:
            self._finish_run(run, success=False)
            run.get("_progress_lbl") and run["_progress_lbl"].set_label(f"failed: {e}")

    def _on_run_stdout(self, source, condition, run: dict, prog_lbl: Optional[Gtk.Label]) -> bool:
        """GLib IO watch callback — runs on main thread."""
        if condition & GLib.IO_IN:
            line = source.readline()
            if not line:
                return GLib.SOURCE_CONTINUE

            line = line.rstrip()
            # Parse progress hints from run_workflow.sh
            if line.startswith("══") or line.startswith("["):
                step = line.strip("═ []")
                progress = step[:40] if step else "running…"
                run["progress"] = progress
                _run_index.update(run["id"], progress=progress)
                if prog_lbl:
                    GLib.idle_add(prog_lbl.set_label, progress)

            # Detect playlist creation
            if "PLAYLIST:" in line:
                parts = line.split(":")
                if len(parts) >= 2:
                    # Format: PLAYLIST:<count>:<name>
                    try:
                        count = int(parts[1])
                        run["artifact_count"] = count
                        # Find playlist_id by name
                        playlist_name = ":".join(parts[2:]).strip()
                        try:
                            import sys
                            sys.path.insert(0, str(_REPO_ROOT / "app"))
                            from playlist_store import PlaylistStore
                            ps = PlaylistStore()
                            candidates = [p for p in ps.all() if p.name == playlist_name and p.record_ids]
                            if candidates:
                                run["playlist_id"] = candidates[0].id
                        except Exception:
                            pass
                    except Exception:
                        pass

        if condition & GLib.IO_HUP:
            proc = self._active_run_proc
            success = (proc is not None and proc.wait() == 0)
            GLib.idle_add(self._finish_run, run, success)
            return GLib.SOURCE_REMOVE

        return GLib.SOURCE_CONTINUE

    def _finish_run(self, run: dict, success: bool) -> None:
        """Called on main thread when subprocess exits."""
        status = "done" if success else "failed"
        now = datetime.now(timezone.utc).isoformat()
        run["status"] = status
        run["finished_at"] = now
        _run_index.update(run["id"], status=status, finished_at=now,
                          playlist_id=run.get("playlist_id"),
                          artifact_count=run.get("artifact_count", 0))

        # Re-enable run button
        self._run_btn.set_label("▶  Run Workflow")
        self._run_btn.set_sensitive(True)
        self._active_run_id = None
        self._active_run_proc = None

        # Refresh the history display for this spec
        idx = self._spec_dd.get_selected()
        if idx < len(self._specs):
            self._rebuild_history(self._specs[idx]["path"])
