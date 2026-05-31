"""Tests for the right-click forge transform pipeline in main_window.py.

Tests cover:
- _transform_available(): caching + import fallback
- _make_thumbnail_for(): subprocess call and shutil fallback
- MainWindow._run_transform(): plugin dispatch, record construction, data flow
- GenerationCard: transform_cb param wiring
- GalleryWidget: transform_cb pass-through to _make_card
"""
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_record(**kwargs):
    from history_store import GenerationRecord
    base = dict(
        id=str(uuid.uuid4()),
        prompt="test prompt",
        negative_prompt="",
        num_inference_steps=20,
        seed=42,
        video_path="/tmp/test.mp4",
        thumbnail_path="/tmp/test_thumb.jpg",
        created_at="2026-01-01T00:00:00+00:00",
        media_type="video",
        image_path="",
        model="wan2",
        extra_meta={},
    )
    base.update(kwargs)
    return GenerationRecord(**base)


def _fake_ok_run(**kwargs):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")


# ── _transform_available ─────────────────────────────────────────────────────

def test_transform_available_returns_true_when_plugin_available(tmp_path):
    import importlib.util as ilu
    import main_window as mw

    # Clear cache
    mw._TRANSFORM_AVAIL.clear()

    plugin_dir = tmp_path / "myplugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text(
        "def is_available(): return True\n"
    )

    orig = Path(mw.__file__).parent.parent / "plugins"
    with patch.object(Path, "__truediv__",
                      side_effect=lambda self, other: plugin_dir / "plugin.py"
                      if other == "myplugin/plugin.py" else orig / other):
        # Bypass the Path magic and just test via spec injection
        pass

    # Direct test: inject a spec that succeeds
    fake_mod = MagicMock()
    fake_mod.is_available.return_value = True

    spec = MagicMock()
    spec.loader.exec_module = lambda m: None

    mw._TRANSFORM_AVAIL.clear()
    with patch("importlib.util.spec_from_file_location", return_value=spec), \
         patch("importlib.util.module_from_spec", return_value=fake_mod):
        result = mw._transform_available("myplugin")

    assert result is True
    mw._TRANSFORM_AVAIL.clear()


def test_transform_available_returns_false_on_import_error():
    import main_window as mw
    mw._TRANSFORM_AVAIL.clear()

    with patch("importlib.util.spec_from_file_location", side_effect=FileNotFoundError):
        result = mw._transform_available("nonexistent_plugin")

    assert result is False
    mw._TRANSFORM_AVAIL.clear()


def test_transform_available_caches_result():
    import main_window as mw
    mw._TRANSFORM_AVAIL.clear()

    call_count = [0]
    fake_mod = MagicMock()
    fake_mod.is_available.return_value = True

    def counting_spec(*args, **kwargs):
        call_count[0] += 1
        return MagicMock()

    with patch("importlib.util.spec_from_file_location", side_effect=counting_spec), \
         patch("importlib.util.module_from_spec", return_value=fake_mod):
        mw._transform_available("cached_test")
        mw._transform_available("cached_test")  # second call — should be from cache

    assert call_count[0] == 1  # import only happened once
    mw._TRANSFORM_AVAIL.clear()


# ── _make_thumbnail_for ───────────────────────────────────────────────────────

def test_make_thumbnail_for_calls_ffmpeg(tmp_path):
    import main_window as mw
    thumb = str(tmp_path / "thumb.jpg")

    with patch("subprocess.run", return_value=_fake_ok_run()) as mock_run:
        mw._make_thumbnail_for("/fake/image.png", thumb)

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "ffmpeg" in args[0]
    assert "-i" in args
    assert thumb in args
    assert mock_run.call_args[1].get("stdin") == subprocess.DEVNULL


def test_make_thumbnail_for_falls_back_to_copy_on_ffmpeg_failure(tmp_path):
    import main_window as mw
    src = str(tmp_path / "image.png")
    thumb = str(tmp_path / "sub" / "thumb.jpg")
    Path(src).write_bytes(b"fake")

    with patch("subprocess.run", side_effect=FileNotFoundError):
        mw._make_thumbnail_for(src, thumb)

    assert Path(thumb).exists()


# ── _run_transform ────────────────────────────────────────────────────────────

def _make_mock_main_window():
    from history_store import HistoryStore
    mw_inst = MagicMock()
    mw_inst._store = MagicMock(spec=HistoryStore)
    mw_inst._image_gallery = MagicMock()
    return mw_inst


