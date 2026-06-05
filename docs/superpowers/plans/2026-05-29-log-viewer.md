# Log Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `LogViewerWindow` — sidebar tree + content pane for browsing all app log files — triggered from a new Debug menu, by double-clicking the status bar on error, and by `open_to(path)` from `_on_error`; simultaneously fix the status bar width expansion caused by long error messages.

**Architecture:** Pure-Python log parsing helpers live in a new `app/log_viewer.py` (testable without GTK). The `LogViewerWindow(Gtk.Window)` is in the same file. `MainWindow` holds a singleton `self._log_viewer_win`, adds a `Debug` submenu between `View` and the context slot (incrementing `_context_slot_idx` by 1), and fixes `_on_error` to truncate + store the log path.

**Tech Stack:** Python 3, GTK4/PyGObject (`Gtk.Window`, `Gtk.Paned`, `Gtk.ListBox`, `Gtk.TextView`, `Gtk.TextBuffer` with color tags), `Gio.SimpleAction`, pytest with no GTK.

---

## File Map

| Action | Path | Purpose |
|---|---|---|
| Create | `app/log_viewer.py` | Pure helpers + `LogViewerWindow` GTK widget |
| Modify | `app/main_window.py` | Fix `_on_error`; add Debug menu; singleton `_log_viewer_win`; status bar click; CSS |
| Create | `tests/test_log_viewer.py` | Unit tests for all pure-Python helpers |

---

## Task 1: Pure-Python log helpers (testable, no GTK)

**Files:**
- Create: `app/log_viewer.py`
- Create: `tests/test_log_viewer.py`

These five functions are the testable core of the feature. All GTK is in a separate section of the same file.

- [ ] **Write failing tests**

Create `tests/test_log_viewer.py`:

```python
"""Unit tests for log viewer pure-Python helpers."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def test_detect_log_path_from_full_log_line():
    from log_viewer import detect_log_path
    msg = "generate_blackhole_v2.py exited with rc=1\n\nLast output:\nfoo\n\nFull log: /home/ttuser/code/tt-local-generator/logs/animatediff/run_20260529_072047_001b89f4.log"
    assert detect_log_path(msg) == "/home/ttuser/code/tt-local-generator/logs/animatediff/run_20260529_072047_001b89f4.log"


def test_detect_log_path_missing():
    from log_viewer import detect_log_path
    assert detect_log_path("Something went wrong") is None


def test_detect_log_path_log_prefix():
    from log_viewer import detect_log_path
    msg = "Script exited 0 but no output file\nLog: /tmp/animatediff.log"
    assert detect_log_path(msg) == "/tmp/animatediff.log"


def test_shorten_error_truncates_at_80():
    from log_viewer import shorten_error
    long_msg = "A" * 100 + "\nSecond line"
    result = shorten_error(long_msg)
    assert len(result) <= 83  # 80 + "..."
    assert result.endswith("…")


def test_shorten_error_short_message_unchanged():
    from log_viewer import shorten_error
    msg = "AnimateDiff requires Blackhole hardware"
    assert shorten_error(msg) == msg


def test_shorten_error_strips_log_path():
    from log_viewer import shorten_error
    msg = "generate_blackhole_v2.py exited with rc=1\n\nFull log: /very/long/path.log"
    result = shorten_error(msg)
    assert "/very/long/path.log" not in result


def test_parse_run_log_name_returns_display():
    from log_viewer import parse_run_log_name
    name = "run_20260529_072047_20260529_142047_001b89f4.log"
    display, ts_str = parse_run_log_name(name)
    assert "07:20" in display
    assert ts_str  # non-empty date string


def test_parse_run_log_name_unrecognised():
    from log_viewer import parse_run_log_name
    display, ts_str = parse_run_log_name("animatediff.log")
    assert display == "animatediff"
    assert ts_str == ""


def test_parse_server_log_name_extracts_model():
    from log_viewer import parse_server_log_name
    name = "media_2026-05-06_08-14-06_SkyReels-V2-I2V-14B-540P_p300x2_server.log"
    model, date_str = parse_server_log_name(name)
    assert model == "SkyReels-V2-I2V-14B-540P"
    assert "May" in date_str or "2026" in date_str


def test_parse_server_log_name_wan():
    from log_viewer import parse_server_log_name
    name = "media_2026-05-06_09-10-02_Wan2.2-T2V-A14B-Diffusers_p300x2_server.log"
    model, _ = parse_server_log_name(name)
    assert model == "Wan2.2-T2V-A14B-Diffusers"


def test_is_error_log_rc1():
    from log_viewer import is_error_log
    content = "some output\nexited with rc=1\nmore"
    assert is_error_log(content) is True


def test_is_error_log_traceback():
    from log_viewer import is_error_log
    content = "running...\nTraceback (most recent call last):\n  File foo"
    assert is_error_log(content) is True


def test_is_error_log_clean():
    from log_viewer import is_error_log
    content = "# animatediff run\nSaved 4 frames → /tmp/out.gif\n"
    assert is_error_log(content) is False


def test_collect_log_files_animatediff(tmp_path):
    from log_viewer import collect_log_files
    logs_dir = tmp_path / "logs" / "animatediff"
    logs_dir.mkdir(parents=True)
    (logs_dir / "run_20260529_072047_20260529_142047_001b89f4.log").write_text("exited with rc=1")
    (logs_dir / "run_20260529_081148_20260529_151148_70059cab.log").write_text("Saved 4 frames")
    (logs_dir / "animatediff.log").write_text("module log")

    files = collect_log_files(repo_root=tmp_path, prompt_log=None)
    ad_section = next(s for s in files if s["section"] == "ANIMATEDIFF")
    assert len(ad_section["files"]) == 2   # run logs only, not animatediff.log
    # Sorted newest first
    names = [f["name"] for f in ad_section["files"]]
    assert names[0] > names[1]   # lexicographic desc = newest first
```

