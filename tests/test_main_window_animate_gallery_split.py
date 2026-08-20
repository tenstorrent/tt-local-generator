"""
Unit tests for MainWindow._load_history()'s animate/video gallery split.

Post the "AnimateDiff is Video" migration, Wan2.2-Animate records are stamped
media_type="video" (so they show up in the main Video gallery) with
generator_type="animate" as the provenance marker. The Animate Discover tab
should still show those same records via a `generator_type == "animate"`
filter — NOT a `media_type == "animate"` filter, which can never match a
real record post-migration.

These tests route records through a REAL HistoryStore/MediaStore round trip
(append() -> all_records()) instead of stubbing `store.all_records.return_value`
with hand-built GenerationRecords. That distinction matters: a final-review
found `HistoryStore._to_gen` (the method all_records() uses to rebuild a
GenerationRecord from a stored MediaRecord) never passed generator_type=
through, so it silently defaulted to None for every record read back from
storage — the earlier version of these tests never exercised that path at
all (they handed _load_history hand-built records that already carried
generator_type, bypassing _to_gen entirely) and so passed despite the bug.
The real round-trip coverage for _to_gen itself lives in
tests/test_history_store.py::test_to_gen_round_trips_generator_type.

Only the gallery widgets are mocked (no GTK, no real gallery rendering) —
storage and routing are real.
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

from history_store import GenerationRecord, HistoryStore  # noqa: E402


def _real_store(monkeypatch, tmp_path):
    """Redirect history_store/media_store to tmp_path and return a real
    HistoryStore instance, so all_records() runs the real _to_gen() read
    path against a real, isolated MediaStore (mirrors
    tests/test_history_store.py's `_patch_store` fixture)."""
    import media_store as ms_mod
    from media_store import MediaStore
    import history_store as hs

    fresh_ms = MediaStore(tmp_path / "media.db")
    monkeypatch.setattr(ms_mod, "_media_store_singleton", fresh_ms)
    monkeypatch.setattr(hs, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(hs, "VIDEOS_DIR", tmp_path)
    monkeypatch.setattr(hs, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(hs, "THUMBNAILS_DIR", tmp_path)
    return HistoryStore()


def _make_mw_with_store(store):
    """Build a minimal fake MainWindow with just enough wired for _load_history,
    backed by a REAL HistoryStore (not a MagicMock stub)."""
    import main_window as mw

    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)

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


def test_generator_type_animate_record_routed_to_animate_gallery(monkeypatch, tmp_path):
    """A post-migration Wan2.2-Animate record (media_type='video',
    generator_type='animate') must still populate the Animate Discover tab,
    in addition to the Video gallery it's now also a member of."""
    store = _real_store(monkeypatch, tmp_path)
    rec = GenerationRecord.new_animate(job_id="anim1", prompt="a prompt",
                                       negative_prompt="", num_inference_steps=20,
                                       seed=1)
    store.append(rec)

    obj = _make_mw_with_store(store)
    fn = _bind(obj)

    fn()

    obj._animate_gallery.load_history.assert_called_once()
    (animate_recs,), _ = obj._animate_gallery.load_history.call_args
    assert [r.id for r in animate_recs] == ["anim1"]

    # It's also a video record, so it must be present in the Video gallery too.
    obj._video_gallery.load_history.assert_called_once()
    (video_recs,), _ = obj._video_gallery.load_history.call_args
    assert [r.id for r in video_recs] == ["anim1"]


def test_plain_video_record_not_routed_to_animate_gallery(monkeypatch, tmp_path):
    """A plain Wan2.2/AnimateDiff video record (generator_type != 'animate')
    must not show up in the Animate Discover tab."""
    store = _real_store(monkeypatch, tmp_path)
    rec = GenerationRecord.new_animatediff(
        job_id="vid1", prompt="a prompt", negative_prompt="",
        num_inference_steps=6, seed=1,
        video_path=str(tmp_path / "vid1.gif"),
        thumbnail_path=str(tmp_path / "vid1.jpg"),
    )
    store.append(rec)

    obj = _make_mw_with_store(store)
    fn = _bind(obj)

    fn()

    obj._animate_gallery.load_history.assert_not_called()
    obj._video_gallery.load_history.assert_called_once()
    (video_recs,), _ = obj._video_gallery.load_history.call_args
    assert [r.id for r in video_recs] == ["vid1"]
