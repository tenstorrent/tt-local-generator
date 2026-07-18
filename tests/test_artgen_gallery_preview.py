"""
Regression tests: clicking an artgen card must open an in-page preview.

Bug (SP-3d-5, commit ff1d246): deleting `ArtgenPanel` in favor of the
standalone `ArtgenGallery` dropped the `on_card_activated` wiring that used
to switch a sub-stack to `ArtgenDetail`. `ArtgenGallery._on_card_activated`
fires on click but only forwards to `self.on_card_activated` -- which
`main_window.py` never sets for the artgen gallery -- so clicks silently did
nothing and `ArtgenDetail` became an orphaned, untested class.

Fix: `ArtgenGallery` now owns an internal Gtk.Stack ("grid" / "detail") and
shows its own `ArtgenDetail` by default on activation, while still calling
any externally-wired `on_card_activated` (additive, not exclusive).

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


def _pump():
    """Drain pending GLib idle callbacks.

    `ArtgenGallery._on_card_activated` DEFERS the grid->detail switch to a
    `GLib.idle_add` callback (crash fix v0.48.2 -- switching synchronously
    inside the FlowBox `child-activated` handler unmapped the just-activated
    card mid-geometry-pass, aborting with a `gtk_widget_compute_point`
    assertion). So the switch is observable only after the idle queue runs.
    """
    from gi.repository import GLib
    ctx = GLib.MainContext.default()
    while ctx.pending():
        ctx.iteration(False)


def _make_media_record(tmp_path: Path, **kwargs) -> "object":
    """Build a MediaRecord backed by a real .txt file on disk (verse-style
    content), so ArtgenDetail._render's fp.read_text() call has something
    real to read."""
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


@gtk_required
def test_activating_a_card_opens_detail_page_with_no_external_wiring(tmp_path):
    """THE regression: a fresh ArtgenGallery with NO on_card_activated wired
    (exactly how main_window.py constructs it today) must still open the
    in-page preview on click -- this is the actual bug."""
    import media_store as ms_mod
    from artgen_gallery import ArtgenGallery

    rec = _make_media_record(tmp_path)
    ms_mod.media_store.add(rec)

    gallery = ArtgenGallery()
    assert gallery.on_card_activated is None, "test assumes no external wiring"
    gallery.refresh()

    assert gallery._stack.get_visible_child_name() == "grid"

    child = _find_child_for(gallery, rec.id)
    assert child is not None, "expected a FlowBoxChild for the seeded record"

    gallery._on_card_activated(gallery._flow, child)
    _pump()

    assert gallery._stack.get_visible_child_name() == "detail", (
        "activating a card must switch the internal stack to the detail page"
    )
    assert gallery._detail._records[gallery._detail._idx].id == rec.id, (
        "ArtgenDetail must be showing the activated record"
    )


@gtk_required
def test_card_activation_defers_stack_switch_to_idle(tmp_path):
    """Crash fix (v0.48.2): the grid->detail switch must NOT happen
    synchronously inside the `child-activated` handler -- doing so unmaps the
    just-activated card while GTK is still computing its geometry, aborting
    with `gtk_widget_compute_point: assertion 'GTK_IS_WIDGET (widget)'`.
    So immediately after `_on_card_activated` the grid is still shown; only
    after the idle queue drains does it switch to detail."""
    import media_store as ms_mod
    from artgen_gallery import ArtgenGallery

    rec = _make_media_record(tmp_path)
    ms_mod.media_store.add(rec)

    gallery = ArtgenGallery()
    gallery.refresh()
    child = _find_child_for(gallery, rec.id)

    gallery._on_card_activated(gallery._flow, child)
    # Synchronous: still on the grid (the switch was deferred, not run inline).
    assert gallery._stack.get_visible_child_name() == "grid", (
        "the stack switch must be deferred out of the child-activated handler"
    )
    _pump()
    assert gallery._stack.get_visible_child_name() == "detail"


@gtk_required
def test_on_back_returns_to_grid_page(tmp_path):
    import media_store as ms_mod
    from artgen_gallery import ArtgenGallery

    rec = _make_media_record(tmp_path)
    ms_mod.media_store.add(rec)

    gallery = ArtgenGallery()
    gallery.refresh()
    child = _find_child_for(gallery, rec.id)
    gallery._on_card_activated(gallery._flow, child)
    _pump()
    assert gallery._stack.get_visible_child_name() == "detail"

    gallery._detail.on_back()

    assert gallery._stack.get_visible_child_name() == "grid"


@gtk_required
def test_external_on_card_activated_still_fires_additively(tmp_path):
    """Public API preserved: if something DOES wire on_card_activated, it
    must still be called (additive to, not replaced by, the internal
    preview)."""
    import media_store as ms_mod
    from artgen_gallery import ArtgenGallery

    rec = _make_media_record(tmp_path)
    ms_mod.media_store.add(rec)

    gallery = ArtgenGallery()
    seen = []
    gallery.on_card_activated = lambda media_id: seen.append(media_id)
    gallery.refresh()

    child = _find_child_for(gallery, rec.id)
    gallery._on_card_activated(gallery._flow, child)
    _pump()

    assert seen == [rec.id], "external on_card_activated callback must still fire"
    assert gallery._stack.get_visible_child_name() == "detail", (
        "internal preview must still open even with external wiring present"
    )


@gtk_required
def test_activation_respects_active_filter_for_detail_record_list(tmp_path):
    """show_record's second argument (the nav list for </> stepping) must be
    the FILTERED records, matching what _rebuild_grid shows -- not the full
    unfiltered self._records."""
    import media_store as ms_mod
    from artgen_gallery import ArtgenGallery

    rec_a = _make_media_record(tmp_path, generator_type="verse")
    rec_b = _make_media_record(tmp_path, generator_type="landscape")
    ms_mod.media_store.add(rec_a)
    ms_mod.media_store.add(rec_b)

    gallery = ArtgenGallery()
    gallery.refresh()
    gallery._active_filter = "verse"
    gallery._rebuild_grid()

    child = _find_child_for(gallery, rec_a.id)
    assert child is not None
    gallery._on_card_activated(gallery._flow, child)
    _pump()

    ids_in_detail = [r.id for r in gallery._detail._records]
    assert ids_in_detail == [rec_a.id], (
        f"detail nav list should be filtered to just the 'verse' record, got {ids_in_detail}"
    )
