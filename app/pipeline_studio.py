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
    from here →" button, plus a top "Remix whole pipeline →" button. Layout
    follows the validated mockup at
    `.superpowers/brainstorm/988333-1783804257/content/open-run.html`. Like
    DiscoverView, data arrives ONLY through `set_run()` — no PipelineStore
    import here. Both remix controls emit the custom `remix-request` signal
    (str): the node_id for a per-step remix, or `""` for the whole pipeline.
    PipelineStudio (below) opens the same whole-run RemixView for either case
    today — RemixView's editing surface isn't per-step yet (see its own
    docstring) — a future task may use node_id to scroll/focus that step.

`RemixView(Gtk.Box)`
    The "change one thing, safely" edit surface (SP-C Phase 2a Task 2), now a
    real STRUCTURAL composer (Phase 2b-1 Task 3): each step renders as an
    intent card with its `spec_remix.editable_params` fields inline
    (pre-filled with the working spec's current values), a **Remove**
    control, and a **＋ add a step after** control that opens a contextual
    `Gtk.Popover` of DYNAMIC capabilities (SP-C Phase 2b-2 Task 2): the
    `capability_fn` constructor arg — `capability_discovery.default_capabilities`
    by default — is called with this node's output_kind and returns a
    `list[capability_discovery.Capability]` mixing native engine intents and
    plugin capabilities, each already flagged live/latent against real
    plugin-load and backend-health state. Live entries are clickable and add a
    step; latent entries render disabled/greyed with their `reason` (e.g.
    "start a video model") as a tooltip and add nothing when clicked. If
    `capability_fn` returns nothing (e.g. an injected test fake, or discovery
    finding no plugins at all), the picker falls back to
    `intent_vocab.compatible_intents(<output_kind>)` wrapped as live native
    Capability stand-ins, so it never renders empty for a kind that genuinely
    has native next-steps. Layout follows the validated mockup at
    `.superpowers/brainstorm/988333-1783804257/content/add-step-wingit.html`
    (live capabilities section) building on
    `.superpowers/brainstorm/988333-1783804257/content/intent-composer.html`.

    Each step card also carries the same mockup's "imagination-first" wing-it
    box (SP-C Phase 2b-3 Task 2): a free-form `Gtk.Entry` + "✨ Compose it"
    button, alongside the capability picker rather than replacing it. The
    `wingit_fn` constructor arg — a real closure wiring
    `wingit.map_freeform_to_step` + `capability_discovery.default_capabilities`
    + `wingit.default_llm_fn` by default — maps the typed text plus that
    step's `output_kind` to a `wingit.WingitResult | None`. The default
    closure may hit the network (the LLM pass inside `default_llm_fn`), so
    `_on_wingit_compose_clicked` runs it on a daemon `threading.Thread` and
    applies the result on the main thread via
    `GLib.idle_add(self._apply_wingit_result, ...)` — the GTK threading rule
    in this repo's CLAUDE.md. Tests inject a synchronous fake and monkeypatch
    `threading.Thread`/`GLib.idle_add` to run inline (same pattern
    `PipelineStudio`'s open-run tests already use), so the fake path needs no
    special-casing. A `WingitResult` is added exactly like a capability
    picker choice — `add_step_after(node_id, result.class_type,
    params=result.params)`, which already commits pending edits, re-renders,
    and guards a kind-incompatible `ValueError`. `None` (nothing mapped) shows
    the same gentle inline `_show_message` the structural guards use and adds
    nothing.

    Data arrives ONLY through `set_run(run, spec_path)` — like Discover/
    OpenView, no `PipelineStore` import here. `set_run` loads a WORKING SPEC
    DICT via `pipeline_engine.load_spec` (pure spec-file IO, not a store/
    history access) into `self.working_spec`; every subsequent render walks
    `pipeline_engine.topo_order(self.working_spec)` and looks up each node's
    `intent_vocab.intent_for(class_type)` directly — NOT `run.steps` — since
    add/remove can change the graph's shape (new nodes `run.steps` never
    knew about) after the very first render.

    `add_step_after(node_id, class_type)` / `remove_step_by_id(node_id)` call
    `spec_remix.add_step`/`remove_step` on `self.working_spec`, catching
    `ValueError` (e.g. a kind-incompatible add, or a caller-forced remove of
    a node that breaks validation) and showing a brief inline message instead
    of crashing — the composer picker itself only ever offers kind-compatible
    choices, so the guard is defensive, not the primary UX gate. Either
    operation first calls `_commit_pending_edits()`, which bakes any
    already-typed-but-not-yet-run param-field edits into `working_spec` via
    `spec_remix.apply_edits` BEFORE the structural change re-renders and
    rebuilds every field widget from scratch — otherwise an in-progress edit
    in one card would be silently discarded by clicking Remove/Add on
    another.

    `current_spec() -> dict` returns `working_spec` with any still-pending
    field edits applied (again via `spec_remix.apply_edits`) — this is the
    FINAL spec a Run should materialize. `run-remix` still emits
    `(str spec_path, object edits)` exactly as Phase 2a did (the base
    spec_path + only-the-param-edits dict — kept for the base-name lookup and
    `PipelineStore.create_run`'s `param_overrides` bookkeeping), but
    PipelineStudio's `_on_run_remix` (Phase 2b-1 Task 4) does NOT feed
    spec_path/edits into `spec_remix.derive_spec` — it calls
    `remix_view.current_spec()` (the emitting widget itself) to get the
    composed graph, re-merges the base spec file's top-level `_`-keys back in
    (`current_spec()`'s `working_spec` came from `pipeline_engine.load_spec`,
    which strips them), and writes THAT via `spec_remix.write_spec` — so
    structural add/remove changes actually make it into the run, which
    `derive_spec` alone could never see since it re-reads spec_path from disk.

`LiveRunView(Gtk.Box)`
    The "watch it run" page (SP-C Phase 2a Task 3): one row per step
    (intent-labelled, same verb/noun/model presentation as Open/RemixView)
    all starting PENDING, plus a live-log tail alongside. Layout follows the
    validated mockup at
    `.superpowers/brainstorm/988333-1783804257/content/run-watch.html`. Unlike
    Discover/Open/RemixView, data doesn't arrive once via a `set_*()` call —
    `begin(run)` renders the initial PENDING state, then three handler
    methods (`on_node_update`, `on_log`, `on_finished`) update it live. Their
    signatures match `pipeline_runner.PipelineRunner`'s callbacks EXACTLY —
    `on_node_update(job, node_id, status, detail)` and `on_finished(success)`
    are literally what a caller passes as `PipelineRunner.start()`'s
    `on_node_update=`/`on_run_finished=` arguments — so this view owns no
    subprocess/PipelineRunner itself; wiring a real runner to it is Task 4's
    job. The runner's synthetic `job="__health__"` node update (chip-health
    warnings — never a real step) renders as a small note instead of a step
    row. `on_finished` emits the custom `run-done` signal (str run_id) after
    resolving any step still "running" to done/failed.

`PipelineStudio(Gtk.Box)`
    The shell: a Gtk.Stack with "discover" (DiscoverView), "open" (OpenView,
    wrapped with a "← Discover" back control), "remix" (RemixView, wrapped
    with a "← Back" control), and "run" (LiveRunView, wrapped with a "← Back"
    control) pages — the full SP-C Phase 2a Task 4 loop: Open → Remix → Run
    → done. Loads runs off the GTK main thread — via
    `pipeline_view_model.list_run_views(PipelineStore())` in a daemon thread —
    then hands them to `DiscoverView.set_runs` through `GLib.idle_add`, per
    the GTK threading rule in this repo's CLAUDE.md (never touch widgets from
    a background thread). `DiscoverView`'s "open-run" signal triggers the
    same pattern for a single run: build its `RunView` off-thread
    (`build_run_view(PipelineStore().get_run(run_id))`), then `GLib.idle_add`
    both `OpenView.set_run` and the stack switch.

    The rest of the loop, wired here:

    - `OpenView`'s "remix-request" → `RemixView.set_run(current_run, spec_path)`
      for whichever run is currently open, then switch to "remix".
    - `RemixView`'s "run-remix" (spec_path, edits) → take the emitting
      `RemixView`'s `current_spec()` (the composed graph — every add/remove
      structural edit included), re-merge the base spec file's top-level
      `_`-keys back in (`current_spec()` strips them via
      `pipeline_engine.load_spec`; see `_with_preserved_top_level_metadata`),
      and write THAT via
      `spec_remix.write_spec` under `REMIXES_DIR` (Phase 2b-1 Task 4 — Phase
      2a's `derive_spec(spec_path, edits)` call is gone: it re-reads spec_path
      fresh off disk and would silently drop any structural edit). Create a
      provisional run record for the written path
      (`PipelineStore.create_run`) so `LiveRunView.begin()` has a real
      `RunView` to render (every step PENDING), switch to "run", then
      construct a `PipelineRunner` and `.start()` it with the `LiveRunView`'s
      own `on_node_update`/`on_log`/`on_finished` bound directly as its
      callbacks (their signatures already match exactly — see LiveRunView's
      docstring), passing `run_id=<the provisional id>` so the runner adopts
      that SAME record (patching in the real subprocess pid via
      `PipelineStore.update_pid`) instead of minting a second, divergent one
      — the provisional record IS the run's single live `PipelineStore`
      entry throughout, so every node/output/finish update lands where
      `LiveRunView` (and later Open's rebuild) actually look.
    - `LiveRunView`'s "run-done" (run_id) → rebuild the Open page from that
      run id's current record (`build_run_view(PipelineStore().get_run(run_id))`,
      off-thread, same `GLib.idle_add` pattern as `open-run`) and switch back
      to "open".
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, GObject, Gtk  # noqa: E402

