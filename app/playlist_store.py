#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
playlist_store.py — Named playlist management for tt-local-generator.

Playlists are ordered collections of media record IDs that can be used as
"channels" in the TT-TV attractor window.  Each playlist also carries an
auto_gen flag controlling whether TT-TV auto-generates new content when that
channel is active.

Storage: delegated to media_store (SQLite WAL database at
         ~/.local/share/tt-video-gen/media.db).
         PLAYLISTS_FILE is kept as a constant for backward-compatibility with
         external scripts that may reference it, but it is no longer written.

This module is now a thin wrapper around media_store that preserves the full
public API for existing callers.
"""

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Kept for backward compatibility — no longer written by this module.
STORAGE_DIR    = Path.home() / ".local" / "share" / "tt-video-gen"
PLAYLISTS_FILE = STORAGE_DIR / "playlists.json"


@dataclass
class Playlist:
    """One named playlist / TT-TV channel."""
    id: str                   # UUID string
    name: str                 # human-readable name
    record_ids: list          # ordered list of GenerationRecord IDs
    auto_gen: bool = True     # TT-TV auto-generates new content for this channel

    def contains(self, record_id: str) -> bool:
        return record_id in self.record_ids


class PlaylistStore:
    """Thin wrapper around media_store — preserves public API for existing callers."""

    def __init__(self) -> None:
        pass  # MediaStore handles all persistence

    # ── Public API ─────────────────────────────────────────────────────────────

    def all(self) -> list[Playlist]:
        """Return all playlists (ordered by creation time, oldest first)."""
        from media_store import media_store as _ms
        return [self._to_pl(r) for r in _ms.list_playlists()]

    def get(self, playlist_id: str) -> Optional[Playlist]:
        """Return the playlist with the given ID, or None."""
        from media_store import media_store as _ms
        rows = [r for r in _ms.list_playlists() if r["id"] == playlist_id]
        return self._to_pl(rows[0]) if rows else None

    def create(self, name: str) -> Playlist:
        """Create a new empty playlist with the given name. Returns the new Playlist."""
        from media_store import media_store as _ms
        pl_id = _ms.create_playlist(name.strip(), filter_expr=None, auto_gen=False)
        log.info("playlist created: %r (%s)", name.strip(), pl_id)
        pl = self.get(pl_id)
        if pl is None:
            raise RuntimeError(f"playlist {pl_id!r} missing immediately after creation")
        return pl

    def rename(self, playlist_id: str, new_name: str) -> bool:
        """Rename a playlist. Returns True if found and renamed, False otherwise."""
        from media_store import media_store as _ms
        result = _ms.rename_playlist(playlist_id, new_name)
        if result:
            log.info("playlist renamed to %r (%s)", new_name.strip(), playlist_id)
        return result

    def delete(self, playlist_id: str) -> bool:
        """Delete a playlist by ID. Returns True if found and deleted."""
        from media_store import media_store as _ms
        deleted = _ms.delete_playlist(playlist_id)
        if deleted:
            log.info("playlist deleted: %s", playlist_id)
        return deleted

    def add_records(self, playlist_id: str, record_ids: list) -> int:
        """
        Append record IDs to a playlist, deduplicating against existing entries.
        Returns the number of newly added IDs.

        Live/filter playlists (auto-generated from a SQL expression) cannot be
        mutated via playlist_items — their membership is computed at query time.
        Calls on such playlists are rejected and logged.
        """
        from media_store import media_store as _ms
        rows = [r for r in _ms.list_playlists() if r["id"] == playlist_id]
        if not rows:
            log.warning("add_records: playlist not found: %s", playlist_id)
            return 0
        if rows[0].get("filter_expr"):
            log.warning("add_records: playlist %s is a live/filter playlist — mutations not supported", playlist_id)
            return 0
        pl = self._to_pl(rows[0])
        existing = {m.id for m in _ms.playlist_records(playlist_id)}
        new_ids = [rid for rid in record_ids if rid not in existing]
        for rid in new_ids:
            _ms.add_to_playlist(playlist_id, rid)
        if new_ids:
            log.info("added %d record(s) to playlist %r (%s)", len(new_ids), pl.name, playlist_id)
        return len(new_ids)

    def remove_record(self, playlist_id: str, record_id: str) -> bool:
        """Remove a single record ID from a playlist. Returns True if removed.

        Live/filter playlists cannot be mutated — rejects with a warning like add_records().
        """
        from media_store import media_store as _ms
        rows = [r for r in _ms.list_playlists() if r["id"] == playlist_id]
        if not rows:
            return False
        if rows[0].get("filter_expr"):
            log.warning("remove_record: playlist %s is a live/filter playlist — mutations not supported", playlist_id)
            return False
        pl = self._to_pl(rows[0])
        if record_id not in pl.record_ids:
            return False
        _ms.remove_from_playlist(playlist_id, record_id)
        return True

    def set_auto_gen(self, playlist_id: str, value: bool) -> bool:
        """Set the auto_gen flag for a playlist. Returns True if found."""
        from media_store import media_store as _ms
        return _ms.set_playlist_auto_gen(playlist_id, value)

    def purge_deleted_records(self, valid_ids: set) -> int:
        """
        Remove any record IDs that no longer exist in the media store.
        Call this after deleting a generation record to keep playlists consistent.
        Returns the total number of IDs pruned.
        """
        from media_store import media_store as _ms
        removed = _ms.purge_playlist_items(valid_ids)
        if removed:
            log.info("purged %d stale record ID(s) from playlists", removed)
        return removed

    # ── Internal ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_pl(r: dict) -> "Playlist":
        """Convert a media_store playlist dict into a Playlist dataclass."""
        from media_store import media_store as _ms
        # Materialise record_ids for both hand-curated and live/filter playlists
        # so callers (including TT-TV channel switch) can always look up records
        # by ID without needing to know the playlist's internal filter expression.
        record_ids = [m.id for m in _ms.playlist_records(r["id"])]
        return Playlist(
            id=r["id"],
            name=r["name"],
            record_ids=record_ids,
            auto_gen=r["auto_gen"],
        )


# ── Module-level singleton ─────────────────────────────────────────────────────

playlist_store = PlaylistStore()
