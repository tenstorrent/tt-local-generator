"""
Unit tests for MainWindow._get_animate_inputs().
Tests the pure logic only — no GTK, no real BundledClipScanner I/O.

gi (PyGObject) lives in the system dist-packages on this machine, not in the
venv.  We add it to sys.path early so `import gi` inside main_window.py works
during unit tests without a running display.
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


def _make_record(media_type="animate", thumb="", image_path="", extra_meta=None):
    r = MagicMock()
    r.media_type = media_type
    r.thumbnail_path = thumb
    r.image_path = image_path
    r.extra_meta = extra_meta if extra_meta is not None else {}
    return r


def _make_mw_with_store(records, clips_scan=None):
    """
    Build a minimal fake MainWindow with _store mocked.
    Uses MainWindow.__new__ with GTK __init__ patched to avoid display requirements.
    """
    import main_window as mw

    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)

    store = MagicMock()
    store.all_records.return_value = records
    obj._store = store
    obj._clips_scan = clips_scan or {}
    return obj


def _bind(obj):
    """Bind _get_animate_inputs to obj so self works."""
    import main_window as mw
    return types.MethodType(mw.MainWindow._get_animate_inputs, obj)


def test_returns_last_frame_path_when_available(tmp_path):
    """Uses extra_meta['last_frame_path'] when it exists and the file is on disk."""
    last_frame = tmp_path / "frame_last.jpg"
    last_frame.write_bytes(b"jpeg")

    rec = _make_record(
        media_type="animate",
        thumb=str(tmp_path / "frame.jpg"),
        extra_meta={"last_frame_path": str(last_frame)},
    )
    obj = _make_mw_with_store([rec])
    fn = _bind(obj)

    clips_data = {"locomotion": [{"mp4": "/clips/walk.mp4", "name": "walk", "thumb": ""}]}

    with patch("animate_picker.BundledClipScanner") as MockScanner, \
         patch("main_window._settings") as mock_settings:
        mock_settings.get.return_value = "/clips"
        MockScanner.return_value.scan.return_value = clips_data
        ref_video, ref_char = fn()

    assert ref_video == "/clips/walk.mp4"
    assert ref_char == str(last_frame)


def test_falls_back_to_thumbnail_when_last_frame_missing(tmp_path):
    """Falls back to thumbnail_path when last_frame_path file does not exist."""
    thumb = tmp_path / "frame.jpg"
    thumb.write_bytes(b"jpeg")

    rec = _make_record(
        media_type="animate",
        thumb=str(thumb),
        extra_meta={"last_frame_path": "/nonexistent/frame_last.jpg"},
    )
    obj = _make_mw_with_store([rec])
    fn = _bind(obj)

    clips_data = {"locomotion": [{"mp4": "/clips/walk.mp4", "name": "walk", "thumb": ""}]}

    with patch("animate_picker.BundledClipScanner") as MockScanner, \
         patch("main_window._settings") as mock_settings:
        mock_settings.get.return_value = "/clips"
        MockScanner.return_value.scan.return_value = clips_data
        ref_video, ref_char = fn()

    assert ref_char == str(thumb)


def test_falls_back_to_flux_image_when_no_animate_records(tmp_path):
    """When no animate records exist, uses a FLUX image record's image_path."""
    img = tmp_path / "flux.jpg"
    img.write_bytes(b"jpeg")

    rec = _make_record(media_type="image", image_path=str(img))
    obj = _make_mw_with_store([rec])
    fn = _bind(obj)

    clips_data = {"locomotion": [{"mp4": "/clips/walk.mp4", "name": "walk", "thumb": ""}]}

    with patch("animate_picker.BundledClipScanner") as MockScanner, \
         patch("main_window._settings") as mock_settings:
        mock_settings.get.return_value = "/clips"
        MockScanner.return_value.scan.return_value = clips_data
        ref_video, ref_char = fn()

    assert ref_char == str(img)


def test_returns_empty_ref_char_when_no_media(tmp_path):
    """Returns ('clip', '') when store is empty — attractor will skip the cycle."""
    obj = _make_mw_with_store([])
    fn = _bind(obj)

    clips_data = {"locomotion": [{"mp4": "/clips/walk.mp4", "name": "walk", "thumb": ""}]}

    with patch("animate_picker.BundledClipScanner") as MockScanner, \
         patch("main_window._settings") as mock_settings:
        mock_settings.get.return_value = "/clips"
        MockScanner.return_value.scan.return_value = clips_data
        ref_video, ref_char = fn()

    assert ref_char == ""


def test_returns_empty_strings_when_no_clips():
    """Returns ('', '') when BundledClipScanner finds no clips."""
    rec = _make_record(media_type="animate", thumb="/some/thumb.jpg",
                       extra_meta={"last_frame_path": "/some/last.jpg"})
    obj = _make_mw_with_store([rec])
    fn = _bind(obj)

    with patch("animate_picker.BundledClipScanner") as MockScanner, \
         patch("main_window._settings") as mock_settings:
        mock_settings.get.return_value = "/clips"
        MockScanner.return_value.scan.return_value = {}  # no clips
        ref_video, ref_char = fn()

    assert ref_video == ""
    assert ref_char == ""
