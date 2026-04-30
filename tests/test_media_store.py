import pytest


def _store(tmp_path):
    from media_store import MediaStore
    return MediaStore(tmp_path / "media.db")


def test_db_created(tmp_path):
    _store(tmp_path)
    assert (tmp_path / "media.db").exists()


def test_tables_exist(tmp_path):
    import sqlite3
    _store(tmp_path)
    conn = sqlite3.connect(tmp_path / "media.db")
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert {"media", "playlists", "playlist_items"} <= tables


def test_wal_mode(tmp_path):
    import sqlite3
    _store(tmp_path)
    conn = sqlite3.connect(tmp_path / "media.db")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


from datetime import datetime, timezone


def _rec(id="r1", media_type="artgen", generator_type="landscape", starred=0):
    from media_store import MediaRecord
    return MediaRecord(
        id=id,
        media_type=media_type,
        created_at=datetime.now(timezone.utc).isoformat(),
        file_path=f"/tmp/{id}.svg",
        thumbnail_path=f"/tmp/{id}.png",
        prompt="a mountain",
        model_id="Qwen3-8B",
        generator_type=generator_type,
        params='{"palette":"sunset","generation_seconds":24}',
        starred=starred,
    )


def test_add_and_get(tmp_path):
    store = _store(tmp_path)
    rec = _rec()
    returned_id = store.add(rec)
    assert returned_id == "r1"
    fetched = store.get("r1")
    assert fetched is not None
    assert fetched.prompt == "a mountain"
    assert fetched.params_dict["palette"] == "sunset"


def test_get_missing_returns_none(tmp_path):
    store = _store(tmp_path)
    assert store.get("nope") is None


def test_delete_removes_row(tmp_path):
    store = _store(tmp_path)
    store.add(_rec())
    result = store.delete("r1")
    assert result is True
    assert store.get("r1") is None


def test_delete_missing_returns_false(tmp_path):
    store = _store(tmp_path)
    assert store.delete("nope") is False


def test_star(tmp_path):
    store = _store(tmp_path)
    store.add(_rec())
    store.star("r1", True)
    assert store.get("r1").starred == 1
    store.star("r1", False)
    assert store.get("r1").starred == 0


def test_star_missing_returns_false(tmp_path):
    store = _store(tmp_path)
    result = store.star("nonexistent", True)
    assert result is False


def test_query_all(tmp_path):
    store = _store(tmp_path)
    store.add(_rec("a", "artgen", "landscape"))
    store.add(_rec("b", "video"))
    store.add(_rec("c", "artgen", "verse"))
    results = store.query()
    assert len(results) == 3


def test_query_by_media_type(tmp_path):
    store = _store(tmp_path)
    store.add(_rec("a", "artgen"))
    store.add(_rec("b", "video"))
    results = store.query(media_type="artgen")
    assert len(results) == 1 and results[0].id == "a"


def test_query_by_generator_type(tmp_path):
    store = _store(tmp_path)
    store.add(_rec("a", "artgen", "landscape"))
    store.add(_rec("b", "artgen", "verse"))
    results = store.query(generator_type="landscape")
    assert len(results) == 1 and results[0].id == "a"


def test_query_starred(tmp_path):
    store = _store(tmp_path)
    store.add(_rec("a"))
    store.add(_rec("b"))
    store.star("a", True)
    results = store.query(starred=True)
    assert len(results) == 1 and results[0].id == "a"


def test_query_newest_first(tmp_path):
    store = _store(tmp_path)
    from media_store import MediaRecord
    r1 = MediaRecord("old","artgen","2025-01-01T00:00:00","/tmp/x","","","",None,"{}",0)
    r2 = MediaRecord("new","artgen","2026-01-01T00:00:00","/tmp/y","","","",None,"{}",0)
    store.add(r1)
    store.add(r2)
    results = store.query()
    assert results[0].id == "new"
