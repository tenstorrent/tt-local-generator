"""
Tests for queue persistence (save_queue / load_queue in HistoryStore).

The queue is saved to queue.json whenever it changes. On restart, queue.json
is reloaded and the items are restored into the in-memory queue.
"""
import json
import sys
from pathlib import Path

# repo root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

import history_store as hs
from history_store import HistoryStore


def _patch_store(monkeypatch, tmp_path):
    """Redirect all HistoryStore paths to tmp_path."""
    monkeypatch.setattr(hs, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(hs, "VIDEOS_DIR", tmp_path)
    monkeypatch.setattr(hs, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(hs, "THUMBNAILS_DIR", tmp_path)
    monkeypatch.setattr(hs, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(hs.HistoryStore, "_QUEUE_FILE", tmp_path / "queue.json")
    return tmp_path / "queue.json"


_SAMPLE_ITEM = {
    "prompt": "a cat running",
    "negative_prompt": "blurry",
    "steps": 20,
    "seed": 42,
    "seed_image_path": "",
    "model_source": "video",
    "guidance_scale": 3.5,
    "ref_video_path": "",
    "ref_char_path": "",
    "animate_mode": "animation",
    "model_id": "wan2.2-t2v",
}


def test_save_queue_writes_json(monkeypatch, tmp_path):
    """save_queue writes a valid JSON array to queue.json."""
    queue_file = _patch_store(monkeypatch, tmp_path)

    store = HistoryStore()
    store.save_queue([_SAMPLE_ITEM])

    assert queue_file.exists(), "queue.json should be created by save_queue"
    data = json.loads(queue_file.read_text())
    assert len(data) == 1
    assert data[0]["prompt"] == "a cat running"


def test_save_queue_empty_clears_file(monkeypatch, tmp_path):
    """save_queue([]) writes an empty array (does not delete the file)."""
    queue_file = _patch_store(monkeypatch, tmp_path)

    store = HistoryStore()
    store.save_queue([_SAMPLE_ITEM])
    store.save_queue([])

    data = json.loads(queue_file.read_text())
    assert data == []


def test_load_queue_returns_saved_items(monkeypatch, tmp_path):
    """load_queue returns the items saved by save_queue."""
    _patch_store(monkeypatch, tmp_path)

    store = HistoryStore()
    store.save_queue([_SAMPLE_ITEM, {**_SAMPLE_ITEM, "prompt": "a dog swimming"}])

    items = store.load_queue()
    assert len(items) == 2
    assert items[0]["prompt"] == "a cat running"
    assert items[1]["prompt"] == "a dog swimming"


def test_load_queue_returns_empty_when_no_file(monkeypatch, tmp_path):
    """load_queue returns [] if queue.json doesn't exist."""
    _patch_store(monkeypatch, tmp_path)

    store = HistoryStore()
    assert store.load_queue() == []


def test_load_queue_returns_empty_on_corrupt_file(monkeypatch, tmp_path):
    """load_queue returns [] if queue.json is corrupt."""
    queue_file = _patch_store(monkeypatch, tmp_path)
    queue_file.write_text("not valid json }")

    store = HistoryStore()
    assert store.load_queue() == []


def test_queue_survives_across_instances(monkeypatch, tmp_path):
    """Queue saved by one store instance is visible to a second (simulates restart)."""
    _patch_store(monkeypatch, tmp_path)

    store1 = HistoryStore()
    store1.save_queue([_SAMPLE_ITEM])

    store2 = HistoryStore()
    items = store2.load_queue()
    assert len(items) == 1
    assert items[0]["prompt"] == "a cat running"


def test_save_queue_atomic_uses_tmp(monkeypatch, tmp_path):
    """save_queue must not leave a .tmp file behind after success."""
    queue_file = _patch_store(monkeypatch, tmp_path)

    store = HistoryStore()
    store.save_queue([_SAMPLE_ITEM])

    tmp = queue_file.with_suffix(".json.tmp")
    assert not tmp.exists(), "queue.json.tmp should be cleaned up by os.replace()"
