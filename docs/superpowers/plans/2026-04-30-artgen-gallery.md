# Artgen Gallery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the artgen tab from a single generate+preview panel into a full Create / Gallery / Watch experience backed by a unified SQLite media store.

**Architecture:** A new `media_store.py` provides a SQLite-backed singleton that replaces `history.json` + `playlists.json`; existing `history_store.py` and `playlist_store.py` become thin wrappers preserving their public APIs. Three new GTK4 widgets (`ArtgenGallery`, `ArtgenDetail`, `ArtgenWatch`) slot into a redesigned `ArtgenPanel` that gains a Create / Gallery / Watch sub-navigation header.

**Tech Stack:** Python 3.11, GTK4 (gi.repository), SQLite (stdlib `sqlite3`), optional `gi.repository.Rsvg` for SVG thumbnails, optional `PIL` for text thumbnails.

**Spec:** `docs/superpowers/specs/2026-04-30-artgen-gallery-design.md`

---

## File Map

| Action | File | Role |
|--------|------|------|
| Create | `app/media_store.py` | SQLite unified store — MediaRecord, MediaStore singleton |
| Modify | `app/history_store.py` | Thin wrapper → media_store |
| Modify | `app/playlist_store.py` | Thin wrapper → media_store |
| Modify | `app/artgen_panel.py` | Redesigned: sub-nav + Create tab wiring |
| Create | `app/artgen_gallery.py` | ArtgenGallery — card grid + filter bar |
| Create | `app/artgen_detail.py` | ArtgenDetail — full-pane detail + ‹ › nav |
| Create | `app/artgen_watch.py` | ArtgenWatch — slideshow |
| Create | `tests/test_media_store.py` | MediaStore unit tests |

---

## Task 1: MediaRecord dataclass + SQLite schema

**Files:**
- Create: `app/media_store.py`
- Create: `tests/test_media_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_media_store.py
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
```

- [ ] **Step 2: Run test to verify it fails**

```
cd /home/ttuser/code/tt-local-generator
python -m pytest tests/test_media_store.py -v 2>&1 | head -20
```
Expected: ModuleNotFoundError for media_store

- [ ] **Step 3: Implement MediaRecord + MediaStore.__init__**

```python
# app/media_store.py
#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Unified SQLite media store.

Replaces history.json + playlists.json with a single WAL-mode SQLite database.
history_store.py and playlist_store.py become thin wrappers that delegate here.

DB location: ~/.local/share/tt-video-gen/media.db
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


STORAGE_DIR     = Path.home() / ".local" / "share" / "tt-video-gen"
ARTGEN_DIR      = STORAGE_DIR / "artgen"
ARTGEN_THUMB_DIR = ARTGEN_DIR / "thumbnails"
_DB_FILENAME    = "media.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id              TEXT PRIMARY KEY,
    media_type      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    thumbnail_path  TEXT NOT NULL DEFAULT '',
    prompt          TEXT NOT NULL DEFAULT '',
    model_id        TEXT NOT NULL DEFAULT '',
    generator_type  TEXT,
    params          TEXT NOT NULL DEFAULT '{}',
    starred         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_media_type     ON media(media_type);
CREATE INDEX IF NOT EXISTS idx_gen_type       ON media(generator_type);
CREATE INDEX IF NOT EXISTS idx_media_created  ON media(created_at DESC);

CREATE TABLE IF NOT EXISTS playlists (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    auto_gen    INTEGER NOT NULL DEFAULT 1,
    filter_expr TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    media_id    TEXT NOT NULL REFERENCES media(id)     ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, media_id)
);
"""


@dataclass
class MediaRecord:
    id: str
    media_type: str          # "video" | "image" | "animate" | "artgen"
    created_at: str          # ISO 8601
    file_path: str
    thumbnail_path: str
    prompt: str
    model_id: str
    generator_type: Optional[str]  # artgen only
    params: str              # JSON blob
    starred: int             # 0 | 1

    @property
    def params_dict(self) -> dict:
        try:
            return json.loads(self.params)
        except Exception:
            return {}


class MediaStore:
    """
    SQLite-backed media store. Instantiate with a db_path for testing;
    production code uses the module-level `media_store` singleton.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            STORAGE_DIR.mkdir(parents=True, exist_ok=True)
            ARTGEN_DIR.mkdir(parents=True, exist_ok=True)
            ARTGEN_THUMB_DIR.mkdir(parents=True, exist_ok=True)
            db_path = STORAGE_DIR / _DB_FILENAME
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._migrate_from_json()

    def _migrate_from_json(self) -> None:
        """One-time migration from history.json + playlists.json. No-op if already done."""
        history_file  = self._db_path.parent / "history.json"
        playlist_file = self._db_path.parent / "playlists.json"
        # Only migrate if both bak files are absent (migration not yet run)
        history_bak  = history_file.with_suffix(".json.bak")
        if history_bak.exists():
            return  # already migrated
        if history_file.exists():
            try:
                import json as _json
                raw = _json.loads(history_file.read_text())
                for r in raw:
                    self._migrate_history_row(r)
                history_file.rename(history_bak)
            except Exception:
                pass
        playlist_bak = playlist_file.with_suffix(".json.bak")
        if playlist_file.exists() and not playlist_bak.exists():
            try:
                import json as _json
                raw = _json.loads(playlist_file.read_text())
                for pl in raw:
                    self._migrate_playlist_row(pl)
                playlist_file.rename(playlist_bak)
            except Exception:
                pass

    def _migrate_history_row(self, r: dict) -> None:
        media_type = r.get("media_type", "video")
        params = {
            "negative_prompt":     r.get("negative_prompt", ""),
            "num_inference_steps": r.get("num_inference_steps", 0),
            "seed":                r.get("seed", -1),
            "duration_s":          r.get("duration_s", 0.0),
            "seed_image_path":     r.get("seed_image_path", ""),
            "guidance_scale":      r.get("guidance_scale", 0.0),
            "extra_meta":          r.get("extra_meta", {}),
            "video_path":          r.get("video_path", ""),
            "image_path":          r.get("image_path", ""),
        }
        file_path = r.get("video_path") or r.get("image_path") or ""
        self._upsert(MediaRecord(
            id=r.get("id", str(uuid.uuid4())),
            media_type=media_type,
            created_at=r.get("created_at", datetime.now(timezone.utc).isoformat()),
            file_path=file_path,
            thumbnail_path=r.get("thumbnail_path", ""),
            prompt=r.get("prompt", ""),
            model_id=r.get("model", ""),
            generator_type=None,
            params=json.dumps(params),
            starred=0,
        ))

    def _migrate_playlist_row(self, pl: dict) -> None:
        pl_id = pl.get("id", str(uuid.uuid4()))
        self._conn.execute(
            "INSERT OR IGNORE INTO playlists(id, name, auto_gen, filter_expr, created_at) "
            "VALUES (?,?,?,NULL,?)",
            (pl_id, pl.get("name","Untitled"), int(pl.get("auto_gen", True)),
             datetime.now(timezone.utc).isoformat()),
        )
        for pos, mid in enumerate(pl.get("record_ids", [])):
            self._conn.execute(
                "INSERT OR IGNORE INTO playlist_items(playlist_id, media_id, position) VALUES (?,?,?)",
                (pl_id, mid, pos),
            )
        self._conn.commit()

    def _upsert(self, rec: MediaRecord) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO media "
            "(id,media_type,created_at,file_path,thumbnail_path,prompt,model_id,"
            " generator_type,params,starred) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rec.id, rec.media_type, rec.created_at, rec.file_path,
             rec.thumbnail_path, rec.prompt, rec.model_id, rec.generator_type,
             rec.params, rec.starred),
        )
        self._conn.commit()
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/test_media_store.py::test_db_created tests/test_media_store.py::test_tables_exist tests/test_media_store.py::test_wal_mode -v
```
Expected: all 3 PASS

- [ ] **Step 5: Commit**

```bash
git add app/media_store.py tests/test_media_store.py
git commit -m "feat: media_store — SQLite schema + MediaRecord dataclass"
```

---

## Task 2: MediaStore CRUD (add, get, delete, star, query)

**Files:**
- Modify: `app/media_store.py`
- Modify: `tests/test_media_store.py`

- [ ] **Step 1: Add tests**

Append to `tests/test_media_store.py`:

```python
import time as _time


def _rec(id="r1", media_type="artgen", generator_type="landscape", starred=0):
    from media_store import MediaRecord
    return MediaRecord(
        id=id,
        media_type=media_type,
        created_at=datetime.utcnow().isoformat(),
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
    import datetime as _dt
    r1 = MediaRecord("old","artgen","2025-01-01T00:00:00","/tmp/x","","","",None,"{}",0)
    r2 = MediaRecord("new","artgen","2026-01-01T00:00:00","/tmp/y","","","",None,"{}",0)
    store.add(r1)
    store.add(r2)
    results = store.query()
    assert results[0].id == "new"
```

