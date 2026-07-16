# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Guard tests for SP-3b Task 2 (.superpowers/sdd/task-2-brief.md, this SDD
cycle): MainWindow constructs and mounts the standalone `ServersControl`
(app/servers_control.py) and stops mounting ControlPanel's own "Servers ▾"
popover / server-status box / server-log revealer.

Constructing the full `MainWindow` -- and, per the convention established by
tests/test_main_window_status_service.py, even a bare `ControlPanel` -- is
heavy and network/disk dependent (no test file in this suite constructs
`ControlPanel` directly). These tests follow that file's established
two-pronged pattern:

  - Source-level assertions on app/main_window.py's text for the parts that
    are pure wiring (construction, mounting order, hiding ControlPanel's
    now-redundant server widgets).
  - A behavioral test that binds the REAL (unbound) callback methods
    (`_on_servers_control_start/_stop/_restart`) onto a minimal stand-in via
    `.__get__` -- exactly like that file's `_make_control_panel_double`
    helper does for `ControlPanel._on_servers_action` -- so the key
    resolution + `note_starting`/`note_stopping` + `ServersControl` routing
    is proven to actually execute, not just that the right substrings appear
    somewhere in the file.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

_SRC = (Path(__file__).parent.parent / "app" / "main_window.py").read_text()
_SC_SRC = (Path(__file__).parent.parent / "app" / "servers_control.py").read_text()


# ── Source-level wiring guards ──────────────────────────────────────────────

def test_mainwindow_constructs_servers_control():
    assert "from servers_control import ServersControl" in _SRC
    assert "self._servers_control = ServersControl(" in _SRC


def test_servers_control_constructed_after_status_service():
    """Construction order matters: the service must exist first so it can be
    injected, same reasoning as CreateView's construction-order guard in
    tests/test_main_window_status_service.py."""
    status_idx = _SRC.index("self._status_service = ModelStatusService(")
    construct_idx = _SRC.index("self._servers_control = ServersControl(")
    assert status_idx < construct_idx


def test_servers_control_callbacks_wired():
    start = _SRC.index("self._servers_control = ServersControl(")
    end = _SRC.index(")", start)
    ctor = _SRC[start:end]
    assert "on_start=self._on_servers_control_start" in ctor
    assert "on_stop=self._on_servers_control_stop" in ctor
    assert "on_restart=self._on_servers_control_restart" in ctor


def test_server_log_routes_to_servers_control():
    assert "self._servers_control.append_server_log" in _SRC
    assert "self._servers_control.set_server_launching" in _SRC
    # The three MainWindow methods that used to talk to ControlPanel's log
    # panel (_on_start_server, _start_log_tail, _on_stop_server) no longer
    # call ControlPanel's copy at all.
    assert "self._controls.append_server_log" not in _SRC
    assert "self._controls.set_server_launching" not in _SRC


def test_servers_button_mounted_in_top_bar():
    assert "main_toolbar.append(self._servers_control.servers_button)" in _SRC


def test_servers_control_mounted_in_footer():
    # ServersControl bundles its own status-bar widget + log revealer into
    # one Box in its own __init__ (servers_control.py), so mounting the
    # whole widget is the only way to place both without re-parenting
    # either child out from under the other.
    assert "self._ctrl_wrapper.append(self._servers_control)" in _SRC


def test_single_servers_control_mounted():
    """ControlPanel's own Servers ▾ button / server-status box / server-log
    revealer are explicitly hidden right after ServersControl is
    constructed, so exactly one Servers control + one status bar are ever
    visible -- no ControlPanel duplicate."""
    construct_idx = _SRC.index("self._servers_control = ServersControl(")
    tail = _SRC[construct_idx:construct_idx + 1500]
    assert "self._controls._servers_btn.set_visible(False)" in tail
    assert "self._controls._server_status_box.set_visible(False)" in tail
    assert "self._controls._srv_log_revealer.set_visible(False)" in tail
    # ControlPanel itself is still constructed and its toolbar/footer are
    # still mounted -- generate/queue/source-toggle aren't migrated until
    # SP-3c/3d, only the server-specific pieces inside them are hidden.
    assert "self._controls = ControlPanel(" in _SRC
    assert "main_toolbar = self._controls.toolbar_box" in _SRC
    assert "self._ctrl_wrapper.append(self._controls.footer_box)" in _SRC


def test_refresh_servers_popover_poll_retired():
    """`ControlPanel._refresh_servers_popover`'s standalone `_sm.status_all()`
    poll is only ever kicked off by the "Servers ▾" popover's "show" signal
    (`_on_servers_popover_show`), itself only reachable via a user click on
    `_servers_btn`. MainWindow now hides that button unconditionally right
    after constructing ServersControl (see test_single_servers_control_mounted),
    so the popover can never open and the poll can never run in the live
    app -- retiring it in practice without deleting the still-intact
    ControlPanel method (kept per the brief's "don't delete ControlPanel"
    rule -- it's needed again until SP-3d).

    `ServersControl` itself (the thing the popover is replaced by) never
    polls at all -- it only ever renders from `status_service.snapshot()`
    at construction and `subscribe()`s for updates, so there's no
    replacement poll introduced either.
    """
    assert "self._controls._servers_btn.set_visible(False)" in _SRC
    # ControlPanel's method is untouched -- still defined, not deleted.
    assert "def _refresh_servers_popover(self) -> None:" in _SRC
    assert "_sm.status_all(timeout=2.0)" in _SRC
    # ServersControl has no polling of its own.
    assert "status_all" not in _SC_SRC
    assert "subscribe(" in _SC_SRC


