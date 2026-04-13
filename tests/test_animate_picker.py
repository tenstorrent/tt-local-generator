"""
Unit tests for animate_picker — BundledClipScanner and extract_thumbnail.
No GTK display required for these tests.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from animate_picker import BundledClipScanner, extract_thumbnail


def test_bundled_scanner_empty_dir_returns_empty(tmp_path):
    """Scanning a non-existent directory returns {}."""
    scanner = BundledClipScanner(str(tmp_path / "nonexistent"))
    assert scanner.scan() == {}


def test_bundled_scanner_empty_clips_dir_returns_empty(tmp_path):
    """A clips dir with no subdirectories returns {}."""
    clips_dir = tmp_path / "motion_clips"
    clips_dir.mkdir()
    scanner = BundledClipScanner(str(clips_dir))
    assert scanner.scan() == {}


def test_bundled_scanner_discovers_categories_and_clips(tmp_path):
    """Scanner finds category subdirs and MP4 files within them."""
    clips_dir = tmp_path / "motion_clips"
    walk_dir = clips_dir / "walk"
    walk_dir.mkdir(parents=True)
    (walk_dir / "walk_forward.mp4").write_bytes(b"fake-mp4")
    (walk_dir / "walk_backward.mp4").write_bytes(b"fake-mp4")
    (clips_dir / "readme.txt").write_text("not a dir")  # should be ignored

    # Patch extract_thumbnail so no ffmpeg call is made
    with patch("animate_picker.extract_thumbnail", return_value=False):
        scanner = BundledClipScanner(str(clips_dir))
        result = scanner.scan()

    assert "walk" in result
    names = {clip["name"] for clip in result["walk"]}
    assert names == {"walk_forward", "walk_backward"}
    assert len(result) == 1  # only the 'walk' category


def test_bundled_scanner_skips_dirs_with_no_mp4(tmp_path):
    """A subdirectory with no MP4 files is omitted from results."""
    clips_dir = tmp_path / "motion_clips"
    empty_cat = clips_dir / "gestures"
    empty_cat.mkdir(parents=True)
    (empty_cat / "readme.txt").write_text("no clips yet")

    with patch("animate_picker.extract_thumbnail", return_value=False):
        result = BundledClipScanner(str(clips_dir)).scan()

    assert result == {}


def test_bundled_scanner_clip_dict_has_required_keys(tmp_path):
    """Each clip dict has 'name', 'mp4', and 'thumb' keys."""
    clips_dir = tmp_path / "motion_clips"
    (clips_dir / "run").mkdir(parents=True)
    mp4 = clips_dir / "run" / "run_forward.mp4"
    mp4.write_bytes(b"fake")

    with patch("animate_picker.extract_thumbnail", return_value=False):
        result = BundledClipScanner(str(clips_dir)).scan()

    clip = result["run"][0]
    assert clip["name"] == "run_forward"
    assert clip["mp4"] == str(mp4)
    assert "thumb" in clip


def test_extract_thumbnail_returns_false_on_missing_ffmpeg(tmp_path):
    """extract_thumbnail returns False when ffmpeg is not found."""
    src = str(tmp_path / "video.mp4")
    dest = str(tmp_path / "thumb.jpg")
    Path(src).write_bytes(b"fake")

    with patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg not found")):
        result = extract_thumbnail(src, dest)

    assert result is False


# ---------- InputWidget tests (require DISPLAY) ----------

import pytest


def _gtk_available() -> bool:
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        app = Gtk.Application(application_id="test.animate.picker")
        return True
    except Exception:
        return False


gtk_required = pytest.mark.skipif(
    not _gtk_available(), reason="GTK4 display not available"
)


@gtk_required
def test_input_widget_empty_on_construction():
    """An InputWidget constructed with no path has no filled CSS class."""
    from animate_picker import InputWidget
    w = InputWidget("motion", "MOTION VIDEO")
    assert not w.has_css_class("input-widget-filled-motion")
    assert not w.has_css_class("input-widget-filled-char")


@gtk_required
def test_input_widget_set_value_nonexistent_path_stays_empty():
    """set_value with a path that doesn't exist leaves the widget empty."""
    from animate_picker import InputWidget
    w = InputWidget("motion", "MOTION VIDEO")
    w.set_value("/nonexistent/path/to/clip.mp4")
    assert not w.has_css_class("input-widget-filled-motion")


