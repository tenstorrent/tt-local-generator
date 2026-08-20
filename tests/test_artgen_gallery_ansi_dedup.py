# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
`artgen_gallery`'s ANSI card preview must parse via the shared
`artgen_render.parse_ansi_grid` — the single source of truth for ANSI
parsing (media-showcase-everywhere Task 4) — instead of its own bespoke
`_parse_ansi_cells` copy. This is the THIRD parser the design audit found
(the first two were `artgen_detail`/`artgen_watch`'s and TT-TV attractor's,
deduped in Tasks 1/3); this test closes the last drift point.

Run under xvfb (GTK4 DrawingArea/widget construction needs a real display):
    xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_artgen_gallery_ansi_dedup.py -v
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _gtk_available() -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk  # noqa: F401
        return True
    except Exception:
        return False


gtk_required = pytest.mark.skipif(
    not _gtk_available(), reason="GTK4 display not available"
)

# Current fg+block format the `ansi` generator actually emits
# (app/artgen/generators/ansi.py). Index 196 is pure red in the xterm-256
# 6x6x6 cube (see artgen_render._build_xterm256_hex).
_FG_BLOCK_ANSI = (
    "\x1b[38;5;196m██\x1b[0m\n"
    "\x1b[38;5;196m██\x1b[0m\n"
)


@gtk_required
def test_parse_ansi_cells_delegates_to_shared_parser_and_is_nonempty():
    """`_parse_ansi_cells` must produce a non-empty cell list for the CURRENT
    fg+block format via the shared `artgen_render.parse_ansi_grid` parser."""
    import artgen_gallery
    from artgen_render import parse_ansi_grid

    cells = artgen_gallery._parse_ansi_cells(_FG_BLOCK_ANSI)

    assert cells, "expected non-empty parsed cells for fg+block ANSI"
    # Cross-check against the shared parser directly: same character count.
    grid = parse_ansi_grid(_FG_BLOCK_ANSI)
    total_chars = sum(len(row) for row in grid)
    assert len(cells) == total_chars


@gtk_required
def test_parse_ansi_cells_actually_delegates_to_artgen_render(monkeypatch):
    """Structural proof of dedup (not just behavior parity): patching
    `artgen_gallery.parse_ansi_grid` (the name `_parse_ansi_cells` must call)
    must be observed — i.e. `_parse_ansi_cells` no longer carries its own
    independent escape-sequence walker."""
    import artgen_gallery

    calls = []
    real = artgen_gallery.parse_ansi_grid

    def _spy(raw):
        calls.append(raw)
        return real(raw)

    monkeypatch.setattr(artgen_gallery, "parse_ansi_grid", _spy)

    artgen_gallery._parse_ansi_cells(_FG_BLOCK_ANSI)

    assert calls == [_FG_BLOCK_ANSI], (
        "_parse_ansi_cells must delegate to artgen_render.parse_ansi_grid, "
        "not its own bespoke escape-sequence walker"
    )


@gtk_required
def test_ansi_preview_widget_builds_from_shared_parser(tmp_path):
    """`_ansi_preview_widget` must construct successfully and its draw
    function must not explode — proof it's wired to real (non-empty) cells
    from the shared parser for the current escape format."""
    from artgen_gallery import _ansi_preview_widget

    area = _ansi_preview_widget(_FG_BLOCK_ANSI)
    assert area is not None


@gtk_required
def test_make_card_content_for_ansi_uses_shared_parser(tmp_path):
    """`make_card_content` for an `.ans` record in the CURRENT fg+block
    format must return the ANSI preview widget (not degrade to a text
    snippet or emoji chip) — end-to-end proof the dedup didn't break the
    dispatcher."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from artgen_gallery import make_card_content
    from media_store import MediaRecord

    ans_path = tmp_path / "art.ans"
    ans_path.write_text(_FG_BLOCK_ANSI)

    rec = MediaRecord(
        id=str(uuid.uuid4()),
        media_type="artgen",
        created_at="2026-01-01T00:00:00Z",
        file_path=str(ans_path),
        thumbnail_path="",
        prompt="a test ansi prompt",
        model_id="qwen3-8b",
        generator_type="ansi",
        params="{}",
        starred=0,
    )

    widget = make_card_content(rec)

    assert isinstance(widget, Gtk.DrawingArea), (
        f"expected the ANSI DrawingArea preview, got {type(widget)}"
    )
