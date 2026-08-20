#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Seed the bundled **demo collection** into a media library.

The project ships a small, curated set of representative generations under
``demo-collection/`` (a ``manifest.json`` plus ``media/`` and ``thumbnails/``
directories — see that dir's README). This module loads that collection into a
``media.db`` so a fresh install opens with real art to look at, grouped in a
"Demo" playlist (which the "Start something" wall also treats as a curated tile
source — `possibilities._default_curated_matcher` matches the name "demo").

Design:
- **Idempotent.** Records are keyed by their original id; a record already
  present is left untouched (``MediaStore.add`` is INSERT-OR-IGNORE), and the
  playlist only gains a member it doesn't already have. Re-running seeds 0.
- **Copies media out of the read-only collection** into a dedicated
  ``<storage>/demo-collection/`` dir so `file_path`/`thumbnail_path` point at
  stable, writable locations the app reads like any other record's (galleries
  key off `media_type`, not the directory, so a video in this dir still shows
  under Video, etc.).
- **`caption` is what the app displays.** The manifest keeps each item's
  original generation `prompt` for provenance and a cleaned `caption`; the
  seeded record's `prompt` column is the caption (that's the field the gallery
  renders). Items with no caption fall back to the original prompt.
- **GTK-free** — stdlib + `media_store` only, so it runs from `tt-ctl`, a
  postinst hook, or a test without a display.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

import media_store
from media_store import MediaRecord

_DEMO_PLAYLIST_NAME = "Demo"
# Where an installed .deb drops the collection; falls back to the in-repo copy
# for dev runs (running straight from a checkout).
_INSTALLED_DIR = Path("/usr/share/tt-local-generator/demo-collection")
_REPO_DIR = Path(__file__).resolve().parent.parent / "demo-collection"


def resolve_collection_dir(explicit: Optional[Path | str] = None) -> Path:
    """Locate the demo-collection directory: an explicit path wins, then the
    installed system copy, then the in-repo copy. Raises if none has a
    manifest."""
    for cand in (explicit, _INSTALLED_DIR, _REPO_DIR):
        if cand is None:
            continue
        p = Path(cand)
        if (p / "manifest.json").exists():
            return p
    raise FileNotFoundError(
        "No demo-collection/manifest.json found (looked at explicit path, "
        f"{_INSTALLED_DIR}, {_REPO_DIR})"
    )


def seed_demo(
    db_path: Optional[Path | str] = None,
    collection_dir: Optional[Path | str] = None,
    *,
    storage_dir: Optional[Path | str] = None,
    force: bool = False,
) -> dict:
    """Load the demo collection into ``db_path`` (default: the app's media.db).

    Returns a report dict:
      {"seeded": int, "skipped": int, "playlist_added": int, "playlist_id": str,
       "collection_dir": str, "target_dir": str}
    """
    src = resolve_collection_dir(collection_dir)
    manifest = json.loads((src / "manifest.json").read_text())
    items = manifest.get("items", [])

    storage = Path(storage_dir) if storage_dir is not None else media_store.STORAGE_DIR
    db = Path(db_path) if db_path is not None else (storage / media_store._DB_FILENAME)
    target = storage / "demo-collection"
    (target / "media").mkdir(parents=True, exist_ok=True)
    (target / "thumbnails").mkdir(parents=True, exist_ok=True)

    store = media_store.MediaStore(db_path=db)
    existing = {r.id for r in store.query(limit=10_000_000)}

    seeded = skipped = 0
    seeded_ids: list[str] = []
    for it in items:
        mid = it["id"]
        if mid in existing and not force:
            skipped += 1
            # still ensure it's in the playlist below (membership guard handles dups)
            seeded_ids.append(mid)
            continue

        # Copy media + thumbnail into the stable target dir.
        media_rel = it["media"]
        media_dst = target / media_rel
        media_dst.parent.mkdir(parents=True, exist_ok=True)
        media_src = src / media_rel
        if media_src.exists():
            shutil.copy2(media_src, media_dst)
        thumb_abs = ""
        if it.get("thumbnail"):
            t_src = src / it["thumbnail"]
            t_dst = target / it["thumbnail"]
            t_dst.parent.mkdir(parents=True, exist_ok=True)
            if t_src.exists():
                shutil.copy2(t_src, t_dst)
                thumb_abs = str(t_dst)

        rec = MediaRecord(
            id=mid,
            media_type=it["media_type"],
            created_at=it["created_at"],
            file_path=str(media_dst),
            thumbnail_path=thumb_abs,
            # caption is the display text; keep original prompt only in the manifest
            prompt=(it.get("caption") or it.get("prompt") or "").strip(),
            model_id=it.get("model_id") or "",
            generator_type=it.get("generator_type"),
            params=it.get("params") or "{}",
            starred=0,
        )
        store.add(rec)
        seeded += 1
        seeded_ids.append(mid)

    # Curated "Demo" playlist (non-auto so it's a stable named collection).
    pid = None
    for pl in store.list_playlists():
        if pl.get("name") == _DEMO_PLAYLIST_NAME:
            pid = pl["id"]
            break
    if pid is None:
        pid = store.create_playlist(_DEMO_PLAYLIST_NAME, auto_gen=False)

    already = {r.id for r in store.playlist_records(pid)}
    playlist_added = 0
    for mid in seeded_ids:
        if mid not in already:
            store.add_to_playlist(pid, mid)
            playlist_added += 1

    return {
        "seeded": seeded,
        "skipped": skipped,
        "playlist_added": playlist_added,
        "playlist_id": pid,
        "collection_dir": str(src),
        "target_dir": str(target),
    }
