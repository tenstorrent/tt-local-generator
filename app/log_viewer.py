# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Log viewer -- pure-Python helpers (testable) + LogViewerWindow (GTK).

Pure helpers: detect_log_path, shorten_error, parse_run_log_name,
parse_server_log_name, is_error_log, collect_log_files.

LogViewerWindow: sidebar tree (ListBox) + content pane (TextView).
Added in the GTK section below.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# ── Repo and log locations ─────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOGS_DIR  = _REPO_ROOT / "logs"
_USER_LOGS_DIR = Path.home() / ".local" / "share" / "tt-local-generator" / "logs"
_ANIMATEDIFF_LOG_DIR = _USER_LOGS_DIR / "animatediff"
_TRANSFORMS_LOG_DIR  = _USER_LOGS_DIR / "transforms"
_PROMPT_LOG = Path("/tmp/tt_prompt_gen.log")


# ── Pure helpers ───────────────────────────────────────────────────────────────

def detect_log_path(message: str) -> Optional[str]:
    """Extract an absolute log file path from an error message string.

    Looks for lines starting with "Full log:" or "Log:" (case-insensitive).
    Returns None if no path is found.
    """
    for line in message.splitlines():
        stripped = line.strip()
        for prefix in ("Full log:", "Log:"):
            if stripped.lower().startswith(prefix.lower()):
                path = stripped[len(prefix):].strip()
                if path:
                    return path
    return None


def shorten_error(message: str) -> str:
    """Return the first non-path line of *message*, truncated to 80 characters.

    Removes any embedded log path lines so long path strings never reach the
    status label. If the first line exceeds 80 chars, it is truncated with
    the ellipsis character (U+2026).
    """
    lines = [
        ln for ln in message.splitlines()
        if not ln.strip().lower().startswith(("full log:", "log:"))
    ]
    first = (lines[0] if lines else message).strip()
    if len(first) > 80:
        return first[:80] + "…"
    return first


def parse_run_log_name(filename: str) -> tuple[str, str]:
    """Parse an AnimateDiff run log filename into (display_name, date_str).

    Expected format: run_YYYYMMDD_HHMMSS_YYYYMMDD_HHMMSS_<jobid8>.log
    Returns ("run_HH:MM", "Mon D") on success, or (stem, "") on mismatch.
    """
    stem = Path(filename).stem
    m = re.match(r"run_(\d{8})_(\d{6})_\d{8}_\d{6}_[0-9a-f]+$", stem)
    if not m:
        return stem, ""
    date_part, time_part = m.group(1), m.group(2)
    hh, mm = time_part[:2], time_part[2:4]
    try:
        from datetime import datetime
        dt = datetime.strptime(date_part, "%Y%m%d")
        date_str = dt.strftime("%b %-d")
    except ValueError:
        date_str = date_part
    return f"run_{hh}:{mm}", date_str


def parse_server_log_name(filename: str) -> tuple[str, str]:
    """Parse a server log filename into (model_name, date_str).

    Expected format: media_YYYY-MM-DD_HH-MM-SS_<ModelName>_<device>_server.log
    Returns (model_name, date_str) on success, or (stem, "") on mismatch.
    """
    stem = Path(filename).stem
    if stem.endswith("_server"):
        stem = stem[:-7]
    m = re.match(r"media_(\d{4}-\d{2}-\d{2})_\d{2}-\d{2}-\d{2}_(.+)$", stem)
    if not m:
        return stem, ""
    _DEVICE_TOKENS = frozenset({
        "p150x4", "p300x2", "p300c", "p150", "p300",
        "n150", "n300", "qb2", "t3k", "galaxy",
    })
    date_raw, rest = m.group(1), m.group(2)
    parts = rest.rsplit("_", 1)
    if len(parts) == 2 and parts[1].lower() in _DEVICE_TOKENS:
        model = parts[0]
    else:
        model = rest
    try:
        from datetime import datetime
        dt = datetime.strptime(date_raw, "%Y-%m-%d")
        date_str = dt.strftime("%b %-d")
    except ValueError:
        date_str = date_raw
    return model, date_str


