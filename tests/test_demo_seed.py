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


def test_seed_creates_welcome_playlist_with_members(tmp_path):
    import demo_seed, media_store
    coll = _make_collection(tmp_path / "demo-collection")
    storage = tmp_path / "storage"; storage.mkdir()
    rep = demo_seed.seed_demo(collection_dir=coll, storage_dir=storage)

    store = media_store.MediaStore(db_path=storage / "media.db")
    pls = {p["name"]: p["id"] for p in store.list_playlists()}
    assert "Welcome to tt-local-generator" in pls
    members = {r.id for r in store.playlist_records(pls["Welcome to tt-local-generator"])}
    assert members == {"vid-1", "art-1"}
    assert rep["playlist_added"] == 2


def test_seed_is_idempotent(tmp_path):
    import demo_seed, media_store
    coll = _make_collection(tmp_path / "demo-collection")
    storage = tmp_path / "storage"; storage.mkdir()

    first = demo_seed.seed_demo(collection_dir=coll, storage_dir=storage)
    second = demo_seed.seed_demo(collection_dir=coll, storage_dir=storage)
    assert first["seeded"] == 2 and first["already_seeded"] is False
    # Re-run of the SAME collection version short-circuits on the
    # `.demo_seed_version` marker before touching the store (Josh PR#24 #5).
    assert second["already_seeded"] is True
    assert second["seeded"] == 0 and second["playlist_added"] == 0

    store = media_store.MediaStore(db_path=storage / "media.db")
    assert len(store.query(limit=100)) == 2
    pls = {p["name"]: p["id"] for p in store.list_playlists()}
    assert len(store.playlist_records(pls["Welcome to tt-local-generator"])) == 2


