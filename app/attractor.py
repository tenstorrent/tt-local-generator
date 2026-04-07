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
from app_settings import settings as _settings  # noqa: E402
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

    # How many positions ahead a "soon" record can land (1 = always next).
    SOON_WINDOW: int = 3

    def add_record(self, record, soon: bool = False) -> None:
        """
        Append a new record and insert its index into the playback order.

        soon=False (default): insert at a random position strictly after
            the current playback position — anywhere from [pos+1, end].
            Preserves natural cycle shuffling for historical records.

        soon=True: insert within the next SOON_WINDOW positions so the new
            record appears on-screen quickly regardless of pool size.  Used
            for freshly generated videos so they debut on TT-TV soon after
            they finish rather than waiting up to a full shuffle cycle.

        No-ops if a record with the same id is already in the pool.
        """
        record_id = getattr(record, "id", None)
        if record_id and any(getattr(r, "id", None) == record_id for r in self._records):
            return  # already in pool — skip
        new_idx = len(self._records)
        self._records.append(record)
        # lower: never insert at pos or before (would rewind to it immediately).
        # If _pos is already past the end (reshuffle pending), only slot is end.
        lower = min(self._pos + 1, len(self._order))
        if soon:
            # Cap the upper bound so the record lands within SOON_WINDOW slots.
            upper = min(lower + self.SOON_WINDOW - 1, len(self._order))
        else:
            upper = len(self._order)
        insert_at = random.randint(lower, upper)
        self._order.insert(insert_at, new_idx)

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
        playlist_id: "str | None" = None,    # None = all videos; str = filter to this playlist
        auto_generate: bool = True,           # whether to run the generation loop
        get_playlists: "Callable[[], list]" = lambda: [],  # for channel switcher dropdown
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
        # Playlist / channel state
        self._playlist_id: "str | None" = playlist_id
        self._auto_generate: bool = auto_generate
        self._get_playlists: Callable = get_playlists
        # Store ALL records so _switch_channel() can refilter from the full set.
        # MainWindow already filtered records to the chosen playlist before passing
        # them in, but we need the full list to support in-window channel switching.
        self._all_records: list = list(records)
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
        # Each _load_slot() call replaces the Gtk.Video widget in the slot rather
        # than reusing it.  Discarded widgets land in this list; a 3-second timer
        # drains the list by pausing + set_file(None)-ing each one so GStreamer
        # fully reaches NULL and releases its file descriptors.  The 3-second
        # delay prevents the PLAYING→NULL teardown from racing with the new
        # pipeline that was just started in the replacement widget.
        self._video_graveyard: list = []      # Gtk.Video widgets waiting for teardown
        self._graveyard_timer: int = 0        # GLib source id for graveyard drain
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
        # Unsubscribe from screensaver D-Bus signals.
        if getattr(self, "_dbus_conn", None):
            for sub_id in getattr(self, "_dbus_sub_ids", []):
                try:
                    self._dbus_conn.signal_unsubscribe(sub_id)
                except Exception:
                    pass
        if self._pending_advance_source is not None:
            GLib.source_remove(self._pending_advance_source)
            self._pending_advance_source = None
        if self._graveyard_timer:
            GLib.source_remove(self._graveyard_timer)
            self._graveyard_timer = 0
        self._video_graveyard.clear()
        if self._pending_flash_source:
            GLib.source_remove(self._pending_flash_source)
            self._pending_flash_source = 0
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

        # ── Channel selector ──────────────────────────────────────────────
        # Shows the current playlist (channel) and lets the user switch
        # without leaving TT-TV.  Uses a simple Gtk.DropDown backed by a
        # Gtk.StringList; rebuilt on open via _populate_channel_dropdown().
        ch_lbl = Gtk.Label(label="CHANNEL")
        ch_lbl.add_css_class("attractor-section-lbl")
        ch_lbl.set_xalign(0)
        ch_lbl.set_max_width_chars(12)
        sidebar.append(ch_lbl)

        self._channel_model = Gtk.StringList()
        self._channel_ids: list = []       # parallel list: index → playlist_id or None
        self._channel_dropdown = Gtk.DropDown(model=self._channel_model)
        self._channel_dropdown.add_css_class("attractor-stat-lbl")
        self._channel_dropdown.set_tooltip_text("Switch TT-TV channel")
        self._populate_channel_dropdown()
        # Connect after populate so the initial set doesn't trigger _switch_channel.
        self._channel_dropdown.connect("notify::selected", self._on_channel_selected)
        sidebar.append(self._channel_dropdown)

        # ── Auto-gen toggle ───────────────────────────────────────────────
        ag_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        ag_lbl = Gtk.Label(label="Auto-gen")
        ag_lbl.add_css_class("attractor-stat-lbl")
        ag_lbl.set_hexpand(True)
        ag_lbl.set_xalign(0)
        ag_row.append(ag_lbl)
        self._autogen_switch = Gtk.Switch()
        self._autogen_switch.set_active(self._auto_generate)
        self._autogen_switch.set_valign(Gtk.Align.CENTER)
        self._autogen_switch.set_tooltip_text("Auto-generate new content for this channel")
        self._autogen_switch.connect("notify::active", self._on_autogen_toggled)
        ag_row.append(self._autogen_switch)
        sidebar.append(ag_row)

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
        _log.info("=== Attractor started - pool size: %d, auto_generate: %s ===",
                  self._pool.size, self._auto_generate)
        self._subscribe_screensaver()
        if self._pool.size > 0:
            self._advance()
        if self._auto_generate:
            threading.Thread(target=self._generation_loop, daemon=True).start()
        threading.Thread(target=self._att_status_poll_loop, daemon=True).start()

    def _subscribe_screensaver(self) -> None:
        """Subscribe to screen-lock signals from every known source.

        Three layers of coverage:
        1. org.freedesktop.ScreenSaver.ActiveChanged at /ScreenSaver
           — KDE Plasma (kscreenlocker)
        2. org.freedesktop.ScreenSaver.ActiveChanged at /org/freedesktop/ScreenSaver
           — GNOME, Cinnamon, and most other DEs
        3. org.gnome.ScreenSaver.ActiveChanged at /org/gnome/ScreenSaver
           — older GNOME / Cinnamon legacy
        4. org.freedesktop.login1.Session.Lock / Unlock on the SYSTEM bus
           — universal: fires regardless of DE because it goes through the
             kernel session manager (systemd-logind). Covers any compositor
             that calls loginctl lock-session (Sway, Hyprland, custom scripts, etc.)

        Without this, the compositor revokes DRM/VA-API access while GStreamer
        pipelines are still running, triggering thousands of gst_poll assertion
        failures per second that exhaust GLib's pipe-creation and kill the process.
        """
        self._dbus_conn = None
        self._dbus_sys_conn = None
        self._dbus_sub_ids: list[int] = []
        self._screen_locked = False   # dedup: ignore duplicate lock/unlock signals

        # ── Session bus: ScreenSaver ActiveChanged (KDE, GNOME, etc.) ─────
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._dbus_conn = bus
            for iface, path in (
                ("org.freedesktop.ScreenSaver", "/ScreenSaver"),
                ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver"),
                ("org.gnome.ScreenSaver",        "/org/gnome/ScreenSaver"),
            ):
                sub_id = bus.signal_subscribe(
                    None, iface, "ActiveChanged", path, None,
                    Gio.DBusSignalFlags.NONE,
                    self._on_screensaver_active_changed,
                )
                self._dbus_sub_ids.append(sub_id)
            _log.debug("screensaver session-bus subscriptions OK (sub_ids=%s)",
                       self._dbus_sub_ids)
        except Exception as exc:
            _log.warning("screensaver session-bus subscription failed: %s", exc)

        # ── System bus: logind Lock / Unlock (universal fallback) ──────────
        try:
            sys_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._dbus_sys_conn = sys_bus
            for signal, lock in (("Lock", True), ("Unlock", False)):
                sub_id = sys_bus.signal_subscribe(
                    "org.freedesktop.login1",
                    "org.freedesktop.login1.Session",
                    signal,
                    None,          # any session path
                    None,
                    Gio.DBusSignalFlags.NONE,
                    self._on_logind_lock_signal,
                )
                self._dbus_sub_ids.append(sub_id)
            _log.debug("screensaver logind system-bus subscriptions OK")
        except Exception as exc:
            _log.warning("screensaver logind system-bus subscription failed: %s", exc)

    def _on_screensaver_active_changed(self, _c, _s, _p, _i, _sig, params, _u):
        """Callback for ScreenSaver.ActiveChanged(bool) — session bus."""
        if not self._alive:
            return
        self._on_screen_lock(bool(params[0]))

    def _on_logind_lock_signal(self, _c, _s, _p, _i, signal, _params, _u):
        """Callback for logind Session.Lock / Session.Unlock — system bus."""
        if not self._alive:
            return
        self._on_screen_lock(signal == "Lock")

    def _on_screen_lock(self, active: bool) -> None:
        """Unified lock/unlock handler — called from any signal source.

        On lock:   fully unload both A/B video slots so GStreamer releases all
                   DRM/VA-API/Wayland surface handles before the compositor takes
                   exclusive control.  Advance and unload timers are also cancelled
                   so nothing tries to open a new pipeline while locked.
        On unlock: reload the last-played record into the active slot and restart
                   the advance schedule.

        The _screen_locked flag deduplicates signals from multiple sources
        (e.g., both logind.Lock and ScreenSaver.ActiveChanged may fire).
        """
        if active == self._screen_locked:
            return  # already in this state — duplicate signal, ignore
        self._screen_locked = active

        if active:
            # Cancel pending timers first so _advance() can't fire mid-teardown.
            if self._pending_advance_source is not None:
                GLib.source_remove(self._pending_advance_source)
                self._pending_advance_source = None
            if self._graveyard_timer:
                GLib.source_remove(self._graveyard_timer)
                self._graveyard_timer = 0
            _unload_slot_video(self._slot_a)
            _unload_slot_video(self._slot_b)
            self._video_graveyard.clear()
            _log.info("screen locked - all GStreamer pipelines released")
        else:
            _log.info("screen unlocked - reloading video")
            try:
                record = self._pool.current_record()
                active_slot = self._slot_b if self._active_slot_name == "b" else self._slot_a
                self._load_slot(active_slot, record)
                if not self._paused:
                    self._schedule_advance(record)
            except RuntimeError:
                # current_record() raises before first advance() — safe to skip.
                pass

    def _advance(self) -> None:
        """
        Load the next media item into the inactive A/B slot and crossfade to it.
        Does nothing if playback is paused.
        """
        if not self._alive or self._paused:
            return

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

    def _flush_video_graveyard(self) -> bool:
        """GLib timer: finalize all discarded Gtk.Video widgets in the graveyard.

        Called 3 seconds after the first widget is added.  By then the new
        pipeline in the replacement widget is fully running, so calling
        set_file(None) on the old widgets cannot race with the new one.
        """
        self._graveyard_timer = 0
        if not self._alive:
            self._video_graveyard.clear()
            return GLib.SOURCE_REMOVE
        for vid in self._video_graveyard:
            stream = vid.get_media_stream()
            if stream is not None:
                try:
                    stream.pause()
                except Exception:
                    pass
            try:
                vid.set_file(None)
            except Exception:
                pass
        self._video_graveyard.clear()
        return GLib.SOURCE_REMOVE

    def _load_slot(self, slot: Gtk.Box, record) -> None:
        """
        Load a GenerationRecord into a media slot widget.
        Shows either the Gtk.Picture (images) or Gtk.Video (videos),
        hiding the other widget.

        For video records, a fresh Gtk.Video widget is created each time.
        The old widget is detached from the slot and queued in
        _video_graveyard; _flush_video_graveyard() finalizes it 3 seconds
        later (well after the new pipeline is running) to avoid the
        PLAYING→NULL async teardown racing with the new pipeline and leaking
        file descriptors (EMFILE).
        """
        if getattr(record, "media_type", "video") == "image":
            slot._video.set_visible(False)
            path = getattr(record, "image_path", None) or getattr(record, "thumbnail_path", None)
            if path:
                slot._picture.set_filename(path)
            slot._picture.set_visible(True)
        else:
            slot._picture.set_visible(False)
            path = getattr(record, "video_path", None)
            if path:
                # Retire the old Gtk.Video widget instead of reusing it.
                # set_filename() on a widget whose pipeline is still in an async
                # PLAYING→NULL teardown (from a prior set_file(None) call) creates
                # two concurrent GStreamer pipelines on the same widget, generating
                # "assertion 'set != NULL' failed" bursts and leaking FDs.
                # Creating a fresh widget sidesteps all teardown races entirely.
                old_vid = slot._video
                slot.remove(old_vid)
                self._video_graveyard.append(old_vid)
                if not self._graveyard_timer:
                    self._graveyard_timer = GLib.timeout_add(3000, self._flush_video_graveyard)

                vid = Gtk.Video()
                vid.set_hexpand(True)
                vid.set_vexpand(True)
                vid.set_autoplay(True)
                # Do NOT set_loop(True) — it prevents notify::ended from firing.
                vid.set_filename(path)
                slot._video = vid
                slot.prepend(vid)   # video before picture in the box
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
            # Read image dwell time from settings so live changes take effect
            # without restarting TT-TV.
            image_dwell_ms = int(_settings.get("tttv_image_dwell_s") * 1000)
            self._pending_advance_source = GLib.timeout_add(
                image_dwell_ms, self._on_advance_timer
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
            # GStreamer pipeline stall), force-advance after the configured fallback.
            video_fallback_ms = int(_settings.get("tttv_video_fallback_s") * 1000)
            _log.debug("stream connected - fallback timer set for %.1f s",
                       video_fallback_ms / 1000)
            self._pending_advance_source = GLib.timeout_add(
                video_fallback_ms, self._on_advance_timer
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
        while not self._gen_stop.wait(0.0) and self._auto_generate:
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
            steps=20,
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
            steps=20,
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
        # If a playlist channel is active, auto-enroll newly generated records
        # into that playlist so the channel grows as TT-TV generates content.
        # Videos generated while watching "Space Adventures" should become part
        # of "Space Adventures" — that's the expected behaviour.
        if self._playlist_id is not None:
            from playlist_store import playlist_store as _ps
            record_id = getattr(record, "id", None)
            pl = _ps.get(self._playlist_id)
            if pl is not None and record_id and record_id not in pl.record_ids:
                _ps.add_records(self._playlist_id, [record_id])
                _log.info("add_record: auto-enrolled %s into active playlist %s",
                          record_id, self._playlist_id)
        # Keep all_records up-to-date for potential _switch_channel() calls.
        self._all_records.append(record)
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
        # soon=True: newly generated videos land within the next SOON_WINDOW
        # slots so they debut on-screen quickly instead of waiting through a
        # full shuffle cycle of potentially 100+ items.
        self._pool.add_record(record, soon=True)
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

    # ── Channel / playlist switching ──────────────────────────────────────

    def _populate_channel_dropdown(self) -> None:
        """
        Fill the channel dropdown with "All Videos" plus every named playlist.
        Selects the entry that matches self._playlist_id without triggering the
        notify::selected signal (called before the signal is connected).
        """
        # Clear existing items
        self._channel_ids = []
        # Remove all items from StringList
        while self._channel_model.get_n_items() > 0:
            self._channel_model.remove(0)

        # "All Videos" is always the first entry (index 0, id = None)
        self._channel_model.append("All Videos")
        self._channel_ids.append(None)

        playlists = self._get_playlists()
        for pl in playlists:
            self._channel_model.append(pl.name)
            self._channel_ids.append(pl.id)

        # Select the entry matching the current playlist
        selected_idx = 0
        for i, pid in enumerate(self._channel_ids):
            if pid == self._playlist_id:
                selected_idx = i
                break
        self._channel_dropdown.set_selected(selected_idx)

    def _on_channel_selected(self, dropdown, _pspec) -> None:
        """Called when the user picks a different entry in the channel dropdown."""
        idx = dropdown.get_selected()
        if idx < 0 or idx >= len(self._channel_ids):
            return
        new_playlist_id = self._channel_ids[idx]
        if new_playlist_id == self._playlist_id:
            return  # same channel, no-op
        self._switch_channel(new_playlist_id)

    def _on_autogen_toggled(self, sw, _pspec) -> None:
        """Called when the auto-gen switch is toggled."""
        self._auto_generate = sw.get_active()
        _log.info("auto-gen toggled: %s", self._auto_generate)
        if self._auto_generate and self._started and self._alive:
            # Restart the generation thread if it was off.
            threading.Thread(target=self._generation_loop, daemon=True).start()
        # If turned off, the running thread's while condition (which checks
        # self._auto_generate) will exit at the top of the next iteration.
        # Persist the preference to the playlist store if a playlist is active.
        if self._playlist_id is not None:
            from playlist_store import playlist_store as _ps
            _ps.set_auto_gen(self._playlist_id, self._auto_generate)

    def _switch_channel(self, new_playlist_id: "str | None") -> None:
        """
        Switch TT-TV to a different playlist channel without closing the window.

        Rebuilds the pool from the stored all_records list filtered to the new
        playlist, updates the auto-gen switch, and triggers a channel-change
        flash to signal the transition to the user.
        """
        from playlist_store import playlist_store as _ps

        self._playlist_id = new_playlist_id

        if new_playlist_id is not None:
            pl = _ps.get(new_playlist_id)
            playlist_record_ids = set(pl.record_ids) if pl else set()
            filtered = [r for r in self._all_records
                        if getattr(r, "id", None) in playlist_record_ids
                        and getattr(r, "media_type", "video") != "image"]
            new_auto_gen = pl.auto_gen if pl else True
        else:
            filtered = [r for r in self._all_records
                        if getattr(r, "media_type", "video") != "image"]
            new_auto_gen = True

        _log.info("_switch_channel → %s: %d records, auto_gen=%s",
                  new_playlist_id, len(filtered), new_auto_gen)

        # Replace the pool.
        self._pool = AttractorPool(filtered)
        self._pool_lbl.set_label(f"🎬  pool: {self._pool.size}")
        self._hud_pool_lbl.set_label(f"pool: {self._pool.size}")
        self._update_coming_soon_ui()
        self._update_next_thumb()

        # Update auto-gen switch (block signal to avoid recursive call).
        was_auto_gen = self._auto_generate
        self._auto_generate = new_auto_gen
        self._autogen_switch.handler_block_by_func(self._on_autogen_toggled)
        self._autogen_switch.set_active(new_auto_gen)
        self._autogen_switch.handler_unblock_by_func(self._on_autogen_toggled)

        # If auto-gen just became enabled and wasn't before, start the thread.
        if self._auto_generate and not was_auto_gen and self._started and self._alive:
            threading.Thread(target=self._generation_loop, daemon=True).start()

        # Flash to signal the channel change, then advance to the first video.
        self._trigger_channel_change()

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

    # ── Chip telemetry helpers (mirroring _StatusBar in main_window.py) ──────────

    # Cached path to tt-smi (None = not yet resolved, "" = not available).
    _tt_smi_path: "str | None" = None
    _TT_SMI_SKIP = ""

    # Known install locations beyond whatever is on PATH at desktop launch time.
    _TT_SMI_SEARCH_PATHS = [
        str(Path.home() / ".tenstorrent-venv" / "bin"),
        "/usr/local/bin",
        "/usr/bin",
    ]

    @classmethod
    def _resolve_tt_smi(cls) -> "str | None":
        """Return the absolute path to a usable tt-smi (>= 4.1), or None.

        The desktop launcher strips ~/.tenstorrent-venv/bin from PATH, so we
        search known locations explicitly.  Result is cached at class level.
        """
        import re
        if cls._tt_smi_path is not None:
            return cls._tt_smi_path or None   # "" sentinel → None

        extended = shutil.which("tt-smi")  # whatever PATH has
        # Build an augmented path string combining $PATH + known locations.
        extra = ":".join(cls._TT_SMI_SEARCH_PATHS)
        found = shutil.which("tt-smi", path=extra) or extended
        if not found:
            cls._tt_smi_path = cls._TT_SMI_SKIP
            return None

        try:
            import subprocess as _sp
            r = _sp.run(
                [found, "--version"],
                capture_output=True, text=True, timeout=5,
                stdin=_sp.DEVNULL,
            )
            m = re.search(r"(\d+)\.(\d+)", (r.stdout + r.stderr).strip())
            if m and (int(m.group(1)), int(m.group(2))) >= (4, 1):
                cls._tt_smi_path = found
                return found
        except Exception:
            pass

        cls._tt_smi_path = cls._TT_SMI_SKIP
        return None

    @staticmethod
    def _read_att_sysfs_clocks() -> list[int]:
        """Read AICLK (MHz) for each chip from sysfs. Instant, no subprocess."""
        clocks: list[int] = []
        try:
            base = Path("/sys/class/tenstorrent")
            for chip_dir in sorted(base.glob("tenstorrent!*")):
                try:
                    clocks.append(int((chip_dir / "tt_aiclk").read_text().strip()))
                except (OSError, ValueError):
                    pass
        except OSError:
            pass
        return clocks

    @staticmethod
    def _clock_to_shade(clock: int, max_clock: int) -> str:
        """Map one chip's aiclk to a shade block relative to the fleet maximum."""
        if max_clock <= 0:
            return "░"
        ratio = clock / max_clock
        if ratio >= 0.85:
            return "█"
        if ratio >= 0.55:
            return "▓"
        if ratio >= 0.25:
            return "▒"
        return "░"

    def _build_chip_text(self) -> str:
        """Return a compact chip summary string for the TT-TV status bar.

        Reads sysfs clocks (always available) for shade blocks + peak clock.
        Adds avg temperature and total power from tt-smi when tt-smi >= 4.1.
        """
        import json as _json
        import subprocess as _sp

        def _f(val) -> float:
            try:
                return float(val) if val is not None else 0.0
            except (TypeError, ValueError):
                return 0.0

        parts: list[str] = []

        # Layer 1: sysfs clocks — instant, no subprocess.
        clocks = self._read_att_sysfs_clocks()
        if clocks:
            max_clk = max(clocks)
            shade_str = "".join(self._clock_to_shade(c, max_clk) for c in clocks)
            parts.append(f"{max_clk} MHz")
        else:
            shade_str = ""

        # Layer 2: tt-smi avg temp + total power (when available).
        tt_smi = self._resolve_tt_smi()
        if tt_smi:
            try:
                result = _sp.run(
                    [tt_smi, "-s"],
                    capture_output=True, text=True, timeout=8,
                    stdin=_sp.DEVNULL,
                )
                if result.returncode == 0:
                    data = _json.loads(result.stdout)
                    chips = data.get("device_info", [])
                    if chips:
                        temps  = [_f(c.get("telemetry", {}).get("asic_temperature"))
                                  for c in chips]
                        powers = [_f(c.get("telemetry", {}).get("power"))
                                  for c in chips]
                        valid_temps = [t for t in temps if t > 0]
                        if valid_temps:
                            parts.insert(0, f"{sum(valid_temps)/len(valid_temps):.0f}°C")
                        if any(p > 0 for p in powers):
                            idx = 1 if parts and "°C" in parts[0] else 0
                            parts.insert(idx, f"{sum(powers):.0f}W")
            except Exception:
                pass   # tt-smi failed — show clock + shade blocks from sysfs only

        if shade_str:
            parts.append(shade_str)

        return "  ".join(parts)

    def _att_status_poll_loop(self) -> None:
        """Background thread: polls server status, disk, and chip telemetry every 10 s.

        Two-stage chip read so the segment is never blank at startup:
          Stage 1 — sysfs clocks only (instant, posted before anything blocks).
          Stage 2 — full telemetry via tt-smi (may take up to ~13 s on cold start).
        """
        # Stage 1: post an instant sysfs-only baseline so _att_chip_lbl is visible
        # immediately, before the slow tt-smi version check + snapshot run.
        clocks = self._read_att_sysfs_clocks()
        if clocks:
            max_clk = max(clocks)
            shade_str = "".join(self._clock_to_shade(c, max_clk) for c in clocks)
            GLib.idle_add(self._apply_att_chip, f"{max_clk} MHz  {shade_str}")

        while not self._att_poll_stop.is_set():
            ready, model = self._get_server_status()
            depth = self._get_queue_depth()
            generating = self._get_is_generating()

            try:
                free = shutil.disk_usage(_STORAGE_DIR).free
                disk_text = f"{free / (1024**3):.0f} GB free"
            except OSError:
                disk_text = "disk ?"

            # Full telemetry (may block up to ~13 s on first call while tt-smi loads).
            chip_text = self._build_chip_text()

            GLib.idle_add(self._update_att_statusbar,
                          ready, model or "", depth, generating, disk_text, chip_text)
            self._att_poll_stop.wait(10.0)

    def _apply_att_chip(self, chip_text: str) -> bool:
        """Update only the chip telemetry label. Called from Stage 1 sysfs read."""
        if not self._alive:
            return False
        self._att_chip_lbl.set_label(chip_text)
        self._att_chip_lbl.set_visible(bool(chip_text))
        return False

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

        # Chip telemetry (reuses the single-label helper for consistency)
        self._apply_att_chip(chip_text)

        return False


def _hdivider() -> Gtk.Separator:
    """Create a horizontal separator for the sidebar."""
    return Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