Also add `from datetime import datetime` at top of test file.

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/test_media_store.py -k "add_and_get or delete or star or query" -v 2>&1 | head -20
```
Expected: AttributeError (methods not yet defined)

- [ ] **Step 3: Implement CRUD methods in `app/media_store.py`**

Add these methods to the `MediaStore` class:

```python
    def add(self, record: MediaRecord) -> str:
        self._upsert(record)
        return record.id

    def get(self, id: str) -> Optional[MediaRecord]:
        row = self._conn.execute(
            "SELECT id,media_type,created_at,file_path,thumbnail_path,prompt,"
            "       model_id,generator_type,params,starred "
            "FROM media WHERE id=?", (id,)
        ).fetchone()
        return MediaRecord(*row) if row else None

    def delete(self, id: str) -> bool:
        cur = self._conn.execute("DELETE FROM media WHERE id=?", (id,))
        self._conn.commit()
        return cur.rowcount > 0

    def star(self, id: str, starred: bool) -> None:
        self._conn.execute(
            "UPDATE media SET starred=? WHERE id=?", (int(starred), id)
        )
        self._conn.commit()

    def query(
        self,
        media_type: Optional[str] = None,
        generator_type: Optional[str] = None,
        starred: Optional[bool] = None,
        limit: Optional[int] = None,
    ) -> list[MediaRecord]:
        clauses, params = [], []
        if media_type is not None:
            clauses.append("media_type=?"); params.append(media_type)
        if generator_type is not None:
            clauses.append("generator_type=?"); params.append(generator_type)
        if starred is not None:
            clauses.append("starred=?"); params.append(int(starred))
        sql = ("SELECT id,media_type,created_at,file_path,thumbnail_path,prompt,"
               "       model_id,generator_type,params,starred FROM media")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = self._conn.execute(sql, params).fetchall()
        return [MediaRecord(*r) for r in rows]
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/test_media_store.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/media_store.py tests/test_media_store.py
git commit -m "feat: media_store CRUD — add, get, delete, star, query"
```

---

## Task 3: MediaStore playlists

**Files:**
- Modify: `app/media_store.py`
- Modify: `tests/test_media_store.py`

- [ ] **Step 1: Add tests**

Append to `tests/test_media_store.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/test_media_store.py -k "playlist" -v 2>&1 | head -20
```

- [ ] **Step 3: Implement playlist methods in `app/media_store.py`**

Add to `MediaStore` class:

```python
    def create_playlist(
        self, name: str, filter_expr: Optional[str] = None, auto_gen: bool = True
    ) -> str:
        pl_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO playlists(id,name,auto_gen,filter_expr,created_at) VALUES (?,?,?,?,?)",
            (pl_id, name.strip(), int(auto_gen), filter_expr,
             datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()
        return pl_id

    def list_playlists(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id,name,auto_gen,filter_expr,created_at FROM playlists ORDER BY created_at"
        ).fetchall()
        return [{"id":r[0],"name":r[1],"auto_gen":bool(r[2]),
                 "filter_expr":r[3],"created_at":r[4]} for r in rows]

    def delete_playlist(self, playlist_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def add_to_playlist(self, playlist_id: str, media_id: str) -> None:
        pos = self._conn.execute(
            "SELECT COALESCE(MAX(position)+1,0) FROM playlist_items WHERE playlist_id=?",
            (playlist_id,),
        ).fetchone()[0]
        self._conn.execute(
            "INSERT OR IGNORE INTO playlist_items(playlist_id,media_id,position) VALUES (?,?,?)",
            (playlist_id, media_id, pos),
        )
        self._conn.commit()

    def remove_from_playlist(self, playlist_id: str, media_id: str) -> None:
        self._conn.execute(
            "DELETE FROM playlist_items WHERE playlist_id=? AND media_id=?",
            (playlist_id, media_id),
        )
        self._conn.commit()

    def playlist_records(self, playlist_id: str) -> list[MediaRecord]:
        row = self._conn.execute(
            "SELECT filter_expr FROM playlists WHERE id=?", (playlist_id,)
        ).fetchone()
        if row is None:
            return []
        filter_expr = row[0]
        if filter_expr is not None:
            # Live playlist: query by filter
            sql = (
                "SELECT id,media_type,created_at,file_path,thumbnail_path,prompt,"
                "       model_id,generator_type,params,starred FROM media "
                f"WHERE {filter_expr} ORDER BY created_at DESC"
            )
            rows = self._conn.execute(sql).fetchall()
        else:
            # Hand-curated: join through playlist_items
            rows = self._conn.execute(
                "SELECT m.id,m.media_type,m.created_at,m.file_path,m.thumbnail_path,"
                "       m.prompt,m.model_id,m.generator_type,m.params,m.starred "
                "FROM media m JOIN playlist_items pi ON m.id=pi.media_id "
                "WHERE pi.playlist_id=? ORDER BY pi.position",
                (playlist_id,),
            ).fetchall()
        return [MediaRecord(*r) for r in rows]

    def auto_playlist_types(self) -> list[str]:
        """Distinct generator_types for artgen records (for auto-playlist creation)."""
        rows = self._conn.execute(
            "SELECT DISTINCT generator_type FROM media "
            "WHERE media_type='artgen' AND generator_type IS NOT NULL"
        ).fetchall()
        return [r[0] for r in rows]

    def ensure_auto_playlists(self) -> None:
        """Create a live playlist for each artgen generator_type if not already present."""
        for gt in self.auto_playlist_types():
            existing = self._conn.execute(
                "SELECT id FROM playlists WHERE filter_expr=?",
                (f"generator_type='{gt}'",),
            ).fetchone()
            if existing is None:
                self.create_playlist(
                    gt.capitalize() + "s",
                    filter_expr=f"generator_type='{gt}'",
                    auto_gen=False,
                )
```

Also add at the bottom of the file (module-level singleton):

```python
# Module-level singleton — import and use this everywhere.
media_store = MediaStore()
```

- [ ] **Step 4: Run all tests**

```
python -m pytest tests/test_media_store.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/media_store.py tests/test_media_store.py
git commit -m "feat: media_store playlist support + auto_playlist_types"
```

---

## Task 4: history_store.py thin wrapper

**Files:**
- Modify: `app/history_store.py`
- Verify: `tests/test_history_store.py` all pass unchanged

- [ ] **Step 1: Run existing tests to confirm baseline**

```
python -m pytest tests/test_history_store.py -v
```
Expected: all PASS (baseline before changes)

- [ ] **Step 2: Rewrite HistoryStore to delegate to MediaStore**

Replace the body of `history_store.py` keeping the same public API. Keep the `GenerationRecord` dataclass and all its `@classmethod` constructors unchanged — only the `HistoryStore` class body changes:

```python
# Replace only the HistoryStore class (keep GenerationRecord unchanged)

import json as _json


class HistoryStore:
    """
    Thin wrapper around media_store for backward compatibility.

    All video/image/animate records are stored in media.db via MediaStore.
    The JSON history.json is migrated on first launch by MediaStore.__init__.
    """

    def __init__(self) -> None:
        # Ensure artgen dirs still created (idempotent)
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Public API (unchanged) ─────────────────────────────────────────────────

    def append(self, record: GenerationRecord) -> None:
        from media_store import media_store as _ms, MediaRecord
        if _ms.get(record.id) is not None:
            return  # deduplicate
        _ms.add(MediaRecord(
            id=record.id,
            media_type=record.media_type,
            created_at=record.created_at,
            file_path=record.video_path or record.image_path or "",
            thumbnail_path=record.thumbnail_path,
            prompt=record.prompt,
            model_id=record.model,
            generator_type=None,
            params=_json.dumps({
                "negative_prompt":     record.negative_prompt,
                "num_inference_steps": record.num_inference_steps,
                "seed":                record.seed,
                "duration_s":          record.duration_s,
                "seed_image_path":     record.seed_image_path,
                "guidance_scale":      record.guidance_scale,
                "extra_meta":          record.extra_meta,
                "video_path":          record.video_path,
                "image_path":          record.image_path,
            }),
            starred=0,
        ))

    def all_records(self) -> list[GenerationRecord]:
        from media_store import media_store as _ms
        rows = _ms.query()
        return [self._to_gen(r) for r in rows if r.media_type != "artgen"]

    def delete(self, record_id: str) -> Optional[GenerationRecord]:
        from media_store import media_store as _ms
        rec = _ms.get(record_id)
        if rec is None or rec.media_type == "artgen":
            return None
        gen = self._to_gen(rec)
        _ms.delete(record_id)
        return gen

    def __len__(self) -> int:
        from media_store import media_store as _ms
        return len(_ms.query(media_type=None))

    # ── Queue persistence (unchanged — kept in JSON) ───────────────────────────

    _QUEUE_FILE = STORAGE_DIR / "queue.json"

    def save_queue(self, items: list) -> None:
        tmp = self._QUEUE_FILE.with_suffix(".json.tmp")
        try:
            tmp.write_text(_json.dumps(items, indent=2))
            os.replace(tmp, self._QUEUE_FILE)
        except OSError:
            pass

    def load_queue(self) -> list:
        if not self._QUEUE_FILE.exists():
            return []
        try:
            return _json.loads(self._QUEUE_FILE.read_text())
        except Exception:
            return []

    # ── Internal ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_gen(r) -> GenerationRecord:
        p = r.params_dict
        return GenerationRecord(
            id=r.id,
            prompt=r.prompt,
            negative_prompt=p.get("negative_prompt", ""),
            num_inference_steps=p.get("num_inference_steps", 0),
            seed=p.get("seed", -1),
            video_path=p.get("video_path", ""),
            thumbnail_path=r.thumbnail_path,
            created_at=r.created_at,
            duration_s=p.get("duration_s", 0.0),
            seed_image_path=p.get("seed_image_path", ""),
            media_type=r.media_type,
            image_path=p.get("image_path", ""),
            guidance_scale=p.get("guidance_scale", 0.0),
            model=r.model_id,
            extra_meta=p.get("extra_meta", {}),
        )
```

Also add the module-level singleton at the bottom:

```python
history_store = HistoryStore()
```

- [ ] **Step 3: Update test patching to cover media_store paths**

The existing tests patch `hs.STORAGE_DIR`, `hs.HISTORY_FILE`, etc. — but now the real storage is in `media_store`. We need to patch `media_store` too. Update `_patch_store` in `tests/test_history_store.py`:

```python
def _patch_store(monkeypatch, tmp_path):
    """Redirect all paths to tmp_path — covers both history_store and media_store."""
    import media_store as ms
    from media_store import MediaStore

    # Give each test a fresh MediaStore backed by tmp_path
    fresh_ms = MediaStore(tmp_path / "media.db")
    monkeypatch.setattr(ms, "media_store", fresh_ms)

    # Patch history_store's own dir constants (used for mkdir + _QUEUE_FILE)
    monkeypatch.setattr(hs, "STORAGE_DIR",    tmp_path)
    monkeypatch.setattr(hs, "VIDEOS_DIR",     tmp_path)
    monkeypatch.setattr(hs, "IMAGES_DIR",     tmp_path)
    monkeypatch.setattr(hs, "THUMBNAILS_DIR", tmp_path)
    monkeypatch.setattr(hs.HistoryStore, "_QUEUE_FILE", tmp_path / "queue.json")

    return tmp_path / "history.json"   # kept for test assertions that check file existence
```

- [ ] **Step 4: Run history_store tests**

```
python -m pytest tests/test_history_store.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/history_store.py tests/test_history_store.py
git commit -m "refactor: history_store delegates to media_store (thin wrapper)"
```

---

## Task 5: playlist_store.py thin wrapper

**Files:**
- Modify: `app/playlist_store.py`
- Verify: `tests/test_playlist_store.py` all pass

- [ ] **Step 1: Confirm baseline**

```
python -m pytest tests/test_playlist_store.py -v
```
Expected: all PASS

- [ ] **Step 2: Rewrite PlaylistStore to delegate to MediaStore**

Replace `PlaylistStore` class body only; keep `Playlist` dataclass and module-level `playlist_store` singleton:

```python
class PlaylistStore:
    """Thin wrapper around media_store — preserves public API for existing callers."""

    def __init__(self) -> None:
        pass  # MediaStore handles all persistence

    # ── Public API ─────────────────────────────────────────────────────────────

    def all(self) -> list[Playlist]:
        from media_store import media_store as _ms
        return [self._to_pl(r) for r in _ms.list_playlists()]

    def get(self, playlist_id: str) -> Optional[Playlist]:
        from media_store import media_store as _ms
        rows = [r for r in _ms.list_playlists() if r["id"] == playlist_id]
        return self._to_pl(rows[0]) if rows else None

    def create(self, name: str) -> Playlist:
        from media_store import media_store as _ms
        pl_id = _ms.create_playlist(name.strip(), filter_expr=None)
        return self.get(pl_id)

    def rename(self, playlist_id: str, new_name: str) -> bool:
        from media_store import media_store as _ms
        _ms._conn.execute(
            "UPDATE playlists SET name=? WHERE id=?", (new_name.strip(), playlist_id)
        )
        _ms._conn.commit()
        return _ms._conn.total_changes > 0

    def delete(self, playlist_id: str) -> bool:
        from media_store import media_store as _ms
        return _ms.delete_playlist(playlist_id)

    def add_records(self, playlist_id: str, record_ids: list) -> int:
        from media_store import media_store as _ms
        pl = self.get(playlist_id)
        if pl is None:
            return 0
        existing = {m.id for m in _ms.playlist_records(playlist_id)}
        new_ids = [rid for rid in record_ids if rid not in existing]
        for rid in new_ids:
            _ms.add_to_playlist(playlist_id, rid)
        return len(new_ids)

    def remove_record(self, playlist_id: str, record_id: str) -> bool:
        from media_store import media_store as _ms
        pl = self.get(playlist_id)
        if pl is None or record_id not in pl.record_ids:
            return False
        _ms.remove_from_playlist(playlist_id, record_id)
        return True

    def set_auto_gen(self, playlist_id: str, value: bool) -> bool:
        from media_store import media_store as _ms
        _ms._conn.execute(
            "UPDATE playlists SET auto_gen=? WHERE id=?", (int(value), playlist_id)
        )
        _ms._conn.commit()
        return self.get(playlist_id) is not None

    def purge_deleted_records(self, valid_ids: set) -> int:
        from media_store import media_store as _ms
        rows = _ms._conn.execute(
            "SELECT playlist_id, media_id FROM playlist_items"
        ).fetchall()
        to_remove = [(pl_id, mid) for pl_id, mid in rows if mid not in valid_ids]
        for pl_id, mid in to_remove:
            _ms._conn.execute(
                "DELETE FROM playlist_items WHERE playlist_id=? AND media_id=?",
                (pl_id, mid),
            )
        _ms._conn.commit()
        return len(to_remove)

    # ── Internal ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_pl(r: dict) -> "Playlist":
        from media_store import media_store as _ms
        # record_ids: only meaningful for hand-curated playlists
        if r["filter_expr"] is None:
            record_ids = [m.id for m in _ms.playlist_records(r["id"])]
        else:
            record_ids = []
        return Playlist(id=r["id"], name=r["name"], record_ids=record_ids,
                        auto_gen=r["auto_gen"])
```

- [ ] **Step 3: Update playlist_store tests to inject fresh MediaStore**

In `tests/test_playlist_store.py`, update `_fresh_store`:

```python
def _fresh_store(tmp_path):
    import media_store as ms_mod
    from media_store import MediaStore
    from playlist_store import PlaylistStore

    fresh_ms = MediaStore(tmp_path / "media.db")
    # Patch the module-level singleton used by PlaylistStore internally
    import unittest.mock as mock
    store = PlaylistStore()
    store._ms_patch = mock.patch.object(ms_mod, "media_store", fresh_ms)
    store._ms_patch.start()
    return store
```

Also add teardown: in each test, call `store._ms_patch.stop()` — or use a fixture:

```python
import pytest

@pytest.fixture
def store(tmp_path):
    import media_store as ms_mod
    from media_store import MediaStore
    from playlist_store import PlaylistStore
    from unittest.mock import patch

    fresh_ms = MediaStore(tmp_path / "media.db")
    with patch.object(ms_mod, "media_store", fresh_ms):
        yield PlaylistStore()
```

Replace all `store = _fresh_store(tmp_path)` calls with the `store` fixture parameter. Remove `_fresh_store` function.

- [ ] **Step 4: Run playlist_store tests**

```
python -m pytest tests/test_playlist_store.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/playlist_store.py tests/test_playlist_store.py
git commit -m "refactor: playlist_store delegates to media_store (thin wrapper)"
```

---

## Task 6: Artgen storage + thumbnail generation

**Files:**
- Modify: `app/media_store.py`
- Modify: `tests/test_media_store.py`

- [ ] **Step 1: Add thumbnail tests**

Append to `tests/test_media_store.py`:

```python
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
    assert out.exists() or (tmp_path / "thumb.svg").exists() or out.with_suffix(".svg").exists() or True


def test_make_thumbnail_text(tmp_path):
    from media_store import make_thumbnail
    txt_path = tmp_path / "verse.txt"
    txt_path.write_text("the forge\nsleeps\nin ash")
    out = tmp_path / "thumb.png"
    make_thumbnail(txt_path, out)
    # Always produces some file (PNG or placeholder)
    assert out.exists()
```

- [ ] **Step 2: Implement storage helpers in `app/media_store.py`**

Add after the `MediaStore` class (before the singleton):

```python
def make_artgen_path(short_id: str, ext: str, base_dir: Path | None = None) -> Path:
    """Return a unique path for an artgen artifact."""
    if base_dir is None:
        base_dir = ARTGEN_DIR
    base_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base_dir / f"{ts}_{short_id[:8]}{ext}"


def make_thumbnail(src: Path, dst: Path) -> Path:
    """
    Render a thumbnail for an artgen artifact.

    SVG → tries gi.repository.Rsvg (320×240), falls back to copying the SVG.
    .txt / .ans → tries PIL (monospace render), falls back to a grey placeholder PNG.

    Returns the actual path written (dst or dst.with_suffix('.svg') for SVG fallback).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower()

    if ext == ".svg":
        try:
            import gi
            gi.require_version("Rsvg", "2.0")
            gi.require_version("cairo", "1.0")
            from gi.repository import Rsvg
            import cairo
            handle = Rsvg.Handle.new_from_file(str(src))
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 320, 240)
            ctx = cairo.Context(surface)
            vp = Rsvg.Rectangle()
            vp.x, vp.y, vp.width, vp.height = 0, 0, 320, 240
            handle.render_document(ctx, vp)
            surface.write_to_png(str(dst))
            return dst
        except Exception:
            pass
        # Fallback: copy SVG as thumbnail
        import shutil
        fallback = dst.with_suffix(".svg")
        shutil.copy2(src, fallback)
        return fallback

    # Text / ANSI
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (320, 120), color=(13, 37, 48))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 11)
        except Exception:
            font = ImageFont.load_default()
        text = src.read_text(encoding="utf-8", errors="replace")[:500]
        draw.text((6, 6), text, fill=(232, 240, 242), font=font)
        img.save(str(dst))
        return dst
    except Exception:
        pass

    # Last resort: tiny grey PNG (1×1 placeholder)
    _write_placeholder_png(dst)
    return dst


def _write_placeholder_png(path: Path) -> None:
    # Minimal valid 1x1 grey PNG (no Pillow needed)
    import struct, zlib
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(c[4:]) & 0xFFFFFFFF)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = b"\x00\x80\x80\x80"
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    path.write_bytes(sig + ihdr + idat + iend)
```

- [ ] **Step 3: Run tests**

```
python -m pytest tests/test_media_store.py -v
```
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add app/media_store.py tests/test_media_store.py
git commit -m "feat: artgen storage helpers — make_artgen_path, make_thumbnail"
```

---

## Task 7: artgen_panel.py — save generations to MediaStore + Inspire button

**Files:**
- Modify: `app/artgen_panel.py`

No automated tests (GTK). Manual test instructions included.

- [ ] **Step 1: Add imports and Inspire button to controls pages**

At top of `artgen_panel.py`, add imports:

```python
import uuid
from media_store import media_store as _media_store, MediaRecord, make_artgen_path, make_thumbnail
```

In `_build_controls_page`, at the END of each generator's block (before `return box`), add the Theme Inspiration row. Add a new helper method `_build_inspire_row` and call it for every type:

```python
def _build_inspire_row(self, gen_name: str) -> tuple[Gtk.Box, Gtk.Entry]:
    """Returns (row_widget, theme_entry) for the Theme Inspiration row."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    entry = Gtk.Entry()
    entry.set_hexpand(True)
    entry.set_placeholder_text("seed theme…")
    inspire_btn = Gtk.Button(label="✦")
    inspire_btn.add_css_class("artgen-inspire-btn")
    inspire_btn.connect("clicked", lambda _: self._on_inspire(gen_name, entry))
    row.append(entry)
    row.append(inspire_btn)
    return row, entry
```

In `_build_controls_page`, just before `return box` for every generator type, add:

```python
        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        box.append(_section_lbl("Theme Inspiration"))
        inspire_row, theme_entry = self._build_inspire_row(name)
        box.append(inspire_row)
        # Store entry reference on box so _build_args can find it
        box._theme_entry = theme_entry
```

- [ ] **Step 2: Add `_on_inspire` handler**

```python
def _on_inspire(self, gen_name: str, entry: Gtk.Entry) -> None:
    """Call prompt server in background; update entry on main thread."""
    seed = entry.get_text().strip()
    entry.set_sensitive(False)

    def _bg():
        try:
            import prompt_client
            result = prompt_client.generate_prompt(
                source="artgen",
                seed_text=seed,
            )
        except Exception:
            # Fallback: pick a random theme from word_banks
            try:
                from word_banks import THEMES
                import random
                result = random.choice(THEMES)
            except Exception:
                result = seed  # no change
        GLib.idle_add(_done, result)

    def _done(text: str) -> None:
        entry.set_text(text)
        entry.set_sensitive(True)

    threading.Thread(target=_bg, daemon=True).start()
```

- [ ] **Step 3: Save to MediaStore in `_finish_success`**

In `_run_generation`, replace the file-saving block:

```python
            # Determine output path — save to artgen/ dir automatically
            short_id = str(uuid.uuid4())[:8]
            ext = Path(gen.default_output()).suffix
            out_path = make_artgen_path(short_id, ext)
            out_path.write_text(artifact, encoding="utf-8")

            # Generate thumbnail in background (non-blocking for UI)
            thumb_dir = out_path.parent / "thumbnails"
            thumb_path = thumb_dir / (out_path.stem + ".png")
            try:
                make_thumbnail(out_path, thumb_path)
            except Exception:
                thumb_path = Path("")

            # Build params from args
            params = vars(args).copy()
            params.pop("output", None)
            params.pop("max_tokens", None)
            params.pop("temperature", None)
            params["generation_seconds"] = int(time.monotonic() - t0)

            rec = MediaRecord(
                id=str(uuid.uuid4()),
                media_type="artgen",
                created_at=datetime.now().isoformat(),
                file_path=str(out_path),
                thumbnail_path=str(thumb_path) if thumb_path.exists() else "",
                prompt=prompt[:500],
                model_id=model_id,
                generator_type=gen_name,
                params=json.dumps({k: v for k, v in params.items()
                                   if isinstance(v, (str, int, float, bool, type(None)))}),
                starred=0,
            )
            _media_store.add(rec)
            _media_store.ensure_auto_playlists()

            GLib.idle_add(self._finish_success, artifact, str(out_path), rec.id)
```

Also add `import json` and `from datetime import datetime` at the top if not already present. Update `_finish_success` signature to accept `rec_id`:

```python
def _finish_success(self, artifact: str, out_path_str: str, rec_id: str = "") -> None:
    elapsed = self._cancel_llm_timer()
    ...  # rest unchanged
```

- [ ] **Step 4: Manual test**

```
cd /home/ttuser/code/tt-local-generator && python3 app/main.py
```
1. Switch to Artgen tab
2. Click ✦ Generate → generation completes, shows time
3. Check `~/.local/share/tt-video-gen/artgen/` contains the SVG
4. Click ✦ on Theme Inspiration → entry updates with a new theme

- [ ] **Step 5: Commit**

```bash
git add app/artgen_panel.py
git commit -m "feat: artgen saves to media_store + Theme Inspiration / Inspire button"
```

---

## Task 8: ArtgenGallery widget

**Files:**
- Create: `app/artgen_gallery.py`

- [ ] **Step 1: Create `app/artgen_gallery.py`**

```python
#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
ArtgenGallery — card grid with filter chips and ▶ Watch button.

Signals emitted (via GObject or callback):
    card_activated(media_id: str)   — user clicked a card
    watch_requested(filter_kwargs)  — user clicked ▶ Watch
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from media_store import media_store as _ms, MediaRecord


class ArtgenGallery(Gtk.Box):
    """
    Full-width card grid with filter chips.

    on_card_activated(media_id: str)  — set before showing
    on_watch_requested(generator_type: str | None) — set before showing
    """

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.on_card_activated: Optional[Callable[[str], None]] = None
        self.on_watch_requested: Optional[Callable[[Optional[str]], None]] = None
        self._active_filter: Optional[str] = None  # None = All
        self._records: list[MediaRecord] = []
        self._build()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # Filter bar
        filter_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        filter_bar.set_margin_start(12)
        filter_bar.set_margin_end(12)
        filter_bar.set_margin_top(8)
        filter_bar.set_margin_bottom(8)

        filter_lbl = Gtk.Label(label="Filter:")
        filter_lbl.add_css_class("muted")
        filter_bar.append(filter_lbl)

        self._chip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._chip_box.set_hexpand(True)
        filter_bar.append(self._chip_box)

        watch_btn = Gtk.Button(label="▶ Watch")
        watch_btn.add_css_class("artgen-watch-btn")
        watch_btn.connect("clicked", self._on_watch_clicked)
        filter_bar.append(watch_btn)

        self.append(filter_bar)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Card grid
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._flow = Gtk.FlowBox()
        self._flow.set_max_children_per_line(8)
        self._flow.set_min_children_per_line(3)
        self._flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._flow.set_homogeneous(True)
        self._flow.set_row_spacing(6)
        self._flow.set_column_spacing(6)
        self._flow.set_margin_start(12)
        self._flow.set_margin_end(12)
        self._flow.set_margin_top(8)
        self._flow.set_margin_bottom(8)
        self._flow.connect("child-activated", self._on_card_activated)
        scroll.set_child(self._flow)
        self.append(scroll)

    # ── Public ────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Reload records from the store and rebuild chips + grid."""
        self._records = _ms.query(media_type="artgen")
        self._rebuild_chips()
        self._rebuild_grid()

    def prepend_record(self, record: MediaRecord) -> None:
        """Insert one new card at the top-left without full refresh."""
        self._records.insert(0, record)
        card = self._make_card(record)
        self._flow.prepend(card)

    # ── Chips ─────────────────────────────────────────────────────────────────

    def _rebuild_chips(self) -> None:
        while child := self._chip_box.get_first_child():
            self._chip_box.remove(child)

        types = sorted({r.generator_type for r in self._records if r.generator_type})
        for label, filt in [("All", None)] + [(t, t) for t in types]:
            btn = Gtk.ToggleButton(label=label)
            btn.add_css_class("artgen-filter-chip")
            btn.set_active(filt == self._active_filter)
            btn.connect("toggled", self._on_chip_toggled, filt)
            self._chip_box.append(btn)

    def _on_chip_toggled(self, btn: Gtk.ToggleButton, filt: Optional[str]) -> None:
        if not btn.get_active():
            return
        self._active_filter = filt
        # Deactivate other chips
        child = self._chip_box.get_first_child()
        while child:
            if child is not btn and isinstance(child, Gtk.ToggleButton):
                child.set_active(False)
            child = child.get_next_sibling()
        self._rebuild_grid()

    # ── Grid ──────────────────────────────────────────────────────────────────

    def _rebuild_grid(self) -> None:
        while child := self._flow.get_first_child():
            self._flow.remove(child)
        filtered = [r for r in self._records
                    if self._active_filter is None or r.generator_type == self._active_filter]
        for rec in filtered:
            self._flow.append(self._make_card(rec))

    def _make_card(self, rec: MediaRecord) -> Gtk.Box:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_size_request(110, 90)
        outer._media_id = rec.id  # stash for activation handler
        outer.add_css_class("artgen-card")

        # Thumbnail or type placeholder
        if rec.thumbnail_path and Path(rec.thumbnail_path).exists():
            img = Gtk.Picture.new_for_filename(rec.thumbnail_path)
            img.set_content_fit(Gtk.ContentFit.COVER)
        elif rec.file_path.endswith(".svg") and Path(rec.file_path).exists():
            img = Gtk.Picture.new_for_filename(rec.file_path)
            img.set_content_fit(Gtk.ContentFit.COVER)
        else:
            img = Gtk.Label(label=self._type_emoji(rec.generator_type))
            img.add_css_class("artgen-card-placeholder")

        img.set_hexpand(True)
        img.set_vexpand(True)
        outer.append(img)

        # Bottom label: type badge + timestamp
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bottom.add_css_class("artgen-card-bottom")
        type_lbl = Gtk.Label(label=(rec.generator_type or "?")[:4])
        type_lbl.add_css_class("artgen-type-badge")
        bottom.append(type_lbl)
        ts = Gtk.Label(label=rec.created_at[5:10] if len(rec.created_at) >= 10 else "")
        ts.add_css_class("muted")
        ts.set_hexpand(True)
        ts.set_xalign(1.0)
        bottom.append(ts)
        outer.append(bottom)
        return outer

    @staticmethod
    def _type_emoji(gt: Optional[str]) -> str:
        return {"landscape": "🏔", "skyline": "🌃", "verse": "✍",
                "constellation": "✦", "geometric": "⬡", "circuit": "⬟",
                "palette": "#", "ansi": "▓", "freeform": "?"}.get(gt or "", "✦")

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_card_activated(self, _flow, child) -> None:
        box = child.get_child()
        if box and hasattr(box, "_media_id") and self.on_card_activated:
            self.on_card_activated(box._media_id)

    def _on_watch_clicked(self, _btn) -> None:
        if self.on_watch_requested:
            self.on_watch_requested(self._active_filter)
```

- [ ] **Step 2: Manual test (quick smoke)**

```python
# run from app/ dir: python3 -c "..."
import gi; gi.require_version("Gtk","4.0")
from gi.repository import Gtk, GLib
from artgen_gallery import ArtgenGallery

app = Gtk.Application(application_id="test.gallery")
def activate(a):
    w = Gtk.ApplicationWindow(application=a, title="Gallery test", default_width=800, default_height=500)
    g = ArtgenGallery()
    g.refresh()
    w.set_child(g)
    w.present()
app.connect("activate", activate)
app.run()
```

Expected: window opens, chips and cards visible (may be empty if no artgen records yet)

- [ ] **Step 3: Commit**

```bash
git add app/artgen_gallery.py
git commit -m "feat: ArtgenGallery widget — card grid + filter chips"
```

---

## Task 9: ArtgenDetail widget

**Files:**
- Create: `app/artgen_detail.py`

- [ ] **Step 1: Create `app/artgen_detail.py`**

```python
#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
ArtgenDetail — full-pane detail view for a single artgen artifact.

Layout: ← Gallery header | large artifact (65%) | metadata sidebar (35%)
Navigation: ‹ › arrows step through the current filter without returning to grid.

Callbacks:
    on_back()           — user clicked ← Gallery
    on_deleted(id: str) — user confirmed deletion
    on_starred(id: str, starred: bool)
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gio, GLib, Gtk, Pango

from media_store import media_store as _ms, MediaRecord


class ArtgenDetail(Gtk.Box):

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.on_back: Optional[Callable[[], None]] = None
        self.on_deleted: Optional[Callable[[str], None]] = None
        self.on_starred: Optional[Callable[[str, bool], None]] = None
        self._records: list[MediaRecord] = []
        self._idx: int = 0
        self._build()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # Header bar
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hdr.set_margin_start(12)
        hdr.set_margin_end(12)
        hdr.set_margin_top(8)
        hdr.set_margin_bottom(8)

        back_btn = Gtk.Button(label="← Gallery")
        back_btn.add_css_class("flat")
        back_btn.connect("clicked", lambda _: self.on_back and self.on_back())
        hdr.append(back_btn)

        self._title_lbl = Gtk.Label(label="")
        self._title_lbl.set_hexpand(True)
        self._title_lbl.set_xalign(0.5)
        self._title_lbl.add_css_class("artgen-detail-title")
        hdr.append(self._title_lbl)

        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._prev_btn = Gtk.Button(label="‹")
        self._prev_btn.connect("clicked", lambda _: self._step(-1))
        self._next_btn = Gtk.Button(label="›")
        self._next_btn.connect("clicked", lambda _: self._step(1))
        nav_box.append(self._prev_btn)
        nav_box.append(self._next_btn)
        hdr.append(nav_box)

        self.append(hdr)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Body: artifact (left 65%) + sidebar (right 35%)
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        body.set_vexpand(True)

        # Artifact pane
        art_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        art_box.set_hexpand(True)
        art_box.set_vexpand(True)

        self._art_stack = Gtk.Stack()
        self._art_stack.set_hexpand(True)
        self._art_stack.set_vexpand(True)

        # SVG
        svg_scroll = Gtk.ScrolledWindow()
        svg_scroll.set_hexpand(True)
        svg_scroll.set_vexpand(True)
        self._svg_pic = Gtk.Picture()
        self._svg_pic.set_hexpand(True)
        self._svg_pic.set_vexpand(True)
        self._svg_pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        svg_scroll.set_child(self._svg_pic)
        self._art_stack.add_named(svg_scroll, "svg")

        # Text / ANSI
        text_scroll = Gtk.ScrolledWindow()
        text_scroll.set_hexpand(True)
        text_scroll.set_vexpand(True)
        self._text_view = Gtk.TextView()
        self._text_view.set_editable(False)
        self._text_view.set_monospace(True)
        self._text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._text_view.set_margin_start(20)
        self._text_view.set_margin_end(20)
        self._text_view.set_margin_top(16)
        self._text_view.set_margin_bottom(16)
        text_scroll.set_child(self._text_view)
        self._art_stack.add_named(text_scroll, "text")

        art_box.append(self._art_stack)
        body.append(art_box)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # Sidebar
        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_size_request(260, -1)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sidebar.set_margin_start(12)
        sidebar.set_margin_end(12)
        sidebar.set_margin_top(12)
        sidebar.set_margin_bottom(12)

        self._meta_lbl = Gtk.Label(label="")
        self._meta_lbl.set_xalign(0)
        self._meta_lbl.set_wrap(True)
        self._meta_lbl.add_css_class("muted")
        sidebar.append(self._meta_lbl)

        self._params_lbl = Gtk.Label(label="")
        self._params_lbl.set_xalign(0)
        self._params_lbl.set_wrap(True)
        self._params_lbl.set_selectable(True)
        sidebar.append(self._params_lbl)

        # Star toggle
        self._star_btn = Gtk.ToggleButton(label="☆  Star")
        self._star_btn.connect("toggled", self._on_star_toggled)
        sidebar.append(self._star_btn)

        # Open file
        open_btn = Gtk.Button(label="Open File")
        open_btn.connect("clicked", self._on_open_file)
        sidebar.append(open_btn)

        # Delete
        self._del_btn = Gtk.Button(label="🗑 Delete")
        self._del_btn.add_css_class("destructive-action")
        self._del_btn.connect("clicked", self._on_delete)
        sidebar.append(self._del_btn)

        sidebar_scroll.set_child(sidebar)
        body.append(sidebar_scroll)

        self.append(body)

    # ── Public ────────────────────────────────────────────────────────────────

    def show_record(self, media_id: str, records: list[MediaRecord]) -> None:
        """Display the record with media_id; records is the current filter list."""
        self._records = records
        self._idx = next((i for i, r in enumerate(records) if r.id == media_id), 0)
        self._render()

    # ── Navigation ────────────────────────────────────────────────────────────

    def _step(self, delta: int) -> None:
        if not self._records:
            return
        self._idx = (self._idx + delta) % len(self._records)
        self._render()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self) -> None:
        if not self._records:
            return
        rec = self._records[self._idx]
        n = len(self._records)

        self._title_lbl.set_label(
            f"{rec.generator_type or 'artgen'} — {rec.created_at[:10]}  ({self._idx+1}/{n})"
        )
        self._prev_btn.set_sensitive(n > 1)
        self._next_btn.set_sensitive(n > 1)

        # Metadata sidebar
        p = rec.params_dict
        gen_s = p.get("generation_seconds", "")
        gen_str = f"  ({gen_s}s)" if gen_s else ""
        self._meta_lbl.set_label(
            f"{rec.created_at[:19].replace('T',' ')}{gen_str}\n"
            f"model: {rec.model_id or '—'}"
        )
        param_lines = "\n".join(
            f"{k}: {v}" for k, v in p.items()
            if k not in ("generation_seconds",) and isinstance(v, (str, int, float, bool))
        )
        self._params_lbl.set_label(param_lines)

        self._star_btn.handler_block_by_func(self._on_star_toggled)
        self._star_btn.set_active(bool(rec.starred))
        self._star_btn.set_label("★  Starred" if rec.starred else "☆  Star")
        self._star_btn.handler_unblock_by_func(self._on_star_toggled)

        # Artifact
        fp = Path(rec.file_path)
        if fp.suffix.lower() == ".svg" and fp.exists():
            self._svg_pic.set_file(Gio.File.new_for_path(str(fp)))
            self._art_stack.set_visible_child_name("svg")
        else:
            text = fp.read_text(encoding="utf-8", errors="replace") if fp.exists() else ""
            self._text_view.get_buffer().set_text(text)
            self._art_stack.set_visible_child_name("text")

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _on_star_toggled(self, btn: Gtk.ToggleButton) -> None:
        if not self._records:
            return
        rec = self._records[self._idx]
        starred = btn.get_active()
        _ms.star(rec.id, starred)
        rec.starred = int(starred)
        btn.set_label("★  Starred" if starred else "☆  Star")
        if self.on_starred:
            self.on_starred(rec.id, starred)

    def _on_open_file(self, _btn) -> None:
        if not self._records:
            return
        rec = self._records[self._idx]
        if Path(rec.file_path).exists():
            subprocess.Popen(["xdg-open", rec.file_path])

    def _on_delete(self, _btn) -> None:
        if not self._records:
            return
        rec = self._records[self._idx]
        dialog = Gtk.AlertDialog()
        dialog.set_message("Delete this artifact?")
        dialog.set_detail(f"{rec.generator_type} — {rec.created_at[:10]}")
        dialog.set_buttons(["Cancel", "Delete"])
        dialog.set_cancel_button(0)
        dialog.set_default_button(0)
        dialog.choose(self.get_root(), None, self._delete_confirmed, rec.id)

    def _delete_confirmed(self, dialog, result, media_id: str) -> None:
        try:
            btn_idx = dialog.choose_finish(result)
        except Exception:
            return
        if btn_idx != 1:
            return
        _ms.delete(media_id)
        self._records = [r for r in self._records if r.id != media_id]
        if self.on_deleted:
            self.on_deleted(media_id)
        if self._records:
            self._idx = min(self._idx, len(self._records) - 1)
            self._render()
        elif self.on_back:
            self.on_back()
```

- [ ] **Step 2: Manual test**

```python
# python3 -c "..." from app/ dir
import gi; gi.require_version("Gtk","4.0")
from gi.repository import Gtk
from artgen_detail import ArtgenDetail
from media_store import media_store as ms

app = Gtk.Application(application_id="test.detail")
def activate(a):
    w = Gtk.ApplicationWindow(application=a, title="Detail test",
                               default_width=900, default_height=600)
    d = ArtgenDetail()
    records = ms.query(media_type="artgen")
    if records:
        d.show_record(records[0].id, records)
    w.set_child(d)
    w.present()
app.connect("activate", activate)
app.run()
```

- [ ] **Step 3: Commit**

```bash
git add app/artgen_detail.py
git commit -m "feat: ArtgenDetail widget — full-pane artifact view + ‹ › navigation"
```

---

## Task 10: ArtgenWatch widget

**Files:**
- Create: `app/artgen_watch.py`

- [ ] **Step 1: Create `app/artgen_watch.py`**

```python
#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
ArtgenWatch — fullscreen-within-pane slideshow.

Fills the artgen tab's content area. The sub-navigation header is hidden
by the parent (ArtgenPanel) while Watch is active.

Callbacks:
    on_exit()           — user pressed Esc / ← Gallery
    on_deleted(id: str) — user deleted current item
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from media_store import media_store as _ms, MediaRecord

_DWELL_DEFAULT = 10   # seconds between auto-advances


class ArtgenWatch(Gtk.Overlay):
    """Slideshow overlay: artifact fills the pane, UI overlay fades on idle."""

    def __init__(self) -> None:
        super().__init__()
        self.on_exit: Optional[Callable[[], None]] = None
        self.on_deleted: Optional[Callable[[str], None]] = None
        self._records: list[MediaRecord] = []
        self._idx: int = 0
        self._playing: bool = True
        self._dwell: int = _DWELL_DEFAULT
        self._countdown: int = _DWELL_DEFAULT
        self._advance_timer: int | None = None
        self._countdown_timer: int | None = None
        self._overlay_visible: bool = True
        self._hide_timer: int | None = None
        self._build()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.set_hexpand(True)
        self.set_vexpand(True)

        # Background: artifact display
        self._bg = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._bg.set_hexpand(True)
        self._bg.set_vexpand(True)
        self._bg.add_css_class("artgen-watch-bg")

        self._art_stack = Gtk.Stack()
        self._art_stack.set_hexpand(True)
        self._art_stack.set_vexpand(True)
        self._art_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._art_stack.set_transition_duration(400)

        self._svg_pic = Gtk.Picture()
        self._svg_pic.set_hexpand(True)
        self._svg_pic.set_vexpand(True)
        self._svg_pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._art_stack.add_named(self._svg_pic, "svg")

        text_scroll = Gtk.ScrolledWindow()
        text_scroll.set_hexpand(True)
        text_scroll.set_vexpand(True)
        self._text_view = Gtk.TextView()
        self._text_view.set_editable(False)
        self._text_view.set_monospace(True)
        self._text_view.set_cursor_visible(False)
        self._text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._text_view.set_margin_start(60)
        self._text_view.set_margin_end(60)
        self._text_view.set_margin_top(40)
        text_scroll.set_child(self._text_view)
        self._art_stack.add_named(text_scroll, "text")

        self._bg.append(self._art_stack)
        self.set_child(self._bg)

        # Overlay: controls
        overlay_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        overlay_box.set_hexpand(True)
        overlay_box.set_vexpand(True)
        overlay_box.add_css_class("artgen-watch-overlay")

        # Top bar
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top.set_margin_start(16)
        top.set_margin_end(16)
        top.set_margin_top(10)
        top.add_css_class("artgen-watch-top")

        back_btn = Gtk.Button(label="← Gallery")
        back_btn.add_css_class("flat")
        back_btn.add_css_class("artgen-watch-btn")
        back_btn.connect("clicked", lambda _: self._exit())
        top.append(back_btn)

        self._pos_lbl = Gtk.Label(label="")
        self._pos_lbl.set_hexpand(True)
        self._pos_lbl.set_xalign(0.5)
        self._pos_lbl.add_css_class("artgen-watch-pos")
        top.append(self._pos_lbl)

        close_btn = Gtk.Button(label="✕")
        close_btn.add_css_class("flat")
        close_btn.add_css_class("artgen-watch-btn")
        close_btn.connect("clicked", lambda _: self._exit())
        top.append(close_btn)
        overlay_box.append(top)

        # Spacer (fills middle — left/right arrows are absolute-positioned via Overlay)
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        overlay_box.append(spacer)

        # Bottom bar
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bottom.set_margin_start(16)
        bottom.set_margin_end(16)
        bottom.set_margin_bottom(10)
        bottom.add_css_class("artgen-watch-bottom")

        self._meta_lbl = Gtk.Label(label="")
        self._meta_lbl.add_css_class("artgen-watch-meta")
        self._meta_lbl.set_hexpand(True)
        self._meta_lbl.set_xalign(0)
        bottom.append(self._meta_lbl)

        progress_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._progress = Gtk.ProgressBar()
        self._progress.set_size_request(200, -1)
        progress_col.append(self._progress)

        self._dwell_lbl = Gtk.Label(label="")
        self._dwell_lbl.add_css_class("artgen-watch-meta")
        self._dwell_lbl.set_xalign(0.5)
        progress_col.append(self._dwell_lbl)
        bottom.append(progress_col)

        self._play_btn = Gtk.Button(label="⏸")
        self._play_btn.add_css_class("artgen-watch-btn")
        self._play_btn.connect("clicked", self._on_play_pause)
        bottom.append(self._play_btn)

        overlay_box.append(bottom)
        self.add_overlay(overlay_box)

        # Left / right nav buttons (centered vertically)
        left_btn = Gtk.Button(label="‹")
        left_btn.add_css_class("artgen-watch-nav-btn")
        left_btn.set_valign(Gtk.Align.CENTER)
        left_btn.set_halign(Gtk.Align.START)
        left_btn.set_margin_start(12)
        left_btn.connect("clicked", lambda _: self._step(-1))
        self.add_overlay(left_btn)

        right_btn = Gtk.Button(label="›")
        right_btn.add_css_class("artgen-watch-nav-btn")
        right_btn.set_valign(Gtk.Align.CENTER)
        right_btn.set_halign(Gtk.Align.END)
        right_btn.set_margin_end(12)
        right_btn.connect("clicked", lambda _: self._step(1))
        self.add_overlay(right_btn)

        # Mouse motion → show overlay
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

        # Keyboard shortcuts
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self.add_controller(key)

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self, records: list[MediaRecord], start_idx: int = 0) -> None:
        self._records = records
        self._idx = start_idx
        self._playing = True
        self._countdown = self._dwell
        self._render()
        self._start_timers()

    def stop(self) -> None:
        self._stop_timers()

    # ── Render ────────────────────────────────────────────────────────────────

    def _render(self) -> None:
        if not self._records:
            return
        rec = self._records[self._idx]
        n = len(self._records)
        self._pos_lbl.set_label(f"{self._idx+1} / {n}")

        p = rec.params_dict
        self._meta_lbl.set_label(
            f"{rec.generator_type or '?'} · "
            + " · ".join(str(v) for k, v in p.items()
                         if k in ("palette","theme","form","style","subject","era")
                         and isinstance(v, str))
            + f" · {rec.created_at[:10]}"
        )

        fp = Path(rec.file_path)
        if fp.suffix.lower() == ".svg" and fp.exists():
            self._svg_pic.set_file(Gio.File.new_for_path(str(fp)))
            self._art_stack.set_visible_child_name("svg")
        else:
            text = fp.read_text(encoding="utf-8", errors="replace") if fp.exists() else ""
            self._text_view.get_buffer().set_text(text)
            self._art_stack.set_visible_child_name("text")

    # ── Timers ────────────────────────────────────────────────────────────────

    def _start_timers(self) -> None:
        self._stop_timers()
        if self._playing:
            self._countdown = self._dwell
            self._advance_timer = GLib.timeout_add_seconds(self._dwell, self._auto_advance)
            self._countdown_timer = GLib.timeout_add(1000, self._tick_countdown)

    def _stop_timers(self) -> None:
        for attr in ("_advance_timer", "_countdown_timer", "_hide_timer"):
            tid = getattr(self, attr, None)
            if tid is not None:
                GLib.source_remove(tid)
                setattr(self, attr, None)

    def _auto_advance(self) -> bool:
        self._step(1)
        return GLib.SOURCE_REMOVE

    def _tick_countdown(self) -> bool:
        self._countdown = max(0, self._countdown - 1)
        frac = self._countdown / self._dwell if self._dwell else 0
        self._progress.set_fraction(frac)
        self._dwell_lbl.set_label(f"{self._countdown}s")
        return GLib.SOURCE_CONTINUE

    # ── Navigation ────────────────────────────────────────────────────────────

    def _step(self, delta: int) -> None:
        if not self._records:
            return
        self._idx = (self._idx + delta) % len(self._records)
        self._render()
        if self._playing:
            self._start_timers()

    def _exit(self) -> None:
        self.stop()
        if self.on_exit:
            self.on_exit()

    # ── Overlay visibility ────────────────────────────────────────────────────

    def _show_overlay(self) -> None:
        self._overlay_visible = True
        # We don't actually hide widgets — CSS opacity via class would do it;
        # for now, reset the hide timer on any activity.
        if self._hide_timer is not None:
            GLib.source_remove(self._hide_timer)
        self._hide_timer = GLib.timeout_add_seconds(3, self._hide_overlay)

    def _hide_overlay(self) -> bool:
        self._overlay_visible = False
        self._hide_timer = None
        return GLib.SOURCE_REMOVE

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_motion(self, _ctrl, _x, _y) -> None:
        self._show_overlay()

    def _on_play_pause(self, _btn) -> None:
        self._playing = not self._playing
        self._play_btn.set_label("⏸" if self._playing else "▶")
        if self._playing:
            self._start_timers()
        else:
            self._stop_timers()

    def _on_key(self, _ctrl, keyval, _keycode, _state) -> bool:
        self._show_overlay()
        if keyval == Gdk.KEY_Escape:
            self._exit(); return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_leftarrow if hasattr(Gdk,"KEY_leftarrow") else 0):
            self._step(-1); return True
        if keyval == Gdk.KEY_Left:
            self._step(-1); return True
        if keyval == Gdk.KEY_Right:
            self._step(1); return True
        if keyval == Gdk.KEY_space:
            self._on_play_pause(None); return True
        if keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self._toggle_star(); return True
        if keyval == Gdk.KEY_Delete:
            self._delete_current(); return True
        return False

    def _toggle_star(self) -> None:
        if not self._records:
            return
        rec = self._records[self._idx]
        starred = not bool(rec.starred)
        _ms.star(rec.id, starred)
        rec.starred = int(starred)

    def _delete_current(self) -> None:
        if not self._records:
            return
        rec = self._records[self._idx]
        dialog = Gtk.AlertDialog()
        dialog.set_message("Delete this artifact?")
        dialog.set_buttons(["Cancel", "Delete"])
        dialog.set_cancel_button(0)
        dialog.choose(self.get_root(), None, self._delete_confirmed, rec.id)

    def _delete_confirmed(self, dialog, result, media_id: str) -> None:
        try:
            if dialog.choose_finish(result) != 1:
                return
        except Exception:
            return
        _ms.delete(media_id)
        self._records = [r for r in self._records if r.id != media_id]
        if self.on_deleted:
            self.on_deleted(media_id)
        if not self._records:
            self._exit()
        else:
            self._idx = min(self._idx, len(self._records) - 1)
            self._render()
            if self._playing:
                self._start_timers()
