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
video/animate/image/artgen children) — it is mounted as a new, not-yet-
reachable `_gallery_stack` child ("create") this task. The loop nav's ✨
Create verb keeps routing to the old UI until every medium panel is ported
(see docs/superpowers/plans/2026-07-13-create-surface.md, Task 8). Generation
itself (`GenerationWorker`/`api_client`) is completely untouched by this file.

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

import threading
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

import gtk_layout  # noqa: E402
import server_manager  # noqa: E402
from create_mediums import Medium, default_mediums  # noqa: E402
from create_param_panels import (  # noqa: E402
    _ANIMATE_MODEL_ID,
    _IMAGE_MODEL_IDS,
    _VIDEO_MODEL_IDS,
    AnimateParamPanel,
    ArtgenParamPanel,
    ImageParamPanel,
    RoleZonePanel,
    VideoParamPanel,
)

# Video-only alias: server_manager's key for the Wan2.2 text-to-video service
# is "wan2.2", but VideoParamPanel's own internal short key (and therefore
# `_VIDEO_MODEL_IDS`'s key) is "wan2" — a pre-existing naming mismatch between
# the two modules (VideoParamPanel predates the scoped dropdown). Image's
# server_manager keys ("flux"/"sdxl"/"z-image-turbo"/"motif") already match
# `_IMAGE_MODEL_IDS`'s keys exactly, and Animate has exactly one native model
# id (`_ANIMATE_MODEL_ID`, no dropdown/dict at all) — so this one-entry alias
# is the only translation table the scoped dropdown needs.
_VIDEO_SERVER_KEY_ALIAS: "dict[str, str]" = {"wan2.2": "wan2"}


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
    choices (e.g. "skyreels" — VideoParamPanel deliberately excludes it, see
    that class's module comment) or for a non-native medium (artgen has no
    "model" field at all) — callers must treat `None` as "don't offer this
    key for this medium", never guess a fallback.
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
# group by resolving the Medium it implies (`_server_key_to_medium_id`) and
# looking at THAT Medium's own `kind` field, rather than a hardcoded
# key->group table — a new server declaring an existing capability, or a new
# artgen generator, lands in the right section with zero changes here.
# "gif" (native Animate's own kind, and artgen's animatediff) maps to
# "Animate" -- the app's only motion/character-animation medium is the
# "gif"-kind one. A key with no medium mapping at all (e.g. "prompt-server",
# capability "prompt") falls back to "Text" -- every key that hits this
# fallback today is in fact a chat/LLM/prompt service.
_MEDIUM_KIND_TO_MODEL_DOOR_GROUP: "dict[str, str]" = {
    "image": "Image",
    "video": "Video",
    "gif": "Animate",
    "text": "Text",
}

# Fixed display order for the model door's sections — independent of dict
# iteration order, and stable regardless of which groups end up non-empty for
# the current mediums_fn()/SERVERS combination.
_MODEL_DOOR_GROUP_ORDER: "tuple[str, ...]" = ("Image", "Video", "Animate", "Text")


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

