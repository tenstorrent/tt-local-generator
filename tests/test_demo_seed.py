#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Tests for app/demo_seed.py — loading the bundled demo collection into a
media.db. Uses a throwaway collection + a temp storage dir, so it never
touches the real library or needs a display.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _make_collection(root: Path):
    """A tiny 2-item demo collection: one video, one artgen — with real files."""
    (root / "media").mkdir(parents=True)
    (root / "thumbnails").mkdir(parents=True)
    (root / "media" / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42fake")
    (root / "media" / "art.svg").write_text("<svg/>")
    (root / "thumbnails" / "clip.jpg").write_bytes(b"\xff\xd8\xff\xe0jfif-fake")
    (root / "thumbnails" / "art.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    manifest = {"collection": "demo", "version": 1, "items": [
        {"id": "vid-1", "media_type": "video", "generator_type": None, "model_id": "wan",
         "created_at": "2026-01-01T00:00:00", "starred": 1,
         "media": "media/clip.mp4", "thumbnail": "thumbnails/clip.jpg",
         "params": "{\"steps\": 20}", "prompt": "the raw generation prompt",
         "caption": "A clean display caption."},
        {"id": "art-1", "media_type": "artgen", "generator_type": "skyline", "model_id": "qwen",
         "created_at": "2026-01-02T00:00:00", "starred": 0,
         "media": "media/art.svg", "thumbnail": "thumbnails/art.png",
         "params": "{}", "prompt": "Generate a city skyline SVG (800x450)...TEMPLATE",
         "caption": "A neon skyline at dusk."},
    ]}
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def test_seed_inserts_records_with_caption_as_prompt(tmp_path):
    import demo_seed
    coll = _make_collection(tmp_path / "demo-collection")
    storage = tmp_path / "storage"; storage.mkdir()

    rep = demo_seed.seed_demo(collection_dir=coll, storage_dir=storage)
    assert rep["seeded"] == 2 and rep["skipped"] == 0

    import media_store
    store = media_store.MediaStore(db_path=storage / "media.db")
    recs = {r.id: r for r in store.query(limit=100)}
    assert set(recs) == {"vid-1", "art-1"}
    # caption is what got stored as the display prompt (original prompt is NOT)
    assert recs["vid-1"].prompt == "A clean display caption."
    assert recs["art-1"].prompt == "A neon skyline at dusk."
    # provenance fields preserved
    assert recs["art-1"].generator_type == "skyline"
    assert recs["vid-1"].media_type == "video"
    # files copied into the storage demo dir, and paths point at real files
    assert Path(recs["vid-1"].file_path).exists()
    assert Path(recs["vid-1"].thumbnail_path).exists()
    assert "demo-collection" in recs["vid-1"].file_path


def test_seed_creates_demo_playlist_with_members(tmp_path):
    import demo_seed, media_store
    coll = _make_collection(tmp_path / "demo-collection")
    storage = tmp_path / "storage"; storage.mkdir()
    rep = demo_seed.seed_demo(collection_dir=coll, storage_dir=storage)

    store = media_store.MediaStore(db_path=storage / "media.db")
    pls = {p["name"]: p["id"] for p in store.list_playlists()}
    assert "Demo" in pls
    members = {r.id for r in store.playlist_records(pls["Demo"])}
    assert members == {"vid-1", "art-1"}
    assert rep["playlist_added"] == 2


def test_seed_is_idempotent(tmp_path):
    import demo_seed, media_store
    coll = _make_collection(tmp_path / "demo-collection")
    storage = tmp_path / "storage"; storage.mkdir()

    first = demo_seed.seed_demo(collection_dir=coll, storage_dir=storage)
    second = demo_seed.seed_demo(collection_dir=coll, storage_dir=storage)
    assert first["seeded"] == 2
    assert second["seeded"] == 0 and second["skipped"] == 2
    assert second["playlist_added"] == 0  # no duplicate playlist rows

    store = media_store.MediaStore(db_path=storage / "media.db")
    assert len(store.query(limit=100)) == 2
    pls = {p["name"]: p["id"] for p in store.list_playlists()}
    assert len(store.playlist_records(pls["Demo"])) == 2


def test_resolve_collection_dir_prefers_explicit(tmp_path, monkeypatch):
    import demo_seed
    coll = _make_collection(tmp_path / "demo-collection")
    assert demo_seed.resolve_collection_dir(coll) == coll
    # With both fallbacks absent, a bogus explicit path raises.
    monkeypatch.setattr(demo_seed, "_INSTALLED_DIR", tmp_path / "no-installed")
    monkeypatch.setattr(demo_seed, "_REPO_DIR", tmp_path / "no-repo")
    with pytest.raises(FileNotFoundError):
        demo_seed.resolve_collection_dir(tmp_path / "nonexistent")


def test_real_shipped_collection_seeds_end_to_end(tmp_path):
    """The ACTUAL demo-collection/ in the repo loads cleanly: all 27 items,
    every media + thumbnail file present and copied, all in the Demo playlist."""
    import demo_seed, media_store
    repo_coll = Path(__file__).parent.parent / "demo-collection"
    if not (repo_coll / "manifest.json").exists():
        pytest.skip("demo-collection not present")
    storage = tmp_path / "storage"; storage.mkdir()
    rep = demo_seed.seed_demo(collection_dir=repo_coll, storage_dir=storage)

    manifest = json.loads((repo_coll / "manifest.json").read_text())
    n = len(manifest["items"])
    assert rep["seeded"] == n
    store = media_store.MediaStore(db_path=storage / "media.db")
    recs = store.query(limit=1000)
    assert len(recs) == n
    for r in recs:
        assert Path(r.file_path).exists(), f"missing seeded media for {r.id}"
        if r.thumbnail_path:
            assert Path(r.thumbnail_path).exists()
    pls = {p["name"]: p["id"] for p in store.list_playlists()}
    assert len(store.playlist_records(pls["Demo"])) == n