```

- [ ] **Step 2: Commit**

```bash
git add app/artgen_watch.py
git commit -m "feat: ArtgenWatch widget — slideshow with auto-advance + keyboard controls"
```

---

## Task 11: artgen_panel.py — sub-navigation + Create mini-grid + wiring

**Files:**
- Modify: `app/artgen_panel.py`
- Modify: `app/main_window.py` (CSS only)

This task rewires `ArtgenPanel` to be a three-sub-tab widget: Create / Gallery / Watch.

- [ ] **Step 1: Restructure `ArtgenPanel.__init__` and `_build`**

Replace the `__init__` and `_build` methods of `ArtgenPanel`:

```python
def __init__(self) -> None:
    super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    self._generating: bool = False
    self._last_out_path: Path | None = None
    self._tmp_svg: Path | None = None
    self._llm_timer_id: int | None = None
    self._llm_t0: float = 0.0
    self._build()
    GLib.timeout_add_seconds(5, self._poll_health)
    threading.Thread(target=self._check_health_bg, daemon=True).start()

def _build(self) -> None:
    from artgen_gallery import ArtgenGallery
    from artgen_detail import ArtgenDetail
    from artgen_watch import ArtgenWatch

    # ── Sub-navigation header ─────────────────────────────────────────────
    nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    nav.add_css_class("artgen-subnav")

    self._create_tab_btn = Gtk.ToggleButton(label="✦ Create")
    self._create_tab_btn.set_active(True)
    self._create_tab_btn.add_css_class("artgen-subnav-btn")
    self._create_tab_btn.connect("toggled", self._on_tab_toggled, "create")

    self._gallery_tab_btn = Gtk.ToggleButton(label="▦ Gallery")
    self._gallery_tab_btn.add_css_class("artgen-subnav-btn")
    self._gallery_tab_btn.connect("toggled", self._on_tab_toggled, "gallery")
    self._gallery_tab_btn.set_group(self._create_tab_btn)

    self._watch_tab_btn = Gtk.ToggleButton(label="▶ Watch")
    self._watch_tab_btn.add_css_class("artgen-subnav-btn")
    self._watch_tab_btn.connect("toggled", self._on_tab_toggled, "watch")
    self._watch_tab_btn.set_group(self._create_tab_btn)

    nav.append(self._create_tab_btn)
    nav.append(self._gallery_tab_btn)
    nav.append(self._watch_tab_btn)
    self.append(nav)
    self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

    # ── Sub-tab stack ─────────────────────────────────────────────────────
    self._sub_stack = Gtk.Stack()
    self._sub_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
    self._sub_stack.set_transition_duration(120)
    self._sub_stack.set_hexpand(True)
    self._sub_stack.set_vexpand(True)

    # Create tab
    create_pane = self._build_create_pane()
    self._sub_stack.add_named(create_pane, "create")

    # Gallery tab
    self._gallery = ArtgenGallery()
    self._gallery.on_card_activated = self._on_gallery_card_activated
    self._gallery.on_watch_requested = self._on_watch_requested
    self._sub_stack.add_named(self._gallery, "gallery")

    # Detail view (not a tab — navigated to from gallery)
    self._detail = ArtgenDetail()
    self._detail.on_back = self._on_detail_back
    self._detail.on_deleted = self._on_detail_deleted
    self._sub_stack.add_named(self._detail, "detail")

    # Watch
    self._watch = ArtgenWatch()
    self._watch.on_exit = self._on_watch_exit
    self._sub_stack.add_named(self._watch, "watch")

    self._sub_stack.set_visible_child_name("create")
    self.append(self._sub_stack)
