"""Tests for model attribution fields in GenerationRecord / HistoryStore."""
import json
import sys
from pathlib import Path

# repo root on path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import pytest
from history_store import GenerationRecord, HistoryStore, HISTORY_FILE


# ── GenerationRecord ──────────────────────────────────────────────────────────

def test_new_video_record_has_model():
    rec = GenerationRecord.new(
        job_id="abc12345",
        prompt="a cat",
        negative_prompt="",
        num_inference_steps=20,
        seed=42,
        model="wan2.2-t2v",
    )
    assert rec.model == "wan2.2-t2v"
    assert rec.extra_meta == {}


def test_new_image_record_has_model():
    rec = GenerationRecord.new_image(
        job_id="def67890",
        prompt="a dog",
        negative_prompt="",
        num_inference_steps=20,
        seed=-1,
        model="flux.1-dev",
    )
    assert rec.model == "flux.1-dev"
    assert rec.extra_meta == {}


def test_new_record_model_defaults_to_empty():
    rec = GenerationRecord.new(
        job_id="abc12345",
        prompt="a cat",
        negative_prompt="",
        num_inference_steps=20,
        seed=42,
    )
    assert rec.model == ""


def test_extra_meta_stored_and_retrieved():
    rec = GenerationRecord.new(
        job_id="aaa00001",
        prompt="test",
        negative_prompt="",
        num_inference_steps=20,
        seed=1,
        model="wan2.2-t2v",
    )
    rec.extra_meta = {"resolution": "720p", "frame_count": 83}
    assert rec.extra_meta["resolution"] == "720p"
    assert rec.extra_meta["frame_count"] == 83


def test_load_legacy_record_without_model(tmp_path, monkeypatch):
    """Records with no 'model' key round-trip through the new SQLite store correctly."""
    import history_store as hs
    import media_store as ms_mod
    from media_store import MediaStore

    # Give this test a fresh, isolated SQLite store in tmp_path
    fresh_ms = MediaStore(tmp_path / "media.db")
    monkeypatch.setattr(ms_mod, "_media_store_singleton", fresh_ms)

    # Patch history_store dir constants so mkdir() goes to tmp_path
    monkeypatch.setattr(hs, "STORAGE_DIR",    tmp_path)
    monkeypatch.setattr(hs, "VIDEOS_DIR",     tmp_path)
    monkeypatch.setattr(hs, "IMAGES_DIR",     tmp_path)
    monkeypatch.setattr(hs, "THUMBNAILS_DIR", tmp_path)
    monkeypatch.setattr(hs.HistoryStore, "_QUEUE_FILE", tmp_path / "queue.json")

    # Build a legacy-style record (no model, no extra_meta) via the dataclass
    # and insert it directly — the new store has no JSON loader for legacy files.
    from history_store import GenerationRecord
    legacy_rec = GenerationRecord(
        id="legacy001",
        prompt="old video",
        negative_prompt="",
        num_inference_steps=20,
        seed=-1,
        video_path="",
        thumbnail_path="",
        created_at="2025-01-01T12:00:00",
        duration_s=120.0,
        # model and extra_meta default to "" and {} respectively
    )

    store = HistoryStore()
    store.append(legacy_rec)

    records = store.all_records()
    assert len(records) == 1
    assert records[0].model == ""
    assert records[0].extra_meta == {}


# ── APIClient.poll_status ─────────────────────────────────────────────────────

from unittest.mock import MagicMock, patch
from api_client import APIClient


def test_poll_status_returns_three_tuple():
    client = APIClient("http://localhost:8000")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "status": "completed",
        "id": "job-abc",
        "output_resolution": "720p",
        "frame_count": 83,
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        status, error, data = client.poll_status("job-abc")

    assert status == "completed"
    assert error is None
    assert data["output_resolution"] == "720p"
    assert data["frame_count"] == 83


def test_poll_status_passes_error_field():
    client = APIClient("http://localhost:8000")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "failed", "error": "OOM"}
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        status, error, data = client.poll_status("job-xyz")

    assert status == "failed"
    assert error == "OOM"
    assert data == {"status": "failed", "error": "OOM"}


# ── _safe_meta helper ─────────────────────────────────────────────────────────

from worker import _safe_meta


def test_safe_meta_strips_images_key():
    data = {
        "status": "completed",
        "images": ["AABBCC=="],   # huge base64 — must be stripped
        "resolution": "1024x1024",
        "frame_count": 1,
    }
    result = _safe_meta(data)
    assert "images" not in result
    assert result["resolution"] == "1024x1024"


def test_safe_meta_strips_b64_keys():
    data = {
        "reference_video_b64": "AABBCC==",
        "reference_image_b64": "DDEEFF==",
        "output_fps": 24,
    }
    result = _safe_meta(data)
    assert "reference_video_b64" not in result
    assert "reference_image_b64" not in result
    assert result["output_fps"] == 24


def test_safe_meta_empty_input():
    assert _safe_meta({}) == {}