/* -- Create CTA --------------------------------------------------------------- */
.create-cta-row {
    padding-top: 4px;
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
.create-model-door {
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
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        _apply_css()
        self.add_css_class("create-view")

        self._mediums_fn = mediums_fn or default_mediums
        self._health_fn = health_fn or server_manager.status_all
        self._on_create = on_create
        self._on_inspiration = on_inspiration

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

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content.add_css_class("create-view-content")
        content.append(self._build_doors_row())
        content.append(self._build_idea_row())
        content.append(self._build_model_door_row())
        content.append(self._build_chip_row())  # fires _select_medium synchronously
        content.append(self._build_model_dropdown_row())
        content.append(self._panel_host)
        content.append(self._build_cta_row())

        # Width clamp (fix: content sprawling edge-to-edge / overflowing on a
        # wide window) — `self` stays a plain Gtk.Box so every existing caller
        # (main_window.py mounts `self._create_view` directly) is unaffected;
        # only what's INSIDE it is now capped to a comfortable column.
        self.append(gtk_layout.wrap_centered(content))

        self._refresh_model_health_async()

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
        self._idea_row = row
        return row

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

        Reuses `_server_key_to_medium_id` (capability -> Medium id) plus the
        resolved Medium's own `kind` field (`_MEDIUM_KIND_TO_MODEL_DOOR_
        GROUP`) rather than hardcoding a key->group table — a new server
        declaring an existing capability, or a new artgen generator, is
        classified correctly with zero changes here.

        Falls back to "Text" when the key doesn't map to any current medium
        at all (e.g. "prompt-server", capability "prompt" has no matching
        medium) — every key that hits this fallback today is in fact a
        chat/LLM/prompt service.
        """
        medium_id = self._server_key_to_medium_id(key)
        if medium_id is not None:
            try:
                mediums = list(self._mediums_fn() or [])
            except Exception:
                mediums = []
            medium = next((m for m in mediums if m.id == medium_id), None)
            if medium is not None:
                return _MEDIUM_KIND_TO_MODEL_DOOR_GROUP.get(medium.kind, "Text")
        return "Text"

    def _model_door_groups(self) -> "dict[str, list[str]]":
        """Every `server_manager.SERVERS` key, classified into Image/Video/
        Animate/Text groups (see `_classify_server_key_for_model_door`).

        Empty groups are omitted from the returned dict — `_build_model_door`
        relies on this to skip empty sections entirely, per the task's "omit
        empty groups" requirement.
        """
        groups: "dict[str, list[str]]" = {g: [] for g in _MODEL_DOOR_GROUP_ORDER}
        for key in server_manager.SERVERS:
            group = self._classify_server_key_for_model_door(key)
            groups.setdefault(group, [])
            groups[group].append(key)
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

        Health reuses `self._model_health` — the SAME source Task 6's scoped
        dropdown reads (`_populate_model_dropdown`) — so a card's dot can
        never disagree with the dropdown's dot for the same server key (the
        same "single source of truth" discipline CLAUDE.md documents for the
        artgen panel's own health dot).
        """
        sdef = server_manager.SERVERS.get(key)
        label_text = sdef.label if sdef is not None else key
        running = self._model_health.get(key, False)

        btn = Gtk.Button()
        btn.add_css_class("create-model-chip")
        btn.add_css_class("create-model-chip-on" if running else "create-model-chip-off")
        btn.set_tooltip_text(label_text)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dot = Gtk.Label(label="●" if running else "○")
        dot.add_css_class("create-model-dot-on" if running else "create-model-dot-off")
        content.append(dot)

        label = Gtk.Label(label=label_text)
        label.add_css_class("create-model-label")
        content.append(label)

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
        currently scoped entries — e.g. a canonical-id-excluded key
        (`_canonical_model_id_for` deliberately excludes "skyreels", see that
        function's docstring) still routes to the Video medium correctly; it
        just can't be pre-selected in the dropdown.
        """
        entries = getattr(self, "_model_dropdown_entries", [])
        for idx, (entry_key, _canonical, _label) in enumerate(entries):
            if entry_key == key:
                self._model_dropdown.set_selected(idx)
                return

    def _activate_model_card(self, key: str) -> None:
        """Model door card click: select that model's medium and return to
        the Idea door, pre-scoped to this model.

        Reuses existing routing rather than reimplementing it:
        `_server_key_to_medium_id` (capability -> Medium id) resolves the
        Medium; activating its chip button fires the SAME
        `_select_medium` -> `_swap_panel` path a manual chip click does
        (repopulating the scoped dropdown for the new medium as a side
        effect), then the Idea door toggle is activated
        (`_set_entry_mode("idea")`). Finally the scoped dropdown is
        pre-selected to *key* when practical (`_preselect_model_key`) — "when
        practical" because a canonical-id-excluded key has nothing to
        pre-select, which is fine.

        A key with no medium mapping at all (`_server_key_to_medium_id`
        returns None — e.g. "prompt-server") is a deliberate no-op: there is
        no medium to route to.
        """
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
            mediums = list(self._mediums_fn() or [])
        except Exception:
            mediums = []

        first_btn: Optional[Gtk.ToggleButton] = None
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

        if first_btn is not None:
            first_btn.set_active(True)  # fires _select_medium via "toggled"

        return row

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
            return

        if medium.source == "artgen" and medium.generator:
            panel = ArtgenParamPanel(medium.generator)
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
        """
        medium = medium if medium is not None else self._active_medium
        if medium is None:
            return []
        cap = "artgen" if medium.source == "artgen" else medium.id
        return [sdef.key for sdef in server_manager.servers_for_capability(cap)]

    def _populate_model_dropdown(self, medium: Medium) -> None:
        """Rebuild `self._model_dropdown` to list ONLY *medium*'s own models.

        For a native medium with a real model field (image/video/animate),
        an entry is included only when `_canonical_model_id_for` resolves a
        value for it — this keeps every SELECTABLE entry able to produce a
        real "model" value in `_collect_params` (see that function and
        `_canonical_model_id_for`'s docstring for why e.g. "skyreels" is
        correctly absent from the video dropdown). For an artgen medium (no
        "model" field at all) every scoped key is listed for information —
        selecting one has no effect on `collect()`.

        Health dots reuse `self._model_health` (kept fresh by
        `_refresh_model_health_async`/`_apply_model_health`) — "if practical,
        else just labels" per the task brief; a key absent from the health
        map (never checked yet) just shows the "offline" dot.
        """
        is_native_with_model = medium.source == "native" and medium.id in (
            "image", "video", "animate",
        )

        entries: "list[tuple]" = []
        labels: "list[str]" = []
        for key in self._scoped_model_keys(medium):
            canonical = _canonical_model_id_for(medium, key)
            if is_native_with_model and canonical is None:
                continue
            sdef = server_manager.SERVERS.get(key)
            label_text = sdef.label if sdef is not None else key
            running = self._model_health.get(key, False)
            dot = "●" if running else "○"
            labels.append(f"{dot} {label_text}")
            entries.append((key, canonical, label_text))

        if not entries:
            labels = ["No models available"]
            entries = [(None, None, "No models available")]

        self._model_dropdown_entries = entries
        self._model_dropdown.set_model(Gtk.StringList.new(labels))
        self._model_dropdown.set_selected(0)

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

    def _server_key_to_medium_id(self, key: str) -> Optional[str]:
        """Model door: map a `server_manager` key to the Medium id it implies.

        Reads `server_manager.SERVERS[key].capabilities` (e.g. `("video",)`,
        `("artgen",)`) against the CURRENT medium list from `mediums_fn()`
        (not a stale cache — a plugin could appear/disappear between calls).

        - A native capability ("video"/"image"/"animate") maps 1:1 to the
          identically-named native medium id.
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
        return row

    def _on_cta_clicked(self, _btn: Gtk.Button) -> None:
        if self._on_create is None or self._active_medium is None:
            return
        self._on_create(self._active_medium, self._collect_params())

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
