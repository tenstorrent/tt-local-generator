# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
CreateView — the unified generation surface's shell (Create-surface plan,
Task 3: docs/superpowers/specs/2026-07-13-create-surface-design.md).

Replaces the four medium tabs (Video / Animate / Image / Generative Art) with
ONE surface where the medium is a *chip*, not a top-level division. This
module builds the shell — three doors in (idea / model / inspiration), the
medium-chip row, a scoped model dropdown, a role-zoned per-medium param-panel
host, and the Create CTA, all clamped to a comfortable centered column width
(`gtk_layout.wrap_centered`). Per-type param panels (real image/video/animate/
artgen controls) arrived across Tasks 4-6; Task 4 ported the IMAGE medium to a
real `ImageParamPanel`; Task 5 ported VIDEO and ANIMATE to
`VideoParamPanel`/`AnimateParamPanel` and added `RoleZonePanel`, the shared
brief/direction/controls wrapper every real panel is now mounted through; this
task (6) ports every artgen generator medium (verse/ansi/landscape/…) to
`ArtgenParamPanel` — one class, parameterized by generator name, that
introspects the generator's own `add_args` (all panel classes live in
`create_param_panels.py`) — AND retires the persistent flat "live-model strip"
in favor of a `_model_dropdown` scoped to the active medium's own models, AND
mounts every real panel wrapped in `RoleZonePanel`. Every medium the Create
surface can offer now has a real panel — the plain Task 3 stub label is now
dead code kept only as a fallback for a hypothetical future medium kind.

**Migration-safe by construction**: this view is built ALONGSIDE the existing
medium-tab generation UI (main_window.py's ControlPanel + `_gallery_stack`
video/animate/image/artgen children), mounted as the `_gallery_stack`
"create" child. As of the Task 8 switchover the loop nav's ✨ Create verb now
routes HERE — CreateView IS the Create surface, and its `on_create` seam is
wired to `MainWindow._on_create_generate` (real generation). The legacy
ControlPanel/medium-tab UI is intentionally left in place as a still-mounted
fallback (its deletion is a later task, deferred until a real-generation
smoke test on hardware). Generation itself (`GenerationWorker`/`api_client`)
is completely untouched by this file — CreateView only translates a chosen
medium + collected params into the SAME `_on_generate`/`tt-ctl artgen` call
the old UI already makes (see `main_window.py._on_create_generate`).

Every external dependency is an injected constructor seam so this widget is
fully unit-testable with fakes (tests/test_create_view.py) — no real
generation, network, or board access happens merely by constructing it:

    mediums_fn()   -> list[create_mediums.Medium]   (default: default_mediums)
    health_fn()    -> dict[str, bool]                (default: server_manager.status_all)
    on_create(medium, params: dict)                  (CTA click)
    on_inspiration()                                 (entering the inspiration door)

**Task 7 — the three doors wired.** Idea/model/inspiration go from three inert
toggles to real entry points:

  - **Idea** (default): a prompt entry ("What do you want to make?") sits
    above the medium chips, visible only while `_entry_mode == "idea"`. Its
    text is merged into the CTA payload as `params["prompt"]` — but only when
    non-empty, so every Tasks 3-6 CTA test (which never touches the entry)
    still gets back its exact pre-Task-7 params dict.
  - **Model**: Task 6 retired the old persistent, non-wrapping live-model
    strip (it was the thing overflowing the window) and left an honest
    placeholder in its place. Task 7 replaces that placeholder with the real
    grouped, wrapping model grid (`_build_model_door`): every
    `server_manager.SERVERS` key, classified into Image/Video/Animate/Text
    sections (empty sections omitted) and rendered as a `Gtk.FlowBox` of
    status-dotted cards per section. Clicking a card (`_activate_model_card`)
    selects that model's medium and returns to the Idea door, pre-scoping the
    scoped `_model_dropdown` to the clicked model when practical. Model
    selection WITHIN a chosen medium still lives in the scoped
    `_model_dropdown` above the panel host, same as Task 6.
  - **Inspiration**: unchanged from Task 3 — still just calls the injected
    `on_inspiration()` seam. `main_window.py` wires this to
    `self._on_loop_nav_remix` (the existing unseeded `show_muse()` bridge) —
    Create does not reimplement the Muse hand-off.

GTK threading rule: `health_fn` may do real network I/O (server_manager hits
each service's health URL with a 2s timeout, sequentially, for every known
server — worst case several seconds). It is therefore ALWAYS run off the GTK
main thread via `threading.Thread`, with the result posted back through
`GLib.idle_add` (see `pipeline_studio.py` for the same pattern). Tests
monkeypatch this module's `threading.Thread` / `GLib.idle_add` names to make
the refresh synchronous (mirrors `_ImmediateThread` in
tests/test_pipeline_studio.py) — no real thread or real health check is ever
exercised by the injected-fake test path.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import GLib, Gtk, WebKit  # noqa: E402

import artgen_render  # noqa: E402
import gtk_layout  # noqa: E402
import server_manager  # noqa: E402
from create_mediums import Medium, default_mediums, sort_mediums_visual_first  # noqa: E402
from create_mediums import _ARTGEN_KIND  # noqa: E402
from create_param_panels import (  # noqa: E402
    _ANIMATE_MODE_REPLACEMENT,
    _ANIMATE_MODEL_ID,
    _IMAGE_MODEL_IDS,
    _VIDEO_MODEL_IDS,
    AnimateParamPanel,
    ArtgenParamPanel,
    ImageParamPanel,
    RoleZonePanel,
    VideoParamPanel,
    build_mode_toggle_row,
    build_path_picker_row,
)
from model_status import Status  # noqa: E402
from possibilities import PossibilitiesWall  # noqa: E402

# Video-only alias: server_manager's key for the Wan2.2 text-to-video service
# is "wan2.2", but VideoParamPanel's own internal short key (and therefore
# `_VIDEO_MODEL_IDS`'s key) is "wan2" — a pre-existing naming mismatch between
# the two modules (VideoParamPanel predates the scoped dropdown). Image's
# server_manager keys ("flux"/"sdxl"/"z-image-turbo"/"motif") already match
# `_IMAGE_MODEL_IDS`'s keys exactly, and Animate has exactly one native model
# id (`_ANIMATE_MODEL_ID`, no dropdown/dict at all) — so this one-entry alias
# is the only translation table the scoped dropdown needs.
_VIDEO_SERVER_KEY_ALIAS: "dict[str, str]" = {"wan2.2": "wan2"}

# SP-3 Task 3 ("running chat model identity"): sentinel key prefix for a
# running chat-LLM model that `ModelStatusService.running_artgen_model()`
# reports with `matched_key=None` — i.e. something IS running (started
# outside this app, or a weights repo with no `ServerDef` yet) but it isn't
# any registered `server_manager.SERVERS` entry. Prefixing with a string that
# can never collide with a real SERVERS key (those are short slugs like
# "flux"/"artgen-qwen3-8b") lets every place that does a `SERVERS.get(key)`
# lookup treat it as "not found" (`None`) safely, while `.startswith(...)`
# checks (`_model_dot_glyph`, the dropdown/door builders) recognize and
# special-case it instead of falling through to a raw, unlabeled key.
_DETECTED_KEY_PREFIX = "__detected__:"


def _is_detected_key(key: Optional[str]) -> bool:
    """True for a synthetic "detected model" sentinel key (see
    `_DETECTED_KEY_PREFIX`), False for a real `server_manager.SERVERS` key or
    `None`."""
    return bool(key) and key.startswith(_DETECTED_KEY_PREFIX)


def _detected_key_model_id(key: str) -> str:
    """Strip `_DETECTED_KEY_PREFIX` off a sentinel key, recovering the raw
    `model_id` string `ArtgenModelInfo` reported. Caller must have already
    confirmed `_is_detected_key(key)`."""
    return key[len(_DETECTED_KEY_PREFIX):]


# SP-2 Task 3: native-medium id -> the capability string `ModelStatusService`
# expects (matches `server_manager.ServerDef.capabilities` verbatim). This is
# keyed by `medium.id`, NOT `medium.kind` — the native "animate" medium's
# `kind` is "gif" (it reports its output file-kind for gallery/playback
# purposes, see `create_mediums.Medium`'s docstring), not "animate", so a
# kind-keyed lookup would silently return `None` for the one medium whose
# model most often needs a moment to finish starting. `medium.id` IS the
# capability string for all three native mediums (mirrors the same id-based
# capability resolution `_scoped_model_keys` already uses one line below via
# `medium.id`), so keying off it here keeps this map correct for Animate too.
# Absent from the map -> no capability -> auto-select is skipped (covers
# every artgen medium, whose id is a generator name, not a capability).
_MODEL_STATUS_CAPABILITY: "dict[str, str]" = {
    "image": "image",
    "video": "video",
    "animate": "animate",
}

# SP-3c-3: native-medium id -> the `source` string `prompt_client.
# generate_prompt()` (app/generate_prompt.py's three-tier generator) expects.
# Same id-keyed shape as `_MODEL_STATUS_CAPABILITY` above (and for the same
# reason — "animate"'s `medium.kind` is "gif", not "animate"). Every artgen
# medium's id is a generator name (verse/ansi/landscape/…), not one of
# generate_prompt.py's known types, so it is deliberately absent here —
# `_inspire_prompt_type` falls back to "video" (generate_prompt.py's own CLI
# default) for those, and for the "no active medium yet" edge case.
_INSPIRE_PROMPT_TYPE: "dict[str, str]" = {
    "image": "image",
    "video": "video",
    "animate": "animate",
}
_INSPIRE_PROMPT_TYPE_DEFAULT = "video"


def _canonical_model_id_for(medium: Medium, server_key: str) -> Optional[str]:
    """Translate a `server_manager.SERVERS` key into the exact canonical
    model-id STRING the corresponding native `CreateParamPanel`'s own model
    dropdown would have produced for the equivalent choice.

    This is the piece that keeps the scoped dropdown's `model` value
    byte-for-byte compatible with the pre-Task-6 panel-owned model field (the
    migration invariant `_collect_params` depends on) — see the values in
    `create_param_panels._IMAGE_MODEL_IDS` / `_VIDEO_MODEL_IDS` /
    `_ANIMATE_MODEL_ID`, which this function reads rather than re-deriving.

    Returns `None` for a server key with no equivalent in the panel's own
    choices (a future server key the panel hasn't been taught about yet) or
    for a non-native medium (artgen has no "model" field at all) — callers
    must treat `None` as "don't offer this key for this medium", never guess
    a fallback. As of SP-3c-1, "skyreels" DOES resolve here (VideoParamPanel
    gained a `SeedImageWell`, so the I2V model is no longer a guaranteed-fail
    trap — see `create_param_panels._VIDEO_MODEL_IDS`'s comment).
    """
    if medium.id == "image":
        return _IMAGE_MODEL_IDS.get(server_key)
    if medium.id == "video":
        panel_key = _VIDEO_SERVER_KEY_ALIAS.get(server_key, server_key)
        return _VIDEO_MODEL_IDS.get(panel_key)
    if medium.id == "animate":
        return _ANIMATE_MODEL_ID
    return None


# Model door (Task 7): a `server_manager.SERVERS` key is classified into a
# group by its OWN `ServerDef.capabilities`, NOT by resolving "the Medium it
# implies" — that indirection (via `_server_key_to_medium_id`) is fatally
# wrong for the six chat-LLM backends, whose capability is the generic
# ("artgen",): `_server_key_to_medium_id` maps "artgen" to the *first
# artgen-sourced medium* in `mediums_fn()`, which in the real app is
# `animatediff` (kind "gif"). That would file every "Qwen3-8B"/"Llama-…"
# card under **Animate** and make clicking one switch the panel to
# AnimateDiff. Capability-based classification is unambiguous instead:
#
#   image   -> Image   (flux/sdxl/z-image-turbo/motif)
#   video   -> Video   (wan2.2/mochi/skyreels/animate — Wan2.2-Animate-14B
#                       is a video model too, filed alongside its siblings
#                       rather than getting its own single-server section)
#   artgen  -> Text    (the chat-LLM backends — Qwen/Llama/DeepSeek/…)
#   prompt  -> Text    (prompt-server, the tiny prompt-gen Qwen)
#
# First matching capability wins (every real ServerDef has exactly one).
# Anything unrecognised falls back to "Text" (every such key today is a
# chat/LLM/prompt service).
_CAPABILITY_TO_MODEL_DOOR_GROUP: "dict[str, str]" = {
    "image": "Image",
    "video": "Video",
    "animate": "Video",   # Wan2.2-Animate is a Video model now
    "artgen": "Text",
    "prompt": "Text",
}

# Fixed display order for the model door's sections — independent of dict
# iteration order, and stable regardless of which groups end up non-empty for
# the current SERVERS table.
_MODEL_DOOR_GROUP_ORDER: "tuple[str, ...]" = ("Image", "Video", "Text")


# Two-pane responsive layout (Task 2, "in-place Create results":
# .superpowers/sdd/task-2-brief.md). The form column and `CreateResultPanel`
# sit side by side once there's enough width for both — `wrap_centered`'s
# shared default (`gtk_layout.CONTENT_MAX_WIDTH`, 960px) was sized for the
# form ALONE (pre-Task-2) and is too tight for two comfortable panes, so the
# whole surface's ceiling is raised here. This is safe precisely because the
# clamp is a CEILING, not a fixed width (see `gtk_layout.MaxWidthBin`'s
# docstring): a window narrower than this never gets forced wide — the
# `Gtk.FlowBox` two-pane container (`_build_panes`) reflows to one column
# per line long before the window could ever approach this cap.
_TWO_PANE_MAX_WIDTH = 1440


# Native medium id -> its real CreateParamPanel class. `_swap_panel` mounts
# a fresh instance of the mapped class for any native medium listed here;
# every other medium (artgen, or a future native medium not yet ported)
# falls through to the Task 3 stub. Image: Task 4. Video/Animate: Task 5.
_NATIVE_PANEL_CLASSES: "dict[str, type]" = {
    "image": ImageParamPanel,
    "video": VideoParamPanel,
    "animate": AnimateParamPanel,
}