- [ ] **Run to confirm failure**

```bash
cd /home/ttuser/code/tt-local-generator
/usr/bin/python3 -m pytest tests/test_log_viewer.py -v 2>&1 | tail -15
```

Expected: ImportError — `log_viewer` doesn't exist.

- [ ] **Create `app/log_viewer.py` with the pure helpers**

```python
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Log viewer — pure-Python helpers (testable) + LogViewerWindow (GTK).

Pure helpers: detect_log_path, shorten_error, parse_run_log_name,
parse_server_log_name, is_error_log, collect_log_files.

LogViewerWindow: sidebar tree (ListBox) + content pane (TextView).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# ── Repo and log locations ─────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOGS_DIR  = _REPO_ROOT / "logs"
_ANIMATEDIFF_LOG_DIR = _LOGS_DIR / "animatediff"
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
    """Return the first line of *message*, truncated to 80 characters.

    Removes any embedded log path lines so long path strings never reach the
    status label. If the first line exceeds 80 chars, it is truncated with U+2026.
    """
    # Strip lines that look like log paths
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
    Returns ("run_HH:MM", "Mon DD") on success, or (stem, "") on mismatch.
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
    stem = Path(filename).stem  # strip .log
    # Strip trailing _server
    if stem.endswith("_server"):
        stem = stem[:-7]
    # Split off leading media_YYYY-MM-DD_HH-MM-SS_
    m = re.match(r"media_(\d{4}-\d{2}-\d{2})_\d{2}-\d{2}-\d{2}_(.+)$", stem)
    if not m:
        return stem, ""
    date_raw, rest = m.group(1), m.group(2)
    # Model name is everything before the last _<device> segment
    # device patterns: p300x2, p150x4, n150, n300 — single word with digits
    parts = rest.rsplit("_", 1)
    model = parts[0] if len(parts) == 2 else rest
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
    repo_root: Path = _REPO_ROOT,
    prompt_log: Optional[Path] = _PROMPT_LOG,
) -> list[dict]:
    """Return a list of section dicts for the log tree.

    Each section dict: {"section": str, "files": list[dict]}
    Each file dict:    {"path": str, "name": str, "date": str, "is_error": bool}

    Sections (in order): ANIMATEDIFF, SERVERS, PROMPT, APP
    Missing directories / files are silently skipped.
    """
    sections = []

    # ── ANIMATEDIFF ────────────────────────────────────────────────────────────
    ad_dir = repo_root / "logs" / "animatediff"
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

    # ── SERVERS ────────────────────────────────────────────────────────────────
    server_logs = sorted(repo_root.glob("media_*_server.log"), reverse=True)
    if server_logs:
        files = []
        for p in server_logs:
            model, date = parse_server_log_name(p.name)
            files.append({"path": str(p), "name": model, "date": date,
                          "is_error": False})
        sections.append({"section": "SERVERS", "files": files})

    # ── PROMPT ─────────────────────────────────────────────────────────────────
    if prompt_log and prompt_log.exists():
        sections.append({"section": "PROMPT", "files": [
            {"path": str(prompt_log), "name": prompt_log.stem, "date": "", "is_error": False}
        ]})

    # ── APP ────────────────────────────────────────────────────────────────────
    app_log = repo_root / "logs" / "animatediff" / "animatediff.log"
    if app_log.exists():
        sections.append({"section": "APP", "files": [
            {"path": str(app_log), "name": "animatediff", "date": "", "is_error": False}
        ]})

    return sections
```

