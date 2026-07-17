#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Tests for app/artgen_render.py — the shared artgen rendering module.

Most of these are pure (str/dict -> str) and need no display. The
AnimatedGifWidget test needs a real GTK4 display (xvfb in CI); it
self-skips when PIL, a display, or GTK4 aren't available.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


# ── parse_ansi_grid: the single parser, both historical escape formats ───────

def test_parse_ansi_grid_fg_block_format():
    """Current generator format: \\x1b[38;5;Nm█ (foreground + block char)."""
    from artgen_render import parse_ansi_grid

    raw = "\x1b[38;5;196m█\x1b[38;5;46m█"
    grid = parse_ansi_grid(raw)

    assert grid, "expected non-empty rows for fg+block format"
    row = grid[0]
    assert len(row) == 2

    ch0, fg0, bg0 = row[0]
    ch1, fg1, bg1 = row[1]
    assert ch0 == "█" and ch1 == "█"
    # xterm-256 index 196 -> pure red; 46 -> pure green.
    assert fg0.lower() == "#ff0000"
    assert fg1.lower() == "#00ff00"
    assert bg0 is None and bg1 is None


def test_parse_ansi_grid_bg_space_format():
    """Legacy generator format: \\x1b[48;5;Nm  (background + space)."""
    from artgen_render import parse_ansi_grid

    raw = "\x1b[48;5;21m \x1b[48;5;226m "
    grid = parse_ansi_grid(raw)

    assert grid, "expected non-empty rows for bg+space format"
    row = grid[0]
    assert len(row) == 2

    ch0, fg0, bg0 = row[0]
    ch1, fg1, bg1 = row[1]
    assert ch0 == " " and ch1 == " "
    # xterm-256 index 21 -> pure blue; 226 -> pure yellow.
    assert bg0.lower() == "#0000ff"
    assert bg1.lower() == "#ffff00"
    assert fg0 is None and fg1 is None


def test_parse_ansi_grid_reset_clears_state():
    from artgen_render import parse_ansi_grid

    raw = "\x1b[38;5;196m█\x1b[0m█"
    grid = parse_ansi_grid(raw)
    row = grid[0]
    assert row[0][1].lower() == "#ff0000"
    assert row[1][1] is None  # reset before the second block char


# ── ansi_to_html builds on parse_ansi_grid ────────────────────────────────────

def test_ansi_to_html_renders_colored_cells_not_raw_escapes():
    from artgen_render import ansi_to_html

    raw = "\x1b[38;5;196m█\x1b[38;5;46m█"
    html = ansi_to_html(raw)

    assert "\x1b" not in html
    assert "\\033" not in html
    assert "\\x1b" not in html
    assert "background:#ff0000" in html.lower()
    assert "background:#00ff00" in html.lower()


def test_ansi_to_html_handles_legacy_bg_space_format():
    from artgen_render import ansi_to_html

    raw = "\x1b[48;5;21m \x1b[48;5;226m "
    html = ansi_to_html(raw)

    assert "\x1b" not in html
    assert "background:#0000ff" in html.lower()
    assert "background:#ffff00" in html.lower()


# ── palette_to_html ────────────────────────────────────────────────────────────

def test_palette_to_html_one_swatch_per_color():
    from artgen_render import palette_to_html

    data = {
        "name": "Test Palette",
        "lore": "a test",
        "colors": [
            {"hex": "#111111", "role": "bg"},
            {"hex": "#222222", "role": "fg"},
            {"hex": "#333333", "role": "accent"},
        ],
    }
    html = palette_to_html(data)
    assert html.count('class="swatch-block"') == 3
    assert "#111111" in html and "#222222" in html and "#333333" in html


# ── md_to_html verse mode ──────────────────────────────────────────────────────

def test_md_to_html_verse_mode_differs_from_prose():
    from artgen_render import md_to_html

    text = "Some lines\nof verse\nhere."
    prose_html = md_to_html(text, verse_mode=False)
    verse_html = md_to_html(text, verse_mode=True)
    assert prose_html != verse_html
    # verse mode layers the verse-specific CSS extra onto the document
    assert "text-align: center" in verse_html
    assert "text-align: center" not in prose_html


# ── code_to_html preserves indentation ─────────────────────────────────────────

def test_code_to_html_preserves_indentation():
    from artgen_render import code_to_html

    code = "def f():\n    return 1\n"
    html = code_to_html(code)
    assert "<pre" in html
    # the 4-space indent must survive HTML-escaping, unlike the prose/nl2br
    # markdown pipeline (which would collapse or reflow it).
    assert "    return 1" in html


def test_code_to_html_escapes_html_special_chars():
    from artgen_render import code_to_html

    code = "if a < b and c > d:\n    pass\n"
    html = code_to_html(code)
    assert "<pre" in html
    assert "a &lt; b" in html
    assert "c &gt; d" in html


# ── derive_title / luminance (moved verbatim) ─────────────────────────────────

def test_derive_title_verse():
    from artgen_render import derive_title

    assert derive_title("verse", {"form": "haiku", "theme": "autumn"}) == "Haiku — autumn"


def test_luminance_black_and_white():
    from artgen_render import luminance

    assert luminance("#000000") == pytest.approx(0.0)
    assert luminance("#ffffff") == pytest.approx(1.0)


# ── AnimatedGifWidget ──────────────────────────────────────────────────────────

def test_animated_gif_widget_advances_frames(tmp_path):
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("PIL not available")

    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        pytest.skip("no GTK4 display available")
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk  # noqa: F401
    except Exception:
        pytest.skip("GTK4 not available")

    gif_path = tmp_path / "test.gif"
    frame1 = Image.new("RGB", (4, 4), (255, 0, 0))
    frame2 = Image.new("RGB", (4, 4), (0, 255, 0))
    frame1.save(gif_path, save_all=True, append_images=[frame2], duration=50, loop=0)

    from artgen_render import AnimatedGifWidget

    widget = AnimatedGifWidget(str(gif_path))
    try:
        assert widget._timer_id is not None, (
            "expected a running GLib timer id for a multi-frame gif"
        )
        assert widget._iter is not None
    finally:
        widget._on_unrealize(widget)
        assert widget._timer_id is None
