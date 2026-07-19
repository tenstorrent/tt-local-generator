"""
Tests for "unify gallery interaction pattern" Task 3 — removes ArtgenGallery's
in-page detail overlay (a crash workaround) and makes card activation/deletion
a pure signal-forwarding contract, matching the native GalleryWidgets.

History: this file used to guard the *previous* fix (SP-3d-5, commit
ff1d246), where `ArtgenGallery` grew its OWN in-page `ArtgenDetail` shown in a
`Gtk.Overlay` over the grid (`_detail_overlay`/`_grid_page` opacity toggle) --
a workaround for a segfault where a `Gtk.Stack` UNMAPPED the grid while its
just-clicked `FlowBoxChild` was mid-dispatch (commits b096a01/d3039f0).

Task 3 removes that overlay entirely: MainWindow's shared `_right_stack` (in
`_detail_wrap`) is a SIBLING subtree of the gallery grid under `inner_paned`,
so switching it never unmaps the FlowBox/grid -- it cannot reproduce that
crash class, so the workaround is no longer needed here. `ArtgenGallery` is
now grid-only; card activation forwards media_id to `on_card_activated`
(still additive/no-op-safe if unwired, same contract the native galleries'
select_cb use).

Task 6 later replaced the FlowBox "child-activated" signal (SelectionMode.
SINGLE, single-click-only) with a per-card Gtk.GestureClick (SelectionMode.
NONE) -- see tests/test_artgen_gallery_click_mechanism.py for that mechanism.
The activation tests below now drive the real per-card gesture instead of
calling the old `_on_card_activated(flow, child)` signal handler directly
(that method no longer exists).

Run under xvfb (GTK4 widgets need a real display):
    xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_artgen_gallery_preview.py -v
"""
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _gtk_available() -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk  # noqa: F401
        return True
    except Exception:
        return False


gtk_required = pytest.mark.skipif(
    not _gtk_available(), reason="GTK4 display not available"
)

_SRC = (Path(__file__).parent.parent / "app" / "artgen_gallery.py").read_text()


def _make_media_record(tmp_path: Path, **kwargs) -> "object":
    """Build a MediaRecord backed by a real .txt file on disk (verse-style
    content), so ArtgenDetail._render's fp.read_text() call has something
    real to read (kept even though this file no longer exercises ArtgenDetail
    directly, so the record shape stays realistic for future callers)."""
    from media_store import MediaRecord

    file_path = tmp_path / f"{uuid.uuid4()}.txt"
    file_path.write_text("# A Test Verse\n\nSomething something artgen.\n")

    base = dict(
        id=str(uuid.uuid4()),
        media_type="artgen",
        created_at="2026-01-01T00:00:00Z",
        file_path=str(file_path),
        thumbnail_path="",
        prompt="a test artgen prompt",
        model_id="qwen3-8b",
        generator_type="verse",
        params="{}",
        starred=0,
    )
    base.update(kwargs)
    return MediaRecord(**base)


def _find_child_for(gallery, media_id: str):
    """Walk the FlowBox to find the FlowBoxChild wrapping the card whose
    stashed `_media_id` matches."""
    child = gallery._flow.get_first_child()
    while child is not None:
        box = child.get_child()
        if box is not None and getattr(box, "_media_id", None) == media_id:
            return child
        child = child.get_next_sibling()
    return None


def _fire_single_click(gallery, media_id: str) -> None:
    """Drive a card's real, connected primary GestureClick with a single
    press (n_press=1) -- the Task 6 replacement for calling the old
    `_on_card_activated(flow, child)` signal handler directly."""
    import gi
    from gi.repository import Gtk

    child = _find_child_for(gallery, media_id)
    assert child is not None, f"expected a FlowBoxChild for {media_id!r}"
    overlay = child.get_child()
    gestures = [c for c in overlay.observe_controllers() if isinstance(c, Gtk.GestureClick)]
    assert gestures, "expected a GestureClick controller on the card overlay"
    gestures[0].emit("pressed", 1, 0.0, 0.0)


# ── Source-level: the in-page overlay/detail is gone ───────────────────────

def test_build_no_longer_creates_an_in_page_detail_or_overlay():
    assert "from artgen_detail import ArtgenDetail" not in _SRC
    assert "self._detail = ArtgenDetail(" not in _SRC
    assert "_detail_overlay" not in _SRC
    assert "def _show_detail(" not in _SRC
    assert "def _detail_shown(" not in _SRC
    # NOTE: `Gtk.Overlay()` itself still appears in _make_card (the per-card
    # hover-actions overlay, unrelated and untouched) -- only the detail
    # container's Overlay is gone, which the _build-scoped check below covers.


