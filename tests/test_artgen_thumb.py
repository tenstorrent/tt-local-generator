# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
`artgen_thumb.make_thumbnail` — regression tests for the gif-renders-as-text
bug (see CLAUDE.md "Root cause" note).

Before the fix, every extension other than `.svg` fell into a "Text / ANSI"
branch that read the file's RAW BYTES as UTF-8 text and drew them onto a PNG
with PIL. For a binary raster file (AnimateDiff's `.gif` output, but also any
`.png`/`.jpg`/etc.) this produced a "thumbnail" that was a PIL text-render of
binary garbage — exactly what the user saw as "the preview ... trying to
display it as text somehow".

These tests pin down the corrected behavior:
  - `.gif` (and other raster extensions) get a REAL PIL-rendered thumbnail of
    the image content, not a text dump of its bytes.
  - `.txt` (and the other declared TEXT extensions) still text-render, same
    as before.
  - An unrecognised/binary extension (e.g. `.bin`) never falls into the text
    branch — it gets the placeholder PNG instead.

Skips gracefully if PIL isn't importable (the module already depends on it
for this code path, but the test env may lack it).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

PIL = pytest.importorskip("PIL", reason="artgen_thumb's raster/text branches need PIL")
from PIL import Image  # noqa: E402

from artgen_thumb import make_thumbnail  # noqa: E402


def _make_multiframe_gif(path: Path) -> None:
    """Write a tiny real 2-frame animated GIF using PIL."""
    frame1 = Image.new("RGB", (64, 48), color=(255, 0, 0))
    frame2 = Image.new("RGB", (64, 48), color=(0, 255, 0))
    frame1.save(path, format="GIF", save_all=True, append_images=[frame2], duration=100, loop=0)


def test_gif_produces_real_raster_thumbnail_not_text_render(tmp_path):
    gif_path = tmp_path / "anim.gif"
    _make_multiframe_gif(gif_path)
    dst = tmp_path / "thumb.png"

    result = make_thumbnail(gif_path, dst)

    assert result.exists()
    # Proof it's a real raster thumbnail, not the old text-render: the source
    # gif frames are 64x48, well under the 320x240 thumbnail cap, so PIL's
    # `Image.thumbnail()` (which only ever shrinks, never upscales) must
    # preserve that exact size. The old text-render branch instead drew onto
    # a hardcoded 320x120 canvas regardless of source content — a dead
    # giveaway that content was never actually decoded as an image.
    with Image.open(result) as out_img:
        out_img.load()
        assert out_img.mode in ("RGB", "RGBA")
        assert out_img.size == (64, 48)


