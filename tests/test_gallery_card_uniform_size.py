"""
Tests for uniform, fixed-size gallery cards across ALL Discover tabs
(video/image/animate GalleryWidget cards AND the artgen ArtgenGallery cards).

Bug: GenerationCard (main_window.py) set a fixed WIDTH but a natural HEIGHT
(-1), and its thumbnail zone only set a size_request MINIMUM via
_make_scalable_thumb — so cards grew/shrank to match thumbnail aspect ratio
(square image vs 16:9 video vs a tall text preview), and the artgen gallery
used a completely different tile size (110x90) and FlowBox grid settings
(min_children_per_line=3) than the native galleries (~220 wide, min=2) —
switching Discover tabs visibly changed the grid even for equivalent media.

Fix: gallery_layout.py is now the single source of truth for the tile size
(TILE_W/TILE_H), the 16:9 thumbnail-zone size (THUMB_W/THUMB_H), and the
FlowBox grid settings, shared by both galleries. `pin_fixed_zone()` makes a
zone's MEASURED (natural) size follow an invisible anchor instead of its
real content, so thumbnail/content aspect ratio can no longer change a
card's footprint.

Run under xvfb (GTK4 widgets need a real display):
    xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_gallery_card_uniform_size.py -q
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


# ── Record builders ────────────────────────────────────────────────────────────

def _make_record(**kwargs):
    from history_store import GenerationRecord
    base = dict(
        id=str(uuid.uuid4()),
        prompt="a test prompt " * 4,
        negative_prompt="",
        num_inference_steps=20,
        seed=42,
        video_path="",
        thumbnail_path="",
        created_at="2026-01-01T00:00:00+00:00",
        media_type="image",
        image_path="",
        model="flux",
        extra_meta={},
    )
    base.update(kwargs)
    return GenerationRecord(**base)


def _make_media_record(**kwargs):
    from media_store import MediaRecord
    base = dict(
        id=str(uuid.uuid4()),
        media_type="artgen",
        created_at="2026-01-01T00:00:00Z",
        file_path="",
        thumbnail_path="",
        prompt="a test artgen prompt",
        model_id="qwen3-8b",
        generator_type="landscape",
        params="{}",
        starred=0,
    )
    base.update(kwargs)
    return MediaRecord(**base)


def _make_square_png(path: Path, size: int = 1024) -> None:
    from PIL import Image
    Image.new("RGB", (size, size), (80, 120, 130)).save(path)


def _make_widescreen_jpg(path: Path, w: int = 1920, h: int = 1080) -> None:
    from PIL import Image
    Image.new("RGB", (w, h), (30, 60, 70)).save(path)


# ── Shared constants module ────────────────────────────────────────────────────

def test_gallery_layout_defines_shared_tile_constants():
    """gallery_layout.py is the single source of truth both galleries import."""
    import gallery_layout as gl
    assert gl.TILE_W > 0 and gl.TILE_H > 0
    assert gl.THUMB_W > 0 and gl.THUMB_H > 0
    assert gl.FLOW_MIN_CHILDREN_PER_LINE > 0
    assert gl.FLOW_MAX_CHILDREN_PER_LINE >= gl.FLOW_MIN_CHILDREN_PER_LINE


def test_tile_size_compact_scales_both_dimensions():
    """Density scaling must shrink BOTH width and height, not just width."""
    import gallery_layout as gl
    comfy_w, comfy_h = gl.tile_size("comfortable")
    compact_w, compact_h = gl.tile_size("compact")
    assert compact_w < comfy_w
    assert compact_h < comfy_h, (
        "compact density left height unchanged -- this is the original bug "
        "in _apply_gallery_density (set_size_request(card_w, -1))"
    )


# ── Card size uniformity: GenerationCard vs artgen card ───────────────────────

@gtk_required
def test_generation_card_and_artgen_card_report_same_fixed_size():
    """A native GenerationCard and an artgen card must be the SAME fixed tile size."""
    import main_window as mw
    from artgen_gallery import ArtgenGallery

    rec = _make_record()
    card = mw.GenerationCard(rec, select_cb=lambda *_: None, delete_cb=lambda *_: None)
    w, h = card.get_size_request()
    assert w > 0 and h > 0

    gallery = ArtgenGallery()
    artgen_rec = _make_media_record()
    artgen_card = gallery._make_card(artgen_rec)
    aw, ah = artgen_card.get_size_request()

    assert (w, h) == (aw, ah), (
        f"GenerationCard tile {(w, h)} != artgen card tile {(aw, ah)} -- "
        "cards must be identically sized across every Discover tab"
    )


@gtk_required
def test_generation_card_size_independent_of_thumbnail_content(tmp_path):
    """Square image, 16:9 thumbnail, and a missing thumbnail must all yield the
    SAME card size_request -- the card must not grow/shrink to match thumbnail
    aspect ratio."""
    import main_window as mw

    square_png = tmp_path / "square.png"
    _make_square_png(square_png)
    wide_jpg = tmp_path / "wide.jpg"
    _make_widescreen_jpg(wide_jpg)

    rec_square = _make_record(thumbnail_path=str(square_png), media_type="image")
    rec_wide = _make_record(thumbnail_path=str(wide_jpg), media_type="video")
    rec_missing = _make_record(thumbnail_path="", media_type="image")

    sizes = []
    for rec in (rec_square, rec_wide, rec_missing):
        card = mw.GenerationCard(rec, select_cb=lambda *_: None, delete_cb=lambda *_: None)
        sizes.append(card.get_size_request())

    assert len(set(sizes)) == 1, f"card sizes differ by thumbnail content: {sizes}"


@gtk_required
def test_generation_card_media_zone_measured_height_independent_of_aspect(tmp_path):
    """Real regression check (not just the literal size_request floor): the
    ACTUAL MEASURED natural height of the card's media/thumbnail zone must be
    identical for a square image vs a 16:9 image -- proving the fix caps the
    zone's natural size instead of merely setting a minimum that a taller
    natural size can still exceed."""
    import main_window as mw
    import gallery_layout as gl

    square_png = tmp_path / "square.png"
    _make_square_png(square_png)
    wide_jpg = tmp_path / "wide.jpg"
    _make_widescreen_jpg(wide_jpg)

    heights = []
    for thumb in (square_png, wide_jpg):
        rec = _make_record(thumbnail_path=str(thumb), media_type="image")
        card = mw.GenerationCard(rec, select_cb=lambda *_: None, delete_cb=lambda *_: None)
        zone = card._media_zone
        _min_h, nat_h, _, _ = zone.measure(mw.Gtk.Orientation.VERTICAL, gl.THUMB_W)
        heights.append(nat_h)

    assert len(set(heights)) == 1, (
        f"media zone natural height varies by thumbnail aspect ratio: {heights}"
    )


# ── Card size uniformity: within the artgen gallery ───────────────────────────

@gtk_required
def test_artgen_card_size_independent_of_content_aspect(tmp_path):
    """A square image record, a long text record, and an empty/placeholder
    record must all produce the SAME artgen card size_request."""
    from artgen_gallery import ArtgenGallery

    square_png = tmp_path / "square.png"
    _make_square_png(square_png, size=800)

    text_file = tmp_path / "note.txt"
    text_file.write_text("# Title\n\n" + ("word " * 400))

    gallery = ArtgenGallery()

    rec_img = _make_media_record(
        file_path=str(square_png), thumbnail_path=str(square_png),
        generator_type="landscape",
    )
    rec_text = _make_media_record(file_path=str(text_file), generator_type="verse")
    rec_empty = _make_media_record(generator_type="freeform")

    sizes = []
    for rec in (rec_img, rec_text, rec_empty):
        card = gallery._make_card(rec)
        sizes.append(card.get_size_request())

    assert len(set(sizes)) == 1, f"artgen card sizes differ by content: {sizes}"


@gtk_required
def test_artgen_gif_card_measured_size_stable_across_hover(tmp_path):
    """GIF cards swap in an animated widget on hover-enter and restore a
    static thumbnail on hover-leave (see ArtgenGallery._make_card's
    _enter_card/_leave_card).  This must swap content INSIDE the pinned
    content zone, not rip the zone itself out of the card -- otherwise the
    hovered widget's own aspect ratio can grow/shrink the card exactly like
    the pre-fix bug, just gated on mouse-over instead of content type."""
    from artgen_gallery import ArtgenGallery

    gif_path = tmp_path / "anim.gif"
    gif_path.write_bytes(bytes([
        0x47, 0x49, 0x46, 0x38, 0x39, 0x61,
        0x01, 0x00, 0x01, 0x00, 0x80, 0x00, 0x00,
        0xff, 0xff, 0xff, 0x00, 0x00, 0x00,
        0x2c, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,
        0x02, 0x02, 0x4c, 0x01, 0x00,
        0x3b,
    ]))
    thumb_path = tmp_path / "thumb.jpg"
    thumb_path.write_bytes(b"\xff\xd8\xff")

    rec = _make_media_record(
        file_path=str(gif_path), thumbnail_path=str(thumb_path),
        generator_type="animatediff",
    )

    gallery = ArtgenGallery()
    card = gallery._make_card(rec)

    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    motion = None
    for ctrl in card.observe_controllers():
        if isinstance(ctrl, Gtk.EventControllerMotion):
            motion = ctrl
            break
    assert motion is not None, "expected an EventControllerMotion on the card"

    before = card.get_size_request()
    motion.emit("enter", 0.0, 0.0)
    during_hover = card.get_size_request()
    motion.emit("leave")
    after = card.get_size_request()

    assert before == during_hover == after, (
        f"card size changed across hover: before={before} during={during_hover} after={after}"
    )


# ── FlowBox grid settings identical across galleries ──────────────────────────

@gtk_required
def test_flowbox_grid_settings_identical_across_galleries():
    """GalleryWidget's FlowBox and ArtgenGallery's FlowBox must use the SAME
    min/max children-per-line and row/column spacing so the grid itself
    (not just individual cards) looks identical when switching tabs."""
    import main_window as mw
    from artgen_gallery import ArtgenGallery

    native = mw.GalleryWidget(select_cb=lambda *_: None, delete_cb=lambda *_: None)
    artgen = ArtgenGallery()

    assert native._flow.get_min_children_per_line() == artgen._flow.get_min_children_per_line()
    assert native._flow.get_max_children_per_line() == artgen._flow.get_max_children_per_line()
    assert native._flow.get_row_spacing() == artgen._flow.get_row_spacing()
    assert native._flow.get_column_spacing() == artgen._flow.get_column_spacing()


# ── Density scaling applies uniformly to every gallery ────────────────────────

@gtk_required
def test_apply_gallery_density_scales_artgen_gallery_too():
    """A card built BEFORE the density switch (a fresh card would trivially
    pick up the new size at construction) must resize when set_tile_size()
    is called on an ALREADY-BUILT gallery -- and the resize must show up in
    the card's MEASURED size, not just get_size_request() (which only
    echoes back whatever was literally passed to set_size_request() and
    would pass even if the resize were a complete no-op, per the regression
    this test used to miss: the card's real content -- the pinned
    content_zone built at the OLD tile size -- silently kept dominating the
    measured size)."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    import gallery_layout as gl
    from artgen_gallery import ArtgenGallery

    gallery = ArtgenGallery()
    assert hasattr(gallery, "set_tile_size"), (
        "ArtgenGallery must expose set_tile_size(w, h) so MainWindow's "
        "_apply_gallery_density can keep it in sync with the native galleries"
    )

    rec = _make_media_record()
    card = gallery._make_card(rec)
    # Actually insert into the grid -- set_tile_size() walks self._flow's
    # children, so an orphan card built via _make_card() alone (never
    # inserted) would silently be skipped and this test would pass for the
    # wrong reason.
    gallery._flow.append(card)

    comfy_w, comfy_h = gl.tile_size("comfortable")
    min_w, _, _, _ = card.measure(Gtk.Orientation.HORIZONTAL, -1)
    min_h, _, _, _ = card.measure(Gtk.Orientation.VERTICAL, comfy_w)
    assert (min_w, min_h) == (comfy_w, comfy_h), "card wasn't comfortable-sized before the switch"

    compact_w, compact_h = gl.tile_size("compact")
    gallery.set_tile_size(compact_w, compact_h)

    min_w, _, _, _ = card.measure(Gtk.Orientation.HORIZONTAL, -1)
    min_h, _, _, _ = card.measure(Gtk.Orientation.VERTICAL, compact_w)
    assert (min_w, min_h) == (compact_w, compact_h), (
        f"already-built artgen card did not shrink to compact: measured "
        f"({min_w}, {min_h}) != target ({compact_w}, {compact_h})"
    )


