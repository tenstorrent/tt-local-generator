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
    HealthWorker     — background thread for /tt-liveness polling
    RecoveryDialog   — modal listing unknown server jobs to re-attach
    MainWindow       — top-level Gtk.ApplicationWindow

(ControlPanel and ArtgenPanel — the legacy left-panel prompt form/queue/
server-status widget and the artgen generation sidebar — were deleted in
SP-3d-5; the app rests entirely on the Create/Discover/Remix shell now.
Discover still browses artgen media via the standalone `ArtgenGallery`.)
"""
import base64
import json
import os
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
from gi.repository import Gdk, GdkPixbuf, GLib, Gio, Gtk, Pango

from api_client import APIClient
from app_settings import settings as _settings
from chip_config import load_chips as _load_chips
from animate_picker import InputWidget, PickerPopover
from artgen_gallery import ArtgenGallery
from artgen_detail import ArtgenDetail
from artgen_render import AnimatedGifWidget
import artgen_kind
from create_view import CreateView
import gallery_layout
from history_store import GenerationRecord, HistoryStore
from media_store import MediaRecord
from model_status import ModelStatusService, Status
from servers_control import ServersControl
from worker import (
    AnimateDiffGenerationWorker,
    AnimateGenerationWorker,
    GenerationWorker,
    ImageGenerationWorker,
)
import attractor
import prompt_client
import server_manager as _sm

# On macOS the Homebrew GTK4 bottle ships without libmedia-gstreamer.dylib,
# so Gtk.Video always returns None from get_media_stream() and shows a blank
# frame.  When this flag is True we skip all Gtk.Video widgets and route video
# playback through the macOS system player (QuickTime via `open`).
_USE_SYSTEM_PLAYER: bool = sys.platform == "darwin"

# On macOS, import GstPlayer which uses gtk4paintablesink to render video into
# a Gtk.Picture without needing libmedia-gstreamer (absent from the Homebrew
# GTK4 bottle).  On Linux, Gtk.Video works normally and this import is skipped.
if _USE_SYSTEM_PLAYER:
    from gst_player import GstPlayer  # noqa: E402

# ── Tenstorrent dark palette as GTK CSS ───────────────────────────────────────

_CSS = b"""
/* -- Tenstorrent color palette --------------------------------------------------
 * The main app is an editor/IDE-style surface, so per the global CLAUDE.md it
 * uses the tt-vscode-toolkit variant (bright teal #4FD1C5 on deep blue-gray
 * #0F2A35), NOT the docs-site forest-teal. (A brief unification to forest-teal
 * was reverted 2026-07-13 -- keep this palette; it is the intended look here.) */
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
/* Emoji glyphs in button labels render with Apple Color Emoji on macOS,
   which has different advance widths than Noto Color Emoji on Linux.
   A small letter-spacing adds breathing room after emoji without requiring
   every label string to be touched individually. Harmless on Linux. */
