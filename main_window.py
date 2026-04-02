#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Main window and all UI widgets for the TT Video Generator — GTK4 implementation.

Threading discipline (CRITICAL):
    GTK is single-threaded. Worker threads must NEVER touch widgets directly.
    Every UI update from a thread must be posted via GLib.idle_add(fn, *args).
    Forgetting this causes silent data corruption or hard crashes.

Classes:
    GenerationCard   — card widget for one completed video
    GalleryWidget    — scrollable flow grid of GenerationCards
    PendingCard      — animated placeholder while a job runs
    ControlPanel     — left panel: prompt form, queue, server status
    HealthWorker     — background thread for /tt-liveness polling
    RecoveryDialog   — modal listing unknown server jobs to re-attach
    MainWindow       — top-level Gtk.ApplicationWindow
"""
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import GdkPixbuf, GLib, Gtk, Pango

from api_client import APIClient
from history_store import GenerationRecord, HistoryStore
from worker import AnimateGenerationWorker, GenerationWorker, ImageGenerationWorker


# ── Tenstorrent dark palette as GTK CSS ───────────────────────────────────────

_CSS = b"""
window, .view {
    background-color: #0F2A35;
    color: #E8F0F2;
}
* {
    font-family: "Noto Sans", "Segoe UI", sans-serif;
    font-size: 13px;
    color: #E8F0F2;
}
.section-label {
    color: #4FD1C5;
    font-weight: bold;
    font-size: 11px;
}
.muted {
    color: #607D8B;
    font-size: 11px;
}
.teal {
    color: #4FD1C5;
}
entry, textview, spinbutton {
    background-color: #1A3C47;
    color: #E8F0F2;
    border: 1px solid #2D5566;
    border-radius: 4px;
    padding: 4px;
}
entry:focus, textview:focus, spinbutton:focus {
    border-color: #4FD1C5;
}
button {
    background-color: #1A3C47;
    color: #E8F0F2;
    border: 1px solid #2D5566;
    border-radius: 4px;
    padding: 5px 10px;
}
button:hover {
    background-color: #2D5566;
    border-color: #4FD1C5;
}
button:disabled {
    color: #607D8B;
    border-color: #1A3C47;
}
.generate-btn {
    background-color: #4FD1C5;
    color: #0F2A35;
    font-weight: bold;
    font-size: 14px;
    padding: 10px;
    border: none;
    border-radius: 4px;
}
.generate-btn:hover {
    background-color: #81E6D9;
}
.generate-btn:disabled {
    background-color: #2D5566;
    color: #607D8B;
}
.cancel-btn {
    background-color: #2D1A1A;
    color: #FF6B6B;
    border: 1px solid #FF6B6B;
    border-radius: 4px;
    padding: 8px;
}
.cancel-btn:hover {
    background-color: #FF6B6B;
    color: #0F2A35;
}
.card {
    background-color: #1A3C47;
    border: 1px solid #2D5566;
    border-radius: 6px;
    padding: 8px;
}
.card:hover {
    border-color: #4FD1C5;
}
.queue-row {
    background-color: #1A3C47;
    border: 1px solid #2D5566;
    border-radius: 3px;
    padding: 3px 6px;
}
.status-bar {
    background-color: #0A1F28;
    color: #607D8B;
    border-top: 1px solid #1A3C47;
    padding: 3px 8px;
    font-size: 12px;
}
progressbar trough {
    background-color: #1A3C47;
    border: 1px solid #2D5566;
    border-radius: 3px;
    min-height: 8px;
}
progressbar progress {
    background-color: #4FD1C5;
    border-radius: 3px;
}
scrollbar {
    background-color: #0F2A35;
}
scrollbar slider {
    background-color: #2D5566;
    border-radius: 5px;
    min-width: 8px;
    min-height: 8px;
}
scrollbar slider:hover {
    background-color: #4FD1C5;
}
.card-selected {
    border-color: #4FD1C5;
    border-width: 2px;
}
.card-selected-image {
    border-color: #EC96B8;
    border-width: 2px;
}
.type-badge-video {
    background-color: #1A3C47;
    color: #4FD1C5;
    border: 1px solid #4FD1C5;
    border-radius: 3px;
    padding: 0px 4px;
    font-size: 10px;
    font-weight: bold;
}
.type-badge-image {
    background-color: #2D1A2D;
    color: #EC96B8;
    border: 1px solid #EC96B8;
    border-radius: 3px;
    padding: 0px 4px;
    font-size: 10px;
    font-weight: bold;
}
.type-badge-model {
    background-color: #0F2A35;
    color: #607D8B;
    border: 1px solid #2D5566;
    border-radius: 3px;
    padding: 0px 4px;
    font-size: 10px;
}
.section-label {
    margin-top: 8px;
}
.hint {
    color: #4A6572;
    font-size: 10px;
    margin-top: -2px;
}
.detail-section {
    color: #4FD1C5;
    font-weight: bold;
    font-size: 11px;
    margin-top: 6px;
}
.mono {
    font-family: monospace;
    font-size: 11px;
    color: #607D8B;
}
.detail-empty {
    color: #2D5566;
    font-size: 15px;
}
.chip-btn {
    background-color: #0F2A35;
    color: #81E6D9;
    border: 1px solid #2D5566;
    border-radius: 12px;
    padding: 2px 8px;
    font-size: 11px;
}
.chip-btn:hover {
    background-color: #1A3C47;
    border-color: #4FD1C5;
    color: #E8F0F2;
}
.source-btn {
    background-color: #1A3C47;
    color: #607D8B;
    border: 1px solid #2D5566;
    border-radius: 0;
    padding: 4px 10px;
    font-size: 12px;
}
.source-btn:hover {
    background-color: #2D5566;
    color: #E8F0F2;
}
.source-btn-left {
    border-radius: 4px 0 0 4px;
}
.source-btn-mid {
    border-radius: 0;
    border-left-width: 0;
}
.source-btn-right {
    border-radius: 0 4px 4px 0;
    border-left-width: 0;
}
.source-btn-active {
    background-color: #4FD1C5;
    color: #0F2A35;
    border-color: #4FD1C5;
    font-weight: bold;
}
.source-btn-active:hover {
    background-color: #81E6D9;
}
.server-start-btn {
    background-color: #1A3C47;
    color: #4FD1C5;
    border: 1px solid #4FD1C5;
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 12px;
}
.server-start-btn:hover {
    background-color: #2D5566;
}
.server-start-btn:disabled {
    color: #607D8B;
    border-color: #2D5566;
}
.server-stop-btn {
    background-color: #2D1A1A;
    color: #FF6B6B;
    border: 1px solid #FF6B6B;
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 12px;
}
.server-stop-btn:hover {
    background-color: #FF6B6B;
    color: #0F2A35;
}
.server-stop-btn:disabled {
    color: #607D8B;
    border-color: #2D5566;
    background-color: #1A3C47;
}
.trash-btn {
    background-color: transparent;
    color: #607D8B;
    border: none;
    border-radius: 4px;
    padding: 2px 5px;
    font-size: 12px;
    min-width: 0;
}
.trash-btn:hover {
    background-color: #2D1A1A;
    color: #FF6B6B;
}
.server-log {
    font-family: monospace;
    font-size: 10px;
    color: #81E6D9;
    background-color: #0A1F28;
    padding: 4px;
}
"""

# ── Prompt component chips ────────────────────────────────────────────────────
# Each entry: (button label, text appended to prompt, tooltip)
# Grouped roughly by category: camera, lighting, motion, style, quality.

_PROMPT_CHIPS = [
    # Camera / shot
    ("🎥 cinematic",        "cinematic shot",                  "Wide-format filmic look"),
    ("🚁 aerial",           "aerial drone shot",               "Top-down or bird's-eye view"),
    ("🔭 dolly in",         "slow dolly in",                   "Camera glides forward"),
    ("↩ pan left",          "slow pan left",                   "Camera sweeps left"),
    ("🔄 orbit",            "orbiting camera",                 "Camera circles the subject"),
    ("📷 close-up",         "extreme close-up",                "Tight shot on subject"),
    ("🏔 wide shot",         "wide establishing shot",          "Full scene context"),
    ("👁 POV",              "point of view shot",              "First-person perspective"),
    # Lighting
    ("🌅 golden hour",      "golden hour lighting",            "Warm sunrise/sunset glow"),
    ("🌙 moonlit",          "moonlight, night scene",          "Cool blue-silver night light"),
    ("💡 neon",             "neon-lit, cyberpunk lighting",    "Vivid colored neon signs"),
    ("⚡ dramatic",         "dramatic chiaroscuro lighting",   "High contrast light and shadow"),
    ("☀ harsh noon",        "harsh noon sunlight, overexposed","Bright midday bleaching"),
    ("🕯 candlelit",        "warm candlelight, flickering",   "Intimate low orange light"),
    # Motion / mood
    ("🌊 slow motion",      "slow motion, 240fps look",        "Stretched, fluid movement"),
    ("⏩ time-lapse",        "time-lapse, sped-up motion",      "Fast-forwarded world"),
    ("🌬 windy",            "strong wind, hair and leaves moving", "Environmental motion cues"),
    ("🔥 intense",          "intense, high energy, dynamic",   "Kinetic, fast-paced feel"),
    ("😌 calm",             "calm, serene, peaceful atmosphere","Tranquil, slow-moving"),
    # Style
    ("🎞 film grain",       "35mm film grain, analog",         "Vintage celluloid texture"),
    ("🖤 noir",             "black and white, film noir",      "High-contrast monochrome"),
    ("🎨 painterly",        "painterly, impressionist style",  "Brushstroke, artistic look"),
    ("🌈 vibrant",          "vibrant colors, oversaturated",   "Bold, punchy color grading"),
    ("🧊 cold tones",       "cold color grading, blue tones",  "Icy, desaturated blues"),
    # Quality / composition
    ("✨ 4K",               "4K, ultra-detailed, sharp",       "High resolution detail"),
    ("📐 rule of thirds",   "rule of thirds composition",      "Classic photographic framing"),
    ("🌁 depth of field",   "shallow depth of field, bokeh",   "Blurred background, sharp subject"),
    ("🎭 photorealistic",   "photorealistic, hyperrealistic",  "Looks like real footage"),
]

_IMAGE_PROMPT_CHIPS = [
    # Artistic style
    ("🎨 oil painting",    "oil painting, thick brushstrokes",    "Classic oil painting style"),
    ("🖋 line art",        "detailed line art",                    "Clean ink illustration"),
    ("📸 photorealistic",  "photorealistic, DSLR photo",           "Looks like a real photograph"),
    ("🔮 fantasy",         "fantasy art, magical atmosphere",      "Otherworldly, mystical look"),
    ("🎭 concept art",     "concept art, digital painting",        "Professional concept illustration"),
    ("🖤 noir",            "black and white, high contrast",       "Monochrome film noir"),
    ("🌈 vibrant",         "vibrant colors, oversaturated",        "Bold punchy color grading"),
    ("🧊 cold tones",      "cold color grading, blue tones",       "Icy desaturated blues"),
    ("🎞 film grain",      "35mm film grain, analog",              "Vintage film texture"),
    # Lighting
    ("🌅 golden hour",     "golden hour lighting, warm glow",      "Warm sunrise/sunset light"),
    ("💡 studio",          "studio lighting, soft box",            "Clean professional lighting"),
    ("⚡ dramatic",        "dramatic chiaroscuro lighting",        "High contrast shadows"),
    ("🌙 moonlit",         "moonlight, night scene",               "Cool blue-silver night"),
    ("🕯 candlelit",       "warm candlelight, intimate",           "Soft amber glow"),
    ("💡 neon",            "neon-lit, cyberpunk lighting",         "Vivid colored neon signs"),
    # Composition / quality
    ("📐 rule of thirds",  "rule of thirds composition",           "Classic photographic framing"),
    ("🌁 depth of field",  "shallow depth of field, bokeh",        "Blurred background, sharp subject"),
    ("📷 close-up",        "extreme close-up, macro",              "Fine detail, macro shot"),
    ("🏔 wide shot",       "wide establishing shot",               "Full scene context"),
    ("🔲 symmetrical",     "perfectly symmetrical composition",    "Mirror-perfect balance"),
    ("✨ ultra detail",    "ultra-detailed, 8K, sharp",            "Maximum detail and resolution"),
    ("🌟 cinematic",       "cinematic composition, anamorphic",    "Widescreen filmic look"),
]

_THUMB_W = 200
_THUMB_H = 112   # 16:9
_DETAIL_VIDEO_W = 480
_DETAIL_VIDEO_H = 270

# Maps internal model ID strings to short display names shown on gallery badges.
# Empty string → no badge (legacy records without model attribution).
_MODEL_DISPLAY: dict = {
    "wan2.2-t2v":       "Wan2.2",
    "mochi-1-preview":  "Mochi-1",
    "flux.1-dev":       "FLUX",
}


def _apply_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gtk.Widget.get_display(Gtk.Window()),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


def _load_pixbuf(path: str, width: int, height: int) -> Optional[GdkPixbuf.Pixbuf]:
    """Load and scale an image file; returns None on failure."""
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(path)
        return pb.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
    except Exception:
        return None


def _make_image_widget(path: str, width: int, height: int, placeholder: str = "🎬") -> Gtk.Widget:
    """Return a Gtk.Picture sized to width×height, or a label placeholder."""
    pb = _load_pixbuf(path, width, height)
    if pb:
        pic = Gtk.Picture.new_for_pixbuf(pb)
        pic.set_size_request(width, height)
        pic.set_can_shrink(False)
        return pic
    lbl = Gtk.Label(label=placeholder)
    lbl.set_size_request(width, height)
    lbl.add_css_class("muted")
    return lbl


def _make_scalable_thumb(path: str, min_width: int, min_height: int,
                         placeholder: str = "🎬") -> Gtk.Widget:
    """
    Load an image for gallery-card thumbnail display.  Unlike _make_image_widget
    (which pre-scales to exact pixel dimensions), this version loads at the file's
    native resolution and marks the Picture as expandable, so it fills the width
    that the FlowBox cell allocates rather than being stuck at a fixed pixel size.

    can_shrink=True  — widget may be allocated less than the image's natural size
    hexpand=True     — widget fills the full horizontal cell width
    size_request     — provides a hard minimum so cards never become too tiny
    """
    if path and Path(path).exists():
        pic = Gtk.Picture.new_for_filename(path)
        pic.set_can_shrink(True)
        pic.set_hexpand(True)
        pic.set_size_request(min_width, min_height)
        return pic
    lbl = Gtk.Label(label=placeholder)
    lbl.set_size_request(min_width, min_height)
    lbl.set_hexpand(True)
    lbl.add_css_class("muted")
    return lbl


# ── Queue item ─────────────────────────────────────────────────────────────────

@dataclass
class _QueueItem:
    prompt: str
    negative_prompt: str
    steps: int
    seed: int
    seed_image_path: str = ""
    model_source: str = "video"     # "video" (Wan2.2), "image" (FLUX), or "animate"
    guidance_scale: float = 3.5     # used when model_source == "image"
    ref_video_path: str = ""        # used when model_source == "animate"
    ref_char_path: str = ""         # used when model_source == "animate"
    animate_mode: str = "animation" # "animation" or "replacement"
    model_id: str = ""               # specific model within the category, e.g. "wan2", "mochi", "flux"


# ── Generation card ────────────────────────────────────────────────────────────

class GenerationCard(Gtk.Frame):
    """
    Thumbnail card in the gallery. Click anywhere on the card to select it and
    show full details in the DetailPanel.
    Buttons: 💾 Save, ↺ Iterate, 🗑 Delete.
    select_cb(self) is called when the card is clicked.
    delete_cb(record) is called when the trash button is clicked.
    """

    def __init__(self, record: GenerationRecord, iterate_cb, select_cb, delete_cb):
        super().__init__()
        self._record = record
        self._iterate_cb = iterate_cb
        self._select_cb = select_cb
        self._delete_cb = delete_cb
        self.add_css_class("card")
        # Minimum card width; FlowBox homogeneous layout makes all cells equal width
        # and expands them to fill the row, so actual width adapts to the pane size.
        self.set_size_request(_THUMB_W + 20, -1)
        self.set_hexpand(True)
        self._build()

        # Clicking anywhere on the card selects it in the detail panel.
        gesture = Gtk.GestureClick()
        gesture.connect("pressed", lambda *_: self._select_cb(self))
        self.add_controller(gesture)

        # Hovering over a video card plays it in the thumbnail area.
        # Image cards (FLUX) don't have a video to play, so no hover controller.
        if record.video_exists:
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", self._on_hover_enter)
            motion.connect("leave", self._on_hover_leave)
            self.add_controller(motion)

    def set_selected(self, selected: bool) -> None:
        # Image cards use a pink selection border; video cards use teal.
        css_class = ("card-selected-image"
                     if self._record.media_type == "image"
                     else "card-selected")
        if selected:
            self.add_css_class(css_class)
        else:
            self.remove_css_class(css_class)

    def _build(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        self.set_child(box)

        # Media area: thumbnail normally; hover swaps in a silent looping video preview.
        # The stack expands horizontally so the thumbnail fills the FlowBox cell width.
        self._media_stack = Gtk.Stack()
        self._media_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._media_stack.set_transition_duration(120)
        self._media_stack.set_hexpand(True)

        # Use _make_scalable_thumb so the thumbnail grows with the card width
        # instead of being clamped to the fixed _THUMB_W pixel value.
        placeholder = "🖼" if self._record.media_type == "image" else "🎬"
        thumb = _make_scalable_thumb(
            self._record.thumbnail_path if self._record.thumbnail_exists else "",
            _THUMB_W, _THUMB_H, placeholder,
        )
        self._media_stack.add_named(thumb, "thumb")

        if self._record.video_exists:
            self._hover_video = Gtk.Video.new_for_filename(self._record.video_path)
            self._hover_video.set_autoplay(False)
            self._hover_video.set_loop(True)
            self._hover_video.set_hexpand(True)
            self._hover_video.set_size_request(_THUMB_W, _THUMB_H)
            self._media_stack.add_named(self._hover_video, "video")
        else:
            self._hover_video = None
        # Tracks whether we've wired notify::ended on the media stream for manual
        # looping.  The stream is created lazily by GStreamer (it's None until the
        # Video widget is first realized), so we connect on first play attempt.
        self._loop_connected = False

        box.append(self._media_stack)

        # Prompt (2-line max, tooltip shows full text)
        prompt_lbl = Gtk.Label(label=self._record.prompt)
        prompt_lbl.set_wrap(True)
        prompt_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        prompt_lbl.set_max_width_chars(26)
        prompt_lbl.set_lines(2)
        prompt_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        prompt_lbl.set_tooltip_text(self._record.prompt)
        prompt_lbl.set_xalign(0)
        box.append(prompt_lbl)

        # Meta row: type badge + time on left, generation duration on right
        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        # Small badge: "IMG" (pink) or "VID" (teal) so type is visible at a glance
        badge_text = "IMG" if self._record.media_type == "image" else "VID"
        badge_css = ("type-badge-image"
                     if self._record.media_type == "image"
                     else "type-badge-video")
        badge = Gtk.Label(label=badge_text)
        badge.add_css_class(badge_css)
        meta.append(badge)

        time_lbl = Gtk.Label(label=self._record.display_time)
        time_lbl.add_css_class("muted")
        dur_text = _fmt_duration(self._record.duration_s) if self._record.duration_s else ""
        dur_lbl = Gtk.Label(label=dur_text)
        dur_lbl.add_css_class("muted")
        meta.append(time_lbl)
        meta_spacer = Gtk.Box()
        meta_spacer.set_hexpand(True)
        meta.append(meta_spacer)
        meta.append(dur_lbl)
        box.append(meta)

        # Buttons: Save, Iterate, and Trash (play/fullscreen are in the detail panel).
        # Trash is right-aligned to keep it visually separated from the safe actions.
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        export_btn = Gtk.Button(label="💾 Save")
        tip = "Export image to a chosen location" if self._record.media_type == "image" else "Export video to a chosen location"
        export_btn.set_tooltip_text(tip)
        export_btn.connect("clicked", self._export)
        iter_btn = Gtk.Button(label="↺ Iterate")
        iter_btn.set_tooltip_text("Re-use this prompt in the control panel")
        iter_btn.connect("clicked", self._iterate)
        if not self._record.media_exists:
            export_btn.set_sensitive(False)
        btn_row.append(export_btn)
        btn_row.append(iter_btn)

        btn_spacer = Gtk.Box()
        btn_spacer.set_hexpand(True)
        btn_row.append(btn_spacer)

        trash_btn = Gtk.Button(label="🗑")
        trash_btn.add_css_class("trash-btn")
        trash_btn.set_tooltip_text("Delete this generation from history and disk (irreversible)")
        # Stop click event from bubbling up to the card's GestureClick (which would
        # select the card while it's being deleted).
        trash_btn.connect("clicked", self._on_trash_clicked)
        btn_row.append(trash_btn)

        box.append(btn_row)

    def _on_hover_enter(self, _ctrl, _x, _y) -> None:
        """Start looping the video silently when the mouse enters the card."""
        if self._hover_video is None:
            return
        self._media_stack.set_visible_child_name("video")
        self._play_hover_stream()

    def _play_hover_stream(self) -> None:
        """
        Play the hover video stream, wiring up the manual loop handler the first
        time.  Gtk.Video creates its GStreamer pipeline lazily — get_media_stream()
        returns None until the widget has been realized, so we guard here and let
        the caller retry if needed.
        """
        if self._hover_video is None:
            return
        stream = self._hover_video.get_media_stream()
        if stream is None:
            # Stream not yet initialised — try again after the widget settles.
            GLib.timeout_add(100, self._play_hover_stream)
            return
        if not self._loop_connected:
            stream.connect("notify::ended", self._on_stream_ended)
            self._loop_connected = True
        if not stream.get_playing():
            stream.play()

    def _on_stream_ended(self, stream, _param) -> None:
        """Seek back to the start and keep playing for seamless in-card looping."""
        if stream.get_ended() and self._media_stack.get_visible_child_name() == "video":
            stream.seek(0)
            GLib.idle_add(stream.play)

    def _on_hover_leave(self, _ctrl) -> None:
        """Stop the video and revert to the thumbnail when the mouse leaves."""
        if self._hover_video is None:
            return
        stream = self._hover_video.get_media_stream()
        if stream is not None:
            stream.pause()
        self._media_stack.set_visible_child_name("thumb")

    def _export(self, _btn) -> None:
        if not self._record.media_exists:
            return
        dlg = Gtk.FileDialog()
        if self._record.media_type == "image":
            dlg.set_title("Export Image")
            dlg.set_initial_name("image_export.jpg")
        else:
            dlg.set_title("Export Video")
            dlg.set_initial_name("video_export.mp4")
        dlg.save(self.get_root(), None, self._export_done)

    def _export_done(self, dlg, result) -> None:
        try:
            gfile = dlg.save_finish(result)
        except Exception:
            return
        dest = gfile.get_path()
        if dest:
            src = self._record.media_file_path
            shutil.copy2(src, dest)
            src_txt = Path(src).with_suffix(".txt")
            if src_txt.exists():
                shutil.copy2(src_txt, Path(dest).with_suffix(".txt"))

    def _iterate(self, _btn) -> None:
        self._iterate_cb(
            self._record.prompt,
            self._record.negative_prompt,
            self._record.seed_image_path,
        )

    def _on_trash_clicked(self, btn) -> None:
        """Propagate the delete request upward; prevent the click from selecting the card."""
        # Stop propagation so the card's GestureClick (which selects the card) does
        # not fire for the same click that requested a deletion.
        btn.set_sensitive(False)  # immediate visual feedback; card is about to be removed
        self._delete_cb(self._record)


# ── Duration formatting helper ────────────────────────────────────────────────

def _fmt_duration(seconds: float) -> str:
    """Format a duration in seconds to a human-readable string like '5m 12s' or '42s'."""
    s = int(seconds)
    m, s = divmod(s, 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


# ── Detail panel ───────────────────────────────────────────────────────────────

class DetailPanel(Gtk.ScrolledWindow):
    """
    Right-side panel showing the selected video at a larger size with full
    generation metadata. Populated by show_record(); shows a placeholder when empty.
    """

    def __init__(self):
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_vexpand(True)
        self.set_hexpand(False)
        self.set_size_request(420, -1)
        self._record: Optional[GenerationRecord] = None
        self._iterate_cb = None
        self._video_widget: Optional[Gtk.Video] = None
        self._play_btn: Optional[Gtk.Button] = None
        self._show_empty()

    def _show_empty(self) -> None:
        """Render the placeholder 'no selection' state."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)
        box.set_hexpand(True)
        lbl = Gtk.Label(label="← Click a card to preview")
        lbl.add_css_class("detail-empty")
        lbl.set_vexpand(True)
        lbl.set_valign(Gtk.Align.CENTER)
        lbl.set_halign(Gtk.Align.CENTER)
        box.append(lbl)
        self.set_child(box)

    def clear(self) -> None:
        """Revert the panel to its empty placeholder state (e.g. after the shown record is deleted)."""
        self._record = None
        if self._video_widget is not None:
            stream = self._video_widget.get_media_stream()
            if stream and stream.get_playing():
                stream.pause()
        self._video_widget = None
        self._play_btn = None
        self._show_empty()

    def show_record(self, record: GenerationRecord, iterate_cb) -> None:
        """Populate the panel with a completed generation record."""
        self._record = record
        self._iterate_cb = iterate_cb

        # Pause any previously playing video before replacing it
        if self._video_widget is not None:
            stream = self._video_widget.get_media_stream()
            if stream and stream.get_playing():
                stream.pause()
        self._video_widget = None
        self._play_btn = None

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        # ── Media area: video player or image viewer ──────────────────────────
        if record.media_type == "image":
            # FLUX image — show at full detail size with no playback controls
            if record.image_exists:
                img_widget = _make_image_widget(record.image_path, _DETAIL_VIDEO_W, _DETAIL_VIDEO_H, "🖼")
            elif record.thumbnail_exists:
                img_widget = _make_image_widget(record.thumbnail_path, _DETAIL_VIDEO_W, _DETAIL_VIDEO_H, "🖼")
            else:
                img_widget = _make_image_widget("", _DETAIL_VIDEO_W, _DETAIL_VIDEO_H, "🖼\n(image not found)")
            img_widget.set_halign(Gtk.Align.START)
            content.append(img_widget)
            # Export action row for images (no play/fullscreen)
            img_ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            open_full_btn = Gtk.Button(label="⛶ View Full")
            open_full_btn.set_tooltip_text("Open image in a maximized window")
            open_full_btn.connect("clicked", self._open_image_fullscreen)
            if not record.image_exists:
                open_full_btn.set_sensitive(False)
            img_ctrl.append(open_full_btn)
            content.append(img_ctrl)
        elif record.video_exists:
            # Wan2.2 video — inline player with play/pause + fullscreen
            self._video_widget = Gtk.Video.new_for_filename(record.video_path)
            self._video_widget.set_autoplay(False)
            self._video_widget.set_loop(True)
            self._video_widget.set_size_request(_DETAIL_VIDEO_W, _DETAIL_VIDEO_H)
            self._video_widget.set_hexpand(False)
            self._video_widget.set_halign(Gtk.Align.START)
            content.append(self._video_widget)

            ctrl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self._play_btn = Gtk.Button(label="▶ Play")
            self._play_btn.connect("clicked", self._toggle_play)
            ctrl_row.append(self._play_btn)
            full_btn = Gtk.Button(label="⛶ Fullscreen")
            full_btn.set_tooltip_text("Open in maximized window (F for true fullscreen)")
            full_btn.connect("clicked", self._open_fullscreen)
            ctrl_row.append(full_btn)
            content.append(ctrl_row)
        else:
            # Video file missing — show large thumbnail or placeholder
            if record.thumbnail_exists:
                thumb = _make_image_widget(record.thumbnail_path, _DETAIL_VIDEO_W, _DETAIL_VIDEO_H)
            else:
                thumb = _make_image_widget("", _DETAIL_VIDEO_W, _DETAIL_VIDEO_H, "🎬\n(video not found)")
            content.append(thumb)

        # ── Prompt ────────────────────────────────────────────────────────────
        content.append(self._detail_section("Prompt"))
        prompt_lbl = Gtk.Label(label=record.prompt)
        prompt_lbl.set_wrap(True)
        prompt_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        prompt_lbl.set_xalign(0)
        prompt_lbl.set_selectable(True)
        content.append(prompt_lbl)

        # ── Negative prompt (only if set) ─────────────────────────────────────
        if record.negative_prompt:
            content.append(self._detail_section("Negative Prompt"))
            neg_lbl = Gtk.Label(label=record.negative_prompt)
            neg_lbl.set_wrap(True)
            neg_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            neg_lbl.set_xalign(0)
            neg_lbl.add_css_class("muted")
            neg_lbl.set_selectable(True)
            content.append(neg_lbl)

        # ── Generation metadata grid ──────────────────────────────────────────
        content.append(self._detail_section("Generation Info"))
        info_grid = Gtk.Grid()
        info_grid.set_column_spacing(12)
        info_grid.set_row_spacing(3)

        # Format date
        try:
            from datetime import datetime as _dt
            dt = _dt.fromisoformat(record.created_at)
            date_str = dt.strftime("%Y-%m-%d  %H:%M")
        except Exception:
            date_str = record.created_at or "—"

        # File size
        size_str = "—"
        media_path = record.media_file_path
        if media_path and Path(media_path).exists():
            try:
                nb = Path(media_path).stat().st_size
                size_str = f"{nb / 1_048_576:.1f} MB"
            except OSError:
                pass

        seed_str = "random" if record.seed == -1 else str(record.seed)
        file_name = Path(media_path).name if media_path else "—"

        rows = [
            ("Date",         date_str),
            ("Type",         "Image (FLUX)" if record.media_type == "image" else "Video (Wan2.2)"),
            ("Steps",        str(record.num_inference_steps)),
        ]
        if record.media_type == "image" and record.guidance_scale:
            rows.append(("Guidance",     f"{record.guidance_scale:.1f}"))
        rows += [
            ("Seed",         seed_str),
            ("Generated in", _fmt_duration(record.duration_s) if record.duration_s else "—"),
            ("File",         file_name),
            ("Size",         size_str),
            ("Job ID",       record.id),
        ]
        for i, (key, val) in enumerate(rows):
            key_lbl = Gtk.Label(label=key)
            key_lbl.set_xalign(1)
            key_lbl.add_css_class("muted")
            val_lbl = Gtk.Label(label=val)
            val_lbl.set_xalign(0)
            val_lbl.set_selectable(True)
            val_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            if key == "Job ID":
                val_lbl.add_css_class("mono")
            info_grid.attach(key_lbl, 0, i, 1, 1)
            info_grid.attach(val_lbl, 1, i, 1, 1)
        content.append(info_grid)

        # ── Seed image ────────────────────────────────────────────────────────
        if record.seed_image_path and Path(record.seed_image_path).exists():
            content.append(self._detail_section("Seed Image"))
            seed_img = _make_image_widget(record.seed_image_path, 96, 54)
            seed_img.set_halign(Gtk.Align.START)
            content.append(seed_img)

        # ── Action buttons ────────────────────────────────────────────────────
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(8)
        sep.set_margin_bottom(4)
        content.append(sep)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        export_btn = Gtk.Button(label="💾 Export")
        tip = "Save a copy of this image" if record.media_type == "image" else "Save a copy of this video"
        export_btn.set_tooltip_text(tip)
        export_btn.connect("clicked", self._export)
        if not record.media_exists:
            export_btn.set_sensitive(False)
        action_row.append(export_btn)

        iter_btn = Gtk.Button(label="↺ Iterate")
        iter_btn.set_tooltip_text("Pre-fill the control panel with this prompt")
        iter_btn.connect("clicked", self._iterate)
        action_row.append(iter_btn)
        content.append(action_row)

        self.set_child(content)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _detail_section(self, text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text.upper())
        lbl.set_xalign(0)
        lbl.add_css_class("detail-section")
        return lbl

    def _toggle_play(self, _btn) -> None:
        if self._video_widget is None:
            return
        stream = self._video_widget.get_media_stream()
        if stream is None:
            return
        if stream.get_playing():
            stream.pause()
            self._play_btn.set_label("▶ Play")
        else:
            stream.play()
            self._play_btn.set_label("⏸ Pause")

    def _open_fullscreen(self, _btn) -> None:
        if self._record and self._record.video_exists:
            win = VideoPlayerWindow(self._record, self.get_root())
            win.present()

    def _open_image_fullscreen(self, _btn) -> None:
        if self._record and self._record.image_exists:
            win = ImageViewerWindow(self._record, self.get_root())
            win.present()

    def _export(self, _btn) -> None:
        if not self._record or not self._record.media_exists:
            return
        dlg = Gtk.FileDialog()
        media_path = self._record.media_file_path
        if self._record.media_type == "image":
            dlg.set_title("Export Image")
        else:
            dlg.set_title("Export Video")
        dlg.set_initial_name(Path(media_path).name)
        dlg.save(self.get_root(), None, self._export_done)

    def _export_done(self, dlg, result) -> None:
        try:
            gfile = dlg.save_finish(result)
        except Exception:
            return
        dest = gfile.get_path()
        if dest and self._record:
            src = self._record.media_file_path
            shutil.copy2(src, dest)
            src_txt = Path(src).with_suffix(".txt")
            if src_txt.exists():
                shutil.copy2(src_txt, Path(dest).with_suffix(".txt"))

    def _iterate(self, _btn) -> None:
        if self._record and self._iterate_cb:
            self._iterate_cb(
                self._record.prompt,
                self._record.negative_prompt,
                self._record.seed_image_path,
            )


