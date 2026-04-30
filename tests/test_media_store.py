import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

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
