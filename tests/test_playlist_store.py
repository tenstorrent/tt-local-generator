"""Unit tests for PlaylistStore — no GTK required."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _fresh_store(tmp_path):
    """Return a PlaylistStore backed by a temp file."""
    from playlist_store import PlaylistStore
    store = PlaylistStore.__new__(PlaylistStore)
    store._playlists = []
    # Patch the module-level paths so saves go to tmp_path
    pl_file = tmp_path / "playlists.json"
    with patch("playlist_store.PLAYLISTS_FILE", pl_file), \
         patch("playlist_store.STORAGE_DIR", tmp_path):
        store._load = lambda: None  # no-op load
        store._save_real = store._save
        # Monkeypatch _save to use the tmp path
        def _save():
            data = json.dumps(
                [{"id": p.id, "name": p.name, "record_ids": p.record_ids, "auto_gen": p.auto_gen}
                 for p in store._playlists],
                indent=2,
            ) + "\n"
            pl_file.write_text(data)
        store._save = _save
    return store


def test_create_playlist(tmp_path):
    store = _fresh_store(tmp_path)
    pl = store.create("Space Adventures")
    assert pl.name == "Space Adventures"
    assert pl.id
    assert pl.record_ids == []
    assert pl.auto_gen is True
    assert len(store.all()) == 1


def test_create_strips_whitespace(tmp_path):
    store = _fresh_store(tmp_path)
    pl = store.create("  Cityscapes  ")
    assert pl.name == "Cityscapes"


def test_get_returns_correct_playlist(tmp_path):
    store = _fresh_store(tmp_path)
    pl = store.create("Alpha")
    store.create("Beta")
    found = store.get(pl.id)
    assert found is pl


def test_get_missing_returns_none(tmp_path):
    store = _fresh_store(tmp_path)
    assert store.get("nonexistent-id") is None


def test_rename(tmp_path):
    store = _fresh_store(tmp_path)
    pl = store.create("Old Name")
    result = store.rename(pl.id, "New Name")
    assert result is True
    assert store.get(pl.id).name == "New Name"


def test_rename_missing_returns_false(tmp_path):
    store = _fresh_store(tmp_path)
    assert store.rename("bad-id", "X") is False


def test_delete(tmp_path):
    store = _fresh_store(tmp_path)
    pl = store.create("Temp")
    assert store.delete(pl.id) is True
    assert store.get(pl.id) is None
    assert store.all() == []


def test_delete_missing_returns_false(tmp_path):
    store = _fresh_store(tmp_path)
    assert store.delete("bad-id") is False


def test_add_records(tmp_path):
    store = _fresh_store(tmp_path)
    pl = store.create("My List")
    added = store.add_records(pl.id, ["id1", "id2", "id3"])
    assert added == 3
    assert store.get(pl.id).record_ids == ["id1", "id2", "id3"]


def test_add_records_deduplicates(tmp_path):
    store = _fresh_store(tmp_path)
    pl = store.create("My List")
    store.add_records(pl.id, ["id1", "id2"])
    added = store.add_records(pl.id, ["id2", "id3"])  # id2 is duplicate
    assert added == 1
    assert store.get(pl.id).record_ids == ["id1", "id2", "id3"]


def test_add_records_missing_playlist(tmp_path):
    store = _fresh_store(tmp_path)
    assert store.add_records("bad-id", ["id1"]) == 0


def test_remove_record(tmp_path):
    store = _fresh_store(tmp_path)
    pl = store.create("My List")
    store.add_records(pl.id, ["id1", "id2", "id3"])
    assert store.remove_record(pl.id, "id2") is True
    assert store.get(pl.id).record_ids == ["id1", "id3"]


def test_remove_record_not_present(tmp_path):
    store = _fresh_store(tmp_path)
    pl = store.create("My List")
    assert store.remove_record(pl.id, "nope") is False


def test_set_auto_gen(tmp_path):
    store = _fresh_store(tmp_path)
    pl = store.create("My List")
    assert pl.auto_gen is True
    store.set_auto_gen(pl.id, False)
    assert store.get(pl.id).auto_gen is False
    store.set_auto_gen(pl.id, True)
    assert store.get(pl.id).auto_gen is True


def test_set_auto_gen_missing(tmp_path):
    store = _fresh_store(tmp_path)
    assert store.set_auto_gen("bad-id", False) is False


def test_contains(tmp_path):
    store = _fresh_store(tmp_path)
    pl = store.create("My List")
    store.add_records(pl.id, ["id1", "id2"])
    assert pl.contains("id1") is True
    assert pl.contains("id3") is False


def test_purge_deleted_records(tmp_path):
    store = _fresh_store(tmp_path)
    pl1 = store.create("A")
    pl2 = store.create("B")
    store.add_records(pl1.id, ["id1", "id2", "id3"])
    store.add_records(pl2.id, ["id2", "id4"])
    # id2 and id4 are "deleted" from the history store
    pruned = store.purge_deleted_records(valid_ids={"id1", "id3"})
    assert pruned == 3  # id2 from pl1, id2+id4 from pl2
    assert store.get(pl1.id).record_ids == ["id1", "id3"]
    assert store.get(pl2.id).record_ids == []


def test_all_preserves_insertion_order(tmp_path):
    store = _fresh_store(tmp_path)
    names = ["Charlie", "Alpha", "Beta"]
    for n in names:
        store.create(n)
    assert [pl.name for pl in store.all()] == names


def test_playlist_store_persistence(tmp_path):
    """Verify that saved JSON can be reloaded into a new store instance."""
    from playlist_store import PlaylistStore, PLAYLISTS_FILE
    pl_file = tmp_path / "playlists.json"
    # Build a store, save to file
    store1 = PlaylistStore.__new__(PlaylistStore)
    store1._playlists = []

    def _save1():
        from dataclasses import asdict
        data = json.dumps([asdict(p) for p in store1._playlists], indent=2) + "\n"
        pl_file.write_text(data)

    store1._save = _save1
    pl = store1.create("Persist Test")
    store1.add_records(pl.id, ["rec1", "rec2"])
    store1.set_auto_gen(pl.id, False)

    # Load into a fresh store
    store2 = PlaylistStore.__new__(PlaylistStore)
    store2._playlists = []

    def _save2():
        pass  # no-op for loading test

    store2._save = _save2
    # Manually call _load logic
    raw = json.loads(pl_file.read_text())
    from playlist_store import Playlist
    import uuid as _uuid
    store2._playlists = [
        Playlist(
            id=item.get("id", str(_uuid.uuid4())),
            name=item.get("name", "Untitled"),
            record_ids=list(item.get("record_ids", [])),
            auto_gen=bool(item.get("auto_gen", True)),
        )
        for item in raw
    ]

    assert len(store2.all()) == 1
    loaded = store2.get(pl.id)
    assert loaded.name == "Persist Test"
    assert loaded.record_ids == ["rec1", "rec2"]
    assert loaded.auto_gen is False
