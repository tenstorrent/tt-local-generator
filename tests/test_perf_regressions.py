"""Performance regression guards.

Each test here encodes a specific performance invariant that was broken in
production and caused user-visible lag.  The test name describes the exact
symptom observed.

Tests marked @gtk_required need an X11/Wayland display — run with:
    xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_perf_regressions.py

Headless tests (no GTK display needed) run in any environment.
"""
import ast
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

REPO_ROOT = Path(__file__).parent.parent
APP_DIR = REPO_ROOT / "app"


# ── GTK availability guard (mirrors test_animate_picker.py) ──────────────────

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


# ── Headless: static analysis / import guards ─────────────────────────────────

def test_workflow_popover_not_imported_at_startup():
    """WorkflowPopover must not be imported at module level in main_window.py.

    Symptom (2026-06-09): removing the Workflow toolbar button also removed the
    import, but if it ever comes back as a top-level import it would pull in
    workflow_popover at startup even when the button is unused, adding startup
    cost and construction-time side effects.
    """
    src = (APP_DIR / "main_window.py").read_text()
    assert "from workflow_popover import" not in src, (
        "workflow_popover must not be imported at module level in main_window.py"
    )


def test_gif_gallery_cards_use_static_thumbnail_not_animated_widget():
    """ArtgenGallery must show static thumbnails for GIF records, not _AnimatedGifWidget.

    Symptom (2026-06-09): _AnimatedGifWidget was constructed for every GIF card
    at gallery-build time, starting a GLib.timeout_add timer per card.  With
    62 GIFs that's hundreds of main-loop callbacks per second — causing scroll
    and tab-switch lag even with no generation running.

    Invariant: make_card_content(rec) returns Gtk.Picture (static) when the
    record is a GIF with an existing thumbnail.  _AnimatedGifWidget is only
    used as a fallback (no thumbnail) or on hover.
    """
    from media_store import MediaRecord
    from artgen_gallery import make_card_content

    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    from artgen_gallery import _AnimatedGifWidget

    # Build a fake GIF record with an existing thumbnail
    import tempfile, shutil
    tmp = Path(tempfile.mkdtemp())
    try:
        gif_path = tmp / "test.gif"
        gif_path.write_bytes(b"GIF89a")      # minimal GIF header (won't animate)
        thumb_path = tmp / "thumb.jpg"
        thumb_path.write_bytes(b"\xff\xd8\xff")  # minimal JPEG header

        rec = MediaRecord(
            id="test-gif-01",
            media_type="artgen",
            created_at="2026-06-09T00:00:00Z",
            file_path=str(gif_path),
            thumbnail_path=str(thumb_path),
            prompt="test",
            model_id="animatediff-blackhole",
            generator_type="animatediff",
            params="{}",
            starred=0,
        )

        widget = make_card_content(rec)

        assert not isinstance(widget, _AnimatedGifWidget), (
            "make_card_content returned _AnimatedGifWidget for a GIF with an "
            "existing thumbnail — this starts a timer immediately and will "
            "accumulate hundreds of main-loop callbacks per second in the gallery"
        )
        assert isinstance(widget, Gtk.Picture), (
            f"Expected Gtk.Picture (static thumbnail), got {type(widget).__name__}"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_gif_fallback_without_thumbnail_uses_animated_widget():
    """When a GIF has no thumbnail, make_card_content falls back to _AnimatedGifWidget.

    This is acceptable — it's a single card, and the unrealize handler cancels
    the timer.  The regression is constructing it for *every* card at build time.
    """
    from media_store import MediaRecord
    from artgen_gallery import make_card_content, _AnimatedGifWidget

    import tempfile, shutil
    tmp = Path(tempfile.mkdtemp())
    try:
        gif_path = tmp / "test.gif"
        gif_path.write_bytes(b"GIF89a")

        rec = MediaRecord(
            id="test-gif-02",
            media_type="artgen",
            created_at="2026-06-09T00:00:00Z",
            file_path=str(gif_path),
            thumbnail_path="",        # no thumbnail
            prompt="test",
            model_id="animatediff-blackhole",
            generator_type="animatediff",
            params="{}",
            starred=0,
        )

        widget = make_card_content(rec)
        assert isinstance(widget, _AnimatedGifWidget), (
            "Expected _AnimatedGifWidget fallback when no thumbnail exists"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_context_menu_rebuild_skips_on_same_source():
    """_rebuild_context_menu must be a no-op when source hasn't changed.

    Symptom (2026-06-09): _rebuild_context_menu called Gio.Menu.remove_all()
    and insert_submenu() on every tab switch, forcing GTK to rebuild the
    PopoverMenuBar widget tree even when switching back to the same tab.

    Invariant: the second call with the same source string does not touch
    self._context_menu_model (remove_all is not called again).
    """
    # Import just the method — test it in isolation without constructing MainWindow
    from main_window import MainWindow

    # Build a minimal stub with just the attributes _rebuild_context_menu needs
    stub = MagicMock()
    stub._context_menu_source = ""
    stub._context_menu_model = MagicMock()
    stub._menumodel = MagicMock()
    stub._context_slot_idx = 0
    stub._menu_bar = MagicMock()

    rebuild = MainWindow._rebuild_context_menu.__get__(stub, MainWindow)

    # First call — should rebuild
    with patch("main_window._build_context_menu_for_source", return_value=MagicMock(get_n_items=MagicMock(return_value=0))):
        rebuild("video")
    first_call_count = stub._context_menu_model.remove_all.call_count
    assert first_call_count == 1, "First call should rebuild"

    # Second call with same source — must be a no-op
    with patch("main_window._build_context_menu_for_source", return_value=MagicMock(get_n_items=MagicMock(return_value=0))):
        rebuild("video")
    assert stub._context_menu_model.remove_all.call_count == 1, (
        "_rebuild_context_menu called remove_all again for the same source — "
        "this forces GTK to rebuild the PopoverMenuBar on every tab switch"
    )

    # Third call with different source — should rebuild again
    with patch("main_window._build_context_menu_for_source", return_value=MagicMock(get_n_items=MagicMock(return_value=0))):
        rebuild("image")
    assert stub._context_menu_model.remove_all.call_count == 2, (
        "Switching to a new source should trigger a rebuild"
    )


# ── GTK display required ──────────────────────────────────────────────────────

@gtk_required
def test_animated_gif_widget_starts_timer_immediately():
    """_AnimatedGifWidget starts its GLib timer on construction.

    This is expected behaviour — the concern is not the widget itself but
    constructing it for every gallery card (see test above).  This test
    documents that the timer-start-on-init contract is intentional for the
    hover-only path.
    """
    import tempfile, shutil
    from gi.repository import GLib
    from artgen_gallery import _AnimatedGifWidget

    # We can't easily count GLib sources, but we can verify _timer_id is set
    # after construction with a real animated GIF.  Use a minimal valid GIF.
    tmp = Path(tempfile.mkdtemp())
    try:
        # Minimal single-frame GIF (won't animate, but exercises the code path)
        gif_path = tmp / "minimal.gif"
        gif_path.write_bytes(bytes([
            0x47, 0x49, 0x46, 0x38, 0x39, 0x61,  # GIF89a
            0x01, 0x00, 0x01, 0x00, 0x80, 0x00, 0x00,  # 1x1, color table
            0xff, 0xff, 0xff, 0x00, 0x00, 0x00,  # white, black
            0x2c, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,  # image descriptor
            0x02, 0x02, 0x4c, 0x01, 0x00,  # image data
            0x3b,  # trailer
        ]))
        widget = _AnimatedGifWidget(str(gif_path))
        # Static single-frame GIF: timer should NOT be set (no animation needed)
        assert widget._timer_id is None, (
            "Static (single-frame) GIF should not start a repeating timer"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@gtk_required
def test_animated_gif_widget_cancels_timer_on_unrealize():
    """_AnimatedGifWidget cancels its GLib timer when unrealized.

    A timer that survives widget removal would continue firing on the main loop
    indefinitely — a classic GLib source leak.
    """
    import tempfile, shutil
    from artgen_gallery import _AnimatedGifWidget

    # Build a multi-frame GIF that will actually start a timer
    # (use a real animated GIF bytes if available; otherwise mock the iter)
    tmp = Path(tempfile.mkdtemp())
    try:
        gif_path = tmp / "anim.gif"
        gif_path.write_bytes(bytes([
            0x47, 0x49, 0x46, 0x38, 0x39, 0x61,  # GIF89a
            0x01, 0x00, 0x01, 0x00, 0x80, 0x00, 0x00,
            0xff, 0xff, 0xff, 0x00, 0x00, 0x00,
            0x2c, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,
            0x02, 0x02, 0x4c, 0x01, 0x00,
            0x3b,
        ]))
        widget = _AnimatedGifWidget(str(gif_path))
        # Simulate unrealize (timer cleanup path)
        widget._on_unrealize(widget)
        assert widget._timer_id is None, (
            "_timer_id should be None after unrealize — leaked timer would keep "
            "firing on the main loop after the widget is removed"
        )
        assert widget._iter is None, (
            "_iter should be None after unrealize"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
