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
import json
import shutil
import subprocess
import sys
import threading
import time

_DISK_SPACE_MIN_BYTES = 18 * 1024 ** 3   # 18 GB — stop generating below this threshold
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import GdkPixbuf, GLib, Gio, Gtk, Pango

from api_client import APIClient
from app_settings import settings as _settings
from chip_config import load_chips as _load_chips
from history_store import GenerationRecord, HistoryStore
from worker import AnimateGenerationWorker, GenerationWorker, ImageGenerationWorker
import attractor
import prompt_client


# ── Tenstorrent dark palette as GTK CSS ───────────────────────────────────────

_CSS = b"""
/* -- Tenstorrent color palette -------------------------------------------- */
@define-color tt_bg_panel    #0A1F28;
@define-color tt_bg_darkest  #0F2A35;
@define-color tt_bg_dark     #1A3C47;
@define-color tt_border      #2D5566;
@define-color tt_accent      #4FD1C5;
@define-color tt_accent_light #81E6D9;
@define-color tt_text        #E8F0F2;
@define-color tt_text_muted  #607D8B;
@define-color tt_pink        #EC96B8;
@define-color tt_success     #27AE60;
@define-color tt_error       #FF6B6B;
@define-color tt_bg_error_dark #2D1A1A;
@define-color tt_bg_pink_dark  #2D1A2D;
@define-color tt_text_hint     #4A6572;

window, .view {
    background-color: @tt_bg_darkest;
    color: @tt_text;
}
* {
    font-family: "Noto Sans", "Segoe UI", sans-serif;
    font-size: 13px;
    color: @tt_text;
}
.section-label {
    color: @tt_accent;
    font-weight: bold;
    font-size: 11px;
}
.muted {
    color: @tt_text_muted;
    font-size: 11px;
}
.teal {
    color: @tt_accent;
}
entry, textview, spinbutton {
    background-color: @tt_bg_dark;
    color: @tt_text;
    border: 1px solid @tt_border;
    border-radius: 4px;
    padding: 4px;
}
entry:focus, textview:focus, spinbutton:focus {
    border-color: @tt_accent;
}
/* Inline validation error - applied when Generate is clicked with empty prompt */
scrolledwindow.prompt-error {
    border: 2px solid @tt_error;
}
label.prompt-error {
    color: @tt_error;
    font-size: 11px;
    margin-top: 2px;
}
button {
    background-color: @tt_bg_dark;
    color: @tt_text;
    border: 1px solid @tt_border;
    border-radius: 4px;
    padding: 5px 10px;
}
button:hover {
    background-color: @tt_border;
    border-color: @tt_accent;
}
button:disabled {
    color: @tt_text_muted;
    border-color: @tt_bg_dark;
}
.generate-btn {
    background-color: @tt_accent;
    color: @tt_bg_darkest;
    font-weight: bold;
    font-size: 14px;
    padding: 10px;
    border: none;
    border-radius: 4px;
}
.generate-btn:hover {
    background-color: @tt_accent_light;
}
.generate-btn:disabled {
    background-color: @tt_border;
    color: @tt_text_muted;
}
.cancel-btn {
    background-color: @tt_bg_error_dark;
    color: @tt_error;
    border: 1px solid @tt_error;
    border-radius: 4px;
    padding: 8px;
}
.cancel-btn:hover {
    background-color: @tt_error;
    color: @tt_bg_darkest;
}
.card {
    background-color: @tt_bg_dark;
    border: 1px solid @tt_border;
    border-radius: 6px;
    padding: 8px;
}
.card:hover {
    border-color: @tt_accent;
}
.queue-row {
    background-color: @tt_bg_dark;
    border: 1px solid @tt_border;
    border-radius: 3px;
    padding: 3px 6px;
}
.status-bar {
    background-color: @tt_bg_panel;
    color: @tt_text_muted;
    border-top: 1px solid @tt_bg_dark;
    padding: 3px 8px;
    font-size: 12px;
}
progressbar trough {
    background-color: @tt_bg_dark;
    border: 1px solid @tt_border;
    border-radius: 3px;
    min-height: 8px;
}
progressbar progress {
    background-color: @tt_accent;
    border-radius: 3px;
}
scrollbar {
    background-color: @tt_bg_darkest;
}
scrollbar slider {
    background-color: @tt_border;
    border-radius: 5px;
    min-width: 8px;
    min-height: 8px;
}
scrollbar slider:hover {
    background-color: @tt_accent;
}
.card-selected {
    border-color: @tt_accent;
    border-width: 2px;
}
.card-selected-image {
    border-color: @tt_pink;
    border-width: 2px;
}
.type-badge-video {
    background-color: @tt_bg_dark;
    color: @tt_accent;
    border: 1px solid @tt_accent;
    border-radius: 3px;
    padding: 0px 4px;
    font-size: 10px;
    font-weight: bold;
}
.type-badge-image {
    background-color: @tt_bg_pink_dark;
    color: @tt_pink;
    border: 1px solid @tt_pink;
    border-radius: 3px;
    padding: 0px 4px;
    font-size: 10px;
    font-weight: bold;
}
.type-badge-model {
    background-color: @tt_bg_darkest;
    color: @tt_text_muted;
    border: 1px solid @tt_border;
    border-radius: 3px;
    padding: 0px 4px;
    font-size: 10px;
}
.section-label {
    margin-top: 8px;
}
.hint {
    color: @tt_text_hint;
    font-size: 10px;
    margin-top: -2px;
}
.detail-section {
    color: @tt_accent;
    font-weight: bold;
    font-size: 11px;
    margin-top: 6px;
}
.mono {
    font-family: monospace;
    font-size: 11px;
    color: @tt_text_muted;
}
.detail-empty {
    color: @tt_border;
    font-size: 15px;
}
.chip-btn {
    background-color: @tt_bg_darkest;
    color: @tt_accent_light;
    border: 1px solid @tt_border;
    border-radius: 12px;
    padding: 2px 8px;
    font-size: 11px;
}
.chip-btn:hover {
    background-color: @tt_bg_dark;
    border-color: @tt_accent;
    color: @tt_text;
}
.chips-category-lbl {
    color: @tt_text_muted;
    font-size: 10px;
    margin-top: 4px;
}
.source-btn {
    background-color: @tt_bg_dark;
    color: @tt_text_muted;
    border: 1px solid @tt_border;
    border-radius: 0;
    padding: 4px 10px;
    font-size: 12px;
}
.source-btn:hover {
    background-color: @tt_border;
    color: @tt_text;
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
.source-btn-active,
.source-btn:checked {
    background-color: @tt_accent;
    color: @tt_bg_darkest;
    border-color: @tt_accent;
    font-weight: bold;
}
.source-btn-active:hover,
.source-btn:checked:hover {
    background-color: @tt_accent_light;
}
.server-start-btn {
    background-color: @tt_bg_dark;
    color: @tt_accent;
    border: 1px solid @tt_accent;
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 12px;
}
.server-start-btn:hover {
    background-color: @tt_border;
}
.server-start-btn:disabled {
    color: @tt_text_muted;
    border-color: @tt_border;
}
.server-stop-btn {
    background-color: @tt_bg_error_dark;
    color: @tt_error;
    border: 1px solid @tt_error;
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 12px;
}
.server-stop-btn:hover {
    background-color: @tt_error;
    color: @tt_bg_darkest;
}
.server-stop-btn:disabled {
    color: @tt_text_muted;
    border-color: @tt_border;
    background-color: @tt_bg_dark;
}
.trash-btn {
    background-color: transparent;
    color: @tt_text_muted;
    border: none;
    border-radius: 4px;
    padding: 2px 5px;
    font-size: 12px;
    min-width: 0;
}
.trash-btn:hover {
    background-color: @tt_bg_error_dark;
    color: @tt_error;
}
.server-log {
    font-family: monospace;
    font-size: 10px;
    color: @tt_accent_light;
    background-color: @tt_bg_panel;
    padding: 4px;
}
.server-launch-box {
    padding: 4px 2px 2px 2px;
}
.server-progress trough {
    background-color: @tt_bg_dark;
    border-radius: 4px;
    min-height: 8px;
}
.server-progress progress {
    background-color: @tt_accent;
    border-radius: 4px;
}
.server-phase-lbl {
    font-size: 10px;
    color: @tt_accent_light;
    margin-top: 1px;
}
.server-log-toggle {
    font-size: 9px;
    padding: 1px 6px;
    min-height: 0;
    min-width: 0;
    background: transparent;
    border: 1px solid @tt_border;
    color: @tt_text_muted;
    border-radius: 3px;
}
.server-log-toggle:hover {
    color: @tt_accent_light;
    border-color: @tt_accent;
}

/* -- Server row states ----------------------------------------------------- */
.server-row-match {
    background-color: @tt_bg_darkest;
    border: 1px solid alpha(@tt_accent, 0.4);
    border-radius: 4px;
    padding: 5px 6px;
}
.server-row-mismatch {
    background-color: #1A1000;
    border: 1px solid #F4C471;
    border-radius: 4px;
    padding: 5px 6px;
}
.server-row-offline {
    background-color: @tt_bg_darkest;
    border: 1px solid @tt_border;
    border-radius: 4px;
    padding: 5px 6px;
}
.server-row-starting {
    background-color: @tt_bg_darkest;
    border: 1px solid @tt_accent;
    border-radius: 4px;
    padding: 5px 6px;
}
.server-model-lbl {
    font-weight: bold;
    font-size: 11px;
}
.server-model-match  { color: @tt_success; }
.server-model-offline { color: @tt_text_muted; }
.server-model-mismatch { color: #F4C471; }
.server-model-starting { color: @tt_accent; }
.server-sub-lbl {
    color: @tt_text_hint;
    font-size: 9px;
}
.server-switch-btn {
    background: transparent;
    border: 1px solid #F4C471;
    color: #F4C471;
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 10px;
}
.server-switch-btn:hover {
    background: rgba(244, 196, 113, 0.15);
}

/* -- Advanced accordion ---------------------------------------------------- */
.adv-hdr-btn {
    background: @tt_bg_darkest;
    border: 1px solid @tt_border;
    border-radius: 4px;
    padding: 5px 8px;
    color: @tt_text_muted;
    font-size: 10px;
}
.adv-hdr-btn:hover {
    background: @tt_bg_dark;
    border-color: @tt_accent;
}
.adv-summary {
    color: @tt_text_muted;
    font-size: 9px;
}
.adv-summary-changed {
    color: @tt_pink;
    font-size: 9px;
}
.adv-body {
    background: @tt_bg_darkest;
    border: 1px solid @tt_border;
    border-top: none;
    border-bottom-left-radius: 4px;
    border-bottom-right-radius: 4px;
    padding: 8px;
}

/* -- Animate inputs box ---------------------------------------------------- */
.animate-inputs-box {
    border: 1px solid alpha(@tt_accent, 0.5);
    border-radius: 4px;
    padding: 6px 7px;
    background: @tt_bg_dark;
}
.animate-inputs-title {
    color: @tt_accent;
    font-size: 9px;
}

/* -- Inspire row (prompt generator) --------------------------------------- */
.inspire-btn {
    background-color: @tt_bg_darkest;
    color: @tt_accent_light;
    border: 1px solid @tt_border;
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 11px;
}
.inspire-btn:hover {
    background-color: @tt_bg_dark;
    border-color: @tt_accent;
    color: @tt_text;
}
.inspire-btn:disabled {
    color: @tt_text_muted;
    border-color: @tt_bg_dark;
}
.inspire-btn-loading {
    color: @tt_accent;
    border: 1px solid @tt_accent;
}
.inspire-dot {
    font-size: 9px;
    color: @tt_text_muted;
}
.inspire-dot-ready {
    color: @tt_success;
}
.inspire-dot-starting {
    color: @tt_accent;
}
.inspire-confirm-box {
    background-color: @tt_bg_darkest;
    border: 1px solid @tt_accent;
    border-radius: 4px;
    padding: 6px 8px;
    margin-top: 2px;
}
.inspire-confirm-btn {
    background-color: @tt_bg_dark;
    color: @tt_accent;
    border: 1px solid @tt_accent;
    border-radius: 3px;
    padding: 3px 8px;
    font-size: 11px;
}
.inspire-confirm-btn:hover {
    background-color: @tt_border;
}
.inspire-confirm-btn:disabled {
    color: @tt_text_muted;
    border-color: @tt_border;
    background-color: @tt_bg_darkest;
}

/* -- Attractor launch button ---------------------------------------------- */
.attractor-launch-btn {
    background-color: @tt_bg_darkest;
    color: @tt_accent_light;
    border: 1px solid @tt_border;
    border-radius: 4px;
    padding: 3px 10px;
    font-size: 11px;
}
.attractor-launch-btn:hover {
    background-color: @tt_bg_dark;
    border-color: @tt_accent;
    color: @tt_text;
}
.attractor-launch-btn:disabled {
    color: @tt_text_muted;
    border-color: @tt_bg_dark;
}

/* -- Toolbar (logo + source + model, pinned to top of window) -------------- */
.tt-toolbar {
    background-color: @tt_bg_darkest;
    border-bottom: 1px solid @tt_border;
    padding: 4px 8px;
    min-height: 34px;
}
.tt-toolbar-title {
    color: @tt_accent;
    font-size: 11px;
    font-weight: bold;
    margin-left: 4px;
    margin-right: 8px;
}

/* -- Status bar (server dot + queue + disk + chip, pinned to bottom) ------- */
.tt-statusbar {
    background-color: @tt_bg_darkest;
    border-top: 1px solid @tt_border;
    padding: 2px 10px;
    min-height: 24px;
}
.tt-statusbar-dot {
    font-size: 8px;
    margin-right: 4px;
}
.tt-statusbar-dot-ready   { color: @tt_success; }
.tt-statusbar-dot-offline { color: @tt_text_muted; }
.tt-statusbar-dot-starting { color: @tt_accent; }
.tt-statusbar-seg {
    font-size: 10px;
    color: @tt_text_muted;
}
.tt-statusbar-seg-warn {
    font-size: 10px;
    color: #FF6B6B;
}
.tt-statusbar-sep {
    color: @tt_border;
    font-size: 10px;
    margin-left: 8px;
    margin-right: 8px;
}
/* MenuButton wrapping the server dot - no decorations, just the label content */
.tt-statusbar-srv-btn {
    background: transparent;
    border: none;
    padding: 0 4px;
    min-height: 0;
    min-width: 0;
}
.tt-statusbar-srv-btn:hover {
    background: alpha(@tt_accent, 0.08);
    border-radius: 3px;
}

/* -- App menu bar ---------------------------------------------------------- */
menubar {
    background-color: @tt_bg_panel;
    border-bottom: 1px solid @tt_bg_dark;
    padding: 0;
    min-height: 0;
}
menubar > item {
    padding: 2px 8px;
    color: @tt_text_muted;
    font-size: 11px;
    border-radius: 0;
}
menubar > item:hover,
menubar > item:selected {
    background-color: @tt_bg_dark;
    color: @tt_text;
}
/* Preferences dialog sections */
.prefs-section-title {
    color: @tt_accent;
    font-weight: bold;
    font-size: 12px;
    margin-top: 8px;
}
.prefs-row {
    padding: 4px 0;
}
"""

# ── Prompt component chips ────────────────────────────────────────────────────
# Loaded once at startup from config/prompt_chips.yaml via chip_config.py.
# Falls back to empty list if the file is missing or malformed.

def _load_chips_safe(tab: str) -> list:
    try:
        return _load_chips(tab)
    except Exception as e:
        print(f"Warning: could not load chips for '{tab}': {e}", file=sys.stderr)
        return []

_VIDEO_CHIPS   = _load_chips_safe("video")
_IMAGE_CHIPS   = _load_chips_safe("image")
_ANIMATE_CHIPS = _load_chips_safe("animate")

_THUMB_W = 200
_THUMB_H = 112   # 16:9
_DETAIL_VIDEO_W = 480
_DETAIL_VIDEO_H = 270

# Maps internal model ID strings to short display names shown on gallery badges.
# Empty string → no badge (legacy records without model attribution).
_MODEL_DISPLAY: dict = {
    "wan2.2-t2v":         "Wan2.2",
    "mochi-1-preview":    "Mochi-1",
    "flux.1-dev":         "FLUX",
    "wan2.2-animate-14b": "Animate-14B",
}

# Short director names shown in the menu + Preferences dialog, mapped to the
# full CINEMATIC_DIRECTORS string that actually goes into the prompt slug.
# "Random" (empty key) means sample from the full list based on director_style_prob.
_DIRECTOR_PINS: list[tuple[str, str]] = [
    ("Random",          ""),
    ("Hitchcock",       "Hitchcock — voyeuristic high-angle thriller, chiaroscuro"),
    ("Spielberg",       "Spielberg — golden-hour backlit silhouette, kinetic wonder, child's-eye rack-focus reveal"),
    ("Penny Marshall",  "Penny Marshall — warm ensemble Americana, naturalistic ensemble blocking, working-class tenderness"),
    ("Roger Corman",    "Roger Corman — garish B-movie color, Gothic camp excess, drive-in spectacle on a shoestring"),
    ("Mel Brooks",      "Mel Brooks — vaudevillian sight gag, wide parody staging, anachronistic wink at the camera"),
    ("Sofia Coppola",   "Sofia Coppola — luxury melancholy, feminine interior silence"),
    ("Kubrick",         "Kubrick — tight frame, obsessive detail, cold symmetry"),
    ("Tarkovsky",       "Tarkovsky — slow-burn long take, transcendent water and fire"),
    ("Fellini",         "Fellini — carnival dreamscape, baroque crowd, memory dissolve"),
    ("Kurosawa",        "Kurosawa — widescreen epic in driving rain, weather as emotion"),
    ("Wong Kar-wai",    "Wong Kar-wai — neon overexposure, slow-motion missed connection"),
    ("Bergman",         "Bergman — faces in extreme close-up, death as quiet presence"),
    ("Godard",          "Godard — jump cut, primary color wall, direct address"),
    ("Varda",           "Varda — tender personal essay, sun-drenched beach, wry voice"),
    ("Herzog",          "Herzog — obsession dwarfed by impossible landscape"),
    ("Ozu",             "Ozu — tatami-level static, family at table, pillow shot"),
    ("Antonioni",       "Antonioni — alienated figure in stark modern architecture"),
]
# Reverse map: full string → display name (for restoring menu state from settings)
_DIRECTOR_PIN_LABEL: dict[str, str] = {v: k for k, v in _DIRECTOR_PINS}

