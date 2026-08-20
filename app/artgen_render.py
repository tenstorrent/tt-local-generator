#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
artgen_render — shared rendering logic for artgen media types.

This module is a LEAF: it may import gi/Gtk/GdkPixbuf/GLib and stdlib only.
It must NEVER import artgen_detail / artgen_watch / artgen_gallery /
create_view / attractor / main_window — any of those importing FROM here is
fine, the reverse is a cycle.

Extracted (v0.48.0, media-showcase-everywhere SP-1 / Task 1) from three
copies of the same rendering logic that had drifted apart:
`artgen_detail.py`, `artgen_watch.py` (which already re-imported the
builders from `artgen_detail`), and `artgen_gallery.py` (the animated-gif
widget). The drift is what caused the live bug where TT-TV (`attractor.py`)
rendered ANSI artifacts as raw escape-code gibberish: its bespoke parser only
understood the legacy `\x1b[48;5;Nm ` (background+space) format, but the
current `ansi` generator (`app/artgen/generators/ansi.py`) emits
`\x1b[38;5;Nm█` (foreground+block) — see `parse_ansi_grid` below, now the
SINGLE place that understands ANSI, so a parser can never drift from the
generator again.

Public API (no leading underscores — this is a shared module, not a private
implementation detail of one view):

    luminance(hex_color) -> float
    derive_title(gen_type, params) -> str
    ansi_to_html(raw) -> str
    palette_to_html(data) -> str
    md_to_html(raw, title="", verse_mode=False) -> str
    code_to_html(raw, title="") -> str
    parse_ansi_grid(raw) -> list[list[(char, fg_hex_or_None, bg_hex_or_None)]]
    class AnimatedGifWidget(Gtk.Picture)
    resolve_render_kind(ext) -> str
    build_reading_html(kind, raw, gen_type="", params=None) -> str
    build_reading_webview(html) -> Gtk.Widget
    render_artifact_widget(record) -> Gtk.Widget

`resolve_render_kind` / `build_reading_html` / `build_reading_webview` /
`render_artifact_widget` (v0.49.0, "unify gallery interaction" Task 5) are the
shared ext -> renderer DISPATCH extracted out of `ArtgenDetail._render` so a
net-new `ArtgenViewerWindow` (app/artgen_viewer.py) can reproduce it exactly
without a second hand-maintained copy of the ext->builder mapping.
`ArtgenDetail` keeps its own persistent `_art_stack`/`_gif_pic`/`_webview`
widgets (needed for cheap prev/next navigation) but now calls
`resolve_render_kind`/`build_reading_html` instead of an inline if/elif chain
that duplicated the decision. `render_artifact_widget` is the convenience
entry point for callers (like the viewer window) that just want a fresh,
disposable widget for one record and don't need to reuse widgets across a
list. NOTE: this is deliberately NOT unified with create_view.py's own
`_artifact_kind`/`_build_reading_webview` (which also considers
generator_type/media_type and image/video/unknown, a broader vocabulary) --
that's a known follow-up, out of scope for this task.

`parse_ansi_grid` return shape
-------------------------------
A list of rows; each row is a list of `(char, fg, bg)` tuples where:
  - `char` is the literal character that followed the escape sequence (a
    Unicode block char such as █▀▄▌▐░▒▓, a plain space, or any other
    printable character the generator happened to emit).
  - `fg` / `bg` are `"#RRGGBB"` strings, or `None` when that channel was
    never explicitly set for this cell (i.e. still at its default / most
    recent SGR-0 reset). Callers decide what "unset" should render as (this
    module's own `ansi_to_html` treats it as black; a future cairo/PIL
    renderer, Tasks 3/4, is free to choose differently).