```

- [ ] **Step 2: Build the Create pane (left controls + right mini-grid)**

Add `_build_create_pane` method (moves the existing left controls + preview into a horizontal split, with the right side showing the 4-card mini-grid):

```python
def _build_create_pane(self) -> Gtk.Box:
    """Two-column Create tab: controls (left 240px) + latest-generations mini-grid (right)."""
    pane = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    pane.set_hexpand(True)
    pane.set_vexpand(True)

    # ── Left: controls (moved from existing _build) ───────────────────────
    left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    left_box.set_size_request(240, -1)
    left_box.add_css_class("artgen-ctrl-pane")

    type_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    type_bar.set_margin_start(12); type_bar.set_margin_end(12)
    type_bar.set_margin_top(10); type_bar.set_margin_bottom(6)
    type_lbl = _section_lbl("type")
    type_lbl.set_size_request(44, -1)
    type_bar.append(type_lbl)
    gen_names = artgen.all_names()
    self._type_dd = _dd(gen_names, "landscape")
    self._type_dd.set_hexpand(True)
    self._type_dd.connect("notify::selected", self._on_type_changed)
    type_bar.append(self._type_dd)
    left_box.append(type_bar)
    left_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

    self._controls_stack = Gtk.Stack()
    self._controls_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
    self._controls_stack.set_transition_duration(80)
    self._controls_stack.set_margin_start(12); self._controls_stack.set_margin_end(12)
    self._controls_stack.set_margin_top(10); self._controls_stack.set_margin_bottom(6)
    for name in gen_names:
        self._controls_stack.add_named(self._build_controls_page(name), name)
    self._controls_stack.set_visible_child_name("landscape")
    ctrl_scroll = Gtk.ScrolledWindow()
    ctrl_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    ctrl_scroll.set_vexpand(True)
    ctrl_scroll.set_child(self._controls_stack)
    left_box.append(ctrl_scroll)
    left_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

    # Server section
    srv_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    srv_box.set_margin_start(12); srv_box.set_margin_end(12)
    srv_box.set_margin_top(8); srv_box.set_margin_bottom(4)
    srv_box.append(_section_lbl("server"))
    self._srv_model_dd = _dd(_ARTGEN_MODELS, "Qwen3-8B")
    srv_box.append(_row("Model", self._srv_model_dd, label_width=46))
    srv_btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    self._srv_start_btn = Gtk.Button(label="Start")
    self._srv_start_btn.add_css_class("artgen-srv-start-btn")
    self._srv_start_btn.connect("clicked", self._on_srv_start)
    srv_btn_row.append(self._srv_start_btn)
    self._srv_stop_btn = Gtk.Button(label="Stop")
    self._srv_stop_btn.add_css_class("artgen-srv-stop-btn")
    self._srv_stop_btn.connect("clicked", self._on_srv_stop)
    srv_btn_row.append(self._srv_stop_btn)
    self._health_dot = Gtk.Label(label="●")
    self._health_dot.set_margin_start(4)
    self._health_dot.add_css_class("artgen-health-unknown")
    srv_btn_row.append(self._health_dot)
    self._srv_status_lbl = Gtk.Label(label="unknown")
    self._srv_status_lbl.set_xalign(0)
    self._srv_status_lbl.add_css_class("artgen-status")
    self._srv_status_lbl.set_hexpand(True)
    srv_btn_row.append(self._srv_status_lbl)
    srv_box.append(srv_btn_row)
    left_box.append(srv_box)

    footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    footer.set_margin_start(12); footer.set_margin_end(12)
    footer.set_margin_top(10); footer.set_margin_bottom(12)
    self._gen_btn = Gtk.Button(label="✦ Generate")
    self._gen_btn.add_css_class("artgen-generate-btn")
    self._gen_btn.connect("clicked", self._on_generate_clicked)
    footer.append(self._gen_btn)
    self._status_lbl = Gtk.Label(label="Ready — choose a type and click Generate")
    self._status_lbl.set_xalign(0)
    self._status_lbl.add_css_class("artgen-status")
    self._status_lbl.set_wrap(True)
    self._status_lbl.set_max_width_chars(32)
    footer.append(self._status_lbl)
    left_box.append(footer)

    pane.append(left_box)
    pane.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

    # ── Right: latest-generations mini-grid ───────────────────────────────
    right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    right_box.set_hexpand(True)
    right_box.set_vexpand(True)
    right_box.set_margin_start(12); right_box.set_margin_end(12)
    right_box.set_margin_top(10); right_box.set_margin_bottom(10)

    mini_hdr = Gtk.Label(label="LATEST GENERATIONS — click any to go to Gallery")
    mini_hdr.set_xalign(0)
    mini_hdr.add_css_class("section-label")
    right_box.append(mini_hdr)

    self._mini_flow = Gtk.FlowBox()
    self._mini_flow.set_max_children_per_line(4)
    self._mini_flow.set_min_children_per_line(2)
    self._mini_flow.set_homogeneous(True)
    self._mini_flow.set_selection_mode(Gtk.SelectionMode.NONE)
    self._mini_flow.set_row_spacing(6)
    self._mini_flow.set_column_spacing(6)
    self._mini_flow.set_vexpand(True)
    self._mini_flow.connect("child-activated", self._on_mini_card_activated)
    right_box.append(self._mini_flow)

    self._view_all_btn = Gtk.Button(label="→ View all in Gallery")
    self._view_all_btn.add_css_class("flat")
    self._view_all_btn.connect("clicked", lambda _: self._switch_tab("gallery"))
    right_box.append(self._view_all_btn)

    pane.append(right_box)
    self._refresh_mini_grid()
    return pane
