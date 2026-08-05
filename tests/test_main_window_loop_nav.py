"""
Tests for the top-level nav.

RN-1 of the Unified-Stage revision (docs/superpowers/specs/2026-08-02-unified-
stage-design.md) replaced the earlier four-verb loop (Create/Discover/Watch/
Remix, with arrow/↺ separators) with a two-place model plus a companion:
  - ✨ Create   -> the Create surface (`_on_loop_nav_create`).
  - 🗂 Library  -> Pipeline Studio's Discover page absorbs browse+curate
                   (`_on_loop_nav_discover`).
  - ▶ Play      -> a plain action button beside Library (not in the radio
                   group) that opens the TT-TV kiosk (`_on_open_attractor`).
Remix is no longer a nav place — it's a per-item action — but the
`_on_loop_nav_remix` METHOD is retained because CreateView's "Start with
inspiration" door still calls it directly.

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
    # SP-3d-6 regression fix: the switcher under test targets all four
    # galleries, not just "video" -- give the harness real named children
    # for "image"/"animate"/"artgen" too so `set_visible_child_name` (called
    # by `_sync_gallery_to_source`, which the switcher's buttons drive) has
    # somewhere real to land instead of silently no-op'ing on a missing name.
    obj._gallery_stack.add_named(Gtk.Box(), "image")
    obj._gallery_stack.add_named(Gtk.Box(), "animate")
    obj._gallery_stack.add_named(Gtk.Box(), "artgen")
    obj._gallery_stack.set_visible_child_name("video")
    obj._detail_wrap = Gtk.Box()
    obj._detail_wrap.set_visible(True)

    # SP-3d-3/5: `_current_medium_source()` replaced ControlPanel's
    # `get_model_source()`; ControlPanel itself is deleted (SP-3d-5).
    obj._current_medium_source = MagicMock(return_value="video")

    # Task 5 (model picker): `_show_pipelines` threads `_status_service` into
    # `PipelineStudio` -- `None` is a legitimate real degrade value.
    obj._status_service = None

    obj._gallery_stack.add_named(Gtk.Box(), "create")

    obj._pipelines_btn = Gtk.ToggleButton()
    obj._pipelines_toggle_syncing = False

    # Bind the real (unbound) methods under test so `self` resolves correctly.
    for name in (
        "_show_pipelines",
        "_hide_pipelines",
        "_on_pipelines_toggled",
        "_sync_gallery_to_source",
        "_uncheck_pipelines_toggle_if_active",
        "_build_loop_nav",
        "_build_discover_type_row",
        "_on_loop_nav_create",
        "_on_loop_nav_discover",
        "_on_loop_nav_remix",
    ):
        setattr(obj, name, getattr(mw.MainWindow, name).__get__(obj))
    obj._pipelines_btn.connect("toggled", obj._on_pipelines_toggled)

    # SP-3d-6 regression fix harness: real `_build_ui()` constructs
    # `_discover_type_row` once up front (mirroring `_build_loop_nav`) and
    # stores it on `self` before the loop nav's default-active button is
    # ever toggled -- reproduce that here so `_on_loop_nav_discover` (which
    # only reads `self._discover_type_row`/`self._discover_type_buttons` via
    # `getattr(..., None)`, never builds them itself) has something real to
    # show/hide.
    obj._discover_type_row = obj._build_discover_type_row()
    obj._discover_type_row.set_visible(False)

    obj._rebuild_context_menu = MagicMock()
    obj.lookup_action = MagicMock(return_value=None)
    obj._on_open_attractor = MagicMock()

    return obj


def test_create_switches_after_pipelines(tmp_path, monkeypatch):
    """Regression: Pipelines -> Create must switch to Create. The 🧩 Pipelines
    toggle sits OUTSIDE the Create/Library radio group, so entering Pipelines
    must clear that group. Otherwise the previously-active loop-nav button stays
    "active" while Pipelines shows, and re-clicking it emits no "toggled" (a
    grouped toggle can't re-activate itself) -- so the view never switches
    ("click Pipelines then Create does nothing")."""
    obj = _make_mw(tmp_path, monkeypatch)
    # _show_pipelines collaborators (already-built studio -> no heavy construct)
    obj._pipeline_studio = MagicMock()
    obj._set_crumbs = MagicMock()
    obj._nav_open_context = MagicMock()
    obj._gallery_stack.add_named(Gtk.Box(), "pipelines")

    obj._build_loop_nav()                       # real create/discover toggles
    create_btn = obj._loop_nav["create"]
    create_btn.set_active(True)                 # Create is the active place
    obj._gallery_stack.set_visible_child_name("create")

    obj._show_pipelines()                       # enter Pipelines
    assert obj._gallery_stack.get_visible_child_name() == "pipelines"
    # The loop-nav radio group MUST be cleared so Create can be re-selected.
    assert create_btn.get_active() is False

    create_btn.set_active(True)                 # click Create
    assert obj._gallery_stack.get_visible_child_name() == "create"


def test_build_loop_nav_two_places_plus_play(tmp_path, monkeypatch):
    """The nav is two places (Create, Library) plus a ▶ Play companion —
    no Remix pill, no arrow/loop separators."""
    obj = _make_mw(tmp_path, monkeypatch)

    row = obj._build_loop_nav()

    assert isinstance(row, Gtk.Widget)
    assert set(obj._loop_nav.keys()) == {"create", "discover"}
    for btn in obj._loop_nav.values():
        assert isinstance(btn, Gtk.ToggleButton)
    assert isinstance(obj._attractor_btn, Gtk.Button)
    assert not obj._attractor_btn.get_sensitive()

    labels = []
    child = row.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            labels.append(child.get_label())
        child = child.get_next_sibling()
    assert labels == ["✨ Create", "🗂 Library", "▶ Play"]
    # no arrow/loop separator labels remain in the row
    texts = []
    child = row.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            texts.append(child.get_text())
        child = child.get_next_sibling()
    assert "→" not in texts and "↺" not in texts


def test_create_is_default_active(tmp_path, monkeypatch):
    """Create starts active; Library starts inactive."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()

    obj._loop_nav["create"].set_active(True)

    assert obj._loop_nav["create"].get_active() is True
    assert obj._loop_nav["discover"].get_active() is False


def test_loop_nav_buttons_are_mutually_exclusive(tmp_path, monkeypatch):
    """Create and Library share one radio group — only one is active at a time."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()
    obj._loop_nav["create"].set_active(True)

    obj._loop_nav["discover"].set_active(True)

    assert obj._loop_nav["discover"].get_active() is True
    assert obj._loop_nav["create"].get_active() is False


def test_loop_nav_create_routes_to_create_view(tmp_path, monkeypatch):
    """Task 8 (switchover subset): activating Create now shows CreateView
    (the "create" `_gallery_stack` child) directly, rather than resolving
    through `_on_source_change`'s medium-tab lookup."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()

    obj._loop_nav["create"].set_active(True)

    assert obj._gallery_stack.get_visible_child_name() == "create"
    assert obj._pipelines_btn.get_active() is False


def test_loop_nav_create_unchecks_pipelines_toggle(tmp_path, monkeypatch):
    """If Pipelines was showing (shares `_gallery_stack`), Create must
    uncheck its toggle — same stale-toggle fix `_on_source_change` already
    provided for the old medium tabs, now reproduced for CreateView. There is
    no Remix nav button anymore, so open Pipelines via the retained
    `_on_loop_nav_remix` method directly (CreateView's inspiration door does
    the same)."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()

    obj._on_loop_nav_remix()
    assert obj._pipelines_btn.get_active() is True

    obj._loop_nav["create"].set_active(True)

    assert obj._gallery_stack.get_visible_child_name() == "create"
    assert obj._pipelines_btn.get_active() is False


def test_loop_nav_discover_routes_to_gallery_full_width(tmp_path, monkeypatch):
    """Discover (absorbs Curate): browse+collect the current medium's gallery,
    with the star/playlist/detail actions intact — this is where you curate
    as you find things. (SP-3d-5: ControlPanel's `_ctrl_wrapper` — the
    generation-controls pane this used to also assert collapsed — is deleted
    alongside the class; there is no longer a separate controls pane to
    collapse.)"""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()

    obj._loop_nav["discover"].set_active(True)

    assert obj._gallery_stack.get_visible_child_name() == "video"
    assert obj._detail_wrap.get_visible() is True


def test_loop_nav_discover_then_create_hides_detail_pane(tmp_path, monkeypatch):
    """Create is full-width and hides `_detail_wrap` (CreateView owns its own
    result area); Discover shows it. So Discover(shows)->Create(hides) must
    leave the pane hidden — no dead 'click a card to preview' placeholder on
    the Create surface, whose possibility tiles seed the composer, not the pane."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()

    obj._loop_nav["discover"].set_active(True)
    assert obj._detail_wrap.get_visible() is True

    obj._loop_nav["create"].set_active(True)

    assert obj._gallery_stack.get_visible_child_name() == "create"
    assert obj._detail_wrap.get_visible() is False


def test_loop_nav_remix_calls_show_muse(tmp_path, monkeypatch):
    """`_on_loop_nav_remix` routes to Pipeline Studio's Muse via the existing
    bridge. There is no Remix nav button anymore — the method is retained
    only for CreateView's "Start with inspiration" door, so this test calls
    it directly rather than via a nav button toggle."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()

    obj._on_loop_nav_remix()

    assert obj._gallery_stack.get_visible_child_name() == "pipelines"
    assert obj._pipeline_studio.stack.get_visible_child_name() == "muse"
    assert obj._pipelines_btn.get_active() is True


def test_loop_nav_remix_reuses_pipeline_studio_instance(tmp_path, monkeypatch):
    """Remix doesn't force a second PipelineStudio construction once one was
    built (avoids re-scanning run history) — called directly since there is
    no Remix nav button anymore."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()

    obj._on_loop_nav_remix()
    first = obj._pipeline_studio
    obj._loop_nav["create"].set_active(True)
    obj._on_loop_nav_remix()

    assert obj._pipeline_studio is first
    assert obj._pipeline_studio.stack.get_visible_child_name() == "muse"


# ── Discover media-type switcher (SP-3d-6 regression fix) ───────────────────
#
# SP-3d-5 deleted the legacy medium-tab source toggle -- the only UI that let
# you pick which gallery `_gallery_stack` showed while browsing. That left
# Discover pinned to whatever `_current_medium_source()` (CreateView's active
# medium) happened to be, so the animate and artgen galleries became
# unreachable: there was no way to ask Discover to show them. These tests
# drive the new Discover-owned `_discover_type_row` switcher that restores
# that browsing capability without resurrecting ControlPanel.


def test_build_discover_type_row_exposes_keyed_buttons(tmp_path, monkeypatch):
    """_build_discover_type_row returns a row and exposes one toggle per
    gallery, keyed the same way `_sync_gallery_to_source` expects."""
    obj = _make_mw(tmp_path, monkeypatch)

    row = obj._build_discover_type_row()

    assert isinstance(row, Gtk.Widget)
    assert set(obj._discover_type_buttons.keys()) == {"video", "image", "animate", "artgen"}
    for btn in obj._discover_type_buttons.values():
        assert isinstance(btn, Gtk.ToggleButton)


def test_discover_type_row_hidden_before_discover_entered(tmp_path, monkeypatch):
    """The switcher starts hidden -- Create is the loop's default landing
    movement, and the row must not show up underneath it."""
    obj = _make_mw(tmp_path, monkeypatch)

    assert obj._discover_type_row.get_visible() is False


def test_entering_discover_shows_type_row(tmp_path, monkeypatch):
    """Activating Discover reveals the switcher."""
    obj = _make_mw(tmp_path, monkeypatch)

    obj._on_loop_nav_discover()

    assert obj._discover_type_row.get_visible() is True


def test_entering_discover_defaults_active_type_to_current_medium_source(tmp_path, monkeypatch):
    """On entering Discover, the type button matching
    `_current_medium_source()` is the one that reads active -- Discover opens
    on a sensible gallery, not always "video"."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._current_medium_source = MagicMock(return_value="animate")

    obj._on_loop_nav_discover()

    assert obj._discover_type_buttons["animate"].get_active() is True
    assert obj._discover_type_buttons["video"].get_active() is False
    assert obj._gallery_stack.get_visible_child_name() == "animate"


def test_clicking_artgen_type_button_switches_gallery(tmp_path, monkeypatch):
    """Clicking the Artgen type button switches `_gallery_stack` to "artgen"
    -- this is the regression: previously there was no way to reach the
    artgen gallery from Discover at all."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._on_loop_nav_discover()

    obj._discover_type_buttons["artgen"].set_active(True)

    assert obj._gallery_stack.get_visible_child_name() == "artgen"


def test_clicking_animate_type_button_switches_gallery(tmp_path, monkeypatch):
    """Clicking the Animate type button switches `_gallery_stack` to
    "animate" -- also unreachable before this fix."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._on_loop_nav_discover()

    obj._discover_type_buttons["animate"].set_active(True)

    assert obj._gallery_stack.get_visible_child_name() == "animate"


def test_discover_type_row_hidden_in_create(tmp_path, monkeypatch):
    """Leaving Discover for Create hides the switcher -- it must not overlap
    the Create surface."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()
    obj._on_loop_nav_discover()
    assert obj._discover_type_row.get_visible() is True

    obj._loop_nav["create"].set_active(True)

    assert obj._discover_type_row.get_visible() is False


def test_discover_type_row_hidden_in_remix(tmp_path, monkeypatch):
    """Leaving Discover for Remix (via the retained `_on_loop_nav_remix`
    method -- there is no Remix nav button anymore) hides the switcher -- it
    must not overlap the Remix/Muse surface."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()
    obj._on_loop_nav_discover()
    assert obj._discover_type_row.get_visible() is True

    obj._on_loop_nav_remix()

    assert obj._discover_type_row.get_visible() is False


def test_create_hides_detail_pane_and_discover_shows_it(tmp_path, monkeypatch):
    """Create is full-width (CreateView owns its own result area), so the
    Discover detail pane is hidden on Create — no dead 'click a card to
    preview' false promise. Discover re-shows it (it has cards to preview)."""
    obj = _make_mw(tmp_path, monkeypatch)
    obj._build_loop_nav()

    obj._detail_wrap.set_visible(True)
    obj._loop_nav["create"].set_active(True)
    assert obj._detail_wrap.get_visible() is False

    obj._loop_nav["discover"].set_active(True)
    assert obj._detail_wrap.get_visible() is True
