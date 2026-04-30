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

    def star(self, id: str, starred: bool) -> bool:
        """Set the starred flag on the record with *id*. Returns True if the row existed."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE media SET starred=? WHERE id=?", (int(starred), id)
            )
            self._conn.commit()
        return cur.rowcount > 0

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

    # ------------------------------------------------------------------
    # Playlist API
    # ------------------------------------------------------------------

    def create_playlist(
        self, name: str, filter_expr: Optional[str] = None, auto_gen: bool = True
    ) -> str:
        """
        Create a new playlist and return its UUID.

        Parameters
        ----------
        name:        Human-readable playlist name (whitespace is stripped).
        filter_expr: Optional SQL WHERE fragment (e.g. "generator_type='landscape'").
                     When set the playlist is "live" — playlist_records() evaluates
                     the expression at query time rather than reading playlist_items.
        auto_gen:    True when the playlist was created automatically by the app;
                     False for user-created playlists.  Stored for UI distinction.
        """
        pl_id = str(uuid.uuid4())
        with self._lock:
            self._conn.execute(
                "INSERT INTO playlists(id,name,auto_gen,filter_expr,created_at) VALUES (?,?,?,?,?)",
                (pl_id, name.strip(), int(auto_gen), filter_expr,
                 datetime.now(timezone.utc).isoformat()),
            )
            self._conn.commit()
        return pl_id

    def list_playlists(self) -> list[dict]:
        """Return all playlists as dicts, ordered by creation time (oldest first)."""
        rows = self._conn.execute(
            "SELECT id,name,auto_gen,filter_expr,created_at FROM playlists ORDER BY created_at"
        ).fetchall()
        return [{"id": r[0], "name": r[1], "auto_gen": bool(r[2]),
                 "filter_expr": r[3], "created_at": r[4]} for r in rows]

    def delete_playlist(self, playlist_id: str) -> bool:
        """
        Delete the playlist with *playlist_id*.

        Cascade rules in the schema will also remove all playlist_items rows
        for this playlist.  Returns True if a row was deleted, False if not found.
        """
        with self._lock:
            cur = self._conn.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def add_to_playlist(self, playlist_id: str, media_id: str) -> None:
        """
        Append *media_id* to the hand-curated playlist *playlist_id*.

        The item is placed after all existing items (position = MAX+1 or 0).
        Duplicate entries are silently ignored (INSERT OR IGNORE).
        """
        with self._lock:
            # Compute the next position in a single atomic read inside the lock.
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
        """Remove *media_id* from the hand-curated playlist *playlist_id*."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM playlist_items WHERE playlist_id=? AND media_id=?",
                (playlist_id, media_id),
            )
            self._conn.commit()

    def playlist_records(self, playlist_id: str) -> list[MediaRecord]:
        """
        Return the MediaRecords for a playlist.

        Live playlists (filter_expr IS NOT NULL): the stored SQL fragment is
        evaluated against the media table and results are returned newest-first.

        Hand-curated playlists (filter_expr IS NULL): items are returned in
        their insertion order (ascending position).

        Returns [] if *playlist_id* does not exist.
        """
        row = self._conn.execute(
            "SELECT filter_expr FROM playlists WHERE id=?", (playlist_id,)
        ).fetchone()
        if row is None:
            return []
        filter_expr = row[0]
        if filter_expr is not None:
            # Live playlist: evaluate the filter expression against the media table.
            # filter_expr is always set by create_playlist() within this application
            # (never from raw user input), so direct interpolation is intentional.
            sql = (
                "SELECT id,media_type,created_at,file_path,thumbnail_path,prompt,"
                "       model_id,generator_type,params,starred FROM media "
                f"WHERE {filter_expr} ORDER BY created_at DESC"
            )
            rows = self._conn.execute(sql).fetchall()
        else:
            # Hand-curated playlist: join through playlist_items in position order.
            rows = self._conn.execute(
                "SELECT m.id,m.media_type,m.created_at,m.file_path,m.thumbnail_path,"
                "       m.prompt,m.model_id,m.generator_type,m.params,m.starred "
                "FROM media m JOIN playlist_items pi ON m.id=pi.media_id "
                "WHERE pi.playlist_id=? ORDER BY pi.position",
                (playlist_id,),
            ).fetchall()
        return [MediaRecord(*r) for r in rows]

    def auto_playlist_types(self) -> list[str]:
        """
        Return the distinct generator_type values for all artgen media records.

        Used by ensure_auto_playlists() to decide which live playlists to create.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT generator_type FROM media "
            "WHERE media_type='artgen' AND generator_type IS NOT NULL"
        ).fetchall()
        return [r[0] for r in rows]

    def ensure_auto_playlists(self) -> None:
        """
        Idempotently create one live playlist per artgen generator_type.

        A playlist whose filter_expr already matches a given generator_type is
        left untouched, so calling this method repeatedly is safe.
        """
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
