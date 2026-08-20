"""
Tests for MainWindow._autostart_prompt_server — brings up the CPU prompt-gen
server (port 8001, powers ✨ Inspire) at launch if it isn't already running.
Runs off the main thread and soft-fails; skips the start when already healthy.
"""
from __future__ import annotations

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
    from gi.repository import Gtk
    Gtk.Entry()
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)


class _SyncThread:
    """Runs the target synchronously on start() so the test needs no real thread."""
    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        if self._target:
            self._target()


def _mw():
    import main_window as mw
    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)
    obj._autostart_prompt_server = mw.MainWindow._autostart_prompt_server.__get__(obj)
    obj._status_service = MagicMock()
    return obj


def test_starts_prompt_server_when_not_healthy():
    obj = _mw()
    fake_sm = MagicMock()
    fake_sm.is_healthy.return_value = False
    with patch("main_window._sm", fake_sm), \
         patch("main_window.threading.Thread", _SyncThread):
        obj._autostart_prompt_server()
    fake_sm.is_healthy.assert_called_once_with("prompt-server")
    fake_sm.start.assert_called_once_with("prompt-server", gui=True)
    obj._status_service.note_starting.assert_called_once_with("prompt-server")


def test_skips_start_when_already_healthy():
    obj = _mw()
    fake_sm = MagicMock()
    fake_sm.is_healthy.return_value = True
    with patch("main_window._sm", fake_sm), \
         patch("main_window.threading.Thread", _SyncThread):
        obj._autostart_prompt_server()
    fake_sm.start.assert_not_called()
    obj._status_service.note_starting.assert_not_called()


def test_soft_fails_when_start_raises():
    obj = _mw()
    fake_sm = MagicMock()
    fake_sm.is_healthy.return_value = False
    fake_sm.start.side_effect = RuntimeError("boom")
    with patch("main_window._sm", fake_sm), \
         patch("main_window.threading.Thread", _SyncThread):
        obj._autostart_prompt_server()  # must not raise
    fake_sm.start.assert_called_once()
