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
from worker import GenerationWorker


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

_THUMB_W = 200
_THUMB_H = 112   # 16:9
_GALLERY_COLS = 2
_DETAIL_VIDEO_W = 480
_DETAIL_VIDEO_H = 270


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


# ── Queue item ─────────────────────────────────────────────────────────────────

@dataclass
class _QueueItem:
    prompt: str
    negative_prompt: str
    steps: int
    seed: int
    seed_image_path: str = ""


# ── Generation card ────────────────────────────────────────────────────────────

class GenerationCard(Gtk.Frame):
    """
    Thumbnail card in the gallery. Click anywhere on the card to select it and
    show full details in the DetailPanel. Buttons: 💾 Save, ↺ Iterate.
    select_cb(self) is called when the card is clicked.
    """

    def __init__(self, record: GenerationRecord, iterate_cb, select_cb):
        super().__init__()
        self._record = record
        self._iterate_cb = iterate_cb
        self._select_cb = select_cb
        self.add_css_class("card")
        self.set_size_request(_THUMB_W + 20, -1)
        self._build()

        # Clicking anywhere on the card selects it in the detail panel.
        gesture = Gtk.GestureClick()
        gesture.connect("pressed", lambda *_: self._select_cb(self))
        self.add_controller(gesture)

        # Hovering over the card plays the video in the thumbnail area (if available).
        if record.video_exists:
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", self._on_hover_enter)
            motion.connect("leave", self._on_hover_leave)
            self.add_controller(motion)

    def set_selected(self, selected: bool) -> None:
        if selected:
            self.add_css_class("card-selected")
        else:
            self.remove_css_class("card-selected")

    def _build(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        self.set_child(box)

        # Media area: thumbnail normally; hover swaps in a silent looping video preview.
        self._media_stack = Gtk.Stack()
        self._media_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._media_stack.set_transition_duration(120)

        if self._record.thumbnail_exists:
            thumb = _make_image_widget(self._record.thumbnail_path, _THUMB_W, _THUMB_H)
        else:
            thumb = _make_image_widget("", _THUMB_W, _THUMB_H, "🎬")
        self._media_stack.add_named(thumb, "thumb")

        if self._record.video_exists:
            self._hover_video = Gtk.Video.new_for_filename(self._record.video_path)
            self._hover_video.set_autoplay(False)
            self._hover_video.set_loop(True)
            self._hover_video.set_size_request(_THUMB_W, _THUMB_H)
            self._media_stack.add_named(self._hover_video, "video")
        else:
            self._hover_video = None

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

        # Meta row: time on left, generation duration on right
        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        time_lbl = Gtk.Label(label=self._record.display_time)
        time_lbl.add_css_class("muted")
        dur_text = _fmt_duration(self._record.duration_s) if self._record.duration_s else ""
        dur_lbl = Gtk.Label(label=dur_text)
        dur_lbl.add_css_class("muted")
        meta.append(time_lbl)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        meta.append(spacer)
        meta.append(dur_lbl)
        box.append(meta)

        # Buttons: Save and Iterate (play/fullscreen are in the detail panel)
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        export_btn = Gtk.Button(label="💾 Save")
        export_btn.set_tooltip_text("Export video to a chosen location")
        export_btn.connect("clicked", self._export)
        iter_btn = Gtk.Button(label="↺ Iterate")
        iter_btn.set_tooltip_text("Re-use this prompt in the control panel")
        iter_btn.connect("clicked", self._iterate)
        if not self._record.video_exists:
            export_btn.set_sensitive(False)
        btn_row.append(export_btn)
        btn_row.append(iter_btn)
        box.append(btn_row)

    def _on_hover_enter(self, _ctrl, _x, _y) -> None:
        """Start looping the video silently when the mouse enters the card."""
        if self._hover_video is not None:
            self._media_stack.set_visible_child_name("video")
            self._hover_video.get_media_stream().play()

    def _on_hover_leave(self, _ctrl) -> None:
        """Stop the video and revert to the thumbnail when the mouse leaves."""
        if self._hover_video is not None:
            self._hover_video.get_media_stream().pause()
            self._media_stack.set_visible_child_name("thumb")

    def _export(self, _btn) -> None:
        if not self._record.video_exists:
            return
        dlg = Gtk.FileDialog()
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
            shutil.copy2(self._record.video_path, dest)
            src_txt = Path(self._record.video_path).with_suffix(".txt")
            if src_txt.exists():
                shutil.copy2(src_txt, Path(dest).with_suffix(".txt"))

    def _iterate(self, _btn) -> None:
        self._iterate_cb(
            self._record.prompt,
            self._record.negative_prompt,
            self._record.seed_image_path,
        )


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
        lbl = Gtk.Label(label="← Click a video to preview")
        lbl.add_css_class("detail-empty")
        lbl.set_vexpand(True)
        lbl.set_valign(Gtk.Align.CENTER)
        lbl.set_halign(Gtk.Align.CENTER)
        box.append(lbl)
        self.set_child(box)

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

        # ── Video player ──────────────────────────────────────────────────────
        if record.video_exists:
            self._video_widget = Gtk.Video.new_for_filename(record.video_path)
            self._video_widget.set_autoplay(False)
            self._video_widget.set_loop(True)
            self._video_widget.set_size_request(_DETAIL_VIDEO_W, _DETAIL_VIDEO_H)
            self._video_widget.set_hexpand(False)
            self._video_widget.set_halign(Gtk.Align.START)
            content.append(self._video_widget)

            # Play/pause + fullscreen controls
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
            # No video file — show large thumbnail or placeholder
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
        if record.video_exists:
            try:
                nb = Path(record.video_path).stat().st_size
                size_str = f"{nb / 1_048_576:.1f} MB"
            except OSError:
                pass

        seed_str = "random" if record.seed == -1 else str(record.seed)

        rows = [
            ("Date",         date_str),
            ("Steps",        str(record.num_inference_steps)),
            ("Seed",         seed_str),
            ("Generated in", _fmt_duration(record.duration_s) if record.duration_s else "—"),
            ("File",         Path(record.video_path).name),
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
        export_btn.set_tooltip_text("Save a copy of this video")
        export_btn.connect("clicked", self._export)
        if not record.video_exists:
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

    def _export(self, _btn) -> None:
        if not self._record or not self._record.video_exists:
            return
        dlg = Gtk.FileDialog()
        dlg.set_title("Export Video")
        dlg.set_initial_name(Path(self._record.video_path).name)
        dlg.save(self.get_root(), None, self._export_done)

    def _export_done(self, dlg, result) -> None:
        try:
            gfile = dlg.save_finish(result)
        except Exception:
            return
        dest = gfile.get_path()
        if dest and self._record:
            shutil.copy2(self._record.video_path, dest)
            src_txt = Path(self._record.video_path).with_suffix(".txt")
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


# ── Pending card ───────────────────────────────────────────────────────────────

class PendingCard(Gtk.Frame):
    """Animated placeholder card shown while a generation is running."""

    def __init__(self, prompt: str = ""):
        super().__init__()
        self.add_css_class("card")
        self.set_size_request(_THUMB_W + 20, -1)
        self._start = time.monotonic()
        self._timer_id: Optional[int] = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(8)
        box.set_margin_end(8)
        self.set_child(box)

        spinner_lbl = Gtk.Label(label="⏳ Generating…")
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

class GalleryWidget(Gtk.ScrolledWindow):
    """Scrollable grid of GenerationCards, newest first."""

    def __init__(self, iterate_cb, select_cb):
        super().__init__()
        self._iterate_cb = iterate_cb
        self._select_cb = select_cb   # select_cb(record: GenerationRecord) called on click
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_hexpand(True)
        self.set_vexpand(True)

        self._grid = Gtk.Grid()
        self._grid.set_column_spacing(12)
        self._grid.set_row_spacing(12)
        self._grid.set_margin_top(12)
        self._grid.set_margin_bottom(12)
        self._grid.set_margin_start(12)
        self._grid.set_margin_end(12)
        self._grid.set_halign(Gtk.Align.START)
        self._grid.set_valign(Gtk.Align.START)
        self.set_child(self._grid)

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

    def add_pending_card(self, prompt: str = "") -> PendingCard:
        card = PendingCard(prompt=prompt)
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
        )

    def _relayout(self) -> None:
        # Remove all children from grid
        child = self._grid.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._grid.remove(child)
            child = nxt

        for i, card in enumerate(self._cards):
            row, col = divmod(i, _GALLERY_COLS)
            self._grid.attach(card, col, row, 1, 1)


# ── Control panel ──────────────────────────────────────────────────────────────

class ControlPanel(Gtk.Box):
    """
    Left panel: prompt fields, parameters, seed image, server status,
    generate/cancel/recover buttons, and the prompt queue.
    """

    def __init__(
        self,
        on_generate,      # (prompt, neg, steps, seed, seed_image_path) -> None
        on_enqueue,       # same signature
        on_cancel,        # () -> None
        on_recover,       # () -> None
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._on_generate = on_generate
        self._on_enqueue = on_enqueue
        self._on_cancel = on_cancel
        self._on_recover = on_recover
        self._seed_image_path = ""
        self._server_ready = False
        self._busy = False
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
        title = Gtk.Label(label="TT VIDEO GENERATOR")
        title.set_xalign(0)
        title.add_css_class("teal")
        attrs = Pango.AttrList()
        attrs.insert(Pango.AttrFontDesc.new(
            Pango.FontDescription.from_string("sans bold 15")))
        title.set_attributes(attrs)
        self.append(title)

        # ── Prompt ────────────────────────────────────────────────────────────
        self.append(self._section("Prompt"))
        scroll1 = Gtk.ScrolledWindow()
        scroll1.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll1.set_size_request(-1, 100)
        self._prompt_view = Gtk.TextView()
        self._prompt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._prompt_view.get_buffer().set_text("")
        ph = Gtk.Label(label="Describe the video…\n\ne.g. a cinematic shot of a red sports car driving through a rainy city at night")
        ph.set_xalign(0)
        ph.set_yalign(0)
        ph.add_css_class("muted")
        ph.set_can_focus(False)
        ph.set_can_target(False)   # pass all pointer/keyboard events through to the TextView below
        # Overlay placeholder over textview
        overlay1 = Gtk.Overlay()
        overlay1.set_child(self._prompt_view)
        overlay1.add_overlay(ph)
        self._prompt_placeholder = ph
        self._prompt_view.get_buffer().connect("changed", lambda b: ph.set_visible(b.get_char_count() == 0))
        scroll1.set_child(overlay1)
        self.append(scroll1)

        # ── Prompt component chips ────────────────────────────────────────────
        # Clicking a chip appends its text to the prompt (with a comma separator).
        chips_scroll = Gtk.ScrolledWindow()
        chips_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        chips_scroll.set_size_request(-1, -1)
        chips_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        chips_box.set_margin_start(2)
        chips_box.set_margin_end(2)
        chips_box.set_margin_top(2)
        chips_box.set_margin_bottom(2)
        for label, text, tip in _PROMPT_CHIPS:
            btn = Gtk.Button(label=label)
            btn.set_tooltip_text(tip)
            btn.add_css_class("chip-btn")
            btn.connect("clicked", lambda _b, t=text: self._append_to_prompt(t))
            chips_box.append(btn)
        chips_scroll.set_child(chips_box)
        self.append(chips_scroll)

        # ── Negative prompt ───────────────────────────────────────────────────
        self.append(self._section("Negative Prompt (optional)"))
        scroll2 = Gtk.ScrolledWindow()
        scroll2.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll2.set_size_request(-1, 60)
        self._neg_view = Gtk.TextView()
        self._neg_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scroll2.set_child(self._neg_view)
        self.append(scroll2)

        # ── Parameters ────────────────────────────────────────────────────────
        self.append(self._section("Parameters"))
        param_grid = Gtk.Grid()
        param_grid.set_column_spacing(8)
        param_grid.set_row_spacing(4)

        param_grid.attach(Gtk.Label(label="Steps (12–50):"), 0, 0, 1, 1)
        self._steps_spin = Gtk.SpinButton()
        self._steps_spin.set_adjustment(Gtk.Adjustment(value=20, lower=12, upper=50, step_increment=1))
        self._steps_spin.set_tooltip_text("More steps = better quality but slower.")
        param_grid.attach(self._steps_spin, 1, 0, 1, 1)

        param_grid.attach(Gtk.Label(label="Seed (−1 = random):"), 0, 1, 1, 1)
        self._seed_spin = Gtk.SpinButton()
        self._seed_spin.set_adjustment(Gtk.Adjustment(value=-1, lower=-1, upper=2**31-1, step_increment=1))
        self._seed_spin.set_tooltip_text("−1 uses a random seed each time.")
        param_grid.attach(self._seed_spin, 1, 1, 1, 1)
        self.append(param_grid)

        # ── Seed image ────────────────────────────────────────────────────────
        self.append(self._section("Seed Image (optional)"))
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
        self.append(seed_row)

        # ── Server status ──────────────────────────────────────────────────────
        self._server_lbl = Gtk.Label(label="⬤  Checking server…")
        self._server_lbl.set_xalign(0)
        self._server_lbl.add_css_class("muted")
        self.append(self._server_lbl)

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

    def set_server_ready(self, ready: bool) -> None:
        self._server_ready = ready
        if ready:
            self._server_lbl.set_label("⬤  Server ready")
            self._server_lbl.remove_css_class("muted")
            self._server_lbl.add_css_class("teal")
        else:
            self._server_lbl.set_label("⬤  Server loading…")
            self._server_lbl.remove_css_class("teal")
            self._server_lbl.add_css_class("muted")
        self._update_btns()

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
        prompt = self._get_prompt()
        if not prompt:
            return
        args = (prompt, self._get_neg(),
                int(self._steps_spin.get_value()),
                int(self._seed_spin.get_value()),
                self._seed_image_path)
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
        )
        self._controls.set_remove_queue_cb(self._on_queue_remove)
        outer_paned.set_start_child(self._controls)
        outer_paned.set_shrink_start_child(False)
        outer_paned.set_resize_start_child(False)

        # Inner paned splits gallery (left) from detail panel (right)
        inner_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        inner_paned.set_position(480)   # default gallery width before detail panel

        gallery_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._gallery = GalleryWidget(
            iterate_cb=self._controls.populate_prompts,
            select_cb=self._on_card_selected,
        )
        gallery_wrap.append(self._gallery)

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

    def _on_card_selected(self, record: GenerationRecord) -> None:
        """Called when the user clicks a gallery card. Populates the detail panel."""
        self._detail.show_record(record, self._controls.populate_prompts)

    def _load_history(self) -> None:
        records = self._store.all_records()
        if records:
            self._gallery.load_history(records)
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

    def _on_generate(self, prompt, neg, steps, seed, seed_image_path="") -> None:
        if self._worker and self._worker.is_alive():
            return

        pending = self._gallery.add_pending_card(prompt=prompt)
        self._controls.set_busy(True)
        self._controls.clear_prompt()
        self._set_status("Submitting generation job…")

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
            # Runs on worker thread — communicates back via GLib.idle_add.
            gen.progress = lambda msg: GLib.idle_add(self._on_progress, msg, pending)
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

    # ── Queue ──────────────────────────────────────────────────────────────────

    def _on_enqueue(self, prompt, neg, steps, seed, seed_image_path) -> None:
        self._queue.append(_QueueItem(prompt, neg, steps, seed, seed_image_path))
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
                          item.steps, item.seed, item.seed_image_path)
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
        pending = self._gallery.add_pending_card()
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
        self._gallery.replace_pending_with(record)
        self._controls.set_busy(False)
        self._set_status(f"Done — {record.video_path}  ({record.duration_s:.0f}s)")
        self._start_next_queued()
        return False

    def _on_error(self, message: str) -> bool:
        self._gallery.remove_pending()
        self._controls.set_busy(False)
        self._set_status(f"Error: {message}")
        return False

    def do_close_request(self) -> bool:
        self._health_stop.set()
        if self._worker_gen:
            self._worker_gen.cancel()
        return False  # allow close
