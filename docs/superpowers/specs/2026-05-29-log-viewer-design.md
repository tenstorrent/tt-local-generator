# Log Viewer Design

> Branch: `feat/remix` | Date: 2026-05-29 | Status: approved

## Overview

Two related changes: (1) stop long error messages from expanding the status bar
width, and (2) add a `LogViewerWindow` — a standalone `Gtk.Window` with a
sidebar file tree and a content pane for reading, copying, and browsing all
app log files.

---

## 1. Status Bar Error Fix

**Problem:** `_on_error(message)` calls `self._set_status(f"Error: {message}")`.
The animatediff error message includes a full log path
(`…Full log: ~/code/tt-local-generator/logs/animatediff/run_20260529…log`) —
a long unbreakable string that forces the label's minimum natural width wide
enough to expand the window.

**Fix:** `_on_error` extracts just the first sentence of the error, truncates
it to ≤80 characters, and appends a clickable affordance if a log path is
detectable in the message.

```python
def _on_error(self, message: str) -> bool:
    gallery = self._gen_gallery or self._active_gallery()
    gallery.remove_pending()
    self._gen_gallery = None
    self._controls.set_busy(False)
    self._screensaver_uninhibit()

    # Extract the log path if present (animatediff errors include "Full log: <path>")
    log_path = None
    for line in message.splitlines():
        if line.startswith("Full log:") or line.startswith("Log:"):
            log_path = line.split(":", 1)[1].strip()
            break

    # Truncate the first sentence so the status label never forces window growth
    first_line = message.splitlines()[0] if message else "Unknown error"
    short = first_line[:80] + ("…" if len(first_line) > 80 else "")
    suffix = " — click for log" if log_path else ""
    self._set_status(f"Error: {short}{suffix}")

    # Store for status bar click handler (initialized to None in __init__)
    self._last_error_log_path = log_path
    self._start_next_queued()
    return False
```

The status label already has `set_ellipsize(Pango.EllipsizeMode.END)` and
`set_hexpand(True)` — the truncation ensures even without ellipsize the
natural width stays bounded.

**Status bar click:** `self._status_lbl` gains a `Gtk.GestureClick` controller.
When clicked and `self._last_error_log_path` is set, it calls
`self._open_log_viewer(self._last_error_log_path)`.

---

## 2. `LogViewerWindow`

**File:** `app/log_viewer.py` (new file, no GTK dependency in tests)

```python
class LogViewerWindow(Gtk.Window):
    """
    Standalone log browser. Sidebar tree on left, content pane on right.

    Usage:
        win = LogViewerWindow(parent=main_window)
        win.present()
        win.open_to("/path/to/specific.log")  # optional: jump to a file
    """
```

### 2.1 Window properties

- `title="Log Viewer"`
- `default_width=900`, `default_height=600`
- `set_transient_for(parent)` — stays on top of the main window
- `set_destroy_with_parent(False)` — survives main window hide (attractor mode)
- Resizable. Singleton: `MainWindow` holds `self._log_viewer_win` and calls
  `present()` if already open rather than creating a second instance.

### 2.2 Layout

```
┌─────────────────────────────────────────────────────────┐
│  🪵 Log Viewer                                    [✕]   │  ← titlebar
├──────────────────┬──────────────────────────────────────┤
│  ANIMATEDIFF     │  run_20260529_072047_001b89f4.log     │  ← content header
│  ✗ run_072047    │                                       │
│  ✓ run_081148    │  critical | Can't convert tensor...   │
│  ✓ run_080644    │  distributed on MeshShape([1, 4])...  │
│                  │  Supply a mesh_composer to...         │
│  SERVERS         │                                       │
│  SkyReels·May 6  │  RuntimeError: TT_FATAL @ ...        │
│  SkyReels·May 6  │                                       │
│                  │                                       │
│  PROMPT          │                                       │
│  prompt_gen      │                                       │
│                  │                                       │
│  APP             │                                       │
│  animatediff.log │                                       │
├──────────────────┴──────────────────────────────────────┤
│  ~/…/run_20260529_072047_001b89f4.log  [Copy path] [Copy all]  │
└─────────────────────────────────────────────────────────┘
```

**Left pane** (`Gtk.ScrolledWindow` + `Gtk.ListBox`): 220px wide, fixed.
Section headers are non-selectable rows with `.log-section-header` CSS.
File rows show: status icon (✓/✗/○) + short display name + muted timestamp.
Selecting a row loads the file into the right pane.

**Right pane** (`Gtk.ScrolledWindow` + `Gtk.TextView`):
- `set_editable(False)`, `set_cursor_visible(False)`
- `set_wrap_mode(Gtk.WrapMode.WORD_CHAR)` — no horizontal scrollbar, lines wrap
- Monospace font via CSS class `.log-content`
- On load: if the file contains a traceback or `rc=1`, auto-scroll to the first
  error line (search for `Traceback` or `critical` or `rc=`); otherwise scroll
  to end

**Footer bar**: shows the full resolved path (truncated with ellipsis if needed,
tooltip shows full path), `[Copy path]` button, `[Copy all]` button.

### 2.3 Log tree structure

