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
    mw_inst._artgen_gallery = MagicMock()
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
    mw_inst._image_gallery.load_history.assert_called_once()
    mw_inst._artgen_gallery.refresh.assert_not_called()  # image path never touches artgen gallery
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


# ── transform logging ─────────────────────────────────────────────────────────

def test_run_transform_writes_log_file(tmp_path):
    """_run_transform writes a structured log file to _TRANSFORMS_LOG_DIR."""
    import main_window as mw
    import log_viewer

    # Redirect log dir to tmp
    orig_log_dir = log_viewer._TRANSFORMS_LOG_DIR
    log_viewer._TRANSFORMS_LOG_DIR = tmp_path / "transforms"

    img_path = str(tmp_path / "src.jpg")
    Path(img_path).write_bytes(b"fake")
    record = _make_record(image_path=img_path, video_path="", media_type="image")

    fake_plugin = MagicMock()
    fake_plugin.estimate_depth.side_effect = lambda src, dest: Path(dest).write_bytes(b"PNG")
    spec = MagicMock(); spec.loader.exec_module = lambda m: None

    try:
        with patch("importlib.util.spec_from_file_location", return_value=spec), \
             patch("importlib.util.module_from_spec", return_value=fake_plugin), \
             patch("main_window._make_thumbnail_for"):
            mw_inst = _make_mock_main_window()
            mw.MainWindow._run_transform(mw_inst, record, "depth")

        logs = list((tmp_path / "transforms").glob("*.log"))
        assert len(logs) == 1, f"Expected 1 log file, got {len(logs)}"
        content = logs[0].read_text()
        assert "transform: depth" in content
        assert "source:" in content
        assert "status:    ok" in content
        assert "elapsed:" in content
    finally:
        log_viewer._TRANSFORMS_LOG_DIR = orig_log_dir


def test_run_transform_logs_error_on_failure(tmp_path):
    """_run_transform writes ERROR to log and re-raises on plugin failure."""
    import main_window as mw
    import log_viewer

    orig_log_dir = log_viewer._TRANSFORMS_LOG_DIR
    log_viewer._TRANSFORMS_LOG_DIR = tmp_path / "transforms"

    img_path = str(tmp_path / "src.jpg")
    Path(img_path).write_bytes(b"fake")
    record = _make_record(image_path=img_path, video_path="", media_type="image")

    fake_plugin = MagicMock()
    fake_plugin.remove_background.side_effect = RuntimeError("plugin crashed")
    spec = MagicMock(); spec.loader.exec_module = lambda m: None

    try:
        with patch("importlib.util.spec_from_file_location", return_value=spec), \
             patch("importlib.util.module_from_spec", return_value=fake_plugin), \
             patch("main_window._make_thumbnail_for"):
            mw_inst = _make_mock_main_window()
            with pytest.raises(RuntimeError, match="plugin crashed"):
                mw.MainWindow._run_transform(mw_inst, record, "rmbg")

        logs = list((tmp_path / "transforms").glob("*.log"))
        assert len(logs) == 1
        content = logs[0].read_text()
        assert "ERROR:" in content
        assert "plugin crashed" in content
    finally:
        log_viewer._TRANSFORMS_LOG_DIR = orig_log_dir


def test_collect_log_files_includes_transforms_section(tmp_path):
    """collect_log_files returns a TRANSFORMS section when logs exist."""
    import log_viewer

    tx_dir = tmp_path / "transforms"
    tx_dir.mkdir()
    (tx_dir / "20260101_120000_rmbg_my_image.log").write_text(
        "[12:00:00] transform: rmbg\n[12:00:01] status:    ok\n"
    )

    orig = log_viewer._TRANSFORMS_LOG_DIR
    log_viewer._TRANSFORMS_LOG_DIR = tx_dir
    try:
        sections = log_viewer.collect_log_files(animatediff_log_dir=tmp_path / "ad")
        names = [s["section"] for s in sections]
        assert "TRANSFORMS" in names
        tx = next(s for s in sections if s["section"] == "TRANSFORMS")
        assert len(tx["files"]) == 1
        assert tx["files"][0]["name"] == "rmbg  ←  my_image"
        assert tx["files"][0]["is_error"] is False
    finally:
        log_viewer._TRANSFORMS_LOG_DIR = orig