# Keys to skip when rendering record.extra_meta in the detail panel — these
# fields are either shown elsewhere in the panel or too noisy to display.
_SKIP_META_KEYS: frozenset = frozenset({
    "status", "error", "id", "model", "prompt", "negative_prompt",
    "num_inference_steps", "seed", "request_parameters", "guidance_scale",
})

# Maps (model_source, model_key) to (script_filename, display_label) for server launch.
_SERVER_SCRIPTS: dict = {
    ("video",   "wan2"):  ("start_wan.sh",     "Wan2.2 video"),
    ("video",   "mochi"): ("start_mochi.sh",   "Mochi-1 video"),
    ("image",   "flux"):  ("start_flux.sh",    "FLUX image"),
    ("animate", ""):      ("start_animate.sh", "Wan2.2-Animate"),
}

# Maps short model keys to canonical model ID strings used in GenerationRecord.
_VIDEO_MODEL_IDS: dict = {
    "wan2":  "wan2.2-t2v",
    "mochi": "mochi-1-preview",
}
_IMAGE_MODEL_IDS: dict = {
    "flux": "flux.1-dev",
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
    job_id_override: str = ""        # non-empty → recovery item; skip submission, poll this job ID directly


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
            # Create the widget without a file so no GStreamer pipeline is opened
            # at construction time.  With a large history every card would eagerly
            # open a pipeline, each holding several file-descriptors.  We load the
            # file lazily (just before first play) and unload it (set_file(None))
            # when the card stops playing, so only actively-playing cards hold fds.
            self._hover_video = Gtk.Video()
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
        # Reset to False whenever the file is unloaded (set_file(None)).
        self._loop_connected = False
        # Tracks whether a GStreamer pipeline is currently open for this card.
        # Used to gate set_file(None)+set_filename() calls so we never open a
        # second pipeline while a previous one is still asynchronously tearing
        # down.  Always call _open_hover_pipeline() / _close_hover_pipeline()
        # instead of set_file / set_filename directly.
        self._hover_pipeline_open: bool = False

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

        # Model attribution badge — omitted for legacy records with no model field
        model_display = _MODEL_DISPLAY.get(self._record.model, "")
        if model_display:
            model_badge = Gtk.Label(label=model_display)
            model_badge.add_css_class("type-badge-model")
            meta.append(model_badge)

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

    def _open_hover_pipeline(self) -> None:
        """Open (or re-open) the GStreamer pipeline for this card's hover video.

        Always calls set_file(None) immediately before set_filename() so that
        any previously-started async pipeline teardown is forced to complete
        synchronously before a new pipeline is created.  Without this, rapid
        open/close cycles (e.g. scrolling) accumulate async-tearing-down
        pipelines, each holding GStreamer file-descriptors, until the process
        hits the fd limit and crashes.
        """
        if self._hover_video is None:
            return
        self._hover_video.set_file(None)          # force-complete any prior teardown
        self._hover_video.set_filename(self._record.video_path)
        self._hover_pipeline_open = True
        self._loop_connected = False              # new pipeline → new stream object

    def _close_hover_pipeline(self) -> None:
        """Pause playback and release the GStreamer pipeline for this card."""
        if self._hover_video is None:
            return
        stream = self._hover_video.get_media_stream()
        if stream is not None:
            stream.pause()
        self._hover_video.set_file(None)
        self._hover_pipeline_open = False
        self._loop_connected = False

    def _on_hover_enter(self, _ctrl, _x, _y) -> None:
        """Start looping the video silently when the mouse enters the card."""
        if self._hover_video is None:
            return
        if not self._hover_pipeline_open:
            self._open_hover_pipeline()
        self._media_stack.set_visible_child_name("video")
        self._play_hover_stream()

    def _play_hover_stream(self) -> None:
        """
        Play the hover video stream, wiring up the manual loop handler the first
        time.  Gtk.Video creates its GStreamer pipeline lazily — get_media_stream()
        returns None until the widget has been realized, so we guard here and let
        the caller retry if needed.
        """
        if self._hover_video is None or not self._hover_pipeline_open:
            # Card was unloaded before this retry fired — stop the retry chain.
            return
        stream = self._hover_video.get_media_stream()
        if stream is None:
            # Pipeline not yet ready — retry after GStreamer initialises.
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
        self._close_hover_pipeline()
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
            # Begin GStreamer pipeline teardown now rather than waiting for GTK's
            # async widget destruction to trigger it.
            self._video_widget.set_file(None)
        self._video_widget = None
        self._play_btn = None
        self._show_empty()

    def show_record(self, record: GenerationRecord, iterate_cb) -> None:
        """Populate the panel with a completed generation record."""
        self._record = record
        self._iterate_cb = iterate_cb

        # Unload the previous video pipeline before replacing it.  Calling
        # set_file(None) starts GStreamer teardown immediately; without it the
        # teardown is deferred until GTK's async widget destruction, which can
        # leave the pipeline's fds open longer than necessary.
        if self._video_widget is not None:
            stream = self._video_widget.get_media_stream()
            if stream and stream.get_playing():
                stream.pause()
            self._video_widget.set_file(None)
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
            ("Model",        record.model if record.model else "unknown"),
            ("Type",         "Image" if record.media_type == "image" else "Video"),
            ("Steps",        str(record.num_inference_steps)),
        ]
        if record.media_type == "image" and record.guidance_scale:
            rows.append(("Guidance",     f"{record.guidance_scale:.1f}"))
        rows += [
            ("Seed",         seed_str),
            ("Generated in", _fmt_duration(record.duration_s) if record.duration_s else "—"),
            ("Speed",        (
                f"{record.duration_s / record.num_inference_steps:.1f} s/step"
                if record.duration_s and record.num_inference_steps
                else "—"
            )),
            ("File",         file_name),
            ("Size",         size_str),
            ("Job ID",       record.id),
        ]

        # Append any extra metadata returned by the server, skipping fields
        # already shown above or too large/noisy to display.
        for k, v in record.extra_meta.items():
            if k in _SKIP_META_KEYS or v is None or not str(v).strip():
                continue
            rows.append((k.replace("_", " ").title(), str(v)))
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

