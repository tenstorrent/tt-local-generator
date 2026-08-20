#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Library converter — brings an older media.db library up to current
conventions. Backs `tt-ctl convert-library`.

GTK-free at import (only stdlib + media_store/artgen_thumb, neither of which
imports gi at module level -- gi is only ever pulled in lazily, inside
artgen_thumb.make_thumbnail's per-extension branches, exactly like every
other headless caller of that function).

Two independent, idempotent, fail-soft conversions:

1. Media-type fold -- AnimateDiff (.gif) and Wan2.2-Animate (.mp4) records
   get media_type='video' (+ a generator_type provenance stamp). This is the
   SAME SQL the app's own startup migration runs
   (media_store.MediaStore._migrate_media_types), delegated through the
   shared `media_store.fold_media_types(conn)` so the two paths can never
   drift apart. The difference here is that this tool calls it UNGATED --
   always, regardless of the DB's PRAGMA user_version -- so it works as a
   standalone dry-run/headless/arbitrary-DB tool independent of whatever the
   app itself has already migrated.

2. Stale-thumbnail regeneration -- every `media_type='artgen'` record whose
   source file is NOT a `.gif` gets its thumbnail re-rendered from scratch
   via the CURRENT renderer (artgen_thumb.make_thumbnail), fixing thumbnails
   baked by an older renderer that text-rendered raw ANSI escapes / raw JSON
   instead of drawing a real color grid / swatch grid. This one the app does
   NOT do automatically -- it is only ever run via this tool.

`analyze()` is strictly read-only (opens the DB in sqlite's `mode=ro` URI
form and never issues a write). `apply()` performs the actual conversion and
returns a report dict; pass `backup=True` to copy the DB file aside first.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

import media_store
from artgen_thumb import make_thumbnail

# Extensions this tool will regenerate a thumbnail for. Deliberately
# everything artgen produces EXCEPT .gif -- an animated gif's thumbnail is a
# still first-frame render that hasn't changed shape/logic since it was
# written, and .gif artgen records are also the ones about to be folded into
# media_type='video' by the fold step above, so re-rendering them here would
# be wasted, soon-to-be-irrelevant work.
_THUMB_REGEN_EXCLUDE_EXTS = {".gif"}


def default_db_path() -> Path:
    """The standard library location -- same as the app's own MediaStore."""
    return media_store.STORAGE_DIR / media_store._DB_FILENAME


def _resolve_db_path(db_path: Optional[Path | str]) -> Path:
    return Path(db_path) if db_path is not None else default_db_path()


def _fold_pending_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM media WHERE media_type != 'video' "
        "AND (media_type IN ('animatediff', 'animate') "
        "OR generator_type IN ('animatediff', 'animate'))"
    ).fetchone()[0]


def _thumb_candidate_rows(conn: sqlite3.Connection):
    """Yield (id, file_path, thumbnail_path) for every artgen record that is
    a plausible thumbnail-regen candidate on SQL terms alone (media_type
    check + non-empty thumbnail_path/file_path) -- file-existence and
    extension are checked in Python since sqlite can't stat the filesystem."""
    return conn.execute(
        "SELECT id, file_path, thumbnail_path FROM media WHERE media_type='artgen'"
    ).fetchall()


def _is_thumb_regen_candidate(file_path: str, thumbnail_path: str) -> bool:
    if not file_path or not thumbnail_path:
        return False
    ext = Path(file_path).suffix.lower()
    if ext in _THUMB_REGEN_EXCLUDE_EXTS:
        return False
    return Path(file_path).exists()


def analyze(db_path: Optional[Path | str] = None) -> dict:
    """Read-only report of what `apply()` would do. Never writes anything --
    opens the database via a read-only sqlite URI connection.

    Returns {"fold_pending": int, "thumbs_pending": int}.
    """
    path = _resolve_db_path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"No library database found at {path}")

    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        fold_pending = _fold_pending_count(conn)
        thumbs_pending = sum(
            1
            for _id, file_path, thumbnail_path in _thumb_candidate_rows(conn)
            if _is_thumb_regen_candidate(file_path, thumbnail_path)
        )
        return {"fold_pending": fold_pending, "thumbs_pending": thumbs_pending}
    finally:
        conn.close()


def _looks_like_a_real_image(path: Path) -> bool:
    """True if `path` loads as an image with both dimensions > 1px -- i.e.
    a genuine render, not the 1x1 grey placeholder artgen_thumb falls back
    to on failure. GdkPixbuf is the same loader the rest of the app already
    uses to display thumbnails, so "loads for us" == "loads for the app"."""
    try:
        if not path.exists():
            return False
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
        return pixbuf.get_width() > 1 and pixbuf.get_height() > 1
    except Exception:
        return False