This is deliberately un-opinionated about *which* channel wins when both are
set (they never both apply to the same escape run in practice — the
generator emits one or the other, never both, for a given cell) so both the
HTML renderer here and a future cairo/PIL renderer can consume it without
this module presuming how the pixel will ultimately be painted.
"""
from __future__ import annotations

import html as _html_mod
import json
import re
import time
from pathlib import Path
from typing import Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, Gio, GdkPixbuf, GLib, Gtk

# WebKit is optional at import time -- some environments (headless CI without
# the webkit2gtk-6.0 typelib) can't load it. `build_reading_webview` degrades
# to a plain Gtk.TextView in that case, mirroring artgen_watch's `_WEBKIT_OK`
# fallback pattern so this module never hard-fails on a missing optional dep.
try:
    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit as _WebKit
    _WEBKIT_OK = True
except Exception:  # pragma: no cover - environment-dependent
    _WEBKIT_OK = False


# ═════════════════════════════════════════════════════════════════════════
# xterm-256 color table (indices 0-255) — the ANSI color source of truth
# ═════════════════════════════════════════════════════════════════════════

def _build_xterm256_hex() -> list[str]:
    """Build the 256-entry xterm color table as "#RRGGBB" hex strings.

    0-15: the 16 standard/bright system colors.
    16-231: the 6x6x6 color cube.
    232-255: the 24-step grayscale ramp.
    """
    sys16 = [
        "#000000", "#AA0000", "#00AA00", "#AA5500",
        "#0000AA", "#AA00AA", "#00AAAA", "#AAAAAA",
        "#555555", "#FF5555", "#55FF55", "#FFFF55",
        "#5555FF", "#FF55FF", "#55FFFF", "#FFFFFF",
    ]
    pal: list[str] = list(sys16)
    for r6 in range(6):
        for g6 in range(6):
            for b6 in range(6):
                cv = lambda x: 0 if x == 0 else 55 + x * 40
                pal.append("#{:02x}{:02x}{:02x}".format(cv(r6), cv(g6), cv(b6)))
    for k in range(24):
        v = 8 + k * 10
        pal.append("#{:02x}{:02x}{:02x}".format(v, v, v))
    return pal


_XTERM256_HEX = _build_xterm256_hex()


def _truecolor_hex(r: int, g: int, b: int) -> str:
    clamp = lambda x: max(0, min(255, x))
    return "#{:02x}{:02x}{:02x}".format(clamp(r), clamp(g), clamp(b))


# ═════════════════════════════════════════════════════════════════════════
# Pure builders
# ═════════════════════════════════════════════════════════════════════════

def luminance(hex_color: str) -> float:
    """Approximate relative luminance of a hex color (0=black, 1=white)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return 0.5
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def derive_title(gen_type: str, params: dict) -> str:
    """Compute a human-readable document title from generator type + params."""
    if gen_type == "verse":
        form = params.get("form", "verse").capitalize()
        theme = params.get("theme", "")
        return f"{form} — {theme}" if theme else form
    if gen_type == "freeform":
        prompt = params.get("freeform", "")
        return (prompt[:72] + "…") if len(prompt) > 72 else prompt
    if gen_type == "ansi":
        return params.get("subject", "")
    return ""


# ── HTML document scaffolding shared by md_to_html / code_to_html ────────────

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><style>{css}</style></head>
<body><div class="content">{body}</div></body>
</html>"""

_READING_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html { background: #1A3C47; min-height: 100%; }
body {
    background: #1A3C47;
    font-family: system-ui, 'Fira Sans', 'Liberation Sans', 'Noto Sans', sans-serif;
    font-size: 18px;
    line-height: 1.82;
    color: #E8F0F2;
    -webkit-font-smoothing: antialiased;
    padding: 56px 24px 80px;
}
.content {
    max-width: 720px;
    margin: 0 auto;
}
h1 {
    font-size: 1.65em; font-weight: 700; color: #4FD1C5;
    letter-spacing: -0.01em;
    border-bottom: 1px solid rgba(79,209,197,0.25);
    padding-bottom: 12px; margin-bottom: 24px; margin-top: 0;
}
h2 { font-size: 1.25em; font-weight: 600; color: #81E6D9; margin-top: 36px; margin-bottom: 12px; }
h3 { font-size: 1.08em; font-weight: 600; color: #B0C4DE; margin-top: 28px; margin-bottom: 8px; }
h4 { font-size: 0.95em; font-weight: 600; color: #8EACC0; text-transform: uppercase;
     letter-spacing: 0.06em; margin-top: 24px; margin-bottom: 6px; }
p { margin-bottom: 18px; }
strong { font-weight: 700; color: #F0F7FA; }
em { font-style: italic; color: #EC96B8; }
a { color: #4FD1C5; text-decoration: underline; text-decoration-thickness: 1px; }
code {
    font-family: 'JetBrains Mono', 'Fira Code', 'Liberation Mono', monospace;
    font-size: 0.86em; background: #0F2A35; color: #4FD1C5;
    padding: 2px 7px; border-radius: 4px;
}
pre {
    background: #0F2A35; border-left: 3px solid #4FD1C5;
    padding: 18px 22px; border-radius: 0 6px 6px 0;
    overflow-x: auto; margin-bottom: 22px;
    white-space: pre-wrap; word-wrap: break-word;
}
pre code { background: none; padding: 0; color: #E8F0F2; font-size: 0.90em; line-height: 1.6; }
blockquote {
    border-left: 3px solid #4FD1C5; margin: 24px 0;
    padding: 6px 0 6px 24px; color: #B0C4DE; font-style: italic;
    font-size: 1.05em;
}
hr { border: none; border-top: 1px solid rgba(79,209,197,0.2); margin: 36px 0; }
ul, ol { padding-left: 28px; margin-bottom: 18px; }
li { margin-bottom: 7px; }
li > p { margin-bottom: 8px; }
table {
    width: 100%; border-collapse: collapse; margin-bottom: 22px;
    font-size: 0.93em;
}
th {
    background: #0F2A35; color: #4FD1C5; font-weight: 600;
    padding: 10px 14px; text-align: left; letter-spacing: 0.03em;
    border-bottom: 2px solid rgba(79,209,197,0.4);
}
td { padding: 9px 14px; border-bottom: 1px solid rgba(255,255,255,0.07); color: #D8E8EC; }
tr:hover td { background: rgba(79,209,197,0.05); }
"""

