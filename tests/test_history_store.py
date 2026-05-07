"""
Tests for the thin HistoryStore wrapper that delegates to media_store (SQLite).

JSON-specific tests (atomic writes, corrupt-file backup, backward-compat loading)
have been removed because storage is now in media.db, not history.json.

Tests kept:
  - test_append_and_reload  : appending a record makes it visible to a new instance
  - test_delete_persists    : deleting a record removes it from storage permanently
"""
import sys
from pathlib import Path

# repo root on path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import history_store as hs
from history_store import GenerationRecord, HistoryStore


def _patch_store(monkeypatch, tmp_path):
    """
    Redirect all storage to tmp_path — covers both history_store and media_store.

    Each test gets a fresh MediaStore backed by tmp_path/media.db so tests
    are fully isolated and never touch ~/.local/share/tt-video-gen/media.db.
    """
    import media_store as ms_mod
    from media_store import MediaStore

    # Give each test a fresh MediaStore backed by tmp_path
    fresh_ms = MediaStore(tmp_path / "media.db")
    monkeypatch.setattr(ms_mod, "_media_store_singleton", fresh_ms)

    # Patch history_store's own dir constants (for mkdir and _QUEUE_FILE)
    monkeypatch.setattr(hs, "STORAGE_DIR",    tmp_path)
    monkeypatch.setattr(hs, "VIDEOS_DIR",     tmp_path)
    monkeypatch.setattr(hs, "IMAGES_DIR",     tmp_path)
    monkeypatch.setattr(hs, "THUMBNAILS_DIR", tmp_path)
    monkeypatch.setattr(hs.HistoryStore, "_QUEUE_FILE", tmp_path / "queue.json")

    return tmp_path / "history.json"   # kept for call-site compatibility


def _sample_record():
    return GenerationRecord.new(
        job_id="test00001",
        prompt="a cat",
        negative_prompt="",
        num_inference_steps=20,
        seed=42,
        model="wan2.2-t2v",
    )


def test_append_and_reload(monkeypatch, tmp_path):
    """Records written by one store instance are loaded correctly by a second."""
    _patch_store(monkeypatch, tmp_path)

    store1 = HistoryStore()
    rec = _sample_record()
    store1.append(rec)

    # A second instance shares the same patched media_store singleton, so it
    # sees the same SQLite DB without needing to re-read any file.
    store2 = HistoryStore()
    records = store2.all_records()
    assert len(records) == 1
    assert records[0].id == rec.id
    assert records[0].prompt == "a cat"


def test_delete_persists(monkeypatch, tmp_path):
    """Deleting a record removes it from the underlying media_store permanently."""
    _patch_store(monkeypatch, tmp_path)

    store = HistoryStore()
    rec = _sample_record()
    store.append(rec)
    store.delete(rec.id)

    store2 = HistoryStore()
    assert store2.all_records() == []


def test_len_excludes_artgen(monkeypatch, tmp_path):
    """len(store) must count only non-artgen records."""
    import media_store as ms_mod

    _patch_store(monkeypatch, tmp_path)

    store = HistoryStore()

    # Insert a normal video record via append()
    rec = _sample_record()
    store.append(rec)

    # Insert an artgen record directly into the patched MediaStore singleton
    from media_store import MediaRecord
    artgen_rec = MediaRecord(
        id="artgen-001",
        media_type="artgen",
        created_at="2025-01-01T00:00:00",
        file_path="",
        thumbnail_path="",
        prompt="some art prompt",
        model_id="sdxl",
        generator_type="sdxl",
        params="{}",
        starred=0,
    )
    ms_mod._media_store_singleton.add(artgen_rec)

    # len() must only count the video record, not the artgen one
    assert len(store) == 1
