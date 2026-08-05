"""
Tests for unify-gallery-interaction Task 4:

  1. GenerationCard's primary GestureClick gets a real handler (`_on_pressed`)
     instead of the old `lambda *_: self._select_cb(self)`. Single click still
     only selects (unchanged). A double click (n_press == 2) ALSO opens the
     record full-screen -- ImageViewerWindow for images, VideoPlayerWindow for
     everything else (video/animate/animatediff) -- mirroring the guard logic
     DetailPanel._open_fullscreen / _open_image_fullscreen already use
     (record.video_exists / record.image_exists).

  2. VideoPlayerWindow gets a GIF branch: when the record is an AnimateDiff
     .gif (media_type == "animatediff" or video_path.endswith(".gif")), the
     main media widget is artgen_render.AnimatedGifWidget instead of
     Gtk.Video -- the app avoids Gtk.Video for gifs everywhere else
     (DetailPanel drives gifs via GdkPixbufAnimationIter directly), so the
     fullscreen window must not regress to a static/broken Gtk.Video frame.

Run under xvfb (GTK4 widgets need a real display):
    xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_gallery_double_click_fullscreen.py -q
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


def _make_record(**kwargs):
    from history_store import GenerationRecord
    base = dict(
        id=str(uuid.uuid4()),
        prompt="a test prompt",
        negative_prompt="",
        num_inference_steps=20,
        seed=42,
        video_path="",
        thumbnail_path="",
        created_at="2026-01-01T00:00:00+00:00",
        media_type="video",
        image_path="",
        model="wan2.2",
        extra_meta={},
    )
    base.update(kwargs)
    return GenerationRecord(**base)


def _make_gif(path: Path) -> None:
    from PIL import Image
    frame1 = Image.new("RGB", (8, 8), (255, 0, 0))
    frame2 = Image.new("RGB", (8, 8), (0, 255, 0))
    frame1.save(path, save_all=True, append_images=[frame2], duration=50, loop=0)


# ── GenerationCard: single click still just selects ────────────────────────────

@gtk_required
def test_single_click_selects_and_opens_no_viewer(monkeypatch):
    import main_window as mw

    opened = []
    monkeypatch.setattr(mw, "VideoPlayerWindow", lambda *a, **k: opened.append(("video", a, k)) or _FakeWin())
    monkeypatch.setattr(mw, "ImageViewerWindow", lambda *a, **k: opened.append(("image", a, k)) or _FakeWin())

    selected = []
    rec = _make_record(media_type="video", video_path="/nonexistent/does_not_exist.mp4")
    card = mw.GenerationCard(rec, select_cb=lambda c: selected.append(c), delete_cb=lambda *_: None)

    card._on_pressed(None, 1, 0.0, 0.0)

    assert selected == [card]
    assert opened == []


class _FakeWin:
    """Stand-in for VideoPlayerWindow/ImageViewerWindow -- records .present()."""
    def __init__(self):
        self.presented = False

    def present(self):
        self.presented = True


# ── GenerationCard: double click opens fullscreen, keyed by media_type ─────────

@gtk_required
def test_double_click_video_opens_video_player_window(tmp_path, monkeypatch):
    import main_window as mw

    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"fake-mp4")
    rec = _make_record(media_type="video", video_path=str(vid))

    calls = []

    class _Fake(_FakeWin):
        def __init__(self, record, parent):
            super().__init__()
            calls.append((record, parent))

    monkeypatch.setattr(mw, "VideoPlayerWindow", _Fake)
    monkeypatch.setattr(mw, "ImageViewerWindow", lambda *a, **k: pytest.fail("wrong window class"))

    selected = []
    card = mw.GenerationCard(rec, select_cb=lambda c: selected.append(c), delete_cb=lambda *_: None)

    card._on_pressed(None, 2, 0.0, 0.0)

    assert len(calls) == 1
    assert calls[0][0] is rec
    assert selected == [card]  # double click still selects too


@gtk_required
def test_double_click_animatediff_opens_video_player_window(tmp_path, monkeypatch):
    """animatediff (.gif) records are NOT images -- they route to VideoPlayerWindow,
    which is what gets the new gif-aware branch (task 4's second half)."""
    import main_window as mw

    gif = tmp_path / "loop.gif"
    _make_gif(gif)
    rec = _make_record(media_type="animatediff", video_path=str(gif))

    calls = []
    monkeypatch.setattr(mw, "VideoPlayerWindow", lambda record, parent: (calls.append((record, parent)), _FakeWin())[1])
    monkeypatch.setattr(mw, "ImageViewerWindow", lambda *a, **k: pytest.fail("wrong window class"))

    card = mw.GenerationCard(rec, select_cb=lambda *_: None, delete_cb=lambda *_: None)
    card._on_pressed(None, 2, 0.0, 0.0)

    assert len(calls) == 1
    assert calls[0][0] is rec


@gtk_required
def test_double_click_image_opens_image_viewer_window(tmp_path, monkeypatch):
    import main_window as mw
    from PIL import Image

    img = tmp_path / "pic.png"
    Image.new("RGB", (16, 16), (10, 20, 30)).save(img)
    rec = _make_record(media_type="image", image_path=str(img), video_path="")

    calls = []
    monkeypatch.setattr(mw, "ImageViewerWindow", lambda record, parent: (calls.append((record, parent)), _FakeWin())[1])
    monkeypatch.setattr(mw, "VideoPlayerWindow", lambda *a, **k: pytest.fail("wrong window class"))

    card = mw.GenerationCard(rec, select_cb=lambda *_: None, delete_cb=lambda *_: None)
    card._on_pressed(None, 2, 0.0, 0.0)

    assert len(calls) == 1
    assert calls[0][0] is rec


@gtk_required
def test_double_click_missing_video_opens_nothing(monkeypatch):
    """Mirrors DetailPanel._open_fullscreen's guard: no file on disk -> no window."""
    import main_window as mw

    rec = _make_record(media_type="video", video_path="/nonexistent/gone.mp4")
    opened = []
    monkeypatch.setattr(mw, "VideoPlayerWindow", lambda *a, **k: opened.append(a) or _FakeWin())
    monkeypatch.setattr(mw, "ImageViewerWindow", lambda *a, **k: opened.append(a) or _FakeWin())

    card = mw.GenerationCard(rec, select_cb=lambda *_: None, delete_cb=lambda *_: None)
    card._on_pressed(None, 2, 0.0, 0.0)

    assert opened == []


@gtk_required
def test_double_click_missing_image_opens_nothing(monkeypatch):
    """Mirrors DetailPanel._open_image_fullscreen's guard."""
    import main_window as mw

    rec = _make_record(media_type="image", image_path="/nonexistent/gone.png", video_path="")
    opened = []
    monkeypatch.setattr(mw, "VideoPlayerWindow", lambda *a, **k: opened.append(a) or _FakeWin())
    monkeypatch.setattr(mw, "ImageViewerWindow", lambda *a, **k: opened.append(a) or _FakeWin())

    card = mw.GenerationCard(rec, select_cb=lambda *_: None, delete_cb=lambda *_: None)
    card._on_pressed(None, 2, 0.0, 0.0)

    assert opened == []


@gtk_required
def test_primary_gesture_wired_to_on_pressed():
    """The card's primary GestureClick must be wired to the real handler, not
    a discard-n_press lambda -- otherwise double-click can never be detected."""
    import main_window as mw

    rec = _make_record(media_type="video", video_path="")
    card = mw.GenerationCard(rec, select_cb=lambda *_: None, delete_cb=lambda *_: None)

    found = [c for c in card.observe_controllers() if isinstance(c, mw.Gtk.GestureClick)]
    # There are two GestureClicks on the card: the primary (default button,
    # left-click) and the right-click (explicitly restricted to button 3).
    # The primary one is the non-button-3 one.
    primary = [g for g in found if g.get_button() != 3]
    assert primary, "expected a non-button-3 GestureClick (the primary click gesture)"


# ── VideoPlayerWindow: gif branch uses AnimatedGifWidget, not Gtk.Video ────────

@gtk_required
def test_video_player_window_uses_animated_gif_widget_for_animatediff(tmp_path):
    import main_window as mw
    from artgen_render import AnimatedGifWidget

    gif = tmp_path / "loop.gif"
    _make_gif(gif)
    rec = _make_record(media_type="animatediff", video_path=str(gif))

    win = mw.VideoPlayerWindow(rec, None)

    assert isinstance(win._video, AnimatedGifWidget)
    assert not isinstance(win._video, mw.Gtk.Video)


@gtk_required
def test_video_player_window_uses_animated_gif_widget_for_dot_gif_path(tmp_path):
    """Even a non-animatediff media_type routes to the gif branch if the path
    literally ends in .gif (mirrors DetailPanel's `_is_gif` check)."""
    import main_window as mw
    from artgen_render import AnimatedGifWidget

    gif = tmp_path / "loop.gif"
    _make_gif(gif)
    rec = _make_record(media_type="video", video_path=str(gif))

    win = mw.VideoPlayerWindow(rec, None)

    assert isinstance(win._video, AnimatedGifWidget)


@gtk_required
def test_video_player_window_uses_gtk_video_for_real_mp4(tmp_path):
    """Non-gif videos are unchanged -- still Gtk.Video."""
    import main_window as mw

    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"fake-mp4")
    rec = _make_record(media_type="video", video_path=str(vid))

    win = mw.VideoPlayerWindow(rec, None)

    assert isinstance(win._video, mw.Gtk.Video)