def is_error_log(content: str) -> bool:
    """Return True if *content* looks like a failed run log."""
    indicators = ("rc=1", "Traceback (most recent", "FAILED", "exited with rc=")
    return any(ind in content for ind in indicators)


def collect_log_files(
    animatediff_log_dir: "Optional[Path]" = None,
    repo_root: Path = _REPO_ROOT,
    prompt_log: Optional[Path] = _PROMPT_LOG,
) -> list[dict]:
    """Return a list of section dicts for the log tree.

    Each section dict: {"section": str, "files": list[dict]}
    Each file dict:    {"path": str, "name": str, "date": str, "is_error": bool}

    Sections (in order): ANIMATEDIFF, SERVERS, PROMPT, APP
    Missing directories/files are silently skipped.
    """
    sections = []

    # TRANSFORMS — forge plugin transform logs (rmbg, blip, depth, etc.)
    tx_dir = _TRANSFORMS_LOG_DIR
    tx_logs = sorted(tx_dir.glob("*.log"), reverse=True) if tx_dir.exists() else []
    if tx_logs:
        files = []
        for p in tx_logs:
            # Filename: YYYYMMDD_HHMMSS_<plugin>_<source_stem>.log
            parts = p.stem.split("_", 3)
            date = f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:]} {parts[1][:2]}:{parts[1][2:4]}" if len(parts) >= 2 else ""
            plugin = parts[2] if len(parts) > 2 else p.stem
            src_stem = parts[3] if len(parts) > 3 else ""
            name = f"{plugin}  ←  {src_stem}" if src_stem else plugin
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            files.append({"path": str(p), "name": name, "date": date,
                          "is_error": is_error_log(content)})
        sections.append({"section": "TRANSFORMS", "files": files})

    # ANIMATEDIFF run logs
    ad_dir = animatediff_log_dir if animatediff_log_dir is not None else _USER_LOGS_DIR / "animatediff"
    run_logs = sorted(ad_dir.glob("run_*.log"), reverse=True) if ad_dir.exists() else []
    if run_logs:
        files = []
        for p in run_logs:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            name, date = parse_run_log_name(p.name)
            files.append({"path": str(p), "name": name, "date": date,
                          "is_error": is_error_log(content)})
        sections.append({"section": "ANIMATEDIFF", "files": files})

    # SERVERS — media_*_server.log files in repo root
    server_logs = sorted(repo_root.glob("media_*_server.log"), reverse=True)
    if server_logs:
        files = []
        for p in server_logs:
            model, date = parse_server_log_name(p.name)
            files.append({"path": str(p), "name": model, "date": date,
                          "is_error": False})
        sections.append({"section": "SERVERS", "files": files})

    # PROMPT — /tmp/tt_prompt_gen.log (optional, may not exist)
    if prompt_log and prompt_log.exists():
        sections.append({"section": "PROMPT", "files": [
            {"path": str(prompt_log), "name": prompt_log.stem,
             "date": "", "is_error": False}
        ]})

    # APP — the animatediff module log (distinct from run logs)
    app_log = (animatediff_log_dir / "animatediff.log") if animatediff_log_dir is not None else (_USER_LOGS_DIR / "animatediff" / "animatediff.log")
    if app_log.exists():
        sections.append({"section": "APP", "files": [
            {"path": str(app_log), "name": "animatediff",
             "date": "", "is_error": False}
        ]})

    return sections


# ── GTK Widget ────────────────────────────────────────────────────────────────

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango


