# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
CreateView — the unified generation surface's shell (Create-surface plan,
Task 3: docs/superpowers/specs/2026-07-13-create-surface-design.md).

Replaces the four medium tabs (Video / Animate / Image / Generative Art) with
ONE surface where the medium is a *chip*, not a top-level division. This
module builds the shell only — three doors in (idea / model / inspiration),
the medium-chip row, a per-medium param-panel host, the live-model strip, and
the Create CTA. Per-type param panels (real image/video/animate/artgen
controls) arrive in Tasks 4-6; this task's panel host shows a plain stub
labeled by the selected medium so the swap is observable and testable.

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

        self.append(self._build_doors_row())
        self.append(self._build_chip_row())

        self._panel_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._panel_host.add_css_class("create-panel-host")
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
        if mode == "inspiration" and self._on_inspiration is not None:
            self._on_inspiration()

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

        Task 3 stub only — a plain, honestly-labeled placeholder. Tasks 4-6
        replace this with real per-type panels (build_params()/collect()).
        """
        child = self._panel_host.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._panel_host.remove(child)
            child = nxt

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
        self._model_health = statuses

        child = self._model_strip.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._model_strip.remove(child)
            child = nxt

        for key, running in statuses.items():
            sdef = server_manager.SERVERS.get(key)
            label_text = sdef.label if sdef is not None else str(key)

            chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            chip.add_css_class("create-model-chip")
            chip.add_css_class("create-model-chip-on" if running else "create-model-chip-off")

            dot = Gtk.Label(label="●" if running else "○")
            dot.add_css_class("create-model-dot-on" if running else "create-model-dot-off")
            chip.append(dot)

            lbl = Gtk.Label(label=label_text)
            lbl.add_css_class("create-model-label")
            chip.append(lbl)

            self._model_strip.append(chip)

        return GLib.SOURCE_REMOVE

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
        """Task 3 stub: no real per-type panel exists yet, so there are no
        params to collect. Tasks 4-6 replace this with the active panel's
        `collect()`."""
        return {}
