# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
WorkflowPopover — toolbar popover for browsing, parameterizing, and running
workflow JSON specs.

Structure mirrors RemixPopover: three zones stacked vertically inside a
Gtk.Popover anchored to a Gtk.MenuButton in the main toolbar.

  ┌─ WORKFLOW ─────────────────────────┐
  │ [spec dropdown                 ▾] │
  │ <description line>               │
  ├────────────────────────────────────┤
  │ PARAMETERS                       │
  │ prompt  [………………………………………]        │
  │ seed    [   1964             ]   │
  ├────────────────────────────────────┤
  │ RECENT RUNS                      │
  │ Jun 01  ✅ 5 art [→ Watch] [📋] │
  │   ▼ expanded: thumbnail grid     │
  │   [img] seed image               │
  │   [img] foreground mask          │
  │   [vid] World's Fair video       │
  │   [img] poem image               │
  │   "The Unisphere gleams…" (poem) │
  └──────────────────────────────────┘

Threading:
  Run launches run_workflow.sh via subprocess.Popen (non-blocking).
  stdout is watched with GLib.io_add_watch — the active run row updates live.
  GTK is never touched from the worker thread.

Persistence:
  Completed run records are written to
  ~/.local/share/tt-local-generator/workflow-runs/index.json
  Each record: {id, spec_path, spec_name, started_at, finished_at,
                status, playlist_id, params_override, output_dir}.

Portfolio view:
  Clicking a completed run row toggles an expanded artifact grid showing
  every output from results.json, with thumbnail, node label, and any
  text output (captions, poems). Built from the run's output_dir.
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

# Map results.json output key → human label
_OUTPUT_LABELS: dict[str, str] = {
    "image_path":  "seed image",
    "image2_path": "poem image",
    "fg_path":     "foreground",
    "depth_path":  "depth map",
    "video_path":  "video",
    "caption":     None,   # text — shown as caption block, not thumbnail
    "poem":        None,   # text — shown as quote block
}

# ── Run portfolio loader ──────────────────────────────────────────────────────

