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


def test_create_playlist(tmp_path):
    store = _store(tmp_path)
    pl_id = store.create_playlist("Sunsets", filter_expr="generator_type='landscape'")
    assert pl_id
    playlists = store.list_playlists()
    assert len(playlists) == 1
    assert playlists[0]["name"] == "Sunsets"
    assert playlists[0]["filter_expr"] == "generator_type='landscape'"


def test_playlist_records_live(tmp_path):
    store = _store(tmp_path)
    store.add(_rec("a", "artgen", "landscape"))
    store.add(_rec("b", "artgen", "verse"))
    pl_id = store.create_playlist("Land", filter_expr="generator_type='landscape'")
    results = store.playlist_records(pl_id)
    assert len(results) == 1 and results[0].id == "a"


def test_add_remove_playlist_item(tmp_path):
    store = _store(tmp_path)
    store.add(_rec("x"))
    pl_id = store.create_playlist("Manual", filter_expr=None)
    store.add_to_playlist(pl_id, "x")
    results = store.playlist_records(pl_id)
    assert len(results) == 1 and results[0].id == "x"
    store.remove_from_playlist(pl_id, "x")
    assert store.playlist_records(pl_id) == []


def test_auto_playlist_types(tmp_path):
    store = _store(tmp_path)
    store.add(_rec("a", "artgen", "landscape"))
    store.add(_rec("b", "artgen", "landscape"))
    store.add(_rec("c", "artgen", "verse"))
    store.add(_rec("d", "video", None))
    types = store.auto_playlist_types()
    assert set(types) == {"landscape", "verse"}


def test_delete_playlist(tmp_path):
    store = _store(tmp_path)
    pl_id = store.create_playlist("Temp")
    assert store.delete_playlist(pl_id) is True
    assert store.list_playlists() == []


def test_ensure_auto_playlists(tmp_path):
    store = _store(tmp_path)
    store.add(_rec("a", "artgen", "landscape"))
    store.add(_rec("b", "artgen", "verse"))
    store.ensure_auto_playlists()
    pls = store.list_playlists()
    filter_exprs = {p["filter_expr"] for p in pls}
    assert "generator_type='landscape'" in filter_exprs
    assert "generator_type='verse'" in filter_exprs
    # Calling again should not create duplicates
    store.ensure_auto_playlists()
    assert len(store.list_playlists()) == 2


def test_rename_playlist(tmp_path):
    s = _store(tmp_path)
    pl_id = s.create_playlist("Old")
    assert s.rename_playlist(pl_id, "New") is True
    pls = {p["id"]: p for p in s.list_playlists()}
    assert pls[pl_id]["name"] == "New"


def test_rename_playlist_missing(tmp_path):
    s = _store(tmp_path)
    assert s.rename_playlist("no-such-id", "X") is False


def test_set_playlist_auto_gen(tmp_path):
    s = _store(tmp_path)
    pl_id = s.create_playlist("P", auto_gen=True)
    assert s.set_playlist_auto_gen(pl_id, False) is True
    pls = {p["id"]: p for p in s.list_playlists()}
    assert pls[pl_id]["auto_gen"] is False


def test_purge_playlist_items(tmp_path):
    from datetime import datetime, timezone
    from media_store import MediaRecord
    s = _store(tmp_path)
    # Insert 3 media rows
    for mid in ["m1", "m2", "m3"]:
        s.add(MediaRecord(
            id=mid, media_type="video",
            created_at=datetime.now(timezone.utc).isoformat(),
            file_path="", thumbnail_path="", prompt="", model_id="",
            generator_type=None, params="{}", starred=0,
        ))
    pl_id = s.create_playlist("P")
    s.add_to_playlist(pl_id, "m1")
    s.add_to_playlist(pl_id, "m2")
    s.add_to_playlist(pl_id, "m3")
    # Purge m2 and m3
    removed = s.purge_playlist_items({"m1"})
    assert removed == 2
    remaining = [m.id for m in s.playlist_records(pl_id)]
    assert remaining == ["m1"]


def test_make_artgen_path(tmp_path):
    from media_store import make_artgen_path
    p = make_artgen_path("abc12345", ".svg", base_dir=tmp_path / "artgen")
    assert p.suffix == ".svg"
    assert "abc12345" in p.name


def test_make_thumbnail_svg(tmp_path):
    from media_store import make_thumbnail
    svg_path = tmp_path / "test.svg"
    svg_path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
                        '<rect width="100" height="100" fill="red"/></svg>')
    out = tmp_path / "thumb.png"
    make_thumbnail(svg_path, out)
    # Either a PNG was written or the SVG was copied as fallback
    assert out.exists() or out.with_suffix(".svg").exists()


def test_make_thumbnail_text(tmp_path):
    from media_store import make_thumbnail
    txt_path = tmp_path / "verse.txt"
    txt_path.write_text("the forge\nsleeps\nin ash")
    out = tmp_path / "thumb.png"
    make_thumbnail(txt_path, out)
    # Always produces some file (PNG or placeholder)
    assert out.exists()