def _regenerate_one_thumbnail(file_path: str, thumbnail_path: str) -> bool:
    """Re-render a single record's thumbnail in place. Returns True on a
    verified-good regeneration, False otherwise (source render failure,
    degraded placeholder, or any exception) -- callers count False as
    failed/skipped, never let it raise past this function."""
    src = Path(file_path)
    dst = Path(thumbnail_path)
    # NOTE: several make_thumbnail branches (.ans/.json/.txt/.md/.py) call
    # PIL's Image.save(path) with NO explicit format= — PIL infers the
    # format from the path's extension, so the tmp target here MUST end in
    # a real image extension (".png") or those branches raise "unknown file
    # extension" and silently degrade to the 1x1 placeholder (only the
    # raster branch passes format="PNG" explicitly and would tolerate any
    # extension). Mirrors the real convention: every make_thumbnail output
    # is a PNG except the svg-render-failure fallback, which .with_suffix
    # (".svg")s whatever dst we hand it regardless of our chosen extension.
    tmp = dst.parent / f"{dst.stem}.regen{uuid.uuid4().hex[:8]}.png"
    try:
        result = make_thumbnail(src, tmp)
        if not _looks_like_a_real_image(result):
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Always land the fresh bytes at the ORIGINAL thumbnail_path the DB
        # already references (never rename result onto a new path) -- some
        # make_thumbnail branches (svg-render-failure fallback) can return a
        # path with a different suffix than requested; the DB's
        # thumbnail_path must keep pointing at a file that exists.
        shutil.move(str(result), str(dst))
        return True
    except Exception:
        return False
    finally:
        # Clean up any leftover tmp file that didn't get moved (failed
        # render, exception before the move, etc) -- including the
        # svg-render-failure fallback name make_thumbnail may have used
        # instead of the exact `tmp` path we requested (`tmp.with_suffix
        # (".svg")`, mirroring its own fallback logic for an .svg source).
        for stray in (tmp, tmp.with_suffix(".svg")):
            try:
                if stray.exists():
                    stray.unlink()
            except Exception:
                pass


def apply(
    db_path: Optional[Path | str] = None,
    *,
    regen_thumbnails: bool = True,
    backup: bool = False,
) -> dict:
    """Perform the conversion. Idempotent + fail-soft: a second call folds 0
    rows, and any single bad record during thumbnail regen is counted as
    failed/skipped rather than aborting the run.

    Returns {"folded": int, "thumbs_regenerated": int, "thumbs_skipped": int,
              "thumbs_failed": int, "backup_path": str | None}.
    """
    path = _resolve_db_path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"No library database found at {path}")

    backup_path: Optional[Path] = None
    if backup:
        ts = time.strftime("%Y%m%d-%H%M%S")
        backup_path = path.with_name(f"{path.name}.bak-{ts}")
        shutil.copy2(path, backup_path)

    conn = sqlite3.connect(str(path))
    try:
        folded = media_store.fold_media_types(conn)
        conn.commit()

        thumbs_regenerated = 0
        thumbs_skipped = 0
        thumbs_failed = 0

        if regen_thumbnails:
            # Read candidate rows BEFORE the fold's effects matter here --
            # the fold only ever moves rows OUT of media_type='video' review
            # scope by turning artgen->video (animatediff), so a row that
            # just got folded is correctly no longer 'artgen' and won't
            # appear in this query (already committed above).
            for _id, file_path, thumbnail_path in _thumb_candidate_rows(conn):
                if not thumbnail_path or not file_path:
                    thumbs_skipped += 1
                    continue
                ext = Path(file_path).suffix.lower()
                if ext in _THUMB_REGEN_EXCLUDE_EXTS:
                    thumbs_skipped += 1
                    continue
                if not Path(file_path).exists():
                    thumbs_skipped += 1
                    continue
                try:
                    ok = _regenerate_one_thumbnail(file_path, thumbnail_path)
                except Exception:
                    ok = False
                if ok:
                    thumbs_regenerated += 1
                else:
                    thumbs_failed += 1

        return {
            "folded": folded,
            "thumbs_regenerated": thumbs_regenerated,
            "thumbs_skipped": thumbs_skipped,
            "thumbs_failed": thumbs_failed,
            "backup_path": str(backup_path) if backup_path else None,
        }
    finally:
        conn.close()