@gtk_required
def test_apply_gallery_density_resizes_already_built_generation_card():
    """The real bug: MainWindow._apply_gallery_density (called when the user
    toggles density in the View menu) must resize a GenerationCard that was
    already built and already showing in the gallery -- not just affect
    cards built afterward. Exercises the actual method via a bare
    MainWindow.__new__() instance (the established pattern for unit-testing
    MainWindow methods without a full app -- see test_main_window_decouple.py
    / test_forge_transforms.py), so a regression in the real code path (not
    a hand-rolled reimplementation) is what gets caught."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    import main_window as mw
    import gallery_layout as gl
    from app_settings import settings as _s

    saved_density = _s.get("gallery_density")
    try:
        _s.set("gallery_density", "comfortable")
        rec = _make_record()
        card = mw.GenerationCard(rec, select_cb=lambda *_: None, delete_cb=lambda *_: None)

        video_gallery = mw.GalleryWidget(select_cb=lambda *_: None, delete_cb=lambda *_: None,
                                          media_type="video")
        video_gallery._cards.append(card)
        image_gallery = mw.GalleryWidget(select_cb=lambda *_: None, delete_cb=lambda *_: None,
                                          media_type="image")
        animate_gallery = mw.GalleryWidget(select_cb=lambda *_: None, delete_cb=lambda *_: None,
                                            media_type="animate")

        obj = mw.MainWindow.__new__(mw.MainWindow)
        obj._video_gallery = video_gallery
        obj._image_gallery = image_gallery
        obj._animate_gallery = animate_gallery
        # No self._artgen_gallery -- _apply_gallery_density must tolerate that
        # (getattr(..., None) guard) exactly like it does in the real app
        # before Discover's artgen page is constructed.

        comfy_w, comfy_h = gl.tile_size("comfortable")
        min_w, _, _, _ = card.measure(Gtk.Orientation.HORIZONTAL, -1)
        min_h, _, _, _ = card.measure(Gtk.Orientation.VERTICAL, comfy_w)
        assert (min_w, min_h) == (comfy_w, comfy_h)

        obj._apply_gallery_density("compact")

        compact_w, compact_h = gl.tile_size("compact")
        min_w, _, _, _ = card.measure(Gtk.Orientation.HORIZONTAL, -1)
        min_h, _, _, _ = card.measure(Gtk.Orientation.VERTICAL, compact_w)
        assert (min_w, min_h) == (compact_w, compact_h), (
            f"already-built GenerationCard did not shrink to compact: "
            f"measured ({min_w}, {min_h}) != target ({compact_w}, {compact_h})"
        )

        # Round trip back to comfortable -- must return to the EXACT original
        # measured size, not some other value settle from the compact pass.
        obj._apply_gallery_density("comfortable")
        min_w, _, _, _ = card.measure(Gtk.Orientation.HORIZONTAL, -1)
        min_h, _, _, _ = card.measure(Gtk.Orientation.VERTICAL, comfy_w)
        assert (min_w, min_h) == (comfy_w, comfy_h), (
            f"round-trip compact->comfortable did not restore the exact "
            f"original size: measured ({min_w}, {min_h}) != ({comfy_w}, {comfy_h})"
        )
    finally:
        _s.set("gallery_density", saved_density)


@gtk_required
def test_generation_card_constructed_while_density_compact_measures_compact():
    """A GenerationCard built WHILE density is already "compact" (e.g. a new
    generation completing after the user already switched density) must be
    compact-sized from construction -- previously every new card was born
    comfortable-sized regardless of the saved preference, only ever fixed
    up (and even that was broken) for cards that existed before the
    switch."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    import main_window as mw
    import gallery_layout as gl
    from app_settings import settings as _s

    saved_density = _s.get("gallery_density")
    try:
        _s.set("gallery_density", "compact")
        rec = _make_record()
        card = mw.GenerationCard(rec, select_cb=lambda *_: None, delete_cb=lambda *_: None)

        compact_w, compact_h = gl.tile_size("compact")
        min_w, _, _, _ = card.measure(Gtk.Orientation.HORIZONTAL, -1)
        min_h, _, _, _ = card.measure(Gtk.Orientation.VERTICAL, compact_w)
        assert (min_w, min_h) == (compact_w, compact_h), (
            f"GenerationCard built under compact density measured "
            f"({min_w}, {min_h}) instead of the compact target "
            f"({compact_w}, {compact_h})"
        )

        pending = mw.PendingCard(prompt="test", model_source="video")
        min_w, _, _, _ = pending.measure(Gtk.Orientation.HORIZONTAL, -1)
        min_h, _, _, _ = pending.measure(Gtk.Orientation.VERTICAL, compact_w)
        assert (min_w, min_h) == (compact_w, compact_h), (
            f"PendingCard built under compact density measured "
            f"({min_w}, {min_h}) instead of the compact target "
            f"({compact_w}, {compact_h})"
        )
    finally:
        _s.set("gallery_density", saved_density)


