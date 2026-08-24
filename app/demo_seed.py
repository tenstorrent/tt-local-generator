#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Seed the bundled **demo collection** into a media library.

The project ships a small, curated set of representative generations under
``demo-collection/`` (a ``manifest.json`` plus ``media/`` and ``thumbnails/``
directories — see that dir's README). This module loads that collection into a
``media.db`` so a fresh install opens with real art to look at, grouped in a
"Welcome to tt-local-generator" playlist (which the "Start something" wall also
treats as a curated tile source — `possibilities._default_curated_matcher`
matches the name "welcome").

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
import os
import shutil
from pathlib import Path
from typing import Optional

import media_store
from media_store import MediaRecord

_DEMO_PLAYLIST_NAME = "Welcome to tt-local-generator"
# The .deb ships the collection under a system data dir (FHS: arch-independent
# read-only data lives in /usr/share, not /usr/lib). We DISCOVER it via the
# standard XDG search path rather than hardcoding one location, which gives the
# fallback chain for free: an admin can drop replacement/extra seed media in
# /usr/local/share/tt-local-generator/ (it takes precedence, per XDG order)
# without the package ever writing outside /usr/share.
_XDG_SUBPATH = Path("tt-local-generator") / "demo-collection"
# Dev-checkout / run-straight-from-source fallback (repo-root/demo-collection).
_REPO_DIR = Path(__file__).resolve().parent.parent / "demo-collection"


def _xdg_data_dirs() -> "list[Path]":
    """The XDG system data dirs, in precedence order (default per spec:
    /usr/local/share:/usr/share)."""
    raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [Path(d) for d in raw.split(":") if d.strip()]


def _candidate_dirs(explicit: Optional[Path | str]) -> "list[Path]":
    cands: list[Path] = []
    if explicit is not None:
        cands.append(Path(explicit))
    cands.extend(d / _XDG_SUBPATH for d in _xdg_data_dirs())
    cands.append(_REPO_DIR)
    return cands


def _already_seeded(marker: Path, version: int) -> bool:
    """True if the `.demo_seed_version` stamp records this collection version
    (or newer) as already seeded — so we don't re-insert user-deleted demo art
    on an upgrade. Fail-open (treat unreadable/absent as not-seeded)."""
    try:
        return marker.exists() and int(marker.read_text().strip() or 0) >= version
    except Exception:
        return False


def resolve_collection_dir(explicit: Optional[Path | str] = None) -> Path:
    """Locate the demo-collection directory: an explicit path wins, then each
    $XDG_DATA_DIRS entry (e.g. /usr/local/share then /usr/share), then the
    in-repo copy. Raises if none has a manifest."""
    cands = _candidate_dirs(explicit)
    for p in cands:
        if (p / "manifest.json").exists():
            return p
    raise FileNotFoundError(
        "No demo-collection/manifest.json found — searched: "
        + ", ".join(str(c) for c in cands)
    )


def seed_demo(
    db_path: Optional[Path | str] = None,
    collection_dir: Optional[Path | str] = None,
    *,
    storage_dir: Optional[Path | str] = None,
    force: bool = False,
) -> dict:
    """Load the demo collection into ``db_path`` (default: the app's media.db).

    Seeds ONCE per collection version (tracked by a `.demo_seed_version` stamp
    in the storage dir): `postinst` runs this on every install AND upgrade, and
    without the stamp a user who deleted demo art through the app would get it
    re-inserted on the next `apt upgrade`. The stamp makes idempotence match
    user intent, not just current DB state. `force=True` bypasses the stamp
    (and re-stamps). A newer collection `version` also re-seeds.

    Returns a report dict:
      {"seeded": int, "skipped": int, "missing": int, "playlist_added": int,
       "playlist_id": str|None, "collection_dir": str, "target_dir": str,
       "already_seeded": bool}
    """
    src = resolve_collection_dir(collection_dir)
    manifest = json.loads((src / "manifest.json").read_text())
    items = manifest.get("items", [])
    version = int(manifest.get("version", 1) or 1)

    # Where the copied media + the seeded-once marker live. Co-locate them with
    # the DB being mutated: an explicit storage_dir wins; else, if a db_path was
    # given, use ITS parent (so `--db /custom/media.db` keeps its demo-collection/
    # and .demo_seed_version next to that DB, not in the default library, and the
    # marker can't leak across DB locations — Copilot review); else the default
    # library dir.
    if storage_dir is not None:
        storage = Path(storage_dir)
    elif db_path is not None:
        storage = Path(db_path).parent
    else:
        storage = media_store.STORAGE_DIR
    db = Path(db_path) if db_path is not None else (storage / media_store._DB_FILENAME)
    target = storage / "demo-collection"
    marker = storage / ".demo_seed_version"

    # Seeded-once guard: skip entirely if this (or a newer) collection version
    # was already seeded, so upgrades never resurrect user-deleted demo art.
    if not force and _already_seeded(marker, version):
        return {"seeded": 0, "skipped": 0, "missing": 0, "playlist_added": 0,
                "playlist_id": None, "collection_dir": str(src),
                "target_dir": str(target), "already_seeded": True}

    (target / "media").mkdir(parents=True, exist_ok=True)
    (target / "thumbnails").mkdir(parents=True, exist_ok=True)

    store = media_store.MediaStore(db_path=db)
    existing = store.all_ids()  # lightweight SELECT id (not full-row materialisation)

    seeded = skipped = missing = 0
    seeded_ids: list[str] = []
    for it in items:
        mid = it["id"]
        media_rel = it["media"]
        media_src = src / media_rel
        # Guard FIRST — before any delete/mutate. A manifest entry whose media
        # file is missing on disk is skipped (counted), never inserted as a
        # dangling record. Doing this ahead of the --force delete also means a
        # missing item can't cause an existing row to be deleted-then-lost.
        if not media_src.exists():
            missing += 1
            continue

        if mid in existing:
            if not force:
                skipped += 1
                # still ensure it's in the playlist below (membership guard handles dups)
                seeded_ids.append(mid)
                continue
            # --force: MediaStore.add is INSERT-OR-IGNORE, so an existing row
            # would never be replaced — delete it first so the re-insert takes.
            # Safe now: we've already confirmed the replacement media exists.
            store.delete(mid)

        # Copy media + thumbnail into the stable target dir.
        media_dst = target / media_rel
        media_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(media_src, media_dst)
        thumb_abs = ""
        if it.get("thumbnail"):
            t_src = src / it["thumbnail"]
            t_dst = target / it["thumbnail"]
            t_dst.parent.mkdir(parents=True, exist_ok=True)
            if t_src.exists():
                shutil.copy2(t_src, t_dst)
                thumb_abs = str(t_dst)

        # The manifest's params blob carries the GENERATING machine's absolute
        # paths (video_path/image_path, e.g. /home/<builder>/.local/share/...).
        # history_store._to_gen PREFERS those over file_path, so left as-is they
        # resolve to dead links on every other machine (the library looks fine —
        # thumbnails come from thumbnail_path — but opening/playing fails). Strip
        # them: file_path (set below to the copied media) is authoritative, and
        # _to_gen falls back to it.
        params_out = it.get("params") or "{}"
        try:
            _pj = json.loads(params_out)
            if isinstance(_pj, dict) and ("video_path" in _pj or "image_path" in _pj):
                _pj.pop("video_path", None)
                _pj.pop("image_path", None)
                params_out = json.dumps(_pj)
        except Exception:
            pass

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
            params=params_out,
            # Shipped art is the DEFAULT, never auto-favorited — the user's own
            # stars stay meaningful. The collection is grouped by the
            # "Welcome to tt-local-generator" playlist instead (the manifest's
            # own `starred` provenance is deliberately not carried into a fresh
            # install).
            starred=0,
        )
        store.add(rec)
        seeded += 1
        seeded_ids.append(mid)

    # Curated "Welcome to tt-local-generator" playlist (non-auto — a stable
    # named collection the fresh install opens with).
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

    # Stamp this collection version as seeded so future upgrades short-circuit
    # (respecting any art the user later deletes). Fail-soft.
    try:
        marker.write_text(str(version))
    except Exception:
        pass

    return {
        "seeded": seeded,
        "skipped": skipped,
        "missing": missing,
        "playlist_added": playlist_added,
        "playlist_id": pid,
        "collection_dir": str(src),
        "target_dir": str(target),
        "already_seeded": False,
    }