# ── "Convert to ANSI art" (ansi-image) — Effort B Task 2 ─────────────────────
#
# Unlike rmbg/blip/depth, this transform produces an ARTGEN MediaRecord (a
# new .ans file in the Generative Art gallery), not a native image
# GenerationRecord in the Image gallery. `_run_transform` special-cases the
# "ansi-image" key to call the plugin's `image_to_ansi(src)` and write a
# `media_store.MediaRecord`, mirroring `_create_generate_artgen`'s artgen
# record-construction pattern. `_on_transform_finished` then branches on
# `media_type == "artgen"` to refresh the artgen gallery instead of the
# image store/gallery.

def test_menu_lists_ansi_image_transform():
    """The right-click menu's hardcoded transform list includes ansi-image."""
    import inspect
    import main_window as mw

    src = inspect.getsource(mw.GenerationCard._on_right_click)
    assert '"ansi-image"' in src
    assert "ANSI" in src


def test_prewarm_probes_ansi_image():
    """MainWindow.__init__'s prewarm thread also probes ansi-image."""
    import inspect
    import main_window as mw

    src = inspect.getsource(mw.MainWindow.__init__)
    assert '"ansi-image"' in src


def test_transform_available_ansi_image_real_plugin():
    """_transform_available('ansi-image') loads the REAL plugin (no mocking)
    and returns True, since Pillow is present in this environment."""
    import main_window as mw

    mw._TRANSFORM_AVAIL.clear()
    try:
        assert mw._transform_available("ansi-image") is True
    finally:
        mw._TRANSFORM_AVAIL.clear()


def test_run_transform_ansi_image_creates_artgen_record(tmp_path):
    """_run_transform('ansi-image') writes a .ans file and returns a
    media_store.MediaRecord (media_type='artgen'), not a GenerationRecord."""
    import main_window as mw
    from media_store import MediaRecord

    img_path = str(tmp_path / "src.jpg")
    Path(img_path).write_bytes(b"fake")
    record = _make_record(
        image_path=img_path, video_path="", media_type="image",
        prompt="a sunset over dunes",
    )

    ansi_text = "\x1b[38;5;196m█" * 20
    fake_plugin = MagicMock()
    fake_plugin.image_to_ansi.return_value = ansi_text

    spec = MagicMock()
    spec.loader.exec_module = lambda m: None

    out_path = tmp_path / "artgen" / "20260101_000000_abcd1234.ans"
    thumb_path = tmp_path / "artgen" / "thumbnails" / "20260101_000000_abcd1234.png"
    fake_media_store = MagicMock()

    with patch("importlib.util.spec_from_file_location", return_value=spec), \
         patch("importlib.util.module_from_spec", return_value=fake_plugin), \
         patch("artgen_thumb.make_artgen_path", return_value=out_path), \
         patch("artgen_thumb.make_thumbnail", return_value=thumb_path), \
         patch("media_store.media_store", fake_media_store):
        mw_inst = _make_mock_main_window()
        result = mw.MainWindow._run_transform(mw_inst, record, "ansi-image")

    assert isinstance(result, MediaRecord)
    assert result.media_type == "artgen"
    assert result.generator_type == "ansi-image"
    assert result.file_path == str(out_path)
    assert Path(result.file_path).read_text(encoding="utf-8") == ansi_text
    assert result.media_file_path == str(out_path)  # duck-typed alias, see _create_generate_artgen

    fake_media_store.add.assert_called_once_with(result)
    fake_media_store.ensure_auto_playlists.assert_called_once()

    import json
    params = json.loads(result.params)
    assert params["_source_id"] == record.id
    assert params["_transform"] == "ansi-image"


