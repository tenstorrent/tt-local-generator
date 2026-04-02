#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Attractor Mode — self-sustaining kiosk that cycles generated media and
continuously queues new generations.

Classes:
    AttractorPool  — pure pool/shuffle logic (no GTK, testable)
    AttractorWindow — GTK4 kiosk window
"""
from __future__ import annotations

import random
import statistics
import threading
from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

import prompt_client  # noqa: E402


class AttractorPool:
    """
    Manages the shuffled playback order for a growing list of media records.

    Records are played in a shuffled order. When the cycle is exhausted the
    pool reshuffles, but the first item of the new cycle is never the same as
    the last item of the previous cycle.  New records added mid-cycle are
    inserted at a random position *after* the current playback position so
    they appear later in the current cycle rather than immediately next.
    """

    def __init__(self, records: list) -> None:
        self._records: list = list(records)
        self._order: list[int] = []
        self._pos: int = 0
        self._last_idx: int | None = None
        self._shuffle_fresh()
        self._recalc_duration()

    # ── public ────────────────────────────────────────────────────────────

    def advance(self) -> int:
        """
        Move to the next item and return its index into self._records.
        Reshuffles automatically at end of cycle.
        """
        if self._pos >= len(self._order):
            self._shuffle_fresh()
        idx = self._order[self._pos]
        self._last_idx = idx
        self._pos += 1
        return idx

    def current_record(self):
        """Return the record most recently returned by advance()."""
        if self._last_idx is None:
            raise RuntimeError("current_record() called before advance()")
        return self._records[self._last_idx]

    def add_record(self, record) -> None:
        """
        Append a new record and insert its index at a random position after
        the current playback position in _order.
        """
        new_idx = len(self._records)
        self._records.append(record)
        # Insert at any position strictly after the current pos so the new record
        # doesn't play immediately next.  If _pos is already at or past the end of
        # _order (cycle about to reshuffle), the only valid slot is the end.
        lower = min(self._pos + 1, len(self._order))
        insert_at = random.randint(lower, len(self._order))
        self._order.insert(insert_at, new_idx)
        self._recalc_duration()

    @property
    def avg_video_duration(self) -> float:
        """Mean duration of video records, or 8.0 s if none exist."""
        return self._avg_dur

    @property
    def size(self) -> int:
        return len(self._records)

    # ── private ───────────────────────────────────────────────────────────

    def _shuffle_fresh(self) -> None:
        order = list(range(len(self._records)))
        random.shuffle(order)
        # Avoid placing last-played item first in new cycle
        if self._last_idx is not None and order and order[0] == self._last_idx:
            if len(order) > 1:
                order[0], order[1] = order[1], order[0]
        self._order = order
        self._pos = 0

    def _recalc_duration(self) -> None:
        durations = [
            r.duration_s for r in self._records
            if r.media_type == "video" and r.duration_s > 0
        ]
        self._avg_dur = statistics.mean(durations) if durations else 8.0


# ---------------------------------------------------------------------------
# CSS — registered by AttractorWindow on first instantiation.
# Uses @define-color variables already declared in main_window.py's stylesheet.
# ---------------------------------------------------------------------------

_CSS = b"""
/* Attractor sidebar */
.attractor-sidebar {
    background-color: @tt_bg_darkest;
    border-right: 1px solid @tt_border;
    padding: 10px 8px;
}
.attractor-header {
    color: @tt_accent;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
}
.attractor-stat-lbl {
    color: @tt_text_muted;
    font-size: 10px;
}
.attractor-status-lbl {
    color: @tt_accent_light;
    font-size: 10px;
    font-style: italic;
}
.attractor-prompt-lbl {
    color: @tt_text_muted;
    font-size: 10px;
    font-style: italic;
}
.attractor-stop-btn {
    background-color: #2D1A1A;
    color: @tt_error;
    border: 1px solid @tt_error;
    border-radius: 4px;
    padding: 5px 8px;
    font-size: 11px;
}
.attractor-stop-btn:hover {
    background-color: @tt_border;
}
/* HUD overlay strip */
.attractor-hud {
    background: linear-gradient(transparent, rgba(0,0,0,0.55));
    padding: 20px 12px 8px;
}
.attractor-hud-lbl {
    color: rgba(232,240,242,0.55);
    font-size: 10px;
    font-style: italic;
}
"""


# ---------------------------------------------------------------------------
# AttractorWindow
# ---------------------------------------------------------------------------

class AttractorWindow(Gtk.Window):
    """
    Kiosk window: narrow sidebar with live status + A/B crossfading media player.

    All communication with MainWindow is through constructor callbacks — this
    class has no imports from main_window.py.

    Keyboard shortcuts:
        Escape  — close window (stops generation loop)
        F       — toggle fullscreen
        Space   — pause/resume playback (generation loop continues)
    """

    def __init__(
        self,
        records: list,                        # initial pool from HistoryStore.all_records()
        system_prompt: str,                   # contents of prompts/prompt_generator.md
        model_source: str,                    # "video" | "image" | "animate"
        on_enqueue: Callable,                 # MainWindow._on_enqueue compatible signature
        get_queue_depth: Callable[[], int],   # returns len(self._queue) in MainWindow
    ) -> None:
        super().__init__(title="Attractor Mode")
        self._system_prompt = system_prompt
        self._model_source = model_source
        self._on_enqueue = on_enqueue
        self._get_queue_depth = get_queue_depth
        self._pool = AttractorPool(records)
        self._gen_stop = threading.Event()
        self._paused = False
        self._pending_advance_source: int | None = None  # GLib source id

        # Load CSS (uses @define-color variables already loaded by main_window.py)
        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self._build()
        self.maximize()

        # Keyboard shortcuts
        ctrl = Gtk.EventControllerKey()
        ctrl.connect("key-pressed", self._on_key)
        self.add_controller(ctrl)

        self.connect("destroy", self._on_destroy)

    # ── Event handlers ────────────────────────────────────────────────────

    def _on_key(self, _ctrl, keyval, _keycode, _state) -> bool:
        name = Gtk.accelerator_name(keyval, 0)
        if name == "Escape":
            self.close()
            return True
        if name == "f":
            if self.is_fullscreen():
                self.unfullscreen()
            else:
                self.fullscreen()
            return True
        if name == "space":
            self._toggle_pause()
            return True
        return False

    def _on_destroy(self, _win) -> None:
        """Stop the generation loop and cancel any pending advance timer."""
        self._gen_stop.set()
        if self._pending_advance_source is not None:
            GLib.source_remove(self._pending_advance_source)
            self._pending_advance_source = None

    def _toggle_pause(self) -> None:
        """Toggle playback pause. Generation loop is unaffected."""
        self._paused = not self._paused
        stream = self._get_current_video_stream()
        if stream:
            if self._paused:
                stream.pause()
            else:
                stream.play()

    # ── Layout ────────────────────────────────────────────────────────────

    def _build(self) -> None:
        """Build the kiosk layout: sidebar on left, media player on right."""
        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.set_child(outer)

        # ── Sidebar ───────────────────────────────────────────────────────
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sidebar.add_css_class("attractor-sidebar")
        sidebar.set_size_request(148, -1)

        hdr = Gtk.Label(label="✦ ATTRACTOR MODE")
        hdr.add_css_class("attractor-header")
        hdr.set_xalign(0)
        sidebar.append(hdr)

        sidebar.append(_hdivider())

        self._status_lbl = Gtk.Label(label="⬤  running")
        self._status_lbl.add_css_class("attractor-stat-lbl")
        self._status_lbl.set_xalign(0)
        sidebar.append(self._status_lbl)

        self._queue_lbl = Gtk.Label(label="⏳  queue: —")
        self._queue_lbl.add_css_class("attractor-stat-lbl")
        self._queue_lbl.set_xalign(0)
        sidebar.append(self._queue_lbl)

        self._pool_lbl = Gtk.Label(label=f"🎬  pool: {self._pool.size}")
        self._pool_lbl.add_css_class("attractor-stat-lbl")
        self._pool_lbl.set_xalign(0)
        sidebar.append(self._pool_lbl)

        sidebar.append(_hdivider())

        self._gen_status_lbl = Gtk.Label(label="Starting…")
        self._gen_status_lbl.add_css_class("attractor-status-lbl")
        self._gen_status_lbl.set_xalign(0)
        self._gen_status_lbl.set_wrap(True)
        sidebar.append(self._gen_status_lbl)

        self._prompt_lbl = Gtk.Label(label="")
        self._prompt_lbl.add_css_class("attractor-prompt-lbl")
        self._prompt_lbl.set_xalign(0)
        self._prompt_lbl.set_wrap(True)
        self._prompt_lbl.set_lines(5)
        self._prompt_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        sidebar.append(self._prompt_lbl)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        sidebar.append(spacer)

        stop_btn = Gtk.Button(label="■  Stop")
        stop_btn.add_css_class("attractor-stop-btn")
        stop_btn.connect("clicked", lambda _: self.close())
        sidebar.append(stop_btn)

        outer.append(sidebar)

        # ── Media player ──────────────────────────────────────────────────
        player_overlay = Gtk.Overlay()
        player_overlay.set_hexpand(True)
        player_overlay.set_vexpand(True)

        # A/B Stack with crossfade
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(500)
        self._stack.set_hexpand(True)
        self._stack.set_vexpand(True)

        self._slot_a = self._make_slot()
        self._slot_b = self._make_slot()
        self._stack.add_named(self._slot_a, "a")
        self._stack.add_named(self._slot_b, "b")
        self._stack.set_visible_child_name("a")
        self._active_slot_name = "a"

        player_overlay.set_child(self._stack)

        # HUD strip overlaid at the bottom
        hud = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hud.add_css_class("attractor-hud")
        hud.set_valign(Gtk.Align.END)
        hud.set_hexpand(True)

        self._hud_prompt_lbl = Gtk.Label(label="")
        self._hud_prompt_lbl.add_css_class("attractor-hud-lbl")
        self._hud_prompt_lbl.set_hexpand(True)
        self._hud_prompt_lbl.set_xalign(0)
        self._hud_prompt_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        hud.append(self._hud_prompt_lbl)

        self._hud_pool_lbl = Gtk.Label(label="")
        self._hud_pool_lbl.add_css_class("attractor-hud-lbl")
        self._hud_pool_lbl.set_xalign(1)
        hud.append(self._hud_pool_lbl)

        player_overlay.add_overlay(hud)
        outer.append(player_overlay)

    def _make_slot(self) -> Gtk.Box:
        """
        Create a media slot: a Gtk.Box holding one Gtk.Video and one Gtk.Picture.
        Only one is visible at a time based on the media type being displayed.
        """
        box = Gtk.Box()
        box.set_hexpand(True)
        box.set_vexpand(True)

        vid = Gtk.Video()
        vid.set_hexpand(True)
        vid.set_vexpand(True)
        vid.set_autoplay(True)
        box._video = vid
        box.append(vid)

        pic = Gtk.Picture()
        pic.set_hexpand(True)
        pic.set_vexpand(True)
        pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        pic.set_visible(False)
        box._picture = pic
        box.append(pic)

        return box

    # ── Placeholder for playback/generation methods (Tasks 3 and 4) ──────

    def _get_current_video_stream(self):
        """Return the Gtk.MediaStream for the active video slot, or None."""
        slot = self._slot_b if self._active_slot_name == "b" else self._slot_a
        vid = slot._video
        if not vid.get_visible():
            return None
        return vid.get_media_stream()


def _hdivider() -> Gtk.Separator:
    """Create a horizontal separator for the sidebar."""
    return Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
