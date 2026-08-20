#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Regression tests for the TT-TV attractor's media-showcase-everywhere fixes
(task 3 of the 2026-07-17 media-showcase-everywhere plan):

  1. ANSI artifacts in the CURRENT fg+block escape format
     (`\\x1b[38;5;Nm█`) must render as a color grid, not fall back to raw
     text -- `attractor._parse_ansi_grid` only understood the legacy
     bg+space form, so every ANSI artifact the `ansi` generator produces
     today rendered as escape-code gibberish.
  2. Artgen AnimateDiff `.gif` artifacts must animate (`AnimatedGifWidget`),
     not freeze on frame 1 via the "unknown extension -> static thumbnail"
     fallback.
  3. Native `media_type == "animatediff"` `.gif` records must also animate
     via the shared GdkPixbufAnimationIter widget, not the fragile
     GStreamer `Gtk.Video` path.

These need a real GTK4 display (xvfb in CI) to construct real widgets, so
each test self-skips when PIL, a display, or GTK4 aren't available -- same
guard pattern as test_artgen_render.py's AnimatedGifWidget test.
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

# Ensure the system PyGObject package is importable inside the venv.
_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)


def _require_gtk_display():
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        pytest.skip("no GTK4 display available")
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk  # noqa: F401
    except Exception:
        pytest.skip("GTK4 not available")


def _make_gif(tmp_path, name="art.gif"):
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("PIL not available")
    gif_path = tmp_path / name
    frame1 = Image.new("RGB", (4, 4), (255, 0, 0))
    frame2 = Image.new("RGB", (4, 4), (0, 255, 0))
    frame1.save(gif_path, save_all=True, append_images=[frame2], duration=50, loop=0)
    return gif_path


# ── Bug 1: ANSI fg+block must parse to a non-empty color grid ───────────────

def test_load_artgen_ansi_fg_block_format_renders_color_grid_not_raw_text(tmp_path):
    """The CURRENT ansi generator format (\\x1b[38;5;Nm█) must produce a
    populated _AnsiCanvas, not the raw-text fallback (the live bug)."""
    _require_gtk_display()
    import attractor
    from gi.repository import Gtk

    ans_path = tmp_path / "art.ans"
    # index 196 -> pure red, index 46 -> pure green (per xterm-256 cube).
    ans_path.write_text("\x1b[38;5;196m█\x1b[38;5;46m█\n", encoding="utf-8")

    box = Gtk.Box()
    attractor._load_artgen_ansi(box, str(ans_path))

    child = box.get_first_child()
    assert isinstance(child, attractor._AnsiCanvas), (
        "expected the ANSI canvas widget, not a raw-text fallback label"
    )
    assert child._rows, "expected non-empty parsed rows for fg+block ANSI"
    assert child._rows[0][0] == (255, 0, 0)
    assert child._rows[0][1] == (0, 255, 0)


def test_load_artgen_ansi_unparseable_falls_back_to_text(tmp_path):
    """Spot-check the fallback path still works for genuinely unparseable input."""
    _require_gtk_display()
    import attractor
    from gi.repository import Gtk

    ans_path = tmp_path / "empty.ans"
    ans_path.write_text("", encoding="utf-8")

    box = Gtk.Box()
    attractor._load_artgen_ansi(box, str(ans_path))

    child = box.get_first_child()
    assert not isinstance(child, attractor._AnsiCanvas)


# ── Bug 2: artgen .gif must animate, not freeze on frame 1 ──────────────────

def test_load_slot_artgen_gif_animates_not_static(tmp_path):
    _require_gtk_display()
    import attractor
    from artgen_render import AnimatedGifWidget

    gif_path = _make_gif(tmp_path)

    slot = attractor.AttractorWindow._make_slot(None)
    record = MagicMock()
    record.media_type = "artgen"
    record.file_path = str(gif_path)
    record.thumbnail_path = ""

    attractor.AttractorWindow._load_slot(MagicMock(), slot, record)

    assert slot._text_box.get_visible()
    assert not slot._picture.get_visible()
    child = slot._text_box.get_first_child()
    assert isinstance(child, AnimatedGifWidget), (
        "artgen .gif must animate via AnimatedGifWidget, not show a static Picture"
    )


# ── Bug 3: native animatediff .gif must use GdkPixbuf, not Gtk.Video ────────

