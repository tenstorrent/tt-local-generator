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


def test_servers_menu_actions_go_through_server_control():
    """Task 8 (server control through tt) supersedes the SP-3b direct
    `_sm.start/stop/restart(key)` calls: every Servers-menu action now goes
    through ServerControl via `self._server_menu` (app/server_menu.py), which
    runs it on a worker, asks first where needed and logs the outcome. The
    note_starting/note_stopping ordering the old test pinned now lives in
    ServerMenu (noted on the worker, and cleared again on any outcome that
    did not start the server)."""
    for name, call in (
        ("_on_servers_control_start", "self._server_menu.start(key)"),
        ("_on_servers_control_stop", "self._server_menu.stop(server)"),
        ("_on_servers_control_restart", "self._server_menu.restart(server)"),
        ("_on_servers_control_use", "self._server_menu.use(server)"),
    ):
        start = _SRC.index(f"def {name}(self, ")
        end = _SRC.index("\n    def ", start + 1)
        body = _SRC[start:end]
        assert call in body, name
        assert "_sm.start(" not in body and "_sm.stop(" not in body
        assert "_sm.restart(" not in body, name


def test_server_menu_built_refreshed_and_closed():
    assert "self._server_menu = self._build_server_menu()" in _SRC
    assert "self._server_menu.start_refresh()" in _SRC
    close = _SRC[_SRC.index("def do_close_request(self) -> bool:"):]
    assert "self._server_menu.close()" in close
    assert "extra_ready_fn=self._servers_extra_ready_keys" in _SRC
    start = _SRC.index("self._servers_control = ServersControl(")
    ctor = _SRC[start:_SRC.index(")", start)]
    assert "on_use=self._on_servers_control_use" in ctor
    assert "on_connect=self._open_connect_dialog" in ctor


class _FakeMenu:
    def __init__(self):
        self.calls = []

    def start(self, key):
        self.calls.append(("start", key))

    def stop(self, s):
        self.calls.append(("stop", s))

    def restart(self, s):
        self.calls.append(("restart", s))

    def use(self, s):
        self.calls.append(("use", s))

    def ready_keys(self):
        return {"wan2.2"}


def test_servers_control_callbacks_delegate_to_the_server_menu():
    import main_window as mw

    class _FakeMainWindow:
        pass

    fmw = _FakeMainWindow()
    fmw._server_menu = _FakeMenu()
    for name in ("_on_servers_control_start", "_on_servers_control_stop",
                 "_on_servers_control_restart", "_on_servers_control_use",
                 "_servers_extra_ready_keys"):
        setattr(fmw, name, getattr(mw.MainWindow, name).__get__(fmw))
    fmw._on_servers_control_start("wan2.2")
    fmw._on_servers_control_stop("flux")
    fmw._on_servers_control_restart("mochi")
    fmw._on_servers_control_use("srv")
    assert fmw._server_menu.calls == [("start", "wan2.2"), ("stop", "flux"),
                                      ("restart", "mochi"), ("use", "srv")]
    assert fmw._servers_extra_ready_keys() == {"wan2.2"}
    # Before the menu exists (the status service polls first) it is empty.
    bare = _FakeMainWindow()
    bare._servers_extra_ready_keys = mw.MainWindow._servers_extra_ready_keys.__get__(bare)
    assert bare._servers_extra_ready_keys() == set()


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