def test_run_transform_ansi_image_writes_log_file(tmp_path):
    """The ansi-image branch still logs like every other transform."""
    import main_window as mw
    import log_viewer

    orig_log_dir = log_viewer._TRANSFORMS_LOG_DIR
    log_viewer._TRANSFORMS_LOG_DIR = tmp_path / "transforms"

    img_path = str(tmp_path / "src.jpg")
    Path(img_path).write_bytes(b"fake")
    record = _make_record(image_path=img_path, video_path="", media_type="image")

    fake_plugin = MagicMock()
    fake_plugin.image_to_ansi.return_value = "\x1b[38;5;196m█"
    spec = MagicMock(); spec.loader.exec_module = lambda m: None

    out_path = tmp_path / "artgen" / "20260101_000000_abcd1234.ans"
    thumb_path = tmp_path / "artgen" / "thumbnails" / "20260101_000000_abcd1234.png"

    try:
        with patch("importlib.util.spec_from_file_location", return_value=spec), \
             patch("importlib.util.module_from_spec", return_value=fake_plugin), \
             patch("artgen_thumb.make_artgen_path", return_value=out_path), \
             patch("artgen_thumb.make_thumbnail", return_value=thumb_path), \
             patch("media_store.media_store", MagicMock()):
            mw_inst = _make_mock_main_window()
            mw.MainWindow._run_transform(mw_inst, record, "ansi-image")

        logs = list((tmp_path / "transforms").glob("*.log"))
        assert len(logs) == 1
        content = logs[0].read_text()
        assert "transform: ansi-image" in content
        assert "status:    ok" in content
    finally:
        log_viewer._TRANSFORMS_LOG_DIR = orig_log_dir


def test_on_transform_finished_refreshes_artgen_gallery_for_artgen_record(tmp_path):
    """_on_transform_finished branches to the artgen gallery for an artgen
    MediaRecord, WITHOUT touching the native image store/gallery."""
    import main_window as mw
    from media_store import MediaRecord

    rec = MediaRecord(
        id=str(uuid.uuid4()),
        media_type="artgen",
        created_at="2026-01-01T00:00:00+00:00",
        file_path=str(tmp_path / "out.ans"),
        thumbnail_path=str(tmp_path / "out.png"),
        prompt="ANSI art from a sunset over dunes",
        model_id="artgen",
        generator_type="ansi-image",
        params="{}",
        starred=0,
    )
    mw_inst = _make_mock_main_window()

    with patch("gi.repository.GLib.idle_add", lambda fn, *a: fn(*a)):
        result = mw.MainWindow._on_transform_finished(mw_inst, rec)

    mw_inst._artgen_gallery.refresh.assert_called_once()
    mw_inst._store.append.assert_not_called()
    mw_inst._image_gallery.load_history.assert_not_called()
    assert result is False


# ── Review I2: _on_error must not crash when a transform fails while an ────────
#    artgen Create medium is active (artgen has no GalleryWidget) ──────────────
def test_on_error_tolerates_artgen_active_medium():
    import main_window as mw
    obj = mw.MainWindow.__new__(mw.MainWindow)
    obj._gen_gallery = None                       # no native job in flight
    obj._current_medium_source = lambda: "artgen"  # artgen Create medium active
    fake_video = MagicMock()                       # a real GalleryWidget stand-in
    obj._video_gallery = fake_video
    obj._image_gallery = MagicMock()
    obj._animate_gallery = MagicMock()
    obj._artgen_gallery = MagicMock()
    obj._screensaver_uninhibit = lambda: None
    obj._last_error_log_path = None
    obj._create_job_active = False
    status = []
    obj._set_status = lambda m: status.append(m)
    drained = []
    obj._start_next_queued = lambda: drained.append(True)

    # Must NOT raise (previously _gallery_for_type('artgen') ValueError aborted it)
    obj._on_error("Transform 'rmbg' failed: boom")

    assert status and "Error" in status[0]        # user actually saw the error
    assert drained == [True]                        # queue drain still ran
    fake_video.remove_pending.assert_called_once()  # fell back to a real gallery