# ── Full-size video player window ─────────────────────────────────────────────

class VideoPlayerWindow(Gtk.Window):
    """
    Standalone window for watching a generated video at full size.

    Opens maximized by default. Supports:
      - Escape / clicking the close button → closes window, pauses video
      - F key or the ⛶ button → toggle fullscreen
      - Space → play / pause
    """

    def __init__(self, record: "GenerationRecord", parent_window: Gtk.Window):
        super().__init__()
        self.set_transient_for(parent_window)
        self.set_modal(False)  # non-modal so the main window stays interactive

        # Title bar: short prompt snippet
        short = record.prompt if len(record.prompt) <= 60 else record.prompt[:60] + "…"
        self.set_title(short)
        self.set_default_size(1280, 720)

        # Main layout
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(outer)

        # ── Video player ──────────────────────────────────────────────────────
        self._video = Gtk.Video.new_for_filename(record.video_path)
        self._video.set_autoplay(True)
        self._video.set_loop(True)
        self._video.set_vexpand(True)
        self._video.set_hexpand(True)
        outer.append(self._video)

        # ── Control strip at bottom ───────────────────────────────────────────
        ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ctrl.set_margin_start(12)
        ctrl.set_margin_end(12)
        ctrl.set_margin_top(6)
        ctrl.set_margin_bottom(6)
        outer.append(ctrl)

        self._play_pause_btn = Gtk.Button(label="⏸ Pause")
        self._play_pause_btn.connect("clicked", self._toggle_play)
        ctrl.append(self._play_pause_btn)

        fs_btn = Gtk.Button(label="⛶ Fullscreen")
        fs_btn.set_tooltip_text("Toggle fullscreen (F)")
        fs_btn.connect("clicked", lambda _: self._toggle_fullscreen())
        ctrl.append(fs_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        ctrl.append(spacer)

        close_btn = Gtk.Button(label="✕ Close")
        close_btn.connect("clicked", lambda _: self.close())
        ctrl.append(close_btn)

        # ── Keyboard shortcuts ────────────────────────────────────────────────
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key)
        self.add_controller(key_ctrl)

        self.maximize()

    def _toggle_play(self, _btn) -> None:
        stream = self._video.get_media_stream()
        if stream is None:
            return
        if stream.get_playing():
            stream.pause()
            self._play_pause_btn.set_label("▶ Play")
        else:
            stream.play()
            self._play_pause_btn.set_label("⏸ Pause")

    def _toggle_fullscreen(self) -> None:
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def _on_key(self, _ctrl, keyval, _keycode, _state) -> bool:
        # Gdk.KEY_Escape = 0xff1b, Gdk.KEY_f = 0x66, Gdk.KEY_space = 0x20
        if keyval == 0xFF1B:   # Escape
            self.close()
            return True
        if keyval in (0x66, 0x46):  # f / F
            self._toggle_fullscreen()
            return True
        if keyval == 0x20:     # Space
            self._toggle_play(None)
            return True
        return False