def test_run_transform_rmbg_creates_image_record(tmp_path):
    import main_window as mw
    from history_store import IMAGES_DIR, THUMBNAILS_DIR

    record = _make_record(video_path=str(tmp_path / "test.mp4"))
    (tmp_path / "test.mp4").write_bytes(b"fake")

    fake_plugin = MagicMock()
    fake_plugin.remove_background.side_effect = lambda src, dest: Path(dest).write_bytes(b"PNG")

    spec = MagicMock()
    spec.loader.exec_module = lambda m: None

    with patch("importlib.util.spec_from_file_location", return_value=spec), \
         patch("importlib.util.module_from_spec", return_value=fake_plugin), \
         patch("main_window._make_thumbnail_for"):
        mw_inst = _make_mock_main_window()
        result = mw.MainWindow._run_transform(mw_inst, record, "rmbg")

    assert result.media_type == "image"
    assert result.image_path.endswith(".png")
    assert "Background removed" in result.prompt
    assert result.extra_meta["_source_id"] == record.id
    assert result.extra_meta["_transform"] == "rmbg"


def test_run_transform_blip_uses_original_image(tmp_path):
    import main_window as mw

    img_path = str(tmp_path / "test.jpg")
    Path(img_path).write_bytes(b"fake")
    record = _make_record(image_path=img_path, video_path="", media_type="image")

    fake_plugin = MagicMock()
    fake_plugin.caption_image.return_value = "A cinematic scene at sunset"

    spec = MagicMock()
    spec.loader.exec_module = lambda m: None

    with patch("importlib.util.spec_from_file_location", return_value=spec), \
         patch("importlib.util.module_from_spec", return_value=fake_plugin), \
         patch("main_window._make_thumbnail_for"):
        mw_inst = _make_mock_main_window()
        result = mw.MainWindow._run_transform(mw_inst, record, "blip")

    assert result.media_type == "image"
    assert result.image_path == img_path          # original image used as card display
    assert result.prompt == "A cinematic scene at sunset"
    assert result.extra_meta["_transform"] == "blip"


def test_run_transform_depth_creates_png(tmp_path):
    import main_window as mw

    img_path = str(tmp_path / "test.jpg")
    Path(img_path).write_bytes(b"fake")
    record = _make_record(image_path=img_path, video_path="", media_type="image")

    fake_plugin = MagicMock()
    fake_plugin.estimate_depth.side_effect = lambda src, dest: Path(dest).write_bytes(b"PNG")

    spec = MagicMock()
    spec.loader.exec_module = lambda m: None

    with patch("importlib.util.spec_from_file_location", return_value=spec), \
         patch("importlib.util.module_from_spec", return_value=fake_plugin), \
         patch("main_window._make_thumbnail_for"):
        mw_inst = _make_mock_main_window()
        result = mw.MainWindow._run_transform(mw_inst, record, "depth")

    assert result.media_type == "image"
    assert result.image_path.endswith(".png")
    assert "Depth map" in result.prompt


def test_on_transform_finished_appends_and_refreshes(tmp_path):
    import main_window as mw
    from history_store import GenerationRecord

    record = _make_record(media_type="image", image_path=str(tmp_path / "out.png"))
    mw_inst = _make_mock_main_window()
    mw_inst._store.all_records.return_value = [record]

    with patch("gi.repository.GLib.idle_add", lambda fn, *a: fn(*a)):
        result = mw.MainWindow._on_transform_finished(mw_inst, record)

    mw_inst._store.append.assert_called_once_with(record)
    mw_inst._image_gallery.rebuild.assert_called_once()
    assert result is False  # must return False for GLib.idle_add


# ── GenerationCard wiring ─────────────────────────────────────────────────────

def test_generation_card_accepts_transform_cb():
    """GenerationCard stores transform_cb without error."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    # Can't instantiate GTK widgets in unit tests — verify the __init__ signature
    import inspect
    import main_window as mw
    sig = inspect.signature(mw.GenerationCard.__init__)
    assert "transform_cb" in sig.parameters


def test_gallery_widget_accepts_transform_cb():
    """GalleryWidget __init__ accepts transform_cb parameter."""
    import inspect
    import main_window as mw
    sig = inspect.signature(mw.GalleryWidget.__init__)
    assert "transform_cb" in sig.parameters