# ─────────────────────────────────────────────────────────────────────────────
# CSS — the MAIN APP's tt-vscode-toolkit palette (teal #4FD1C5 on deep
# blue-gray #0F2A35), NOT the forest-teal palette used by pipeline_studio.py /
# the gallery views. This is an editor surface living alongside the main
# window's ControlPanel/galleries, so it must look like the rest of the main
# app, not like Pipeline Studio.
#
# CreateView is its own module with its own Gtk.CssProvider (same pattern as
# pipeline_studio.py's `_apply_css`), so `@tt_*` named colors defined in
# main_window.py's provider are not reliably visible here — the hex literals
# below are copied verbatim from main_window.py's `_CSS` `@define-color`
# block (tt_bg_darkest/tt_bg_dark/tt_bg_panel/tt_border/tt_accent/
# tt_accent_light/tt_text/tt_text_muted/tt_success/tt_error) so a CreateView
# chip and a main-window button never disagree visually.
# ─────────────────────────────────────────────────────────────────────────────
_CSS = b"""
.create-view {
    background-color: #0F2A35;
    color: #E8F0F2;
    padding: 8px;
}

/* -- Doors row (idea / model / inspiration) -------------------------------- */
.create-doors-row {
    padding: 4px 0 8px 0;
}
.create-door-btn {
    background-color: #1A3C47;
    color: #607D8B;
    border: 1px solid #2D5566;
    border-radius: 0;
    padding: 6px 16px;
    font-size: 12.5px;
    font-weight: bold;
    min-height: 0;
}
.create-door-btn:hover {
    background-color: #2D5566;
    color: #E8F0F2;
}
.create-door-btn-left  { border-radius: 6px 0 0 6px; }
.create-door-btn-mid   { border-radius: 0; border-left-width: 0; }
.create-door-btn-right { border-radius: 0 6px 6px 0; border-left-width: 0; }
.create-door-btn:checked {
    background-color: #4FD1C5;
    color: #0F2A35;
    border-color: #4FD1C5;
}
.create-door-btn:checked:hover {
    background-color: #81E6D9;
}

/* -- Medium chip row -------------------------------------------------------- */
.create-chip-row {
    padding: 2px 0 8px 0;
}
.create-chip-btn {
    background-color: #1A3C47;
    color: #E8F0F2;
    border: 1px solid #2D5566;
    border-radius: 16px;
    padding: 4px 12px;
    font-size: 12px;
}
.create-chip-btn:hover {
    border-color: #4FD1C5;
}
.create-chip-btn:checked {
    background-color: #4FD1C5;
    color: #0F2A35;
    border-color: #4FD1C5;
    font-weight: bold;
}

/* -- Per-medium param-panel host (stub this task) --------------------------- */
.create-panel-host {
    background-color: #0A1F28;
    border: 1px solid #2D5566;
    border-radius: 8px;
    padding: 14px;
    margin-bottom: 8px;
}
.create-panel-stub-label {
    color: #607D8B;
    font-size: 12.5px;
}

/* -- Per-medium real param panels (ImageParamPanel, Task 4; more to follow) - */
.image-param-panel {
    padding: 2px 0;
}
.image-param-row {
    padding: 3px 0;
}
.image-param-label {
    color: #E8F0F2;
    font-size: 12px;
}
.image-param-input {
    background-color: #1A3C47;
    color: #E8F0F2;
    border: 1px solid #2D5566;
    border-radius: 4px;
}
.image-param-input:focus-within {
    border-color: #4FD1C5;
}
.image-param-hint {
    color: #607D8B;
    font-size: 11px;
}

/* -- SeedImageWell (SP-3c-1) -- shared by ImageParamPanel + VideoParamPanel;
   one namespace since it's the exact same widget class in both panels. ---- */
.seed-image-well {
    background-color: #1A3C47;
    color: #607D8B;
    border: 1px dashed #2D5566;
    border-radius: 4px;
}
.seed-image-well:hover {
    border-color: #4FD1C5;
}
.seed-image-well.has-seed {
    border: 1px solid #4FD1C5;
}

/* -- VideoParamPanel (Task 5) -- same look as ImageParamPanel, own namespace
   so a later change to one never accidentally reskins the other. -------- */
.video-param-panel {
    padding: 2px 0;
}
.video-param-row {
    padding: 3px 0;
}
.video-param-label {
    color: #E8F0F2;
    font-size: 12px;
}
.video-param-input {
    background-color: #1A3C47;
    color: #E8F0F2;
    border: 1px solid #2D5566;
    border-radius: 4px;
}
.video-param-input:focus-within {
    border-color: #4FD1C5;
}
.video-param-hint {
    color: #607D8B;
    font-size: 11px;
}

/* -- SeedModeControl (SP-3d-2) -- random/repeat-last/keep dropdown, shared by
   Image/Video/Animate's seed rows. Small left margin separates it from the
   seed spin it sits beside within the same row. --------------------------- */
.seed-mode-control {
    margin-left: 6px;
}

/* -- AnimateParamPanel (Task 5) ---------------------------------------------- */
.animate-param-panel {
    padding: 2px 0;
}
.animate-param-row {
    padding: 3px 0;
}
.animate-param-label {
    color: #E8F0F2;
    font-size: 12px;
}
.animate-param-input {
    background-color: #1A3C47;
    color: #E8F0F2;
    border: 1px solid #2D5566;
    border-radius: 4px;
}
.animate-param-input:focus-within {
    border-color: #4FD1C5;
}
/* Mode toggle buttons (Animation/Replacement) get the active-accent look
   shared with the doors row's :checked state. */
.animate-param-input:checked {
    background-color: #4FD1C5;
    color: #0F2A35;
    border-color: #4FD1C5;
}

/* -- ArtgenParamPanel (Task 6) -- one class for every artgen generator, so
   one namespace here covers verse/ansi/landscape/etc uniformly. ---------- */
.artgen-param-panel {
    padding: 2px 0;
}
.artgen-param-row {
    padding: 3px 0;
}
.artgen-param-label {
    color: #E8F0F2;
    font-size: 12px;
}
.artgen-param-input {
    background-color: #1A3C47;
    color: #E8F0F2;
    border: 1px solid #2D5566;
    border-radius: 4px;
}
.artgen-param-input:focus-within {
    border-color: #4FD1C5;
}
.artgen-param-empty-label {
    color: #607D8B;
    font-size: 12.5px;
}

/* -- Model door cards (Task 7) -- reuses the dot/chip/label vocabulary the
   pre-Task-6 live-model strip introduced (that strip's own container class,
   .create-model-strip, is gone -- these per-card classes are not: they are
   now the model door's clickable Gtk.Button cards, one per server key). --- */
.create-model-chip {
    border-radius: 12px;
    padding: 3px 10px;
    border: 1px solid #2D5566;
}
.create-model-chip-on {
    border-color: #27AE60;
}
.create-model-chip-off {
    border-color: #2D5566;
}
.create-model-dot-on  { color: #27AE60; font-size: 9px; }
.create-model-dot-off { color: #607D8B; font-size: 9px; }
.create-model-label   { color: #E8F0F2; font-size: 11px; }
/* A visible hover affordance says "tap me". */
.create-model-chip:hover {
    border-color: #4FD1C5;
}

/* -- Model picker benefit taglines (Task 4) -- dimmed second line shown on
   the scoped dropdown's popup rows and the Model door's cards. ASCII only:
   this whole block is inside the _CSS bytes literal, no non-ASCII glyphs
   allowed here (dots/icons live in Python str labels only). */
.model-row-benefit {
    font-size: 0.85em;
    opacity: 0.7;
}

/* -- Idea door's prompt entry (Task 7) ---------------------------------------- */
.create-idea-row {
    padding: 0 0 6px 0;
}
.create-idea-entry {
    background-color: #1A3C47;
    color: #E8F0F2;
    border: 1px solid #2D5566;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
}
.create-idea-entry:focus-within {
    border-color: #4FD1C5;
}

/* -- Inspire-me button (SP-3c-3) -- deliberately NOT .create-cta-btn's look:
   that solid-teal fill is reserved for the "Create" CTA. This is a small,
   outlined companion button next to the brief entry. ---------------------- */
.create-inspire-btn {
    background-color: #1A3C47;
    color: #4FD1C5;
    border: 1px solid #2D5566;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
}
.create-inspire-btn:hover {
    border-color: #4FD1C5;
    color: #81E6D9;
}
.create-inspire-btn:disabled {
    color: #607D8B;
}

/* -- Create CTA --------------------------------------------------------------- */
.create-cta-row {
    padding-top: 4px;
}
/* Pinned CTA bar: a fixed footer below the scrolling form so Create is
   always visible. A top border + subtle fill separate it from the scroll. */
.create-cta-bar {
    border-top: 1px solid #1d4655;
    background-color: #0c222c;
    padding: 8px 12px;
}
.create-cta-btn {
    background-color: #4FD1C5;
    color: #0F2A35;
    font-weight: bold;
    border-radius: 8px;
    padding: 8px 20px;
    font-size: 13px;
}
.create-cta-btn:hover {
    background-color: #81E6D9;
}

/* -- Theme Set button (SP-3d-1) -- migrated from ControlPanel's own "Theme
   Set" button; outlined companion next to the CTA, same family as the
   Inspire-me button above but its own class so either can restyle
   independently. ------------------------------------------------------- */
.create-theme-set-btn {
    background-color: #1A3C47;
    color: #4FD1C5;
    border: 1px solid #2D5566;
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 13px;
}
.create-theme-set-btn:hover {
    border-color: #4FD1C5;
    color: #81E6D9;
}
.create-theme-set-btn:disabled {
    color: #607D8B;
}

/* -- RoleZonePanel zones (Task 6) -- one class per zone so a future change to
   the brief/direction/controls look never has to touch create_param_panels.py
   (which stays GTK-styling-agnostic beyond its own per-field row classes). -- */
.role-zone-panel {
    padding: 2px 0;
}
.role-zone-brief,
.role-zone-direction {
    border: 1px solid #2D5566;
    border-radius: 6px;
    padding: 6px;
    margin-bottom: 4px;
}
.role-zone-brief-body,
.role-zone-direction-body {
    padding: 2px 0;
}
.role-zone-controls {
    margin-top: 2px;
}
.role-zone-controls-grid {
    padding: 4px 0;
}

/* -- ModifierPills (RoleZonePanel's Direction-zone add-chips / pills) ------- */
.modifier-pills {
    padding: 2px 0;
}
.modifier-pills-category-label {
    color: #607D8B;
    font-size: 11px;
    font-weight: bold;
}
.modifier-pills-add-row,
.modifier-pills-applied {
    padding: 2px 0;
}
.create-addchip {
    background-color: #1A3C47;
    color: #E8F0F2;
    border: 1px solid #2D5566;
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 11px;
}
.create-addchip:hover {
    border-color: #4FD1C5;
}
/* "N more" / "less" disclosure inside a modifier-pill category -- a flat,
   muted text link, deliberately NOT the boxed .create-addchip pill so it
   reads as a control, not another selectable chip. */
.modifier-pills-more {
    background: none;
    border: none;
    box-shadow: none;
    color: #7FB3AD;
    font-size: 10.5px;
    padding: 0 6px;
    min-height: 0;
    min-width: 0;
}
.modifier-pills-more:hover {
    color: #4FD1C5;
    text-decoration: underline;
}
.create-pill {
    background-color: #4FD1C5;
    color: #0F2A35;
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: bold;
}
.create-pill:hover {
    background-color: #81E6D9;
}

/* -- Scoped model dropdown (Task 6) -- replaces the retired flat model strip;
   lists ONLY the active medium's own models, above the panel host. --------- */
.create-model-dropdown-row {
    padding: 0 0 6px 0;
}
.create-model-dropdown-label {
    color: #E8F0F2;
    font-size: 12px;
}
.create-model-dropdown {
    background-color: #1A3C47;
    color: #E8F0F2;
    border: 1px solid #2D5566;
    border-radius: 4px;
}

/* -- Model door: grouped, wrapping model grid (Task 7) -- replaces the Task 6
   "not built yet" placeholder. One section per non-empty group (Image/Video/
   Animate/Text); each section's cards live in a wrapping Gtk.FlowBox (see
   .create-model-door-flow) so the whole ~15-and-growing model collection is
   browsable without ever overflowing the window (width-clamp requirement,
   same discipline as .create-chip-row). ------------------------------------ */
.create-model-door-row {
    padding: 0 0 6px 0;
}
.create-model-door {
    padding: 2px 0;
}
.create-model-door-section {
    padding: 2px 0;
}
.create-model-door-header {
    color: #4FD1C5;
    font-size: 11px;
    font-weight: bold;
    letter-spacing: 0.5px;
}
.create-model-door-flow {
    padding: 2px 0 4px 0;
}

/* -- CreateResultPanel (Task 1, in-place Create results) -- a standalone
   widget this task (not yet mounted in CreateView -- see the class
   docstring); its own namespace so a future wiring task never has to touch
   this CSS. Palette matches the rest of CreateView (tt-vscode-toolkit
   teal/deep-blue-gray), not the forest-teal used by pipeline_studio.py. ---- */
.create-result-panel {
    padding: 4px 0;
}
.create-result-current {
    background-color: #0A1F28;
    border: 1px solid #2D5566;
    border-radius: 8px;
    padding: 12px;
    min-height: 120px;
    min-width: 320px;
}
.create-result-empty-label {
    color: #607D8B;
    font-size: 12.5px;
}
.create-result-spinner {
    color: #4FD1C5;
}
.create-result-status {
    color: #E8F0F2;
    font-size: 13px;
    font-weight: bold;
}
.create-result-elapsed {
    color: #4FD1C5;
    font-size: 11.5px;
}
.create-result-prompt {
    color: #A9C1C6;
    font-size: 12px;
}
.create-result-error-label {
    color: #FF6B6B;
    font-size: 13px;
}
.create-result-placeholder {
    color: #607D8B;
    font-size: 12.5px;
}
.create-result-picture {
    border-radius: 6px;
}
.create-result-text-scroll {
    border: 1px solid #2D5566;
    border-radius: 4px;
}
.create-result-text-view {
    background-color: #1A3C47;
    color: #E8F0F2;
    font-family: monospace;
    font-size: 12px;
    padding: 6px;
}
.create-result-reading {
    border: 1px solid #2D5566;
    border-radius: 4px;
    min-height: 220px;
}
/* -- Pending-queue display (SP-3c-4, task-4-brief.md) -- the this-session
   Create job queue, shown between the current result and the recents strip:
   one row per `_QueueItem` still waiting to run, each with a cancel (X)
   button. Deliberately understated (muted colors, no border) next to the
   current-result box above it -- these are QUEUED, not yet running. ---- */
.create-result-queue {
    padding: 2px 0 4px 0;
}
.create-result-queue-row {
    padding: 2px 4px;
}
.create-result-queue-prompt {
    color: #A9C1C6;
    font-size: 12px;
}
.create-result-queue-cancel-btn {
    min-width: 20px;
    min-height: 20px;
    padding: 0;
    color: #FF9E8A;
}
.create-result-recents {
    padding: 6px 0 0 0;
}
.create-result-recent-btn {
    background-color: #1A3C47;
    border: 1px solid #2D5566;
    border-radius: 4px;
    padding: 2px;
    min-width: 64px;
    min-height: 36px;
}
.create-result-recent-btn:hover {
    border-color: #4FD1C5;
}

/* -- Two-pane responsive layout (Task 2, in-place Create results) -- the
   form column (.create-form-pane) and CreateResultPanel side by side in a
   wrapping Gtk.FlowBox (.create-panes). No fixed widths here on purpose:
   each pane's own natural/hexpand settings (see CreateView._build_panes)
   decide sizing; this class only adds a little breathing room between the
   two panes and above/below the row. --------------------------------- */
.create-panes {
    padding: 4px 0;
}
.create-form-pane {
    padding: 0 8px 0 0;
}

/* -- Animate-needs reveal section (Task 6) -- Motion video / Character
   image / Mode, shown only when the scoped model dropdown's selection is
   the Animate model (`_animate_extras_visible_for`). Rows inside reuse the
   existing `.animate-param-row`/`.animate-param-label`/`.animate-param-input`
   classes verbatim (shared via create_param_panels.build_path_picker_row/
   build_mode_toggle_row), so only the outer wrapper needs its own rule
   here -- a little top margin to separate it from the model dropdown row
   above it. --------------------------------------------------------- */
.create-animate-extras {
    margin-top: 6px;
    padding-top: 6px;
    border-top: 1px solid #2D5566;
}
"""

_css_applied = False


def _apply_css() -> None:
    """Register CreateView's CSS provider for the default display.

    Guarded by a module-level flag so repeated CreateView construction (e.g.
    across tests) doesn't stack up duplicate providers — same pattern as
    `pipeline_studio._apply_css`.
    """
    global _css_applied
    if _css_applied:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gtk.Widget.get_display(Gtk.Window()),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _css_applied = True


def _animate_extras_visible_for(model_key: "Optional[str]") -> bool:
    """The Animate-needs section (motion video / character image / mode)
    shows only when the scoped `_model_dropdown`'s current selection is the
    Animate model — the server key `"animate"` (see `_scoped_model_keys`'s
    docstring: that key is appended to the "video" medium's scoped list by
    hand, since Wan2.2-Animate is a real server but not returned by
    `server_manager.servers_for_capability("video")`).

    A pure, one-line predicate on purpose (SDD task-6-brief.md Step 3) — it
    is the SINGLE place both the widget-visibility toggle
    (`CreateView._update_animate_extras_visibility`) and the `_collect_params`
    merge guard consult, so the two can never disagree about when the
    Animate-needs section is "on".
    """
    return model_key == "animate"


class _AnimateExtras(Gtk.Box):
    """The Video form's reveal-on-demand "Animate needs" section: Motion
    video / Character image path pickers + an Animation/Replacement mode
    toggle, built from the exact same `create_param_panels.
    build_path_picker_row`/`build_mode_toggle_row` helpers `AnimateParamPanel`
    itself is built from (SDD task-6-brief.md) — no duplicated FileDialog
    wiring, no drift between the two.

    **CreateView-owned chrome, not a `CreateParamPanel`.** Per the task
    brief this widget is deliberately kept OFF the wrapped `RoleZonePanel` —
    it is mounted directly under the model row (like `_prompt_entry`) and
    its `collect()` dict is folded into `_collect_params()` by hand, guarded
    by `_animate_extras_visible_for(self._selected_model_key())`. This keeps
    every OTHER model's `collect()` output byte-for-byte unchanged: the
    fold only ever happens when the Animate model is the scoped dropdown's
    current selection (see `CreateView._collect_params`'s Task-6 docstring
    addendum and the collect-equality regression test in
    `tests/test_create_view_animate_reveal.py`).

    Starts hidden (`set_visible(False)`) — `CreateView` toggles it via
    `_update_animate_extras_visibility`, called once per
    `_populate_model_dropdown` (medium swap AND same-medium health refresh)
    and on every scoped-dropdown selection change
    (`_on_scoped_model_dropdown_changed`).
    """

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class("create-animate-extras")
        self.set_visible(False)

        self._animate_mode: str = "animation"

        video_row, self._ref_video_entry, _on_browse_video, _on_picked_video = (
            build_path_picker_row(
                "Motion video", "no motion video selected", "Select motion video",
            )
        )
        self.append(video_row)

        image_row, self._ref_image_entry, _on_browse_image, _on_picked_image = (
            build_path_picker_row(
                "Character image", "no character image selected", "Select character image",
            )
        )
        self.append(image_row)

        mode_row, self._mode_anim_btn, self._mode_repl_btn = build_mode_toggle_row(
            self._set_mode
        )
        self.append(mode_row)

    def _set_mode(self, mode: str) -> None:
        self._animate_mode = mode

    # ── Test seams (SDD task-6-brief.md) — set values without a real dialog ──

    def set_paths(self, video_path: str, character_path: str) -> None:
        """Set both path entries directly, bypassing the async FileDialog —
        the seam `tests/test_create_view_animate_reveal.py` uses."""
        self._ref_video_entry.set_text(video_path)
        self._ref_image_entry.set_text(character_path)

    def set_mode(self, mode: str) -> None:
        """Set the animation/replacement mode directly, bypassing a real
        button click. Any value other than the replacement mode string
        selects "animation" (the toggle group's default), matching how a
        `Gtk.ToggleButton` group actually behaves (exactly one of the two
        is ever active)."""
        if mode == _ANIMATE_MODE_REPLACEMENT:
            self._mode_repl_btn.set_active(True)
        else:
            self._mode_anim_btn.set_active(True)

    def collect(self) -> "dict":
        """The exact three keys `CreateView._collect_params` folds into a
        Video job's params when the Animate model is selected."""
        return {
            "reference_video_path": self._ref_video_entry.get_text(),
            "reference_image_path": self._ref_image_entry.get_text(),
            "animate_mode": self._animate_mode,
        }