@gtk_required
def test_apply_gallery_density_resizes_already_built_artgen_card_via_main_window():
    """Same as test_apply_gallery_density_resizes_already_built_generation_card
    but through the artgen path -- MainWindow._apply_gallery_density's call
    to ArtgenGallery.set_tile_size() must also land on an already-built card."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    import main_window as mw
    import gallery_layout as gl
    from artgen_gallery import ArtgenGallery

    video_gallery = mw.GalleryWidget(select_cb=lambda *_: None, delete_cb=lambda *_: None,
                                      media_type="video")
    image_gallery = mw.GalleryWidget(select_cb=lambda *_: None, delete_cb=lambda *_: None,
                                      media_type="image")
    animate_gallery = mw.GalleryWidget(select_cb=lambda *_: None, delete_cb=lambda *_: None,
                                        media_type="animate")

    artgen_gallery = ArtgenGallery()
    rec = _make_media_record()
    card = artgen_gallery._make_card(rec)
    artgen_gallery._flow.append(card)

    obj = mw.MainWindow.__new__(mw.MainWindow)
    obj._video_gallery = video_gallery
    obj._image_gallery = image_gallery
    obj._animate_gallery = animate_gallery
    obj._artgen_gallery = artgen_gallery

    obj._apply_gallery_density("compact")

    compact_w, compact_h = gl.tile_size("compact")
    min_w, _, _, _ = card.measure(Gtk.Orientation.HORIZONTAL, -1)
    min_h, _, _, _ = card.measure(Gtk.Orientation.VERTICAL, compact_w)
    assert (min_w, min_h) == (compact_w, compact_h)


# ── gallery_layout.set_pinned_size() unit tests ───────────────────────────────

@gtk_required
def test_set_pinned_size_changes_the_zones_measured_size():
    """The core mechanism this whole fix depends on: resizing a
    pin_fixed_zone()'s anchor must change what the ZONE measures as, not
    just what get_size_request() echoes back."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    import gallery_layout as gl

    child = Gtk.Box()
    zone = gl.pin_fixed_zone(child, 200, 100)

    min_w, _, _, _ = zone.measure(Gtk.Orientation.HORIZONTAL, -1)
    min_h, _, _, _ = zone.measure(Gtk.Orientation.VERTICAL, 200)
    assert (min_w, min_h) == (200, 100)

    gl.set_pinned_size(zone, 80, 40)

    min_w, _, _, _ = zone.measure(Gtk.Orientation.HORIZONTAL, -1)
    min_h, _, _, _ = zone.measure(Gtk.Orientation.VERTICAL, 80)
    assert (min_w, min_h) == (80, 40), (
        "set_pinned_size() must change the zone's MEASURED size -- the bug "
        "this whole fix targets is set_size_request() on the wrong widget "
        "raising only a floor that a stale anchor still exceeds"
    )


@gtk_required
def test_set_pinned_size_rejects_a_zone_not_built_via_pin_fixed_zone():
    """A plain Gtk.Overlay (never wrapped via pin_fixed_zone) has no stashed
    anchor -- set_pinned_size must fail loudly rather than silently no-op,
    so a future caller mistake is caught immediately instead of quietly
    reproducing this exact bug."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    import gallery_layout as gl

    plain_overlay = Gtk.Overlay()
    try:
        gl.set_pinned_size(plain_overlay, 10, 10)
        assert False, "expected ValueError for a non-pinned zone"
    except ValueError:
        pass