- [ ] **Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_log_viewer.py -v 2>&1 | tail -20
```

Expected: all 14 tests pass.

- [ ] **Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Commit**

```bash
git add app/log_viewer.py tests/test_log_viewer.py
git commit -m "feat(logs): add log viewer pure helpers — detect_log_path, shorten_error, parse_*, collect_log_files"
```

---

## Task 2: `LogViewerWindow` GTK widget

**Files:**
- Modify: `app/log_viewer.py` (append GTK section)

No tests (GTK requires a display). The widget is thin over the helpers from Task 1.

- [ ] **Append `LogViewerWindow` to `app/log_viewer.py`**

```python
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

        refresh_btn = Gtk.Button(label="↻ Refresh")
        refresh_btn.add_css_class("log-footer-btn")
        refresh_btn.connect("clicked", lambda _: self.refresh())
        footer.append(refresh_btn)

        right_box.append(footer)
        paned.set_end_child(right_box)

        # ── State ──────────────────────────────────────────────────────────────
        self._current_path: Optional[str] = None
        self._row_paths: dict[Gtk.ListBoxRow, str] = {}

        # ── Color tags ────────────────────────────────────────────────────────
        self._tag_error = self._text_buf.create_tag("error-line",
            foreground="#FF6B6B")
        self._tag_muted = self._text_buf.create_tag("muted-line",
            foreground="#607D8B")

        self._build_tree()

    # ── Tree ───────────────────────────────────────────────────────────────────

    def _build_tree(self) -> None:
        """Populate the sidebar ListBox from collect_log_files()."""
        # Remove all existing rows
        while True:
            row = self._list_box.get_row_at_index(0)
            if row is None:
                break
            self._list_box.remove(row)
        self._row_paths.clear()

        sections = collect_log_files()
        for section in sections:
            # Section header (non-selectable)
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
                    icon_lbl = Gtk.Label(label="✗")  # ✗
                    icon_lbl.add_css_class("log-row-error")
                else:
                    icon_lbl = Gtk.Label(label="✓")  # ✓
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

        # Truncate very large files
        lines = content.splitlines()
        truncated = False
        if len(lines) > 50_000:
            lines = lines[-50_000:]
            truncated = True

        self._text_buf.set_text("")
        start = self._text_buf.get_start_iter()
        if truncated:
            self._text_buf.insert(start, "[... file truncated — showing last 50,000 lines ...]\n")
            start = self._text_buf.get_end_iter()

        first_error_mark = None
        for line in lines:
            end = self._text_buf.get_end_iter()
            line_start_offset = end.get_offset()
            self._text_buf.insert(end, line + "\n")

            # Apply color tags
            ls = self._text_buf.get_iter_at_offset(line_start_offset)
            le = self._text_buf.get_end_iter()
            lo = line.lower()
            if any(k in lo for k in ("critical", "traceback", "rc=1", "failed", "error")):
                self._text_buf.apply_tag(self._tag_error, ls, le)
                if first_error_mark is None:
                    first_error_mark = self._text_buf.create_mark(None, ls, True)
            elif any(k in lo for k in (" | info ", " | debug ", "debug |", "info |")):
                self._text_buf.apply_tag(self._tag_muted, ls, le)

        # Scroll to first error, or to end
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
        # Try to find the row
        row = next((r for r, p in self._row_paths.items() if p == path), None)
        if row is None:
            self._build_tree()
            row = next((r for r, p in self._row_paths.items() if p == path), None)
        if row is not None:
            self._list_box.select_row(row)
            # Scroll sidebar to show the row
            row.grab_focus()
        else:
            # File not in tree — load it directly without selecting
            self._load_file(path)

    # ── Callbacks ──────────────────────────────────────────────────────────────

    def _on_row_selected(self, _lb: Gtk.ListBox, row: Optional[Gtk.ListBoxRow]) -> None:
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
```

- [ ] **Run full suite (no new tests — GTK can't be unit tested)**

```bash
/usr/bin/python3 -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all still pass.