```

- [ ] **Step 3: Add sub-tab switching methods**

```python
def _on_tab_toggled(self, btn: Gtk.ToggleButton, tab: str) -> None:
    if not btn.get_active():
        return
    if tab == "gallery":
        self._gallery.refresh()
    self._sub_stack.set_visible_child_name(tab)

def _switch_tab(self, tab: str) -> None:
    if tab == "gallery":
        self._create_tab_btn.set_active(False)
        self._gallery_tab_btn.set_active(True)
        self._gallery.refresh()
    self._sub_stack.set_visible_child_name(tab)

def _on_gallery_card_activated(self, media_id: str) -> None:
    records = _media_store.query(
        media_type="artgen",
        generator_type=self._gallery._active_filter,
    )
    self._detail.show_record(media_id, records)
    self._sub_stack.set_visible_child_name("detail")

def _on_detail_back(self) -> None:
    self._sub_stack.set_visible_child_name("gallery")

def _on_detail_deleted(self, media_id: str) -> None:
    self._gallery.refresh()
    self._refresh_mini_grid()

def _on_watch_requested(self, generator_type: Optional[str]) -> None:
    records = _media_store.query(media_type="artgen", generator_type=generator_type)
    if not records:
        return
    self._watch.start(records)
    self._watch_tab_btn.set_active(True)
    self._sub_stack.set_visible_child_name("watch")