import capability_discovery  # noqa: E402
import showcase  # noqa: E402
import wingit  # noqa: E402
from intent_vocab import compatible_intents, intent_for, label  # noqa: E402
from pipeline_engine import load_spec, topo_order  # noqa: E402
from pipeline_runner import PipelineRunner  # noqa: E402
from pipeline_store import PipelineStore  # noqa: E402
from pipeline_view_model import RunView, StepView, build_run_view, list_run_views  # noqa: E402
from spec_remix import add_step, apply_edits, editable_params, remove_step, write_spec  # noqa: E402

log = logging.getLogger(__name__)

# Where derived/composed remix spec files land (SP-C Phase 2a Task 4; Phase
# 2b-1 Task 4 pointed the write path at spec_remix.write_spec directly). A
# sibling of PipelineStore's own workflow-runs/ dir under the same app data
# root — see spec_remix.write_spec's docstring for the naming scheme used
# inside it (remix_<base_stem>_<n>.json).
REMIXES_DIR = Path.home() / ".local" / "share" / "tt-local-generator" / "remixes"

# Where "Build showcase" (SP-C Phase 3 Task 2) writes its self-contained HTML
# pages — a sibling of REMIXES_DIR under the same app data root. See
# `showcase.write_showcase`'s docstring for the filename scheme used inside it
# (showcase_<slug(title)>_<n>.html).
SHOWCASES_DIR = Path.home() / ".local" / "share" / "tt-local-generator" / "showcases"


def _default_remix_jobs() -> "list[dict]":
    """The job list passed to PipelineStore.create_run/PipelineRunner.start
    for a remix run.

    Pipeline mode's run_workflow.sh drives one spec file end-to-end and
    reports NODE:/LOG: signals per node regardless of "job name" — the
    multi-job fan-out (one job per prompt) that PipelineStore's job_states
    shape supports is a batch-UI concept, not something remix needs. A
    single named job is enough bookkeeping for create_run()'s job_states/
    playlist_ids dicts.
    """
    return [{"name": "remix", "prompt": ""}]


