#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for app/artgen_viewer.py — "unify gallery interaction" Task 5:
`ArtgenViewerWindow`, a net-new full-screen viewer for artgen media
(svg/gif/ansi/palette/verse/markdown/codeart), mirroring
`main_window.VideoPlayerWindow`/`ImageViewerWindow`'s shape.

Creating GTK widgets needs a display; the full suite runs under xvfb. This
module self-skips when no display is available (matches the repo's
headless-fallback pattern for GTK-widget tests).

Run under xvfb:
    xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_artgen_viewer.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)


# ── MediaRecord helper (mirrors tests/test_artgen_pipeline_bridge.py) ────────

def _media_record(tmp_path, filename="lore.txt", content="Once upon a time...",
                   generator_type="lore", thumbnail_path="", media_id="mr-1",
                   prompt="a lore prompt"):
    """Build a real MediaRecord backed by a real file on disk (or no file at
    all when content is None)."""
    from media_store import MediaRecord

    p = tmp_path / filename
    if content is not None:
        p.write_text(content, encoding="utf-8")
    return MediaRecord(
        id=media_id,
        media_type="artgen",
        created_at="2026-07-01T00:00:00Z",
        file_path=str(p),
        thumbnail_path=thumbnail_path,
        prompt=prompt,
        model_id="artgen-qwen3-8b",
        generator_type=generator_type,
        params="{}",
        starred=0,
    )


def _make_gif(path: Path) -> None:
    from PIL import Image
    frame1 = Image.new("RGB", (8, 8), (255, 0, 0))
    frame2 = Image.new("RGB", (8, 8), (0, 255, 0))
    frame1.save(path, save_all=True, append_images=[frame2], duration=50, loop=0)


def _assert_webview_or_fallback(widget):
    from artgen_render import _WEBKIT_OK
    if _WEBKIT_OK:
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit
        assert isinstance(widget, WebKit.WebView)
    else:
        assert isinstance(widget, Gtk.TextView)


# ── Construction: no exception, body widget type matches kind ───────────────

def test_construct_for_gif(tmp_path):
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        pytest.skip("PIL not available")
    from artgen_viewer import ArtgenViewerWindow
    from artgen_render import AnimatedGifWidget

    gif_path = tmp_path / "a.gif"
    _make_gif(gif_path)
    rec = _media_record(tmp_path, filename="a.gif", content=None, generator_type="artgen")
    rec.file_path = str(gif_path)

    win = ArtgenViewerWindow(rec, None)
    try:
        assert isinstance(win._body, AnimatedGifWidget)
    finally:
        # AnimatedGifWidget starts its GLib timer unconditionally at
        # construction (not tied to realize) and only cancels it on its own
        # "unrealize" -- this window is never presented/realized in this
        # test, so nothing will fire that signal. Cancel it manually (same
        # pattern test_artgen_render.py uses for the same reason) so the
        # repeating timeout doesn't keep firing for the rest of the suite.
        win._body._on_unrealize(win._body)


def test_construct_for_svg(tmp_path):
    from artgen_viewer import ArtgenViewerWindow

    rec = _media_record(
        tmp_path, filename="a.svg",
        content="<svg xmlns='http://www.w3.org/2000/svg' width='4' height='4'></svg>",
        generator_type="svgart",
    )

    win = ArtgenViewerWindow(rec, None)
    try:
        assert isinstance(win._body, Gtk.Picture)
    finally:
        win.destroy()


def test_construct_for_ansi(tmp_path):
    from artgen_viewer import ArtgenViewerWindow

    rec = _media_record(
        tmp_path, filename="a.ans",
        content="\x1b[38;5;196m█\x1b[38;5;46m█\n",
        generator_type="ansi",
    )

    win = ArtgenViewerWindow(rec, None)
    try:
        _assert_webview_or_fallback(win._body)
    finally:
        win.destroy()


def test_construct_for_palette_json(tmp_path):
    import json
    from artgen_viewer import ArtgenViewerWindow

    data = {"name": "Test", "colors": [{"hex": "#abcdef", "role": "bg"}]}
    rec = _media_record(
        tmp_path, filename="p.json", content=json.dumps(data), generator_type="palette",
    )

    win = ArtgenViewerWindow(rec, None)
    try:
        _assert_webview_or_fallback(win._body)
    finally:
        win.destroy()


def test_construct_for_verse_txt(tmp_path):
    from artgen_viewer import ArtgenViewerWindow

    rec = _media_record(
        tmp_path, filename="verse.txt", content="a lonely line\nof verse",
        generator_type="verse", prompt="",
    )

    win = ArtgenViewerWindow(rec, None)
    try:
        _assert_webview_or_fallback(win._body)
    finally:
        win.destroy()


def test_construct_for_codeart_py(tmp_path):
    from artgen_viewer import ArtgenViewerWindow

    rec = _media_record(
        tmp_path, filename="art.py", content="def f():\n    return 1\n",
        generator_type="codeart",
    )

    win = ArtgenViewerWindow(rec, None)
    try:
        _assert_webview_or_fallback(win._body)
    finally:
        win.destroy()


# ── Title ─────────────────────────────────────────────────────────────────────

