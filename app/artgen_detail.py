#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
ArtgenDetail — full-pane detail view for a single artgen artifact.

Layout: header (title + ‹ › nav + ⛶ Fullscreen) | large artifact (65%) | metadata sidebar (35%)
Navigation: ‹ › arrows step through the current filter without returning to grid.

Callbacks:
    on_back()           — no longer user-facing (see below); still invoked
                          programmatically when a delete empties the list
    on_deleted(id: str) — user confirmed deletion
    on_starred(id: str, starred: bool)

"Unify gallery interaction" Task 7 (v0.50.0): the visible "← Gallery" back
button is removed -- in the two-pane right-stack layout (Tasks 2/3) the
gallery grid is always visible on the left, so there is nothing left to
"go back" to, and native `DetailPanel` (the other `_right_stack` child) has
no back button at all. The `on_back` CALLABLE ATTRIBUTE stays: main_window.py
still wires `self._artgen_detail.on_back = lambda: self._set_detail_pane_visible(False)`
so `_delete_confirmed` can collapse the pane when a delete empties the record
list -- that path is unrelated to the removed button. In its place the header
gets a "⛶ Fullscreen" button (parity with native `DetailPanel`'s ⛶ buttons)
that opens `ArtgenViewerWindow` for the currently-shown record.
"""
from __future__ import annotations

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
# Moved to artgen_render.py (v0.48.0, media-showcase-everywhere Task 1) --
# aliased here under their old private names so the rest of this file (and
# any other module that imported them from here) doesn't need to change.
# `artgen_watch.py` used to import these FROM this module; it now imports
# them directly from artgen_render, so no back-compat re-export is needed
# for external callers -- these aliases exist purely to keep this file's own
# call sites (_render(), below) unchanged.
#
# "unify gallery interaction" Task 5 (v0.49.0): the ext -> builder DECISION
# itself (which used to be an inline if/elif chain in `_render` against
# `.gif`/`.svg`/`.ans`/`.json`) is now `artgen_render.resolve_render_kind` +
# `.build_reading_html`, shared with the new `ArtgenViewerWindow`
# (app/artgen_viewer.py) so the two can never drift apart the way
# artgen_detail/artgen_watch/artgen_gallery drifted before Task 1.
# `ansi_to_html`/`palette_to_html`/`md_to_html`/`derive_title` are no longer
# imported directly here -- `build_reading_html` calls all four internally --
# so their old `_ansi_to_html`/`_palette_to_html`/`_md_to_html`/`_derive_title`
# aliases are removed too (nothing in this file called them outside `_render`).
from artgen_render import (
    drive_gif_animation as _drive_gif_animation,
    resolve_render_kind as _resolve_render_kind,
    build_reading_html as _build_reading_html,
)
from artgen_viewer import ArtgenViewerWindow


class ArtgenDetail(Gtk.Box):

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.on_back: Optional[Callable[[], None]] = None
        self.on_deleted: Optional[Callable[[str], None]] = None
        self.on_starred: Optional[Callable[[str, bool], None]] = None
        # Task 8 follow-up (remix-pipeline-unification): the parallel "🔀
        # Remix" popover seam (`on_remix`) is gone — `on_remix_as_pipeline`
        # is the single remix affordance now, wired to the sidebar's one
        # remaining button.
        self.on_remix_as_pipeline: Optional[Callable[["MediaRecord"], None]] = None
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

        self._full_btn = Gtk.Button(label="⛶ Fullscreen")
        self._full_btn.set_tooltip_text("Open in a maximized window")
        self._full_btn.add_css_class("flat")
        self._full_btn.set_sensitive(False)  # no record shown yet
        self._full_btn.connect("clicked", self._open_fullscreen)
        hdr.append(self._full_btn)

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

        # SVG / static image
        svg_scroll = Gtk.ScrolledWindow()
        svg_scroll.set_hexpand(True)
        svg_scroll.set_vexpand(True)
        self._svg_pic = Gtk.Picture()
        self._svg_pic.set_hexpand(True)
        self._svg_pic.set_vexpand(True)
        self._svg_pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        svg_scroll.set_child(self._svg_pic)
        self._art_stack.add_named(svg_scroll, "svg")

        # Animated GIF — GdkPixbufAnimationIter drives frames; Gtk.Image.set_from_animation
        # does not exist in GTK 4.14
        gif_scroll = Gtk.ScrolledWindow()
        gif_scroll.set_hexpand(True)
        gif_scroll.set_vexpand(True)
        self._gif_pic = Gtk.Picture()
        self._gif_pic.set_hexpand(True)
        self._gif_pic.set_vexpand(True)
        self._gif_pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        gif_scroll.set_child(self._gif_pic)
        self._art_stack.add_named(gif_scroll, "gif")
        self._gif_timer_id: int | None = None

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
        # Pending HTML to load once the WebView is realized. load_html() called
        # before realize is a silent no-op that leaves the widget white — we
        # queue the content here and flush it in _on_webview_realize().
        self._pending_html: str | None = None
        self._webview.connect("realize", self._on_webview_realize)

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

        # Prompt display — shown when rec.prompt is non-empty
        self._prompt_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self._prompt_sep.set_margin_top(4)
        self._prompt_sep.set_margin_bottom(4)
        sidebar.append(self._prompt_sep)

        prompt_hdr = Gtk.Label(label="Prompt")
        prompt_hdr.set_xalign(0)
        prompt_hdr.add_css_class("caption-heading")
        sidebar.append(prompt_hdr)
        self._prompt_hdr = prompt_hdr

        self._prompt_lbl = Gtk.Label(label="")
        self._prompt_lbl.set_xalign(0)
        self._prompt_lbl.set_wrap(True)
        self._prompt_lbl.set_selectable(True)
        self._prompt_lbl.add_css_class("muted")
        sidebar.append(self._prompt_lbl)

        # Star toggle
        self._star_btn = Gtk.ToggleButton(label="☆  Star")
        self._star_btn.connect("toggled", self._on_star_toggled)
        sidebar.append(self._star_btn)

        # Open file
        open_btn = Gtk.Button(label="Open File")
        open_btn.connect("clicked", self._on_open_file)
        sidebar.append(open_btn)

        # Single remix affordance (Task 8 follow-up): opens Pipeline Studio's
        # Muse scoped to this artifact. The former parallel "🔀 Remix"
        # popover button (`_seed_btn` → `_on_remix_clicked` → `on_remix`) is
        # gone; this is relabeled to the canonical name.
        self._remix_as_pipeline_btn = Gtk.Button(label="🔀 Remix")
        self._remix_as_pipeline_btn.set_tooltip_text("Remix this into a pipeline")
        self._remix_as_pipeline_btn.connect("clicked", self._on_remix_as_pipeline_clicked)
        sidebar.append(self._remix_as_pipeline_btn)

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

    def pause_animation(self) -> None:
        """Cancel any running GIF animation timer.

        Unify-gallery-interaction-pattern Task 3: `self` now lives as one of
        two children inside MainWindow's shared `_right_stack` (a Gtk.Stack).
        A Gtk.Stack keeps its hidden child realized (unlike the removed
        artgen-gallery Overlay this replaced), so a GIF-driving GLib timer
        started while this pane was visible would otherwise keep firing
        forever after the stack switches to the "native" `DetailPanel` child
        -- harmless (nothing is drawn) but a wasted, indefinitely-repeating
        timer. Callers switch the stack away from "artgen" and call this in
        the same breath (see `MainWindow._on_card_selected`). Same guard
        `_render` uses at its own top, exposed as a public no-arg method so
        it can be called from outside without touching `_render`'s internals.
        """
        if self._gif_timer_id is not None:
            GLib.source_remove(self._gif_timer_id)
            self._gif_timer_id = None

    # ── Navigation ────────────────────────────────────────────────────────────

    def _step(self, delta: int) -> None:
        if not self._records:
            return
        self._idx = (self._idx + delta) % len(self._records)
        self._render()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self) -> None:
        if self._gif_timer_id is not None:
            GLib.source_remove(self._gif_timer_id)
            self._gif_timer_id = None
        self._full_btn.set_sensitive(bool(self._records))
        if not self._records:
            return
        rec = self._records[self._idx]
        n = len(self._records)

        from time_utils import fmt_local_date, fmt_local_12h
        self._title_lbl.set_label(
            f"{rec.generator_type or 'artgen'} — {fmt_local_date(rec.created_at)}  ({self._idx+1}/{n})"
        )
        self._prev_btn.set_sensitive(n > 1)
        self._next_btn.set_sensitive(n > 1)

        # Metadata sidebar
        p = rec.params_dict
        gen_s = p.get("generation_seconds", "")
        gen_str = f"  ({gen_s}s)" if gen_s else ""
        self._meta_lbl.set_label(
            f"{fmt_local_12h(rec.created_at)}{gen_str}\n"
            f"model: {rec.model_id or '—'}"
        )
        _PARAMS_SKIP = {"generation_seconds", "prompt"}
        param_lines = "\n".join(
            f"{k}: {v}" for k, v in p.items()
            if k not in _PARAMS_SKIP and isinstance(v, (str, int, float, bool))
        )
        self._params_lbl.set_label(param_lines)

        # Prompt — prefer rec.prompt; fall back to params["prompt"] for older records
        prompt_text = rec.prompt or p.get("prompt", "")
        has_prompt = bool(prompt_text and prompt_text.strip())
        self._prompt_sep.set_visible(has_prompt)
        self._prompt_hdr.set_visible(has_prompt)
        self._prompt_lbl.set_visible(has_prompt)
        if has_prompt:
            self._prompt_lbl.set_label(prompt_text.strip())

        self._star_btn.handler_block_by_func(self._on_star_toggled)
        self._star_btn.set_active(bool(rec.starred))
        self._star_btn.set_label("★  Starred" if rec.starred else "☆  Star")
        self._star_btn.handler_unblock_by_func(self._on_star_toggled)

        # Artifact — ext -> kind decision is the shared
        # `artgen_render.resolve_render_kind`/`build_reading_html` dispatch
        # (see the import block above); this file keeps only the WIDGET
        # plumbing (which persistent `_art_stack` child to show / how to
        # drive the reused `_gif_pic`/`_webview`) since that reuse-across-
        # records behavior isn't shared with the fresh-widget-per-record
        # `ArtgenViewerWindow`.
        fp = Path(rec.file_path)
        ext = fp.suffix.lower()
        gen_type = rec.generator_type or ""
        kind = _resolve_render_kind(ext)

        if kind == "gif" and fp.exists():
            self._animate_gif(self._gif_pic, str(fp))
            self._art_stack.set_visible_child_name("gif")
        elif kind == "svg" and fp.exists():
            self._svg_pic.set_file(Gio.File.new_for_path(str(fp)))
            self._art_stack.set_visible_child_name("svg")
        else:
            raw = fp.read_text(encoding="utf-8", errors="replace") if fp.exists() else ""
            html = _build_reading_html(kind, raw, gen_type=gen_type, params=p)
            self._load_html(html)
            self._art_stack.set_visible_child_name("reading")

    def _load_html(self, html: str) -> None:
        """Load HTML into the WebView, deferring until it is realized.

        WebKit.WebView.load_html() called before realize is a silent no-op
        that leaves the widget white — this is the white-screen bug seen when
        opening a palette or verse from the gallery before the panel has been
        shown for the first time.  Queue the content and flush on realize.
        """
        if self._webview.get_realized():
            self._webview.load_html(html, "about:blank")
        else:
            self._pending_html = html

    def _on_webview_realize(self, _widget) -> None:
        """Flush any HTML that was queued before the WebView was realized."""
        if self._pending_html is not None:
            self._webview.load_html(self._pending_html, "about:blank")
            self._pending_html = None

    def _animate_gif(self, pic: Gtk.Picture, path: str) -> None:
        """Drive an animated GIF on a Gtk.Picture via the shared driver.

        Cancels any running animation before starting the new one. Delegates
        to `artgen_render.drive_gif_animation` (shared with
        `ArtgenWatch._animate_gif`) so the two panes' identical
        GdkPixbufAnimationIter drivers can't drift apart.
        """
        if self._gif_timer_id is not None:
            GLib.source_remove(self._gif_timer_id)
            self._gif_timer_id = None

        def _on_timer_id(tid: "int | None") -> None:
            self._gif_timer_id = tid

        _drive_gif_animation(pic, path, _on_timer_id)

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

    def _open_fullscreen(self, _btn) -> None:
        """Open the currently-shown record in a standalone maximized window.

        Parity with native `DetailPanel`'s "⛶ Fullscreen"/"⛶ View Full"
        buttons (`VideoPlayerWindow`/`ImageViewerWindow` in main_window.py),
        just routed to the artgen-specific `ArtgenViewerWindow` (Task 5)
        since the native viewers don't handle svg/gif/ansi/palette/verse/
        markdown/codeart. The button is desensitized (see `_render`) whenever
        `_records` is empty, so this is unreachable via a real click in that
        state -- the guard below only matters for programmatic `emit`.
        """
        if not self._records:
            return
        ArtgenViewerWindow(self._records[self._idx], self.get_root()).present()

    def _on_open_file(self, _btn) -> None:
        if not self._records:
            return
        rec = self._records[self._idx]
        if Path(rec.file_path).exists():
            subprocess.Popen(["xdg-open", rec.file_path])

    def _on_remix_as_pipeline_clicked(self, _btn) -> None:
        """Forward the "remix as pipeline" request to the panel callback if wired."""
        if not self._records or self.on_remix_as_pipeline is None:
            return
        self.on_remix_as_pipeline(self._records[self._idx])

    def _on_delete(self, _btn) -> None:
        if not self._records:
            return
        from time_utils import fmt_local_date
        rec = self._records[self._idx]
        dialog = Gtk.AlertDialog()
        dialog.set_message("Delete this artifact?")
        dialog.set_detail(f"{rec.generator_type} — {fmt_local_date(rec.created_at)}")
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
