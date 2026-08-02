import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()
except Exception:
    pytest.skip("no GTK display available", allow_module_level=True)


def _mw(selected_key, status_map):
    import main_window as mw
    from model_status import Status
    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)
    obj._ensure_server_ready_then = mw.MainWindow._ensure_server_ready_then.__get__(obj)
    obj._create_view = SimpleNamespace(_selected_model_key=lambda: selected_key)
    def _status(k):
        return Status(status_map.get(k, "off"))
    obj._status_service = SimpleNamespace(status=_status)
    obj._launch_create_job = MagicMock()
    obj._confirm_start_server = MagicMock()
    return obj


def test_ready_server_launches_immediately():
    obj = _mw("flux", {"flux": "ready"})
    obj._ensure_server_ready_then(SimpleNamespace(label="Image"), {"p": 1})
    obj._launch_create_job.assert_called_once()
    obj._confirm_start_server.assert_not_called()


def test_synthetic_key_launches_immediately():
    obj = _mw("animatediff", {"wan2.2": "ready"})
    obj._ensure_server_ready_then(SimpleNamespace(label="AnimateDiff"), {})
    obj._launch_create_job.assert_called_once()
    obj._confirm_start_server.assert_not_called()


def test_offline_server_shows_confirm_dialog_and_defers():
    obj = _mw("flux", {"wan2.2": "ready"})   # flux off, wan2.2 running (conflict)
    obj._ensure_server_ready_then(SimpleNamespace(label="Image"), {})
    obj._launch_create_job.assert_not_called()
    obj._confirm_start_server.assert_called_once()
    plan = obj._confirm_start_server.call_args.args[0]
    assert plan.target == "flux" and plan.conflict == "wan2.2" and plan.needs_reset is True


def test_ensure_server_ready_then_no_status_service_launches_directly():
    """Task 2 minor: `_ensure_server_ready_then` with no status service (e.g.
    a standalone/test window that never got one wired up) must call
    `_launch_create_job` directly -- no gate, no dialog, since there's no
    service to consult for readiness."""
    import main_window as mw
    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)
    obj._ensure_server_ready_then = mw.MainWindow._ensure_server_ready_then.__get__(obj)
    obj._status_service = None
    obj._launch_create_job = MagicMock()
    obj._confirm_start_server = MagicMock()

    medium = SimpleNamespace(label="Image")
    params = {"prompt": "x"}
    obj._ensure_server_ready_then(medium, params)

    obj._launch_create_job.assert_called_once_with(medium, params)
    obj._confirm_start_server.assert_not_called()


# --- Regression coverage for the CRITICAL review finding -------------------
#
# `_confirm_start_server` and `_perform_switch_then` referenced the bare name
# `server_manager` (e.g. `server_manager.SERVERS[...]`, `server_manager.stop`,
# `.start`, `.is_healthy`) even though this module only ever imports the
# aliased `import server_manager as _sm` -- `server_manager` was UNDEFINED,
# so the very first click on an offline/conflicting server raised a
# `NameError` before the dialog ever rendered. The prior gate tests above
# mock `_confirm_start_server`/`_perform_switch_then` as MagicMocks, so their
# bodies never actually executed and never caught this. The tests below call
# the REAL (unmocked) method bodies so the `_sm.` lines actually run.

def _confirm_harness():
    import main_window as mw
    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)
    obj._confirm_start_server = mw.MainWindow._confirm_start_server.__get__(obj)
    return obj


def test_confirm_start_server_builds_dialog_and_routes_accept():
    """Exercises the `_sm.SERVERS[plan.target].label` /
    `_sm.SERVERS[plan.conflict].label` lines directly -- these are exactly
    the lines that raised NameError under the bare `server_manager` bug."""
    import ready_to_run

    obj = _confirm_harness()
    obj._perform_switch_then = MagicMock()

    plan = ready_to_run.SwitchPlan(target="flux", conflict="wan2.2", needs_reset=True)
    medium = SimpleNamespace(label="Image")
    proceed = MagicMock()

    fake_dialog = MagicMock()
    fake_dialog.add_button.return_value = MagicMock()
    captured = {}
    fake_dialog.connect.side_effect = lambda signal, cb: captured.__setitem__(signal, cb)

    with patch("main_window.Gtk.MessageDialog", return_value=fake_dialog) as MD:
        obj._confirm_start_server(plan, medium, proceed)   # must not raise NameError

    MD.assert_called_once()
    fake_dialog.present.assert_called_once()
    assert "response" in captured

    # ACCEPT routes to _perform_switch_then with the same plan + proceed.
    captured["response"](fake_dialog, Gtk.ResponseType.ACCEPT)
    obj._perform_switch_then.assert_called_once_with(plan, proceed)


