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
from gi.repository import Gio, GLib, Gtk

from media_store import media_store as _ms, MediaRecord


# ── Rich card content builders ────────────────────────────────────────────────

_TYPE_EMOJI: dict[str, str] = {
    "landscape": "🏔", "skyline": "🌃", "verse": "✍",
    "constellation": "✦", "geometric": "⬡", "circuit": "⬟",
    "palette": "◼", "ansi": "▓", "freeform": "?",
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

# xterm-256 color table: indices 0-255 → (r, g, b) in 0-255 range
def _build_xterm256() -> list[tuple[int, int, int]]:
    # 0-15: system colors (standard + bright)
    sys16 = [
        (0,0,0),(170,0,0),(0,170,0),(170,85,0),(0,0,170),(170,0,170),(0,170,170),(170,170,170),
        (85,85,85),(255,85,85),(85,255,85),(255,255,85),(85,85,255),(255,85,255),(85,255,255),(255,255,255),
    ]
    table: list[tuple[int,int,int]] = list(sys16)
    # 16-231: 6×6×6 color cube
    for r6 in range(6):
        for g6 in range(6):
            for b6 in range(6):
                def cv(x: int) -> int: return 0 if x == 0 else 55 + x * 40
                table.append((cv(r6), cv(g6), cv(b6)))
    # 232-255: grayscale
    for k in range(24):
        v = 8 + k * 10
        table.append((v, v, v))
    return table

_XTERM256 = _build_xterm256()


def _xterm_rgb01(n: int) -> tuple[float, float, float]:
    r, g, b = _XTERM256[max(0, min(255, n))]
    return r / 255, g / 255, b / 255


def _parse_ansi_cells(
    text: str,
    max_cols: int = 100,
    max_rows: int = 50,
) -> list[tuple[int, int, tuple[float,float,float]]]:
    """
    Walk ANSI escape sequences and return (row, col, bg_rgb) for every
    character cell that has a non-default background color set.
    Handles: SGR 0 (reset), 30-37/90-97 fg, 40-47/100-107 bg,
             38;5;N / 48;5;N (256-color), 38;2;R;G;B / 48;2;R;G;B (truecolor).
    """
    DEFAULT_BG: tuple[float,float,float] = (0.0, 0.0, 0.0)
    bg: tuple[float,float,float] = DEFAULT_BG
    row = col = 0
    cells: list[tuple[int,int,tuple[float,float,float]]] = []
    i = 0

    # Normalise escape variants to actual ESC byte (handles files saved before fix).
    # Must happen before n = len(text) — normalisation shrinks the string.
    if "\x1b" not in text:
        import re as _re
        text = text.replace("\\033", "\x1b").replace("\\x1b", "\x1b").replace("\\e", "\x1b").replace("^[", "\x1b")
        text = _re.sub(r"(?<![\\x\d])033\[", "\x1b[", text)

    n = len(text)
    while i < n:
        ch = text[i]

        if ch == "\x1b" and i + 1 < n and text[i + 1] == "[":
            # Find end of CSI sequence
            j = i + 2
            while j < n and text[j] not in "ABCDEFGHJKSTfm":
                j += 1
            if j < n and text[j] == "m":
                parts = text[i + 2 : j].split(";")
                nums: list[int] = []
                for p in parts:
                    try:
                        nums.append(int(p))
                    except ValueError:
                        nums.append(0)
                # Process SGR parameters
                k = 0
                while k < len(nums):
                    v = nums[k]
                    if v == 0:
                        bg = DEFAULT_BG
                    elif 40 <= v <= 47:
                        bg = _xterm_rgb01(v - 40)
                    elif 100 <= v <= 107:
                        bg = _xterm_rgb01(v - 100 + 8)
                    elif v == 48:
                        if k + 1 < len(nums) and nums[k + 1] == 5 and k + 2 < len(nums):
                            bg = _xterm_rgb01(nums[k + 2])
                            k += 2
                        elif k + 1 < len(nums) and nums[k + 1] == 2 and k + 4 < len(nums):
                            bg = (nums[k+2]/255, nums[k+3]/255, nums[k+4]/255)
                            k += 4
                    k += 1
            i = j + 1

        elif ch == "\r":
            col = 0
            i += 1
        elif ch == "\n":
            row += 1
            col = 0
            i += 1
        else:
            if row < max_rows and col < max_cols:
                cells.append((row, col, bg))
            col += 1
            i += 1

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


class ArtgenGallery(Gtk.Box):
    """
    Full-width card grid with filter chips.

    on_card_activated(media_id: str)  — set before showing
    on_watch_requested(generator_type: str | None) — set before showing
    """

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.on_card_activated: Optional[Callable[[str], None]] = None
        self.on_watch_requested: Optional[Callable[[Optional[str]], None]] = None
        self._active_filter: Optional[str] = None  # None = All
        self._records: list[MediaRecord] = []
        self._build()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
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

        self.append(filter_bar)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Card grid
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._flow = Gtk.FlowBox()
        self._flow.set_max_children_per_line(8)
        self._flow.set_min_children_per_line(3)
        self._flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._flow.set_homogeneous(True)
        self._flow.set_row_spacing(6)
        self._flow.set_column_spacing(6)
        self._flow.set_margin_start(12)
        self._flow.set_margin_end(12)
        self._flow.set_margin_top(8)
        self._flow.set_margin_bottom(8)
        self._flow.connect("child-activated", self._on_card_activated)
        scroll.set_child(self._flow)
        self.append(scroll)

    # ── Public ────────────────────────────────────────────────────────────────

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

    # ── Chips ─────────────────────────────────────────────────────────────────

    def _rebuild_chips(self) -> None:
        while child := self._chip_box.get_first_child():
            self._chip_box.remove(child)

        types = sorted({r.generator_type for r in self._records if r.generator_type})
        for label, filt in [("All", None)] + [(t, t) for t in types]:
            btn = Gtk.ToggleButton(label=label)
            btn.add_css_class("artgen-filter-chip")
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

    def _rebuild_grid(self) -> None:
        while child := self._flow.get_first_child():
            self._flow.remove(child)
        filtered = [r for r in self._records
                    if self._active_filter is None or r.generator_type == self._active_filter]
        for rec in filtered:
            self._flow.append(self._make_card(rec))

    def _make_card(self, rec: MediaRecord) -> Gtk.Box:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_size_request(110, 90)
        outer._media_id = rec.id  # stash for activation handler
        outer.add_css_class("artgen-card")

        content = make_card_content(rec)
        content.set_hexpand(True)
        content.set_vexpand(True)
        outer.append(content)

        # Bottom label: type badge + timestamp
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
        outer.append(bottom)
        return outer

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_card_activated(self, _flow, child) -> None:
        box = child.get_child()
        if box and hasattr(box, "_media_id") and self.on_card_activated:
            self.on_card_activated(box._media_id)

    def _on_watch_clicked(self, _btn) -> None:
        if self.on_watch_requested:
            self.on_watch_requested(self._active_filter)
