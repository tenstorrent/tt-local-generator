#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
gst_player.py — inline GStreamer video player via gtk4paintablesink.

Used on macOS where the Homebrew GTK4 bottle lacks libmedia-gstreamer.dylib,
so Gtk.Video always shows a blank frame.  GstPlayer builds a native GStreamer
playbin pipeline and routes video frames through gtk4paintablesink into a
Gtk.Picture widget using the GdkPaintable interface — no recompile of GTK4
required.

On Linux the normal Gtk.Video path is used instead (libmedia-gstreamer is
available in the distro packages), so this module is only imported when
_USE_SYSTEM_PLAYER is True.

Thread safety
─────────────
All public methods must be called from the GTK / GLib main thread.
GStreamer bus messages are dispatched to that same thread via
bus.add_signal_watch(), so on_eos and on_error callbacks also fire there.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gst", "1.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gst, GLib, Gtk, Gdk

# Initialise GStreamer exactly once per process.  tt-gen sets GST_PLUGIN_PATH
# before exec'ing Python, so the Homebrew plugin directory is already in the
# environment when we get here.
if not Gst.is_initialized():
    Gst.init(None)

# Inject a small CSS rule so the Gtk.Picture used as the video surface always
# has a black background.  Without this, letterbox/pillarbox areas and any
# video transparency blend against whatever the parent widget's background is
# (which may be a light colour on macOS Adwaita).  Applied once at import time.
_gst_css_provider = Gtk.CssProvider()
_gst_css_provider.load_from_data(
    b"picture.gst-video-surface { background-color: black; }"
)
# The display may not be open yet at import time on some platforms, so we
# defer the install until the first GstPlayer is constructed.
_gst_css_installed: bool = False


class GstPlayer:
    """
    A single-video GStreamer player that renders into a Gtk.Picture widget.

    Typical lifecycle
    ─────────────────
        player = GstPlayer(muted=True)           # for hover/gallery thumbnails
        layout.append(player.widget)             # Gtk.Picture — add to UI once
        player.load("/path/to/clip.mp4")
        player.play()
        player.set_on_eos(lambda: player.seek(0) or player.play())  # loop

        # ... later ...
        player.close()                           # tears down the GStreamer pipeline

    muted=False (default) plays audio.  muted=True silences it — used for the
    hover preview in gallery cards where audio would be distracting.
    """

    def __init__(self, muted: bool = False) -> None:
        global _gst_css_installed

        self._muted = muted
        self._pipeline: Optional[Gst.Element] = None
        self._bus: Optional[Gst.Bus] = None
        self._bus_watch_id: int = 0
        self._on_eos_cb: Optional[Callable[[], None]] = None
        self._ended: bool = False

        # The paintable sink and the Gtk.Picture it feeds are created once and
        # reused across load() calls so the widget stays in the layout.
        self._sink: Optional[Gst.Element] = Gst.ElementFactory.make(
            "gtk4paintablesink", "sink"
        )
        self._picture = Gtk.Picture()
        self._picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        if self._sink is not None:
            self._picture.set_paintable(self._sink.props.paintable)

        # Install the black-background CSS rule the first time any GstPlayer is
        # created (deferred from module level because the GDK display isn't
        # guaranteed open at import time).
        if not _gst_css_installed:
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(
                    display,
                    _gst_css_provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )
                _gst_css_installed = True

        # Tag this Picture so the CSS rule targets it specifically.
        self._picture.add_css_class("gst-video-surface")

    # ── public API ────────────────────────────────────────────────────────

    @property
    def widget(self) -> Gtk.Picture:
        """The Gtk.Picture widget — add to your layout and leave it there."""
        return self._picture

    @property
    def available(self) -> bool:
        """False if gtk4paintablesink could not be created (plugin missing)."""
        return self._sink is not None

    def set_on_eos(self, cb: Optional[Callable[[], None]]) -> None:
        """Register a callback invoked on the GTK main thread when the video ends."""
        self._on_eos_cb = cb

    def load(self, path: str) -> bool:
        """
        Load a video file, replacing any currently-playing pipeline.

        Returns True if the pipeline was created successfully.
        The pipeline starts in PAUSED state (first frame rendered); call play()
        to begin playback.
        """
        self._teardown()
        self._ended = False

        if self._sink is None:
            print("[GstPlayer] gtk4paintablesink not available", file=sys.stderr)
            return False

        playbin = Gst.ElementFactory.make("playbin", "player")
        if playbin is None:
            print("[GstPlayer] playbin element not available", file=sys.stderr)
            return False

        uri = Path(path).resolve().as_uri()
        playbin.props.uri = uri
        playbin.props.video_sink = self._sink
        if self._muted:
            playbin.props.volume = 0.0

        # Route bus messages through the GLib main loop so our callbacks fire
        # on the GTK main thread (safe for widget manipulation).
        bus = playbin.get_bus()
        bus.add_signal_watch()
        self._bus_watch_id = bus.connect("message", self._on_bus_message)
        self._bus = bus

        self._pipeline = playbin

        # PAUSED renders the first frame immediately so the widget is not blank.
        playbin.set_state(Gst.State.PAUSED)
        return True

    def play(self) -> None:
        """Start / resume playback."""
        if self._pipeline:
            self._pipeline.set_state(Gst.State.PLAYING)

    def pause(self) -> None:
        """Pause playback (keeps pipeline alive, first frame stays visible)."""
        if self._pipeline:
            self._pipeline.set_state(Gst.State.PAUSED)

    def seek(self, pos_ns: int = 0) -> None:
        """
        Seek to *pos_ns* nanoseconds from the start (0 = beginning).
        Typically called as seek(0) to loop back to the start.
        """
        if self._pipeline:
            self._pipeline.seek_simple(
                Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                pos_ns,
            )

    def get_playing(self) -> bool:
        """True if the pipeline is currently in the PLAYING state."""
        if self._pipeline is None:
            return False
        _, state, _ = self._pipeline.get_state(0)
        return state == Gst.State.PLAYING

    def get_ended(self) -> bool:
        """True after EOS has been received (reset to False on load())."""
        return self._ended

    def close(self) -> None:
        """
        Tear down the GStreamer pipeline and clear the picture.

        The widget (self.widget) stays valid and can remain in the layout;
        it just shows nothing until the next load() call.
        """
        self._teardown()
        # Clear the displayed frame so the widget doesn't freeze on the last
        # frame of a closed video.
        if self._sink is not None:
            self._picture.set_paintable(self._sink.props.paintable)

    # ── internals ─────────────────────────────────────────────────────────

    def _on_bus_message(self, _bus: Gst.Bus, message: Gst.Message) -> None:
        """GStreamer bus callback — always runs on the GTK main thread."""
        t = message.type
        if t == Gst.MessageType.EOS:
            self._ended = True
            if self._on_eos_cb:
                self._on_eos_cb()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"[GstPlayer] pipeline error: {err.message}", file=sys.stderr)
            if debug:
                print(f"[GstPlayer] debug info: {debug}", file=sys.stderr)

    def _teardown(self) -> None:
        """Shut down the current pipeline without destroying the sink/widget."""
        if self._pipeline is None:
            return
        # NULL state releases all file descriptors and GStreamer resources.
        self._pipeline.set_state(Gst.State.NULL)
        if self._bus is not None:
            if self._bus_watch_id:
                try:
                    self._bus.disconnect(self._bus_watch_id)
                except Exception:
                    pass
            self._bus.remove_signal_watch()
            self._bus = None
        self._bus_watch_id = 0
        self._pipeline = None
        self._ended = False