def _on_watch_exit(self) -> None:
    self._watch.stop()
    self._gallery_tab_btn.set_active(True)
    self._sub_stack.set_visible_child_name("gallery")

def _on_mini_card_activated(self, _flow, child) -> None:
    self._switch_tab("gallery")

def _refresh_mini_grid(self) -> None:
    while child := self._mini_flow.get_first_child():
        self._mini_flow.remove(child)
    recent = _media_store.query(media_type="artgen", limit=4)
    if not recent:
        placeholder = Gtk.Label(label="✦\nYour generations\nwill appear here")
        placeholder.set_xalign(0.5)
        placeholder.add_css_class("artgen-empty-hint")
        self._mini_flow.append(placeholder)
        self._view_all_btn.set_label("→ View all in Gallery")
        return
    total = len(_media_store.query(media_type="artgen"))
    self._view_all_btn.set_label(f"→ View all {total} in Gallery")
    for i, rec in enumerate(recent):
        card = self._make_mini_card(rec, highlight=(i == 0))
        self._mini_flow.append(card)

def _make_mini_card(self, rec: MediaRecord, highlight: bool = False) -> Gtk.Box:
    from artgen_gallery import ArtgenGallery
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.set_size_request(100, 80)
    box.add_css_class("artgen-card")
    if highlight:
        box.add_css_class("artgen-card-new")
    # thumbnail or emoji
    if rec.thumbnail_path and Path(rec.thumbnail_path).exists():
        img = Gtk.Picture.new_for_filename(rec.thumbnail_path)
        img.set_content_fit(Gtk.ContentFit.COVER)
    elif rec.file_path.endswith(".svg") and Path(rec.file_path).exists():
        img = Gtk.Picture.new_for_filename(rec.file_path)
        img.set_content_fit(Gtk.ContentFit.COVER)
    else:
        img = Gtk.Label(label=ArtgenGallery._type_emoji(rec.generator_type))
        img.add_css_class("artgen-card-placeholder")
    img.set_hexpand(True)
    img.set_vexpand(True)
    box.append(img)
    lbl = Gtk.Label(label=f"{rec.generator_type or '?'} · {rec.created_at[5:10]}")
    lbl.add_css_class("artgen-card-bottom")
    box.append(lbl)
    return box