`LogViewerWindow._build_tree()` scans for log files and builds the sidebar.
Called on `__init__` and on `refresh()` (toolbar button).

**Sections and sources:**

| Section header | Source | Sort |
|---|---|---|
| `ANIMATEDIFF` | `~/code/tt-local-generator/logs/animatediff/run_*.log` | newest first |
| `SERVERS` | `~/code/tt-local-generator/media_*_server.log` (repo root, not logs/) | newest first |
| `PROMPT` | `/tmp/tt_prompt_gen.log` | n/a |
| `APP` | `~/code/tt-local-generator/logs/animatediff/animatediff.log` | n/a |

**Display name derivation:**

- AnimateDiff run: `run_<datetime>_<id>.log` → show as `run_<HH:MM>` with date
  if not today; prefix `✗` if log contains `rc=1` or `Traceback`, else `✓`
- Server log: `media_<datetime>_<ModelName>_<device>_server.log` →
  show as `<ModelName> · <date>`
- Prompt / App: show filename without extension

**Status icon colors:**
- `✗` → `.log-row-error` (red)
- `✓` → `.log-row-ok` (green)
- `○` → `.log-row-neutral` (muted, for server/app logs)

### 2.4 `open_to(path: str)` method

Selects the row matching `path` in the tree. If the path isn't in the current
tree (e.g. a brand-new log file), calls `refresh()` first, then selects.
Scrolls the list to make the selected row visible. Loads the file into the
content pane and scrolls to the first error line if one exists.

### 2.5 File loading

`_load_file(path: str)` reads the file, sets the `Gtk.TextBuffer`, applies
color tags:
- Lines containing `critical`, `error`, `rc=1`, `Traceback`, `FAILED` →
  `error-line` tag (red foreground)
- Lines containing `info |`, `debug |` → `muted-line` tag (muted foreground)
- Lines containing a file path ending in `.log` → `path-line` tag (teal,
  underlined) — clicking opens that file in the viewer

Large files (>1 MB): read last 50,000 lines only, prepend
`"[… file truncated — showing last 50,000 lines …]\n"`.

Loading is synchronous (files are small). For files >500 KB, load in a
background thread and show a spinner in the content pane while loading.

---

## 3. Debug Menu Entry

**New menu entry** in the fixed menu bar, inserted between `View` and the
context slot:

```
File · Playlists · View · Debug ·· [context slot]
```

```
Debug
  ├─ Open Log Viewer          win.open-log-viewer
  └─ Open Logs Folder…        win.open-logs-folder
```

`win.open-log-viewer` — opens `LogViewerWindow` (or presents existing).
`win.open-logs-folder` — calls `Gio.AppInfo.launch_default_for_uri` on
`~/code/tt-local-generator/logs/`.

The `Debug` menu is always visible regardless of source tab (logs are global).
It does NOT go in the context slot — it's a fixed menu.

`_build_menu_bar` adds `Debug` between `View` and the context slot.
`_context_slot_idx` increments by 1 since a new fixed menu was inserted before it.

---

## 4. CSS

Add to the CSS block in `main_window.py`:

```css
/* Log viewer window */
.log-section-header {
    color: @tt_text_muted;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 8px 12px 2px;
}
.log-row-error label { color: @tt_error; }
.log-row-ok label { color: @tt_success; }
.log-row-neutral label { color: @tt_text_muted; }
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
    padding: 4px 10px;
}
```

Color tags applied programmatically in `_load_file`:
- `error-line`: foreground `#FF6B6B`
- `muted-line`: foreground `#607D8B`
- `path-line`: foreground `#4FD1C5`, underline single

---

## 5. Files Changed

| File | Change |
|---|---|
| `app/log_viewer.py` | New file — `LogViewerWindow` |
| `app/main_window.py` | `_on_error` fix; status bar click; `_build_menu_bar` Debug menu; `win.open-log-viewer` + `win.open-logs-folder` actions; `_log_viewer_win` singleton; CSS additions |

---

## 6. Test Plan

**`tests/test_log_viewer.py`**
- `_detect_log_path(message)` extracts path from "Full log: ..." line, returns None when absent
- `_shorten_error(message)` returns ≤80 chars, no embedded log path
- `_parse_run_log_name("run_20260529_072047_20260529_142047_001b89f4.log")` → display name `"run_07:20"`, date `"May 29"`
- `_parse_server_log_name("media_2026-05-06_08-14-06_SkyReels-I2V-14B_p300x2_server.log")` → model `"SkyReels-I2V-14B"`, date `"May 6"`
- `_is_error_log(content: str)` returns True for content containing "rc=1" or "Traceback"
- `_is_error_log(content: str)` returns False for clean log content

---

## 7. Open Questions (deferred)

- Live tailing: should the log viewer auto-refresh when a log file grows while
  open? Initial answer: no for v1 — add a manual ↻ refresh button in the
  toolbar instead.
- Search/filter within log content? Deferred — Ctrl+F via system find-in-text
  may work since Gtk.TextView supports it via `Gtk.SearchBar`.
- Clicking a `.log` path in the content pane to open it: initial implementation
  marks the text with `path-line` tag but click-to-open is deferred (needs
  `Gtk.TextTag` + mouse event on the TextView, non-trivial).
