# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Pipeline Studio — Discover/Open views, RemixView, and shell
(SP-C Phase 1 Tasks 3-4, SP-C Phase 2a Task 2).

Pipeline Studio's "front door": browse already-run pipelines as plain-language
intent recipes ("Generate an image → Describe it → Film it → ...") instead of
raw node/class_type graphs, so people learn what's possible by seeing real
finished runs. Layout follows the validated mockup at
`.superpowers/brainstorm/988333-1783804257/content/discover-gallery.html`:
one big featured "hero" card (the most recent run) + a grid of the rest.

Four pieces:

`DiscoverView(Gtk.Box)`
    Renders the hero + grid from a plain `list[RunView]` handed in via
    `set_runs()`. It does NOT import PipelineStore (or anything else that
    touches disk/history) itself — all data arrives through `set_runs`, which
    keeps this widget fully unit-testable with hand-built RunView fixtures
    and a display (see tests/test_pipeline_studio.py). Emits the custom
    `open-run` signal (str run_id) when a card's "Open" button is clicked;
    the caller (PipelineStudio, and eventually MainWindow) decides what that
    means (switch to the "open" stack page, drill into the run, etc).

`OpenView(Gtk.Box)`
    The "learn from example" page (SP-C Task 4): one run's steps laid out
    end-to-end, in order, each with its real artifact (or an honest
    placeholder if the step hasn't produced one yet) and a per-step "Remix
    from here →" stub, plus a top "Remix whole pipeline →" stub. Layout
    follows the validated mockup at
    `.superpowers/brainstorm/988333-1783804257/content/open-run.html`. Like
    DiscoverView, data arrives ONLY through `set_run()` — no PipelineStore
    import here. Both remix controls emit the custom `remix-request` signal
    (str): the node_id for a per-step remix, or `""` for the whole pipeline.
    Phase 1 wiring (PipelineStudio, below) treats this as a stub — "coming in
    the editor (Phase 2)" — not an actual editor.

`RemixView(Gtk.Box)`
    The "change one thing, safely" edit surface (SP-C Phase 2a Task 2): each
    step renders as an intent card with its `spec_remix.editable_params`
    fields inline, pre-filled with the run's current values, plus a single
    "Run this remix →" button. Layout follows the validated mockup at
    `.superpowers/brainstorm/988333-1783804257/content/intent-composer.html`.
    Data arrives ONLY through `set_run(run, spec_path)` — like Discover/
    OpenView, no `PipelineStore` import here (it does load the spec file
    itself via `pipeline_engine.load_spec`, which is pure spec-file IO, not a
    store/history access). Emits `run-remix` (str spec_path, object edits)
    where `edits` is `{node_id: {key: new_value}}` containing only fields the
    user actually changed — an unmodified Run reproduces the base run.

`PipelineStudio(Gtk.Box)`
    The shell: a Gtk.Stack with "discover" (DiscoverView) and "open"
    (OpenView, wrapped with a "← Discover" back control) pages. Loads runs
    off the GTK main thread — via `pipeline_view_model.list_run_views(PipelineStore())`
    in a daemon thread — then hands them to `DiscoverView.set_runs` through
    `GLib.idle_add`, per the GTK threading rule in this repo's CLAUDE.md
    (never touch widgets from a background thread). `DiscoverView`'s
    "open-run" signal triggers the same pattern for a single run: build its
    `RunView` off-thread (`build_run_view(PipelineStore().get_run(run_id))`),
    then `GLib.idle_add` both `OpenView.set_run` and the stack switch.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, GObject, Gtk  # noqa: E402

from pipeline_engine import load_spec  # noqa: E402
from pipeline_store import PipelineStore  # noqa: E402
from pipeline_view_model import RunView, StepView, build_run_view, list_run_views  # noqa: E402
from spec_remix import editable_params  # noqa: E402

log = logging.getLogger(__name__)


def _thumb_pixbuf(path: "str | None", width: int, height: int):
    """Aspect-preserving thumbnail load, or None if there's nothing to load.

    Delegates to `main_window._load_pixbuf` rather than re-implementing
    pixbuf scaling. Imported lazily (inside the function, not at module
    scope) so importing pipeline_studio never drags in all of main_window's
    heavier dependencies, and so there's no module-load-time circular import
    once a later task embeds PipelineStudio inside MainWindow.
    """
    if not path:
        return None
    from main_window import _load_pixbuf
    return _load_pixbuf(path, width, height)


def _build_thumb_frame(path: "str | None", width: int, height: int,
                        css_class: str) -> Gtk.Widget:
    """A fixed-size box holding either a scaled Gtk.Picture or an honest placeholder.

    Shared by DiscoverView (run/hero cards) and OpenView (per-step artifacts)
    so there's exactly one "thumbnail or placeholder" rendering rule in this
    module. A step/run with no artifact yet (still pending, or an intent that
    never produces a file — e.g. caption/text) renders the placeholder rather
    than an empty or missing box.
    """
    frame = Gtk.Box()
    frame.set_size_request(width, height)
    frame.add_css_class(css_class)
    frame.set_halign(Gtk.Align.CENTER)
    frame.set_valign(Gtk.Align.CENTER)

    pb = _thumb_pixbuf(path, width, height)
    if pb is not None:
        pic = Gtk.Picture.new_for_pixbuf(pb)
        pic.set_can_shrink(True)
        frame.append(pic)
    else:
        placeholder = Gtk.Label(label="\U0001f5bc️")  # 🖼️
        placeholder.add_css_class("muted")
        frame.append(placeholder)
    return frame


# ── Dark forest-teal theme ──────────────────────────────────────────────────
#
# Tokens lifted straight from the validated discover-gallery mockup's <style>
# block (base/surf/teal/gold/ink), per the brand palette in CLAUDE.md.
_CSS = b"""
.ps-discover, .ps-studio {
    background-color: #071a19;
}
.ps-empty-icon {
    font-size: 40px;
}
.ps-empty-msg {
    color: #94b8b2;
    font-size: 13px;
}
.ps-hero {
    background-color: #0d2b2a;
    border: 1px solid alpha(#74C5DF, 0.16);
    border-radius: 13px;
    padding: 16px;
}
.ps-hero-thumb, .ps-card-thumb {
    background-color: #0a1f1e;
    border-radius: 8px;
}
.ps-hero-title {
    font-size: 20px;
    font-weight: 700;
    color: #eef8f6;
}
.ps-card-title {
    font-size: 14px;
    font-weight: 700;
    color: #eef8f6;
}
.ps-lead {
    font-size: 12px;
    color: #94b8b2;
    margin-top: 4px;
    margin-bottom: 4px;
}
.ps-chip {
    background-color: #123c3a;
    border: 1px solid alpha(#74C5DF, 0.16);
    border-radius: 8px;
    padding: 4px 9px;
    font-size: 11px;
    color: #dcefe9;
}
.ps-chip-arrow {
    color: #1B8EB1;
    font-weight: 700;
}
.ps-card {
    background-color: #0d2b2a;
    border: 1px solid alpha(#74C5DF, 0.16);
    border-radius: 12px;
    padding: 10px;
}
.ps-btn-primary {
    background-color: #1B8EB1;
    color: #fff;
    border-radius: 9px;
}
.ps-btn-ghost {
    color: #74C5DF;
    border-radius: 9px;
}
.ps-open-title {
    font-size: 18px;
    font-weight: 700;
    color: #eef8f6;
}
.ps-open-back {
    color: #6f948d;
    font-size: 13px;
}
.ps-step {
    background-color: #0d2b2a;
    border: 1px solid alpha(#74C5DF, 0.16);
    border-radius: 12px;
    padding: 12px;
}
.ps-step-n {
    font-family: monospace;
    font-size: 13px;
    color: #6f948d;
}
.ps-step-verb {
    font-size: 15px;
    font-weight: 700;
    color: #eef8f6;
}
.ps-step-noun {
    font-size: 12px;
    color: #94b8b2;
}
.ps-step-model {
    font-size: 10.5px;
    color: #6f948d;
}
.ps-status-done {
    color: #6FABA0;
}
.ps-status-running {
    color: #F6BC42;
}
.ps-status-failed {
    color: #FF9E8A;
}
.ps-status-pending {
    color: #6f948d;
}
.ps-remix-btn {
    color: #4FD1C5;
    font-size: 10.5px;
}
.ps-remix-all {
    background-color: #1B8EB1;
    color: #fff;
    border-radius: 8px;
    padding: 7px 13px;
    font-size: 12px;
}
.ps-remix-toast {
    color: #F6BC42;
    font-size: 12px;
}
.ps-remix-header {
    background-color: #0d2b2a;
    border: 1px solid alpha(#74C5DF, 0.16);
    border-radius: 10px;
    padding: 10px 14px;
}
.ps-remix-safe {
    font-size: 11.5px;
    color: #bfe6d9;
    background-color: alpha(#6FABA0, 0.16);
    border: 1px solid alpha(#6FABA0, 0.32);
    border-radius: 20px;
    padding: 4px 10px;
}
.ps-field-key {
    font-size: 10.5px;
    color: #7fb0a8;
}
.ps-field-entry {
    background-color: #071a19;
    border: 1px solid alpha(#74C5DF, 0.35);
    border-radius: 8px;
    color: #eef8f6;
    padding: 6px 9px;
}
.ps-remix-run-btn {
    background-color: #1B8EB1;
    color: #fff;
    border-radius: 9px;
    padding: 10px 16px;
    font-size: 13px;
    font-weight: 700;
}
"""

_css_applied = False


def _apply_css() -> None:
    """Register the dark forest-teal CSS provider for the default display.

    Guarded by a module-level flag so repeated DiscoverView/PipelineStudio
    construction (e.g. across tests) doesn't stack up duplicate providers.
    Uses a throwaway Gtk.Window to fetch the default display, same pattern
    as `main_window._apply_css` — this works before the widget itself is
    realized/attached to any window.
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


class DiscoverView(Gtk.Box):
    """Discover page: one featured hero card + a grid of the rest.

    Data comes ONLY through `set_runs()` — see module docstring for why.
    """

    __gsignals__ = {
        "open-run": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    HERO_THUMB_W, HERO_THUMB_H = 320, 200
    CARD_THUMB_W, CARD_THUMB_H = 220, 120
    _GRID_COLUMNS = 3

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("ps-discover")
        _apply_css()

        # Attributes populated by set_runs(); declared here so a fresh
        # DiscoverView has sane (empty) defaults before the first set_runs.
        self._hero_title: "Gtk.Label | None" = None
        self._hero_recipe_labels: list = []
        self._hero_open_btn: "Gtk.Button | None" = None
        self._card_open_buttons: dict = {}

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        self.append(scroller)

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._content.set_margin_top(18)
        self._content.set_margin_bottom(18)
        self._content.set_margin_start(18)
        self._content.set_margin_end(18)
        scroller.set_child(self._content)

        self.set_runs([])

    # ── Public API ───────────────────────────────────────────────────────────

    def set_runs(self, runs: "list[RunView]") -> None:
        """(Re)build the whole view from a list of RunViews. Main-thread only.

        runs[0] becomes the featured hero card; the rest become grid cards.
        An empty list renders a friendly "no runs yet" placeholder instead of
        an empty page.
        """
        while child := self._content.get_first_child():
            self._content.remove(child)
        self._hero_title = None
        self._hero_recipe_labels = []
        self._hero_open_btn = None
        self._card_open_buttons = {}

        if not runs:
            self._content.append(self._build_empty_state())
            return

        hero, *rest = runs
        self._content.append(self._build_hero_card(hero))

        if rest:
            lead = Gtk.Label(label="More runs · click to open")
            lead.set_xalign(0)
            lead.add_css_class("ps-lead")
            self._content.append(lead)
            self._content.append(self._build_grid(rest))

    # ── Empty state ──────────────────────────────────────────────────────────

    def _build_empty_state(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)

        icon = Gtk.Label(label="\U0001f5fa️")  # 🗺️
        icon.add_css_class("ps-empty-icon")
        box.append(icon)

        msg = Gtk.Label(label="No runs yet — run a pipeline to see it here.")
        msg.add_css_class("ps-empty-msg")
        box.append(msg)
        return box

    # ── Hero card ────────────────────────────────────────────────────────────

    def _build_hero_card(self, run: RunView) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        card.add_css_class("ps-hero")

        thumb = self._make_thumb(run.hero_path, self.HERO_THUMB_W, self.HERO_THUMB_H,
                                  "ps-hero-thumb")
        card.append(thumb)

        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        meta.set_hexpand(True)
        meta.set_valign(Gtk.Align.CENTER)

        title = Gtk.Label(label=run.title)
        title.set_xalign(0)
        title.set_wrap(True)
        title.add_css_class("ps-hero-title")
        meta.append(title)
        self._hero_title = title

        chips_row, chip_labels = self._build_recipe_row(run.recipe)
        meta.append(chips_row)
        self._hero_recipe_labels = chip_labels

        open_btn = Gtk.Button(label="Open — see every step")
        open_btn.add_css_class("ps-btn-primary")
        open_btn.set_halign(Gtk.Align.START)
        open_btn.connect("clicked", self._on_open_clicked, run.run_id)
        meta.append(open_btn)
        self._hero_open_btn = open_btn

        card.append(meta)
        return card

    # ── Grid of run cards ────────────────────────────────────────────────────

    def _build_grid(self, runs: "list[RunView]") -> Gtk.Widget:
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(self._GRID_COLUMNS)
        flow.set_min_children_per_line(1)
        flow.set_row_spacing(14)
        flow.set_column_spacing(14)
        flow.set_homogeneous(True)
        for run in runs:
            flow.insert(self._build_run_card(run), -1)
        return flow

    def _build_run_card(self, run: RunView) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("ps-card")

        thumb = self._make_thumb(run.hero_path, self.CARD_THUMB_W, self.CARD_THUMB_H,
                                  "ps-card-thumb")
        card.append(thumb)

        title = Gtk.Label(label=run.title)
        title.set_xalign(0)
        title.set_wrap(True)
        title.add_css_class("ps-card-title")
        card.append(title)

        chips_row, _chip_labels = self._build_recipe_row(run.recipe)
        card.append(chips_row)

        open_btn = Gtk.Button(label="Open")
        open_btn.add_css_class("ps-btn-ghost")
        open_btn.set_halign(Gtk.Align.START)
        open_btn.connect("clicked", self._on_open_clicked, run.run_id)
        card.append(open_btn)
        self._card_open_buttons[run.run_id] = open_btn

        # Plain attribute, not set_data() — PyGObject blocks GObject's C-level
        # data methods (see CLAUDE.md "PyGObject gotchas").
        card.run_id = run.run_id
        return card

    # ── Shared helpers ───────────────────────────────────────────────────────

    def _on_open_clicked(self, _button: Gtk.Button, run_id: str) -> None:
        self.emit("open-run", run_id)

    def _build_recipe_row(self, recipe: "list[str]") -> "tuple[Gtk.Widget, list[Gtk.Label]]":
        """Build the '🖼 Generate an image → 📝 Write about it → ...' chip row.

        Returns the container plus the list of chip Labels (recipe steps
        only, excluding the "→" arrow separators) so callers/tests can read
        back exactly the strings in `run.recipe`.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_hexpand(False)
        chip_labels: list = []
        for i, step in enumerate(recipe):
            if i > 0:
                arrow = Gtk.Label(label="→")  # →
                arrow.add_css_class("ps-chip-arrow")
                box.append(arrow)
            chip = Gtk.Label(label=step)
            chip.add_css_class("ps-chip")
            box.append(chip)
            chip_labels.append(chip)
        return box, chip_labels

    def _make_thumb(self, path: "str | None", width: int, height: int,
                     css_class: str) -> Gtk.Widget:
        return _build_thumb_frame(path, width, height, css_class)


class OpenView(Gtk.Box):
    """Open page: one run's steps laid out end-to-end (learn-by-example).

    Data comes ONLY through `set_run()` — same rule as DiscoverView, and for
    the same reason: this widget must be unit-testable with a hand-built
    RunView and never reach into PipelineStore/disk itself.
    """

    __gsignals__ = {
        "remix-request": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    STEP_THUMB_W, STEP_THUMB_H = 150, 92

    # status -> (glyph, css class). Anything outside pipeline_view_model's
    # known statuses (which is already constrained to done/running/pending/
    # failed — see _resolve_status) falls back to the pending glyph rather
    # than raising, matching the "always render *something*" rule the rest
    # of this module follows for unrecognized data.
    _STATUS_GLYPH = {"done": "✓", "running": "⟳", "pending": "•",
                      "failed": "✕"}
    _STATUS_CSS = {"done": "ps-status-done", "running": "ps-status-running",
                    "pending": "ps-status-pending", "failed": "ps-status-failed"}

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("ps-discover")  # same dark-teal page background
        _apply_css()

        # Populated by set_run(); keyed by node_id, in step order (Python
        # dicts preserve insertion order) so tests/callers can both look up a
        # specific step's widget and read back the render order.
        self._step_remix_buttons: dict = {}
        self._step_thumb_frames: dict = {}

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_margin_top(18)
        header.set_margin_start(18)
        header.set_margin_end(18)

        self._title_label = Gtk.Label(label="")
        self._title_label.set_xalign(0)
        self._title_label.set_wrap(True)
        self._title_label.add_css_class("ps-open-title")
        self._title_label.set_hexpand(True)
        header.append(self._title_label)

        self._remix_all_btn = Gtk.Button(label="Remix whole pipeline →")
        self._remix_all_btn.add_css_class("ps-remix-all")
        # Whole-pipeline remix doesn't depend on which run is loaded, so this
        # is wired once here rather than rebuilt in set_run().
        self._remix_all_btn.connect("clicked", lambda _b: self.emit("remix-request", ""))
        header.append(self._remix_all_btn)

        self.append(header)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        self.append(scroller)

        self._steps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._steps_box.set_margin_top(10)
        self._steps_box.set_margin_bottom(18)
        self._steps_box.set_margin_start(18)
        self._steps_box.set_margin_end(18)
        scroller.set_child(self._steps_box)

    # ── Public API ───────────────────────────────────────────────────────────

    def set_run(self, run: RunView) -> None:
        """(Re)build the step list for *run*. Main-thread only, repeat-safe."""
        self._title_label.set_label(run.title)

        while child := self._steps_box.get_first_child():
            self._steps_box.remove(child)
        self._step_remix_buttons = {}
        self._step_thumb_frames = {}

        if not run.steps:
            empty = Gtk.Label(label="This run has no steps.")
            empty.add_css_class("ps-empty-msg")
            self._steps_box.append(empty)
            return

        for index, step in enumerate(run.steps, start=1):
            self._steps_box.append(self._build_step_row(index, step))

    # ── Row building ─────────────────────────────────────────────────────────

    def _build_step_row(self, index: int, step: StepView) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.add_css_class("ps-step")

        n_label = Gtk.Label(label=str(index))
        n_label.add_css_class("ps-step-n")
        n_label.set_valign(Gtk.Align.START)
        row.append(n_label)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        main.set_hexpand(True)
        main.set_valign(Gtk.Align.CENTER)

        verb_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        verb_label = Gtk.Label(label=step.intent.verb)
        verb_label.set_xalign(0)
        verb_label.add_css_class("ps-step-verb")
        verb_row.append(verb_label)

        status_label = Gtk.Label(label=self._STATUS_GLYPH.get(step.status, "•"))
        status_label.add_css_class(self._STATUS_CSS.get(step.status, "ps-status-pending"))
        verb_row.append(status_label)
        main.append(verb_row)

        noun_label = Gtk.Label(label=step.intent.noun)
        noun_label.set_xalign(0)
        noun_label.add_css_class("ps-step-noun")
        main.append(noun_label)

        # model_label is omitted entirely (not shown as blank) for intents
        # that don't name an underlying tool/model — e.g. Describe/Cut
        # out/Read its depth are implementation-agnostic in the mockup.
        if step.intent.model_label:
            model_label = Gtk.Label(label=step.intent.model_label)
            model_label.set_xalign(0)
            model_label.add_css_class("ps-step-model")
            main.append(model_label)

        row.append(main)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right.set_valign(Gtk.Align.CENTER)

        remix_btn = Gtk.Button(label="Remix from here →")
        remix_btn.add_css_class("ps-remix-btn")
        remix_btn.connect("clicked", self._on_remix_clicked, step.node_id)
        right.append(remix_btn)
        self._step_remix_buttons[step.node_id] = remix_btn

        thumb = _build_thumb_frame(step.artifact_path, self.STEP_THUMB_W, self.STEP_THUMB_H,
                                    "ps-card-thumb")
        right.append(thumb)
        self._step_thumb_frames[step.node_id] = thumb

        row.append(right)
        return row

    def _on_remix_clicked(self, _button: Gtk.Button, node_id: str) -> None:
        self.emit("remix-request", node_id)


class RemixView(Gtk.Box):
    """Remix page: edit a run's plain-language params and re-run it.

    "Change one thing, safely" (SP-C Phase 2a Task 2): every field is
    PRE-FILLED with the base run's actual current value, so hitting Run with
    zero edits reproduces the base run unchanged — the empty edits dict this
    view emits in that case is a documented no-op for
    `spec_remix.derive_spec`. Data arrives ONLY through `set_run()`: this
    widget loads the spec via `pipeline_engine.load_spec` (pure spec-file IO,
    no store/history) and lists its editable inputs via
    `spec_remix.editable_params` (pure dict math) — it never imports or
    touches `PipelineStore` itself, matching Discover/OpenView's rule.

    Layout follows the validated mockup at
    `.superpowers/brainstorm/988333-1783804257/content/intent-composer.html`:
    each step is an intent card (verb + noun, model as a quiet secondary
    detail) with its editable fields inline, and a single prominent "Run this
    remix →" button at the bottom.
    """

    __gsignals__ = {
        # (spec_path, edits) — edits is {node_id: {key: new_value}}, containing
        # ONLY fields the user actually changed from their pre-filled value.
        "run-remix": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("ps-discover")  # same dark-teal page background
        _apply_css()

        self._spec_path: "str | None" = None
        # Populated by set_run(), keyed by node_id then input key, in step/
        # field order (Python dicts preserve insertion order). Two parallel
        # dicts: the live widget (to read back the CURRENT value at Run time)
        # and (kind, original_value) (to know what "changed" means for that
        # widget's kind — see _read_widget_value/_collect_edits).
        self._field_widgets: "dict[str, dict[str, Gtk.Widget]]" = {}
        self._field_meta: "dict[str, dict[str, tuple]]" = {}

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("ps-remix-header")
        header.set_margin_top(18)
        header.set_margin_start(18)
        header.set_margin_end(18)

        self._title_label = Gtk.Label(label="")
        self._title_label.set_xalign(0)
        self._title_label.set_wrap(True)
        self._title_label.add_css_class("ps-open-title")
        self._title_label.set_hexpand(True)
        header.append(self._title_label)

        safe_pill = Gtk.Label(label="change one thing — the rest still runs")
        safe_pill.add_css_class("ps-remix-safe")
        header.append(safe_pill)
        self.append(header)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        self.append(scroller)

        self._steps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._steps_box.set_margin_top(10)
        self._steps_box.set_margin_bottom(10)
        self._steps_box.set_margin_start(18)
        self._steps_box.set_margin_end(18)
        scroller.set_child(self._steps_box)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        footer.set_margin_top(4)
        footer.set_margin_bottom(18)
        footer.set_margin_start(18)
        footer.set_margin_end(18)
        self._run_button = Gtk.Button(label="Run this remix →")
        self._run_button.add_css_class("ps-remix-run-btn")
        self._run_button.connect("clicked", self._on_run_clicked)
        footer.append(self._run_button)
        self.append(footer)

    # ── Public API ───────────────────────────────────────────────────────────

    def set_run(self, run: RunView, spec_path: str) -> None:
        """(Re)build the editable step list for *run*'s *spec_path*.

        Main-thread only, repeat-safe (clears any prior children/state before
        rebuilding, same pattern as DiscoverView.set_runs/OpenView.set_run).
        """
        self._spec_path = spec_path
        self._title_label.set_label(f"Remixing · {run.title}")

        while child := self._steps_box.get_first_child():
            self._steps_box.remove(child)
        self._field_widgets = {}
        self._field_meta = {}

        spec = load_spec(spec_path)
        params_by_node = editable_params(spec)

        if not run.steps:
            empty = Gtk.Label(label="This run has no steps to remix.")
            empty.add_css_class("ps-empty-msg")
            self._steps_box.append(empty)
            return

        for index, step in enumerate(run.steps, start=1):
            fields = params_by_node.get(step.node_id, [])
            self._steps_box.append(self._build_step_card(index, step, fields))

    # ── Row building ─────────────────────────────────────────────────────────

    def _build_step_card(self, index: int, step: StepView, fields: list) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("ps-step")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        n_label = Gtk.Label(label=str(index))
        n_label.add_css_class("ps-step-n")
        n_label.set_valign(Gtk.Align.START)
        head.append(n_label)

        verb_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        verb_label = Gtk.Label(label=step.intent.verb)
        verb_label.set_xalign(0)
        verb_label.add_css_class("ps-step-verb")
        verb_col.append(verb_label)

        noun_label = Gtk.Label(label=step.intent.noun)
        noun_label.set_xalign(0)
        noun_label.add_css_class("ps-step-noun")
        verb_col.append(noun_label)

        # model_label is a quiet secondary detail — omitted entirely (not
        # shown blank) when the intent doesn't name an underlying tool,
        # mirroring OpenView._build_step_row's identical guard.
        if step.intent.model_label:
            model_label = Gtk.Label(label=step.intent.model_label)
            model_label.set_xalign(0)
            model_label.add_css_class("ps-step-model")
            verb_col.append(model_label)

        head.append(verb_col)
        card.append(head)

        node_widgets: "dict[str, Gtk.Widget]" = {}
        node_meta: "dict[str, tuple]" = {}
        for field in fields:
            row, widget = self._build_field_row(field)
            card.append(row)
            node_widgets[field.key] = widget
            node_meta[field.key] = (field.kind, field.value)

        if node_widgets:
            self._field_widgets[step.node_id] = node_widgets
            self._field_meta[step.node_id] = node_meta

        return card

    def _build_field_row(self, field) -> "tuple[Gtk.Widget, Gtk.Widget]":
        """One label + editable widget row for a single ParamField.

        Returns (row, widget) — the caller keeps the widget reference (keyed
        by node_id/key) so Run-time diffing can read its current value; the
        row is just what gets appended to the step card.
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        key_label = Gtk.Label(label=field.label)
        key_label.set_xalign(0)
        key_label.add_css_class("ps-field-key")
        key_label.set_size_request(120, -1)
        row.append(key_label)

        widget = self._build_field_widget(field)
        widget.set_hexpand(True)
        row.append(widget)
        return row, widget

    def _build_field_widget(self, field) -> Gtk.Widget:
        """Kind -> widget, per the brief: Entry (text), SpinButton (number),
        Switch (bool). `editable_params` never actually produces "choice"
        today (it's reserved for a future enum-aware caller — see
        spec_remix.ParamField's docstring) and a ParamField carries no
        options list to build a real dropdown from, so "choice" falls back to
        the same free-text Entry as "text"."""
        if field.kind == "number":
            # Wide-open bounds: a ParamField has no min/max metadata, so the
            # SpinButton must accept whatever numeric value the base spec
            # already has plus any reasonable adjustment either direction.
            adjustment = Gtk.Adjustment(
                value=float(field.value), lower=-1_000_000_000, upper=1_000_000_000,
                step_increment=1, page_increment=10,
            )
            spin = Gtk.SpinButton(adjustment=adjustment)
            spin.set_digits(0 if isinstance(field.value, int) else 3)
            spin.set_value(float(field.value))
            spin.add_css_class("ps-field-entry")
            return spin
        if field.kind == "bool":
            switch = Gtk.Switch()
            switch.set_active(bool(field.value))
            switch.set_halign(Gtk.Align.START)
            return switch
        entry = Gtk.Entry()
        entry.set_text(str(field.value))
        entry.add_css_class("ps-field-entry")
        return entry

    # ── Run ──────────────────────────────────────────────────────────────────

    def _on_run_clicked(self, _button: Gtk.Button) -> None:
        if self._spec_path is None:
            return  # set_run() never called — nothing to run
        self.emit("run-remix", self._spec_path, self._collect_edits())

    def _collect_edits(self) -> "dict[str, dict]":
        """Diff every field's current widget value against its pre-filled
        original. Only genuinely-changed fields make it into the result, so
        a Run with zero tweaks yields an empty dict — a documented no-op for
        `spec_remix.derive_spec` (it applies edits over a full copy of the
        base spec, so "no edits" reproduces the base run exactly)."""
        edits: "dict[str, dict]" = {}
        for node_id, widgets in self._field_widgets.items():
            for key, widget in widgets.items():
                kind, orig_value = self._field_meta[node_id][key]
                new_value = self._read_widget_value(kind, orig_value, widget)
                if new_value != orig_value:
                    edits.setdefault(node_id, {})[key] = new_value
        return edits

    def _read_widget_value(self, kind: str, orig_value, widget: Gtk.Widget):
        """Extract *widget*'s current value, coerced back to *orig_value*'s
        Python type so the diff in _collect_edits (and any downstream
        derive_spec call) compares/writes like-for-like — e.g. a SpinButton
        always reports float, so an int-valued field rounds back to int."""
        if kind == "number":
            raw = widget.get_value()
            return int(round(raw)) if isinstance(orig_value, int) else raw
        if kind == "bool":
            return widget.get_active()
        return widget.get_text()


class PipelineStudio(Gtk.Box):
    """Pipeline Studio shell: a Gtk.Stack of {discover, open}.

    Constructing this widget kicks off a background thread that loads run
    history and populates the Discover page — see module docstring for the
    threading rule this follows.
    """

    def __init__(self, on_open_run: "Optional[Callable[[str], None]]" = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("ps-studio")
        _apply_css()
        self._on_open_run_cb = on_open_run

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.append(self.stack)

        self.discover = DiscoverView()
        self.discover.connect("open-run", self._on_open_run)
        self.stack.add_titled(self.discover, "discover", "Discover")

        # "open" page: a back-to-discover bar + a remix-stub toast wrapped
        # around the real OpenView (fleshed out from the Task-3 stub here in
        # Task 4).
        open_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        back_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        back_bar.set_margin_top(10)
        back_bar.set_margin_start(18)
        back_btn = Gtk.Button(label="← Discover")
        back_btn.add_css_class("ps-open-back")
        back_btn.add_css_class("ps-btn-ghost")
        back_btn.connect("clicked", self._on_back_to_discover)
        back_bar.append(back_btn)
        open_page.append(back_bar)

        self.open_view = OpenView()
        self.open_view.connect("remix-request", self._on_remix_request)
        self.open_view.set_vexpand(True)
        open_page.append(self.open_view)

        # Phase-2 stub surface: remix-request is wired up here but does NOT
        # open an editor yet — see module docstring. Hidden until the first
        # remix click so it doesn't clutter the page on a fresh Open.
        self._remix_toast = Gtk.Label(label="")
        self._remix_toast.add_css_class("ps-remix-toast")
        self._remix_toast.set_margin_start(18)
        self._remix_toast.set_margin_bottom(10)
        self._remix_toast.set_visible(False)
        open_page.append(self._remix_toast)

        self.stack.add_titled(open_page, "open", "Open")

        self.stack.set_visible_child_name("discover")

        self._load_runs_async()

    def show_discover(self) -> None:
        """Reset the inner {discover, open} stack to "discover". Main-thread only.

        Called by MainWindow._show_pipelines every time the Pipelines toolbar
        toggle is re-activated, so leaving and re-entering Pipelines never
        strands the user on a stale Open page from a previous visit — Discover
        is always the front door.
        """
        self.stack.set_visible_child_name("discover")

    def _on_open_run(self, _widget: DiscoverView, run_id: str) -> None:
        self._load_run_async(run_id)
        if self._on_open_run_cb is not None:
            self._on_open_run_cb(run_id)

    def _on_back_to_discover(self, _button: Gtk.Button) -> None:
        self.stack.set_visible_child_name("discover")

    def _on_remix_request(self, _widget: "OpenView", node_id: str) -> None:
        """Phase-2 stub: acknowledge the remix intent without opening an editor.

        node_id == "" means "remix whole pipeline"; otherwise it's the
        specific step's node_id. Real editing lands in SP-C Phase 2 — this
        just proves the signal is wired end-to-end.
        """
        what = "the whole pipeline" if node_id == "" else f"step {node_id}"
        self._remix_toast.set_label(f"Remixing {what} — coming in the editor (Phase 2)")
        self._remix_toast.set_visible(True)

    def _load_runs_async(self) -> None:
        """Load RunViews off the GTK main thread; hand results back via idle_add.

        Any failure to load (missing store, unreadable index, etc.) degrades
        to an empty Discover page rather than crashing the shell — matching
        `list_run_views`'s own per-record error tolerance.
        """
        def worker() -> None:
            try:
                runs = list_run_views(PipelineStore())
            except Exception:  # noqa: BLE001 — never let a load error crash the shell
                log.warning("failed to load pipeline runs for Discover view", exc_info=True)
                runs = []
            GLib.idle_add(self.discover.set_runs, runs)

        threading.Thread(target=worker, daemon=True).start()

    def _load_run_async(self, run_id: str) -> None:
        """Build one run's RunView off the GTK main thread; show it via idle_add.

        A missing run (deleted/renamed since Discover last loaded), an
        unloadable spec, or any other failure logs a warning and leaves the
        Discover page showing rather than crashing the shell or switching to
        a half-populated Open page.
        """
        def worker() -> None:
            try:
                record = PipelineStore().get_run(run_id)
                if record is None:
                    log.warning("open-run: no run found for id %s", run_id)
                    return
                run_view = build_run_view(record)
            except Exception:  # noqa: BLE001 — never let a load error crash the shell
                log.warning("failed to build run view for %s", run_id, exc_info=True)
                return
            GLib.idle_add(self._show_run, run_view)

        threading.Thread(target=worker, daemon=True).start()

    def _show_run(self, run_view: RunView) -> None:
        """Main-thread only: populate OpenView and switch the stack to it."""
        self.open_view.set_run(run_view)
        self.stack.set_visible_child_name("open")
        return False
