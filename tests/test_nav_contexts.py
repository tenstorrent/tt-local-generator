"""
Tests for RN-2 Task 3: registering the two resumable "live contexts" (a
pipeline session, a watch session) into the NavState tray shipped in Task 2,
and wiring the resume/dismiss stubs so a chip click actually resumes the
activity, and dismiss actually ends it (going to Library for a pipeline,
closing the kiosk window for watch).

Mirrors tests/test_main_window_loop_nav.py's harness: a minimal MainWindow
built via `__new__` with `Gtk.ApplicationWindow.__init__` patched out, then
hand-populated with only the collaborators the seams under test actually
touch.
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
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)

import main_window as mw
from nav_state import NavState, Context


def _mw():
    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)
    obj._nav_state = NavState()
    for name in ("_nav_open_context", "_nav_close_context",
                 "_on_context_resume", "_on_context_dismiss"):
        setattr(obj, name, getattr(mw.MainWindow, name).__get__(obj))
    obj._show_pipelines = MagicMock()
    obj._on_open_attractor = MagicMock()
    obj._attractor_win = None
    obj._gallery_stack = MagicMock()
    obj._gallery_stack.get_visible_child_name.return_value = "pipelines"
    obj._loop_nav = {"discover": MagicMock()}
    return obj


def test_resume_pipeline_shows_pipelines():
    obj = _mw()
    obj._on_context_resume("pipeline")
    obj._show_pipelines.assert_called_once()


def test_resume_watch_opens_attractor():
    obj = _mw()
    obj._on_context_resume("watch")
    obj._on_open_attractor.assert_called_once()


def test_dismiss_pipeline_leaves_to_library_and_closes_context():
    obj = _mw()
    obj._nav_state.open_context(Context("pipeline", "Pipeline", kind="pipeline"))
    obj._on_context_dismiss("pipeline")
    obj._loop_nav["discover"].set_active.assert_called_once_with(True)   # go to Library
    assert not obj._nav_state.has_context("pipeline")


def test_dismiss_watch_closes_the_window():
    obj = _mw()
    obj._nav_state.open_context(Context("watch", "Watch", kind="watch", running=True))
    win = MagicMock(); obj._attractor_win = win
    obj._on_context_dismiss("watch")
    win.close.assert_called_once()   # closing the window triggers _on_attractor_closed -> close_context


def test_nav_helpers_noop_without_nav_state():
    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)
    for n in ("_nav_open_context", "_nav_close_context"):
        setattr(obj, n, getattr(mw.MainWindow, n).__get__(obj))
    obj._nav_open_context(Context("x", "x"))   # no _nav_state -> no crash
    obj._nav_close_context("x")
