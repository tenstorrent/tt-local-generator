# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
CreateView — the unified generation surface's shell (Create-surface plan,
Task 3: docs/superpowers/specs/2026-07-13-create-surface-design.md).

Replaces the four medium tabs (Video / Animate / Image / Generative Art) with
ONE surface where the medium is a *chip*, not a top-level division. This
module builds the shell — three doors in (idea / model / inspiration), the
medium-chip row, a per-medium param-panel host, the live-model strip, and the
Create CTA. Per-type param panels (real image/video/animate/artgen controls)
arrived across Tasks 4-6; Task 4 ported the IMAGE medium to a real
`ImageParamPanel`; Task 5 ported VIDEO and ANIMATE to
`VideoParamPanel`/`AnimateParamPanel`; this task (6) ports every artgen
generator medium (verse/ansi/landscape/…) to `ArtgenParamPanel` — one class,
parameterized by generator name, that introspects the generator's own
`add_args` (all panel classes live in `create_param_panels.py`). Every medium
the Create surface can offer now has a real panel — the plain Task 3 stub
label is now dead code kept only as a fallback for a hypothetical future
medium kind.

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
  - **Model**: the live-model strip (`_apply_model_strip`) stops being a
    passive readout — every entry is now a clickable `Gtk.Button` card.
    Clicking one (running or not — see `_on_model_card_clicked`) resolves the
    server's medium via `_server_key_to_medium_id` (its
    `server_manager.SERVERS[key].capabilities` matched against the current
    medium list) and activates that medium's chip, mounting its panel — "it's
    a video model -> you're making video." A not-yet-running model is still
    one tap (starting it for real is out of scope this task, see
    task-7-brief.md); its card is honestly labeled/tooltipped so nothing lies
    about whether hardware is actually up.
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

import server_manager  # noqa: E402
from create_mediums import Medium, default_mediums  # noqa: E402
from create_param_panels import (  # noqa: E402
    AnimateParamPanel,
    ArtgenParamPanel,
    CreateParamPanel,
    ImageParamPanel,
    VideoParamPanel,
)


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