def test_seed_strips_build_machine_paths_so_to_gen_resolves(tmp_path):
    """Josh PR#24 #1 (blocker): the manifest's params carry the GENERATING
    machine's absolute video_path/image_path. history_store._to_gen PREFERS
    those over file_path, so if left in they resolve to dead links on every
    other machine. Seeding must strip them so _to_gen falls back to the copied
    file_path — the media that actually exists on the install."""
    import demo_seed, media_store, history_store
    coll = tmp_path / "demo-collection"
    (coll / "media").mkdir(parents=True)
    (coll / "thumbnails").mkdir(parents=True)
    (coll / "media" / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42fake")
    (coll / "media" / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (coll / "thumbnails" / "clip.jpg").write_bytes(b"\xff\xd8\xff\xe0fake")
    manifest = {"collection": "demo", "version": 1, "items": [
        {"id": "vid-1", "media_type": "video", "generator_type": None, "model_id": "wan",
         "created_at": "2026-01-01T00:00:00",
         "media": "media/clip.mp4", "thumbnail": "thumbnails/clip.jpg",
         # params carry a build-machine absolute path that does NOT exist here
         "params": json.dumps({"steps": 20,
                               "video_path": "/home/builder/.local/share/tt-video-gen/videos/DEAD.mp4"}),
         "caption": "A clip."},
        {"id": "img-1", "media_type": "image", "generator_type": None, "model_id": "flux",
         "created_at": "2026-01-02T00:00:00",
         "media": "media/pic.png", "thumbnail": "",
         "params": json.dumps({"image_path": "/home/builder/.local/share/tt-video-gen/images/DEAD.png"}),
         "caption": "A pic."},
    ]}
    (coll / "manifest.json").write_text(json.dumps(manifest))
    storage = tmp_path / "storage"; storage.mkdir()
    demo_seed.seed_demo(collection_dir=coll, storage_dir=storage)

    store = media_store.MediaStore(db_path=storage / "media.db")
    recs = {r.id: r for r in store.query(limit=100)}
    # The dead build-machine paths are stripped from the stored params …
    assert "video_path" not in recs["vid-1"].params_dict
    assert "image_path" not in recs["img-1"].params_dict
    # … so _to_gen (the app's actual resolution path) falls back to file_path,
    # which points at the copied media that really exists on this machine.
    gen_vid = history_store.HistoryStore._to_gen(recs["vid-1"])
    gen_img = history_store.HistoryStore._to_gen(recs["img-1"])
    assert gen_vid.video_path == recs["vid-1"].file_path
    assert Path(gen_vid.video_path).exists()
    assert gen_img.image_path == recs["img-1"].file_path
    assert Path(gen_img.image_path).exists()


def test_deleted_demo_art_not_reseeded_until_newer_version(tmp_path):
    """Josh PR#24 #5: postinst re-runs seed_demo on every upgrade. The
    `.demo_seed_version` marker must stop a user-deleted demo item from being
    resurrected on the next run — UNLESS the collection ships a newer version."""
    import demo_seed, media_store
    coll = _make_collection(tmp_path / "demo-collection")
    storage = tmp_path / "storage"; storage.mkdir()
    demo_seed.seed_demo(collection_dir=coll, storage_dir=storage)

    # User deletes one demo item through the app.
    store = media_store.MediaStore(db_path=storage / "media.db")
    store.delete("art-1")
    assert {r.id for r in store.query(limit=100)} == {"vid-1"}

    # Re-run (same version) — short-circuits, does NOT resurrect art-1.
    rep = demo_seed.seed_demo(collection_dir=coll, storage_dir=storage)
    assert rep["already_seeded"] is True
    store2 = media_store.MediaStore(db_path=storage / "media.db")
    assert {r.id for r in store2.query(limit=100)} == {"vid-1"}

    # Ship a NEWER collection version → the marker no longer matches, so it
    # re-seeds (bringing the deleted item back is intended for a real update).
    man = json.loads((coll / "manifest.json").read_text())
    man["version"] = 2
    (coll / "manifest.json").write_text(json.dumps(man))
    rep2 = demo_seed.seed_demo(collection_dir=coll, storage_dir=storage)
    assert rep2["already_seeded"] is False and rep2["seeded"] == 1
    store3 = media_store.MediaStore(db_path=storage / "media.db")
    assert {r.id for r in store3.query(limit=100)} == {"vid-1", "art-1"}


def test_seed_does_not_favorite_shipped_art(tmp_path):
    """Shipped art is the DEFAULT, not the user's pick — even a manifest item
    marked starred=1 seeds as starred=0 (grouped by the Welcome playlist)."""
    import demo_seed, media_store
    coll = _make_collection(tmp_path / "demo-collection")  # vid-1 has starred=1
    storage = tmp_path / "storage"; storage.mkdir()
    demo_seed.seed_demo(collection_dir=coll, storage_dir=storage)
    store = media_store.MediaStore(db_path=storage / "media.db")
    recs = {r.id: r for r in store.query(limit=100)}
    assert recs["vid-1"].starred == 0


def test_force_replaces_existing_record(tmp_path):
    """--force actually replaces a record (MediaStore.add is INSERT-OR-IGNORE,
    so a delete-then-insert is required for the new value to take)."""
    import demo_seed, media_store
    coll = _make_collection(tmp_path / "demo-collection")
    storage = tmp_path / "storage"; storage.mkdir()
    demo_seed.seed_demo(collection_dir=coll, storage_dir=storage)

    # Change vid-1's caption in the manifest, then re-seed WITHOUT force → no-op.
    man = json.loads((coll / "manifest.json").read_text())
    man["items"][0]["caption"] = "UPDATED caption"
    (coll / "manifest.json").write_text(json.dumps(man))
    demo_seed.seed_demo(collection_dir=coll, storage_dir=storage)
    store = media_store.MediaStore(db_path=storage / "media.db")
    assert {r.id: r for r in store.query(limit=100)}["vid-1"].prompt == "A clean display caption."

    # Now WITH force → the record is replaced and the new caption takes effect.
    rep = demo_seed.seed_demo(collection_dir=coll, storage_dir=storage, force=True)
    assert rep["seeded"] == 2
    store2 = media_store.MediaStore(db_path=storage / "media.db")
    recs = {r.id: r for r in store2.query(limit=100)}
    assert recs["vid-1"].prompt == "UPDATED caption"
    assert len(recs) == 2  # no duplicate rows


def test_missing_media_is_skipped_not_inserted(tmp_path):
    """A manifest item whose media file is missing on disk is skipped (counted
    in `missing`), never inserted as a record pointing at a nonexistent file."""
    import demo_seed, media_store
    coll = _make_collection(tmp_path / "demo-collection")
    (coll / "media" / "art.svg").unlink()  # art-1's media now missing
    storage = tmp_path / "storage"; storage.mkdir()
    rep = demo_seed.seed_demo(collection_dir=coll, storage_dir=storage)
    assert rep["seeded"] == 1 and rep["missing"] == 1
    store = media_store.MediaStore(db_path=storage / "media.db")
    ids = {r.id for r in store.query(limit=100)}
    assert ids == {"vid-1"}  # art-1 not inserted


def test_resolve_collection_dir_prefers_explicit(tmp_path, monkeypatch):
    import demo_seed
    coll = _make_collection(tmp_path / "demo-collection")
    assert demo_seed.resolve_collection_dir(coll) == coll
    # With XDG dirs pointing nowhere and the repo fallback absent, a bogus
    # explicit path raises.
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "no-share"))
    monkeypatch.setattr(demo_seed, "_REPO_DIR", tmp_path / "no-repo")
    with pytest.raises(FileNotFoundError):
        demo_seed.resolve_collection_dir(tmp_path / "nonexistent")


def test_resolve_collection_dir_found_via_xdg_data_dirs(tmp_path, monkeypatch):
    """The .deb ships to /usr/share/tt-local-generator/; discovery walks
    $XDG_DATA_DIRS. A data dir holding tt-local-generator/demo-collection is
    found, and an earlier dir (like /usr/local/share) wins over a later one."""
    import demo_seed
    local_share = tmp_path / "local_share"          # stands in for /usr/local/share
    usr_share = tmp_path / "usr_share"              # stands in for /usr/share
    _make_collection(usr_share / "tt-local-generator" / "demo-collection")
    monkeypatch.setenv("XDG_DATA_DIRS", f"{local_share}:{usr_share}")
    monkeypatch.setattr(demo_seed, "_REPO_DIR", tmp_path / "no-repo")
    # Only usr_share has it → found there.
    assert demo_seed.resolve_collection_dir() == usr_share / "tt-local-generator" / "demo-collection"
    # Now put one in the earlier (local_share) dir → it takes precedence.
    _make_collection(local_share / "tt-local-generator" / "demo-collection")
    assert demo_seed.resolve_collection_dir() == local_share / "tt-local-generator" / "demo-collection"


def test_real_shipped_collection_seeds_end_to_end(tmp_path):
    """The ACTUAL demo-collection/ in the repo loads cleanly: all 27 items,
    every media + thumbnail file present and copied, all in the Welcome playlist."""
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
    assert len(store.playlist_records(pls["Welcome to tt-local-generator"])) == n