def test_confirm_start_server_cancel_does_not_proceed():
    import ready_to_run

    obj = _confirm_harness()
    obj._perform_switch_then = MagicMock()

    plan = ready_to_run.SwitchPlan(target="flux", conflict="wan2.2", needs_reset=True)
    medium = SimpleNamespace(label="Image")
    proceed = MagicMock()

    fake_dialog = MagicMock()
    fake_dialog.add_button.return_value = MagicMock()
    captured = {}
    fake_dialog.connect.side_effect = lambda signal, cb: captured.__setitem__(signal, cb)

    with patch("main_window.Gtk.MessageDialog", return_value=fake_dialog):
        obj._confirm_start_server(plan, medium, proceed)

    captured["response"](fake_dialog, Gtk.ResponseType.CANCEL)
    obj._perform_switch_then.assert_not_called()


def _switch_harness():
    import main_window as mw
    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)
    obj._perform_switch_then = mw.MainWindow._perform_switch_then.__get__(obj)
    obj._servers_control = SimpleNamespace(
        set_server_launching=MagicMock(),
        append_server_log=MagicMock(),
    )
    obj._status_service = SimpleNamespace(
        note_stopping=MagicMock(),
        note_starting=MagicMock(),
    )
    return obj


def _run_perform_switch_then(obj, plan, proceed, calls):
    """Runs `_perform_switch_then`, capturing the background-thread `target`
    (via a patched `threading.Thread` that never actually spawns) and then
    invoking it synchronously so its body executes in-test. `GLib.idle_add`
    is patched to call its callback immediately (main-thread semantics are
    irrelevant here -- we only care that the real bodies run in order)."""
    import ready_to_run  # noqa: F401  (imported for type clarity only)

    captured_thread = {}

    def fake_thread(target=None, daemon=None):
        captured_thread["target"] = target
        return MagicMock()

    def fake_idle_add(fn, *args):
        fn(*args)
        return False

    def _stop(key):
        calls.append(("stop", key))

    def _start(key, **kwargs):
        calls.append(("start", key))

    def _healthy(key):
        calls.append(("is_healthy", key))
        return True

    def _reset():
        calls.append(("reset",))

    with patch("main_window.threading.Thread", side_effect=fake_thread), \
         patch("main_window.GLib.idle_add", side_effect=fake_idle_add), \
         patch("main_window._sm.stop", side_effect=_stop), \
         patch("main_window._sm.start", side_effect=_start), \
         patch("main_window._sm.is_healthy", side_effect=_healthy), \
         patch("pipeline_engine._tt_smi_reset", side_effect=_reset):
        obj._perform_switch_then(plan, proceed)   # must not raise NameError
        assert captured_thread["target"] is not None
        captured_thread["target"]()   # run the "background" body in-thread


def test_perform_switch_then_stops_resets_starts_in_order_when_conflict():
    import ready_to_run

    obj = _switch_harness()
    proceed = MagicMock()
    plan = ready_to_run.SwitchPlan(target="flux", conflict="wan2.2", needs_reset=True)

    calls = []
    _run_perform_switch_then(obj, plan, proceed, calls)

    assert calls == [
        ("stop", "wan2.2"),
        ("reset",),
        ("start", "flux"),
        ("is_healthy", "flux"),
    ]
    proceed.assert_called_once()


def test_perform_switch_then_no_conflict_skips_stop_and_reset():
    import ready_to_run

    obj = _switch_harness()
    proceed = MagicMock()
    plan = ready_to_run.SwitchPlan(target="flux", conflict=None, needs_reset=False)

    calls = []
    _run_perform_switch_then(obj, plan, proceed, calls)

    assert calls == [
        ("start", "flux"),
        ("is_healthy", "flux"),
    ]
    proceed.assert_called_once()
