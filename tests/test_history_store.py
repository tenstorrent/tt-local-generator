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


def _make_workflow_record(rid, model_id="workflow"):
    """Return a MediaRecord whose model_id looks like a workflow-runner artifact."""
    from media_store import MediaRecord
    import json
    return MediaRecord(
        id=rid,
        media_type="video",
        created_at="2025-06-01T00:00:00",
        file_path="",
        thumbnail_path="",
        prompt="1964 World's Fair workflow output",
        model_id=model_id,
        generator_type=None,
        params=json.dumps({"workflow": "1964-worlds-fair"}),
        starred=0,
    )


def _by_model_counts(records):
    """
    Replicate the By Model grouping logic from main_window._rebuild_playlists_menu
    without importing GTK.  Returns {model_id: count} for non-image, non-workflow
    records so tests can assert on it independently of the GTK widget layer.
    """
    counts: dict[str, int] = {}
    for r in records:
        mid = getattr(r, "model", "") or ""
        # Mirror the filter added in main_window.py: skip workflow-runner records.
        if mid.startswith("workflow"):
            continue
        if mid and getattr(r, "media_type", "video") != "image":
            counts[mid] = counts.get(mid, 0) + 1
    return counts


def test_by_model_excludes_workflow_records(monkeypatch, tmp_path):
    """
    Workflow-runner records (model_id starts with "workflow") must not appear in
    the By Model playlist section.

    This test exercises the same filter logic that lives in
    main_window._rebuild_playlists_menu without importing GTK.
    """
    import media_store as ms_mod

    _patch_store(monkeypatch, tmp_path)
    store = HistoryStore()

    # Normal inference record — should appear in By Model.
    normal_rec = _sample_record()           # model="wan2.2-t2v"
    store.append(normal_rec)

    # Workflow artifact with the canonical model_id written by run_workflow.sh.
    ms_mod._media_store_singleton.add(_make_workflow_record("wf-001", "workflow"))

    # Workflow artifact with a versioned variant (future-proof guard).
    ms_mod._media_store_singleton.add(_make_workflow_record("wf-002", "workflow-v2"))

    records = store.all_records()

    # all_records() must return all three (workflow records are in the DB).
    assert len(records) == 3

    counts = _by_model_counts(records)

    # Only the normal model must appear; workflow variants must be absent.
    assert "wan2.2-t2v" in counts,        "normal record missing from By Model counts"
    assert counts["wan2.2-t2v"] == 1,     "wrong count for wan2.2-t2v"
    assert "workflow" not in counts,       "bare 'workflow' model_id leaked into By Model"
    assert "workflow-v2" not in counts,    "versioned workflow model_id leaked into By Model"


def test_new_animatediff_and_animate_are_video_with_generator_type():
    """AnimateDiff/Animate record factories must write media_type='video' plus a
    generator_type provenance stamp, not their own bespoke media_type strings."""
    ad = GenerationRecord.new_animatediff(job_id="j1", prompt="p", negative_prompt="",
                                          num_inference_steps=6, seed=42,
                                          video_path="/x/a.gif", thumbnail_path="/x/a.png")
    assert ad.media_type == "video"
    assert ad.generator_type == "animatediff"
    assert ad.video_path.endswith(".gif")

    an = GenerationRecord.new_animate(job_id="j2", prompt="p", negative_prompt="",
                                      num_inference_steps=20, seed=1)
    assert an.media_type == "video"
    assert an.generator_type == "animate"
    assert an.video_path.endswith(".mp4")


def test_append_persists_generator_type(monkeypatch, tmp_path):
    """HistoryStore.append() must persist record.generator_type into media_store,
    not hardcode None — otherwise provenance is lost the moment a record round-trips."""
    _patch_store(monkeypatch, tmp_path)
    from media_store import media_store as _ms

    store = HistoryStore()
    rec = GenerationRecord.new_animatediff(job_id="j3", prompt="p", negative_prompt="",
                                           num_inference_steps=6, seed=42,
                                           video_path="/x/b.gif", thumbnail_path="/x/b.png")
    store.append(rec)

    got = _ms.get("j3")
    assert got.media_type == "video"
    assert got.generator_type == "animatediff"


def test_to_gen_round_trips_generator_type(monkeypatch, tmp_path):
    """HistoryStore._to_gen() must carry generator_type from the underlying
    MediaRecord back onto the reconstructed GenerationRecord.

    This is the real READ path used by all_records()/delete() — previously
    _to_gen() built GenerationRecord(...) without passing generator_type=,
    so it silently defaulted to None for every record, permanently breaking
    any caller that filters all_records() by generator_type (the Animate
    Discover tab, the attractor's animate-input chaining)."""
    _patch_store(monkeypatch, tmp_path)

    store = HistoryStore()
    rec = GenerationRecord.new_animate(job_id="j4", prompt="p", negative_prompt="",
                                       num_inference_steps=20, seed=1)
    store.append(rec)

    reloaded = store.all_records()
    assert len(reloaded) == 1
    assert reloaded[0].generator_type == "animate"