def test_gif_thumbnail_is_first_frame_not_garbled(tmp_path):
    """The thumbnail should reflect the GIF's first frame (red), proving PIL
    actually decoded the image rather than falling through to any fallback
    that ignores frame content."""
    gif_path = tmp_path / "anim.gif"
    _make_multiframe_gif(gif_path)
    dst = tmp_path / "thumb.png"

    result = make_thumbnail(gif_path, dst)

    with Image.open(result) as out_img:
        out_img = out_img.convert("RGB")
        # Sample the center pixel — first frame was solid red.
        w, h = out_img.size
        r, g, b = out_img.getpixel((w // 2, h // 2))
        assert r > g and r > b


def test_txt_still_text_renders(tmp_path):
    """Existing behavior for the declared TEXT extensions must be unchanged."""
    txt_path = tmp_path / "verse.txt"
    txt_path.write_text("the forge\nsleeps\nin ash")
    dst = tmp_path / "thumb.png"

    result = make_thumbnail(txt_path, dst)

    assert result.exists()
    assert result == dst
    with Image.open(result) as out_img:
        # The text branch draws onto a fixed 320x120 canvas.
        assert out_img.size == (320, 120)


def test_unknown_binary_extension_yields_placeholder_not_text_render(tmp_path):
    """A `.bin` (or any extension outside the raster/text allow-lists) must
    NEVER be text-rendered from raw bytes — it should degrade to the
    placeholder PNG instead."""
    bin_path = tmp_path / "blob.bin"
    bin_path.write_bytes(bytes(range(256)) * 4)
    dst = tmp_path / "thumb.png"

    result = make_thumbnail(bin_path, dst)

    assert result.exists()
    with Image.open(result) as out_img:
        # The placeholder PNG (`_write_placeholder_png`) is a 1x1 grey pixel —
        # nothing like the 320x120 text-render canvas.
        assert out_img.size == (1, 1)


# ── Task 4 (media-showcase-everywhere): real thumbnails for palette/ansi/code ──
#
# Before this fix, `.json` (palette) and `.ans` (ANSI art) fell into the
# generic text-render branch and got their RAW bytes (JSON syntax / escape
# codes) drawn onto a PNG as if they were prose — "garbage baked into a PNG"
# per CLAUDE.md's media-showcase-everywhere note. `.py` (codeart) wasn't in
# _TEXT_EXTS at all, so it fell all the way through to the 1x1 grey
# placeholder even though its source is perfectly good monospace text.


def _find_red_pixel(img) -> bool:
    """True if the image contains at least one strongly-red pixel."""
    img = img.convert("RGB")
    w, h = img.size
    for x in range(0, w, 4):       # sample on a grid, not every pixel — speed
        for y in range(0, h, 4):
            r, g, b = img.getpixel((x, y))
            if r > 180 and g < 80 and b < 80:
                return True
    return False


def test_palette_json_produces_swatch_thumbnail_with_real_colors(tmp_path):
    """A palette `.json` (the real generator schema: colors is a list of
    {"hex": ..., "role": ...} dicts) must render an actual swatch grid PNG —
    proven by finding the swatch colors as real pixels, not JSON-syntax text
    rendered as glyphs."""
    import json as _json

    json_path = tmp_path / "palette.json"
    json_path.write_text(_json.dumps({
        "name": "Test Palette",
        "colors": [
            {"hex": "#ff0000", "role": "accent"},
            {"hex": "#00ff00", "role": "background"},
            {"hex": "#0000ff", "role": "shadow"},
        ],
        "lore": "not rendered here",
    }))
    dst = tmp_path / "thumb.png"

    result = make_thumbnail(json_path, dst)

    assert result.exists()
    with Image.open(result) as out_img:
        out_img.load()
        assert out_img.size[0] > 1 and out_img.size[1] > 1  # not the 1x1 placeholder
        assert _find_red_pixel(out_img), (
            "expected a real red swatch pixel — got a text-render or placeholder instead"
        )


def test_palette_json_without_colors_falls_back_to_text_render(tmp_path):
    """A `.json` that isn't a colors-palette (no "colors" key, or empty) must
    still fall through to the ordinary text render — .json isn't ALWAYS a
    palette."""
    json_path = tmp_path / "plain.json"
    json_path.write_text('{"not_a_palette": true}')
    dst = tmp_path / "thumb.png"

    result = make_thumbnail(json_path, dst)

    assert result.exists()
    with Image.open(result) as out_img:
        # Same fixed canvas the generic text branch has always used.
        assert out_img.size == (320, 120)


def test_ansi_fg_block_produces_color_grid_thumbnail(tmp_path):
    """An `.ans` artifact in the CURRENT fg+block format
    (`\\x1b[38;5;196m█`) must render an actual color-grid PNG via the shared
    `artgen_render.parse_ansi_grid` parser — not a text dump of the raw
    escape bytes."""
    ans_path = tmp_path / "art.ans"
    # xterm-256 index 196 is pure red (#ff0000) — see artgen_render's 6x6x6
    # color-cube derivation. \x1b[0m resets between rows.
    ans_content = (
        "\x1b[38;5;196m████\x1b[0m\n"
        "\x1b[38;5;196m████\x1b[0m\n"
    )
    ans_path.write_text(ans_content)
    dst = tmp_path / "thumb.png"

    result = make_thumbnail(ans_path, dst)

    assert result.exists()
    with Image.open(result) as out_img:
        out_img.load()
        assert out_img.size[0] > 1 and out_img.size[1] > 1  # not the 1x1 placeholder
        assert _find_red_pixel(out_img), (
            "expected a real red grid-cell pixel — got a text-render of the raw escapes instead"
        )


def test_ansi_empty_or_unparseable_falls_back_to_placeholder_not_text(tmp_path):
    """Malformed/empty ANSI must never fall back to raw-escape text-render —
    only the honest placeholder."""
    ans_path = tmp_path / "empty.ans"
    ans_path.write_text("")
    dst = tmp_path / "thumb.png"

    result = make_thumbnail(ans_path, dst)

    assert result.exists()
    with Image.open(result) as out_img:
        assert out_img.size == (1, 1)


def test_py_codeart_produces_monospace_thumbnail_not_placeholder(tmp_path):
    """`.py` (codeart) is genuine text and must get the monospace text-render
    thumbnail, not the grey 1x1 placeholder (the previous bug: `.py` wasn't
    in `_TEXT_EXTS` at all)."""
    py_path = tmp_path / "art.py"
    py_path.write_text("def make_art():\n    return '<svg></svg>'\n")
    dst = tmp_path / "thumb.png"

    result = make_thumbnail(py_path, dst)

    assert result.exists()
    with Image.open(result) as out_img:
        # Full text-render canvas size, not the 1x1 placeholder.
        assert out_img.size == (320, 120)


def test_md_still_text_renders(tmp_path):
    """`.md` behavior must be unchanged by this pass."""
    md_path = tmp_path / "verse.md"
    md_path.write_text("# Title\n\nSome verse content.\n")
    dst = tmp_path / "thumb.png"

    result = make_thumbnail(md_path, dst)

    assert result.exists()
    with Image.open(result) as out_img:
        assert out_img.size == (320, 120)


def test_svg_behavior_unchanged(tmp_path):
    """`.svg` still goes through the Rsvg/placeholder path (sanity spot-check
    that this pass didn't touch the svg branch)."""
    svg_path = tmp_path / "art.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
        '<rect width="100" height="100" fill="red"/></svg>'
    )
    dst = tmp_path / "thumb.png"

    result = make_thumbnail(svg_path, dst)

    assert result.exists()
