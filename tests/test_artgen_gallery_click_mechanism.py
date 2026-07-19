"""
Tests for "unify gallery interaction" Task 6 -- ArtgenGallery's card click
mechanism becomes GestureClick-based (SelectionMode.NONE + one GestureClick
per card handling both single and double click), replacing FlowBox
SelectionMode.SINGLE + "child-activated" (single-click-only). Mirrors
GenerationCard._on_pressed (main_window.py, Task 4) so a click means the same
thing on every gallery in the app: single click selects (right pane, via
on_card_activated), double click opens the record full-screen
(ArtgenViewerWindow, Task 5).

Written FIRST per the Task 6 brief, before app/artgen_gallery.py was changed:
the FlowBox+gesture interaction was flagged as the highest risk part of this
change -- does SelectionMode.NONE still deliver a "pressed" emission to a
per-child GestureClick, the same way it does for the native galleries' plain
Gtk.Box cards (which were never inside a SelectionMode.SINGLE FlowBox to
begin with)? These tests answer that empirically by firing the signal
through the REAL, connected GestureClick controller (found via
observe_controllers(), not by calling a private handler method directly) --
mirroring test_gallery_double_click_fullscreen.py's
test_primary_gesture_wired_to_on_pressed, which does the same kind of
real-wiring check for the native GenerationCard.

Run under xvfb (GTK4 widgets need a real display):
    xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_artgen_gallery_click_mechanism.py -v
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


def _make_media_record(tmp_path: Path, **kwargs) -> "object":
    """Build a MediaRecord backed by a real .txt file on disk, mirroring
    test_artgen_gallery_preview.py's helper."""
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


def _find_overlay_for(gallery, media_id: str):
    """Walk the FlowBox to find the card Overlay (FlowBox wraps it in a
    FlowBoxChild) whose stashed `_media_id` matches."""
    child = gallery._flow.get_first_child()
    while child is not None:
        overlay = child.get_child()
        if overlay is not None and getattr(overlay, "_media_id", None) == media_id:
            return overlay
        child = child.get_next_sibling()
    return None


def _primary_gesture(overlay):
    """Find the card's real, connected GestureClick controller on the
    overlay via observe_controllers() -- exercising the actual wiring
    (add_controller/connect) rather than calling a bound handler method
    directly."""
    import gi
    from gi.repository import Gtk
    found = [c for c in overlay.observe_controllers() if isinstance(c, Gtk.GestureClick)]
    assert found, "expected a GestureClick controller on the card overlay"
    return found[0]


# ── The flagged risk: does SelectionMode.NONE still deliver clicks? ────────

@gtk_required
def test_flow_uses_selection_mode_none():
    from artgen_gallery import ArtgenGallery
    import gi
    from gi.repository import Gtk

    gallery = ArtgenGallery()
    assert gallery._flow.get_selection_mode() == Gtk.SelectionMode.NONE


@gtk_required
def test_single_click_selects_via_real_gesture_and_does_not_open_viewer(tmp_path, monkeypatch):
    """Fire n_press=1 through the ACTUAL connected GestureClick signal (found
    via observe_controllers(), not a direct Python call to a handler method)
    to prove SelectionMode.NONE still lets a per-card gesture receive a
    "pressed" emission under a FlowBox -- the flagged risk."""
    import media_store as ms_mod
    import artgen_gallery as ag_mod
    from artgen_gallery import ArtgenGallery

    opened = []
    monkeypatch.setattr(
        ag_mod, "ArtgenViewerWindow",
        lambda *a, **k: opened.append(a) or pytest.fail("must not open a viewer on single click"),
    )

    rec = _make_media_record(tmp_path)
    ms_mod.media_store.add(rec)

    gallery = ArtgenGallery()
    seen = []
    gallery.on_card_activated = lambda media_id: seen.append(media_id)
    gallery.refresh()

    overlay = _find_overlay_for(gallery, rec.id)
    assert overlay is not None, "expected a card overlay for the seeded record"
    gesture = _primary_gesture(overlay)

    gesture.emit("pressed", 1, 0.0, 0.0)

    assert seen == [rec.id]
    assert opened == []


@gtk_required
def test_double_click_opens_artgen_viewer_window_via_real_gesture(tmp_path, monkeypatch):
    """n_press=2 through the real gesture opens ArtgenViewerWindow with this
    card's record, and single-click selection still also fires (mirroring
    GenerationCard._on_pressed: _select_cb fires on every press)."""
    import media_store as ms_mod
    import artgen_gallery as ag_mod
    from artgen_gallery import ArtgenGallery

    calls = []

    class _FakeWin:
        def __init__(self, record, parent):
            calls.append((record, parent))

        def present(self):
            pass

    monkeypatch.setattr(ag_mod, "ArtgenViewerWindow", _FakeWin)

    rec = _make_media_record(tmp_path)
    ms_mod.media_store.add(rec)

    gallery = ArtgenGallery()
    seen = []
    gallery.on_card_activated = lambda media_id: seen.append(media_id)
    gallery.refresh()

    overlay = _find_overlay_for(gallery, rec.id)
    gesture = _primary_gesture(overlay)

    gesture.emit("pressed", 2, 0.0, 0.0)

    assert len(calls) == 1
    # refresh() reloads records fresh from the store (media_store.query()),
    # so the card's captured record is an equal-but-distinct MediaRecord
    # instance from the one seeded above -- compare by id, not identity.
    assert calls[0][0].id == rec.id
    assert seen == [rec.id]


@gtk_required
def test_double_click_missing_file_opens_nothing(tmp_path, monkeypatch):
    """Guard mirrors GenerationCard._on_pressed's video_exists/image_exists
    checks: an artgen record whose file_path doesn't exist on disk must not
    open a viewer window."""
    import media_store as ms_mod
    import artgen_gallery as ag_mod
    from artgen_gallery import ArtgenGallery

    opened = []
    monkeypatch.setattr(ag_mod, "ArtgenViewerWindow", lambda *a, **k: opened.append(a))

    rec = _make_media_record(tmp_path, file_path="/nonexistent/does_not_exist.txt")
    ms_mod.media_store.add(rec)

    gallery = ArtgenGallery()
    gallery.refresh()

    overlay = _find_overlay_for(gallery, rec.id)
    gesture = _primary_gesture(overlay)

    gesture.emit("pressed", 2, 0.0, 0.0)

    assert opened == []


@gtk_required
def test_no_child_activated_connection_left_on_flow():
    """Source-level: the old FlowBox "child-activated" wiring (single-click,
    SelectionMode.SINGLE-only mechanism) must be gone -- the FlowBox itself
    no longer owns any click semantics; each card's own GestureClick does."""
    src = (Path(__file__).parent.parent / "app" / "artgen_gallery.py").read_text()
    assert 'connect("child-activated"' not in src
    assert "Gtk.SelectionMode.SINGLE" not in src


@gtk_required
def test_hover_action_buttons_unchanged(tmp_path):
    """Sanity check: the hover star/delete/remix buttons are still plain
    Gtk.Buttons living in the overlay (same precedent as native
    GenerationCard, whose buttons already coexist with its own card-level
    GestureClick in main_window.py) -- this task must not have disturbed
    that structure."""
    import media_store as ms_mod
    from artgen_gallery import ArtgenGallery

    rec = _make_media_record(tmp_path)
    ms_mod.media_store.add(rec)

    gallery = ArtgenGallery()
    gallery.refresh()

    overlay = _find_overlay_for(gallery, rec.id)
    assert hasattr(overlay, "_remix_as_pipeline_btn")