class GalleryWidget(Gtk.Box):
    """
    Scrollable grid of GenerationCards, newest first.

    Uses Gtk.FlowBox so the number of columns adjusts automatically as the pane
    is resized — no fixed column count.  Cards expand to fill the row.

    Hover-to-preview: hovering over a video card plays it silently in the
    thumbnail.  Pipelines are loaded lazily on hover-enter and released on
    hover-leave to minimise GStreamer resource use.
    """

    def __init__(self, iterate_cb, select_cb, delete_cb, media_type: str = "video"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_vexpand(True)
        self.set_hexpand(True)
        self._iterate_cb = iterate_cb
        self._select_cb = select_cb   # select_cb(record: GenerationRecord) called on click
        self._delete_cb = delete_cb   # delete_cb(record: GenerationRecord) called on trash

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

    def _video_cards(self) -> list:
        """Return GenerationCards whose video file exists (skips pending and image cards)."""
        return [c for c in self._cards
                if isinstance(c, GenerationCard) and c._record.video_exists]

    def stop_all_playback(self) -> None:
        """Release every open hover-preview pipeline. Called before launching Attractor Mode."""
        for card in self._video_cards():
            try:
                card._close_hover_pipeline()
                card._media_stack.set_visible_child_name("thumb")
            except Exception:
                pass

    def add_pending_card(self, prompt: str = "", model_source: str = "video") -> PendingCard:
        card = PendingCard(prompt=prompt, model_source=model_source)
        self._pending = card
        self._cards.insert(0, card)
        self._relayout()
        return card

    def replace_pending_with(self, record: GenerationRecord) -> None:
        # Guard: don't add a card if this record is already in the gallery
        # (can happen if a recovery worker races with load_history on restart).
        if any(isinstance(c, GenerationCard) and c._record.id == record.id
               for c in self._cards):
            if self._pending and self._pending in self._cards:
                self._pending.stop_timer()
                self._cards.remove(self._pending)
            self._pending = None
            self._relayout()
            return

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
        seen: set = set()
        for record in records:
            if record.id in seen:
                continue  # skip duplicates (shouldn't happen after HistoryStore dedup)
            seen.add(record.id)
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

# Maps server model ID → UI source tab key.
# Used by both ControlPanel.set_server_state() and MainWindow._on_health_result().
_MODEL_TO_SOURCE: dict = {
    "wan2.2-t2v":           "video",
    "mochi-1-preview":      "video",
    "wan2.2-animate-14b":   "animate",
    "flux.1-dev":           "image",
}
# Maps server model ID → internal video-model key used by ControlPanel
_MODEL_TO_VIDEO_KEY: dict = {
    "wan2.2-t2v":      "wan2",
    "mochi-1-preview": "mochi",
}
_MODEL_DISPLAY_SERVER: dict = {
    "wan2.2-t2v":           "Wan2.2 online",
    "mochi-1-preview":      "Mochi-1 online",
    "wan2.2-animate-14b":   "Animate-14B online",
    "flux.1-dev":           "FLUX online",
}

class ControlPanel(Gtk.Box):
    """
    Left panel: prompt fields, parameters, seed image, server status,
    generate/cancel/recover buttons, and the prompt queue.
    """

    def __init__(
        self,
        on_generate,       # (prompt, neg, steps, seed, seed_image_path, model_source, guidance_scale, ref_video_path, ref_char_path, animate_mode, model_id) -> None
        on_enqueue,        # same signature
        on_cancel,         # () -> None
        on_start_server,   # (model_source: str) -> None
        on_stop_server,    # () -> None
        on_source_change,  # (model_source: str) -> None — called after the mode toggle switches
        on_start_prompt_gen = None,  # () -> None — launch start_prompt_gen.sh --gui
        on_inspire = None,           # (source: str, seed_text: str) -> None — start generation thread
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._on_generate = on_generate
        self._on_enqueue = on_enqueue
        self._on_cancel = on_cancel
        self._on_start_server = on_start_server
        self._on_stop_server = on_stop_server
        self._on_source_change = on_source_change
        self._on_start_prompt_gen = on_start_prompt_gen or (lambda: None)
        self._on_inspire = on_inspire or (lambda s, t: None)
        # ── Prompt gen server state ───────────────────────────────────────────
        self._prompt_gen_ready: bool = False      # True when port 8001 health check passes
        self._prompt_gen_starting: bool = False   # True while start_prompt_gen.sh is running
        self._prompt_gen_generating: bool = False # True while waiting for generate_prompt()
        self._confirm_box_visible: bool = False   # True while inline confirm box is shown
        # Source + seed captured at click time for auto-generate after server starts
        self._inspire_pending_source: "str | None" = None
        self._inspire_pending_seed: str = ""
        self._seed_image_path = ""
        self._ref_video_path = ""      # animate: motion source video
        self._ref_char_path = ""       # animate: character image
        self._animate_mode = "animation"
        self._server_ready = False
        self._running_model: "str | None" = None  # model ID from /v1/models, or None
        self._adv_open: bool = False               # accordion expanded state
        self._server_launching = False   # True while start/stop script is running
        self._busy = False
        self._model_source = "video"   # "video", "image", or "animate"
        self._video_model: str = "wan2"   # "wan2" | "mochi"
        self._image_model: str = "flux"   # "flux" | future models
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
        # ── Toolbar (lives outside the panel scroll area, pinned at the top of the
        #    window by MainWindow._build_ui).  Contains the logo/title, source toggle,
        #    and model selector so the scrollable control panel can focus entirely on
        #    prompt composition.
        self._toolbar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._toolbar_box.add_css_class("tt-toolbar")

        # Logo + title
        _logo_path = str(Path(__file__).parent / "assets" / "tenstorrent.png")
        _logo_img = Gtk.Image.new_from_file(_logo_path)
        _logo_img.set_pixel_size(22)
        self._toolbar_box.append(_logo_img)
        self._title_lbl = Gtk.Label(label="TT VIDEO GENERATOR")
        self._title_lbl.add_css_class("tt-toolbar-title")
        attrs = Pango.AttrList()
        attrs.insert(Pango.AttrFontDesc.new(
            Pango.FontDescription.from_string("sans bold 13")))
        self._title_lbl.set_attributes(attrs)
        self._toolbar_box.append(self._title_lbl)

        # Divider
        _tb_sep1 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        _tb_sep1.set_margin_start(6)
        _tb_sep1.set_margin_end(6)
        _tb_sep1.set_margin_top(6)
        _tb_sep1.set_margin_bottom(6)
        self._toolbar_box.append(_tb_sep1)

        # ── Source toggle (Video / Animate / Image) ───────────────────────────
        src_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._src_video_btn = Gtk.ToggleButton(label="🎬 Video")
        self._src_video_btn.add_css_class("source-btn")
        self._src_video_btn.add_css_class("source-btn-left")
        self._src_video_btn.set_tooltip_text(
            "Wan2.2-T2V-A14B  ·  Async job-based  ·  5-second 720p MP4\n"
            "Supports seed images for motion reference"
        )
        self._src_animate_btn = Gtk.ToggleButton(label="💃 Animate")
        self._src_animate_btn.add_css_class("source-btn")
        self._src_animate_btn.add_css_class("source-btn-mid")
        self._src_animate_btn.set_tooltip_text(
            "Wan2.2-Animate-14B  ·  Character animation  ·  Video-to-video\n"
            "Requires a motion video + character image"
        )
        self._src_image_btn = Gtk.ToggleButton(label="🖼 Image")
        self._src_image_btn.add_css_class("source-btn")
        self._src_image_btn.add_css_class("source-btn-right")
        self._src_image_btn.set_tooltip_text(
            "FLUX.1-dev  ·  Synchronous request  ·  ~1024×1024 JPEG\n"
            "Blocks until image is ready (~15–90 s)"
        )
        self._src_animate_btn.set_group(self._src_video_btn)
        self._src_image_btn.set_group(self._src_video_btn)
        self._src_video_btn.connect("toggled", lambda b: b.get_active() and self._set_source("video"))
        self._src_animate_btn.connect("toggled", lambda b: b.get_active() and self._set_source("animate"))
        self._src_image_btn.connect("toggled", lambda b: b.get_active() and self._set_source("image"))
        self._src_video_btn.set_active(True)
        src_row.append(self._src_video_btn)
        src_row.append(self._src_animate_btn)
        self._src_animate_btn.set_visible(False)  # hidden until Wan2.2-Animate-14B is ready
        src_row.append(self._src_image_btn)
        self._toolbar_box.append(src_row)

        # ── Video model selector ──────────────────────────────────────────────
        self._model_sel_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._model_sel_row.set_margin_start(4)

        self._mdl_wan2_btn = Gtk.ToggleButton(label="Wan2.2")
        self._mdl_wan2_btn.add_css_class("source-btn")
        self._mdl_wan2_btn.add_css_class("source-btn-left")
        self._mdl_wan2_btn.set_tooltip_text(
            "Wan2.2-T2V-A14B  ·  720p MP4  ·  ~3–10 min\n"
            "Launches start_wan.sh"
        )
        self._mdl_mochi_btn = Gtk.ToggleButton(label="Mochi-1")
        self._mdl_mochi_btn.add_css_class("source-btn")
        self._mdl_mochi_btn.add_css_class("source-btn-right")
        self._mdl_mochi_btn.set_tooltip_text(
            "Mochi-1  ·  480×848  ·  168 frames  ·  ~5–15 min\n"
            "Launches start_mochi.sh"
        )
        self._mdl_mochi_btn.set_group(self._mdl_wan2_btn)
        self._mdl_wan2_btn.connect("toggled", lambda b: b.get_active() and self._set_model("wan2"))
        self._mdl_mochi_btn.connect("toggled", lambda b: b.get_active() and self._set_model("mochi"))
        self._mdl_wan2_btn.set_active(True)
        self._model_sel_row.append(self._mdl_wan2_btn)
        self._model_sel_row.append(self._mdl_mochi_btn)

        # ── Image model selector ──────────────────────────────────────────────
        self._img_model_sel_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._img_model_sel_row.set_margin_start(4)

        self._mdl_flux_btn = Gtk.Button(label="FLUX.1-dev")
        self._mdl_flux_btn.add_css_class("source-btn")
        self._mdl_flux_btn.add_css_class("source-btn-left")
        self._mdl_flux_btn.add_css_class("source-btn-right")
        self._mdl_flux_btn.add_css_class("source-btn-active")
        self._mdl_flux_btn.set_tooltip_text(
            "FLUX.1-dev  ·  1024×1024 JPEG  ·  ~15–90 s\n"
            "Launches start_flux.sh"
        )
        self._mdl_flux_btn.connect("clicked", lambda _: self._set_model("flux"))
        self._img_model_sel_row.append(self._mdl_flux_btn)

        # Video selector visible by default; image selector shown when source=image
        self._model_sel_row.set_visible(True)
        self._img_model_sel_row.set_visible(False)

        self._toolbar_box.append(self._model_sel_row)
        self._toolbar_box.append(self._img_model_sel_row)

        # Spacer (MainWindow appends attractor + other buttons after this)
        _tb_spacer = Gtk.Box()
        _tb_spacer.set_hexpand(True)
        self._toolbar_box.append(_tb_spacer)

        # _source_desc_lbl is kept for internal _update_source_desc() calls
        # but no longer shown in the panel — the status bar shows model info.
        self._source_desc_lbl = Gtk.Label(label="")
        self._source_desc_lbl.set_visible(False)

        # ── Prompt ────────────────────────────────────────────────────────────
        self.append(self._section("Prompt"))
        scroll1 = Gtk.ScrolledWindow()
        self._prompt_scroll = scroll1   # kept for inline-validation error styling
        scroll1.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll1.set_size_request(-1, 110)
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
        self._prompt_buf = self._prompt_view.get_buffer()
        self._prompt_buf.connect(
            "changed", lambda b: ph.set_visible(b.get_char_count() == 0)
        )
        # Clear validation error state as soon as the user types anything
        self._prompt_buf.connect("changed", self._on_prompt_changed)
        scroll1.set_child(overlay1)
        self.append(scroll1)

        # Inline validation error label — hidden until Generate is clicked with empty prompt
        self._prompt_error_lbl = Gtk.Label(label="Prompt cannot be empty.")
        self._prompt_error_lbl.add_css_class("prompt-error")
        self._prompt_error_lbl.set_halign(Gtk.Align.START)
        self._prompt_error_lbl.set_visible(False)
        self.append(self._prompt_error_lbl)

        # ── Inspire row ───────────────────────────────────────────────────────
        # "✨ Inspire me" button + status dot for the prompt gen server (port 8001).
        inspire_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._inspire_btn = Gtk.Button(label="✨ Inspire me")
        self._inspire_btn.add_css_class("inspire-btn")
        self._inspire_btn.set_tooltip_text(
            "Generate a cinematic prompt using the local Qwen3-0.6B model.\n"
            "If the prompt box already has text, it is used as a creative seed.\n"
            "Requires: ./start_prompt_gen.sh  (CPU-only, ~1.2 GB one-time download)"
        )
        self._inspire_btn.connect("clicked", self._on_inspire_clicked)
        inspire_row.append(self._inspire_btn)

        _inspire_spacer = Gtk.Box()
        _inspire_spacer.set_hexpand(True)
        inspire_row.append(_inspire_spacer)

        self._inspire_dot_lbl = Gtk.Label(label="⬤ algo only")
        self._inspire_dot_lbl.add_css_class("inspire-dot")
        inspire_row.append(self._inspire_dot_lbl)
        self.append(inspire_row)

        # Confirm box — hidden; slides in when Inspire is clicked while server is offline
        self._inspire_confirm_revealer = Gtk.Revealer()
        self._inspire_confirm_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN
        )
        self._inspire_confirm_revealer.set_transition_duration(150)
        self._inspire_confirm_revealer.set_reveal_child(False)
        _confirm_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        _confirm_box.add_css_class("inspire-confirm-box")
        _confirm_msg = Gtk.Label(
            label="Prompt generator isn't running. Start it now? (~20s warm-up)"
        )
        _confirm_msg.set_xalign(0)
        _confirm_msg.set_wrap(True)
        _confirm_box.append(_confirm_msg)
        _confirm_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._inspire_start_btn = Gtk.Button(label="▶ Start")
        self._inspire_start_btn.add_css_class("inspire-confirm-btn")
        self._inspire_start_btn.connect("clicked", self._on_inspire_confirm_start)
        _confirm_btns.append(self._inspire_start_btn)
        _inspire_cancel_btn = Gtk.Button(label="Not now")
        _inspire_cancel_btn.connect("clicked", self._on_inspire_confirm_cancel)
        _confirm_btns.append(_inspire_cancel_btn)
        _confirm_box.append(_confirm_btns)
        self._inspire_confirm_revealer.set_child(_confirm_box)
        self.append(self._inspire_confirm_revealer)

        # ── Prompt component chips ────────────────────────────────────────────
        # Clicking a chip appends its modifier text to the prompt.
        # The chip list changes when source changes (video ↔ image).
        chips_hdr = Gtk.Label(label="Style modifiers — click to append:")
        chips_hdr.set_xalign(0)
        chips_hdr.add_css_class("hint")
        self.append(chips_hdr)
        self._chips_scroll = Gtk.ScrolledWindow()
        # No scrolling here — the outer ctrl_scroll handles it.
        # Propagate natural height so all chip rows are fully visible.
        self._chips_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        self._chips_scroll.set_propagate_natural_height(True)
        self._chips_scroll.set_propagate_natural_width(True)
        self._chips_scroll.set_child(self._make_chips_box("video"))
        self.append(self._chips_scroll)

        # ── Advanced settings accordion ───────────────────────────────────────
        # Note: self.append(self._animate_box) is deferred until after
        # self._animate_box is fully constructed below; the accordion header/revealer
        # are set up here, but adv_body is assembled later after all widget vars exist.
        self._adv_revealer = Gtk.Revealer()
        self._adv_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._adv_revealer.set_transition_duration(150)
        self._adv_revealer.set_reveal_child(False)

        # Header button — full-width toggle
        self._adv_hdr_btn = Gtk.Button()
        self._adv_hdr_btn.add_css_class("adv-hdr-btn")
        self._adv_hdr_btn.connect("clicked", self._on_adv_toggle)
        hdr_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._adv_arrow_lbl = Gtk.Label(label="\u25b8")
        self._adv_arrow_lbl.set_xalign(0)
        hdr_inner.append(self._adv_arrow_lbl)
        hdr_section_lbl = Gtk.Label(label="Advanced settings")
        hdr_section_lbl.set_xalign(0)
        hdr_section_lbl.set_hexpand(True)
        hdr_inner.append(hdr_section_lbl)
        self._adv_summary_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hdr_inner.append(self._adv_summary_box)
        self._adv_hdr_btn.set_child(hdr_inner)

        # ── Negative prompt ───────────────────────────────────────────────────
        # (appended into accordion adv_body below, not directly to self)
        _neg_section_lbl = self._section("Negative Prompt")
        neg_hint = Gtk.Label(label="Steer away from: blurry, watermark, low quality, distorted")
        neg_hint.set_xalign(0)
        neg_hint.set_ellipsize(Pango.EllipsizeMode.END)
        neg_hint.add_css_class("hint")
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

        # ── Parameters ────────────────────────────────────────────────────────
        # (appended into accordion adv_body below, not directly to self)
        _param_section_lbl = self._section("Parameters")
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

        # ── Seed image ────────────────────────────────────────────────────────
        # Only relevant for Wan2.2 video; hidden when FLUX image source is selected.
        # (appended into accordion adv_body below, not directly to self)
        self._seed_img_section = self._section("Seed Image (optional)")
        self._seed_img_section.set_tooltip_text(
            "Reference image passed to Wan2.2 to guide motion and composition.\n"
            "The model uses it as a visual starting point — not copied verbatim.\n"
            "PNG or JPEG, any aspect ratio (resized internally)."
        )
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

        # ── Animate inputs ────────────────────────────────────────────────────
        # Visible only when "animate" source is active.
        self._animate_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._animate_box.add_css_class("animate-inputs-box")
        self._animate_box.set_visible(False)

        _anim_title = Gtk.Label(label="💃 ANIMATE INPUTS")
        _anim_title.set_xalign(0)
        _anim_title.add_css_class("animate-inputs-title")
        self._animate_box.append(_anim_title)

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
        self._anim_mode_anim_btn = Gtk.ToggleButton(label="🔄 Animation")
        self._anim_mode_anim_btn.add_css_class("source-btn")
        self._anim_mode_anim_btn.add_css_class("source-btn-left")
        self._anim_mode_anim_btn.set_tooltip_text(
            "Character mimics the motion from the reference video"
        )
        self._anim_mode_repl_btn = Gtk.ToggleButton(label="🔀 Replacement")
        self._anim_mode_repl_btn.add_css_class("source-btn")
        self._anim_mode_repl_btn.add_css_class("source-btn-right")
        self._anim_mode_repl_btn.set_tooltip_text(
            "Character replaces the person in the reference video"
        )
        # Animate mode button group — only one mode active at a time.
        self._anim_mode_repl_btn.set_group(self._anim_mode_anim_btn)
        self._anim_mode_anim_btn.connect("toggled", lambda b: b.get_active() and self._set_animate_mode("animation"))
        self._anim_mode_repl_btn.connect("toggled", lambda b: b.get_active() and self._set_animate_mode("replacement"))
        self._anim_mode_anim_btn.set_active(True)
        mode_row.append(self._anim_mode_anim_btn)
        mode_row.append(self._anim_mode_repl_btn)
        self._animate_box.append(mode_row)

        # Animate inputs — visible only in animate mode, positioned below chips.
        # Appended here (after construction) so self._animate_box is ready.
        self.append(self._animate_box)

        # ── Pinned footer — always visible, NOT inside the scroll ─────────────
        # MainWindow places self._footer_box below ctrl_scroll so these widgets
        # remain visible regardless of how short the window is.
        self._footer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._footer_box.append(self._adv_hdr_btn)  # accordion header

        # ── Accordion body — neg prompt, params, seed image ───────────────────
        # adv_body is the content revealed when the accordion header is clicked.
        # All three sections (neg prompt, parameters, seed image) live here so
        # they are hidden by default and only visible when the user opens the drawer.
        adv_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        adv_body.add_css_class("adv-body")
        adv_body.append(_neg_section_lbl)
        adv_body.append(neg_hint)
        adv_body.append(scroll2)
        adv_body.append(_param_section_lbl)
        adv_body.append(param_grid)
        adv_body.append(self._seed_img_section)
        adv_body.append(seed_row)
        self._adv_revealer.set_child(adv_body)
        self._footer_box.append(self._adv_revealer)

        # Connect spinbuttons to update summary on value change
        self._steps_spin.connect("value-changed", lambda _: self._update_adv_summary())
        self._seed_spin.connect("value-changed", lambda _: self._update_adv_summary())
        self._update_adv_summary()

        # ── Server status row ─────────────────────────────────────────────────
        # Two-line status box: dot + model name + sub-label + action buttons.
        self._server_status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._server_status_box.add_css_class("server-row-offline")

        # Left side: indicator dot
        self._server_dot_lbl = Gtk.Label(label="⬤")
        self._server_dot_lbl.add_css_class("server-model-offline")
        self._server_status_box.append(self._server_dot_lbl)

        # Center: two-line text column (model name + sub-label)
        text_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text_col.set_hexpand(True)
        self._server_model_lbl = Gtk.Label(label="No server")
        self._server_model_lbl.add_css_class("server-model-lbl")
        self._server_model_lbl.add_css_class("server-model-offline")
        self._server_model_lbl.set_xalign(0)
        self._server_sub_lbl = Gtk.Label(label="localhost:8000 unreachable")
        self._server_sub_lbl.add_css_class("server-sub-lbl")
        self._server_sub_lbl.set_xalign(0)
        text_col.append(self._server_model_lbl)
        text_col.append(self._server_sub_lbl)
        self._server_status_box.append(text_col)

        # Right side: action buttons (Start, Stop, Switch tab)
        btn_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._server_start_btn = Gtk.Button(label="▶ Start")
        self._server_start_btn.add_css_class("server-start-btn")
        self._server_start_btn.set_tooltip_text(
            "Start the inference server using the local launch script.\n"
            "Video → start_wan.sh  ·  Animate → start_animate.sh  ·  Image → start_flux.sh"
        )
        self._server_start_btn.set_sensitive(False)
        self._server_start_btn.connect("clicked", self._on_start_server_clicked)
        btn_col.append(self._server_start_btn)

        self._server_stop_btn = Gtk.Button(label="■ Stop")
        self._server_stop_btn.add_css_class("server-stop-btn")
        self._server_stop_btn.set_tooltip_text(
            "Stop the running inference server Docker container.\n"
            "Stops any container using the tt-media-inference-server image."
        )
        self._server_stop_btn.set_sensitive(False)
        self._server_stop_btn.connect("clicked", self._on_stop_server_clicked)
        btn_col.append(self._server_stop_btn)

        self._server_switch_btn = Gtk.Button(label="Switch tab")
        self._server_switch_btn.add_css_class("server-switch-btn")
        self._server_switch_btn.set_visible(False)
        self._server_switch_btn.set_tooltip_text(
            "Switch to the source tab that matches the running server model"
        )
        self._server_switch_btn.connect("clicked", self._on_switch_to_running_model_tab)
        btn_col.append(self._server_switch_btn)

        self._server_status_box.append(btn_col)
        # Server status row is hidden — state dot, model label, and Start/Stop
        # now live in the _StatusBar popover at the bottom of the window.
        # The widgets still exist so set_server_state() can update them internally
        # (for sensitivity logic, switch-tab button, etc.) without needing rewiring.
        self._server_status_box.set_visible(False)
        self._footer_box.append(self._server_status_box)

        # Collapsible launch panel — shown while a start/stop operation is in progress.
        # Contains a pulsing progress bar + phase label, with an optional raw log detail
        # view that the user can expand via the "▸ Log" toggle button.
        self._srv_log_revealer = Gtk.Revealer()
        self._srv_log_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._srv_log_revealer.set_transition_duration(150)
        self._srv_pulse_timer: int = 0   # GLib source id; 0 when not running
        self._srv_log_detail_open: bool = False

        srv_launch_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        srv_launch_box.add_css_class("server-launch-box")

        # Row 1: pulsing progress bar + "▸ Log" toggle button
        prog_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._srv_progress_bar = Gtk.ProgressBar()
        self._srv_progress_bar.set_hexpand(True)
        self._srv_progress_bar.set_pulse_step(0.07)
        self._srv_progress_bar.add_css_class("server-progress")
        prog_row.append(self._srv_progress_bar)
        self._srv_log_toggle = Gtk.Button(label="▸ Log")
        self._srv_log_toggle.add_css_class("server-log-toggle")
        self._srv_log_toggle.connect("clicked", self._on_srv_log_toggle)
        prog_row.append(self._srv_log_toggle)
        srv_launch_box.append(prog_row)

        # Row 2: phase label ("Docker starting…", "Loading model weights…", etc.)
        self._srv_phase_lbl = Gtk.Label(label="Starting…")
        self._srv_phase_lbl.set_xalign(0)
        self._srv_phase_lbl.add_css_class("server-phase-lbl")
        srv_launch_box.append(self._srv_phase_lbl)

        # Row 3: raw log text — hidden by default, toggled by the button above
        self._srv_log_detail_revealer = Gtk.Revealer()
        self._srv_log_detail_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._srv_log_detail_revealer.set_transition_duration(150)
        self._srv_log_detail_revealer.set_reveal_child(False)
        self._srv_log_buf = Gtk.TextBuffer()
        srv_log_view = Gtk.TextView.new_with_buffer(self._srv_log_buf)
        srv_log_view.set_editable(False)
        srv_log_view.set_cursor_visible(False)
        srv_log_view.set_wrap_mode(Gtk.WrapMode.CHAR)
        srv_log_view.add_css_class("server-log")
        srv_log_scroll = Gtk.ScrolledWindow()
        srv_log_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        srv_log_scroll.set_size_request(-1, 80)
        srv_log_scroll.set_child(srv_log_view)
        self._srv_log_scroll = srv_log_scroll
        self._srv_log_detail_revealer.set_child(srv_log_scroll)
        srv_launch_box.append(self._srv_log_detail_revealer)

        self._srv_log_revealer.set_child(srv_launch_box)
        self._footer_box.append(self._srv_log_revealer)

        # ── Buttons ────────────────────────────────────────────────────────────
        # Single action button: "Generate" when idle, "+ Add to Queue" when busy.
        self._gen_btn = Gtk.Button(label="Generate")
        self._gen_btn.add_css_class("generate-btn")
        self._gen_btn.set_margin_top(6)
        self._gen_btn.set_sensitive(False)
        self._gen_btn.connect("clicked", self._on_action_clicked)
        self._footer_box.append(self._gen_btn)

        self._cancel_btn = Gtk.Button(label="✕ Cancel")
        self._cancel_btn.add_css_class("cancel-btn")
        self._cancel_btn.set_visible(False)
        self._cancel_btn.connect("clicked", lambda _: self._on_cancel())
        self._footer_box.append(self._cancel_btn)

        # Recover Jobs moved to File menu — no button here.

    @property
    def footer_box(self) -> Gtk.Box:
        """Pinned footer — MainWindow places this below ctrl_scroll."""
        return self._footer_box

    @property
    def toolbar_box(self) -> Gtk.Box:
        """Toolbar strip (logo, source toggle, model selector) built in _build().
        MainWindow pins this at the top of the window above the paned layout."""
        return self._toolbar_box

    # ── State ──────────────────────────────────────────────────────────────────

    # ── Advanced settings accordion ────────────────────────────────────────────

    def _on_adv_toggle(self, _btn) -> None:
        """Toggle the advanced settings accordion open/closed."""
        self._adv_open = not self._adv_open
        self._adv_revealer.set_reveal_child(self._adv_open)
        self._adv_arrow_lbl.set_label("\u25be" if self._adv_open else "\u25b8")

    def _update_adv_summary(self) -> None:
        """
        Rebuild the accordion header summary labels.
        Shows current steps and seed values; highlights non-defaults in pink.
        Called when steps or seed spinbuttons change, and once at build time.
        """
        steps_val = int(self._steps_spin.get_value())
        seed_val = int(self._seed_spin.get_value())
        steps_default = (steps_val == 20)
        seed_default = (seed_val == -1)

        # Clear existing summary labels
        child = self._adv_summary_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._adv_summary_box.remove(child)
            child = nxt

        # Rebuild with one label per value, styled by default/changed state
        for text, is_default in [
            (f"steps:{steps_val}", steps_default),
            (f"seed:{seed_val if seed_val != -1 else chr(8722) + '1'}", seed_default),
        ]:
            lbl = Gtk.Label(label=text)
            lbl.add_css_class("adv-summary" if is_default else "adv-summary-changed")
            self._adv_summary_box.append(lbl)

    # ── Source toggle ──────────────────────────────────────────────────────────

    def _set_source(self, source: str) -> None:
        """Switch between 'video' (Wan2.2), 'animate' (Animate-14B), and 'image' (FLUX)."""
        if source == self._model_source:
            return
        self._model_source = source
        is_image = source == "image"
        is_animate = source == "animate"
        is_video = source == "video"

        # Active state is handled automatically by the ToggleButton group (:checked CSS).
        if is_image:
            self._title_lbl.set_label("TT IMAGE GENERATOR")
            self._source_desc_lbl.set_label(
                "synchronous  ·  FLUX.1-dev  ·  ~15–90 s  ·  1024×1024 JPEG"
            )
        elif is_animate:
            self._title_lbl.set_label("TT ANIMATE GENERATOR")
            self._source_desc_lbl.set_label(
                "async job  ·  Animate-14B  ·  motion video + character"
            )
        else:
            self._title_lbl.set_label("TT VIDEO GENERATOR")
            if self._video_model == "mochi":
                self._source_desc_lbl.set_label(
                    "async job  ·  Mochi-1  ·  ~5–15 min  ·  480×848 168-frame"
                )
            else:
                self._source_desc_lbl.set_label(
                    "async job  ·  Wan2.2-T2V  ·  ~3–10 min  ·  720p MP4"
                )

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

        # Swap chips: each source tab has its own curated chip vocabulary
        if is_image:
            chip_source = "image"
        elif is_animate:
            chip_source = "animate"
        else:
            chip_source = "video"
        self._chips_scroll.set_child(self._make_chips_box(chip_source))

        # Seed image: only relevant for video (Wan2.2 init image)
        self._seed_img_section.set_visible(is_video)
        self._seed_row_widget.set_visible(is_video)

        # Animate inputs: visible only in animate mode
        self._animate_box.set_visible(is_animate)

        # Model selector rows: video selector shown for video source,
        # image selector shown for image source, neither for animate.
        self._model_sel_row.set_visible(is_video)
        self._img_model_sel_row.set_visible(is_image)

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

        # Re-evaluate match/mismatch for the newly selected tab.
        if self._running_model is not None or self._server_ready:
            self.set_server_state(self._server_ready, self._running_model)

        # Notify the main window so it can switch the gallery stack to show
        # only the cards that match the newly selected generation mode.
        self._on_source_change(source)

    def _set_model(self, model: str) -> None:
        """
        Switch the active model within the current source category.
        Updates button visual state, description label, and Start button tooltip.
        Guard against being called before _build() has finished constructing all widgets.
        """
        # _source_desc_lbl and _server_start_btn are constructed after the model
        # selector buttons; set_active(True) on those buttons fires this callback
        # mid-_build() before those widgets exist. Skip silently in that case.
        if not hasattr(self, "_source_desc_lbl"):
            return
        if self._model_source == "video":
            self._video_model = model
            # Active state handled by ToggleButton group (:checked CSS); no manual CSS needed.
            if model == "mochi":
                self._source_desc_lbl.set_label(
                    "async job  ·  Mochi-1  ·  ~5–15 min  ·  480×848 168-frame"
                )
                self._server_start_btn.set_tooltip_text(
                    "Start the Mochi-1 inference server.\n"
                    "Video (Mochi-1) → start_mochi.sh"
                )
            else:
                self._source_desc_lbl.set_label(
                    "async job  ·  Wan2.2-T2V  ·  ~3–10 min  ·  720p MP4"
                )
                self._server_start_btn.set_tooltip_text(
                    "Start the inference server using the local launch script.\n"
                    "Video (Wan2.2) → start_wan.sh  ·  Image → start_flux.sh"
                )
        elif self._model_source == "image":
            self._image_model = model

    def get_model_source(self) -> str:
        return self._model_source

    def get_video_model(self) -> str:
        """Return the currently selected video model key ('wan2' or 'mochi')."""
        return self._video_model

    def get_image_model(self) -> str:
        """Return the currently selected image model key ('flux' or future)."""
        return self._image_model

    def set_server_state(self, ready: bool, running_model: "str | None") -> None:
        """
        Update all server-related UI from a health check result.

        ready         — True if /tt-liveness returned 200
        running_model — model ID string from /v1/models, or None if unknown/offline
        """
        self._running_model = running_model
        self._server_ready = False  # recalculated below

        if self._server_launching and not ready:
            # Still launching and health check is returning 500 — don't flash
            # the indicator to "offline" while the start script is in progress.
            return

        if not ready:
            # Offline
            self._apply_server_row_style("offline")
            self._server_model_lbl.set_label("No server")
            self._server_sub_lbl.set_label("localhost:8000 unreachable")
            self._server_start_btn.set_sensitive(True)
            self._server_stop_btn.set_sensitive(False)
            self._server_switch_btn.set_visible(False)
        else:
            # Server is up — determine match/mismatch
            source_for_model = (
                _MODEL_TO_SOURCE.get(running_model) if running_model else None
            )
            current_source = self._model_source
            mismatch = (
                source_for_model is not None
                and source_for_model != current_source
            )
            display = (
                _MODEL_DISPLAY_SERVER.get(running_model, "Server online")
                if running_model
                else "Server online"
            )

            if mismatch:
                self._apply_server_row_style("mismatch")
                self._server_model_lbl.set_label(display)
                self._server_sub_lbl.set_label(
                    f"{current_source.capitalize()} tab needs a different server"
                )
                self._server_ready = False
                self._server_switch_btn.set_visible(True)
                self._server_start_btn.set_sensitive(False)
                self._server_stop_btn.set_sensitive(True)
            else:
                self._apply_server_row_style("match")
                self._server_model_lbl.set_label(display)
                self._server_sub_lbl.set_label("localhost:8000")
                self._server_ready = True
                self._server_switch_btn.set_visible(False)
                self._server_start_btn.set_sensitive(False)
                self._server_stop_btn.set_sensitive(True)
                # Collapse startup log once server confirmed ready
                if self._server_launching:
                    self.set_server_launching(False)
                # Sync the video model toggle to match what's actually running.
                # e.g. when Mochi is running, select the Mochi-1 button.
                video_key = _MODEL_TO_VIDEO_KEY.get(running_model) if running_model else None
                if video_key and self._video_model != video_key:
                    if video_key == "mochi":
                        self._mdl_mochi_btn.set_active(True)
                    else:
                        self._mdl_wan2_btn.set_active(True)

        self._update_btns()

    # ── Server control helpers ─────────────────────────────────────────────────

    def set_server_launching(self, launching: bool, clear_log: bool = False) -> None:
        """Show or hide the startup progress panel and lock Start/Stop during the operation."""
        self._server_launching = launching
        self._srv_log_revealer.set_reveal_child(launching)
        if clear_log:
            self._srv_log_buf.set_text("")
            self._srv_phase_lbl.set_label("Starting…")
            self._srv_progress_bar.set_fraction(0.0)
        # While an operation is in progress, disable both buttons to prevent overlap.
        self._server_start_btn.set_sensitive(not launching)
        self._server_stop_btn.set_sensitive(not launching)
        # Manage the pulse animation timer.
        if launching and not self._srv_pulse_timer:
            self._srv_pulse_timer = GLib.timeout_add(200, self._srv_pulse_tick)
        elif not launching and self._srv_pulse_timer:
            GLib.source_remove(self._srv_pulse_timer)
            self._srv_pulse_timer = 0

    def _srv_pulse_tick(self) -> bool:
        """GLib timer callback: advance the indeterminate progress bar animation."""
        if not self._server_launching:
            self._srv_pulse_timer = 0
            return GLib.SOURCE_REMOVE
        self._srv_progress_bar.pulse()
        return GLib.SOURCE_CONTINUE

    def _on_srv_log_toggle(self, _btn) -> None:
        """Toggle raw log detail panel open/closed."""
        self._srv_log_detail_open = not self._srv_log_detail_open
        self._srv_log_detail_revealer.set_reveal_child(self._srv_log_detail_open)
        self._srv_log_toggle.set_label("▾ Log" if self._srv_log_detail_open else "▸ Log")

    def _apply_server_row_style(self, state: str) -> None:
        """
        Switch server row and dot/model labels to the given state style.
        state is one of: 'offline', 'match', 'mismatch', 'starting'.
        Removes all server-row-* and server-model-* classes before adding the new one.
        """
        for cls in ("server-row-offline", "server-row-match",
                    "server-row-mismatch", "server-row-starting"):
            self._server_status_box.remove_css_class(cls)
        self._server_status_box.add_css_class(f"server-row-{state}")

        for cls in ("server-model-offline", "server-model-match",
                    "server-model-mismatch", "server-model-starting"):
            self._server_dot_lbl.remove_css_class(cls)
            self._server_model_lbl.remove_css_class(cls)
        self._server_dot_lbl.add_css_class(f"server-model-{state}")
        self._server_model_lbl.add_css_class(f"server-model-{state}")

    def append_server_log(self, line: str) -> None:
        """Append one line to the server startup log. Must be called on the main thread.

        Also scans for known milestone strings to update the phase label above the
        progress bar, giving the user a human-readable summary of where the startup is.
        """
        end = self._srv_log_buf.get_end_iter()
        self._srv_log_buf.insert(end, line + "\n")
        # Auto-scroll the log to the bottom so the latest output is always visible.
        adj = self._srv_log_scroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        # Update the phase label when we hit known startup milestones.
        # NOTE: "Application startup complete" means uvicorn is up but the model
        # workers haven't started yet — /tt-liveness still returns 500 for several
        # more minutes. The real readiness signal is "All devices are warmed up".
        if "Workflow PID" in line:
            self._srv_phase_lbl.set_label("Docker container starting…")
        elif "Log file:" in line or "Server started in Docker" in line:
            self._srv_phase_lbl.set_label("Container up · loading model…")
        elif "─── tailing" in line:
            self._srv_phase_lbl.set_label("Loading model weights…")
        elif "Application startup complete" in line:
            self._srv_phase_lbl.set_label("Starting model workers…")
        elif "All devices are warmed up" in line:
            self._srv_phase_lbl.set_label("Server ready!")

    def _on_start_server_clicked(self, _btn) -> None:
        self._on_start_server(self._model_source)

    def _on_stop_server_clicked(self, _btn) -> None:
        self._on_stop_server()

    def _on_switch_to_running_model_tab(self, _btn) -> None:
        """Switch the source selector to the tab that matches the running model."""
        source = _MODEL_TO_SOURCE.get(self._running_model) if self._running_model else None
        if source:
            self.switch_to_source(source)

    def switch_to_source(self, source: str) -> None:
        """
        Programmatically activate a source tab.
        Fires _set_source() via the existing toggled signal handler.
        """
        if source == "video":
            self._src_video_btn.set_active(True)
        elif source == "animate":
            self._src_animate_btn.set_active(True)
        elif source == "image":
            self._src_image_btn.set_active(True)

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
        pass  # recover button removed — sensitivity managed via win.recover-jobs action

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
        # Active state handled by ToggleButton group (:checked CSS); no manual CSS needed.
        self._animate_mode = mode

    # ── Chips helper ───────────────────────────────────────────────────────────

    def _make_chips_box(self, source: str) -> Gtk.Box:
        """Build a vertically grouped chip box for *source* ('video'/'image'/'animate')."""
        categories = {
            "video":   _VIDEO_CHIPS,
            "image":   _IMAGE_CHIPS,
            "animate": _ANIMATE_CHIPS,
        }.get(source, [])

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_margin_start(2)
        outer.set_margin_end(2)
        outer.set_margin_top(2)
        outer.set_margin_bottom(2)

        for cat in categories:
            lbl = Gtk.Label(label=cat.name)
            lbl.set_xalign(0)
            lbl.add_css_class("chips-category-lbl")
            outer.append(lbl)

            flow = Gtk.FlowBox()
            flow.set_selection_mode(Gtk.SelectionMode.NONE)
            flow.set_row_spacing(3)
            flow.set_column_spacing(4)
            for chip in cat.chips:
                btn = Gtk.Button(label=chip.label)
                btn.set_tooltip_text(chip.tip)
                btn.add_css_class("chip-btn")
                btn.connect("clicked", lambda _b, t=chip.text: self._append_to_prompt(t))
                flow.append(btn)
            outer.append(flow)

        return outer

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

    # ── Prompt validation ──────────────────────────────────────────────────────

    def _on_prompt_changed(self, buf: Gtk.TextBuffer) -> None:
        """Clear the empty-prompt error state as soon as the user types anything."""
        if self._prompt_scroll.has_css_class("prompt-error"):
            self._prompt_scroll.remove_css_class("prompt-error")
            self._prompt_error_lbl.set_visible(False)

    def set_prompt_gen_state(self, ready: bool) -> None:
        """
        Update the inspire row dot and button sensitivity from the health poll result.

        Called from the main thread via GLib.idle_add.  Handles the auto-generate
        flow: if the server just became ready after the user clicked "▶ Start" in
        the confirm box, fires the pending generation automatically.
        """
        was_starting = self._prompt_gen_starting
        self._prompt_gen_ready = ready

        if ready:
            self._prompt_gen_starting = False
            # Update dot to green "ready"
            self._inspire_dot_lbl.set_label("⬤ ready")
            for cls in ("inspire-dot", "inspire-dot-starting"):
                self._inspire_dot_lbl.remove_css_class(cls)
            self._inspire_dot_lbl.add_css_class("inspire-dot-ready")
            # Restore button if not mid-generation and confirm box is not open
            if not self._prompt_gen_generating and not self._confirm_box_visible:
                self._inspire_btn.set_label("✨ Inspire me")
                self._inspire_btn.remove_css_class("inspire-btn-loading")
                self._inspire_btn.add_css_class("inspire-btn")
                self._inspire_btn.set_sensitive(True)
            # Auto-generate if pending from the confirm-start flow
            if was_starting and self._inspire_pending_source is not None:
                source = self._inspire_pending_source
                seed = self._inspire_pending_seed
                self._inspire_pending_source = None
                self._inspire_pending_seed = ""
                self._trigger_inspire(source, seed)
        elif not self._prompt_gen_starting:
            # Server is offline and not actively starting — algo-only mode
            self._inspire_dot_lbl.set_label("⬤ algo only")
            for cls in ("inspire-dot-ready", "inspire-dot-starting"):
                self._inspire_dot_lbl.remove_css_class(cls)
            self._inspire_dot_lbl.add_css_class("inspire-dot")
            if not self._prompt_gen_generating and not self._confirm_box_visible:
                self._inspire_btn.set_label("✨ Inspire me")
                self._inspire_btn.remove_css_class("inspire-btn-loading")
                self._inspire_btn.add_css_class("inspire-btn")
                self._inspire_btn.set_sensitive(True)

    def set_prompt_gen_starting(self, starting: bool) -> None:
        """Show/hide the starting… state on the inspire row button and dot."""
        self._prompt_gen_starting = starting
        if starting:
            self._inspire_dot_lbl.set_label("⬤ starting…")
            for cls in ("inspire-dot", "inspire-dot-ready"):
                self._inspire_dot_lbl.remove_css_class(cls)
            self._inspire_dot_lbl.add_css_class("inspire-dot-starting")
            self._inspire_btn.set_label("⏳ Starting…")
            self._inspire_btn.remove_css_class("inspire-btn")
            self._inspire_btn.add_css_class("inspire-btn-loading")
            self._inspire_btn.set_sensitive(False)

    def _on_inspire_clicked(self, _btn) -> None:
        """Handle Inspire button click.

        Always generates — algo/markov works without Qwen.  The confirm box
        (▶ Start / Not now) is only shown when the user explicitly wants to
        start the Qwen server before generating.
        """
        source = self._model_source
        seed_text = self._prompt_buf.get_text(
            self._prompt_buf.get_start_iter(),
            self._prompt_buf.get_end_iter(),
            False,
        ).strip()
        self._trigger_inspire(source, seed_text)

    def _on_inspire_confirm_start(self, _btn) -> None:
        """User clicked ▶ Start in the confirm box — launch server and set auto-generate."""
        self._inspire_start_btn.set_sensitive(False)
        self._inspire_confirm_revealer.set_reveal_child(False)
        self._confirm_box_visible = False
        # Capture source + seed at click time so auto-generate uses the right values
        self._inspire_pending_source = self._model_source
        self._inspire_pending_seed = self._prompt_buf.get_text(
            self._prompt_buf.get_start_iter(),
            self._prompt_buf.get_end_iter(),
            False,
        ).strip()
        self.set_prompt_gen_starting(True)
        self._on_start_prompt_gen()

    def _on_inspire_confirm_cancel(self, _btn) -> None:
        """User clicked Not now — dismiss confirm box, restore button."""
        self._inspire_confirm_revealer.set_reveal_child(False)
        self._confirm_box_visible = False
        self._inspire_btn.set_sensitive(True)

    def _trigger_inspire(self, source: str, seed_text: str) -> None:
        """Set loading state and call on_inspire(source, seed_text) to fire the thread."""
        self._prompt_gen_generating = True
        self._inspire_btn.set_label("⏳ Generating…")
        self._inspire_btn.remove_css_class("inspire-btn")
        self._inspire_btn.add_css_class("inspire-btn-loading")
        self._inspire_btn.set_sensitive(False)
        self._on_inspire(source, seed_text)

    def set_inspire_result(self, text: str) -> None:
        """Called on main thread when generation succeeds — replace textarea content."""
        self._prompt_gen_generating = False
        self._prompt_buf.set_text(text)
        self._inspire_btn.set_label("✨ Inspire me")
        self._inspire_btn.remove_css_class("inspire-btn-loading")
        self._inspire_btn.add_css_class("inspire-btn")
        self._inspire_btn.set_sensitive(True)

    def set_inspire_error(self, msg: str) -> None:
        """Called on main thread when generation fails — restore button state."""
        self._prompt_gen_generating = False
        self._inspire_btn.set_label("✨ Inspire me")
        self._inspire_btn.remove_css_class("inspire-btn-loading")
        self._inspire_btn.add_css_class("inspire-btn")
        self._inspire_btn.set_sensitive(True)

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
                self._prompt_scroll.add_css_class("prompt-error")
                self._prompt_error_lbl.set_visible(True)
                return
        # Determine the specific model within the active category
        if self._model_source == "video":
            current_model_id = self._video_model
        elif self._model_source == "image":
            current_model_id = self._image_model
        else:
            current_model_id = ""

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
            current_model_id,
        )
        # Clear the prompt fields so the user can type the next one immediately.
        # This happens only on explicit user click, never on auto-queue or attractor paths.
        self.clear_prompt()
        if self._busy:
            self._on_enqueue(*args)
        else:
            self._on_generate(*args)

    # ── Queue display ──────────────────────────────────────────────────────────