def test_load_slot_native_animatediff_gif_uses_gdkpixbuf_not_gtkvideo(tmp_path):
    _require_gtk_display()
    import attractor
    from artgen_render import AnimatedGifWidget

    gif_path = _make_gif(tmp_path, name="native.gif")

    slot = attractor.AttractorWindow._make_slot(None)
    record = MagicMock()
    record.media_type = "animatediff"
    record.video_path = str(gif_path)

    win_self = MagicMock()
    attractor.AttractorWindow._load_slot(win_self, slot, record)

    assert not slot._video.get_visible(), "the fragile Gtk.Video widget must stay hidden"
    assert slot._video.get_file() is None, "Gtk.Video must never be loaded for animatediff gif"
    child = slot._text_box.get_first_child()
    assert isinstance(child, AnimatedGifWidget), (
        "native animatediff .gif must animate via the shared GdkPixbuf path"
    )
    assert slot._text_box.get_visible()


# ── Spot-check: existing image behavior is unchanged ────────────────────────

def test_load_slot_image_media_type_unchanged(tmp_path):
    _require_gtk_display()
    import attractor

    png_path = tmp_path / "still.png"
    try:
        from PIL import Image
        Image.new("RGB", (4, 4), (0, 0, 255)).save(png_path)
    except ImportError:
        pytest.skip("PIL not available")

    slot = attractor.AttractorWindow._make_slot(None)
    record = MagicMock()
    record.media_type = "image"
    record.thumbnail_path = str(png_path)
    record.image_path = str(png_path)

    attractor.AttractorWindow._load_slot(MagicMock(), slot, record)

    assert slot._picture.get_visible()
    assert not slot._text_box.get_visible()
    assert not slot._video.get_visible()


# ── Bug: gif-decode timer must stop when a slot swaps away from a gif ──────
#
# AnimatedGifWidget only cancels its GLib.timeout_add decode timer on
# "unrealize" (see artgen_render.AnimatedGifWidget._on_unrealize). That signal
# only fires for a widget that was actually realized -- i.e. actually part of
# a shown/mapped window, not merely constructed in memory. So this test (unlike
# the others in this file) must present a real Gtk.Window and pump the GLib
# main context, or the assertions would pass trivially without exercising the
# real bug (set_visible(False) never unrealizes a widget either way).
def _pump(n=30):
    """Iterate the default GLib main context to let GTK realize/unrealize
    widgets and process idle callbacks."""
    from gi.repository import GLib
    ctx = GLib.MainContext.default()
    for _ in range(n):
        ctx.iteration(False)


def test_load_slot_swap_from_gif_to_image_stops_gif_timer(tmp_path):
    """Loading an image into a slot that last showed a gif must cancel the
    prior AnimatedGifWidget's decode timer, not just hide it via
    set_visible(False) (which does not unrealize the child)."""
    _require_gtk_display()
    import attractor
    from artgen_render import AnimatedGifWidget
    from gi.repository import Gtk

    gif_path = _make_gif(tmp_path)
    png_path = tmp_path / "still.png"
    try:
        from PIL import Image
        Image.new("RGB", (4, 4), (0, 0, 255)).save(png_path)
    except ImportError:
        pytest.skip("PIL not available")

    slot = attractor.AttractorWindow._make_slot(None)

    # Present the slot in a real window so realize/unrealize actually fire
    # (a bare Gtk.Box() that's never shown never realizes its children).
    win = Gtk.Window()
    win.set_child(slot)
    win.present()
    _pump()

    # 1) Load the gif -- an AnimatedGifWidget with a live timer gets parented
    #    into slot._text_box.
    gif_record = MagicMock()
    gif_record.media_type = "animatediff"
    gif_record.video_path = str(gif_path)
    attractor.AttractorWindow._load_slot(MagicMock(), slot, gif_record)
    _pump()

    gif_widget = slot._text_box.get_first_child()
    assert isinstance(gif_widget, AnimatedGifWidget)
    assert gif_widget._timer_id is not None, (
        "expected a running gif-decode timer after loading the gif"
    )

    # 2) Load an image into the SAME slot.
    image_record = MagicMock()
    image_record.media_type = "image"
    image_record.thumbnail_path = str(png_path)
    image_record.image_path = str(png_path)
    attractor.AttractorWindow._load_slot(MagicMock(), slot, image_record)
    _pump()

    # The old gif widget must be fully torn down: unrealized (timer
    # cancelled) and no longer parented in the slot at all.
    assert gif_widget._timer_id is None, (
        "gif-decode timer must be cancelled once the slot swaps to an image "
        "-- set_visible(False) alone does not unrealize the child, so the "
        "timer would otherwise keep firing on a hidden widget forever"
    )
    assert gif_widget.get_parent() is None, (
        "the old AnimatedGifWidget must be unparented, not merely hidden"
    )

    # The new image content must still load correctly.
    assert slot._picture.get_visible()
    assert not slot._text_box.get_visible()

    win.destroy()
    _pump()