class LogViewerWindow(Gtk.Window):
    """Standalone log browser: sidebar tree (left) + content pane (right).

    Usage:
        win = LogViewerWindow(parent=main_window)
        win.present()
        win.open_to("/path/to/specific.log")  # optional: jump to a file
    """

    def __init__(self, parent: Gtk.Window):
        super().__init__(title="Log Viewer")
        self.set_default_size(900, 600)
        self.set_transient_for(parent)
        self.set_destroy_with_parent(False)

        # ── Outer paned: sidebar | content ────────────────────────────────────
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_vexpand(True)
        paned.set_position(220)
        self.set_child(paned)

        # ── Left sidebar ──────────────────────────────────────────────────────
        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_size_request(180, -1)
        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list_box.add_css_class("log-sidebar")
        self._list_box.connect("row-selected", self._on_row_selected)
        sidebar_scroll.set_child(self._list_box)
        paned.set_start_child(sidebar_scroll)
        paned.set_resize_start_child(False)

        # ── Right content area ────────────────────────────────────────────────
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        right_box.set_hexpand(True)
        right_box.set_vexpand(True)

        content_scroll = Gtk.ScrolledWindow()
        content_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content_scroll.set_vexpand(True)
        self._content_scroll = content_scroll

        self._text_buf = Gtk.TextBuffer()
        self._tv = Gtk.TextView.new_with_buffer(self._text_buf)
        self._tv.set_editable(False)
        self._tv.set_cursor_visible(False)
        self._tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._tv.add_css_class("log-content")
        content_scroll.set_child(self._tv)
        right_box.append(content_scroll)

        # ── Footer ────────────────────────────────────────────────────────────
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        footer.add_css_class("log-footer")
        footer.set_margin_start(8)
        footer.set_margin_end(8)
        footer.set_margin_top(4)
        footer.set_margin_bottom(4)

        self._path_lbl = Gtk.Label(label="")
        self._path_lbl.set_ellipsize(Pango.EllipsizeMode.START)
        self._path_lbl.set_hexpand(True)
        self._path_lbl.set_xalign(0)
        self._path_lbl.add_css_class("muted")
        footer.append(self._path_lbl)

        copy_path_btn = Gtk.Button(label="Copy path")
        copy_path_btn.add_css_class("log-footer-btn")
        copy_path_btn.connect("clicked", self._on_copy_path)
        footer.append(copy_path_btn)

        copy_all_btn = Gtk.Button(label="Copy all")
        copy_all_btn.add_css_class("log-footer-btn")
        copy_all_btn.connect("clicked", self._on_copy_all)
        footer.append(copy_all_btn)

        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.add_css_class("log-footer-btn")
        refresh_btn.connect("clicked", lambda _: self.refresh())
        footer.append(refresh_btn)

        right_box.append(footer)
        paned.set_end_child(right_box)

        # ── State ──────────────────────────────────────────────────────────────
        self._current_path: Optional[str] = None
        self._row_paths: dict = {}

        # ── Color tags ────────────────────────────────────────────────────────
        self._tag_error = self._text_buf.create_tag("error-line",
            foreground="#FF6B6B")
        self._tag_muted = self._text_buf.create_tag("muted-line",
            foreground="#607D8B")

        self._build_tree()

    # ── Tree ───────────────────────────────────────────────────────────────────

    def _build_tree(self) -> None:
        """Populate the sidebar ListBox from collect_log_files()."""
        while True:
            row = self._list_box.get_row_at_index(0)
            if row is None:
                break
            self._list_box.remove(row)
        self._row_paths.clear()

        sections = collect_log_files()
        for section in sections:
            hdr = Gtk.Label(label=section["section"])
            hdr.set_xalign(0)
            hdr.add_css_class("log-section-header")
            hdr_row = Gtk.ListBoxRow()
            hdr_row.set_selectable(False)
            hdr_row.set_activatable(False)
            hdr_row.set_child(hdr)
            self._list_box.append(hdr_row)

            for f in section["files"]:
                row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                row_box.set_margin_start(12)
                row_box.set_margin_top(2)
                row_box.set_margin_bottom(2)

                if f["is_error"]:
                    icon_lbl = Gtk.Label(label="✗")
                    icon_lbl.add_css_class("log-row-error")
                else:
                    icon_lbl = Gtk.Label(label="✓")
                    icon_lbl.add_css_class("log-row-ok")
                row_box.append(icon_lbl)

                name_lbl = Gtk.Label(label=f["name"])
                name_lbl.set_xalign(0)
                name_lbl.set_hexpand(True)
                name_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                row_box.append(name_lbl)

                if f["date"]:
                    date_lbl = Gtk.Label(label=f["date"])
                    date_lbl.add_css_class("muted")
                    row_box.append(date_lbl)

                file_row = Gtk.ListBoxRow()
                file_row.set_child(row_box)
                self._list_box.append(file_row)
                self._row_paths[file_row] = f["path"]

    def refresh(self) -> None:
        """Re-scan log files and rebuild the sidebar."""
        current = self._current_path
        self._build_tree()
        if current:
            self.open_to(current)

    # ── File loading ───────────────────────────────────────────────────────────

    def _load_file(self, path: str) -> None:
        """Load *path* into the content pane with error/muted line highlighting."""
        self._current_path = path
        self._path_lbl.set_label(path)
        self._path_lbl.set_tooltip_text(path)

        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            content = f"[Could not read file: {e}]"

        lines = content.splitlines()
        truncated = False
        if len(lines) > 50_000:
            lines = lines[-50_000:]
            truncated = True

        self._text_buf.set_text("")
        if truncated:
            end = self._text_buf.get_end_iter()
            self._text_buf.insert(end, "[... file truncated - showing last 50,000 lines ...]\n")

        first_error_mark = None
        for line in lines:
            end = self._text_buf.get_end_iter()
            line_start_offset = end.get_offset()
            self._text_buf.insert(end, line + "\n")

            ls = self._text_buf.get_iter_at_offset(line_start_offset)
            le = self._text_buf.get_end_iter()
            lo = line.lower()
            if any(k in lo for k in ("critical", "traceback", "rc=1", "failed", "error")):
                self._text_buf.apply_tag(self._tag_error, ls, le)
                if first_error_mark is None:
                    first_error_mark = self._text_buf.create_mark(None, ls, True)
            elif any(k in lo for k in (" | info ", " | debug ", "debug |", "info |")):
                self._text_buf.apply_tag(self._tag_muted, ls, le)

        def _scroll():
            if first_error_mark is not None:
                it = self._text_buf.get_iter_at_mark(first_error_mark)
                self._tv.scroll_to_iter(it, 0.1, False, 0.0, 0.0)
            else:
                adj = self._content_scroll.get_vadjustment()
                adj.set_value(adj.get_upper() - adj.get_page_size())
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_scroll)

    # ── Public API ─────────────────────────────────────────────────────────────

    def open_to(self, path: str) -> None:
        """Select *path* in the tree and load it. Refreshes tree if not found."""
        row = next((r for r, p in self._row_paths.items() if p == path), None)
        if row is None:
            self._build_tree()
            row = next((r for r, p in self._row_paths.items() if p == path), None)
        if row is not None:
            self._list_box.select_row(row)
            row.grab_focus()
        else:
            self._load_file(path)

    # ── Callbacks ──────────────────────────────────────────────────────────────

    def _on_row_selected(self, _lb: Gtk.ListBox, row) -> None:
        if row is None:
            return
        path = self._row_paths.get(row)
        if path:
            self._load_file(path)

    def _on_copy_path(self, _btn: Gtk.Button) -> None:
        if self._current_path:
            display = self.get_display()
            cb = display.get_clipboard()
            cb.set(self._current_path)

    def _on_copy_all(self, _btn: Gtk.Button) -> None:
        start = self._text_buf.get_start_iter()
        end = self._text_buf.get_end_iter()
        text = self._text_buf.get_text(start, end, False)
        if text:
            display = self.get_display()
            cb = display.get_clipboard()
            cb.set(text)
