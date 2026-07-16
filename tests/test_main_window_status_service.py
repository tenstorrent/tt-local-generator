# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Guard tests for MainWindow owning the single ModelStatusService instance
(SP-2 Task 1: .superpowers/sdd/task-1-brief.md).

Constructing the full `MainWindow` is heavy and network/disk dependent (see
tests/test_main_window_pipelines.py's docstring) — the same reasoning that
made tests/test_main_window_create_view_mount.py a source-level guard rather
than a live construction test. These tests follow that established pattern:
assert the exact lines exist in app/main_window.py's source rather than
instantiating MainWindow.

The one non-source-level assertion (CreateView actually accepting and
storing `status_service=`) lives in tests/test_create_view.py, since
CreateView (unlike MainWindow) is cheap to construct under xvfb.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

_SRC = (Path(__file__).parent.parent / "app" / "main_window.py").read_text()


def test_constructs_and_starts_status_service():
    assert "from model_status import ModelStatusService" in _SRC
    assert "self._status_service = ModelStatusService(" in _SRC
    assert "self._status_service.start()" in _SRC


def test_status_service_constructed_before_create_view():
    """The service must exist before `CreateView(...)` is constructed so it
    can be injected — construction order matters here, not just presence."""
    construct_idx = _SRC.index("self._status_service = ModelStatusService(")
    create_view_idx = _SRC.index("self._create_view = CreateView(")
    assert construct_idx < create_view_idx


def test_stops_service_on_close():
    assert "self._status_service.stop()" in _SRC
    # Guard it lands inside do_close_request, alongside the other pollers'
    # stop signals (self._health_stop.set()), not somewhere unrelated.
    start = _SRC.index("def do_close_request(self) -> bool:")
    body = _SRC[start:]  # do_close_request is the last method in the class
    assert "self._health_stop.set()" in body
    assert "self._status_service.stop()" in body


def test_create_view_gets_status_service():
    assert "status_service=self._status_service" in _SRC


def test_start_stop_hooks_present():
    assert "self._status_service.note_starting(" in _SRC
    assert "self._status_service.note_stopping(" in _SRC


def test_servers_popover_start_stop_hooked():
    """The Servers-popover start/stop/restart actions (_on_servers_action)
    each note their key with the status service, guarded so a note failure
    can never break the actual _sm.start/_sm.stop/_sm.restart call."""
    start = _SRC.index("def _on_servers_action(self, key: str, action: str) -> None:")
    end = _SRC.index("\n    def ", start + 1)
    body = _SRC[start:end]
    assert "_sm.start(key, gui=True)" in body
    assert "_sm.stop(key)" in body
    assert "self._status_service.note_starting(key)" in body
    assert "self._status_service.note_stopping(key)" in body


def test_on_start_stop_server_resolve_and_note_keys():
    """`_on_start_server`/`_on_stop_server` (the Video/Image tab start/stop
    buttons) resolve a server_manager key from the script they're about to
    run/have run, via the shared `_server_key_for_script` helper, and note
    it with the status service -- guarded so a bad resolution can't break
    the real start/stop flow."""
    assert "def _server_key_for_script(script_name: str)" in _SRC

    start = _SRC.index("def _on_start_server(self, model_source: str) -> None:")
    end = _SRC.index("\n    def ", start + 1)
    body = _SRC[start:end]
    assert "_server_key_for_script(script_name)" in body
    assert "self._status_service.note_starting(server_key)" in body

    start2 = _SRC.index("def _on_stop_server(self) -> None:")
    end2 = _SRC.index("\n    def ", start2 + 1)
    body2 = _SRC[start2:end2]
    assert "_server_key_for_script(" in body2
    assert "self._status_service.note_stopping(server_key)" in body2


# ── Behavioral: the SP-2 final-review CRITICAL ──────────────────────────────
#
# `_on_servers_action` (asserted above only at the source-string level) is a
# `ControlPanel` method, NOT a `MainWindow` method -- `ControlPanel` spans
# roughly lines 3952-7809, `MainWindow` starts after it. `ControlPanel.__init__`
# never sets `self._status_service`, so unless MainWindow injects it onto the
# already-built `self._controls` instance (the same post-construction idiom
# already used for `self._controls._store = self._store`), every popover
# Start/Stop/Restart raises `AttributeError: 'ControlPanel' object has no
# attribute '_status_service'` inside `_on_servers_action`'s own
# `except Exception: pass` -- silently swallowed, so `note_starting`/
# `note_stopping` never actually fire. The source-string tests above cannot
# catch this: they only check that the call text appears somewhere in the
# method body, never that `self._status_service` resolves to anything at
# runtime, let alone the SAME instance MainWindow constructed and started.
#
# These tests mirror the method-binding harness in
# tests/test_main_window_loop_nav.py (bind the real unbound method via
# `.__get__` onto a minimal stand-in rather than constructing the full
# `ControlPanel`/`MainWindow`, which is heavy -- see that file's docstring).


class _ImmediateThread:
    """threading.Thread stand-in that runs its target synchronously on start().

    Mirrors tests/test_create_view.py's `_ImmediateThread` -- `_on_servers_action`
    does its real work (`_sm.start`/`stop`/`restart` + the note_ calls) inside a
    background `threading.Thread`; running it inline keeps these tests
    deterministic instead of racing a real daemon thread.
    """

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


class _Recorder:
    """Fake ModelStatusService: records note_starting/note_stopping calls
    instead of touching any real poll thread or server_manager state."""

    def __init__(self):
        self.starting: list = []
        self.stopping: list = []

    def note_starting(self, key):
        self.starting.append(key)

    def note_stopping(self, key):
        self.stopping.append(key)


def _make_control_panel_double(monkeypatch):
    """Build a bare object shaped like the slice of `ControlPanel` that
    `_on_servers_action` (and the two GLib-idle helpers it calls) actually
    touch, with the real (unbound) methods bound onto it via `.__get__` --
    same pattern as test_main_window_loop_nav.py's `_make_mw`. Deliberately
    does NOT set `_status_service` -- that's the one attribute this whole
    bug is about, so each test wires it (or doesn't) explicitly.
    """
    import main_window as mw

    monkeypatch.setattr(mw.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(mw.GLib, "idle_add", lambda fn, *a: fn(*a))
    monkeypatch.setattr(mw._sm, "start", lambda key, gui=True: None)
    monkeypatch.setattr(mw._sm, "stop", lambda key: None)
    monkeypatch.setattr(mw._sm, "restart", lambda key, gui=True: None)
    monkeypatch.setattr(mw._sm, "is_healthy", lambda key, timeout=2.0: True)
    monkeypatch.setattr(mw._sm, "status_all", lambda timeout=2.0: {})

    class _FakeControls:
        pass

    fc = _FakeControls()
    fc._servers_popover_dots = {}
    fc._servers_popover_start_btns = {}
    fc._servers_popover_stop_btns = {}
    fc._servers_popover_restart_btns = {}
    fc._set_server_row_busy = mw.ControlPanel._set_server_row_busy.__get__(fc)
    fc._apply_servers_status = mw.ControlPanel._apply_servers_status.__get__(fc)
    fc._on_servers_action = mw.ControlPanel._on_servers_action.__get__(fc)
    return fc


def test_on_servers_action_notes_start_and_stop_when_status_service_present(monkeypatch):
    """With `_status_service` present (the post-fix, correctly-injected
    state), invoking the REAL `_on_servers_action` for "start" and "stop"
    must actually call note_starting/note_stopping -- exercised at runtime,
    not just grepped from source."""
    fc = _make_control_panel_double(monkeypatch)
    rec = _Recorder()
    fc._status_service = rec

    fc._on_servers_action("wan2.2", "start")
    assert rec.starting == ["wan2.2"]
    assert rec.stopping == []

    fc._on_servers_action("wan2.2", "stop")
    assert rec.stopping == ["wan2.2"]


def test_mainwindow_injects_status_service_into_controls(monkeypatch):
    """THE regression guard for the SP-2 final-review CRITICAL.

    Rather than hand-writing `fake_controls._status_service = ...` in the
    test (which would pass regardless of whether MainWindow's __init__
    actually performs the injection), this lifts the REAL injection
    statement out of app/main_window.py's source -- the two lines starting
    at `self._controls._store = self._store` -- and executes it against a
    bare stand-in. Pre-fix (commit 72043d7), that source block wires only
    `_store`; `_controls` never gets `_status_service`, `_on_servers_action`
    AttributeErrors internally (swallowed by its own try/except), and
    `rec.starting` stays empty -- this test fails. Post-fix, the block also
    contains `self._controls._status_service = self._status_service`, so the
    exact same instance MainWindow constructed reaches `_controls`, and the
    popover note calls actually land.
    """
    marker = "self._controls._store = self._store"
    marker_idx = _SRC.index(marker)
    line_start = _SRC.rfind("\n", 0, marker_idx) + 1  # start of that line (keep its indent)
    end = _SRC.index("\n\n", marker_idx)  # up through the blank line ending the block
    injection_src = textwrap.dedent(_SRC[line_start:end])

    fc = _make_control_panel_double(monkeypatch)
    rec = _Recorder()

    class _FakeMainWindow:
        pass

    fake_mw = _FakeMainWindow()
    fake_mw._store = object()
    fake_mw._status_service = rec
    fake_mw._controls = fc

    # Execute the ACTUAL source lines from MainWindow.__init__ against the
    # stand-in -- `self` inside that snippet resolves to `fake_mw`.
    exec(compile(injection_src, "<main_window.py injection block>", "exec"), {}, {"self": fake_mw})

    fc._on_servers_action("wan2.2", "start")
    assert rec.starting == ["wan2.2"], (
        "self._controls._status_service was never injected by "
        "MainWindow.__init__ -- _on_servers_action's note_starting call "
        "AttributeErrors and is silently swallowed"
    )