# ── Full-size image viewer window ─────────────────────────────────────────────

class ImageViewerWindow(Gtk.Window):
    """
    Standalone window for viewing a generated FLUX image at full size.

    Opens maximized by default. Supports:
      - Escape / close button → closes window
      - F → toggle fullscreen
    """

    def __init__(self, record: "GenerationRecord", parent_window: Gtk.Window):
        super().__init__()
        self.set_transient_for(parent_window)
        self.set_modal(False)

        short = record.prompt if len(record.prompt) <= 60 else record.prompt[:60] + "…"
        self.set_title(short)
        self.set_default_size(1280, 720)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(outer)

        # Image fills the window
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        pb = _load_pixbuf(record.image_path, 1920, 1080)
        if pb:
            pic = Gtk.Picture.new_for_pixbuf(pb)
            pic.set_can_shrink(True)
            pic.set_vexpand(True)
            pic.set_hexpand(True)
            scroll.set_child(pic)
        else:
            lbl = Gtk.Label(label="🖼  Image not available")
            lbl.set_vexpand(True)
            scroll.set_child(lbl)
        outer.append(scroll)

        # Controls strip
        ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ctrl.set_margin_start(12)
        ctrl.set_margin_end(12)
        ctrl.set_margin_top(6)
        ctrl.set_margin_bottom(6)
        outer.append(ctrl)

        fs_btn = Gtk.Button(label="⛶ Fullscreen")
        fs_btn.set_tooltip_text("Toggle fullscreen (F)")
        fs_btn.connect("clicked", lambda _: self._toggle_fullscreen())
        ctrl.append(fs_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        ctrl.append(spacer)

        close_btn = Gtk.Button(label="✕ Close")
        close_btn.connect("clicked", lambda _: self.close())
        ctrl.append(close_btn)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key)
        self.add_controller(key_ctrl)

        self.maximize()

    def _toggle_fullscreen(self) -> None:
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def _on_key(self, _ctrl, keyval, _keycode, _state) -> bool:
        if keyval == 0xFF1B:    # Escape
            self.close()
            return True
        if keyval in (0x66, 0x46):   # f / F
            self._toggle_fullscreen()
            return True
        return False


