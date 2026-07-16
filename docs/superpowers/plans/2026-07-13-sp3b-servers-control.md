# SP-3b — Standalone Servers control + status bar + log — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Lift server management (Servers ▾ popover), the status bar, and the server-log out of ControlPanel into a standalone `ServersControl` owned by MainWindow and driven by `ModelStatusService`, so ControlPanel can be deleted in SP-3d without losing server management.

**Architecture:** New `app/servers_control.py` `ServersControl` (popover + status bar + log revealer), constructed with the status service + start/stop/restart callbacks; MainWindow mounts it and stops mounting ControlPanel's server bits; the popover reads the service (retiring `_refresh_servers_popover`'s poll).

**Tech Stack:** Python 3, GTK4/PyGObject, pytest (xvfb).

## Global Constraints

- Behavior parity: start/stop/restart, log streaming, status-bar segments identical to today.
- Popover + status-bar dots read `ModelStatusService` (3-state); start/stop call `note_starting`/`note_stopping`. Retire `_refresh_servers_popover`'s standalone poll.
- Exactly ONE visible Servers control + one status bar (no ControlPanel duplicate).
- GTK threading: service callbacks + log lines via `GLib.idle_add`. Palette tt-vscode-toolkit; `_CSS` ASCII-only; glyphs in Python strings.
- No ControlPanel deletion (SP-3d); no generate/queue/attractor migration (SP-3c). System python; version bump + changelog on landing; local only. Deselect known flakes: `tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`, `tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`.

---

### Task 1: `ServersControl` widget (extracted, service-driven)

**Files:** Create `app/servers_control.py`; Test `tests/test_servers_control.py`.

**Interfaces — Produces:**
```python
class ServersControl:                 # or Gtk.Box subclass
    def __init__(self, status_service, *, on_start, on_stop, on_restart): ...
    @property
    def servers_button(self) -> Gtk.MenuButton   # the "Servers ▾" top-bar button (+ popover)
    @property
    def status_bar(self) -> Gtk.Widget            # server dot + queue/disk/chip segments
    def append_server_log(self, line: str) -> None
    def set_server_launching(self, key: str, launching: bool) -> None
    def set_status_segments(self, *, queue=None, disk=None, chip=None) -> None
```
Behavior lifted from ControlPanel's `_build_servers_popover`/`_on_servers_action`/`_refresh_servers_popover` + `_server_status_box`/`set_server_state` + `_srv_log_*`/`append_server_log`, adapted so: (a) the popover rows + status-bar dot render 3-state glyphs (◌/◐/●) from `status_service.snapshot()`; (b) it `subscribe()`s to the service (callback → `GLib.idle_add(refresh)`), no standalone poll; (c) Start/Stop/Restart buttons call `on_start(key)`/`on_stop(key)`/`on_restart(key)`.

- [ ] **Step 1: failing tests** (fake `status_service` with `snapshot()`/`subscribe()`; real `model_status.Status`)
```python
import servers_control as sc, model_status as ms
def _svc(snap): 
    class F:
        def __init__(s): s._cb=None
        def snapshot(s): return dict(snap)
        def subscribe(s, cb): s._cb=cb; return lambda: None
    return F()
def test_constructs_standalone():
    calls=[]
    c = sc.ServersControl(_svc({}), on_start=lambda k:calls.append(("start",k)),
                          on_stop=lambda k:calls.append(("stop",k)), on_restart=lambda k:calls.append(("restart",k)))
    assert c.servers_button is not None and c.status_bar is not None
def test_popover_dots_from_snapshot():
    c = sc.ServersControl(_svc({"flux": ms.Status.READY, "wan2.2": ms.Status.STARTING}), on_start=lambda k:None, on_stop=lambda k:None, on_restart=lambda k:None)
    glyphs = c._server_row_glyphs()   # test helper: {key: glyph}
    assert glyphs["flux"] == "●" and glyphs["wan2.2"] == "◐"
def test_start_button_invokes_callback():
    calls=[]
    c = sc.ServersControl(_svc({"flux": ms.Status.OFF}), on_start=lambda k:calls.append(k), on_stop=lambda k:None, on_restart=lambda k:None)
    c._activate_start("flux"); assert calls == ["flux"]
def test_append_log_reveals():
    c = sc.ServersControl(_svc({}), on_start=lambda k:None, on_stop=lambda k:None, on_restart=lambda k:None)
    c.append_server_log("Application startup..."); assert c._log_revealed() is True
```
(Add the small test-helper accessors `_server_row_glyphs`/`_activate_start`/`_log_revealed`.)
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement `ServersControl` by adapting the ControlPanel code (grouping by `server_manager` capability, per-server Start/Stop/Restart, log revealer that collapses when the service reports the server READY). Dots from the service; subscribe not poll. ASCII CSS; glyphs in Python strings.
- [ ] **Step 4:** `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_servers_control.py -q` → PASS.
- [ ] **Step 5:** commit `feat(servers): standalone ServersControl (popover + status bar + log) on ModelStatusService`.