def test_title_uses_prompt_snippet_when_present(tmp_path):
    from artgen_viewer import ArtgenViewerWindow

    rec = _media_record(tmp_path, prompt="a short prompt")
    win = ArtgenViewerWindow(rec, None)
    try:
        assert win.get_title() == "a short prompt"
    finally:
        win.destroy()


def test_title_truncates_long_prompt(tmp_path):
    from artgen_viewer import ArtgenViewerWindow

    long_prompt = "x" * 100
    rec = _media_record(tmp_path, prompt=long_prompt)
    win = ArtgenViewerWindow(rec, None)
    try:
        title = win.get_title()
        assert len(title) == 61  # 60 chars + ellipsis
        assert title.endswith("…")
    finally:
        win.destroy()


def test_title_falls_back_to_derived_title_when_no_prompt(tmp_path):
    from artgen_viewer import ArtgenViewerWindow

    rec = _media_record(
        tmp_path, filename="verse.txt", content="verse text",
        generator_type="verse", prompt="",
    )
    rec.params = '{"form": "haiku", "theme": "autumn"}'

    win = ArtgenViewerWindow(rec, None)
    try:
        assert win.get_title() == "Haiku — autumn"
    finally:
        win.destroy()


# ── Keyboard: Escape closes, F toggles fullscreen ─────────────────────────────

def test_escape_key_closes_window(tmp_path):
    from artgen_viewer import ArtgenViewerWindow

    rec = _media_record(tmp_path)
    win = ArtgenViewerWindow(rec, None)
    try:
        handled = win._on_key(None, 0xFF1B, 0, 0)  # Gdk.KEY_Escape
        assert handled is True
    finally:
        win.destroy()


def test_f_key_toggles_fullscreen(tmp_path, monkeypatch):
    from artgen_viewer import ArtgenViewerWindow

    rec = _media_record(tmp_path)
    win = ArtgenViewerWindow(rec, None)
    try:
        calls = []
        monkeypatch.setattr(win, "_toggle_fullscreen", lambda: calls.append(True))

        handled_lower = win._on_key(None, 0x66, 0, 0)  # 'f'
        handled_upper = win._on_key(None, 0x46, 0, 0)  # 'F'

        assert handled_lower is True
        assert handled_upper is True
        assert calls == [True, True]
    finally:
        win.destroy()


def test_unrelated_key_not_handled(tmp_path):
    from artgen_viewer import ArtgenViewerWindow

    rec = _media_record(tmp_path)
    win = ArtgenViewerWindow(rec, None)
    try:
        handled = win._on_key(None, 0x41, 0, 0)  # 'A' -- not bound to anything
        assert handled is False
    finally:
        win.destroy()


# ── Control strip: Fullscreen/Close buttons exist and are wired ──────────────

def test_control_strip_has_fullscreen_and_close_buttons(tmp_path):
    from artgen_viewer import ArtgenViewerWindow

    rec = _media_record(tmp_path)
    win = ArtgenViewerWindow(rec, None)
    try:
        labels = []

        def _walk(widget):
            if isinstance(widget, Gtk.Button):
                labels.append(widget.get_label())
            child = widget.get_first_child()
            while child is not None:
                _walk(child)
                child = child.get_next_sibling()

        _walk(win.get_child())
        assert any("Fullscreen" in (l or "") for l in labels)
        assert any("Close" in (l or "") for l in labels)
    finally:
        win.destroy()


# ── Unrealize cleanup: gif timer is cancelled ─────────────────────────────────
#
# NOTE: `Gtk.Window.destroy()`/`.close()` on a window that was never
# `.present()`-ed does NOT synchronously fire "unrealize" or "destroy" in
# PyGObject (verified interactively: "destroy" only fires once the Python
# wrapper is garbage-collected, and "unrealize" only fires if the window was
# ever actually realized) -- so this test invokes the window's own
# `_on_unrealize` handler directly rather than depending on that timing.

def test_on_unrealize_cancels_gif_timer_if_still_present(tmp_path):
    """AnimatedGifWidget cancels its own timer on its own "unrealize", so by
    the time the WINDOW's unrealize handler runs, `_body._timer_id` is
    normally already None -- this test forces the belt-and-suspenders branch
    by re-populating `_timer_id` right before invoking the handler, proving
    `_on_unrealize` actually removes the GLib source rather than just
    clearing the attribute blindly."""
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        pytest.skip("PIL not available")
    from artgen_viewer import ArtgenViewerWindow
    from gi.repository import GLib

    gif_path = tmp_path / "a.gif"
    _make_gif(gif_path)
    rec = _media_record(tmp_path, filename="a.gif", content=None, generator_type="artgen")
    rec.file_path = str(gif_path)

    win = ArtgenViewerWindow(rec, None)
    body = win._body
    # Cancel the REAL timer AnimatedGifWidget started at construction first
    # (this window is never realized in this test, so nothing else will) --
    # then force a fake one in to exercise the handler under test.
    body._on_unrealize(body)
    fake_timer_id = GLib.timeout_add(60_000, lambda: True)
    body._timer_id = fake_timer_id

    win._on_unrealize(win)

    assert GLib.MainContext.default().find_source_by_id(fake_timer_id) is None
    assert win._body is None
