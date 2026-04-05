#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Attractor Mode - self-sustaining kiosk that cycles generated media and
continuously queues new generations.

Classes:
    AttractorPool  - pure pool/shuffle logic (no GTK, testable)
    AttractorWindow - GTK4 kiosk window
"""
from __future__ import annotations

import datetime
import logging
import random
import shutil
import threading
import traceback
from pathlib import Path
from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gio, Gtk, Pango  # noqa: E402

import prompt_client  # noqa: E402
from history_store import STORAGE_DIR as _STORAGE_DIR  # noqa: E402

_DISK_SPACE_MIN_BYTES = 18 * 1024 ** 3   # 18 GB — pause TT-TV generation below this

# ── Logging ───────────────────────────────────────────────────────────────────
_LOG_DIR = Path.home() / ".local" / "share" / "tt-video-gen"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_log = logging.getLogger("attractor")
if not _log.handlers:
    _log.setLevel(logging.DEBUG)
    _fh = logging.FileHandler(_LOG_DIR / "attractor.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-5s  %(message)s",
                                       datefmt="%Y-%m-%d %H:%M:%S"))
    _log.addHandler(_fh)


class AttractorPool:
    """
    Manages the shuffled playback order for a growing list of media records.

    Records are played in a shuffled order. When the cycle is exhausted the
    pool reshuffles, but the first item of the new cycle is never the same as
    the last item of the previous cycle.  New records added mid-cycle are
    inserted at a random position *after* the current playback position so
    they appear later in the current cycle rather than immediately next.
    """

    # Fixed scheduling constants.
    # NOTE: GenerationRecord.duration_s is wall-clock *inference* time (27–634 s),
    # NOT video playback duration.  Never use it for scheduling advances.
    IMAGE_DWELL_MS: int = 10_000    # how long to show a still image (10 s)
    VIDEO_FALLBACK_MS: int = 90_000  # safety-net timer if notify::ended never fires (90 s)

    def __init__(self, records: list) -> None:
        self._records: list = list(records)
        self._order: list[int] = []
        self._pos: int = 0
        self._last_idx: int | None = None
        self._shuffle_fresh()

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

    def peek_next(self):
        """Return the record that advance() would play next, without consuming it.
        Returns None if the pool is empty."""
        if not self._records:
            return None
        if self._pos < len(self._order):
            return self._records[self._order[self._pos]]
        # At end of cycle - next would be first item of a fresh shuffle.
        # Peek without actually shuffling: return any record that isn't last.
        if len(self._records) == 1:
            return self._records[0]
        for idx in range(len(self._records)):
            if idx != self._last_idx:
                return self._records[idx]
        return self._records[0]

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

    def remove_record(self, record_id: str) -> bool:
        """
        Remove a record by ID.  Adjusts _order and index references so the
        remaining records continue to play in the correct shuffled order.
        Returns True if the record was found and removed, False otherwise.
        """
        idx = next(
            (i for i, r in enumerate(self._records)
             if getattr(r, "id", None) == record_id),
            None,
        )
        if idx is None:
            return False

        self._records.pop(idx)

        # Remap _order: drop entries pointing at idx, shift entries > idx down by 1.
        new_order = []
        for o in self._order:
            if o == idx:
                continue          # removed item — skip
            new_order.append(o - 1 if o > idx else o)
        self._order = new_order

        # _pos may now point past the end of a shortened order; cap it.
        self._pos = min(self._pos, len(self._order))

        # Remap _last_idx.
        if self._last_idx == idx:
            self._last_idx = None
        elif self._last_idx is not None and self._last_idx > idx:
            self._last_idx -= 1

        return True

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


# ---------------------------------------------------------------------------
# CSS - registered by AttractorWindow on first instantiation.
# Uses @define-color variables already declared in main_window.py's stylesheet.
# ---------------------------------------------------------------------------

_CSS = b"""
/* Channel-change flash overlay */
/* TT brand purple (#5A1A6E) wash - slightly darker than the logo purple */
@keyframes tt-channel-change {
    0%   { opacity: 0; }
    8%   { opacity: 1; }
    65%  { opacity: 0.5; }
    100% { opacity: 0; }
}
.channel-flash {
    background-color: #5A1A6E;
    opacity: 0;
}
.channel-flash.flash-active {
    animation: tt-channel-change 0.65s ease-out forwards;
}