---

### Task 2: MainWindow mounts ServersControl; stop mounting ControlPanel's server bits

**Files:** Modify `app/main_window.py`; Test `tests/test_main_window_servers_control.py` (new, source/behavioral).

**Interfaces — Consumes:** `ServersControl`. **Produces:** MainWindow constructs `self._servers_control = ServersControl(self._status_service, on_start=..., on_stop=..., on_restart=...)`, mounts its `servers_button` in the top bar and its `status_bar`/log in the footer, routes server-log output to it, retires `_refresh_servers_popover`'s poll, and no longer mounts ControlPanel's `Servers ▾`/server-status/log widgets (exactly one visible Servers control + one status bar).

- [ ] **Step 1: failing tests**
```python
# _SRC assertions + behavioral where feasible
def test_mainwindow_constructs_servers_control():
    assert "self._servers_control = ServersControl(" in _SRC
    assert "from servers_control import ServersControl" in _SRC
def test_server_log_routes_to_servers_control():
    assert "self._servers_control.append_server_log" in _SRC
def test_refresh_servers_popover_poll_retired():
    # the standalone status_all poll in _refresh_servers_popover is removed / replaced by the service
    ...
def test_single_servers_control_mounted():
    # ControlPanel's servers button / server-status / log are no longer added to the window
    ...
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement — `from servers_control import ServersControl`; construct it after `self._status_service` (reuse `_on_start_server`/`_on_stop_server`/servers-action logic as the callbacks, so start/stop still resolve the right key + `note_starting`/`note_stopping`). Mount `servers_button` in the top bar (next to loop nav) and `status_bar`/log in the footer. Route the code that called `self._controls.append_server_log(...)`/`set_server_launching(...)` to `self._servers_control.*`. Stop adding ControlPanel's server widgets to the window (keep ControlPanel constructed for generate/queue/source-toggle until 3c/3d — just don't mount its server bits; if the toolbar/footer can't be split cleanly, hide the specific server widgets). Remove `_refresh_servers_popover`'s poll (the popover reads the service).
- [ ] **Step 4:** `pytest tests/test_main_window_servers_control.py -q` + full suite → PASS (one Servers control, one status bar).
- [ ] **Step 5:** commit `feat(servers): MainWindow mounts standalone ServersControl; ControlPanel server bits unmounted`.

---

### Task 3: Version, changelog, CLAUDE.md

**Files:** `VERSION`, `debian/changelog`, `CLAUDE.md`.

- [ ] **Step 1:** `VERSION` → `0.35.0`.
- [ ] **Step 2:** changelog 0.35.0: server management (start/stop/restart), the status bar, and the server-log now live in a standalone top-bar control driven by the single source of truth (3-state dots), lifted out of the legacy ControlPanel ahead of its retirement. Behavior unchanged.
- [ ] **Step 3:** CLAUDE.md "Retiring the vestiges" section: SP-3b done — `servers_control.py`/`ServersControl` owns servers popover + status bar + log on the service; `_refresh_servers_popover` poll retired (2 of 3 legacy pollers gone — `_health_loop` + artgen poller remain for 3d).
- [ ] **Step 4:** full suite green (deselect the two known flakes).
- [ ] **Step 5:** commit `chore: release v0.35.0 -- standalone Servers control (SP-3b)`.

---

## Notes for the executor
- Behavior parity: server start/stop/restart, log streaming, and status-bar segments must work as before.
- Exactly ONE Servers control + one status bar visible — no ControlPanel duplicate.
- Don't delete ControlPanel (SP-3d); only stop mounting its server widgets.