def test_build_does_not_construct_a_detail_overlay():
    start = _SRC.index("def _build(self")
    end = _SRC.index("\n    def ", start + 1)
    body = _SRC[start:end]
    assert "Gtk.Overlay()" not in body


def test_build_appends_grid_page_directly():
    start = _SRC.index("def _build(self")
    end = _SRC.index("\n    def ", start + 1)
    body = _SRC[start:end]
    assert "self.append(grid_page)" in body


# ── Behavioral: activation is pure signal-forwarding ────────────────────────

@gtk_required
def test_card_activation_forwards_media_id_when_wired(tmp_path):
    """`on_card_activated` fires with the clicked card's media_id -- the only
    thing a single click on the card's GestureClick does (Task 6)."""
    import media_store as ms_mod
    from artgen_gallery import ArtgenGallery

    rec = _make_media_record(tmp_path)
    ms_mod.media_store.add(rec)

    gallery = ArtgenGallery()
    seen = []
    gallery.on_card_activated = lambda media_id: seen.append(media_id)
    gallery.refresh()

    _fire_single_click(gallery, rec.id)

    assert seen == [rec.id]


@gtk_required
def test_card_activation_is_a_noop_without_external_wiring(tmp_path):
    """With no `on_card_activated` wired (the pre-Task-3 regression path),
    activating a card must not raise -- there is no more internal detail
    view for it to open."""
    import media_store as ms_mod
    from artgen_gallery import ArtgenGallery

    rec = _make_media_record(tmp_path)
    ms_mod.media_store.add(rec)

    gallery = ArtgenGallery()
    assert gallery.on_card_activated is None
    gallery.refresh()

    _fire_single_click(gallery, rec.id)  # must not raise


# ── Behavioral: remove_record ───────────────────────────────────────────────

@gtk_required
def test_remove_record_prunes_records_and_rebuilds_grid(tmp_path):
    import media_store as ms_mod
    from artgen_gallery import ArtgenGallery

    rec_a = _make_media_record(tmp_path)
    rec_b = _make_media_record(tmp_path)
    ms_mod.media_store.add(rec_a)
    ms_mod.media_store.add(rec_b)

    gallery = ArtgenGallery()
    gallery.refresh()
    assert {r.id for r in gallery._records} == {rec_a.id, rec_b.id}

    gallery.remove_record(rec_a.id)

    assert [r.id for r in gallery._records] == [rec_b.id]
    assert _find_child_for(gallery, rec_a.id) is None
    assert _find_child_for(gallery, rec_b.id) is not None


@gtk_required
def test_remove_record_of_unknown_id_is_a_noop(tmp_path):
    import media_store as ms_mod
    from artgen_gallery import ArtgenGallery

    rec = _make_media_record(tmp_path)
    ms_mod.media_store.add(rec)

    gallery = ArtgenGallery()
    gallery.refresh()

    gallery.remove_record("does-not-exist")

    assert [r.id for r in gallery._records] == [rec.id]


@gtk_required
def test_remove_record_updates_starred_chip_when_last_starred_record_removed(tmp_path):
    """Mirrors the old _on_detail_deleted's chip-refresh behavior -- removing
    the only starred record should make the "⭐ Starred" chip disappear."""
    from artgen_gallery import ArtgenGallery, _STARRED_FILTER
    import media_store as ms_mod

    rec = _make_media_record(tmp_path, starred=1)
    ms_mod.media_store.add(rec)

    gallery = ArtgenGallery()
    gallery.refresh()

    labels_before = []
    child = gallery._chip_box.get_first_child()
    while child is not None:
        labels_before.append(child.get_label())
        child = child.get_next_sibling()
    assert any("Starred" in lbl for lbl in labels_before)

    gallery.remove_record(rec.id)

    labels_after = []
    child = gallery._chip_box.get_first_child()
    while child is not None:
        labels_after.append(child.get_label())
        child = child.get_next_sibling()
    assert not any("Starred" in lbl for lbl in labels_after)


# ── Source-level: grid's own hover-delete routes through remove_record ─────

def test_delete_confirmed_calls_remove_record_not_duplicated_logic():
    """The card's own hover 🗑 -> confirm dialog path (`_delete_confirmed`
    inside `_make_card`) should route through the public `remove_record`
    rather than duplicating the list-prune/rebuild logic inline."""
    start = _SRC.index("def _delete_confirmed(dialog, result, media_id):")
    end = _SRC.index("\n        del_btn.connect", start)
    body = _SRC[start:end]
    assert "self.remove_record(media_id)" in body
    # The old inline duplication of remove_record's body must be gone here.
    assert "self._records = [r for r in self._records if r.id != media_id]" not in body