# ── Recovery dialog ────────────────────────────────────────────────────────────

_RECOVERY_DISMISS = Gtk.ResponseType.REJECT   # reuse a built-in int constant for our "Ignore" button


class RecoveryDialog(Gtk.Dialog):
    """Modal dialog listing unknown server jobs; user selects which to recover.

    Buttons:
      Cancel        — close without recovering or dismissing anything.
      🚫 Ignore     — permanently hide the checked jobs from future scans.
      ✓ Recover     — recover the checked jobs (default action).

    After the dialog emits a response, inspect:
      .selected_jobs  — jobs to recover  (populated on OK / Recover)
      .dismissed_jobs — jobs to ignore forever (populated on _RECOVERY_DISMISS / Ignore)
    """

    def __init__(self, parent, jobs: list):
        super().__init__(title="Recover Server Jobs", transient_for=parent, modal=True)
        self.set_default_size(520, -1)
        self.selected_jobs: list = []
        self.dismissed_jobs: list = []
        self._checkboxes: list = []
        self._jobs = jobs

        self.add_button("Cancel",       Gtk.ResponseType.CANCEL)
        self.add_button("🚫 Ignore",    _RECOVERY_DISMISS)
        self.add_button("✓ Recover",    Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)

        content = self.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        header = Gtk.Label(
            label=f"Found <b>{len(jobs)}</b> server job(s) not in local history.\n"
                  "Check jobs to recover, then click <b>✓ Recover</b>.\n"
                  "To hide a job from future scans, check it and click <b>🚫 Ignore</b>.",
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
        checked = [cb.job for cb in self._checkboxes if cb.get_active()]
        if response == Gtk.ResponseType.OK:
            self.selected_jobs = checked
        elif response == _RECOVERY_DISMISS:
            self.dismissed_jobs = checked


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


# ── Hardware status bar ────────────────────────────────────────────────────────

class _StatusBar(Gtk.Box):
    """Slim status strip pinned to the bottom of the window.

    Shows four segments separated by `│` dividers:
      ⬤ <model>  │  queue: N  │  NN GB free  │  NN°C  NNW  NNMHz

    The chip telemetry segment is populated by polling `tt-smi -s` every 10 s
    on a background thread.  All public update methods must be called on the
    main (GTK) thread.
    """

    _DISK_WARN_BYTES = 18 * 1024 ** 3   # match _DISK_SPACE_MIN_BYTES

    def __init__(self, start_cb, stop_cb) -> None:
        """
        Args:
            start_cb: callable() — invoked when the user clicks Start in the server popover.
                      The caller is responsible for determining the current model source.
            stop_cb:  callable() — invoked when the user clicks Stop in the server popover.
        """
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.add_css_class("tt-statusbar")

        def _sep() -> Gtk.Label:
            lbl = Gtk.Label(label=" │ ")
            lbl.add_css_class("tt-statusbar-sep")
            return lbl

        # ── Server segment: MenuButton (dot + model) → popover with controls ──
        # Clicking the server segment opens a slim popover with Start / Stop.
        self._srv_dot = Gtk.Label(label="⬤")
        self._srv_dot.add_css_class("tt-statusbar-dot")
        self._srv_dot.add_css_class("tt-statusbar-dot-offline")
        self._srv_lbl = Gtk.Label(label="offline")
        self._srv_lbl.add_css_class("tt-statusbar-seg")

        srv_btn_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        srv_btn_content.append(self._srv_dot)
        srv_btn_content.append(self._srv_lbl)

        self._srv_menu_btn = Gtk.MenuButton()
        self._srv_menu_btn.set_has_frame(False)
        self._srv_menu_btn.add_css_class("tt-statusbar-srv-btn")
        self._srv_menu_btn.set_child(srv_btn_content)

        # Build the server-control popover
        self._pop_status_lbl = Gtk.Label(label="Server offline")
        self._pop_status_lbl.set_xalign(0)
        self._pop_status_lbl.add_css_class("tt-statusbar-seg")

        self._pop_start = Gtk.Button(label="▶  Start server")
        self._pop_start.add_css_class("generate-btn")
        self._pop_stop  = Gtk.Button(label="■  Stop server")
        self._pop_stop.add_css_class("cancel-btn")
        pop_start = self._pop_start
        pop_stop  = self._pop_stop

        _popover = Gtk.Popover()
        _popover.set_position(Gtk.PositionType.TOP)

        def _start_and_close(_btn):
            _popover.popdown()
            start_cb()

        def _stop_and_close(_btn):
            _popover.popdown()
            stop_cb()

        pop_start.connect("clicked", _start_and_close)
        pop_stop.connect("clicked", _stop_and_close)

        pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        pop_box.set_margin_top(10)
        pop_box.set_margin_bottom(10)
        pop_box.set_margin_start(14)
        pop_box.set_margin_end(14)
        pop_box.append(self._pop_status_lbl)
        pop_box.append(pop_start)
        pop_box.append(pop_stop)
        _popover.set_child(pop_box)
        self._srv_menu_btn.set_popover(_popover)

        self.append(self._srv_menu_btn)

        # ── Queue depth (hidden when empty) ───────────────────────────────────
        self._q_sep = _sep()
        self._q_sep.set_visible(False)
        self.append(self._q_sep)
        self._queue_lbl = Gtk.Label(label="")
        self._queue_lbl.add_css_class("tt-statusbar-seg")
        self._queue_lbl.set_visible(False)
        self.append(self._queue_lbl)

        # ── Disk free ─────────────────────────────────────────────────────────
        self.append(_sep())
        self._disk_lbl = Gtk.Label(label="")
        self._disk_lbl.add_css_class("tt-statusbar-seg")
        self.append(self._disk_lbl)

        # ── Chip telemetry (tt-smi) ───────────────────────────────────────────
        self._chip_sep = _sep()
        self._chip_sep.set_visible(False)
        self.append(self._chip_sep)
        self._chip_lbl = Gtk.Label(label="")
        self._chip_lbl.add_css_class("tt-statusbar-seg")
        self._chip_lbl.set_visible(False)
        self.append(self._chip_lbl)

        # Kick off background polling; populate disk + chip labels immediately.
        self._stop = threading.Event()
        self._last_chip_text: str = ""   # retain last good reading across failed polls
        GLib.idle_add(self._refresh_disk)
        threading.Thread(target=self._poll_loop, daemon=True).start()

    # ── Public update methods (main-thread only) ───────────────────────────────

    def _set_srv_dot(self, css_state: str, model_text: str, pop_text: str) -> None:
        for cls in ("tt-statusbar-dot-ready", "tt-statusbar-dot-offline",
                    "tt-statusbar-dot-starting"):
            self._srv_dot.remove_css_class(cls)
        self._srv_dot.add_css_class(f"tt-statusbar-dot-{css_state}")
        self._srv_lbl.set_label(model_text)
        self._pop_status_lbl.set_label(pop_text)

    def update_server(self, ready: bool, model: "str | None") -> None:
        """Reflect server health in the status dot and model label."""
        if ready:
            self._set_srv_dot("ready", model or "ready", f"● {model or 'Server'} ready")
        else:
            self._set_srv_dot("offline", "offline", "Server offline")
        # Re-enable popover controls once the launch/stop operation has settled.
        self._pop_start.set_sensitive(True)
        self._pop_stop.set_sensitive(True)

    def update_starting(self) -> None:
        """Show 'starting' state while the server launch script is running."""
        self._set_srv_dot("starting", "starting…", "Server starting…")
        # Disable popover buttons while the script is in flight — prevents
        # double-starting or stopping a server that is mid-launch.
        self._pop_start.set_sensitive(False)
        self._pop_stop.set_sensitive(False)

    def update_queue(self, depth: int) -> None:
        """Show or hide the queue-depth segment."""
        visible = depth > 0
        self._queue_lbl.set_label(f"queue: {depth}" if visible else "")
        self._queue_lbl.set_visible(visible)
        self._q_sep.set_visible(visible)

    # ── Disk / chip helpers (main-thread callbacks) ────────────────────────────

    def _refresh_disk(self) -> bool:
        """Update the disk-free label. Called on main thread."""
        try:
            from history_store import STORAGE_DIR
            free = shutil.disk_usage(STORAGE_DIR).free
            free_gb = free / (1024 ** 3)
            self._disk_lbl.set_label(f"{free_gb:.0f} GB free")
            if free < self._DISK_WARN_BYTES:
                self._disk_lbl.remove_css_class("tt-statusbar-seg")
                self._disk_lbl.add_css_class("tt-statusbar-seg-warn")
            else:
                self._disk_lbl.remove_css_class("tt-statusbar-seg-warn")
                self._disk_lbl.add_css_class("tt-statusbar-seg")
        except OSError:
            self._disk_lbl.set_label("")
        return GLib.SOURCE_REMOVE

    def _apply_chip(self, text: str) -> bool:
        """Apply chip telemetry string to the label. Called on main thread.

        If text is empty (poll failed / tt-smi unavailable), we fall back to
        the last successful reading so the segment stays visible during
        transient failures (e.g. tt-smi timeout while a generation is running).
        The segment is only hidden when we have never successfully read chip
        data at all.
        """
        if text:
            self._last_chip_text = text
        display = self._last_chip_text  # keep last good value on failure
        visible = bool(display)
        self._chip_lbl.set_label(display)
        self._chip_lbl.set_visible(visible)
        self._chip_sep.set_visible(visible)
        return GLib.SOURCE_REMOVE

    # ── Background polling loop ────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Background thread: refresh disk + chip telemetry every 10 s.

        An initial chip read is done immediately (no leading wait) so the
        segment is populated as soon as the window appears rather than 10 s
        after startup.
        """
        # Initial read — no wait so the chip segment populates right away.
        chip_text = self._read_chip_telemetry()
        GLib.idle_add(self._apply_chip, chip_text)
        while not self._stop.wait(10.0):
            GLib.idle_add(self._refresh_disk)
            chip_text = self._read_chip_telemetry()
            GLib.idle_add(self._apply_chip, chip_text)

    # ── Chip telemetry: sysfs baseline + optional tt-smi enhancement ──────────
    #
    # Primary source (always): /sys/class/tenstorrent/tenstorrent!N/tt_aiclk
    #   Passive kernel sysfs read — no subprocess, no PATH issues, instant.
    #
    # Enhancement layer (when tt-smi >= 4.1 is reachable): tt-smi -s snapshot
    #   Adds ASIC temperature and total board power.
    #   tt-smi lives in a virtualenv not on the default PATH, so we search
    #   known locations once and cache the resolved path at class level.
    #   Version is checked once; if < 4.1 or not found, the class-level flag
    #   is set to _TT_SMI_SKIP so no further subprocess calls are made.

    _tt_smi_path: "str | None" = None   # None = not yet resolved
    _TT_SMI_SKIP = ""                   # sentinel stored in _tt_smi_path when unavailable

    # Known locations to search for tt-smi beyond the inherited PATH.
    _TT_SMI_SEARCH_PATHS = [
        str(Path.home() / ".tenstorrent-venv" / "bin"),
        "/usr/local/bin",
        "/usr/bin",
    ]

    @classmethod
    def _resolve_tt_smi(cls) -> "str | None":
        """Return the absolute path to a usable tt-smi (>= 4.1), or None.

        Result is cached at class level so the version check only ever runs once.
        """
        import re, shutil as sh
        if cls._tt_smi_path is not None:
            return cls._tt_smi_path or None   # "" sentinel → None

        extended = (os.environ.get("PATH", "") + os.pathsep
                    + os.pathsep.join(cls._TT_SMI_SEARCH_PATHS))
        found = sh.which("tt-smi", path=extended)
        if not found:
            cls._tt_smi_path = cls._TT_SMI_SKIP
            return None

        try:
            r = subprocess.run(
                [found, "--version"],
                capture_output=True, text=True, timeout=5,
                stdin=subprocess.DEVNULL,
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
    def _read_sysfs_clocks() -> list[int]:
        """Read AICLK (MHz) for each chip from sysfs. Never raises."""
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
    def _f(val) -> float:
        """Coerce tt-smi JSON values (may be int, float, or leading-space string) to float."""
        try:
            return float(val) if val is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    # Shade blocks ordered light → full: ░ ▒ ▓ █  (CP437 176–178, 219)
    _SHADE = ["░", "▒", "▓", "█"]

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

    def _read_chip_telemetry(self) -> str:
        """Return a compact chip summary string for the status bar.

        Format (example with 4 chips):
          61°C  196W  1350 MHz  █▓█▓

        Temp is the average across all chips.  The shade blocks show per-chip
        activity (aiclk relative to the highest clock in the group):
          ░ idle / very low   ▒ low-medium   ▓ medium-high   █ near peak

        Always reads clocks from sysfs (passive, no subprocess).
        Adds avg temperature and total power from tt-smi when tt-smi >= 4.1.
        Falls back gracefully at each layer.
        """
        parts: list[str] = []

        # ── Layer 1: sysfs clocks — shade blocks + peak clock ─────────────────
        clocks = self._read_sysfs_clocks()
        if clocks:
            max_clk = max(clocks)
            blocks = "".join(self._clock_to_shade(c, max_clk) for c in clocks)
            parts.append(f"{max_clk} MHz")
            # blocks go at the end so they don't crowd the numbers
            shade_str = blocks   # held separately, appended last
        else:
            shade_str = ""

        # ── Layer 2: tt-smi avg temp + total power (when available) ───────────
        tt_smi = self._resolve_tt_smi()
        if tt_smi:
            try:
                result = subprocess.run(
                    [tt_smi, "-s"],
                    capture_output=True, text=True, timeout=8,
                    stdin=subprocess.DEVNULL,
                )
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    chips = data.get("device_info", [])
                    if chips:
                        temps  = [self._f(c.get("telemetry", {}).get("asic_temperature"))
                                  for c in chips]
                        powers = [self._f(c.get("telemetry", {}).get("power"))
                                  for c in chips]
                        valid_temps = [t for t in temps if t > 0]
                        if valid_temps:
                            avg_t = sum(valid_temps) / len(valid_temps)
                            parts.insert(0, f"{avg_t:.0f}°C")
                        if any(p > 0 for p in powers):
                            idx = 1 if parts and "°C" in parts[0] else 0
                            parts.insert(idx, f"{sum(powers):.0f}W")
            except Exception:
                pass  # tt-smi failed this poll — show clock + blocks from sysfs

        if shade_str:
            parts.append(shade_str)

        return "  ".join(parts)

    def stop(self) -> None:
        """Signal the background polling thread to exit. Call from do_close_request."""
        self._stop.set()


# ── Preferences Dialog ─────────────────────────────────────────────────────────

class PreferencesDialog(Gtk.Window):
    """
    Application preferences dialog.

    Sections:
      • Generation — default steps quality preset, sleep-after-N counter
      • System     — screensaver inhibit during generation
      • Disk       — minimum free space (stop-generating threshold)
      • TT-TV      — image dwell time, video fallback timer
      • Prompt     — director style probability, pinned director

    All widgets write through to the _settings singleton on change.
    Pass main_window so the dialog can keep the steps spin and action states in sync.
    """

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(
            title="Preferences",
            default_width=420,
            default_height=560,
            resizable=False,
        )
        self._mw = main_window
        self.set_transient_for(main_window)
        self._build()

    def _section(self, title: str) -> Gtk.Label:
        lbl = Gtk.Label(label=title)
        lbl.set_xalign(0)
        lbl.add_css_class("prefs-section-title")
        lbl.set_margin_top(12)
        return lbl

    def _row(self, label_text: str, widget: Gtk.Widget, hint: str = "") -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("prefs-row")
        lbl = Gtk.Label(label=label_text)
        lbl.set_xalign(0)
        lbl.set_hexpand(True)
        row.append(lbl)
        if hint:
            widget.set_tooltip_text(hint)
        row.append(widget)
        return row

    def _build(self) -> None:
        # Scrollable outer container
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(16)
        box.set_margin_end(16)
        scroll.set_child(box)
        self.set_child(scroll)

        # ── Generation ────────────────────────────────────────────────────────
        box.append(self._section("Generation"))

        # Quality preset: radio buttons via Gtk.CheckButton.set_group()
        quality_lbl = Gtk.Label(label="Default quality:")
        quality_lbl.set_xalign(0)
        box.append(quality_lbl)

        q_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        q_row.set_margin_start(8)
        current_steps = int(_settings.get("quality_steps"))
        self._quality_btns: list[Gtk.CheckButton] = []
        first_qbtn = None
        for label, steps in [("Fast (10)", 10), ("Standard (30)", 30),
                              ("High Quality (40)", 40)]:
            btn = Gtk.CheckButton(label=label)
            btn.steps_value = steps
            if first_qbtn is None:
                first_qbtn = btn
            else:
                btn.set_group(first_qbtn)
            if steps == current_steps:
                btn.set_active(True)
            btn.connect("toggled", self._on_quality_toggled)
            q_row.append(btn)
            self._quality_btns.append(btn)
        box.append(q_row)

        # Sleep after N completions
        sleep_spin = Gtk.SpinButton()
        sleep_spin.set_adjustment(Gtk.Adjustment(
            value=_settings.get("sleep_after_n_gens"),
            lower=0, upper=500, step_increment=1, page_increment=10,
        ))
        sleep_spin.set_digits(0)
        sleep_spin.connect("value-changed", lambda w: _settings.set(
            "sleep_after_n_gens", int(w.get_value())
        ))
        box.append(self._row("Sleep after N completions:", sleep_spin,
                             "Suspend the machine after this many successful generations. "
                             "0 = never."))

        # ── System ────────────────────────────────────────────────────────────
        box.append(self._section("System"))

        inhibit_check = Gtk.CheckButton(label="Inhibit screensaver while generating")
        inhibit_check.set_active(bool(_settings.get("inhibit_screensaver")))
        inhibit_check.set_tooltip_text(
            "Calls org.freedesktop.ScreenSaver.Inhibit while a generation job is running, "
            "preventing the screen from locking mid-job."
        )
        inhibit_check.connect("toggled", lambda w: _settings.set(
            "inhibit_screensaver", w.get_active()
        ))
        box.append(inhibit_check)

        # ── Disk ──────────────────────────────────────────────────────────────
        box.append(self._section("Disk"))

        disk_spin = Gtk.SpinButton()
        disk_spin.set_adjustment(Gtk.Adjustment(
            value=_settings.get("max_disk_gb"),
            lower=0, upper=2000, step_increment=1, page_increment=10,
        ))
        disk_spin.set_digits(0)
        disk_spin.connect("value-changed", lambda w: _settings.set(
            "max_disk_gb", int(w.get_value())
        ))
        box.append(self._row(
            "Minimum free disk space (GB):", disk_spin,
            "Stop generating when less than this many GB remain free. "
            "0 = use default 18 GB floor."
        ))

        # ── TT-TV ─────────────────────────────────────────────────────────────
        self._tttv_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(self._section("TT-TV"))
        box.append(self._tttv_box)

        dwell_spin = Gtk.SpinButton()
        dwell_spin.set_adjustment(Gtk.Adjustment(
            value=_settings.get("tttv_image_dwell_s"),
            lower=1, upper=300, step_increment=1, page_increment=5,
        ))
        dwell_spin.set_digits(0)
        dwell_spin.connect("value-changed", lambda w: _settings.set(
            "tttv_image_dwell_s", int(w.get_value())
        ))
        self._tttv_box.append(self._row("Image dwell time (seconds):", dwell_spin,
                                        "How long each still image is shown before advancing."))

        fallback_spin = Gtk.SpinButton()
        fallback_spin.set_adjustment(Gtk.Adjustment(
            value=_settings.get("tttv_video_fallback_s"),
            lower=10, upper=600, step_increment=5, page_increment=30,
        ))
        fallback_spin.set_digits(0)
        fallback_spin.connect("value-changed", lambda w: _settings.set(
            "tttv_video_fallback_s", int(w.get_value())
        ))
        self._tttv_box.append(self._row(
            "Video fallback timer (seconds):", fallback_spin,
            "Force-advance after this many seconds if the video end signal never fires "
            "(e.g. corrupt file, GStreamer stall)."
        ))

        # ── Prompt Style ──────────────────────────────────────────────────────
        box.append(self._section("Prompt Style"))

        # Director style probability
        dir_lbl = Gtk.Label(label="Director style in video prompts:")
        dir_lbl.set_xalign(0)
        box.append(dir_lbl)

        dir_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dir_row.set_margin_start(8)
        current_prob = float(_settings.get("director_style_prob"))
        self._dir_prob_btns: list[Gtk.CheckButton] = []
        first_dbtn = None
        for label, prob in [("Never", 0.0), ("Sometimes (33%)", 0.33),
                             ("Often (66%)", 0.66), ("Always", 1.0)]:
            btn = Gtk.CheckButton(label=label)
            btn.prob_value = prob
            if first_dbtn is None:
                first_dbtn = btn
            else:
                btn.set_group(first_dbtn)
            if abs(prob - current_prob) < 0.01:
                btn.set_active(True)
            btn.connect("toggled", self._on_dir_prob_toggled)
            dir_row.append(btn)
            self._dir_prob_btns.append(btn)
        box.append(dir_row)

        # Pinned director dropdown
        director_model = Gtk.StringList()
        for display, _ in _DIRECTOR_PINS:
            director_model.append(display)
        director_drop = Gtk.DropDown(model=director_model)
        director_drop.set_size_request(180, -1)
        current_pin = _settings.get("director_pin") or ""
        current_label = _DIRECTOR_PIN_LABEL.get(current_pin, "Random")
        for i, (display, _) in enumerate(_DIRECTOR_PINS):
            if display == current_label:
                director_drop.set_selected(i)
                break
        director_drop.connect("notify::selected", self._on_director_pin_changed)
        box.append(self._row("Pinned director:", director_drop,
                             "Always use this director's style in video prompts. "
                             "'Random' samples from the full list based on the probability above."))
        self._director_drop = director_drop

    # ── Change handlers ────────────────────────────────────────────────────────

    def _on_quality_toggled(self, btn: Gtk.CheckButton) -> None:
        if not btn.get_active():
            return
        steps = btn.steps_value
        _settings.set("quality_steps", steps)
        self._mw._controls._steps_spin.set_value(steps)
        # Keep menu action state in sync
        action = self._mw.lookup_action("quality")
        if action:
            action.set_state(GLib.Variant("s", str(steps)))

    def _on_dir_prob_toggled(self, btn: Gtk.CheckButton) -> None:
        if not btn.get_active():
            return
        prob = btn.prob_value
        _settings.set("director_style_prob", prob)
        action = self._mw.lookup_action("director-prob")
        if action:
            action.set_state(GLib.Variant("s", str(int(prob * 100))))

    def _on_director_pin_changed(self, drop: Gtk.DropDown, _pspec) -> None:
        idx = drop.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION:
            return
        _, full_value = _DIRECTOR_PINS[idx]
        _settings.set("director_pin", full_value)
        action = self._mw.lookup_action("director-pin")
        if action:
            action.set_state(GLib.Variant("s", full_value))

    def scroll_to_tttv(self) -> None:
        """Scroll to the TT-TV section — used when opening from the TT-TV menu."""
        def _do_scroll():
            alloc = self._tttv_box.get_allocation()
            adj = self.get_child().get_vadjustment()
            adj.set_value(alloc.y)
            return False
        GLib.idle_add(_do_scroll)


# ── Main Window ────────────────────────────────────────────────────────────────

class MainWindow(Gtk.ApplicationWindow):
    """Top-level window: owns client, store, workers, and the prompt queue."""

    def __init__(self, app: Gtk.Application, server_url: str = "http://localhost:8000"):
        super().__init__(application=app, title="TT Video Generator")
        self.set_default_size(1400, 800)

        self._alive: bool = True   # set False in do_close_request; guards idle_add callbacks
        self._client = APIClient(server_url)
        self._store = HistoryStore()
        self._worker: Optional[threading.Thread] = None
        self._worker_gen: Optional[GenerationWorker] = None
        self._queue: list = []
        self._server_proc: Optional[subprocess.Popen] = None  # running start/stop script subprocess
        # Track which gallery owns the current pending card (set in _on_generate,
        # used in _on_finished/_on_error to update the right gallery).
        self._gen_gallery = None
        self._auto_tab_switched = False  # True after first model detection auto-switch
        self._pg_stop: "threading.Event | None" = None  # set when prompt gen poll starts
        self._log_tail_stop: "threading.Event | None" = None  # set to stop server log tail
        self._attractor_win: "attractor.AttractorWindow | None" = None
        self._prompt_gen_system_prompt: str = self._load_prompt_gen_system()
        # Settings-backed state
        self._gen_completed_count: int = 0          # incremented in _on_finished; triggers sleep
        self._screensaver_inhibit_cookie: "int | None" = None  # D-Bus inhibit cookie
        self._prefs_dialog: "PreferencesDialog | None" = None  # singleton instance

        self._build_ui()
        self._load_history()
        self._restore_queue()
        self._start_health_worker()
        self._start_prompt_gen_health_worker()

        # Apply persisted quality preference to the steps spin button
        saved_steps = int(_settings.get("quality_steps"))
        self._controls._steps_spin.set_value(saved_steps)

    def _build_ui(self) -> None:
        # Apply CSS to the display now that we have a window
        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Root vertical box: toolbar (top) | menu bar | paned layout | status bar (bottom)
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(root_box)

        self._controls = ControlPanel(
            on_generate=self._on_generate,
            on_enqueue=self._on_enqueue,
            on_cancel=self._on_cancel,
            on_start_server=self._on_start_server,
            on_stop_server=self._on_stop_server,
            on_source_change=self._on_source_change,
            on_start_prompt_gen=self._on_start_prompt_gen,
            on_inspire=self._on_inspire,
        )

        # ── Main toolbar ──────────────────────────────────────────────────────
        # The toolbar strip is built inside ControlPanel (logo, source toggle,
        # model selectors).  We append it at the top of root_box and add the
        # Watch TT-TV button on the right side (after the internal spacer).
        # Build and register menu actions before creating the bar
        self._build_menu_actions()

        main_toolbar = self._controls.toolbar_box
        self._attractor_btn = Gtk.Button(label="📺 Watch TT-TV")
        self._attractor_btn.add_css_class("attractor-launch-btn")
        self._attractor_btn.set_tooltip_text(
            "Watch TT-TV — plays all media in a kiosk loop\n"
            "and continuously generates new content."
        )
        self._attractor_btn.set_sensitive(False)
        self._attractor_btn.connect("clicked", self._on_open_attractor)
        main_toolbar.append(self._attractor_btn)
        root_box.append(main_toolbar)

        # ── App menu bar ──────────────────────────────────────────────────────
        root_box.append(self._build_menu_bar())

        # ── Three-pane layout: controls | gallery | detail ────────────────────
        outer_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        outer_paned.set_vexpand(True)
        root_box.append(outer_paned)

        # Left pane: scrollable content area on top, pinned footer below.
        # The footer (Advanced settings + Server status + action buttons) stays
        # visible at all times; prompt/chips/inspire scroll when the window is short.
        ctrl_scroll = Gtk.ScrolledWindow()
        ctrl_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        ctrl_scroll.set_vexpand(True)
        ctrl_scroll.set_child(self._controls)

        ctrl_wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        ctrl_wrapper.append(ctrl_scroll)
        ctrl_wrapper.append(self._controls.footer_box)

        outer_paned.set_start_child(ctrl_wrapper)
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

        # Narrow status label for generation progress messages (above status bar)
        self._status_lbl = Gtk.Label(label="Ready")
        self._status_lbl.set_xalign(0)
        self._status_lbl.add_css_class("status-bar")
        gallery_wrap.append(self._status_lbl)

        inner_paned.set_start_child(gallery_wrap)
        inner_paned.set_shrink_start_child(False)

        self._detail = DetailPanel()

        # Queue display lives below the detail/preview panel on the right side.
        self._queue_section_lbl = Gtk.Label(label="QUEUED PROMPTS")
        self._queue_section_lbl.add_css_class("section-label")
        self._queue_section_lbl.set_xalign(0)
        self._queue_section_lbl.set_visible(False)
        self._queue_section_lbl.set_margin_start(6)
        self._queue_section_lbl.set_margin_top(6)

        self._queue_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self._queue_box.set_visible(False)
        self._queue_box.set_margin_start(6)
        self._queue_box.set_margin_end(6)
        self._queue_box.set_margin_bottom(6)

        detail_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        detail_wrap.append(self._detail)
        detail_wrap.append(self._queue_section_lbl)
        detail_wrap.append(self._queue_box)

        inner_paned.set_end_child(detail_wrap)
        inner_paned.set_shrink_end_child(False)

        outer_paned.set_end_child(inner_paned)
        outer_paned.set_shrink_end_child(False)

        # ── Hardware / infra status bar (pinned to window bottom) ─────────────
        # Clicking the server segment opens a popover with Start / Stop controls.
        # start_cb captures self._controls so it always reads the current source.
        self._hw_statusbar = _StatusBar(
            start_cb=lambda: self._on_start_server(self._controls.get_model_source()),
            stop_cb=self._on_stop_server,
        )
        root_box.append(self._hw_statusbar)

    def _set_status(self, text: str) -> None:
        """Update status bar. Safe to call from main thread only."""
        self._status_lbl.set_label(text)

    # ── Menu bar ───────────────────────────────────────────────────────────────

    def _build_menu_actions(self) -> None:
        """Register all Gio.SimpleActions for the menu bar on this window."""

        # ── File actions ──────────────────────────────────────────────────────
        open_folder = Gio.SimpleAction.new("open-media-folder", None)
        open_folder.connect("activate", self._on_open_media_folder)
        self.add_action(open_folder)

        prefs = Gio.SimpleAction.new("preferences", None)
        prefs.connect("activate", lambda *_: self._open_preferences(scroll_tttv=False))
        self.add_action(prefs)

        prefs_tttv = Gio.SimpleAction.new("preferences-tttv", None)
        prefs_tttv.connect("activate", lambda *_: self._open_preferences(scroll_tttv=True))
        self.add_action(prefs_tttv)

        recover = Gio.SimpleAction.new("recover-jobs", None)
        recover.connect("activate", lambda *_: self._on_recover())
        recover.set_enabled(False)   # enabled once the server is reachable
        self.add_action(recover)

        # ── Generation: quality preset (radio via stateful string action) ─────
        quality_action = Gio.SimpleAction.new_stateful(
            "quality",
            GLib.VariantType.new("s"),
            GLib.Variant("s", str(int(_settings.get("quality_steps")))),
        )
        quality_action.connect("activate", self._on_quality_action)
        self.add_action(quality_action)

        # ── Generation: sleep after N (radio via stateful string action) ──────
        sleep_action = Gio.SimpleAction.new_stateful(
            "sleep-after",
            GLib.VariantType.new("s"),
            GLib.Variant("s", str(int(_settings.get("sleep_after_n_gens")))),
        )
        sleep_action.connect("activate", self._on_sleep_after_action)
        self.add_action(sleep_action)

        # ── Prompt: director style probability (radio) ─────────────────────────
        prob_pct = str(int(float(_settings.get("director_style_prob")) * 100))
        dir_prob_action = Gio.SimpleAction.new_stateful(
            "director-prob",
            GLib.VariantType.new("s"),
            GLib.Variant("s", prob_pct),
        )
        dir_prob_action.connect("activate", self._on_director_prob_action)
        self.add_action(dir_prob_action)

        # ── Prompt: pinned director (radio) ────────────────────────────────────
        dir_pin_action = Gio.SimpleAction.new_stateful(
            "director-pin",
            GLib.VariantType.new("s"),
            GLib.Variant("s", _settings.get("director_pin") or ""),
        )
        dir_pin_action.connect("activate", self._on_director_pin_action)
        self.add_action(dir_pin_action)

        # ── View: toggle detail panel ─────────────────────────────────────────
        toggle_detail = Gio.SimpleAction.new_stateful(
            "toggle-detail",
            None,
            GLib.Variant("b", True),
        )
        toggle_detail.connect("activate", self._on_toggle_detail)
        self.add_action(toggle_detail)
        self._detail_visible: bool = True

    def _build_menu_bar(self) -> Gtk.PopoverMenuBar:
        """Build and return the Gtk.PopoverMenuBar driven by a Gio.Menu model."""
        menumodel = Gio.Menu()

        # ── File ──────────────────────────────────────────────────────────────
        file_menu = Gio.Menu()
        file_menu.append("Open Media Folder", "win.open-media-folder")
        file_menu.append_section(None, Gio.Menu())  # visual separator via empty section
        file_menu.append("Recover Jobs…", "win.recover-jobs")
        file_menu.append_section(None, Gio.Menu())
        file_menu.append("Preferences…", "win.preferences")
        file_menu.append("Quit", "app.quit")
        menumodel.append_submenu("File", file_menu)

        # ── Generation ────────────────────────────────────────────────────────
        gen_menu = Gio.Menu()
        quality_section = Gio.Menu()
        for label, steps in [("Fast (10 steps)", "10"), ("Standard (30 steps)", "30"),
                              ("High Quality (40 steps)", "40")]:
            item = Gio.MenuItem.new(label, "win.quality")
            item.set_attribute_value("target", GLib.Variant("s", steps))
            quality_section.append_item(item)
        gen_menu.append_section("Quality", quality_section)

        sleep_section = Gio.Menu()
        for label, val in [("Never", "0"), ("After 10 completions", "10"),
                           ("After 20 completions", "20"), ("After 50 completions", "50")]:
            item = Gio.MenuItem.new(label, "win.sleep-after")
            item.set_attribute_value("target", GLib.Variant("s", val))
            sleep_section.append_item(item)
        gen_menu.append_section("Sleep After", sleep_section)
        menumodel.append_submenu("Generation", gen_menu)

        # ── Prompt ────────────────────────────────────────────────────────────
        prompt_menu = Gio.Menu()
        dir_prob_section = Gio.Menu()
        for label, pct in [("Never", "0"), ("Sometimes (33%)", "33"),
                           ("Often (66%)", "66"), ("Always", "100")]:
            item = Gio.MenuItem.new(label, "win.director-prob")
            item.set_attribute_value("target", GLib.Variant("s", pct))
            dir_prob_section.append_item(item)
        prompt_menu.append_section("Director Style", dir_prob_section)

        pin_section = Gio.Menu()
        for display, full in _DIRECTOR_PINS:
            item = Gio.MenuItem.new(display or "Random", "win.director-pin")
            item.set_attribute_value("target", GLib.Variant("s", full))
            pin_section.append_item(item)
        prompt_menu.append_section("Pinned Director", pin_section)
        menumodel.append_submenu("Prompt", prompt_menu)

        # ── TT-TV ─────────────────────────────────────────────────────────────
        tttv_menu = Gio.Menu()
        tttv_menu.append("Configure TT-TV…", "win.preferences-tttv")
        menumodel.append_submenu("TT-TV", tttv_menu)

        # ── View ──────────────────────────────────────────────────────────────
        view_menu = Gio.Menu()
        view_menu.append("Toggle Detail Panel", "win.toggle-detail")
        menumodel.append_submenu("View", view_menu)

        return Gtk.PopoverMenuBar.new_from_model(menumodel)

    # ── Menu action handlers ───────────────────────────────────────────────────

    def _on_open_media_folder(self, _action, _param) -> None:
        """Open the tt-video-gen storage directory in the desktop file manager."""
        from history_store import STORAGE_DIR
        try:
            GLib.spawn_async(
                ["xdg-open", str(STORAGE_DIR)],
                flags=GLib.SpawnFlags.SEARCH_PATH,
            )
        except Exception as exc:
            self._set_status(f"Could not open folder: {exc}")

    def _open_preferences(self, scroll_tttv: bool = False) -> None:
        """Open (or present) the Preferences dialog."""
        if self._prefs_dialog is None or not self._prefs_dialog.get_visible():
            self._prefs_dialog = PreferencesDialog(self)
        self._prefs_dialog.present()
        if scroll_tttv:
            self._prefs_dialog.scroll_to_tttv()

    def _on_quality_action(self, action: Gio.SimpleAction,
                           param: GLib.Variant) -> None:
        """Menu: change default quality / steps preset."""
        val = param.get_string()
        action.set_state(GLib.Variant("s", val))
        steps = int(val)
        _settings.set("quality_steps", steps)
        self._controls._steps_spin.set_value(steps)
        # Keep Preferences dialog in sync if open
        if self._prefs_dialog and self._prefs_dialog.get_visible():
            for btn in self._prefs_dialog._quality_btns:
                btn.set_active(btn.steps_value == steps)

    def _on_sleep_after_action(self, action: Gio.SimpleAction,
                               param: GLib.Variant) -> None:
        val = param.get_string()
        action.set_state(GLib.Variant("s", val))
        _settings.set("sleep_after_n_gens", int(val))

    def _on_director_prob_action(self, action: Gio.SimpleAction,
                                 param: GLib.Variant) -> None:
        val = param.get_string()
        action.set_state(GLib.Variant("s", val))
        _settings.set("director_style_prob", int(val) / 100.0)
        # Sync Preferences dialog if open
        if self._prefs_dialog and self._prefs_dialog.get_visible():
            prob = int(val) / 100.0
            for btn in self._prefs_dialog._dir_prob_btns:
                btn.set_active(abs(btn.prob_value - prob) < 0.01)

    def _on_director_pin_action(self, action: Gio.SimpleAction,
                                param: GLib.Variant) -> None:
        full = param.get_string()
        action.set_state(GLib.Variant("s", full))
        _settings.set("director_pin", full)
        # Sync Preferences dialog if open
        if self._prefs_dialog and self._prefs_dialog.get_visible():
            label = _DIRECTOR_PIN_LABEL.get(full, "Random")
            for i, (display, _) in enumerate(_DIRECTOR_PINS):
                if display == label:
                    self._prefs_dialog._director_drop.set_selected(i)
                    break

    def _on_toggle_detail(self, action: Gio.SimpleAction, _param) -> None:
        self._detail_visible = not self._detail_visible
        action.set_state(GLib.Variant("b", self._detail_visible))
        # self._detail's parent is detail_wrap (the Box containing detail + queue).
        # Hiding it collapses the entire right panel of the inner paned.
        self._detail.get_parent().set_visible(self._detail_visible)

    # ── Screensaver inhibit ────────────────────────────────────────────────────

    def _screensaver_inhibit(self) -> None:
        """Call org.freedesktop.ScreenSaver.Inhibit to prevent screen lock while generating."""
        if self._screensaver_inhibit_cookie is not None:
            return  # already inhibiting
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            result = bus.call_sync(
                "org.freedesktop.ScreenSaver",
                "/org/freedesktop/ScreenSaver",
                "org.freedesktop.ScreenSaver",
                "Inhibit",
                GLib.Variant("(ss)", ("tt-video-gen", "Generation in progress")),
                GLib.VariantType.new("(u)"),
                Gio.DBusCallFlags.NONE,
                5000,
                None,
            )
            self._screensaver_inhibit_cookie = result.get_child_value(0).get_uint32()
        except Exception as exc:
            # Non-fatal — inhibit is best-effort; the unload-on-lock safety net handles the rest
            print(f"[tt-gen] screensaver inhibit failed: {exc}", file=sys.stderr)

    def _screensaver_uninhibit(self) -> None:
        """Release a previously acquired screensaver inhibit cookie."""
        cookie = self._screensaver_inhibit_cookie
        if cookie is None:
            return
        self._screensaver_inhibit_cookie = None
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync(
                "org.freedesktop.ScreenSaver",
                "/org/freedesktop/ScreenSaver",
                "org.freedesktop.ScreenSaver",
                "UnInhibit",
                GLib.Variant("(u)", (cookie,)),
                None,
                Gio.DBusCallFlags.NONE,
                5000,
                None,
            )
        except Exception as exc:
            print(f"[tt-gen] screensaver uninhibit failed: {exc}", file=sys.stderr)

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
        # Sync deletion with the TT-TV pool so it stops trying to play the file.
        if self._attractor_win is not None:
            self._attractor_win.remove_record(record)
        short = record.prompt[:50] + ("…" if len(record.prompt) > 50 else "")
        self._set_status(f'Deleted: "{short}"')

    def _load_history(self) -> None:
        records = self._store.all_records()
        if not records:
            return
        # Route each record to the gallery that matches its media type.
        video_recs   = [r for r in records if r.media_type == "video"]
        animate_recs = [r for r in records if r.media_type == "animate"]
        image_recs   = [r for r in records if r.media_type == "image"]
        if video_recs:
            self._video_gallery.load_history(video_recs)
        if animate_recs:
            self._animate_gallery.load_history(animate_recs)
        if image_recs:
            self._image_gallery.load_history(image_recs)
        self._set_status(f"Loaded {len(records)} previous generation(s)")
        self._update_attractor_btn()

    # ── Health worker ──────────────────────────────────────────────────────────

    def _start_health_worker(self) -> None:
        self._health_stop = threading.Event()
        self._health_thread = threading.Thread(
            target=self._health_loop, daemon=True
        )
        self._health_thread.start()

    def _health_loop(self) -> None:
        """Runs on background thread. Posts UI updates via GLib.idle_add.

        Requires two consecutive failed pings before reporting offline so that
        a single slow response (e.g. TT chip saturated during active inference)
        doesn't flip the UI dot to red.  A successful ping resets the counter
        immediately.
        """
        consecutive_failures = 0
        last_reported_ready: "bool | None" = None

        while not self._health_stop.is_set():
            ready = self._client.health_check()
            running_model: "str | None" = None

            if ready:
                consecutive_failures = 0
                running_model = self._client.detect_running_model()
            else:
                consecutive_failures += 1
                if consecutive_failures < 2:
                    # First miss — don't flip UI yet; wait for the next poll.
                    self._health_stop.wait(10.0)
                    continue

            # Only post a result when the state has changed or we just came up.
            # This avoids redundant idle_add calls every 10 s while steady.
            if ready != last_reported_ready:
                last_reported_ready = ready
                GLib.idle_add(self._on_health_result, ready, running_model)
            elif ready and running_model is not None:
                # Model may have changed (e.g. server restarted with different model).
                GLib.idle_add(self._on_health_result, ready, running_model)

            self._health_stop.wait(10.0)

    def _on_health_result(self, ready: bool, running_model: "str | None") -> bool:
        """Runs on main thread (called by GLib.idle_add)."""
        if not self._alive:
            return False
        # Auto-switch source tab on first model detection — once only.
        if running_model and not self._auto_tab_switched:
            source = _MODEL_TO_SOURCE.get(running_model)
            if source and source != self._controls.get_model_source():
                self._controls.switch_to_source(source)
            self._auto_tab_switched = True

        self._controls.set_server_state(ready, running_model)

        # Enable Recover Jobs (File menu) whenever the server is reachable,
        # regardless of which model tab is active or whether a job is running.
        server_reachable = ready or (running_model is not None)
        recover_action = self.lookup_action("recover-jobs")
        if recover_action:
            recover_action.set_enabled(server_reachable and not self._server_launching)

        # Mirror server health in the hardware status bar.
        display_model = _MODEL_DISPLAY.get(running_model or "", running_model or "")
        self._hw_statusbar.update_server(ready, display_model or None)

        if ready:
            # Stop tailing the Docker log — server is confirmed up
            if self._log_tail_stop:
                self._log_tail_stop.set()
                self._log_tail_stop = None
            if not (self._worker_gen and self._worker_gen._running()):
                self._set_status("Server ready — enter a prompt and click Generate")
        return False  # don't repeat (one-shot idle callback)

    def _load_prompt_gen_system(self) -> str:
        """
        Read the system prompt for the Qwen prompt generator from disk.

        Returns the file contents as a string.  Returns "" if the file is
        missing so the feature degrades gracefully (the model will still
        generate something, just without the cinematic mad-libs guidance).
        """
        path = Path(__file__).parent / "prompts" / "prompt_generator.md"
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _start_prompt_gen_health_worker(self) -> None:
        """Start the background thread that polls the prompt gen server on port 8001."""
        self._pg_stop = threading.Event()
        threading.Thread(
            target=self._prompt_gen_health_loop, daemon=True
        ).start()

    def _prompt_gen_health_loop(self) -> None:
        """
        Runs on background thread.  Polls the prompt gen server every 5 seconds
        and posts the result to the main thread via GLib.idle_add.
        """
        while not self._pg_stop.wait(5.0):
            ready = prompt_client.check_health()
            # THREADING: must not touch GTK widgets here — post to main thread
            GLib.idle_add(self._on_prompt_gen_health, ready)

    def _on_prompt_gen_health(self, ready: bool) -> bool:
        """Runs on main thread (called by GLib.idle_add)."""
        if not self._alive:
            return False
        self._controls.set_prompt_gen_state(ready)
        return False  # one-shot idle callback

    def _on_start_prompt_gen(self) -> None:
        """
        Launch start_prompt_gen.sh --gui in the background.

        Runs silently — no log streaming.  The health poll on port 8001 will
        detect when the server is ready.  Users can watch /tmp/tt_prompt_gen.log
        for details.
        """
        script = Path(__file__).parent / "start_prompt_gen.sh"
        subprocess.Popen(
            [str(script), "--gui"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )

    def _on_inspire(self, source: str, seed_text: str) -> None:
        """
        Start a prompt generation job in a background thread.

        Called by ControlPanel._trigger_inspire() via the on_inspire callback.
        Posts the result back to ControlPanel on the main thread.
        """
        system_prompt = self._prompt_gen_system_prompt

        def run():
            try:
                text = prompt_client.generate_prompt(source, seed_text, system_prompt)
                GLib.idle_add(self._on_inspire_result, text)
            except Exception as e:  # noqa: BLE001
                GLib.idle_add(self._on_inspire_error, str(e))

        threading.Thread(target=run, daemon=True).start()

    def _on_inspire_result(self, text: str) -> bool:
        """Runs on main thread — forward generated prompt text to ControlPanel."""
        self._controls.set_inspire_result(text)
        return False

    def _on_inspire_error(self, msg: str) -> bool:
        """Runs on main thread — log error and restore ControlPanel inspire button."""
        print(f"[tt-gen] Prompt generation error: {msg}", file=sys.stderr)
        self._controls.set_inspire_error(msg)
        return False

    # ── Attractor Mode ─────────────────────────────────────────────────────────

    def _on_open_attractor(self, _btn=None) -> None:
        """Open (or raise) the Attractor Mode kiosk window."""
        if self._attractor_win is not None:
            self._attractor_win.present()
            return

        # Stop any gallery videos that are currently playing so their GStreamer
        # pipelines are released before the attractor opens its own video slots.
        for gallery in (self._video_gallery, self._animate_gallery, self._image_gallery):
            gallery.stop_all_playback()

        try:
            win = attractor.AttractorWindow(
                records=self._store.all_records(),
                system_prompt=self._prompt_gen_system_prompt,
                model_source=self._controls.get_model_source(),
                on_enqueue=self._on_attractor_generate,
                on_user_enqueue=self._on_attractor_priority_enqueue,
                get_queue_depth=lambda: len(self._queue),
                get_queue_prompts=lambda: [item.prompt for item in self._queue],
                get_current_prompt=lambda: (
                    self._worker_gen._prompt
                    if self._worker_gen and self._worker and self._worker.is_alive()
                    else None
                ),
                get_is_generating=lambda: bool(self._worker and self._worker.is_alive()),
                get_server_status=lambda: (
                    self._controls._server_ready,
                    # Map raw model ID → display name for the TT-TV status bar.
                    # Falls back to the raw ID, or None if server is offline.
                    _MODEL_DISPLAY_SERVER.get(
                        self._controls._running_model or "",
                        self._controls._running_model,
                    ),
                ),
            )
        except Exception:
            import traceback
            msg = traceback.format_exc()
            print(f"[tt-gen] Attractor launch failed:\n{msg}", file=sys.stderr)
            # Also write to the attractor log so it survives terminal close
            import logging as _logging
            _logging.getLogger("attractor").exception("AttractorWindow() raised")
            self._set_status("Attractor Mode failed to open — see terminal or attractor.log")
            return
        win.set_transient_for(self)
        win.connect("destroy", self._on_attractor_closed)
        self._attractor_win = win
        win.present()
        GLib.idle_add(win.start)

    def _on_attractor_closed(self, _win) -> None:
        """Called when the attractor window is destroyed."""
        self._attractor_win = None

    def _on_attractor_priority_enqueue(self, prompt, neg="", steps=30, seed=-1,
                                        seed_image_path="", model_source="video",
                                        guidance_scale=5.0, ref_video_path="",
                                        ref_char_path="", animate_mode="animation",
                                        model_id="") -> None:
        """Enqueue a user-typed TT-TV prompt ahead of any pending auto-generated ones.

        Inserts at position 0 so the user's intent is served before the attractor's
        auto-prompts.  If the worker is idle, starts the job directly instead.
        """
        if not self._check_disk_space():
            return
        if self._worker and self._worker.is_alive():
            self._queue.insert(0, _QueueItem(prompt, neg, steps, seed, seed_image_path,
                                              model_source, guidance_scale,
                                              ref_video_path, ref_char_path,
                                              animate_mode, model_id))
            self._persist_queue()
            self._update_queue_display()
        else:
            self._on_generate(prompt, neg, steps, seed, seed_image_path,
                              model_source, guidance_scale, ref_video_path,
                              ref_char_path, animate_mode, model_id)

    def _on_attractor_generate(self, prompt, neg, steps, seed, seed_image_path="",
                                model_source="video", guidance_scale=3.5,
                                ref_video_path="", ref_char_path="",
                                animate_mode="animation", model_id="") -> None:
        """
        Called by AttractorWindow when it wants to enqueue a new generation.

        Starts the generation immediately if the worker is idle; parks it in
        the queue if a generation is already running.  This prevents prompts
        from accumulating unprocessed when attractor mode is the first thing
        started (before any manual generation has been triggered).
        """
        args = (prompt, neg, steps, seed, seed_image_path,
                model_source, guidance_scale, ref_video_path,
                ref_char_path, animate_mode, model_id)
        if self._worker and self._worker.is_alive():
            self._on_enqueue(*args)
        else:
            self._on_generate(*args)

    def _update_attractor_btn(self) -> None:
        """Enable/disable the Attractor button based on whether any media exists."""
        has_media = len(self._store.all_records()) > 0
        self._attractor_btn.set_sensitive(has_media)

    # ── Generation ─────────────────────────────────────────────────────────────

    def _check_disk_space(self) -> bool:
        """Return True if there is enough disk space to generate, False if critically low.

        Shows a status-bar warning when low. Uses the tt-video-gen storage directory
        as the reference path (videos and images are written there).
        """
        from history_store import STORAGE_DIR
        try:
            free = shutil.disk_usage(STORAGE_DIR).free
        except OSError:
            return True  # can't determine — allow generation rather than block it
        max_gb = int(_settings.get("max_disk_gb"))
        threshold = (max_gb * 1024 ** 3) if max_gb > 0 else _DISK_SPACE_MIN_BYTES
        if free < threshold:
            free_gb = free / (1024 ** 3)
            self._set_status(
                f"Disk space critically low ({free_gb:.1f} GB free) — "
                "generation paused. Free up space to continue."
            )
            return False
        return True

    def _on_generate(self, prompt, neg, steps, seed, seed_image_path="",
                     model_source="video", guidance_scale=3.5,
                     ref_video_path="", ref_char_path="",
                     animate_mode="animation", model_id="") -> None:
        if self._worker and self._worker.is_alive():
            return
        if not self._check_disk_space():
            return

        # Inhibit screensaver if the user has that preference enabled.
        # The unload-on-lock safety net in attractor.py already handles crashes,
        # but inhibiting prevents the lock from activating in the first place.
        if _settings.get("inhibit_screensaver"):
            self._screensaver_inhibit()

        # Add the pending card to the gallery that matches the generation type,
        # and remember that gallery so _on_finished/_on_error update the right one.
        self._gen_gallery = self._gallery_for_type(model_source)
        pending = self._gen_gallery.add_pending_card(prompt=prompt, model_source=model_source)
        self._controls.set_busy(True)
        # Do NOT call clear_prompt() here — the user may have typed a prompt they
        # haven't submitted yet, and auto-queue/attractor calls should not wipe it.
        # Prompt clearing is handled by ControlPanel._on_action_clicked (user-click only).

        if model_source == "image":
            model_name = _IMAGE_MODEL_IDS.get(
                model_id or self._controls.get_image_model(), "flux.1-dev"
            )
            self._set_status(f"Generating image with {model_name}…")
            gen = ImageGenerationWorker(
                client=self._client,
                store=self._store,
                prompt=prompt,
                negative_prompt=neg,
                num_inference_steps=steps,
                seed=seed,
                guidance_scale=guidance_scale,
                model=model_name,
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
                model="wan2.2-animate-14b",
            )
        else:
            model_name = _VIDEO_MODEL_IDS.get(
                model_id or self._controls.get_video_model(), "wan2.2-t2v"
            )
            self._set_status(f"Submitting {model_name} video generation job…")
            gen = GenerationWorker(
                client=self._client,
                store=self._store,
                prompt=prompt,
                negative_prompt=neg,
                num_inference_steps=steps,
                seed=seed,
                seed_image_path=seed_image_path,
                model=model_name,
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
        """Launch the server script matching the current source + model selection."""
        if model_source == "video":
            model_key = self._controls.get_video_model()
        elif model_source == "image":
            model_key = self._controls.get_image_model()
        else:
            model_key = ""

        script_name, label = _SERVER_SCRIPTS.get(
            (model_source, model_key), ("start_wan.sh", "Wan2.2 video")
        )
        script_path = str(Path(__file__).parent / script_name)

        self._controls.set_server_launching(True, clear_log=True)
        self._controls.append_server_log(f"Starting {label} server ({script_name} --gui)…")
        self._set_status(f"Launching {label} server…")
        self._hw_statusbar.update_starting()
        if a := self.lookup_action("recover-jobs"):
            a.set_enabled(False)

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
                _detected_log_file: "str | None" = None
                for line in proc.stdout:
                    stripped = line.rstrip()
                    GLib.idle_add(self._controls.append_server_log, stripped)
                    # The start script prints "Log file: /path/to/workflow.log" just
                    # before it exits in --gui mode.  Capture it so we can tail it.
                    if stripped.startswith("Log file: "):
                        _detected_log_file = stripped[len("Log file: "):]
                proc.wait()
                if proc.returncode != 0:
                    GLib.idle_add(self._controls.append_server_log,
                                  f"Script exited with code {proc.returncode}")
                    GLib.idle_add(self._set_status, "Server start script failed — check log")
                    GLib.idle_add(self._controls.set_server_launching, False)
                else:
                    GLib.idle_add(self._set_status,
                                  f"{label} server started — waiting for health check…")
                    # Leave the log panel open; set_server_state(True, ...) will collapse it.
                    # If the script handed off to a Docker log file, tail it so the user
                    # can see server startup progress without leaving the app.
                    if _detected_log_file:
                        GLib.idle_add(self._start_log_tail, _detected_log_file)
            except Exception as e:
                GLib.idle_add(self._controls.append_server_log, f"Error: {e}")
                GLib.idle_add(self._set_status, f"Server start error: {e}")
                GLib.idle_add(self._controls.set_server_launching, False)
            finally:
                self._server_proc = None

        threading.Thread(target=run, daemon=True).start()

    def _start_log_tail(self, log_path: str) -> None:
        """
        Start a background thread that tails log_path and appends new lines to the
        server log panel.

        Called after the start script exits and hands off to the Docker log file.
        The tail stops when the health check confirms the server is ready (via
        _on_health_result setting self._log_tail_stop), or when the server is stopped.
        """
        # Cancel any previous tail still running (e.g., restart after stop)
        if self._log_tail_stop:
            self._log_tail_stop.set()

        stop = threading.Event()
        self._log_tail_stop = stop

        # Show a visual separator in the log panel so the user knows we switched sources
        GLib.idle_add(
            self._controls.append_server_log,
            f"─── tailing {log_path.split('/')[-1]} ───",
        )

        def tail():
            try:
                with open(log_path, encoding="utf-8", errors="replace") as f:
                    # Seek to current end — skip lines the script already emitted
                    f.seek(0, 2)
                    while not stop.wait(0.5):
                        line = f.readline()
                        if line:
                            GLib.idle_add(
                                self._controls.append_server_log, line.rstrip()
                            )
            except OSError as e:
                GLib.idle_add(
                    self._controls.append_server_log, f"[log tail error: {e}]"
                )

        threading.Thread(target=tail, daemon=True).start()

    def _on_stop_server(self) -> None:
        """Run the stop command (via start_wan.sh --stop) in a background thread."""
        # Stop any active log tail before clearing the log panel
        if self._log_tail_stop:
            self._log_tail_stop.set()
            self._log_tail_stop = None
        # Both video and image use the same Docker image, so either script can stop it.
        script_path = str(Path(__file__).parent / "start_wan.sh")

        self._controls.set_server_launching(True, clear_log=True)
        self._controls.append_server_log("Stopping inference server…")
        self._set_status("Stopping inference server…")
        self._hw_statusbar.update_starting()
        if a := self.lookup_action("recover-jobs"):
            a.set_enabled(False)

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

    def _persist_queue(self) -> None:
        """Save the current queue to disk so it can be reloaded after a crash."""
        self._store.save_queue([
            {
                "prompt": item.prompt,
                "negative_prompt": item.negative_prompt,
                "steps": item.steps,
                "seed": item.seed,
                "seed_image_path": item.seed_image_path,
                "model_source": item.model_source,
                "guidance_scale": item.guidance_scale,
                "ref_video_path": item.ref_video_path,
                "ref_char_path": item.ref_char_path,
                "animate_mode": item.animate_mode,
                "model_id": item.model_id,
                "job_id_override": item.job_id_override,
            }
            for item in self._queue
        ])

    def _restore_queue(self) -> None:
        """Reload a queue saved by a previous session (survives crashes)."""
        saved = self._store.load_queue()
        if not saved:
            return
        # Track recovery job IDs seen so far to drop duplicate queue.json entries.
        seen_overrides: set = set()
        for d in saved:
            try:
                override = d.get("job_id_override", "")
                if override:
                    if override in seen_overrides:
                        continue  # duplicate recovery entry — skip
                    seen_overrides.add(override)
                self._queue.append(_QueueItem(
                    prompt=d.get("prompt", ""),
                    negative_prompt=d.get("negative_prompt", ""),
                    steps=d.get("steps", 20),
                    seed=d.get("seed", -1),
                    seed_image_path=d.get("seed_image_path", ""),
                    model_source=d.get("model_source", "video"),
                    guidance_scale=d.get("guidance_scale", 3.5),
                    ref_video_path=d.get("ref_video_path", ""),
                    ref_char_path=d.get("ref_char_path", ""),
                    animate_mode=d.get("animate_mode", "animation"),
                    model_id=d.get("model_id", ""),
                    job_id_override=override,
                ))
            except Exception:
                pass  # skip malformed items
        if self._queue:
            self._update_queue_display()
            n = len(self._queue)
            self._set_status(
                f"Restored {n} queued prompt{'s' if n != 1 else ''} from last session"
            )
            # Auto-start processing if nothing is already generating.
            # Without this, restored items are visible in the queue but
            # never kicked off — the server sits idle after a crash/restart.
            if not (self._worker and self._worker.is_alive()):
                GLib.idle_add(self._start_next_queued)

    def _update_queue_display(self) -> None:
        """Rebuild the queue list below the preview panel. Call from main thread only."""
        child = self._queue_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._queue_box.remove(child)
            child = nxt

        for i, item in enumerate(self._queue):
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

        has = bool(self._queue)
        self._queue_section_lbl.set_visible(has)
        self._queue_box.set_visible(has)
        # Keep the hardware status bar queue counter in sync.
        self._hw_statusbar.update_queue(len(self._queue))

    def _on_enqueue(self, prompt, neg, steps, seed, seed_image_path,
                    model_source="video", guidance_scale=3.5,
                    ref_video_path="", ref_char_path="",
                    animate_mode="animation", model_id="") -> None:
        if not self._check_disk_space():
            return
        self._queue.append(_QueueItem(prompt, neg, steps, seed, seed_image_path,
                                      model_source, guidance_scale,
                                      ref_video_path, ref_char_path, animate_mode,
                                      model_id))
        self._persist_queue()
        self._update_queue_display()
        # Do NOT call clear_prompt() here — clearing is handled by
        # ControlPanel._on_action_clicked (user-click only), not auto-queue paths.
        n = len(self._queue)
        self._set_status(f"Added to queue ({n} item{'s' if n != 1 else ''} queued)")

    def _on_queue_remove(self, index: int) -> None:
        if 0 <= index < len(self._queue):
            removed = self._queue.pop(index)
            self._persist_queue()
            self._update_queue_display()
            short = removed.prompt[:40] + ("…" if len(removed.prompt) > 40 else "")
            self._set_status(f'Removed from queue: "{short}"')

    def _start_next_queued(self) -> bool:
        if not self._queue:
            self._persist_queue()   # ensure queue.json is cleared when fully drained
            return False
        item = self._queue.pop(0)
        self._persist_queue()
        self._update_queue_display()

        if item.job_id_override:
            # Recovery item — skip if the job was already recovered into history
            # (happens when app restarts after a recovery job finished: history
            # has the card, but queue.json still has the stale entry).
            known_ids = {r.id for r in self._store.all_records()}
            if item.job_id_override in known_ids:
                self._set_status(
                    f"Recovery job {item.job_id_override[:8]}… already in history — skipping."
                )
                return self._start_next_queued()  # drain the rest of the queue
            # Use direct recovery path (no submission needed)
            self._launch_recovery_worker(
                item.job_id_override, item.prompt, item.negative_prompt,
                item.steps, item.seed,
            )
            return True

        remaining = len(self._queue)
        suffix = f" — {remaining} more queued" if remaining else ""
        self._set_status(f"Auto-starting next queued prompt{suffix}…")
        self._on_generate(item.prompt, item.negative_prompt,
                          item.steps, item.seed, item.seed_image_path,
                          item.model_source, item.guidance_scale,
                          item.ref_video_path, item.ref_char_path, item.animate_mode,
                          item.model_id)
        return True

    # ── Recovery ───────────────────────────────────────────────────────────────

    def _on_recover(self) -> None:
        # Build the full exclusion set:
        # 1. Jobs already in local history.
        known_ids = {r.id for r in self._store.all_records()}
        # 2. The job the current worker is actively tracking (not yet in history).
        if self._worker_gen and self._worker and self._worker.is_alive():
            live_id = self._worker_gen._current_job_id
            if live_id:
                known_ids.add(live_id)
        # 3. Jobs already queued for recovery (don't offer them twice).
        for item in self._queue:
            if item.job_id_override:
                known_ids.add(item.job_id_override)
        # 4. Jobs the user has permanently dismissed.
        dismissed = set(_settings.get("dismissed_job_ids") or [])
        known_ids |= dismissed

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
        if response == _RECOVERY_DISMISS and dlg.dismissed_jobs:
            # Permanently hide these jobs from future scans.
            existing = list(_settings.get("dismissed_job_ids") or [])
            new_ids = [j["id"] for j in dlg.dismissed_jobs if j["id"] not in existing]
            _settings.set("dismissed_job_ids", existing + new_ids)
            self._set_status(
                f"Ignored {len(dlg.dismissed_jobs)} job(s) — "
                "they won't appear in future recovery scans."
            )
            return
        if response != Gtk.ResponseType.OK or not dlg.selected_jobs:
            self._set_status("Recovery cancelled.")
            return
        for job in dlg.selected_jobs:
            self._attach_recovery_job(job)

    def _attach_recovery_job(self, job: dict) -> None:
        """Attach a recovered server job.

        If a generation is already running, insert the recovery item at the front
        of the queue so it starts immediately after the current job finishes.
        If idle, start it directly.
        """
        if self._worker and self._worker.is_alive():
            # Worker is busy — queue the recovery job at high priority (front)
            self._queue.insert(0, _QueueItem(
                prompt=job["prompt"],
                negative_prompt=job["negative_prompt"],
                steps=job["steps"],
                seed=job["seed"],
                model_source="video",
                job_id_override=job["id"],
            ))
            self._persist_queue()
            self._update_queue_display()
            self._set_status(
                f"Recovery job {job['id'][:8]} queued — "
                "will start after current generation."
            )
            return
        # No active worker — start immediately.
        self._launch_recovery_worker(
            job["id"], job["prompt"], job["negative_prompt"],
            job["steps"], job["seed"], job.get("status", ""),
        )

    def _launch_recovery_worker(self, job_id: str, prompt: str, neg: str,
                                 steps: int, seed: int, status: str = "") -> None:
        """Start a recovery GenerationWorker directly (caller must verify no worker is running)."""
        # Recovery jobs are video jobs; route to the video gallery.
        self._gen_gallery = self._video_gallery
        pending = self._video_gallery.add_pending_card()
        pending.update_status(f"Recovering {job_id[:8]}… ({status})")
        self._controls.set_busy(True)

        gen = GenerationWorker(
            client=self._client,
            store=self._store,
            prompt=prompt,
            negative_prompt=neg,
            num_inference_steps=steps,
            seed=seed,
            model="",  # unknown at recovery time; server response will set it
        )
        gen._job_id_override = job_id
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
        self._set_status(f"Re-attached job {job_id[:8]}…")

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
        self._screensaver_uninhibit()
        self._start_next_queued()
        if self._attractor_win is not None:
            GLib.idle_add(self._attractor_win.add_record, record)
        self._update_attractor_btn()
        # Sleep-after-N: count completions and suspend if the threshold is reached
        self._gen_completed_count += 1
        limit = int(_settings.get("sleep_after_n_gens"))
        if limit > 0 and self._gen_completed_count >= limit:
            self._gen_completed_count = 0
            self._set_status(f"Completed {limit} generation(s) — suspending…")
            GLib.timeout_add(1500, lambda: subprocess.Popen(["systemctl", "suspend"]) and False)
        return False

    def _on_error(self, message: str) -> bool:
        gallery = self._gen_gallery or self._active_gallery()
        gallery.remove_pending()
        self._gen_gallery = None
        self._controls.set_busy(False)
        self._set_status(f"Error: {message}")
        self._screensaver_uninhibit()
        self._start_next_queued()
        return False

    def do_close_request(self) -> bool:
        self._alive = False   # stop any pending GLib.idle_add callbacks from touching widgets
        self._health_stop.set()
        if self._pg_stop:
            self._pg_stop.set()
        self._hw_statusbar.stop()
        if self._log_tail_stop:
            self._log_tail_stop.set()
        if self._worker_gen:
            self._worker_gen.cancel()
        if self._server_proc and self._server_proc.poll() is None:
            self._server_proc.terminate()
        return False  # allow close