/* Attractor sidebar */
.attractor-sidebar {
    background-color: @tt_bg_darkest;
    border-right: 1px solid @tt_border;
    padding: 6px 4px;
    min-width: 84px;
}
.attractor-header {
    color: @tt_accent;
    font-size: 11px;
    font-weight: bold;
}
.attractor-stat-lbl {
    color: @tt_text_muted;
    font-size: 9px;
}
.attractor-section-lbl {
    color: @tt_text_muted;
    font-size: 8px;
    font-weight: bold;
    margin-top: 4px;
}
/* "Coming soon" prompt cards - identical geometry, only border/color differ */
.cs-card {
    background-color: @tt_bg_dark;
    border: 1px solid @tt_border;
    border-radius: 6px;
    padding: 5px 6px;
    margin-bottom: 3px;
    min-height: 52px;   /* tag(12) + gap(2) + 2-line-prompt(28) + v-padding(10) */
}
.cs-card-generating {
    background-color: @tt_bg_dark;
    border: 1px solid @tt_accent;
    border-radius: 6px;
    padding: 5px 6px;
    margin-bottom: 3px;
    min-height: 52px;   /* must match .cs-card exactly - prevents height shift on swap */
}
.cs-card-tag {
    color: @tt_text_muted;
    font-size: 8px;
    font-weight: bold;
    min-height: 12px;   /* lock tag row height regardless of text */
}
.cs-card-tag-generating {
    color: @tt_accent;
    font-size: 8px;
    font-weight: bold;
    min-height: 12px;   /* must match .cs-card-tag */
}
.cs-card-prompt {
    color: @tt_text;
    font-size: 9px;
    font-style: italic;
    min-height: 28px;   /* 2 lines x 14px - keeps card height stable when text arrives */
}
.cs-card-empty {
    color: @tt_text_muted;
    font-size: 9px;
    font-style: italic;
    min-height: 28px;   /* must match .cs-card-prompt */
}
/* "Next on TT-TV" card */
.next-card {
    background-color: @tt_bg_dark;
    border: 1px solid @tt_border;
    border-radius: 6px;
    padding: 5px 6px;
    min-height: 90px;   /* tag(12) + gap(4) + thumb(60) + v-padding(14) */
}
.next-card-tag {
    color: @tt_accent_light;
    font-size: 8px;
    font-weight: bold;
    margin-bottom: 4px;
    min-height: 12px;
}
.attractor-stop-btn {
    background-color: @tt_bg_darkest;
    color: @tt_error;
    border: 1px solid @tt_error;
    border-radius: 4px;
    padding: 5px 8px;
    font-size: 11px;
    margin-top: 4px;
}
.attractor-stop-btn:hover {
    background-color: @tt_border;
}
/* User prompt entry at bottom of sidebar */
.attractor-user-entry {
    background-color: @tt_bg_dark;
    color: @tt_text;
    border: 1px solid @tt_border;
    border-radius: 3px;
    font-size: 9px;
    padding: 2px 5px;
    margin-top: 3px;
    min-height: 22px;
}
.attractor-user-entry:focus {
    border-color: @tt_accent;
}
/* HUD overlay - broadcast lower third */
.attractor-hud {
    background-color: rgba(10, 28, 35, 0.90);
    background-image: repeating-linear-gradient(
        45deg,
        transparent            0px,
        transparent            3px,
        rgba(79,209,197,0.06)  3px,
        rgba(79,209,197,0.06)  4px,
        transparent            4px,
        transparent            11px,
        rgba(0,0,0,0.08)       11px,
        rgba(0,0,0,0.08)       12px
    );
    border-top: 2px solid @tt_accent;
    padding: 6px 14px 10px;
}
.attractor-hud-tag {
    color: @tt_accent;
    font-size: 8px;
    font-weight: bold;
    font-family: monospace;
    letter-spacing: 2px;
    margin-bottom: 2px;
}
.attractor-hud-meta {
    color: @tt_text_muted;
    font-size: 8px;
    font-family: monospace;
}
.attractor-hud-prompt {
    color: rgba(232,240,242,0.96);
    font-size: 13px;
    font-weight: bold;
    font-style: italic;
    letter-spacing: 0.2px;
}
"""


# ---------------------------------------------------------------------------
# AttractorWindow
# ---------------------------------------------------------------------------

def _unload_slot_video(slot: Gtk.Box) -> None:
    """Pause a slot's Gtk.Video stream before calling set_file(None).

    GStreamer's async state machine needs to transition from PLAYING → PAUSED
    → NULL before it can release file descriptors.  Calling set_file(None)
    while the stream is still PLAYING starts that transition asynchronously but
    doesn't block - so hundreds of ms can pass before the fds are freed.
    Pausing first moves the pipeline to PAUSED synchronously (the gst-play
    element handles PAUSED immediately), which dramatically shortens the
    PLAYING→NULL teardown and prevents fd accumulation across rapid advances.
    """
    stream = slot._video.get_media_stream()
    if stream is not None:
        try:
            stream.pause()
        except Exception:
            pass
    slot._video.set_file(None)


class AttractorWindow(Gtk.Window):
    """
    Kiosk window: narrow sidebar with live status + A/B crossfading media player.

    All communication with MainWindow is through constructor callbacks - this
    class has no imports from main_window.py.

    Keyboard shortcuts:
        Escape  - close window (stops generation loop)
        F       - toggle fullscreen
        Space   - pause/resume playback (generation loop continues)
    """

    def __init__(
        self,
        records: list,                        # initial pool from HistoryStore.all_records()
        model_source: str,                    # "video" | "image" | "animate"
        on_enqueue: Callable,                 # MainWindow._on_enqueue compatible signature
        get_queue_depth: Callable[[], int],   # returns len(self._queue) in MainWindow
        get_is_generating: Callable[[], bool] = lambda: False,  # True when worker is active
        get_server_status: "Callable[[], tuple[bool, str | None]]" = lambda: (False, None),
        system_prompt: str = "",              # unused; kept for caller compatibility
        get_queue_prompts: "Callable[[], list[str]]" = lambda: [],   # prompts waiting in queue
        get_current_prompt: "Callable[[], str | None]" = lambda: None,  # prompt currently generating
        on_user_enqueue: "Callable | None" = None,  # high-priority enqueue for user-typed prompts
    ) -> None:
        _log.debug("AttractorWindow.__init__ - %d records, model_source=%s", len(records), model_source)
        super().__init__(title="TT-TV")
        self._system_prompt = system_prompt
        self._model_source = model_source
        self._on_enqueue = on_enqueue
        self._get_queue_depth = get_queue_depth
        self._get_is_generating = get_is_generating
        self._get_server_status = get_server_status
        self._get_queue_prompts = get_queue_prompts
        self._get_current_prompt = get_current_prompt
        # Fall back to regular enqueue if no priority path provided
        self._on_user_enqueue = on_user_enqueue if on_user_enqueue is not None else on_enqueue
        self._att_poll_stop = threading.Event()
        video_records = [r for r in records if getattr(r, "media_type", "video") != "image"]
        _log.debug("pool filter: %d total → %d video records", len(records), len(video_records))
        self._pool = AttractorPool(video_records)
        self._gen_stop = threading.Event()
        self._paused = False
        self._pending_advance_source: int | None = None  # GLib source id
        self._watched_stream = None          # stream we connected notify::ended to
        self._stream_handler_id: int | None = None  # handler ID so we can disconnect
        # Set to True by start().  add_record() defers its first-advance call until
        # start() has run, preventing a double-advance race when a generation completes
        # while the window is being constructed (start() is called via idle_add so it
        # fires after __init__ returns, but add_record may also be queued via idle_add).
        self._started: bool = False
        # Cleared to False at the very start of _on_destroy so that idle/timer
        # callbacks queued by the background generation thread (which may still be
        # mid-call when the window closes) silently no-op instead of touching
        # destroyed widgets and triggering GTK's "window shown after destroyed" crash.
        self._alive: bool = True
        # After each A/B crossfade we schedule a GStreamer pipeline teardown for the
        # now-inactive slot.  Keeping a pipeline open in the hidden slot for the full
        # duration of the next video doubles steady-state fd usage and causes "Too
        # many open files" crashes after many advance cycles.
        self._pending_unload_source: int = 0  # GLib source id for the unload timer
        self._slot_to_unload = None           # Gtk.Box slot whose pipeline to teardown
        self._pending_flash_source: int = 0   # GLib source id for flash clear timer

        # Load CSS (uses @define-color variables already loaded by main_window.py)
        try:
            provider = Gtk.CssProvider()
            provider.load_from_data(_CSS)
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(),
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            _log.debug("CSS loaded OK")
        except Exception:
            _log.exception("CSS load failed (non-fatal)")

        _log.debug("building UI")
        self._build()
        _log.debug("UI built, maximizing")
        self.maximize()

        # Keyboard shortcuts
        ctrl = Gtk.EventControllerKey()
        ctrl.connect("key-pressed", self._on_key)
        self.add_controller(ctrl)

        self.connect("destroy", self._on_destroy)
        # Explicitly destroy (not merely hide) when the user clicks X or the Stop
        # button calls close().  GTK4's default close-request can hide the window
        # instead of destroying it, leaving _attractor_win non-None and preventing
        # a fresh open on the second launch.
        self.connect("close-request", self._on_close_requested)

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

    def _on_close_requested(self, _win) -> bool:
        """Force full window destruction on close so _on_destroy always fires.

        GTK4's default close-request handler may merely hide the window, which
        would leave _attractor_win non-None in MainWindow and break the second
        launch.  We explicitly call destroy() then return True so GTK doesn't
        do a redundant second close.
        """
        self.destroy()
        return True  # we handled it

    def _on_destroy(self, _win) -> None:
        """Stop the generation loop and cancel any pending timers."""
        # Mark dead FIRST so any idle/timer callbacks that fire after this
        # point (e.g. queued by the generation thread mid-call) silently bail
        # instead of touching destroyed widgets.
        self._alive = False
        # Log a stack trace so we can see what triggered the close.
        _log.info("=== Attractor stopped ===\n%s", "".join(traceback.format_stack()))
        self._gen_stop.set()
        self._att_poll_stop.set()
        # Unsubscribe from screensaver D-Bus signal.
        if getattr(self, "_dbus_conn", None) and getattr(self, "_dbus_sub_id", 0):
            try:
                self._dbus_conn.signal_unsubscribe(self._dbus_sub_id)
            except Exception:
                pass
        if self._pending_advance_source is not None:
            GLib.source_remove(self._pending_advance_source)
            self._pending_advance_source = None
        if self._pending_unload_source:
            GLib.source_remove(self._pending_unload_source)
            self._pending_unload_source = 0
        if self._pending_flash_source:
            GLib.source_remove(self._pending_flash_source)
            self._pending_flash_source = 0
        self._slot_to_unload = None
        # Disconnect the notify::ended handler so a late-firing signal after
        # window destruction doesn't call _advance() on a dead widget tree.
        if self._watched_stream is not None and self._stream_handler_id is not None:
            try:
                self._watched_stream.disconnect(self._stream_handler_id)
            except Exception:
                pass
        self._watched_stream = None
        self._stream_handler_id = None

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
        """Build the kiosk layout: sidebar on left, media player on right, status bar at bottom."""
        root_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(root_vbox)

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        outer.set_vexpand(True)

        # ── Sidebar ───────────────────────────────────────────────────────
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        sidebar.add_css_class("attractor-sidebar")
        sidebar.set_size_request(84, -1)
        sidebar.set_hexpand(False)
        sidebar.set_vexpand(True)

        hdr = Gtk.Label(label="📺  TT-TV")
        hdr.add_css_class("attractor-header")
        hdr.set_xalign(0)
        hdr.set_max_width_chars(10)
        hdr.set_ellipsize(Pango.EllipsizeMode.END)
        sidebar.append(hdr)

        sidebar.append(_hdivider())

        self._queue_lbl = Gtk.Label(label="⏳  queue: -")
        self._queue_lbl.add_css_class("attractor-stat-lbl")
        self._queue_lbl.set_xalign(0)
        self._queue_lbl.set_max_width_chars(12)
        self._queue_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        sidebar.append(self._queue_lbl)

        self._pool_lbl = Gtk.Label(label=f"🎬  pool: {self._pool.size}")
        self._pool_lbl.add_css_class("attractor-stat-lbl")
        self._pool_lbl.set_xalign(0)
        self._pool_lbl.set_max_width_chars(12)
        self._pool_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        sidebar.append(self._pool_lbl)

        sidebar.append(_hdivider())

        # ── Coming soon cards (3 slots) ───────────────────────────────────
        cs_hdr = Gtk.Label(label="COMING SOON")
        cs_hdr.add_css_class("attractor-section-lbl")
        cs_hdr.set_xalign(0)
        cs_hdr.set_max_width_chars(12)
        cs_hdr.set_ellipsize(Pango.EllipsizeMode.END)
        sidebar.append(cs_hdr)

        # Build 4 reusable card widgets; updated by _update_coming_soon_ui().
        # Card dimensions are locked (set_size_request + CSS min-height) so the
        # sidebar never reflows when text appears or CSS classes swap.
        self._cs_cards: list[dict] = []
        for _ in range(4):
            card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            card_box.add_css_class("cs-card")
            # Lock height: must match CSS min-height (52px) so GTK never reflows.
            card_box.set_size_request(-1, 52)

            tag_lbl = Gtk.Label(label="COMING SOON")
            tag_lbl.add_css_class("cs-card-tag")
            tag_lbl.set_xalign(0)
            tag_lbl.set_max_width_chars(12)
            tag_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            card_box.append(tag_lbl)

            prompt_lbl = Gtk.Label(label="")
            prompt_lbl.add_css_class("cs-card-empty")
            prompt_lbl.set_xalign(0)
            prompt_lbl.set_wrap(True)
            prompt_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            prompt_lbl.set_lines(2)
            prompt_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            # Reserve 2-line height immediately so card size is stable before text arrives.
            prompt_lbl.set_size_request(-1, 28)
            card_box.append(prompt_lbl)

            sidebar.append(card_box)
            self._cs_cards.append({"box": card_box, "tag": tag_lbl, "prompt": prompt_lbl})

        # ── Spacer pushes the "Next on TT-TV" section to the bottom ──────
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        sidebar.append(spacer)

        # ── Next on TT-TV ─────────────────────────────────────────────────
        next_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        next_card.add_css_class("next-card")
        # Lock height so the bottom section never shifts when a thumbnail loads.
        next_card.set_size_request(-1, 90)

        next_tag = Gtk.Label(label="NEXT ON TT-TV")
        next_tag.add_css_class("next-card-tag")
        next_tag.set_xalign(0)
        next_tag.set_max_width_chars(12)
        next_tag.set_ellipsize(Pango.EllipsizeMode.END)
        next_card.append(next_tag)

        self._next_thumb = Gtk.Picture()
        self._next_thumb.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._next_thumb.set_size_request(1, 60)  # width=1: don't drive sidebar width
        self._next_thumb.set_hexpand(False)
        self._next_thumb.set_halign(Gtk.Align.FILL)
        self._next_thumb.set_vexpand(True)
        next_card.append(self._next_thumb)

        sidebar.append(next_card)

        # ── User prompt input (low-key) ───────────────────────────────────
        # Small entry to queue your own prompt at high priority; Enter submits.
        self._user_entry = Gtk.Entry()
        self._user_entry.set_placeholder_text("add to queue…")
        self._user_entry.add_css_class("attractor-user-entry")
        self._user_entry.connect("activate", self._on_user_prompt_activate)
        sidebar.append(self._user_entry)

        stop_btn = Gtk.Button(label="■  Stop TT-TV")
        stop_btn.add_css_class("attractor-stop-btn")
        stop_btn.connect("clicked", lambda _: self.close())
        sidebar.append(stop_btn)

        outer.append(sidebar)

        # ── Media player ──────────────────────────────────────────────────
        player_overlay = Gtk.Overlay()
        player_overlay.set_hexpand(True)
        player_overlay.set_vexpand(True)

        # A/B Stack — instant cut; the channel-change effect is handled by the
        # flash overlay below, not by a GTK stack transition.
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self._stack.set_transition_duration(0)
        self._stack.set_hexpand(True)
        self._stack.set_vexpand(True)

        self._slot_a = self._make_slot()
        self._slot_b = self._make_slot()
        self._stack.add_named(self._slot_a, "a")
        self._stack.add_named(self._slot_b, "b")
        self._stack.set_visible_child_name("a")
        self._active_slot_name = "a"

        player_overlay.set_child(self._stack)

        # HUD broadcast lower-third: dark checker card pinned to the bottom.
        hud = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        hud.add_css_class("attractor-hud")
        hud.set_valign(Gtk.Align.END)
        hud.set_hexpand(True)

        # Top row: station tag (left) + pool count (right)
        tag_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        station_tag = Gtk.Label(label="▶  NOW PLAYING ON TT-TV")
        station_tag.add_css_class("attractor-hud-tag")
        station_tag.set_hexpand(True)
        station_tag.set_xalign(0)
        tag_row.append(station_tag)

        self._hud_pool_lbl = Gtk.Label(label="")
        self._hud_pool_lbl.add_css_class("attractor-hud-meta")
        self._hud_pool_lbl.set_xalign(1)
        tag_row.append(self._hud_pool_lbl)

        hud.append(tag_row)

        # Full prompt — wraps to as many lines as needed, no truncation.
        self._hud_prompt_lbl = Gtk.Label(label="")
        self._hud_prompt_lbl.add_css_class("attractor-hud-prompt")
        self._hud_prompt_lbl.set_hexpand(True)
        self._hud_prompt_lbl.set_xalign(0)
        self._hud_prompt_lbl.set_wrap(True)
        self._hud_prompt_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        hud.append(self._hud_prompt_lbl)

        player_overlay.add_overlay(hud)

        # Channel-change flash overlay — full-screen, non-interactive.
        # Normally transparent (opacity: 0). CSS class "flash-active" triggers
        # the @keyframes tt-channel-change animation for the between-video flash.
        self._channel_flash = Gtk.Box()
        self._channel_flash.set_hexpand(True)
        self._channel_flash.set_vexpand(True)
        self._channel_flash.set_can_target(False)
        self._channel_flash.add_css_class("channel-flash")
        player_overlay.add_overlay(self._channel_flash)

        outer.append(player_overlay)
        root_vbox.append(outer)
        root_vbox.append(self._build_att_status_bar())

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

    # ── Playback ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """
        Begin playback and start the generation loop daemon thread.
        Must be called after the window is presented so the display is ready.
        """
        if not self._alive:
            return
        self._started = True
        _log.info("=== Attractor started - pool size: %d ===", self._pool.size)
        self._subscribe_screensaver()
        if self._pool.size > 0:
            self._advance()
        threading.Thread(target=self._generation_loop, daemon=True).start()
        threading.Thread(target=self._att_status_poll_loop, daemon=True).start()

    def _subscribe_screensaver(self) -> None:
        """Subscribe to the org.freedesktop.ScreenSaver D-Bus signal so we can
        pause GStreamer before the display is locked.

        Without this, VA-API/GBM video decoding can segfault when the kernel
        DRM device becomes inaccessible during a lock-screen transition.
        KDE, GNOME, and most compositors implement this standard interface.
        """
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._dbus_conn = bus          # keep reference alive
            self._dbus_sub_id = bus.signal_subscribe(
                None,                                      # any sender
                "org.freedesktop.ScreenSaver",
                "ActiveChanged",
                "/org/freedesktop/ScreenSaver",
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_screensaver_changed,
            )
            _log.debug("screensaver D-Bus subscription OK (sub_id=%d)", self._dbus_sub_id)
        except Exception as exc:
            _log.warning("screensaver D-Bus subscription failed (non-fatal): %s", exc)
            self._dbus_conn = None
            self._dbus_sub_id = 0

    def _on_screensaver_changed(self, _conn, _sender, _path, _iface, _signal, params, _udata):
        """Screensaver activated/deactivated — pause or resume GStreamer accordingly."""
        if not self._alive:
            return
        active = params[0]   # GLib.Variant bool
        if active:
            # Lock screen about to obscure display — pause pipeline to release
            # DRM/VA-API resources before the compositor revokes access.
            stream = self._get_current_video_stream()
            if stream is not None:
                try:
                    stream.pause()
                except Exception:
                    pass
            _log.info("screensaver active - GStreamer paused")
        else:
            # Unlocked — resume only if user hasn't manually paused via Space.
            if not self._paused:
                stream = self._get_current_video_stream()
                if stream is not None:
                    try:
                        stream.play()
                    except Exception:
                        pass
            _log.info("screensaver inactive - GStreamer resumed")

    def _advance(self) -> None:
        """
        Load the next media item into the inactive A/B slot and crossfade to it.
        Does nothing if playback is paused.
        """
        if not self._alive or self._paused:
            return

        # Cancel any already-scheduled slot unload.  If _advance fires before the
        # unload timer fires, execute the unload eagerly so we don't skip it.
        if self._pending_unload_source:
            GLib.source_remove(self._pending_unload_source)
            self._pending_unload_source = 0
        if self._slot_to_unload is not None:
            _unload_slot_video(self._slot_to_unload)
            self._slot_to_unload = None

        self._pool.advance()
        record = self._pool.current_record()

        # Pick the inactive slot (the one not currently showing).
        # The current active slot will become invisible after the crossfade - we'll
        # unload its GStreamer pipeline once the transition completes.
        prev_name = self._active_slot_name
        prev_slot = self._slot_a if prev_name == "a" else self._slot_b
        next_name = "b" if prev_name == "a" else "a"
        next_slot = self._slot_b if next_name == "b" else self._slot_a

        self._load_slot(next_slot, record)
        self._stack.set_visible_child_name(next_name)
        self._active_slot_name = next_name

        # Schedule the now-invisible slot's pipeline teardown after the crossfade
        # (250 ms) plus a small safety margin.  This keeps steady-state pipeline
        # count at 1 instead of 2, preventing fd accumulation over many cycles.
        self._slot_to_unload = prev_slot
        self._pending_unload_source = GLib.timeout_add(
            self._stack.get_transition_duration() + 50,
            self._on_unload_timer,
        )

        # Update HUD — full prompt, no truncation
        prompt_text = (getattr(record, "prompt", "") or "")
        self._hud_prompt_lbl.set_label(prompt_text)
        self._hud_pool_lbl.set_label(f"pool: {self._pool.size}")

        # Refresh "Next on TT-TV" thumbnail for the upcoming item
        self._update_next_thumb()

        media_type = getattr(record, "media_type", "video")
        path = getattr(record, "video_path", None) or getattr(record, "image_path", None) or "?"
        gen_time = getattr(record, "duration_s", None)  # inference wall-clock time
        model_id = getattr(record, "model", "") or ""
        steps = getattr(record, "num_inference_steps", 0) or 0
        _log.info("advance → %s  [%s]  gen=%.1fs  pool=%d",
                  Path(path).name if path != "?" else "?",
                  media_type,
                  gen_time or 0.0,
                  self._pool.size)

        # Update now-playing stats in the status bar.
        # Show: model  |  N steps  |  Xs gen
        now_parts: list[str] = []
        if model_id:
            now_parts.append(model_id)
        if steps:
            now_parts.append(f"{steps} steps")
        if gen_time:
            now_parts.append(f"{gen_time:.0f}s gen")
        now_text = "  |  ".join(now_parts)
        self._att_now_lbl.set_label(now_text)
        self._att_now_lbl.set_visible(bool(now_text))

        self._schedule_advance(record)

    def _on_unload_timer(self) -> bool:
        """GLib timer: tear down the GStreamer pipeline for the now-invisible slot."""
        self._pending_unload_source = 0
        if not self._alive:
            return GLib.SOURCE_REMOVE
        if self._slot_to_unload is not None:
            _unload_slot_video(self._slot_to_unload)
            self._slot_to_unload = None
        return GLib.SOURCE_REMOVE

    def _load_slot(self, slot: Gtk.Box, record) -> None:
        """
        Load a GenerationRecord into a media slot widget.
        Shows either the Gtk.Picture (images) or Gtk.Video (videos),
        hiding the other widget.
        """
        if getattr(record, "media_type", "video") == "image":
            slot._video.set_visible(False)
            path = getattr(record, "image_path", None) or getattr(record, "thumbnail_path", None)
            if path:
                slot._picture.set_filename(path)
            slot._picture.set_visible(True)
        else:
            slot._picture.set_visible(False)
            # Do NOT call set_loop(True) - it prevents the notify::ended signal
            # from firing on Gtk.MediaStream, breaking advance-on-video-end.
            # (See CLAUDE.md: "Gtk.Video.set_loop(True) is unreliable")
            path = getattr(record, "video_path", None)
            if path:
                # Pause then clear the slot's pipeline before loading the new file.
                # Pausing first moves GStreamer from PLAYING→PAUSED synchronously,
                # which dramatically shortens the subsequent PAUSED→NULL teardown
                # triggered by set_file(None) and reduces fd accumulation.
                _unload_slot_video(slot)
                slot._video.set_filename(path)
            slot._video.set_visible(True)

    def _schedule_advance(self, record) -> None:
        """
        Schedule the next advance() call based on media type.

        Images: fixed IMAGE_DWELL_MS timer.
        Videos: connect to notify::ended; VIDEO_FALLBACK_MS safety-net timer
                in case the signal never fires (corrupt file, GStreamer issue).

        NOTE: GenerationRecord.duration_s is inference wall-clock time, NOT
        video playback duration — never use it here for scheduling.
        """
        # Cancel any pending timer
        if self._pending_advance_source is not None:
            GLib.source_remove(self._pending_advance_source)
            self._pending_advance_source = None

        # Disconnect previous notify::ended handler to prevent accumulation
        if self._watched_stream is not None and self._stream_handler_id is not None:
            try:
                self._watched_stream.disconnect(self._stream_handler_id)
            except Exception:
                pass
        self._watched_stream = None
        self._stream_handler_id = None

        if getattr(record, "media_type", "video") == "image":
            self._pending_advance_source = GLib.timeout_add(
                AttractorPool.IMAGE_DWELL_MS, self._on_advance_timer
            )
        else:
            self._connect_video_ended()

    def _connect_video_ended(self) -> None:
        """Connect notify::ended to the current video stream, or retry in 500 ms."""
        stream = self._get_current_video_stream()
        if stream:
            self._watched_stream = stream
            self._stream_handler_id = stream.connect("notify::ended", self._on_video_ended)
            # If the video already ended while we were waiting, advance now
            if stream.get_ended():
                _log.debug("stream already ended on connect - advancing immediately")
                GLib.idle_add(self._advance)
                return
            # Safety net: if notify::ended never fires (corrupt file, screensaver,
            # GStreamer pipeline stall), force-advance after VIDEO_FALLBACK_MS.
            _log.debug("stream connected - fallback timer set for %.1f s",
                       AttractorPool.VIDEO_FALLBACK_MS / 1000)
            self._pending_advance_source = GLib.timeout_add(
                AttractorPool.VIDEO_FALLBACK_MS, self._on_advance_timer
            )
        else:
            # Stream not initialised yet - retry
            _log.debug("stream not ready - retrying in 500 ms")
            self._pending_advance_source = GLib.timeout_add(
                500, self._retry_connect_stream
            )

    def _on_advance_timer(self) -> bool:
        """GLib timeout callback - fires when dwell time or fallback timer expires."""
        self._pending_advance_source = None
        if not self._alive:
            return GLib.SOURCE_REMOVE
        _log.warning("advance timer fired (fallback or image dwell) - forcing advance")
        self._advance()
        return GLib.SOURCE_REMOVE

    def _on_video_ended(self, stream, _param) -> None:
        """Called when the active video stream's ended property changes."""
        if stream.get_ended() and self._alive:
            _log.debug("notify::ended received - triggering channel change")
            self._trigger_channel_change()

    def _retry_connect_stream(self) -> bool:
        """Fallback: GStreamer stream not ready at _schedule_advance time; try again."""
        self._pending_advance_source = None
        if not self._alive:
            return GLib.SOURCE_REMOVE
        _log.debug("retry connecting stream")
        self._connect_video_ended()
        return GLib.SOURCE_REMOVE

    # ── Generation loop ───────────────────────────────────────────────────

    def _generation_loop(self) -> None:
        """
        Background daemon thread. Continuously generates prompts and enqueues
        new generation jobs via on_enqueue callback.

        Back-pressure: if queue depth >= 3, waits 30 s before retrying (server
        isn't consuming jobs fast enough). Stops when _gen_stop is set.
        """
        while not self._gen_stop.wait(0.0):
            depth = self._get_queue_depth()
            generating = self._get_is_generating()
            GLib.idle_add(self._update_work_lbl, depth, generating)
            GLib.idle_add(self._update_coming_soon_ui)

            if depth >= 3:
                _log.debug("queue full (depth=%d) - waiting 30 s", depth)
                GLib.idle_add(self._set_gen_status, "⏸  queue full…")
                if self._gen_stop.wait(30.0):
                    break
                continue

            # Check disk space before generating more — avoid filling the drive.
            try:
                free = shutil.disk_usage(_STORAGE_DIR).free
            except OSError:
                free = _DISK_SPACE_MIN_BYTES + 1  # unknown → assume OK
            if free < _DISK_SPACE_MIN_BYTES:
                free_gb = free / (1024 ** 3)
                _log.warning("disk space low (%.1f GB) - pausing TT-TV generation", free_gb)
                GLib.idle_add(self._set_gen_status, f"⚠  disk low ({free_gb:.1f} GB) - paused")
                if self._gen_stop.wait(60.0):
                    break
                continue

            try:
                GLib.idle_add(self._set_gen_status, "✦  writing prompt…")
                prompt = prompt_client.generate_prompt(
                    source=self._model_source,
                    seed_text="",
                )
                _log.info("prompt generated (depth=%d): %s", depth, prompt[:80])
                GLib.idle_add(self._set_prompt_lbl, prompt)
                GLib.idle_add(self._set_gen_status, "⏳  submitted")
                GLib.idle_add(self._enqueue_generation, prompt)
            except Exception as exc:
                _log.error("prompt generation failed: %s", exc)
                GLib.idle_add(self._set_gen_status, f"⚠  {exc}")
                if self._gen_stop.wait(15.0):
                    break
                continue

            # Brief pause before checking queue depth again; exit immediately on stop
            if self._gen_stop.wait(5.0):
                break

    def _enqueue_generation(self, prompt: str) -> None:
        """
        Called on the main thread via GLib.idle_add.
        Forwards a generation request to MainWindow via the on_enqueue callback.
        Uses the model defaults from the spec (steps=30, seed=-1, guidance=5.0).
        """
        if not self._alive:
            return
        self._on_enqueue(
            prompt=prompt,
            neg="",
            steps=30,
            seed=-1,
            seed_image_path="",
            model_source=self._model_source,
            guidance_scale=5.0,
            ref_video_path="",
            ref_char_path="",
            animate_mode="animation",
            model_id="",
        )

    def _on_user_prompt_activate(self, entry) -> None:
        """User pressed Enter in the sidebar prompt box — enqueue at high priority."""
        if not self._alive:
            return
        text = entry.get_text().strip()
        if not text:
            return
        entry.set_text("")
        self._on_user_enqueue(
            prompt=text,
            neg="",
            steps=30,
            seed=-1,
            seed_image_path="",
            model_source=self._model_source,
            guidance_scale=5.0,
            ref_video_path="",
            ref_char_path="",
            animate_mode="animation",
            model_id="",
        )
        GLib.idle_add(self._update_coming_soon_ui)

    def _set_gen_status(self, text: str) -> None:
        """No-op: status is now shown via the coming-soon cards. Kept for _alive guard."""
        pass  # generation status is conveyed by the coming-soon card updates

    def _set_prompt_lbl(self, text: str) -> None:
        """No-op: prompt is now pushed via _enqueue_generation → _update_coming_soon_ui."""
        pass

    def _update_coming_soon_ui(self) -> None:
        """Refresh the coming-soon cards from the live queue callbacks.

        Slot 0 — what's currently generating (teal border, "▶ GENERATING").
        Remaining slots — next items in queue, oldest first.
        User-submitted prompts appear first because they're inserted at the
        front of MainWindow's queue by _on_attractor_priority_enqueue.
        Empty slots show a muted placeholder.
        """
        if not self._alive:
            return
        current = self._get_current_prompt()   # str | None
        queued = self._get_queue_prompts()     # list[str], front = next to run

        # Build ordered display list: (text, is_generating)
        display: list[tuple[str, bool]] = []
        if current:
            display.append((current, True))
        for p in queued:
            if len(display) >= len(self._cs_cards):
                break
            display.append((p, False))

        for i, card in enumerate(self._cs_cards):
            box = card["box"]
            tag = card["tag"]
            prompt_lbl = card["prompt"]

            for cls in ("cs-card", "cs-card-generating"):
                try:
                    box.remove_css_class(cls)
                except Exception:
                    pass

            if i < len(display):
                text, is_generating = display[i]
                box.add_css_class("cs-card-generating" if is_generating else "cs-card")
                for cls in ("cs-card-tag", "cs-card-tag-generating"):
                    try:
                        tag.remove_css_class(cls)
                    except Exception:
                        pass
                tag.add_css_class("cs-card-tag-generating" if is_generating else "cs-card-tag")
                tag.set_label("▶ GENERATING" if is_generating else "COMING SOON")
                for cls in ("cs-card-prompt", "cs-card-empty"):
                    try:
                        prompt_lbl.remove_css_class(cls)
                    except Exception:
                        pass
                prompt_lbl.add_css_class("cs-card-prompt")
                prompt_lbl.set_label(text)
            else:
                box.add_css_class("cs-card")
                for cls in ("cs-card-tag", "cs-card-tag-generating"):
                    try:
                        tag.remove_css_class(cls)
                    except Exception:
                        pass
                tag.add_css_class("cs-card-tag")
                tag.set_label("COMING SOON")
                for cls in ("cs-card-prompt", "cs-card-empty"):
                    try:
                        prompt_lbl.remove_css_class(cls)
                    except Exception:
                        pass
                prompt_lbl.add_css_class("cs-card-empty")
                prompt_lbl.set_label("…")

    def _update_next_thumb(self) -> None:
        """Update the 'Next on TT-TV' thumbnail from the pool's upcoming record."""
        if not self._alive:
            return
        record = self._pool.peek_next()
        if record is None:
            self._next_thumb.set_file(None)
            return
        thumb = getattr(record, "thumbnail_path", None)
        if not thumb:
            # Fall back to video_path or image_path as a static frame source
            thumb = getattr(record, "image_path", None)
        if thumb and Path(thumb).exists():
            self._next_thumb.set_filename(thumb)
        else:
            self._next_thumb.set_file(None)

    def _update_work_lbl(self, depth: int, generating: bool) -> None:
        """Update the queue/generating status label. Must be called on the main thread."""
        if not self._alive:
            return
        if generating and depth > 0:
            self._queue_lbl.set_label(f"🔄  generating +{depth} queued")
        elif generating:
            self._queue_lbl.set_label("🔄  generating")
        elif depth > 0:
            self._queue_lbl.set_label(f"⏳  {depth} queued")
        else:
            self._queue_lbl.set_label("⬤  idle")

    # ── Record addition from MainWindow ───────────────────────────────────

    def add_record(self, record) -> None:
        """
        Called via GLib.idle_add from MainWindow._on_finished when a new
        generation completes. Adds the record to the live pool so it will
        be included in future playback cycles.

        If the pool was empty when this window was opened (no media yet),
        also starts playback now that the first item has arrived.
        """
        if not self._alive:
            return
        if getattr(record, "media_type", "video") == "image":
            return  # images excluded from attractor playback
        path = getattr(record, "video_path", None) or ""
        if path:
            p = Path(path)
            if not p.exists() or p.stat().st_size < 1024:
                # File missing or suspiciously small — skip until it's written.
                _log.warning("add_record: skipping incomplete/missing file: %s (size=%d)",
                             path, p.stat().st_size if p.exists() else -1)
                return
        _log.info("new record added to pool: %s  (pool now %d)",
                  Path(path).name if path else "?", self._pool.size + 1)
        was_empty = self._pool.size == 0
        self._pool.add_record(record)
        self._pool_lbl.set_label(f"🎬  pool: {self._pool.size}")
        self._hud_pool_lbl.set_label(f"pool: {self._pool.size}")
        # Refresh the coming-soon display now that the queue has shifted.
        self._update_coming_soon_ui()
        # Also refresh the "Next on TT-TV" thumbnail since the pool just grew.
        self._update_next_thumb()
        if was_empty and self._started:
            # First item arrived after start() - begin playback now.
            # If start() hasn't fired yet (add_record raced ahead via idle_add),
            # defer to start() which will see pool.size > 0 and call _advance().
            self._advance()

    def remove_record(self, record) -> None:
        """
        Called (on the main thread) when the user deletes a record from the
        main window gallery while TT-TV is open. Removes the item from the
        playback pool so it no longer appears in future advance() calls.
        The currently-playing video is unaffected; if it happens to be the
        deleted record it will finish naturally before the pool skips it.
        """
        if not self._alive:
            return
        record_id = getattr(record, "id", None)
        if record_id is None:
            return
        removed = self._pool.remove_record(record_id)
        if removed:
            self._pool_lbl.set_label(f"🎬  pool: {self._pool.size}")
            self._hud_pool_lbl.set_label(f"pool: {self._pool.size}")
            self._update_next_thumb()
            _log.info("record removed from pool: %s  (pool now %d)",
                      record_id, self._pool.size)

    # ── Channel-change transition ─────────────────────────────────────────

    def _trigger_channel_change(self) -> None:
        """Show a brief channel-change flash overlay, then cut to the next video.

        Sequence:
          0 ms  — flash overlay CSS animation starts (ramps to full brightness)
        150 ms  — _advance() fires: instant stack cut happens under the flash
        750 ms  — CSS animation has faded out; remove the active class
        """
        if not self._alive:
            return
        # Cancel any pending advance timer that may have been set (e.g. fallback timer)
        if self._pending_advance_source is not None:
            GLib.source_remove(self._pending_advance_source)
            self._pending_advance_source = None
        # Cancel any previous flash-clear timer so we can restart the animation
        if self._pending_flash_source:
            GLib.source_remove(self._pending_flash_source)
            self._pending_flash_source = 0
        # Restart the CSS animation: remove then re-add the active class so GTK
        # re-triggers the @keyframes from 0% even if it was already running.
        self._channel_flash.remove_css_class("flash-active")
        self._channel_flash.add_css_class("flash-active")
        # Do the actual video switch at the flash peak (~150 ms in)
        self._pending_advance_source = GLib.timeout_add(150, self._on_channel_peak)

    def _on_channel_peak(self) -> bool:
        """GLib timer: execute the instant video switch during the channel-change flash."""
        self._pending_advance_source = None
        if not self._alive:
            return GLib.SOURCE_REMOVE
        self._advance()
        # Schedule class removal after the full animation (~650 ms for the CSS animation
        # itself, fired 150 ms after the animation started = 500 ms after _advance).
        self._pending_flash_source = GLib.timeout_add(550, self._clear_channel_flash)
        return GLib.SOURCE_REMOVE

    def _clear_channel_flash(self) -> bool:
        """GLib timer: remove the flash active class once the CSS animation has ended."""
        self._pending_flash_source = 0
        if self._alive:
            self._channel_flash.remove_css_class("flash-active")
        return GLib.SOURCE_REMOVE

    def _get_current_video_stream(self):
        """Return the Gtk.MediaStream for the active video slot, or None."""
        slot = self._slot_b if self._active_slot_name == "b" else self._slot_a
        vid = slot._video
        if not vid.get_visible():
            return None
        return vid.get_media_stream()

    # ── TT-TV status bar ──────────────────────────────────────────────────

    def _build_att_status_bar(self) -> Gtk.Box:
        """Slim status strip mirroring the main window status bar."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bar.add_css_class("tt-statusbar")
        bar.set_hexpand(True)

        self._att_srv_dot = Gtk.Label(label="⬤")
        self._att_srv_dot.add_css_class("tt-statusbar-dot-offline")
        bar.append(self._att_srv_dot)

        self._att_srv_lbl = Gtk.Label(label="offline")
        self._att_srv_lbl.add_css_class("tt-statusbar-seg")
        bar.append(self._att_srv_lbl)

        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self._att_queue_lbl = Gtk.Label(label="")
        self._att_queue_lbl.add_css_class("tt-statusbar-seg")
        self._att_queue_lbl.set_visible(False)
        bar.append(self._att_queue_lbl)

        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self._att_disk_lbl = Gtk.Label(label="")
        self._att_disk_lbl.add_css_class("tt-statusbar-seg")
        bar.append(self._att_disk_lbl)

        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self._att_chip_lbl = Gtk.Label(label="")
        self._att_chip_lbl.add_css_class("tt-statusbar-seg")
        self._att_chip_lbl.set_visible(False)
        bar.append(self._att_chip_lbl)

        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # Now-playing card stats: model, steps, generation time.
        # Updated immediately in _advance() on each channel change.
        self._att_now_lbl = Gtk.Label(label="")
        self._att_now_lbl.add_css_class("tt-statusbar-seg")
        self._att_now_lbl.set_visible(False)
        bar.append(self._att_now_lbl)

        return bar

    def _att_status_poll_loop(self) -> None:
        """Background thread: polls server status, disk, and chip telemetry every 10s."""
        import json as _json
        import subprocess as _subprocess

        def _f(val) -> float:
            try:
                return float(val) if val is not None else 0.0
            except (TypeError, ValueError):
                return 0.0

        while not self._att_poll_stop.is_set():
            ready, model = self._get_server_status()
            depth = self._get_queue_depth()
            generating = self._get_is_generating()

            try:
                free = shutil.disk_usage(_STORAGE_DIR).free
                disk_text = f"{free / (1024**3):.0f} GB free"
            except OSError:
                disk_text = "disk ?"

            chip_text = ""
            try:
                proc = _subprocess.run(
                    ["tt-smi", "-s"], capture_output=True, text=True, timeout=5
                )
                if proc.returncode == 0:
                    data = _json.loads(proc.stdout)
                    chips = data.get("device_info", [])
                    if chips:
                        temps  = [_f(c.get("telemetry", {}).get("asic_temperature")) for c in chips]
                        powers = [_f(c.get("telemetry", {}).get("power"))            for c in chips]
                        clocks = [_f(c.get("telemetry", {}).get("aiclk"))            for c in chips]
                        parts: list[str] = []
                        if any(temps):  parts.append(f"{max(temps):.0f}°C")
                        if any(powers): parts.append(f"{sum(powers):.0f}W")
                        if any(clocks): parts.append(f"{max(clocks):.0f}MHz")
                        chip_text = "  ".join(parts)
            except Exception:
                pass

            GLib.idle_add(self._update_att_statusbar,
                          ready, model or "", depth, generating, disk_text, chip_text)
            self._att_poll_stop.wait(10.0)

    def _update_att_statusbar(
        self, ready: bool, model: str, depth: int,
        generating: bool, disk_text: str, chip_text: str,
    ) -> bool:
        """Update the TT-TV status strip. Runs on the main thread via GLib.idle_add."""
        if not self._alive:
            return False

        # Server dot
        for cls in ("tt-statusbar-dot-ready", "tt-statusbar-dot-offline",
                    "tt-statusbar-dot-starting"):
            self._att_srv_dot.remove_css_class(cls)
        self._att_srv_dot.add_css_class(
            "tt-statusbar-dot-ready" if ready else "tt-statusbar-dot-offline"
        )
        # Show model name when known; "online" if server is up but model
        # wasn't detected (not "offline" — the server IS responding).
        self._att_srv_lbl.set_label(
            model if model else ("online" if ready else "offline")
        )

        # Queue / generating status
        if generating and depth > 0:
            q = f"🔄 +{depth} queued"
        elif generating:
            q = "🔄 generating"
        elif depth > 0:
            q = f"⏳ {depth} queued"
        else:
            q = ""
        self._att_queue_lbl.set_label(q)
        self._att_queue_lbl.set_visible(bool(q))

        # Disk
        self._att_disk_lbl.set_label(disk_text)

        # Chip telemetry
        self._att_chip_lbl.set_label(chip_text)
        self._att_chip_lbl.set_visible(bool(chip_text))

        return False


def _hdivider() -> Gtk.Separator:
    """Create a horizontal separator for the sidebar."""
    return Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
