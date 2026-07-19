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


def _make_multiframe_gif_widget(tmp_path):
    """Shared setup for the set_playing/toggle_playing tests below --
    same skip guards as test_animated_gif_widget_advances_frames (PIL,
    display, GTK4 all required to build a real multi-frame AnimatedGifWidget).
    Returns None if any prerequisite is missing (caller should pytest.skip)."""
    try:
        from PIL import Image
    except ImportError:
        return None
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk  # noqa: F401
    except Exception:
        return None

    gif_path = tmp_path / "test2.gif"
    frame1 = Image.new("RGB", (4, 4), (255, 0, 0))
    frame2 = Image.new("RGB", (4, 4), (0, 255, 0))
    frame1.save(gif_path, save_all=True, append_images=[frame2], duration=50, loop=0)

    from artgen_render import AnimatedGifWidget
    return AnimatedGifWidget(str(gif_path))


# ── AnimatedGifWidget.set_playing / toggle_playing (gif-hygiene fix 2) ──────
#
# The fullscreen viewer's Pause button / Space key used to no-op for the GIF
# branch entirely (AnimatedGifWidget has no media stream to pause). These
# give it a real pause/resume API so VideoPlayerWindow can drive it.

def test_animated_gif_widget_set_playing_false_cancels_timer(tmp_path):
    widget = _make_multiframe_gif_widget(tmp_path)
    if widget is None:
        pytest.skip("PIL/display/GTK4 not available")
    try:
        assert widget._timer_id is not None
        assert widget._playing is True

        widget.set_playing(False)

        assert widget._timer_id is None
        assert widget._playing is False
    finally:
        widget._on_unrealize(widget)


def test_animated_gif_widget_set_playing_true_reschedules_timer(tmp_path):
    widget = _make_multiframe_gif_widget(tmp_path)
    if widget is None:
        pytest.skip("PIL/display/GTK4 not available")
    try:
        widget.set_playing(False)
        assert widget._timer_id is None

        widget.set_playing(True)

        assert widget._timer_id is not None
        assert widget._playing is True
    finally:
        widget._on_unrealize(widget)


def test_animated_gif_widget_set_playing_true_is_noop_when_already_playing(tmp_path):
    """Calling set_playing(True) while already playing must not schedule a
    second competing timer (which would double the frame-advance rate)."""
    widget = _make_multiframe_gif_widget(tmp_path)
    if widget is None:
        pytest.skip("PIL/display/GTK4 not available")
    try:
        first_timer_id = widget._timer_id
        widget.set_playing(True)
        assert widget._timer_id == first_timer_id
    finally:
        widget._on_unrealize(widget)


def test_animated_gif_widget_toggle_playing_flips_state(tmp_path):
    widget = _make_multiframe_gif_widget(tmp_path)
    if widget is None:
        pytest.skip("PIL/display/GTK4 not available")
    try:
        assert widget._playing is True

        result = widget.toggle_playing()
        assert result is False
        assert widget._playing is False
        assert widget._timer_id is None

        result = widget.toggle_playing()
        assert result is True
        assert widget._playing is True
        assert widget._timer_id is not None
    finally:
        widget._on_unrealize(widget)


def test_animated_gif_widget_on_unrealize_still_clears_playing_state(tmp_path):
    """`_on_unrealize` must remain a clean cancel regardless of `_playing`."""
    widget = _make_multiframe_gif_widget(tmp_path)
    if widget is None:
        pytest.skip("PIL/display/GTK4 not available")
    widget._on_unrealize(widget)
    assert widget._timer_id is None
    assert widget._iter is None


# ═════════════════════════════════════════════════════════════════════════
# "unify gallery interaction" Task 5 — shared ext -> renderer dispatch
# ═════════════════════════════════════════════════════════════════════════
#
# resolve_render_kind / build_reading_html are pure. render_artifact_widget
# builds real GTK widgets, so those tests self-skip without a display (same
# pattern as the AnimatedGifWidget test above).

