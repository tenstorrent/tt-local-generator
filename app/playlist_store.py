#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
playlist_store.py — Named playlist management for tt-local-generator.

Playlists are ordered collections of GenerationRecord IDs that can be
used as "channels" in the TT-TV attractor window.  Each playlist also
carries an auto_gen flag controlling whether TT-TV auto-generates new
content when that channel is active.

Storage: ~/.local/share/tt-video-gen/playlists.json
Format: JSON array of playlist objects, written atomically.

    [
      {
        "id": "a1b2c3d4-...",
        "name": "Space Adventures",
        "record_ids": ["uuid1", "uuid2", ...],
        "auto_gen": true
      },
      ...
    ]

Can be read/modified by external scripts:

    python3 -c "
    import json, pathlib
    p = pathlib.Path.home() / '.local/share/tt-video-gen/playlists.json'
    playlists = json.loads(p.read_text())
    print([pl['name'] for pl in playlists])
    "
"""

import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

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
    """
    Persistent collection of named playlists.

    All mutating methods write through to disk immediately so the JSON file
    is always current and can be read or written by external scripts.
    """

    def __init__(self) -> None:
        self._playlists: list[Playlist] = []
        self._load()

    # ── Public API ─────────────────────────────────────────────────────────────

    def all(self) -> list[Playlist]:
        """Return a copy of the playlist list (insertion order preserved)."""
        return list(self._playlists)

    def get(self, playlist_id: str) -> Optional[Playlist]:
        """Return the playlist with the given ID, or None."""
        for pl in self._playlists:
            if pl.id == playlist_id:
                return pl
        return None

    def create(self, name: str) -> Playlist:
        """Create a new empty playlist with the given name. Returns the new Playlist."""
        pl = Playlist(id=str(uuid.uuid4()), name=name.strip(), record_ids=[])
        self._playlists.append(pl)
        self._save()
        log.info("playlist created: %r (%s)", pl.name, pl.id)
        return pl

    def rename(self, playlist_id: str, new_name: str) -> bool:
        """Rename a playlist. Returns True if found and renamed, False otherwise."""
        pl = self.get(playlist_id)
        if pl is None:
            return False
        pl.name = new_name.strip()
        self._save()
        log.info("playlist renamed to %r (%s)", pl.name, playlist_id)
        return True

    def delete(self, playlist_id: str) -> bool:
        """Delete a playlist by ID. Returns True if found and deleted."""
        before = len(self._playlists)
        self._playlists = [pl for pl in self._playlists if pl.id != playlist_id]
        if len(self._playlists) < before:
            self._save()
            log.info("playlist deleted: %s", playlist_id)
            return True
        return False

    def add_records(self, playlist_id: str, record_ids: list) -> int:
        """
        Append record IDs to a playlist, deduplicating against existing entries.
        Returns the number of newly added IDs.
        """
        pl = self.get(playlist_id)
        if pl is None:
            log.warning("add_records: playlist not found: %s", playlist_id)
            return 0
        existing = set(pl.record_ids)
        new_ids = [rid for rid in record_ids if rid not in existing]
        pl.record_ids.extend(new_ids)
        if new_ids:
            self._save()
            log.info("added %d record(s) to playlist %r (%s)", len(new_ids), pl.name, playlist_id)
        return len(new_ids)

    def remove_record(self, playlist_id: str, record_id: str) -> bool:
        """Remove a single record ID from a playlist. Returns True if removed."""
        pl = self.get(playlist_id)
        if pl is None:
            return False
        if record_id not in pl.record_ids:
            return False
        pl.record_ids.remove(record_id)
        self._save()
        return True

    def set_auto_gen(self, playlist_id: str, value: bool) -> bool:
        """Set the auto_gen flag for a playlist. Returns True if found."""
        pl = self.get(playlist_id)
        if pl is None:
            return False
        pl.auto_gen = value
        self._save()
        return True

    def purge_deleted_records(self, valid_ids: set) -> int:
        """
        Remove any record IDs that no longer exist in the history store.
        Call this after deleting a generation record to keep playlists consistent.
        Returns the total number of IDs pruned.
        """
        total = 0
        for pl in self._playlists:
            before = len(pl.record_ids)
            pl.record_ids = [rid for rid in pl.record_ids if rid in valid_ids]
            total += before - len(pl.record_ids)
        if total:
            self._save()
            log.info("purged %d stale record ID(s) from playlists", total)
        return total

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if PLAYLISTS_FILE.exists():
                raw = json.loads(PLAYLISTS_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    for item in raw:
                        if not isinstance(item, dict):
                            continue
                        self._playlists.append(Playlist(
                            id=item.get("id", str(uuid.uuid4())),
                            name=item.get("name", "Untitled"),
                            record_ids=list(item.get("record_ids", [])),
                            auto_gen=bool(item.get("auto_gen", True)),
                        ))
        except Exception as exc:
            log.warning("playlist_store: could not load %s: %s", PLAYLISTS_FILE, exc)

    def _save(self) -> None:
        try:
            STORAGE_DIR.mkdir(parents=True, exist_ok=True)
            data = json.dumps(
                [asdict(pl) for pl in self._playlists],
                indent=2,
                ensure_ascii=False,
            ) + "\n"
            tmp = PLAYLISTS_FILE.with_suffix(".json.tmp")
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, PLAYLISTS_FILE)
        except Exception as exc:
            log.warning("playlist_store: could not save %s: %s", PLAYLISTS_FILE, exc)


# ── Module-level singleton ─────────────────────────────────────────────────────

playlist_store = PlaylistStore()