- [ ] **Commit**

```bash
git add app/log_viewer.py
git commit -m "feat(logs): add LogViewerWindow GTK widget"
```

---

## Task 3: Fix `_on_error` + status bar click in `MainWindow`

**Files:**
- Modify: `app/main_window.py`

Two changes: (1) `_on_error` truncates the message and stores the log path, (2) clicking the status label when a log path is stored opens the viewer.

- [ ] **Initialize `_last_error_log_path` and `_log_viewer_win` in `MainWindow.__init__`**

Find the instance variable block (around line 6862, where `_attractor_win` and `_prefs_dialog` are defined). Add after `self._prefs_dialog`:

```python
        self._last_error_log_path: "str | None" = None  # log path from most recent error
        self._log_viewer_win: "LogViewerWindow | None" = None  # singleton log viewer
```

- [ ] **Fix `_on_error` (around line 9400)**

Replace:

```python
    def _on_error(self, message: str) -> bool:
        gallery = self._gen_gallery or self._active_gallery()
        gallery.remove_pending()
        self._gen_gallery = None
        self._controls.set_busy(False)
        self._set_status(f"Error: {message}")
        self._screensaver_uninhibit()
        self._start_next_queued()
        return False
```

With:

```python
    def _on_error(self, message: str) -> bool:
        from log_viewer import detect_log_path, shorten_error
        gallery = self._gen_gallery or self._active_gallery()
        gallery.remove_pending()
        self._gen_gallery = None
        self._controls.set_busy(False)
        self._screensaver_uninhibit()

        # Extract log path before truncating, then show a short status message.
        # Long messages (e.g. full animatediff output + log path) would force the
        # status label to expand the window width if displayed in full.
        self._last_error_log_path = detect_log_path(message)
        short = shorten_error(message)
        suffix = " — click for log" if self._last_error_log_path else ""
        self._set_status(f"Error: {short}{suffix}")

        self._start_next_queued()
        return False
```

- [ ] **Add `_open_log_viewer` method to `MainWindow`**

Place it near `_open_preferences` (around line 7494):

```python
    def _open_log_viewer(self, path: "str | None" = None) -> None:
        """Open (or present) the singleton LogViewerWindow, optionally jumping to *path*."""
        from log_viewer import LogViewerWindow
        if self._log_viewer_win is None or not self._log_viewer_win.get_visible():
            self._log_viewer_win = LogViewerWindow(parent=self)
        self._log_viewer_win.present()
        if path:
            self._log_viewer_win.open_to(path)
```

- [ ] **Add status bar click handler**

Find where `self._status_lbl` is created (around line 7063):

```python
        self._status_lbl = Gtk.Label(label="Ready")
        self._status_lbl.set_xalign(0)
        self._status_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self._status_lbl.set_hexpand(True)
        self._status_lbl.add_css_class("status-bar")
        gallery_wrap.append(self._status_lbl)
```

Add after the `gallery_wrap.append(self._status_lbl)` line:

```python
        # Click the status label when an error log is available → open log viewer
        _status_click = Gtk.GestureClick()
        _status_click.connect("released", self._on_status_bar_clicked)
        self._status_lbl.add_controller(_status_click)
```