@gtk_required
def test_input_widget_set_value_existing_image_adds_filled_char(tmp_path):
    """set_value with a real image file adds input-widget-filled-char class."""
    from animate_picker import InputWidget
    img = tmp_path / "char.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # minimal JPEG header
    w = InputWidget("char", "CHARACTER")
    w.set_value(str(img))
    assert w.has_css_class("input-widget-filled-char")


@gtk_required
def test_input_widget_clear_removes_filled_class(tmp_path):
    """set_value('') removes the filled CSS class."""
    from animate_picker import InputWidget
    img = tmp_path / "char.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    w = InputWidget("char", "CHARACTER")
    w.set_value(str(img))
    assert w.has_css_class("input-widget-filled-char")
    w.set_value("")
    assert not w.has_css_class("input-widget-filled-char")


@gtk_required
def test_input_widget_always_has_base_css_class():
    """InputWidget always carries the .input-widget CSS class."""
    from animate_picker import InputWidget
    w = InputWidget("motion", "MOTION VIDEO")
    assert w.has_css_class("input-widget")


@gtk_required
def test_mode_desc_bar_css_class_swap():
    """Mode description bar correctly swaps CSS variant classes."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    bar = Gtk.Box()
    bar.add_css_class("mode-desc-bar")
    # Simulate switching to animation mode
    bar.remove_css_class("mode-desc-bar-anim")
    bar.remove_css_class("mode-desc-bar-repl")
    bar.add_css_class("mode-desc-bar-anim")
    assert bar.has_css_class("mode-desc-bar-anim")
    assert not bar.has_css_class("mode-desc-bar-repl")
    # Simulate switching to replacement mode
    bar.remove_css_class("mode-desc-bar-anim")
    bar.remove_css_class("mode-desc-bar-repl")
    bar.add_css_class("mode-desc-bar-repl")
    assert bar.has_css_class("mode-desc-bar-repl")
    assert not bar.has_css_class("mode-desc-bar-anim")


@gtk_required
def test_generation_card_stores_action_callbacks():
    """Verify animate_cb and motion_cb are stored on GenerationCard."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    from main_window import GenerationCard
    from history_store import GenerationRecord
    import datetime

    animate_calls = []
    motion_calls  = []

    rec = GenerationRecord(
        id="test-card-0001",
        prompt="test",
        negative_prompt="",
        num_inference_steps=20,
        seed=42,
        video_path="/nonexistent/video.mp4",
        thumbnail_path="/nonexistent/thumb.jpg",
        created_at=datetime.datetime.now().isoformat(),
        media_type="video",
    )

    card = GenerationCard(
        rec,
        iterate_cb=lambda r: None,
        select_cb=lambda c: None,
        delete_cb=lambda r: None,
        animate_cb=lambda r: animate_calls.append(r),
        motion_cb=lambda r: motion_calls.append(r),
    )

    card._animate_cb(rec)
    card._motion_cb(rec)

    assert len(animate_calls) == 1
    assert len(motion_calls)  == 1
    assert animate_calls[0].id == "test-card-0001"


@gtk_required
def test_gallery_card_action_callbacks_invoked():
    """Verify animate_cb and motion_cb are stored on GenerationCard."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    from main_window import GenerationCard
    from history_store import GenerationRecord

    animate_calls = []
    motion_calls = []

    rec = GenerationRecord(
        id="test-0001", prompt="test", negative_prompt="",
        num_inference_steps=20, seed=42,
        video_path="/nonexistent/video.mp4",
        thumbnail_path="/nonexistent/thumb.jpg",
        created_at="2025-01-01T00:00:00",
        media_type="video",
    )

    card = GenerationCard(
        rec,
        iterate_cb=lambda r: None,
        select_cb=lambda c: None,
        delete_cb=lambda r: None,
        animate_cb=lambda r: animate_calls.append(r),
        motion_cb=lambda r: motion_calls.append(r),
    )

    # Simulate callbacks being invoked
    card._animate_cb(rec)
    card._motion_cb(rec)

    assert len(animate_calls) == 1
    assert len(motion_calls) == 1
    assert animate_calls[0].id == "test-0001"
