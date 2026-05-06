#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
ArtgenDetail — full-pane detail view for a single artgen artifact.

Layout: ← Gallery header | large artifact (65%) | metadata sidebar (35%)
Navigation: ‹ › arrows step through the current filter without returning to grid.

Callbacks:
    on_back()           — user clicked ← Gallery
    on_deleted(id: str) — user confirmed deletion
    on_starred(id: str, starred: bool)
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gio, GLib, Gtk, Pango, WebKit

from media_store import media_store as _ms, MediaRecord

# ── Reading-view helpers ──────────────────────────────────────────────────────

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

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><style>{css}</style></head>
<body><div class="content">{body}</div></body>
</html>"""


def _luminance(hex_color: str) -> float:
    """Approximate relative luminance of a hex color (0=black, 1=white)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return 0.5
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def _palette_to_html(data: dict) -> str:
    """Build a palette-viewer HTML page from the parsed palette JSON."""
    import html as _html
    name = _html.escape(data.get("name", "Palette"))
    lore = _html.escape(data.get("lore", ""))
    colors = data.get("colors", [])

    strip_segs = "".join(
        f'<div class="strip-seg" style="background:{c.get("hex","#888")};"></div>'
        for c in colors
    )

    swatch_cards = []
    for c in colors:
        hex_val = c.get("hex", "#888888")
        role = _html.escape(c.get("role", ""))
        swatch_cards.append(
            f'<div class="swatch">'
            f'<div class="swatch-block" style="background:{hex_val};"></div>'
            f'<div class="swatch-label">'
            f'<div class="swatch-hex">{_html.escape(hex_val)}</div>'
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


_MD_EXTENSIONS = ["fenced_code", "nl2br", "tables", "sane_lists", "smarty", "attr_list"]

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


def _derive_title(gen_type: str, params: dict) -> str:
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


def _md_to_html(text: str, title: str = "", verse_mode: bool = False) -> str:
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
        import html as _html
        text = f"# {_html.escape(title)}\n\n{text}"

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
            import html as _html
            body = f"<pre>{_html.escape(text)}</pre>"
    except Exception:
        import html as _html
        body = f"<pre>{_html.escape(text)}</pre>"

    css = _READING_CSS + (_VERSE_CSS_EXTRA if verse_mode else "")
    return _HTML_TEMPLATE.format(css=css, body=body)


# xterm-256 default colour table (indices 0-255) — same as in artgen_gallery
def _build_ansi_pal() -> list[str]:
    sys16 = [
        "#000000","#AA0000","#00AA00","#AA5500","#0000AA","#AA00AA","#00AAAA","#AAAAAA",
        "#555555","#FF5555","#55FF55","#FFFF55","#5555FF","#FF55FF","#55FFFF","#FFFFFF",
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

_ANSI_PAL = _build_ansi_pal()


def _ansi_to_html(text: str) -> str:
    """
    Convert ANSI escape sequences to a full-viewport CSS-grid HTML document.
    Each pixel cell becomes a <div> coloured by its background colour; the
    grid fills 100vw × 100vh so the art always fills the entire detail view.
    Handles: SGR 0 (reset), 40-47/100-107 (8-colour bg), 48;5;N (256-colour),
    48;2;R;G;B (truecolour).
    """
    import re

    # Normalise escape variants to actual ESC byte.
    text = text.replace("\\033", "\x1b").replace("\\x1b", "\x1b")
    text = text.replace("\\e", "\x1b").replace("^[", "\x1b")
    text = re.sub(r"(?<![\\x\d])033\[", "\x1b[", text)

    DEFAULT = "#000000"
    bg = DEFAULT
    grid: list[list[str]] = [[]]

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b" and i + 1 < n and text[i + 1] == "[":
            j = i + 2
            while j < n and text[j] not in "ABCDEFGHJKSTfm":
                j += 1
            if j < n and text[j] == "m":
                params = text[i + 2 : j]
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
                        bg = DEFAULT
                    elif 40 <= v <= 47:
                        bg = _ANSI_PAL[v - 40]
                    elif 100 <= v <= 107:
                        bg = _ANSI_PAL[v - 100 + 8]
                    elif v == 48:
                        if k + 1 < len(nums) and nums[k + 1] == 5 and k + 2 < len(nums):
                            bg = _ANSI_PAL[max(0, min(255, nums[k + 2]))]
                            k += 2
                        elif k + 1 < len(nums) and nums[k + 1] == 2 and k + 4 < len(nums):
                            bg = "#{:02x}{:02x}{:02x}".format(
                                max(0, min(255, nums[k + 2])),
                                max(0, min(255, nums[k + 3])),
                                max(0, min(255, nums[k + 4])),
                            )
                            k += 4
                    k += 1
            i = j + 1
        elif ch == "\n":
            grid.append([])
            bg = DEFAULT
            i += 1
        elif ch == "\r":
            i += 1
        else:
            grid[-1].append(bg)
            i += 1

    # Drop empty trailing rows
    while grid and not grid[-1]:
        grid.pop()

    if not grid:
        return "<html><body style='background:#000'></body></html>"

    num_rows = len(grid)
    num_cols = max((len(r) for r in grid), default=1)

    # Build one <div> per cell; empty cells default to black.
    cells: list[str] = []
    for row in grid:
        for j in range(num_cols):
            colour = row[j] if j < len(row) else DEFAULT
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


class ArtgenDetail(Gtk.Box):

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.on_back: Optional[Callable[[], None]] = None
        self.on_deleted: Optional[Callable[[str], None]] = None
        self.on_starred: Optional[Callable[[str, bool], None]] = None
        self.on_use_as_seed: Optional[Callable[["MediaRecord"], None]] = None
        self._records: list[MediaRecord] = []
        self._idx: int = 0
        self._build()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # Header bar
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hdr.set_margin_start(12)
        hdr.set_margin_end(12)
        hdr.set_margin_top(8)
        hdr.set_margin_bottom(8)

        self._back_btn = Gtk.Button(label="← Gallery")
        self._back_btn.add_css_class("flat")
        self._back_btn.connect("clicked", lambda _: self.on_back and self.on_back())
        hdr.append(self._back_btn)

        self._title_lbl = Gtk.Label(label="")
        self._title_lbl.set_hexpand(True)
        self._title_lbl.set_xalign(0.5)
        self._title_lbl.add_css_class("artgen-detail-title")
        hdr.append(self._title_lbl)

        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._prev_btn = Gtk.Button(label="‹")
        self._prev_btn.connect("clicked", lambda _: self._step(-1))
        self._next_btn = Gtk.Button(label="›")
        self._next_btn.connect("clicked", lambda _: self._step(1))
        nav_box.append(self._prev_btn)
        nav_box.append(self._next_btn)
        hdr.append(nav_box)

        self.append(hdr)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Body: artifact (left 65%) + sidebar (right 35%)
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        body.set_vexpand(True)

        # Artifact pane
        art_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        art_box.set_hexpand(True)
        art_box.set_vexpand(True)

        self._art_stack = Gtk.Stack()
        self._art_stack.set_hexpand(True)
        self._art_stack.set_vexpand(True)

        # SVG
        svg_scroll = Gtk.ScrolledWindow()
        svg_scroll.set_hexpand(True)
        svg_scroll.set_vexpand(True)
        self._svg_pic = Gtk.Picture()
        self._svg_pic.set_hexpand(True)
        self._svg_pic.set_vexpand(True)
        self._svg_pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        svg_scroll.set_child(self._svg_pic)
        self._art_stack.add_named(svg_scroll, "svg")

        # Plain text fallback (kept for any edge cases)
        text_scroll = Gtk.ScrolledWindow()
        text_scroll.set_hexpand(True)
        text_scroll.set_vexpand(True)
        self._text_view = Gtk.TextView()
        self._text_view.set_editable(False)
        self._text_view.set_monospace(True)
        self._text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._text_view.set_margin_start(20)
        self._text_view.set_margin_end(20)
        self._text_view.set_margin_top(16)
        self._text_view.set_margin_bottom(16)
        text_scroll.set_child(self._text_view)
        self._art_stack.add_named(text_scroll, "text")

        # Markdown reading view — rich, cozy rendering for verse / palette / freeform
        self._webview = WebKit.WebView()
        self._webview.get_settings().set_enable_javascript(False)
        self._webview.set_hexpand(True)
        self._webview.set_vexpand(True)
        self._art_stack.add_named(self._webview, "reading")

        art_box.append(self._art_stack)
        body.append(art_box)
        self._sidebar_sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        body.append(self._sidebar_sep)

        # Sidebar
        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_size_request(260, -1)
        self._sidebar_scroll = sidebar_scroll

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sidebar.set_margin_start(12)
        sidebar.set_margin_end(12)
        sidebar.set_margin_top(12)
        sidebar.set_margin_bottom(12)

        self._meta_lbl = Gtk.Label(label="")
        self._meta_lbl.set_xalign(0)
        self._meta_lbl.set_wrap(True)
        self._meta_lbl.add_css_class("muted")
        sidebar.append(self._meta_lbl)

        self._params_lbl = Gtk.Label(label="")
        self._params_lbl.set_xalign(0)
        self._params_lbl.set_wrap(True)
        self._params_lbl.set_selectable(True)
        sidebar.append(self._params_lbl)

        # Star toggle
        self._star_btn = Gtk.ToggleButton(label="☆  Star")
        self._star_btn.connect("toggled", self._on_star_toggled)
        sidebar.append(self._star_btn)

        # Open file
        open_btn = Gtk.Button(label="Open File")
        open_btn.connect("clicked", self._on_open_file)
        sidebar.append(open_btn)

        # Use as seed for video/animate
        self._seed_btn = Gtk.Button(label="🎬  Use as Seed")
        self._seed_btn.set_tooltip_text("Set as seed image for video/animate generation")
        self._seed_btn.connect("clicked", self._on_use_as_seed_clicked)
        sidebar.append(self._seed_btn)

        # Delete
        self._del_btn = Gtk.Button(label="🗑 Delete")
        self._del_btn.add_css_class("destructive-action")
        self._del_btn.connect("clicked", self._on_delete)
        sidebar.append(self._del_btn)

        sidebar_scroll.set_child(sidebar)
        body.append(sidebar_scroll)

        self.append(body)

    # ── Public ────────────────────────────────────────────────────────────────

    def set_back_label(self, label: str) -> None:
        self._back_btn.set_label(label)

    def show_record(self, media_id: str, records: list[MediaRecord]) -> None:
        """Display the record with media_id; records is the current filter list."""
        self._records = records
        self._idx = next((i for i, r in enumerate(records) if r.id == media_id), 0)
        self._render()

    # ── Navigation ────────────────────────────────────────────────────────────

    def _step(self, delta: int) -> None:
        if not self._records:
            return
        self._idx = (self._idx + delta) % len(self._records)
        self._render()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self) -> None:
        if not self._records:
            return
        rec = self._records[self._idx]
        n = len(self._records)

        self._title_lbl.set_label(
            f"{rec.generator_type or 'artgen'} — {rec.created_at[:10]}  ({self._idx+1}/{n})"
        )
        self._prev_btn.set_sensitive(n > 1)
        self._next_btn.set_sensitive(n > 1)

        # Metadata sidebar
        p = rec.params_dict
        gen_s = p.get("generation_seconds", "")
        gen_str = f"  ({gen_s}s)" if gen_s else ""
        self._meta_lbl.set_label(
            f"{rec.created_at[:19].replace('T',' ')}{gen_str}\n"
            f"model: {rec.model_id or '—'}"
        )
        param_lines = "\n".join(
            f"{k}: {v}" for k, v in p.items()
            if k not in ("generation_seconds",) and isinstance(v, (str, int, float, bool))
        )
        self._params_lbl.set_label(param_lines)

        self._star_btn.handler_block_by_func(self._on_star_toggled)
        self._star_btn.set_active(bool(rec.starred))
        self._star_btn.set_label("★  Starred" if rec.starred else "☆  Star")
        self._star_btn.handler_unblock_by_func(self._on_star_toggled)

        # Artifact
        fp = Path(rec.file_path)
        ext = fp.suffix.lower()
        raw = fp.read_text(encoding="utf-8", errors="replace") if fp.exists() else ""

        gen_type = rec.generator_type or ""
        doc_title = _derive_title(gen_type, p)
        verse_mode = gen_type == "verse"

        if ext == ".svg" and fp.exists():
            self._svg_pic.set_file(Gio.File.new_for_path(str(fp)))
            self._art_stack.set_visible_child_name("svg")
        elif ext == ".ans":
            self._webview.load_html(_ansi_to_html(raw), "about:blank")
            self._art_stack.set_visible_child_name("reading")
        elif ext == ".json":
            # Palette JSON — render color swatches
            try:
                data = json.loads(raw)
                html = _palette_to_html(data) if "colors" in data else _md_to_html(raw, title=doc_title)
            except Exception:
                html = _md_to_html(raw, title=doc_title)
            self._webview.load_html(html, "about:blank")
            self._art_stack.set_visible_child_name("reading")
        else:
            # verse .txt, freeform — render as markdown reading view
            html = _md_to_html(raw, title=doc_title, verse_mode=verse_mode)
            self._webview.load_html(html, "about:blank")
            self._art_stack.set_visible_child_name("reading")

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _on_star_toggled(self, btn: Gtk.ToggleButton) -> None:
        if not self._records:
            return
        rec = self._records[self._idx]
        starred = btn.get_active()
        _ms.star(rec.id, starred)
        rec.starred = int(starred)
        btn.set_label("★  Starred" if starred else "☆  Star")
        if self.on_starred:
            self.on_starred(rec.id, starred)

    def _on_open_file(self, _btn) -> None:
        if not self._records:
            return
        rec = self._records[self._idx]
        if Path(rec.file_path).exists():
            subprocess.Popen(["xdg-open", rec.file_path])

    def _on_use_as_seed_clicked(self, _btn) -> None:
        if not self._records or self.on_use_as_seed is None:
            return
        self.on_use_as_seed(self._records[self._idx])

    def _on_delete(self, _btn) -> None:
        if not self._records:
            return
        rec = self._records[self._idx]
        dialog = Gtk.AlertDialog()
        dialog.set_message("Delete this artifact?")
        dialog.set_detail(f"{rec.generator_type} — {rec.created_at[:10]}")
        dialog.set_buttons(["Cancel", "Delete"])
        dialog.set_cancel_button(0)
        dialog.set_default_button(0)
        dialog.choose(self.get_root(), None, self._delete_confirmed, rec.id)

    def _delete_confirmed(self, dialog, result, media_id: str) -> None:
        try:
            btn_idx = dialog.choose_finish(result)
        except Exception:
            return
        if btn_idx != 1:
            return
        _ms.delete(media_id)
        self._records = [r for r in self._records if r.id != media_id]
        if self.on_deleted:
            self.on_deleted(media_id)
        if self._records:
            self._idx = min(self._idx, len(self._records) - 1)
            self._render()
        elif self.on_back:
            self.on_back()