/* -- Live-model strip -------------------------------------------------------- */
.create-model-strip {
    padding: 2px 0 8px 0;
}
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
/* Task 7: the strip's chips are now clickable model-door cards (Gtk.Button),
   not a passive Gtk.Box readout -- a visible hover affordance says "tap me". */
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
    """The Create surface shell: doors, medium chips, a param-panel host, the
    live-model strip, and the Create CTA.

    Injectable seams (see module docstring) make this fully testable with
    fakes. Nothing here imports `GenerationWorker`, `api_client`, or
    `ControlPanel` — wiring the CTA to real generation is a later task.
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
        # Model door (Task 7): server_manager key -> the clickable Gtk.Button
        # card built for it in `_apply_model_strip`. Populated fresh on every
        # health refresh; `_on_model_card_clicked` doesn't need this map
        # directly (it re-derives the medium from `_server_key_to_medium_id`),
        # but tests address cards by key, and a future "highlight the model
        # behind the active medium" feature would read it too.
        self._model_cards: dict = {}
        # The currently-mounted real param panel (Task 4+), or None while the
        # active medium still shows the Task 3 stub. `_collect_params` reads
        # this to decide between a real `panel.collect()` and `{}`.
        self._active_panel: Optional[CreateParamPanel] = None

        # Built (but not yet appended) BEFORE the chip row: `_build_chip_row`
        # activates the first chip's toggle button as its last step, which
        # synchronously fires `_select_medium` -> `_swap_panel` -> reads
        # `self._panel_host`. Constructing the chip row before this existed
        # was a Task 3 latent bug — PyGObject swallows the resulting
        # AttributeError inside the GTK signal marshaller (logs a traceback,
        # doesn't raise), so the initial swap silently no-opped and the
        # default-active medium never actually got a mounted panel. Building
        # (not appending) `_panel_host` first fixes the ordering while
        # leaving the visual append order — doors, chips, panel host, model
        # strip, CTA — unchanged.
        self._panel_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._panel_host.add_css_class("create-panel-host")

        self.append(self._build_doors_row())
        self.append(self._build_idea_row())
        self.append(self._build_chip_row())
        self.append(self._panel_host)

        self._model_strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._model_strip.add_css_class("create-model-strip")
        self.append(self._model_strip)

        self.append(self._build_cta_row())

        self._refresh_model_strip_async()

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
        if mode == "inspiration" and self._on_inspiration is not None:
            self._on_inspiration()

    # ── Idea door: prompt entry ──────────────────────────────────────────────

    def _build_idea_row(self) -> Gtk.Box:
        """The idea door's prompt entry: "What do you want to make?".

        Visible only while `_entry_mode == "idea"` (see `_set_entry_mode`) —
        the model door has its own way in (a model card) and the inspiration
        door hands off entirely to the Muse, so neither needs a competing
        prompt field on screen.
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

    # ── Medium chip row ──────────────────────────────────────────────────────

    def _build_chip_row(self) -> Gtk.Box:
        """One chip per `mediums_fn()` medium; selecting one swaps the panel."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
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
        """
        child = self._panel_host.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._panel_host.remove(child)
            child = nxt

        if medium.source == "native" and medium.id in _NATIVE_PANEL_CLASSES:
            panel = _NATIVE_PANEL_CLASSES[medium.id]()
            self._panel_host.append(panel.build())
            self._active_panel = panel
            return

        if medium.source == "artgen" and medium.generator:
            panel = ArtgenParamPanel(medium.generator)
            self._panel_host.append(panel.build())
            self._active_panel = panel
            return

        self._active_panel = None
        stub = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        label = Gtk.Label(label=f"{medium.icon} {medium.label} — controls coming soon")
        label.add_css_class("create-panel-stub-label")
        label.set_xalign(0.0)
        stub.append(label)
        self._panel_host.append(stub)

    # ── Live-model strip ─────────────────────────────────────────────────────

    def _refresh_model_strip_async(self) -> None:
        """Fetch health off the GTK main thread; apply on the main thread.

        `health_fn` (real default: `server_manager.status_all`) may perform
        real HTTP calls with per-service timeouts, so it must never run on
        the GTK thread (see module docstring / CLAUDE.md's GTK threading
        rule). Any exception from a bad/fake `health_fn` degrades to an
        empty strip rather than crashing the view.
        """
        def _bg() -> None:
            try:
                statuses = dict(self._health_fn() or {})
            except Exception:
                statuses = {}
            GLib.idle_add(self._apply_model_strip, statuses)

        threading.Thread(target=_bg, daemon=True).start()

    def _apply_model_strip(self, statuses: dict) -> bool:
        """Rebuild the model strip as clickable model-door cards (Task 7).

        Each entry is now a `Gtk.Button` — clicking it is the model door's
        "choose a model" gesture (`_on_model_card_clicked`), one tap whether
        the model is running or not. A not-running card is honestly
        relabeled/tooltipped ("needs starting") rather than pretending
        selecting it starts anything for real — that wiring is deferred (see
        task-7-brief.md).
        """
        self._model_health = statuses
        self._model_cards = {}

        child = self._model_strip.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._model_strip.remove(child)
            child = nxt

        for key, running in statuses.items():
            sdef = server_manager.SERVERS.get(key)
            label_text = sdef.label if sdef is not None else str(key)

            inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

            dot = Gtk.Label(label="●" if running else "○")
            dot.add_css_class("create-model-dot-on" if running else "create-model-dot-off")
            inner.append(dot)

            lbl = Gtk.Label(label=label_text if running else f"{label_text} (needs starting)")
            lbl.add_css_class("create-model-label")
            inner.append(lbl)

            card = Gtk.Button()
            card.set_has_frame(False)
            card.set_child(inner)
            card.add_css_class("create-model-chip")
            card.add_css_class("create-model-chip-on" if running else "create-model-chip-off")
            if not running:
                card.set_tooltip_text(
                    "Not running — starting it costs time and may reset the board."
                )
            card.connect("clicked", lambda _b, k=key: self._on_model_card_clicked(k))

            self._model_strip.append(card)
            self._model_cards[key] = card

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

    def _on_model_card_clicked(self, key: str) -> None:
        """Model door: selecting a card sets the medium from its capability
        and mounts that medium's panel — "it's a video model -> you're
        making video." Works the same whether the model is running or not
        (see `_apply_model_strip`'s docstring for why that's honest, not
        misleading)."""
        medium_id = self._server_key_to_medium_id(key)
        if medium_id is None:
            return  # unmapped capability (e.g. prompt-server) or unknown key

        try:
            mediums = list(self._mediums_fn() or [])
        except Exception:
            mediums = []
        medium = next((m for m in mediums if m.id == medium_id), None)
        if medium is None:
            return

        btn = self._chip_buttons.get(medium_id)
        if btn is not None:
            btn.set_active(True)  # fires _select_medium via the chip's "toggled"
        else:
            self._select_medium(medium)

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
        """Delegate to the active medium's real panel, if one is mounted,
        then merge in the idea door's typed prompt (Task 7).

        Mediums without a ported panel yet (still showing the Task 3 stub)
        fall back to `{}`, matching Task 3's behavior exactly. A panel whose
        `collect()` raises degrades to `{}` too, rather than crashing the CTA
        click — `on_create` is never worth losing over a bad widget read.

        `params["prompt"]` is added ONLY when the prompt entry holds
        non-whitespace text. This is deliberate, not an oversight: every
        Tasks 3-6 CTA test asserts an exact params dict with no "prompt" key
        (they never touch the entry, so it's empty) — always injecting
        `"prompt": ""` would silently break every one of them. A typed
        prompt is real signal; an empty box is not.
        """
        if self._active_panel is None:
            params = {}
        else:
            try:
                params = self._active_panel.collect()
            except Exception:
                params = {}

        prompt_entry = getattr(self, "_prompt_entry", None)
        if prompt_entry is not None:
            prompt_text = prompt_entry.get_text().strip()
            if prompt_text:
                params = {**params, "prompt": prompt_text}

        return params