@gtk_required
def test_detail_pane_autoplays_native_video_on_select(tmp_path):
    """Selecting a native (non-gif) video shows it in the detail pane already
    PLAYING: the inline Gtk.Video has autoplay on and the play control starts as
    "⏸ Pause" (parity with the macOS GstPlayer path + hover preview). Regression
    for "clicking non-animatediff videos doesn't autoplay in the details pane"."""
    import main_window as mw
    if mw._USE_SYSTEM_PLAYER:  # pragma: no cover - macOS uses the GstPlayer path
        pytest.skip("Linux Gtk.Video path only")

    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"fake-mp4")   # path just needs to exist; content irrelevant
    rec = _make_record(media_type="video", video_path=str(vid))

    panel = mw.DetailPanel()
    panel.show_record(rec, lambda *_a: None)

    assert isinstance(panel._video_widget, mw.Gtk.Video)
    assert panel._video_widget.get_autoplay() is True
    assert panel._play_btn is not None
    assert panel._play_btn.get_label() == "⏸ Pause"


# ── VideoPlayerWindow._toggle_play: real gif pause/resume (gif-hygiene fix 2) ─
#
# Space/Pause used to no-op entirely for the gif branch (AnimatedGifWidget
# has no get_media_stream()). Now it should genuinely pause/resume the gif
# via AnimatedGifWidget.set_playing/toggle_playing and flip the button label.