def _load_run_portfolio(run: dict) -> list[dict]:
    """
    Read the run's results.json and return an ordered list of artifact items.

    Each item is one of:
      {"type": "image", "path": str, "label": str}
      {"type": "video", "path": str, "label": str}
      {"type": "text",  "text": str, "label": str}
    """
    output_dir = run.get("output_dir") or ""
    results_path = Path(output_dir) / "results.json" if output_dir else None
    if not results_path or not results_path.exists():
        return []

    try:
        results = json.loads(results_path.read_text())
    except Exception:
        return []

    items: list[dict] = []
    for node_id in sorted(results.keys(), key=lambda k: int(k) if k.isdigit() else 999):
        node_data = results.get(node_id, {})
        # _label written by set_node_label — use it to override default key labels
        node_label_override = node_data.get("_label")

        for key, value in node_data.items():
            if key == "_label" or not value:
                continue

            if key in ("caption", "poem"):
                # Text outputs — shown as quoted blocks with the node label
                label_text = node_label_override or key
                items.append({"type": "text", "text": str(value), "label": label_text})
            elif key in _OUTPUT_LABELS and _OUTPUT_LABELS[key] is not None:
                p = Path(str(value))
                if not p.exists():
                    continue
                suffix = p.suffix.lower()
                media_type = "video" if suffix in (".mp4", ".webm", ".mov") else "image"
                label = node_label_override or _OUTPUT_LABELS[key]
                items.append({"type": media_type, "path": str(value), "label": label})
    return items


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
        # "run" or "cancel" — tracks what the run button currently does
        self._run_btn_mode: str = "run"

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
        """Build a history row widget.

        For completed runs, the row is a collapsible container: a summary header
        line (date / status / buttons) plus a hidden portfolio grid that expands
        when the user clicks the row.
        """
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        container.set_margin_top(2)
        container.set_name(f"wf-run-{run['id']}")

        # ── Summary header ────────────────────────────────────────────────────
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        container.append(header)

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
        header.append(date_lbl)

        # Status + live progress
        status = run.get("status", "running")
        icon = {"done": "✅", "failed": "❌", "running": "⏳"}.get(status, "⏳")
        status_lbl = Gtk.Label(label=icon)
        header.append(status_lbl)

        # Progress / artifact count label
        if status == "running":
            prog = Gtk.Label(label=run.get("progress", "starting…"))
        else:
            artifacts = run.get("artifact_count", 0)
            prog = Gtk.Label(label=f"{artifacts} artifacts" if artifacts else "")
        prog.set_hexpand(True)
        prog.set_xalign(0)
        prog.add_css_class("muted")
        header.append(prog)
        run["_progress_lbl"] = prog  # stash for live updates

        # Warning badge
        if run.get("had_partial_failure"):
            wcount = run.get("warning_count", 1)
            warn_lbl = Gtk.Label(label=f"⚠️ {wcount}" if wcount > 1 else "⚠️")
            warn_lbl.set_tooltip_text(
                f"{wcount} warning(s) during this run — open Log for details"
            )
            header.append(warn_lbl)

        # Watch button
        playlist_id = run.get("playlist_id")
        if playlist_id and status == "done":
            watch_btn = Gtk.Button(label="→ Watch")
            watch_btn.add_css_class("flat")
            watch_btn.connect("clicked", lambda _, pid=playlist_id: self._on_watch(pid))
            header.append(watch_btn)

        # Log button
        log_path = run.get("log_file") or run.get("log_path")
        if log_path and status in ("done", "failed"):
            if Path(log_path).exists():
                log_btn = Gtk.Button(label="📋")
                log_btn.set_tooltip_text("View run log")
                log_btn.add_css_class("flat")
                log_btn.connect("clicked", lambda _, lp=log_path: self._on_view_log(lp))
                header.append(log_btn)

        # Expand/collapse toggle for completed runs with output
        if status == "done":
            toggle_btn = Gtk.Button(label="▾")
            toggle_btn.set_tooltip_text("Show artifacts")
            toggle_btn.add_css_class("flat")
            header.append(toggle_btn)

            # Portfolio grid — hidden by default
            portfolio = self._make_portfolio_grid(run)
            portfolio.set_visible(False)
            container.append(portfolio)

            def _toggle_portfolio(btn, grid=portfolio):
                expanded = grid.get_visible()
                grid.set_visible(not expanded)
                btn.set_label("▴" if not expanded else "▾")
                btn.set_tooltip_text("Hide artifacts" if not expanded else "Show artifacts")
            toggle_btn.connect("clicked", _toggle_portfolio)

        # Cancel button — only while this run is the active running run
        if status == "running" and run.get("id") == self._active_run_id:
            cancel_btn = Gtk.Button(label="⏹")
            cancel_btn.set_tooltip_text("Cancel this run")
            cancel_btn.add_css_class("flat")
            cancel_btn.connect("clicked", lambda _, r=run: self._on_cancel_row(r))
            header.append(cancel_btn)

        return container

    def _make_portfolio_grid(self, run: dict) -> Gtk.Box:
        """Build the expanded artifact grid for a completed run.

        Shows each artifact from results.json: thumbnail (image/video frame)
        with label, plus text outputs (caption, poem) as quoted blocks.
        """
        grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        grid.set_margin_top(6)
        grid.set_margin_bottom(4)
        grid.set_margin_start(8)

        items = _load_run_portfolio(run)
        if not items:
            lbl = Gtk.Label(label="No artifact data found for this run.")
            lbl.add_css_class("muted")
            lbl.set_xalign(0)
            grid.append(lbl)
            return grid

        # Collect image/video items for a thumbnail strip
        media_items = [it for it in items if it["type"] in ("image", "video")]
        text_items  = [it for it in items if it["type"] == "text"]

        if media_items:
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
            scroll.set_min_content_height(90)
            scroll.set_max_content_height(90)

            strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            strip.set_margin_top(2)
            strip.set_margin_bottom(2)
            scroll.set_child(strip)

            for item in media_items:
                cell = self._make_artifact_cell(item)
                strip.append(cell)

            grid.append(scroll)

        for item in text_items:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.set_margin_top(2)

            tag = Gtk.Label(label=item["label"].upper())
            tag.set_size_request(52, -1)
            tag.set_xalign(0)
            tag.set_valign(Gtk.Align.START)
            tag.add_css_class("muted")
            row.append(tag)

            text_lbl = Gtk.Label(label=item["text"][:160] + ("…" if len(item["text"]) > 160 else ""))
            text_lbl.set_xalign(0)
            text_lbl.set_wrap(True)
            text_lbl.set_max_width_chars(36)
            text_lbl.set_hexpand(True)
            row.append(text_lbl)

            grid.append(row)

        return grid

    def _make_artifact_cell(self, item: dict) -> Gtk.Box:
        """80×80 thumbnail cell with label below."""
        cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        cell.set_size_request(80, -1)

        try:
            if item["type"] == "image":
                pb = self._load_thumbnail(item["path"], 80, 80)
            else:
                # Video — try to extract a frame via GdkPixbuf paintable
                pb = self._load_video_thumbnail(item["path"], 80, 80)

            if pb:
                img = Gtk.Image.new_from_pixbuf(pb)
                img.set_size_request(80, 80)
            else:
                img = Gtk.Label(label="🎬" if item["type"] == "video" else "🖼")
                img.set_size_request(80, 80)
        except Exception:
            img = Gtk.Label(label="?")
            img.set_size_request(80, 80)

        cell.append(img)

        lbl = Gtk.Label(label=item["label"])
        lbl.set_max_width_chars(10)
        lbl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        lbl.add_css_class("muted")
        lbl.set_xalign(0.5)
        cell.append(lbl)
        return cell

    @staticmethod
    def _load_thumbnail(path: str, w: int, h: int):
        """Load image as scaled GdkPixbuf."""
        try:
            from gi.repository import GdkPixbuf
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, w, h, True)
            return pb
        except Exception:
            return None

    @staticmethod
    def _load_video_thumbnail(path: str, w: int, h: int):
        """Extract a GdkPixbuf thumbnail from an MP4 using the app's existing thumbnail."""
        try:
            # First try: same-basename .jpg in thumbnails dir
            from gi.repository import GdkPixbuf
            from pathlib import Path as _Path
            storage = _Path.home() / ".local" / "share" / "tt-video-gen" / "thumbnails"
            stem = _Path(path).stem
            for ext in (".jpg", ".jpeg", ".png"):
                thumb = storage / (stem + ext)
                if thumb.exists():
                    return GdkPixbuf.Pixbuf.new_from_file_at_scale(str(thumb), w, h, True)
            # Second try: tt-local-generator thumbnails dir
            storage2 = _Path.home() / ".local" / "share" / "tt-local-generator" / "thumbnails"
            for ext in (".jpg", ".jpeg", ".png"):
                thumb2 = storage2 / (stem + ext)
                if thumb2.exists():
                    return GdkPixbuf.Pixbuf.new_from_file_at_scale(str(thumb2), w, h, True)
        except Exception:
            pass
        return None

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_spec_changed(self, dd: Gtk.DropDown, _pspec) -> None:
        idx = dd.get_selected()
        if idx < len(self._specs):
            self._load_spec(idx)

    def _on_watch(self, playlist_id: str) -> None:
        self.popdown()
        self._on_watch_playlist(playlist_id)

    def _on_view_log(self, log_path: str) -> None:
        """Open the workflow run log file.

        Tries the app's Debug → Log Viewer first (via the root MainWindow).
        Falls back to xdg-open so the log is always reachable even if the
        LogViewerWindow class is unavailable.
        """
        opened_in_viewer = False
        try:
            root = self.get_root()
            if root is not None and hasattr(root, "_open_log_viewer"):
                root._open_log_viewer()
                # Give the viewer a moment to construct, then load the file
                def _load_after_open():
                    try:
                        viewer = getattr(root, "_log_viewer_win", None)
                        if viewer and hasattr(viewer, "open_to"):
                            viewer.open_to(log_path)
                    except Exception:
                        pass
                    return GLib.SOURCE_REMOVE
                GLib.timeout_add(300, _load_after_open)
                opened_in_viewer = True
        except Exception:
            pass
        if not opened_in_viewer:
            subprocess.Popen(["xdg-open", log_path])

    def _on_cancel_row(self, run: dict) -> None:
        """Cancel button in a running history row — terminate the active process."""
        if run.get("id") != self._active_run_id:
            return  # guard: only the live run can be cancelled this way
        self._cancel_run()

    def _cancel_run(self) -> None:
        """Terminate the active run process and mark the run as failed."""
        proc = self._active_run_proc
        if proc and proc.poll() is None:
            proc.terminate()
        # _finish_run will be called via the IO_HUP path once the process exits;
        # reset mode immediately so the run button is usable again right away.
        self._run_btn_mode = "run"
        self._run_btn.set_label("▶  Run Workflow")
        self._run_btn.set_sensitive(True)

    def _on_run_clicked(self, _btn) -> None:
        # If the button is in cancel mode (active run in progress), cancel it.
        if self._run_btn_mode == "cancel":
            self._cancel_run()
            return

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

        # Repurpose the run button as a cancel button while a run is active.
        # Keeping it sensitive lets the user abort a long-running SkyReels warmup
        # without having to kill the terminal process manually.
        self._run_btn.set_label("■  Cancel")
        self._run_btn.set_sensitive(True)
        self._run_btn_mode = "cancel"

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

            # Fix 5: Capture the log file path emitted early by run_workflow.sh.
            # Format: LOG:/path/to/file.log — store in run record so the history
            # row "Log" button can open it in LogViewerWindow.
            if line.startswith("LOG:"):
                log_path = line[4:].strip()
                run["log_file"] = log_path
                _run_index.update(run["id"], log_file=log_path)

            # Parse progress hints from run_workflow.sh.
            # Matches lines starting with ══ (log_step) or [ (tagged progress like
            # "[SkyReels warmup] 8min elapsed" or "[10:23:45] Node 1:…").
            if line.startswith("══") or line.startswith("["):
                step = line.strip("═ []")
                progress = step[:40] if step else "running…"
                run["progress"] = progress
                _run_index.update(run["id"], progress=progress)
                if prog_lbl:
                    GLib.idle_add(prog_lbl.set_label, progress)

            # Fix 4: SkyReels warmup lines — run_workflow.sh tails the container log
            # and emits "⏳ SkyReels: <last non-empty log line>" every ~2 min.
            # Show these verbatim in the progress label so the operator can see
            # compile / weight-load progress without opening the full log.
            if line.startswith("⏳ SkyReels:") or "⏳ SkyReels:" in line:
                skyreels_text = line.strip()[:60]
                run["progress"] = skyreels_text
                _run_index.update(run["id"], progress=skyreels_text)
                if prog_lbl:
                    GLib.idle_add(prog_lbl.set_label, skyreels_text)

            # Fix 3 / Fix 5: Surface ⚠️ partial-failure / warning lines in the
            # progress label so operators notice skipped nodes without opening
            # the log file.  Also track warning count for the badge in history rows.
            if "⚠️" in line or "partial failures" in line.lower():
                warning_text = line.strip()[:60]
                run["had_partial_failure"] = True
                run["warning_count"] = run.get("warning_count", 0) + 1
                _run_index.update(run["id"], had_partial_failure=True,
                                  warning_count=run["warning_count"])
                if prog_lbl:
                    GLib.idle_add(prog_lbl.set_label, warning_text)

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

        # Restore the run button to its default state
        self._run_btn.set_label("▶  Run Workflow")
        self._run_btn.set_sensitive(True)
        self._run_btn_mode = "run"
        self._active_run_id = None
        self._active_run_proc = None

        # Refresh the history display for this spec
        idx = self._spec_dd.get_selected()
        if idx < len(self._specs):
            self._rebuild_history(self._specs[idx]["path"])