button label,
togglebutton label {
    letter-spacing: 1px;
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
    font-size: 12px;
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
/* Pending card thumbnail placeholder - recessed area matching the thumbnail zone */
.pending-thumb-area {
    background-color: @tt_bg_darkest;
    border-radius: 4px;
    min-height: 112px;
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
    min-height: 0;
}
.source-btn label {
    padding: 0;
    margin: 0;
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
.loop-nav-row {
    padding: 6px 10px;
    background-color: @tt_bg_darkest;
    border-bottom: 1px solid @tt_border;
}
.loop-nav-btn {
    background-color: @tt_bg_dark;
    color: @tt_text_muted;
    border: 1px solid @tt_border;
    border-radius: 6px;
    padding: 6px 18px;
    font-size: 13px;
    font-weight: bold;
    min-height: 0;
}
.loop-nav-btn label {
    padding: 0;
    margin: 0;
}
.loop-nav-btn:hover {
    background-color: @tt_border;
    color: @tt_text;
}
.loop-nav-arrow {
    color: @tt_text_muted;
    padding: 0 5px;
    font-size: 13px;
}
.loop-nav-loop {
    color: @tt_accent;
    padding: 0 10px 0 5px;
    font-size: 15px;
    font-weight: bold;
}
.loop-nav-divider {
    margin: 4px 12px;
}
.loop-nav-action {
    color: @tt_accent;
}
.loop-nav-btn-active,
.loop-nav-btn:checked {
    background-color: @tt_accent;
    color: @tt_bg_darkest;
    border-color: @tt_accent;
    font-weight: bold;
}
.loop-nav-btn-active:hover,
.loop-nav-btn:checked:hover {
    background-color: @tt_accent_light;
}
.discover-type-row {
    padding: 6px 10px 0 10px;
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

/* -- Servers toolbar button + popover -------------------------------------- */
.servers-menu-btn {
    background: transparent;
    border: none;
    box-shadow: none;
    border-radius: 4px;
    color: @tt_text_secondary;
    font-size: 10px;
    padding: 2px 6px;
    margin-left: 2px;
    min-width: 0;
}
.servers-menu-btn:hover {
    background: rgba(79, 209, 197, 0.12);
    color: @tt_accent;
}
.servers-popover-row {
    padding: 4px 2px;
}
.servers-popover-key {
    font-size: 11px;
    font-weight: bold;
    color: @tt_accent;
    min-width: 110px;
}
.servers-popover-label {
    font-size: 10px;
    color: @tt_text_muted;
}
.servers-popover-dot {
    font-size: 9px;
    margin-right: 4px;
}
.servers-popover-dot-on  { color: @tt_success; }
.servers-popover-dot-off { color: @tt_text_muted; }
.servers-popover-btn {
    background: transparent;
    border: 1px solid @tt_border;
    border-radius: 3px;
    color: @tt_text_secondary;
    font-size: 10px;
    padding: 1px 6px;
    min-width: 42px;
}
.servers-popover-btn:hover { background: rgba(79,209,197,0.1); border-color: @tt_accent; }
.servers-popover-btn-stop:hover { background: rgba(255,107,107,0.1); border-color: #FF6B6B; color: #FF6B6B; }
.servers-popover-last-star { color: @tt_accent; font-size: .8rem; margin-left: .2rem; }
.servers-cap-header {
    font-size: 0.72rem; font-weight: 700; letter-spacing: 0.08em;
    color: @tt_text_muted; text-transform: uppercase;
    margin-top: 10px; margin-bottom: 2px;
}
/* -- Capability dashboard rows in the status bar popover -------------------- */
.cap-row-ready   { color: @tt_success; font-size: 12px; }
.cap-row-offline { color: @tt_text_muted; font-size: 12px; }
.cap-row-label   { font-size: 12px; color: @tt_text_secondary; min-width: 160px; }

/* -- Named control rows (QUALITY, CLIP LENGTH) ------------------------------ */
.named-ctrl-row {
    margin-top: 2px;
    margin-bottom: 0;
}
.named-ctrl-btn {
    min-width: 0;
    padding: 5px 4px;
    border-radius: 0;
    font-size: 0.78em;
}
.named-ctrl-btn:first-child  { border-radius: 5px 0 0 5px; }
.named-ctrl-btn:last-child   { border-radius: 0 5px 5px 0; }
.named-ctrl-btn:checked,
.named-ctrl-btn.active       { background: alpha(@accent_color, 0.18);
                                color: @accent_color;
                                border-color: @accent_color; }
.named-ctrl-sub {
    font-size: 0.72em;
    opacity: 0.65;
    margin-top: 1px;
}
.create-zone-label {
    font-size: 0.7em;
    font-weight: bold;
    letter-spacing: 0.08em;
    opacity: 0.55;
    margin-top: 6px;
    margin-bottom: 1px;
}

/* -- SHOT panel -------------------------------------------------------------- */
.shot-panel {
    border: 1px solid alpha(@borders, 0.5);
    border-radius: 6px;
    padding: 6px 8px;
    margin-top: 4px;
    margin-bottom: 2px;
}
.model-badge-label {
    font-size: 0.8em;
    font-weight: bold;
}
.model-badge-sub {
    font-size: 0.75em;
    opacity: 0.6;
}
.shot-switcher-btn {
    font-size: 0.72em;
    padding: 2px 6px;
    border-radius: 10px;
}
.seed-btn {
    min-width: 0;
    padding: 4px 4px;
    border-radius: 0;
    font-size: 0.78em;
}
.seed-btn:first-child { border-radius: 5px 0 0 5px; }
.seed-btn:last-child  { border-radius: 0 5px 5px 0; }
.seed-btn:checked,
.seed-btn.active      { background: alpha(#ec96b8, 0.18);
                        color: #ec96b8;
                        border-color: #ec96b8; }

/* -- Seed thumbnail well ---------------------------------------------------- */
/* Small 40x40 drop target that sits inline before the Inspire me button. */
.seed-thumb-well {
    border: 1px dashed alpha(@borders, 0.7);
    border-radius: 5px;
    min-width: 36px;
    min-height: 36px;
}
/* Amber border + pulse when model requires an image but none is loaded */
.seed-thumb-well.required {
    border-style: solid;
    border-color: #F4C471;
    animation: seed-pulse 1.8s ease-in-out infinite;
}
@keyframes seed-pulse {
    0%   { border-color: #F4C471; }
    50%  { border-color: alpha(#F4C471, 0.35); }
    100% { border-color: #F4C471; }
}
/* Solid teal border when a seed image is loaded (overrides required amber) */
.seed-thumb-well.has-seed {
    border-style: solid;
    border-color: @accent_color;
    animation: none;
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

/* -- Theme Set button ------------------------------------------------------- */
.theme-btn {
    background-color: @tt_bg_darkest;
    color: @tt_pink;
    border: 1px solid @tt_border;
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 11px;
}
.theme-btn:hover {
    background-color: @tt_bg_dark;
    border-color: @tt_pink;
    color: @tt_text;
}
.theme-btn:disabled {
    color: @tt_text_muted;
    border-color: @tt_bg_dark;
}
.theme-btn-loading {
    color: @tt_pink;
    border: 1px solid @tt_pink;
}

/* -- Theme popover ---------------------------------------------------------- */
.theme-popover {
    background-color: @tt_bg_darkest;
    border: 1px solid @tt_border;
    border-radius: 6px;
    padding: 8px;
}
.theme-shot-row {
    background-color: @tt_bg_dark;
    border-radius: 4px;
    padding: 4px 6px;
    margin-bottom: 2px;
}
.theme-shot-label {
    color: @tt_accent;
    font-size: 10px;
    font-weight: bold;
}
.theme-shot-text {
    color: @tt_text;
    font-size: 11px;
}
.theme-queue-btn {
    background-color: @tt_accent;
    color: @tt_bg_darkest;
    border: none;
    border-radius: 4px;
    padding: 4px 12px;
    font-size: 11px;
    font-weight: bold;
}
.theme-queue-btn:hover {
    background-color: @tt_accent_light;
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


/* -- Detail pane dismiss bar ----------------------------------------------- */
.detail-close-bar { padding: 2px 4px 0; min-height: 20px; }
.detail-close-bar button { padding: 0 4px; min-height: 16px; font-size: 10px; color: @tt_text_muted; }

/* -- Phase grid ------------------------------------------------------------ */
.phase-grid-header {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: @tt_text_muted;
    padding: 2px 4px;
}
.phase-cell-pending   { background-color: @tt_bg_dark;    border: 1px solid rgba(79,209,197,.1);  border-radius: 4px; }
.phase-cell-running   { background-color: #1a2a3a;        border: 2px solid @tt_accent;           border-radius: 4px; }
.phase-cell-done      { background-color: #1a3a20;        border: 2px solid @tt_success;          border-radius: 4px; }
.phase-cell-failed    { background-color: #3a1a1a;        border: 2px solid @tt_error;            border-radius: 4px; }
.phase-cell-skipped   { background-color: #2a2010;        border: 1px solid rgba(244,196,113,.4); border-radius: 4px; }
.phase-cell-selected  { outline: 2px solid @tt_accent; outline-offset: 1px; }
.phase-job-label      { font-size: 10px; font-weight: 700; color: @tt_text; }
.phase-job-sublabel   { font-size: 9px;  color: @tt_text_muted; }

/* Hide GTK4's built-in video mediacontrols overlay (eject button, etc.). */
video > mediacontrols { opacity: 0; }
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
.tt-statusbar-dot-error   { color: @tt_error; }
.tt-statusbar-seg-error   { font-size: 10px; color: @tt_error; }
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
/* Context slot - teal accent to distinguish from fixed menus */
menubar > item.context-menu-item > label {
    color: @tt_accent;
    font-weight: 600;
}
menubar > item.context-menu-item:hover > label,
menubar > item.context-menu-item:selected > label {
    color: @tt_accent_light;
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
/* -- Playlists popover -------------------------------------------------------- */
.playlists-popover-row {
    padding: 5px 2px;
}
.playlists-popover-name {
    font-size: 11px;
    font-weight: bold;
    color: @tt_text;
    min-width: 120px;
}
.playlists-popover-count {
    font-size: 10px;
    color: @tt_text_muted;
    margin-top: 1px;
}
/* "+ New" header button - accent tinted so it reads as a create action */
.playlists-new-btn {
    background: alpha(@tt_accent, 0.10);
    border: 1px solid alpha(@tt_accent, 0.35);
    border-radius: 4px;
    color: @tt_accent;
    font-size: 10px;
    padding: 2px 10px;
}
.playlists-new-btn:hover {
    background: alpha(@tt_accent, 0.20);
    border-color: @tt_accent;
}
/* Destructive delete button in playlist rows */
.playlists-del-btn {
    background: transparent;
    border: 1px solid @tt_border;
    border-radius: 3px;
    color: @tt_text_muted;
    font-size: 10px;
    padding: 1px 6px;
    min-width: 28px;
}
.playlists-del-btn:hover {
    background: rgba(255, 107, 107, 0.10);
    border-color: #FF6B6B;
    color: #FF6B6B;
}
/* -- Selection mode banner ---------------------------------------------------- */
.selection-banner {
    background-color: alpha(@tt_accent, 0.07);
    border-bottom: 1px solid alpha(@tt_accent, 0.30);
    padding: 6px 14px;
}
.selection-banner-label {
    font-size: 12px;
    color: @tt_accent;
    font-weight: bold;
}
/* Primary "Add Selected" button - matches the banner's weight */
.selection-add-btn {
    background: alpha(@tt_accent, 0.14);
    border: 1px solid alpha(@tt_accent, 0.50);
    border-radius: 4px;
    color: @tt_accent;
    font-size: 12px;
    font-weight: bold;
    padding: 4px 16px;
    min-width: 0;
}
.selection-add-btn:hover {
    background: alpha(@tt_accent, 0.24);
    border-color: @tt_accent;
}
/* Cancel button in the selection banner */
.selection-cancel-btn {
    background: transparent;
    border: 1px solid @tt_border;
    border-radius: 4px;
    color: @tt_text_secondary;
    font-size: 12px;
    padding: 4px 12px;
    min-width: 0;
}
.selection-cancel-btn:hover {
    background: rgba(255, 107, 107, 0.08);
    border-color: #FF6B6B;
    color: #FF6B6B;
}
/* -- Card checkbox overlay ---------------------------------------------------- */
/* Semi-opaque pill behind the checkbox so it reads against any card image */
.card-check {
    margin: 6px;
    background: rgba(15, 42, 53, 0.72);
    border-radius: 4px;
    padding: 2px 3px;
}
/* Detail-panel playlist checkboxes */
.detail-playlist-check {
    font-size: 11px;
    color: @tt_text;
}

/* -- Animate InputWidget ---------------------------------------------------- */
.input-widget {
    background-color: @tt_bg_dark;
    border: 1px solid @tt_border;
    border-radius: 4px;
    padding: 0;
}
.input-widget:hover {
    border-color: @tt_accent_light;
}
.input-widget-filled-motion {
    border-color: @tt_pink;
}
.input-widget-filled-char {
    border-color: @tt_accent;
}
.input-widget-type {
    color: @tt_text_muted;
    font-size: 7px;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}
.input-widget-name {
    font-size: 8px;
    color: @tt_text;
}
.input-widget-placeholder {
    color: @tt_text_muted;
    font-size: 18px;
}
.input-widget-thumb {
    background-color: @tt_bg_darkest;
    border-radius: 2px;
}
.input-widget-caret {
    font-size: 8px;
    color: @tt_text_muted;
}

/* -- Gallery card hover action bar ------------------------------------------ */
.hover-action-bar {
    background: linear-gradient(to top, rgba(10,30,40,0.92), transparent);
    padding: 6px 4px 4px 4px;
}
.hover-action-btn {
    border-radius: 3px;
    padding: 2px 6px;
    font-size: 10px;
    font-weight: bold;
    border: 1px solid @tt_border;
    background-color: rgba(15,42,53,0.85);
    min-height: 0;
}
.hover-action-btn label {
    padding: 0;
    margin: 0;
}
.hover-action-btn-remix {
    background: rgba(79, 209, 197, 0.18);
    color: @tt_accent;
    border: 1px solid @tt_accent;
}
.hover-action-btn-remix:hover {
    background: rgba(79, 209, 197, 0.32);
}
.remix-target-btn {
    background: rgba(79, 209, 197, 0.12);
    border: 1px solid @tt_accent;
    border-radius: 4px;
    color: @tt_accent;
    padding: 3px 10px;
    font-size: 11px;
}
.remix-target-btn:hover {
    background: rgba(79, 209, 197, 0.25);
}
.remix-hint-preview {
    background: @tt_bg_dark;
    border-left: 2px solid @tt_accent;
    border-radius: 0 3px 3px 0;
    padding: 4px 8px;
    color: @tt_text_muted;
    font-style: italic;
    font-size: 11px;
}

/* -- Mode description bar --------------------------------------------------- */
.mode-desc-bar {
    background-color: @tt_bg_dark;
    border: 1px solid @tt_border;
    border-top: none;
    border-radius: 0 0 4px 4px;
    padding: 5px 8px;
}
.mode-desc-bar-anim {
    border-color: @tt_accent;
}
.mode-desc-bar-repl {
    border-color: @tt_pink;
}
.mode-desc-bar-icon {
    font-size: 14px;
}
.mode-desc-bar-text {
    font-size: 9px;
    color: @tt_text;
}
.mode-desc-bar-impact-anim {
    font-size: 8px;
    color: @tt_accent;
}
.mode-desc-bar-impact-repl {
    font-size: 8px;
    color: @tt_pink;
}

.mode-desc-static {
    font-size: 10px;
    color: alpha(@tt_text, 0.5);
    padding: 2px 0 4px 0;
}

/* -- Picker popover --------------------------------------------------------- */
popover.picker-popover > contents {
    background-color: @tt_bg_darkest;
    border: 1px solid @tt_accent;
    border-radius: 6px;
    padding: 0;
}
.picker-title {
    font-size: 10px;
    font-weight: bold;
    color: @tt_accent;
}
.picker-tab-btn {
    background-color: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    color: @tt_text_muted;
    font-size: 9px;
    padding: 3px 8px;
    min-height: 0;
}
.picker-tab-btn label { padding: 0; margin: 0; }
.picker-tab-btn:hover { color: @tt_text; border-bottom-color: @tt_border; }
.picker-tab-btn-active {
    color: @tt_accent;
    border-bottom-color: @tt_accent;
    font-weight: bold;
}
.picker-tab-btn-active:hover { color: @tt_accent_light; }
.picker-thumb-cell {
    background-color: @tt_bg_dark;
    border: 1px solid @tt_border;
    border-radius: 3px;
    min-width: 60px;
    min-height: 44px;
}
.picker-thumb-cell:hover { border-color: @tt_accent_light; }
.picker-thumb-cell-selected {
    border-color: @tt_accent;
    border-width: 2px;
}
.picker-cat-chip {
    background-color: @tt_bg_dark;
    border: 1px solid @tt_border;
    border-radius: 10px;
    color: @tt_text_muted;
    font-size: 8px;
    padding: 2px 7px;
    min-height: 0;
}
.picker-cat-chip label { padding: 0; margin: 0; }
.picker-cat-chip:hover { border-color: @tt_accent; color: @tt_text; }
.picker-cat-chip-active {
    border-color: @tt_accent;
    color: @tt_accent;
}
.picker-folder-row {
    background-color: @tt_bg_dark;
    border: 1px solid @tt_border;
    border-radius: 3px;
    padding: 4px 6px;
}
.picker-empty {
    color: @tt_text_muted;
    font-size: 10px;
}
.picker-browse-tile {
    background-color: transparent;
    border: 1px dashed @tt_accent;
    border-radius: 3px;
    color: @tt_accent;
    font-size: 10px;
    min-width: 60px;
    min-height: 44px;
}
.picker-browse-tile label { padding: 0; margin: 0; }
.picker-use-btn {
    background-color: @tt_accent;
    color: @tt_bg_darkest;
    border-color: @tt_accent;
    border-radius: 3px;
    font-size: 9px;
    font-weight: bold;
    padding: 3px 8px;
    min-height: 0;
}
.picker-use-btn label { padding: 0; margin: 0; }
.picker-use-btn:disabled { background-color: @tt_border; color: @tt_text_muted; }
.picker-cancel-btn {
    background-color: @tt_bg_dark;
    border: 1px solid @tt_border;
    border-radius: 3px;
    color: @tt_text_muted;
    font-size: 9px;
    padding: 3px 8px;
    min-height: 0;
}
.picker-cancel-btn label { padding: 0; margin: 0; }

/* -- Artgen panel ----------------------------------------------------------- */
.artgen-ctrl-pane {
    background-color: @tt_bg_panel;
    border-right: 1px solid @tt_border;
}
.artgen-preview-pane {
    background-color: @tt_bg_darkest;
}
.artgen-generate-btn {
    background-color: @tt_accent;
    color: @tt_bg_darkest;
    font-weight: bold;
    font-size: 14px;
    padding: 10px;
    border: none;
    border-radius: 4px;
}
.artgen-generate-btn:hover {
    background-color: @tt_accent_light;
}
.artgen-generate-btn:disabled {
    background-color: @tt_border;
    color: @tt_text_muted;
}
.artgen-status {
    color: @tt_text_muted;
    font-size: 11px;
}
.artgen-empty-hint {
    color: @tt_border;
    font-size: 20px;
    margin-bottom: 6px;
}
.artgen-empty-sub {
    color: @tt_text_hint;
    font-size: 12px;
}
.freeform-entry {
    border: 1px solid @tt_border;
    border-radius: 4px;
}
.artgen-health-ok    { color: #27AE60; font-size: 15px; }
.artgen-health-bad   { color: #FF6B6B; font-size: 15px; }
.artgen-health-unknown { color: #607D8B; font-size: 15px; }
.artgen-srv-start-btn {
    background: @tt_accent;
    color: @tt_bg_darkest;
    padding: 2px 10px;
    font-size: 12px;
    border-radius: 4px;
    border: none;
}
.artgen-srv-start-btn:hover { background: @tt_accent_hover; }
.artgen-srv-stop-btn {
    background: @tt_bg_medium;
    color: @tt_text_primary;
    padding: 2px 10px;
    font-size: 12px;
    border-radius: 4px;
    border: 1px solid @tt_border;
}
.artgen-srv-stop-btn:hover { background: @tt_bg_panel; }
.artgen-subnav { background: shade(@tt_bg_dark, 0.85); }
.artgen-subnav-btn { border-radius: 0; padding: 6px 16px; font-size: 12px; }
.artgen-subnav-btn:checked { color: @tt_accent; border-bottom: 2px solid @tt_accent; }
.artgen-filter-chip { border-radius: 12px; padding: 2px 10px; font-size: 11px; }
.artgen-filter-chip:checked { background: @tt_accent; color: @tt_bg_dark; }
.gallery-page-label { font-size: 11px; color: @tt_text_muted; }
.artgen-card { border-radius: 4px; background: @tt_bg_panel; }
.artgen-card-new { border: 2px solid @tt_accent; }
.artgen-card-placeholder { font-size: 20px; }
.artgen-text-preview { padding: 6px 8px; }
.artgen-preview-title { font-size: 12px; font-weight: bold; color: @tt_accent; letter-spacing: 0.01em; }
.artgen-preview-rule { min-height: 1px; background: alpha(@tt_accent, 0.3); margin: 1px 0 2px; }
.artgen-preview-body { font-size: 9px; color: @tt_muted; }
.artgen-palette-name { font-size: 9px; color: @tt_muted; padding: 2px 5px; }
.artgen-card-bottom { font-size: 9px; padding: 3px 5px; color: @tt_muted; }
.artgen-type-badge { font-size: 8px; background: alpha(@tt_bg_dark,0.8); color: @tt_accent; padding: 1px 4px; border-radius: 2px; }
.artgen-card-hover-actions { background: alpha(@tt_bg_dark, 0.72); border-radius: 0 4px 0 4px; }
.artgen-card-action-btn { min-width: 22px; min-height: 22px; padding: 1px 3px; font-size: 11px; border-radius: 3px; background: transparent; border: none; color: @tt_text; }
.artgen-card-action-btn:hover { background: alpha(@tt_accent, 0.25); color: @tt_accent; }
.artgen-starred-chip { color: @tt_accent; }
.artgen-watch-btn-bar { padding: 2px 10px; font-size: 12px; }
.artgen-watch-bg { background: #000; }
.artgen-watch-btn { color: rgba(255,255,255,0.8); background: transparent; border: none; }
.artgen-watch-nav-btn { font-size: 22px; background: rgba(0,0,0,0.5); border-radius: 50%; color: white; padding: 4px 10px; }
.artgen-watch-pos { color: rgba(255,255,255,0.7); font-size: 12px; }
.artgen-watch-meta { color: rgba(255,255,255,0.6); font-size: 11px; }
.artgen-detail-title { font-size: 12px; color: @tt_muted; }
.artgen-inspire-btn { background: @tt_accent; color: @tt_bg_dark; border-radius: 3px; padding: 3px 8px; }
.artgen-preset-menu-btn { font-size: 14px; padding: 2px 4px; }
.artgen-preset-section { font-size: 9px; color: @tt_muted; margin-top: 6px; margin-bottom: 2px; }
.artgen-preset-btn { background: transparent; border: none; border-radius: 4px; padding: 4px 6px; }
.artgen-preset-btn:hover { background: alpha(@tt_accent, 0.12); }
.artgen-preset-name { font-size: 12px; font-weight: bold; color: @tt_text; }
.artgen-preset-desc { font-size: 10px; color: @tt_muted; }

/* Star toggle - used on GenerationCard hover bar and DetailPanel action row */
.gen-star-btn { color: @tt_accent; }
.gen-star-btn:hover { background: alpha(@tt_accent, 0.12); }

/* Prev/Next nav buttons in DetailPanel */
.detail-nav-btn { min-width: 28px; padding: 2px 6px; }

/* Mosaic waiting screen (artgen panel) */
.mosaic-grid { background: #0A1F28; }
.mosaic-tile { min-width: 1px; min-height: 1px; }
.mosaic-status-strip {
  background: rgba(10, 31, 40, 0.75);
  padding: 6px 12px;
}
.mosaic-status-lbl {
  color: #E8F0F2;
  font-size: 12px;
}
/* -- Log viewer ------------------------------------------------------------ */
.log-sidebar {
    background-color: @tt_bg_panel;
}
.log-section-header {
    color: @tt_text_muted;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    padding: 8px 12px 2px;
}
.log-row-error {
    color: @tt_error;
}
.log-row-ok {
    color: @tt_success;
}
.log-content {
    font-family: "Noto Mono", "Fira Code", monospace;
    font-size: 11px;
    background-color: @tt_bg_darkest;
    color: @tt_text;
    padding: 8px;
}
.log-footer {
    background-color: @tt_bg_panel;
    border-top: 1px solid @tt_border;
}
.log-footer-btn {
    background: rgba(79, 209, 197, 0.12);
    border: 1px solid @tt_accent;
    border-radius: 3px;
    color: @tt_accent;
    padding: 2px 8px;
    font-size: 11px;
}
.log-footer-btn:hover {
    background: rgba(79, 209, 197, 0.25);
}

/* -- Possibilities wall ("Start something") on the Create surface ---------- */
.possibilities-wall { padding: 6px 2px; }
.possibilities-title { font-size: 20px; font-weight: bold; color: @tt_text; }
.possibilities-surprise {
    background-color: @tt_accent; color: @tt_bg_darkest;
    border-radius: 10px; padding: 8px 16px; font-weight: bold;
}
.possibilities-card {
    padding: 0; border-radius: 12px; border: 1px solid @tt_border;
    background-color: @tt_bg_dark;
}
.possibilities-card:hover { border-color: @tt_accent; }
.possibilities-cap {
    padding: 8px 10px;
    background-image: linear-gradient(to top, rgba(4,18,23,0.93), rgba(4,18,23,0.0));
    border-radius: 0 0 12px 12px;
}
.possibilities-med { font-size: 13px; font-weight: bold; color: @tt_text; }
.possibilities-eg { font-size: 11px; color: @tt_text_muted; }
.possibilities-grad-icon { font-size: 34px; opacity: 0.55; }
.poss-grad-image { background-image: linear-gradient(135deg, #123240, #3aa89e); }
.poss-grad-video { background-image: linear-gradient(135deg, #123f4a, #e0a24a); }
.poss-grad-gif   { background-image: linear-gradient(135deg, #1a2b4a, #7d3a8f); }
.poss-grad-text  { background-image: linear-gradient(135deg, #0b1f28, #2b6373); }
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

# Gallery-card tile sizing is centralised in gallery_layout.py (shared with
# artgen_gallery.py -- see its docstring for the "why" of the uniform-size
# fix). _THUMB_W/_THUMB_H are kept as local aliases since they're referenced
# throughout this file (ffmpeg thumbnail dims, detail-panel sizing, etc.).
_THUMB_W = gallery_layout.THUMB_W
_THUMB_H = gallery_layout.THUMB_H   # 16:9
_TILE_W = gallery_layout.TILE_W     # fixed OUTER card size -- both dimensions
_TILE_H = gallery_layout.TILE_H


def _gallery_density() -> str:
    """
    Return the live gallery-density setting ("comfortable" if never set).

    Single accessor so every card constructor (read at BUILD time) and
    `_apply_gallery_density` (read at menu-toggle time) resolve the setting
    identically. "gallery_density" is deliberately absent from
    `app_settings.DEFAULTS` (it's an opt-in toggle, only written once the
    user actually switches density via the Discover menu), so a bare
    `settings.get()` can return `None` — this centralises the
    `or "comfortable"` fallback in one place instead of repeating it at
    every call site.
    """
    return _settings.get("gallery_density") or "comfortable"


_DETAIL_VIDEO_W = 400
_DETAIL_VIDEO_H = 225

# Maps internal model ID strings to short display names shown on gallery badges.
# Empty string → no badge (legacy records without model attribution).
_MODEL_DISPLAY: dict = {
    "wan2.2-t2v":            "Wan2.2",
    "mochi-1-preview":       "Mochi-1",
    "flux.1-schnell":        "FLUX",
    "wan2.2-animate-14b":    "Animate-14B",
    "skyreels-v2-i2v-14b-540p": "SkyReels I2V",
    "z-image-turbo":         "Z-Image",
    "motif-image-6b-preview": "Motif",
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
    ("video",   "wan2"):           ("start_wan_qb2.sh",         "Wan2.2 video (P300X2)"),
    ("video",   "mochi"):          ("start_mochi.sh",           "Mochi-1 video"),
    ("video",   "skyreels"):       ("start_skyreels_i2v.sh",    "SkyReels-V2-I2V video (Blackhole)"),
    ("image",   "flux"):           ("start_flux.sh",            "FLUX image"),
    ("image",   "sdxl"):           ("start_sdxl.sh",            "SDXL image (cpp_server)"),
    ("image",   "z-image-turbo"):  ("start_z_image_turbo.sh",   "Z-Image-Turbo image (P150X4)"),
    ("image",   "motif"):          ("start_motif.sh",           "Motif image (P300X2)"),
    ("animate", ""):               ("start_animate.sh",         "Wan2.2-Animate"),
}


def _server_key_for_script(script_name: str) -> "str | None":
    """Reverse-lookup a `server_manager.SERVERS` key from its launch script.

    `_on_start_server`/`_on_stop_server` below resolve *which script* to run
    from `_SERVER_SCRIPTS` (keyed by model_source/model_key, the legacy
    Video/Image tab vocabulary) rather than a `server_manager.SERVERS` key
    directly. `ModelStatusService.note_starting`/`note_stopping` need the
    latter, so this bridges the two by matching on `ServerDef.script` — every
    script in `_SERVER_SCRIPTS` corresponds to exactly one `SERVERS` entry.
    Returns None (rather than raising) if no match is found, since a bad
    match must never break the start/stop flow that calls it.
    """
    for key, sdef in _sm.SERVERS.items():
        if sdef.script == script_name:
            return key
    return None

# Maps short model keys to canonical model ID strings used in GenerationRecord.
_VIDEO_MODEL_IDS: dict = {
    "wan2":         "wan2.2-t2v",
    "mochi":        "mochi-1-preview",
    "skyreels":     "skyreels-v2-i2v-14b-540p",
    "animatediff":  "animatediff-blackhole",
}
_IMAGE_MODEL_IDS: dict = {
    "flux":           "flux.1-schnell",
    "sdxl":           "stable-diffusion-xl-base-1.0",
    "z-image-turbo":  "z-image-turbo",
    "motif":          "motif-image-6b-preview",
}

# Inverse of the two maps above: canonical server-side model id -> short key.
# `create_param_panels.ImageParamPanel`/`VideoParamPanel.collect()` already
# resolve their own "model" field to the CANONICAL id (they hold their own
# duplicate copy of these dicts — see that module's CRITICAL STRATEGY note),
# but `_on_generate`'s `model_id=` parameter expects the SHORT key (it does
# `_IMAGE_MODEL_IDS.get(model_id or ..., default)` itself). CreateView's
# `_on_create_generate` needs to invert back from canonical id to short key
# before calling `_on_generate`, or every non-default model choice would
# silently fall back to the default model.
_IMAGE_MODEL_ID_TO_KEY: dict = {v: k for k, v in _IMAGE_MODEL_IDS.items()}
_VIDEO_MODEL_ID_TO_KEY: dict = {v: k for k, v in _VIDEO_MODEL_IDS.items()}

# SP-3a (decouple `_on_generate` from ControlPanel): the short-key fallbacks
# `_on_generate` uses when a caller supplies neither an explicit
# `video_model_key`/`image_model_key` nor a `model_id` it can resolve. These
# mirror ControlPanel's own fresh-session defaults (`self._video_model =
# "animatediff"`, `self._image_model = "flux"` in `ControlPanel.__init__`) so
# a caller that omits model selection entirely still behaves the way it did
# before `_on_generate` read those defaults off `self._controls`.
_DEFAULT_VIDEO_KEY = "animatediff"
_DEFAULT_IMAGE_KEY = "flux"

# SP-3a follow-up fix (review finding): `_on_generate`'s AnimateDiff branch
# indexes every one of these keys directly (`ad["mode"]`, `ad["chain_save"]`,
# ...). Before this task, `ad` always came from a fresh
# `self._controls.get_animatediff_args()` call, which — reading real GTK
# widget state — ALWAYS returns every key. Now `ad` can be a caller-supplied
# `animatediff_args` param that's `None` (a caller that never had AnimateDiff
# in scope, e.g. an AnimateDiff `_QueueItem` persisted by a PRE-SP-3a build
# and reloaded by `_restore_queue`, whose `queue.json` predates the
# `"animatediff_args"` key entirely) or a partial dict. `_on_generate` merges
# this default dict under whatever the caller passed
# (`{**_ANIMATEDIFF_DEFAULTS, **(animatediff_args or {})}`) so a missing or
# partial dict can never `KeyError`, while a full dict passes through
# unchanged (caller values win). Values mirror
# `ControlPanel._build_animatediff_box()`'s widget defaults exactly (see
# `ControlPanel.get_animatediff_args()` a few hundred lines below — read that
# method, not this comment, if the two ever drift).
_ANIMATEDIFF_DEFAULTS: dict = dict(
    mode="blackhole",
    negative_prompt="blurry, low quality",
    temporal_alpha=0.35,
    lightning=False,
    lightning_steps=4,
    multi_chip=True,
    device_id=None,
    chain_from=None,
    chain_save=False,
    chain_alpha=0.6,
    motion_adapter=None,
    motion_adapter_alpha=1.0,
    motion_adapter_skip=None,
)


def _theme_key_from_text(text: str) -> str:
    """Map free text (Create's brief) to a `generate_theme.THEME_LIBRARY`
    key by a loose, case-insensitive containment match against each key and
    its display label.

    SP-3d-1: `generate_theme.generate_theme()` only ever accepts a fixed
    `theme_key` (or `""` for "pick randomly" — see that function's own
    docstring); it has no free-text theme parameter to fork or extend. This
    is how Create's "supply the theme text" launch actually influences which
    theme gets used, without changing the backend's contract at all — the
    same `generate_theme.generate_theme(theme_key=..., enhance=True)` call
    ControlPanel's legacy Theme Set button already made. No match (including
    an empty/blank brief) returns `""`, which `generate_theme.generate_theme`
    already treats as "pick randomly" — identical to what ControlPanel's
    Theme Set button did unconditionally (it never passed a theme_key at
    all).
    """
    needle = (text or "").strip().lower()
    if not needle:
        return ""
    import generate_theme
    for key, spec in generate_theme.THEME_LIBRARY.items():
        if key.replace("_", " ") in needle or spec.label.lower() in needle:
            return key
    return ""


def _artgen_accepts_prompt(generator: str) -> bool:
    """True if artgen generator *generator*'s own `add_args` declares a
    `--prompt` argument (dest == "prompt").

    Most artgen generators (verse/ansi/palette/landscape/…) have no common
    `--prompt` flag at all — each has its own bespoke vocabulary (--theme,
    --subject, --mood, …) — so `_create_generate_artgen` has always (by
    design) skipped forwarding the idea-door's typed prompt to them: doing
    so would raise an argparse "unrecognized argument" error.

    The artgen "animatediff" plugin is the exception: `plugins/animatediff/
    plugin.py`'s `add_args` DOES declare `--prompt` (default "a candle flame
    flickering") — so when `_create_generate_artgen` skipped "prompt"
    unconditionally, the user's typed prompt never reached it and every
    AnimateDiff Create generation silently rendered the default candle
    instead. This is the introspection this bug fix hangs the fix on: forward
    "prompt" only for generators that actually declare it.

    Reuses `create_param_panels._introspect_generator_args` — the SAME
    "build a throwaway argparse.ArgumentParser, call the generator's own
    add_args against it" approach `artgen_bool_flags` already relies on for
    the FIX-3 bool-flag spelling — rather than duplicating that argparse
    walk here. Same fail-soft contract: an unknown/broken generator name (or
    any import/introspection failure) returns False, never raises — matching
    the existing behavior of skipping "prompt" for every generator this
    function doesn't recognize as accepting it.
    """
    try:
        from create_param_panels import _introspect_generator_args
        return any(
            spec.dest == "prompt" for spec in _introspect_generator_args(generator)
        )
    except Exception:
        return False


# Phase markers for parsing server log output.  Each entry is (substring, phase_label).
# Checked in order; the first match wins.  phase_label=None means no update (terminal state
# handled by the health check).
_PHASE_MARKERS: list[tuple[str, "str | None"]] = [
    ("Device 0,1,2,3: Loading model",       "Loading model"),
    ("Loading checkpoint shards",            "Loading weights"),
    ("loading cache at",                     "Loading compiled weights"),
    ("Device 0,1,2,3: Model loaded",         "Model loaded"),
    ("Submitted warmup task",                "Warming up"),
    ("Model warmup completed",               None),
    ("Application startup complete",         None),
]


def _detect_phase(line: str) -> "str | None | bool":
    """Return the phase label for a log line, or None if no match.

    Returns the string label to display, or False if the line matched but
    has no label (terminal state — let the health check handle it).
    """
    for marker, label in _PHASE_MARKERS:
        if marker in line:
            return label if label is not None else False
    return None


def _apply_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gtk.Widget.get_display(Gtk.Window()),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


def _load_pixbuf(path: str, width: int, height: int) -> Optional[GdkPixbuf.Pixbuf]:
    """Load an image scaled to fit within width×height, preserving aspect ratio.

    Uses new_from_file_at_scale(..., preserve_aspect_ratio=True) so the image is
    fit (letterboxed) inside the box rather than force-stretched to it. The old
    scale_simple(width, height) baked non-uniform distortion into the pixels for
    any source whose aspect ratio didn't match the box (e.g. square 1024×1024
    FLUX output in a 16:9 slot). Returns None on failure.
    """
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, width, height, True)
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
    Build gallery-card thumbnail CONTENT: a Gtk.Picture that letterboxes
    (aspect-preserving CONTAIN fit) into whatever area its container
    allocates, or a placeholder Label if no thumbnail exists.

    Regression note: this function used to ALSO call
    `pic.set_size_request(min_width, min_height)` as a hard MINIMUM. That
    only floors the widget's size — it does not cap it, so a Gtk.Picture's
    own natural size (computed from the source image's intrinsic aspect
    ratio when keep_aspect_ratio=True, the default) could still exceed that
    floor for non-16:9 content (e.g. a square 1024x1024 image naturally
    wants height == width, not the 16:9 floor), and Gtk.FlowBox
    (non-homogeneous) honors that larger natural size — this was the root
    cause of gallery cards growing/shrinking to match thumbnail aspect
    ratio.  The caller now wraps this widget in
    `gallery_layout.pin_fixed_zone(widget, THUMB_W, THUMB_H)`, which pins
    the OUTER container's measured size regardless of this widget's own
    natural size — so min_width/min_height are unused here and this widget
    itself must NOT set its own size_request (that would just reintroduce a
    (harmless, since it's overridden) but confusing floor).  Params are
    kept for call-site/signature compatibility.
    """
    if path and Path(path).exists():
        pic = Gtk.Picture.new_for_filename(path)
        pic.set_can_shrink(True)
        pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        return pic
    lbl = Gtk.Label(label=placeholder)
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
    from_attractor: bool = False     # True → enqueued by TT-TV auto-gen; purged on attractor close
    # SP-3a (decouple `_on_generate` from ControlPanel): captured at enqueue
    # time (from whichever ControlPanel getter the enqueuing call site already
    # had in hand) so `_start_next_queued` can replay the job without itself
    # reading `self._controls` — `model_id` alone isn't enough because it's
    # already a SHORT key (e.g. "wan2"), not the CANONICAL id that
    # `_VIDEO_MODEL_ID_TO_KEY`/`_IMAGE_MODEL_ID_TO_KEY` invert, so those maps
    # can't total-ly derive it back.
    video_model_key: "str | None" = None    # e.g. "wan2" | "mochi" | "skyreels" | "animatediff"
    image_model_key: "str | None" = None    # e.g. "flux" | "sdxl" | "z-image-turbo" | "motif"
    animatediff_args: "dict | None" = None  # snapshot of get_animatediff_args() when relevant


class _NativeGenerateGuardError(Exception):
    """Raised by `MainWindow._native_generate_args` when a native medium's
    collected params fail a hard pre-flight guard (currently only the
    SkyReels-I2V "no seed image" case) and neither `_on_generate` nor
    `_on_enqueue` should be called at all.

    Deliberately just a message-carrying exception rather than a return-None
    sentinel: `_create_generate_native` (the not-busy path) and
    `_create_enqueue_native` (SP-3c-4's busy-path enqueue) need to react
    DIFFERENTLY to the same guard failure — the former is the active job
    itself, so it clears `_create_job_active` via `_fail_create_job`; the
    latter is a REJECTED enqueue attempt for a job that never became active,
    so it must leave the currently-running job's flag alone and only show a
    status message. Keeping the guard logic itself in one place
    (`_native_generate_args`) means both call sites can't drift out of sync
    on what "invalid" means.
    """


# ── Forge plugin transform helpers ────────────────────────────────────────────

_TRANSFORM_AVAIL: "dict[str, bool]" = {}


def _transform_available(key: str) -> bool:
    """Return True if the named plugin is installed and its deps are available.

    Result is cached after first call so repeated right-clicks are fast.
    """
    if key not in _TRANSFORM_AVAIL:
        try:
            import importlib.util as _ilu
            _p = Path(__file__).parent.parent / "plugins" / key / "plugin.py"
            spec = _ilu.spec_from_file_location(f"ttlg_transform_{key}", _p)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _TRANSFORM_AVAIL[key] = getattr(mod, "is_available", lambda: False)()
        except Exception:
            _TRANSFORM_AVAIL[key] = False
    return _TRANSFORM_AVAIL[key]


def _make_thumbnail_for(image_path: str, thumb_path: str) -> None:
    """Create a 200×112 JPEG thumbnail via ffmpeg.  Mirrors worker.py._make_thumbnail."""
    Path(thumb_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", image_path,
                "-vf", "scale=200:112:force_original_aspect_ratio=decrease,"
                       "pad=200:112:(ow-iw)/2:(oh-ih)/2",
                "-q:v", "3", thumb_path,
            ],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
        )
    except Exception:
        try:
            shutil.copy2(image_path, thumb_path)
        except Exception:
            pass


# ── Generation card ────────────────────────────────────────────────────────────

class GenerationCard(Gtk.Box):
    """
    Thumbnail card in the gallery. Click anywhere on the card to select it and
    show full details in the DetailPanel.
    Buttons: 💾 Save, 🔀 Remix, 🗑 Delete.
    Hover reveals: 🔀 Remix button, 🧩 Remix as pipeline button, ☆/★ star toggle.
    select_cb(self) is called when the card is clicked.
    delete_cb(record) is called when the trash button is clicked.
    remix_cb(record) is called when the Remix button is clicked (opens RemixPopover).
    remix_as_pipeline_cb(record) is called when "Remix as pipeline…" is clicked
      (opens Pipeline Studio's Muse scoped to this card's artifact).
    star_cb(record, starred: bool) is called when the star is toggled.

    Base class is `Gtk.Box`, not `Gtk.Frame` (pre-density-fix history): a
    plain Box has NO intrinsic themed border/chrome of its own (the ".card"
    CSS class supplies border/radius/padding explicitly), whereas Gtk.Frame
    draws its OWN ~1px border from GTK's built-in fallback stylesheet even
    before any custom CSS loads. That extra, undeclared chrome sat on top of
    whatever `self._card_zone` (see below) pinned the content to, so the
    card's OWN measured size could never be pinned to an EXACT value -- only
    to "pinned content size + a couple of untracked chrome pixels". Since
    switching gallery density needs the card's measured size to land on
    `gallery_layout.tile_size(density)` exactly (verified in tests via
    `.measure()`), the base class had to lose that hidden offset.
    """

    def __init__(self, record: GenerationRecord, select_cb, delete_cb,
                 remix_cb=None, star_cb=None, transform_cb=None,
                 remix_as_pipeline_cb=None):
        super().__init__()
        self._record = record
        self._select_cb = select_cb
        self._delete_cb = delete_cb
        self._remix_cb = remix_cb           # callable(record) or None — opens RemixPopover
        self._remix_as_pipeline_cb = remix_as_pipeline_cb  # callable(record) or None — opens scoped Muse
        self._star_cb = star_cb             # callable(record, starred: bool) or None
        self._transform_cb = transform_cb   # callable(record, key: str) or None — forge transforms
        self._ctx_pop: "Gtk.Popover | None" = None   # only one right-click popover at a time
        self.add_css_class("card")
        # FIXED card tile size — BOTH dimensions, shared with the artgen
        # gallery via gallery_layout.tile_size() — so every media-entry box
        # is identical across every Discover tab and never resizes to match
        # thumbnail content (see gallery_layout.py + _build's media zone,
        # which pins the thumbnail area's measured size the same way).
        #
        # Resolved from the LIVE density setting at construction time (not
        # the hardcoded comfortable constants) so a freshly-built card
        # honors "compact" immediately if that's already the active density
        # — previously every new GenerationCard was born at comfortable size
        # regardless of the saved preference, only fixed up for
        # ALREADY-BUILT cards (and even that was broken — see
        # gallery_layout.set_pinned_size's docstring).
        density = _gallery_density()
        self._tile_w, self._tile_h = gallery_layout.tile_size(density)
        self._thumb_w, self._thumb_h = gallery_layout.thumb_size(density)
        self.set_size_request(self._tile_w, self._tile_h)
        self.set_hexpand(True)
        self._build()

        # Clicking anywhere on the card selects it in the detail panel; a
        # double click ALSO opens the record full-screen (see _on_pressed).
        gesture = Gtk.GestureClick()
        gesture.connect("pressed", self._on_pressed)
        self.add_controller(gesture)

        # Right-click: forge transform menu (remove background, describe, show depth…)
        rclick = Gtk.GestureClick()
        rclick.set_button(3)
        rclick.connect("pressed", self._on_right_click)
        self.add_controller(rclick)

        # Hover controller: reveals action bar (star, animate) on all card types;
        # also starts video preview on video/animate cards.
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_hover_enter)
        motion.connect("leave", self._on_hover_leave)
        self.add_controller(motion)

    def _on_pressed(self, _gesture, n_press: int, _x: float, _y: float) -> None:
        """Primary-button press handler for the card's main GestureClick.

        Single click (n_press == 1): select the card into the DetailPanel --
        unchanged from the previous plain `lambda *_: self._select_cb(self)`.

        Double click (n_press == 2): ALSO open the record full-screen in its
        own window, mirroring DetailPanel's "⛶ View Full"/"⛶ Fullscreen"
        buttons (`_open_fullscreen` / `_open_image_fullscreen`) but reachable
        directly from the gallery card -- no round-trip through the right
        pane required. `_select_cb` still fires on every press (including
        both presses of a double-click); that's harmless since the first
        press already populates the pane with this same record.

        Guards mirror the DetailPanel methods exactly: images need
        `image_exists`, everything else (video/animate/animatediff) needs
        `video_exists`. `VideoPlayerWindow`/`ImageViewerWindow` are the same
        module-level classes DetailPanel uses.
        """
        self._select_cb(self)
        if n_press != 2:
            return
        record = self._record
        if record.media_type == "image":
            if record.image_exists:
                win = ImageViewerWindow(record, self.get_root())
                win.present()
        elif record.video_exists:
            win = VideoPlayerWindow(record, self.get_root())
            win.present()

    def _on_right_click(self, gesture, n_press: int, x: float, y: float) -> None:
        """Build and show a forge-transform popover anchored to the click position."""
        if not self._transform_cb:
            return

        # All available transforms: (plugin_key, intent → result label)
        all_transforms = [
            ("rmbg",       "Remove background  →  transparent PNG"),
            ("blip",       "Describe this  →  text caption"),
            ("depth",      "Show depth  →  depth map"),
            ("ansi-image", "Convert to ANSI art  →  .ans"),
        ]
        available = [(k, lbl) for k, lbl in all_transforms if _transform_available(k)]
        if not available:
            return  # no plugins available — suppress empty menu

        # Dismiss any popover left open from a previous right-click on this card.
        if self._ctx_pop is not None:
            self._ctx_pop.popdown()
            self._ctx_pop = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(4)
        box.set_margin_end(4)

        pop = Gtk.Popover()
        self._ctx_pop = pop

        for key, label in available:
            btn = Gtk.Button(label=label)
            btn.add_css_class("flat")
            btn.set_hexpand(True)

            def _on_clicked(_, k=key, p=pop):
                p.popdown()
                self._transform_cb(self._record, k)

            btn.connect("clicked", _on_clicked)
            box.append(btn)

        pop.set_child(box)
        pop.set_parent(self)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        pop.set_pointing_to(rect)
        pop.popup()

    def set_selected(self, selected: bool) -> None:
        # Image cards use a pink selection border; video cards use teal.
        css_class = ("card-selected-image"
                     if self._record.media_type == "image"
                     else "card-selected")
        if selected:
            self.add_css_class(css_class)
        else:
            self.remove_css_class(css_class)

    def set_selection_visible(self, visible: bool) -> None:
        """Show or hide the selection checkbox overlay."""
        self._check.set_visible(visible)

    def is_checked(self) -> bool:
        """Return True if the selection checkbox is checked."""
        return self._check.get_active()

    def set_checked(self, checked: bool) -> None:
        """Programmatically set the checkbox state."""
        self._check.set_active(checked)

    def _build(self) -> None:
        # Wrap the card content in a Gtk.Overlay so the selection checkbox
        # can float in the top-left corner without affecting the card layout.
        overlay = Gtk.Overlay()
        self.append(overlay)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        # Pin the WHOLE card content's measured size to exactly
        # (self._tile_w, self._tile_h), the same trick _media_zone below
        # uses for the thumbnail -- otherwise the outer widget's
        # set_size_request() above is only a MINIMUM floor, and the prompt/
        # meta/button rows' own natural sizes (which don't shrink with
        # density) can exceed a smaller "compact" floor, leaving the card
        # visibly larger than tile_size("compact") despite the floor being
        # set correctly. Stored as self._card_zone so _apply_gallery_density
        # can resize an ALREADY-BUILT card in place via
        # gallery_layout.set_pinned_size() (see its docstring for why
        # set_size_request() alone can't do this).
        self._card_zone = gallery_layout.pin_fixed_zone(box, self._tile_w, self._tile_h)
        overlay.set_child(self._card_zone)

        # Checkbox overlay: hidden until selection mode is activated.
        # Positioned top-left; pointer events are swallowed by the checkbox so
        # clicks on it don't bubble up to the card's GestureClick.
        self._check = Gtk.CheckButton()
        self._check.add_css_class("card-check")
        self._check.set_halign(Gtk.Align.START)
        self._check.set_valign(Gtk.Align.START)
        self._check.set_visible(False)
        overlay.add_overlay(self._check)

        # ── Hover action bar ─────────────────────────────────────────────────
        # Gtk.Revealer(SLIDE_UP) overlaid at the bottom of the card thumbnail.
        # Only added to the overlay when at least one action callback is present.
        self._action_revealer = Gtk.Revealer()
        self._action_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_UP
        )
        self._action_revealer.set_transition_duration(150)
        self._action_revealer.set_valign(Gtk.Align.END)
        self._action_revealer.set_halign(Gtk.Align.FILL)
        self._action_revealer.set_reveal_child(False)

        action_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        action_bar.add_css_class("hover-action-bar")
        action_bar.set_hexpand(True)

        # Remix button — always present so any card can be remixed into a new generation.
        remix_btn = Gtk.Button(label="🔀 Remix")
        remix_btn.add_css_class("hover-action-btn")
        remix_btn.add_css_class("hover-action-btn-remix")
        remix_btn.set_can_focus(False)
        remix_btn.set_tooltip_text("Remix this into a new generation")
        remix_btn.connect("clicked", self._on_remix_clicked)
        action_bar.append(remix_btn)

        # Remix-as-pipeline button — always present, parallel to 🔀 Remix above,
        # so any card can seed a multi-step Pipeline Studio remix.
        self._remix_as_pipeline_btn = Gtk.Button(label="🧩 Remix as pipeline…")
        self._remix_as_pipeline_btn.add_css_class("hover-action-btn")
        self._remix_as_pipeline_btn.set_can_focus(False)
        self._remix_as_pipeline_btn.set_tooltip_text(
            "Remix this into a multi-step pipeline"
        )
        self._remix_as_pipeline_btn.connect("clicked", self._on_remix_as_pipeline_clicked)
        action_bar.append(self._remix_as_pipeline_btn)

        # Star toggle — always present so every card type can be starred.
        self._star_btn = Gtk.Button(label="★" if self._record.starred else "☆")
        self._star_btn.add_css_class("hover-action-btn")
        self._star_btn.add_css_class("gen-star-btn")
        self._star_btn.set_can_focus(False)
        self._star_btn.set_tooltip_text("Unstar" if self._record.starred else "Star")
        self._star_btn.connect("clicked", self._on_star_clicked)
        action_bar.append(self._star_btn)

        self._action_revealer.set_child(action_bar)
        overlay.add_overlay(self._action_revealer)

        # Media area: thumbnail normally; hover swaps in a silent looping video preview.
        # The stack expands horizontally so the thumbnail fills the FlowBox cell width.
        self._media_stack = Gtk.Stack()
        self._media_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._media_stack.set_transition_duration(120)
        self._media_stack.set_hexpand(True)

        placeholder = "🖼" if self._record.media_type == "image" else "🎬"
        thumb = _make_scalable_thumb(
            self._record.thumbnail_path if self._record.thumbnail_exists else "",
            self._thumb_w, self._thumb_h, placeholder,
        )
        self._media_stack.add_named(thumb, "thumb")

        # GIFs (AnimateDiff) use GdkPixbufAnimationIter instead of GStreamer —
        # GStreamer's gifparse plugin does not support reliable seeking so the
        # notify::ended → seek(0) loop that works for MP4s fails silently for GIFs.
        _is_gif = (
            self._record.media_type == "animatediff"
            or self._record.video_path.endswith(".gif")
        )
        self._gif_pic: "Gtk.Picture | None" = None
        self._gif_iter: "GdkPixbuf.PixbufAnimationIter | None" = None
        self._gif_timer_id: "int | None" = None

        if _is_gif and self._record.video_exists:
            # Use a GdkPixbufAnimationIter-driven Gtk.Picture so the GIF loops
            # correctly without any GStreamer pipeline.
            self._gif_pic = Gtk.Picture()
            self._gif_pic.set_hexpand(True)
            self._gif_pic.set_size_request(self._thumb_w, self._thumb_h)
            self._gif_pic.set_content_fit(Gtk.ContentFit.COVER)
            self._media_stack.add_named(self._gif_pic, "video")
            self._hover_video = None
            self._hover_gst = None
        elif self._record.video_exists and not _USE_SYSTEM_PLAYER:
            # Linux: Create Gtk.Video without a file so no GStreamer pipeline is
            # opened at construction time.  With a large history every card would
            # eagerly open a pipeline, holding several file-descriptors each.
            # We load lazily (just before first play) and unload (set_file(None))
            # when done, so only actively-playing cards hold fds.
            self._hover_video = Gtk.Video()
            self._hover_video.set_autoplay(False)
            self._hover_video.set_loop(True)
            self._hover_video.set_hexpand(True)
            self._hover_video.set_size_request(self._thumb_w, self._thumb_h)
            self._media_stack.add_named(self._hover_video, "video")
            self._hover_gst = None   # macOS-only; always None on Linux
        elif self._record.video_exists:
            # macOS: GTK4 Homebrew bottle lacks libmedia-gstreamer.dylib; use
            # gtk4paintablesink (GstPlayer) to render inline video into a Gtk.Picture
            # without needing to recompile GTK4.
            self._hover_gst = GstPlayer(muted=True)
            self._hover_gst.widget.set_hexpand(True)
            self._hover_gst.widget.set_size_request(self._thumb_w, self._thumb_h)
            self._media_stack.add_named(self._hover_gst.widget, "video")
            self._hover_video = None
        else:
            self._hover_video = None
            self._hover_gst = None
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

        # Pin the media zone's MEASURED size to a fixed self._thumb_w x
        # self._thumb_h, regardless of the thumbnail's / hover-video's own
        # aspect ratio — this is the fix for cards growing/shrinking to
        # match content (a square image otherwise requests height == width,
        # taller than the 16:9 floor, and Gtk.FlowBox honors that larger
        # natural request). See gallery_layout.pin_fixed_zone for the
        # mechanism. Stored as self._media_zone so tests can assert its
        # measured size directly, and so _apply_gallery_density can resize
        # it in place via gallery_layout.set_pinned_size() when density
        # changes after this card is already built.
        self._media_zone = gallery_layout.pin_fixed_zone(
            self._media_stack, self._thumb_w, self._thumb_h
        )
        box.append(self._media_zone)

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
        is_gif = self._record.media_type == "animatediff" or (
            self._record.video_path.endswith(".gif")
        )
        export_label = "💾 Save GIF" if is_gif else (
            "💾 Save" if self._record.media_type == "image" else "💾 Save"
        )
        export_btn = Gtk.Button(label=export_label)
        tip = "Export image to a chosen location" if self._record.media_type == "image" else "Export video to a chosen location"
        export_btn.set_tooltip_text(tip)
        export_btn.connect("clicked", self._export)
        if not self._record.media_exists:
            export_btn.set_sensitive(False)
        btn_row.append(export_btn)

        # GIF↔MP4 conversion button — only for video/animatediff records.
        if self._record.media_type not in ("image", "artgen"):
            if is_gif:
                conv_btn = Gtk.Button(label="→ MP4")
                conv_btn.set_tooltip_text("Convert this GIF to an MP4 video file")
                conv_btn.connect("clicked", self._convert_to_mp4)
            else:
                conv_btn = Gtk.Button(label="→ GIF")
                conv_btn.set_tooltip_text("Convert this video to an animated GIF")
                conv_btn.connect("clicked", self._convert_to_gif)
            if not self._record.media_exists:
                conv_btn.set_sensitive(False)
            btn_row.append(conv_btn)

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

    def _on_star_clicked(self, _btn) -> None:
        """Toggle the starred state and fire star_cb if provided."""
        new_starred = not bool(self._record.starred)
        self._record.starred = int(new_starred)
        self._star_btn.set_label("★" if new_starred else "☆")
        self._star_btn.set_tooltip_text("Unstar" if new_starred else "Star")
        if self._star_cb:
            self._star_cb(self._record, new_starred)

    def _on_hover_enter(self, _ctrl, _x, _y) -> None:
        """Start looping the video silently when the mouse enters the card.
        Also reveals the hover action bar."""
        self._action_revealer.set_reveal_child(True)
        # GIF path: GdkPixbufAnimationIter (no GStreamer needed)
        if self._gif_pic is not None:
            self._start_gif_animation()
            self._media_stack.set_visible_child_name("video")
            return
        if _USE_SYSTEM_PLAYER:
            # macOS: load and play via GstPlayer (gtk4paintablesink → Gtk.Picture)
            gst = getattr(self, "_hover_gst", None)
            if gst is None or not gst.available:
                return
            gst.load(self._record.video_path)
            gst.set_on_eos(self._on_gst_eos)
            gst.play()
            self._media_stack.set_visible_child_name("video")
            return
        # Linux: Gtk.Video path
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

    def _on_gst_eos(self) -> None:
        """Loop the GstPlayer hover video when it reaches end-of-stream (macOS)."""
        gst = getattr(self, "_hover_gst", None)
        if gst is None:
            return
        gst.seek(0)
        gst.play()

    def _on_hover_leave(self, _ctrl) -> None:
        """Stop the video and revert to the thumbnail when the mouse leaves.
        Also hides the hover action bar."""
        self._action_revealer.set_reveal_child(False)
        # GIF path
        if self._gif_pic is not None:
            self._stop_gif_animation()
            self._media_stack.set_visible_child_name("thumb")
            return
        if _USE_SYSTEM_PLAYER:
            # macOS: tear down the GstPlayer pipeline to release file-descriptors
            gst = getattr(self, "_hover_gst", None)
            if gst is not None:
                gst.close()
            self._media_stack.set_visible_child_name("thumb")
            return
        # Linux: Gtk.Video path
        if self._hover_video is None:
            return
        self._close_hover_pipeline()
        self._media_stack.set_visible_child_name("thumb")

    def _start_gif_animation(self) -> None:
        """Start GdkPixbufAnimationIter loop on self._gif_pic. Idempotent."""
        if self._gif_pic is None:
            return
        self._stop_gif_animation()
        try:
            anim = GdkPixbuf.PixbufAnimation.new_from_file(self._record.video_path)
        except Exception:
            return
        if anim.is_static_image():
            self._gif_pic.set_paintable(
                Gdk.Texture.new_for_pixbuf(anim.get_static_image())
            )
            return
        it = anim.get_iter(None)
        self._gif_iter = it

        def _tick() -> bool:
            if self._gif_pic is None or self._gif_iter is not it:
                # Animation was stopped or replaced — don't reschedule.
                return GLib.SOURCE_REMOVE
            it.advance(None)
            pb = it.get_pixbuf()
            if pb is not None:
                self._gif_pic.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
            delay = it.get_delay_time()
            if delay < 0:
                self._gif_timer_id = None
                return GLib.SOURCE_REMOVE
            self._gif_timer_id = GLib.timeout_add(max(delay, 10), _tick)
            return GLib.SOURCE_REMOVE

        pb = it.get_pixbuf()
        if pb is not None:
            self._gif_pic.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
        delay = max(it.get_delay_time(), 10)
        self._gif_timer_id = GLib.timeout_add(delay, _tick)

    def _stop_gif_animation(self) -> None:
        """Cancel the GdkPixbufAnimationIter timer for this card."""
        if self._gif_timer_id is not None:
            GLib.source_remove(self._gif_timer_id)
            self._gif_timer_id = None
        self._gif_iter = None

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

    def _convert_to_mp4(self, _btn) -> None:
        """Convert the current GIF record to an MP4 via ffmpeg in a background thread."""
        if not self._record.media_exists:
            return
        src = self._record.media_file_path
        import tempfile
        tmp = tempfile.mktemp(suffix=".mp4")
        def _worker():
            subprocess.run(
                ["ffmpeg", "-y", "-i", src,
                 "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
            )
            GLib.idle_add(self._offer_converted_save, tmp, ".mp4")
        threading.Thread(target=_worker, daemon=True).start()

    def _convert_to_gif(self, _btn) -> None:
        """Convert the current video to an animated GIF via two-pass ffmpeg."""
        if not self._record.media_exists:
            return
        src = self._record.media_file_path
        import tempfile
        palette = tempfile.mktemp(suffix=".png")
        tmp = tempfile.mktemp(suffix=".gif")
        def _worker():
            subprocess.run(
                ["ffmpeg", "-y", "-i", src,
                 "-vf", "fps=12,scale=480:-1:flags=lanczos,palettegen", palette],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
            )
            subprocess.run(
                ["ffmpeg", "-y", "-i", src, "-i", palette,
                 "-vf", "fps=12,scale=480:-1:flags=lanczos,paletteuse", tmp],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
            )
            GLib.idle_add(self._offer_converted_save, tmp, ".gif")
        threading.Thread(target=_worker, daemon=True).start()

    def _offer_converted_save(self, tmp_path: str, suffix: str) -> None:
        """Open a save dialog for the converted file after ffmpeg completes."""
        if not Path(tmp_path).exists():
            return
        dlg = Gtk.FileDialog()
        stem = Path(self._record.media_file_path).stem
        dlg.set_initial_name(stem + suffix)
        dlg.save(self.get_root(), None,
                 lambda d, r, p=tmp_path: self._save_converted(d, r, p))

    def _save_converted(self, dlg, result, tmp_path: str) -> None:
        """Move the converted temp file to the user-chosen destination."""
        try:
            dest = dlg.save_finish(result).get_path()
        except Exception:
            return
        if dest:
            shutil.move(tmp_path, dest)

    def _on_remix_clicked(self, _btn) -> None:
        """Open the RemixPopover for this card when the 🔀 Remix button is clicked."""
        if self._remix_cb:
            self._remix_cb(self._record)

    def _on_remix_as_pipeline_clicked(self, _btn) -> None:
        """Open Pipeline Studio's Muse for this card when "🧩 Remix as pipeline…" is clicked."""
        if self._remix_as_pipeline_cb:
            self._remix_as_pipeline_cb(self._record)

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

    def __init__(self, download_cb=None, on_localized_cb=None, star_cb=None):
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_vexpand(True)
        self.set_hexpand(False)
        self.set_size_request(420, -1)
        self._record: Optional[GenerationRecord] = None
        self._remix_cb = None
        self._remix_as_pipeline_cb = None
        self._video_widget: Optional[Gtk.Video] = None
        # macOS inline player via gtk4paintablesink; always None on Linux
        self._gst_player = None
        self._play_btn: Optional[Gtk.Button] = None
        # GIF animation state (used when record is an AnimateDiff .gif)
        self._detail_gif_timer_id: "int | None" = None
        self._detail_gif_iter: "GdkPixbuf.PixbufAnimationIter | None" = None
        self._detail_gif_pic: "Gtk.Picture | None" = None
        self._detail_gif_paused: bool = False
        self._detail_star_btn: Optional[Gtk.Button] = None
        self._nav_records: list = []   # GenerationRecords in current filter order
        self._nav_idx: int = 0
        self._nav_prev_btn: Optional[Gtk.Button] = None
        self._nav_next_btn: Optional[Gtk.Button] = None
        # Callable(record_id: str, dest_path: Path) → None — injected by MainWindow.
        # When provided, a "Download from server" button appears for missing videos.
        self._download_cb = download_cb
        # Callable(localized_record: GenerationRecord) → None — injected by MainWindow.
        # Called (on main thread via GLib.idle_add) after a remote video is downloaded
        # to local storage, so MainWindow can add it to HistoryStore and refresh gallery.
        self._on_localized_cb = on_localized_cb
        # Callable(record: GenerationRecord, starred: bool) → None
        self._star_cb = star_cb
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
        # macOS: tear down the GstPlayer pipeline if one was created for the
        # previous record.  This releases the file-descriptor early instead of
        # waiting for widget destruction.
        if self._gst_player is not None:
            self._gst_player.close()
            self._gst_player = None
        self._play_btn = None
        if self._detail_gif_timer_id is not None:
            GLib.source_remove(self._detail_gif_timer_id)
            self._detail_gif_timer_id = None
        self._detail_gif_iter = None
        self._detail_gif_pic = None
        self._show_empty()

    def show_record(self, record: GenerationRecord, remix_cb, remix_as_pipeline_cb=None) -> None:
        """Populate the panel with a completed generation record."""
        self._record = record
        self._remix_cb = remix_cb
        self._remix_as_pipeline_cb = remix_as_pipeline_cb

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
        # macOS: close the previous GstPlayer pipeline before building a new one
        if self._gst_player is not None:
            self._gst_player.close()
            self._gst_player = None
        self._play_btn = None
        # Cancel any running GIF animation timer
        if self._detail_gif_timer_id is not None:
            GLib.source_remove(self._detail_gif_timer_id)
            self._detail_gif_timer_id = None
        self._detail_gif_iter = None
        self._detail_gif_pic = None
        self._detail_gif_paused = False

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        # ── Prev / Next navigation ─────────────────────────────────────────────
        nav_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        nav_row.set_margin_bottom(4)

        self._nav_prev_btn = Gtk.Button(label="‹")
        self._nav_prev_btn.add_css_class("flat")
        self._nav_prev_btn.add_css_class("detail-nav-btn")
        self._nav_prev_btn.set_tooltip_text("Previous  [←]")
        self._nav_prev_btn.connect("clicked", lambda _: self._step(-1))
        nav_row.append(self._nav_prev_btn)

        self._nav_next_btn = Gtk.Button(label="›")
        self._nav_next_btn.add_css_class("flat")
        self._nav_next_btn.add_css_class("detail-nav-btn")
        self._nav_next_btn.set_tooltip_text("Next  [→]")
        self._nav_next_btn.connect("clicked", lambda _: self._step(1))
        nav_row.append(self._nav_next_btn)

        n = len(self._nav_records)
        if n > 1:
            pos_lbl = Gtk.Label(label=f"{self._nav_idx + 1} / {n}")
            pos_lbl.add_css_class("muted")
            pos_lbl.set_margin_start(4)
            nav_row.append(pos_lbl)

        # Hide nav when there is only one item in the context.
        nav_row.set_visible(n > 1)
        self._nav_prev_btn.set_sensitive(n > 1)
        self._nav_next_btn.set_sensitive(n > 1)
        content.append(nav_row)

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
            _is_gif = (
                record.media_type == "animatediff"
                or record.video_path.endswith(".gif")
            )
            if _is_gif:
                # GIF: drive via GdkPixbufAnimationIter (GStreamer seek unreliable)
                self._detail_gif_pic = Gtk.Picture()
                self._detail_gif_pic.set_content_fit(Gtk.ContentFit.CONTAIN)
                self._detail_gif_pic.set_size_request(_DETAIL_VIDEO_W, _DETAIL_VIDEO_H)
                self._detail_gif_pic.set_halign(Gtk.Align.START)
                content.append(self._detail_gif_pic)
                # Start animation immediately
                try:
                    anim = GdkPixbuf.PixbufAnimation.new_from_file(record.video_path)
                except Exception:
                    anim = None
                # Single tick function — self-reschedules; reads self._detail_gif_iter
                # directly so pause/resume can reuse it without a second closure.
                def _gif_tick() -> bool:
                    if self._detail_gif_paused or self._detail_gif_iter is None:
                        self._detail_gif_timer_id = None
                        return GLib.SOURCE_REMOVE
                    self._detail_gif_iter.advance(None)
                    pb = self._detail_gif_iter.get_pixbuf()
                    if pb is not None and self._detail_gif_pic is not None:
                        self._detail_gif_pic.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
                    delay = self._detail_gif_iter.get_delay_time()
                    if delay < 0:
                        self._detail_gif_timer_id = None
                        return GLib.SOURCE_REMOVE
                    self._detail_gif_timer_id = GLib.timeout_add(max(delay, 10), _gif_tick)
                    return GLib.SOURCE_REMOVE

                def _start_gif_tick() -> None:
                    """Schedule _gif_tick if not already running."""
                    if self._detail_gif_iter is None or self._detail_gif_timer_id is not None:
                        return
                    delay = max(self._detail_gif_iter.get_delay_time(), 10)
                    self._detail_gif_timer_id = GLib.timeout_add(delay, _gif_tick)

                if anim and not anim.is_static_image():
                    it = anim.get_iter(None)
                    self._detail_gif_iter = it
                    pb = it.get_pixbuf()
                    if pb:
                        self._detail_gif_pic.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
                    _start_gif_tick()
                elif anim:
                    # Static GIF (single frame)
                    self._detail_gif_pic.set_paintable(
                        Gdk.Texture.new_for_pixbuf(anim.get_static_image())
                    )
                # Controls: pause/resume + external open
                ctrl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                self._play_btn = Gtk.Button(label="⏸ Pause")

                def _toggle_gif_anim(_btn):
                    self._detail_gif_paused = not self._detail_gif_paused
                    if self._detail_gif_paused:
                        self._play_btn.set_label("▶ Resume")
                    else:
                        self._play_btn.set_label("⏸ Pause")
                        _start_gif_tick()

                self._play_btn.connect("clicked", _toggle_gif_anim)
                ctrl_row.append(self._play_btn)
                ext_btn = Gtk.Button(label="⧉ Open externally")
                ext_btn.set_tooltip_text("Open the GIF in the system default viewer")
                ext_btn.connect("clicked", self._open_external)
                ctrl_row.append(ext_btn)
                content.append(ctrl_row)
            elif _USE_SYSTEM_PLAYER:
                # macOS: GTK4 Homebrew bottle lacks libmedia-gstreamer.dylib so
                # Gtk.Video shows a blank frame.  Use GstPlayer (gtk4paintablesink
                # → Gtk.Picture) for true inline video without a GTK4 recompile.
                self._gst_player = GstPlayer(muted=False)
                if self._gst_player.available:
                    self._gst_player.widget.set_size_request(_DETAIL_VIDEO_W, _DETAIL_VIDEO_H)
                    self._gst_player.widget.set_halign(Gtk.Align.START)
                    content.append(self._gst_player.widget)
                    self._gst_player.load(record.video_path)
                    # Auto-play immediately — matches hover behaviour.
                    # The EOS callback loops seamlessly; the play button starts
                    # as "⏸ Pause" to reflect that playback is already running.
                    self._gst_player.play()
                    # Re-use a closure that captures the player reference so EOS
                    # looping still works if the panel is rebuilt (self._gst_player
                    # may be replaced before the callback fires).
                    _player_ref = self._gst_player
                    _play_btn_ref = [None]   # filled in below after button is created
                    def _on_detail_eos(p=_player_ref, btn=_play_btn_ref):
                        p.seek(0)
                        p.play()
                        # Keep button label in sync after EOS-triggered restart
                        if btn[0] is not None:
                            btn[0].set_label("⏸ Pause")
                    self._gst_player.set_on_eos(_on_detail_eos)
                    ctrl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                    self._play_btn = Gtk.Button(label="⏸ Pause")
                    _play_btn_ref[0] = self._play_btn
                    self._play_btn.connect("clicked", self._toggle_play)
                    ctrl_row.append(self._play_btn)
                    ext_btn = Gtk.Button(label="⧉ Open externally")
                    ext_btn.set_tooltip_text(
                        "Open the video in QuickTime or the system default player"
                    )
                    ext_btn.connect("clicked", self._open_external)
                    ctrl_row.append(ext_btn)
                    content.append(ctrl_row)
                else:
                    # gtk4paintablesink not available — fall back to static
                    # thumbnail with a button to open in QuickTime.
                    if record.thumbnail_exists:
                        thumb_widget = _make_image_widget(
                            record.thumbnail_path, _DETAIL_VIDEO_W, _DETAIL_VIDEO_H
                        )
                    else:
                        thumb_widget = _make_image_widget(
                            "", _DETAIL_VIDEO_W, _DETAIL_VIDEO_H, "🎬"
                        )
                    thumb_widget.set_halign(Gtk.Align.START)
                    content.append(thumb_widget)
                    ctrl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                    ext_btn = Gtk.Button(label="▶ Open in QuickTime")
                    ext_btn.set_tooltip_text(
                        "Open the video in the system default player (QuickTime)"
                    )
                    ext_btn.connect("clicked", self._open_external)
                    ctrl_row.append(ext_btn)
                    content.append(ctrl_row)
            else:
                # Linux (or macOS with GStreamer backend): inline Gtk.Video player
                self._video_widget = Gtk.Video.new_for_filename(record.video_path)
                self._video_widget.set_autoplay(False)
                self._video_widget.set_loop(True)
                self._video_widget.set_size_request(_DETAIL_VIDEO_W, _DETAIL_VIDEO_H)
                self._video_widget.set_hexpand(False)
                self._video_widget.set_halign(Gtk.Align.START)
                content.append(self._video_widget)

                # Wire GStreamer error reporting.  The media stream is created lazily
                # (None until the Gtk.Video widget is realised), so we retry until
                # it's available.  This surfaces codec / backend errors in the
                # terminal (stderr) rather than silently showing a blank frame.
                _video_ref = self._video_widget

                def _connect_stream_error(play_btn_ref=self._play_btn if hasattr(self, '_play_btn') else None):
                    if _video_ref is None or _video_ref.get_parent() is None:
                        return False  # widget was replaced — stop retry
                    stream = _video_ref.get_media_stream()
                    if stream is None:
                        return True  # not realised yet — retry in 200 ms
                    def _on_stream_error(s, _param):
                        err = s.get_error()
                        if err:
                            import logging as _log
                            _log.getLogger(__name__).warning(
                                "GTK media stream error (path=%s): %s",
                                record.video_path, err.message,
                            )
                            print(
                                f"[GTK video] GStreamer error: {err.message}\n"
                                f"  path: {record.video_path}\n"
                                f"  hint: check GST_PLUGIN_PATH / install gst-libav",
                                file=__import__('sys').stderr,
                            )
                    stream.connect("notify::error", _on_stream_error)
                    return False  # connected — stop retry

                GLib.timeout_add(200, _connect_stream_error)

                ctrl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                self._play_btn = Gtk.Button(label="▶ Play")
                self._play_btn.connect("clicked", self._toggle_play)
                ctrl_row.append(self._play_btn)
                full_btn = Gtk.Button(label="⛶ Fullscreen")
                full_btn.set_tooltip_text("Open in maximized window (F for true fullscreen)")
                full_btn.connect("clicked", self._open_fullscreen)
                ctrl_row.append(full_btn)
                ext_btn = Gtk.Button(label="⧉ Open externally")
                ext_btn.set_tooltip_text(
                    "Open the video in the system default player (e.g. totem/mpv on Linux)"
                )
                ext_btn.connect("clicked", self._open_external)
                ctrl_row.append(ext_btn)
                content.append(ctrl_row)
        else:
            # Video file missing — show large thumbnail or placeholder, and
            # offer a download button if there is any download source available.
            if record.thumbnail_exists:
                thumb = _make_image_widget(record.thumbnail_path, _DETAIL_VIDEO_W, _DETAIL_VIDEO_H)
            else:
                thumb = _make_image_widget("", _DETAIL_VIDEO_W, _DETAIL_VIDEO_H, "🎬\n(video not cached)")
            content.append(thumb)
            # Show download button when: inventory URL present (remote record)
            # OR inference-server download callback available with a job ID.
            inv_url = record.extra_meta.get("_inventory_video_url", "") if record.extra_meta else ""
            has_download = bool(inv_url) or (self._download_cb and record.id)
            if has_download:
                dl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                if inv_url:
                    label = "⬇ Download from remote library"
                    tip   = "Download this video from the remote inventory server and cache it locally"
                else:
                    label = "⬇ Download from server"
                    tip   = "Download this video from the inference server and cache it locally"
                dl_btn = Gtk.Button(label=label)
                dl_btn.set_tooltip_text(tip)
                dl_btn.connect("clicked", self._on_download_video)
                dl_row.append(dl_btn)
                content.append(dl_row)

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

        # Format date — UTC stored, display in local 12-hour time
        from time_utils import fmt_local_12h
        date_str = fmt_local_12h(record.created_at) if record.created_at else "—"

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
        for k, v in (record.extra_meta or {}).items():
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

        # ── Playlists membership ──────────────────────────────────────────────
        # Show every playlist as a checkbox. Checking/unchecking adds or removes
        # this record from the playlist immediately, without any extra Save step.
        from playlist_store import playlist_store as _ps
        all_playlists = _ps.all()
        if all_playlists:
            content.append(self._detail_section("Playlists"))
            pl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            pl_box.set_margin_top(2)
            pl_box.set_margin_bottom(2)
            for pl in all_playlists:
                cb = Gtk.CheckButton(label=pl.name)
                cb.add_css_class("detail-playlist-check")
                cb.set_active(pl.contains(record.id))
                cb.set_tooltip_text(
                    f"Remove from \"{pl.name}\"" if pl.contains(record.id)
                    else f"Add to \"{pl.name}\""
                )
                def _on_pl_toggled(check, pid=pl.id, rid=record.id):
                    from playlist_store import playlist_store as _ps2
                    if check.get_active():
                        _ps2.add_records(pid, [rid])
                    else:
                        _ps2.remove_record(pid, rid)
                cb.connect("toggled", _on_pl_toggled)
                pl_box.append(cb)
            content.append(pl_box)

        # ── Action buttons ────────────────────────────────────────────────────
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(8)
        sep.set_margin_bottom(4)
        content.append(sep)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        is_gif_detail = record.media_type == "animatediff" or (
            record.video_path.endswith(".gif")
        )
        export_label = "💾 Export GIF" if is_gif_detail else (
            "💾 Export" if record.media_type == "image" else "💾 Export"
        )
        export_btn = Gtk.Button(label=export_label)
        tip = "Save a copy of this image" if record.media_type == "image" else "Save a copy of this video"
        export_btn.set_tooltip_text(tip)
        export_btn.connect("clicked", self._export)
        if not record.media_exists:
            export_btn.set_sensitive(False)
        action_row.append(export_btn)

        # GIF↔MP4 conversion button — only for video/animatediff records.
        if record.media_type not in ("image", "artgen"):
            if is_gif_detail:
                conv_btn = Gtk.Button(label="→ MP4")
                conv_btn.set_tooltip_text("Convert this GIF to an MP4 video file")
                conv_btn.connect("clicked", self._convert_to_mp4)
            else:
                conv_btn = Gtk.Button(label="→ GIF")
                conv_btn.set_tooltip_text("Convert this video to an animated GIF")
                conv_btn.connect("clicked", self._convert_to_gif)
            if not record.media_exists:
                conv_btn.set_sensitive(False)
            action_row.append(conv_btn)

        # Star toggle button
        self._detail_star_btn = Gtk.Button(
            label="★ Starred" if record.starred else "☆ Star"
        )
        self._detail_star_btn.add_css_class("gen-star-btn")
        self._detail_star_btn.set_tooltip_text(
            "Remove from starred" if record.starred else "Add to starred"
        )
        self._detail_star_btn.connect("clicked", self._on_detail_star_clicked)
        action_row.append(self._detail_star_btn)

        remix_btn = Gtk.Button(label="🔀 Remix")
        remix_btn.add_css_class("action-btn")
        remix_btn.set_tooltip_text("Remix this into a new generation")
        remix_btn.connect("clicked", self._on_remix_clicked)
        action_row.append(remix_btn)

        self._remix_as_pipeline_btn = Gtk.Button(label="🧩 Remix as pipeline…")
        self._remix_as_pipeline_btn.add_css_class("action-btn")
        self._remix_as_pipeline_btn.set_tooltip_text(
            "Remix this into a multi-step pipeline"
        )
        self._remix_as_pipeline_btn.connect("clicked", self._on_remix_as_pipeline_clicked)
        action_row.append(self._remix_as_pipeline_btn)
        content.append(action_row)

        self.set_child(content)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _detail_section(self, text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text.upper())
        lbl.set_xalign(0)
        lbl.add_css_class("detail-section")
        return lbl

    def set_context(self, records: list, idx: int) -> None:
        """Set the ordered record list for prev/next navigation."""
        self._nav_records = list(records)
        self._nav_idx = max(0, min(idx, len(records) - 1))

    def _step(self, delta: int) -> None:
        """Navigate to an adjacent record in the current filter context."""
        if not self._nav_records:
            return
        self._nav_idx = (self._nav_idx + delta) % len(self._nav_records)
        rec = self._nav_records[self._nav_idx]
        self.show_record(rec, self._remix_cb, self._remix_as_pipeline_cb)

    def _on_detail_star_clicked(self, _btn) -> None:
        """Toggle the starred state for the currently displayed record."""
        if self._record is None:
            return
        new_starred = not bool(self._record.starred)
        self._record.starred = int(new_starred)
        if self._detail_star_btn is not None:
            self._detail_star_btn.set_label("★ Starred" if new_starred else "☆ Star")
            self._detail_star_btn.set_tooltip_text(
                "Remove from starred" if new_starred else "Add to starred"
            )
        if self._star_cb:
            self._star_cb(self._record, new_starred)

    def _toggle_play(self, _btn) -> None:
        import sys as _sys
        if _USE_SYSTEM_PLAYER:
            # macOS: drive playback through GstPlayer (gtk4paintablesink)
            if self._gst_player is None:
                return
            if self._gst_player.get_playing():
                self._gst_player.pause()
                if self._play_btn:
                    self._play_btn.set_label("▶ Play")
            else:
                self._gst_player.play()
                if self._play_btn:
                    self._play_btn.set_label("⏸ Pause")
            return
        # Linux: drive playback through Gtk.Video / GStreamer media stream
        if self._video_widget is None:
            return
        stream = self._video_widget.get_media_stream()
        if stream is None:
            # Stream is None → GTK4 found no working GStreamer media backend.
            print(
                "[GTK video] get_media_stream() returned None — "
                "GTK4 GStreamer backend not found.\n"
                "  On macOS: brew install gstreamer gst-plugins-base "
                "gst-plugins-good gst-plugins-bad gst-libav\n"
                "  Relaunch via ./tt-gen (sets GST_PLUGIN_PATH automatically).\n"
                "  Diagnostic: ./bin/test_macos.sh",
                file=_sys.stderr,
            )
            return
        # Check whether the stream is already in an error state (e.g. missing
        # codec, unreadable file).  stream.play() on an errored stream is a
        # silent no-op — detect it here so the error appears in the terminal.
        err = stream.get_error()
        if err is not None:
            print(
                f"[GTK video] GStreamer stream error (cannot play):\n"
                f"  {err.message}\n"
                f"  path: {getattr(self._record, 'video_path', '?')}\n"
                f"  Diagnostic: ./bin/test_macos.sh",
                file=_sys.stderr,
            )
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

    def _on_download_video(self, btn) -> None:
        """Download the selected record's video and reload the panel.

        Priority:
        1. If the record has an ``_inventory_video_url`` in extra_meta (remote
           library record), stream from the inventory server.
        2. Otherwise use the inference-server download callback (local history
           record whose job file the server still has on disk).
        """
        if not self._record:
            return
        btn.set_sensitive(False)
        btn.set_label("⬇ Downloading…")
        record = self._record
        remix_cb = self._remix_cb

        inv_video_url  = (record.extra_meta or {}).get("_inventory_video_url", "")
        inv_thumb_url  = (record.extra_meta or {}).get("_inventory_thumbnail_url", "")

        def _do_download():
            try:
                if inv_video_url:
                    # Remote inventory record — stream to the main local VIDEOS_DIR
                    # so the resulting record is treated as a true local record and
                    # the GTK inline video player can play it immediately.
                    import requests as _req  # noqa: PLC0415
                    import dataclasses  # noqa: PLC0415
                    from history_store import VIDEOS_DIR, THUMBNAILS_DIR  # noqa: PLC0415
                    from urllib.parse import urlparse as _up  # noqa: PLC0415

                    # Derive local filename from the inventory URL path.
                    v_filename = Path(_up(inv_video_url).path).name or f"gen_{record.id[:8]}.mp4"
                    dest = VIDEOS_DIR / v_filename
                    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

                    r = _req.get(inv_video_url, stream=True, timeout=60)
                    r.raise_for_status()
                    with open(dest, "wb") as fh:
                        for chunk in r.iter_content(65_536):
                            fh.write(chunk)

                    # Also download thumbnail into THUMBNAILS_DIR.
                    new_thumb_path = record.thumbnail_path  # fallback: remote-cache path
                    if inv_thumb_url:
                        t_filename = Path(_up(inv_thumb_url).path).name or ""
                        if t_filename:
                            thumb_dest = THUMBNAILS_DIR / t_filename
                            THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
                            if not thumb_dest.exists():
                                try:
                                    tr = _req.get(inv_thumb_url, stream=True, timeout=10)
                                    if tr.status_code == 200:
                                        with open(thumb_dest, "wb") as fh:
                                            for chunk in tr.iter_content(65_536):
                                                fh.write(chunk)
                                except Exception:
                                    pass  # thumbnail cache failure is non-fatal
                            new_thumb_path = str(thumb_dest)

                    # Build a localized record with main-storage paths and no
                    # remote-inventory flags so the gallery treats it as local.
                    clean_meta = {k: v for k, v in (record.extra_meta or {}).items()
                                  if not k.startswith("_inventory_") and k != "_is_remote"}
                    localized = dataclasses.replace(
                        record,
                        video_path=str(dest),
                        thumbnail_path=new_thumb_path,
                        extra_meta=clean_meta,
                    )

                    # Notify MainWindow (on main thread) to persist the record.
                    if self._on_localized_cb:
                        GLib.idle_add(self._on_localized_cb, localized)

                    # Show the localized record in the detail panel.
                    GLib.idle_add(self.show_record, localized, remix_cb)

                elif self._download_cb and record.id:
                    # Local history record — use the inference-server API.
                    dest = Path(record.video_path)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    self._download_cb(record.id, dest)
                    GLib.idle_add(self.show_record, record, remix_cb)
                else:
                    raise RuntimeError("No download source available for this record")

            except Exception as exc:
                GLib.idle_add(btn.set_label, f"Download failed: {exc}")
                GLib.idle_add(btn.set_sensitive, True)

        threading.Thread(target=_do_download, daemon=True).start()

    def _open_external(self, _btn) -> None:
        """Open the video in the system default player.

        Useful on macOS where GStreamer / the GTK video backend may not be
        available, causing Gtk.Video to show a blank frame.
        """
        if not self._record or not self._record.video_exists:
            return
        import platform, subprocess  # noqa: PLC0415
        path = self._record.video_path
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

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

    def _convert_to_mp4(self, _btn) -> None:
        """Convert the current GIF record to an MP4 via ffmpeg in a background thread."""
        if not self._record or not self._record.media_exists:
            return
        src = self._record.media_file_path
        import tempfile
        tmp = tempfile.mktemp(suffix=".mp4")
        def _worker():
            subprocess.run(
                ["ffmpeg", "-y", "-i", src,
                 "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
            )
            GLib.idle_add(self._offer_converted_save, tmp, ".mp4")
        threading.Thread(target=_worker, daemon=True).start()

    def _convert_to_gif(self, _btn) -> None:
        """Convert the current video to an animated GIF via two-pass ffmpeg."""
        if not self._record or not self._record.media_exists:
            return
        src = self._record.media_file_path
        import tempfile
        palette = tempfile.mktemp(suffix=".png")
        tmp = tempfile.mktemp(suffix=".gif")
        def _worker():
            subprocess.run(
                ["ffmpeg", "-y", "-i", src,
                 "-vf", "fps=12,scale=480:-1:flags=lanczos,palettegen", palette],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
            )
            subprocess.run(
                ["ffmpeg", "-y", "-i", src, "-i", palette,
                 "-vf", "fps=12,scale=480:-1:flags=lanczos,paletteuse", tmp],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
            )
            GLib.idle_add(self._offer_converted_save, tmp, ".gif")
        threading.Thread(target=_worker, daemon=True).start()

    def _offer_converted_save(self, tmp_path: str, suffix: str) -> None:
        """Open a save dialog for the converted file after ffmpeg completes."""
        if not Path(tmp_path).exists():
            return
        dlg = Gtk.FileDialog()
        stem = Path(self._record.media_file_path).stem if self._record else "converted"
        dlg.set_initial_name(stem + suffix)
        dlg.save(self.get_root(), None,
                 lambda d, r, p=tmp_path: self._save_converted(d, r, p))

    def _save_converted(self, dlg, result, tmp_path: str) -> None:
        """Move the converted temp file to the user-chosen destination."""
        try:
            dest = dlg.save_finish(result).get_path()
        except Exception:
            return
        if dest:
            shutil.move(tmp_path, dest)

    def _on_remix_clicked(self, btn) -> None:
        """Open a RemixPopover anchored to the Remix button in the detail panel."""
        if self._record and self._remix_cb:
            from remix_popover import RemixPopover
            pop = RemixPopover(self._record, on_remix=self._remix_cb)
            pop.set_parent(btn)
            pop.popup()

    def _on_remix_as_pipeline_clicked(self, _btn) -> None:
        """Open Pipeline Studio's Muse scoped to the displayed record."""
        if self._record and self._remix_as_pipeline_cb:
            self._remix_as_pipeline_cb(self._record)


# ── Full-size video player window ─────────────────────────────────────────────

class VideoPlayerWindow(Gtk.Window):
    """
    Standalone window for watching a generated video at full size.

    Opens maximized by default. Supports:
      - Escape / clicking the close button → closes window, pauses video
      - F key or the ⛶ button → toggle fullscreen
      - Space → play / pause (GIF branch below pauses/resumes the frame timer
        via `AnimatedGifWidget.toggle_playing()` -- see `_toggle_play`)

    GIF branch: AnimateDiff `.gif` records (media_type == "animatediff", or
    any video_path literally ending in ".gif") are NOT played via Gtk.Video --
    the app avoids Gtk.Video for gifs everywhere else (DetailPanel drives them
    frame-by-frame via GdkPixbufAnimationIter instead, since GStreamer seeking
    on a gif is unreliable) -- so the fullscreen window uses the same
    self-driving `artgen_render.AnimatedGifWidget` the Discover galleries use.
    Everything else (maximize/F/Escape/title/layout/control strip) is
    identical between the two branches.
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

        # ── Video / GIF player ───────────────────────────────────────────────
        is_gif = (
            record.media_type == "animatediff"
            or record.video_path.endswith(".gif")
        )
        if is_gif:
            # Self-driving animated Gtk.Picture -- same widget the Discover
            # galleries use for gif thumbnails/hover-preview. It manages its
            # own GLib timer and cancels it on unrealize (i.e. window close).
            self._video = AnimatedGifWidget(record.video_path)
        else:
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
        # AnimatedGifWidget (the GIF branch) is a plain Gtk.Picture -- it has
        # no media stream, so it can't be driven through Gtk.MediaStream's
        # play()/pause(). gif-hygiene fix 2: route Pause/Space through its
        # own toggle_playing() API instead of no-opping, so the button/key
        # genuinely pause/resume the gif's frame-advance timer.
        if isinstance(self._video, AnimatedGifWidget):
            playing = self._video.toggle_playing()
            self._play_pause_btn.set_label("⏸ Pause" if playing else "▶ Play")
            return
        if not hasattr(self._video, "get_media_stream"):
            return
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

class PendingCard(Gtk.Box):
    """
    Animated placeholder card shown while a generation is running.

    Base class is `Gtk.Box`, not `Gtk.Frame` — same reasoning as
    `GenerationCard` (see its class docstring): Frame's own intrinsic
    themed border adds a couple of untracked pixels on top of whatever
    `self._card_zone` pins the content to, which breaks an EXACT match to
    `gallery_layout.tile_size(density)`.
    """

    def __init__(self, prompt: str = "", model_source: str = "video"):
        super().__init__()
        self.add_css_class("card")
        # FIXED tile size, matching GenerationCard exactly (see
        # gallery_layout.py) so a pending job's placeholder is never a
        # different size than the finished card that will replace it.
        # Resolved from the LIVE density setting at construction time (see
        # GenerationCard.__init__ for why this matters — a freshly-spawned
        # PendingCard used to always be comfortable-sized regardless of the
        # active density).
        density = _gallery_density()
        self._tile_w, self._tile_h = gallery_layout.tile_size(density)
        thumb_w, thumb_h = gallery_layout.thumb_size(density)
        self.set_size_request(self._tile_w, self._tile_h)
        self.set_hexpand(True)
        self._start = time.monotonic()
        self._timer_id: Optional[int] = None

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        # Pin the WHOLE card's measured size to exactly (self._tile_w,
        # self._tile_h) -- same trick as GenerationCard._card_zone (see its
        # comment) and required for the same reason: the outer
        # set_size_request() above is only a floor, and the footer/prompt
        # rows below don't shrink with density on their own. Stored as
        # self._card_zone so _apply_gallery_density can resize an
        # ALREADY-BUILT pending card in place.
        self._card_zone = gallery_layout.pin_fixed_zone(outer, self._tile_w, self._tile_h)
        self.append(self._card_zone)

        # Fixed-size thumbnail area matching GenerationCard's media zone.
        # Anchors the FlowBox cell height so the gallery stays uniform while a
        # job is in progress — the pending card won't be taller than a completed card.
        thumb_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        thumb_area.set_size_request(thumb_w, thumb_h)
        thumb_area.set_hexpand(False)   # children must not push this box wider
        thumb_area.set_valign(Gtk.Align.CENTER)
        thumb_area.add_css_class("pending-thumb-area")
        thumb_area.set_margin_start(4)
        thumb_area.set_margin_end(4)
        thumb_area.set_margin_top(4)

        # Label differs by media type so the user can tell what is in flight
        if model_source == "image":
            spinner_text = "🖼 Generating image…"
        elif model_source == "animate":
            spinner_text = "💃 Animating…"
        else:
            spinner_text = "⏳ Generating video…"
        spinner_lbl = Gtk.Label(label=spinner_text)
        spinner_lbl.add_css_class("teal")
        spinner_lbl.set_halign(Gtk.Align.CENTER)
        spinner_lbl.set_max_width_chars(1)
        spinner_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        thumb_area.append(spinner_lbl)

        self._bar = Gtk.ProgressBar()
        self._bar.set_pulse_step(0.08)
        self._bar.set_margin_start(12)
        self._bar.set_margin_end(12)
        thumb_area.append(self._bar)

        self._status_lbl = Gtk.Label(label="Queued")
        self._status_lbl.add_css_class("muted")
        self._status_lbl.set_halign(Gtk.Align.CENTER)
        self._status_lbl.set_max_width_chars(1)   # never wider than allocated space
        self._status_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        thumb_area.append(self._status_lbl)

        self._elapsed_lbl = Gtk.Label(label="0s elapsed")
        self._elapsed_lbl.add_css_class("teal")
        self._elapsed_lbl.set_attributes(_small_attrs())
        self._elapsed_lbl.set_halign(Gtk.Align.CENTER)
        self._elapsed_lbl.set_max_width_chars(1)
        self._elapsed_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        thumb_area.append(self._elapsed_lbl)

        outer.append(thumb_area)

        # Footer: prompt text below the thumbnail area (same zone as card buttons)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(6)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        outer.append(box)

        if prompt:
            prompt_lbl = Gtk.Label(label=prompt)
            prompt_lbl.set_wrap(True)
            prompt_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            prompt_lbl.set_max_width_chars(26)
            prompt_lbl.set_lines(2)
            prompt_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            prompt_lbl.set_xalign(0)
            prompt_lbl.set_tooltip_text(prompt)
            prompt_lbl.add_css_class("muted")
            box.append(prompt_lbl)

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

    Pagination: at most _PAGE_SIZE cards are built and shown at once.  The pager
    bar at the bottom lets the user navigate pages.  All records are kept in
    self._cards; only the current page slice is appended to the FlowBox.
    """

    _PAGE_SIZE = 48  # cards per page — ~4 rows of 4 at comfortable density

    def __init__(self, select_cb, delete_cb, media_type: str = "video",
                 remix_cb=None, star_cb=None, transform_cb=None,
                 remix_as_pipeline_cb=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_vexpand(True)
        self.set_hexpand(True)
        self._select_cb = select_cb        # select_cb(record: GenerationRecord) called on click
        self._delete_cb = delete_cb        # delete_cb(record: GenerationRecord) called on trash
        self._remix_cb = remix_cb          # callable(record) or None — opens RemixPopover
        self._remix_as_pipeline_cb = remix_as_pipeline_cb  # callable(record) or None — opens scoped Muse
        self._star_cb = star_cb            # callable(record, starred: bool) or None
        self._transform_cb = transform_cb  # callable(record, key) or None — forge transforms
        self._media_type = media_type
        self._active_filter: str = "all"   # "all" | "starred"
        self._page: int = 0                # 0-indexed current page

        # ── Filter bar ────────────────────────────────────────────────────────
        filter_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        filter_bar.set_margin_start(12)
        filter_bar.set_margin_end(12)
        filter_bar.set_margin_top(6)
        filter_bar.set_margin_bottom(4)

        self._filter_all_btn = Gtk.ToggleButton(label="All")
        self._filter_all_btn.add_css_class("artgen-filter-chip")
        self._filter_all_btn.set_active(True)
        self._filter_all_btn.connect("toggled", self._on_filter_chip, "all")
        filter_bar.append(self._filter_all_btn)

        self._filter_star_btn = Gtk.ToggleButton(label="★ Starred")
        self._filter_star_btn.add_css_class("artgen-filter-chip")
        self._filter_star_btn.add_css_class("artgen-starred-chip")
        self._filter_star_btn.set_active(False)
        self._filter_star_btn.connect("toggled", self._on_filter_chip, "starred")
        filter_bar.append(self._filter_star_btn)

        self.append(filter_bar)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Scrolled flow box ──────────────────────────────────────────────────
        # FlowBox automatically computes the number of columns that fit in the
        # available width, so the gallery re-flows when the pane is resized.
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_hexpand(True)
        self._scroll.set_vexpand(True)

        # FlowBox grid settings -- IDENTICAL to the artgen gallery
        # (ArtgenGallery._flow in artgen_gallery.py), sourced from
        # gallery_layout.py so switching Discover tabs never changes the grid.
        self._flow = Gtk.FlowBox()
        self._flow.set_column_spacing(gallery_layout.FLOW_COLUMN_SPACING)
        self._flow.set_row_spacing(gallery_layout.FLOW_ROW_SPACING)
        self._flow.set_margin_top(4)
        self._flow.set_margin_bottom(12)
        self._flow.set_margin_start(12)
        self._flow.set_margin_end(12)
        self._flow.set_homogeneous(False)   # cards pack at natural width; extra space adds columns
        self._flow.set_selection_mode(Gtk.SelectionMode.NONE)  # selection handled manually
        self._flow.set_min_children_per_line(gallery_layout.FLOW_MIN_CHILDREN_PER_LINE)
        self._flow.set_max_children_per_line(gallery_layout.FLOW_MAX_CHILDREN_PER_LINE)
        self._flow.set_halign(Gtk.Align.FILL)
        self._flow.set_valign(Gtk.Align.START)
        self._scroll.set_child(self._flow)
        self.append(self._scroll)

        # ── Pager bar (hidden until there are multiple pages) ──────────────────
        self._pager_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._pager_bar.set_halign(Gtk.Align.CENTER)
        self._pager_bar.set_margin_top(6)
        self._pager_bar.set_margin_bottom(8)

        self._pager_prev = Gtk.Button(label="◀ Prev")
        self._pager_prev.add_css_class("flat")
        self._pager_prev.connect("clicked", lambda _b: self._set_page(self._page - 1))
        self._pager_bar.append(self._pager_prev)

        self._pager_label = Gtk.Label(label="")
        self._pager_label.add_css_class("gallery-page-label")
        self._pager_bar.append(self._pager_label)

        self._pager_next = Gtk.Button(label="Next ▶")
        self._pager_next.add_css_class("flat")
        self._pager_next.connect("clicked", lambda _b: self._set_page(self._page + 1))
        self._pager_bar.append(self._pager_next)

        self.append(self._pager_bar)

        self._cards: list = []                       # all card widgets, index 0 = top-left
        self._pending: Optional[PendingCard] = None
        self._selected_card: Optional[GenerationCard] = None
        self._selection_mode: bool = False           # True while adding to a playlist
        self._active_playlist_id: "str | None" = None  # playlist being edited

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
                if _USE_SYSTEM_PLAYER:
                    # macOS: close GstPlayer pipeline to release fds
                    gst = getattr(card, "_hover_gst", None)
                    if gst is not None:
                        gst.close()
                else:
                    # Linux: release Gtk.Video pipeline
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
        # New generations always land on page 1 so they're immediately visible.
        self._page = 0
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
        """Replace all GenerationCards with cards built from *records*.

        Any PendingCard (in-flight generation) is preserved at position 0.
        Calling this method twice is safe — the second call replaces, not
        appends, so there are no duplicate cards after a gallery refresh.
        """
        # Preserve in-flight pending card so active generations survive a refresh.
        preserved = [c for c in self._cards if isinstance(c, PendingCard)]
        self._cards = preserved  # clear all GenerationCards
        self._page = 0           # reset to first page on every history reload

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
            select_cb=self.select_card,
            delete_cb=self._delete_cb,
            remix_cb=self._remix_cb,
            star_cb=self._star_cb,
            transform_cb=self._transform_cb,
            remix_as_pipeline_cb=self._remix_as_pipeline_cb,
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

    def enter_selection_mode(self, playlist_id: str, pre_checked_ids: set) -> None:
        """
        Activate checkbox selection mode for the given playlist.

        Shows a checkbox on every video card.  Cards whose record IDs are
        already in pre_checked_ids are pre-checked so editing a playlist
        shows the existing membership at a glance.
        """
        self._selection_mode = True
        self._active_playlist_id = playlist_id
        for card in self._cards:
            if not isinstance(card, GenerationCard):
                continue
            card.set_selection_visible(True)
            card.set_checked(card._record.id in pre_checked_ids)

    def exit_selection_mode(self) -> None:
        """Deactivate selection mode and hide all checkboxes."""
        self._selection_mode = False
        self._active_playlist_id = None
        for card in self._cards:
            if isinstance(card, GenerationCard):
                card.set_selection_visible(False)
                card.set_checked(False)

    def get_checked_ids(self) -> list:
        """Return a list of record IDs for all currently checked cards."""
        return [
            card._record.id
            for card in self._cards
            if isinstance(card, GenerationCard) and card.is_checked()
        ]

    def _on_filter_chip(self, btn: Gtk.ToggleButton, filt: str) -> None:
        if not btn.get_active():
            return
        self._active_filter = filt
        # Deactivate the other chip (manual radio group).
        other = self._filter_star_btn if filt == "all" else self._filter_all_btn
        other.set_active(False)
        self._page = 0  # reset to first page when filter changes
        self._relayout()

    def _filtered_cards(self) -> list:
        """All GenerationCards (and any PendingCard) that pass the active filter."""
        if self._active_filter == "starred":
            return [c for c in self._cards
                    if isinstance(c, PendingCard) or
                    (isinstance(c, GenerationCard) and c._record.starred)]
        return list(self._cards)

    def _set_page(self, page: int) -> None:
        """Navigate to page *page* (0-indexed), clamped to valid range."""
        filtered = self._filtered_cards()
        # PendingCards are always on page 0; exclude them for page-count math.
        gen_cards = [c for c in filtered if isinstance(c, GenerationCard)]
        n_pages = max(1, -(-len(gen_cards) // self._PAGE_SIZE))  # ceil division
        self._page = max(0, min(page, n_pages - 1))
        self._relayout()
        # Scroll back to the top when the page changes.
        adj = self._scroll.get_vadjustment()
        if adj:
            adj.set_value(0)

    def visible_cards(self) -> list:
        """Return the GenerationCards currently shown on the active page."""
        filtered = self._filtered_cards()
        pending = [c for c in filtered if isinstance(c, PendingCard)]
        gen_cards = [c for c in filtered if isinstance(c, GenerationCard)]
        start = self._page * self._PAGE_SIZE
        return pending + gen_cards[start: start + self._PAGE_SIZE]

    def all_cards(self) -> list:
        """Return all filtered cards across all pages — used for detail-panel navigation."""
        return self._filtered_cards()

    def _relayout(self) -> None:
        """Re-populate the FlowBox with the current page slice, update pager."""
        filtered = self._filtered_cards()
        pending = [c for c in filtered if isinstance(c, PendingCard)]
        gen_cards = [c for c in filtered if isinstance(c, GenerationCard)]

        n_pages = max(1, -(-len(gen_cards) // self._PAGE_SIZE))  # ceil division
        # Clamp page in case records were deleted.
        self._page = max(0, min(self._page, n_pages - 1))

        start = self._page * self._PAGE_SIZE
        page_cards = pending + gen_cards[start: start + self._PAGE_SIZE]

        # Remove all FlowBoxChild wrappers; card widgets remain alive in self._cards.
        child = self._flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._flow.remove(child)
            child = nxt
        # Add only the current page.
        for card in page_cards:
            self._flow.append(card)

        # Re-apply selection mode checkboxes to newly visible cards.
        if self._selection_mode and self._active_playlist_id:
            for card in page_cards:
                if isinstance(card, GenerationCard) and not card.is_checked():
                    card.set_selection_visible(True)

        # Update pager bar visibility and state.
        if n_pages <= 1:
            self._pager_bar.set_visible(False)
        else:
            self._pager_bar.set_visible(True)
            total = len(gen_cards)
            page_start = start + 1
            page_end = min(start + self._PAGE_SIZE, total)
            self._pager_label.set_label(
                f"{page_start}–{page_end} of {total}  ·  page {self._page + 1}/{n_pages}"
            )
            self._pager_prev.set_sensitive(self._page > 0)
            self._pager_next.set_sensitive(self._page < n_pages - 1)


# ── Model/source key-space helpers (ControlPanel deleted, SP-3d-5) ─────────────
# These two maps are the survivors of the old ControlPanel-era model/source
# vocabulary. Everything else that used to live here — _MODEL_TO_SOURCE,
# _MODEL_TO_VIDEO_KEY, _MODEL_DISPLAY_SERVER, _MODEL_TO_SERVER_KEY,
# _MODEL_TO_CAP, _MODEL_TO_IMAGE_KEY — was read only by ControlPanel's own
# internals (health-poller re-apply, SHOT panel, etc.) and was deleted
# alongside the class in SP-3d-5; nothing surviving ever read them.

# Maps server key → (source_tab, image_model_key or video_model_key) for startup pre-selection
_SERVER_KEY_TO_SOURCE_MODEL: dict = {
    "wan2.2":         ("video",   "wan2"),
    "mochi":          ("video",   "mochi"),
    "skyreels":       ("video",   "skyreels"),
    "flux":           ("image",   "flux"),
    "sdxl":           ("image",   "sdxl"),
    "z-image-turbo":  ("image",   "z-image-turbo"),
    "motif":          ("image",   "motif"),
    "animate":        ("animate", ""),
}
# Maps source tab key → capability key
_SOURCE_TO_CAP: dict = {
    "video":   "video",
    "animate": "animate",
    "image":   "image",
    "artgen":  "artgen",
}

# ── Recovery dialog ────────────────────────────────────────────────────────────

_RECOVERY_DISMISS = Gtk.ResponseType.REJECT   # reuse a built-in int constant for our "Ignore" button


class RecoveryDialog(Gtk.Dialog):
    """Modal dialog listing unknown server jobs; user selects which to recover.

    Buttons (duplicated at top and bottom for long lists):
      Cancel        — close without recovering or dismissing anything.
      🚫 Ignore     — permanently hide the checked jobs from future scans.
      ✓ Recover     — recover the checked jobs (default action).

    The job list is scrollable so the dialog stays usable even with many entries.

    After the dialog emits a response, inspect:
      .selected_jobs  — jobs to recover  (populated on OK / Recover)
      .dismissed_jobs — jobs to ignore forever (populated on _RECOVERY_DISMISS / Ignore)
    """

    def __init__(self, parent, jobs: list):
        super().__init__(title="Recover Server Jobs", transient_for=parent, modal=True)
        self.set_default_size(560, 480)
        self.selected_jobs: list = []
        self.dismissed_jobs: list = []
        self._checkboxes: list = []
        self._jobs = jobs

        # Bottom action area (standard dialog buttons)
        self.add_button("Cancel",       Gtk.ResponseType.CANCEL)
        self.add_button("🚫 Ignore",    _RECOVERY_DISMISS)
        self.add_button("✓ Recover",    Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)

        content = self.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(12)
        content.set_margin_bottom(4)
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

        # Top action bar — mirrors the bottom buttons so the user never has to
        # scroll down to submit when the list is long.
        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top_bar.set_margin_top(4)
        top_bar.set_margin_bottom(4)
        lbl = Gtk.Label(label="Quick actions:")
        lbl.set_xalign(0)
        top_bar.append(lbl)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        top_bar.append(spacer)
        for label_text, resp in (
            ("Cancel",      Gtk.ResponseType.CANCEL),
            ("🚫 Ignore",   _RECOVERY_DISMISS),
            ("✓ Recover",   Gtk.ResponseType.OK),
        ):
            btn = Gtk.Button(label=label_text)
            btn.connect("clicked", lambda _b, r=resp: self.response(r))
            top_bar.append(btn)
        content.append(top_bar)

        # Scrollable list of checkboxes
        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for job in jobs:
            short = job["prompt"][:80] + ("…" if len(job["prompt"]) > 80 else "")
            label = f"[{job['status']}]  {short}  (id: {job['id'][:8]})"
            cb = Gtk.CheckButton(label=label)
            cb.set_active(True)
            cb.job = job  # plain Python attribute — GObject set_data() is unsupported in PyGObject
            self._checkboxes.append(cb)
            list_box.append(cb)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_margin_top(4)
        scroll.set_child(list_box)
        content.append(scroll)

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

        # ── Server segment: MenuButton (dot + label) → capability dashboard popover ──
        self._srv_dot = Gtk.Label(label="⬤")
        self._srv_dot.add_css_class("tt-statusbar-dot")
        self._srv_dot.add_css_class("tt-statusbar-dot-offline")
        self._srv_lbl = Gtk.Label(label="offline")
        self._srv_lbl.add_css_class("tt-statusbar-seg")
        self._srv_lbl.set_max_width_chars(1)
        self._srv_lbl.set_ellipsize(Pango.EllipsizeMode.END)

        srv_btn_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        srv_btn_content.append(self._srv_dot)
        srv_btn_content.append(self._srv_lbl)

        self._srv_menu_btn = Gtk.MenuButton()
        self._srv_menu_btn.set_has_frame(False)
        self._srv_menu_btn.add_css_class("tt-statusbar-srv-btn")
        self._srv_menu_btn.set_child(srv_btn_content)

        # ── Capability dashboard popover ──────────────────────────────────────
        # Shows readiness for every capability (one row each).
        # Start/Stop buttons at the bottom operate on the port-8000 server
        # (video/animate/image).  Prompt AI and Artgen are managed via Servers ▾.
        self._pop_start = Gtk.Button(label="▶  Start server")
        self._pop_start.add_css_class("generate-btn")
        self._pop_stop  = Gtk.Button(label="■  Stop server")
        self._pop_stop.add_css_class("cancel-btn")

        _popover = Gtk.Popover()
        _popover.set_position(Gtk.PositionType.TOP)

        def _start_and_close(_btn):
            _popover.popdown()
            start_cb()

        def _stop_and_close(_btn):
            _popover.popdown()
            stop_cb()

        self._pop_start.connect("clicked", _start_and_close)
        self._pop_stop.connect("clicked", _stop_and_close)

        pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pop_box.set_margin_top(10)
        pop_box.set_margin_bottom(10)
        pop_box.set_margin_start(14)
        pop_box.set_margin_end(14)

        # One row per capability: left = capability label, right = status dot + detail
        self._cap_rows: dict = {}  # cap_key → Gtk.Label (status)
        for cap, cap_label in _sm.CAPABILITY_LABELS.items():
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            name_lbl = Gtk.Label(label=cap_label)
            name_lbl.add_css_class("cap-row-label")
            name_lbl.set_xalign(0)
            name_lbl.set_hexpand(True)
            row.append(name_lbl)
            status_lbl = Gtk.Label(label="○ checking…")
            status_lbl.add_css_class("cap-row-offline")
            status_lbl.set_xalign(1)
            status_lbl.set_max_width_chars(1)
            status_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            row.append(status_lbl)
            self._cap_rows[cap] = status_lbl
            pop_box.append(row)

        _sep_line = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        _sep_line.set_margin_top(4)
        _sep_line.set_margin_bottom(4)
        pop_box.append(_sep_line)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_row.append(self._pop_start)
        btn_row.append(self._pop_stop)
        pop_box.append(btn_row)

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

        # ── Elapsed-timer state ──────────────────────────────────────────────
        # Set by update_starting(), ticked every second, cleared by
        # update_server() and update_error().
        self._phase: str = "starting"
        self._start_ts: float = 0.0
        self._timer_id: "int | None" = None
        self._in_error: bool = False

    # ── Public update methods (main-thread only) ───────────────────────────────

    def _set_srv_dot(self, css_state: str, model_text: str, _unused: str = "") -> None:
        for cls in ("tt-statusbar-dot-ready", "tt-statusbar-dot-offline",
                    "tt-statusbar-dot-starting", "tt-statusbar-dot-error"):
            self._srv_dot.remove_css_class(cls)
        self._srv_dot.add_css_class(f"tt-statusbar-dot-{css_state}")
        self._srv_lbl.set_label(model_text)
        # Mirror error vs normal colour on the label too
        self._srv_lbl.remove_css_class("tt-statusbar-seg-error")
        self._srv_lbl.remove_css_class("tt-statusbar-seg")
        self._srv_lbl.add_css_class(
            "tt-statusbar-seg-error" if css_state == "error" else "tt-statusbar-seg"
        )

    def update_capability(self, cap: str, ready: bool, detail: str = "") -> None:
        """Update one capability row in the dashboard popover.

        cap    — capability key ("video", "prompt", "artgen", "animatediff", …)
        ready  — True = green dot, False = grey dot
        detail — short model or status string shown next to the dot
        """
        lbl = self._cap_rows.get(cap)
        if lbl is None:
            return
        dot = "●" if ready else "○"
        text = f"{dot} {detail}" if detail else ("● ready" if ready else "○ offline")
        lbl.set_label(text)
        lbl.remove_css_class("cap-row-ready")
        lbl.remove_css_class("cap-row-offline")
        lbl.add_css_class("cap-row-ready" if ready else "cap-row-offline")

    def update_server(self, ready: bool, model: "str | None") -> None:
        """Reflect server health in the status dot and model label.

        Ignores ready=False calls while in error state so the health worker
        does not silently overwrite the error indicator between retries.
        """
        if not ready and self._in_error:
            return
        self._in_error = False
        self._stop_timer()
        if ready:
            self._set_srv_dot("ready", model or "ready", "")
        else:
            self._set_srv_dot("offline", model or "offline", "")
        # Re-enable popover controls once the launch/stop operation has settled.
        self._pop_start.set_sensitive(True)
        self._pop_stop.set_sensitive(True)

    def update_starting(self) -> None:
        """Show 'starting' state while the server launch script is running."""
        self._in_error = False
        self._phase = "starting"
        self._start_ts = time.monotonic()
        self._set_srv_dot("starting", "starting… 0:00", "Server starting…")
        # Disable popover buttons while the script is in flight — prevents
        # double-starting or stopping a server that is mid-launch.
        self._pop_start.set_sensitive(False)
        self._pop_stop.set_sensitive(False)
        self._start_timer()

    def update_error(self, msg: str = "failed — click for details") -> None:
        """Show the error state: red dot, error message, re-enable Start."""
        self._in_error = True
        self._stop_timer()
        self._set_srv_dot("error", msg, "Server failed to start")
        self._pop_start.set_sensitive(True)
        self._pop_stop.set_sensitive(False)

    def set_phase(self, phase: str) -> None:
        """Update the phase label while in starting state (called on main thread)."""
        if self._timer_id is None:
            return  # not in starting state; ignore stale callbacks
        self._phase = phase
        elapsed = int(time.monotonic() - self._start_ts)
        m, s = divmod(elapsed, 60)
        self._srv_lbl.set_label(f"{phase}… {m}:{s:02d}")

    # ── Elapsed timer (runs on main thread via GLib.timeout_add) ─────────────

    def _start_timer(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
        self._timer_id = GLib.timeout_add(1000, self._tick)

    def _stop_timer(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _tick(self) -> bool:
        """Update the elapsed-time counter in the starting label. Main thread."""
        elapsed = int(time.monotonic() - self._start_ts)
        m, s = divmod(elapsed, 60)
        self._srv_lbl.set_label(f"{self._phase}… {m}:{s:02d}")
        return True  # keep repeating until _stop_timer() cancels the source

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

        Two-stage initial read:
          1. Post sysfs-only clock data immediately (zero-wait, passive read)
             so the chip segment is visible as soon as the window appears.
          2. Run the full _read_chip_telemetry() which resolves tt-smi and
             fetches ASIC temperature + power (may block up to 13 s on first
             call due to version check + snapshot).  Posts the enriched result.
        """
        # Stage 1: instant sysfs seed so the segment is never blank at startup.
        clocks = self._read_sysfs_clocks()
        if clocks:
            max_clk = max(clocks)
            shade_str = "".join(self._clock_to_shade(c, max_clk) for c in clocks)
            GLib.idle_add(self._apply_chip, f"{max_clk} MHz  {shade_str}")

        # Stage 2: full read with tt-smi (blocks up to ~13 s on cold start).
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

    def _count_blackhole_chips(self) -> int:
        """Return the number of Blackhole devices visible to tt-smi, or 0 on failure.

        Used to decide whether AnimateDiff and a running server would compete
        for the same chip. Counts only live (non-sentinel) chips.
        """
        import json as _json
        tt_smi = self._resolve_tt_smi()
        if not tt_smi:
            return 0
        try:
            result = __import__("subprocess").run(
                [tt_smi, "-s"], capture_output=True, text=True, timeout=10
            )
            data = _json.loads(result.stdout)
            count = 0
            for dev in data.get("device_info", []):
                arch = dev.get("board_info", {}).get("board_type", "").lower()
                telem = dev.get("telemetry", {})
                temp = telem.get("asic_temperature", 0)
                # Skip ARC-dead chips (sentinel temp > 1000°C)
                try:
                    if float(temp) > 1000:
                        continue
                except (TypeError, ValueError):
                    pass
                if any(k in arch for k in ("blackhole", "p100", "p150", "p300")):
                    count += 1
            return count
        except Exception:
            return 0

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
            default_width=480,
            default_height=660,
            resizable=True,
        )
        self._mw = main_window
        self.set_transient_for(main_window)
        app = main_window.get_application()
        if app is not None:
            self.set_application(app)
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

        # ── Plugins ───────────────────────────────────────────────────────────
        box.append(self._section("Plugins"))
        plugins_note = Gtk.Label(
            label="Uncheck a plugin or model to hide it from the UI. "
                  "Nothing is removed — you can re-enable at any time."
        )
        plugins_note.set_xalign(0)
        plugins_note.set_wrap(True)
        plugins_note.add_css_class("muted")
        plugins_note.set_margin_bottom(4)
        box.append(plugins_note)

        hidden = set(_settings.get("hidden_plugins") or [])

        # Video models
        video_entries = [
            ("wan2",        "Wan2.2-T2V  —  720p video"),
            ("mochi",       "Mochi-1  —  480×848 video"),
            ("skyreels",    "SkyReels I2V  —  960×544"),
            ("animatediff", "AnimateDiff  —  local Blackhole GIF"),
        ]
        for key, label in video_entries:
            cb = Gtk.CheckButton(label=label)
            cb.set_active(key not in hidden)
            cb.set_tooltip_text(f"Video model key: {key!r}")
            def _on_plugin_toggle(widget, k=key):
                h = set(_settings.get("hidden_plugins") or [])
                if widget.get_active():
                    h.discard(k)
                else:
                    h.add(k)
                _settings.set("hidden_plugins", sorted(h))
            cb.connect("toggled", _on_plugin_toggle)
            box.append(cb)

        # Artgen generators (from plugin loader)
        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        try:
            import plugin_loader as _pl
            _pl.load_plugins()
            artgen_names = sorted(
                n for n in _pl.all_names()
                if _pl.get(n).runnable
            )
        except Exception:
            artgen_names = []
        for name in artgen_names:
            cb = Gtk.CheckButton(label=f"Artgen: {name}")
            cb.set_active(name not in hidden)
            cb.set_tooltip_text(f"Artgen plugin key: {name!r}")
            def _on_artgen_toggle(widget, k=name):
                h = set(_settings.get("hidden_plugins") or [])
                if widget.get_active():
                    h.discard(k)
                else:
                    h.add(k)
                _settings.set("hidden_plugins", sorted(h))
            cb.connect("toggled", _on_artgen_toggle)
            box.append(cb)

        # ── Generation ────────────────────────────────────────────────────────
        box.append(self._section("Generation"))

        # Note: quality preset and clip length are now controlled by the QUALITY
        # and CLIP LENGTH button rows in the main panel.

        # AnimateDiff frame count
        anim_frames_spin = Gtk.SpinButton()
        anim_frames_spin.set_adjustment(Gtk.Adjustment(
            value=_settings.get("animatediff_frames"),
            lower=1, upper=64, step_increment=1, page_increment=4,
        ))
        anim_frames_spin.set_digits(0)
        anim_frames_spin.connect("value-changed", lambda w: _settings.set(
            "animatediff_frames", int(w.get_value())
        ))
        box.append(self._row(
            "AnimateDiff frames:", anim_frames_spin,
            "Number of frames to generate per AnimateDiff GIF. "
            "More frames = longer GIF, ~5 min/frame on Blackhole. "
            "Default: 8 (~40 min total)."
        ))

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

        gif_dwell_spin = Gtk.SpinButton()
        gif_dwell_spin.set_adjustment(Gtk.Adjustment(
            value=_settings.get("tttv_gif_dwell_s"),
            lower=3, upper=300, step_increment=1, page_increment=5,
        ))
        gif_dwell_spin.set_digits(0)
        gif_dwell_spin.connect("value-changed", lambda w: _settings.set(
            "tttv_gif_dwell_s", int(w.get_value())
        ))
        self._tttv_box.append(self._row("AnimateDiff GIF dwell (seconds):", gif_dwell_spin,
                                        "How long each AnimateDiff GIF is shown before advancing."))

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

        # ── Servers ───────────────────────────────────────────────────────────
        box.append(self._section("Servers"))

        note = Gtk.Label(
            label="Host / port changes take effect on next launch.\n"
                  "Token changes apply immediately."
        )
        note.set_xalign(0)
        note.set_margin_start(2)
        note.set_margin_bottom(6)
        note.add_css_class("muted")
        box.append(note)

        self._build_servers_config(box)

    def _build_servers_config(self, parent: Gtk.Box) -> None:
        """Build the per-service host / port / token grid for the Servers section."""
        from server_config import server_config as _sc, DEFAULTS as _SC_DEFAULTS
        import server_manager as _sm_mod

        grid = Gtk.Grid()
        grid.set_column_spacing(8)
        grid.set_row_spacing(6)
        grid.set_margin_start(2)

        # Column header labels
        for col, txt in enumerate(["Service", "Host", "Port", "Token"]):
            hdr = Gtk.Label(label=txt)
            hdr.set_xalign(0)
            hdr.add_css_class("muted")
            grid.attach(hdr, col, 0, 1, 1)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_bottom(2)
        grid.attach(sep, 0, 1, 4, 1)

        for row_idx, (key, sdef) in enumerate(_sm_mod.SERVERS.items(), start=2):
            # Service label
            svc_lbl = Gtk.Label(label=key)
            svc_lbl.set_xalign(0)
            svc_lbl.set_tooltip_text(sdef.label)
            grid.attach(svc_lbl, 0, row_idx, 1, 1)

            # Host entry
            host_entry = Gtk.Entry()
            host_entry.set_text(_sc.get(key, "host") or "localhost")
            host_entry.set_width_chars(14)
            host_entry.set_placeholder_text("localhost")
            host_entry.connect("changed", lambda w, k=key: _sc.set(k, "host", w.get_text().strip()))
            grid.attach(host_entry, 1, row_idx, 1, 1)

            # Port spin
            port_spin = Gtk.SpinButton()
            port_spin.set_adjustment(Gtk.Adjustment(
                value=float(_sc.get(key, "port") or 8000),
                lower=1, upper=65535, step_increment=1, page_increment=100,
            ))
            port_spin.set_digits(0)
            port_spin.set_width_chars(6)
            port_spin.connect("value-changed",
                              lambda w, k=key: _sc.set(k, "port", int(w.get_value())))
            grid.attach(port_spin, 2, row_idx, 1, 1)

            # Token entry — masked by default, eye icon toggles visibility.
            token_entry = Gtk.Entry()
            token_entry.set_visibility(False)
            token_entry.set_icon_from_icon_name(
                Gtk.EntryIconPosition.SECONDARY, "view-reveal-symbolic"
            )
            token_entry.set_icon_activatable(Gtk.EntryIconPosition.SECONDARY, True)
            token_entry.connect(
                "icon-press",
                lambda w, _pos: w.set_visibility(not w.get_visibility()),
            )
            current_token = _sc.get(key, "token") or ""
            token_entry.set_text(current_token)
            has_default_token = bool((_SC_DEFAULTS.get(key) or {}).get("token"))
            token_entry.set_placeholder_text("" if has_default_token else "no auth")
            token_entry.set_hexpand(True)
            token_entry.connect("changed", lambda w, k=key: _sc.set(k, "token", w.get_text()))
            grid.attach(token_entry, 3, row_idx, 1, 1)

        parent.append(grid)

        # Config file path hint
        from server_config import CONFIG_FILE
        path_lbl = Gtk.Label(label=f"Config file: {CONFIG_FILE}")
        path_lbl.set_xalign(0)
        path_lbl.set_margin_top(8)
        path_lbl.add_css_class("muted")
        path_lbl.set_selectable(True)   # so user can copy the path
        parent.append(path_lbl)

    # ── Change handlers ────────────────────────────────────────────────────────

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


# ── Context-menu builder (module-level for testability) ────────────────────────

def _build_context_menu_for_source(source: str) -> "Gio.Menu":
    """
    Build and return a fresh Gio.Menu for the context slot matching *source*.

    source: "video" | "animate" | "image" | "artgen"

    Module-level so it can be unit-tested without a MainWindow instance.
    Uses Gio and GLib (already imported at top of module) and _DIRECTOR_PINS
    (module-level constant).
    """
    menu = Gio.Menu()

    # Quality (video / animate / image)
    if source in ("video", "animate", "image"):
        quality_section = Gio.Menu()
        for label, steps in [("Fast (10 steps)", "10"),
                              ("Standard (30 steps)", "30"),
                              ("High Quality (40 steps)", "40")]:
            item = Gio.MenuItem.new(label, "win.quality")
            item.set_attribute_value("target", GLib.Variant("s", steps))
            quality_section.append_item(item)
        menu.append_section("Quality", quality_section)

    # Sleep After (all sources)
    sleep_section = Gio.Menu()
    for label, val in [("Never", "0"), ("After 10 completions", "10"),
                       ("After 20 completions", "20"), ("After 50 completions", "50")]:
        item = Gio.MenuItem.new(label, "win.sleep-after")
        item.set_attribute_value("target", GLib.Variant("s", val))
        sleep_section.append_item(item)
    menu.append_section("Sleep After", sleep_section)

    # Director Style (video / image only)
    if source in ("video", "image"):
        dir_prob_section = Gio.Menu()
        for label, pct in [("Never", "0"), ("Sometimes (33%)", "33"),
                           ("Often (66%)", "66"), ("Always", "100")]:
            item = Gio.MenuItem.new(label, "win.director-prob")
            item.set_attribute_value("target", GLib.Variant("s", pct))
            dir_prob_section.append_item(item)
        menu.append_section("Director Style", dir_prob_section)

    # Pinned Director (video only)
    if source == "video":
        pin_section = Gio.Menu()
        for display, full in _DIRECTOR_PINS:
            item = Gio.MenuItem.new(display or "Random", "win.director-pin")
            item.set_attribute_value("target", GLib.Variant("s", full))
            pin_section.append_item(item)
        menu.append_section("Pinned Director", pin_section)

    # Art auto-generate (removed SP-3d-5 — was ArtgenPanel-sidebar-only, an
    # ACCEPTED FLAGGED loss; see CLAUDE.md and .superpowers/sdd/task-5-report.md).
    # Advanced Settings (removed SP-3d-5 — was ControlPanel-only;
    # superseded by the per-medium collapsed "Controls (N)" Gtk.Expander
    # in Create/Remix, see CLAUDE.md's "Create surface" section).

    return menu


# ── Main Window ────────────────────────────────────────────────────────────────

class MainWindow(Gtk.ApplicationWindow):
    """Top-level window: owns client, store, workers, and the prompt queue."""

    def __init__(self, app: Gtk.Application, server_url: str = "http://localhost:8000",
                 prompt_server_url: str = "http://127.0.0.1:8001",
                 inventory_url: str = ""):
        super().__init__(application=app, title="TT Local Generator")
        # Default size is the un-maximized fallback (used if the user restores
        # the window down). The app should open maximized every launch — but
        # calling maximize() before the surface is realized/mapped is racy on
        # Wayland (GTK4 only queues the request and may drop it), which is why
        # it was inconsistent. Defer to a one-shot "map" handler so the request
        # is issued once the toplevel actually exists.
        self.set_default_size(1280, 800)
        self._did_initial_maximize = False
        self.connect("map", self._maximize_on_first_map)

        self._alive: bool = True   # set False in do_close_request; guards idle_add callbacks
        self._flash_restore_id: int = 0   # GLib timer id for pending _flash_status restore
        self._flash_baseline: str = ""    # status label text captured before current flash burst
        self._client = APIClient(server_url)
        self._prompt_server_url = prompt_server_url
        self._inventory_url = inventory_url  # e.g. "http://remote:8002" or "" for local-only
        # Patch generate_prompt module globals so LLM calls hit the right host.
        prompt_client.configure_llm_url(prompt_server_url)
        self._store = HistoryStore()
        self._worker: Optional[threading.Thread] = None
        self._worker_gen: Optional[GenerationWorker] = None
        self._queue: list = []
        self._server_proc: Optional[subprocess.Popen] = None  # running start/stop script subprocess
        # Track which gallery owns the current pending card (set in _on_generate,
        # used in _on_finished/_on_error to update the right gallery).
        self._gen_gallery = None
        # True while a generation launched from the Create surface is in
        # flight — set in _on_create_generate, cleared in _on_finished/
        # _on_error. When True, _on_generate skips the gallery's own pending
        # card (CreateResultPanel owns that UI instead) and the worker
        # callbacks (_on_progress/_on_finished/_on_error) also forward to
        # `self._create_view._result_panel`.
        self._create_job_active = False
        self._log_tail_stop: "threading.Event | None" = None  # set to stop server log tail
        self._attractor_win: "attractor.AttractorWindow | None" = None
        self._prompt_gen_system_prompt: str = self._load_prompt_gen_system()
        # Settings-backed state
        self._gen_completed_count: int = 0          # incremented in _on_finished; triggers sleep
        self._screensaver_inhibit_cookie: "int | None" = None  # D-Bus inhibit cookie
        self._prefs_dialog: "PreferencesDialog | None" = None  # singleton instance
        self._last_error_log_path: "str | None" = None  # log path from most recent error
        self._log_viewer_win: "LogViewerWindow | None" = None  # singleton log viewer
        # Remote inventory records fetched from the inventory server (if running).
        # These are shown alongside local records; keyed by record ID to avoid duplicates.
        self._remote_records: dict = {}  # {record.id: GenerationRecord}
        # Pipeline Studio (SP-C): constructed lazily on first activation of the
        # "Pipelines" toolbar toggle (see _show_pipelines) since it scans run
        # history on construction — no need to pay that cost at startup.
        self._pipeline_studio = None  # type: "PipelineStudio | None"
        # Guards _on_pipelines_toggled against recursing back into
        # _hide_pipelines when _uncheck_pipelines_toggle_if_active programmatically
        # unchecks the Pipelines toggle (see _sync_gallery_to_source / _on_pipelines_toggled).
        self._pipelines_toggle_syncing = False

        # ModelStatusService (SP-2 Task 1): the single "is a model on" poller,
        # unifying server_manager health, artgen endpoint detection, and port
        # probing behind one status map. Constructed and started here, BEFORE
        # `_build_ui()` (which constructs CreateView below), so the service
        # instance exists in time to be injected into CreateView. Stopped in
        # `do_close_request` alongside the other background pollers.
        self._status_service = ModelStatusService()
        self._status_service.start()

        self._build_ui()
        # Now that _build_ui() has constructed everything the loop nav's
        # "toggled" handlers touch (_gallery_stack, _detail_wrap,
        # _pipelines_btn), it's safe to activate Create — the loop's default
        # landing movement. Fires _on_loop_nav_create(), which is a no-op-safe
        # re-application of the state _build_ui already left things in.
        self._loop_nav_create_btn.set_active(True)
        self._load_history()
        self._rebuild_playlists_menu()   # populate Playlists menu after history is loaded
        self._restore_queue()
        # SP-3d-6: `_hw_statusbar` is now driven ENTIRELY by ModelStatusService.
        # The three legacy pollers that used to feed it (`_health_loop`/
        # `_artgen_health_loop`/`_prompt_gen_health_loop`, each with its own
        # background thread hitting a different port) are retired -- one
        # status dot, one source of truth. Subscribe LAST (mirrors
        # ServersControl.__init__'s own ordering) so a synchronous notify
        # from within subscribe() can never hit a half-built widget, then
        # paint the already-known snapshot immediately since subscribe()
        # only pushes on the *next* change.
        self._status_agg_prev = Status.OFF
        self._status_unsubscribe = self._status_service.subscribe(
            lambda snap: GLib.idle_add(self._on_status_snapshot, snap)
        )
        self._on_status_snapshot(self._status_service.snapshot())
        # "animatediff" is a hardware capability with no server_manager.SERVERS
        # entry (see CAPABILITY_LABELS's comment), so ModelStatusService never
        # resolves a status for it -- check it once, standalone, same as the
        # old `_start_artgen_health_worker`'s nested one-shot thread did.
        self._check_animatediff_hardware()
        # Pre-warm transform availability cache off the main thread so the first
        # right-click doesn't block while importing plugin modules (torch etc.).
        threading.Thread(
            target=lambda: [_transform_available(k) for k in ("rmbg", "blip", "depth", "ansi-image")],
            daemon=True,
        ).start()
        if self._inventory_url:
            self._start_inventory_fetch()
        # SP-3d-5: the ControlPanel-only startup steps that used to live here
        # (sync_quality_btn_to_steps / switch_to_source / _set_model, driven
        # by `quality_steps` and `last_successful_deployment` settings) are
        # gone with the class — Create reads those settings directly, and
        # `_current_medium_source()` derives "what am I making" from
        # CreateView's own active-medium state instead of a startup pre-select.

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

        # ── Standalone Servers control (SP-3b Task 2) ─────────────────────────
        # ControlPanel (its own "Servers ▾" popover, server-status box, and
        # server-log revealer) is deleted (SP-3d-5) — this MainWindow-owned
        # widget, driven by ModelStatusService (3-state dots via subscribe(),
        # not a standalone poll), is now the ONLY servers control in the window.
        self._servers_control = ServersControl(
            self._status_service,
            on_start=self._on_servers_control_start,
            on_stop=self._on_servers_control_stop,
            on_restart=self._on_servers_control_restart,
        )

        # ── Loop nav: Create · Curate · Discover · Remix ────────────────────────
        # The new top-level nav (SP-C Task 1 — see docs/superpowers/specs/
        # 2026-07-13-create-surface-design.md). Appended FIRST so it sits above
        # everything else. Its default-active button isn't set yet — that
        # happens at the end of
        # __init__, once _gallery_stack / _detail_wrap /
        # _pipelines_btn (built further below) exist for the "toggled" handler
        # to touch safely.
        #
        # SP-3d-4: this row is now the window's ONLY top bar. It used to sit
        # above a second row — ControlPanel's own `toolbar_box` (logo/title +
        # the medium-tab source toggle) — onto which MainWindow appended these
        # same three buttons (Watch TT-TV / Pipelines / Servers ▾). That toggle
        # is superseded by CreateView's medium doors/chips (see CLAUDE.md's
        # "Create surface" section), so `toolbar_box` is no longer read or
        # mounted at all — these MainWindow-owned buttons fold directly into
        # the loop-nav row instead, which becomes the surviving top bar.
        loop_nav_row = self._build_loop_nav()
        root_box.append(loop_nav_row)

        # Build and register menu actions before creating the bar
        self._build_menu_actions()

        # ── Pipelines: set apart from the four-verb loop as the advanced tool ──
        _loop_div = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        _loop_div.add_css_class("loop-nav-divider")
        loop_nav_row.append(_loop_div)

        # ── Pipelines nav entry ────────────────────────────────────────────────
        # Deliberately NOT part of ControlPanel's video/animate/image/artgen
        # radio group: those buttons drive _set_source's generation-mode state
        # (_model_source, prompt placeholder, chip vocab, model rows, ...) which
        # has nothing to do with browsing already-finished runs. Keeping this
        # button independent means _set_source and the source toggle group are
        # completely untouched by this feature — see task-5-brief.md.
        self._pipelines_btn = Gtk.ToggleButton(label="🧩 Pipelines")
        self._pipelines_btn.add_css_class("attractor-launch-btn")
        self._pipelines_btn.set_tooltip_text(
            "Pipeline Studio — browse finished multi-step runs and open one\n"
            "to see every step (Discover + Open)."
        )
        self._pipelines_btn.connect("toggled", self._on_pipelines_toggled)
        loop_nav_row.append(self._pipelines_btn)

        # Standalone Servers control's "Servers ▾" button (SP-3b Task 2) —
        # mounted here instead of ControlPanel's own (now-hidden) one.
        # Pinned to the RIGHT edge of the window: an expanding spacer between
        # the loop-nav verbs (left) and Servers (right) pushes it hard-right,
        # separating the primary navigation from the machine-control popover
        # (a distinct concern) — the conventional header-bar placement for a
        # secondary/utility control.
        _nav_spacer = Gtk.Box()
        _nav_spacer.set_hexpand(True)
        loop_nav_row.append(_nav_spacer)
        loop_nav_row.append(self._servers_control.servers_button)

        # ── App menu bar ──────────────────────────────────────────────────────
        self._menu_bar = self._build_menu_bar()
        self._context_menu_source: str = ""   # last source built; skip rebuild if unchanged
        self._rebuild_context_menu("video")
        root_box.append(self._menu_bar)

        # ── Two-pane layout: gallery | detail (SP-3d-4) ───────────────────────
        # A horizontal Gtk.Paned split (gallery | detail) — the window's only
        # paned. ControlPanel (and the 3-pane controls|gallery|detail split it
        # used to anchor) is gone entirely as of SP-3d-5.
        inner_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        inner_paned.set_vexpand(True)
        inner_paned.set_position(480)   # default gallery width before detail panel
        self._inner_paned = inner_paned  # stored so _sync_gallery_to_source can adjust split
        root_box.append(inner_paned)

        gallery_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Native-medium galleries + the artgen gallery, switched via Gtk.Stack.
        self._gallery_stack = Gtk.Stack()
        self._gallery_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._gallery_stack.set_transition_duration(150)
        self._gallery_stack.set_hexpand(True)
        self._gallery_stack.set_vexpand(True)
        # Report only the visible child's minimum width, not the max of all children,
        # so the window doesn't inflate past set_default_size(1280, 800).
        self._gallery_stack.set_hhomogeneous(False)

        shared_cbs = dict(
            select_cb=self._on_card_selected,
            delete_cb=self._on_delete_card,
            remix_cb=self._on_remix_card,
            star_cb=self._on_star,
            transform_cb=self._on_transform_card,
            remix_as_pipeline_cb=self._remix_as_pipeline,
        )
        self._video_gallery   = GalleryWidget(**shared_cbs, media_type="video")
        self._animate_gallery = GalleryWidget(**shared_cbs, media_type="animate")
        self._image_gallery   = GalleryWidget(**shared_cbs, media_type="image")
        # SP-3d-5: ArtgenPanel (its generation sidebar + `_check_health_bg`
        # poller) is deleted — Discover keeps browsing artgen media through
        # the standalone ArtgenGallery it always wrapped, wired the same way
        # the three native GalleryWidgets are above.
        self._artgen_gallery = ArtgenGallery()
        self._artgen_gallery.on_remix = self._on_remix_card
        self._artgen_gallery.on_remix_as_pipeline = self._remix_as_pipeline
        # Unify-gallery-interaction-pattern Task 3: card selection routes into
        # the shared right-pane `ArtgenDetail` (self._artgen_detail, built
        # below) via self._right_stack -- see _on_artgen_card_selected /
        # _on_artgen_card_deleted. This replaces the in-page overlay preview
        # ArtgenGallery used to own itself (a crash workaround; see CLAUDE.md
        # and the removed Overlay comment this superseded).
        self._artgen_gallery.on_card_activated = self._on_artgen_card_selected
        self._artgen_gallery.on_card_deleted = self._on_artgen_card_deleted
        self._gallery_stack.add_named(self._video_gallery, "video")
        self._gallery_stack.add_named(self._animate_gallery, "animate")
        self._gallery_stack.add_named(self._image_gallery, "image")
        self._gallery_stack.add_named(self._artgen_gallery, "artgen")
        # Defer the initial artgen gallery load (ArtgenPanel used to do the
        # same in its own _build()) so it runs after the window paints —
        # _rebuild_grid() with 100+ artgen records takes ~250 ms synchronously.
        GLib.idle_add(self._artgen_gallery.refresh)

        # ── Discover media-type switcher (SP-3d-6 regression fix) ───────────
        # SP-3d-5 deleted the legacy medium-tab source toggle -- the ONLY UI
        # that used to let you pick which gallery `_gallery_stack` showed.
        # That left Discover pinned to whatever `_current_medium_source()`
        # (CreateView's active medium) happened to be, with no way to ask for
        # a different one -- the animate and artgen galleries became
        # completely unreachable. This row is Discover's OWN switcher (built
        # once here, like `_build_loop_nav`); it is shown/hidden by
        # `_on_loop_nav_discover`/`_on_loop_nav_create`/`_on_loop_nav_remix`
        # below and reuses `_sync_gallery_to_source` unchanged for the actual
        # stack-switch, so star/playlist/detail/context-menu wiring for
        # whichever gallery is showing is completely untouched.
        self._discover_type_row = self._build_discover_type_row()
        self._discover_type_row.set_visible(False)
        gallery_wrap.append(self._discover_type_row)

        # Create-surface (docs/superpowers/specs/2026-07-13-create-surface-
        # design.md): CreateView is built and mounted here ALONGSIDE the
        # existing medium-tab UI above, as the `_gallery_stack` "create"
        # child. As of the Task 8 switchover it IS the reachable Create
        # surface — the loop-nav ✨ Create verb switches `_gallery_stack` to
        # "create" (see `_on_loop_nav_create`). The legacy medium-tab UI
        # ("video"/"animate"/"image"/"artgen") is intentionally left mounted
        # as a still-reachable fallback; deleting it is a later task. Real
        # generation (GenerationWorker/api_client/ControlPanel) is untouched —
        # CreateView's `on_create` seam is wired to `_on_create_generate`
        # (below), which only translates a chosen medium + params into the
        # SAME generation calls the old UI already makes.
        #
        # Task 7: the inspiration door DOES get wired to something real —
        # `_on_loop_nav_remix` already does exactly the unseeded `show_muse()`
        # activation dance (toggle/activate Pipelines, then
        # `self._pipeline_studio.show_muse(seed_artifact=None)`), so it's
        # reused verbatim as CreateView's zero-arg `on_inspiration` callable
        # rather than reimplementing the Muse hand-off here.
        #
        # Task 8 (migration-safe switchover subset — see
        # .superpowers/sdd/task-8-report.md): the Create CTA now routes to
        # REAL generation via `_on_create_generate`, which translates the
        # chosen medium + collected params into the exact same `_on_generate`
        # call (native mediums) or the exact same `tt-ctl artgen ...`
        # subprocess pattern `pipeline_engine._h_artgen_generate` already
        # uses (artgen mediums) — no new generation code, no reimplemented
        # worker launching.
        self._create_view = CreateView(
            on_inspiration=self._on_loop_nav_remix,
            on_create=self._on_create_generate,
            status_service=self._status_service,
            inspire_fn=self._create_inspire_fn,
            # SP-3d-1: Theme Set migrated into Create (see `_on_create_theme_set`
            # below) — reuses `generate_theme.generate_theme()`, the SAME
            # theme-expansion backend ControlPanel's now-legacy "🎬 Theme Set"
            # button drove, only the launch UI has moved.
            on_theme_set=self._on_create_theme_set,
        )
        # SP-3c-4: `_restore_queue()` (called earlier in `__init__`, before
        # `self._create_view` existed) may already have repopulated
        # `self._queue` from a crash/restart — `_refresh_create_queue_display`
        # no-op'd at that point (`getattr(self, "_create_view", None)` was
        # None). Sync the Create surface's pending-queue display now that the
        # view exists, so a restored queue shows up there too, not just in
        # the legacy queue box.
        self._refresh_create_queue_display()
        # CreateView is a tall vertical surface (doors + role zones + model
        # door + CTA). Unlike the gallery children (which scroll internally),
        # it must be wrapped in a vertical scroller or its lower elements —
        # including the Create button — become unreachable when the content is
        # taller than the window. Horizontal never scrolls: CreateView already
        # clamps its own width via gtk_layout.wrap_centered.
        create_scroll = Gtk.ScrolledWindow()
        create_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        create_scroll.set_vexpand(True)
        create_scroll.set_child(self._create_view)
        self._gallery_stack.add_named(create_scroll, "create")

        self._gallery_stack.set_visible_child_name("video")

        # Apply saved gallery density preference on startup.  The default
        # "comfortable" requires no work (it is the widget's natural size);
        # only call _apply_gallery_density when a non-default value was saved.
        _density = _gallery_density()
        if _density != "comfortable":
            self._apply_gallery_density(_density)

        # art-autogen menu-action sync removed alongside the action itself
        # (SP-3d-5 — ArtgenPanel deleted; see CLAUDE.md's flagged loss note).

        gallery_wrap.append(self._gallery_stack)

        # ── Selection-mode banner (hidden until user edits a playlist) ─────────
        # A slide-down Gtk.Revealer containing a banner with the playlist name,
        # an "Add Selected" button, and a Cancel (✕) button.
        self._selection_banner_revealer = Gtk.Revealer()
        self._selection_banner_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN
        )
        self._selection_banner_revealer.set_transition_duration(180)

        banner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        banner_box.add_css_class("selection-banner")
        banner_box.set_margin_start(12)
        banner_box.set_margin_end(12)
        banner_box.set_margin_top(4)
        banner_box.set_margin_bottom(4)

        self._selection_banner_lbl = Gtk.Label(label="")
        self._selection_banner_lbl.add_css_class("selection-banner-label")
        self._selection_banner_lbl.set_hexpand(True)
        self._selection_banner_lbl.set_xalign(0)
        banner_box.append(self._selection_banner_lbl)

        self._selection_add_btn = Gtk.Button(label="Add Selected")
        self._selection_add_btn.add_css_class("selection-add-btn")
        self._selection_add_btn.connect("clicked", self._on_selection_add)
        banner_box.append(self._selection_add_btn)

        cancel_btn = Gtk.Button(label="✕ Cancel")
        cancel_btn.add_css_class("selection-cancel-btn")
        cancel_btn.connect("clicked", lambda _: self._exit_selection_mode())
        banner_box.append(cancel_btn)

        self._selection_banner_revealer.set_child(banner_box)
        gallery_wrap.append(self._selection_banner_revealer)

        # Narrow status label for generation progress messages (above status bar)
        self._status_lbl = Gtk.Label(label="Ready")
        self._status_lbl.set_xalign(0)
        self._status_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self._status_lbl.set_hexpand(True)
        self._status_lbl.set_max_width_chars(1)   # let hexpand+ellipsize control width, not content
        self._status_lbl.add_css_class("status-bar")
        gallery_wrap.append(self._status_lbl)

        _status_click = Gtk.GestureClick()
        _status_click.connect("released", self._on_status_bar_clicked)
        self._status_lbl.add_controller(_status_click)

        inner_paned.set_start_child(gallery_wrap)
        inner_paned.set_shrink_start_child(False)

        self._detail = DetailPanel(
            download_cb=lambda rec_id, dest: self._client.download(rec_id, Path(dest)),
            on_localized_cb=self._on_remote_record_localized,
            star_cb=self._on_star,
        )

        # Unify-gallery-interaction-pattern Task 2: the right-hand detail pane
        # is a dual-renderer container -- a Gtk.Stack holding both
        # `DetailPanel` (native video/image/animate records, unchanged above)
        # and `ArtgenDetail` (artgen records).
        #
        # Task 3 finishes the wiring Task 2 deferred (it needed
        # self._artgen_gallery, constructed earlier in __init__, above): the
        # artgen gallery's own in-page detail overlay -- a crash workaround,
        # see CLAUDE.md's "unify gallery interaction pattern" notes -- is
        # gone; card selection/deletion now route through this shared
        # ArtgenDetail via _on_artgen_card_selected/_on_artgen_card_deleted/
        # _on_artgen_detail_deleted/_on_artgen_detail_starred below. This is
        # safe because self._right_stack is a SIBLING subtree of the gallery
        # grid (both live under inner_paned, not one inside the other) --
        # switching it never unmaps the FlowBox/grid, so it cannot reproduce
        # the segfault the removed Overlay was a workaround for.
        self._artgen_detail = ArtgenDetail()
        self._artgen_detail.on_remix = self._on_remix_card
        self._artgen_detail.on_remix_as_pipeline = self._remix_as_pipeline
        self._artgen_detail.on_back = lambda: self._set_detail_pane_visible(False)
        self._artgen_detail.on_deleted = self._on_artgen_detail_deleted
        self._artgen_detail.on_starred = self._on_artgen_detail_starred

        self._right_stack = Gtk.Stack()
        self._right_stack.add_named(self._detail, "native")
        self._right_stack.add_named(self._artgen_detail, "artgen")
        self._right_stack.set_visible_child_name("native")

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

        self._detail_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Thin dismiss bar at the top of the detail pane — ← collapses it.
        # Visible universally (not just Pipeline) so users can always reclaim space.
        _detail_close_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        _detail_close_bar.add_css_class("detail-close-bar")
        _detail_close_spacer = Gtk.Box()
        _detail_close_spacer.set_hexpand(True)
        _detail_close_bar.append(_detail_close_spacer)
        _detail_close_btn = Gtk.Button(label="✕")
        _detail_close_btn.add_css_class("flat")
        _detail_close_btn.set_tooltip_text("Close detail pane")
        _detail_close_btn.connect(
            "clicked", lambda _: self._set_detail_pane_visible(False)
        )
        _detail_close_bar.append(_detail_close_btn)
        self._detail_wrap.append(_detail_close_bar)

        self._detail_wrap.append(self._right_stack)
        self._detail_wrap.append(self._queue_section_lbl)
        self._detail_wrap.append(self._queue_box)

        inner_paned.set_end_child(self._detail_wrap)
        inner_paned.set_shrink_end_child(False)

        # Standalone Servers control's server-log revealer (SP-3b Task 2,
        # fixed after review) — mounted directly into `root_box`, a
        # persistent container independent of the gallery/detail paned above.
        # Deliberately mounts ONLY `log_widget`, not
        # `status_bar` — see servers_control.py's module docstring and
        # task-2-report.md's "Issue 2": this window already has an aggregate
        # server dot below (`_hw_statusbar`, fed by the older per-tab health
        # loop), and showing ServersControl's OWN aggregate dot too would
        # put two disagreeing "is a server on" answers on screen at once.
        # Collapsed (invisible) by default — a `Gtk.Revealer` with
        # reveal_child=False — so it costs no vertical space until a
        # start/stop/restart actually streams output into it.
        root_box.append(self._servers_control.log_widget)

        # ── Hardware / infra status bar (pinned to window bottom) ─────────────
        # Clicking the server segment opens a popover with Start / Stop controls.
        # start_cb resolves via `_current_medium_source()` (CreateView's active
        # medium) so it always reflects the current medium, not ControlPanel.
        # SP-3d-6 (DONE): the per-active-tab boolean health pollers that used to
        # feed this bar's dot (`_health_loop`/`_artgen_health_loop`/
        # `_prompt_gen_health_loop`, via `update_server`/`update_capability`)
        # are retired. `_render_status_snapshot` (subscribed to
        # `self._status_service` right after `_build_ui()` returns, in
        # `__init__`) now drives this same dot from the identical
        # `ModelStatusService` snapshot `ServersControl` already reads --
        # the single remaining aggregate server dot in the whole window is
        # driven by the single remaining source of truth. `_StatusBar` itself
        # (this class) is unchanged; only its data source changed.
        self._hw_statusbar = _StatusBar(
            start_cb=lambda: self._on_start_server(self._current_medium_source()),
            stop_cb=self._on_stop_server,
        )
        root_box.append(self._hw_statusbar)

    def _set_status(self, text: str) -> None:
        """Update status bar. Safe to call from main thread only."""
        self._status_lbl.set_label(text)

    def _on_status_bar_clicked(self, _gesture, _n_press, _x, _y) -> None:
        """Open log viewer to the most recent error log when status bar is clicked."""
        if self._last_error_log_path:
            self._open_log_viewer(self._last_error_log_path)
        elif self._status_lbl.get_label().startswith("Error"):
            self._open_log_viewer()

    def _flash_status(self, message: str, duration_ms: int = 1500) -> None:
        """Show *message* in the status label for *duration_ms* ms, then restore.

        Overlapping calls cancel the pending restore to preserve the pre-flash
        baseline.  The baseline is captured only on the *first* call in a burst;
        subsequent calls within the same burst cancel the previous timer and
        extend the duration without losing the original label text.
        """
        if self._flash_restore_id:
            GLib.source_remove(self._flash_restore_id)
            self._flash_restore_id = 0
        else:
            # First flash in this burst — capture the true baseline label
            self._flash_baseline = self._status_lbl.get_label()
        self._status_lbl.set_label(message)
        def _restore() -> bool:
            self._flash_restore_id = 0
            if self._alive:
                self._status_lbl.set_label(self._flash_baseline)
            return GLib.SOURCE_REMOVE
        self._flash_restore_id = GLib.timeout_add(duration_ms, _restore)

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

        sync = Gio.SimpleAction.new("sync-from-server", None)
        sync.connect("activate", lambda *_: self._on_sync_from_server())
        self.add_action(sync)

        refresh_inv = Gio.SimpleAction.new("refresh-remote-library", None)
        refresh_inv.connect("activate", lambda *_: self._on_refresh_remote_library())
        self.add_action(refresh_inv)

        # Advanced-settings action removed SP-3d-5 (ControlPanel-only; see
        # AdvancedSettingsDialog deletion note above).

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

        # ── Playlists ─────────────────────────────────────────────────────────
        pl_all = Gio.SimpleAction.new("playlist-all", None)
        pl_all.connect("activate", lambda *_: self._on_open_attractor_for_playlist(None))
        self.add_action(pl_all)

        pl_play = Gio.SimpleAction.new("playlist-play", GLib.VariantType.new("s"))
        pl_play.connect("activate", lambda _a, p: self._on_open_attractor_for_playlist(p.get_string()))
        self.add_action(pl_play)

        pl_model = Gio.SimpleAction.new("playlist-model", GLib.VariantType.new("s"))
        pl_model.connect("activate", lambda _a, p: self._on_open_attractor_for_model(p.get_string()))
        self.add_action(pl_model)

        pl_new = Gio.SimpleAction.new("playlist-new", None)
        pl_new.connect("activate", lambda *_: self._on_playlist_new())
        self.add_action(pl_new)

        pl_delete = Gio.SimpleAction.new("playlist-delete", GLib.VariantType.new("s"))
        pl_delete.connect("activate", lambda _a, p: self._on_playlist_delete(p.get_string()))
        self.add_action(pl_delete)

        # ── View: gallery density (radio) ─────────────────────────────────────
        density_val = _gallery_density()
        gallery_density_action = Gio.SimpleAction.new_stateful(
            "gallery-density",
            GLib.VariantType.new("s"),
            GLib.Variant("s", density_val),
        )
        gallery_density_action.connect("activate", self._on_gallery_density_action)
        self.add_action(gallery_density_action)

        # Art auto-generate actions ("art-autogen"/"art-autogen-delay") removed
        # SP-3d-5 — ArtgenPanel-sidebar-only feature, an ACCEPTED FLAGGED loss
        # (overlaps the surviving TT-TV attractor). See CLAUDE.md and
        # .superpowers/sdd/task-5-report.md.

        # -- Debug: log viewer ------------------------------------------------
        open_log_viewer_action = Gio.SimpleAction.new("open-log-viewer", None)
        open_log_viewer_action.connect("activate", lambda *_: self._open_log_viewer())
        self.add_action(open_log_viewer_action)

        open_logs_folder_action = Gio.SimpleAction.new("open-logs-folder", None)
        open_logs_folder_action.connect("activate", self._on_open_logs_folder)
        self.add_action(open_logs_folder_action)

    def _build_menu_bar(self) -> Gtk.PopoverMenuBar:
        """Build the PopoverMenuBar.

        Structure: File · Playlists · View ·· [context slot]
        The context slot (last entry) is rebuilt by _rebuild_context_menu()
        each time the source tab changes.
        """
        self._menumodel = Gio.Menu()

        # ── File ─────────────────────────────────────────────────────────
        file_menu = Gio.Menu()
        file_menu.append("Open Media Folder", "win.open-media-folder")
        file_menu.append_section(None, Gio.Menu())
        file_menu.append("Recover Jobs…", "win.recover-jobs")
        file_menu.append("Refresh Remote Library", "win.refresh-remote-library")
        file_menu.append("Download Remote Library…", "win.sync-from-server")
        file_menu.append_section(None, Gio.Menu())
        file_menu.append("Preferences…", "win.preferences")
        file_menu.append("Quit", "app.quit")
        self._menumodel.append_submenu("File", file_menu)

        # ── Playlists ─────────────────────────────────────────────────────
        # Keep mutable section references so _rebuild_playlists_menu() can
        # clear and repopulate them without rebuilding the whole menu model.
        pl_menu = Gio.Menu()
        pl_menu.append("Watch All Videos", "win.playlist-all")
        self._playlists_model_section = Gio.Menu()
        pl_menu.append_section("By Model", self._playlists_model_section)
        self._playlists_playlist_section = Gio.Menu()
        pl_menu.append_section("Your Playlists", self._playlists_playlist_section)
        pl_manage = Gio.Menu()
        pl_manage.append("New Playlist…", "win.playlist-new")
        pl_menu.append_section(None, pl_manage)
        self._menumodel.append_submenu("Playlists", pl_menu)

        # ── View ───────────────────────────────────────────────────────────
        view_menu = Gio.Menu()
        toggle_section = Gio.Menu()
        toggle_section.append("Detail Panel", "win.toggle-detail")
        view_menu.append_section(None, toggle_section)

        density_section = Gio.Menu()
        for label, val in [("Comfortable", "comfortable"), ("Compact", "compact")]:
            item = Gio.MenuItem.new(label, "win.gallery-density")
            item.set_attribute_value("target", GLib.Variant("s", val))
            density_section.append_item(item)
        view_menu.append_section("Gallery Density", density_section)
        self._menumodel.append_submenu("View", view_menu)

        # -- Debug ------------------------------------------------------------
        debug_menu = Gio.Menu()
        debug_menu.append("Open Log Viewer", "win.open-log-viewer")
        debug_menu.append("Open Logs Folder…", "win.open-logs-folder")
        self._menumodel.append_submenu("Debug", debug_menu)

        # ── Context slot placeholder (replaced by _rebuild_context_menu) ────
        self._context_slot_idx = self._menumodel.get_n_items()
        self._context_menu_model = Gio.Menu()
        self._menumodel.append_submenu("🎥 Video", self._context_menu_model)

        bar = Gtk.PopoverMenuBar.new_from_model(self._menumodel)
        self._apply_context_menu_css(bar)
        return bar

    def _apply_context_menu_css(self, bar: Gtk.PopoverMenuBar) -> None:
        """Add context-menu-item CSS class to the last (context slot) item."""
        child = bar.get_last_child()
        if child is not None:
            child.add_css_class("context-menu-item")

    def _rebuild_context_menu(self, source: str) -> None:
        """Replace the context slot title and contents for the given source tab.

        Clears self._context_menu_model in-place and repopulates it, then
        replaces the submenu title by remove+insert_submenu on self._menumodel.
        The PopoverMenuBar reflects the change immediately.
        """
        if getattr(self, "_context_menu_source", None) == source:
            return   # nothing changed — skip the relayout
        self._context_menu_source = source
        _TITLES = {
            "video":   "\U0001f3a5 Video",
            "animate": "\U0001f483 Animate",
            "image":   "\U0001f5bc️ Image",
            "artgen":  "\U0001f3a8 Art",
        }
        title = _TITLES.get(source, "\U0001f3a5 Video")

        # Rebuild context_menu_model contents in-place
        self._context_menu_model.remove_all()
        fresh = _build_context_menu_for_source(source)
        for i in range(fresh.get_n_items()):
            section = fresh.get_item_link(i, "section")
            submenu = fresh.get_item_link(i, "submenu")
            if section:
                label_v = fresh.get_item_attribute_value(
                    i, "label", GLib.VariantType.new("s"))
                self._context_menu_model.append_section(
                    label_v.get_string() if label_v else None, section)
            elif submenu:
                label_v = fresh.get_item_attribute_value(
                    i, "label", GLib.VariantType.new("s"))
                self._context_menu_model.append_submenu(
                    label_v.get_string() if label_v else None, submenu)
            else:
                lv = fresh.get_item_attribute_value(i, "label", GLib.VariantType.new("s"))
                av = fresh.get_item_attribute_value(i, "action", GLib.VariantType.new("s"))
                if lv and av:
                    self._context_menu_model.append(lv.get_string(), av.get_string())

        # Update the submenu title in the top-level menu model
        self._menumodel.remove(self._context_slot_idx)
        self._menumodel.insert_submenu(self._context_slot_idx, title,
                                       self._context_menu_model)

        # Re-mark the context CSS class on the last bar item
        self._apply_context_menu_css(self._menu_bar)

    # ── Playlist menu helpers ──────────────────────────────────────────────────

    def _rebuild_playlists_menu(self) -> None:
        """Repopulate the dynamic By Model and Your Playlists menu sections."""
        from playlist_store import playlist_store as _ps

        # ── By Model ──────────────────────────────────────────────────────────
        self._playlists_model_section.remove_all()
        records = self._store.all_records()
        counts: dict[str, int] = {}
        for r in records:
            mid = getattr(r, "model", "") or ""
            # Skip workflow-runner records — their model_id is "workflow" (or a
            # workflow-prefixed variant like "workflow-v2").  These are pipeline
            # artifacts, not direct inference generations, so they should not
            # appear as standalone model entries in the By Model menu.
            if mid.startswith("workflow"):
                continue
            if mid and getattr(r, "media_type", "video") != "image":
                counts[mid] = counts.get(mid, 0) + 1
        for mid, cnt in sorted(counts.items()):
            short = mid.split("/")[-1]   # strip HF org prefix if present
            item = Gio.MenuItem.new(f"{short} ({cnt})", "win.playlist-model")
            item.set_attribute_value("target", GLib.Variant("s", mid))
            self._playlists_model_section.append_item(item)

        # ── Your Playlists ────────────────────────────────────────────────────
        self._playlists_playlist_section.remove_all()
        for pl in _ps.all():
            cnt = len(pl.record_ids)
            label = f"{pl.name} ({cnt} video{'s' if cnt != 1 else ''})"
            # Each playlist gets a submenu: Watch ▶ | Delete…
            sub = Gio.Menu()
            watch_item = Gio.MenuItem.new("Watch ▶", "win.playlist-play")
            watch_item.set_attribute_value("target", GLib.Variant("s", pl.id))
            sub.append_item(watch_item)
            del_item = Gio.MenuItem.new("Delete…", "win.playlist-delete")
            del_item.set_attribute_value("target", GLib.Variant("s", pl.id))
            sub.append_item(del_item)
            self._playlists_playlist_section.append_submenu(label, sub)

    def _on_playlist_new(self) -> None:
        """Show a name-entry dialog and create the playlist, then enter selection mode."""
        dialog = Gtk.Dialog(title="New Playlist", modal=True)
        dialog.set_transient_for(self)
        dialog.set_default_size(300, -1)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        ok_btn = dialog.add_button("Create", Gtk.ResponseType.OK)
        ok_btn.add_css_class("suggested-action")
        ok_btn.set_sensitive(False)

        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(12)
        content.set_margin_bottom(4)
        content.set_margin_start(12)
        content.set_margin_end(12)
        lbl = Gtk.Label(label="Playlist name:")
        lbl.set_xalign(0)
        content.append(lbl)
        entry = Gtk.Entry()
        entry.set_placeholder_text("e.g. Space Adventures")
        entry.set_activates_default(True)
        content.append(entry)
        entry.connect("changed", lambda _e: ok_btn.set_sensitive(bool(entry.get_text().strip())))

        def _on_response(dlg, resp):
            name = entry.get_text().strip()
            dlg.destroy()
            if resp == Gtk.ResponseType.OK and name:
                from playlist_store import playlist_store as _ps
                pl = _ps.create(name)
                self._rebuild_playlists_menu()
                self._on_enter_selection_mode(pl.id)

        dialog.connect("response", _on_response)
        dialog.present()

    def _on_playlist_delete(self, playlist_id: str) -> None:
        """Show a confirmation dialog then delete the playlist."""
        from playlist_store import playlist_store as _ps
        pl = _ps.get(playlist_id)
        if pl is None:
            return
        dialog = Gtk.MessageDialog(
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=f"Delete playlist ‘{pl.name}’?",
            secondary_text="The videos themselves are not deleted.",
        )
        dialog.set_transient_for(self)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        del_btn = dialog.add_button("Delete", Gtk.ResponseType.ACCEPT)
        del_btn.add_css_class("destructive-action")

        def _on_response(dlg, resp):
            dlg.destroy()
            if resp == Gtk.ResponseType.ACCEPT:
                _ps.delete(playlist_id)
                self._rebuild_playlists_menu()

        dialog.connect("response", _on_response)
        dialog.present()

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

    def _on_open_logs_folder(self, _action, _param) -> None:
        """Open the logs/ directory in the system file manager."""
        from log_viewer import _LOGS_DIR
        logs_uri = f"file://{_LOGS_DIR}"
        try:
            Gio.AppInfo.launch_default_for_uri(logs_uri, None)
        except Exception as e:
            self._set_status(f"Could not open logs folder: {e}")

    def _open_log_viewer(self, path: "str | None" = None) -> None:
        """Open (or present) the singleton LogViewerWindow, optionally jumping to *path*."""
        from log_viewer import LogViewerWindow
        if self._log_viewer_win is None or not self._log_viewer_win.get_visible():
            self._log_viewer_win = LogViewerWindow(parent=self)
        self._log_viewer_win.present()
        if path:
            self._log_viewer_win.open_to(path)

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
        # SP-3d-5: the ControlPanel widget-sync call that used to live here
        # (sync_quality_btn_to_steps) is gone with the class — this setting
        # write is the persistence Create's own Controls zone reads directly.

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

    def _set_detail_pane_visible(self, visible: bool) -> None:
        """Show/hide the detail pane and keep `self._detail_visible` in sync.

        Unify-gallery-interaction-pattern Task 1: single source of truth for
        detail-pane visibility, replacing two call sites (the ✕ dismiss-bar
        button and `win.toggle-detail`) that each used to reach for the
        widget themselves — one via `self._detail_wrap` directly (the ✕
        button's inline lambda), the other via `self._detail.get_parent()`
        (`_on_toggle_detail`, below). The `get_parent()` route is an
        assumption that breaks once a later task inserts a `Gtk.Stack`
        between `_detail_wrap` and `self._detail` to host a second renderer
        — at that point `self._detail.get_parent()` returns the new Stack,
        not `_detail_wrap`, so hiding "the detail pane" would actually hide
        just the Stack (a fixed-size child) while `_detail_wrap` — and the
        space it occupies in `inner_paned` — stayed put. Targeting
        `_detail_wrap` directly here removes that assumption for both
        callers up front.

        Hiding also snaps `inner_paned`'s divider to the window's full
        allocated width — the same repositioning the ✕ button always did —
        so the gallery reclaims the space immediately instead of leaving a
        collapsed-but-still-reserved sliver. Showing does not reposition the
        paned (this mirrors prior behavior: `_on_toggle_detail` never
        repositioned it either, only the ✕ button's hide path did).
        """
        self._detail_visible = visible
        self._detail_wrap.set_visible(visible)
        if not visible and hasattr(self, "_inner_paned"):
            self._inner_paned.set_position(
                self._inner_paned.get_allocation().width
            )
        if not visible:
            # gif-hygiene fix 1: collapsing the whole pane must also pause
            # any GIF timer running inside the shared `_artgen_detail`
            # renderer. A Gtk.Stack keeps its hidden child realized, so
            # without this the timer keeps firing indefinitely on a fully
            # hidden pane (CPU-idle waste; harmless but wasteful). Showing
            # the pane again doesn't need an explicit "resume" -- the next
            # `show_record`/`_render` call restarts the timer itself.
            artgen_detail = getattr(self, "_artgen_detail", None)
            if artgen_detail is not None:
                artgen_detail.pause_animation()

    def _on_toggle_detail(self, action: Gio.SimpleAction, _param) -> None:
        self._set_detail_pane_visible(not self._detail_visible)
        action.set_state(GLib.Variant("b", self._detail_visible))

    def _on_gallery_density_action(self, action: Gio.SimpleAction,
                                    param: GLib.Variant) -> None:
        """Menu: switch gallery card size between comfortable and compact."""
        val = param.get_string()
        action.set_state(GLib.Variant("s", val))
        _settings.set("gallery_density", val)
        self._apply_gallery_density(val)

    def _apply_gallery_density(self, density: str) -> None:
        """
        Resize cards on EVERY gallery (video/image/animate GalleryWidget
        instances, plus the artgen ArtgenGallery) to the SAME density-scaled
        tile size, sourced from gallery_layout.tile_size() -- the single
        source of truth also used to build cards in the first place.  Both
        dimensions are set (not just width, per the historical bug: `-1`
        height meant the fix that made cards a FIXED size at "comfortable"
        density silently reverted to natural/ragged height as soon as the
        user switched to "compact").

        Resizing an ALREADY-BUILT card is NOT just `card.set_size_request()`
        on the outer widget -- that only raises the widget's minimum-size
        FLOOR, and `Gtk.Widget.set_size_request()` can never shrink a widget
        below what its content already needs (the pinned zone built at the
        OLD density still dominates the measured size). The real fix is to
        resize each card's PINNED zone anchor(s) in place via
        `gallery_layout.set_pinned_size()` -- see its docstring. Every
        GenerationCard/PendingCard exposes `._card_zone` (the whole-card
        pin) and GenerationCard additionally exposes `._media_zone` (the
        thumbnail sub-zone); `set_size_request()` is still called too, kept
        in sync purely so `card.get_size_request()` (used by other tests/
        callers) reports the current density's floor as well.
        """
        card_w, card_h = gallery_layout.tile_size(density)
        thumb_w, thumb_h = gallery_layout.thumb_size(density)
        for gallery in (self._video_gallery, self._image_gallery, self._animate_gallery):
            for card in gallery._cards:
                card_zone = getattr(card, "_card_zone", None)
                if card_zone is not None:
                    gallery_layout.set_pinned_size(card_zone, card_w, card_h)
                media_zone = getattr(card, "_media_zone", None)
                if media_zone is not None:
                    gallery_layout.set_pinned_size(media_zone, thumb_w, thumb_h)
                card.set_size_request(card_w, card_h)
            gallery._relayout()
        artgen_gallery = getattr(self, "_artgen_gallery", None)
        if artgen_gallery is not None:
            artgen_gallery.set_tile_size(card_w, card_h)

    # _on_art_autogen_action / _on_art_autogen_delay_action removed SP-3d-5
    # alongside ArtgenPanel (the only thing that implemented toggle_auto_gen/
    # set_auto_gen_delay) — an ACCEPTED FLAGGED loss, see CLAUDE.md.

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

    # ── Medium/source resolution (SP-3d-3) ──────────────────────────────────────
    #
    # Every surviving caller that used to ask ControlPanel "what am I
    # currently making" (`get_model_source()`/`get_video_model()`/
    # `get_image_model()`) now asks CreateView instead, via
    # `CreateView._active_medium` — the same "what medium is active" state
    # Create's own scoped dropdown/model door already track. This is NOT a
    # literal drop-in: `Medium.id` is the legacy "video"/"image"/"animate"
    # vocabulary for native mediums, but is the artgen GENERATOR NAME
    # ("verse", "ansi", "animatediff", …) for artgen mediums — every one of
    # those folds to the single legacy string "artgen" here, per the audit's
    # explicit direction (`.superpowers/sdd/sp3d-audit.md` §1). Callers that
    # need the finer-grained "is the AnimateDiff generator specifically
    # active" distinction (TT-TV's local/no-server special case) use
    # `_active_medium_is_animatediff()` below instead of/alongside this.
    #
    # `CreateView._active_medium` persists even when Create isn't the visible
    # surface (Discover/Pipelines showing) — it's only ever reassigned by a
    # medium pick, never cleared on navigation — so this remains meaningful
    # from any surface. A missing `_create_view` (unit-test harnesses that
    # don't build one) or no medium picked yet both fall back to "video",
    # matching ControlPanel's own original default.

    def _current_medium_source(self) -> str:
        """Legacy "video"/"image"/"animate"/"artgen" vocabulary, derived from
        CreateView's active medium instead of `self._controls.get_model_source()`
        (SP-3d-3 — ControlPanel is being deleted; nothing surviving may read it)."""
        create_view = getattr(self, "_create_view", None)
        medium = getattr(create_view, "_active_medium", None) if create_view is not None else None
        if medium is None:
            return "video"  # matches ControlPanel's original default
        return "artgen" if medium.source == "artgen" else medium.id

    def _active_medium_is_animatediff(self) -> bool:
        """True when CreateView's active medium is specifically the
        AnimateDiff artgen generator (source="artgen", id="animatediff").

        AnimateDiff moved from a video-tab sub-model (the old
        `get_model_source()=="video" and get_video_model()=="animatediff"`
        combination) to its own Create medium chip — this is the equivalent
        check for callers (TT-TV launch/status, the attractor button) that
        need the distinction `_current_medium_source()` above deliberately
        collapses away (it folds every artgen medium, including this one, to
        the single string "artgen")."""
        create_view = getattr(self, "_create_view", None)
        medium = getattr(create_view, "_active_medium", None) if create_view is not None else None
        return medium is not None and medium.source == "artgen" and medium.id == "animatediff"

    def _current_medium_model_key(self, model_source: str) -> str:
        """Short video/image model key ("wan2"/"mochi"/"skyreels" or
        "flux"/"sdxl"/"z-image-turbo"/"motif") for the CURRENT medium,
        sourced from CreateView's scoped model dropdown instead of
        `self._controls.get_video_model()`/`get_image_model()` (SP-3d-3).

        `CreateView._selected_model_key()` returns a `server_manager` key
        (e.g. "wan2.2") — `_SERVER_KEY_TO_SOURCE_MODEL` (the same map
        `MainWindow.__init__`'s startup pre-select and
        `_resolve_attractor_model` already use — see that method's
        docstring) converts it into the (source, short_key) pair
        `_SERVER_SCRIPTS` is keyed by. Falls back to each medium's
        documented default key (mirrors `_on_generate`'s own fallback) when
        CreateView has no selection yet, the selected key isn't recognized,
        or it resolves to a different source than `model_source` (e.g. the
        active medium is "image" but a caller asks for "video" — shouldn't
        happen in practice, but must never silently hand back the wrong
        model's key). Returns "" for any other `model_source` (mirrors the
        pre-existing `_on_start_server`/`_on_stop_server` else-branch)."""
        create_view = getattr(self, "_create_view", None)
        try:
            server_key = create_view._selected_model_key() if create_view is not None else None
        except Exception:
            server_key = None
        src, mdl = _SERVER_KEY_TO_SOURCE_MODEL.get(server_key, (None, None))
        if model_source == "video":
            return mdl if src == "video" and mdl else _DEFAULT_VIDEO_KEY
        if model_source == "image":
            return mdl if src == "image" and mdl else _DEFAULT_IMAGE_KEY
        return ""

    def _running_generation_server(self, capability: str) -> "tuple[bool, Optional[str]]":
        """(is a server currently READY for `capability`, its server_manager
        key), from `ModelStatusService` (SP-3d-3) — replaces ControlPanel's
        own `_server_ready`/`_running_model` attributes (fed by the now-legacy
        `_health_loop`) for the two surviving readers: `_on_open_attractor`'s
        TT-TV status line, and `_on_generate`'s AnimateDiff-blackhole
        chip-busy guard. Prefers READY over STARTING (`ready_keys`, not
        `running_or_starting`) — a server still loading hasn't actually
        claimed the chip yet, so the guard/status line should only fire once
        a server has actually finished coming up.

        CAPABILITY-AWARE (SP-3d-3 review fix): video/image/animate/skyreels
        `ServerDef`s all share ONE port (8000) and are mutually exclusive —
        only one model is ever loaded at a time. An earlier version of this
        helper checked every capability unconditionally and returned "ready"
        as long as ANY of them was up, even when the loaded model belonged to
        an UNRELATED capability (e.g. FLUX/image loaded while the caller's
        context is "video") — a real regression versus the old
        `ControlPanel._server_ready`, which went False on exactly this
        mismatch (see `ControlPanel.set_server_state`'s
        `mismatch = source_for_model != current_source` check). Scoping the
        `ready_keys()` lookup to the SINGLE capability the caller cares about
        restores that mismatch-aware behavior for free: a mismatched loaded
        model simply isn't in `ready_keys(capability)`, so this correctly
        returns `(False, None)`.
        """
        ready = self._status_service.ready_keys(capability)
        if ready:
            return True, ready[0]
        return False, None

    def _display_label_for_server_key(self, server_key: "Optional[str]") -> "Optional[str]":
        """Human label (`server_manager.ServerDef.label`) for a server key, or
        `None` if `server_key` is falsy — mirrors the previous
        `_MODEL_DISPLAY_SERVER.get(self._controls._running_model or "", self._controls._running_model)`
        behavior (also `None`) for "nothing currently running"."""
        if not server_key:
            return None
        import server_manager
        sdef = server_manager.SERVERS.get(server_key)
        return sdef.label if sdef is not None else server_key

    def _attractor_capability(self) -> str:
        """The `ModelStatusService` capability TT-TV's server-status line
        (`_on_open_attractor`'s `get_server_status` closure, extracted to
        `_attractor_server_status`) should check RIGHT NOW (SP-3d-3 review
        fix — see `_running_generation_server`'s docstring for why this must
        be capability-scoped, not "any of video/image").

        Mirrors `_on_open_attractor`'s own AnimateDiff override: when the
        AnimateDiff generator medium is active, the effective source is
        "animatediff", which has no `_SOURCE_TO_CAP` entry — falling back to
        "video" exactly matches ControlPanel's old behavior (its own
        `_model_source` stayed "video" for the AnimateDiff sub-model; it
        never became "animatediff" itself).

        "artgen" is deliberately special-cased to "video" too, rather than
        reusing `_SOURCE_TO_CAP["artgen"]` ("artgen" — the CAPABILITY
        DASHBOARD's row for the chat-LLM backends on port 8002): this method
        answers a different question — "is the port-8000 generation server
        (video/image/animate) currently occupying the chip" — which an
        artgen medium has no real answer for. Naively reusing "artgen" here
        would check `ready_keys("artgen")` (the LLM backend, near-always up)
        instead, wrongly reporting "ready" for a question that doesn't apply.
        Mirrors the identical `_source != "artgen"` special-case the
        (now-retired, SP-3d-6) `_on_health_result` used for the same reason.
        Read fresh on
        every call (not captured once) so it stays correct if the active
        medium changes while TT-TV is open, matching the pre-fix closure's
        own live reads.
        """
        source = "animatediff" if self._active_medium_is_animatediff() else self._current_medium_source()
        if source == "artgen":
            return "video"
        return _SOURCE_TO_CAP.get(source, "video")

    def _attractor_server_status(self) -> "tuple[bool, Optional[str]]":
        """`get_server_status` callback handed to `attractor.AttractorWindow`
        (`_on_open_attractor`) — extracted to a real method (rather than an
        inline lambda) so it's independently testable without constructing
        a real `AttractorWindow`.

        SP-3d-3 review fix: capability-scoped via `_attractor_capability()`
        (not "any of video/image" — see `_running_generation_server`'s
        docstring) so a MISMATCHED loaded model (e.g. FLUX/image up while
        this medium is "video") correctly reports NOT ready, restoring
        ControlPanel's old mismatch-aware `_server_ready` behavior. AnimateDiff
        runs locally — no server needed — so it's always treated as ready
        regardless of what `_running_generation_server` reports.
        """
        ready, server_key = self._running_generation_server(self._attractor_capability())
        is_animatediff = self._active_medium_is_animatediff()
        return (
            ready or is_animatediff,
            "AnimateDiff (local)" if is_animatediff
            else self._display_label_for_server_key(server_key),
        )

    # ── Gallery helpers ────────────────────────────────────────────────────────

    def _active_gallery(self) -> "GalleryWidget":
        """Return the gallery that matches the currently selected generation source."""
        return self._gallery_for_type(self._current_medium_source())

    def _gallery_for_type(self, media_type: str) -> "GalleryWidget":
        """Return the gallery for the given media_type string.

        Unify-gallery-interaction-pattern Task 1: "artgen" is deliberately
        NOT one of the branches below. `ArtgenGallery` (`self._artgen_gallery`)
        does not implement `GalleryWidget`'s API (`all_cards()`/
        `delete_card()`/`replace_pending_with()`/...) — before this guard, an
        "artgen" media_type silently fell through to `_video_gallery`, a
        latent misroute that would only surface as an AttributeError deep
        inside whatever caller then tried to use the returned object as a
        `GalleryWidget`. Raising here makes the misroute loud at the call
        site instead. Callers that want the artgen gallery must reach for
        `self._artgen_gallery` directly (it isn't reachable through this
        helper at all).
        """
        if media_type == "image":
            return self._image_gallery
        if media_type == "animate":
            return self._animate_gallery
        if media_type == "artgen":
            raise ValueError(
                "ArtgenGallery does not implement GalleryWidget's API; "
                "use self._artgen_gallery directly"
            )
        return self._video_gallery

    def _uncheck_pipelines_toggle_if_active(self) -> None:
        """Uncheck the 🧩 Pipelines toggle without recursing into `_hide_pipelines`.

        Shared by every navigation seam that moves the gallery stack off of
        "pipelines" (Create, Discover, and — via `_sync_gallery_to_source` —
        leaving Pipelines to land on a medium page): whichever one fires
        first should visually uncheck the toggle so it never lags behind
        reality. Guarded by `_pipelines_toggle_syncing` because
        `set_active(False)` fires the button's own "toggled" signal, which
        would otherwise call `_hide_pipelines` again on top of whatever the
        caller is already doing.
        """
        pipelines_btn = getattr(self, "_pipelines_btn", None)
        if pipelines_btn is not None and pipelines_btn.get_active():
            self._pipelines_toggle_syncing = True
            try:
                pipelines_btn.set_active(False)
            finally:
                self._pipelines_toggle_syncing = False

    def _sync_gallery_to_source(self, source: str) -> None:
        """Switch `_gallery_stack` to *source* and rebuild the context-menu slot.

        Replaces the old ControlPanel-era `_on_source_change` (deleted
        SP-3d-5, along with the medium-tab toggle that used to call it).
        The two surviving callers are `_on_loop_nav_discover` (browsing the
        current medium's gallery) and `_hide_pipelines` (returning to
        whatever medium was active before Pipelines opened) — both reached
        via `_current_medium_source()`, never a literal tab click anymore.

        Also drops the old ControlPanel-era "artgen mode collapses the left/
        right panes for full width" special case: the "artgen" gallery page
        is now a plain `ArtgenGallery` (grid + filter chips), laid out like
        any other medium gallery, so it no longer needs extra width.
        """
        self._uncheck_pipelines_toggle_if_active()
        self._gallery_stack.set_visible_child_name(source)
        self._rebuild_context_menu(source)

    # ── Loop nav: Create · Curate · Discover · Remix ────────────────────────────
    #
    # SP-C Task 1 (docs/superpowers/specs/2026-07-13-create-surface-design.md):
    # the new top-level nav that reframes the app by *activity* rather than by
    # medium.

    def _build_loop_nav(self) -> Gtk.Box:
        """Build the loop nav row: three movements sharing one radio group.

        Returns the row widget; the caller appends it to `root_box` *first*
        so it sits above everything else.

        Does NOT set any button active — `MainWindow.__init__` does that once
        `_build_ui()` has finished constructing everything the handlers below
        touch (`_gallery_stack`, `_detail_wrap`, `_pipelines_btn`), so firing
        the "toggled" signal here can never touch an attribute that doesn't
        exist yet.
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.add_css_class("loop-nav-row")

        def _sep(glyph: str = "→", *, loop: bool = False) -> Gtk.Label:
            # → = "->", ↺ = the "go again" loop glyph. Kept as escapes
            # so this stays copy-safe; they are Python str labels, never CSS.
            lbl = Gtk.Label(label=glyph)
            lbl.add_css_class("loop-nav-loop" if loop else "loop-nav-arrow")
            return lbl

        create_btn = Gtk.ToggleButton(label="✨ Create")
        create_btn.add_css_class("loop-nav-btn")
        create_btn.set_tooltip_text(
            "Create — start something new: pick a medium or type an idea."
        )

        # Discover absorbs Curate: browsing and collecting are one act — you
        # star, build playlists, and thread things together AS you find them.
        discover_btn = Gtk.ToggleButton(label="\U0001f52d Discover")
        discover_btn.add_css_class("loop-nav-btn")
        discover_btn.set_tooltip_text(
            "Discover & curate — browse what you've made: star it, add it to "
            "playlists, thread it together (individual artifacts or whole projects)."
        )

        # Watch is an ACTION (opens the TT-TV kiosk window), not a surface radio,
        # but it sits in the loop between Discover and Remix so the cycle reads
        # left to right. Built HERE now (moved out of _build_ui) so it can be
        # interleaved in loop order. Same attribute/handler/initial-state as before.
        self._attractor_btn = Gtk.Button(label="\U0001f4fa Watch")
        self._attractor_btn.add_css_class("loop-nav-btn")
        self._attractor_btn.add_css_class("loop-nav-action")
        self._attractor_btn.set_tooltip_text(
            "Watch TT-TV — a living kiosk stream of your media that also keeps\n"
            "generating new content; remix anything you see."
        )
        self._attractor_btn.set_sensitive(False)
        self._attractor_btn.connect("clicked", self._on_open_attractor)

        remix_btn = Gtk.ToggleButton(label="\U0001f500 Remix")
        remix_btn.add_css_class("loop-nav-btn")
        remix_btn.set_tooltip_text(
            "Remix — the Muse: turn anything into a new pipeline, and go again."
        )

        discover_btn.set_group(create_btn)
        remix_btn.set_group(create_btn)

        create_btn.connect("toggled", lambda b: b.get_active() and self._on_loop_nav_create())
        discover_btn.connect("toggled", lambda b: b.get_active() and self._on_loop_nav_discover())
        remix_btn.connect("toggled", lambda b: b.get_active() and self._on_loop_nav_remix())

        row.append(create_btn)
        row.append(_sep())
        row.append(discover_btn)
        row.append(_sep())
        row.append(self._attractor_btn)
        row.append(_sep())
        row.append(remix_btn)
        row.append(_sep("↺", loop=True))

        # Keyed lookup for tests and for __init__'s default-active line.
        self._loop_nav = {
            "create": create_btn,
            "discover": discover_btn,
            "remix": remix_btn,
        }
        self._loop_nav_create_btn = create_btn
        return row

    def _build_discover_type_row(self) -> Gtk.Box:
        """Build the Discover media-type switcher: one toggle per gallery
        (Video / Image / Animate / Artgen), sharing a single radio group.

        SP-3d-5 deleted the legacy medium-tab source toggle -- the ONLY UI
        that used to let you choose which gallery `_gallery_stack` showed.
        Without it, Discover was pinned to whatever `_current_medium_source()`
        (CreateView's active medium) happened to be, so the animate and
        artgen galleries became unreachable: there was simply no control left
        that could ask for them. This row is Discover's OWN switcher -- it
        does not resurrect ControlPanel or its deleted toggle. Each button's
        "toggled" handler calls `_sync_gallery_to_source` (unchanged), so the
        existing star/playlist/detail/context-menu wiring for whichever
        gallery ends up visible needs no changes.

        Mirrors `_build_loop_nav`'s left/mid/mid/right button-group pattern,
        reusing the `.source-btn` CSS classes (left over from the deleted
        medium-tab toggle, otherwise unused since SP-3d-5) rather than
        inventing new ones.
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.add_css_class("discover-type-row")

        # (source key used by _sync_gallery_to_source, button label, edge CSS class)
        specs = [
            ("video", "Video", "source-btn-left"),
            ("image", "Image", "source-btn-mid"),
            ("animate", "Animate", "source-btn-mid"),
            ("artgen", "Artgen", "source-btn-right"),
        ]

        buttons: "dict[str, Gtk.ToggleButton]" = {}
        group_btn = None
        for source, label, edge_cls in specs:
            btn = Gtk.ToggleButton(label=label)
            btn.add_css_class("source-btn")
            btn.add_css_class(edge_cls)
            if group_btn is None:
                group_btn = btn
            else:
                btn.set_group(group_btn)
            # `src=source` binds each closure to its OWN loop iteration's
            # value -- without the default arg every button's callback would
            # close over the same `source` variable and all fire "artgen"
            # (the loop's final value).
            btn.connect(
                "toggled",
                lambda b, src=source: b.get_active() and self._sync_gallery_to_source(src),
            )
            row.append(btn)
            buttons[source] = btn

        self._discover_type_buttons = buttons
        return row

    def _on_loop_nav_create(self) -> None:
        """Create: the unified Create surface (CreateView).

        Keeps the pipelines-toggle uncheck dance `_sync_gallery_to_source`
        also provides for Discover: Pipeline Studio shares this same
        `_gallery_stack`, so leaving `_pipelines_btn` checked while "create"
        shows underneath would leave a stale-checked toggle.
        """
        self._uncheck_pipelines_toggle_if_active()
        self._gallery_stack.set_visible_child_name("create")

        # Restore the right detail pane to the startup Create state (visible
        # — `_build_ui` leaves it at its GTK4 default, and startup's own
        # `_on_loop_nav_create` never touches it). Discover doesn't collapse
        # it either (only Pipelines does), but a Pipelines→Create hop needs
        # this restored.
        self._detail_wrap.set_visible(True)

        # SP-3d-6: the Discover-only media-type switcher must not linger
        # underneath Create.
        self._hide_discover_type_row()

    def _on_loop_nav_discover(self) -> None:
        """Discover (absorbs Curate): browse AND collect what you've made — the
        gallery, full-width, with the star/playlist/detail actions you use to
        thread things together as you find them. Reuses `_sync_gallery_to_source`
        for the gallery switch + pipelines-toggle sync. Whole-project /
        pipeline discovery (Pipeline Studio's Discover) stays reachable via the
        🧩 Pipelines toggle; a later slice unifies artifact- and project-browse
        under this one Discover.

        SP-3d-6 regression fix: also shows `_discover_type_row` (the
        Video/Image/Animate/Artgen switcher SP-3d-5 left with no replacement)
        and syncs its active button to `_current_medium_source()` so Discover
        opens on a sensible gallery rather than always defaulting blind. The
        row's own button clicks drive the gallery from here on — this method
        only sets the STARTING point each time Discover is (re-)entered.
        """
        source = self._current_medium_source()
        self._sync_gallery_to_source(source)

        row = getattr(self, "_discover_type_row", None)
        if row is not None:
            row.set_visible(True)
        btn = getattr(self, "_discover_type_buttons", {}).get(source)
        if btn is not None and not btn.get_active():
            # Triggers "toggled" -> _sync_gallery_to_source(source) again;
            # harmless repeat of the call two lines above, but also correctly
            # unchecks whichever OTHER type button was left active from a
            # previous Discover visit (same `set_group` radio behavior the
            # loop nav buttons rely on).
            btn.set_active(True)

    def _hide_discover_type_row(self) -> None:
        """Hide the Discover media-type switcher (see `_build_discover_type_row`).
        Shared by `_on_loop_nav_create`/`_on_loop_nav_remix` — the switcher is
        Discover-only and must not overlap either surface. No-op if the row
        hasn't been built (e.g. minimal test harnesses)."""
        row = getattr(self, "_discover_type_row", None)
        if row is not None:
            row.set_visible(False)

    def _on_loop_nav_remix(self) -> None:
        """Remix: Pipeline Studio's Muse, unseeded — reuses the exact
        activation bridge `_remix_as_pipeline` uses (enter Pipelines the same
        way the toolbar toggle does, then hand off to `show_muse()`), just
        without a seed artifact.
        """
        pipelines_btn = getattr(self, "_pipelines_btn", None)
        if pipelines_btn is not None and not pipelines_btn.get_active():
            pipelines_btn.set_active(True)  # triggers _on_pipelines_toggled -> _show_pipelines
        else:
            self._show_pipelines()

        self._pipeline_studio.show_muse(seed_artifact=None)

        # SP-3d-6: the Discover-only media-type switcher must not linger
        # underneath Remix.
        self._hide_discover_type_row()

    # ── Pipeline Studio (Discover + Open) ───────────────────────────────────────

    def _on_pipelines_toggled(self, btn: Gtk.ToggleButton) -> None:
        """Toolbar toggle handler: show Pipeline Studio while active, restore on untoggle."""
        if getattr(self, "_pipelines_toggle_syncing", False):
            # _uncheck_pipelines_toggle_if_active is cosmetically unchecking
            # this button because Create/Discover/hide-pipelines already
            # switched the gallery/panel state — don't re-run _hide_pipelines
            # on top of it.
            return
        if btn.get_active():
            self._show_pipelines()
        else:
            self._hide_pipelines()

    def _show_pipelines(self) -> None:
        """Mount Pipeline Studio full-width, constructing it lazily on first use.

        `PipelineStudio` scans run history off-thread as soon as it is built
        (see pipeline_studio.py), so it is deliberately NOT constructed at
        startup — only here, the first time the "Pipelines" toggle is
        activated. Subsequent activations reuse the same instance and its
        already-loaded Discover page.
        """
        if self._pipeline_studio is None:
            from pipeline_studio import PipelineStudio
            # ✨ Inspire (regression fix 2/2): reuse the exact same seam
            # CreateView's idea-door/ArtgenParamPanel wiring drives
            # (`_create_inspire_fn`, backed by `prompt_client.generate_prompt`)
            # so a pipeline step's text field gets the identical two-mode
            # fresh/remix behavior, not a forked pipeline-only prompt-gen path.
            self._pipeline_studio = PipelineStudio(inspire_fn=self._create_inspire_fn)
            self._gallery_stack.add_named(self._pipeline_studio, "pipelines")

        # Always land on Discover, never a stale Open page from a previous
        # visit — Discover is Pipeline Studio's front door every time.
        self._pipeline_studio.show_discover()
        self._gallery_stack.set_visible_child_name("pipelines")
        # Full-width: Pipeline Studio has its own layout and needs neither
        # the (now-deleted) prompt-composition panel nor the detail pane.
        self._detail_wrap.set_visible(False)

    def _hide_pipelines(self) -> None:
        """Restore whatever CreateView's active medium was (SP-3d-3: no
        longer ControlPanel's `_model_source` — see `_current_medium_source`),
        and re-show the detail pane `_show_pipelines` collapsed.

        Reuses `_sync_gallery_to_source` (the gallery/context-menu-slot half
        of the old `_on_source_change`) rather than duplicating its logic, so
        leaving Pipelines always lands back in a state it already knows how
        to produce for the current medium.
        """
        self._sync_gallery_to_source(self._current_medium_source())
        self._detail_wrap.set_visible(True)

    # Maps GenerationRecord.media_type -> the "kind" vocabulary Pipeline Studio's
    # Muse expects for a seed_artifact. Anything absent from this table (e.g.
    # "artgen") has no resolvable kind, so _remix_as_pipeline falls back to a
    # blank muse rather than guessing.
    _REMIX_KIND_BY_MEDIA_TYPE = {
        "image": "image",
        "video": "video",
        "animate": "video",
        "animatediff": "gif",
    }

    def _remix_as_pipeline(self, record) -> None:
        """Open Pipeline Studio's Muse scoped to this record's primary artifact.

        Wired as `remix_as_pipeline_cb` to every GenerationCard/DetailPanel's
        "🧩 Remix as pipeline…" button ("Make this image into…") AND as
        `ArtgenPanel.on_remix_as_pipeline` for the Generative Art gallery's own
        "🧩 Remix as pipeline…" affordance. Accepts either record type and
        dispatches on it:

        - `history_store.GenerationRecord` (video/image galleries): resolves
          (path, kind, thumb_path) via `_REMIX_KIND_BY_MEDIA_TYPE`, as before.
        - `media_store.MediaRecord` (artgen gallery): resolves via
          `_resolve_artgen_media_seed`, which classifies the artifact's file
          extension through `artgen_kind.artgen_seed_kind` — a "text" artifact
          (e.g. a lore .txt) seeds its own file content as the muse's opaque
          seed value ("Make this text into…"), an "image"/"gif" artifact seeds
          its path like the GenerationRecord branch, and anything unresolved
          (json/unknown, missing/unreadable/empty) falls back to a blank muse.

        Either way this activates the Pipelines area exactly the way the
        "🧩 Pipelines" toolbar toggle does, then hands the seed artifact to
        PipelineStudio.show_muse().

        Never fails: a missing/unreadable file, an unresolved kind, or an
        unrecognized media_type all fall back to a blank muse
        (`show_muse(seed_artifact=None)`) instead of raising or seeding
        garbage.
        """
        if isinstance(record, MediaRecord):
            seed_artifact = self._resolve_artgen_media_seed(record)
        else:
            kind = self._REMIX_KIND_BY_MEDIA_TYPE.get(record.media_type)
            seed_artifact = None
            if record.media_exists and kind is not None:
                seed_artifact = (record.media_file_path, kind, record.thumbnail_path)

        # Activate the Pipelines area the same way the toolbar toggle does.
        pipelines_btn = getattr(self, "_pipelines_btn", None)
        if pipelines_btn is not None and not pipelines_btn.get_active():
            pipelines_btn.set_active(True)  # triggers _on_pipelines_toggled -> _show_pipelines
        else:
            self._show_pipelines()

        self._pipeline_studio.show_muse(seed_artifact=seed_artifact)

    @staticmethod
    def _resolve_artgen_media_seed(record: "MediaRecord"):
        """Resolve an artgen `MediaRecord` into a Muse seed_artifact tuple.

        `artgen_kind.artgen_seed_kind` classifies the artifact by file
        extension:

        - `"text"` (.txt/.md/.py — e.g. a lore artifact): read the file as
          utf-8, best-effort. Non-empty (post-strip) content seeds
          `(content, "text", None)` — the thumbnail is deliberately `None`
          because the Muse shows the "Make this text into…" heading with no
          image thumb for a text seed, never the file's own thumbnail_path.
        - `"image"`/`"gif"` (.png/.jpg/.svg/.ans/.webp/.gif): seeds
          `(file_path, kind, thumbnail_path)` if the file exists on disk.
        - `None` (json/unknown extension, or any failure above): `None` —
          the caller opens a blank muse.

        Never raises — any exception here is treated the same as "not
        seedable" (blank muse), matching the GenerationRecord branch's
        never-fails contract.
        """
        try:
            kind = artgen_kind.artgen_seed_kind(record.file_path, record.generator_type)
            if kind is None:
                return None
            if kind == "text":
                try:
                    content = Path(record.file_path).read_text(encoding="utf-8")
                except Exception:
                    return None
                if not content.strip():
                    return None
                return (content, "text", None)
            # "image" / "gif"
            if record.file_path and Path(record.file_path).exists():
                return (record.file_path, kind, record.thumbnail_path)
            return None
        except Exception:
            return None

    # ── Card selection ─────────────────────────────────────────────────────────

    def _on_card_selected(self, record: GenerationRecord) -> None:
        """Called when the user clicks a gallery card. Populates the detail panel."""
        # Unify-gallery-interaction-pattern Task 3: a native card click always
        # means the shared right pane should show `DetailPanel`, even if it
        # was last left on the "artgen" page (e.g. the user clicked an
        # artgen card, then clicked a video/image/animate card without
        # closing the pane in between). Pause any GIF timer left running on
        # the now-hidden ArtgenDetail first -- a Gtk.Stack keeps its hidden
        # child realized, so an un-paused timer would otherwise keep firing
        # forever (see ArtgenDetail.pause_animation's docstring).
        self._artgen_detail.pause_animation()
        self._right_stack.set_visible_child_name("native")
        gallery = self._gallery_for_type(record.media_type)
        # Use all_cards() so prev/next navigation crosses page boundaries.
        all_cards = gallery.all_cards()
        idx = next((i for i, c in enumerate(all_cards) if c._record.id == record.id), 0)
        self._detail.set_context([c._record for c in all_cards], idx)
        self._detail.show_record(record, self._dispatch_remix,
                                  remix_as_pipeline_cb=self._remix_as_pipeline)

    # ── Artgen card selection (shared right pane) ───────────────────────────────
    # Unify-gallery-interaction-pattern Task 3: routes ArtgenGallery's card
    # activation/deletion signals into self._right_stack + self._artgen_detail,
    # the same shared-pane pattern `_on_card_selected` above uses for the
    # three native galleries. Replaces ArtgenGallery's own in-page detail
    # overlay (a crash workaround; see CLAUDE.md and artgen_gallery.py).

    def _on_artgen_card_selected(self, media_id: str) -> None:
        """Called when the user clicks an artgen gallery card."""
        # Never force the pane open if the user collapsed it -- matches
        # _on_card_selected above, which also never calls
        # _set_detail_pane_visible(True).
        self._right_stack.set_visible_child_name("artgen")
        self._artgen_detail.show_record(media_id, self._artgen_gallery._filtered_records())

    def _on_artgen_card_deleted(self, media_id: str) -> None:
        """Called after ArtgenGallery's own hover-🗑 already removed the card
        from its grid+in-memory records. Only clear the shared pane if it is
        currently showing the record that was just deleted."""
        if self._right_stack.get_visible_child_name() != "artgen":
            return
        records = self._artgen_detail._records
        idx = self._artgen_detail._idx
        current = records[idx] if records and 0 <= idx < len(records) else None
        if current is not None and current.id == media_id:
            self._set_detail_pane_visible(False)

    def _on_artgen_detail_deleted(self, media_id: str) -> None:
        """Called after the shared ArtgenDetail's OWN 🗑 deletes a record --
        unlike the grid's hover-delete, ArtgenGallery doesn't know about this
        deletion yet, so sync its grid+chips here. ArtgenDetail already
        stepped to the next record (or called on_back if the list emptied)
        inside its own `_delete_confirmed` -- nothing further to manage on
        the detail side."""
        self._artgen_gallery.remove_record(media_id)

    def _on_artgen_detail_starred(self, media_id: str, starred: bool) -> None:
        """Mirror _on_star's native counterpart: persist the starred state
        and keep ArtgenGallery's in-memory records/chips (the ⭐ Starred
        filter chip) in sync with a star toggle made from inside the shared
        detail pane."""
        from media_store import media_store as _ms
        _ms.star(media_id, starred)
        for r in self._artgen_gallery._records:
            if r.id == media_id:
                r.starred = int(starred)
                break
        self._artgen_gallery._rebuild_chips()

    # ── Remix routing ──────────────────────────────────────────────────────────

    def _on_remix_card(self, record) -> None:
        """Open RemixPopover anchored to the gallery card's 🔀 Remix button.

        Called by GalleryWidget cards via the remix_cb hook.  The popover is
        anchored to the MainWindow (self) as a fallback parent since the exact
        button widget is not passed through the callback chain — the popover
        will still appear in a reasonable position near the window centre.
        Resolves remix ingredients in a background thread (see RemixPopover),
        then calls _dispatch_remix on the GTK main thread.
        """
        from remix_popover import RemixPopover
        pop = RemixPopover(record, on_remix=self._dispatch_remix)
        pop.set_parent(self)
        pop.popup()

    def _dispatch_remix(self, ctx) -> None:
        """Route a fully-resolved RemixContext into Pipeline Studio's Muse.

        Called on the GTK main thread by RemixPopover after ingredient
        resolution completes (via GLib.idle_add inside the popover's
        background thread).

        DISCOVERED GAP (SP-3d-5): the original `remix_dispatch.dispatch_remix`
        this delegated to needed `controls.switch_to_source`/`populate_prompts`
        and `artgen_panel.set_generator`/`set_theme` — both classes are now
        deleted. This quick "reimagine as X" popover predates the
        Create/Discover/Remix shell (docs/superpowers/specs/
        2026-05-26-remix-ui-design.md) and was never migrated when Create took
        over generation; the SP-3d audit did not catch this live dependency.
        Rather than leave a dangling call into deleted classes, this now opens
        Pipeline Studio's Muse seeded with whatever artifact the popover
        resolved — the same bridge "🧩 Remix as pipeline…" (`_remix_as_pipeline`)
        already uses. `remix_dispatch.dispatch_remix` itself is left in place,
        untouched and still unit-tested (tests/test_remix_dispatch.py exercises
        it directly against mocks) — it is simply no longer called from here.

        ACCEPTED, FLAGGED UX regression: the popover's own target-type switch
        and single-step "regenerate inline, stay on this tab" behavior is
        gone — the user now lands in Pipeline Studio's Muse instead, same as
        "🧩 Remix as pipeline…". See .superpowers/sdd/task-5-report.md and
        CLAUDE.md.
        """
        seed_path = ctx.seed_image_path or ctx.ref_video_path
        seed_artifact = None
        if seed_path:
            kind = "image" if ctx.seed_image_path else "video"
            seed_artifact = (seed_path, kind, "")

        pipelines_btn = getattr(self, "_pipelines_btn", None)
        if pipelines_btn is not None and not pipelines_btn.get_active():
            pipelines_btn.set_active(True)  # triggers _on_pipelines_toggled -> _show_pipelines
        else:
            self._show_pipelines()
        self._pipeline_studio.show_muse(seed_artifact=seed_artifact)
        self._flash_status(f"Remix ready — {ctx.target_label} ✓")

    # ── Forge transform pipeline ───────────────────────────────────────────────

    def _on_transform_card(self, record: GenerationRecord, key: str) -> None:
        """Dispatch a forge plugin transform in a background thread.

        Called by GenerationCard right-click menu on the GTK main thread.
        The transform runs in a daemon thread to avoid blocking the UI.
        Result is posted back via GLib.idle_add.
        """
        self._flash_status(f"Applying transform: {key}…")

        def _worker():
            try:
                result = self._run_transform(record, key)
                GLib.idle_add(self._on_transform_finished, result)
            except Exception as e:
                GLib.idle_add(self._on_error, f"Transform '{key}' failed: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def _run_transform(self, record: GenerationRecord, key: str):
        """Run a forge plugin transform and return the resulting record.

        rmbg/blip/depth produce a native image `GenerationRecord` for the
        Image gallery (the original behavior). "ansi-image" is a SPECIAL
        CASE (Effort B Task 2): it produces a NEW ARTGEN
        `media_store.MediaRecord` (a `.ans` ANSI-art file) instead, mirroring
        `_create_generate_artgen`'s artgen record-construction pattern rather
        than the `_META`-driven `(fn_name, ext, label)` dispatch used below
        for the image-output transforms — `image_to_ansi(src)` takes no
        `dest` and returns text, not a file it writes itself.
        `_on_transform_finished` branches on the returned record's
        `media_type` to route it to the right gallery.
        """
        import importlib.util as _ilu
        import uuid
        from datetime import datetime, timezone
        from history_store import IMAGES_DIR, THUMBNAILS_DIR
        from log_viewer import _TRANSFORMS_LOG_DIR

        plugins_dir = Path(__file__).parent.parent / "plugins"
        spec = _ilu.spec_from_file_location(f"ttlg_transform_{key}",
                                             plugins_dir / key / "plugin.py")
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)

        src = record.media_file_path
        job_id = str(uuid.uuid4())
        ts = datetime.now(timezone.utc)
        ts_str = ts.strftime("%Y%m%d_%H%M%S")
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
        _TRANSFORMS_LOG_DIR.mkdir(parents=True, exist_ok=True)

        # Log file: YYYYMMDD_HHMMSS_<plugin>_<source_stem>.log
        src_stem = Path(src).stem[:32]
        log_path = _TRANSFORMS_LOG_DIR / f"{ts_str}_{key}_{src_stem}.log"
        t_start = datetime.now(timezone.utc)

        def _writelog(msg: str) -> None:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}\n")

        _writelog(f"transform: {key}")
        _writelog(f"source:    {src}")
        _writelog(f"plugin:    {plugins_dir / key / 'plugin.py'}")
        _writelog(f"record_id: {record.id}")

        if key == "ansi-image":
            # Produces a media_store MediaRecord (artgen), not a
            # history_store GenerationRecord — see the docstring above and
            # `_create_generate_artgen` for the pattern being mirrored.
            from artgen_thumb import make_thumbnail, make_artgen_path
            from media_store import MediaRecord
            from media_store import media_store as _ms

            try:
                ansi_text = mod.image_to_ansi(src)
                out_path = make_artgen_path(job_id[:8], ".ans")
                # make_artgen_path() normally creates its own parent dir
                # (base_dir.mkdir(...)), but tests stub the function out
                # with a bare Path, so create it here too — cheap and
                # idempotent either way.
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(ansi_text, encoding="utf-8")
                _writelog(f"output:    {out_path}")
            except Exception as e:
                _writelog(f"ERROR:     {e}")
                raise

            elapsed = (datetime.now(timezone.utc) - t_start).total_seconds()
            _writelog(f"elapsed:   {elapsed:.1f}s")
            _writelog("status:    ok")

            thumb_path = out_path.parent / "thumbnails" / (out_path.stem + ".png")
            try:
                thumb_path = make_thumbnail(out_path, thumb_path)
            except Exception:
                thumb_path = Path("")

            source_label = (record.prompt or Path(src).stem)[:100]
            rec = MediaRecord(
                id=job_id,
                media_type="artgen",
                created_at=ts.isoformat(),
                file_path=str(out_path),
                thumbnail_path=str(thumb_path) if Path(thumb_path).exists() else "",
                prompt=f"ANSI art from {source_label}",
                model_id="artgen",
                generator_type="ansi-image",
                params=json.dumps({"_source_id": record.id, "_transform": "ansi-image"}),
                starred=0,
            )
            # Duck-typed alias — see `_create_generate_artgen`'s identical
            # comment: `CreateResultPanel`/gallery code reads
            # `media_file_path`, which plain `MediaRecord` doesn't declare.
            rec.media_file_path = str(out_path)
            _ms.add(rec)
            _ms.ensure_auto_playlists()
            return rec

        # (fn_name, output_ext_or_None, display_label)
        _META = {
            "rmbg":  ("remove_background", ".png", "Background removed"),
            "blip":  ("caption_image",     None,   "Described"),
            "depth": ("estimate_depth",    ".png", "Depth map"),
        }
        fn_name, ext, label = _META[key]
        fn = getattr(mod, fn_name)

        try:
            if ext:
                dest = str(IMAGES_DIR / f"{ts_str}_{job_id[:8]}{ext}")
                fn(src, dest)
                image_path = dest
                prompt = f"{label}: {record.prompt[:100]}"
                _writelog(f"output:    {dest}")
            else:
                # blip: returns text, use original image for the card display
                caption = fn(src)
                image_path = src
                prompt = caption
                (IMAGES_DIR / f"{ts_str}_{job_id[:8]}.txt").write_text(caption)
                _writelog(f"caption:   {caption[:200]}")
        except Exception as e:
            _writelog(f"ERROR:     {e}")
            raise

        elapsed = (datetime.now(timezone.utc) - t_start).total_seconds()
        _writelog(f"elapsed:   {elapsed:.1f}s")
        _writelog("status:    ok")

        thumb = str(THUMBNAILS_DIR / f"{ts_str}_{job_id[:8]}.jpg")
        _make_thumbnail_for(image_path, thumb)

        return GenerationRecord(
            id=job_id,
            prompt=prompt,
            negative_prompt="",
            num_inference_steps=0,
            seed=-1,
            video_path="",
            thumbnail_path=thumb,
            created_at=ts.isoformat(),
            media_type="image",
            image_path=image_path,
            model="",
            extra_meta={
                "_source_id": record.id,
                "_transform": key,
                "_transform_label": label,
                "_log_path": str(log_path),
            },
        )

    def _on_transform_finished(self, record) -> bool:
        """Route a finished transform's record to the right gallery.

        rmbg/blip/depth return a native image `GenerationRecord` — append to
        `self._store` and refresh the Image gallery (unchanged behavior).

        "ansi-image" (Effort B Task 2) returns a `media_store.MediaRecord`
        (`media_type == "artgen"`) instead — it was already written to
        `media_store` by `_run_transform` itself (mirroring
        `_create_generate_artgen`), so here we only need to refresh the
        artgen gallery, same as `_on_create_artgen_done` does. This record
        must NEVER be appended to `self._store` (a `history_store`-only
        list) or handed to `self._image_gallery` — wrong type, wrong gallery.
        """
        if getattr(record, "media_type", None) == "artgen":
            artgen_gallery = getattr(self, "_artgen_gallery", None)
            if artgen_gallery is not None:
                try:
                    artgen_gallery.refresh()
                except Exception:
                    pass  # a refresh failure must never crash the transform flow
            self._flash_status("New ANSI art in the Generative Art gallery")
            return False

        self._store.append(record)
        self._image_gallery.load_history(
            [r for r in self._store.all_records() if r.media_type == "image"]
        )
        self._flash_status(f"Transform complete — new card in Image gallery")
        return False

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
        # Remove the record from any playlists that contained it.
        from playlist_store import playlist_store as _ps
        valid_ids = {r.id for r in self._store.all_records()}
        _ps.purge_deleted_records(valid_ids)
        short = record.prompt[:50] + ("…" if len(record.prompt) > 50 else "")
        self._set_status(f'Deleted: "{short}"')

    def _on_star(self, record: GenerationRecord, starred: bool) -> None:
        """Persist the starred state and refresh gallery filter if needed."""
        self._store.star(record.id, starred)
        record.starred = int(starred)
        # If the starred filter is active, relayout so the card appears/disappears.
        gallery = self._gallery_for_type(record.media_type)
        if gallery._active_filter == "starred":
            gallery._relayout()

    def _load_history(self) -> None:
        local_records = self._store.all_records()
        # Merge remote records, excluding any whose ID already exists locally.
        local_ids = {r.id for r in local_records}
        remote_records = [r for r in self._remote_records.values()
                          if r.id not in local_ids]
        # Sort newest-first by created_at so that remote records are interleaved
        # with local records chronologically rather than appended at the tail.
        records = sorted(
            local_records + remote_records,
            key=lambda r: getattr(r, "created_at", ""),
            reverse=True,
        )
        if not records:
            return
        # Route each record to the gallery that matches its media type.
        # GalleryWidget.load_history() replaces existing cards rather than
        # appending, so calling this method more than once is safe.
        # animatediff GIFs live in the video gallery (same as _gallery_for_type routing).
        video_recs   = [r for r in records if r.media_type in ("video", "animatediff")]
        animate_recs = [r for r in records if r.media_type == "animate"]
        image_recs   = [r for r in records if r.media_type == "image"]
        if video_recs:
            self._video_gallery.load_history(video_recs)
        if animate_recs:
            self._animate_gallery.load_history(animate_recs)
        if image_recs:
            self._image_gallery.load_history(image_recs)
        n_remote = len(remote_records)
        n_local  = len(local_records)
        if n_remote:
            self._set_status(
                f"Loaded {n_local} local + {n_remote} remote generation(s)"
            )
        else:
            self._set_status(f"Loaded {n_local} previous generation(s)")
        self._update_attractor_btn()

    # ── ModelStatusService-driven status bar (SP-3d-6) ──────────────────────────
    #
    # The three legacy pollers that used to live in this section
    # (`_start_health_worker`/`_health_loop`/`_on_health_result` for port 8000,
    # `_start_prompt_gen_health_worker`/`_prompt_gen_health_loop`/
    # `_on_prompt_gen_health` for port 8001, and
    # `_start_artgen_health_worker`/`_artgen_health_loop`/
    # `_on_artgen_health_result` for port 8002) are retired. Each ran its own
    # background thread pinging a different port on its own timer and fed
    # `_hw_statusbar` directly; `ModelStatusService` already reconciles all
    # three into one polled, subscribable status map (see model_status.py's
    # module docstring), so those threads were pure duplication of work the
    # service was already doing. `_on_status_snapshot`/`_render_status_snapshot`
    # below are the single subscribe() callback that replaces all three.

    def _on_status_snapshot(self, snap: "dict[str, Status]") -> bool:
        """`ModelStatusService.subscribe()` callback, wrapped in `GLib.idle_add`
        (see `__init__`) so it always runs on the main thread — fires on every
        poll tick whose resolved status map actually changed (change-only
        gating, per model_status.py), plus once at startup via the explicit
        call right after subscribing (subscribe() only pushes on the *next*
        change).
        """
        if not self._alive:
            return False
        self._render_status_snapshot(snap)
        return False  # one-shot idle callback (re-registered on the next change)

    def _render_status_snapshot(self, snap: "dict[str, Status]") -> None:
        """Re-render `_hw_statusbar` from a fresh `ModelStatusService` snapshot.

        Per-capability rows: group `server_manager.SERVERS` by capability via
        `_sm.servers_for_capability` (same grouping `ServersControl` and the
        retired `_on_health_result` both used) — READY if any server for that
        capability is READY (showing its label), else "starting…" if any is
        STARTING, else offline. "animatediff" is a hardware capability with no
        `SERVERS` entry (see `_sm.CAPABILITY_LABELS`'s comment) so it's skipped
        here — `_check_animatediff_hardware` owns that row instead.

        Aggregate dot: READY > STARTING > ERROR > OFF across EVERY key,
        mirroring `ServersControl._refresh_bar_dot`'s aggregation policy (the
        `TODO(SP-3d)` this replaces asked for exactly this). STARTING only
        calls `update_starting()` on the actual transition into that state —
        `update_starting()` resets the elapsed timer to 0:00 on every call, so
        calling it on every snapshot while already STARTING would freeze the
        counter instead of letting it tick.

        Unregistered running chat model (bugfix): `snap`'s per-key statuses
        alone under-report "artgen" when the running chat-LLM backend
        doesn't match any registered `server_manager.SERVERS` entry
        (`ModelStatusService.running_artgen_model().matched_key is None`) --
        every artgen/prompt key legitimately resolves OFF in that case (see
        `model_status.py`'s `_tick()` docstring), even though a chat endpoint
        genuinely answers requests and CreateView already surfaces it as a
        selectable "(detected)" entry. `running_artgen_model() is not None`
        is the single source of truth for "a chat model is running, matched
        or not" -- both the "artgen" capability row below AND the overall
        aggregate dot fold it in, so the two rows painted from the same
        snapshot never disagree with each other.
        """
        artgen_model = self._status_service.running_artgen_model()

        for cap, cap_label in _sm.CAPABILITY_LABELS.items():
            if cap == "animatediff":
                continue
            sdefs = _sm.servers_for_capability(cap)
            statuses = [snap.get(s.key, Status.OFF) for s in sdefs]
            ready_sdef = next((s for s in sdefs if snap.get(s.key) == Status.READY), None)
            if ready_sdef is not None:
                self._hw_statusbar.update_capability(cap, True, ready_sdef.label)
            elif cap == "artgen" and artgen_model is not None:
                self._hw_statusbar.update_capability(
                    cap, True, f"{artgen_model.model_id} (detected)"
                )
            elif Status.STARTING in statuses:
                self._hw_statusbar.update_capability(cap, False, "starting…")
            else:
                self._hw_statusbar.update_capability(cap, False, "")

        values = list(snap.values())
        if Status.READY in values:
            agg = Status.READY
        elif Status.STARTING in values:
            agg = Status.STARTING
        elif Status.ERROR in values:
            agg = Status.ERROR
        else:
            agg = Status.OFF

        if agg != Status.READY and artgen_model is not None:
            agg = Status.READY

        if agg == Status.READY:
            self._hw_statusbar.update_server(True, "ready")
            # A launch script may have handed off to a Docker log tail (see
            # _start_log_tail) — stop it now that a real health check has
            # confirmed readiness. Same trigger point the retired
            # _on_health_result used to own.
            if self._log_tail_stop:
                self._log_tail_stop.set()
                self._log_tail_stop = None
            if not (self._worker_gen and self._worker_gen._running()):
                self._set_status("Server ready — enter a prompt and click Generate")
        elif agg == Status.STARTING:
            if self._status_agg_prev != Status.STARTING:
                self._hw_statusbar.update_starting()
        elif agg == Status.ERROR:
            self._hw_statusbar.update_error()
        else:
            self._hw_statusbar.update_server(False, "offline")
        self._status_agg_prev = agg

        # Recover Jobs (File menu): enabled iff a media server (video/image/
        # animate — NOT artgen/prompt, which _on_recover has nothing to do
        # with) is actually READY. While a launch is in flight the service
        # itself reports STARTING (via note_starting()), so this naturally
        # stays disabled until a real health check succeeds — no separate
        # "is launching" flag needed (the retired _on_health_result read
        # ControlPanel's own `_server_launching` for this; that dependency is
        # gone).
        media_ready = any(
            snap.get(s.key) == Status.READY
            for cap in ("video", "image", "animate")
            for s in _sm.servers_for_capability(cap)
        )
        if a := self.lookup_action("recover-jobs"):
            a.set_enabled(media_ready)

    def _check_animatediff_hardware(self) -> None:
        """One-shot AnimateDiff (Blackhole) hardware capability check.

        "animatediff" has no `server_manager.SERVERS` entry — it's a local
        hardware capability, not a managed service (see
        `_sm.CAPABILITY_LABELS`'s comment) — so `ModelStatusService` never
        resolves a status for it and `_render_status_snapshot` deliberately
        skips it. This preserves the one-time hardware probe that used to run
        as a nested thread inside the now-retired `_start_artgen_health_worker`,
        as its own small background thread; the result is posted back via
        `GLib.idle_add` per the GTK threading rule.

        NOTE (bug fix while porting): `check_hardware()` returns a 3-tuple
        `(ok, message, num_chips)` — every other caller in this codebase
        (`artgen_panel.py`, `worker.py`, `artgen/cli.py`) unpacks all three.
        The original nested closure this replaces unpacked only `ok, msg =
        check_hardware()`, which always raised `ValueError` (caught by the
        blanket `except Exception` right below it) — so the "animatediff"
        capability row has always shown "unavailable" regardless of real
        hardware state. Fixed here to unpack the actual 3-tuple.
        """
        def _check():
            try:
                from artgen.generators.animatediff import check_hardware
                ok, msg, _num_chips = check_hardware()
            except Exception:
                ok, msg = False, "unavailable"
            GLib.idle_add(
                self._hw_statusbar.update_capability,
                "animatediff", ok, msg if ok else "hardware not detected",
            )
        threading.Thread(target=_check, daemon=True).start()

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

    # ── Remote record localization ──────────────────────────────────────────────

    def _on_remote_record_localized(self, record: GenerationRecord) -> None:
        """Called on the main thread after a remote video is downloaded to VIDEOS_DIR.

        Adds the record to the local HistoryStore and removes it from
        self._remote_records so the gallery shows it as a regular local card
        (with hover video preview and inline GTK player) going forward.

        Called via GLib.idle_add from DetailPanel._on_download_video or
        _on_download_remote_library so it always runs on the main thread.
        """
        self._store.append(record)
        self._remote_records.pop(record.id, None)
        # Refresh gallery so the card transitions from remote-style to local-style.
        self._load_history()

    # ── Remote inventory ────────────────────────────────────────────────────────

    def _start_inventory_fetch(self) -> None:
        """Start a one-shot background thread to fetch the remote inventory.

        Called at startup when --server points at a non-localhost host and the
        inventory URL (port 8002) is derived automatically by main.py.
        If the inventory server is not running the fetch silently fails.
        """
        threading.Thread(
            target=self._fetch_remote_inventory, daemon=True
        ).start()

    def _fetch_remote_inventory(self) -> None:
        """Fetch records from the remote inventory server (background thread).

        For each remote record not already in the local history store, a
        synthetic GenerationRecord is created with:
          - Local cache paths (under ~/.local/share/tt-video-gen/remote-cache/)
          - extra_meta["_is_remote"] = True
          - extra_meta["_inventory_video_url"] / _inventory_thumbnail_url

        Thumbnails are downloaded eagerly so gallery cards render immediately.
        Videos are lazy — downloaded only when the user clicks the Download button.
        """
        import requests as _req  # noqa: PLC0415
        url = self._inventory_url.rstrip("/") + "/inventory/records"
        try:
            resp = _req.get(url, timeout=10)
            resp.raise_for_status()
            raw_records: list = resp.json()
        except Exception as exc:
            import logging as _log  # noqa: PLC0415
            _log.getLogger(__name__).warning(
                "inventory fetch failed (%s): %s", self._inventory_url, exc
            )
            return

        from history_store import GenerationRecord  # noqa: PLC0415
        from urllib.parse import urlparse as _up     # noqa: PLC0415

        parsed_inv  = _up(self._inventory_url)
        host        = parsed_inv.hostname or "remote"
        inv_port    = parsed_inv.port or 8002
        inv_scheme  = parsed_inv.scheme or "http"
        # Cache directory for this remote host
        from history_store import STORAGE_DIR  # noqa: PLC0415
        cache_root = STORAGE_DIR / "remote-cache" / host

        _LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0"}

        def _rewrite(url: str) -> str:
            """Rewrite inventory-server-generated URLs that contain localhost.

            The inventory server is bound to 0.0.0.0 and may not know its
            external hostname, so it generates media URLs like
            http://localhost:8002/inventory/media/... — which are unreachable
            from the remote GUI.  Substitute the hostname/port we actually used
            to reach the inventory server.
            """
            if not url:
                return url
            p = _up(url)
            if p.hostname in _LOCAL_HOSTS:
                return f"{inv_scheme}://{host}:{inv_port}{p.path}"
            return url

        fetched: dict = {}
        for raw in raw_records:
            rec_id = raw.get("id", "")
            if not rec_id:
                continue

            media_type = raw.get("media_type", "video")
            # Rewrite any localhost URLs to use the actual remote host/port.
            video_url  = _rewrite(raw.get("video_url", ""))
            thumb_url  = _rewrite(raw.get("thumbnail_url", ""))
            image_url  = _rewrite(raw.get("image_url", ""))

            # Build local cache paths for this remote record.
            def _cache_name(url: str) -> str:
                return Path(_up(url).path).name if url else ""

            v_name = _cache_name(video_url)
            t_name = _cache_name(thumb_url)
            i_name = _cache_name(image_url)

            video_dest = str(cache_root / "videos"     / v_name) if v_name else ""
            thumb_dest = str(cache_root / "thumbnails" / t_name) if t_name else ""
            image_dest = str(cache_root / "images"     / i_name) if i_name else ""

            # Eagerly download thumbnail (small, needed for gallery card display).
            if thumb_url and thumb_dest and not Path(thumb_dest).exists():
                try:
                    Path(thumb_dest).parent.mkdir(parents=True, exist_ok=True)
                    tr = _req.get(thumb_url, stream=True, timeout=15)
                    if tr.status_code == 200:
                        with open(thumb_dest, "wb") as fh:
                            for chunk in tr.iter_content(65_536):
                                fh.write(chunk)
                except Exception:
                    thumb_dest = ""  # cache failure — card will show placeholder

            # Check whether the video was already cached from a previous session.
            if video_dest and Path(video_dest).exists():
                v_path = video_dest  # already cached — no download needed on select
            else:
                # Not cached — use the remote URL; DetailPanel shows Download button.
                v_path = video_dest  # path doesn't exist yet → video_exists = False

            rec = GenerationRecord(
                id=rec_id,
                prompt=raw.get("prompt", ""),
                negative_prompt=raw.get("negative_prompt", ""),
                num_inference_steps=int(raw.get("num_inference_steps", 0)),
                seed=int(raw.get("seed", -1)),
                video_path=v_path,
                thumbnail_path=thumb_dest,
                image_path=image_dest,
                created_at=raw.get("created_at", ""),
                duration_s=float(raw.get("duration_s", 0.0)),
                seed_image_path="",
                media_type=media_type,
                guidance_scale=float(raw.get("guidance_scale", 0.0)),
                model=raw.get("model", ""),
                extra_meta={
                    **(raw.get("extra_meta") or {}),
                    "_is_remote": True,
                    "_inventory_host": host,
                    "_inventory_video_url": video_url,
                    "_inventory_thumbnail_url": thumb_url,
                    "_inventory_image_url": image_url,
                },
            )
            fetched[rec_id] = rec

        if not fetched:
            return

        def _apply():
            if not self._alive:
                return False
            self._remote_records.update(fetched)
            self._load_history()
            return False

        GLib.idle_add(_apply)

    # ── Prompt gen launcher ─────────────────────────────────────────────────────
    #
    # SP-3d-5: `_on_start_prompt_gen`/`_on_inspire`/`_on_inspire_result`/
    # `_on_inspire_error` (ControlPanel's own Inspire-button callbacks, wired
    # only via the now-deleted `ControlPanel(...)` constructor call) and
    # `_on_theme`/`_on_theme_result`/`_on_theme_error`/`_on_theme_queue_shots`
    # (ControlPanel's own Theme Set path, same story) are removed — they had
    # no other caller once ControlPanel was gone. Their functionality already
    # lives on in the Create surface's own seams below: `_create_inspire_fn`
    # (Inspire me, SP-3c-3) and `_on_create_theme_set`/`_on_create_theme_result`
    # (Theme Set, SP-3d-1) — both were built to reuse the identical backends
    # (`prompt_client.generate_prompt` / `generate_theme.generate_theme`)
    # rather than fork them, so nothing about prompt generation or theme
    # batches is lost — only the ControlPanel-only launch UI is gone.

    def _create_inspire_fn(self, prompt_type, seed_text, on_result, on_error) -> None:
        # (prompt_type: str, seed_text: str, on_result: Callable[[str], None], on_error: Callable[[str], None]) -> None
        """CreateView's "Inspire me" seam (SP-3c-3) — drives the same
        prompt-gen backend (`prompt_client.generate_prompt`, the
        `generate_prompt.py` three-tier algo/markov/LLM-polish generator) the
        old ControlPanel Inspire button used, with generic on_result/on_error
        callbacks rather than reimplementing the generator itself.

        Runs `generate_prompt()` in a background thread (it can block on a
        network call to the prompt server) and posts the outcome back via
        `GLib.idle_add`, per the GTK threading rule in CLAUDE.md.

        **Two-mode (regression fix 1/2):** `seed_text` is threaded straight
        through to `generate_prompt(prompt_type, seed_text, system_prompt)`
        unchanged — empty seed_text -> fresh three-tier generation from
        scratch; non-empty seed_text -> the backend polishes/remixes those
        exact words (see `prompt_client.generate_prompt`'s own docstring).
        This mirrors the deleted ControlPanel/ArtgenPanel Inspire buttons'
        behavior (SP-3d-5 lost only the caller wiring, hardcoding `""` here,
        not the backend, which was two-mode the whole time). The caller
        (`CreateView._on_inspire_clicked`, or `create_param_panels.
        attach_inspire_button` for any other prompt entry) is responsible for
        reading the field's current text and deciding what to pass as
        seed_text — this seam just forwards it.
        """
        system_prompt = self._prompt_gen_system_prompt

        def run():
            try:
                text = prompt_client.generate_prompt(prompt_type, seed_text, system_prompt)
                GLib.idle_add(on_result, text)
            except Exception as e:  # noqa: BLE001 - fail-soft
                GLib.idle_add(on_error, str(e))

        threading.Thread(target=run, daemon=True).start()

    # ── Theme Set — migrated into Create (SP-3d-1) ──────────────────────────────
    #
    # ControlPanel's ORIGINAL theme path (`_on_theme`/`_on_theme_result`/
    # `_on_theme_error`/`_on_theme_queue_shots`) is deleted alongside the class
    # (SP-3d-5) — the methods below are CreateView's OWN launch of the exact
    # same backend (`generate_theme.generate_theme`, imported the identical
    # way the old path did — never forked/reimplemented), wired via
    # CreateView's `on_theme_set` seam. The one real difference from the old
    # path is WHERE the per-shot generation settings come from: ControlPanel's
    # path read its own steps/seed/guidance/model state; this path reads
    # CreateView's own collected params via `_native_generate_args` — the same
    # translation `_create_enqueue_native` already uses for the Create CTA's
    # busy-path enqueue. Both paths bottom out in the identical
    # `_on_enqueue` → `_start_next_queued` machinery.

    def _on_create_theme_set(self, medium, params: dict) -> None:
        """CreateView's `on_theme_set` seam — start a background thematic
        batch generation for *medium*, using *params* (CreateView's own
        `_collect_params()` output, the same dict the Create CTA would have
        sent to `_on_create_generate`) as the per-shot generation settings.

        Only native mediums (image/video/animate) are supported — the same
        restriction ControlPanel's Theme Set effectively had (its
        `get_generation_defaults()` only ever populated video/image/animate
        settings; artgen was never wired to Theme Set either). Fails soft
        with a status message for anything else.

        `params.get("prompt", "")` (Create's typed brief, plus any applied
        Direction-zone modifier text) is passed through `_theme_key_from_text`
        to pick a `generate_theme.THEME_LIBRARY` key when the brief happens
        to name one (e.g. typing "hitchcock") — otherwise `generate_theme`
        picks randomly, exactly like ControlPanel's button always did.
        """
        if medium.source != "native":
            self._set_status(
                f"Theme Set isn't available for {medium.label} yet — try an "
                "image, video, or animate medium instead."
            )
            return

        theme_key = _theme_key_from_text(params.get("prompt", ""))

        def run():
            try:
                import generate_theme
                result = generate_theme.generate_theme(theme_key=theme_key, enhance=True)
                GLib.idle_add(self._on_create_theme_result, medium, params, result)
            except Exception as e:  # noqa: BLE001 - fail-soft, mirrors _on_theme
                import traceback
                traceback.print_exc()
                GLib.idle_add(self._on_create_theme_error, str(e))

        threading.Thread(target=run, daemon=True).start()

    def _on_create_theme_result(self, medium, params: dict, result: dict) -> bool:
        """Runs on the main thread — enqueue every shot `generate_theme`
        produced, exactly the way `_on_theme_queue_shots` enqueues
        ControlPanel's shots (same `_on_enqueue`/`_start_next_queued`
        machinery), swapping in each shot's polished prompt over *params* and
        translating via `_native_generate_args` (see this method group's
        docstring for why that differs from `get_generation_defaults()`).

        A `_NativeGenerateGuardError` (e.g. SkyReels-I2V with no seed image)
        stops the batch at that shot and surfaces the message — the same
        guard `_create_enqueue_native` already enforces for a single Create
        CTA click applies per-shot here too; whatever queued before the
        failing shot is left queued, not rolled back.
        """
        shots = result.get("shots", [])
        queued = 0
        for shot in shots:
            prompt = shot.get("prompt", shot.get("slug", ""))
            if not prompt:
                continue
            shot_params = {**params, "prompt": prompt}
            try:
                args, kwargs = self._native_generate_args(medium, shot_params)
            except _NativeGenerateGuardError as exc:
                self._set_status(str(exc))
                break
            self._on_enqueue(*args, **kwargs)
            queued += 1

        theme_label = result.get("theme", "Theme Set")
        if queued:
            self._set_status(
                f"\U0001f3ac {theme_label}: queued {queued} shot(s) for {medium.label}."
            )
            # Start the queue if nothing is currently generating — same
            # not-busy check `_on_theme_queue_shots` already makes.
            if not (self._worker and self._worker.is_alive()):
                self._start_next_queued()
        else:
            self._set_status(f"\U0001f3ac {theme_label}: nothing to queue.")

        self._create_view.set_theme_queued(queued, theme_label)
        return False

    def _on_create_theme_error(self, msg: str) -> bool:
        """Runs on the main thread — log and surface the failure in Create's
        own result panel (`CreateView.set_theme_error`), mirroring
        `_on_theme_error`'s fail-soft contract for the legacy button."""
        print(f"[tt-gen] Create Theme Set error: {msg}", file=sys.stderr)
        self._create_view.set_theme_error(msg)
        return False

    # ── Playlist selection mode ────────────────────────────────────────────────

    def _on_enter_selection_mode(self, playlist_id: str) -> None:
        """
        Enter checkbox selection mode on the active gallery so the user can
        add or remove videos from a playlist.

        Scrolls back to the video tab, shows the selection banner with the
        playlist name, and pre-checks cards already in the playlist.
        """
        from playlist_store import playlist_store as _ps
        pl = _ps.get(playlist_id)
        if pl is None:
            return

        # Switch to the video gallery tab (video playlists only for now).
        # The gallery stack has named children; switch to the video page.
        self._gallery_stack.set_visible_child_name("video")

        pre_checked = set(pl.record_ids)
        self._video_gallery.enter_selection_mode(playlist_id, pre_checked)

        # Show the banner with an instructive label.
        self._selection_banner_lbl.set_text(
            f"☑  Adding to \"{pl.name}\" — check videos to include"
        )
        self._selection_banner_revealer.set_reveal_child(True)

    def _exit_selection_mode(self) -> None:
        """Hide the selection banner and deactivate checkboxes on all galleries."""
        self._selection_banner_revealer.set_reveal_child(False)
        for gallery in (self._video_gallery, self._animate_gallery, self._image_gallery):
            gallery.exit_selection_mode()

    def _on_selection_add(self, _btn) -> None:
        """
        Save the currently checked video IDs to the active playlist, then exit
        selection mode.  Only replaces the playlist membership — records that
        were previously in the playlist but are not checked get removed, and
        newly checked records are added.
        """
        from playlist_store import playlist_store as _ps

        # Find whichever gallery is currently in selection mode.
        gallery = None
        for g in (self._video_gallery, self._animate_gallery, self._image_gallery):
            if g._selection_mode:
                gallery = g
                break

        if gallery is None or gallery._active_playlist_id is None:
            self._exit_selection_mode()
            return

        playlist_id = gallery._active_playlist_id
        pl = _ps.get(playlist_id)
        if pl is None:
            self._exit_selection_mode()
            return

        checked_ids = gallery.get_checked_ids()

        # Replace playlist contents: add new, remove unchecked.
        # Keep existing ordering for IDs that are already there; append new ones.
        checked_set = set(checked_ids)
        # Remove records that were unchecked
        for rid in list(pl.record_ids):
            if rid not in checked_set:
                _ps.remove_record(playlist_id, rid)
        # Add newly checked records (deduplication is handled inside add_records)
        if checked_ids:
            _ps.add_records(playlist_id, checked_ids)

        refreshed = _ps.get(playlist_id)
        count = len(refreshed.record_ids) if refreshed is not None else 0
        self._set_status(
            f"Playlist \"{pl.name}\" updated — {count} video{'s' if count != 1 else ''}"
        )
        self._exit_selection_mode()

    def _on_open_attractor_for_playlist(self, playlist_id: "str | None") -> None:
        """Open TT-TV filtered to the given playlist (or all videos if None)."""
        if self._attractor_win is not None:
            self._attractor_win.destroy()
            self._attractor_win = None
        self._on_open_attractor(playlist_id=playlist_id)

    def _on_open_attractor_for_model(self, model_id: str) -> None:
        """Open TT-TV showing only videos generated by the given model."""
        if self._attractor_win is not None:
            self._attractor_win.destroy()
            self._attractor_win = None
        self._on_open_attractor(model_filter=model_id)

    # ── Attractor Mode ─────────────────────────────────────────────────────────

    def _get_animate_inputs(self) -> "tuple[str, str]":
        """
        Pick (ref_video_path, ref_char_path) for TT-TV animate auto-generation.

        ref_video: random bundled motion clip from motion_clips_dir.
        ref_char:  last frame of most recent animate record (extra_meta['last_frame_path'])
                   → fallback: thumbnail of most recent animate record
                   → fallback: image_path of most recent FLUX image record
                   → fallback: "" (attractor skips the cycle)

        Returns ("", "") if no valid inputs can be found (no bundled clips available).
        """
        import random as _random
        from animate_picker import BundledClipScanner

        # ── ref_video: random bundled clip ─────────────────────────────────────
        clips_dir = _settings.get("motion_clips_dir")
        all_clips = [
            clip["mp4"]
            for clips in BundledClipScanner(clips_dir).scan().values()
            for clip in clips
            if clip.get("mp4")
        ]
        if not all_clips:
            return "", ""
        ref_video = _random.choice(all_clips)

        # ── ref_char: last frame chain, then fallbacks ─────────────────────────
        all_records = self._store.all_records()

        # Priority 1: last frame of most recent animate record
        for r in all_records:
            if r.media_type != "animate":
                continue
            lfp = r.extra_meta.get("last_frame_path", "")
            if lfp and Path(lfp).exists():
                return ref_video, lfp
            # Priority 2: thumbnail of most recent animate record
            if r.thumbnail_path and Path(r.thumbnail_path).exists():
                return ref_video, r.thumbnail_path
            break  # only check the most recent animate record

        # Priority 3: most recent FLUX image
        for r in all_records:
            if r.media_type == "image" and r.image_path and Path(r.image_path).exists():
                return ref_video, r.image_path

        return ref_video, ""

    def _on_open_attractor(
        self, _btn=None,
        playlist_id: "str | None" = None,
        model_filter: "str | None" = None,
    ) -> None:
        """Open (or raise) the Attractor Mode kiosk window."""
        if self._attractor_win is not None:
            self._attractor_win.present()
            return

        # Stop any gallery videos that are currently playing so their GStreamer
        # pipelines are released before the attractor opens its own video slots.
        for gallery in (self._video_gallery, self._animate_gallery, self._image_gallery):
            gallery.stop_all_playback()

        # Filter records to the chosen playlist / model, or use all records.
        # Include artgen MediaRecord objects so artgen channels (Palettes, etc.) work.
        all_records = self._store.all_records() + self._store.artgen_records()
        if model_filter is not None:
            records = [r for r in all_records
                       if getattr(r, "model", "") == model_filter]
            auto_generate = False   # don't auto-gen into a model-filtered view
            # Encode as a model-virtual channel sentinel so the in-window
            # dropdown pre-selects the right entry on open.
            playlist_id = f"__model__{model_filter}"
        elif playlist_id is not None:
            from playlist_store import playlist_store as _ps
            pl = _ps.get(playlist_id)
            playlist_record_ids = set(pl.record_ids) if pl else set()
            records = [r for r in all_records if r.id in playlist_record_ids]
            auto_generate = pl.auto_gen if pl else True
        else:
            records = all_records
            auto_generate = True

        current_source = self._current_medium_source()
        # AnimateDiff moved from a video-tab sub-model to its own Create
        # medium chip (an artgen generator, source="artgen" id="animatediff")
        # — override the generic artgen folding above (SP-3d-3:
        # `_current_medium_source` always folds artgen mediums to "artgen")
        # so the generation loop still uses the dedicated "animatediff"
        # prompt vocabulary and the server-health gate stays bypassed
        # (AnimateDiff runs locally, no server needed), exactly as before.
        if self._active_medium_is_animatediff():
            current_source = "animatediff"

        try:
            win = attractor.AttractorWindow(
                records=records,
                system_prompt=self._prompt_gen_system_prompt,
                model_source=current_source,
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
                get_server_status=self._attractor_server_status,
                playlist_id=playlist_id,
                auto_generate=auto_generate,
                get_playlists=lambda: (
                    __import__("playlist_store").playlist_store.all()
                ),
                get_all_records=lambda: self._store.all_records() + self._store.artgen_records(),
                get_animate_inputs=(
                    self._get_animate_inputs if current_source == "animate" else None
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
        # Associate with the Gtk.Application so Wayland sets the correct app_id
        # (used by KDE and other compositors to look up the window icon).
        # Without this, plain Gtk.Window instances have no app_id and show a
        # generic icon in the taskbar / title bar.
        app = self.get_application()
        if app is not None:
            win.set_application(app)
        win.set_transient_for(self)
        win.connect("destroy", self._on_attractor_closed)
        self._attractor_win = win
        win.present()
        GLib.idle_add(win.start)

    def _on_attractor_closed(self, _win) -> None:
        """Called when the attractor window is destroyed.

        Purges any auto-generated TT-TV jobs still waiting in the queue so
        they don't continue running after the user has closed TT-TV.
        User-typed prompts (from_attractor=False) are preserved.
        """
        self._attractor_win = None
        before = len(self._queue)
        self._queue = [item for item in self._queue if not item.from_attractor]
        purged = before - len(self._queue)
        if purged:
            self._persist_queue()
            self._update_queue_display()
            self._set_status(f"TT-TV closed — {purged} queued auto-gen job{'s' if purged != 1 else ''} cancelled")

    def _resolve_attractor_model(self, model_source: str):
        """SP-3c-5: resolve which model attractor/TT-TV auto-gen should use
        for `model_source`, WITHOUT reading ControlPanel (which SP-3d
        deletes).

        Attractor jobs carry no model selection of their own — `attractor.py`
        always passes `model_id=""` — so before this task the only source of
        truth was ControlPanel's live `get_video_model()`/`get_image_model()`/
        `get_animatediff_args()`. That's replaced here with the SAME "is a
        model on" authority CLAUDE.md documents for this repo:
        `ModelStatusService.running_or_starting(capability)` — the identical
        call CreateView's auto-select and the health dot already use — so the
        attractor auto-generates with whatever model is currently RUNNING
        (READY, or else STARTING) for the capability, consistent with SP-2's
        auto-select philosophy.

        `running_or_starting` returns a `server_manager` key (e.g. "wan2.2",
        "flux", "animate") or `None` if nothing is running/starting for that
        capability. `_SERVER_KEY_TO_SOURCE_MODEL` — the same map
        `MainWindow.__init__` already uses to pre-select the source tab/model
        from `last_successful_deployment` — converts that key into the
        (model_source, model_key) pair used everywhere else in this file.
        `model_source == "animate"` has no video/image model key of its own
        (`_on_generate`'s animate branch never reads either), so the "animate"
        entry's empty model key is harmless — both return values stay at
        their medium defaults.

        No server running/starting for the capability (or an unrecognized
        key) falls back to each medium's documented default —
        `_DEFAULT_VIDEO_KEY`/`_DEFAULT_IMAGE_KEY`, the exact same fallback
        `_on_generate` itself uses when a caller passes no model at all
        (mirrors ControlPanel's fresh-session default of "animatediff").

        Returns `(video_model_key, image_model_key, animatediff_args)` — the
        three values `_on_attractor_generate`/`_on_attractor_priority_enqueue`
        thread straight through to `_on_generate`/`_QueueItem` unchanged.
        """
        capability = {"video": "video", "image": "image", "animate": "animate"}.get(
            model_source, "video"
        )
        server_key = self._status_service.running_or_starting(capability)
        src, mdl = _SERVER_KEY_TO_SOURCE_MODEL.get(server_key, (None, None))

        video_model_key = _DEFAULT_VIDEO_KEY
        image_model_key = _DEFAULT_IMAGE_KEY
        if src == "video" and mdl:
            video_model_key = mdl
        elif src == "image" and mdl:
            image_model_key = mdl
        # src == "animate" (or nothing running): video_model_key/
        # image_model_key stay at their defaults — irrelevant for the
        # "animate" model_source, which never reads either.

        animatediff_args = (
            dict(_ANIMATEDIFF_DEFAULTS) if video_model_key == "animatediff" else None
        )
        return video_model_key, image_model_key, animatediff_args

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
        # SP-3c-5: attractor/TT-TV jobs have no per-item model selection of
        # their own (attractor.py always passes model_id="") — resolved via
        # `_resolve_attractor_model` (the shared ModelStatusService, not
        # ControlPanel — see that method's docstring for the full reasoning)
        # once here, at the same call-time `_on_generate` would have read it
        # before SP-3a, and threaded through explicitly whether the job runs
        # immediately or is queued.
        video_model_key, image_model_key, animatediff_args = (
            self._resolve_attractor_model(model_source)
        )
        if self._worker and self._worker.is_alive():
            self._queue.insert(0, _QueueItem(prompt, neg, steps, seed, seed_image_path,
                                              model_source, guidance_scale,
                                              ref_video_path, ref_char_path,
                                              animate_mode, model_id,
                                              video_model_key=video_model_key,
                                              image_model_key=image_model_key,
                                              animatediff_args=animatediff_args))
            self._persist_queue()
            self._update_queue_display()
        else:
            self._on_generate(prompt, neg, steps, seed, seed_image_path,
                              model_source, guidance_scale, ref_video_path,
                              ref_char_path, animate_mode, model_id,
                              video_model_key=video_model_key,
                              image_model_key=image_model_key,
                              animatediff_args=animatediff_args)

    def _on_attractor_generate(self, prompt, neg, steps, seed, seed_image_path="",
                                model_source="video", guidance_scale=3.5,
                                ref_video_path="", ref_char_path="",
                                animate_mode="animation", model_id="") -> None:
        """
        Called by AttractorWindow when it wants to enqueue a new auto-generation.

        Starts the generation immediately if the worker is idle; otherwise parks
        it in the queue tagged as from_attractor=True so it is purged if TT-TV
        is closed before the job runs.
        """
        if not self._check_disk_space():
            return
        # SP-3c-5: same reasoning as `_on_attractor_priority_enqueue` above —
        # resolve via `_resolve_attractor_model` (ModelStatusService, not
        # ControlPanel) once, here, and pass it through explicitly rather
        # than letting `_on_generate` read it.
        video_model_key, image_model_key, animatediff_args = (
            self._resolve_attractor_model(model_source)
        )
        if self._worker and self._worker.is_alive():
            item = _QueueItem(prompt, neg, steps, seed, seed_image_path,
                              model_source, guidance_scale,
                              ref_video_path, ref_char_path, animate_mode,
                              model_id, from_attractor=True,
                              video_model_key=video_model_key,
                              image_model_key=image_model_key,
                              animatediff_args=animatediff_args)
            self._queue.append(item)
            self._persist_queue()
            self._update_queue_display()
        else:
            self._on_generate(prompt, neg, steps, seed, seed_image_path,
                              model_source, guidance_scale, ref_video_path,
                              ref_char_path, animate_mode, model_id,
                              video_model_key=video_model_key,
                              image_model_key=image_model_key,
                              animatediff_args=animatediff_args)

    def _update_attractor_btn(self) -> None:
        """Enable/disable the Attractor button based on whether any media exists.

        AnimateDiff generates locally without a server and can seed TT-TV from
        scratch, so we enable the button even when no prior records exist.
        SP-3d-3: "is AnimateDiff active" is now `_active_medium_is_animatediff()`
        (CreateView's active medium), not ControlPanel's
        `get_model_source()=="video" and get_video_model()=="animatediff"`.
        """
        has_media = len(self._store.all_records()) > 0
        self._attractor_btn.set_sensitive(has_media or self._active_medium_is_animatediff())

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

    def _fail_create_job(self, reason: str) -> None:
        """Clear Create-job state and surface *reason* in the inline result
        panel when `_on_generate` bails out via an early return before doing
        any actual work.

        Without this, a Create-launched job that hits one of `_on_generate`'s
        early-return guards (worker-already-running, disk space, AnimateDiff
        chip-busy) would leave `_create_job_active` stuck True forever — the
        panel would sit on "Generating…" with no way to clear, and because
        the flag is window-global, the very NEXT unrelated job (attractor/
        TT-TV/queue) would then wrongly skip its own gallery pending card and
        have its progress/finished/error misrouted into this stale Create
        panel until some later job happened to complete normally and clear
        the flag. Called at every early return in `_on_generate` that can
        fire for a Create-originated job, mirroring `_begin_create_job`'s
        try/except discipline (a panel error must never break anything else).
        A no-op when no Create job is active, so non-Create early returns are
        unaffected.
        """
        if not self._create_job_active:
            return
        try:
            self._create_view._result_panel.show_error(reason)
        except Exception:
            pass
        self._create_job_active = False

    def _on_generate(self, prompt, neg, steps, seed, seed_image_path="",
                     model_source="video", guidance_scale=3.5,
                     ref_video_path="", ref_char_path="",
                     animate_mode="animation", model_id="",
                     video_model_key: "str | None" = None,
                     image_model_key: "str | None" = None,
                     animatediff_args: "dict | None" = None) -> None:
        """
        SP-3a (decouple from ControlPanel): model SELECTION is entirely
        explicit-param driven — `video_model_key`/`image_model_key`/
        `animatediff_args` — this method never reads
        `self._controls.get_video_model()`/`get_image_model()`/
        `get_animatediff_args()`. Every caller (the legacy ControlPanel
        generate/enqueue button, Create's `_create_generate_native`, the
        queue's `_start_next_queued`, and attractor/TT-TV) resolves and
        passes these itself — see each caller for where its value comes
        from. SP-3d-3: the `set_busy` calls ControlPanel used to receive here
        are gone (audit-confirmed dead weight — it only ever drove
        ControlPanel's own Generate/Cancel buttons; nothing surviving reads
        `_controls._busy`), and the
        AnimateDiff-blackhole chip-busy guard below now reads
        `ModelStatusService` via `_running_generation_server()` instead of
        ControlPanel's own server-ready/running-model polling attributes.
        """
        if self._worker and self._worker.is_alive():
            self._fail_create_job("A generation is already running.")
            return
        if not self._check_disk_space():
            self._fail_create_job(
                "Disk space critically low — generation paused. Free up space to continue."
            )
            return

        # Inhibit screensaver if the user has that preference enabled.
        # The unload-on-lock safety net in attractor.py already handles crashes,
        # but inhibiting prevents the lock from activating in the first place.
        if _settings.get("inhibit_screensaver"):
            self._screensaver_inhibit()

        # Add the pending card to the gallery that matches the generation type,
        # and remember that gallery so _on_finished/_on_error update the right one.
        self._gen_gallery = self._gallery_for_type(model_source)
        # Create-originated jobs already show their own pending state in the
        # inline CreateResultPanel (_begin_create_job) — skip the redundant
        # gallery pending card for them. `_gen_gallery` is still set above so
        # the finished record lands in the correct gallery/store either way.
        if self._create_job_active:
            pending = None
        else:
            pending = self._gen_gallery.add_pending_card(prompt=prompt, model_source=model_source)
        # Do NOT call clear_prompt() here — the user may have typed a prompt they
        # haven't submitted yet, and auto-queue/attractor calls should not wipe it.
        # Prompt clearing is handled by ControlPanel._on_action_clicked (user-click only).

        if model_source == "image":
            img_model_key = model_id or image_model_key or _DEFAULT_IMAGE_KEY
            model_name = _IMAGE_MODEL_IDS.get(img_model_key, "flux.1-schnell")
            self._set_status(f"Generating image with {model_name}…")
            # SDXL (cpp_server) uses a different default guidance scale and
            # a dedicated service_key for correct auth token resolution.
            is_sdxl = img_model_key == "sdxl" or "sdxl" in model_name.lower()
            effective_guidance = guidance_scale if guidance_scale != 3.5 else (5.0 if is_sdxl else 3.5)
            image_client = APIClient(
                self._client.base_url,
                service_key="sdxl" if is_sdxl else "flux",
            ) if is_sdxl else self._client
            gen = ImageGenerationWorker(
                client=image_client,
                store=self._store,
                prompt=prompt,
                negative_prompt=neg,
                num_inference_steps=steps,
                seed=seed,
                guidance_scale=effective_guidance,
                model=model_name,
            )
        elif model_source == "animate":
            self._set_status("Submitting Animate-14B job…")
            # Character image: prefer ref_char_path (attractor/TT-TV auto-gen path),
            # fall back to seed_image_path (manual UI path — set via the seed image well).
            char_image = ref_char_path or seed_image_path
            gen = AnimateGenerationWorker(
                client=self._client,
                store=self._store,
                reference_video_path=ref_video_path,
                reference_image_path=char_image,
                prompt=prompt,
                num_inference_steps=steps,
                seed=seed,
                animate_mode=animate_mode,
                model="wan2.2-animate-14b",
            )
        else:
            # SP-3a: resolve the effective video key from the explicit param
            # first, then a canonical-id `model_id` (kept for callers that
            # only have the canonical id in hand — `_VIDEO_MODEL_ID_TO_KEY`
            # inverts CANONICAL id -> short key, so this only fires when
            # `model_id` happens to be canonical; the SHORT-key `model_id`
            # convention used everywhere else in this file falls through to
            # the medium default here, same as `self._controls.get_video_model()`
            # would have on a fresh/unrecognized session), then the medium
            # default — never `self._controls`. Reassigning the parameter
            # itself (rather than introducing a new local name) keeps every
            # other reference to `video_model_key` below unchanged.
            video_model_key = (
                video_model_key
                or _VIDEO_MODEL_ID_TO_KEY.get(model_id)
                or _DEFAULT_VIDEO_KEY
            )  # "wan2" | "mochi" | "skyreels" | "animatediff"

            if video_model_key == "animatediff":
                # Review fix: merge under the defaults rather than `animatediff_args
                # or {}` — a None/partial dict (e.g. a PRE-SP-3a `queue.json`
                # AnimateDiff item, restored with no "animatediff_args" key at
                # all) must not KeyError on the `ad["..."]` indexing below. A
                # full dict passes through unchanged (its values win).
                ad = {**_ANIMATEDIFF_DEFAULTS, **(animatediff_args or {})}
                # Chip-busy guard only applies to blackhole mode; cpu/sim don't need
                # exclusive Blackhole access and should not be blocked by a running
                # server — the `ad["mode"] == "blackhole"` check below is deliberately
                # first so `_running_generation_server()` (and therefore
                # `self._status_service`) is never consulted for cpu/sim, matching
                # the reachability of ControlPanel's server-ready attribute this
                # guard used to read directly (SP-3d-3: rehomed onto `ModelStatusService`).
                # Capability is "video" (SP-3d-3 review fix, not "any of
                # video/image") — AnimateDiff is a video-capability model, and
                # this branch only runs for `model_source == "video"` anyway
                # (the only caller of `_on_generate`'s video branch), matching
                # ControlPanel's old `_server_ready`, which was scoped to
                # whatever the CURRENT source was, never any unrelated one.
                if ad["mode"] == "blackhole":
                    server_ready, server_key = self._running_generation_server("video")
                    if server_ready and self._count_blackhole_chips() == 1:
                        model_lbl = self._display_label_for_server_key(server_key) or "a model"
                        busy_msg = (
                            f"Can't run AnimateDiff (blackhole) while {model_lbl} is loaded — "
                            "your Blackhole chip is busy. Stop the server first, then try again."
                        )
                        self._gen_gallery.remove_pending()
                        self._gen_gallery = None
                        self._set_status(busy_msg)
                        self._fail_create_job(busy_msg)
                        return
                self._set_status(f"Starting AnimateDiff generation ({ad['mode']})…")
                # Auto-derive chain_save path from a session temp file when requested.
                chain_save_path = None
                if ad["chain_save"]:
                    import tempfile, os
                    chain_save_path = os.path.join(
                        tempfile.gettempdir(), f"tt_ad_chain_{seed if seed >= 0 else 'auto'}.pt"
                    )
                gen = AnimateDiffGenerationWorker(
                    store=self._store,
                    prompt=prompt,
                    negative_prompt=ad["negative_prompt"],
                    steps=steps,
                    seed=seed if seed >= 0 else 42,
                    frames=int(_settings.get("animatediff_frames") or 8),
                    temporal_alpha=ad["temporal_alpha"],
                    model="animatediff-blackhole",
                    mode=ad["mode"],
                    lightning=ad["lightning"],
                    lightning_steps=ad["lightning_steps"],
                    multi_chip=ad["multi_chip"],
                    device_id=ad["device_id"],
                    chain_from=ad["chain_from"],
                    chain_save=chain_save_path,
                    chain_alpha=ad["chain_alpha"],
                    motion_adapter=ad["motion_adapter"],
                    motion_adapter_alpha=ad["motion_adapter_alpha"],
                    motion_adapter_skip=ad["motion_adapter_skip"],
                )
            else:
                model_name = _VIDEO_MODEL_IDS.get(
                    model_id or video_model_key, "wan2.2-t2v"
                )
                self._set_status(f"Submitting {model_name} video generation job…")
                # Resolve num_frames from the CLIP LENGTH slot setting.
                # Models in MODELS_WITH_FIXED_FRAMES hard-code their frame count in the
                # runner and ignore num_frames — pass None so the worker uses its default.
                from generation_config import clip_frames, MODELS_WITH_FIXED_FRAMES
                num_frames_arg: "int | None" = None
                slot = str(_settings.get("clip_length_slot") or "standard")
                if video_model_key not in MODELS_WITH_FIXED_FRAMES:
                    num_frames_arg = clip_frames(video_model_key, slot)

                # For I2V models (skyreels), base64-encode the seed image and send
                # it to the server as the conditioning frame.
                image_b64: "str | None" = None
                if video_model_key == "skyreels" and seed_image_path and Path(seed_image_path).is_file():
                    with open(seed_image_path, "rb") as _f:
                        _raw = _f.read()
                    _ext = Path(seed_image_path).suffix.lower().lstrip(".")
                    _mime = "image/jpeg" if _ext in ("jpg", "jpeg") else f"image/{_ext}"
                    image_b64 = f"data:{_mime};base64," + base64.b64encode(_raw).decode()

                gen = GenerationWorker(
                    client=self._client,
                    store=self._store,
                    prompt=prompt,
                    negative_prompt=neg,
                    num_inference_steps=steps,
                    seed=seed,
                    seed_image_path=seed_image_path,
                    model=model_name,
                    num_frames=num_frames_arg,
                    image=image_b64,
                )
        self._worker_gen = gen

        def run():
            try:
                gen.run_with_callbacks(
                    on_progress=lambda msg: GLib.idle_add(self._on_progress, msg, pending),
                    on_finished=lambda rec: GLib.idle_add(self._on_finished, rec),
                    on_error=lambda msg: GLib.idle_add(self._on_error, msg),
                )
            except Exception as _exc:
                import traceback as _tb
                GLib.idle_add(self._on_error, f"Worker crashed: {_exc}\n{_tb.format_exc()}")

        self._worker = threading.Thread(target=run, daemon=True)
        self._worker.start()

    def _on_cancel(self) -> None:
        if self._worker_gen:
            self._worker_gen.cancel()
        self._set_status("Cancelling…")

    # ── Create surface: real generation (Task 8 switchover subset) ─────────────
    #
    # CreateView (create_view.py) is a self-contained widget with zero
    # knowledge of workers, the API client, or artgen's subprocess plumbing —
    # by design (see its module docstring). This is the seam that gives its
    # `on_create(medium, params)` callback real teeth: dispatch by
    # `medium.source`, then hand off to the ONE existing generation path for
    # that source. Nothing here constructs a worker or an artgen subprocess
    # call directly except the artgen branch's `tt-ctl` shell-out, which
    # mirrors `pipeline_engine._h_artgen_generate` verbatim (same helpers) —
    # the same pattern a pipeline node already uses today.

    def _on_create_generate(self, medium, params: dict) -> None:
        """CreateView's `on_create` seam — dispatch a chosen medium + its
        collected panel params to real generation.

        - native mediums (image/video/animate) translate straight into the
          exact `_on_generate(...)` call the OLD ControlPanel/source-toggle
          UI already makes for that medium — same worker classes, same
          queue/callback plumbing. `_on_generate` is reused verbatim; this
          method never constructs a worker itself.
        - artgen mediums (verse/ansi/landscape/…) shell out to
          `tt-ctl artgen <generator> ...`, exactly the way
          `pipeline_engine._h_artgen_generate` already does for pipeline
          nodes (same `_flag_from_key`/`_append_flag_value`/`_run_tt_ctl`
          helpers, reused not reimplemented), then record the artifact into
          the media store the same way `ArtgenPanel._run_generation` does
          (`media_type="artgen"`, `generator_type=<generator>`) so it shows
          up in the existing Artgen gallery immediately.

        Fails soft everywhere: a bad/missing medium mapping, a failed
        subprocess, or any other exception surfaces as a status-bar message
        — a Create-surface click must never be able to crash the app.

        Re-entrancy: if a Create job is already in flight
        (`_create_job_active`), a second click (the Create CTA isn't
        disabled while generating) must NOT be allowed to re-enter
        `_begin_create_job` — that would overwrite the first job's pending
        display in the panel, and if the second call's dispatch then hit
        `_on_generate`'s worker-busy guard, `_fail_create_job` would clear
        the flag out from under the FIRST job, which is still running (its
        `_on_finished` would then see the flag already False and never
        update the panel). So this never touches `_begin_create_job`/
        `_create_job_active` while a job is already active.

        SP-3c-4: a second click for a NATIVE medium no longer no-ops — it
        ENQUEUES via `_create_enqueue_native` (the same `_QueueItem`/
        `_on_enqueue`/`_persist_queue` machinery the legacy ControlPanel's
        own Generate-when-busy button already uses), so `_start_next_queued`
        replays it once the running job finishes. Artgen mediums have no
        equivalent queue item type (their generation path shells out to
        `tt-ctl` directly, never through `_on_generate`/`self._queue`), so a
        second artgen click while busy still shows a status message rather
        than silently dropping the request or (worse) re-entering
        `_begin_create_job` and clobbering the running job's panel state.

        Review fix (whole-slice review, post-4938dfc): `_create_enqueue_native`
        only catches its own `_NativeGenerateGuardError` — any OTHER
        exception (e.g. a malformed `params` value failing `int()`/`float()`
        inside `_native_generate_args`) used to propagate straight out of
        this method, violating the "never crash the app" invariant the
        not-busy branch's own `try/except` below already upholds. Wrapped
        here too, but deliberately NOT via `_fail_create_job` — that would
        touch `_create_job_active`/show an error in the panel, both of which
        belong to the FIRST job, which is still running and uninvolved in
        this (failed) enqueue attempt. A friendly status message is enough.
        """
        if self._create_job_active:
            if medium.source == "native":
                try:
                    self._create_enqueue_native(medium, params)
                except Exception as exc:
                    self._set_status(f"Couldn't queue generation: {exc}")
            else:
                self._set_status(
                    "A generation is already running — "
                    f"{medium.label} can't be queued yet; try again once it finishes."
                )
            return
        try:
            if medium.source == "native":
                self._begin_create_job(medium, params)
                self._create_generate_native(medium, params)
            elif medium.source == "artgen":
                self._begin_create_job(medium, params)
                self._create_generate_artgen(medium, params)
            else:
                self._set_status(f"Don't know how to generate a {medium.label} yet.")
        except Exception as exc:
            # A synchronous exception anywhere in dispatch (a worker
            # constructor raising, `_gallery_for_type` raising, a bad
            # int()/float() param parse in `_create_generate_native`, ...)
            # happens AFTER `_begin_create_job` already set the flag + shown
            # "pending" — without clearing it here, the panel would stay
            # stuck on "Generating…" forever and the flag would bleed into
            # whatever job runs next. `_fail_create_job` is a no-op if no
            # Create job was actually started (e.g. the `else` branch above
            # never calls `_begin_create_job`), so it's safe to call
            # unconditionally.
            self._fail_create_job(str(exc))
            self._set_status(f"Couldn't start generation: {exc}")

    def _begin_create_job(self, medium, params: dict) -> None:
        """Mark a Create-originated generation as active and put the inline
        result panel (`self._create_view._result_panel`) into its "pending"
        state before dispatching to the real generation path.

        Shared by both the native and artgen branches of `_on_create_generate`
        (artgen's own finished/error forwarding is Task 4 — showing pending
        here for artgen too is harmless and desired now). Wrapped in its own
        try/except: a panel/widget error must never block generation itself.
        """
        self._create_job_active = True
        try:
            self._create_view._result_panel.show_pending(params.get("prompt", ""), medium)
        except Exception:
            pass

    def _native_generate_args(self, medium, params: dict):
        """Translate a native medium's `CreateParamPanel.collect()` dict into
        the exact positional args + kwargs `_on_generate`/`_on_enqueue` share
        (identical signatures) for that medium — the same call the old
        ControlPanel/source-toggle UI already makes.

        `params["model"]` (from `ImageParamPanel`/`VideoParamPanel`) is
        already the CANONICAL server-side model id (e.g. "flux.1-schnell") —
        `_on_generate`'s `model_id=` parameter instead expects the SHORT key
        it uses to look itself up in its own `_IMAGE_MODEL_IDS`/
        `_VIDEO_MODEL_IDS` dicts, so the inverse maps above convert back
        before the call. An unrecognized canonical id (e.g. an injected fake
        in a test) falls back to each medium's documented default key rather
        than raising.

        Returns `(args, kwargs)`. Raises `_NativeGenerateGuardError(message)`
        when a hard pre-flight guard blocks generation entirely (currently
        only SkyReels-I2V with no seed image) — this method never touches
        `self._set_status`/`_fail_create_job`/`_create_job_active` itself so
        it's equally reusable by `_create_generate_native` (the not-busy
        path, which owns the active job and clears its flag on a guard
        failure) and `_create_enqueue_native` (SP-3c-4's busy-path enqueue,
        which must NOT touch the already-running job's flag).
        """
        prompt = params.get("prompt", "")

        if medium.id == "image":
            model_key = _IMAGE_MODEL_ID_TO_KEY.get(params.get("model", ""), "flux")
            args = (
                prompt,
                params.get("negative_prompt", ""),
                int(params.get("num_inference_steps", 20)),
                int(params.get("seed", -1)),
            )
            kwargs = dict(
                # SP-3c-1: ImageParamPanel's SeedImageWell — "" (the default
                # when no image is chosen) preserves today's exact
                # text-to-image behavior; a non-empty path drives i2i.
                seed_image_path=params.get("seed_image_path", ""),
                model_source="image",
                guidance_scale=float(params.get("guidance_scale", 3.5)),
                model_id=model_key,
                # SP-3a: passed explicitly now that _on_generate no longer
                # reads self._controls.get_image_model() itself.
                image_model_key=model_key,
            )
            return args, kwargs

        if medium.id == "animate":
            # AnimateGenerationWorker (via _on_generate's animate branch)
            # takes no negative_prompt at all — pass "" for the positional
            # `neg` slot, matching what the old ControlPanel's Animate tab
            # already sends.
            args = (
                prompt,
                "",
                int(params.get("num_inference_steps", 20)),
                int(params.get("seed", -1)),
            )
            kwargs = dict(
                # `_on_generate`'s own default is also "" — set explicitly
                # (rather than omitted, as the pre-Task-4 code did) only
                # because `_on_enqueue` has NO default for this parameter
                # (unlike `_on_generate`), so a caller sharing this dict with
                # both must always supply it. Same value either way — no
                # behavior change for the not-busy path.
                seed_image_path="",
                model_source="animate",
                ref_video_path=params.get("reference_video_path", ""),
                ref_char_path=params.get("reference_image_path", ""),
                animate_mode=params.get("animate_mode", "animation"),
            )
            return args, kwargs

        # "video"
        model_key = _VIDEO_MODEL_ID_TO_KEY.get(params.get("model", ""), "wan2")
        # SP-3c-1 review fix (Important): SkyReels-I2V requires a
        # conditioning image — the exact reason it was pulled from the
        # Video door in v0.27.1 (see `_VIDEO_MODEL_IDS`'s module comment in
        # create_param_panels.py). Re-enabling it in the model list without
        # also gating generation here would let a user submit an I2V request
        # with `seed_image_path=""`, which can only fail server-side with no
        # explanation — mirrors ControlPanel's own guard
        # (`_seed_image_required()` + `_on_action_clicked`'s check, ~line
        # 6579) so both surfaces enforce the same rule. Blocked BEFORE
        # calling `_on_generate`/`_on_enqueue` at all — no worker is started
        # and nothing is queued.
        if model_key == "skyreels" and not params.get("seed_image_path"):
            raise _NativeGenerateGuardError(
                "SkyReels I2V requires a starting image — add one to the "
                "seed image well before generating."
            )
        # SP-3a (decouple `_on_generate` from ControlPanel): `_on_generate`
        # used to pick the video worker from `self._controls.get_video_model()`
        # regardless of `model_id` — which DEFAULTS to "animatediff" on a
        # fresh session until a health check finds a running video server —
        # so choosing Wan2.2/Mochi here would silently run AnimateDiff unless
        # CreateView first synced `self._controls._video_model` (the v0.27.1
        # "FIX 1" hack, previously here). `_on_generate` now takes
        # `video_model_key` as an explicit param and no longer reads
        # `self._controls` for it at all, so that sync is unnecessary —
        # passing the medium's own resolved key directly is sufficient and
        # cannot clobber the legacy Image tab's `_image_model` the way the
        # old `_set_model()` route could.
        #
        # SP-3c-2 (native AnimateDiff in Create): only build/forward
        # `animatediff_args` when AnimateDiff is actually the selected video
        # model — every other model keeps the pre-existing
        # `animatediff_args=None` default, byte-identical to before this task
        # (parity). `VideoParamPanel.collect()`'s own "animatediff_args" is
        # already complete (every widget has a real default — see that
        # method's docstring), but the merge over `_ANIMATEDIFF_DEFAULTS`
        # here is a defensive belt-and-suspenders: any caller that reaches
        # this branch with a partial/missing dict (e.g. a hand-built
        # `params` in a test, or a future caller that doesn't go through
        # VideoParamPanel at all) still gets a dict `_on_generate`'s
        # `ad["..."]` indexing can never KeyError on.
        animatediff_args = None
        if model_key == "animatediff":
            animatediff_args = {
                **_ANIMATEDIFF_DEFAULTS, **(params.get("animatediff_args") or {})
            }
        args = (
            prompt,
            params.get("negative_prompt", ""),
            int(params.get("num_inference_steps", 20)),
            int(params.get("seed", -1)),
        )
        kwargs = dict(
            # SP-3c-1: VideoParamPanel's SeedImageWell — "" preserves today's
            # exact text-to-video behavior for wan2/mochi; SkyReels-I2V
            # (re-enabled this same task) needs it non-empty. `_on_generate`'s
            # video branch already knows how to send this to the server for
            # `video_model_key == "skyreels"` — see the base64-encode block
            # further down that method.
            seed_image_path=params.get("seed_image_path", ""),
            model_source="video",
            model_id=model_key,
            video_model_key=model_key,
            animatediff_args=animatediff_args,
        )
        # NOTE (remaining concern, see task-8-report.md):
        # VideoParamPanel.collect()'s "num_frames" has no destination in
        # `_on_generate` — that method derives num_frames internally from
        # generation_config.clip_frames + the Preferences "clip length slot"
        # setting, not from a parameter. Reusing `_on_generate` verbatim
        # (required — never reimplement worker launching) means CreateView's
        # frame-count spinner is currently cosmetic for the video medium.
        # Out of scope to fix here (would require changing the one function
        # every rule says must stay untouched).
        return args, kwargs

    def _create_generate_native(self, medium, params: dict) -> None:
        """Not-busy path: build this native medium's `_on_generate(...)` call
        via `_native_generate_args` and dispatch it immediately — this IS the
        active Create job (`_begin_create_job` already ran), so a guard
        failure clears its flag via `_fail_create_job`."""
        try:
            args, kwargs = self._native_generate_args(medium, params)
        except _NativeGenerateGuardError as exc:
            msg = str(exc)
            self._set_status(msg)
            # `_begin_create_job` already set _create_job_active + showed
            # "pending" in the result panel before this method was called
            # (see `_on_create_generate`) — `_fail_create_job` clears that
            # flag and surfaces the error in the same panel, mirroring
            # `_create_generate_artgen`'s "no generator mapped" early-return
            # pattern.
            self._fail_create_job(msg)
            return
        self._on_generate(*args, **kwargs)

    def _create_enqueue_native(self, medium, params: dict) -> None:
        """SP-3c-4: busy path for a native medium — a Create job is already
        running (`_create_job_active`), so instead of generating now this
        ENQUEUES via the exact same machinery the legacy ControlPanel's own
        Generate-when-busy button uses (`_on_enqueue` -> `_QueueItem` ->
        `_persist_queue`/`_update_queue_display`). `_start_next_queued` later
        drains the item straight into `_on_generate` with these SAME args/
        kwargs (model/seed-image/animatediff_args all captured on the
        `_QueueItem`), so the queued job replays faithfully — identical to
        what would have happened had it been allowed to generate immediately.

        Unlike `_create_generate_native`, a guard failure here must NOT touch
        `_create_job_active` or the result panel — that state belongs to the
        job that's still running, not to this (rejected) enqueue attempt.
        """
        try:
            args, kwargs = self._native_generate_args(medium, params)
        except _NativeGenerateGuardError as exc:
            self._set_status(str(exc))
            return
        self._on_enqueue(*args, **kwargs)

    def _create_generate_artgen(self, medium, params: dict) -> None:
        """Artgen mediums generate the SAME way `pipeline_engine`'s
        `TTLGArtgenGenerate` pipeline node does: shell out to
        ``tt-ctl artgen <generator> --output <path> [--flag value...]``
        (`pipeline_engine._flag_from_key`/`_append_flag_value`/`_run_tt_ctl`
        reused verbatim), then record the artifact into the media store the
        same way `ArtgenPanel._run_generation` does — `media_type="artgen"`,
        `generator_type=<generator>` — so the new artifact shows up in the
        existing Artgen gallery immediately, without a separate re-scan.

        Runs entirely off the GTK main thread (subprocess + disk + sqlite
        I/O); only the final status message + gallery refresh are posted
        back via `GLib.idle_add`, per CLAUDE.md's GTK threading rule.

        The idea-door's `params["prompt"]` is, by default, NOT forwarded as
        a CLI flag — most artgen generators have no common `--prompt` flag
        (each has its own bespoke vocabulary: `--theme`, `--form`,
        `--palette`, …), so passing it through would make every artgen
        generation fail with an "unrecognized argument" error from argparse.

        FIX 4 (Create's AnimateDiff medium always rendered its default "a
        candle flame flickering" instead of the user's prompt): the
        blanket skip above was wrong for the artgen "animatediff" plugin,
        whose `add_args` DOES declare `--prompt` (`plugins/animatediff/
        plugin.py`). `_artgen_accepts_prompt(generator)` introspects the
        generator's own argparse (mirroring `artgen_bool_flags`'s approach)
        to tell the two cases apart: forward `params["prompt"]` as
        `--prompt <value>` only when the generator declares it AND the
        value is non-empty (an empty/whitespace prompt lets the generator's
        own default apply, same as a bare CLI invocation would).
        """
        generator = medium.generator
        if not generator:
            msg = f"No artgen generator mapped for {medium.label}."
            self._set_status(msg)
            # _begin_create_job already set _create_job_active + shown
            # "pending" before this method was called — this terminal path
            # must clear it too (unreachable today since discover_mediums
            # always sets a generator, but every terminal path should leave
            # the flag consistent, not just the common ones).
            self._fail_create_job(msg)
            return

        prompt = params.get("prompt", "")
        self._set_status(f"Generating {medium.label}…")

        def run():
            try:
                import uuid as _uuid
                from datetime import datetime, timezone

                import artgen as _artgen
                from artgen_thumb import make_thumbnail, make_artgen_path
                from media_store import MediaRecord
                from media_store import media_store as _ms
                from pipeline_engine import _append_flag_value, _flag_from_key, _run_tt_ctl
                from create_param_panels import artgen_bool_flags

                try:
                    ext = _artgen.get(generator).output_ext
                except Exception:
                    ext = ".txt"

                short_id = str(_uuid.uuid4())[:8]
                out_path = make_artgen_path(short_id, ext)

                argv = ["artgen", generator, "--output", str(out_path)]
                # FIX 3 (whole-branch review — default-True bool switches can't
                # emit their "off" spelling): a store_true default=True flag
                # paired with a store_false (e.g. landscape's --mountains /
                # --no-mountains, both dest "mountains") renders as ONE switch.
                # Turning it OFF collects `mountains=False`, but
                # `_append_flag_value` only knows the positive flag and OMITS a
                # False value — so neither --mountains nor --no-mountains ever
                # reaches the generator and it silently falls back to its
                # default (mountains ON). `artgen_bool_flags` gives each bool
                # dest its (positive, negative) CLI spelling straight from the
                # generator's own argparse, so we emit the EXPLICIT flag that
                # matches the switch: --mountains when ON, --no-mountains when
                # OFF (or nothing OFF when there is no negative spelling, e.g. a
                # bare --glitch — the previous, correct behavior for those).
                # Done here, not in the shared `_append_flag_value` (which the
                # pipeline engine also uses and must stay generator-agnostic).
                bool_flags = artgen_bool_flags(generator)
                # FIX 4 (see this method's docstring): forward the resolved
                # prompt ONLY for a generator that actually declares
                # `--prompt` (currently just "animatediff"), and only when
                # there is a real value to forward — an empty/whitespace
                # prompt is skipped so the generator's own default (e.g.
                # animatediff's "a candle flame flickering") still applies,
                # matching what a bare `tt-ctl artgen <generator>` invocation
                # would do. This runs once here, OUTSIDE the per-key loop
                # below (which still unconditionally skips the "prompt" key
                # itself, so this is the ONLY place "--prompt" can be added —
                # never duplicated).
                if isinstance(prompt, str) and prompt.strip() and _artgen_accepts_prompt(generator):
                    _append_flag_value(argv, "--prompt", prompt)
                for key, value in params.items():
                    if key == "prompt" or value is None:
                        continue
                    if isinstance(value, bool):
                        pos_flag, neg_flag = bool_flags.get(key, (None, None))
                        if value:
                            argv.append(pos_flag or _flag_from_key(key))
                        elif neg_flag:
                            argv.append(neg_flag)
                        # False + no negative spelling: omit entirely (a bare
                        # store_true with no --no-x — the generator's default
                        # already means "off").
                        continue
                    # FIX 2 (task-8 review — artgen "animatediff" medium failed
                    # 100% via Create): argparse `action="append"` flags (e.g.
                    # animatediff's `--per-chip-prompt`/`--prompt-schedule`,
                    # default None) are rendered by ArtgenParamPanel as plain
                    # entries that collect "" — not None — so the None-skip
                    # above misses them, and forwarding `--prompt-schedule ""`
                    # makes `tt-ctl artgen animatediff` raise every time. Only
                    # forward values that carry a real choice: skip an empty/
                    # whitespace-only string and an empty list/tuple. A blank
                    # scalar entry (e.g. an untouched `--theme`) is likewise
                    # skipped so the generator's own default applies, which is
                    # the same thing a bare CLI invocation would do.
                    if isinstance(value, str) and not value.strip():
                        continue
                    if isinstance(value, (list, tuple)) and not value:
                        continue
                    _append_flag_value(argv, _flag_from_key(key), value)
                _run_tt_ctl(argv)

                thumb_path = out_path.parent / "thumbnails" / (out_path.stem + ".png")
                try:
                    thumb_path = make_thumbnail(out_path, thumb_path)
                except Exception:
                    thumb_path = Path("")

                safe_params = {
                    k: v for k, v in params.items()
                    if isinstance(v, (str, int, float, bool, type(None)))
                }
                rec = MediaRecord(
                    id=str(_uuid.uuid4()),
                    media_type="artgen",
                    created_at=datetime.now(timezone.utc).isoformat(),
                    file_path=str(out_path),
                    thumbnail_path=str(thumb_path) if Path(thumb_path).exists() else "",
                    prompt=(prompt or medium.label)[:500],
                    model_id="artgen",
                    generator_type=generator,
                    params=json.dumps(safe_params),
                    starred=0,
                )
                # `CreateResultPanel._build_artifact_widget` (create_view.py)
                # reads `record.media_file_path` — the attribute name
                # `history_store.GenerationRecord` exposes as a computed
                # property for the native path. `media_store.MediaRecord`
                # only has `file_path`, so without this the panel's "finished"
                # render would always fall through to its "Result file not
                # found." placeholder even though the artifact exists. Set
                # the same duck-typed attribute the panel already documents
                # accepting ("a duck-typed stand-in works too"). Safe:
                # `MediaRecord` is a plain (non-frozen, non-slotted) dataclass
                # and `MediaStore.add`/`_upsert` only read its declared
                # fields, so this extra instance attribute is never persisted.
                rec.media_file_path = str(out_path)
                _ms.add(rec)
                _ms.ensure_auto_playlists()

                GLib.idle_add(self._on_create_artgen_done, medium)
                # Task 4: forward the just-written record to the inline
                # Create result panel and clear the Create-job flag — see
                # `_on_create_artgen_finished` below.
                GLib.idle_add(self._on_create_artgen_finished, rec)
            except Exception as exc:
                GLib.idle_add(self._on_create_artgen_error, medium, str(exc))
                # Task 4: reuse `_fail_create_job` (Task 3) so a failed artgen
                # Create job surfaces in the panel and clears the flag exactly
                # like a failed native job does — no terminal path of this
                # worker may leave `_create_job_active` stuck True.
                GLib.idle_add(self._fail_create_job, str(exc))

        threading.Thread(target=run, daemon=True).start()

    def _on_create_artgen_done(self, medium) -> bool:
        """Main-thread completion callback for `_create_generate_artgen`."""
        self._set_status(f"{medium.label} ready.")
        # SP-3d-5: reaches the standalone ArtgenGallery directly now (used to
        # reach past ArtgenPanel to its wrapped `_gallery` — see the audit,
        # .superpowers/sdd/sp3d-audit.md §3) so Discover's artgen page shows
        # the freshly-written record.
        artgen_gallery = getattr(self, "_artgen_gallery", None)
        if artgen_gallery is not None:
            try:
                artgen_gallery.refresh()
            except Exception:
                pass  # a refresh failure must never crash the Create surface
        return GLib.SOURCE_REMOVE

    def _on_create_artgen_error(self, medium, msg: str) -> bool:
        """Main-thread error callback for `_create_generate_artgen`."""
        self._set_status(f"Couldn't generate {medium.label}: {msg}")
        return GLib.SOURCE_REMOVE

    def _on_create_artgen_finished(self, record) -> bool:
        """Main-thread success callback that forwards an artgen Create job's
        freshly-written media-store `record` to the inline `CreateResultPanel`
        and clears `_create_job_active` (Task 4).

        Mirrors `_on_finished`'s Create-forwarding block for the native path:
        a no-op when no Create job is active (so a non-Create artgen call —
        none exist today, but the guard is free and consistent — is
        unaffected), and the panel call is try/except-wrapped so a
        panel/widget error can never leave the flag stuck True. Without this,
        `_begin_create_job`'s "pending" state (shown before the subprocess
        runs) would never resolve for the artgen path — the panel would sit
        on "Generating…" forever and the window-global flag would stay True,
        wrongly affecting the next unrelated job (see `_fail_create_job`'s
        docstring for the exact failure mode this class of bug causes).
        """
        if self._create_job_active:
            try:
                self._create_view._result_panel.show_finished(record)
            except Exception:
                pass
            self._create_job_active = False
        return GLib.SOURCE_REMOVE

    # ── Server start / stop ────────────────────────────────────────────────────

    def _on_start_server(self, model_source: str) -> None:
        """Launch the server script matching the current source + model selection.

        SP-3d-3: `model_key` is resolved via `_current_medium_model_key`
        (CreateView's scoped model dropdown, translated through
        `_SERVER_KEY_TO_SOURCE_MODEL`) instead of
        `self._controls.get_video_model()`/`get_image_model()`.
        """
        model_key = self._current_medium_model_key(model_source)

        script_name, label = _SERVER_SCRIPTS.get(
            (model_source, model_key), ("start_wan.sh", "Wan2.2 video")
        )
        script_path = str(Path(__file__).parent.parent / "bin" / script_name)

        server_key = None
        try:
            server_key = _server_key_for_script(script_name)
            if server_key:
                self._status_service.note_starting(server_key)
        except Exception:
            pass

        # `append_server_log`/`set_server_launching` route to the standalone
        # ServersControl widget (SP-3b Task 2) rather than ControlPanel's now-
        # unmounted equivalents. ServersControl tracks "launching" per key, so
        # fall back to the script name when a server_manager key can't be
        # resolved — the log still streams and the panel still reveals/
        # settles correctly either way (see ServersControl.set_server_launching).
        launch_key = server_key or script_name
        self._servers_control.set_server_launching(launch_key, True)
        self._servers_control.append_server_log(f"Starting {label} server ({script_name} --gui)…")
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
                    GLib.idle_add(self._servers_control.append_server_log, stripped)
                    # The start script prints "Log file: /path/to/workflow.log" just
                    # before it exits in --gui mode.  Capture it so we can tail it.
                    if stripped.startswith("Log file: "):
                        _detected_log_file = stripped[len("Log file: "):]
                proc.wait()
                if proc.returncode != 0:
                    GLib.idle_add(self._servers_control.append_server_log,
                                  f"Script exited with code {proc.returncode}")
                    GLib.idle_add(self._set_status, "Server start script failed — check log")
                    GLib.idle_add(self._servers_control.set_server_launching, launch_key, False)
                    GLib.idle_add(self._hw_statusbar.update_error, "start failed — click for log")
                else:
                    GLib.idle_add(self._set_status,
                                  f"{label} server started — waiting for health check…")
                    # Leave the log panel open; ServersControl auto-collapses it once
                    # ModelStatusService reports `launch_key` READY in a snapshot (see
                    # ServersControl._refresh_launching). If the script handed off to
                    # a Docker log file, tail it so the user can see server startup
                    # progress without leaving the app.
                    if _detected_log_file:
                        GLib.idle_add(self._start_log_tail, _detected_log_file)
            except Exception as e:
                GLib.idle_add(self._servers_control.append_server_log, f"Error: {e}")
                GLib.idle_add(self._set_status, f"Server start error: {e}")
                GLib.idle_add(self._servers_control.set_server_launching, launch_key, False)
            finally:
                self._server_proc = None

        threading.Thread(target=run, daemon=True).start()

    def _start_log_tail(self, log_path: str) -> None:
        """
        Start a background thread that tails log_path and appends new lines to the
        server log panel.

        Called after the start script exits and hands off to the Docker log file.
        The tail stops when the health check confirms the server is ready (via
        `_render_status_snapshot` clearing `self._log_tail_stop` on the
        ModelStatusService snapshot that first reports READY -- see SP-3d-6),
        or when the server is stopped.
        """
        # Cancel any previous tail still running (e.g., restart after stop)
        if self._log_tail_stop:
            self._log_tail_stop.set()

        stop = threading.Event()
        self._log_tail_stop = stop

        # Show a visual separator in the log panel so the user knows we switched sources
        GLib.idle_add(
            self._servers_control.append_server_log,
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
                            stripped = line.rstrip()
                            GLib.idle_add(
                                self._servers_control.append_server_log, stripped
                            )
                            # Update the phase label in the status bar when we
                            # recognise a known milestone in the server log.
                            phase = _detect_phase(stripped)
                            if isinstance(phase, str):
                                GLib.idle_add(self._hw_statusbar.set_phase, phase)
            except OSError as e:
                GLib.idle_add(
                    self._servers_control.append_server_log, f"[log tail error: {e}]"
                )

        threading.Thread(target=tail, daemon=True).start()

    def _on_stop_server(self) -> None:
        """Run the stop command (via start_wan.sh --stop) in a background thread."""
        # Stop any active log tail before clearing the log panel
        if self._log_tail_stop:
            self._log_tail_stop.set()
            self._log_tail_stop = None
        # Both video and image use the same Docker image, so either script can stop it.
        script_path = str(Path(__file__).parent.parent / "bin" / "start_wan_qb2.sh")

        # There's no model_source argument here (unlike _on_start_server) since
        # any script stops the one shared port-8000 container. Resolve "which
        # server_manager key is presumably running" the same way
        # _on_start_server would resolve what to *start* for the current
        # medium's source/model selection (SP-3d-3: CreateView, not
        # ControlPanel), so note_stopping targets the right key rather than
        # guessing.
        server_key = None
        try:
            _stop_source = self._current_medium_source()
            _stop_model_key = self._current_medium_model_key(_stop_source)
            _stop_script_name, _ = _SERVER_SCRIPTS.get(
                (_stop_source, _stop_model_key), ("start_wan.sh", "Wan2.2 video")
            )
            server_key = _server_key_for_script(_stop_script_name)
            if server_key:
                self._status_service.note_stopping(server_key)
        except Exception:
            pass

        # See _on_start_server's comment: routes to ServersControl (SP-3b Task
        # 2) instead of ControlPanel's now-unmounted log/launching widgets.
        launch_key = server_key or "stop"
        self._servers_control.set_server_launching(launch_key, True)
        self._servers_control.append_server_log("Stopping inference server…")
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
                    GLib.idle_add(self._servers_control.append_server_log, line)
                GLib.idle_add(self._set_status, "Server stopped.")
            except Exception as e:
                GLib.idle_add(self._servers_control.append_server_log, f"Error: {e}")
                GLib.idle_add(self._set_status, f"Server stop error: {e}")
            finally:
                self._server_proc = None
                GLib.idle_add(self._servers_control.set_server_launching, launch_key, False)

        threading.Thread(target=run, daemon=True).start()

    # ── Standalone ServersControl callbacks (SP-3b Task 2) ─────────────────────
    #
    # Wired as ServersControl(on_start=, on_stop=, on_restart=) in _build_ui().
    # Each callback receives a `server_manager.SERVERS` key directly (the
    # popover row already knows exactly which service it is — no model_source
    # -> script -> key resolution needed here, unlike _on_start_server/
    # _on_stop_server above). They otherwise mirror ControlPanel.
    # _on_servers_action's worker (same _sm.start/stop/restart(key) calls,
    # same note_starting/note_stopping hooks, noted only AFTER the real
    # server_manager call succeeds — same ordering _on_servers_action uses,
    # so a synchronous `_sm.*` failure never tells the status service a
    # launch is in progress that never actually started), but:
    #   - route log/launching feedback to ServersControl instead of
    #     ControlPanel's now-unmounted popover dots + busy-lock buttons.
    #   - do NOT run their own "poll until healthy" loop — ServersControl's
    #     row dots and log-auto-collapse already come from subscribing to
    #     `self._status_service`, whose own poll thread is what will surface
    #     READY (see task-1-report.md's "Design decisions" section).

    def _on_servers_control_start(self, key: str) -> None:
        """Start one managed service by its server_manager key."""
        self._servers_control.set_server_launching(key, True)
        self._servers_control.append_server_log(f"Starting {key}…")

        def run() -> None:
            try:
                _sm.start(key, gui=True)
                try:
                    self._status_service.note_starting(key)
                except Exception:
                    pass
            except Exception as e:
                GLib.idle_add(self._servers_control.append_server_log, f"Error: {e}")
            finally:
                GLib.idle_add(self._servers_control.set_server_launching, key, False)

        threading.Thread(target=run, daemon=True).start()

    def _on_servers_control_stop(self, key: str) -> None:
        """Stop one managed service by its server_manager key."""
        self._servers_control.set_server_launching(key, True)
        self._servers_control.append_server_log(f"Stopping {key}…")

        def run() -> None:
            try:
                _sm.stop(key)
                try:
                    self._status_service.note_stopping(key)
                except Exception:
                    pass
            except Exception as e:
                GLib.idle_add(self._servers_control.append_server_log, f"Error: {e}")
            finally:
                GLib.idle_add(self._servers_control.set_server_launching, key, False)

        threading.Thread(target=run, daemon=True).start()

    def _on_servers_control_restart(self, key: str) -> None:
        """Restart one managed service by its server_manager key."""
        self._servers_control.set_server_launching(key, True)
        self._servers_control.append_server_log(f"Restarting {key}…")

        def run() -> None:
            try:
                _sm.restart(key, gui=True)
                try:
                    self._status_service.note_starting(key)
                except Exception:
                    pass
            except Exception as e:
                GLib.idle_add(self._servers_control.append_server_log, f"Error: {e}")
            finally:
                GLib.idle_add(self._servers_control.set_server_launching, key, False)

        threading.Thread(target=run, daemon=True).start()

    # ── Queue ──────────────────────────────────────────────────────────────────

    def _persist_queue(self) -> None:
        """Save the current queue to disk so it can be reloaded after a crash.

        TT-TV auto-gen items (from_attractor=True) are excluded: they should
        not survive a restart because TT-TV is no longer open, and persisting
        them would cause the same server job to be re-submitted on next launch.
        """
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
                # SP-3a: persisted so a restored queue (after a crash/restart)
                # still replays without _start_next_queued reading
                # self._controls (all the AD arg values are plain
                # str/float/bool/None — JSON-safe).
                "video_model_key": item.video_model_key,
                "image_model_key": item.image_model_key,
                "animatediff_args": item.animatediff_args,
            }
            for item in self._queue
            if not item.from_attractor
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
                    video_model_key=d.get("video_model_key"),
                    image_model_key=d.get("image_model_key"),
                    animatediff_args=d.get("animatediff_args"),
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
        # SP-3c-4: keep Create's own pending-queue display (in the inline
        # result pane, near the recents strip) in sync too — every call site
        # that mutates `self._queue` already routes through this method
        # (enqueue, cancel, drain, restore), so one call here covers all of
        # them without touching each call site individually.
        self._refresh_create_queue_display()

    def _refresh_create_queue_display(self) -> None:
        """Push the current `self._queue` into CreateView's pending-queue
        display (`CreateResultPanel.set_queue`, near the recents strip).

        Wrapped in `GLib.idle_add`: `_update_queue_display` (this method's
        only caller) is itself only ever called from the main thread today,
        but a couple of its own callers are reached via worker-thread
        callbacks that already forward through `GLib.idle_add` themselves
        (see `_start_next_queued`'s callers) — scheduling here too costs
        nothing when already on the main thread and removes any doubt for a
        future caller that isn't. The whole body is try/except'd, matching
        `_begin_create_job`/`_fail_create_job`'s discipline: a panel/widget
        error must never break the underlying queue mutation that triggered
        the refresh. A no-op before `self._create_view` exists (e.g. very
        early startup) or if CreateView doesn't expose `refresh_queue` (a
        minimal test double).
        """
        create_view = getattr(self, "_create_view", None)
        if create_view is None:
            return
        pending = list(self._queue)

        def _do() -> bool:
            try:
                create_view.refresh_queue(pending, self._on_queue_remove)
            except Exception:
                pass
            return False

        GLib.idle_add(_do)

    def _on_enqueue(self, prompt, neg, steps, seed, seed_image_path,
                    model_source="video", guidance_scale=3.5,
                    ref_video_path="", ref_char_path="",
                    animate_mode="animation", model_id="",
                    video_model_key: "str | None" = None,
                    image_model_key: "str | None" = None,
                    animatediff_args: "dict | None" = None) -> None:
        if not self._check_disk_space():
            return
        # SP-3a: the three model-selection kwargs are captured by the CALLER
        # (ControlPanel's own button, or _on_theme_queue_shots forwarding
        # ControlPanel's get_generation_defaults()) — this method just stores
        # them on the item so _start_next_queued can replay faithfully
        # without itself reading self._controls.
        self._queue.append(_QueueItem(prompt, neg, steps, seed, seed_image_path,
                                      model_source, guidance_scale,
                                      ref_video_path, ref_char_path, animate_mode,
                                      model_id, video_model_key=video_model_key,
                                      image_model_key=image_model_key,
                                      animatediff_args=animatediff_args))
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
        # Guard against a race where the recovery dialog starts a worker between
        # the time _restore_queue() schedules this via GLib.idle_add and the time
        # it actually fires.  Starting a second worker would produce a duplicate
        # pending card and lose track of the first worker.
        if self._worker and self._worker.is_alive():
            return False
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
        # SP-3a: replay the model selection captured on the item at enqueue
        # time — this method never reads self._controls.
        self._on_generate(item.prompt, item.negative_prompt,
                          item.steps, item.seed, item.seed_image_path,
                          item.model_source, item.guidance_scale,
                          item.ref_video_path, item.ref_char_path, item.animate_mode,
                          item.model_id,
                          video_model_key=item.video_model_key,
                          image_model_key=item.image_model_key,
                          animatediff_args=item.animatediff_args)
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

    def _on_refresh_remote_library(self) -> None:
        """
        File → Refresh Remote Library

        Re-fetches the inventory from the remote server — updating metadata and
        downloading any new thumbnails — without downloading video files.  This
        is the lightweight "sync metadata" operation.

        Only available when the app was started with ``--server http://host:8000``
        pointing at a non-local machine.
        """
        if not self._inventory_url:
            self._set_status("No remote inventory configured (run with --server http://remote:8000).")
            return
        self._set_status("Refreshing remote library…")
        threading.Thread(target=self._fetch_remote_inventory, daemon=True).start()

    def _on_sync_from_server(self) -> None:
        """
        File → Download Remote Library…

        Downloads every remote-inventory video that is not yet cached locally,
        saving each video to VIDEOS_DIR (the normal local-generation directory)
        and adding it to the local HistoryStore as a warm-copy record.

        The server remains authoritative — this is purely a local cache.
        Local history records with missing video files are also re-downloaded
        from the inference-server API as a secondary action.

        Download sources per record, in priority order:
        1. Inventory server URL (``extra_meta["_inventory_video_url"]``) — used for
           remote records.  Video is saved to VIDEOS_DIR (not remote-cache) so that
           the GTK inline player can play it immediately after download.
        2. Inference-server API (``/v1/videos/generations/{id}/download``) — used for
           local history records whose job file the server still holds.
        """
        import dataclasses as _dc  # noqa: PLC0415
        from history_store import VIDEOS_DIR, THUMBNAILS_DIR  # noqa: PLC0415
        from urllib.parse import urlparse as _up  # noqa: PLC0415

        local_ids = {r.id for r in self._store.all_records()}

        # Remote records not yet in local store.
        remote_pending = [
            r for r in self._remote_records.values()
            if r.id not in local_ids
            and r.media_type in ("video", "animate")
            and (rec_meta := r.extra_meta or {}).get("_inventory_video_url", "")
            # Only queue records that have an inventory URL to download from.
        ]

        # Local history records missing their video file on disk.
        local_missing = [
            r for r in self._store.all_records()
            if r.media_type in ("video", "animate") and not r.video_exists and r.id
        ]

        total = len(remote_pending) + len(local_missing)
        if total == 0:
            self._set_status("All videos are already cached locally — nothing to download.")
            return

        self._set_status(f"Downloading {total} video(s) to local library…")

        def _worker():
            import requests as _req  # noqa: PLC0415
            done = 0
            failed = 0
            localized: list = []  # (old_id, new_GenerationRecord) pairs

            # ── Remote records → VIDEOS_DIR warm copy ────────────────────────
            for rec in remote_pending:
                try:
                    inv_url   = (rec.extra_meta or {}).get("_inventory_video_url", "")
                    thumb_url = (rec.extra_meta or {}).get("_inventory_thumbnail_url", "")
                    if not inv_url:
                        continue

                    v_filename = Path(_up(inv_url).path).name or f"gen_{rec.id[:8]}.mp4"
                    dest = VIDEOS_DIR / v_filename
                    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

                    r = _req.get(inv_url, stream=True, timeout=120)
                    r.raise_for_status()
                    with open(dest, "wb") as fh:
                        for chunk in r.iter_content(65_536):
                            fh.write(chunk)

                    # Thumbnail — save to THUMBNAILS_DIR if possible.
                    new_thumb = rec.thumbnail_path  # fallback: remote-cache path
                    if thumb_url:
                        t_filename = Path(_up(thumb_url).path).name or ""
                        if t_filename:
                            thumb_dest = THUMBNAILS_DIR / t_filename
                            THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
                            if not thumb_dest.exists():
                                try:
                                    tr = _req.get(thumb_url, stream=True, timeout=15)
                                    if tr.status_code == 200:
                                        with open(thumb_dest, "wb") as fh:
                                            for chunk in tr.iter_content(65_536):
                                                fh.write(chunk)
                                except Exception:
                                    pass
                            new_thumb = str(thumb_dest)

                    # Build a localized record with main-storage paths.
                    clean_meta = {k: v for k, v in (rec.extra_meta or {}).items()
                                  if not k.startswith("_inventory_") and k != "_is_remote"}
                    localized.append(_dc.replace(
                        rec,
                        video_path=str(dest),
                        thumbnail_path=new_thumb,
                        extra_meta=clean_meta,
                    ))
                    done += 1
                    GLib.idle_add(
                        self._set_status,
                        f"Downloading… {done + failed}/{total} "
                        f"({done} done, {failed} failed)",
                    )
                except Exception:
                    failed += 1

            # ── Local records with missing video → inference-server API ───────
            for rec in local_missing:
                try:
                    dest = Path(rec.video_path)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    self._client.download(rec.id, dest)
                    done += 1
                    GLib.idle_add(
                        self._set_status,
                        f"Downloading… {done + failed}/{total} "
                        f"({done} done, {failed} failed)",
                    )
                except Exception:
                    failed += 1

            def _finish():
                # Persist all newly-localized records and remove from remote dict.
                for loc_rec in localized:
                    self._store.append(loc_rec)
                    self._remote_records.pop(loc_rec.id, None)
                if failed:
                    self._set_status(
                        f"Download complete: {done} cached locally, {failed} unavailable."
                    )
                else:
                    self._set_status(f"Downloaded {done} video(s) to local library.")
                self._load_history()
                return False

            GLib.idle_add(_finish)

        threading.Thread(target=_worker, daemon=True).start()

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

    def _on_progress(self, message: str, pending: "PendingCard | None") -> bool:
        self._set_status(message)
        if pending is not None:
            pending.update_status(message)
        if self._create_job_active:
            try:
                self._create_view._result_panel.show_progress(message)
            except Exception:
                pass
        return False

    def _on_finished(self, record: GenerationRecord) -> bool:
        gallery = self._gen_gallery or self._gallery_for_type(record.media_type)
        # `replace_pending_with` degrades gracefully when there's no pending
        # card to replace (the Create-job case, where _on_generate skipped
        # add_pending_card): it falls through to inserting the record as a
        # normal card instead, so persistence/gallery display is identical
        # either way.
        gallery.replace_pending_with(record)
        if self._create_job_active:
            try:
                self._create_view._result_panel.show_finished(record)
            except Exception:
                pass
            self._create_job_active = False
        self._gen_gallery = None
        self._last_error_log_path = None  # clear stale error so status bar click no longer opens old log
        # SP-3d-5: ControlPanel's own "Repeat last" availability sync (which
        # used to live here) is gone with the class — Create's seed-mode
        # control (SP-3d-2) reads history fresh at collect() time instead of
        # needing a cached "is repeat-last available" flag refreshed here.
        media_path = record.media_file_path
        self._set_status(f"Done — {media_path}  ({record.duration_s:.0f}s)")
        self._screensaver_uninhibit()
        self._start_next_queued()
        if self._attractor_win is not None:
            GLib.idle_add(self._attractor_win.add_record, record)
        self._update_attractor_btn()
        self._rebuild_playlists_menu()   # refresh By Model counts after new generation
        # Sleep-after-N: count completions and suspend if the threshold is reached
        self._gen_completed_count += 1
        limit = int(_settings.get("sleep_after_n_gens"))
        if limit > 0 and self._gen_completed_count >= limit:
            self._gen_completed_count = 0
            self._set_status(f"Completed {limit} generation(s) — suspending…")
            GLib.timeout_add(1500, lambda: (
                subprocess.Popen(["systemctl", "suspend"])
                if __import__("platform").system() == "Linux" else None
            ) and False)
        return False

    def _on_error(self, message: str) -> bool:
        from log_viewer import detect_log_path, shorten_error
        gallery = self._gen_gallery or self._active_gallery()
        gallery.remove_pending()
        self._gen_gallery = None
        self._screensaver_uninhibit()
        self._last_error_log_path = detect_log_path(message)
        short = shorten_error(message)
        suffix = " — click for log" if self._last_error_log_path else ""
        self._set_status(f"Error: {short}{suffix}")
        if self._create_job_active:
            try:
                self._create_view._result_panel.show_error(message)
            except Exception:
                pass
            self._create_job_active = False
        self._start_next_queued()
        return False


    def _maximize_on_first_map(self, _win) -> None:
        """Maximize the window the first time it's mapped (reliable on Wayland).

        Guarded so later maps (e.g. un-minimize) don't re-force maximize and
        override a user who deliberately restored the window down.
        """
        if self._did_initial_maximize:
            return
        self._did_initial_maximize = True
        self.maximize()

    def do_close_request(self) -> bool:
        self._alive = False   # stop any pending GLib.idle_add callbacks from touching widgets
        if self._flash_restore_id:
            GLib.source_remove(self._flash_restore_id)
        self._status_service.stop()
        # SP-3d-6: the three legacy poller threads (_health_loop/
        # _artgen_health_loop/_prompt_gen_health_loop) that used to need their
        # own stop-event teardown here are retired; `_status_unsubscribe()`
        # tears down `_render_status_snapshot`'s subscription to the service
        # stopped just above (mirrors `_servers_control.close()` below, which
        # does the same for its own subscription).
        self._status_unsubscribe()
        # ServersControl's own `unrealize`-triggered cleanup (see
        # servers_control.py's __init__) only fires if it's ever mounted
        # itself; since only its `servers_button`/`log_widget` sub-widgets
        # are mounted (not `self`), that signal may never fire — close()
        # explicitly here so its status_service subscription is always torn
        # down alongside the service it subscribes to.
        self._servers_control.close()
        self._hw_statusbar.stop()
        if self._log_tail_stop:
            self._log_tail_stop.set()
        if self._worker_gen:
            self._worker_gen.cancel()
        if self._server_proc and self._server_proc.poll() is None:
            self._server_proc.terminate()
        return False  # allow close
