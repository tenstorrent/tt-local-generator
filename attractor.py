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
    min-height: 30px;   /* 2 lines - prevents layout shift when text changes */
}
.attractor-prompt-lbl {
    color: @tt_text_muted;
    font-size: 10px;
    font-style: italic;
    min-height: 70px;   /* 5 lines - reserves space so sidebar never shrinks */
}
.attractor-stop-btn {
    background-color: @tt_bg_error_dark;
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
        model_source: str,                    # "video" | "image" | "animate"
        on_enqueue: Callable,                 # MainWindow._on_enqueue compatible signature
        get_queue_depth: Callable[[], int],   # returns len(self._queue) in MainWindow
        get_is_generating: Callable[[], bool] = lambda: False,  # True when worker is active
        system_prompt: str = "",              # unused; kept for caller compatibility
    ) -> None:
        super().__init__(title="Attractor Mode")
        self._system_prompt = system_prompt
        self._model_source = model_source
        self._on_enqueue = on_enqueue
        self._get_queue_depth = get_queue_depth
        self._get_is_generating = get_is_generating
        self._pool = AttractorPool(records)
        self._gen_stop = threading.Event()
        self._paused = False
        self._pending_advance_source: int | None = None  # GLib source id
        self._watched_stream = None          # stream we connected notify::ended to
        self._stream_handler_id: int | None = None  # handler ID so we can disconnect

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
        sidebar.set_hexpand(False)
        sidebar.set_vexpand(True)

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
        self._gen_status_lbl.set_max_width_chars(20)
        self._gen_status_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        sidebar.append(self._gen_status_lbl)

        self._prompt_lbl = Gtk.Label(label="")
        self._prompt_lbl.add_css_class("attractor-prompt-lbl")
        self._prompt_lbl.set_xalign(0)
        self._prompt_lbl.set_wrap(True)
        self._prompt_lbl.set_max_width_chars(20)
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

    # ── Playback ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """
        Begin playback and start the generation loop daemon thread.
        Must be called after the window is presented so the display is ready.
        """
        if self._pool.size == 0:
            self._gen_status_lbl.set_label("No media — generating first…")
        else:
            self._advance()
        threading.Thread(
            target=self._generation_loop, daemon=True
        ).start()

    def _advance(self) -> None:
        """
        Load the next media item into the inactive A/B slot and crossfade to it.
        Does nothing if playback is paused.
        """
        if self._paused:
            return
        self._pool.advance()
        record = self._pool.current_record()

        # Pick the inactive slot (the one not currently showing)
        next_name = "b" if self._active_slot_name == "a" else "a"
        next_slot = self._slot_b if next_name == "b" else self._slot_a

        self._load_slot(next_slot, record)
        self._stack.set_visible_child_name(next_name)
        self._active_slot_name = next_name

        # Update HUD
        prompt_text = (getattr(record, "prompt", "") or "")[:100]
        self._hud_prompt_lbl.set_label(prompt_text)
        self._hud_pool_lbl.set_label(f"pool: {self._pool.size}")

        self._schedule_advance(record)

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
            # Do NOT call set_loop(True) — it prevents the notify::ended signal
            # from firing on Gtk.MediaStream, breaking advance-on-video-end.
            # (See CLAUDE.md: "Gtk.Video.set_loop(True) is unreliable")
            path = getattr(record, "video_path", None)
            if path:
                slot._video.set_filename(path)
            slot._video.set_visible(True)

    def _schedule_advance(self, record) -> None:
        """
        Schedule the next advance() call based on media type.
        Images use a timer (avg_video_duration seconds).
        Videos connect to the stream's notify::ended signal.
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
            ms = int(self._pool.avg_video_duration * 1000)
            self._pending_advance_source = GLib.timeout_add(ms, self._on_advance_timer)
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
                GLib.idle_add(self._advance)
                return
            # Safety net: if notify::ended never fires (broken/missing file),
            # force-advance after 2× the average video duration.
            fallback_ms = int(self._pool.avg_video_duration * 2 * 1000)
            self._pending_advance_source = GLib.timeout_add(
                fallback_ms, self._on_advance_timer
            )
        else:
            # Stream not initialised yet — retry
            self._pending_advance_source = GLib.timeout_add(
                500, self._retry_connect_stream
            )

    def _on_advance_timer(self) -> bool:
        """GLib timeout callback for image dwell time. Advances to next item."""
        self._pending_advance_source = None
        self._advance()
        return False  # one-shot

    def _on_video_ended(self, stream, _param) -> None:
        """Called when the active video stream's ended property changes."""
        if stream.get_ended():
            self._advance()

    def _retry_connect_stream(self) -> bool:
        """Fallback: GStreamer stream not ready at _schedule_advance time; try again."""
        self._pending_advance_source = None
        self._connect_video_ended()
        return False

    # ── Generation loop ───────────────────────────────────────────────────

    def _generation_loop(self) -> None:
        """
        Background daemon thread. Continuously generates prompts and enqueues
        new generation jobs via on_enqueue callback.

        Back-pressure: if queue depth >= 2, waits 30 s before retrying (server
        isn't consuming jobs fast enough). Stops when _gen_stop is set.
        """
        while not self._gen_stop.wait(0.0):
            depth = self._get_queue_depth()
            generating = self._get_is_generating()
            GLib.idle_add(self._update_work_lbl, depth, generating)

            if depth >= 2:
                GLib.idle_add(self._set_gen_status, "⏸  queue full…")
                if self._gen_stop.wait(30.0):
                    break
                continue

            try:
                GLib.idle_add(self._set_gen_status, "✦  writing prompt…")
                prompt = prompt_client.generate_prompt(
                    source=self._model_source,
                    seed_text="",
                )
                GLib.idle_add(self._prompt_lbl.set_label, prompt)
                GLib.idle_add(self._set_gen_status, "⏳  submitted")
                GLib.idle_add(self._enqueue_generation, prompt)
            except Exception as exc:
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

    def _set_gen_status(self, text: str) -> None:
        """Update the generation status label. Must be called on the main thread."""
        self._gen_status_lbl.set_label(text)

    def _update_work_lbl(self, depth: int, generating: bool) -> None:
        """Update the queue/generating status label. Must be called on the main thread."""
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
        was_empty = self._pool.size == 0
        self._pool.add_record(record)
        self._pool_lbl.set_label(f"🎬  pool: {self._pool.size}")
        self._hud_pool_lbl.set_label(f"pool: {self._pool.size}")
        if was_empty:
            # First item arrived — start playback
            self._advance()

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
