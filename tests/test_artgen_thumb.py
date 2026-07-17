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