# ── Behavioral: the callbacks resolve keys + note the service + route logs ──
#
# Mirrors test_main_window_status_service.py's `_make_control_panel_double`
# pattern: bind the REAL unbound methods onto a bare stand-in via `.__get__`
# rather than constructing the full (heavy) MainWindow/ControlPanel.

class _ImmediateThread:
    """threading.Thread stand-in that runs its target synchronously on
    start(), so these tests are deterministic instead of racing a real
    daemon thread (same helper as test_main_window_status_service.py's)."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


class _Recorder:
    """Fake ModelStatusService: records note_starting/note_stopping calls."""

    def __init__(self):
        self.starting: list = []
        self.stopping: list = []

    def note_starting(self, key):
        self.starting.append(key)

    def note_stopping(self, key):
        self.stopping.append(key)


class _FakeServersControl:
    """Records append_server_log/set_server_launching calls; no real GTK."""

    def __init__(self):
        self.log_lines: list = []
        self.launching: list = []

    def append_server_log(self, line):
        self.log_lines.append(line)

    def set_server_launching(self, key, launching):
        self.launching.append((key, launching))


def _make_main_window_double(monkeypatch):
    """Bind the real (unbound) _on_servers_control_* methods from
    MainWindow onto a bare stand-in, with _sm.start/stop/restart and the
    threading/idle_add plumbing swapped for synchronous fakes."""
    import main_window as mw

    monkeypatch.setattr(mw.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(mw.GLib, "idle_add", lambda fn, *a: fn(*a))
    calls = {"start": [], "stop": [], "restart": []}
    monkeypatch.setattr(mw._sm, "start", lambda key, gui=True: calls["start"].append((key, gui)))
    monkeypatch.setattr(mw._sm, "stop", lambda key: calls["stop"].append(key))
    monkeypatch.setattr(mw._sm, "restart", lambda key, gui=True: calls["restart"].append((key, gui)))

    class _FakeMainWindow:
        pass

    fmw = _FakeMainWindow()
    fmw._status_service = _Recorder()
    fmw._servers_control = _FakeServersControl()
    fmw._on_servers_control_start = mw.MainWindow._on_servers_control_start.__get__(fmw)
    fmw._on_servers_control_stop = mw.MainWindow._on_servers_control_stop.__get__(fmw)
    fmw._on_servers_control_restart = mw.MainWindow._on_servers_control_restart.__get__(fmw)
    return fmw, calls


def test_on_servers_control_start_notes_and_starts(monkeypatch):
    fmw, calls = _make_main_window_double(monkeypatch)
    fmw._on_servers_control_start("wan2.2")
    assert fmw._status_service.starting == ["wan2.2"]
    assert fmw._status_service.stopping == []
    assert calls["start"] == [("wan2.2", True)]
    # launching set True immediately, then False once the (synchronous, in
    # this test) worker settles.
    assert fmw._servers_control.launching == [("wan2.2", True), ("wan2.2", False)]
    assert any("wan2.2" in line for line in fmw._servers_control.log_lines)


def test_on_servers_control_stop_notes_and_stops(monkeypatch):
    fmw, calls = _make_main_window_double(monkeypatch)
    fmw._on_servers_control_stop("flux")
    assert fmw._status_service.stopping == ["flux"]
    assert fmw._status_service.starting == []
    assert calls["stop"] == ["flux"]
    assert fmw._servers_control.launching == [("flux", True), ("flux", False)]


def test_on_servers_control_restart_notes_starting_and_restarts(monkeypatch):
    fmw, calls = _make_main_window_double(monkeypatch)
    fmw._on_servers_control_restart("mochi")
    # Restart -> note_starting (matches ControlPanel._on_servers_action's
    # restart branch, which also calls note_starting rather than a separate
    # note_restarting hook).
    assert fmw._status_service.starting == ["mochi"]
    assert calls["restart"] == [("mochi", True)]
    assert fmw._servers_control.launching == [("mochi", True), ("mochi", False)]


def test_servers_control_callback_note_failure_does_not_block_start(monkeypatch):
    """A status_service.note_starting exception must never prevent the real
    _sm.start(key, gui=True) call, mirroring _on_servers_action's own
    try/except-around-note_ guard."""
    fmw, calls = _make_main_window_double(monkeypatch)

    class _BoomRecorder:
        def note_starting(self, key):
            raise RuntimeError("boom")

        def note_stopping(self, key):
            raise RuntimeError("boom")

    fmw._status_service = _BoomRecorder()
    fmw._on_servers_control_start("wan2.2")
    assert calls["start"] == [("wan2.2", True)]