def _with_preserved_top_level_metadata(spec: dict, base_spec_path: str) -> dict:
    """Copy any top-level ``_``-prefixed key from *base_spec_path* that *spec* lacks.

    `RemixView.current_spec()` is built from `working_spec`, which was loaded
    via `pipeline_engine.load_spec` — that function STRIPS top-level
    ``_``-prefixed keys (``_spec_version``, ``_comment``) since they aren't
    part of the node graph `load_spec`/`topo_order` care about. That means
    `current_spec()` never carries them either, unlike `spec_remix.derive_spec`
    (Phase 2a), which reads the RAW base file specifically to keep them. This
    restores that same guarantee for the composer's write path (Phase 2b-1
    Task 4) — a composed run preserves ``_spec_version``/``_comment`` exactly
    like a param-only remix did. Returns a NEW dict; *spec* is never mutated.
    """
    raw_base = json.loads(Path(base_spec_path).read_text())
    merged = dict(spec)
    for key, value in raw_base.items():
        if key.startswith("_") and key not in merged:
            merged[key] = value
    return merged


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
.ps-composer-remove {
    color: #FF9E8A;
    font-size: 11px;
    border-radius: 8px;
    padding: 4px 8px;
}
.ps-composer-add {
    color: #4FD1C5;
    font-size: 11px;
    border-radius: 8px;
    padding: 4px 8px;
}
.ps-wingit-row {
    margin-top: 2px;
    margin-bottom: 2px;
}
.ps-wingit-compose {
    background-color: #4FD1C5;
    color: #062c28;
    font-weight: 700;
    font-size: 11.5px;
    border-radius: 8px;
    padding: 4px 12px;
}
.ps-cap-label {
    font-size: 12px;
    font-weight: 600;
    color: #eef8f6;
}
.ps-cap-detail {
    font-size: 10px;
    color: #6f948d;
}
.ps-cap-latent {
    opacity: 0.5;
}
.ps-composer-message {
    font-size: 11.5px;
    color: #FF9E8A;
    background-color: alpha(#FF9E8A, 0.12);
    border-radius: 8px;
    padding: 4px 10px;
}
.ps-health-note {
    font-size: 11px;
    color: #F6BC42;
    background-color: alpha(#F6BC42, 0.12);
    border-radius: 8px;
    padding: 4px 10px;
}
.ps-log-panel {
    background-color: #040d0c;
    border-left: 1px solid alpha(#74C5DF, 0.16);
}
.ps-log-line {
    font-family: monospace;
    font-size: 11px;
    color: #bcd7d2;
}
.ps-log-switch {
    font-family: monospace;
    font-size: 11px;
    color: #F6BC42;
    padding: 4px 8px;
    border-left: 2px solid #F6BC42;
    background-color: alpha(#F6BC42, 0.08);
    border-radius: 0 6px 6px 0;
}
.ps-showcase-btn {
    background-color: #F6BC42;
    color: #3a2a00;
    font-weight: 650;
    border-radius: 9px;
    padding: 8px 16px;
    font-size: 12.5px;
}
.ps-showcase-open-btn {
    color: #74C5DF;
    font-size: 12px;
}
.ps-showcase-path {
    font-family: monospace;
    font-size: 11px;
    color: #9bc0ba;
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

    Also the home of the "Build showcase" capstone (SP-C Phase 3 Task 2):
    a run — finished or not, this view doesn't gate on it — can be turned
    into a self-contained, shareable HTML page via the `showcase_fn` seam
    (production default: `showcase.write_showcase`, writing into
    `SHOWCASES_DIR`). Generation touches disk and downscales/base64-encodes
    every artifact, so it runs on a daemon thread (GTK threading rule, see
    CLAUDE.md) with the same busy-guard + `GLib.idle_add` reveal pattern
    RemixView's wing-it compose button already established — see
    `_on_build_showcase_clicked`/`_apply_showcase_result`.
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

    def __init__(self, showcase_fn: "Callable[[RunView], str] | None" = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("ps-discover")  # same dark-teal page background
        _apply_css()

        # "Build showcase" seam (SP-C Phase 3 Task 2): tests inject a fake
        # `showcase_fn(run_view) -> str` (or one that raises); production
        # default builds a real page and writes it under SHOWCASES_DIR.
        self._showcase_fn: "Callable[[RunView], str]" = showcase_fn or (
            lambda run_view: showcase.write_showcase(run_view, SHOWCASES_DIR)
        )
        self._run_view: "RunView | None" = None
        self._showcase_path: "str | None" = None

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

        # ── "Build showcase" capstone footer ────────────────────────────────
        showcase_footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        showcase_footer.set_margin_top(4)
        showcase_footer.set_margin_bottom(4)
        showcase_footer.set_margin_start(18)
        showcase_footer.set_margin_end(18)

        self._showcase_btn = Gtk.Button(label="✦ Build showcase")
        self._showcase_btn.add_css_class("ps-showcase-btn")
        self._showcase_btn.connect("clicked", self._on_build_showcase_clicked)
        showcase_footer.append(self._showcase_btn)

        self._showcase_open_btn = Gtk.Button(label="Open →")
        self._showcase_open_btn.add_css_class("ps-showcase-open-btn")
        self._showcase_open_btn.connect("clicked", self._on_open_showcase_clicked)
        self._showcase_open_btn.set_visible(False)
        showcase_footer.append(self._showcase_open_btn)

        self._showcase_path_label = Gtk.Label(label="")
        self._showcase_path_label.set_xalign(0)
        self._showcase_path_label.set_wrap(True)
        self._showcase_path_label.set_hexpand(True)
        self._showcase_path_label.add_css_class("ps-showcase-path")
        self._showcase_path_label.set_visible(False)
        showcase_footer.append(self._showcase_path_label)

        self.append(showcase_footer)

        # Hidden until a showcase build fails (see _show_showcase_message) —
        # same gentle-inline-message pattern as RemixView's _message_label.
        self._showcase_message_label = Gtk.Label(label="")
        self._showcase_message_label.set_xalign(0)
        self._showcase_message_label.add_css_class("ps-composer-message")
        self._showcase_message_label.set_visible(False)
        self._showcase_message_label.set_margin_top(2)
        self._showcase_message_label.set_margin_bottom(10)
        self._showcase_message_label.set_margin_start(18)
        self._showcase_message_label.set_margin_end(18)
        self.append(self._showcase_message_label)

    # ── Public API ───────────────────────────────────────────────────────────

    def set_run(self, run: RunView) -> None:
        """(Re)build the step list for *run*. Main-thread only, repeat-safe.

        Also resets the showcase capstone's state (button re-enabled, any
        previously-revealed path/message hidden) — a stale result from a
        previously-loaded run must never bleed into a freshly-loaded one.
        """
        self._title_label.set_label(run.title)
        self._run_view = run
        self._showcase_btn.set_sensitive(True)
        self._hide_showcase_message()
        self._hide_showcase_result()

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

    # ── "Build showcase" capstone ────────────────────────────────────────────

    def _on_build_showcase_clicked(self, _button: Gtk.Button) -> None:
        """Build a shareable showcase page for the currently loaded run.

        Runs `self._showcase_fn` on a daemon thread — the production default
        (`showcase.write_showcase`) downscales/base64-encodes every artifact,
        which can be slow for a run with a large video, so it must never
        block the GTK main thread (see CLAUDE.md's GTK threading rule).

        Busy-guard: the button is disabled synchronously here, before the
        worker thread is even started, so a second click while a build is in
        flight can't kick off a duplicate build. `_apply_showcase_result`
        (posted back via `GLib.idle_add`) re-enables it on both the success
        and failure path — same pattern as
        `RemixView._on_wingit_compose_clicked`/`_apply_wingit_result`.
        """
        if self._run_view is None:
            return  # defensive: no run loaded yet, nothing to showcase

        self._showcase_btn.set_sensitive(False)
        self._hide_showcase_message()
        self._hide_showcase_result()

        run_view = self._run_view
        showcase_fn = self._showcase_fn

        def worker() -> None:
            try:
                path = showcase_fn(run_view)
            except Exception:
                # A raising showcase_fn must never crash the worker thread —
                # treated identically to any other build failure.
                GLib.idle_add(self._apply_showcase_result, None, True)
                return
            GLib.idle_add(self._apply_showcase_result, path, False)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_showcase_result(self, path: "str | None", failed: bool) -> None:
        """Apply a `showcase_fn` outcome from `_on_build_showcase_clicked`.

        Always runs on the main thread (posted via `GLib.idle_add`, or
        invoked directly by a test simulating that hop). Re-enables the
        Build showcase button on BOTH the success and failure path so a
        failed build never leaves the control stuck disabled.
        """
        self._showcase_btn.set_sensitive(True)
        if failed or not path:
            self._show_showcase_message("Couldn't build the showcase.")
            return
        self._showcase_path = path
        self._showcase_path_label.set_label(path)
        self._showcase_path_label.set_visible(True)
        self._showcase_open_btn.set_visible(True)

    def _on_open_showcase_clicked(self, _button: Gtk.Button) -> None:
        """Launch the just-built showcase file in the system default handler.

        Same pattern `main_window.DetailPanel._open_external` already uses
        for videos — `subprocess.Popen(["xdg-open", path])` (or `["open",
        path]` on macOS) — wrapped in try/except since a missing opener
        binary or an unreadable path must never crash the app.
        """
        if not self._showcase_path:
            return
        import platform
        import subprocess  # noqa: PLC0415 — local import, mirrors main_window's _open_external
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", self._showcase_path])
            else:
                subprocess.Popen(["xdg-open", self._showcase_path])
        except Exception:
            pass

    def _show_showcase_message(self, text: str) -> None:
        self._showcase_message_label.set_label(text)
        self._showcase_message_label.set_visible(True)

    def _hide_showcase_message(self) -> None:
        self._showcase_message_label.set_label("")
        self._showcase_message_label.set_visible(False)

    def _hide_showcase_result(self) -> None:
        self._showcase_path = None
        self._showcase_path_label.set_label("")
        self._showcase_path_label.set_visible(False)
        self._showcase_open_btn.set_visible(False)


class RemixView(Gtk.Box):
    """Remix page: edit a run's plain-language params and re-run it.

    "Change one thing, safely" (SP-C Phase 2a Task 2): every field is
    PRE-FILLED with the base run's actual current value, so hitting Run with
    zero edits reproduces the base run unchanged — the empty edits dict this
    view emits in that case is a documented no-op for
    `spec_remix.apply_edits` (the shared edit-application rule both
    `spec_remix.derive_spec` and this view's own `current_spec()` use). Data
    arrives ONLY through `set_run()`: this
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

    def __init__(
        self,
        capability_fn: "Callable[[str], list] | None" = None,
        wingit_fn: "Callable[[str, str], object] | None" = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("ps-discover")  # same dark-teal page background
        _apply_css()

        # Real deps by default (plugin manifests on disk + live plugin/backend
        # health); tests inject a fake so the picker is exercised without ever
        # touching disk/hardware. See class docstring for the full contract.
        self._capability_fn = capability_fn or capability_discovery.default_capabilities

        # Same seam pattern for wing-it (SP-C Phase 2b-3 Task 2): the default
        # closure wires the real mapper + real capability discovery + real LLM
        # call, none of which this module touches directly — tests inject a
        # plain fake `wingit_fn(text, output_kind) -> WingitResult | None`.
        self._wingit_fn = wingit_fn or (
            lambda text, output_kind: wingit.map_freeform_to_step(
                text, output_kind, capability_discovery.default_capabilities(output_kind),
                llm_fn=wingit.default_llm_fn,
            )
        )

        self._spec_path: "str | None" = None
        # The composer's WORKING SPEC DICT (Phase 2b-1 Task 3) — loaded fresh
        # by set_run() via pipeline_engine.load_spec, then mutated in place
        # (well: replaced wholesale — add_step/remove_step never mutate their
        # input, they return a new dict) by add_step_after/remove_step_by_id.
        # Every render walks THIS, not run.steps, so structural changes (new
        # nodes run.steps never knew about) show up immediately.
        self.working_spec: dict = {}
        # Populated by _render(), keyed by node_id then input key, in step/
        # field order (Python dicts preserve insertion order). Two parallel
        # dicts: the live widget (to read back the CURRENT value at Run time)
        # and (kind, original_value) (to know what "changed" means for that
        # widget's kind — see _read_widget_value/_collect_edits).
        self._field_widgets: "dict[str, dict[str, Gtk.Widget]]" = {}
        self._field_meta: "dict[str, dict[str, tuple]]" = {}
        # Composer controls, keyed by node_id — kept around purely so tests
        # can find/introspect a specific step's Remove button or add-after
        # popover without walking the widget tree.
        self._remove_buttons: "dict[str, Gtk.Button]" = {}
        self._add_after_buttons: "dict[str, Gtk.MenuButton]" = {}
        self._add_after_popovers: "dict[str, Gtk.Popover]" = {}
        self._add_after_choice_buttons: "dict[str, dict[str, Gtk.Button]]" = {}
        # Wing-it controls (SP-C Phase 2b-3 Task 2), keyed by node_id — same
        # "kept around so tests can find/introspect them" rationale as the
        # composer controls above.
        self._wingit_entries: "dict[str, Gtk.Entry]" = {}
        self._wingit_compose_buttons: "dict[str, Gtk.Button]" = {}

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

        # Hidden until an add/remove guard rejects an operation (see
        # _show_message/_hide_message) — a healthy composer's header stays
        # clean, matching LiveRunView's __health__ note pattern.
        self._message_label = Gtk.Label(label="")
        self._message_label.set_xalign(0)
        self._message_label.add_css_class("ps-composer-message")
        self._message_label.set_visible(False)
        self._message_label.set_margin_top(4)
        self._message_label.set_margin_start(18)
        self._message_label.set_margin_end(18)
        self.append(self._message_label)

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
        """Load *spec_path* into a fresh working spec dict and render it.

        Main-thread only, repeat-safe — a fresh `working_spec` is loaded
        every call (same pattern as DiscoverView.set_runs/OpenView.set_run),
        so re-opening the composer for a different run never carries over a
        previous run's structural edits.
        """
        self._spec_path = spec_path
        self._title_label.set_label(f"Remixing · {run.title}")
        self.working_spec = load_spec(spec_path)
        self._hide_message()
        self._render()

    def current_spec(self) -> dict:
        """The working spec with any still-pending param-field edits applied.

        This is the FINAL spec a "Run this remix" should materialize —
        structural add/remove changes are already baked into `working_spec`
        itself (every add/remove commits pending field edits first — see
        `_commit_pending_edits`), so this only needs to layer in whatever the
        user has typed since the last render.
        """
        return apply_edits(self.working_spec, self._collect_edits())

    # ── Composer: add / remove steps ────────────────────────────────────────

    def add_step_after(
        self, node_id: str, class_type: str, params: "dict | None" = None,
    ) -> None:
        """Add a new *class_type* node after *node_id*, then re-render.

        *params* is forwarded verbatim to `spec_remix.add_step` — used by the
        add-after picker's plugin capabilities to set the new
        TTLGArtgenGenerate node's `plugin` input (e.g.
        `params={"plugin": "verse"}`); native capabilities pass `None`.

        Commits any pending param-field edits into `working_spec` FIRST (see
        `_commit_pending_edits`) so a structural change never discards an
        in-progress edit elsewhere on the page. A `ValueError` from
        `spec_remix.add_step` (e.g. a kind-incompatible pairing forced past
        the picker, which only ever offers compatible choices) is caught and
        shown as a brief message rather than crashing — `working_spec` is
        left as committed (unchanged by the failed add) and still re-rendered
        so the committed edit is reflected.
        """
        self._commit_pending_edits()
        try:
            self.working_spec = add_step(self.working_spec, node_id, class_type, params=params)
        except ValueError as exc:
            self._show_message(f"Couldn't add that step: {exc}")
            self._render()
            return
        self._hide_message()
        self._render()

    def remove_step_by_id(self, node_id: str) -> None:
        """Remove *node_id* (rewiring its consumers), then re-render.

        Same commit-then-guard pattern as `add_step_after` — see its
        docstring.
        """
        self._commit_pending_edits()
        try:
            self.working_spec = remove_step(self.working_spec, node_id)
        except ValueError as exc:
            self._show_message(f"Couldn't remove that step: {exc}")
            self._render()
            return
        self._hide_message()
        self._render()

    def _commit_pending_edits(self) -> None:
        """Bake current param-field widget values into `working_spec`.

        Called before every structural change: `_render()` (invoked right
        after add/remove) tears down and rebuilds every field widget from
        `working_spec`'s literal values, so any edit sitting in a widget that
        hasn't been "Run" yet would otherwise be silently lost.
        """
        edits = self._collect_edits()
        if edits:
            self.working_spec = apply_edits(self.working_spec, edits)

    def _show_message(self, text: str) -> None:
        self._message_label.set_label(text)
        self._message_label.set_visible(True)

    def _hide_message(self) -> None:
        self._message_label.set_label("")
        self._message_label.set_visible(False)

    def _add_after_intents_for(self, node_id: str) -> "list[capability_discovery.Capability]":
        """Dynamic add-after `Capability` list for *node_id*'s output, or
        `[]` if it has no `output_kind` (e.g. an unrecognized/generic-fallback
        intent).

        Delegates to `self._capability_fn(output_kind)` (see class docstring)
        rather than the static `intent_vocab.compatible_intents` directly.
        Falls back to a live-native-only Capability list synthesized from
        `compatible_intents` when `capability_fn` returns nothing (an empty
        test fake, or — defensively — an unexpected exception from the real
        discovery path) so the picker degrades gracefully instead of ever
        rendering a false "nothing can follow this" empty state or crashing.
        """
        node = self.working_spec.get(node_id, {})
        class_type = node.get("class_type", "")
        output_kind = intent_for(class_type).output_kind
        if not output_kind:
            return []
        try:
            caps = self._capability_fn(output_kind)
        except Exception:
            caps = None
        if caps:
            return caps
        return [
            capability_discovery.Capability(
                id=intent.class_type,
                label=label(intent.class_type),
                kind_out=intent.output_kind,
                kind_in=intent.input_kind,
                source="native",
                class_type=intent.class_type,
                plugin=None,
                hardware=None,
                live=True,
                reason=None,
            )
            for intent in compatible_intents(output_kind)
        ]

    # ── Row building ─────────────────────────────────────────────────────────

    def _render(self) -> None:
        """(Re)build the whole step-card list from `self.working_spec`.

        Walks `pipeline_engine.topo_order(working_spec)` — NOT `run.steps` —
        so nodes added/removed since the initial `set_run()` render
        correctly (see class docstring for why `run.steps` can't be trusted
        after a structural edit).
        """
        while child := self._steps_box.get_first_child():
            self._steps_box.remove(child)
        self._field_widgets = {}
        self._field_meta = {}
        self._remove_buttons = {}
        self._add_after_buttons = {}
        self._add_after_popovers = {}
        self._add_after_choice_buttons = {}
        self._wingit_entries = {}
        self._wingit_compose_buttons = {}

        order = topo_order(self.working_spec)
        if not order:
            empty = Gtk.Label(label="This run has no steps to remix.")
            empty.add_css_class("ps-empty-msg")
            self._steps_box.append(empty)
            return

        params_by_node = editable_params(self.working_spec)
        for index, node_id in enumerate(order, start=1):
            class_type = self.working_spec[node_id].get("class_type", "")
            fields = params_by_node.get(node_id, [])
            self._steps_box.append(self._build_step_card(index, node_id, class_type, fields))

    def _build_step_card(self, index: int, node_id: str, class_type: str,
                          fields: list) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("ps-step")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        n_label = Gtk.Label(label=str(index))
        n_label.add_css_class("ps-step-n")
        n_label.set_valign(Gtk.Align.START)
        head.append(n_label)

        intent = intent_for(class_type)

        verb_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        verb_col.set_hexpand(True)
        verb_label = Gtk.Label(label=intent.verb)
        verb_label.set_xalign(0)
        verb_label.add_css_class("ps-step-verb")
        verb_col.append(verb_label)

        noun_label = Gtk.Label(label=intent.noun)
        noun_label.set_xalign(0)
        noun_label.add_css_class("ps-step-noun")
        verb_col.append(noun_label)

        # model_label is a quiet secondary detail — omitted entirely (not
        # shown blank) when the intent doesn't name an underlying tool,
        # mirroring OpenView._build_step_row's identical guard.
        if intent.model_label:
            model_label = Gtk.Label(label=intent.model_label)
            model_label.set_xalign(0)
            model_label.add_css_class("ps-step-model")
            verb_col.append(model_label)

        head.append(verb_col)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        controls.set_valign(Gtk.Align.START)

        remove_btn = Gtk.Button(label="Remove")
        remove_btn.add_css_class("ps-composer-remove")
        remove_btn.connect("clicked", lambda _b, nid=node_id: self.remove_step_by_id(nid))
        controls.append(remove_btn)
        self._remove_buttons[node_id] = remove_btn

        add_after_btn = self._build_add_after_button(node_id)
        controls.append(add_after_btn)
        self._add_after_buttons[node_id] = add_after_btn

        head.append(controls)
        card.append(head)

        card.append(self._build_wingit_row(node_id))

        node_widgets: "dict[str, Gtk.Widget]" = {}
        node_meta: "dict[str, tuple]" = {}
        for field in fields:
            row, widget = self._build_field_row(field)
            card.append(row)
            node_widgets[field.key] = widget
            node_meta[field.key] = (field.kind, field.value)

        if node_widgets:
            self._field_widgets[node_id] = node_widgets
            self._field_meta[node_id] = node_meta

        return card

    def _build_add_after_button(self, node_id: str) -> Gtk.MenuButton:
        """A "＋ add a step after" control whose popover lists the dynamic
        capabilities compatible with *node_id*'s output kind
        (`_add_after_intents_for`) — live ones enabled, latent ones greyed
        with their reason (see `add-step-wingit.html` mockup)."""
        menu_button = Gtk.MenuButton(label="＋ add a step after")  # ＋
        menu_button.add_css_class("ps-composer-add")

        popover = Gtk.Popover()
        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        popover_box.set_margin_top(6)
        popover_box.set_margin_bottom(6)
        popover_box.set_margin_start(6)
        popover_box.set_margin_end(6)

        choices = self._add_after_intents_for(node_id)
        # Keyed by Capability.id, NOT class_type — every plugin capability
        # shares the same class_type (TTLGArtgenGenerate), so class_type
        # would collide across plugins; `id` is the stable per-capability
        # identifier (see Capability's docstring in capability_discovery.py).
        choice_buttons: "dict[str, Gtk.Button]" = {}
        if not choices:
            none_label = Gtk.Label(label="No compatible next step")
            none_label.add_css_class("ps-empty-msg")
            popover_box.append(none_label)
        for cap in choices:
            item = Gtk.Button()
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            main_label = Gtk.Label(label=cap.label)
            main_label.set_xalign(0)
            main_label.add_css_class("ps-cap-label")
            content.append(main_label)

            # Quiet secondary detail: the latent reason ("start a video
            # model") when greyed, or the plugin name for a live plugin
            # capability (native capabilities have no such detail to show).
            detail_text = cap.reason if not cap.live else cap.plugin
            if detail_text:
                detail_label = Gtk.Label(label=detail_text)
                detail_label.set_xalign(0)
                detail_label.add_css_class("ps-cap-detail")
                content.append(detail_label)

            item.set_child(content)

            if cap.live:
                item.add_css_class("ps-btn-ghost")
                params = {"plugin": cap.plugin} if cap.source == "plugin" else None
                item.connect(
                    "clicked", self._on_add_after_chosen, node_id, cap.class_type, popover, params,
                )
            else:
                item.add_css_class("ps-cap-latent")
                item.set_sensitive(False)
                if cap.reason:
                    item.set_tooltip_text(cap.reason)
                # No click handler: a disabled Gtk.Button never emits
                # "clicked", but the guard is explicit in intent too — a
                # latent capability must never add a step.

            popover_box.append(item)
            choice_buttons[cap.id] = item

        popover.set_child(popover_box)
        menu_button.set_popover(popover)

        self._add_after_popovers[node_id] = popover
        self._add_after_choice_buttons[node_id] = choice_buttons
        return menu_button

    def _on_add_after_chosen(
        self, _button: Gtk.Button, node_id: str, class_type: str,
        popover: Gtk.Popover, params: "dict | None",
    ) -> None:
        popover.popdown()
        self.add_step_after(node_id, class_type, params=params)

    # ── Wing-it: free-form "describe the next step" (SP-C Phase 2b-3 Task 2) ─

    def _build_wingit_row(self, node_id: str) -> Gtk.Widget:
        """The "imagination-first" escape hatch alongside the capability
        picker — a free-form entry + "✨ Compose it" button, per the top
        "wing" box of `add-step-wingit.html`. Purely presentational; the
        actual mapping happens in `_on_wingit_compose_clicked`."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("ps-wingit-row")

        entry = Gtk.Entry()
        entry.set_placeholder_text(
            "Say what should happen next, in your own words…"
        )
        entry.add_css_class("ps-field-entry")
        entry.set_hexpand(True)
        row.append(entry)
        self._wingit_entries[node_id] = entry

        compose_btn = Gtk.Button(label="✨ Compose it")
        compose_btn.add_css_class("ps-wingit-compose")
        compose_btn.connect("clicked", self._on_wingit_compose_clicked, node_id)
        row.append(compose_btn)
        self._wingit_compose_buttons[node_id] = compose_btn

        return row

    def _on_wingit_compose_clicked(self, _button: Gtk.Button, node_id: str) -> None:
        """Map *node_id*'s wing-it entry text to a step via `self._wingit_fn`.

        An empty/whitespace-only entry is a no-op — Compose does nothing
        rather than reporting a spurious "couldn't compose that". Otherwise
        `wingit_fn` runs on a daemon thread (the default closure's LLM call
        may hit the network — GTK threading rule, see CLAUDE.md) and the
        result is applied back on the main thread via
        `GLib.idle_add(self._apply_wingit_result, ...)`. A raising
        `wingit_fn` is treated the same as a `None` result (gentle message,
        nothing added) rather than crashing the worker thread.

        Busy-guard: the step's Compose button is disabled synchronously
        (main thread, before the worker thread is even started) so a second
        click while a mapping is in flight can't spawn a second worker/add a
        duplicate step. `_apply_wingit_result` re-enables it — see there.
        """
        entry = self._wingit_entries.get(node_id)
        if entry is None:
            return
        text = entry.get_text().strip()
        if not text:
            return

        compose_btn = self._wingit_compose_buttons.get(node_id)
        if compose_btn is not None:
            compose_btn.set_sensitive(False)

        class_type = self.working_spec.get(node_id, {}).get("class_type", "")
        output_kind = intent_for(class_type).output_kind
        wingit_fn = self._wingit_fn

        def worker() -> None:
            try:
                result = wingit_fn(text, output_kind)
            except Exception:
                result = None
            GLib.idle_add(self._apply_wingit_result, node_id, result)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_wingit_result(self, node_id: str, result) -> None:
        """Apply a `wingit.WingitResult` (or `None`) from
        `_on_wingit_compose_clicked`. Always runs on the main thread (posted
        via `GLib.idle_add`).

        `None` means wing-it couldn't map the text to any live capability at
        all — shown as a gentle inline message via the same `_show_message`
        the add/remove structural guards use, adding nothing. A real result
        is added exactly like a capability-picker choice: `add_step_after`
        already commits pending edits, re-renders, and guards a
        kind-incompatible `ValueError` with its own message.

        Re-enables the Compose button `_on_wingit_compose_clicked` disabled
        before starting the worker — on BOTH the None and the real-result
        path (`try/finally`, so a `_render()`/`add_step_after` bug can't
        leave the button stuck disabled). The reference is grabbed *before*
        either branch runs, because both branches re-render (`_render()`
        directly, or indirectly via `add_step_after`), which rebuilds
        `_wingit_compose_buttons` with brand-new (already-sensitive) widgets
        — looking the button up fresh afterwards would silently re-enable
        the wrong (new) object and never touch the one this click actually
        disabled. Re-enabling the old, possibly-detached widget is harmless
        either way, defensively wrapped so a stale reference can't crash.
        """
        compose_btn = self._wingit_compose_buttons.get(node_id)
        try:
            if result is None:
                self._show_message("Couldn't turn that into a step — try rephrasing.")
                self._render()
                return
            self.add_step_after(node_id, result.class_type, params=result.params)
        finally:
            if compose_btn is not None:
                try:
                    compose_btn.set_sensitive(True)
                except Exception:
                    pass

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


class LiveRunView(Gtk.Box):
    """Live-run page: watch a pipeline run's progress in real time (SP-C Phase 2a Task 3).

    Renders one row per `RunView.steps` (intent-labelled, same verb/noun/model
    presentation as Open/RemixView) plus a scrolling live-log tail, per the
    validated mockup at
    `.superpowers/brainstorm/988333-1783804257/content/run-watch.html`. Unlike
    Discover/Open/RemixView, data doesn't arrive once through a `set_*()`
    call — `begin(run)` renders the initial PENDING state, and three handler
    methods keep it live. Their signatures match
    `pipeline_runner.PipelineRunner`'s callbacks EXACTLY, so a caller (Task 4)
    can wire a real runner straight to this view:

        runner.start(spec_path, jobs, overrides,
                     on_node_update=view.on_node_update,
                     on_run_finished=view.on_finished)

    This view owns NO PipelineRunner/subprocess itself — `begin()` takes a
    plain RunView and the `on_*` methods are pure handlers, matching the rule
    Discover/Open/RemixView already follow for staying store/engine-free.

    Per the mockup, board-switch `LOG:` lines ("resetting boards", "starting
    server") are surfaced as first-class "switch" rows instead of plain log
    text — transparency about the real hardware behaviour (stopping the
    container, `tt-smi -r`, re-warming) is a feature, not noise to hide. The
    runner's synthetic `job="__health__"` node update (chip-health/reattach
    warnings — never a real pipeline step) renders as a small note instead of
    a step row, so it can never be mistaken for one or crash the lookup.
    """

    __gsignals__ = {
        # (run_id,) — emitted once from on_finished(), after any step still
        # "running" has been resolved to done/failed.
        "run-done": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    # Reuse OpenView's status glyph/CSS maps — same status vocabulary
    # (done/running/pending/failed) and same visual language, so a step
    # looks identical whether you're browsing it after the fact (OpenView)
    # or watching it happen live (this view).
    _STATUS_GLYPH = OpenView._STATUS_GLYPH
    _STATUS_CSS = OpenView._STATUS_CSS

    # Substrings (case-insensitive) that mark a LOG: line as a board-switch
    # event per the mockup ("LOG:  resetting boards (flux → skyreels)",
    # "LOG: starting server flux") — surfaced as first-class rows instead of
    # plain log text. Matched against the line with the leading "LOG:" intact
    # since on_log() receives the runner's raw stdout line verbatim.
    _SWITCH_MARKERS = ("resetting boards", "starting server")

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("ps-discover")  # same dark-teal page background
        _apply_css()

        self._run_id: "str | None" = None
        # node_id -> current status string ("pending"/"running"/"done"/
        # "failed"), tracked in parallel with the rendered glyph so
        # on_finished() can tell which steps were still "running" without
        # reverse-parsing a glyph character back into a status name.
        self._step_status: "dict[str, str]" = {}
        # node_id -> that step's status-glyph Gtk.Label, updated in place by
        # on_node_update/on_finished (rows themselves are never rebuilt after
        # begin() — only this label's text/css-class changes).
        self._step_status_labels: "dict[str, Gtk.Label]" = {}

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

        # Hidden until the runner sends a __health__ update (see
        # _show_health_note) so a healthy run's header stays clean.
        self._health_note = Gtk.Label(label="")
        self._health_note.add_css_class("ps-health-note")
        self._health_note.set_visible(False)
        header.append(self._health_note)

        self.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        body.set_vexpand(True)
        self.append(body)

        steps_scroller = Gtk.ScrolledWindow()
        steps_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        steps_scroller.set_vexpand(True)
        steps_scroller.set_hexpand(True)
        body.append(steps_scroller)

        self._steps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._steps_box.set_margin_top(10)
        self._steps_box.set_margin_bottom(18)
        self._steps_box.set_margin_start(18)
        self._steps_box.set_margin_end(18)
        steps_scroller.set_child(self._steps_box)

        # Live-log tail: a narrow fixed-width side panel, per the mockup's
        # two-column `.body { grid-template-columns: 1fr 300px }` layout.
        log_scroller = Gtk.ScrolledWindow()
        log_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        log_scroller.set_vexpand(True)
        log_scroller.set_size_request(280, -1)
        log_scroller.add_css_class("ps-log-panel")
        body.append(log_scroller)

        self._log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._log_box.set_margin_top(10)
        self._log_box.set_margin_bottom(10)
        self._log_box.set_margin_start(10)
        self._log_box.set_margin_end(10)
        log_scroller.set_child(self._log_box)

    # ── Public API ───────────────────────────────────────────────────────────

    def begin(self, run: RunView) -> None:
        """(Re)initialise the view for a fresh run: every step PENDING, log cleared.

        Main-thread only, repeat-safe — same clear-then-rebuild pattern as
        DiscoverView.set_runs/OpenView.set_run/RemixView.set_run, so calling
        begin() again (e.g. re-opening Live for a different run without
        recreating the widget) never leaves stale rows/state behind.
        """
        self._run_id = run.run_id
        self._title_label.set_label(run.title)
        self._health_note.set_visible(False)
        self._health_note.set_label("")

        while child := self._steps_box.get_first_child():
            self._steps_box.remove(child)
        while child := self._log_box.get_first_child():
            self._log_box.remove(child)
        self._step_status = {}
        self._step_status_labels = {}

        for index, step in enumerate(run.steps, start=1):
            row, status_label = self._build_step_row(index, step)
            self._steps_box.append(row)
            self._step_status[step.node_id] = "pending"
            self._step_status_labels[step.node_id] = status_label

    # ── PipelineRunner callback handlers ─────────────────────────────────────
    #
    # Signatures below match pipeline_runner.PipelineRunner exactly: it
    # dispatches on_node_update(job, node_id, status, detail) via
    # self._idle_add for every NODE: line (_parse_line) plus the synthetic
    # job="__health__" chip-health signal (start()); on_finished(success) is
    # called once, with a single positional bool, when the run's subprocess
    # exits (_watch_stdout/_tail_log) — never a run_id, never keyword args.

    def on_node_update(self, job: str, node_id: str, status: str, detail: str) -> None:
        """Update the step row whose node_id matches, or show a health note.

        The runner's `__health__` job (today: node_id "__chips__" for a
        degraded chip-health check, or "__reattach__" for a stalled
        reattach — see pipeline_runner.py's start()/reattach()) is never a
        real pipeline step. Rather than hardcode one node_id, this checks
        `job == "__health__"` generically so any future __health__ signal
        still renders a note instead of crashing a node_id lookup.
        """
        if job == "__health__":
            self._show_health_note(node_id, status, detail)
            return

        label = self._step_status_labels.get(node_id)
        if label is None:
            # Unknown node_id — e.g. a stale callback delivered after begin()
            # was called again for a different run. Ignore rather than crash;
            # there is no step row to update.
            return

        self._step_status[node_id] = status
        self._set_status_glyph(label, status)

    def on_log(self, line: str) -> None:
        """Append one raw stdout line to the live log tail.

        Board-switch lines (per the mockup: "resetting boards", "starting
        server") are styled as first-class "switch" rows instead of plain
        log text — see _is_switch_line and this class's docstring.
        """
        text = line.rstrip("\n")
        row = Gtk.Label(label=text)
        row.set_xalign(0)
        row.set_wrap(True)
        row.add_css_class("ps-log-switch" if self._is_switch_line(text) else "ps-log-line")
        self._log_box.append(row)

    def on_finished(self, success: bool) -> None:
        """Resolve any step still "running" to done/failed, then emit run-done.

        Steps that never started ("pending") are left untouched: the run
        genuinely never reached them, so flipping them to done or failed
        would misrepresent what actually happened. Matches PipelineRunner's
        on_run_finished(success) call exactly — see this class's docstring.
        """
        resolved = "done" if success else "failed"
        for node_id, status in list(self._step_status.items()):
            if status == "running":
                self._step_status[node_id] = resolved
                self._set_status_glyph(self._step_status_labels[node_id], resolved)

        if self._run_id is not None:
            self.emit("run-done", self._run_id)

    # ── Row building / helpers ───────────────────────────────────────────────

    def _build_step_row(self, index: int, step: StepView) -> "tuple[Gtk.Widget, Gtk.Label]":
        """Build one PENDING step row; returns (row, status_label) so begin()
        can keep the label reference for later in-place glyph updates."""
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

        status_label = Gtk.Label(label=self._STATUS_GLYPH["pending"])
        status_label.add_css_class(self._STATUS_CSS["pending"])
        verb_row.append(status_label)
        main.append(verb_row)

        noun_label = Gtk.Label(label=step.intent.noun)
        noun_label.set_xalign(0)
        noun_label.add_css_class("ps-step-noun")
        main.append(noun_label)

        # model_label omitted entirely (not shown blank) when the intent
        # doesn't name an underlying tool — same guard as OpenView/RemixView.
        if step.intent.model_label:
            model_label = Gtk.Label(label=step.intent.model_label)
            model_label.set_xalign(0)
            model_label.add_css_class("ps-step-model")
            main.append(model_label)

        row.append(main)
        return row, status_label

    def _set_status_glyph(self, label: Gtk.Label, status: str) -> None:
        """Update *label*'s glyph text and status CSS class in place."""
        label.set_label(self._STATUS_GLYPH.get(status, "•"))
        for css in self._STATUS_CSS.values():
            label.remove_css_class(css)
        label.add_css_class(self._STATUS_CSS.get(status, "ps-status-pending"))

    def _is_switch_line(self, text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in self._SWITCH_MARKERS)

    def _show_health_note(self, node_id: str, status: str, detail: str) -> None:
        """Render the runner's synthetic __health__ signal as a small note.

        PipelineRunner only ever sends node_id="__chips__" (chip-health
        degraded — status e.g. "degraded", detail a human hint like "AC power
        cycle recommended") or "__reattach__" (a stalled reattach warning,
        status="warn") today. "__chips__" additionally gets a friendlier
        "N chips" phrasing when status is a bare count, since that's the
        most likely shape a future chip-count signal would take; any other
        __health__ shape still degrades to showing detail (or status if no
        detail) instead of crashing or being silently dropped.
        """
        if node_id == "__chips__" and status.isdigit():
            text = f"{status} chip{'' if status == '1' else 's'}"
        else:
            text = detail or status
        self._health_note.set_label(f"⚠ {text}" if text else "")
        self._health_note.set_visible(bool(text))


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

        # The run currently shown on the Open page — the source for
        # "Remix ..." (either button on OpenView). None until the first
        # open-run completes, and _on_remix_request no-ops if either is
        # still None (there is nothing open yet to remix).
        self._current_run_view: "Optional[RunView]" = None
        self._current_spec_path: "Optional[str]" = None

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.append(self.stack)

        self.discover = DiscoverView()
        self.discover.connect("open-run", self._on_open_run)
        self.stack.add_titled(self.discover, "discover", "Discover")

        # "open" page: a back-to-discover bar wrapped around the real OpenView.
        open_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        open_back_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        open_back_bar.set_margin_top(10)
        open_back_bar.set_margin_start(18)
        open_back_btn = Gtk.Button(label="← Discover")
        open_back_btn.add_css_class("ps-open-back")
        open_back_btn.add_css_class("ps-btn-ghost")
        open_back_btn.connect("clicked", self._on_back_to_discover)
        open_back_bar.append(open_back_btn)
        open_page.append(open_back_bar)

        self.open_view = OpenView()
        self.open_view.connect("remix-request", self._on_remix_request)
        self.open_view.set_vexpand(True)
        open_page.append(self.open_view)

        self.stack.add_titled(open_page, "open", "Open")

        # "remix" page: a back-to-open bar wrapped around RemixView.
        remix_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        remix_back_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        remix_back_bar.set_margin_top(10)
        remix_back_bar.set_margin_start(18)
        remix_back_btn = Gtk.Button(label="← Back")
        remix_back_btn.add_css_class("ps-open-back")
        remix_back_btn.add_css_class("ps-btn-ghost")
        remix_back_btn.connect("clicked", self._on_back_to_open)
        remix_back_bar.append(remix_back_btn)
        remix_page.append(remix_back_bar)

        self.remix_view = RemixView()
        self.remix_view.connect("run-remix", self._on_run_remix)
        self.remix_view.set_vexpand(True)
        remix_page.append(self.remix_view)

        self.stack.add_titled(remix_page, "remix", "Remix")

        # "run" page: a back-to-open bar wrapped around LiveRunView. Back
        # only navigates the UI away — it does not cancel the runner, which
        # keeps driving PipelineRunner.start()'s callbacks in the background
        # regardless of which stack page is visible.
        run_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        run_back_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        run_back_bar.set_margin_top(10)
        run_back_bar.set_margin_start(18)
        run_back_btn = Gtk.Button(label="← Back")
        run_back_btn.add_css_class("ps-open-back")
        run_back_btn.add_css_class("ps-btn-ghost")
        run_back_btn.connect("clicked", self._on_back_to_open)
        run_back_bar.append(run_back_btn)
        run_page.append(run_back_bar)

        self.live_run = LiveRunView()
        self.live_run.connect("run-done", self._on_run_done)
        self.live_run.set_vexpand(True)
        run_page.append(self.live_run)

        self.stack.add_titled(run_page, "run", "Run")

        self.stack.set_visible_child_name("discover")

        self._load_runs_async()

    def show_discover(self) -> None:
        """Reset the inner stack to "discover". Main-thread only.

        Called by MainWindow._show_pipelines every time the Pipelines toolbar
        toggle is re-activated, so leaving and re-entering Pipelines never
        strands the user on a stale Open/Remix/Run page from a previous
        visit — Discover is always the front door.
        """
        self.stack.set_visible_child_name("discover")

    def _on_open_run(self, _widget: DiscoverView, run_id: str) -> None:
        self._load_run_async(run_id)
        if self._on_open_run_cb is not None:
            self._on_open_run_cb(run_id)

    def _on_back_to_discover(self, _button: Gtk.Button) -> None:
        self.stack.set_visible_child_name("discover")

    def _on_back_to_open(self, _button: Gtk.Button) -> None:
        self.stack.set_visible_child_name("open")

    def _on_remix_request(self, _widget: "OpenView", node_id: str) -> None:
        """OpenView's "Remix from here"/"Remix whole pipeline" → open RemixView.

        node_id distinguishes the two buttons ("" for whole-pipeline) but
        RemixView edits the whole run either way today (see its docstring) —
        a future task may use node_id to scroll/focus that step's card. If
        nothing has been opened yet (both _current_* are None — shouldn't
        happen in practice since these buttons only exist inside a rendered
        OpenView, but defensive per this module's "always tolerate stale/
        missing data" rule), this is a no-op rather than opening RemixView
        with nothing to edit.
        """
        del node_id  # not yet used — see docstring
        if self._current_run_view is None or self._current_spec_path is None:
            return
        self.remix_view.set_run(self._current_run_view, self._current_spec_path)
        self.stack.set_visible_child_name("remix")

    def _on_run_remix(self, remix_view: "RemixView", spec_path: str, edits: dict) -> None:
        """RemixView's "Run this remix →" → compose, write, launch, and watch it live.

        Phase 2b-1 Task 4: RemixView is now a structural composer (Task 3) —
        its full edited graph (add/remove steps, plus any pending field edit)
        lives in `remix_view.current_spec()`, not in *edits* (which is only
        the param-edit dict `derive_spec` used to consume). Feeding *spec_path*
        + *edits* into `spec_remix.derive_spec` here — as Phase 2a did — would
        re-read *spec_path* fresh off disk and silently DROP every add/remove
        structural edit, since `derive_spec` has no way to see them. So this
        instead takes `remix_view.current_spec()` (the emitting widget itself
        — the composed graph), re-merges the base spec file's top-level
        ``_``-metadata back in (`current_spec()`'s `working_spec` came from
        `pipeline_engine.load_spec`, which strips it — see
        `_with_preserved_top_level_metadata`), and writes THAT via
        `spec_remix.write_spec` under the original spec's stem — so the
        composed graph is what actually runs.

        Composing/writing the spec and creating the provisional run record
        are pure/fast JSON I/O (same cost class as RemixView's own
        load_spec() call in set_run()), so they run synchronously here rather
        than off-thread — only the actual pipeline subprocess
        (PipelineRunner.start) does real background work. The provisional
        record created here IS the run's single PipelineStore record: run_id
        is passed straight through to PipelineRunner.start(run_id=...), which
        adopts it (patches in the real subprocess PID via update_pid())
        instead of minting a second, divergent record — see
        PipelineRunner.start's docstring.
        """
        REMIXES_DIR.mkdir(parents=True, exist_ok=True)
        final_spec = _with_preserved_top_level_metadata(remix_view.current_spec(), spec_path)
        derived_path = write_spec(final_spec, Path(spec_path).stem, str(REMIXES_DIR))

        jobs = _default_remix_jobs()
        store = PipelineStore()
        run_id = store.create_run(
            spec_path=derived_path,
            spec_name=Path(derived_path).stem,
            jobs=jobs,
            param_overrides=edits,
            pid=0,
            log_file="",
        )
        record = store.get_run(run_id)
        run_view = build_run_view(record)

        self.live_run.begin(run_view)
        self.stack.set_visible_child_name("run")

        runner = PipelineRunner(idle_add=GLib.idle_add)
        runner.start(
            derived_path,
            jobs,
            param_overrides=edits,
            on_node_update=self.live_run.on_node_update,
            on_run_finished=self.live_run.on_finished,
            on_log=self.live_run.on_log,
            run_id=run_id,
        )

    def _on_run_done(self, _widget: "LiveRunView", run_id: str) -> None:
        """LiveRunView's "run-done" → rebuild the Open page from the fresh record.

        Same off-thread-then-idle_add pattern as _load_run_async — building a
        RunView re-reads the spec + globs the output dir, so it's disk I/O
        that must not run on the GTK main thread.
        """
        def worker() -> None:
            try:
                record = PipelineStore().get_run(run_id)
                if record is None:
                    log.warning("run-done: no run found for id %s", run_id)
                    return
                run_view = build_run_view(record)
            except Exception:  # noqa: BLE001 — never let a load error crash the shell
                log.warning("failed to rebuild run view for %s", run_id, exc_info=True)
                return
            GLib.idle_add(self._show_run, run_view, record.get("spec_path"))

        threading.Thread(target=worker, daemon=True).start()

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
            GLib.idle_add(self._show_run, run_view, record.get("spec_path"))

        threading.Thread(target=worker, daemon=True).start()

    def _show_run(self, run_view: RunView, spec_path: "str | None") -> None:
        """Main-thread only: populate OpenView, remember it for remix, switch to it.

        spec_path is remembered (alongside the RunView) so a later
        "remix-request" from OpenView has something to hand RemixView.set_run
        — see _on_remix_request. It's accepted as possibly-None (e.g. an old
        record missing the key) rather than required, matching this module's
        "always tolerate stale/missing data" rule; _on_remix_request already
        no-ops if it's None.
        """
        self._current_run_view = run_view
        self._current_spec_path = spec_path
        self.open_view.set_run(run_view)
        self.stack.set_visible_child_name("open")
        return False
