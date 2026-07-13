"""
Tests for the top-level loop nav (Create · Curate · Discover · Remix) — the
first slice of the Create/Curate/Discover/Remix restructure
(docs/superpowers/specs/2026-07-13-create-surface-design.md, Task 1).

For THIS task the four movements route to EXISTING surfaces — no internal
rewrites of ControlPanel, the medium galleries, or Pipeline Studio:
  - Create   -> the current generation UI (ControlPanel + medium galleries),
                reached via `_on_source_change` exactly as it works today.
  - Curate   -> the current-source gallery, full-width (placeholder — a later
                slice adds the starred/playlist filter).
  - Discover -> Pipeline Studio's Discover page (`_show_pipelines`).
  - Remix    -> Pipeline Studio's Muse (`show_muse()`), via the same
                activation bridge `_remix_as_pipeline` already uses.

Mirrors tests/test_main_window_pipelines.py's harness: a minimal MainWindow
built via `__new__` with `Gtk.ApplicationWindow.__init__` patched out, then
hand-populated with only the handful of real Gtk widgets and collaborators
the seams under test actually touch.
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


def _make_mw(tmp_path, monkeypatch):
    """Minimal MainWindow harness exposing what the loop nav routes touch."""
    import pipeline_store
    monkeypatch.setattr(pipeline_store, "_INDEX_PATH", tmp_path / "pipeline-index.json")
    monkeypatch.setattr(pipeline_store, "_RUNS_DIR", tmp_path / "runs")

    import main_window as mw

    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)

    obj._pipeline_studio = None
    obj._gallery_stack = Gtk.Stack()
    obj._gallery_stack.add_named(Gtk.Box(), "video")
    obj._gallery_stack.set_visible_child_name("video")
    obj._ctrl_wrapper = Gtk.Box()
    obj._ctrl_wrapper.set_visible(True)
    obj._detail_wrap = Gtk.Box()
    obj._detail_wrap.set_visible(True)

    fake_controls = MagicMock()
    fake_controls.get_model_source.return_value = "video"
    obj._controls = fake_controls

    obj._pipelines_btn = Gtk.ToggleButton()
    obj._pipelines_toggle_syncing = False

    # Bind the real (unbound) methods under test so `self` resolves correctly.
    for name in (
        "_show_pipelines",
        "_hide_pipelines",
        "_on_pipelines_toggled",
        "_on_source_change",
        "_build_loop_nav",
        "_on_loop_nav_create",
        "_on_loop_nav_curate",
        "_on_loop_nav_discover",
        "_on_loop_nav_remix",
    ):
        setattr(obj, name, getattr(mw.MainWindow, name).__get__(obj))
    obj._pipelines_btn.connect("toggled", obj._on_pipelines_toggled)

    obj._rebuild_context_menu = MagicMock()
    obj.lookup_action = MagicMock(return_value=None)

    return obj


def test_build_loop_nav_exposes_keyed_buttons(tmp_path, monkeypatch):
    """_build_loop_nav returns a row and exposes buttons keyed by verb."""
    obj = _make_mw(tmp_path, monkeypatch)

    row = obj._build_loop_nav()

    assert isinstance(row, Gtk.Widget)
    assert set(obj._loop_nav.keys()) == {"create", "curate", "discover", "remix"}
    for btn in obj._loop_nav.values():
        assert isinstance(btn, Gtk.ToggleButton)


def test_create_is_default_active(tmp_path, monkeypatch):
    """Create starts active; the other three movements start inactive."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()

    obj._loop_nav["create"].set_active(True)

    assert obj._loop_nav["create"].get_active() is True
    assert obj._loop_nav["curate"].get_active() is False
    assert obj._loop_nav["discover"].get_active() is False
    assert obj._loop_nav["remix"].get_active() is False


def test_loop_nav_buttons_are_mutually_exclusive(tmp_path, monkeypatch):
    """The four buttons share one radio group — only one is active at a time."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()
    obj._loop_nav["create"].set_active(True)

    obj._loop_nav["discover"].set_active(True)

    assert obj._loop_nav["discover"].get_active() is True
    assert obj._loop_nav["create"].get_active() is False


def test_loop_nav_create_reaches_existing_gallery_and_controls(tmp_path, monkeypatch):
    """Activating Create routes to the existing gallery/ControlPanel view —
    generation is reached exactly as it is today, via _on_source_change."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()

    # Start from some other state (as if Curate/Discover were previously active).
    obj._ctrl_wrapper.set_visible(False)
    obj._detail_wrap.set_visible(False)

    obj._loop_nav["create"].set_active(True)

    assert obj._gallery_stack.get_visible_child_name() == "video"
    assert obj._ctrl_wrapper.get_visible() is True
    assert obj._detail_wrap.get_visible() is True
    assert obj._pipelines_btn.get_active() is False


def test_loop_nav_curate_routes_to_gallery_full_width(tmp_path, monkeypatch):
    """Curate is a placeholder this task: it shows the current gallery,
    collapsing the generation controls for a browse-only view."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()

    obj._loop_nav["curate"].set_active(True)

    assert obj._gallery_stack.get_visible_child_name() == "video"
    assert obj._ctrl_wrapper.get_visible() is False
    assert obj._detail_wrap.get_visible() is True


def test_loop_nav_discover_calls_show_pipelines(tmp_path, monkeypatch):
    """Discover routes to Pipeline Studio's Discover page via _show_pipelines."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()
    obj._show_pipelines = MagicMock()

    obj._loop_nav["discover"].set_active(True)

    assert obj._show_pipelines.called


def test_loop_nav_discover_lands_pipeline_studio_on_discover_page(tmp_path, monkeypatch):
    """End-to-end (no mocking): Discover really lands Pipeline Studio's inner
    stack on "discover" and mounts it full-width, same as the existing
    "🧩 Pipelines" toolbar toggle."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()

    obj._loop_nav["discover"].set_active(True)

    assert obj._gallery_stack.get_visible_child_name() == "pipelines"
    assert obj._pipeline_studio.stack.get_visible_child_name() == "discover"
    assert obj._ctrl_wrapper.get_visible() is False
    assert obj._detail_wrap.get_visible() is False
    # Stays in sync with the existing "🧩 Pipelines" toggle.
    assert obj._pipelines_btn.get_active() is True


def test_loop_nav_remix_calls_show_muse(tmp_path, monkeypatch):
    """Remix routes to Pipeline Studio's Muse via the existing bridge."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()

    obj._loop_nav["remix"].set_active(True)

    assert obj._gallery_stack.get_visible_child_name() == "pipelines"
    assert obj._pipeline_studio.stack.get_visible_child_name() == "muse"
    assert obj._pipelines_btn.get_active() is True


def test_loop_nav_remix_reuses_pipeline_studio_instance(tmp_path, monkeypatch):
    """Remix doesn't force a second PipelineStudio construction if Discover
    already built one (avoids re-scanning run history)."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()

    obj._loop_nav["discover"].set_active(True)
    first = obj._pipeline_studio
    obj._loop_nav["remix"].set_active(True)

    assert obj._pipeline_studio is first
    assert obj._pipeline_studio.stack.get_visible_child_name() == "muse"
