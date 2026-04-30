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
import logging
import sqlite3
import threading
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
        self._lock = threading.Lock()  # serialises concurrent writes
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._migrate_from_json()

    def _migrate_from_json(self) -> None:
        """One-time migration from history.json + playlists.json. No-op if already done."""
        history_file  = self._db_path.parent / "history.json"
        playlist_file = self._db_path.parent / "playlists.json"
        history_bak  = history_file.with_suffix(".json.bak")
        if history_bak.exists():
            return  # already migrated
        if history_file.exists():
            try:
                raw = json.loads(history_file.read_text())
                for r in raw:
                    self._migrate_history_row(r)
                history_file.rename(history_bak)
            except Exception as exc:
                logging.warning("media_store: migration error: %s", exc)
        playlist_bak = playlist_file.with_suffix(".json.bak")
        if playlist_file.exists() and not playlist_bak.exists():
            try:
                raw = json.loads(playlist_file.read_text())
                for pl in raw:
                    self._migrate_playlist_row(pl)
                playlist_file.rename(playlist_bak)
            except Exception as exc:
                logging.warning("media_store: migration error: %s", exc)

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
            # Guard against orphaned record_ids whose media rows no longer exist.
            # Without this check, FK enforcement raises IntegrityError and aborts
            # the entire playlist migration, leaving playlists.json un-renamed.
            exists = self._conn.execute(
                "SELECT 1 FROM media WHERE id=?", (mid,)
            ).fetchone()
            if exists:
                self._conn.execute(
                    "INSERT OR IGNORE INTO playlist_items(playlist_id, media_id, position) VALUES (?,?,?)",
                    (pl_id, mid, pos),
                )
        self._conn.commit()

    def _upsert(self, rec: MediaRecord) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO media "
                "(id,media_type,created_at,file_path,thumbnail_path,prompt,model_id,"
                " generator_type,params,starred) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (rec.id, rec.media_type, rec.created_at, rec.file_path,
                 rec.thumbnail_path, rec.prompt, rec.model_id, rec.generator_type,
                 rec.params, rec.starred),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Public CRUD API
    # ------------------------------------------------------------------

    def add(self, record: MediaRecord) -> str:
        """Insert *record* into the store and return its id."""
        self._upsert(record)
        return record.id

    def get(self, id: str) -> Optional[MediaRecord]:
        """Return the MediaRecord for *id*, or None if not found."""
        row = self._conn.execute(
            "SELECT id,media_type,created_at,file_path,thumbnail_path,prompt,"
            "       model_id,generator_type,params,starred "
            "FROM media WHERE id=?", (id,)
        ).fetchone()
        return MediaRecord(*row) if row else None

    def delete(self, id: str) -> bool:
        """Delete the record with *id*. Returns True if a row was removed."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM media WHERE id=?", (id,))
            self._conn.commit()
        return cur.rowcount > 0

    def star(self, id: str, starred: bool) -> None:
        """Set the starred flag on the record with *id*."""
        with self._lock:
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
        """
        Return records matching the supplied filters, newest-first.

        All filter arguments are optional; omitting them returns everything.
        """
        clauses, params = [], []
        if media_type is not None:
            clauses.append("media_type=?")
            params.append(media_type)
        if generator_type is not None:
            clauses.append("generator_type=?")
            params.append(generator_type)
        if starred is not None:
            clauses.append("starred=?")
            params.append(int(starred))
        sql = (
            "SELECT id,media_type,created_at,file_path,thumbnail_path,prompt,"
            "       model_id,generator_type,params,starred FROM media"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = self._conn.execute(sql, params).fetchall()
        return [MediaRecord(*r) for r in rows]


_media_store_singleton: "MediaStore | None" = None


def _get_media_store() -> "MediaStore":
    """Return the production singleton, creating it on first call."""
    global _media_store_singleton
    if _media_store_singleton is None:
        _media_store_singleton = MediaStore()
    return _media_store_singleton


# Lazy singleton proxy — behaves like a MediaStore instance for attribute access.
# Instantiation of the real MediaStore (and therefore the DB open + migration)
# is deferred until the first attribute access, so importing this module during
# tests does NOT touch ~/.local/share/tt-video-gen/media.db.
# Use `from media_store import media_store` in other modules.
class _MediaStoreProxy:
    def __getattr__(self, name):
        return getattr(_get_media_store(), name)


media_store = _MediaStoreProxy()