Add the handler method near `_set_status` (around line 7113):

```python
    def _on_status_bar_clicked(self, _gesture, _n_press, _x, _y) -> None:
        """Open log viewer to the most recent error log when status bar is clicked."""
        if self._last_error_log_path:
            self._open_log_viewer(self._last_error_log_path)
        elif self._status_lbl.get_label().startswith("Error"):
            self._open_log_viewer()
```

- [ ] **Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Commit**

```bash
git add app/main_window.py
git commit -m "fix(status): truncate error messages; add click-to-open-log on status bar"
```

---

## Task 4: Debug menu + `win.open-log-viewer` / `win.open-logs-folder` actions

**Files:**
- Modify: `app/main_window.py`

Add `Debug` as a new fixed menu between `View` and the context slot. Update `_context_slot_idx` (it will now be 4 instead of 3).

- [ ] **Add new actions to `_build_menu_actions`**

Find the end of `_build_menu_actions` (just before `def _build_menu_bar`, around line 7271). Add:

```python
        # ── Debug: log viewer ─────────────────────────────────────────────────
        open_logs_action = Gio.SimpleAction.new("open-log-viewer", None)
        open_logs_action.connect("activate", lambda *_: self._open_log_viewer())
        self.add_action(open_logs_action)

        open_logs_folder_action = Gio.SimpleAction.new("open-logs-folder", None)
        open_logs_folder_action.connect("activate", self._on_open_logs_folder)
        self.add_action(open_logs_folder_action)
```

- [ ] **Add `_on_open_logs_folder` handler near `_on_open_media_folder`**

Find `_on_open_media_folder` (search for it in the file). Add after it:

```python
    def _on_open_logs_folder(self, _action, _param) -> None:
        """Open the logs/ directory in the system file manager."""
        from log_viewer import _LOGS_DIR
        logs_uri = f"file://{_LOGS_DIR}"
        try:
            Gio.AppInfo.launch_default_for_uri(logs_uri, None)
        except Exception as e:
            self._set_status(f"Could not open logs folder: {e}")
```

- [ ] **Add Debug submenu to `_build_menu_bar`**

Find the View submenu section (around line 7307–7319) and add immediately after `self._menumodel.append_submenu("View", view_menu)`:

```python
        # ── Debug ─────────────────────────────────────────────────────────────
        debug_menu = Gio.Menu()
        debug_menu.append("Open Log Viewer", "win.open-log-viewer")
        debug_menu.append("Open Logs Folder…", "win.open-logs-folder")
        self._menumodel.append_submenu("Debug", debug_menu)
```

- [ ] **Fix `_context_slot_idx`**

The context slot line is:
```python
        self._context_slot_idx = self._menumodel.get_n_items()
```
This is called *after* all fixed menus are appended, so it will automatically be 4 (File=0, Playlists=1, View=2, Debug=3, context=4). No manual change needed — the `get_n_items()` call is already correct.

- [ ] **Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Commit**

```bash
git add app/main_window.py
git commit -m "feat(menu): add Debug menu with Open Log Viewer and Open Logs Folder"
```

---

## Task 5: CSS for log viewer

**Files:**
- Modify: `app/main_window.py` (CSS bytes literal)

The CSS bytes literal (`_CSS = b"""..."""`) spans lines 71–1328. All new rules must use ASCII-only characters.

- [ ] **Add log viewer CSS to `app/main_window.py`**

Find the closing `"""` of `_CSS` (around line 1328, after `color: #E8F0F2; font-size: 12px; }`). Insert before that closing `"""`:

```css
/* -- Log viewer ------------------------------------------------------------ */
.log-sidebar {
    background-color: @tt_bg_panel;
}
.log-section-header {
    color: @tt_text_muted;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    padding: 8px 12px 2px;
}
.log-row-error {
    color: @tt_error;
}
.log-row-ok {
    color: @tt_success;
}
.log-content {
    font-family: "Noto Mono", "Fira Code", monospace;
    font-size: 11px;
    background-color: @tt_bg_darkest;
    color: @tt_text;
    padding: 8px;
}
.log-footer {
    background-color: @tt_bg_panel;
    border-top: 1px solid @tt_border;
}
.log-footer-btn {
    background: rgba(79, 209, 197, 0.12);
    border: 1px solid @tt_accent;
    border-radius: 3px;
    color: @tt_accent;
    padding: 2px 8px;
    font-size: 11px;
}
.log-footer-btn:hover {
    background: rgba(79, 209, 197, 0.25);
}
```

