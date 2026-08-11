#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
ArtgenGallery — card grid with filter chips and ▶ Watch button.

Signals emitted (via GObject or callback):
    card_activated(media_id: str)   — user clicked a card
    watch_requested(filter_kwargs)  — user clicked ▶ Watch
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from media_store import media_store as _ms, MediaRecord
import gallery_layout
from artgen_render import AnimatedGifWidget as _AnimatedGifWidget
from artgen_render import parse_ansi_grid
from artgen_viewer import ArtgenViewerWindow


# ── Rich card content builders ────────────────────────────────────────────────

_TYPE_EMOJI: dict[str, str] = {
    "landscape": "🏔", "skyline": "🌃", "verse": "✍",
    "constellation": "✦", "geometric": "⬡", "circuit": "⬟",
    "palette": "◼", "ansi": "▓", "freeform": "?",
    "ansi-image": "▓",  # image->ANSI transform (Effort B Task 2) — same
                        # color-grid render as the LLM "ansi" generator.
}


def _hex_to_rgb01(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return (0.2, 0.3, 0.35)
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


# ── Palette ───────────────────────────────────────────────────────────────────

def _rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    """Trace a rounded rectangle path on the given Cairo context."""
    cr.new_sub_path()
    cr.arc(x + r,     y + r,     r, 3.14159, 2.70 * 3.14159 / 2)
    cr.arc(x + w - r, y + r,     r, -0.5 * 3.14159, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 0.5 * 3.14159)
    cr.arc(x + r,     y + h - r, r, 0.5 * 3.14159, 3.14159)
    cr.close_path()


def _palette_card_widget(data: dict) -> Gtk.Box:
    """
    Card content for a palette JSON: a grid of rounded color swatches
    with the palette name overlaid at the bottom.
    """
    colors = [c.get("hex", "#888888") for c in data.get("colors", [])]
    name = data.get("name", "")

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.set_hexpand(True)
    box.set_vexpand(True)

    # Swatch grid — Cairo DrawingArea
    area = Gtk.DrawingArea()
    area.set_hexpand(True)
    area.set_vexpand(True)

    def draw(_widget, cr, w, h):
        # Dark background
        cr.set_source_rgb(0.06, 0.16, 0.20)
        cr.paint()
        if not colors:
            return
        n = len(colors)
        # Lay out in at most 2 rows; prefer more columns than rows
        cols = max(1, (n + 1) // 2) if n > 3 else n
        rows = (n + cols - 1) // cols

        pad = 5.0
        gap = 3.0
        avail_w = w - 2 * pad
        avail_h = h - 2 * pad
        sw = (avail_w - gap * (cols - 1)) / cols
        sh = (avail_h - gap * (rows - 1)) / rows
        size = min(sw, sh)

        # Center the swatch grid
        total_w = cols * size + gap * (cols - 1)
        total_h = rows * size + gap * (rows - 1)
        ox = (w - total_w) / 2
        oy = (h - total_h) / 2

        radius = max(2.0, size * 0.12)
        for i, hx in enumerate(colors):
            row_i = i // cols
            col_i = i % cols
            x = ox + col_i * (size + gap)
            y = oy + row_i * (size + gap)
            cr.set_source_rgb(*_hex_to_rgb01(hx))
            _rounded_rect(cr, x, y, size, size, radius)
            cr.fill()

    area.set_draw_func(draw)
    box.append(area)

    # Palette name strip at the bottom of the content area
    if name:
        lbl = Gtk.Label(label=name)
        lbl.add_css_class("artgen-palette-name")
        lbl.set_ellipsize(3)   # PANGO_ELLIPSIZE_END
        lbl.set_max_width_chars(18)
        box.append(lbl)

    return box


# ── Text snippet ──────────────────────────────────────────────────────────────

def _strip_md(line: str) -> str:
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
    line = re.sub(r"\*(.+?)\*", r"\1", line)
    line = re.sub(r"__(.+?)__", r"\1", line)
    line = re.sub(r"`(.+?)`", r"\1", line)
    return line.strip(" *_#`")


def _text_preview_parts(text: str) -> tuple[str, str]:
    title = ""
    body_parts: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s == "---":
            continue
        if s.startswith("#") and not title:
            title = _strip_md(s)
        else:
            clean = _strip_md(s)
            if clean:
                body_parts.append(clean)
        if title and len(" ".join(body_parts)) > 140:
            break
    body = " ".join(body_parts)[:140]
    if not title and body:
        title, body = body[:50], body[50:]
    return title, body


def _text_preview_widget(text: str) -> Gtk.Box:
    title, body = _text_preview_parts(text)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.set_hexpand(True)
    box.set_vexpand(True)
    box.add_css_class("artgen-text-preview")

    if title:
        t = Gtk.Label(label=title)
        t.set_xalign(0)
        t.set_wrap(True)
        t.set_max_width_chars(20)
        t.set_lines(2)
        t.set_ellipsize(3)   # PANGO_ELLIPSIZE_END
        t.add_css_class("artgen-preview-title")
        box.append(t)

        # Thin teal rule under the title
        rule = Gtk.Box()
        rule.add_css_class("artgen-preview-rule")
        box.append(rule)

    if body:
        b = Gtk.Label(label=body)
        b.set_xalign(0)
        b.set_wrap(True)
        b.set_max_width_chars(20)
        b.set_lines(3)
        b.set_ellipsize(3)
        b.add_css_class("artgen-preview-body")
        box.append(b)

    return box


# ── ANSI ──────────────────────────────────────────────────────────────────────
#
# Parsing lives in `artgen_render.parse_ansi_grid` (media-showcase-everywhere
# Task 1/4) — this module used to carry its own bespoke escape-sequence
# walker + xterm-256 table (the THIRD copy of ANSI-parsing logic the design
# audit found, after artgen_detail/watch's and TT-TV attractor's). Deleted in
# favor of delegating to the shared parser below, so a fourth drift point
# can never appear.

_ANSI_CELL_DEFAULT: tuple[float, float, float] = (0.0, 0.0, 0.0)


def _parse_ansi_cells(
    text: str,
    max_cols: int = 100,
    max_rows: int = 50,
) -> list[tuple[int, int, tuple[float, float, float]]]:
    """
    Walk ANSI escape sequences and return (row, col, display_rgb) for every
    character cell, via the shared `artgen_render.parse_ansi_grid` — the
    single place that understands both the legacy bg+space format
    (`\\x1b[48;5;Nm `) and the current fg+block format the `ansi` generator
    actually emits (`\\x1b[38;5;Nm█`).

    Color resolution mirrors `artgen_render.ansi_to_html`: a space character
    uses the cell's background color; any other character uses the
    foreground color; an unset channel defaults to black. Cells are clipped
    to `max_cols`/`max_rows` (this widget only ever draws a small preview
    tile, unlike the full-viewport detail view).
    """
    grid = parse_ansi_grid(text)
    cells: list[tuple[int, int, tuple[float, float, float]]] = []
    for row_i, row in enumerate(grid):
        if row_i >= max_rows:
            break
        for col_i, (ch, fg, bg) in enumerate(row):
            if col_i >= max_cols:
                break
            color_hex = bg if ch == " " else fg
            color = _hex_to_rgb01(color_hex) if color_hex else _ANSI_CELL_DEFAULT
            cells.append((row_i, col_i, color))
    return cells


def _ansi_preview_widget(text: str) -> Gtk.DrawingArea:
    """
    Render ANSI escape sequences as a pixelated color-grid preview.
    Each character cell → one colored rectangle; characters are ignored —
    only background colors matter for the visual impression.
    """
    cells = _parse_ansi_cells(text)
    area = Gtk.DrawingArea()
    area.set_hexpand(True)
    area.set_vexpand(True)

    def draw(_widget, cr, w, h):
        # Black canvas
        cr.set_source_rgb(0.0, 0.0, 0.0)
        cr.paint()
        if not cells:
            return
        max_col = max(col for _, col, _ in cells) + 1
        max_row = max(row for row, _, _ in cells) + 1
        cw = w / max_col
        ch2 = h / max_row
        for row, col, bg in cells:
            cr.set_source_rgb(*bg)
            cr.rectangle(col * cw, row * ch2, cw + 0.5, ch2 + 0.5)
            cr.fill()

    area.set_draw_func(draw)
    return area


# ── Animated GIF card ─────────────────────────────────────────────────────────
# `_AnimatedGifWidget` moved to artgen_render.py (v0.48.0, media-showcase-
# everywhere Task 1) as the public `AnimatedGifWidget` -- imported and
# re-exported under this module's old private name above so every existing
# caller (create_view.py's result-panel gif branch, this module's own hover
# swap below, and the perf-regression / create-result-panel tests) is
# untouched.


# ── Dispatcher ────────────────────────────────────────────────────────────────

def make_card_content(rec: MediaRecord) -> Gtk.Widget:
    """
    Return the content widget for a card (the area above the bottom label bar).
    Priority: thumbnail PNG → SVG → palette JSON → ANSI color grid → text snippet → emoji.
    """
    fp = Path(rec.file_path) if rec.file_path else Path()
    ext = fp.suffix.lower()

    # Palette JSON: always render swatch grid — any stored thumbnail is just
    # a PIL text render of the raw JSON, which looks terrible.
    if ext == ".json" and fp.exists():
        try:
            raw = fp.read_text(encoding="utf-8", errors="replace")
            data = json.loads(raw)
            if data.get("colors"):
                return _palette_card_widget(data)
        except Exception:
            pass

    # ANSI: always render the colour-grid — any stored thumbnail is a PIL text
    # render of the raw escape codes, which looks terrible.
    if ext == ".ans" and fp.exists():
        try:
            raw = fp.read_text(encoding="utf-8", errors="replace")
            if raw.strip():
                return _ansi_preview_widget(raw)
        except Exception:
            pass

    # Animated GIF: show static thumbnail in gallery to avoid running 60+ timers
    # simultaneously.  The ArtgenCard hover handler swaps in an _AnimatedGifWidget
    # on enter and restores the thumbnail on leave.
    if ext == ".gif" and rec.thumbnail_path and Path(rec.thumbnail_path).exists():
        img = Gtk.Picture.new_for_filename(rec.thumbnail_path)
        img.set_content_fit(Gtk.ContentFit.COVER)
        return img
    if ext == ".gif" and fp.exists():
        # No thumbnail — render a genuinely STATIC first frame, NOT a live
        # _AnimatedGifWidget: the latter runs a GLib decode timer continuously
        # in the grid, contradicting the "avoid 60+ timers" note above (review
        # M4). Hover still swaps in a live animation.
        try:
            anim = GdkPixbuf.PixbufAnimation.new_from_file(str(fp))
            img = Gtk.Picture.new_for_paintable(
                Gdk.Texture.new_for_pixbuf(anim.get_static_image()))
            img.set_content_fit(Gtk.ContentFit.COVER)
            return img
        except Exception:
            return _AnimatedGifWidget(str(fp))  # unreadable: last-resort

    if rec.thumbnail_path and Path(rec.thumbnail_path).exists():
        img = Gtk.Picture.new_for_filename(rec.thumbnail_path)
        img.set_content_fit(Gtk.ContentFit.COVER)
        return img

    if ext == ".svg" and fp.exists():
        img = Gtk.Picture.new_for_filename(str(fp))
        img.set_content_fit(Gtk.ContentFit.COVER)
        return img

    if fp.exists():
        try:
            raw = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            raw = ""
    else:
        raw = ""

    if ext in (".txt", ".md") and raw.strip():
        return _text_preview_widget(raw)

    lbl = Gtk.Label(label=_TYPE_EMOJI.get(rec.generator_type or "", "✦"))
    lbl.add_css_class("artgen-card-placeholder")
    return lbl


_STARRED_FILTER = "__starred__"

# Natural height of the bottom badge/timestamp bar in _make_card (two
# single-line, non-wrapping Labels -- deterministic regardless of content).
# Measured empirically (xvfb) so the content zone above it can be pinned to
# exactly (tile_h - _BOTTOM_BAR_H), making the WHOLE card's measured size a
# true ceiling (== gallery_layout.TILE_H) instead of just a floor.
_BOTTOM_BAR_H = 24


class ArtgenGallery(Gtk.Box):
    """
    Full-width card grid with filter chips.

    on_card_activated(media_id: str)  — set before showing
    on_watch_requested(generator_type: str | None) — set before showing
    on_card_deleted(media_id: str)    — set before showing
    """

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.on_card_activated: Optional[Callable[[str], None]] = None
        self.on_watch_requested: Optional[Callable[[Optional[str]], None]] = None
        self.on_card_deleted: Optional[Callable[[str], None]] = None
        # Task 8 (remix-pipeline-unification): the parallel "🔀 Remix" popover
        # seam (`on_remix`) is gone — `on_remix_as_pipeline` is the single
        # remix affordance now, wired to the card's one remaining button.
        self.on_remix_as_pipeline: Optional[Callable[["MediaRecord"], None]] = None
        self._active_filter: Optional[str] = None  # None = All, "__starred__" = starred only
        self._records: list[MediaRecord] = []
        # Card tile size -- defaults to the SAME fixed tile as the native
        # video/image/animate galleries (gallery_layout.TILE_W/TILE_H), kept
        # in sync with gallery density via set_tile_size().
        self._tile_w = gallery_layout.TILE_W
        self._tile_h = gallery_layout.TILE_H
        self._build()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # Grid page: filter bar + separator + card grid. Unify-gallery-
        # interaction-pattern Task 3 removed the in-page ArtgenDetail overlay
        # that used to live alongside this -- see the removed Overlay/_show_
        # detail machinery this replaced (git history / CLAUDE.md) and
        # main_window.py's shared `_right_stack`, which now hosts ArtgenDetail
        # as a SIBLING subtree instead of stacking it over this grid.
        grid_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Filter bar
        filter_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        filter_bar.set_margin_start(12)
        filter_bar.set_margin_end(12)
        filter_bar.set_margin_top(8)
        filter_bar.set_margin_bottom(8)

        filter_lbl = Gtk.Label(label="Filter:")
        filter_lbl.add_css_class("muted")
        filter_bar.append(filter_lbl)

        self._chip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._chip_box.set_hexpand(True)
        filter_bar.append(self._chip_box)

        watch_btn = Gtk.Button(label="▶ Watch")
        watch_btn.add_css_class("artgen-watch-btn-bar")
        watch_btn.connect("clicked", self._on_watch_clicked)
        filter_bar.append(watch_btn)

        grid_page.append(filter_bar)
        grid_page.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Card grid
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        # FlowBox grid settings -- IDENTICAL to the native video/image/animate
        # galleries (GalleryWidget._flow in main_window.py), sourced from
        # gallery_layout.py so switching Discover tabs never changes the grid.
        # SelectionMode.NONE -- unify-gallery-interaction Task 6: click
        # handling moves OFF the FlowBox (which used to own it via
        # SelectionMode.SINGLE + "child-activated", a single-click-only
        # mechanism) and ONTO a per-card Gtk.GestureClick built in
        # _make_card, the SAME mechanism the native GenerationCard uses
        # (main_window.py's _on_pressed). This lets one gesture distinguish
        # single-click (select, via on_card_activated) from double-click
        # (open ArtgenViewerWindow) instead of needing two different signals.
        self._flow = Gtk.FlowBox()
        self._flow.set_max_children_per_line(gallery_layout.FLOW_MAX_CHILDREN_PER_LINE)
        self._flow.set_min_children_per_line(gallery_layout.FLOW_MIN_CHILDREN_PER_LINE)
        self._flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._flow.set_row_spacing(gallery_layout.FLOW_ROW_SPACING)
        self._flow.set_column_spacing(gallery_layout.FLOW_COLUMN_SPACING)
        self._flow.set_margin_start(12)
        self._flow.set_margin_end(12)
        self._flow.set_margin_top(8)
        self._flow.set_margin_bottom(8)
        scroll.set_child(self._flow)
        self._scroll = scroll
        grid_page.append(scroll)

        self.append(grid_page)

    # ── Public ────────────────────────────────────────────────────────────────

    def set_tile_size(self, width: int, height: int) -> None:
        """
        Resize every card to width x height and remember it for future cards.

        Mirrors MainWindow._apply_gallery_density's handling of the native
        video/image/animate galleries so switching gallery density resizes
        the artgen gallery identically instead of leaving it at a stale or
        differently-hardcoded size.

        `overlay.set_size_request(width, height)` alone is NOT enough to
        resize an already-built card: it only raises the outer Overlay's
        minimum-size FLOOR, and `Gtk.Widget.set_size_request()` can never
        shrink a widget below what its content already needs -- here, the
        pinned `content_zone` built at the OLD tile size (see `_make_card`)
        still dominates the measured size, so the card's true rendered size
        wouldn't change. The fix is to also resize `content_zone`'s pinned
        anchor in place via `gallery_layout.set_pinned_size()` (see its
        docstring), recomputing content_h exactly like `_make_card` does so
        the content zone + the fixed-height bottom badge/timestamp bar still
        sum to exactly `height`.
        """
        self._tile_w = width
        self._tile_h = height
        content_h = max(height - _BOTTOM_BAR_H, 1)
        child = self._flow.get_first_child()
        while child is not None:
            overlay = child.get_child()  # FlowBoxChild wraps our Gtk.Overlay
            if overlay is not None:
                overlay.set_size_request(width, height)
                content_zone = getattr(overlay, "_content_zone", None)
                if content_zone is not None:
                    gallery_layout.set_pinned_size(content_zone, width, content_h)
            child = child.get_next_sibling()

    def scroll_to_top(self) -> None:
        """Scroll the grid back to the top (call after prepending a new card)."""
        adj = self._scroll.get_vadjustment()
        if adj:
            adj.set_value(0)

    def refresh(self) -> None:
        """Reload records from the store and rebuild chips + grid."""
        self._records = _ms.query(media_type="artgen")
        self._rebuild_chips()
        self._rebuild_grid()

    def prepend_record(self, record: MediaRecord) -> None:
        """Insert one new card at the top-left without full refresh."""
        self._records.insert(0, record)
        card = self._make_card(record)
        self._flow.prepend(card)

    def remove_record(self, media_id: str) -> None:
        """Remove one record from the in-memory list and rebuild grid+chips.

        Mirrors `prepend_record`'s public, incremental-update style. Used by
        the grid's own hover-delete flow (`_make_card`'s `_delete_confirmed`)
        AND by `MainWindow` (unify-gallery-interaction-pattern Task 3) to
        sync the grid after the shared right-pane `ArtgenDetail`'s OWN 🗑
        deletes a record this grid doesn't otherwise know about.
        """
        self._records = [r for r in self._records if r.id != media_id]
        self._rebuild_grid()
        self._rebuild_chips()

    # ── Chips ─────────────────────────────────────────────────────────────────

    def _rebuild_chips(self) -> None:
        while child := self._chip_box.get_first_child():
            self._chip_box.remove(child)

        types = sorted({r.generator_type for r in self._records if r.generator_type})
        has_starred = any(r.starred for r in self._records)

        chips = [("All", None)]
        if has_starred:
            chips.append(("⭐ Starred", _STARRED_FILTER))
        chips += [(t, t) for t in types]

        for label, filt in chips:
            btn = Gtk.ToggleButton(label=label)
            btn.add_css_class("artgen-filter-chip")
            if filt == _STARRED_FILTER:
                btn.add_css_class("artgen-starred-chip")
            btn.set_active(filt == self._active_filter)
            btn.connect("toggled", self._on_chip_toggled, filt)
            self._chip_box.append(btn)

    def _on_chip_toggled(self, btn: Gtk.ToggleButton, filt: Optional[str]) -> None:
        if not btn.get_active():
            return
        self._active_filter = filt
        # Deactivate other chips
        child = self._chip_box.get_first_child()
        while child:
            if child is not btn and isinstance(child, Gtk.ToggleButton):
                child.set_active(False)
            child = child.get_next_sibling()
        self._rebuild_grid()

    # ── Grid ──────────────────────────────────────────────────────────────────

    def _filtered_records(self) -> list[MediaRecord]:
        """Return self._records narrowed by the active filter chip.

        Shared by _rebuild_grid (what the grid page shows) and
        MainWindow's _on_artgen_card_selected, which passes this list to
        ArtgenDetail.show_record as the nav list it steps through with
        ‹ / › -- so the two never disagree about which records are "in view".
        """
        if self._active_filter == _STARRED_FILTER:
            return [r for r in self._records if r.starred]
        return [r for r in self._records
                if self._active_filter is None or r.generator_type == self._active_filter]

    def _rebuild_grid(self) -> None:
        while child := self._flow.get_first_child():
            self._flow.remove(child)
        for rec in self._filtered_records():
            self._flow.append(self._make_card(rec))

    def _make_card(self, rec: MediaRecord) -> Gtk.Overlay:
        tile_w, tile_h = self._tile_w, self._tile_h
        overlay = Gtk.Overlay()
        overlay.set_size_request(tile_w, tile_h)
        overlay._media_id = rec.id  # stash for activation handler
        overlay.add_css_class("artgen-card")
        # Hard boundary so a hover-swapped animation or the revealed action bar
        # can't paint past the card and overlap neighbours (mirrors the native
        # GenerationCard; the content_zone below also clips via pin_fixed_zone).
        overlay.set_overflow(Gtk.Overflow.HIDDEN)

        # Base: art content + bottom bar.  The content widget's own natural
        # size otherwise follows the underlying artwork's aspect ratio (a
        # square 1024x1024 palette render vs. a 16:9 ANSI grid vs. a long
        # text preview each want a different height) -- overlay.set_size_
        # request above is only a MINIMUM, so without pinning, cards would
        # balloon per-content just like the pre-fix GenerationCard
        # (main_window.py) did.  gallery_layout.pin_fixed_zone caps the
        # content's MEASURED size to a fixed area (tile_h minus the bottom
        # badge/timestamp bar's own — constant, unwrapped-label — height) so
        # every artgen card reports the identical size_request regardless of
        # content, and matches the native galleries' tile size exactly.
        base = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content = make_card_content(rec)
        content_h = max(tile_h - _BOTTOM_BAR_H, 1)
        content_zone = gallery_layout.pin_fixed_zone(content, tile_w, content_h)
        overlay._content_zone = content_zone  # stashed so set_tile_size() can resize it in place
        base.append(content_zone)
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bottom.add_css_class("artgen-card-bottom")
        type_lbl = Gtk.Label(label=(rec.generator_type or "?")[:4])
        type_lbl.add_css_class("artgen-type-badge")
        bottom.append(type_lbl)
        ts = Gtk.Label(label=rec.created_at[5:10] if len(rec.created_at) >= 10 else "")
        ts.add_css_class("muted")
        ts.set_hexpand(True)
        ts.set_xalign(1.0)
        bottom.append(ts)
        base.append(bottom)
        overlay.set_child(base)

        # Hover overlay: star + delete buttons
        hover_rev = Gtk.Revealer()
        hover_rev.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        hover_rev.set_transition_duration(100)
        hover_rev.set_reveal_child(False)
        hover_rev.set_halign(Gtk.Align.END)
        hover_rev.set_valign(Gtk.Align.START)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        actions.set_margin_top(3)
        actions.set_margin_end(3)
        actions.add_css_class("artgen-card-hover-actions")

        star_btn = Gtk.Button(label="★" if rec.starred else "☆")
        star_btn.add_css_class("artgen-card-action-btn")
        star_btn.set_tooltip_text("Unstar" if rec.starred else "Star")

        def _on_star(_b, _rec=rec, _sb=star_btn):
            new = not bool(_rec.starred)
            _ms.star(_rec.id, new)
            _rec.starred = int(new)
            _sb.set_label("★" if new else "☆")
            _sb.set_tooltip_text("Unstar" if new else "Star")
            # Refresh chips so Starred chip appears/disappears as needed
            self._rebuild_chips()

        star_btn.connect("clicked", _on_star)
        actions.append(star_btn)

        del_btn = Gtk.Button(label="🗑")
        del_btn.add_css_class("artgen-card-action-btn")
        del_btn.set_tooltip_text("Delete")

        def _on_delete(_b, _rec=rec, _ov=overlay):
            dialog = Gtk.AlertDialog()
            dialog.set_message("Delete this artifact?")
            from time_utils import fmt_local_date
            dialog.set_detail(f"{_rec.generator_type} — {fmt_local_date(_rec.created_at)}")
            dialog.set_buttons(["Cancel", "Delete"])
            dialog.set_cancel_button(0)
            dialog.set_default_button(0)
            dialog.choose(_ov.get_root(), None, _delete_confirmed, _rec.id)

        def _delete_confirmed(dialog, result, media_id):
            try:
                btn_idx = dialog.choose_finish(result)
            except Exception:
                return
            if btn_idx != 1:
                return
            _ms.delete(media_id)
            self.remove_record(media_id)
            if self.on_card_deleted:
                self.on_card_deleted(media_id)

        del_btn.connect("clicked", _on_delete)
        actions.append(del_btn)

        # Single remix affordance (Task 8): opens Pipeline Studio's Muse
        # scoped to this artifact. The former parallel "🔀 Remix" popover
        # button is gone; this is relabeled to the canonical name.
        pipeline_btn = Gtk.Button(label="🔀 Remix")
        pipeline_btn.add_css_class("artgen-card-remix-btn")
        pipeline_btn.set_tooltip_text("Remix this into a pipeline")

        def _on_pipeline_seed(_b, _rec=rec):
            if self.on_remix_as_pipeline:
                self.on_remix_as_pipeline(_rec)

        pipeline_btn.connect("clicked", _on_pipeline_seed)
        actions.append(pipeline_btn)
        overlay._remix_as_pipeline_btn = pipeline_btn  # stashed for test access

        hover_rev.set_child(actions)
        overlay.add_overlay(hover_rev)

        # Show/hide hover actions via motion controller.
        # For GIF cards: swap in animated widget on enter, restore thumb on leave.
        fp = Path(rec.file_path) if rec.file_path else Path()
        _is_gif = fp.suffix.lower() == ".gif" and fp.exists()
        _thumb_path = rec.thumbnail_path if (rec.thumbnail_path and Path(rec.thumbnail_path).exists()) else None
        # Tracks the CURRENT overlay child of content_zone (a 1-element list so
        # the nested closures below can mutate it).  Swapping content in/out of
        # content_zone itself (remove_overlay/add_overlay) -- rather than
        # ripping content_zone out of `base` and replacing it with a bare,
        # unpinned widget as this used to do -- keeps the fixed-size pin
        # (gallery_layout.pin_fixed_zone) intact across hover in/out, so a
        # hovered GIF card can't grow/shrink the tile either.
        _zone_content = [content]

        def _swap_zone_content(new_widget: Gtk.Widget) -> None:
            # CRASH FIX (v0.48.4): the actual overlay mutation is DEFERRED to an
            # idle callback. `remove_overlay(old)` unparents (and, since nothing
            # else holds a ref, FREES) the outgoing widget. Doing that free while
            # GTK is still mid signal-dispatch on this card -- which is exactly
            # when the motion "enter"/"leave" and click handlers run -- leaves a
            # dangling widget pointer in GTK's in-flight layout/event machinery,
            # so a subsequent `gtk_widget_compute_point` dereferences freed
            # memory: `assertion 'GTK_IS_WIDGET (widget)' failed` then a
            # nondeterministic SEGFAULT (reproduced: hover an AnimateDiff GIF
            # card -> live-gif swap -> click -> crash). Running the swap at idle
            # lets the current dispatch fully unwind before anything is freed.
            new_widget.set_hexpand(True)
            new_widget.set_vexpand(True)
            new_widget.set_halign(Gtk.Align.FILL)
            new_widget.set_valign(Gtk.Align.FILL)

            def _do_swap() -> bool:
                # Re-check at idle time: the card may have been detached (grid
                # rebuilt) or already swapped to this very widget in between.
                if content_zone.get_parent() is None or _zone_content[0] is None:
                    # Card detached (grid rebuilt) before this idle ran:
                    # new_widget was never attached, so it will never be
                    # realized/unrealized — cancel its animation timer now or
                    # it leaks forever (review I3).
                    if hasattr(new_widget, "cancel_animation"):
                        new_widget.cancel_animation()
                    return False
                old = _zone_content[0]
                if old is new_widget:
                    return False
                # add BEFORE remove so the zone always has a child, then free
                # the outgoing widget now that no dispatch is on the stack.
                content_zone.add_overlay(new_widget)
                content_zone.remove_overlay(old)
                _zone_content[0] = new_widget
                return False

            GLib.idle_add(_do_swap)

        def _enter_card(*_):
            hover_rev.set_reveal_child(True)
            if _is_gif and _thumb_path:
                anim = _AnimatedGifWidget(str(fp))
                _swap_zone_content(anim)

        def _leave_card(*_):
            hover_rev.set_reveal_child(False)
            if _is_gif and _thumb_path:
                still = Gtk.Picture.new_for_filename(_thumb_path)
                still.set_content_fit(Gtk.ContentFit.CONTAIN)
                _swap_zone_content(still)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", _enter_card)
        motion.connect("leave", _leave_card)
        overlay.add_controller(motion)

        # Primary click gesture -- unify-gallery-interaction Task 6. ONE
        # Gtk.GestureClick per card handles both single- and double-click,
        # the same mechanism native GenerationCard uses
        # (main_window.py's _on_pressed): a single click (any press) selects
        # the card into the shared right-pane detail view via
        # on_card_activated (the FlowBox itself no longer does this --
        # SelectionMode.NONE above), and a double click (n_press == 2) ALSO
        # opens the artifact full-screen in ArtgenViewerWindow, mirroring
        # GenerationCard's VideoPlayerWindow/ImageViewerWindow double-click
        # branch. Guarded the same way: only opens if the artifact's file
        # actually exists on disk (record.video_exists/image_exists there;
        # a plain Path.exists() check here since MediaRecord has no such
        # property).
        click = Gtk.GestureClick()
        click.set_button(1)

        def _on_pressed(_gesture, n_press, _x, _y, _rec=rec, _ov=overlay):
            if self.on_card_activated:
                self.on_card_activated(_rec.id)
            if n_press != 2:
                return
            fp = Path(_rec.file_path) if _rec.file_path else Path()
            if not fp.exists():
                return
            win = ArtgenViewerWindow(_rec, _ov.get_root())
            win.present()

        click.connect("pressed", _on_pressed)
        overlay.add_controller(click)

        return overlay

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_watch_clicked(self, _btn) -> None:
        if self.on_watch_requested:
            self.on_watch_requested(self._active_filter)