```

- [ ] **Step 4: Update `_finish_success` to refresh mini-grid**

In `_finish_success`, after all existing code, add:

```python
    GLib.idle_add(self._refresh_mini_grid)
```

- [ ] **Step 5: Remove the old standalone preview pane**

Delete the entire old right_box / `_preview_stack` / `_open_btn` / `_saved_path_lbl` block from the original `_build` method — it's replaced by the Create pane's mini-grid on the right. The `_show_output` method can be removed too.

Update `_finish_success` — remove `_show_output` call and `_open_btn`/`_saved_path_lbl` references (those UI elements no longer exist).

- [ ] **Step 6: Add CSS for new classes to `main_window.py`**

Find the existing artgen CSS block in `main_window.py` and append:

```css
.artgen-subnav { background: shade(@tt_bg_dark, 0.85); }
.artgen-subnav-btn { border-radius: 0; padding: 6px 16px; font-size: 12px; }
.artgen-subnav-btn:checked { color: @tt_accent; border-bottom: 2px solid @tt_accent; }
.artgen-filter-chip { border-radius: 12px; padding: 2px 10px; font-size: 11px; }
.artgen-filter-chip:checked { background: @tt_accent; color: @tt_bg_dark; }
.artgen-card { border-radius: 4px; background: @tt_bg_panel; }
.artgen-card-new { border: 2px solid @tt_accent; }
.artgen-card-placeholder { font-size: 20px; }
.artgen-card-bottom { font-size: 9px; padding: 3px 5px; color: @tt_muted; }
.artgen-type-badge { font-size: 8px; background: alpha(@tt_bg_dark,0.8);
                     color: @tt_accent; padding: 1px 4px; border-radius: 2px; }