@gtk_required
def test_toggle_play_pauses_and_resumes_gif(tmp_path):
    import main_window as mw

    gif = tmp_path / "loop.gif"
    _make_gif(gif)
    rec = _make_record(media_type="animatediff", video_path=str(gif))

    win = mw.VideoPlayerWindow(rec, None)
    assert win._video._playing is True

    win._toggle_play(None)
    assert win._video._playing is False
    assert win._video._timer_id is None
    assert win._play_pause_btn.get_label() == "▶ Play"

    win._toggle_play(None)
    assert win._video._playing is True
    assert win._video._timer_id is not None
    assert win._play_pause_btn.get_label() == "⏸ Pause"


@gtk_required
def test_toggle_play_via_space_key_pauses_gif(tmp_path):
    """The Space handler routes through `_toggle_play` -- confirm the gif
    branch responds to it too, not just the button."""
    import main_window as mw

    gif = tmp_path / "loop.gif"
    _make_gif(gif)
    rec = _make_record(media_type="animatediff", video_path=str(gif))

    win = mw.VideoPlayerWindow(rec, None)
    win._on_key(None, 0x20, 0, 0)  # Space

    assert win._video._playing is False
    assert win._play_pause_btn.get_label() == "▶ Play"


@gtk_required
def test_toggle_play_unchanged_for_mp4_branch(tmp_path):
    """Non-gif path must still be driven by get_media_stream() -- unaffected
    by the new gif pause/resume wiring. In this headless test the Gtk.Video
    widget has no real GStreamer backend, so get_media_stream() returns None
    and _toggle_play is a no-op (same as before this fix)."""
    import main_window as mw

    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"fake-mp4")
    rec = _make_record(media_type="video", video_path=str(vid))

    win = mw.VideoPlayerWindow(rec, None)
    label_before = win._play_pause_btn.get_label()

    win._toggle_play(None)  # must not raise

    assert win._play_pause_btn.get_label() == label_before
