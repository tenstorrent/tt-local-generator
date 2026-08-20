"""
Unit tests for MainWindow._load_history()'s animate/video gallery split.

Post the "AnimateDiff is Video" migration, Wan2.2-Animate records are stamped
media_type="video" (so they show up in the main Video gallery) with
generator_type="animate" as the provenance marker. The Animate Discover tab
should still show those same records via a `generator_type == "animate"`
filter — NOT a `media_type == "animate"` filter, which can never match a
real record post-migration.

Tests the pure routing logic only — no GTK, no real gallery widgets (each
gallery is a MagicMock whose `load_history` calls are asserted on).
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import types

# Ensure the system PyGObject package is importable inside the venv.
_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from history_store import GenerationRecord  # noqa: E402


def _make_record(media_type, generator_type=None, created_at="2026-01-01T00:00:00", rid="r1"):
    return GenerationRecord(
        id=rid,
        prompt="a prompt",
        negative_prompt="",
        num_inference_steps=20,
        seed=1,
        video_path="",
        thumbnail_path="",
        created_at=created_at,
        media_type=media_type,
        generator_type=generator_type,
    )


def _make_mw_with_records(records):
    """Build a minimal fake MainWindow with just enough wired for _load_history."""
    import main_window as mw

    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)

    store = MagicMock()
    store.all_records.return_value = records
    obj._store = store
    obj._remote_records = {}
    obj._video_gallery = MagicMock()
    obj._animate_gallery = MagicMock()
    obj._image_gallery = MagicMock()
    obj._set_status = MagicMock()
    obj._update_attractor_btn = MagicMock()
    return obj


def _bind(obj):
    import main_window as mw
    return types.MethodType(mw.MainWindow._load_history, obj)


def test_generator_type_animate_record_routed_to_animate_gallery():
    """A post-migration Wan2.2-Animate record (media_type='video',
    generator_type='animate') must still populate the Animate Discover tab,
    in addition to the Video gallery it's now also a member of."""
    rec = _make_record(media_type="video", generator_type="animate", rid="anim1")
    obj = _make_mw_with_records([rec])
    fn = _bind(obj)

    fn()

    obj._animate_gallery.load_history.assert_called_once()
    (animate_recs,), _ = obj._animate_gallery.load_history.call_args
    assert [r.id for r in animate_recs] == ["anim1"]

    # It's also a video record, so it must be present in the Video gallery too.
    obj._video_gallery.load_history.assert_called_once()
    (video_recs,), _ = obj._video_gallery.load_history.call_args
    assert [r.id for r in video_recs] == ["anim1"]


def test_plain_video_record_not_routed_to_animate_gallery():
    """A plain Wan2.2/AnimateDiff video record (generator_type != 'animate')
    must not show up in the Animate Discover tab."""
    rec = _make_record(media_type="video", generator_type="animatediff", rid="vid1")
    obj = _make_mw_with_records([rec])
    fn = _bind(obj)

    fn()

    obj._animate_gallery.load_history.assert_not_called()
    obj._video_gallery.load_history.assert_called_once()
    (video_recs,), _ = obj._video_gallery.load_history.call_args
    assert [r.id for r in video_recs] == ["vid1"]