**IMPORTANT:** No em dashes, curly quotes, or non-ASCII characters anywhere in these CSS rules — the entire `_CSS` block is a `b"""..."""` bytes literal. Use plain ASCII hyphens, straight quotes, and ASCII-only text.

- [ ] **Verify no syntax error**

```bash
/usr/bin/python3 -c "import ast; ast.parse(open('app/main_window.py').read()); print('syntax OK')"
```

- [ ] **Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Commit**

```bash
git add app/main_window.py
git commit -m "feat(logs): add log viewer CSS"
```

---

## Task 6: Final cleanup and push

**Files:**
- Modify: `app/main_window.py` (verify no regressions)

- [ ] **Verify Debug menu is in the right position**

```bash
grep -n "append_submenu.*Debug\|append_submenu.*View\|_context_slot_idx" app/main_window.py | head -5
```

Expected: View appears before Debug, Debug before `_context_slot_idx`.

- [ ] **Verify `_on_error` no longer puts full log path in status label**

```bash
grep -n "def _on_error" app/main_window.py
```

Read the function — it should call `shorten_error` and show `"Error: <short>[ — click for log]"`.

- [ ] **Run full suite one final time**

```bash
/usr/bin/python3 -m pytest tests/ -q 2>&1 | tail -10
```

- [ ] **Push**

```bash
git push origin HEAD
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| §1 `_on_error` truncates to 80 chars | Task 3 ✓ |
| §1 `detect_log_path` + `shorten_error` | Task 1 ✓ |
| §1 `self._last_error_log_path` stored | Task 3 ✓ |
| §1 Status bar click opens viewer to log | Task 3 ✓ |
| §2 `LogViewerWindow(Gtk.Window)` | Task 2 ✓ |
| §2 `default_size=900×600`, `set_transient_for` | Task 2 ✓ |
| §2 Sidebar 220px, `Gtk.ListBox` | Task 2 ✓ |
| §2 Content pane `Gtk.TextView`, `WORD_CHAR` wrap | Task 2 ✓ |
| §2 Section headers non-selectable | Task 2 ✓ |
| §2 ✓/✗ icons per run log | Task 2 ✓ |
| §2 Auto-scroll to first error line | Task 2 (`_load_file`) ✓ |
| §2 50,000 line truncation | Task 2 ✓ |
| §2 `open_to(path)` selects + loads | Task 2 ✓ |
| §2 Copy path + Copy all buttons | Task 2 ✓ |
| §2 ↻ Refresh button | Task 2 ✓ |
| §2 Footer path label with ellipsis | Task 2 ✓ |
| §3 Debug menu between View and context slot | Task 4 ✓ |
| §3 `win.open-log-viewer` action | Task 4 ✓ |
| §3 `win.open-logs-folder` action | Task 4 ✓ |
| §3 `_context_slot_idx` still correct | Task 4 (auto via `get_n_items()`) ✓ |
| §4 CSS for all log viewer classes | Task 5 ✓ |
| §5 Files changed: `log_viewer.py`, `main_window.py` | Tasks 1–5 ✓ |
| §6 Test plan (all helpers testable) | Task 1 ✓ |
| Singleton `_log_viewer_win` | Task 3 ✓ |

**Type consistency:** `detect_log_path` returns `Optional[str]`, `shorten_error` returns `str`, `collect_log_files` returns `list[dict]` — consistent across Tasks 1, 2, 3. `open_to(path: str)` defined in Task 2, called with `str` in Task 3. ✓

**One gap in spec:** `_on_open_logs_folder` uses `_LOGS_DIR` imported from `log_viewer`. The spec says `Gio.AppInfo.launch_default_for_uri` — verified this is the correct GTK4 API for opening a folder in the file manager. ✓