def _display_available() -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        Gtk.Entry()  # probe: raises without a usable display
        return True
    except Exception:
        return False


display_required = pytest.mark.skipif(
    not _display_available(), reason="no GTK4 display available"
)


class _Rec:
    """Minimal duck-typed MediaRecord stand-in — render_artifact_widget only
    reads .file_path / .generator_type / .params_dict."""

    def __init__(self, file_path, generator_type="", params=None, prompt=""):
        self.file_path = file_path
        self.generator_type = generator_type
        self.params = params or {}
        self.prompt = prompt

    @property
    def params_dict(self):
        return self.params


# ── resolve_render_kind: pure ext -> kind mapping ─────────────────────────────

def test_resolve_render_kind_maps_every_known_ext():
    from artgen_render import resolve_render_kind

    assert resolve_render_kind(".gif") == "gif"
    assert resolve_render_kind(".svg") == "svg"
    assert resolve_render_kind(".ans") == "ansi"
    assert resolve_render_kind(".json") == "json"
    assert resolve_render_kind(".py") == "code"


def test_resolve_render_kind_defaults_to_text():
    from artgen_render import resolve_render_kind

    assert resolve_render_kind(".txt") == "text"
    assert resolve_render_kind(".md") == "text"
    assert resolve_render_kind("") == "text"
    assert resolve_render_kind(".weird") == "text"


# ── build_reading_html: shared html-builder dispatch ──────────────────────────

def test_build_reading_html_ansi_matches_ansi_to_html():
    from artgen_render import build_reading_html, ansi_to_html

    raw = "\x1b[38;5;196m█\x1b[38;5;46m█"
    assert build_reading_html("ansi", raw) == ansi_to_html(raw)


def test_build_reading_html_json_with_colors_matches_palette_to_html():
    import json as _json
    from artgen_render import build_reading_html, palette_to_html

    data = {"name": "P", "colors": [{"hex": "#111111", "role": "bg"}]}
    raw = _json.dumps(data)
    assert build_reading_html("json", raw) == palette_to_html(data)


def test_build_reading_html_json_without_colors_falls_back_to_markdown():
    import json as _json
    from artgen_render import build_reading_html, md_to_html, derive_title

    raw = _json.dumps({"foo": "bar"})
    title = derive_title("freeform", {"freeform": raw})
    html = build_reading_html("json", raw, gen_type="freeform", params={"freeform": raw})
    assert html == md_to_html(raw, title=title)
    assert 'class="swatch' not in html


def test_build_reading_html_json_invalid_falls_back_to_markdown():
    from artgen_render import build_reading_html

    html = build_reading_html("json", "not valid json{{{")
    assert "not valid json" in html
    assert 'class="swatch' not in html


def test_build_reading_html_code_matches_code_to_html():
    from artgen_render import build_reading_html, code_to_html, derive_title

    code = "def f():\n    return 1\n"
    title = derive_title("codeart", {})
    assert build_reading_html("code", code, gen_type="codeart") == code_to_html(code, title=title)
    # indentation must survive -- this is the whole point of the code branch
    assert "    return 1" in build_reading_html("code", code, gen_type="codeart")


def test_build_reading_html_text_applies_verse_mode_from_gen_type():
    from artgen_render import build_reading_html, md_to_html, derive_title

    text = "line one\nline two"
    title = derive_title("verse", {})
    html = build_reading_html("text", text, gen_type="verse")
    assert html == md_to_html(text, title=title, verse_mode=True)
    assert "text-align: center" in html  # verse CSS layered in


def test_build_reading_html_text_no_verse_mode_for_other_gen_types():
    from artgen_render import build_reading_html

    html = build_reading_html("text", "plain prose", gen_type="freeform")
    assert "text-align: center" not in html


# ── build_reading_webview: realize-deferral + WebKit-unavailable fallback ────