class CreateView(Gtk.Box):
    """The Create surface shell: doors, medium chips, a scoped model dropdown,
    a role-zoned param-panel host, and the Create CTA — all clamped to a
    comfortable centered column width.

    Injectable seams (see module docstring) make this fully testable with
    fakes. Nothing here imports `GenerationWorker`, `api_client`, or
    `ControlPanel` — wiring the CTA to real generation is a later task.

    **Task 6 retires the persistent flat "live-model strip"** (a `Gtk.Box` of
    every known server, unconditionally visible and NOT wrapping — the thing
    overflowing the window per user report). In its place:

      - a `_model_dropdown` scoped to the ACTIVE medium's own models sits
        directly above `_panel_host` (see `_populate_model_dropdown`), and
      - the "model" entry door showed an honest placeholder in Task 6.

    **Task 7 replaces that placeholder** with the real grouped, wrapping
    model door (`_build_model_door`/`_model_door_groups`/
    `_activate_model_card` — see the module docstring's "Model" bullet).
    `_server_key_to_medium_id` (kept unused-but-intact since Task 6) is now
    the routing core `_activate_model_card` reuses to turn a clicked card
    back into an active medium.

    `self._model_strip` / `self._model_cards` / `_apply_model_strip` /
    `_refresh_model_strip_async` no longer exist — those were the Task 6
    retired live-model strip's own attributes, distinct from this task's
    `_model_door_row`/`_build_model_door` grid.
    """

    def __init__(
        self,
        *,
        mediums_fn: Optional[Callable[[], "list[Medium]"]] = None,
        health_fn: Optional[Callable[[], dict]] = None,
        on_create: Optional[Callable[[Medium, dict], None]] = None,
        on_inspiration: Optional[Callable[[], None]] = None,
        status_service: "Optional[object]" = None,
        inspire_fn: "Optional[Callable[[str, str, Callable[[str], None], Callable[[str], None]], None]]" = None,
        on_theme_set: Optional[Callable[[Medium, dict], None]] = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        _apply_css()
        self.add_css_class("create-view")

        # RAW order (native-first). The visual→textual sort is applied only at
        # the DISPLAY points (chip row + possibilities wall) via
        # sort_mediums_visual_first — deliberately NOT here, so the DEFAULT
        # active medium stays the raw first entry (Image), not whatever sorts
        # first (Video). Reordering the row must not silently change what a
        # fresh Create generates.
        self._mediums_fn = mediums_fn or default_mediums
        self._health_fn = health_fn or server_manager.status_all
        self._on_create = on_create
        self._on_inspiration = on_inspiration
        # SP-3c-3 "Inspire me" seam — DISTINCT from `on_inspiration` above
        # (the inspiration DOOR, which hands off entirely to the Muse).
        # `inspire_fn(prompt_type, seed_text, on_result, on_error)` reuses the
        # exact `generate_prompt.py`/prompt-server path ControlPanel's own
        # "Inspire me" button already drove (see `MainWindow._create_inspire_fn`
        # for the callback shape this mirrors) — it is expected to run off the
        # GTK main thread and call back via `GLib.idle_add`, but CreateView's
        # own `_on_inspire_result`/`_on_inspire_error` wrap the widget
        # mutation in another `GLib.idle_add` regardless, so a same-thread
        # (test) fake is just as safe as a real background thread. `None`
        # (the default) means "no button at all" — see `_build_idea_row`.
        #
        # TWO-MODE (regression fix 1/2): `seed_text` is `_prompt_entry`'s
        # CURRENT text at click time, read by `_on_inspire_clicked` — empty
        # -> fresh generation; non-empty -> the backend polishes/remixes the
        # existing words instead of discarding them. This restores behavior
        # the deleted ControlPanel/ArtgenPanel Inspire buttons had; the
        # backend (`prompt_client.generate_prompt`) was two-mode the whole
        # time — only this caller's seed-threading had been lost. The shared
        # helper `create_param_panels.attach_inspire_button` implements this
        # exact same click contract for any OTHER prompt `Gtk.Entry` (e.g.
        # pipeline-editor field entries) so the two-mode behavior isn't
        # forked per surface.
        self._inspire_fn = inspire_fn
        self._inspire_generating = False
        # SP-3d-1 "Theme Set" — migrated from ControlPanel's own "🎬 Theme
        # Set" button (never dropped, per CLAUDE.md's "user: never drop"
        # note; see the audit `.superpowers/sdd/sp3d-audit.md` §1). Fired
        # SYNCHRONOUSLY with `(medium, params)` — the same shape as
        # `on_create` — rather than the async callback shape `inspire_fn`
        # uses: MainWindow's implementation (`_on_create_theme_set`) owns the
        # background thread itself and calls back into `set_theme_queued`/
        # `set_theme_error` (below) via `GLib.idle_add`, mirroring how
        # `_begin_create_job`/`_fail_create_job` already reach into
        # `self._result_panel` directly for the Create CTA. `None` (the
        # default) means "no button at all" — same migration-safe contract
        # as `inspire_fn`.
        self._on_theme_set = on_theme_set
        self._theme_generating = False
        # ModelStatusService (SP-2 Task 1/2): MainWindow constructs and starts
        # the single service instance and passes it in so CreateView doesn't
        # build its own competing poller. Accepts a bare `object` type hint
        # (not `ModelStatusService`) so a test fake need not subclass the real
        # service.
        self._status_service = status_service
        # Last snapshot pushed by the service (key -> Status). Stays `{}`
        # when no service is injected -- the boolean `_model_health` path
        # (below) is what drives dots in that case instead.
        self._status_snapshot: dict = {}
        # Unsubscribe closure returned by `status_service.subscribe()`, or
        # None when there's no service (or after `_on_unrealize` has already
        # torn it down). Checked instead of blindly calling so `unrealize`
        # firing twice -- or a status_service=None CreateView -- never
        # raises.
        self._status_unsub: Optional[Callable[[], None]] = None
        if self._status_service is not None:
            # Seed synchronously from the service's current state so the
            # very first render (chip row / model door built below, before
            # any snapshot could possibly have been pushed) already shows
            # real dots instead of a blank "all OFF" flash.
            self._status_snapshot = self._status_service.snapshot()
            self._status_unsub = self._status_service.subscribe(
                lambda snap: GLib.idle_add(self._on_status_snapshot, snap)
            )
        # Unsubscribe when this widget is torn down -- guards both the
        # status_service=None case and a double `unrealize` fire (GTK can
        # emit it more than once during some teardown paths); the
        # subscribe() closure is independently idempotent (model_status.py)
        # but `_on_unrealize` also self-guards via `_status_unsub`.
        self.connect("unrealize", self._on_unrealize)

        self._entry_mode = "idea"
        self._active_medium: Optional[Medium] = None
        self._chip_buttons: dict = {}
        self._model_health: dict = {}
        # The currently-mounted RoleZonePanel (wrapping a real param panel,
        # Task 4+/6), or None while the active medium still shows the Task 3
        # stub. `_collect_params` reads this to decide between a real
        # `panel.collect()` and `{}`, and to read `applied_modifier_text()`.
        self._active_panel: Optional[RoleZonePanel] = None
        # Scoped model dropdown (Task 6): (server_key, canonical_model_id or
        # None, label) aligned 1:1 with the Gtk.StringList currently mounted
        # in `_model_dropdown` — see `_populate_model_dropdown`/`_collect_params`.
        self._model_dropdown_entries: "list[tuple]" = []
        # Parallel (Task 4): (friendly_name, benefit, dot_glyph) per row, read
        # by the dropdown's `Gtk.SignalListItemFactory` `bind` callback (by
        # position) so the popped-open list shows a friendly name + dimmed
        # benefit tagline. See `_populate_model_dropdown`.
        self._model_row_meta: "list[tuple]" = []

        # Built (but not yet appended) BEFORE the chip row: `_build_chip_row`
        # activates the first chip's toggle button as its last step, which
        # synchronously fires `_select_medium` -> `_swap_panel` -> reads
        # `self._panel_host` AND `self._model_dropdown`. Constructing the chip
        # row before these existed was a Task 3 latent bug — PyGObject
        # swallows the resulting AttributeError inside the GTK signal
        # marshaller (logs a traceback, doesn't raise), so the initial swap
        # silently no-opped and the default-active medium never actually got
        # a mounted panel. Building (not appending) both widgets first fixes
        # the ordering while leaving the visual append order unchanged.
        self._panel_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._panel_host.add_css_class("create-panel-host")

        self._model_dropdown = Gtk.DropDown()
        self._model_dropdown.add_css_class("create-model-dropdown")
        # SP-3c-2: keep the mounted native panel's OWN (otherwise-invisible —
        # RoleZonePanel skips `kind == "model"` rows) model state in lockstep
        # with THIS scoped dropdown, the one the user actually sees/clicks.
        # Today this only matters for VideoParamPanel's AnimateDiff options
        # box (see `_sync_panel_model_selection`), but is wired generically
        # so any future panel that exposes `set_selected_model_key` benefits
        # too.
        self._model_dropdown.connect("notify::selected", self._on_scoped_model_dropdown_changed)
        # Task 4: two-line popup rows (friendly name + dimmed benefit
        # tagline), read from `self._model_row_meta` (built alongside
        # `_model_dropdown_entries` by `_populate_model_dropdown`). Set as
        # `list_factory` ONLY — not `factory` — so the collapsed/selected
        # button keeps its existing single-line rendering (dot + a label
        # string) and only the popped-open list gets the richer two-line
        # row. Follow-up fix: that single-line label string itself is now
        # built from `server_manager.display_name_for(key)` in
        # `_populate_model_dropdown` (not the raw `ServerDef.label`/
        # `CAPABILITY_LABELS` implementation string) — the resting button
        # must never show an implementation string either.
        model_row_factory = Gtk.SignalListItemFactory()
        model_row_factory.connect("setup", self._on_model_row_setup)
        model_row_factory.connect("bind", self._on_model_row_bind)
        self._model_dropdown.set_list_factory(model_row_factory)

        # Animate-needs reveal section (SDD task-6-brief.md): CreateView-owned
        # chrome, NOT a `CreateParamPanel` field — built here (before
        # `_build_chip_row()`'s synchronous first `_swap_panel` ->
        # `_populate_model_dropdown` -> `_update_animate_extras_visibility`
        # call below) so that first populate has something to toggle. Starts
        # hidden; see `_AnimateExtras`'s own docstring.
        self._animate_extras = _AnimateExtras()

        # "Start something" possibilities wall (SP-2 Task 2): a full-width
        # wall of per-medium exemplar tiles above the doors/chips. Tapping a
        # tile calls `_on_possibility_picked`, which only SEEDS the existing
        # composer (selects the medium chip, switches to the idea door, fills
        # the prompt entry) — it never touches a generation param directly,
        # so `_collect_params()` is byte-for-byte identical to doing the same
        # three steps by hand (pinned by
        # tests/test_create_view_possibilities.py::test_collect_params_unchanged_by_pick).
        # Constructed defensively: a wall failure (e.g. the real media_store
        # singleton raising during art resolution) must never break Create.
        try:
            self._possibilities = PossibilitiesWall(
                # Display sorted visual→textual, same as the chip row.
                mediums_fn=lambda: sort_mediums_visual_first(self._mediums_fn()),
                on_pick=self._on_possibility_picked,
            )
        except Exception:
            self._possibilities = None

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content.add_css_class("create-view-content")
        content.add_css_class("create-form-pane")
        content.set_valign(Gtk.Align.START)  # hug the top; don't stretch rows
        if self._possibilities is not None:
            # The wall is now a compact, self-contained horizontal SHELF
            # (single row, scrolls sideways) — always-present inspiration with a
            # minimal vertical footprint, so the Create surface fits without
            # scrolling. Appended directly (no outer height-capped band needed).
            content.append(self._possibilities)
        # The doors row (idea / model / inspiration toggles) is built for its
        # side effects — it populates `self._doors` and sets the default "idea"
        # entry mode — but is NO LONGER MOUNTED: the possibilities wall is the
        # entry point now, so a separate mode-toggle row is redundant clutter.
        # Hiding (not deleting) keeps `_doors`/entry-mode wiring + tests intact
        # while reclaiming the vertical space, echoing the release's compact
        # generation column.
        _doors_row = self._build_doors_row()
        _doors_row.set_visible(False)
        content.append(_doors_row)
        content.append(self._build_idea_row())
        content.append(self._build_model_door_row())
        content.append(self._build_chip_row())  # fires _select_medium synchronously
        content.append(self._build_model_dropdown_row())
        # Mounted directly under the model row, like `_prompt_entry` — CreateView
        # chrome, not part of the wrapped RoleZonePanel (see `_AnimateExtras`'s
        # docstring). Visibility toggled by `_update_animate_extras_visibility`.
        content.append(self._animate_extras)
        content.append(self._panel_host)
        # NOTE: the CTA row is NOT appended here — it's pinned below the
        # scrolling form (see the form_scroll + cta_bar assembly below) so
        # ✨ Create is always visible, never scrolled below the fold.

        # Two-pane responsive layout (Task 2): the form column built above
        # becomes one child of a Gtk.FlowBox, alongside a fresh
        # CreateResultPanel (Task 1 — standalone until this task). See
        # `_build_panes` for the reflow settings and `_panes_wrap` for the
        # test seam.
        self._result_panel = CreateResultPanel()
        panes = self._build_panes(content, self._result_panel)

        # Width clamp (fix: content sprawling edge-to-edge / overflowing on a
        # wide window) — `self` stays a plain Gtk.Box so every existing caller
        # (main_window.py mounts `self._create_view` directly) is unaffected;
        # only what's INSIDE it is now capped to a comfortable column. The
        # cap is raised to `_TWO_PANE_MAX_WIDTH` (see that constant's
        # docstring) now that the clamped content is two panes, not one.
        # The form + result panes SCROLL; the CTA is PINNED below them so the
        # primary action (✨ Create) is always visible — a CTA must never sit
        # below the fold. CreateView is mounted directly in the gallery stack
        # (no outer ScrolledWindow), so this internal scroll owns the form's
        # overflow and the CTA bar stays fixed at the bottom.
        self.set_vexpand(True)
        form_scroll = Gtk.ScrolledWindow()
        form_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        form_scroll.set_vexpand(True)
        # align="start": flush-LEFT within the width cap so a wide window doesn't
        # leave a big empty left gutter (the surplus sits on the right, past the
        # result pane), instead of centering the whole column.
        form_scroll.set_child(
            gtk_layout.wrap_centered(panes, max_width=_TWO_PANE_MAX_WIDTH, align="start")
        )
        self.append(form_scroll)

        cta_bar = gtk_layout.wrap_centered(
            self._build_cta_row(), max_width=_TWO_PANE_MAX_WIDTH, align="start"
        )
        cta_bar.add_css_class("create-cta-bar")
        self.append(cta_bar)

        # The boolean poller is the pre-Task-2 fallback: only start it when
        # no ModelStatusService was injected -- when one is present it (and
        # its own background poll thread, started by MainWindow) is the
        # single source of truth, and running both would let the two dot
        # surfaces disagree depending on which one last wrote _model_health.
        if self._status_service is None:
            self._refresh_model_health_async()

    def _build_panes(self, form_pane: Gtk.Widget, result_pane: Gtk.Widget) -> Gtk.FlowBox:
        """Wrap *form_pane* and *result_pane* as the two children of a
        `Gtk.FlowBox` that lays them out side by side on a wide window and
        stacks them (form first — `Gtk.FlowBox` preserves append order) on a
        narrow one, with no manual resize handling.

        `min_children_per_line=1` / `max_children_per_line=2` is exactly the
        brief's spec: never more than 2 columns, and 1 is always allowed (the
        stacked case). `homogeneous=False` lets the two panes have different
        natural widths instead of being forced equal — the form pane keeps
        its own natural width; `result_pane.set_hexpand(True)` lets the
        result panel fill whatever width remains in its own FlowBox cell.
        `selection_mode=NONE` matches every other FlowBox in this file (the
        chip row, the model door's per-group flows) — these are layout
        containers, not selectable lists.
        """
        panes = Gtk.FlowBox()
        panes.set_selection_mode(Gtk.SelectionMode.NONE)
        panes.set_min_children_per_line(1)
        panes.set_max_children_per_line(2)
        panes.set_homogeneous(False)
        panes.add_css_class("create-panes")

        # Compact form column on the left, result fills the rest — echoing the
        # release's cohesive "compact generation panel + big content area".
        # The form is a defined ~660px column (roomy but not sprawling); the
        # result/recents pane hexpands to fill the remaining width. Top-aligned
        # so the form hugs the top instead of stretching its rows.
        form_pane.set_hexpand(False)
        form_pane.set_size_request(660, -1)
        form_pane.set_valign(Gtk.Align.START)
        # Cap the form column's MAX width too — not just its 660 min. The form
        # interior is now a two-column layout (Direction | Controls — see
        # RoleZonePanel), so it wants ~880px; without a cap a wide medium form
        # would sprawl further and, added to the result pane's own natural
        # width, overflow the two-pane FlowBox line and kick the result pane
        # BELOW the form. MaxWidthBin clamps the reported natural width, so an
        # 880 cap gives the two columns room while still leaving the result pane
        # a side slot on a wide window (it stacks below on a genuinely narrow one
        # via min_children_per_line=1). Pairs with the pending prompt label's own
        # max-width-chars cap (see show_pending) — both are needed: this bounds
        # the form, that bounds the result.
        form_column = gtk_layout.wrap_centered(form_pane, max_width=880, align="start")
        form_column.set_hexpand(False)
        form_column.set_valign(Gtk.Align.START)
        result_pane.set_hexpand(True)
        panes.append(form_column)
        panes.append(result_pane)

        self._panes = panes
        return panes

    # ── Pending-queue display (SP-3c-4, task-4-brief.md) ────────────────────

    def refresh_queue(self, items: "list", on_cancel: "Callable[[int], None]") -> None:
        """MainWindow's seam for pushing the current generation queue
        (`self._queue`) into the result pane's pending list, near the
        recents strip. Thin forwarding to `CreateResultPanel.set_queue` —
        kept as its own CreateView method (rather than making MainWindow
        reach into `self._create_view._result_panel` directly) so a future
        change to where/how the queue renders inside CreateView only touches
        this one seam.
        """
        self._result_panel.set_queue(items, on_cancel)

    # ── Width clamp test helper ──────────────────────────────────────────────

    def _is_width_clamped(self) -> bool:
        """True if some ancestor in the built tree is a `MaxWidthBin` —
        proof the surface's content is actually capped, not just visually
        similar. Walks `self`'s direct children (that's where `wrap_centered`
        inserts its wrapper in `__init__`)."""
        child = self.get_first_child()
        while child is not None:
            if isinstance(child, gtk_layout.MaxWidthBin):
                return True
            child = child.get_next_sibling()
        return False

    def _panes_wrap(self) -> bool:
        """True if the two-pane container (form + `CreateResultPanel`) is a
        `Gtk.FlowBox` — a wrapping/reflowing container, never a fixed-
        direction `Gtk.Box` that could overflow the window (Task 2's
        two-pane responsive layout)."""
        return isinstance(getattr(self, "_panes", None), Gtk.FlowBox)

    # ── Doors row (idea default / model / inspiration) ──────────────────────

    def _build_doors_row(self) -> Gtk.Box:
        """Three toggles sharing one radio group; "idea" is default-active.

        Entry mode varies per task, so switching doors is always one tap —
        not a locked first choice (design spec resolution #2).
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.add_css_class("create-doors-row")

        idea_btn = Gtk.ToggleButton(label="\U0001f4a1 Start with an idea")
        idea_btn.add_css_class("create-door-btn")
        idea_btn.add_css_class("create-door-btn-left")
        idea_btn.set_tooltip_text("Start with a prompt and pick a medium.")

        model_btn = Gtk.ToggleButton(label="\U0001f5a5 Start with a model")
        model_btn.add_css_class("create-door-btn")
        model_btn.add_css_class("create-door-btn-mid")
        model_btn.set_tooltip_text(
            "Start from a running or runnable model — the medium follows the model."
        )

        inspiration_btn = Gtk.ToggleButton(label="\U0001f30c Start with inspiration")
        inspiration_btn.add_css_class("create-door-btn")
        inspiration_btn.add_css_class("create-door-btn-right")
        inspiration_btn.set_tooltip_text("Hand off to the Muse for a creative spark.")

        model_btn.set_group(idea_btn)
        inspiration_btn.set_group(idea_btn)

        idea_btn.connect("toggled", lambda b: b.get_active() and self._set_entry_mode("idea"))
        model_btn.connect("toggled", lambda b: b.get_active() and self._set_entry_mode("model"))
        inspiration_btn.connect(
            "toggled", lambda b: b.get_active() and self._set_entry_mode("inspiration")
        )

        row.append(idea_btn)
        row.append(model_btn)
        row.append(inspiration_btn)

        self._doors = {"idea": idea_btn, "model": model_btn, "inspiration": inspiration_btn}
        idea_btn.set_active(True)
        return row

    def _set_entry_mode(self, mode: str) -> None:
        self._entry_mode = mode
        # `_idea_row` may not exist yet the very first time this fires: the
        # idea toggle's `set_active(True)` inside `_build_doors_row` runs
        # synchronously, BEFORE `_build_idea_row` has been called in __init__
        # (doors row is built first). The default visible state of a freshly
        # constructed Gtk.Entry is already True, so skipping the explicit set
        # on that first call is harmless — it only matters for later door
        # switches, by which point `_idea_row` always exists.
        idea_row = getattr(self, "_idea_row", None)
        if idea_row is not None:
            idea_row.set_visible(mode == "idea")
        prompt_entry = getattr(self, "_prompt_entry", None)
        if prompt_entry is not None:
            prompt_entry.set_visible(mode == "idea")
        model_door_row = getattr(self, "_model_door_row", None)
        if model_door_row is not None:
            model_door_row.set_visible(mode == "model")
        if mode == "inspiration" and self._on_inspiration is not None:
            self._on_inspiration()

    # ── Idea door: prompt entry ──────────────────────────────────────────────

    def _build_idea_row(self) -> Gtk.Box:
        """The idea door's prompt entry: "What do you want to make?".

        Visible only while `_entry_mode == "idea"` (see `_set_entry_mode`) —
        the model door shows its own grouped model grid (`_build_model_door_row`)
        and the inspiration door hands off entirely to the Muse, so neither
        needs a competing prompt field on screen.
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.add_css_class("create-idea-row")

        entry = Gtk.Entry()
        entry.set_placeholder_text("What do you want to make?")
        entry.set_hexpand(True)
        entry.add_css_class("create-idea-entry")
        self._prompt_entry = entry
        row.append(entry)

        # SP-3c-3 "Inspire me": a small, distinctly-styled button (never the
        # CTA's `.create-cta-btn` look — that's reserved for "✨ Create") that
        # fills THIS entry via `self._inspire_fn`. Migration-safe: no
        # `inspire_fn` injected -> no button at all, so pre-3c-3 tests that
        # never pass it see byte-identical idea-row contents.
        if self._inspire_fn is not None:
            inspire_btn = Gtk.Button(label="✨ Inspire me")
            inspire_btn.add_css_class("create-inspire-btn")
            inspire_btn.set_tooltip_text(
                "Inspire a fresh prompt, or reimagine what you've typed."
            )
            inspire_btn.connect("clicked", self._on_inspire_clicked)
            self._inspire_btn = inspire_btn
            row.append(inspire_btn)
        else:
            self._inspire_btn = None

        self._idea_row = row
        return row

    # ── Inspire-me prompt-gen (SP-3c-3) ──────────────────────────────────────

    def _inspire_prompt_type(self) -> str:
        """The `prompt_client.generate_prompt()` "source" string for the
        currently-active medium — image/video/animate map 1:1 via
        `_INSPIRE_PROMPT_TYPE`; an artgen medium (or no active medium yet)
        falls back to `_INSPIRE_PROMPT_TYPE_DEFAULT` ("video", matching
        `generate_prompt.py`'s own CLI default)."""
        medium = self._active_medium
        if medium is None:
            return _INSPIRE_PROMPT_TYPE_DEFAULT
        return _INSPIRE_PROMPT_TYPE.get(medium.id, _INSPIRE_PROMPT_TYPE_DEFAULT)

    def _on_inspire_clicked(self, _btn) -> None:
        """Fire `self._inspire_fn`, showing a loading state while it runs.

        **Two-mode (regression fix 1/2):** reads `_prompt_entry`'s CURRENT
        text as `seed_text` — empty means fresh generation, non-empty means
        "reimagine these exact words" (see the `inspire_fn` seam docstring in
        `__init__`). Previously this hardcoded no seed at all (never read the
        entry), so Inspire could only ever generate from scratch even when
        the brief already had text in it.

        Fail-soft by construction: no `inspire_fn` injected is a no-op (the
        button wouldn't exist to click anyway, but this guards a direct call
        too — see the migration-safe test); an `inspire_fn` that raises
        SYNCHRONOUSLY (e.g. a thread failing to spawn) is caught here so a
        misbehaving seam can never crash Create, mirroring the fail-soft
        contract `MainWindow._on_inspire`/`set_inspire_error` already uphold
        for the legacy ControlPanel button.
        """
        if self._inspire_fn is None or self._inspire_generating:
            return
        prompt_type = self._inspire_prompt_type()
        seed_text = self._prompt_entry.get_text().strip()
        self._set_inspire_generating(True)
        try:
            self._inspire_fn(prompt_type, seed_text, self._on_inspire_result, self._on_inspire_error)
        except Exception as e:  # noqa: BLE001 - fail-soft, see docstring
            self._on_inspire_error(str(e))

    def _on_inspire_result(self, text: str) -> None:
        """`inspire_fn`'s success callback — may be invoked from any thread,
        so the actual widget mutation is posted via `GLib.idle_add` (GTK
        threading rule, CLAUDE.md)."""
        GLib.idle_add(self._apply_inspire_result, text)

    def _apply_inspire_result(self, text: str) -> bool:
        """Runs on the GTK main thread — fill the brief and restore the
        button. Returns False so `GLib.idle_add` fires it exactly once."""
        self._prompt_entry.set_text(text)
        self._set_inspire_generating(False)
        return False

    def _on_inspire_error(self, msg: str) -> None:
        """`inspire_fn`'s failure callback — same any-thread contract as
        `_on_inspire_result`."""
        GLib.idle_add(self._apply_inspire_error, msg)

    def _apply_inspire_error(self, msg: str) -> bool:
        """Runs on the GTK main thread — log and restore the button. The
        brief is left untouched (never overwritten with an error message)."""
        print(f"[tt-gen] Create inspire-me error: {msg}", file=sys.stderr)
        self._set_inspire_generating(False)
        return False

    def _set_inspire_generating(self, generating: bool) -> None:
        """Toggle the inspire button's loading state. A no-op when the
        button doesn't exist (defensive — `_on_inspire_clicked` already
        guards `inspire_fn is None`, but this keeps the setter itself safe
        to call from anywhere)."""
        self._inspire_generating = generating
        btn = getattr(self, "_inspire_btn", None)
        if btn is None:
            return
        if generating:
            btn.set_label("⏳ Generating…")
            btn.set_sensitive(False)
        else:
            btn.set_label("✨ Inspire me")
            btn.set_sensitive(True)

    # ── Model door: grouped, wrapping model grid (Task 7) ───────────────────

    def _build_model_door_row(self) -> Gtk.Box:
        """The model door's content while `_entry_mode == "model"`.

        Task 6 retired the persistent, non-wrapping "live-model strip" that
        used to double as this door's clickable cards (it was the thing
        overflowing the window — see the class docstring) and left an honest
        placeholder in its place. Task 7 mounts the real grouped model grid
        (`_build_model_door`) here instead — hidden outside "model" mode the
        same way `_idea_row` is hidden outside "idea" mode.
        """
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        row.add_css_class("create-model-door-row")
        row.set_visible(False)  # "idea" is the default-active door

        row.append(self._build_model_door())

        self._model_door_row = row
        return row

    def _classify_server_key_for_model_door(self, key: str) -> str:
        """One `server_manager.SERVERS` key -> its model-door group name.

        Classifies by the server's OWN `ServerDef.capabilities`
        (`_CAPABILITY_TO_MODEL_DOOR_GROUP`), NOT by "the medium it implies" —
        see that table's comment for why routing the six chat-LLM backends
        (capability ("artgen",)) through `_server_key_to_medium_id` would
        mis-file them under Animate. First matching capability wins; an
        unknown key or an unrecognised capability falls back to "Text" (every
        such key today is a chat/LLM/prompt service).
        """
        sdef = server_manager.SERVERS.get(key)
        if sdef is not None:
            for cap in sdef.capabilities:
                group = _CAPABILITY_TO_MODEL_DOOR_GROUP.get(cap)
                if group is not None:
                    return group
        return "Text"

    def _model_door_groups(self) -> "dict[str, list[str]]":
        """Every `server_manager.SERVERS` key, classified into Image/Video/
        Animate/Text groups (see `_classify_server_key_for_model_door`).

        Empty groups are omitted from the returned dict — `_build_model_door`
        relies on this to skip empty sections entirely, per the task's "omit
        empty groups" requirement. Purely a function of `SERVERS` +
        capabilities (no `mediums_fn()` I/O) since the classification-by-
        capability fix.

        SP-3 Task 3: when `_detected_model_key()` returns a sentinel (an
        UNMATCHED chat model is currently running), it's appended to "Text"
        — the same group every registered chat-LLM/prompt server lands in
        (`_CAPABILITY_TO_MODEL_DOOR_GROUP`'s "artgen"/"prompt" -> "Text"
        rows) — after the real SERVERS keys, so it never needs its own
        `mediums_fn()`-independent classification rule.
        """
        groups: "dict[str, list[str]]" = {g: [] for g in _MODEL_DOOR_GROUP_ORDER}
        for key in server_manager.SERVERS:
            group = self._classify_server_key_for_model_door(key)
            groups.setdefault(group, [])
            groups[group].append(key)
        detected_key = self._detected_model_key()
        if detected_key is not None:
            groups.setdefault("Text", [])
            groups["Text"].append(detected_key)
        return {g: keys for g, keys in groups.items() if keys}

    def _build_model_door(self) -> Gtk.Widget:
        """The model door's real content (Task 7) — a vertical stack of
        group sections (a header `Gtk.Label` + a wrapping `Gtk.FlowBox` of
        model cards), empty groups omitted. Rebuilt wholesale on every health
        refresh (`_refresh_model_door`) so a card's status dot never goes
        stale — the same tear-down-then-rebuild pattern `_swap_panel` uses
        for `_panel_host`.

        Every wrapping container here is a `Gtk.FlowBox`, never an unbounded
        horizontal `Gtk.Box` — the width-clamp discipline this task exists to
        enforce (see the class docstring's Task 6 "overflowed the window"
        history).
        """
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        container.add_css_class("create-model-door")

        groups = self._model_door_groups()
        for group_name in _MODEL_DOOR_GROUP_ORDER:
            keys = groups.get(group_name)
            if not keys:
                continue  # omit empty groups

            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            section.add_css_class("create-model-door-section")

            header = Gtk.Label(label=group_name)
            header.add_css_class("create-model-door-header")
            header.set_xalign(0.0)
            section.append(header)

            flow = Gtk.FlowBox()
            flow.set_selection_mode(Gtk.SelectionMode.NONE)
            flow.set_max_children_per_line(8)
            flow.set_row_spacing(6)
            flow.set_column_spacing(6)
            flow.add_css_class("create-model-door-flow")
            for key in keys:
                flow.append(self._build_model_card(key))
            section.append(flow)

            container.append(section)

        return container

    def _build_model_card(self, key: str) -> Gtk.Widget:
        """One clickable model card: a live-status dot + the model's label.

        The dot glyph is routed through `_model_dot_glyph` — the SAME helper
        Task 6's scoped dropdown reads (`_populate_model_dropdown`) — so a
        card's dot can never disagree with the dropdown's dot for the same
        server key (the same "single source of truth" discipline CLAUDE.md
        documents for the artgen panel's own health dot). `_model_dot_glyph`
        itself picks the 3-state `ModelStatusService` snapshot when one is
        injected, else falls back to the pre-Task-2 boolean `_model_health`
        map. `running` (used only for the on/off CSS classes below) is
        derived from the glyph rather than re-reading health directly, so it
        stays true exactly when the glyph is "●" — byte-identical to the old
        boolean behavior in the status_service=None case.

        SP-3 Task 3: a `_DETECTED_KEY_PREFIX` sentinel key has no `ServerDef`
        (it was never a real SERVERS entry) — its label is built directly
        from the model id it carries, suffixed " (detected)" so the card
        reads unambiguously as "this is running, but not one of ours" rather
        than looking like an ordinary registered model.

        Task 4: real/synthetic keys show `server_manager.display_name_for(key)`
        as the title (friendly name — e.g. "Wan 2.2" instead of the raw
        "Wan2.2-T2V-A14B  (P300X2)" `ServerDef.label`) with a dimmed
        `benefit_for(key)` subtitle underneath (the SAME `.model-row-benefit`
        CSS class the scoped dropdown's popup rows use), omitted when there's
        no benefit tagline for this key. The detected-sentinel card is
        unaffected — `display_name_for`/`benefit_for` only make sense for a
        real `SERVERS`/`MODEL_DISPLAY_NAMES` entry.
        """
        if _is_detected_key(key):
            label_text = f"{_detected_key_model_id(key)} (detected)"
            benefit_text = ""
        else:
            label_text = server_manager.display_name_for(key)
            benefit_text = server_manager.benefit_for(key)
        dot_glyph = self._model_dot_glyph(key)
        running = dot_glyph == "●"

        btn = Gtk.Button()
        btn.add_css_class("create-model-chip")
        btn.add_css_class("create-model-chip-on" if running else "create-model-chip-off")
        btn.set_tooltip_text(benefit_text or label_text)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dot = Gtk.Label(label=dot_glyph)
        dot.add_css_class("create-model-dot-on" if running else "create-model-dot-off")
        content.append(dot)

        text_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        label = Gtk.Label(label=label_text)
        label.set_xalign(0.0)
        label.add_css_class("create-model-label")
        text_col.append(label)
        if benefit_text:
            benefit_label = Gtk.Label(label=benefit_text)
            benefit_label.set_xalign(0.0)
            benefit_label.add_css_class("model-row-benefit")
            text_col.append(benefit_label)
        content.append(text_col)

        btn.set_child(content)
        btn.connect("clicked", lambda _b, k=key: self._activate_model_card(k))
        return btn

    def _refresh_model_door(self) -> None:
        """Rebuild `_model_door_row`'s content from scratch — called after
        every health refresh so a card's status dot never goes stale. Mirrors
        `_swap_panel`'s tear-down-then-rebuild pattern for `_panel_host`."""
        row = getattr(self, "_model_door_row", None)
        if row is None:
            return
        child = row.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            row.remove(child)
            child = nxt
        row.append(self._build_model_door())

    def _preselect_model_key(self, key: str) -> None:
        """Select *key*'s entry in the scoped `_model_dropdown`, if present.

        A no-op (not an error) when *key* isn't among the active medium's
        currently scoped entries — e.g. a server key `_canonical_model_id_for`
        can't resolve for this medium (see that function's docstring) still
        routes to the Video medium correctly; it just can't be pre-selected
        in the dropdown.
        """
        entries = getattr(self, "_model_dropdown_entries", [])
        for idx, (entry_key, _canonical, _label) in enumerate(entries):
            if entry_key == key:
                self._model_dropdown.set_selected(idx)
                return

    def _activate_model_card(self, key: str) -> None:
        """Model door card click: for a native-medium card (Image/Video/
        Animate), select that model's medium and return to the Idea door,
        pre-scoped to this model. For a **Text** card (a chat-LLM backend or
        prompt-server), return to the Idea door WITHOUT changing the active
        medium.

        Why the split: a Text card's server has the generic ("artgen",)
        capability, which `_server_key_to_medium_id` maps to "the first
        artgen-sourced medium" = `animatediff` (kind "gif") in the real app.
        Routing a "Qwen3-8B" click through that heuristic would silently jump
        the user to AnimateDiff — the exact bug this method must not have. So
        classification-by-capability (`_classify_server_key_for_model_door`)
        gates the routing: only NON-Text cards resolve+activate a medium.

        Reuses existing routing rather than reimplementing it: for a native
        card, `_server_key_to_medium_id` resolves the Medium and activating
        its chip button fires the SAME `_select_medium` -> `_swap_panel` path
        a manual chip click does (repopulating the scoped dropdown as a side
        effect); the dropdown is then pre-selected to *key* when practical
        (`_preselect_model_key` — "when practical" because a key
        `_canonical_model_id_for` can't resolve has nothing to pre-select).
        Either way the Idea door toggle is activated last
        (`_set_entry_mode("idea")`).
        """
        # Text cards (chat LLMs / prompt-server): never resolve a medium —
        # just move the user into the Idea flow, active medium untouched. A
        # Text card click must NEVER land on AnimateDiff.
        if self._classify_server_key_for_model_door(key) == "Text":
            self._doors["idea"].set_active(True)
            return

        medium_id = self._server_key_to_medium_id(key)
        if medium_id is None:
            return

        try:
            mediums = list(self._mediums_fn() or [])
        except Exception:
            mediums = []
        medium = next((m for m in mediums if m.id == medium_id), None)
        if medium is None:
            return

        btn = self._chip_buttons.get(medium_id)
        if btn is not None:
            btn.set_active(True)  # fires _select_medium -> _swap_panel via "toggled"
        else:
            self._select_medium(medium)

        self._preselect_model_key(key)
        self._doors["idea"].set_active(True)  # fires _set_entry_mode("idea")

    # ── Medium chip row ──────────────────────────────────────────────────────

    def _build_chip_row(self) -> Gtk.Widget:
        """One chip per `mediums_fn()` medium; selecting one swaps the panel.

        A `Gtk.FlowBox` (not a plain horizontal `Gtk.Box`) — with ~11 artgen
        generators plus the 3 native mediums, a fixed-direction box would run
        the row off the edge of even the width-clamped column; FlowBox wraps
        onto additional lines instead (width-clamp requirement, Task 6).
        """
        row = Gtk.FlowBox()
        row.set_selection_mode(Gtk.SelectionMode.NONE)
        row.add_css_class("create-chip-row")
        self._chip_row = row

        try:
            raw = list(self._mediums_fn() or [])
        except Exception:
            raw = []
        # DISPLAY order: most-visual → most-textual. The DEFAULT selection,
        # though, is the RAW-first medium (Image) — see __init__'s note — so a
        # reordered row doesn't change what a fresh Create generates.
        mediums = sort_mediums_visual_first(raw)
        default_medium_id = raw[0].id if raw else None

        first_btn: Optional[Gtk.ToggleButton] = None
        default_btn: Optional[Gtk.ToggleButton] = None
        for medium in mediums:
            btn = Gtk.ToggleButton(label=f"{medium.icon} {medium.label}")
            btn.add_css_class("create-chip-btn")
            if first_btn is None:
                first_btn = btn
            else:
                btn.set_group(first_btn)
            btn.connect(
                "toggled",
                lambda b, m=medium: b.get_active() and self._select_medium(m),
            )
            row.append(btn)
            self._chip_buttons[medium.id] = btn
            if medium.id == default_medium_id:
                default_btn = btn

        chosen = default_btn or first_btn
        if chosen is not None:
            chosen.set_active(True)  # fires _select_medium via "toggled"

        return row

    def _on_possibility_picked(self, medium: Medium, idea: str) -> None:
        """Seed the existing composer from a possibilities-wall tile: select
        the medium chip (fires `_select_medium` -> `_swap_panel` via the same
        "toggled" path a manual chip click takes), switch to the idea door,
        and fill the prompt entry.

        Pure convenience wiring — it touches no generation param directly, so
        `_collect_params()` is byte-for-byte identical whether a tile was
        picked or the same medium + prompt were set by hand (pinned by
        tests/test_create_view_possibilities.py::test_collect_params_unchanged_by_pick).
        """
        btn = self._chip_buttons.get(medium.id)
        if btn is not None and not btn.get_active():
            btn.set_active(True)  # fires _select_medium -> _swap_panel via "toggled"
        elif btn is not None:
            self._select_medium(medium)  # already active -> ensure panel matches
        door = self._doors.get("idea")
        if door is not None and not door.get_active():
            door.set_active(True)
        if getattr(self, "_prompt_entry", None) is not None:
            self._prompt_entry.set_text(idea)
            self._prompt_entry.grab_focus()

    def _select_medium(self, medium: Medium) -> None:
        self._active_medium = medium
        self._swap_panel(medium)

    def _swap_panel(self, medium: Medium) -> None:
        """Rebuild the param-panel host for the newly-selected medium.

        Task 4 ported the native "image" medium to a real `ImageParamPanel`
        (`create_param_panels.py`); Task 5 ported "video" and "animate" to
        `VideoParamPanel`/`AnimateParamPanel`; this task (6) ports every
        artgen medium (`medium.source == "artgen"` — verse/ansi/landscape/…)
        to `ArtgenParamPanel`, constructed with `medium.generator` so it
        introspects that generator's own argparse args. A medium that is
        neither a mapped native id nor an artgen medium (only possible for a
        future medium kind not yet ported) still falls back to the Task 3
        stub — a plain, honestly-labeled placeholder.

        Task 6: every real panel is wrapped in `RoleZonePanel(panel, medium)`
        before mounting — `self._active_panel` is the RoleZonePanel, not the
        bare panel (see class docstring). The scoped model dropdown is
        repopulated for the new medium on every swap, including the stub
        fallback (an empty/placeholder dropdown is still correct there).
        """
        child = self._panel_host.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._panel_host.remove(child)
            child = nxt

        self._populate_model_dropdown(medium)

        if medium.source == "native" and medium.id in _NATIVE_PANEL_CLASSES:
            panel = _NATIVE_PANEL_CLASSES[medium.id]()
            zoned = RoleZonePanel(panel, medium)
            self._panel_host.append(zoned)
            self._active_panel = zoned
            # SP-3c-2: `_populate_model_dropdown` above already selected the
            # scoped dropdown's fresh-populate default (or restored/auto-
            # selected a key) BEFORE this panel existed to sync against —
            # push that selection into the panel now that it's mounted.
            self._sync_panel_model_selection()
            return

        if medium.source == "artgen" and medium.generator:
            # ✨ Inspire (regression fix 2/2): thread the SAME `_inspire_fn`
            # seam (and its two-mode click contract, see `attach_inspire_button`)
            # into every artgen generator's theme/subject/prompt-shaped
            # fields that the OLD (deleted, SP-3d-5) ArtgenPanel gave a ✦
            # Inspire button — `_inspire_fn is None` (no seam injected)
            # propagates straight through to "no ✨ buttons", same
            # migration-safe contract the idea-door button already has.
            panel = ArtgenParamPanel(
                medium.generator,
                inspire_fn=self._inspire_fn,
                prompt_type_getter=self._inspire_prompt_type,
            )
            zoned = RoleZonePanel(panel, medium)
            self._panel_host.append(zoned)
            self._active_panel = zoned
            return

        self._active_panel = None
        stub = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        label = Gtk.Label(label=f"{medium.icon} {medium.label} — controls coming soon")
        label.add_css_class("create-panel-stub-label")
        label.set_xalign(0.0)
        stub.append(label)
        self._panel_host.append(stub)

    # ── Scoped model dropdown (Task 6 — replaces the retired model strip) ───

    def _on_model_row_setup(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        """Build the two-line row template (Task 4): a name `Gtk.Label` over
        a dimmed benefit `Gtk.Label` (`.model-row-benefit`). `_on_model_row_bind`
        fills in the actual text per row — this only builds the reusable
        widget skeleton, per the factory pattern."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        name_label = Gtk.Label()
        name_label.set_xalign(0.0)
        name_label.add_css_class("model-row-name")
        box.append(name_label)
        benefit_label = Gtk.Label()
        benefit_label.set_xalign(0.0)
        benefit_label.add_css_class("model-row-benefit")
        box.append(benefit_label)
        list_item.set_child(box)

    def _on_model_row_bind(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        """Fill the row template from `self._model_row_meta[position]` — the
        parallel `(friendly_name, benefit, dot_glyph)` list `_populate_model_dropdown`
        builds alongside `_model_dropdown_entries`. Reads by POSITION (not the
        bound `Gtk.StringObject`'s own string) since the same `Gtk.StringList`
        backs both this richer popup row and `_model_dropdown_entries`'s
        single-line label — the two are aligned 1:1 by index."""
        pos = list_item.get_position()
        box = list_item.get_child()
        name_label = box.get_first_child()
        benefit_label = name_label.get_next_sibling()
        meta = getattr(self, "_model_row_meta", [])
        if 0 <= pos < len(meta):
            name, benefit, dot = meta[pos]
        else:
            name, benefit, dot = ("", "", "")
        name_label.set_label(f"{dot} {name}".strip())
        if benefit:
            benefit_label.set_label(benefit)
            benefit_label.set_visible(True)
        else:
            benefit_label.set_label("")
            benefit_label.set_visible(False)

    def _build_model_dropdown_row(self) -> Gtk.Box:
        """Label + `self._model_dropdown`, mounted directly above
        `_panel_host`. The dropdown's contents are populated per-medium by
        `_populate_model_dropdown` (called from `_swap_panel` and from the
        health refresh), never here — at construction time the active medium
        isn't chosen yet.
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("create-model-dropdown-row")

        label = Gtk.Label(label="Model")
        label.add_css_class("create-model-dropdown-label")
        row.append(label)

        self._model_dropdown.set_hexpand(True)
        row.append(self._model_dropdown)
        return row

    def _scoped_model_keys(self, medium: Optional[Medium] = None) -> "list[str]":
        """`server_manager` keys whose `capabilities` match *medium* (default:
        the currently-active medium) — the pure "what belongs in the scoped
        dropdown" query, split out from the GTK-building side so a test (or a
        future caller) can assert the list without touching widgets.

        Capability lookup: a native medium's own id IS its capability string
        ("image"/"video"/"animate" — matches `server_manager.ServerDef.
        capabilities` verbatim); an artgen medium's capability is the fixed
        string "artgen" (every artgen generator shares the same chat-LLM
        servers — there's no per-generator server). Anything else (no active
        medium yet, or a future medium kind) returns `[]`.

        SP-3c-2: the "video" medium gets one extra synthetic key,
        "animatediff", appended after the server-backed ones.
        `server_manager.servers_for_capability("video")` can never include it
        — `server_manager.CAPABILITY_LABELS`'s own comment notes AnimateDiff
        is "hardware-only (no server)", so there is no `ServerDef` to match —
        but native AnimateDiff still needs a slot in this SCOPED dropdown:
        it's the one CreateView surface the user actually sees and clicks
        (`RoleZonePanel` deliberately never renders a wrapped panel's own
        `kind == "model"` row — see that class's module comment — so
        `VideoParamPanel`'s internal AnimateDiff choice would otherwise be
        unreachable). `_canonical_model_id_for`/`_populate_model_dropdown`
        already know how to resolve and label this key generically (via
        `create_param_panels._VIDEO_MODEL_IDS`/`server_manager.
        CAPABILITY_LABELS` respectively) — no special-casing needed there.

        **AnimateDiff-model-fix**: an artgen medium whose generator bypasses
        the chat LLM entirely (`medium.uses_llm is False` — e.g. the artgen
        "animatediff" medium; see `Medium.uses_llm`'s docstring) does NOT
        share the chat-LLM servers with every other artgen medium the way
        verse/ansi/landscape/… do. It gets a single self-entry, `[medium.id]`
        — that id equals the generator name for every artgen medium, so it
        reads as "the medium IS the model". No detected-key entry either:
        there is nothing running to detect, since this medium never talks to
        a chat LLM at all.
        """
        medium = medium if medium is not None else self._active_medium
        if medium is None:
            return []
        if medium.source == "artgen" and not medium.uses_llm:
            return [medium.id]
        cap = "artgen" if medium.source == "artgen" else medium.id
        keys = [sdef.key for sdef in server_manager.servers_for_capability(cap)]
        if medium.id == "video":
            # AnimateDiff (local, no server) leads — always-ready + the
            # index-0 auto-select fallback. Animate (Wan2.2-Animate) is a
            # real server keyed ("animate",), so servers_for_capability("video")
            # won't return it; append it as a Video model by hand.
            keys = ["animatediff"] + keys + ["animate"]
        if medium.source == "artgen":
            detected_key = self._detected_model_key()
            if detected_key is not None:
                keys.append(detected_key)
        return keys

    def _detected_model_key(self) -> Optional[str]:
        """SP-3 Task 3: the sentinel key (`_DETECTED_KEY_PREFIX` + model id)
        for the currently-running chat-LLM model, IF AND ONLY IF it doesn't
        match any registered `server_manager.SERVERS` entry — `None` in every
        other case (no status service injected, nothing running, the
        service call raises, or the running model DOES match a known key —
        Task 2 already makes that key's own dot read READY, so no synthetic
        entry is needed).

        Single source of truth for "is there a detected-but-unregistered
        model right now" — `_scoped_model_keys` (dropdown), `_model_door_groups`
        (Model door "Text" group), and `_autoselect_running_model_index` all
        call this instead of each re-deriving it from `running_artgen_model()`
        their own way, so the three surfaces can never disagree.
        """
        if self._status_service is None:
            return None
        try:
            info = self._status_service.running_artgen_model()
        except Exception:
            return None
        if info is None or info.matched_key is not None:
            return None
        return f"{_DETECTED_KEY_PREFIX}{info.model_id}"

    def _populate_model_dropdown(self, medium: Medium) -> None:
        """Rebuild `self._model_dropdown` to list ONLY *medium*'s own models.

        For a native medium with a real model field (image/video/animate),
        an entry is included only when `_canonical_model_id_for` resolves a
        value for it — this keeps every SELECTABLE entry able to produce a
        real "model" value in `_collect_params` (see that function's
        docstring for the one case, today, where a server key has no panel
        equivalent). For an artgen medium (no "model" field at all) every
        scoped key is listed for information — selecting one has no effect
        on `collect()`.

        Health dots are rendered via `_model_dot_glyph` (SP-2 Task 2) — when
        a `ModelStatusService` is injected it reads the 3-state snapshot
        (●/◐/◌), else it falls back to the pre-Task-2 boolean
        `self._model_health` map (kept fresh by `_refresh_model_health_async`/
        `_apply_model_health`) — "if practical, else just labels" per the
        task brief; a key absent from either map (never checked yet) just
        shows the "offline"/"off" dot.

        **Selection is preserved across repopulation** by SERVER KEY, not by
        index. Repopulation fires both on a medium swap AND on the async
        health refresh (`_apply_model_health`), which can land seconds after
        the view appears (`status_all` sweeps ~16 servers at a 2s-per-server
        timeout when they're down). Snapping unconditionally back to index 0
        would silently discard a user's just-made choice — e.g. pick
        "Mochi-1", the initial health check completes, and Create would
        generate "wan2.2-t2v" instead. So: capture the currently-selected
        key before rebuilding, then re-select it if it's still present in the
        new list, else fall back to index 0. On a medium SWAP the previous
        key belongs to a different medium and won't be present, so it
        naturally falls back to 0 (resetting to the new medium's default —
        the correct behavior there); on a same-medium health refresh the key
        IS present, so the selection holds. This also makes
        `_preselect_model_key`'s choice survive the health refresh that races
        a Model-door card click.

        **SP-2 Task 3 — auto-select the running model on a FRESH populate.**
        When the previous key does NOT survive the rebuild (medium swap /
        first build), the "index 0" fallback described above is no longer
        unconditional: `_autoselect_running_model_index` gets first crack,
        defaulting the selection to whatever `self._status_service` reports
        running or starting for *medium*'s capability, and only falling back
        to index 0 itself when there's no service, no capability, nothing
        running/starting, or the running key isn't in this medium's scoped
        list. Because this only runs on the "prev_key not found" branch, a
        same-medium refresh is untouched — a model finishing its startup
        can't override a manual pick, preserving the guarantee above.
        """
        is_native_with_model = medium.source == "native" and medium.id in (
            "image", "video", "animate",
        )

        # Capture the currently-selected server key BEFORE the rebuild — the
        # list contents (and therefore indices) can change, so the index is
        # not a stable handle; the key is.
        prev_key = self._selected_model_key()

        entries: "list[tuple]" = []
        labels: "list[str]" = []
        # Parallel to `entries`/`labels` (Task 4): `(friendly_name, benefit,
        # dot_glyph)` per row, read by the `_model_dropdown` factory's `bind`
        # (indexed by `list_item.get_position()`) so the popped-open list can
        # show a friendly name + dimmed benefit tagline without disturbing
        # `entries`' `(key, canonical, label)` shape, which `_selected_model_key`
        # and `_collect_params` still key off unchanged.
        row_meta: "list[tuple]" = []
        llm_free_artgen_self_key = (
            medium.source == "artgen" and not medium.uses_llm
        )
        for key in self._scoped_model_keys(medium):
            if llm_free_artgen_self_key and key == medium.id:
                # AnimateDiff-model-fix: the ONE entry `_scoped_model_keys`
                # produces for an LLM-free artgen medium — the generator
                # itself, standing in for "model". No `ServerDef` and no
                # `CAPABILITY_LABELS` entry backs a generator name, so this
                # is handled here rather than falling through to the
                # SERVERS.get lookup below (`medium.label` — e.g.
                # "AnimateDiff" — not the bare key, and not the native
                # video-medium's "AnimateDiff  (Blackhole)" label, which is a
                # DIFFERENT medium's synthetic key that happens to share this
                # string). `canonical=None`: artgen mediums have no "model"
                # field, so `_collect_params`'s override stays a no-op —
                # `collect()` is unaffected, exactly like every other artgen
                # medium's dropdown.
                label_text = medium.label
                dot = self._model_dot_glyph(key, medium=medium)
                labels.append(f"{dot} {label_text}")
                entries.append((key, None, label_text))
                row_meta.append((medium.label, "", dot))
                continue
            if _is_detected_key(key):
                # SP-3 Task 3: the synthetic "detected model" entry — never a
                # real SERVERS key, so it skips `_canonical_model_id_for`/
                # `SERVERS.get` entirely. `canonical=None` means "no model
                # override for collect()" (see `_collect_params`'s "model"
                # override, which is a no-op when canonical is None) — this
                # is what keeps the entry inert for artgen mediums (the only
                # medium kind that can ever produce this key; native mediums
                # never call `_detected_model_key`, see `_scoped_model_keys`).
                label_text = f"{_detected_key_model_id(key)} (detected)"
                dot = self._model_dot_glyph(key)
                labels.append(f"{dot} {label_text}")
                entries.append((key, None, label_text))
                row_meta.append((label_text, "", dot))
                continue
            canonical = _canonical_model_id_for(medium, key)
            if is_native_with_model and canonical is None:
                continue
            sdef = server_manager.SERVERS.get(key)
            # "animatediff" (SP-3c-2) has no `ServerDef` — fall back to
            # `server_manager.CAPABILITY_LABELS` (which already carries a
            # human label for it, "AnimateDiff  (Blackhole)", for exactly
            # this reason — see that dict's own comment) before falling all
            # the way back to the bare key.
            label_text = (
                sdef.label if sdef is not None
                else server_manager.CAPABILITY_LABELS.get(key, key)
            )
            dot = self._model_dot_glyph(key)
            # Task 4 follow-up: the collapsed/selected DropDown button renders
            # straight off this `labels` StringList (the popup's two-line
            # friendly-name+benefit rendering is a separate factory —
            # `_on_model_row_setup`/`_on_model_row_bind`, wired via
            # `set_list_factory` above — that never touches the resting
            # button). Using the raw `label_text` here would put the
            # implementation string ("Wan2.2-T2V-A14B  (P300X2)") right back
            # in front of the user the instant they collapse the popup —
            # exactly what this whole feature exists to avoid. Swap in the
            # same friendly name the popup shows; `entries`' `label_text`
            # (3rd tuple element) is left alone since selection logic
            # (`_selected_model_key`, `_collect_params`) keys off `key`/
            # `canonical`, never that string.
            labels.append(f"{dot} {server_manager.display_name_for(key)}")
            entries.append((key, canonical, label_text))
            row_meta.append((
                server_manager.display_name_for(key),
                server_manager.benefit_for(key),
                dot,
            ))

        if not entries:
            labels = ["No models available"]
            entries = [(None, None, "No models available")]
            row_meta = [("No models available", "", "")]

        self._model_dropdown_entries = entries
        self._model_row_meta = row_meta
        self._model_dropdown.set_model(Gtk.StringList.new(labels))
        # Re-select the previously-chosen key if it survived the rebuild
        # (same-medium health refresh) — v0.28.1 fix, untouched: `restored`
        # stays `None` until a match is found, which is exactly how we tell
        # "fresh populate" (medium swap / first build — prev_key belongs to a
        # different medium's key set, or there was no prior selection at all)
        # from "same-medium refresh" (prev_key IS one of the new entries).
        restored: "Optional[int]" = None
        if prev_key is not None:
            for idx, (entry_key, _canonical, _label) in enumerate(entries):
                if entry_key == prev_key:
                    restored = idx
                    break
        if restored is None:
            # Fresh populate: prefer the running/starting model for this
            # medium's capability (SP-2 Task 3) over the hardcoded index 0
            # default. A same-medium refresh never reaches this branch, so a
            # model finishing its health check can't yank a manual pick out
            # from under the user (the exact regression the v0.28.1 fix
            # above guards against).
            restored = self._autoselect_running_model_index(medium, entries)
        self._model_dropdown.set_selected(restored)
        # Explicit call, not a reliance on `notify::selected` firing (SDD
        # task-6-brief.md Step 4): `set_selected(restored)` above only emits
        # a change notification when `restored` differs from whatever index
        # was already selected — a same-medium health refresh that lands on
        # the SAME index would otherwise leave the Animate-needs section's
        # visibility stale. Covers both a medium swap and a same-medium
        # repopulate; `_on_scoped_model_dropdown_changed` covers every
        # subsequent manual dropdown pick.
        self._update_animate_extras_visibility()

    def _autoselect_running_model_index(
        self, medium: Medium, entries: "list[tuple]"
    ) -> int:
        """SP-2 Task 3: index into *entries* of the model actually running or
        starting for *medium*'s capability, or `0` (the pre-existing medium
        default) when auto-select doesn't apply.

        Only called from `_populate_model_dropdown` on a FRESH populate (see
        that method) — never on a same-medium refresh, which preserves a
        manual pick instead.

        Falls back to `0` in every case that isn't a clean "yes, auto-select
        this": no `_status_service` injected (byte-identical to pre-Task-3
        behavior), *medium* has no capability (a future non-native,
        non-artgen medium kind — no model dropdown semantics), the service
        has nothing running/starting for that capability, or the key it
        returns isn't one of *entries* (e.g. it maps to a server key this
        medium's dropdown doesn't scope, or (defensively) the service call
        itself raises) — never guess, just use the existing default.

        SP-3 Task 3: an artgen medium's capability is the fixed "artgen"
        string (mirrors `_scoped_model_keys`'s own capability lookup, not
        `_MODEL_STATUS_CAPABILITY` — that map is native-medium-only, see its
        own comment). `running_or_starting("artgen")` already resolves to
        the ONE matched key Task 2's per-model readiness marks READY, so a
        KNOWN running model auto-selects exactly as before. When the running
        model is UNMATCHED (`running_or_starting` finds no READY/STARTING
        artgen key at all — every registered artgen entry is OFF), fall back
        to `_detected_model_key()`'s synthetic entry instead of leaving the
        selection at the arbitrary index-0 default.
        """
        if self._status_service is None:
            return 0
        cap = "artgen" if medium.source == "artgen" else _MODEL_STATUS_CAPABILITY.get(medium.id)
        if cap is None:
            return 0
        # 'Video is Video': the video medium's models span two capabilities
        # (video servers + the one animate server). Prefer whichever is
        # actually running; only then fall through to the index-0 default
        # (AnimateDiff — the local no-server model that always works).
        caps = ("video", "animate") if medium.id == "video" else (cap,)
        running_key = None
        for c in caps:
            if c is None:
                continue
            try:
                rk = self._status_service.running_or_starting(c)
            except Exception:
                rk = None
            if rk is not None:
                running_key = rk
                break
        if running_key is None and medium.source == "artgen":
            running_key = self._detected_model_key()
        if running_key is None:
            return 0
        for idx, (entry_key, _canonical, _label) in enumerate(entries):
            if entry_key == running_key:
                return idx
        return 0

    def _selected_model_key(self) -> Optional[str]:
        """The server key of the scoped dropdown's current selection, or
        `None` when nothing valid is selected yet (no entries, or the
        placeholder "No models available" sentinel whose key is `None`).
        Reads the `(server_key, canonical, label)` entry list built by
        `_populate_model_dropdown` — the index is only meaningful paired with
        that list, which is why this is the single place that maps one to the
        other."""
        entries = getattr(self, "_model_dropdown_entries", [])
        idx = self._model_dropdown.get_selected()
        if 0 <= idx < len(entries):
            return entries[idx][0]
        return None

    def _on_scoped_model_dropdown_changed(self, _dropdown, _pspec) -> None:
        """`notify::selected` handler for the scoped `_model_dropdown` — see
        `_sync_panel_model_selection` and `_update_animate_extras_visibility`."""
        self._sync_panel_model_selection()
        self._update_animate_extras_visibility()

    def _update_animate_extras_visibility(self) -> None:
        """Show the Animate-needs section (`self._animate_extras`) only when
        BOTH the active medium is "video" AND the scoped dropdown's current
        selection is the Animate model — the SAME two-part gate
        `_collect_params`'s merge guard uses, so visibility and collect()
        behavior can never disagree (SDD task-6-brief.md Step 4).

        The "video" medium check matters because the scoped-dropdown key
        `"animate"` is not unique to it: a still-reachable (test-only in
        this codebase today — see `create_mediums._NATIVE_MEDIUMS`, which no
        longer lists a native "animate" medium) `AnimateParamPanel`-backed
        medium whose id is itself `"animate"` also resolves that same scoped
        key (its capability IS its id). That panel already renders its own
        motion/character/mode rows directly — this CreateView-level section
        must never double them.

        No-op when `self._animate_extras` doesn't exist yet — guards the
        narrow window during `__init__` before it's constructed, and any
        test double that skips `__init__` entirely (`CreateView.__new__`).
        """
        animate_extras = getattr(self, "_animate_extras", None)
        if animate_extras is None:
            return
        medium = getattr(self, "_active_medium", None)
        visible = (
            medium is not None
            and medium.id == "video"
            and _animate_extras_visible_for(self._selected_model_key())
        )
        animate_extras.set_visible(visible)

    def _sync_panel_model_selection(self) -> None:
        """Push the scoped dropdown's current selection into the active
        native panel's OWN model dropdown (SP-3c-2).

        `RoleZonePanel` deliberately never renders a wrapped panel's own
        `kind == "model"` row (see that class's module comment) — the
        user-visible model picker is this SCOPED `_model_dropdown` instead.
        Without this sync, a panel's internal model state (which some panels
        use for their OWN purposes beyond just "what canonical id to submit"
        — e.g. `VideoParamPanel` keys its AnimateDiff-options-box visibility
        off it) would sit frozen at its own built-in default forever, since
        nothing else ever touches it. Called both right after a fresh panel
        is mounted (`_swap_panel`) and on every subsequent scoped-dropdown
        selection change.

        No-op when there's no active `RoleZonePanel`, the wrapped panel
        doesn't expose a `set_selected_model_key` hook (only `VideoParamPanel`
        does today — Image/Animate/Artgen panels are unaffected), or nothing
        valid is currently selected in the scoped dropdown.
        """
        if not isinstance(self._active_panel, RoleZonePanel):
            return
        set_key = getattr(self._active_panel._panel, "set_selected_model_key", None)
        if set_key is None:
            return
        key = self._selected_model_key()
        if key is None:
            return
        if self._active_medium is not None and self._active_medium.id == "video":
            key = _VIDEO_SERVER_KEY_ALIAS.get(key, key)
        set_key(key)

    # ── Model health (feeds the scoped dropdown's status dots) ──────────────

    def _refresh_model_health_async(self) -> None:
        """Fetch health off the GTK main thread; apply on the main thread.

        `health_fn` (real default: `server_manager.status_all`) may perform
        real HTTP calls with per-service timeouts, so it must never run on
        the GTK thread (see module docstring / CLAUDE.md's GTK threading
        rule). Any exception from a bad/fake `health_fn` degrades to an
        empty status map rather than crashing the view.
        """
        def _bg() -> None:
            try:
                statuses = dict(self._health_fn() or {})
            except Exception:
                statuses = {}
            GLib.idle_add(self._apply_model_health, statuses)

        threading.Thread(target=_bg, daemon=True).start()

    def _apply_model_health(self, statuses: dict) -> bool:
        """Store the fresh health map and refresh the scoped dropdown's dots
        for whichever medium is currently active (a no-op if none is active
        yet — shouldn't happen post-`__init__`, but defensive)."""
        self._model_health = statuses
        if self._active_medium is not None:
            self._populate_model_dropdown(self._active_medium)
        self._refresh_model_door()
        return GLib.SOURCE_REMOVE

    # ── ModelStatusService (SP-2 Task 2): 3-state dots ───────────────────────

    def _status_glyph(self, status: "Status") -> str:
        """Map a `model_status.Status` to its dot glyph: READY -> "●" (ready),
        STARTING -> "◐" (half-lit, coming up), anything else (OFF/ERROR) ->
        "◌" (hollow, not available). ERROR folds into the same glyph as OFF
        deliberately — from a "can I generate with this right now" glance,
        a model that errored out is exactly as unusable as one that's off;
        the distinction only matters to whoever's debugging the backend, not
        to this at-a-glance dot."""
        if status == Status.READY:
            return "●"
        if status == Status.STARTING:
            return "◐"
        return "◌"

    def _model_dot_glyph(self, key: str, medium: Optional[Medium] = None) -> str:
        """Single source of truth for a `server_manager` key's dot glyph —
        both the scoped dropdown's rows (`_populate_model_dropdown`) and the
        Model-door cards (`_build_model_card`) call this instead of each
        rolling their own health lookup, so the two surfaces can never
        disagree for the same key (mirrors the artgen panel's health-dot
        discipline documented in CLAUDE.md).

        When a `ModelStatusService` is injected it is the sole source: looks
        `key` up in the last-pushed `_status_snapshot`, defaulting an unknown
        key to OFF (never polled yet reads the same as "off"). When no
        service is injected, falls back to the pre-Task-2 boolean
        `_model_health` map — byte-identical to the old inline
        `"●" if running else "○"` logic, so the status_service=None path is
        unchanged.

        SP-3c-2: "animatediff" always reads READY ("●") — it's hardware-only
        with no server to start/stop/health-check (see `_scoped_model_keys`'s
        comment), so showing it as perpetually "offline" (the default for a
        key neither health source has ever heard of) would be actively
        misleading — it is, in fact, always ready to generate.

        SP-3 Task 3: a `_DETECTED_KEY_PREFIX` sentinel key ALSO always reads
        READY ("●") — same reasoning as animatediff: `_detected_model_key`
        (the only place that ever produces this key) already only returns
        one when `running_artgen_model()` reports something IS currently
        running, so there is nothing to poll and no other state it could be
        in. It is never looked up in `_status_snapshot`/`_model_health` (a
        raw sentinel string was never, and will never be, a real
        `server_manager.SERVERS`/health-map key).

        AnimateDiff-model-fix: an LLM-free artgen medium's self key (see
        `_scoped_model_keys`/`_populate_model_dropdown`) ALSO always reads
        READY ("●") for the same reason — it's a self-contained generator
        with no server to start/stop/health-check, so "offline" would be
        just as misleading as it is for the native "animatediff" key above.
        `medium` is an OPTIONAL param (only `_populate_model_dropdown`'s
        artgen-medium loop passes it) precisely so this check can confirm
        *this specific key* is that medium's own self-entry, not merely
        equal to some other medium's key by coincidence — every other caller
        (e.g. `_build_model_card`, which only ever passes real
        `server_manager.SERVERS` keys) is unaffected by leaving it `None`.
        """
        if key == "animatediff":
            return "●"
        if _is_detected_key(key):
            return "●"
        if (
            medium is not None
            and medium.source == "artgen"
            and not medium.uses_llm
            and key == medium.id
        ):
            return "●"
        if self._status_service is not None:
            status = self._status_snapshot.get(key, Status.OFF)
            return self._status_glyph(status)
        return "●" if self._model_health.get(key, False) else "○"

    def _on_status_snapshot(self, snap: dict) -> bool:
        """`GLib.idle_add` target for the service's `subscribe()` callback —
        runs on the main thread. Stores the fresh snapshot and re-renders
        both dot surfaces (scoped dropdown + Model-door cards) so neither one
        goes stale relative to the other. Returns `GLib.SOURCE_REMOVE`
        (`False`) since this fires once per pushed snapshot, not on a
        repeating `GLib` timer (mirrors `_apply_model_health`'s return)."""
        self._status_snapshot = dict(snap or {})
        if self._active_medium is not None:
            self._populate_model_dropdown(self._active_medium)
        self._refresh_model_door()
        return GLib.SOURCE_REMOVE

    def _on_unrealize(self, *_args) -> None:
        """Unsubscribe from the status service when this widget is torn down,
        so a long-lived `ModelStatusService` never keeps calling back into a
        destroyed `CreateView`. Guards both "no service was ever injected"
        and "already unsubscribed" (GTK can fire `unrealize` more than once
        in some teardown paths) by clearing `_status_unsub` to None right
        after calling it."""
        unsub = self._status_unsub
        if unsub is not None:
            self._status_unsub = None
            unsub()

    def _server_key_to_medium_id(self, key: str) -> Optional[str]:
        """Model door: map a `server_manager` key to the Medium id it implies.

        Reads `server_manager.SERVERS[key].capabilities` (e.g. `("video",)`,
        `("artgen",)`) against the CURRENT medium list from `mediums_fn()`
        (not a stale cache — a plugin could appear/disappear between calls).

        - A native capability ("video"/"image"/"animate") maps 1:1 to the
          identically-named native medium id — EXCEPT "animate": Task 2
          deleted the native `"animate"` medium (Wan2.2-Animate lives on the
          Video medium now, gated by `_update_animate_extras_visibility`), so
          there is no `native_ids` entry named "animate" for it to match.
          Without a special case, an Animate-capability key would fall
          through every rule below and return None — the dead-end this
          docstring's Finding-1 fix exists to close. Folds onto "video"
          instead, mirroring how `_classify_server_key_for_model_door`
          already files Animate under the "Video" model-door group.
        - "artgen" has no single matching medium id (each artgen generator is
          its own medium) — it maps to the FIRST artgen-sourced medium in the
          list, a deliberate "pick a generative default", not a precise
          per-model mapping.
        - Anything else (unknown key, or a capability with no medium at all —
          e.g. "prompt" for prompt-server) returns None. Callers must treat
          None as "this model doesn't map to a medium" and no-op, never guess.
        """
        sdef = server_manager.SERVERS.get(key)
        if sdef is None:
            return None

        try:
            mediums = list(self._mediums_fn() or [])
        except Exception:
            mediums = []

        native_ids = {m.id for m in mediums if m.source == "native"}
        first_artgen_id = next((m.id for m in mediums if m.source == "artgen"), None)

        for cap in sdef.capabilities:
            if cap in native_ids:
                return cap
            if cap == "animate" and "video" in native_ids:
                return "video"
            if cap == "artgen" and first_artgen_id is not None:
                return first_artgen_id
        return None

    # ── Create CTA ───────────────────────────────────────────────────────────

    def _build_cta_row(self) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("create-cta-row")

        btn = Gtk.Button(label="✨ Create")
        btn.add_css_class("create-cta-btn")
        btn.connect("clicked", self._on_cta_clicked)
        self._cta_btn = btn
        row.append(btn)

        # "Surprise me" lives here in the reserved button area (moved out of the
        # possibilities-wall header) — it drives the wall's `surprise()` to pick
        # a medium + seed the composer. Guarded: no wall -> no button.
        if getattr(self, "_possibilities", None) is not None:
            surprise_btn = Gtk.Button(label="✨ Surprise me")
            surprise_btn.add_css_class("create-surprise-btn")
            surprise_btn.set_tooltip_text("Pick a medium and idea for me")
            surprise_btn.connect("clicked", lambda _b: self._possibilities.surprise())
            self._surprise_btn = surprise_btn
            row.append(surprise_btn)
        else:
            self._surprise_btn = None

        # SP-3d-1: Theme Set — generates a coherent N-shot themed batch and
        # queues it for the active medium, migrated from ControlPanel's own
        # "🎬 Theme Set" button (never reimplemented — see `_on_theme_set`'s
        # docstring above). Migration-safe: no `on_theme_set` injected -> no
        # button at all, mirroring `inspire_fn`'s None-safety, so every
        # pre-SP-3d-1 test that never passes it sees a byte-identical CTA row.
        if self._on_theme_set is not None:
            theme_btn = Gtk.Button(label="\U0001f3ac Theme Set")
            theme_btn.add_css_class("create-theme-set-btn")
            theme_btn.set_tooltip_text(
                "Generate a coherent multi-shot themed batch and queue it "
                "for the active medium."
            )
            theme_btn.connect("clicked", self._on_theme_set_clicked)
            self._theme_set_btn = theme_btn
            row.append(theme_btn)
        else:
            self._theme_set_btn = None

        return row

    def _on_cta_clicked(self, _btn: Gtk.Button) -> None:
        if self._on_create is None or self._active_medium is None:
            return
        self._on_create(self._active_medium, self._collect_params())

    # ── Theme Set (SP-3d-1) ────────────────────────────────────────────────

    def _on_theme_set_clicked(self, _btn) -> None:
        """Fire `self._on_theme_set(medium, params)` for the active medium,
        showing a busy state on the button while MainWindow's theme-expansion
        + enqueue work runs (off the GTK main thread on the real seam).

        Fail-soft by construction, mirroring `_on_inspire_clicked`: no seam
        injected, no active medium yet, or already-in-flight are all no-ops;
        a synchronous exception from the seam itself (e.g. a thread failing
        to spawn) is caught here and surfaced through the result panel
        instead of crashing Create.
        """
        if (
            self._on_theme_set is None
            or self._active_medium is None
            or self._theme_generating
        ):
            return
        self._set_theme_generating(True)
        try:
            self._on_theme_set(self._active_medium, self._collect_params())
        except Exception as e:  # noqa: BLE001 - fail-soft, see docstring
            self.set_theme_error(str(e))

    def _set_theme_generating(self, generating: bool) -> None:
        """Toggle the Theme Set button's busy state. No-op if the button
        doesn't exist (migration-safe — mirrors `_set_inspire_generating`)."""
        self._theme_generating = generating
        btn = getattr(self, "_theme_set_btn", None)
        if btn is None:
            return
        if generating:
            btn.set_label("⏳ Thinking…")
            btn.set_sensitive(False)
        else:
            btn.set_label("\U0001f3ac Theme Set")
            btn.set_sensitive(True)

    def set_theme_queued(self, count: int, theme_label: str) -> None:
        """MainWindow's success seam (SP-3d-1) — called on the main thread
        once every shot the theme backend produced has been enqueued via the
        existing queue path. Only resets the button: the "N shots queued"
        message itself is MainWindow's own status bar, and the queued items
        show up in Create's own pending-queue strip (`refresh_queue`) exactly
        like any other queued job — no separate confirmation UI needed here.
        """
        self._set_theme_generating(False)

    def set_theme_error(self, msg: str) -> None:
        """MainWindow's failure seam (SP-3d-1) — resets the button and
        surfaces the error in the inline result panel, exactly like a failed
        Create CTA click already does (`_result_panel.show_error`)."""
        self._set_theme_generating(False)
        try:
            self._result_panel.show_error(f"Theme Set: {msg}")
        except Exception:
            pass

    def _collect_params(self) -> dict:
        """Delegate to the active medium's mounted `RoleZonePanel`, if one is
        mounted, then fold in the idea door's typed prompt + the Direction
        zone's applied modifier text (Task 7 / Task 6), and finally override
        "model" from the scoped dropdown (Task 6).

        Mediums without a ported panel yet (still showing the Task 3 stub)
        fall back to `{}`, matching Task 3's behavior exactly. A panel whose
        `collect()` raises degrades to `{}` too, rather than crashing the CTA
        click — `on_create` is never worth losing over a bad widget read.

        **Prompt assembly** (Task 6): the final `prompt` is the idea door's
        typed text, plus a trailing space and the active RoleZonePanel's
        `applied_modifier_text()`, but ONLY when that modifier text is
        non-empty — an untouched Direction zone must not glue a stray
        trailing space onto the prompt. `params["prompt"]` is added at all
        ONLY when the combined text is non-empty. This is deliberate, not an
        oversight: every Tasks 3-6 CTA test asserts an exact params dict with
        no "prompt" key (they never touch the entry or a modifier, so both
        are empty) — always injecting `"prompt": ""` would silently break
        every one of them.

        **Model override** (Task 6): `RoleZonePanel.collect()` returns the
        wrapped panel's OWN "model" value verbatim — but that panel's model
        dropdown is never shown (RoleZonePanel deliberately skips `kind ==
        "model"` fields, see that class's module comment), so its selection
        can never change from its built-in default. The scoped
        `_model_dropdown` is what the user actually sees and clicks; its
        current selection's canonical id (via `_model_dropdown_entries`,
        populated by `_populate_model_dropdown`) replaces "model" in the
        collected dict whenever one is available — e.g. for an artgen medium
        (no "model" key in `collect()` at all) there is nothing to override,
        so the dict is left exactly as `collect()` produced it.

        **Animate-needs fold** (SDD task-6-brief.md — a separate task from
        this module's own "Task 6" numbering above, despite the coincidental
        number): when `_animate_extras_visible_for(self._selected_model_key())`
        is True (i.e. the scoped dropdown's selection is the Animate model),
        `self._animate_extras.collect()`'s three keys
        (`reference_video_path`/`reference_image_path`/`animate_mode`) are
        merged in. For every other model this merge never runs, so `params`
        is byte-for-byte identical to pre-task-6 behavior — pinned by
        `tests/test_create_view_animate_reveal.py`'s collect-equality guard.
        """
        if self._active_panel is None:
            params = {}
        else:
            try:
                params = self._active_panel.collect()
            except Exception:
                params = {}

        if "model" in params:
            entries = getattr(self, "_model_dropdown_entries", [])
            idx = self._model_dropdown.get_selected()
            if 0 <= idx < len(entries):
                _key, canonical, _label = entries[idx]
                if canonical is not None:
                    params["model"] = canonical

        # Task 6 (SDD task-6-brief.md): fold the reveal-on-demand "Animate
        # needs" section's motion video / character image / mode into params
        # -- but ONLY when the active medium is "video" AND the scoped
        # dropdown's current selection IS the Animate model. Same two-part
        # gate as `_update_animate_extras_visibility` (see that method's
        # docstring for why the medium check matters -- a still-reachable
        # `AnimateParamPanel`-backed medium whose own id is "animate" also
        # resolves that scoped key, and its `collect()` already carries
        # these three keys itself; folding this section's on TOP of that
        # would silently clobber the user's real edits with this section's
        # untouched-empty defaults). For every other case
        # `self._animate_extras` is either absent (a bare test double, e.g.
        # `test_create_view.py`'s many `CreateView.__new__` fixtures) or
        # present-but-not-merged, so `params` stays byte-for-byte identical
        # to pre-Task-6 behavior -- pinned by
        # tests/test_create_view_animate_reveal.py's collect-equality guard.
        # A raising `.collect()` degrades to a no-op merge rather than
        # crashing the CTA click, matching every other fail-soft read above.
        animate_extras = getattr(self, "_animate_extras", None)
        active_medium = getattr(self, "_active_medium", None)
        if (
            animate_extras is not None
            and active_medium is not None
            and active_medium.id == "video"
            and _animate_extras_visible_for(self._selected_model_key())
        ):
            try:
                params = {**params, **animate_extras.collect()}
            except Exception:
                pass

        prompt_entry = getattr(self, "_prompt_entry", None)
        prompt_text = prompt_entry.get_text().strip() if prompt_entry is not None else ""

        modifier_text = ""
        if isinstance(self._active_panel, RoleZonePanel):
            try:
                modifier_text = (self._active_panel.applied_modifier_text() or "").strip()
            except Exception:
                modifier_text = ""

        if modifier_text:
            combined = f"{prompt_text} {modifier_text}".strip()
        else:
            combined = prompt_text

        if combined:
            params = {**params, "prompt": combined}

        return params


# ─────────────────────────────────────────────────────────────────────────────
# CreateResultPanel — in-place Create results (SDD Task 1,
# .superpowers/sdd/task-1-brief.md).
#
# A STANDALONE widget this task: it is not constructed by `CreateView`, not
# mounted in `main_window.py`, and does not touch generation. Wiring it into
# the real Create flow (replacing/augmenting the legacy PendingCard/gallery
# hand-off) is Tasks 2-4. This file only has to render one current result
# (pending / finished / error) plus a capped "recents" strip so the user
# doesn't lose track of what they just made without leaving the Create
# surface — the same job `PendingCard`/`GenerationCard` do for the gallery,
# just inline.
#
# Elapsed-timer discipline mirrors `main_window.PendingCard` exactly: a
# `GLib.timeout_add(1000, ...)` ticks a "Ns elapsed" label while pending, and
# is cancelled via `GLib.source_remove` on EVERY state transition (finished,
# error, or an explicit `clear()`) — never left running past the state that
# started it, per CLAUDE.md's GTK-threading discipline.
# ─────────────────────────────────────────────────────────────────────────────

_RECENTS_MAX = 6

# Extension sets used to classify a result artifact's kind for rendering.
# Task 2 (media-showcase-everywhere, docs/superpowers/specs/
# 2026-07-17-media-showcase-everywhere-design.md) widened this from
# image/video/gif/text to the full "one small stable vocabulary per media
# type" contract: every artgen output format gets its own kind so
# `_build_artifact_widget` can pick the right renderer (a raster/vector
# Gtk.Picture, or a WebKit reading view built from `artgen_render`) instead
# of falling through to the "Result file not found" placeholder for a
# perfectly valid, existing file.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_VIDEO_EXTS = {".mp4"}
_GIF_EXTS = {".gif"}
_SVG_EXTS = {".svg"}
_ANSI_EXTS = {".ans"}
_PALETTE_EXTS = {".json"}
_CODE_EXTS = {".py"}
_TEXT_EXTS = {".txt", ".md"}


def _artifact_kind(path: str, generator_type: "Optional[str]" = None) -> str:
    """Classify an artifact path's rendering kind.

    Returns one of "image" | "video" | "gif" | "svg" | "ansi" | "palette" |
    "code" | "text" | "unknown". Never raises — an empty path maps straight
    to "unknown".

    Extension is the PRIMARY signal (it never lies about what format the
    file actually is — the ansi generator always writes `.ans`, palette
    always writes `.json`, codeart always writes `.py`, etc. — see each
    generator's own `output_ext`). Only when the extension is missing or not
    one of the recognised set do we fall back to `generator_type`, resolved
    through `create_mediums._ARTGEN_KIND` (the SAME table that already
    classifies a generator's Medium chip) collapsed to this function's finer
    vocabulary. This is deliberately NOT a second hand-maintained
    generator-name -> kind table — reusing `_ARTGEN_KIND` means a new
    generator can never register a Medium chip kind that this function
    doesn't already agree with, even in the fallback path.
    """
    if not path:
        return "unknown"
    ext = Path(path).suffix.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _GIF_EXTS:
        return "gif"
    if ext in _SVG_EXTS:
        return "svg"
    if ext in _ANSI_EXTS:
        return "ansi"
    if ext in _PALETTE_EXTS:
        return "palette"
    if ext in _CODE_EXTS:
        return "code"
    if ext in _TEXT_EXTS:
        return "text"

    # Extension unrecognised (or absent) — fall back to the generator's own
    # declared coarse kind so a known generator with an unusual/missing
    # extension still gets SOME rich rendering rather than "unknown".
    if generator_type:
        coarse = _ARTGEN_KIND.get(generator_type)
        if coarse in ("text", "image", "gif"):
            return coarse
    return "unknown"


def _build_reading_webview(html: str) -> Gtk.Widget:
    """Build a WebKit "reading" view pre-loaded with `html`.

    Mirrors `ArtgenDetail._load_html`/`_on_webview_realize`'s realize-
    deferral: `WebKit.WebView.load_html()` called before the widget is
    realized is a silent no-op that leaves the view permanently blank.
    `ArtgenDetail` handles this with a mutable `_pending_html` slot because
    it reuses ONE persistent WebView across every record; here a fresh
    WebView is built per call, so the pending HTML can simply live in this
    closure instead of an instance attribute — same fix, simpler because
    the lifetime is 1:1 with the widget.
    """
    webview = WebKit.WebView()
    try:
        webview.get_settings().set_enable_javascript(False)
    except Exception:
        pass  # never let a settings lookup failure block rendering
    webview.set_hexpand(True)
    webview.set_vexpand(True)
    webview.add_css_class("create-result-reading")

    if webview.get_realized():
        webview.load_html(html, "about:blank")
    else:
        webview.connect("realize", lambda _w: webview.load_html(html, "about:blank"))
    return webview


# Kind -> recents-strip chip label for kinds whose label doesn't depend on
# `generator_type`. "text" is handled specially in `_recent_chip_label`
# below (verse gets its own "Verse" label instead of the generic "TXT").
_CHIP_LABELS: dict[str, str] = {
    "image": "IMG",
    "video": "VID",
    "gif": "GIF",
    "svg": "SVG",
    "ansi": "ANSI",
    "palette": "Palette",
    "code": "Code",
    "text": "TXT",
}


def _recent_chip_label(kind: str, generator_type: "Optional[str]") -> str:
    """Compact type label for a recents-strip chip. Never returns a bare
    "?" for any of this module's recognised kinds — only a truly "unknown"
    kind (no extension match, no generator-type fallback) falls through to
    that. "verse" gets its own label since it's the one generator whose
    identity (a poem) is more informative to the user than its generic
    kind ("text", shared with freeform)."""
    if kind == "text" and generator_type == "verse":
        return "Verse"
    return _CHIP_LABELS.get(kind, "?")


class CreateResultPanel(Gtk.Box):
    """Renders a single "current result" inline, plus a newest-first recents
    strip capped at `_RECENTS_MAX`.

    Three states drive the current-result area — "empty" (nothing generated
    yet), "pending" (spinner + elapsed seconds + the prompt that's cooking),
    "finished" (the artifact itself), "error" (a message) — exposed via the
    `state` test seam so callers/tests never have to infer state from widget
    structure. `recents_count()` is the other test seam: the number of items
    currently in the capped strip.

    Every external input is a plain value (`prompt: str`, `record`, a
    message string) — this widget never touches `GenerationWorker`,
    `api_client`, or `server_manager`, so it is fully unit-testable with
    fakes/fixtures and importing it triggers no network or subprocess I/O.
    """

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        _apply_css()
        self.add_css_class("create-result-panel")

        self._state = "empty"
        self._timer_id: Optional[int] = None
        self._pending_start: float = 0.0
        # Newest-first list of `history_store.GenerationRecord`-like objects
        # (only `.prompt`/`.thumbnail_path`/`.media_file_path` are read, so a
        # duck-typed stand-in works too) — capped at `_RECENTS_MAX` by
        # `_push_recent`, which drops the OLDEST (list tail) entry once full.
        self._recents: list = []

        # References populated only while pending — read by `show_progress`
        # and `_tick`; absent (via getattr default) outside the pending state.
        self._pending_status_lbl: Optional[Gtk.Label] = None
        self._pending_elapsed_lbl: Optional[Gtk.Label] = None

        self._current_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._current_box.add_css_class("create-result-current")
        self.append(self._current_box)

        # SP-3c-4: this-session pending-queue display, between the current
        # result and the recents strip -- see `set_queue`. Empty (and
        # invisible via `set_queue`'s own visibility toggle) until MainWindow
        # first calls `set_queue` with a non-empty list.
        self._queue_items: list = []
        self._on_queue_cancel: "Optional[Callable[[int], None]]" = None
        self._queue_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._queue_box.add_css_class("create-result-queue")
        self._queue_box.set_visible(False)
        self.append(self._queue_box)

        self._recents_flow = Gtk.FlowBox()
        self._recents_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._recents_flow.set_max_children_per_line(_RECENTS_MAX)
        self._recents_flow.add_css_class("create-result-recents")
        self.append(self._recents_flow)

        self._show_empty()

    # ── Test seams ───────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    def recents_count(self) -> int:
        return len(self._recents)

    def queue_count(self) -> int:
        """Test seam: number of items currently rendered in the pending-queue
        display (mirrors `recents_count()`)."""
        return len(self._queue_items)

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _clear_current(self) -> None:
        """Tear down every child of the current-result box. Called at the
        start of every state transition so each state's `_show_*`/`show_*`
        method starts from a clean slate (mirrors `_swap_panel`'s tear-down-
        then-rebuild pattern elsewhere in this file)."""
        child = self._current_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._current_box.remove(child)
            child = nxt

    def _stop_timer(self) -> None:
        """Cancel the pending elapsed-timer, if one is running. MUST be
        called before every state transition away from "pending" — mirrors
        `PendingCard.stop_timer()`."""
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    # ── State: empty ─────────────────────────────────────────────────────────

    def _show_empty(self) -> None:
        self._stop_timer()
        self._clear_current()
        self._state = "empty"
        self._pending_status_lbl = None
        self._pending_elapsed_lbl = None

        label = Gtk.Label(label="Nothing yet — press Create to make something.")
        label.add_css_class("create-result-empty-label")
        label.set_wrap(True)
        self._current_box.append(label)

    def clear(self) -> None:
        """Reset the current-result area to "empty". Does NOT touch the
        recents strip — recents are a history of what's been made, independent
        of whatever the current-result area happens to be showing right now."""
        self._show_empty()

    # ── State: pending ───────────────────────────────────────────────────────

    def show_pending(self, prompt: str, medium=None) -> None:
        """Show the pending state: spinner + elapsed seconds + the prompt.

        `medium` (a `create_mediums.Medium` or `None`) only affects the
        header text (its icon/label, when present) — never required, since
        Create's "idea" door can generate without an explicit medium choice.
        """
        self._stop_timer()
        self._clear_current()
        self._state = "pending"
        self._pending_start = time.monotonic()

        spinner = Gtk.Spinner()
        spinner.set_spinning(True)
        spinner.add_css_class("create-result-spinner")
        spinner.set_halign(Gtk.Align.CENTER)
        self._current_box.append(spinner)

        icon = getattr(medium, "icon", "") if medium is not None else ""
        medium_label = getattr(medium, "label", "") if medium is not None else ""
        if icon or medium_label:
            header_text = f"{icon} Generating {medium_label}…".strip()
        else:
            header_text = "Generating…"
        self._pending_status_lbl = Gtk.Label(label=header_text)
        self._pending_status_lbl.add_css_class("create-result-status")
        self._pending_status_lbl.set_wrap(True)
        # Bound + center: progress messages (`show_progress`) change length as a
        # job runs; without a max-width-chars cap the label's natural width
        # tracks the text and jitters the whole card's width every update. Cap
        # it and center it so a changing message re-wraps in place instead of
        # resizing the pane.
        self._pending_status_lbl.set_max_width_chars(40)
        self._pending_status_lbl.set_justify(Gtk.Justification.CENTER)
        self._pending_status_lbl.set_halign(Gtk.Align.CENTER)
        self._current_box.append(self._pending_status_lbl)

        self._pending_elapsed_lbl = Gtk.Label(label="0s elapsed")
        self._pending_elapsed_lbl.add_css_class("create-result-elapsed")
        # Centered so the per-second "…s elapsed" text re-centers within the
        # (now fixed-width) card instead of nudging its layout.
        self._pending_elapsed_lbl.set_halign(Gtk.Align.CENTER)
        self._current_box.append(self._pending_elapsed_lbl)

        if prompt:
            prompt_lbl = Gtk.Label(label=prompt)
            prompt_lbl.add_css_class("create-result-prompt")
            prompt_lbl.set_wrap(True)
            # Cap the NATURAL width. A wrapping Gtk.Label with no max-width-chars
            # still reports its full single-line text as its natural width, so a
            # long prompt balloons the result pane's natural width to ~1000px —
            # which, added to a wide form (e.g. AnimateDiff's expanded options),
            # overflows the two-pane FlowBox line and kicks the result pane BELOW
            # the form once generation fills in the prompt. Bounding it keeps the
            # pane narrow enough to stay side-by-side; hexpand still lets it fill
            # the real width it's allocated.
            prompt_lbl.set_max_width_chars(48)
            prompt_lbl.set_xalign(0.0)
            self._current_box.append(prompt_lbl)

        self._timer_id = GLib.timeout_add(1000, self._tick)

    def _tick(self) -> bool:
        """GLib.timeout_add callback — runs on the main thread, so touching
        widgets directly is safe (mirrors `PendingCard._tick`). Returns True
        to keep firing every second until `_stop_timer` cancels it."""
        elapsed = int(time.monotonic() - self._pending_start)
        m, s = divmod(elapsed, 60)
        text = f"{m}m {s:02d}s elapsed" if m else f"{s}s elapsed"
        if self._pending_elapsed_lbl is not None:
            self._pending_elapsed_lbl.set_label(text)
        return True

    def show_progress(self, message: str) -> None:
        """Update the pending status text in place. A no-op outside the
        pending state — a stray progress message arriving after the job has
        already finished/errored/been cleared must not resurrect a pending
        label that no longer exists."""
        if self._state != "pending" or self._pending_status_lbl is None:
            return
        self._pending_status_lbl.set_label(message)

    # ── State: error ─────────────────────────────────────────────────────────

    def show_error(self, message: str) -> None:
        self._stop_timer()
        self._clear_current()
        self._state = "error"
        self._pending_status_lbl = None
        self._pending_elapsed_lbl = None

        label = Gtk.Label(label=f"⚠ {message}")
        label.add_css_class("create-result-error-label")
        label.set_wrap(True)
        self._current_box.append(label)

    # ── State: finished ──────────────────────────────────────────────────────

    def show_finished(self, record) -> None:
        """Render `record`'s artifact inline and prepend it to the recents
        strip (newest first, capped at `_RECENTS_MAX` — see `_push_recent`).
        """
        self._stop_timer()
        self._render_record(record)
        self._push_recent(record)

    def _render_record(self, record) -> None:
        """Render `record` into the current-result area and set state to
        "finished". Split out from `show_finished` so `_on_recent_clicked`
        can re-render a past result WITHOUT re-adding it to the recents strip
        (that would let one recent's repeated clicks spam duplicates)."""
        self._clear_current()
        self._state = "finished"
        self._pending_status_lbl = None
        self._pending_elapsed_lbl = None
        self._current_box.append(self._build_artifact_widget(record))

    def _build_artifact_widget(self, record) -> Gtk.Widget:
        """Render `record`'s primary artifact by kind (derived from its file
        extension via `_artifact_kind`, never from `media_type` — a "video"
        record could theoretically point at a still image after a forge
        transform, etc.). A missing/unreadable/unrecognised path ALWAYS
        degrades to an honest placeholder label — never a broken-image icon
        (the brief's explicit requirement)."""
        path = getattr(record, "media_file_path", "") or ""
        gen_type = getattr(record, "generator_type", None)
        kind = _artifact_kind(path, gen_type)
        exists = bool(path) and Path(path).exists()

        if kind in ("image", "svg") and exists:
            try:
                pic = Gtk.Picture.new_for_filename(path)
                pic.set_can_shrink(True)
                pic.add_css_class("create-result-picture")
                return pic
            except Exception:
                pass  # fall through to the placeholder below

        if kind == "gif" and exists:
            # Animate the ORIGINAL .gif inline — never the thumbnail. Before
            # this fix `make_thumbnail` text-rendered every non-svg
            # extension's raw bytes (see artgen_thumb.py / CLAUDE.md's
            # root-cause note), so a gif's "thumbnail" could be garbage; even
            # now that it's a real PIL first-frame render, the whole point
            # of this branch is to show the ARTIFACT ITSELF moving, not a
            # static stand-in for it.
            #
            # Lazy import: artgen_gallery has no import-time dependency on
            # create_view (verified — it only imports artgen_detail/
            # media_store/gallery_layout), so this is safe at module scope
            # too, but kept lazy/local so a gif is the only code path that
            # pays for pulling in the gallery module.
            try:
                from artgen_gallery import _AnimatedGifWidget
                anim_widget = _AnimatedGifWidget(path)
                # _AnimatedGifWidget swallows load failures internally and
                # simply leaves its paintable unset — detect that case here
                # so we can degrade to the placeholder instead of showing a
                # blank tile. (It self-manages its GLib.timeout_add timer via
                # its own "unrealize" handler, so no timer bookkeeping is
                # needed on this side — _clear_current's container removal
                # triggers that cleanup the same way artgen_gallery's own
                # hover-swap does.)
                if anim_widget.get_paintable() is not None:
                    anim_widget.add_css_class("create-result-picture")
                    return anim_widget
            except Exception:
                pass
            label = Gtk.Label(label="\U0001f3ac Result ready — open to view")
            label.add_css_class("create-result-placeholder")
            return label

        if kind == "video" and exists:
            # v1: a poster/thumbnail stands in for the real lazy-stream+loop
            # video widget (GenerationCard's pattern in main_window.py) — an
            # acceptable v1 per the brief. A real inline player is a
            # reasonable follow-up once this panel is actually wired in.
            thumb_path = getattr(record, "thumbnail_path", "") or ""
            if thumb_path and Path(thumb_path).exists():
                try:
                    pic = Gtk.Picture.new_for_filename(thumb_path)
                    pic.set_can_shrink(True)
                    pic.add_css_class("create-result-picture")
                    return pic
                except Exception:
                    pass
            label = Gtk.Label(label="\U0001f3ac Result ready — open to view")
            label.add_css_class("create-result-placeholder")
            return label

        if kind in ("ansi", "palette", "code", "text") and exists:
            # Every "reading" kind renders through `artgen_render`'s themed
            # HTML builders in a WebKit reading view — mirrors
            # `ArtgenDetail._render`'s dispatch exactly, just built fresh per
            # widget instead of driving one persistent WebView. Never a raw
            # `Gtk.TextView` of escape codes / unformatted prose (the bug
            # this task fixes).
            try:
                raw = Path(path).read_text(encoding="utf-8", errors="replace")
            except Exception:
                raw = ""
            params = getattr(record, "params_dict", None) or {}
            doc_title = artgen_render.derive_title(gen_type or "", params)

            if kind == "ansi":
                html = artgen_render.ansi_to_html(raw)
            elif kind == "palette":
                try:
                    data = json.loads(raw)
                    html = (
                        artgen_render.palette_to_html(data)
                        if isinstance(data, dict) and "colors" in data
                        else artgen_render.md_to_html(raw, title=doc_title)
                    )
                except Exception:
                    html = artgen_render.md_to_html(raw, title=doc_title)
            elif kind == "code":
                html = artgen_render.code_to_html(raw, title=doc_title)
            else:  # "text" — .txt/.md, verse gets its centered-poem CSS
                html = artgen_render.md_to_html(
                    raw, title=doc_title, verse_mode=(gen_type == "verse")
                )

            webview = _build_reading_webview(html)
            return webview

        # Missing file, unreadable file, or an unrecognised extension — an
        # honest placeholder, distinguishing "no path at all" from "path set
        # but the file isn't there" purely for a clearer message.
        if not path:
            msg = "No result file."
        else:
            msg = "Result file not found."
        label = Gtk.Label(label=msg)
        label.add_css_class("create-result-placeholder")
        return label

    # ── Pending-queue display (SP-3c-4) ─────────────────────────────────────

    def set_queue(self, items: "list", on_cancel: "Callable[[int], None]") -> None:
        """Render the this-session pending Create-job queue: one row per
        item (its `.prompt`, duck-typed — a real `main_window._QueueItem`
        or any stand-in with a `.prompt` attribute both work) with a cancel
        (X) button that calls `on_cancel(index)` -- MainWindow wires this to
        `_on_queue_remove`, which pops `self._queue[index]`, persists, and
        calls back into this same method via `_refresh_create_queue_display`
        so the display reflects the post-removal queue.

        Hidden entirely (`set_visible(False)`) when *items* is empty -- an
        idle Create surface with no queue shows nothing extra, matching the
        legacy queue box's own `has = bool(self._queue)` visibility rule.
        Called fresh on every queue mutation (enqueue/cancel/drain/restore),
        so it always rebuilds from scratch rather than diffing.
        """
        self._queue_items = list(items)
        self._on_queue_cancel = on_cancel
        self._rebuild_queue_box()

    def _rebuild_queue_box(self) -> None:
        child = self._queue_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._queue_box.remove(child)
            child = nxt

        for i, item in enumerate(self._queue_items):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            row.add_css_class("create-result-queue-row")

            prompt = getattr(item, "prompt", "") or ""
            short = prompt if len(prompt) <= 50 else prompt[:50] + "…"
            lbl = Gtk.Label(label=f"{i + 1}. {short}" if short else f"{i + 1}. (queued)")
            lbl.set_xalign(0.0)
            lbl.set_hexpand(True)
            lbl.set_wrap(True)
            lbl.add_css_class("create-result-queue-prompt")
            if prompt:
                lbl.set_tooltip_text(prompt)
            row.append(lbl)

            cancel_btn = Gtk.Button(label="✕")
            cancel_btn.add_css_class("create-result-queue-cancel-btn")
            cancel_btn.set_tooltip_text("Remove from queue")
            cancel_btn.connect("clicked", lambda _b, idx=i: self._on_cancel_clicked(idx))
            row.append(cancel_btn)

            self._queue_box.append(row)

        self._queue_box.set_visible(bool(self._queue_items))

    def _on_cancel_clicked(self, index: int) -> None:
        if self._on_queue_cancel is not None:
            self._on_queue_cancel(index)

    # ── Recents strip ────────────────────────────────────────────────────────

    def _push_recent(self, record) -> None:
        """Prepend `record` to the recents list (newest first) and drop the
        oldest entry once past `_RECENTS_MAX`, then rebuild the strip."""
        self._recents.insert(0, record)
        if len(self._recents) > _RECENTS_MAX:
            del self._recents[_RECENTS_MAX:]
        self._rebuild_recents_flow()

    def _rebuild_recents_flow(self) -> None:
        child = self._recents_flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._recents_flow.remove(child)
            child = nxt
        for rec in self._recents:
            self._recents_flow.append(self._build_recent_card(rec))

    def _build_recent_card(self, record) -> Gtk.Widget:
        """One clickable recent: a small thumbnail (image/video/gif/svg) or a
        kind-labeled chip (ansi/palette/code/text, or anything unreadable) —
        clicking it re-renders that record in the current-result area. Never
        a bare "?" chip for a recognised kind — see `_recent_chip_label`."""
        btn = Gtk.Button()
        btn.add_css_class("create-result-recent-btn")
        btn.set_tooltip_text(getattr(record, "prompt", "") or "")

        media_path = getattr(record, "media_file_path", "") or ""
        gen_type = getattr(record, "generator_type", None)
        kind = _artifact_kind(media_path, gen_type)
        thumb_path = getattr(record, "thumbnail_path", "") or ""
        if not thumb_path and kind in ("image", "svg"):
            thumb_path = media_path  # image/svg have no separate thumbnail file

        if thumb_path and Path(thumb_path).exists() and kind in ("image", "video", "gif", "svg"):
            try:
                pic = Gtk.Picture.new_for_filename(thumb_path)
                pic.set_can_shrink(True)
                pic.set_size_request(64, 36)
                btn.set_child(pic)
                btn.connect("clicked", lambda _b, r=record: self._on_recent_clicked(r))
                return btn
            except Exception:
                pass  # fall through to the label chip below

        btn.set_label(_recent_chip_label(kind, gen_type))
        btn.connect("clicked", lambda _b, r=record: self._on_recent_clicked(r))
        return btn

    def _on_recent_clicked(self, record) -> None:
        """Re-render a past recent in the current-result area. Does NOT call
        `show_finished` (that would re-insert/reorder the recents strip on
        every click) — it goes straight through `_render_record`, same as
        `show_finished` does internally, just without the recents side
        effect."""
        self._stop_timer()
        self._render_record(record)
