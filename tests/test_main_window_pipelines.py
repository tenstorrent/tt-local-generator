"""
Tests for mounting Pipeline Studio (Discover+Open) in the main window
(SP-C Phase 1, Task 5).

Constructing the full `MainWindow` (ControlPanel, GalleryWidget, DetailPanel,
history load, health workers, ...) is heavy and network/disk dependent, so —
mirroring the existing pattern in tests/test_main_window_animate_inputs.py —
these tests build a minimal `MainWindow` via `__new__` with `Gtk.ApplicationWindow
.__init__` patched out, then hand-populate only the handful of real Gtk widgets
and collaborators the seam under test (`_show_pipelines` / `_hide_pipelines` /
`_on_pipelines_toggled`) actually touches: `_gallery_stack`, `_ctrl_wrapper`,
`_detail_wrap`, and a stand-in `_controls` exposing `get_model_source()`.

`PipelineStore.list_runs` is monkeypatched to a small fixture (following
tests/test_pipeline_studio.py's own convention of pointing pipeline_store's
module-level paths at tmp_path) so PipelineStudio's background load thread
never touches the real user's pipeline history/disk.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the system PyGObject package is importable inside the venv, and that
# app/ is on sys.path for `import main_window` / `import pipeline_studio`.
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
    """Minimal MainWindow harness exposing only what _show_pipelines touches."""
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

    # Bind the real (unbound) methods under test so `self` resolves correctly.
    obj._show_pipelines = mw.MainWindow._show_pipelines.__get__(obj)
    obj._hide_pipelines = mw.MainWindow._hide_pipelines.__get__(obj)
    obj._on_pipelines_toggled = mw.MainWindow._on_pipelines_toggled.__get__(obj)
    obj._on_source_change = mw.MainWindow._on_source_change.__get__(obj)
    obj._rebuild_context_menu = MagicMock()
    obj.lookup_action = MagicMock(return_value=None)

    return obj


def test_show_pipelines_lazily_constructs_pipeline_studio(tmp_path, monkeypatch):
    """First activation constructs PipelineStudio and mounts it on the gallery stack."""
    from pipeline_studio import PipelineStudio

    obj = _make_mw(tmp_path, monkeypatch)
    assert obj._pipeline_studio is None

    obj._show_pipelines()

    assert isinstance(obj._pipeline_studio, PipelineStudio)
    assert obj._gallery_stack.get_child_by_name("pipelines") is obj._pipeline_studio
    assert obj._gallery_stack.get_visible_child_name() == "pipelines"
    # Pipeline Studio is full-width, like artgen mode: side panels collapse.
    assert obj._ctrl_wrapper.get_visible() is False
    assert obj._detail_wrap.get_visible() is False


def test_show_pipelines_reuses_instance_on_second_activation(tmp_path, monkeypatch):
    """Repeat activation doesn't rebuild PipelineStudio (avoids re-scanning history)."""
    obj = _make_mw(tmp_path, monkeypatch)

    obj._show_pipelines()
    first = obj._pipeline_studio
    obj._hide_pipelines()
    obj._show_pipelines()

    assert obj._pipeline_studio is first


def test_hide_pipelines_restores_current_source_view(tmp_path, monkeypatch):
    """Leaving Pipelines restores the gallery/side-panel state for the active source."""
    obj = _make_mw(tmp_path, monkeypatch)

    obj._show_pipelines()
    obj._hide_pipelines()

    assert obj._gallery_stack.get_visible_child_name() == "video"
    assert obj._ctrl_wrapper.get_visible() is True
    assert obj._detail_wrap.get_visible() is True


def test_pipelines_toggle_button_drives_show_and_hide(tmp_path, monkeypatch):
    """The toolbar toggle handler dispatches to _show_pipelines / _hide_pipelines."""
    obj = _make_mw(tmp_path, monkeypatch)
    btn = Gtk.ToggleButton()

    btn.set_active(True)
    obj._on_pipelines_toggled(btn)
    assert obj._gallery_stack.get_visible_child_name() == "pipelines"

    btn.set_active(False)
    obj._on_pipelines_toggled(btn)
    assert obj._gallery_stack.get_visible_child_name() == "video"


def test_source_change_unchecks_pipelines_toggle(tmp_path, monkeypatch):
    """Selecting a source tab while Pipelines is showing must visually uncheck
    the Pipelines toggle button, making the two mutually exclusive.

    Regression: previously the toggle stayed "checked" after a source tab was
    clicked — the gallery correctly switched away from Pipelines, but the
    toolbar button only self-corrected the next time Pipelines itself was
    clicked. Wired via the button's real "toggled" signal (not a direct call)
    so this also proves the fix doesn't recurse back into _hide_pipelines /
    _on_source_change when the toggle is flipped off programmatically.
    """
    obj = _make_mw(tmp_path, monkeypatch)
    obj._pipelines_btn = Gtk.ToggleButton()
    obj._pipelines_btn.connect("toggled", obj._on_pipelines_toggled)

    obj._pipelines_btn.set_active(True)
    assert obj._gallery_stack.get_visible_child_name() == "pipelines"
    assert obj._pipelines_btn.get_active() is True

    # Simulate clicking the "video" source tab while Pipelines is showing.
    obj._on_source_change("video")

    assert obj._gallery_stack.get_visible_child_name() == "video"
    assert obj._pipelines_btn.get_active() is False
