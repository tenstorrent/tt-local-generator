# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Integration/wiring tests for regression fix 2/2 ("tt-local-generator
inspire2") — confirms the ✨ Inspire seam actually reaches
`ArtgenParamPanel` (via `CreateView`) and `RemixView` (via `PipelineStudio`),
not just that the receiving ends (`create_param_panels.py`,
`pipeline_studio.py`, tested in tests/test_artgen_inspire.py and
tests/test_pipeline_inspire.py) behave correctly in isolation.

Two harnesses, mirroring existing conventions:
  - CreateView <-> ArtgenParamPanel: `tests/test_create_view.py`'s own
    `_make_view`/`_fake_mediums`/`_panel_of` pattern (duplicated here per
    that file's own "duplicated rather than shared via conftest.py" note).
  - MainWindow <-> PipelineStudio: `tests/test_main_window_pipelines.py`'s
    `_make_mw` harness (`__new__` + `Gtk.ApplicationWindow.__init__` patched
    out, only the handful of real attributes `_show_pipelines` touches
    hand-populated), extended with an assertion on the newly-threaded
    `inspire_fn`.
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

from create_mediums import Medium
from create_param_panels import ArtgenParamPanel, RoleZonePanel


# ── CreateView -> ArtgenParamPanel ──────────────────────────────────────────


class _ImmediateThread:
    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _fake_mediums():
    return [
        Medium(id="image", label="Image", icon="\U0001f5bc", kind="image",
               source="native", generator=None),
        Medium(id="verse", label="Verse", icon="✍", kind="text",
               source="artgen", generator="verse"),
    ]


def _make_create_view(monkeypatch, **kwargs):
    import create_view
    monkeypatch.setattr(create_view.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(create_view.GLib, "idle_add", lambda fn, *a: fn(*a))
    kwargs.setdefault("mediums_fn", _fake_mediums)
    kwargs.setdefault("health_fn", lambda: {})
    return create_view.CreateView(**kwargs)


def _panel_of(view):
    return view._active_panel._panel


class _FakeInspire:
    def __call__(self, prompt_type, seed_text, on_result, on_error):
        pass


def test_create_view_threads_inspire_fn_into_artgen_param_panel(monkeypatch):
    """Selecting an artgen medium (verse) must mount an ArtgenParamPanel
    built with THIS CreateView's own `_inspire_fn`/`_inspire_prompt_type` —
    not a bare `ArtgenParamPanel(medium.generator)` that silently drops the
    seam (which is what the code did before this wiring)."""
    fake = _FakeInspire()
    view = _make_create_view(monkeypatch, inspire_fn=fake)

    view._chip_buttons["verse"].set_active(True)

    panel = _panel_of(view)
    assert isinstance(panel, ArtgenParamPanel)
    assert panel._inspire_fn is fake
    assert panel._prompt_type_getter == view._inspire_prompt_type


def test_create_view_artgen_panel_gets_no_inspire_fn_when_not_injected(monkeypatch):
    """Migration-safe default: no `inspire_fn` passed to CreateView ->
    ArtgenParamPanel gets `inspire_fn=None` -> no ✨ buttons (see
    tests/test_artgen_inspire.py)."""
    view = _make_create_view(monkeypatch)  # no inspire_fn

    view._chip_buttons["verse"].set_active(True)

    panel = _panel_of(view)
    assert isinstance(panel, ArtgenParamPanel)
    assert panel._inspire_fn is None


def test_artgen_panel_row_zone_wrapped_collect_unaffected_by_inspire_wiring(monkeypatch):
    """End-to-end sanity for the hard RoleZonePanel.collect() invariant: the
    real CreateView -> ArtgenParamPanel construction path (with an
    inspire_fn wired through) still yields a RoleZonePanel whose collect()
    equals a same-generator panel built with no inspire_fn at all."""
    view_wired = _make_create_view(monkeypatch, inspire_fn=_FakeInspire())
    view_wired._chip_buttons["verse"].set_active(True)
    assert isinstance(view_wired._active_panel, RoleZonePanel)

    view_plain = _make_create_view(monkeypatch)
    view_plain._chip_buttons["verse"].set_active(True)
    assert isinstance(view_plain._active_panel, RoleZonePanel)

    assert view_wired._active_panel.collect() == view_plain._active_panel.collect()


# ── MainWindow -> PipelineStudio -> RemixView ───────────────────────────────


def _make_mw(tmp_path, monkeypatch):
    """Same harness as tests/test_main_window_pipelines.py's `_make_mw`,
    duplicated here per that file's own precedent (a full MainWindow() is
    too heavy/network-dependent to build in tests)."""
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
    obj._detail_wrap = Gtk.Box()
    obj._detail_wrap.set_visible(True)
    obj._current_medium_source = MagicMock(return_value="video")

    # Task 5 (model picker): `_show_pipelines` threads `_status_service` into
    # `PipelineStudio` -- `None` is a legitimate real degrade value.
    obj._status_service = None

    obj._show_pipelines = mw.MainWindow._show_pipelines.__get__(obj)
    obj._rebuild_context_menu = MagicMock()
    obj.lookup_action = MagicMock(return_value=None)

    return obj


def test_show_pipelines_wires_create_inspire_fn_into_pipeline_studio(tmp_path, monkeypatch):
    """`_show_pipelines` must construct `PipelineStudio(inspire_fn=self.
    _create_inspire_fn)` — the exact seam CreateView's idea-door/
    ArtgenParamPanel already drive — so RemixView's step-card text fields get
    the same ✨ two-mode behavior, not a dropped/forked seam."""
    obj = _make_mw(tmp_path, monkeypatch)

    obj._show_pipelines()

    assert obj._pipeline_studio._inspire_fn == obj._create_inspire_fn
    assert obj._pipeline_studio.remix_view._inspire_fn == obj._create_inspire_fn


def test_main_window_source_wires_inspire_fn_at_both_construction_sites():
    """Belt-and-suspenders source check (mirrors
    test_main_window_wires_artgen_gallery_on_remix_as_pipeline_source's own
    style in tests/test_main_window_pipelines.py): both the CreateView
    construction and the lazy PipelineStudio construction must literally
    pass an `inspire_fn=` keyword, so a future edit that drops either
    wiring is caught even before a harness-level test would notice."""
    src = (Path(__file__).parent.parent / "app" / "main_window.py").read_text()
    assert "self._create_view = CreateView(" in src
    # Task 5 (model picker) added a second keyword (`status_service=`) to this
    # same call site, so the literal single-line form no longer appears --
    # check for the call site + the inspire_fn keyword instead.
    assert "self._pipeline_studio = PipelineStudio(inspire_fn=self._create_inspire_fn," in src
    assert "status_service=self._status_service)" in src

    create_view_src = (Path(__file__).parent.parent / "app" / "create_view.py").read_text()
    assert "inspire_fn=self._inspire_fn,\n                prompt_type_getter=self._inspire_prompt_type," in create_view_src
