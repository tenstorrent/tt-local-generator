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

# ── Reading-view HTML template ────────────────────────────────────────────────

_READING_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html { background: #1A3C47; }
body {
    max-width: 680px;
    margin: 0 auto;
    padding: 48px 36px 64px;
    font-family: 'Fira Sans', 'Liberation Sans', 'Noto Sans', sans-serif;
    font-size: 17px;
    line-height: 1.75;
    color: #E8F0F2;
    background: #1A3C47;
    -webkit-font-smoothing: antialiased;
}
h1 {
    font-size: 1.55em;
    font-weight: 700;
    color: #4FD1C5;
    border-bottom: 1px solid rgba(79,209,197,0.25);
    padding-bottom: 10px;
    margin-bottom: 20px;
    margin-top: 0;
}
h2 {
    font-size: 1.2em;
    font-weight: 600;
    color: #81E6D9;
    margin-top: 32px;
    margin-bottom: 10px;
}
h3 {
    font-size: 1.05em;
    font-weight: 600;
    color: #B0C4DE;
    margin-top: 24px;
    margin-bottom: 8px;
}
p { margin-bottom: 16px; }
strong { font-weight: 700; color: #F0F7FA; }
em { font-style: italic; color: #EC96B8; }
code {
    font-family: 'JetBrains Mono', 'Fira Code', 'Liberation Mono', monospace;
    font-size: 0.88em;
    background: #0F2A35;
    color: #4FD1C5;
    padding: 2px 7px;
    border-radius: 4px;
}
pre {
    background: #0F2A35;
    border-left: 3px solid #4FD1C5;
    padding: 16px 20px;
    border-radius: 0 6px 6px 0;
    overflow-x: auto;
    margin-bottom: 20px;
}
pre code {
    background: none;
    padding: 0;
    color: #E8F0F2;
    font-size: 0.92em;
    line-height: 1.55;
}
blockquote {
    border-left: 3px solid #4FD1C5;
    margin: 20px 0;
    padding: 4px 0 4px 20px;
    color: #B0C4DE;
    font-style: italic;
}
hr {
    border: none;
    border-top: 1px solid rgba(79,209,197,0.2);
    margin: 32px 0;
}
ul, ol { padding-left: 24px; margin-bottom: 16px; }
li { margin-bottom: 6px; }
"""

_READING_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8">
<style>{css}</style>
</head>
<body>{body}</body>
</html>"""


def _md_to_html(text: str) -> str:
    """Convert markdown text to a themed HTML document for the reading view."""
    try:
        import markdown as _markdown
        body = _markdown.markdown(
            text,
            extensions=["fenced_code", "nl2br"],
        )
    except Exception:
        # Fallback: wrap in <pre> if markdown conversion fails
        import html as _html
        body = f"<pre>{_html.escape(text)}</pre>"
    return _READING_TEMPLATE.format(css=_READING_CSS, body=body)


class ArtgenDetail(Gtk.Box):

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.on_back: Optional[Callable[[], None]] = None
        self.on_deleted: Optional[Callable[[str], None]] = None
        self.on_starred: Optional[Callable[[str, bool], None]] = None
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

        back_btn = Gtk.Button(label="← Gallery")
        back_btn.add_css_class("flat")
        back_btn.connect("clicked", lambda _: self.on_back and self.on_back())
        hdr.append(back_btn)

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

        # ANSI / monospace fallback (for .ans files only)
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
        settings = self._webview.get_settings()
        settings.set_enable_javascript(False)
        settings.set_enable_hyperlink_auditing(False)
        self._webview.set_hexpand(True)
        self._webview.set_vexpand(True)
        self._art_stack.add_named(self._webview, "reading")

        art_box.append(self._art_stack)
        body.append(art_box)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # Sidebar
        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_size_request(260, -1)

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

        # Delete
        self._del_btn = Gtk.Button(label="🗑 Delete")
        self._del_btn.add_css_class("destructive-action")
        self._del_btn.connect("clicked", self._on_delete)
        sidebar.append(self._del_btn)

        sidebar_scroll.set_child(sidebar)
        body.append(sidebar_scroll)

        self.append(body)

    # ── Public ────────────────────────────────────────────────────────────────

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
        if ext == ".svg" and fp.exists():
            self._svg_pic.set_file(Gio.File.new_for_path(str(fp)))
            self._art_stack.set_visible_child_name("svg")
        elif ext == ".ans":
            # ANSI art: monospace only — escape codes corrupt in HTML
            text = fp.read_text(encoding="utf-8", errors="replace") if fp.exists() else ""
            self._text_view.get_buffer().set_text(text)
            self._art_stack.set_visible_child_name("text")
        else:
            # Everything else (verse .txt, palette .json, freeform) — render as markdown
            raw = fp.read_text(encoding="utf-8", errors="replace") if fp.exists() else ""
            self._webview.load_html(_md_to_html(raw), None)
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
