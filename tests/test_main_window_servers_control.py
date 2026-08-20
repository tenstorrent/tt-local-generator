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
    """SP-3d-4: ControlPanel's `toolbar_box` (the composite the servers button
    used to be appended onto, alongside its now-superseded medium-tab toggle)
    is no longer mounted at all -- the servers button folds directly into the
    loop-nav row instead, which becomes the window's only top bar."""
    assert "loop_nav_row.append(self._servers_control.servers_button)" in _SRC
    assert "main_toolbar.append(self._servers_control.servers_button)" not in _SRC


def test_log_widget_mounted_in_persistent_root_box():
    """Post-review fix (Issue 1): `log_widget` must be mounted directly on
    `root_box` -- a container that survives Discover mode's
    `_ctrl_wrapper.set_visible(False)` and ControlPanel's eventual SP-3d
    deletion -- not into `_ctrl_wrapper` (ControlPanel's own left-panel
    wrapper, which is hidden/deleted in exactly those two situations)."""
    assert "root_box.append(self._servers_control.log_widget)" in _SRC


def test_servers_control_not_mounted_into_ctrl_wrapper():
    """Issue 1 regression guard: the original (wrong) fix mounted the whole
    `self._servers_control` widget into `_ctrl_wrapper`. That line must never
    come back -- `_ctrl_wrapper` is hidden in Discover mode
    (`_on_loop_nav_discover`) and in the artgen tab (`_on_source_change`),
    and is deleted entirely alongside ControlPanel in SP-3d."""
    assert "self._ctrl_wrapper.append(self._servers_control)" not in _SRC


def test_status_bar_never_mounted_anywhere():
    """Issue 2 regression guard: ServersControl's own aggregate server dot
    (`.status_bar`) must never be mounted anywhere in MainWindow. The window
    already has one aggregate server dot (`_hw_statusbar`/`_StatusBar`, fed
    by the older per-tab health loop) -- mounting a second, differently-
    sourced dot is the exact "two disagreeing sources of truth" bug this
    whole program (ModelStatusService) exists to eliminate."""
    assert "self._servers_control.status_bar" not in _SRC


def test_only_one_aggregate_status_bar_constructed():
    """Exactly one `_StatusBar` instance is ever constructed in
    main_window.py -- the pre-existing `self._hw_statusbar` -- confirming
    ServersControl's status_bar isn't wrapped into a second one under a
    different name. (`_StatusBar(` alone would also match the class's own
    `class _StatusBar(Gtk.Box):` definition line, hence the more specific
    `= _StatusBar(` construction-call pattern.)"""
    assert _SRC.count("= _StatusBar(") == 1


def test_servers_control_closed_on_window_close():
    """`self` (the ServersControl Gtk.Box) is no longer guaranteed to ever
    be mounted -- only its servers_button/log_widget sub-widgets are -- so
    its own `unrealize`-triggered cleanup (see servers_control.py) may never
    fire. do_close_request must explicitly call close() so the
    status_service subscription is always torn down when the window closes."""
    start = _SRC.index("def do_close_request(self) -> bool:")
    body = _SRC[start:]
    assert "self._servers_control.close()" in body
    assert "self._status_service.stop()" in body  # still present, unrelated


def test_note_ordering_matches_legacy_on_servers_action():
    """Issue 3: note_starting/note_stopping must be textually AFTER the real
    _sm.start/stop/restart(key, ...) call inside each callback's body,
    mirroring ControlPanel._on_servers_action's ordering -- a synchronous
    _sm.* failure must never have already told the status service a
    launch/stop began."""
    for name, sm_call, note_call in (
        ("_on_servers_control_start", "_sm.start(key, gui=True)", "self._status_service.note_starting(key)"),
        ("_on_servers_control_stop", "_sm.stop(key)", "self._status_service.note_stopping(key)"),
        ("_on_servers_control_restart", "_sm.restart(key, gui=True)", "self._status_service.note_starting(key)"),
    ):
        start = _SRC.index(f"def {name}(self, key: str) -> None:")
        end = _SRC.index("\n    def ", start + 1)
        body = _SRC[start:end]
        assert body.index(sm_call) < body.index(note_call), name


def test_single_servers_control_mounted():
    """SP-3d-5: ControlPanel -- and the servers_btn/server_status_box/
    srv_log_revealer hide-calls that used to run right after ServersControl
    was constructed (SP-3b state) -- is deleted entirely. ServersControl is
    now the ONLY servers control the window builds; there is no ControlPanel
    duplicate to hide because there is no ControlPanel."""
    assert "self._controls = ControlPanel(" not in _SRC
    assert "class ControlPanel(Gtk.Box):" not in _SRC
    assert "self._controls._servers_btn.set_visible(False)" not in _SRC
    assert "self._controls._server_status_box.set_visible(False)" not in _SRC
    assert "self._controls._srv_log_revealer.set_visible(False)" not in _SRC


def test_refresh_servers_popover_poll_retired():
    """SP-3d-5: `ControlPanel._refresh_servers_popover` (and the whole class
    it lived on) is deleted outright -- superseding the SP-3b-era state where
    it was merely unreachable (hidden button, method still defined, per this
    test's earlier version). `ServersControl` (the thing the popover is
    replaced by) never polls at all -- it only ever renders from
    `status_service.snapshot()` at construction and `subscribe()`s for
    updates, so there's no replacement poll introduced either.
    """
    assert "def _refresh_servers_popover(self) -> None:" not in _SRC
    assert "_reconcile_artgen_statuses" not in _SRC
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


def test_note_starting_never_fires_when_sm_start_raises(monkeypatch):
    """Issue 3 behavioral counterpart: if the REAL _sm.start(...) call raises
    synchronously, note_starting must never have fired -- the status
    service must not be told a launch began that never actually happened.
    (Complements test_note_ordering_matches_legacy_on_servers_action, which
    only checks textual order; this proves the runtime effect.)"""
    fmw, calls = _make_main_window_double(monkeypatch)
    import main_window as mw

    def _boom(key, gui=True):
        raise RuntimeError("boom")

    monkeypatch.setattr(mw._sm, "start", _boom)
    fmw._on_servers_control_start("wan2.2")
    assert fmw._status_service.starting == []
    assert any("Error" in line for line in fmw._servers_control.log_lines)


# ── Behavioral: ServersControl's three widgets are independently mountable ──

def test_servers_control_widgets_unparented_at_construction():
    """Post-review fix (Issue 1/2): ServersControl no longer bundles its
    status-bar widget + log revealer inside `self` (the ServersControl
    Gtk.Box itself). Constructing a real ServersControl against a minimal
    fake status_service (cheap -- no MainWindow/ControlPanel needed) and
    checking each public widget has no parent proves all three
    (servers_button / status_bar / log_widget) are genuinely independent and
    freely placeable -- the structural property that makes the Issue 1 fix
    (mount log_widget alone, elsewhere) possible at all."""
    import servers_control as sc

    class _FakeService:
        def snapshot(self):
            return {}

        def subscribe(self, cb):
            return lambda: None

        def running_artgen_model(self):
            return None

    c = sc.ServersControl(
        _FakeService(), on_start=lambda k: None, on_stop=lambda k: None, on_restart=lambda k: None
    )
    assert c.servers_button.get_parent() is None
    assert c.status_bar.get_parent() is None
    assert c.log_widget.get_parent() is None
