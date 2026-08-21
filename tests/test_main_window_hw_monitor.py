"""Monitor HW toggle wiring (v0.93.x re-home): one `win.toggle-hw-monitor`
stateful action drives the View-menu item + the header ToggleButton + the viz's
✕ dismiss, stays in sync, persists to `hw_monitor_default_on`, and calls
CreateView.set_hw_monitor. Built on a minimal MainWindow harness (no full UI),
mirroring tests/test_main_window_loop_nav.py's __new__ pattern.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gtk, GLib, Gio
    Gtk.ToggleButton()  # probe for a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)


def _make_mw(tmp_path, monkeypatch):
    import app_settings
    monkeypatch.setattr(app_settings, "SETTINGS_FILE", tmp_path / "settings.json")
    fresh = app_settings.AppSettings()

    import main_window as mw
    monkeypatch.setattr(mw, "_settings", fresh)

    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)

    obj._syncing_hw_monitor = False
    obj._create_view = MagicMock()
    obj._hw_monitor_btn = Gtk.ToggleButton()

    for name in ("_on_toggle_hw_monitor", "_sync_hw_monitor_button",
                 "_on_hw_monitor_button_toggled", "_on_hw_monitor_closed_from_viz",
                 "_apply_hw_monitor_startup"):
        setattr(obj, name, getattr(mw.MainWindow, name).__get__(obj))

    action = Gio.SimpleAction.new_stateful("toggle-hw-monitor", None, GLib.Variant("b", False))
    action.connect("activate", obj._on_toggle_hw_monitor)
    obj.lookup_action = lambda n: action if n == "toggle-hw-monitor" else None
    obj._hw_monitor_btn.connect("toggled", obj._on_hw_monitor_button_toggled)
    return obj, action, fresh


def test_menu_action_drives_create_view_and_persists(tmp_path, monkeypatch):
    obj, action, settings = _make_mw(tmp_path, monkeypatch)

    action.activate(None)  # menu item click
    assert action.get_state().get_boolean() is True
    obj._create_view.set_hw_monitor.assert_called_with(True)
    assert settings.get("hw_monitor_default_on") is True
    assert obj._hw_monitor_btn.get_active() is True   # header button synced

    action.activate(None)  # toggle back off
    assert action.get_state().get_boolean() is False
    obj._create_view.set_hw_monitor.assert_called_with(False)
    assert settings.get("hw_monitor_default_on") is False
    assert obj._hw_monitor_btn.get_active() is False


def test_header_button_drives_action(tmp_path, monkeypatch):
    obj, action, settings = _make_mw(tmp_path, monkeypatch)

    obj._hw_monitor_btn.set_active(True)   # user clicks the header toggle
    assert action.get_state().get_boolean() is True
    obj._create_view.set_hw_monitor.assert_called_with(True)
    assert settings.get("hw_monitor_default_on") is True


def test_viz_close_flips_action_off(tmp_path, monkeypatch):
    obj, action, settings = _make_mw(tmp_path, monkeypatch)
    action.activate(None)                  # on
    assert action.get_state().get_boolean() is True

    obj._on_hw_monitor_closed_from_viz()   # the viz's ✕ dismiss
    assert action.get_state().get_boolean() is False
    assert obj._hw_monitor_btn.get_active() is False
    obj._create_view.set_hw_monitor.assert_called_with(False)


def test_apply_startup_restores_persisted_on(tmp_path, monkeypatch):
    obj, action, settings = _make_mw(tmp_path, monkeypatch)
    settings.set("hw_monitor_default_on", True)

    obj._apply_hw_monitor_startup()
    assert obj._hw_monitor_btn.get_active() is True
    obj._create_view.set_hw_monitor.assert_called_with(True)


def test_apply_startup_default_off_is_quiet(tmp_path, monkeypatch):
    obj, action, settings = _make_mw(tmp_path, monkeypatch)
    obj._apply_hw_monitor_startup()  # default False
    assert obj._hw_monitor_btn.get_active() is False
    obj._create_view.set_hw_monitor.assert_not_called()  # viz never built when off
