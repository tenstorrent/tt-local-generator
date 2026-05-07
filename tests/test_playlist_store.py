"""Unit tests for PlaylistStore — no GTK required."""
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import pytest
import media_store as ms_mod
from media_store import MediaStore, MediaRecord
from playlist_store import PlaylistStore, Playlist
from datetime import datetime, timezone


# ── Helpers ────────────────────────────────────────────────────────────────────

def _media_rec(id: str, media_type: str = "video") -> MediaRecord:
    """Build a minimal MediaRecord suitable for inserting into a test DB."""
    return MediaRecord(
        id=id,
        media_type=media_type,
        created_at=datetime.now(timezone.utc).isoformat(),
        file_path="",
        thumbnail_path="",
        prompt="",
        model_id="",
        generator_type=None,
        params="{}",
        starred=0,
    )


# ── Fixture ────────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    """
    Return a PlaylistStore backed by a fresh in-tmp_path MediaStore.

    The module-level singleton is replaced via monkeypatch so that the lazy
    proxy (_MediaStoreProxy) resolves to the test instance for the duration of
    the test.  The patch is automatically undone by pytest after the test.
    """
    fresh_ms = MediaStore(tmp_path / "media.db")
    monkeypatch.setattr(ms_mod, "_media_store_singleton", fresh_ms)
    return PlaylistStore()


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_create_playlist(store):
    pl = store.create("Space Adventures")
    assert pl.name == "Space Adventures"
    assert pl.id
    assert pl.record_ids == []
    # create() passes auto_gen=False to media_store; Playlist.auto_gen default is True
    # but the stored value is False — get() should reflect the stored value.
    assert store.get(pl.id).auto_gen is False
    assert len(store.all()) == 1


def test_create_strips_whitespace(store):
    pl = store.create("  Cityscapes  ")
    assert pl.name == "Cityscapes"


def test_get_returns_correct_playlist(store):
    pl = store.create("Alpha")
    store.create("Beta")
    found = store.get(pl.id)
    assert found is not None
    assert found.id == pl.id
    assert found.name == "Alpha"


def test_get_missing_returns_none(store):
    assert store.get("nonexistent-id") is None


def test_rename(store):
    pl = store.create("Old Name")
    result = store.rename(pl.id, "New Name")
    assert result is True
    assert store.get(pl.id).name == "New Name"


def test_rename_missing_returns_false(store):
    assert store.rename("bad-id", "X") is False


def test_delete(store):
    pl = store.create("Temp")
    assert store.delete(pl.id) is True
    assert store.get(pl.id) is None
    assert store.all() == []


def test_delete_missing_returns_false(store):
    assert store.delete("bad-id") is False


def test_add_records(tmp_path, monkeypatch):
    """add_records requires matching media rows (FK constraint on playlist_items)."""
    fresh_ms = MediaStore(tmp_path / "media.db")
    monkeypatch.setattr(ms_mod, "_media_store_singleton", fresh_ms)
    store = PlaylistStore()

    pl = store.create("My List")
    # Insert the media rows first so FK constraints are satisfied.
    for rid in ["id1", "id2", "id3"]:
        fresh_ms.add(_media_rec(rid))

    added = store.add_records(pl.id, ["id1", "id2", "id3"])
    assert added == 3
    assert store.get(pl.id).record_ids == ["id1", "id2", "id3"]


def test_add_records_deduplicates(tmp_path, monkeypatch):
    fresh_ms = MediaStore(tmp_path / "media.db")
    monkeypatch.setattr(ms_mod, "_media_store_singleton", fresh_ms)
    store = PlaylistStore()

    pl = store.create("My List")
    for rid in ["id1", "id2", "id3"]:
        fresh_ms.add(_media_rec(rid))

    store.add_records(pl.id, ["id1", "id2"])
    added = store.add_records(pl.id, ["id2", "id3"])  # id2 is a duplicate
    assert added == 1
    assert store.get(pl.id).record_ids == ["id1", "id2", "id3"]


def test_add_records_missing_playlist(store):
    assert store.add_records("bad-id", ["id1"]) == 0


def test_remove_record(tmp_path, monkeypatch):
    fresh_ms = MediaStore(tmp_path / "media.db")
    monkeypatch.setattr(ms_mod, "_media_store_singleton", fresh_ms)
    store = PlaylistStore()

    pl = store.create("My List")
    for rid in ["id1", "id2", "id3"]:
        fresh_ms.add(_media_rec(rid))
    store.add_records(pl.id, ["id1", "id2", "id3"])

    assert store.remove_record(pl.id, "id2") is True
    assert store.get(pl.id).record_ids == ["id1", "id3"]


def test_remove_record_not_present(store):
    pl = store.create("My List")
    assert store.remove_record(pl.id, "nope") is False


def test_set_auto_gen(store):
    pl = store.create("My List")
    # The playlist was stored with auto_gen=False (create() passes False).
    assert store.get(pl.id).auto_gen is False
    store.set_auto_gen(pl.id, True)
    assert store.get(pl.id).auto_gen is True
    store.set_auto_gen(pl.id, False)
    assert store.get(pl.id).auto_gen is False


def test_set_auto_gen_missing(store):
    assert store.set_auto_gen("bad-id", False) is False


def test_contains(tmp_path, monkeypatch):
    fresh_ms = MediaStore(tmp_path / "media.db")
    monkeypatch.setattr(ms_mod, "_media_store_singleton", fresh_ms)
    store = PlaylistStore()

    pl = store.create("My List")
    for rid in ["id1", "id2"]:
        fresh_ms.add(_media_rec(rid))
    store.add_records(pl.id, ["id1", "id2"])

    loaded = store.get(pl.id)
    assert loaded.contains("id1") is True
    assert loaded.contains("id3") is False


def test_purge_deleted_records(tmp_path, monkeypatch):
    fresh_ms = MediaStore(tmp_path / "media.db")
    monkeypatch.setattr(ms_mod, "_media_store_singleton", fresh_ms)
    store = PlaylistStore()

    pl1 = store.create("A")
    pl2 = store.create("B")
    for rid in ["id1", "id2", "id3", "id4"]:
        fresh_ms.add(_media_rec(rid))
    store.add_records(pl1.id, ["id1", "id2", "id3"])
    store.add_records(pl2.id, ["id2", "id4"])

    # id2 and id4 are "deleted" from the history store.
    pruned = store.purge_deleted_records(valid_ids={"id1", "id3"})
    assert pruned == 3  # id2 from pl1, id2+id4 from pl2
    assert store.get(pl1.id).record_ids == ["id1", "id3"]
    assert store.get(pl2.id).record_ids == []


def test_all_preserves_insertion_order(store):
    """
    SQLite orders by created_at; add a small sleep between creates so timestamps
    are distinct even when the loop runs in well under a millisecond.
    """
    names = ["Charlie", "Alpha", "Beta"]
    for n in names:
        store.create(n)
        time.sleep(0.001)
    returned_names = [pl.name for pl in store.all()]
    assert returned_names == names


def test_playlist_store_persistence(tmp_path, monkeypatch):
    """
    Verify that a playlist created via one PlaylistStore instance is visible
    through a second instance that shares the same underlying MediaStore.
    """
    fresh_ms = MediaStore(tmp_path / "media.db")
    monkeypatch.setattr(ms_mod, "_media_store_singleton", fresh_ms)

    store1 = PlaylistStore()
    pl = store1.create("Persist Test")
    store1.set_auto_gen(pl.id, True)

    # A second PlaylistStore talking to the same patched singleton must see the data.
    store2 = PlaylistStore()
    assert len(store2.all()) == 1
    loaded = store2.get(pl.id)
    assert loaded is not None
    assert loaded.name == "Persist Test"
    assert loaded.auto_gen is True
