#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Artgen artifact path helpers and thumbnail rendering.

Separated from media_store.py so that rendering imports (cairo, PIL, Rsvg)
don't bleed into the pure-storage module.
"""
from __future__ import annotations

import json
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
#
# `.json` (palette) and `.ans` (ansi) are DELIBERATELY not blanket members of
# this set even though they're UTF-8 text: each gets its own real raster
# render below (a swatch grid / a color grid) BEFORE this branch is reached,
# because rendering their raw syntax as prose is exactly the
# "garbage-text-PNG" bug this module exists to avoid (see CLAUDE.md's
# media-showcase-everywhere note). `.json` still falls through to this
# generic text branch when it ISN'T a colors-palette (plain JSON is
# legitimately fine to preview as text); `.ans` never falls through here —
# an unparseable/empty `.ans` degrades to the honest placeholder instead,
# because raw ANSI escape bytes read as "text" is unreadable garbage, not a
# real preview.
_TEXT_EXTS = {".txt", ".md", ".json", ".py"}

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
    .json (palette) → parses `{"colors": [{"hex": ...}, ...]}` (media-showcase-
                      everywhere Task 4) and draws a real swatch-grid PNG via
                      PIL. Falls through to the generic text render below if
                      the JSON isn't a colors-palette (plain JSON is fine to
                      preview as text) or parsing fails.
    .ans (ansi)    → parses via the shared `artgen_render.parse_ansi_grid`
                      (the single place that understands both ANSI pixel
                      formats — see that module's docstring) and draws a real
                      color-grid PNG via PIL. NEVER falls back to text-render
                      of the raw escape bytes on failure/empty — that's the
                      exact "garbage baked into a PNG" bug this fixes — it
                      falls back to the honest placeholder instead.
    .txt/.md/.py   → PIL monospace text render of the file's UTF-8 content
                      (genuine text/source; `.py` codeart previously fell all
                      the way through to the placeholder because it wasn't in
                      the allow-list, not because binary-safety demanded it).
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

    # Palette JSON: a real swatch-grid render. Falls through to the generic
    # text branch below (still member of _TEXT_EXTS) if this isn't actually
    # a colors-palette — plain JSON is legitimately fine to text-preview.
    if ext == ".json":
        try:
            raw = src.read_text(encoding="utf-8", errors="replace")
            data = json.loads(raw)
            colors = _extract_palette_hexes(data)
            if colors:
                _render_palette_png(colors, dst)
                return dst
        except Exception as exc:
            logging.debug("make_thumbnail: palette swatch render failed for %s: %s", src, exc)
        # Not a colors-palette (or parse failed) — fall through to the
        # generic text branch, which handles .json as plain text.

    # ANSI art: a real color-grid render via the shared parser. Never falls
    # back to raw-escape text-render — that's the exact bug being fixed —
    # so failure/empty content degrades straight to the placeholder.
    if ext == ".ans":
        try:
            raw = src.read_text(encoding="utf-8", errors="replace")
            from artgen_render import parse_ansi_grid
            grid = parse_ansi_grid(raw)
            if grid:
                _render_ansi_grid_png(grid, dst)
                return dst
        except Exception as exc:
            logging.debug("make_thumbnail: ansi grid render failed for %s: %s", src, exc)
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


def _extract_palette_hexes(data) -> list[str]:
    """Pull a flat list of "#RRGGBB" strings out of a palette-JSON payload.

    Accepts both the real generator schema (`palette.py`:
    `colors: [{"hex": "#RRGGBB", "role": "..."}, ...]`) and a plain
    `colors: ["#RRGGBB", ...]` list of bare hex strings. Returns an empty
    list (never raises) for anything that isn't a colors-palette, so the
    caller can cleanly fall through to the generic text render.
    """
    if not isinstance(data, dict):
        return []
    colors = data.get("colors")
    if not isinstance(colors, list):
        return []
    hexes: list[str] = []
    for item in colors:
        if isinstance(item, dict):
            hx = item.get("hex")
        elif isinstance(item, str):
            hx = item
        else:
            hx = None
        if isinstance(hx, str) and hx.startswith("#") and len(hx) in (4, 7):
            hexes.append(hx)
    return hexes


def _render_palette_png(colors: list[str], dst: Path) -> None:
    """Draw a real swatch-grid PNG (equal-width vertical strips, one per
    color) — the raster-thumbnail counterpart to
    `artgen_render.palette_to_html`'s `.strip` HTML element."""
    from PIL import Image, ImageDraw
    w, h = 320, 120
    img = Image.new("RGB", (w, h), color=(15, 42, 53))
    draw = ImageDraw.Draw(img)
    n = len(colors)
    seg_w = w / n
    for i, hx in enumerate(colors):
        x0 = int(i * seg_w)
        x1 = int((i + 1) * seg_w) if i < n - 1 else w
        draw.rectangle([x0, 0, x1, h], fill=hx)
    img.save(str(dst))


# Default cell color for an ANSI grid cell with no explicit fg/bg on the
# relevant channel — mirrors `artgen_render.ansi_to_html`'s DEFAULT.
_ANSI_GRID_DEFAULT_HEX = "#000000"


def _render_ansi_grid_png(grid: list[list[tuple]], dst: Path) -> None:
    """Draw a real color-grid PNG from a parsed ANSI grid (a list of rows of
    `(char, fg_hex, bg_hex)` cells — see `artgen_render.parse_ansi_grid`).

    Color resolution mirrors `artgen_render.ansi_to_html` exactly: a space
    character uses the cell's background color (the legacy bg+space pixel
    format); any other character uses the foreground color (the current
    fg+block format). An unset channel defaults to black.
    """
    from PIL import Image, ImageDraw
    w, h = 320, 240
    img = Image.new("RGB", (w, h), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)

    num_rows = len(grid)
    num_cols = max((len(row) for row in grid), default=1)
    if num_rows == 0 or num_cols == 0:
        img.save(str(dst))
        return

    cell_w = w / num_cols
    cell_h = h / num_rows
    for row_i, row in enumerate(grid):
        for col_i, (ch, fg, bg) in enumerate(row):
            color = bg if ch == " " else fg
            if color is None:
                color = _ANSI_GRID_DEFAULT_HEX
            x0 = col_i * cell_w
            y0 = row_i * cell_h
            draw.rectangle([x0, y0, x0 + cell_w + 0.5, y0 + cell_h + 0.5], fill=color)

    img.save(str(dst))


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