@display_required
def test_build_reading_webview_returns_webview_or_textview_fallback():
    from artgen_render import build_reading_webview, _WEBKIT_OK

    widget = build_reading_webview("<html><body><p>hello</p></body></html>")
    if _WEBKIT_OK:
        import gi
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit
        assert isinstance(widget, WebKit.WebView)
    else:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        assert isinstance(widget, Gtk.TextView)
        buf = widget.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        assert "hello" in text


# ── render_artifact_widget: the full ext -> widget dispatch ───────────────────

@display_required
def test_render_artifact_widget_gif_returns_animated_gif_widget(tmp_path):
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("PIL not available")

    from artgen_render import render_artifact_widget, AnimatedGifWidget

    gif_path = tmp_path / "a.gif"
    frame1 = Image.new("RGB", (4, 4), (255, 0, 0))
    frame2 = Image.new("RGB", (4, 4), (0, 255, 0))
    frame1.save(gif_path, save_all=True, append_images=[frame2], duration=50, loop=0)

    widget = render_artifact_widget(_Rec(str(gif_path)))
    try:
        assert isinstance(widget, AnimatedGifWidget)
    finally:
        widget._on_unrealize(widget)


@display_required
def test_render_artifact_widget_svg_returns_gtk_picture(tmp_path):
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    from artgen_render import render_artifact_widget

    svg_path = tmp_path / "a.svg"
    svg_path.write_text("<svg xmlns='http://www.w3.org/2000/svg' width='4' height='4'></svg>")

    widget = render_artifact_widget(_Rec(str(svg_path)))
    assert isinstance(widget, Gtk.Picture)
    assert widget.get_content_fit() == Gtk.ContentFit.CONTAIN


def _assert_webview_or_fallback(widget):
    from artgen_render import _WEBKIT_OK
    import gi
    if _WEBKIT_OK:
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit
        assert isinstance(widget, WebKit.WebView)
    else:
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        assert isinstance(widget, Gtk.TextView)


@display_required
def test_render_artifact_widget_ansi_returns_webview_or_fallback(tmp_path):
    from artgen_render import render_artifact_widget

    ans_path = tmp_path / "a.ans"
    ans_path.write_text("\x1b[38;5;196m█\x1b[38;5;46m█\n")

    widget = render_artifact_widget(_Rec(str(ans_path), generator_type="ansi"))
    _assert_webview_or_fallback(widget)


@display_required
def test_render_artifact_widget_json_palette_returns_webview_or_fallback(tmp_path):
    import json as _json
    from artgen_render import render_artifact_widget

    json_path = tmp_path / "p.json"
    json_path.write_text(_json.dumps({
        "name": "Test",
        "colors": [{"hex": "#abcdef", "role": "bg"}],
    }))

    widget = render_artifact_widget(_Rec(str(json_path), generator_type="palette"))
    _assert_webview_or_fallback(widget)


@display_required
def test_render_artifact_widget_txt_returns_webview_or_fallback(tmp_path):
    from artgen_render import render_artifact_widget

    txt_path = tmp_path / "verse.txt"
    txt_path.write_text("a lonely line\nof verse")

    widget = render_artifact_widget(_Rec(str(txt_path), generator_type="verse"))
    _assert_webview_or_fallback(widget)


@display_required
def test_render_artifact_widget_py_returns_webview_or_fallback(tmp_path):
    from artgen_render import render_artifact_widget

    py_path = tmp_path / "art.py"
    py_path.write_text("def f():\n    return 1\n")

    widget = render_artifact_widget(_Rec(str(py_path), generator_type="codeart"))
    _assert_webview_or_fallback(widget)


@display_required
def test_render_artifact_widget_missing_file_degrades_gracefully(tmp_path):
    """A record pointing at a nonexistent path must never raise -- it falls
    through to the text/reading branch with empty content."""
    from artgen_render import render_artifact_widget

    missing = tmp_path / "gone.txt"  # never written
    widget = render_artifact_widget(_Rec(str(missing), generator_type="freeform"))
    _assert_webview_or_fallback(widget)