# ── Pending card ───────────────────────────────────────────────────────────────

class PendingCard(Gtk.Frame):
    """Animated placeholder card shown while a generation is running."""

    def __init__(self, prompt: str = "", model_source: str = "video"):
        super().__init__()
        self.add_css_class("card")
        self.set_size_request(_THUMB_W + 20, -1)
        self.set_hexpand(True)
        self._start = time.monotonic()
        self._timer_id: Optional[int] = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(8)
        box.set_margin_end(8)
        self.set_child(box)

        # Label differs by media type so the user can tell what is in flight
        if model_source == "image":
            spinner_text = "🖼 Generating image…"
        elif model_source == "animate":
            spinner_text = "💃 Animating…"
        else:
            spinner_text = "⏳ Generating video…"
        spinner_lbl = Gtk.Label(label=spinner_text)
        spinner_lbl.add_css_class("teal")
        box.append(spinner_lbl)

        if prompt:
            prompt_lbl = Gtk.Label(label=prompt)
            prompt_lbl.set_wrap(True)
            prompt_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            prompt_lbl.set_max_width_chars(26)
            prompt_lbl.set_lines(3)
            prompt_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            prompt_lbl.set_xalign(0)
            prompt_lbl.set_tooltip_text(prompt)
            prompt_lbl.add_css_class("muted")
            box.append(prompt_lbl)

        self._bar = Gtk.ProgressBar()
        self._bar.set_pulse_step(0.08)
        box.append(self._bar)

        self._status_lbl = Gtk.Label(label="Queued")
        self._status_lbl.add_css_class("muted")
        box.append(self._status_lbl)

        self._elapsed_lbl = Gtk.Label(label="0s elapsed")
        self._elapsed_lbl.add_css_class("teal")
        self._elapsed_lbl.set_attributes(_small_attrs())
        box.append(self._elapsed_lbl)

        self._timer_id = GLib.timeout_add(1000, self._tick)

    def _tick(self) -> bool:
        # Called on the main thread by GLib — safe to touch widgets directly.
        self._bar.pulse()
        elapsed = int(time.monotonic() - self._start)
        m, s = divmod(elapsed, 60)
        self._elapsed_lbl.set_label(f"{m}m {s:02d}s elapsed" if m else f"{s}s elapsed")
        return True  # keep firing

    def update_status(self, text: str) -> None:
        # Must be called on main thread (via GLib.idle_add from workers).
        self._status_lbl.set_label(text)

    def stop_timer(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None


# ── Gallery ────────────────────────────────────────────────────────────────────

_GALLERY_AUTOPLAY_LIMIT = 12   # max cards whose videos are loaded at once during "play all"


class GalleryWidget(Gtk.Box):
    """
    Scrollable grid of GenerationCards, newest first.

    Uses Gtk.FlowBox so the number of columns adjusts automatically as the pane
    is resized — no fixed column count.  Cards expand to fill the row.

    Contains a toolbar with a "▶ Play All" / "⏸ Pause All" button that starts or
    stops looping all visible video thumbnails simultaneously.  When the number of
    video cards exceeds _GALLERY_AUTOPLAY_LIMIT, only the top N are played to
    avoid excessive GStreamer resource use.  Scrolling pauses cards that scroll
    off the top and unpauses new ones that become visible (handled by
    _sync_autoplay()).
    """

    def __init__(self, iterate_cb, select_cb, delete_cb, media_type: str = "video"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_vexpand(True)
        self.set_hexpand(True)
        self._iterate_cb = iterate_cb
        self._select_cb = select_cb   # select_cb(record: GenerationRecord) called on click
        self._delete_cb = delete_cb   # delete_cb(record: GenerationRecord) called on trash
        self._playing_all = False

        # ── Toolbar ───────────────────────────────────────────────────────────
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_start(12)
        toolbar.set_margin_end(12)
        toolbar.set_margin_top(6)
        toolbar.set_margin_bottom(4)

        self._play_all_btn = Gtk.Button(label="▶ Play All")
        self._play_all_btn.set_tooltip_text(
            "Loop all video thumbnails in the gallery (limited to top "
            f"{_GALLERY_AUTOPLAY_LIMIT} to save resources)"
        )
        self._play_all_btn.connect("clicked", self._toggle_play_all)
        # Image-only gallery has no videos to play; hide the button entirely.
        self._play_all_btn.set_visible(media_type != "image")
        toolbar.append(self._play_all_btn)
        self.append(toolbar)

        # ── Scrolled flow box ──────────────────────────────────────────────────
        # FlowBox automatically computes the number of columns that fit in the
        # available width, so the gallery re-flows when the pane is resized.
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_hexpand(True)
        self._scroll.set_vexpand(True)

        self._flow = Gtk.FlowBox()
        self._flow.set_column_spacing(12)
        self._flow.set_row_spacing(12)
        self._flow.set_margin_top(4)
        self._flow.set_margin_bottom(12)
        self._flow.set_margin_start(12)
        self._flow.set_margin_end(12)
        self._flow.set_homogeneous(True)    # all cells same width → cards fill the row
        self._flow.set_selection_mode(Gtk.SelectionMode.NONE)  # selection handled manually
        self._flow.set_min_children_per_line(2)
        self._flow.set_max_children_per_line(8)
        self._flow.set_halign(Gtk.Align.FILL)
        self._flow.set_valign(Gtk.Align.START)
        self._scroll.set_child(self._flow)
        self.append(self._scroll)

        # Sync autoplay as user scrolls, and also when the viewport width changes
        # (i.e. window resize changes column count).  GTK4 removed size-allocate as
        # a public signal, so we listen to the horizontal adjustment's page-size
        # instead — it changes whenever the scrolled window is resized.
        self._scroll.get_vadjustment().connect("value-changed", self._on_scroll_changed)
        self._scroll.get_hadjustment().connect(
            "notify::page-size", lambda *_: self._sync_autoplay()
        )

        self._cards: list = []                       # all card widgets, index 0 = top-left
        self._pending: Optional[PendingCard] = None
        self._selected_card: Optional[GenerationCard] = None

    def select_card(self, card: "GenerationCard") -> None:
        """Highlight card as selected and notify the detail panel."""
        if self._selected_card is not None:
            self._selected_card.set_selected(False)
        self._selected_card = card
        card.set_selected(True)
        self._select_cb(card._record)

    # ── Play all / pause all ──────────────────────────────────────────────────

    def _video_cards(self) -> list:
        """Return GenerationCards whose video file exists (skips pending and image cards)."""
        return [c for c in self._cards
                if isinstance(c, GenerationCard) and c._record.video_exists]

    def _toggle_play_all(self, _btn=None) -> None:
        """Start or stop looping all video thumbnails in the gallery."""
        self._playing_all = not self._playing_all
        if self._playing_all:
            self._play_all_btn.set_label("⏸ Pause All")
            self._sync_autoplay()
        else:
            self._play_all_btn.set_label("▶ Play All")
            # Stop all currently playing thumbnail videos
            for card in self._video_cards():
                if card._hover_video is not None:
                    try:
                        card._hover_video.get_media_stream().pause()
                        card._media_stack.set_visible_child_name("thumb")
                    except Exception:
                        pass

    def _cols_per_row(self) -> int:
        """
        Estimate the current column count from the FlowBox's allocated width.
        Used by _sync_autoplay to convert a card's list index to a row number.
        """
        w = self._flow.get_allocated_width()
        if w <= 0:
            return 2  # sensible fallback before first allocation
        margin = self._flow.get_margin_start() + self._flow.get_margin_end()
        col_gap = self._flow.get_column_spacing()
        # Minimum card width matches GenerationCard.set_size_request(_THUMB_W + 20, …)
        card_min_w = _THUMB_W + 20
        usable = max(card_min_w, w - margin)
        return max(1, (usable + col_gap) // (card_min_w + col_gap))

    def _sync_autoplay(self) -> None:
        """
        Play the top-N video cards that are visible in the viewport; pause the rest.
        Called when play-all is active and the scroll position changes or the
        FlowBox is resized (which changes the column count and therefore row positions).
        """
        if not self._playing_all:
            return

        video_cards = self._video_cards()
        # Determine which cards are "visible" by estimating row positions from the
        # index order.  GTK4 doesn't expose per-child coordinates cheaply without
        # forcing a full layout pass, so we use row-index arithmetic as a proxy.
        adj = self._scroll.get_vadjustment()
        scroll_top = adj.get_value()
        scroll_bottom = scroll_top + adj.get_page_size()

        # Estimate card height (thumbnail + padding + labels + buttons ≈ 220px)
        _CARD_H_EST = 220
        cards_per_row = self._cols_per_row()

        playing_count = 0
        for i, card in enumerate(self._video_cards()):
            row = i // cards_per_row
            card_top = row * (_CARD_H_EST + self._flow.get_row_spacing())
            card_bottom = card_top + _CARD_H_EST
            is_visible = card_bottom > scroll_top and card_top < scroll_bottom
            should_play = is_visible and playing_count < _GALLERY_AUTOPLAY_LIMIT

            if card._hover_video is None:
                continue
            stream = card._hover_video.get_media_stream()
            try:
                if should_play:
                    playing_count += 1
                    card._media_stack.set_visible_child_name("video")
                    if stream is None:
                        # Stream not yet initialized; delegate to the card's own
                        # play helper which retries after the pipeline is ready.
                        card._play_hover_stream()
                    else:
                        # Wire loop handler if this is the first time play-all
                        # has driven this card (hover may not have done it yet).
                        if not card._loop_connected:
                            stream.connect("notify::ended", card._on_stream_ended)
                            card._loop_connected = True
                        if not stream.get_playing():
                            stream.play()
                else:
                    if stream is not None and stream.get_playing():
                        stream.pause()
                    card._media_stack.set_visible_child_name("thumb")
            except Exception:
                pass

    def _on_scroll_changed(self, _adj) -> None:
        """Pause/play cards as user scrolls when play-all is active."""
        if self._playing_all:
            self._sync_autoplay()

    def add_pending_card(self, prompt: str = "", model_source: str = "video") -> PendingCard:
        card = PendingCard(prompt=prompt, model_source=model_source)
        self._pending = card
        self._cards.insert(0, card)
        self._relayout()
        return card

    def replace_pending_with(self, record: GenerationRecord) -> None:
        card = self._make_card(record)
        if self._pending and self._pending in self._cards:
            self._pending.stop_timer()
            idx = self._cards.index(self._pending)
            self._cards[idx] = card
        else:
            self._cards.insert(0, card)
        self._pending = None
        self._relayout()
        # Auto-select the freshly completed card so the detail panel updates immediately
        self.select_card(card)

    def remove_pending(self) -> None:
        if self._pending and self._pending in self._cards:
            self._pending.stop_timer()
            self._cards.remove(self._pending)
            self._pending = None
            self._relayout()

    def load_history(self, records) -> None:
        for record in records:
            self._cards.append(self._make_card(record))
        self._relayout()

    def _make_card(self, record: GenerationRecord) -> "GenerationCard":
        return GenerationCard(
            record,
            iterate_cb=self._iterate_cb,
            select_cb=self.select_card,
            delete_cb=self._delete_cb,
        )

    def delete_card(self, record_id: str) -> None:
        """
        Remove the card matching record_id from the internal list and re-layout.
        Called by MainWindow after it has already removed the record from the store.
        """
        to_remove = [c for c in self._cards
                     if isinstance(c, GenerationCard) and c._record.id == record_id]
        for card in to_remove:
            if self._selected_card is card:
                self._selected_card = None
            self._cards.remove(card)
        if to_remove:
            self._relayout()

    def _relayout(self) -> None:
        """Re-populate the FlowBox from self._cards (newest first)."""
        # Remove all FlowBoxChild wrappers; our card widgets remain alive in self._cards.
        child = self._flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._flow.remove(child)
            child = nxt
        # Re-add every card; FlowBox automatically wraps each in a FlowBoxChild.
        for card in self._cards:
            self._flow.append(card)


# ── Control panel ──────────────────────────────────────────────────────────────

class ControlPanel(Gtk.Box):
    """
    Left panel: prompt fields, parameters, seed image, server status,
    generate/cancel/recover buttons, and the prompt queue.
    """

    def __init__(
        self,
        on_generate,       # (prompt, neg, steps, seed, seed_image_path, model_source, guidance_scale, ref_video_path, ref_char_path, animate_mode) -> None
        on_enqueue,        # same signature
        on_cancel,         # () -> None
        on_recover,        # () -> None
        on_start_server,   # (model_source: str) -> None
        on_stop_server,    # () -> None
        on_source_change,  # (model_source: str) -> None — called after the mode toggle switches
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._on_generate = on_generate
        self._on_enqueue = on_enqueue
        self._on_cancel = on_cancel
        self._on_recover = on_recover
        self._on_start_server = on_start_server
        self._on_stop_server = on_stop_server
        self._on_source_change = on_source_change
        self._seed_image_path = ""
        self._ref_video_path = ""      # animate: motion source video
        self._ref_char_path = ""       # animate: character image
        self._animate_mode = "animation"
        self._server_ready = False
        self._server_launching = False   # True while start/stop script is running
        self._busy = False
        self._model_source = "video"   # "video", "image", or "animate"
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_size_request(310, -1)
        self._build()

    def _section(self, text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text.upper())
        lbl.set_xalign(0)
        lbl.add_css_class("section-label")
        return lbl

    def _build(self) -> None:
        self._title_lbl = Gtk.Label(label="TT VIDEO GENERATOR")
        self._title_lbl.set_xalign(0)
        self._title_lbl.add_css_class("teal")
        attrs = Pango.AttrList()
        attrs.insert(Pango.AttrFontDesc.new(
            Pango.FontDescription.from_string("sans bold 15")))
        self._title_lbl.set_attributes(attrs)
        self.append(self._title_lbl)

        # ── Model source toggle ───────────────────────────────────────────────
        # Switches between Wan2.2 (video) and FLUX.1-dev (image) generation.
        self.append(self._section("Generation Source"))
        src_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._src_video_btn = Gtk.Button(label="🎬 Video")
        self._src_video_btn.add_css_class("source-btn")
        self._src_video_btn.add_css_class("source-btn-left")
        self._src_video_btn.add_css_class("source-btn-active")
        self._src_video_btn.set_tooltip_text(
            "Wan2.2-T2V-A14B  ·  Async job-based  ·  5-second 720p MP4\n"
            "Supports seed images for motion reference"
        )
        self._src_video_btn.connect("clicked", lambda _: self._set_source("video"))
        src_row.append(self._src_video_btn)
        self._src_animate_btn = Gtk.Button(label="💃 Animate")
        self._src_animate_btn.add_css_class("source-btn")
        self._src_animate_btn.add_css_class("source-btn-mid")
        self._src_animate_btn.set_tooltip_text(
            "Wan2.2-Animate-14B  ·  Character animation  ·  Video-to-video\n"
            "Requires a motion video + character image"
        )
        self._src_animate_btn.connect("clicked", lambda _: self._set_source("animate"))
        src_row.append(self._src_animate_btn)
        self._src_image_btn = Gtk.Button(label="🖼 Image")
        self._src_image_btn.add_css_class("source-btn")
        self._src_image_btn.add_css_class("source-btn-right")
        self._src_image_btn.set_tooltip_text(
            "FLUX.1-dev  ·  Synchronous request  ·  ~1024×1024 JPEG\n"
            "Blocks until image is ready (~15–90 s)"
        )
        self._src_image_btn.connect("clicked", lambda _: self._set_source("image"))
        src_row.append(self._src_image_btn)
        self.append(src_row)

        # One-line model spec shown below the toggle — updates on source change
        self._source_desc_lbl = Gtk.Label(
            label="async job  ·  ~3–10 min  ·  720p MP4"
        )
        self._source_desc_lbl.set_xalign(0)
        self._source_desc_lbl.add_css_class("hint")
        self.append(self._source_desc_lbl)

        # ── Prompt ────────────────────────────────────────────────────────────
        self.append(self._section("Prompt"))
        scroll1 = Gtk.ScrolledWindow()
        scroll1.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll1.set_size_request(-1, 90)
        self._prompt_view = Gtk.TextView()
        self._prompt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._prompt_view.get_buffer().set_text("")
        # Placeholder text — updated when source changes
        self._prompt_ph_text_video = (
            "Describe the video…\n\n"
            "e.g. a cinematic shot of a red sports car\n"
            "driving through a rainy city at night"
        )
        self._prompt_ph_text_image = (
            "Describe the image…\n\n"
            "e.g. a lone lighthouse on a rocky cliff\n"
            "at sunset, oil painting, dramatic sky"
        )
        ph = Gtk.Label(label=self._prompt_ph_text_video)
        ph.set_xalign(0)
        ph.set_yalign(0)
        ph.add_css_class("muted")
        ph.set_can_focus(False)
        ph.set_can_target(False)   # pass pointer/keyboard events through to the TextView
        overlay1 = Gtk.Overlay()
        overlay1.set_child(self._prompt_view)
        overlay1.add_overlay(ph)
        self._prompt_placeholder = ph
        self._prompt_view.get_buffer().connect(
            "changed", lambda b: ph.set_visible(b.get_char_count() == 0)
        )
        scroll1.set_child(overlay1)
        self.append(scroll1)

        # ── Prompt component chips ────────────────────────────────────────────
        # Clicking a chip appends its modifier text to the prompt.
        # The chip list changes when source changes (video ↔ image).
        chips_hdr = Gtk.Label(label="Style modifiers — click to append:")
        chips_hdr.set_xalign(0)
        chips_hdr.add_css_class("hint")
        self.append(chips_hdr)
        self._chips_scroll = Gtk.ScrolledWindow()
        self._chips_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self._chips_scroll.set_size_request(-1, -1)
        self._chips_scroll.set_child(self._make_chips_box("video"))
        self.append(self._chips_scroll)

        # ── Negative prompt ───────────────────────────────────────────────────
        self.append(self._section("Negative Prompt"))
        neg_hint = Gtk.Label(label="Steer away from: blurry, watermark, low quality, distorted")
        neg_hint.set_xalign(0)
        neg_hint.set_ellipsize(Pango.EllipsizeMode.END)
        neg_hint.add_css_class("hint")
        self.append(neg_hint)
        scroll2 = Gtk.ScrolledWindow()
        scroll2.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll2.set_size_request(-1, 52)
        self._neg_view = Gtk.TextView()
        self._neg_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._neg_view.set_tooltip_text(
            "Describe what you do NOT want in the output.\n"
            "Common: blurry, watermark, text overlay, distorted, low quality"
        )
        scroll2.set_child(self._neg_view)
        self.append(scroll2)

        # ── Parameters ────────────────────────────────────────────────────────
        self.append(self._section("Parameters"))
        param_grid = Gtk.Grid()
        param_grid.set_column_spacing(8)
        param_grid.set_row_spacing(2)

        # Steps — row 0 (label+spin) + row 1 (hint)
        self._steps_lbl = Gtk.Label(label="Steps (12–50):")
        self._steps_lbl.set_xalign(1)
        param_grid.attach(self._steps_lbl, 0, 0, 1, 1)
        self._steps_spin = Gtk.SpinButton()
        self._steps_spin.set_adjustment(
            Gtk.Adjustment(value=20, lower=12, upper=50, step_increment=1)
        )
        self._steps_spin.set_tooltip_text(
            "Denoising steps — each step refines the output.\n"
            "More = sharper quality, but proportionally slower.\n"
            "Wan2.2 sweet spot: 20–28  ·  FLUX sweet spot: 20–30"
        )
        param_grid.attach(self._steps_spin, 1, 0, 1, 1)
        self._steps_hint_lbl = Gtk.Label(label="sweet spot 20–28  ·  more = sharper, slower")
        self._steps_hint_lbl.set_xalign(0)
        self._steps_hint_lbl.add_css_class("hint")
        param_grid.attach(self._steps_hint_lbl, 0, 1, 2, 1)

        # Seed — row 2 (label+spin) + row 3 (hint)
        seed_lbl = Gtk.Label(label="Seed (−1=random):")
        seed_lbl.set_xalign(1)
        param_grid.attach(seed_lbl, 0, 2, 1, 1)
        self._seed_spin = Gtk.SpinButton()
        self._seed_spin.set_adjustment(
            Gtk.Adjustment(value=-1, lower=-1, upper=2**31-1, step_increment=1)
        )
        self._seed_spin.set_tooltip_text(
            "Random seed controlling the noise pattern.\n"
            "−1 picks a new random seed every time.\n"
            "Set a fixed value to reproduce a previous result\n"
            "(same seed + same settings = identical output)."
        )
        param_grid.attach(self._seed_spin, 1, 2, 1, 1)
        seed_hint = Gtk.Label(label="same seed + prompt → identical result")
        seed_hint.set_xalign(0)
        seed_hint.add_css_class("hint")
        param_grid.attach(seed_hint, 0, 3, 2, 1)

        # Guidance scale — row 4 (label+spin) + row 5 (hint)
        # Only shown for FLUX (image); hidden for Wan2.2 (video).
        self._guidance_lbl = Gtk.Label(label="Guidance (1–20):")
        self._guidance_lbl.set_xalign(1)
        self._guidance_lbl.set_tooltip_text(
            "Classifier-free guidance scale — how strictly the model\n"
            "follows the text prompt vs. exploring on its own.\n\n"
            "Low (2–4): more creative, unexpected results\n"
            "Mid (4–7): good balance — recommended range\n"
            "High (8+): very literal, can over-saturate or distort\n\n"
            "Rainbow/artifact issues → raise to 5–7."
        )
        param_grid.attach(self._guidance_lbl, 0, 4, 1, 1)
        self._guidance_spin = Gtk.SpinButton()
        self._guidance_spin.set_adjustment(
            Gtk.Adjustment(value=3.5, lower=1.0, upper=20.0, step_increment=0.5)
        )
        self._guidance_spin.set_digits(1)
        self._guidance_spin.set_tooltip_text(
            "FLUX guidance scale (1.0–20.0). Default 3.5.\n"
            "Raise to 5–7 if you see rainbow or distortion artifacts."
        )
        param_grid.attach(self._guidance_spin, 1, 4, 1, 1)
        self._guidance_hint_lbl = Gtk.Label(
            label="3.5–7 typical  ·  rainbow artifacts → raise value"
        )
        self._guidance_hint_lbl.set_xalign(0)
        self._guidance_hint_lbl.add_css_class("hint")
        param_grid.attach(self._guidance_hint_lbl, 0, 5, 2, 1)

        # Guidance rows hidden by default (Wan2.2 doesn't use guidance scale)
        self._guidance_lbl.set_visible(False)
        self._guidance_spin.set_visible(False)
        self._guidance_hint_lbl.set_visible(False)

        self.append(param_grid)

        # ── Seed image ────────────────────────────────────────────────────────
        # Only relevant for Wan2.2 video; hidden when FLUX image source is selected.
        self._seed_img_section = self._section("Seed Image (optional)")
        self._seed_img_section.set_tooltip_text(
            "Reference image passed to Wan2.2 to guide motion and composition.\n"
            "The model uses it as a visual starting point — not copied verbatim.\n"
            "PNG or JPEG, any aspect ratio (resized internally)."
        )
        self.append(self._seed_img_section)
        seed_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self._seed_img_widget = Gtk.Label(label="none")
        self._seed_img_widget.set_size_request(64, 36)
        self._seed_img_widget.add_css_class("muted")
        seed_row.append(self._seed_img_widget)

        seed_btns = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        browse_btn = Gtk.Button(label="Browse…")
        browse_btn.set_tooltip_text("Pick a reference image (PNG/JPG)")
        browse_btn.connect("clicked", self._pick_seed_image)
        seed_btns.append(browse_btn)
        self._clear_seed_btn = Gtk.Button(label="Clear")
        self._clear_seed_btn.set_sensitive(False)
        self._clear_seed_btn.connect("clicked", lambda _: self._clear_seed_image())
        seed_btns.append(self._clear_seed_btn)
        seed_row.append(seed_btns)
        self._seed_row_widget = seed_row
        self.append(seed_row)

        # ── Animate inputs ────────────────────────────────────────────────────
        # Visible only when "animate" source is active.
        self._animate_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._animate_box.set_visible(False)

        self._animate_box.append(self._section("Motion Video"))
        mv_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._anim_video_lbl = Gtk.Label(label="none")
        self._anim_video_lbl.add_css_class("muted")
        self._anim_video_lbl.set_ellipsize(Pango.EllipsizeMode.START)
        self._anim_video_lbl.set_hexpand(True)
        self._anim_video_lbl.set_xalign(1)
        self._anim_video_lbl.set_tooltip_text("Reference video supplying the motion pattern")
        mv_row.append(self._anim_video_lbl)
        anim_video_btn = Gtk.Button(label="Browse…")
        anim_video_btn.set_tooltip_text("Pick an MP4 motion source video")
        anim_video_btn.connect("clicked", self._pick_ref_video)
        mv_row.append(anim_video_btn)
        self._animate_box.append(mv_row)

        self._animate_box.append(self._section("Character Image"))
        ci_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._anim_char_lbl = Gtk.Label(label="none")
        self._anim_char_lbl.add_css_class("muted")
        self._anim_char_lbl.set_ellipsize(Pango.EllipsizeMode.START)
        self._anim_char_lbl.set_hexpand(True)
        self._anim_char_lbl.set_xalign(1)
        self._anim_char_lbl.set_tooltip_text("Character image to animate")
        ci_row.append(self._anim_char_lbl)
        anim_char_btn = Gtk.Button(label="Browse…")
        anim_char_btn.set_tooltip_text("Pick a character image (PNG/JPG)")
        anim_char_btn.connect("clicked", self._pick_ref_image)
        ci_row.append(anim_char_btn)
        self._animate_box.append(ci_row)

        self._animate_box.append(self._section("Animation Mode"))
        mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._anim_mode_anim_btn = Gtk.Button(label="🔄 Animation")
        self._anim_mode_anim_btn.add_css_class("source-btn")
        self._anim_mode_anim_btn.add_css_class("source-btn-left")
        self._anim_mode_anim_btn.add_css_class("source-btn-active")
        self._anim_mode_anim_btn.set_tooltip_text(
            "Character mimics the motion from the reference video"
        )
        self._anim_mode_anim_btn.connect("clicked", lambda _: self._set_animate_mode("animation"))
        mode_row.append(self._anim_mode_anim_btn)
        self._anim_mode_repl_btn = Gtk.Button(label="🔀 Replacement")
        self._anim_mode_repl_btn.add_css_class("source-btn")
        self._anim_mode_repl_btn.add_css_class("source-btn-right")
        self._anim_mode_repl_btn.set_tooltip_text(
            "Character replaces the person in the reference video"
        )
        self._anim_mode_repl_btn.connect("clicked", lambda _: self._set_animate_mode("replacement"))
        mode_row.append(self._anim_mode_repl_btn)
        self._animate_box.append(mode_row)

        self.append(self._animate_box)

        # ── Server control ─────────────────────────────────────────────────────
        # Status row: indicator label + Start + Stop buttons side by side.
        srv_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._server_lbl = Gtk.Label(label="⬤  Checking server…")
        self._server_lbl.set_xalign(0)
        self._server_lbl.set_hexpand(True)
        self._server_lbl.add_css_class("muted")
        srv_row.append(self._server_lbl)

        self._server_start_btn = Gtk.Button(label="▶ Start")
        self._server_start_btn.add_css_class("server-start-btn")
        self._server_start_btn.set_tooltip_text(
            "Start the inference server using the local launch script.\n"
            "Video → start_wan.sh  ·  Animate → start_animate.sh  ·  Image → start_flux.sh"
        )
        self._server_start_btn.set_sensitive(False)  # enabled once health check confirms server is offline
        self._server_start_btn.connect("clicked", self._on_start_server_clicked)
        srv_row.append(self._server_start_btn)

        self._server_stop_btn = Gtk.Button(label="■ Stop")
        self._server_stop_btn.add_css_class("server-stop-btn")
        self._server_stop_btn.set_tooltip_text(
            "Stop the running inference server Docker container.\n"
            "Stops any container using the tt-media-inference-server image."
        )
        self._server_stop_btn.set_sensitive(False)  # enabled once server is confirmed running
        self._server_stop_btn.connect("clicked", self._on_stop_server_clicked)
        srv_row.append(self._server_stop_btn)

        self.append(srv_row)

        # Collapsible log area — shown while a start/stop operation is in progress.
        self._srv_log_revealer = Gtk.Revealer()
        self._srv_log_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._srv_log_revealer.set_transition_duration(150)
        self._srv_log_buf = Gtk.TextBuffer()
        srv_log_view = Gtk.TextView.new_with_buffer(self._srv_log_buf)
        srv_log_view.set_editable(False)
        srv_log_view.set_cursor_visible(False)
        srv_log_view.set_wrap_mode(Gtk.WrapMode.CHAR)
        srv_log_view.add_css_class("server-log")
        srv_log_scroll = Gtk.ScrolledWindow()
        srv_log_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        srv_log_scroll.set_size_request(-1, 90)
        srv_log_scroll.set_child(srv_log_view)
        self._srv_log_scroll = srv_log_scroll
        self._srv_log_revealer.set_child(srv_log_scroll)
        self.append(self._srv_log_revealer)

        # Push buttons to bottom
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        self.append(spacer)

        # ── Buttons ────────────────────────────────────────────────────────────
        # Single action button: "Generate" when idle, "+ Add to Queue" when busy.
        self._gen_btn = Gtk.Button(label="Generate")
        self._gen_btn.add_css_class("generate-btn")
        self._gen_btn.set_sensitive(False)
        self._gen_btn.connect("clicked", self._on_action_clicked)
        self.append(self._gen_btn)

        self._cancel_btn = Gtk.Button(label="✕ Cancel")
        self._cancel_btn.add_css_class("cancel-btn")
        self._cancel_btn.set_visible(False)
        self._cancel_btn.connect("clicked", lambda _: self._on_cancel())
        self.append(self._cancel_btn)

        self._recover_btn = Gtk.Button(label="⟳ Recover Jobs")
        self._recover_btn.set_tooltip_text("Discover server jobs not in local history")
        self._recover_btn.set_sensitive(False)
        self._recover_btn.connect("clicked", lambda _: self._on_recover())
        self.append(self._recover_btn)

        # ── Queue display ──────────────────────────────────────────────────────
        self._queue_section_lbl = self._section("Queued Prompts")
        self._queue_section_lbl.set_visible(False)
        self.append(self._queue_section_lbl)

        self._queue_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self._queue_box.set_visible(False)
        self.append(self._queue_box)

    # ── State ──────────────────────────────────────────────────────────────────

    # ── Source toggle ──────────────────────────────────────────────────────────

    def _set_source(self, source: str) -> None:
        """Switch between 'video' (Wan2.2), 'animate' (Animate-14B), and 'image' (FLUX)."""
        if source == self._model_source:
            return
        self._model_source = source
        is_image = source == "image"
        is_animate = source == "animate"
        is_video = source == "video"

        # Update toggle button visual states
        self._src_video_btn.remove_css_class("source-btn-active")
        self._src_animate_btn.remove_css_class("source-btn-active")
        self._src_image_btn.remove_css_class("source-btn-active")
        if is_image:
            self._src_image_btn.add_css_class("source-btn-active")
            self._title_lbl.set_label("TT IMAGE GENERATOR")
            self._source_desc_lbl.set_label("synchronous  ·  ~15–90 s  ·  1024×1024 JPEG")
        elif is_animate:
            self._src_animate_btn.add_css_class("source-btn-active")
            self._title_lbl.set_label("TT ANIMATE GENERATOR")
            self._source_desc_lbl.set_label("async job  ·  Animate-14B  ·  motion video + character")
        else:
            self._src_video_btn.add_css_class("source-btn-active")
            self._title_lbl.set_label("TT VIDEO GENERATOR")
            self._source_desc_lbl.set_label("async job  ·  ~3–10 min  ·  720p MP4")

        # Update prompt placeholder — prompt is optional/style-only for animate
        if is_image:
            self._prompt_placeholder.set_label(self._prompt_ph_text_image)
        elif is_animate:
            self._prompt_placeholder.set_label(
                "Optional style guidance…\n\n"
                "e.g. photorealistic, anime style, cinematic lighting\n"
                "(leave blank to let the model decide)"
            )
        else:
            self._prompt_placeholder.set_label(self._prompt_ph_text_video)

        # Show guidance scale only for FLUX image
        self._guidance_lbl.set_visible(is_image)
        self._guidance_spin.set_visible(is_image)
        self._guidance_hint_lbl.set_visible(is_image)

        # Swap chips: video/animate share motion vocabulary; image uses style chips
        chip_source = "image" if is_image else "video"
        self._chips_scroll.set_child(self._make_chips_box(chip_source))

        # Seed image: only relevant for video (Wan2.2 init image)
        self._seed_img_section.set_visible(is_video)
        self._seed_row_widget.set_visible(is_video)

        # Animate inputs: visible only in animate mode
        self._animate_box.set_visible(is_animate)

        # Adjust steps range: FLUX min is 4, others min is 12
        if is_image:
            self._steps_lbl.set_label("Steps (4–50):")
            self._steps_hint_lbl.set_label("sweet spot 20–30  ·  more = cleaner, slower")
            adj = self._steps_spin.get_adjustment()
            adj.set_lower(4)
            if adj.get_value() < 4:
                adj.set_value(4)
        else:
            self._steps_lbl.set_label("Steps (12–50):")
            self._steps_hint_lbl.set_label("sweet spot 20–28  ·  more = sharper, slower")
            adj = self._steps_spin.get_adjustment()
            adj.set_lower(12)
            if adj.get_value() < 12:
                adj.set_value(12)

        # Notify the main window so it can switch the gallery stack to show
        # only the cards that match the newly selected generation mode.
        self._on_source_change(source)

    def get_model_source(self) -> str:
        return self._model_source

    def set_server_ready(self, ready: bool) -> None:
        self._server_ready = ready
        if ready:
            self._server_lbl.set_label("⬤  Server ready")
            self._server_lbl.remove_css_class("muted")
            self._server_lbl.add_css_class("teal")
            # Collapse the startup log once the server is confirmed up.
            if self._server_launching:
                self.set_server_launching(False)
        else:
            self._server_lbl.set_label("⬤  Server offline")
            self._server_lbl.remove_css_class("teal")
            self._server_lbl.add_css_class("muted")
        # Start button enabled when server is offline and no operation running.
        # Stop button enabled only when server is confirmed running.
        if not self._server_launching:
            self._server_start_btn.set_sensitive(not ready)
            self._server_stop_btn.set_sensitive(ready)
        self._update_btns()

    # ── Server control helpers ─────────────────────────────────────────────────

    def set_server_launching(self, launching: bool, clear_log: bool = False) -> None:
        """Show or hide the startup log panel and lock Start/Stop during the operation."""
        self._server_launching = launching
        self._srv_log_revealer.set_reveal_child(launching)
        if clear_log:
            self._srv_log_buf.set_text("")
        # While an operation is in progress, disable both buttons to prevent overlap.
        self._server_start_btn.set_sensitive(not launching)
        self._server_stop_btn.set_sensitive(not launching)

    def append_server_log(self, line: str) -> None:
        """Append one line to the server startup log. Must be called on the main thread."""
        end = self._srv_log_buf.get_end_iter()
        self._srv_log_buf.insert(end, line + "\n")
        # Auto-scroll the log to the bottom so the latest output is always visible.
        adj = self._srv_log_scroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())

    def _on_start_server_clicked(self, _btn) -> None:
        self._on_start_server(self._model_source)

    def _on_stop_server_clicked(self, _btn) -> None:
        self._on_stop_server()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        # Fields stay editable while busy so the user can write the next prompt to queue.
        # Only the Generate button is gated; queue button remains available.
        self._cancel_btn.set_visible(busy)
        self._update_btns()

    def clear_prompt(self) -> None:
        """Clear the prompt and negative prompt fields so the user can type the next one."""
        self._prompt_view.get_buffer().set_text("")
        self._neg_view.get_buffer().set_text("")

    def _update_btns(self) -> None:
        # When idle: "Generate" (disabled until server ready).
        # When busy: "+ Add to Queue" (always enabled so user can queue the next prompt).
        if self._busy:
            self._gen_btn.set_label("+ Add to Queue")
            self._gen_btn.set_sensitive(self._server_ready)
            self._gen_btn.set_tooltip_text("Queue this prompt — runs automatically after current generation")
        else:
            self._gen_btn.set_label("Generate")
            self._gen_btn.set_sensitive(self._server_ready)
            self._gen_btn.set_tooltip_text("")
        self._recover_btn.set_sensitive(self._server_ready and not self._busy)

    # ── Seed image ─────────────────────────────────────────────────────────────

    def _pick_seed_image(self, _btn) -> None:
        dlg = Gtk.FileDialog()
        dlg.set_title("Select Seed Image")
        f = Gtk.FileFilter()
        f.set_name("Images")
        for pat in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp"):
            f.add_pattern(pat)
        filters = Gio_ListStore_from_items([f])
        dlg.set_filters(filters)
        dlg.open(self.get_root(), None, self._seed_image_chosen)

    def _seed_image_chosen(self, dlg, result) -> None:
        try:
            gfile = dlg.open_finish(result)
        except Exception:
            return
        path = gfile.get_path()
        if path:
            self._set_seed_image(path)

    def _set_seed_image(self, path: str) -> None:
        self._seed_image_path = path
        pb = _load_pixbuf(path, 64, 36)
        # Replace the placeholder label with a Picture widget
        parent = self._seed_img_widget.get_parent()
        parent.remove(self._seed_img_widget)
        if pb:
            self._seed_img_widget = Gtk.Picture.new_for_pixbuf(pb)
            self._seed_img_widget.set_size_request(64, 36)
            self._seed_img_widget.set_can_shrink(False)
            self._seed_img_widget.set_tooltip_text(path)
        else:
            self._seed_img_widget = Gtk.Label(label="?")
            self._seed_img_widget.set_size_request(64, 36)
        parent.prepend(self._seed_img_widget)
        self._clear_seed_btn.set_sensitive(True)

    def _clear_seed_image(self) -> None:
        self._seed_image_path = ""
        parent = self._seed_img_widget.get_parent()
        parent.remove(self._seed_img_widget)
        self._seed_img_widget = Gtk.Label(label="none")
        self._seed_img_widget.set_size_request(64, 36)
        self._seed_img_widget.add_css_class("muted")
        parent.prepend(self._seed_img_widget)
        self._clear_seed_btn.set_sensitive(False)

    # ── Animate file pickers ───────────────────────────────────────────────────

    def _pick_ref_video(self, _btn) -> None:
        dlg = Gtk.FileDialog()
        dlg.set_title("Select Motion Video")
        f = Gtk.FileFilter()
        f.set_name("Videos")
        for pat in ("*.mp4", "*.mov", "*.avi", "*.webm", "*.mkv"):
            f.add_pattern(pat)
        filters = Gio_ListStore_from_items([f])
        dlg.set_filters(filters)
        dlg.open(self.get_root(), None, self._ref_video_chosen)

    def _ref_video_chosen(self, dlg, result) -> None:
        try:
            gfile = dlg.open_finish(result)
        except Exception:
            return
        path = gfile.get_path()
        if path:
            self._ref_video_path = path
            self._anim_video_lbl.set_label(Path(path).name)
            self._anim_video_lbl.remove_css_class("muted")
            self._anim_video_lbl.set_tooltip_text(path)

    def _pick_ref_image(self, _btn) -> None:
        dlg = Gtk.FileDialog()
        dlg.set_title("Select Character Image")
        f = Gtk.FileFilter()
        f.set_name("Images")
        for pat in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp"):
            f.add_pattern(pat)
        filters = Gio_ListStore_from_items([f])
        dlg.set_filters(filters)
        dlg.open(self.get_root(), None, self._ref_image_chosen)

    def _ref_image_chosen(self, dlg, result) -> None:
        try:
            gfile = dlg.open_finish(result)
        except Exception:
            return
        path = gfile.get_path()
        if path:
            self._ref_char_path = path
            self._anim_char_lbl.set_label(Path(path).name)
            self._anim_char_lbl.remove_css_class("muted")
            self._anim_char_lbl.set_tooltip_text(path)

    def _set_animate_mode(self, mode: str) -> None:
        self._animate_mode = mode
        if mode == "animation":
            self._anim_mode_anim_btn.add_css_class("source-btn-active")
            self._anim_mode_repl_btn.remove_css_class("source-btn-active")
        else:
            self._anim_mode_repl_btn.add_css_class("source-btn-active")
            self._anim_mode_anim_btn.remove_css_class("source-btn-active")

    # ── Chips helper ───────────────────────────────────────────────────────────

    def _make_chips_box(self, source: str) -> Gtk.Box:
        """Build and return a horizontal chip box for the given source ('video'/'image')."""
        chip_list = _PROMPT_CHIPS if source == "video" else _IMAGE_PROMPT_CHIPS
        chips_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        chips_box.set_margin_start(2)
        chips_box.set_margin_end(2)
        chips_box.set_margin_top(2)
        chips_box.set_margin_bottom(2)
        for label, text, tip in chip_list:
            btn = Gtk.Button(label=label)
            btn.set_tooltip_text(tip)
            btn.add_css_class("chip-btn")
            btn.connect("clicked", lambda _b, t=text: self._append_to_prompt(t))
            chips_box.append(btn)
        return chips_box

    # ── Form helpers ───────────────────────────────────────────────────────────

    def _append_to_prompt(self, text: str) -> None:
        """Append a chip's text to the prompt, inserting a comma separator if needed."""
        buf = self._prompt_view.get_buffer()
        current = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).rstrip()
        if current and not current.endswith(","):
            new_text = current + ", " + text
        elif current:
            new_text = current + " " + text
        else:
            new_text = text
        buf.set_text(new_text)
        # Move cursor to end
        buf.place_cursor(buf.get_end_iter())
        self._prompt_view.grab_focus()

    def _get_prompt(self) -> str:
        buf = self._prompt_view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()

    def _get_neg(self) -> str:
        buf = self._neg_view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()

    def populate_prompts(self, prompt: str, neg: str, seed_image_path: str = "") -> None:
        self._prompt_view.get_buffer().set_text(prompt)
        self._neg_view.get_buffer().set_text(neg)
        if seed_image_path and Path(seed_image_path).exists():
            self._set_seed_image(seed_image_path)
        else:
            self._clear_seed_image()

    # ── Button handlers ────────────────────────────────────────────────────────

    def _on_action_clicked(self, _btn) -> None:
        """Single button: Generate when idle, Add to Queue when busy."""
        if self._model_source == "animate":
            # Prompt is optional for animate (style guidance only); video+image are required.
            if not self._ref_video_path or not self._ref_char_path:
                return
            prompt = self._get_prompt()
        else:
            prompt = self._get_prompt()
            if not prompt:
                return
        args = (
            prompt,
            self._get_neg(),
            int(self._steps_spin.get_value()),
            int(self._seed_spin.get_value()),
            self._seed_image_path,
            self._model_source,
            float(self._guidance_spin.get_value()),
            self._ref_video_path,
            self._ref_char_path,
            self._animate_mode,
        )
        if self._busy:
            self._on_enqueue(*args)
        else:
            self._on_generate(*args)

    # ── Queue display ──────────────────────────────────────────────────────────

    def update_queue_display(self, items: list) -> None:
        """Rebuild the queue list. Call from main thread only."""
        # Clear existing rows
        child = self._queue_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._queue_box.remove(child)
            child = nxt

        for i, item in enumerate(items):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            row.add_css_class("queue-row")
            short = item.prompt if len(item.prompt) <= 55 else item.prompt[:55] + "…"
            lbl = Gtk.Label(label=f"{i+1}. {short}")
            lbl.set_xalign(0)
            lbl.set_hexpand(True)
            lbl.add_css_class("muted")
            lbl.set_tooltip_text(item.prompt)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            row.append(lbl)
            rm_btn = Gtk.Button(label="×")
            rm_btn.set_tooltip_text("Remove from queue")
            rm_btn.connect("clicked", lambda _b, idx=i: self._on_queue_remove(idx))
            row.append(rm_btn)
            self._queue_box.append(row)

        has = bool(items)
        self._queue_section_lbl.set_visible(has)
        self._queue_box.set_visible(has)

    def _on_queue_remove(self, index: int) -> None:
        # Delegate upward — MainWindow owns the queue list
        self._remove_queue_cb(index)

    def set_remove_queue_cb(self, cb) -> None:
        """Called by MainWindow to wire remove callbacks after construction."""
        self._remove_queue_cb = cb


# ── Recovery dialog ────────────────────────────────────────────────────────────

class RecoveryDialog(Gtk.Dialog):
    """Modal dialog listing unknown server jobs; user selects which to recover."""

    def __init__(self, parent, jobs: list):
        super().__init__(title="Recover Server Jobs", transient_for=parent, modal=True)
        self.set_default_size(520, -1)
        self.selected_jobs: list = []
        self._checkboxes: list = []
        self._jobs = jobs

        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("OK", Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)

        content = self.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        header = Gtk.Label(
            label=f"Found <b>{len(jobs)}</b> server job(s) not in local history.\n"
                  "Select jobs to re-attach.",
        )
        header.set_use_markup(True)
        header.set_wrap(True)
        header.set_xalign(0)
        content.append(header)

        for job in jobs:
            short = job["prompt"][:80] + ("…" if len(job["prompt"]) > 80 else "")
            label = f"[{job['status']}]  {short}  (id: {job['id'][:8]})"
            cb = Gtk.CheckButton(label=label)
            cb.set_active(True)
            cb.job = job  # plain Python attribute — GObject set_data() is unsupported in PyGObject
            self._checkboxes.append(cb)
            content.append(cb)

        self.connect("response", self._on_response)

    def _on_response(self, _dlg, response) -> None:
        if response == Gtk.ResponseType.OK:
            self.selected_jobs = [
                cb.job for cb in self._checkboxes if cb.get_active()
            ]


# ── Helper: Gio.ListStore from filter items ────────────────────────────────────

def Gio_ListStore_from_items(filters):
    """Build a Gio.ListStore of Gtk.FileFilter for FileDialog."""
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    store = Gio.ListStore.new(Gtk.FileFilter)
    for f in filters:
        store.append(f)
    return store


# ── Pango small-text attribute helper ─────────────────────────────────────────

def _small_attrs() -> Pango.AttrList:
    attrs = Pango.AttrList()
    attrs.insert(Pango.AttrSize.new(10 * Pango.SCALE))
    return attrs


# ── Main Window ────────────────────────────────────────────────────────────────

class MainWindow(Gtk.ApplicationWindow):
    """Top-level window: owns client, store, workers, and the prompt queue."""

    def __init__(self, app: Gtk.Application, server_url: str = "http://localhost:8000"):
        super().__init__(application=app, title="TT Video Generator")
        self.set_default_size(1400, 800)

        self._client = APIClient(server_url)
        self._store = HistoryStore()
        self._worker: Optional[threading.Thread] = None
        self._worker_gen: Optional[GenerationWorker] = None
        self._queue: list = []
        self._server_proc: Optional[subprocess.Popen] = None  # running start/stop script subprocess
        # Track which gallery owns the current pending card (set in _on_generate,
        # used in _on_finished/_on_error to update the right gallery).
        self._gen_gallery = None

        self._build_ui()
        self._load_history()
        self._start_health_worker()

    def _build_ui(self) -> None:
        # Apply CSS to the display now that we have a window
        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Three-pane layout: controls | gallery | detail
        outer_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.set_child(outer_paned)

        self._controls = ControlPanel(
            on_generate=self._on_generate,
            on_enqueue=self._on_enqueue,
            on_cancel=self._on_cancel,
            on_recover=self._on_recover,
            on_start_server=self._on_start_server,
            on_stop_server=self._on_stop_server,
            on_source_change=self._on_source_change,
        )
        self._controls.set_remove_queue_cb(self._on_queue_remove)
        outer_paned.set_start_child(self._controls)
        outer_paned.set_shrink_start_child(False)
        outer_paned.set_resize_start_child(False)

        # Inner paned splits gallery (left) from detail panel (right)
        inner_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        inner_paned.set_position(480)   # default gallery width before detail panel

        gallery_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Two separate galleries — one for video cards, one for image cards.
        # A Gtk.Stack switches between them when the generation source toggle changes.
        self._gallery_stack = Gtk.Stack()
        self._gallery_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._gallery_stack.set_transition_duration(150)
        self._gallery_stack.set_hexpand(True)
        self._gallery_stack.set_vexpand(True)

        shared_cbs = dict(
            iterate_cb=self._controls.populate_prompts,
            select_cb=self._on_card_selected,
            delete_cb=self._on_delete_card,
        )
        self._video_gallery = GalleryWidget(**shared_cbs, media_type="video")
        self._animate_gallery = GalleryWidget(**shared_cbs, media_type="video")
        self._image_gallery = GalleryWidget(**shared_cbs, media_type="image")
        self._gallery_stack.add_named(self._video_gallery, "video")
        self._gallery_stack.add_named(self._animate_gallery, "animate")
        self._gallery_stack.add_named(self._image_gallery, "image")
        self._gallery_stack.set_visible_child_name("video")
        gallery_wrap.append(self._gallery_stack)

        # Status bar spans the full bottom of the gallery+detail area
        self._status_lbl = Gtk.Label(label="Ready")
        self._status_lbl.set_xalign(0)
        self._status_lbl.add_css_class("status-bar")
        gallery_wrap.append(self._status_lbl)

        inner_paned.set_start_child(gallery_wrap)
        inner_paned.set_shrink_start_child(False)

        self._detail = DetailPanel()
        inner_paned.set_end_child(self._detail)
        inner_paned.set_shrink_end_child(False)

        outer_paned.set_end_child(inner_paned)
        outer_paned.set_shrink_end_child(False)

    def _set_status(self, text: str) -> None:
        """Update status bar. Safe to call from main thread only."""
        self._status_lbl.set_label(text)

    # ── Gallery helpers ────────────────────────────────────────────────────────

    def _active_gallery(self) -> "GalleryWidget":
        """Return the gallery that matches the currently selected generation source."""
        return self._gallery_for_type(self._controls.get_model_source())

    def _gallery_for_type(self, media_type: str) -> "GalleryWidget":
        """Return the gallery for the given media_type string."""
        if media_type == "image":
            return self._image_gallery
        if media_type == "animate":
            return self._animate_gallery
        return self._video_gallery

    def _on_source_change(self, source: str) -> None:
        """Switch the gallery stack when the user toggles between video and image mode."""
        self._gallery_stack.set_visible_child_name(source)

    # ── Card selection ─────────────────────────────────────────────────────────

    def _on_card_selected(self, record: GenerationRecord) -> None:
        """Called when the user clicks a gallery card. Populates the detail panel."""
        self._detail.show_record(record, self._controls.populate_prompts)

    def _on_delete_card(self, record: GenerationRecord) -> None:
        """
        Delete a generation: remove from history JSON, delete the media and thumbnail
        files from disk, remove the card from the gallery, and clear the detail panel
        if it was showing the deleted record.
        """
        removed = self._store.delete(record.id)
        if removed:
            # Delete associated files; tolerate missing files gracefully.
            for fpath in (removed.video_path, removed.image_path, removed.thumbnail_path):
                if fpath:
                    try:
                        Path(fpath).unlink(missing_ok=True)
                    except Exception:
                        pass
        self._gallery_for_type(record.media_type).delete_card(record.id)
        if self._detail._record is not None and self._detail._record.id == record.id:
            self._detail.clear()
        short = record.prompt[:50] + ("…" if len(record.prompt) > 50 else "")
        self._set_status(f'Deleted: "{short}"')

    def _load_history(self) -> None:
        records = self._store.all_records()
        if not records:
            return
        # Route each record to the gallery that matches its media type.
        video_recs = [r for r in records if r.media_type != "image"]
        image_recs = [r for r in records if r.media_type == "image"]
        if video_recs:
            self._video_gallery.load_history(video_recs)
        if image_recs:
            self._image_gallery.load_history(image_recs)
        self._set_status(f"Loaded {len(records)} previous generation(s)")

    # ── Health worker ──────────────────────────────────────────────────────────

    def _start_health_worker(self) -> None:
        self._health_stop = threading.Event()
        self._health_thread = threading.Thread(
            target=self._health_loop, daemon=True
        )
        self._health_thread.start()

    def _health_loop(self) -> None:
        """Runs on background thread. Posts UI updates via GLib.idle_add."""
        while not self._health_stop.is_set():
            ready = self._client.health_check()
            # THREADING: must not touch GTK widgets here — post to main thread
            GLib.idle_add(self._on_health_result, ready)
            self._health_stop.wait(10.0)

    def _on_health_result(self, ready: bool) -> bool:
        # Runs on main thread (called by GLib.idle_add).
        self._controls.set_server_ready(ready)
        if ready and not (self._worker_gen and self._worker_gen._running()):
            self._set_status("Server ready — enter a prompt and click Generate")
        return False  # don't repeat (one-shot idle callback)

    # ── Generation ─────────────────────────────────────────────────────────────

    def _on_generate(self, prompt, neg, steps, seed, seed_image_path="",
                     model_source="video", guidance_scale=3.5,
                     ref_video_path="", ref_char_path="",
                     animate_mode="animation") -> None:
        if self._worker and self._worker.is_alive():
            return

        # Add the pending card to the gallery that matches the generation type,
        # and remember that gallery so _on_finished/_on_error update the right one.
        self._gen_gallery = self._gallery_for_type(model_source)
        pending = self._gen_gallery.add_pending_card(prompt=prompt, model_source=model_source)
        self._controls.set_busy(True)
        self._controls.clear_prompt()

        if model_source == "image":
            self._set_status("Generating image with FLUX.1-dev…")
            gen = ImageGenerationWorker(
                client=self._client,
                store=self._store,
                prompt=prompt,
                negative_prompt=neg,
                num_inference_steps=steps,
                seed=seed,
                guidance_scale=guidance_scale,
            )
        elif model_source == "animate":
            self._set_status("Submitting Animate-14B job…")
            gen = AnimateGenerationWorker(
                client=self._client,
                store=self._store,
                reference_video_path=ref_video_path,
                reference_image_path=ref_char_path,
                prompt=prompt,
                num_inference_steps=steps,
                seed=seed,
                animate_mode=animate_mode,
            )
        else:
            self._set_status("Submitting video generation job…")
            gen = GenerationWorker(
                client=self._client,
                store=self._store,
                prompt=prompt,
                negative_prompt=neg,
                num_inference_steps=steps,
                seed=seed,
                seed_image_path=seed_image_path,
            )
        self._worker_gen = gen

        def run():
            gen.run_with_callbacks(
                on_progress=lambda msg: GLib.idle_add(self._on_progress, msg, pending),
                on_finished=lambda rec: GLib.idle_add(self._on_finished, rec),
                on_error=lambda msg: GLib.idle_add(self._on_error, msg),
            )

        self._worker = threading.Thread(target=run, daemon=True)
        self._worker.start()

    def _on_cancel(self) -> None:
        if self._worker_gen:
            self._worker_gen.cancel()
        self._set_status("Cancelling…")

    # ── Server start / stop ────────────────────────────────────────────────────

    def _on_start_server(self, model_source: str) -> None:
        """Launch the appropriate server script in a background thread, streaming output to the log panel."""
        if model_source == "image":
            script_name, label = "start_flux.sh", "FLUX image"
        elif model_source == "animate":
            script_name, label = "start_animate.sh", "Wan2.2-Animate"
        else:
            script_name, label = "start_wan.sh", "Wan2.2 video"
        script_path = str(Path(__file__).parent / script_name)

        self._controls.set_server_launching(True, clear_log=True)
        self._controls.append_server_log(f"Starting {label} server ({script_name} --gui)…")
        self._set_status(f"Launching {label} server…")

        def run():
            try:
                proc = subprocess.Popen(
                    [script_path, "--gui"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    stdin=subprocess.DEVNULL,
                )
                self._server_proc = proc
                for line in proc.stdout:
                    GLib.idle_add(self._controls.append_server_log, line.rstrip())
                proc.wait()
                if proc.returncode != 0:
                    GLib.idle_add(self._controls.append_server_log,
                                  f"Script exited with code {proc.returncode}")
                    GLib.idle_add(self._set_status, "Server start script failed — check log")
                    GLib.idle_add(self._controls.set_server_launching, False)
                else:
                    GLib.idle_add(self._set_status,
                                  f"{label} server started — waiting for health check…")
                    # Leave the log panel open; set_server_ready(True) will collapse it.
            except Exception as e:
                GLib.idle_add(self._controls.append_server_log, f"Error: {e}")
                GLib.idle_add(self._set_status, f"Server start error: {e}")
                GLib.idle_add(self._controls.set_server_launching, False)
            finally:
                self._server_proc = None

        threading.Thread(target=run, daemon=True).start()

    def _on_stop_server(self) -> None:
        """Run the stop command (via start_wan.sh --stop) in a background thread."""
        # Both video and image use the same Docker image, so either script can stop it.
        script_path = str(Path(__file__).parent / "start_wan.sh")

        self._controls.set_server_launching(True, clear_log=True)
        self._controls.append_server_log("Stopping inference server…")
        self._set_status("Stopping inference server…")

        def run():
            try:
                proc = subprocess.Popen(
                    [script_path, "--stop"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    stdin=subprocess.DEVNULL,
                )
                self._server_proc = proc
                output = proc.communicate()[0]
                for line in output.splitlines():
                    GLib.idle_add(self._controls.append_server_log, line)
                GLib.idle_add(self._set_status, "Server stopped.")
            except Exception as e:
                GLib.idle_add(self._controls.append_server_log, f"Error: {e}")
                GLib.idle_add(self._set_status, f"Server stop error: {e}")
            finally:
                self._server_proc = None
                GLib.idle_add(self._controls.set_server_launching, False)

        threading.Thread(target=run, daemon=True).start()

    # ── Queue ──────────────────────────────────────────────────────────────────

    def _on_enqueue(self, prompt, neg, steps, seed, seed_image_path,
                    model_source="video", guidance_scale=3.5,
                    ref_video_path="", ref_char_path="",
                    animate_mode="animation") -> None:
        self._queue.append(_QueueItem(prompt, neg, steps, seed, seed_image_path,
                                      model_source, guidance_scale,
                                      ref_video_path, ref_char_path, animate_mode))
        self._controls.update_queue_display(self._queue)
        self._controls.clear_prompt()   # ready for the next prompt immediately
        n = len(self._queue)
        self._set_status(f"Added to queue ({n} item{'s' if n != 1 else ''} queued)")

    def _on_queue_remove(self, index: int) -> None:
        if 0 <= index < len(self._queue):
            removed = self._queue.pop(index)
            self._controls.update_queue_display(self._queue)
            short = removed.prompt[:40] + ("…" if len(removed.prompt) > 40 else "")
            self._set_status(f'Removed from queue: "{short}"')

    def _start_next_queued(self) -> bool:
        if not self._queue:
            return False
        item = self._queue.pop(0)
        self._controls.update_queue_display(self._queue)
        remaining = len(self._queue)
        suffix = f" — {remaining} more queued" if remaining else ""
        self._set_status(f"Auto-starting next queued prompt{suffix}…")
        self._on_generate(item.prompt, item.negative_prompt,
                          item.steps, item.seed, item.seed_image_path,
                          item.model_source, item.guidance_scale,
                          item.ref_video_path, item.ref_char_path, item.animate_mode)
        return True

    # ── Recovery ───────────────────────────────────────────────────────────────

    def _on_recover(self) -> None:
        known_ids = {r.id for r in self._store.all_records()}
        self._set_status("Scanning server for unknown jobs…")

        def fetch():
            jobs = self._client.list_jobs()
            unknown = []
            for job in jobs:
                if job.get("id") in known_ids:
                    continue
                if job.get("status") not in ("completed", "in_progress", "queued"):
                    continue
                params = job.get("request_parameters") or {}
                unknown.append({
                    "id": job["id"],
                    "status": job["status"],
                    "prompt": params.get("prompt", ""),
                    "negative_prompt": params.get("negative_prompt") or "",
                    "steps": params.get("num_inference_steps", 20),
                    "seed": params.get("seed") or -1,
                })
            # THREADING: post result back to main thread
            GLib.idle_add(self._on_recovery_found, unknown)

        threading.Thread(target=fetch, daemon=True).start()

    def _on_recovery_found(self, jobs: list) -> bool:
        if not jobs:
            self._set_status("No unknown jobs found on server.")
            return False

        dlg = RecoveryDialog(self, jobs)
        dlg.connect("response", self._on_recovery_response)
        dlg.present()
        return False

    def _on_recovery_response(self, dlg, response) -> None:
        dlg.close()
        if response != Gtk.ResponseType.OK or not dlg.selected_jobs:
            self._set_status("Recovery cancelled.")
            return
        for job in dlg.selected_jobs:
            self._attach_recovery_job(job)

    def _attach_recovery_job(self, job: dict) -> None:
        # Recovery jobs are video jobs (Wan2.2); route to the video gallery.
        self._gen_gallery = self._video_gallery
        pending = self._video_gallery.add_pending_card()
        pending.update_status(f"Recovering {job['id'][:8]}… ({job['status']})")
        self._controls.set_busy(True)

        gen = GenerationWorker(
            client=self._client,
            store=self._store,
            prompt=job["prompt"],
            negative_prompt=job["negative_prompt"],
            num_inference_steps=job["steps"],
            seed=job["seed"],
        )
        gen._job_id_override = job["id"]
        self._worker_gen = gen

        self._worker = threading.Thread(
            target=lambda: gen.run_with_callbacks(
                on_progress=lambda msg: GLib.idle_add(self._on_progress, msg, pending),
                on_finished=lambda rec: GLib.idle_add(self._on_finished, rec),
                on_error=lambda msg: GLib.idle_add(self._on_error, msg),
            ),
            daemon=True,
        )
        self._worker.start()
        self._set_status(f"Re-attached job {job['id'][:8]}…")

    # ── Worker callbacks (all called on main thread via GLib.idle_add) ─────────

    def _on_progress(self, message: str, pending: PendingCard) -> bool:
        self._set_status(message)
        pending.update_status(message)
        return False

    def _on_finished(self, record: GenerationRecord) -> bool:
        gallery = self._gen_gallery or self._gallery_for_type(record.media_type)
        gallery.replace_pending_with(record)
        self._gen_gallery = None
        self._controls.set_busy(False)
        media_path = record.media_file_path
        self._set_status(f"Done — {media_path}  ({record.duration_s:.0f}s)")
        self._start_next_queued()
        return False

    def _on_error(self, message: str) -> bool:
        gallery = self._gen_gallery or self._active_gallery()
        gallery.remove_pending()
        self._gen_gallery = None
        self._controls.set_busy(False)
        self._set_status(f"Error: {message}")
        return False

    def do_close_request(self) -> bool:
        self._health_stop.set()
        if self._worker_gen:
            self._worker_gen.cancel()
        if self._server_proc and self._server_proc.poll() is None:
            self._server_proc.terminate()
        return False  # allow close
