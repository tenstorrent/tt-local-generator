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

from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from media_store import media_store as _ms, MediaRecord


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

        # Thumbnail or type placeholder
        if rec.thumbnail_path and Path(rec.thumbnail_path).exists():
            img = Gtk.Picture.new_for_filename(rec.thumbnail_path)
            img.set_content_fit(Gtk.ContentFit.COVER)
        elif rec.file_path.endswith(".svg") and Path(rec.file_path).exists():
            img = Gtk.Picture.new_for_filename(rec.file_path)
            img.set_content_fit(Gtk.ContentFit.COVER)
        else:
            img = Gtk.Label(label=self._type_emoji(rec.generator_type))
            img.add_css_class("artgen-card-placeholder")

        img.set_hexpand(True)
        img.set_vexpand(True)
        outer.append(img)

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

    @staticmethod
    def _type_emoji(gt: Optional[str]) -> str:
        return {"landscape": "🏔", "skyline": "🌃", "verse": "✍",
                "constellation": "✦", "geometric": "⬡", "circuit": "⬟",
                "palette": "#", "ansi": "▓", "freeform": "?"}.get(gt or "", "✦")

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_card_activated(self, _flow, child) -> None:
        box = child.get_child()
        if box and hasattr(box, "_media_id") and self.on_card_activated:
            self.on_card_activated(box._media_id)

    def _on_watch_clicked(self, _btn) -> None:
        if self.on_watch_requested:
            self.on_watch_requested(self._active_filter)