# Extra CSS layered on top of _READING_CSS for verse/haiku content.
_VERSE_CSS_EXTRA = """
.content { text-align: center; max-width: 560px; }
h1 { text-align: center; border-bottom: none; margin-bottom: 48px; }
p {
    font-size: 1.15em;
    line-height: 2.1;
    margin-bottom: 36px;
    font-style: italic;
    color: #C8DDE5;
}
"""

_PALETTE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: #0F2A35; }
body {
    font-family: system-ui, 'Fira Sans', 'Liberation Sans', sans-serif;
    font-size: 15px; line-height: 1.6; color: #E8F0F2;
    padding: 0 0 48px;
    -webkit-font-smoothing: antialiased;
}
.strip { display: flex; width: 100%; height: 80px; }
.strip-seg { flex: 1; }
.info { max-width: 680px; margin: 0 auto; padding: 32px 36px 0; }
h1 { font-size: 1.5em; font-weight: 700; color: #4FD1C5; margin-bottom: 12px; }
.lore {
    font-size: 15px; line-height: 1.7; color: #B0C4DE;
    font-style: italic; margin-bottom: 28px;
    border-left: 3px solid rgba(79,209,197,0.35); padding-left: 16px;
}
.swatches {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
    gap: 12px;
}
.swatch {
    border-radius: 8px; overflow: hidden;
    box-shadow: 0 2px 8px rgba(0,0,0,0.4);
}
.swatch-block { width: 100%; height: 80px; }
.swatch-label {
    background: rgba(15,42,53,0.85); padding: 8px 10px;
}
.swatch-hex {
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 12px; font-weight: 600; color: #E8F0F2;
    letter-spacing: 0.05em;
}
.swatch-role { font-size: 11px; color: #607D8B; margin-top: 2px; text-transform: uppercase; letter-spacing: 0.08em; }
"""

_MD_EXTENSIONS = ["fenced_code", "nl2br", "tables", "sane_lists", "smarty", "attr_list"]


def md_to_html(text: str, title: str = "", verse_mode: bool = False) -> str:
    """Convert markdown text to a themed HTML document for the reading view."""

    # Strip outer triple-backtick fence that LLMs sometimes add around their output.
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        inner = stripped[3:]
        if inner.startswith("\n"):
            inner = inner[1:]
        # Remove trailing fence
        inner = inner[:inner.rfind("```")].rstrip()
        text = inner

    # Dedent: if every non-empty line has consistent leading whitespace, strip it.
    # This catches LLM outputs indented with 4 spaces (which markdown reads as code).
    lines = text.splitlines()
    non_empty = [l for l in lines if l.strip()]
    if non_empty:
        common = len(non_empty[0]) - len(non_empty[0].lstrip())
        if common > 0 and all(l.startswith(" " * common) for l in non_empty):
            text = "\n".join(l[common:] if l.strip() else l for l in lines)

    # Prepend title as H1 unless the text already starts with a heading.
    if title and not text.lstrip().startswith("#"):
        text = f"# {_html_mod.escape(title)}\n\n{text}"

    try:
        import markdown as _markdown
        exts = _MD_EXTENSIONS[:]
        while exts:
            try:
                body = _markdown.markdown(text, extensions=exts)
                break
            except Exception:
                exts.pop()
        else:
            body = f"<pre>{_html_mod.escape(text)}</pre>"
    except Exception:
        body = f"<pre>{_html_mod.escape(text)}</pre>"

    css = _READING_CSS + (_VERSE_CSS_EXTRA if verse_mode else "")
    return _HTML_TEMPLATE.format(css=css, body=body)


def code_to_html(raw: str, title: str = "") -> str:
    """Monospace, indentation-preserving code view (for codeart .py artifacts).

    Deliberately bypasses `md_to_html`'s prose/markdown pipeline (dedent +
    `nl2br` + `markdown.markdown(...)`) — that pipeline is tuned for prose
    and destroys Python's syntactically-significant leading whitespace (it
    dedents common indentation and lets `markdown` reflow the rest). Here we
    only HTML-escape the source and drop it straight into a `<pre>`, so every
    space survives byte-for-byte.
    """
    escaped = _html_mod.escape(raw)
    heading = f"<h1>{_html_mod.escape(title)}</h1>" if title else ""
    body = f'{heading}<pre class="code-block">{escaped}</pre>'
    return _HTML_TEMPLATE.format(css=_READING_CSS, body=body)


def palette_to_html(data: dict) -> str:
    """Build a palette-viewer HTML page from the parsed palette JSON."""
    name = _html_mod.escape(data.get("name", "Palette"))
    lore = _html_mod.escape(data.get("lore", ""))
    colors = data.get("colors", [])

    strip_segs = "".join(
        f'<div class="strip-seg" style="background:{c.get("hex","#888")};"></div>'
        for c in colors
    )

    swatch_cards = []
    for c in colors:
        hex_val = c.get("hex", "#888888")
        role = _html_mod.escape(c.get("role", ""))
        swatch_cards.append(
            f'<div class="swatch">'
            f'<div class="swatch-block" style="background:{hex_val};"></div>'
            f'<div class="swatch-label">'
            f'<div class="swatch-hex">{_html_mod.escape(hex_val)}</div>'
            f'<div class="swatch-role">{role}</div>'
            f'</div></div>'
        )

    body = (
        f'<div class="strip">{strip_segs}</div>'
        f'<div class="info">'
        f'<h1>{name}</h1>'
        f'<p class="lore">{lore}</p>'
        f'<div class="swatches">{"".join(swatch_cards)}</div>'
        f'</div>'
    )
    return _HTML_TEMPLATE.format(css=_PALETTE_CSS, body=body)


# ═════════════════════════════════════════════════════════════════════════
# ANSI: the single parser + its HTML renderer
# ═════════════════════════════════════════════════════════════════════════

def _normalize_ansi_escapes(text: str) -> str:
    """Normalise every way an ESC byte might arrive as literal text back to \\x1b.

    LLMs and copy/paste sometimes hand back the ESCAPE sequence written out
    as text (`\\033[`, `\\x1b[`, `\\e[`, `^[`, or a bare `033[`) instead of the
    actual 0x1B byte. Both pixel formats need this normalisation applied
    identically, so it lives once, here.
    """
    text = text.replace("\\033", "\x1b").replace("\\x1b", "\x1b")
    text = text.replace("\\e", "\x1b").replace("^[", "\x1b")
    text = re.sub(r"(?<![\\x\d])033\[", "\x1b[", text)
    return text


def parse_ansi_grid(raw: str) -> list[list[tuple]]:
    """Parse ANSI-escaped text into rows of `(char, fg_hex, bg_hex)` cells.

    The single source of truth for ANSI grid parsing — see the module
    docstring for the full contract. Handles BOTH pixel formats the `ansi`
    generator has emitted over time:
      - Foreground+block (current): `\\x1b[38;5;Nm█`  -> fg set, bg None
      - Background+space (legacy):  `\\x1b[48;5;Nm `  -> bg set, fg None
    Also understands 8/16-colour SGR (30-37/90-97 fg, 40-47/100-107 bg),
    truecolour (`38;2;R;G;B` / `48;2;R;G;B`), and SGR 0 (reset both channels
    to `None`, i.e. "unset").
    """
    text = _normalize_ansi_escapes(raw)

    fg: Optional[str] = None
    bg: Optional[str] = None
    rows: list[list[tuple]] = [[]]

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b" and i + 1 < n and text[i + 1] == "[":
            j = i + 2
            while j < n and text[j] not in "ABCDEFGHJKSTfm":
                j += 1
            if j < n and text[j] == "m":
                params = text[i + 2:j]
                nums: list[int] = []
                for p in (params.split(";") if params else []):
                    try:
                        nums.append(int(p))
                    except ValueError:
                        nums.append(0)
                if not nums:
                    nums = [0]
                k = 0
                while k < len(nums):
                    v = nums[k]
                    if v == 0:
                        fg = None
                        bg = None
                    elif 30 <= v <= 37:
                        fg = _XTERM256_HEX[v - 30]
                    elif 90 <= v <= 97:
                        fg = _XTERM256_HEX[v - 90 + 8]
                    elif v == 38:
                        if k + 1 < len(nums) and nums[k + 1] == 5 and k + 2 < len(nums):
                            fg = _XTERM256_HEX[max(0, min(255, nums[k + 2]))]
                            k += 2
                        elif k + 1 < len(nums) and nums[k + 1] == 2 and k + 4 < len(nums):
                            fg = _truecolor_hex(nums[k + 2], nums[k + 3], nums[k + 4])
                            k += 4
                    elif 40 <= v <= 47:
                        bg = _XTERM256_HEX[v - 40]
                    elif 100 <= v <= 107:
                        bg = _XTERM256_HEX[v - 100 + 8]
                    elif v == 48:
                        if k + 1 < len(nums) and nums[k + 1] == 5 and k + 2 < len(nums):
                            bg = _XTERM256_HEX[max(0, min(255, nums[k + 2]))]
                            k += 2
                        elif k + 1 < len(nums) and nums[k + 1] == 2 and k + 4 < len(nums):
                            bg = _truecolor_hex(nums[k + 2], nums[k + 3], nums[k + 4])
                            k += 4
                    k += 1
            i = j + 1
        elif ch == "\n":
            rows.append([])
            fg = None
            bg = None
            i += 1
        elif ch == "\r":
            i += 1
        else:
            rows[-1].append((ch, fg, bg))
            i += 1

    # Drop empty trailing rows
    while rows and not rows[-1]:
        rows.pop()

    return rows


def ansi_to_html(text: str) -> str:
    """
    Convert ANSI escape sequences to a full-viewport CSS-grid HTML document.
    Each character cell becomes a <div> coloured by its display colour; the
    grid fills 100vw x 100vh so the art always fills the entire detail view.

    Built on `parse_ansi_grid`: for a space character, the cell's background
    colour is used (the legacy bg+space format); for any other character,
    the foreground colour is used (the current fg+block format). Cells
    without an explicit colour on the relevant channel default to black —
    matching the original per-format behaviour exactly.
    """
    DEFAULT = "#000000"
    grid = parse_ansi_grid(text)

    if not grid:
        return "<html><body style='background:#000'></body></html>"

    num_rows = len(grid)
    num_cols = max((len(r) for r in grid), default=1)

    cells: list[str] = []
    for row in grid:
        for j in range(num_cols):
            if j < len(row):
                ch, fg, bg = row[j]
                colour = bg if ch == " " else fg
                if colour is None:
                    colour = DEFAULT
            else:
                colour = DEFAULT
            cells.append(f'<div style="background:{colour}"></div>')

    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
        "*{margin:0;padding:0;box-sizing:border-box}"
        "html,body{width:100%;height:100%;background:#000;overflow:hidden}"
        f"#g{{display:grid;width:100%;height:100%;"
        f"grid-template-columns:repeat({num_cols},1fr);"
        f"grid-template-rows:repeat({num_rows},1fr)}}"
        "</style></head><body>"
        f'<div id="g">{"".join(cells)}</div>'
        "</body></html>"
    )


# ═════════════════════════════════════════════════════════════════════════
# Shared GdkPixbufAnimationIter driver (used by ArtgenDetail/ArtgenWatch's
# _animate_gif, which reuse a single persistent Gtk.Picture placeholder
# rather than swapping in a whole new widget per record).
# ═════════════════════════════════════════════════════════════════════════

def drive_gif_animation(pic: Gtk.Picture, path: str, on_timer_id) -> None:
    """Start driving an animated GIF onto an existing `Gtk.Picture`.

    `on_timer_id` is called every time the internal GLib timeout id changes
    (including to `None` once the animation is static, fails to load, or
    finishes a non-looping run) so the caller's own tracking attribute
    (e.g. `self._gif_timer_id`) stays in sync and can be cancelled at any
    time by the caller — the caller is responsible for cancelling any prior
    timer before calling this (both `ArtgenDetail._animate_gif` and
    `ArtgenWatch._animate_gif` do this identically, which is the whole point
    of sharing this driver instead of maintaining three copies of it).
    """
    try:
        anim = GdkPixbuf.PixbufAnimation.new_from_file(path)
    except Exception:
        on_timer_id(None)
        return

    if anim.is_static_image():
        pic.set_paintable(Gdk.Texture.new_for_pixbuf(anim.get_static_image()))
        on_timer_id(None)
        return

    it = anim.get_iter(None)

    def tick() -> bool:
        _t0 = time.monotonic()
        it.advance(None)
        pic.set_paintable(Gdk.Texture.new_for_pixbuf(it.get_pixbuf()))
        delay = it.get_delay_time()
        if delay < 0:
            on_timer_id(None)
            return GLib.SOURCE_REMOVE
        # Self-throttle: a heavy GIF whose per-frame decode costs more than its
        # frame delay would starve the GTK main loop and freeze the whole app
        # (an AnimateDiff result did exactly this). Never re-arm sooner than the
        # decode itself took, so the main loop always gets at least that much
        # breathing room between frames — the GIF animates slower, never frozen.
        decode_ms = int((time.monotonic() - _t0) * 1000)
        on_timer_id(GLib.timeout_add(max(delay, 10, decode_ms), tick))
        return GLib.SOURCE_REMOVE

    pic.set_paintable(Gdk.Texture.new_for_pixbuf(it.get_pixbuf()))
    delay = max(it.get_delay_time(), 10)
    on_timer_id(GLib.timeout_add(delay, tick))


# ═════════════════════════════════════════════════════════════════════════
# AnimatedGifWidget — self-managed animated-gif Gtk.Picture
# ═════════════════════════════════════════════════════════════════════════

class AnimatedGifWidget(Gtk.Picture):
    """Gtk.Picture that self-drives a GdkPixbufAnimationIter loop.

    Cancels its own timer when unrealized so it doesn't fire after removal
    (moved verbatim from `artgen_gallery._AnimatedGifWidget`; that module now
    re-exports this class under its old private name so its existing
    callers — `create_view.py`, gallery hover-swap, and the perf-regression
    tests — are untouched).
    """

    def __init__(self, path: str):
        super().__init__()
        self._timer_id: "int | None" = None
        self._iter: "GdkPixbuf.PixbufAnimationIter | None" = None
        # gif-hygiene fix 2: tracks whether the animation is currently
        # advancing, independent of `_timer_id` being set/None -- lets
        # callers (VideoPlayerWindow's fullscreen Pause/Space) query and
        # toggle play state without reaching into timer internals directly.
        # Starts True: a freshly-constructed multi-frame gif schedules its
        # first `_tick` immediately below, same as before this fix.
        self._playing: bool = True
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_content_fit(Gtk.ContentFit.COVER)
        self.connect("unrealize", self._on_unrealize)
        try:
            anim = GdkPixbuf.PixbufAnimation.new_from_file(path)
        except Exception:
            return
        if anim.is_static_image():
            self.set_paintable(Gdk.Texture.new_for_pixbuf(anim.get_static_image()))
            self._playing = False  # nothing to animate
            return
        it = anim.get_iter(None)
        self._iter = it
        pb = it.get_pixbuf()
        if pb:
            self.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
        delay = max(it.get_delay_time(), 10)
        self._timer_id = GLib.timeout_add(delay, self._tick)

    def _tick(self) -> bool:
        if self._iter is None:
            self._timer_id = None
            return GLib.SOURCE_REMOVE
        _t0 = time.monotonic()
        self._iter.advance(None)
        pb = self._iter.get_pixbuf()
        if pb is not None:
            self.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
        delay = self._iter.get_delay_time()
        if delay < 0:
            self._timer_id = None
            return GLib.SOURCE_REMOVE
        # Self-throttle (see drive_gif_animation.tick): never re-arm sooner than
        # the frame decode took, so a heavy GIF can't starve the main loop and
        # freeze the app.
        decode_ms = int((time.monotonic() - _t0) * 1000)
        self._timer_id = GLib.timeout_add(max(delay, 10, decode_ms), self._tick)
        return GLib.SOURCE_REMOVE

    def set_playing(self, playing: bool) -> None:
        """Pause/resume the frame-advance timer (gif-hygiene fix 2).

        Used by `VideoPlayerWindow`'s fullscreen Pause button / Space key so
        a GIF can actually be paused there instead of silently no-opping.
        Pausing cancels the pending `GLib.timeout` outright (same mechanism
        `_on_unrealize` already uses). Resuming re-schedules `_tick` using
        the iter's current per-frame delay -- the same "delay, floor 10ms"
        logic `__init__`/`_tick` use -- so playback picks back up from
        whatever frame it was paused on. No-ops if there's nothing to
        animate (`_iter is None`, e.g. a static image) or if the requested
        state already matches (avoids scheduling a second, competing timer).
        """
        self._playing = playing
        if not playing:
            if self._timer_id is not None:
                GLib.source_remove(self._timer_id)
                self._timer_id = None
            return
        if self._iter is None or self._timer_id is not None:
            return
        delay = max(self._iter.get_delay_time(), 10)
        self._timer_id = GLib.timeout_add(delay, self._tick)

    def toggle_playing(self) -> bool:
        """Flip play state and return the new `_playing` value."""
        self.set_playing(not self._playing)
        return self._playing

    def cancel_animation(self) -> None:
        """Stop and release the decode timer for a widget being discarded
        WITHOUT having been realized. `_on_unrealize` (the only other teardown)
        can never fire for a widget that was never attached — e.g. an
        idle-deferred hover-swap that bails before attaching this widget — so
        without this its `GLib.timeout_add` decode loop would run forever on an
        invisible Picture (review I3). Idempotent; safe on a static image."""
        self._on_unrealize(self)

    def _on_unrealize(self, _widget) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        self._iter = None


# ═════════════════════════════════════════════════════════════════════════
# Shared ext -> renderer dispatch ("unify gallery interaction" Task 5)
# ═════════════════════════════════════════════════════════════════════════
#
# `ArtgenDetail._render` used to hardcode this ext -> builder mapping as an
# inline if/elif chain against its own persistent `_art_stack` widgets. A
# net-new `ArtgenViewerWindow` (app/artgen_viewer.py) needs the identical
# DECISION for a fresh, disposable widget per record. Extracting the
# decision here (rather than copy-pasting the if/elif chain a second time)
# is the whole point -- see the module docstring's "Task 5" note.

_KIND_GIF = "gif"
_KIND_SVG = "svg"
_KIND_ANSI = "ansi"
_KIND_JSON = "json"    # palette-or-markdown; decided from *content*, not ext
_KIND_CODE = "code"
_KIND_TEXT = "text"


def resolve_render_kind(ext: str) -> str:
    """Classify a lowercased file extension into a rendering kind.

    Returns one of "gif" | "svg" | "ansi" | "json" | "code" | "text".
    Mirrors `ArtgenDetail._render`'s original ext dispatch exactly for
    .gif/.svg/.ans/.json, and adds ".py" -> "code" (codeart artifacts used to
    silently fall into the "text"/markdown branch, which mangles Python's
    significant whitespace via `md_to_html`'s dedent+`nl2br` pipeline --
    `create_view.py`'s own inline result panel already special-cased this via
    `code_to_html`; unifying the dispatch here fixes the same gap for
    ArtgenDetail and the new viewer for free). Anything else (.txt/.md/
    verse/freeform/unknown) is "text".

    "json" is deliberately not further split into palette-vs-markdown here
    -- that decision needs the *parsed* content (does it have a "colors"
    key?), not just the extension. See `build_reading_html`.
    """
    if ext == ".gif":
        return _KIND_GIF
    if ext == ".svg":
        return _KIND_SVG
    if ext == ".ans":
        return _KIND_ANSI
    if ext == ".json":
        return _KIND_JSON
    if ext == ".py":
        return _KIND_CODE
    return _KIND_TEXT


def build_reading_html(
    kind: str, raw: str, *, gen_type: str = "", params: "Optional[dict]" = None
) -> str:
    """Build the HTML document for any of the "reading-view" kinds
    ("ansi" | "json" | "code" | "text") -- the single builder dispatch shared
    by `ArtgenDetail._render` and `render_artifact_widget`/`ArtgenViewerWindow`
    so a WebView's content can never diverge between the persistent detail
    pane and the standalone viewer window.

    `gen_type`/`params` feed `derive_title`/verse-mode detection exactly like
    `ArtgenDetail._render`'s own `doc_title`/`verse_mode` locals did before
    this extraction.
    """
    params = params or {}
    doc_title = derive_title(gen_type, params)

    if kind == _KIND_ANSI:
        return ansi_to_html(raw)

    if kind == _KIND_JSON:
        try:
            data = json.loads(raw)
            return (
                palette_to_html(data)
                if isinstance(data, dict) and "colors" in data
                else md_to_html(raw, title=doc_title)
            )
        except Exception:
            return md_to_html(raw, title=doc_title)

    if kind == _KIND_CODE:
        return code_to_html(raw, title=doc_title)

    # "text" (and any unrecognised kind, defensively) -- verse gets the
    # centered-poem CSS layered on via verse_mode.
    return md_to_html(raw, title=doc_title, verse_mode=(gen_type == "verse"))


def build_reading_webview(html: str) -> Gtk.Widget:
    """Build a FRESH, one-shot WebView pre-loaded with `html`, or a plain-text
    `Gtk.TextView` fallback if WebKit isn't importable (matching
    `artgen_watch`'s `_WEBKIT_OK` degrade pattern).

    Uses the realize-deferral pattern documented on
    `ArtgenDetail._load_html`/`_on_webview_realize`: `WebKit.WebView.
    load_html()` called before the widget is realized is a silent no-op that
    leaves the view permanently blank. Since this widget is freshly built and
    1:1 with its content (unlike `ArtgenDetail`'s single persistent
    `_webview`, reused across every record it ever shows), the pending HTML
    can just live in this closure instead of needing an instance attribute
    like `ArtgenDetail._pending_html` -- there is no "later render() call"
    that could need to replace in-flight pending content for a one-shot
    widget.
    """
    if not _WEBKIT_OK:  # pragma: no cover - environment-dependent
        tv = Gtk.TextView()
        tv.set_editable(False)
        tv.set_monospace(True)
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.set_hexpand(True)
        tv.set_vexpand(True)
        # Best-effort plain-text degrade: strip tags rather than showing raw
        # HTML soup.
        tv.get_buffer().set_text(re.sub(r"<[^>]+>", " ", html))
        return tv

    webview = _WebKit.WebView()
    try:
        webview.get_settings().set_enable_javascript(False)
    except Exception:
        pass  # never let a settings lookup failure block rendering
    webview.set_hexpand(True)
    webview.set_vexpand(True)

    if webview.get_realized():
        webview.load_html(html, "about:blank")
    else:
        def _on_realize(widget, _html=html) -> None:
            widget.load_html(_html, "about:blank")

        webview.connect("realize", _on_realize)

    return webview


def render_artifact_widget(record) -> Gtk.Widget:
    """Build a fresh, display-ready `Gtk.Widget` for `record`'s primary
    artifact -- the single entry point `ArtgenViewerWindow` uses.

    `record` is any MediaRecord-like object exposing `.file_path`,
    `.generator_type` (optional), and `.params_dict` (optional dict-like) --
    duck-typed so a plain namespace/mock works fine in tests, not just a
    real `media_store.MediaRecord`.

    `ArtgenDetail` cannot call this directly -- it reuses one persistent
    `Gtk.Stack` of widgets across records for cheap prev/next navigation --
    but shares the same `resolve_render_kind`/`build_reading_html` decision
    logic, so the two can never disagree about what a given file renders as.
    """
    fp = Path(record.file_path)
    ext = fp.suffix.lower()
    kind = resolve_render_kind(ext)
    exists = fp.exists()
    gen_type = getattr(record, "generator_type", "") or ""
    params = getattr(record, "params_dict", None) or {}

    if kind == _KIND_GIF and exists:
        return AnimatedGifWidget(str(fp))

    if kind == _KIND_SVG and exists:
        pic = Gtk.Picture()
        pic.set_hexpand(True)
        pic.set_vexpand(True)
        pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        pic.set_file(Gio.File.new_for_path(str(fp)))
        return pic

    raw = fp.read_text(encoding="utf-8", errors="replace") if exists else ""
    html = build_reading_html(kind, raw, gen_type=gen_type, params=params)
    return build_reading_webview(html)
