#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Artgen artifact path helpers and thumbnail rendering.

Separated from media_store.py so that rendering imports (cairo, PIL, Rsvg)
don't bleed into the pure-storage module.
"""
from __future__ import annotations

import logging
import struct
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path

# Mirror the storage layout from media_store — defined here to avoid a circular
# import (media_store re-exports these functions for backward compatibility).
_STORAGE_DIR     = Path.home() / ".local" / "share" / "tt-video-gen"
ARTGEN_DIR       = _STORAGE_DIR / "artgen"
ARTGEN_THUMB_DIR = ARTGEN_DIR / "thumbnails"

# Extensions that are genuinely plain text and safe to render by reading
# their bytes as UTF-8 and drawing them with a monospace font. Anything NOT
# in this set (and not `.svg`, and not a recognised raster format) is
# treated as opaque binary — it must NEVER be text-rendered (that was the
# gif-shows-as-garbage-text bug: every non-svg extension fell into the text
# branch, including binary raster files like `.gif`/`.png`).
_TEXT_EXTS = {".txt", ".ans", ".md", ".json"}

# Raster image extensions PIL can open directly — includes `.gif`, whose
# first frame is used for the thumbnail (a still is the correct "preview"
# for a card thumbnail; the Create result panel animates the real .gif
# separately, see create_view.CreateResultPanel._build_artifact_widget).
_RASTER_EXTS = {".gif", ".png", ".jpg", ".jpeg", ".webp"}


def make_artgen_path(short_id: str, ext: str, base_dir: Path | None = None) -> Path:
    """Return a timestamped unique path for an artgen artifact."""
    if base_dir is None:
        base_dir = ARTGEN_DIR
    base_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return base_dir / f"{ts}_{short_id[:8]}{ext}"


def make_thumbnail(src: Path, dst: Path) -> Path:
    """
    Render a thumbnail for an artgen artifact.

    SVG            → tries gi.repository.Rsvg at 320×240, falls back to
                      copying the SVG as <dst>.with_suffix('.svg').
    raster/gif     → PIL opens the file and thumbnails it to fit 320×240; for
                      an animated `.gif` this uses PIL's default first-frame
                      (`img.seek(0)` is where `Image.open` already leaves the
                      cursor). Falls back to the placeholder PNG on failure —
                      NEVER to the text-render branch (a `.gif`/`.png`/etc.
                      is opaque binary, not text).
    .txt/.ans/.md/.json → PIL monospace text render of the file's UTF-8
                      content, same as before.
    anything else  → a 1×1 grey placeholder PNG. Previously every
                      unrecognised extension fell into the text-render
                      branch and got its raw binary bytes decoded as
                      "text" — this is the fix for that bug (see
                      CLAUDE.md's "artgen_thumb.make_thumbnail" root-cause
                      note): binary content is never text-rendered again.

    Returns the actual path written.
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
        except Exception as exc:
            logging.debug("make_thumbnail: Rsvg failed for %s: %s", src, exc)
        import shutil
        fallback = dst.with_suffix(".svg")
        shutil.copy2(src, fallback)
        return fallback

    # Raster images (including animated .gif — first frame only). This must
    # come BEFORE the text branch: these are binary formats, and the bug
    # this function is fixing was exactly this case falling through to the
    # text-render branch and drawing raw bytes as "text".
    if ext in _RASTER_EXTS:
        try:
            from PIL import Image
            with Image.open(src) as img:
                # For animated gifs, Image.open() already leaves the cursor
                # at frame 0 — no explicit seek needed, but this is
                # intentionally the first frame, not an attempt to
                # composite/animate.
                frame = img.convert("RGB")
                frame.thumbnail((320, 240))
                frame.save(str(dst), format="PNG")
            return dst
        except Exception as exc:
            logging.debug("make_thumbnail: PIL raster failed for %s: %s", src, exc)
        _write_placeholder_png(dst)
        return dst

    # Text (a deliberately narrow allow-list — see _TEXT_EXTS above)
    if ext in _TEXT_EXTS:
        try:
            from PIL import Image, ImageDraw, ImageFont
            img = Image.new("RGB", (320, 120), color=(13, 37, 48))
            draw = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 11
                )
            except Exception:
                font = ImageFont.load_default()
            text = src.read_text(encoding="utf-8", errors="replace")[:500]
            draw.text((6, 6), text, fill=(232, 240, 242), font=font)
            img.save(str(dst))
            return dst
        except Exception as exc:
            logging.debug("make_thumbnail: PIL failed for %s: %s", src, exc)
        _write_placeholder_png(dst)
        return dst

    # Anything else (unknown/binary extension): honest placeholder, never a
    # text-render of raw bytes.
    _write_placeholder_png(dst)
    return dst


def _write_placeholder_png(path: Path) -> None:
    """Write a minimal valid 1×1 grey PNG without requiring Pillow."""
    def _chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(c[4:]) & 0xFFFFFFFF)

    sig  = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw  = b"\x00\x80\x80\x80"  # filter byte + 1 RGB pixel (R=G=B=128, grey)
    idat = _chunk(b"IDAT", zlib.compress(raw))
    iend = _chunk(b"IEND", b"")
    path.write_bytes(sig + ihdr + idat + iend)
