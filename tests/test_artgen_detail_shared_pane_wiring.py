# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for "unify gallery interaction pattern" Task 3 — routes ArtgenGallery
card selection/deletion into MainWindow's shared right-pane `ArtgenDetail`
(`self._right_stack`, built by Task 2), replacing ArtgenGallery's own in-page
detail overlay (a crash workaround; see test_artgen_gallery_preview.py and
CLAUDE.md's "unify gallery interaction pattern" notes).

Harness mirrors test_detail_pane_toggle.py's `_make_bare_mw`: a bare
`MainWindow.__new__` with the real (unbound) method under test bound on, and
every collaborator it touches stubbed with plain fakes/Mocks -- no real
widget tree is built.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

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

_SRC = (Path(__file__).parent.parent / "app" / "main_window.py").read_text()


# ── Source-level: ArtgenGallery signal wiring ───────────────────────────────

def test_artgen_gallery_card_activated_wired_to_shared_pane_handler():
    assert (
        "self._artgen_gallery.on_card_activated = self._on_artgen_card_selected"
        in _SRC
    )


def test_artgen_gallery_card_deleted_wired_to_shared_pane_handler():
    assert (
        "self._artgen_gallery.on_card_deleted = self._on_artgen_card_deleted" in _SRC
    )


# ── Behavioral: _on_artgen_card_selected ────────────────────────────────────

def _make_bare_mw(**attrs):
    import main_window as mw

    obj = mw.MainWindow.__new__(mw.MainWindow)
    for k, v in attrs.items():
        setattr(obj, k, v)
    return obj


def _bind(obj, *names):
    import main_window as mw

    for name in names:
        setattr(obj, name, getattr(mw.MainWindow, name).__get__(obj))


def test_on_artgen_card_selected_switches_stack_and_shows_record():
    artgen_gallery = MagicMock()
    artgen_gallery._filtered_records.return_value = ["r1", "r2"]
    obj = _make_bare_mw(
        _right_stack=MagicMock(),
        _artgen_detail=MagicMock(),
        _artgen_gallery=artgen_gallery,
    )
    _bind(obj, "_on_artgen_card_selected")

    obj._on_artgen_card_selected("media-123")

    obj._right_stack.set_visible_child_name.assert_called_once_with("artgen")
    obj._artgen_detail.show_record.assert_called_once_with("media-123", ["r1", "r2"])


def test_on_artgen_card_selected_never_force_shows_the_pane():
    """Matches native _on_card_selected: neither handler ever calls
    _set_detail_pane_visible(True) -- a collapsed pane stays collapsed until
    the user reopens it. Deliberately NOT setting _set_detail_pane_visible on
    the bare object -- if the method touched it, this would raise
    AttributeError instead of silently passing."""
    artgen_gallery = MagicMock()
    artgen_gallery._filtered_records.return_value = []
    obj = _make_bare_mw(
        _right_stack=MagicMock(),
        _artgen_detail=MagicMock(),
        _artgen_gallery=artgen_gallery,
    )
    _bind(obj, "_on_artgen_card_selected")

    obj._on_artgen_card_selected("media-123")  # must not raise


# ── Behavioral: _on_artgen_card_deleted ──────────────────────────────────────

def _make_record(rid):
    return SimpleNamespace(id=rid)


def test_on_artgen_card_deleted_collapses_pane_when_showing_deleted_record():
    obj = _make_bare_mw(
        _right_stack=MagicMock(),
        _artgen_detail=SimpleNamespace(_records=[_make_record("a")], _idx=0),
        _set_detail_pane_visible=MagicMock(),
    )
    obj._right_stack.get_visible_child_name.return_value = "artgen"
    _bind(obj, "_on_artgen_card_deleted")

    obj._on_artgen_card_deleted("a")

    obj._set_detail_pane_visible.assert_called_once_with(False)


def test_on_artgen_card_deleted_ignores_other_records():
    obj = _make_bare_mw(
        _right_stack=MagicMock(),
        _artgen_detail=SimpleNamespace(_records=[_make_record("a")], _idx=0),
        _set_detail_pane_visible=MagicMock(),
    )
    obj._right_stack.get_visible_child_name.return_value = "artgen"
    _bind(obj, "_on_artgen_card_deleted")

    obj._on_artgen_card_deleted("some-other-id")

    obj._set_detail_pane_visible.assert_not_called()


def test_on_artgen_card_deleted_ignores_when_pane_not_showing_artgen():
    """The right pane is showing a native record (or is collapsed) -- an
    artgen grid delete elsewhere must not touch it."""
    obj = _make_bare_mw(
        _right_stack=MagicMock(),
        _artgen_detail=SimpleNamespace(_records=[_make_record("a")], _idx=0),
        _set_detail_pane_visible=MagicMock(),
    )
    obj._right_stack.get_visible_child_name.return_value = "native"
    _bind(obj, "_on_artgen_card_deleted")

    obj._on_artgen_card_deleted("a")

    obj._set_detail_pane_visible.assert_not_called()


def test_on_artgen_card_deleted_tolerates_empty_records():
    obj = _make_bare_mw(
        _right_stack=MagicMock(),
        _artgen_detail=SimpleNamespace(_records=[], _idx=0),
        _set_detail_pane_visible=MagicMock(),
    )
    obj._right_stack.get_visible_child_name.return_value = "artgen"
    _bind(obj, "_on_artgen_card_deleted")

    obj._on_artgen_card_deleted("a")  # must not raise

    obj._set_detail_pane_visible.assert_not_called()


# ── Behavioral: _on_artgen_detail_deleted ───────────────────────────────────

def test_on_artgen_detail_deleted_syncs_gallery():
    artgen_gallery = MagicMock()
    obj = _make_bare_mw(_artgen_gallery=artgen_gallery)
    _bind(obj, "_on_artgen_detail_deleted")

    obj._on_artgen_detail_deleted("media-xyz")

    artgen_gallery.remove_record.assert_called_once_with("media-xyz")


# ── Behavioral: _on_artgen_detail_starred ───────────────────────────────────

def test_on_artgen_detail_starred_persists_updates_record_and_rebuilds_chips(monkeypatch):
    import media_store as ms_mod

    star_calls = []
    monkeypatch.setattr(
        ms_mod.media_store, "star",
        lambda mid, starred: star_calls.append((mid, starred)),
    )

    rec = SimpleNamespace(id="abc", starred=0)
    artgen_gallery = MagicMock()
    artgen_gallery._records = [rec]
    obj = _make_bare_mw(_artgen_gallery=artgen_gallery)
    _bind(obj, "_on_artgen_detail_starred")

    obj._on_artgen_detail_starred("abc", True)

    assert star_calls == [("abc", True)]
    assert rec.starred == 1
    artgen_gallery._rebuild_chips.assert_called_once()


def test_on_artgen_detail_starred_ignores_unknown_record(monkeypatch):
    import media_store as ms_mod

    monkeypatch.setattr(ms_mod.media_store, "star", lambda mid, starred: None)

    rec = SimpleNamespace(id="abc", starred=0)
    artgen_gallery = MagicMock()
    artgen_gallery._records = [rec]
    obj = _make_bare_mw(_artgen_gallery=artgen_gallery)
    _bind(obj, "_on_artgen_detail_starred")

    obj._on_artgen_detail_starred("does-not-exist", True)  # must not raise

    assert rec.starred == 0
    artgen_gallery._rebuild_chips.assert_called_once()


# ── Behavioral: native _on_card_selected pauses artgen + switches stack ─────

def test_on_card_selected_pauses_artgen_animation_and_switches_stack_to_native():
    record = SimpleNamespace(id="vid-1", media_type="video")
    card = SimpleNamespace(_record=record)
    gallery = MagicMock()
    gallery.all_cards.return_value = [card]

    obj = _make_bare_mw(
        _artgen_detail=MagicMock(),
        _right_stack=MagicMock(),
        _gallery_for_type=lambda media_type: gallery,
        _detail=MagicMock(),
        _dispatch_remix=MagicMock(),
        _remix_as_pipeline=MagicMock(),
    )
    _bind(obj, "_on_card_selected")

    obj._on_card_selected(record)

    obj._artgen_detail.pause_animation.assert_called_once()
    obj._right_stack.set_visible_child_name.assert_called_once_with("native")
    obj._detail.set_context.assert_called_once_with([record], 0)
    obj._detail.show_record.assert_called_once()
