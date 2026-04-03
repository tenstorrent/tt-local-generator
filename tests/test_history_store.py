"""
Tests for HistoryStore hardening:
- Atomic writes (tmp + rename) prevent corruption on crash
- Corrupt history.json is backed up before resetting
- Backward-compatible loading (missing optional fields)
"""
import json
import sys
from pathlib import Path

# repo root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import history_store as hs
from history_store import GenerationRecord, HistoryStore


def _patch_store(monkeypatch, tmp_path):
    """Redirect all HistoryStore paths to tmp_path."""
    monkeypatch.setattr(hs, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(hs, "VIDEOS_DIR", tmp_path)
    monkeypatch.setattr(hs, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(hs, "THUMBNAILS_DIR", tmp_path)
    hist = tmp_path / "history.json"
    monkeypatch.setattr(hs, "HISTORY_FILE", hist)
    # Also patch the class-level _QUEUE_FILE reference
    monkeypatch.setattr(hs.HistoryStore, "_QUEUE_FILE", tmp_path / "queue.json")
    return hist


def _sample_record():
    return GenerationRecord.new(
        job_id="test00001",
        prompt="a cat",
        negative_prompt="",
        num_inference_steps=20,
        seed=42,
        model="wan2.2-t2v",
    )


def test_save_writes_tmp_then_renames(monkeypatch, tmp_path):
    """_save() uses atomic rename: history.json.tmp must not exist after save."""
    hist = _patch_store(monkeypatch, tmp_path)

    store = HistoryStore()
    store.append(_sample_record())

    assert hist.exists(), "history.json should exist after append"
    assert not (tmp_path / "history.json.tmp").exists(), (
        "history.json.tmp should be removed by os.replace()"
    )


def test_atomic_write_leaves_good_file_on_partial_failure(monkeypatch, tmp_path):
    """If the tmp write fails, the original history.json is untouched."""
    import os
    hist = _patch_store(monkeypatch, tmp_path)

    # Write an initial good history
    good_data = [{"id": "good001", "prompt": "initial"}]
    hist.write_text(json.dumps(good_data))

    store = HistoryStore()
    # Force os.replace to fail, simulating a disk-full scenario
    original_replace = os.replace

    def fail_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError):
        store._save()

    monkeypatch.setattr(os, "replace", original_replace)

    # history.json should still contain the original good data
    content = json.loads(hist.read_text())
    assert content == good_data, "history.json should be untouched after failed write"


def test_corrupt_history_backed_up(monkeypatch, tmp_path):
    """A corrupt history.json is copied to history.json.bak before resetting."""
    hist = _patch_store(monkeypatch, tmp_path)
    bak = tmp_path / "history.json.bak"

    hist.write_text("not valid json {{{")

    store = HistoryStore()

    assert store.all_records() == [], "Store should start empty after corrupt load"
    assert bak.exists(), "history.json.bak should have been created"
    assert bak.read_text() == "not valid json {{{", (
        "Backup should contain the original corrupt content"
    )


def test_missing_fields_backward_compat(monkeypatch, tmp_path):
    """Records without newer optional fields load without error."""
    hist = _patch_store(monkeypatch, tmp_path)
    legacy = [
        {
            "id": "legacy001",
            "prompt": "old video",
            "negative_prompt": "",
            "num_inference_steps": 20,
            "seed": -1,
            "video_path": "",
            "thumbnail_path": "",
            "created_at": "2025-01-01T12:00:00",
            "duration_s": 120.0,
            # no model, extra_meta, media_type, image_path, guidance_scale, seed_image_path
        }
    ]
    hist.write_text(json.dumps(legacy))

    store = HistoryStore()
    records = store.all_records()

    assert len(records) == 1
    assert records[0].model == ""
    assert records[0].extra_meta == {}
    assert records[0].media_type == "video"
    assert records[0].seed_image_path == ""


def test_append_and_reload(monkeypatch, tmp_path):
    """Records written by one store instance are loaded correctly by a second."""
    hist = _patch_store(monkeypatch, tmp_path)

    store1 = HistoryStore()
    rec = _sample_record()
    store1.append(rec)

    store2 = HistoryStore()
    records = store2.all_records()
    assert len(records) == 1
    assert records[0].id == rec.id
    assert records[0].prompt == "a cat"


def test_delete_persists(monkeypatch, tmp_path):
    """Deleting a record removes it from disk."""
    _patch_store(monkeypatch, tmp_path)

    store = HistoryStore()
    rec = _sample_record()
    store.append(rec)
    store.delete(rec.id)

    store2 = HistoryStore()
    assert store2.all_records() == []