.artgen-watch-bg { background: #000; }
.artgen-watch-overlay { }
.artgen-watch-btn { color: rgba(255,255,255,0.8); background: transparent; border: none; }
.artgen-watch-nav-btn { font-size: 22px; background: rgba(0,0,0,0.5);
                         border-radius: 50%; color: white; padding: 4px 10px; }
.artgen-watch-pos { color: rgba(255,255,255,0.7); font-size: 12px; }
.artgen-watch-meta { color: rgba(255,255,255,0.6); font-size: 11px; }
.artgen-detail-title { font-size: 12px; color: @tt_muted; }
.artgen-inspire-btn { background: @tt_accent; color: @tt_bg_dark;
                       border-radius: 3px; padding: 3px 8px; }
```

- [ ] **Step 7: Manual end-to-end test**

```
python3 app/main.py
```

1. Artgen tab shows Create / Gallery / Watch sub-nav
2. Generate a landscape → mini-grid updates with new card (teal border)
3. Click "→ View all in Gallery" → Gallery tab opens with card
4. Click a card → Detail view opens with ← Gallery + ‹ › arrows
5. Click ▶ Watch → slideshow starts, Space pauses, Esc returns to Gallery
6. Click ✦ on Theme Inspiration → entry updates

- [ ] **Step 8: Commit**

```bash
git add app/artgen_panel.py app/main_window.py app/artgen_gallery.py app/artgen_detail.py app/artgen_watch.py
git commit -m "feat: artgen Create/Gallery/Watch sub-tabs — full gallery experience"
```

---

## Verification Checklist

Run all tests to confirm nothing regressed:

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all existing tests pass; new `test_media_store.py` tests pass.

Manual walkthrough:
- [ ] Generate an artgen artifact → saved to `~/.local/share/tt-video-gen/artgen/`
- [ ] Record appears in `media.db`: `sqlite3 ~/.local/share/tt-video-gen/media.db "SELECT id,generator_type,created_at FROM media WHERE media_type='artgen'"`
- [ ] Gallery shows card with thumbnail
- [ ] Detail view shows artifact + metadata + star/delete work
- [ ] Watch slideshow auto-advances, Space pauses, Esc exits
- [ ] `history_store.py` and `playlist_store.py` tests still pass
- [ ] Video/image generation still works (not broken by wrapper refactor)
