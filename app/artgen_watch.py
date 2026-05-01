#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
ArtgenWatch — fullscreen-within-pane slideshow.

Fills the artgen tab's content area. The sub-navigation header is hidden
by the parent (ArtgenPanel) while Watch is active.

Callbacks:
    on_exit()           — user pressed Esc / ← Gallery
    on_deleted(id: str) — user deleted current item
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
try:
    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit as _WebKit
    _WEBKIT_OK = True
except Exception:
    _WEBKIT_OK = False
from gi.repository import Gdk, Gio, GLib, Gtk

from artgen_detail import _ansi_to_html
from media_store import media_store as _ms, MediaRecord

_DWELL_DEFAULT = 10   # seconds between auto-advances


class ArtgenWatch(Gtk.Overlay):
    """Slideshow overlay: artifact fills the pane, UI overlay fades on idle."""

    def __init__(self) -> None:
        super().__init__()
        self.on_exit: Optional[Callable[[], None]] = None
        self.on_deleted: Optional[Callable[[str], None]] = None
        self._records: list[MediaRecord] = []
        self._idx: int = 0
        self._playing: bool = True
        self._dwell: int = _DWELL_DEFAULT
        self._countdown: int = _DWELL_DEFAULT
        self._advance_timer: int | None = None
        self._countdown_timer: int | None = None
        self._overlay_visible: bool = True
        self._hide_timer: int | None = None
        self._build()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.set_hexpand(True)
        self.set_vexpand(True)

        # Background: artifact display
        self._bg = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._bg.set_hexpand(True)
        self._bg.set_vexpand(True)
        self._bg.add_css_class("artgen-watch-bg")

        self._art_stack = Gtk.Stack()
        self._art_stack.set_hexpand(True)
        self._art_stack.set_vexpand(True)
        self._art_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._art_stack.set_transition_duration(400)

        self._svg_pic = Gtk.Picture()
        self._svg_pic.set_hexpand(True)
        self._svg_pic.set_vexpand(True)
        self._svg_pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._art_stack.add_named(self._svg_pic, "svg")

        text_scroll = Gtk.ScrolledWindow()
        text_scroll.set_hexpand(True)
        text_scroll.set_vexpand(True)
        self._text_view = Gtk.TextView()
        self._text_view.set_editable(False)
        self._text_view.set_monospace(True)
        self._text_view.set_cursor_visible(False)
        self._text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._text_view.set_margin_start(60)
        self._text_view.set_margin_end(60)
        self._text_view.set_margin_top(40)
        text_scroll.set_child(self._text_view)
        self._art_stack.add_named(text_scroll, "text")

        if _WEBKIT_OK:
            self._ansi_web = _WebKit.WebView()
            self._ansi_web.set_hexpand(True)
            self._ansi_web.set_vexpand(True)
            self._art_stack.add_named(self._ansi_web, "ansi")
        else:
            self._ansi_web = None

        self._bg.append(self._art_stack)
        self.set_child(self._bg)

        # Overlay: controls
        overlay_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        overlay_box.set_hexpand(True)
        overlay_box.set_vexpand(True)
        overlay_box.add_css_class("artgen-watch-overlay")

        # Top bar
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top.set_margin_start(16)
        top.set_margin_end(16)
        top.set_margin_top(10)
        top.add_css_class("artgen-watch-top")

        back_btn = Gtk.Button(label="← Gallery")
        back_btn.add_css_class("flat")
        back_btn.add_css_class("artgen-watch-btn")
        back_btn.connect("clicked", lambda _: self._exit())
        top.append(back_btn)

        self._pos_lbl = Gtk.Label(label="")
        self._pos_lbl.set_hexpand(True)
        self._pos_lbl.set_xalign(0.5)
        self._pos_lbl.add_css_class("artgen-watch-pos")
        top.append(self._pos_lbl)

        close_btn = Gtk.Button(label="✕")
        close_btn.add_css_class("flat")
        close_btn.add_css_class("artgen-watch-btn")
        close_btn.connect("clicked", lambda _: self._exit())
        top.append(close_btn)
        overlay_box.append(top)

        # Spacer (fills middle)
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        overlay_box.append(spacer)

        # Bottom bar
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bottom.set_margin_start(16)
        bottom.set_margin_end(16)
        bottom.set_margin_bottom(10)
        bottom.add_css_class("artgen-watch-bottom")

        self._meta_lbl = Gtk.Label(label="")
        self._meta_lbl.add_css_class("artgen-watch-meta")
        self._meta_lbl.set_hexpand(True)
        self._meta_lbl.set_xalign(0)
        bottom.append(self._meta_lbl)

        progress_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._progress = Gtk.ProgressBar()
        self._progress.set_size_request(200, -1)
        progress_col.append(self._progress)

        self._dwell_lbl = Gtk.Label(label="")
        self._dwell_lbl.add_css_class("artgen-watch-meta")
        self._dwell_lbl.set_xalign(0.5)
        progress_col.append(self._dwell_lbl)
        bottom.append(progress_col)

        self._play_btn = Gtk.Button(label="⏸")
        self._play_btn.add_css_class("artgen-watch-btn")
        self._play_btn.connect("clicked", self._on_play_pause)
        bottom.append(self._play_btn)

        self._star_btn = Gtk.Button(label="☆")
        self._star_btn.add_css_class("artgen-watch-btn")
        self._star_btn.set_tooltip_text("Star this artifact  [S]")
        self._star_btn.connect("clicked", lambda _: self._toggle_star())
        bottom.append(self._star_btn)

        overlay_box.append(bottom)
        self.add_overlay(overlay_box)

        # Left / right nav buttons (centered vertically)
        left_btn = Gtk.Button(label="‹")
        left_btn.add_css_class("artgen-watch-nav-btn")
        left_btn.set_valign(Gtk.Align.CENTER)
        left_btn.set_halign(Gtk.Align.START)
        left_btn.set_margin_start(12)
        left_btn.connect("clicked", lambda _: self._step(-1))
        self.add_overlay(left_btn)

        right_btn = Gtk.Button(label="›")
        right_btn.add_css_class("artgen-watch-nav-btn")
        right_btn.set_valign(Gtk.Align.CENTER)
        right_btn.set_halign(Gtk.Align.END)
        right_btn.set_margin_end(12)
        right_btn.connect("clicked", lambda _: self._step(1))
        self.add_overlay(right_btn)

        # Mouse motion → show overlay
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

        # Keyboard shortcuts
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self.add_controller(key)

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self, records: list[MediaRecord], start_idx: int = 0) -> None:
        self._records = records
        self._idx = start_idx
        self._playing = True
        self._countdown = self._dwell
        self._render()
        self._start_timers()

    def stop(self) -> None:
        self._stop_timers()

    # ── Render ────────────────────────────────────────────────────────────────

    def _render(self) -> None:
        if not self._records:
            return
        rec = self._records[self._idx]
        n = len(self._records)
        self._pos_lbl.set_label(f"{self._idx+1} / {n}")

        p = rec.params_dict
        self._meta_lbl.set_label(
            f"{rec.generator_type or '?'} · "
            + " · ".join(str(v) for k, v in p.items()
                         if k in ("palette", "theme", "form", "style", "subject", "era")
                         and isinstance(v, str))
            + f" · {rec.created_at[:10]}"
        )

        self._star_btn.set_label("★" if rec.starred else "☆")
        self._star_btn.set_tooltip_text("Unstar  [S]" if rec.starred else "Star this artifact  [S]")

        fp = Path(rec.file_path)
        ext = fp.suffix.lower()
        if ext == ".svg" and fp.exists():
            self._svg_pic.set_file(Gio.File.new_for_path(str(fp)))
            self._art_stack.set_visible_child_name("svg")
        elif ext == ".ans" and fp.exists() and self._ansi_web is not None:
            raw = fp.read_text(encoding="utf-8", errors="replace")
            html = _ansi_to_html(raw)
            self._ansi_web.load_html(html, "file:///")
            self._art_stack.set_visible_child_name("ansi")
        else:
            text = fp.read_text(encoding="utf-8", errors="replace") if fp.exists() else ""
            self._text_view.get_buffer().set_text(text)
            self._art_stack.set_visible_child_name("text")

    # ── Timers ────────────────────────────────────────────────────────────────

    def _start_timers(self) -> None:
        self._stop_timers()
        if self._playing:
            self._countdown = self._dwell
            self._advance_timer = GLib.timeout_add_seconds(self._dwell, self._auto_advance)
            self._countdown_timer = GLib.timeout_add(1000, self._tick_countdown)

    def _stop_timers(self) -> None:
        for attr in ("_advance_timer", "_countdown_timer", "_hide_timer"):
            tid = getattr(self, attr, None)
            if tid is not None:
                GLib.source_remove(tid)
                setattr(self, attr, None)

    def _auto_advance(self) -> bool:
        self._step(1)
        return GLib.SOURCE_REMOVE

    def _tick_countdown(self) -> bool:
        self._countdown = max(0, self._countdown - 1)
        frac = self._countdown / self._dwell if self._dwell else 0
        self._progress.set_fraction(frac)
        self._dwell_lbl.set_label(f"{self._countdown}s")
        return GLib.SOURCE_CONTINUE

    # ── Navigation ────────────────────────────────────────────────────────────

    def _step(self, delta: int) -> None:
        if not self._records:
            return
        self._idx = (self._idx + delta) % len(self._records)
        self._render()
        if self._playing:
            self._start_timers()

    def _exit(self) -> None:
        self.stop()
        if self.on_exit:
            self.on_exit()

    # ── Overlay visibility ────────────────────────────────────────────────────

    def _show_overlay(self) -> None:
        self._overlay_visible = True
        if self._hide_timer is not None:
            GLib.source_remove(self._hide_timer)
        self._hide_timer = GLib.timeout_add_seconds(3, self._hide_overlay)

    def _hide_overlay(self) -> bool:
        self._overlay_visible = False
        self._hide_timer = None
        return GLib.SOURCE_REMOVE

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_motion(self, _ctrl, _x, _y) -> None:
        self._show_overlay()

    def _on_play_pause(self, _btn) -> None:
        self._playing = not self._playing
        self._play_btn.set_label("⏸" if self._playing else "▶")
        if self._playing:
            self._start_timers()
        else:
            self._stop_timers()

    def _on_key(self, _ctrl, keyval, _keycode, _state) -> bool:
        self._show_overlay()
        if keyval == Gdk.KEY_Escape:
            self._exit()
            return True
        if keyval == Gdk.KEY_Left:
            self._step(-1)
            return True
        if keyval == Gdk.KEY_Right:
            self._step(1)
            return True
        if keyval == Gdk.KEY_space:
            self._on_play_pause(None)
            return True
        if keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self._toggle_star()
            return True
        if keyval == Gdk.KEY_Delete:
            self._delete_current()
            return True
        return False

    def _toggle_star(self) -> None:
        if not self._records:
            return
        rec = self._records[self._idx]
        starred = not bool(rec.starred)
        _ms.star(rec.id, starred)
        rec.starred = int(starred)
        self._star_btn.set_label("★" if starred else "☆")
        self._star_btn.set_tooltip_text("Unstar  [S]" if starred else "Star this artifact  [S]")

    def _delete_current(self) -> None:
        if not self._records:
            return
        rec = self._records[self._idx]
        dialog = Gtk.AlertDialog()
        dialog.set_message("Delete this artifact?")
        dialog.set_buttons(["Cancel", "Delete"])
        dialog.set_cancel_button(0)
        dialog.choose(self.get_root(), None, self._delete_confirmed, rec.id)

    def _delete_confirmed(self, dialog, result, media_id: str) -> None:
        try:
            if dialog.choose_finish(result) != 1:
                return
        except Exception:
            return
        _ms.delete(media_id)
        self._records = [r for r in self._records if r.id != media_id]
        if self.on_deleted:
            self.on_deleted(media_id)
        if not self._records:
            self._exit()
        else:
            self._idx = min(self._idx, len(self._records) - 1)
            self._render()
            if self._playing:
                self._start_timers()
