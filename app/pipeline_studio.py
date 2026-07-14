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

Five pieces:

`DiscoverView(Gtk.Box)`
    Renders the hero + grid from a plain `list[RunView]` handed in via
    `set_runs()`. It does NOT import PipelineStore (or anything else that
    touches disk/history) itself — all data arrives through `set_runs`, which
    keeps this widget fully unit-testable with hand-built RunView fixtures
    and a display (see tests/test_pipeline_studio.py). Emits the custom
    `open-run` signal (str run_id) when a card's "Open" button is clicked;
    the caller (PipelineStudio, and eventually MainWindow) decides what that
    means (switch to the "open" stack page, drill into the run, etc). Also
    carries the "✦ Start from scratch" affordance (SP-C Phase 2b-3 Task 4) —
    a button, always visible above the hero/grid/empty state, that emits the
    custom `start-from-scratch` signal (no args); `PipelineStudio` wires this
    straight to `show_muse()` (blank canvas — no seed artifact).

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

`MuseView(Gtk.Box)`
    The Muse — a goal-first "start from scratch" creative wizard (SP-C Phase
    2b-3 Task 4). Where RemixView starts from an already-run pipeline, Muse
    starts from nothing (or from one existing artifact) and asks "what do you
    want to make?" instead of "what do you want to change?" `set_context(
    seed_artifact=None)` renders either the blank-canvas heading ("What do you
    want to make?") or, given `(path, kind, thumb_path)`, the scoped heading
    ("Make this {kind} into…") with a thumbnail (`_build_thumb_frame`, shared
    with Discover/Open) — plus one card per `goals_fn(seed_output_kind)`
    (default `recipes.goals_for`), a "✨ Surprise me" button, and a free-text
    entry + "Dream it up →" button.

    Three paths all end the same way — emitting the custom `goal-chosen`
    signal (object: the built seed spec dict), which `PipelineStudio` writes
    to disk and hands to `RemixView.load_seed_spec`:

    - **Goal card** click: synchronous — `recipes.build_seed_spec(goal,
      seed_artifact=(path, kind))` (the goal path calls this REAL function
      directly, not an injectable seam; only the free-text path's spec build
      is injectable, via `seed_spec_fn`).
    - **"Surprise me"**: same synchronous path, but the goal is chosen
      deterministically — index `len(goals) // 2` of whatever `goals_fn` just
      returned — NOT `random`/time, so repeated clicks (and tests) are
      reproducible.
    - **Free text** ("Dream it up →"): the `wingit_pipeline_fn` seam (default
      wraps `wingit.map_freeform_to_pipeline` + `capability_discovery.
      default_capabilities` + `wingit.default_llm_fn`) may hit the network, so
      it runs on a daemon `threading.Thread`, applying the result back via
      `GLib.idle_add` — the GTK threading rule in this repo's CLAUDE.md. A
      `None` result (nothing mapped) shows the same gentle inline
      `_show_message` RemixView's wing-it box uses, and emits nothing — never
      a crash.

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
    wrapped with a "← Discover" back control), "muse" (MuseView, wrapped with
    a "← Discover" back control), "remix" (RemixView, wrapped with a "← Back"
    control), and "run" (LiveRunView, wrapped with a "← Back" control) pages —
    the full SP-C Phase 2a Task 4 loop: Open → Remix → Run → done, PLUS the
    Phase 2b-3 Task 4 "start from scratch" loop: Muse → Remix → Run → done.
    Loads runs off the GTK main thread — via
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
    - `DiscoverView`'s "start-from-scratch" → `show_muse()` (blank canvas).
    - `MuseView`'s "goal-chosen" (spec dict) → write it to `REMIXES_DIR` via
      `spec_remix.write_spec(spec, "muse", str(REMIXES_DIR))`, then
      `RemixView.load_seed_spec(derived_path, title)` — `title` is "a new
      pipeline" for blank mode or `f"your {kind}"` for scoped mode (the kind
      `show_muse` was called with) — and switch to "remix". Unlike
      `_on_run_remix` below, there is no base run/spec to preserve top-level
      metadata from — a seed spec is brand new.
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
from create_param_panels import ModifierPills  # noqa: E402
from field_roles import (  # noqa: E402
    MARKER_TIP, ROLE_BRIEF, ROLE_DIRECTION,
    classify_pipeline_field, marker_prefix,
)
from gtk_layout import CONTENT_MAX_WIDTH, MaxWidthBin, wrap_centered  # noqa: E402
import recipes  # noqa: E402
import showcase  # noqa: E402
import wingit  # noqa: E402
from intent_vocab import compatible_intents, intent_for, label  # noqa: E402
from pipeline_engine import load_spec, topo_order  # noqa: E402
from pipeline_runner import PipelineRunner  # noqa: E402
from pipeline_store import PipelineStore  # noqa: E402
from pipeline_view_model import RunView, StepView, build_run_view, list_run_views  # noqa: E402
from spec_remix import (  # noqa: E402
    add_step, apply_edits, editable_params, remove_step, seed_spec, write_spec,
)

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


# Video extensions that GdkPixbuf can't load directly — we render a poster
# frame instead (see _poster_frame_for). GIF is deliberately excluded: pixbuf
# loads it fine (first/animated frame), so a produced GIF shows as itself.
_POSTER_VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".avi")


def _poster_frame_for(path: str, *, extract_fn) -> "str | None":
    """Return an IMAGE path to render for *path*.

    Image paths pass straight through. Video paths get a poster frame — the
    first frame extracted (once, cached next to the file as ``<stem>.poster.jpg``)
    via ``extract_fn(src, dest) -> bool`` — so a produced video renders as a real
    frame instead of failing to load as a pixbuf and falling back to the 🎬
    placeholder. Returns None only when a video's poster can't be produced
    (ffmpeg absent/failed) — the caller then shows the honest placeholder.

    Pure/injectable (`extract_fn` is the real ffmpeg wrapper in production,
    a fake in tests) — no GTK, no subprocess of its own.
    """
    p = Path(path)
    if p.suffix.lower() not in _POSTER_VIDEO_EXTS:
        return path
    poster = p.with_name(p.stem + ".poster.jpg")
    if poster.exists():
        return str(poster)
    if extract_fn(path, str(poster)) and poster.exists():
        return str(poster)
    return None


def _thumb_pixbuf(path: "str | None", width: int, height: int):
    """Aspect-preserving thumbnail load, or None if there's nothing to load.

    Delegates to `main_window._load_pixbuf` rather than re-implementing
    pixbuf scaling. Imported lazily (inside the function, not at module
    scope) so importing pipeline_studio never drags in all of main_window's
    heavier dependencies, and so there's no module-load-time circular import
    once a later task embeds PipelineStudio inside MainWindow.

    Video artifacts (a produced .mp4 etc.) are rendered via a poster frame
    (`_poster_frame_for` + animate_picker.extract_thumbnail) so they show as a
    real frame rather than the placeholder tile.
    """
    if not path:
        return None
    from animate_picker import extract_thumbnail
    from main_window import _load_pixbuf
    src = _poster_frame_for(path, extract_fn=extract_thumbnail)
    if src is None:
        return None
    return _load_pixbuf(src, width, height)


def _build_thumb_frame(path: "str | None", width: int, height: int,
                        css_class: str, intent: "Intent | None" = None) -> Gtk.Widget:
    """A fixed-size box holding either a filled Gtk.Picture or an honest placeholder.

    Shared by DiscoverView (run/hero cards) and OpenView (per-step artifacts)
    so there's exactly one "thumbnail or placeholder" rendering rule in this
    module.

    Fix #3 (hero doesn't fill its frame): the Picture is set to `hexpand`/
    `vexpand` + `Gtk.ContentFit.COVER` so it fills/crops to the frame's exact
    size rather than rendering at the (possibly much smaller, aspect-
    preserved) pixbuf's own natural size, letterboxed and off-center inside
    an otherwise-empty box.

    Fix #4 (placeholder reads as broken): a step/run with no artifact yet
    (still pending, or an intent that never produces a file — e.g. caption/
    text) renders *intent*'s icon centered on this same styled frame — an
    intentional "nothing here yet" tile — instead of a bare 🖼️ glyph floating
    in a big empty dark box. *intent* is optional: DiscoverView's run-level
    hero/card thumbs have no single per-step intent to show, so they fall
    back to the generic image glyph (still on the same tidy tile styling).
    """
    frame = Gtk.Box()
    frame.set_size_request(width, height)
    frame.add_css_class(css_class)
    frame.set_halign(Gtk.Align.CENTER)
    frame.set_valign(Gtk.Align.CENTER)
    frame.set_overflow(Gtk.Overflow.HIDDEN)
    # CRITICAL: pin the frame's expand OFF explicitly. In GTK4 a container's
    # expand is auto-computed as the OR of its children's expand flags, so a
    # hexpand/vexpand child (the Picture/placeholder below, which must fill the
    # frame) would otherwise PROPAGATE that expand up to the frame and then to
    # the enclosing card — inflating the whole card and drifting the image/
    # placeholder. An explicit set_*expand(False) overrides the propagation:
    # the frame stays exactly width×height while its child still fills it.
    frame.set_hexpand(False)
    frame.set_vexpand(False)

    pb = _thumb_pixbuf(path, width, height)
    if pb is not None:
        pic = Gtk.Picture.new_for_pixbuf(pb)
        pic.set_can_shrink(True)
        pic.set_content_fit(Gtk.ContentFit.COVER)
        # hexpand/vexpand fill the FIXED frame (the frame's own expand is
        # pinned False above, so this never escapes to the card).
        pic.set_hexpand(True)
        pic.set_vexpand(True)
        frame.append(pic)
    else:
        frame.add_css_class("ps-thumb-placeholder")
        icon_text = intent.icon if intent is not None else "\U0001f5bc️"  # 🖼️
        placeholder = Gtk.Label(label=icon_text)
        placeholder.add_css_class("ps-placeholder-icon")
        # Fill + center within the fixed frame so the icon reads as an
        # intentional "nothing here yet" tile, not a tiny glyph in a corner.
        placeholder.set_hexpand(True)
        placeholder.set_vexpand(True)
        placeholder.set_halign(Gtk.Align.CENTER)
        placeholder.set_valign(Gtk.Align.CENTER)
        frame.append(placeholder)
    return frame


# Comfortable reading/gallery column width (fix #5, user feedback: Discover/
# Open/Remix content was sprawling across the whole window on a wide screen).
#
# `_MaxWidthBin`/`_wrap_centered` moved to `gtk_layout.py` (Task 2 of the
# Create surface redesign) so other surfaces can share the same
# battle-tested width-capping container. These aliases keep every existing
# reference/test in this module resolving unchanged.
_CONTENT_MAX_WIDTH = CONTENT_MAX_WIDTH
_MaxWidthBin = MaxWidthBin
_wrap_centered = wrap_centered


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
.ps-step-compact {
    padding: 8px 12px;
}
.ps-step-rich {
    padding: 14px;
}
.ps-step-n {
    font-family: monospace;
    font-size: 13px;
    color: #6f948d;
}
.ps-step-intent {
    font-size: 15px;
    font-weight: 700;
    color: #eef8f6;
}
.ps-step-model {
    font-size: 10.5px;
    color: #6f948d;
}
.ps-step-text-block {
    background-color: #0a1f1e;
    border-radius: 8px;
    padding: 10px 12px;
}
.ps-step-text {
    font-size: 12.5px;
    color: #dcefe9;
}
.ps-thumb-placeholder {
    border: 1px dashed alpha(#74C5DF, 0.22);
}
.ps-placeholder-icon {
    font-size: 38px;
    opacity: 0.5;
}
.ps-content-column {
    /* Fix #5: centered max-width column wrapper - see _wrap_centered(). No
       visual styling of its own; purely a layout constraint. */
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
.ps-controls-expander {
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
.ps-muse-cta {
    background-color: #F6BC42;
    color: #3a2a00;
    font-weight: 650;
    border-radius: 9px;
    padding: 8px 16px;
    font-size: 12.5px;
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
        # Emitted by the "✦ Start from scratch" button (SP-C Phase 2b-3
        # Task 4) — no args; PipelineStudio wires this to show_muse() (blank
        # canvas, no seed artifact).
        "start-from-scratch": (GObject.SignalFlags.RUN_FIRST, None, ()),
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

        # "✦ Start from scratch" — always visible above the hero/grid/empty
        # state (unlike that content, it is never rebuilt by set_runs(), so
        # it's appended here rather than inside self._content).
        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top_bar.set_margin_top(18)
        top_bar.set_margin_start(18)
        top_bar.set_margin_end(18)
        self._start_from_scratch_btn = Gtk.Button(label="✦ Start from scratch")
        self._start_from_scratch_btn.add_css_class("ps-muse-cta")
        self._start_from_scratch_btn.set_halign(Gtk.Align.START)
        self._start_from_scratch_btn.connect(
            "clicked", lambda _b: self.emit("start-from-scratch"),
        )
        top_bar.append(self._start_from_scratch_btn)
        self.append(top_bar)

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
        # Fix #5 (user feedback): constrain to a comfortable gallery-column
        # width instead of sprawling across the whole window.
        self._content_wrapper = _wrap_centered(self._content)
        scroller.set_child(self._content_wrapper)

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

        Fix #5 (review follow-up): uses a wrapping `Gtk.FlowBox` rather than
        an unbounded horizontal `Gtk.Box`. A long pipeline's chip row used to
        force its own natural width to the sum of every chip — which, inside
        the content-column clamp, is exactly the over-wide child that would
        push the column past its cap. A FlowBox reflows chips onto multiple
        lines within whatever width it's allocated, so a 9-step recipe wraps
        instead of stretching. The "→" separators are ordinary flow items
        between chips, so the "Generate an image → Describe it → …" reading
        is preserved as the row reflows.
        """
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_orientation(Gtk.Orientation.HORIZONTAL)
        flow.set_min_children_per_line(1)
        # A high per-line cap lets the FlowBox pack as many chips as FIT on a
        # line and wrap the rest — the wrap point is driven by allocated
        # width, not this number.
        flow.set_max_children_per_line(100)
        flow.set_row_spacing(6)
        flow.set_column_spacing(6)
        flow.set_homogeneous(False)
        flow.set_hexpand(True)
        chip_labels: list = []
        for i, step in enumerate(recipe):
            if i > 0:
                arrow = Gtk.Label(label="→")  # →
                arrow.add_css_class("ps-chip-arrow")
                flow.insert(arrow, -1)
            chip = Gtk.Label(label=step)
            chip.add_css_class("ps-chip")
            flow.insert(chip, -1)
            chip_labels.append(chip)
        return flow, chip_labels

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

    # Fix #2 (compact rows when empty): the placeholder tile for a step with
    # no content yet is small — a tight list-item footprint, not a big empty
    # box competing for attention with steps that DID produce something.
    STEP_THUMB_W, STEP_THUMB_H = 96, 64

    # Fix #6 (see each step's real content): a step WITH a produced artifact
    # gets a substantially larger preview than the old flat 150×92 thumb —
    # large enough to actually see what was made, not just a token that it
    # exists.
    PREVIEW_W, PREVIEW_H = 420, 260
    FANOUT_THUMB = 128   # per-still tile size in a fan-out step's grid

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
        # Fix #6: text-producing steps (caption/text/prompt — no file
        # artifact) render their real text inline instead of a thumb frame.
        # Keyed by node_id -> the Gtk.Label actually holding the text, so
        # tests/callers can read it back directly.
        self._step_text_blocks: dict = {}

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
        # Fix #5 (user feedback): constrain to a comfortable gallery-column
        # width instead of sprawling across the whole window.
        self._content_wrapper = _wrap_centered(self._steps_box)
        scroller.set_child(self._content_wrapper)

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
        self._step_text_blocks = {}

        if not run.steps:
            empty = Gtk.Label(label="This run has no steps.")
            empty.add_css_class("ps-empty-msg")
            self._steps_box.append(empty)
            return

        for index, step in enumerate(run.steps, start=1):
            self._steps_box.append(self._build_step_row(index, step))

    # ── Row building ─────────────────────────────────────────────────────────

    def _build_step_row(self, index: int, step: StepView) -> Gtk.Widget:
        """Build one step row.

        Fix #1 (cohesive intent label): verb+noun render as ONE label
        (`f"{verb} {noun}"`) with the status glyph inline, and the model (when
        present) as a single small muted caption below — not three stacked
        lines that read as fragmented text.

        Fix #2/#6 reconciled: a step with neither a real artifact nor real
        text (still pending, or an intent that never produces either) gets
        the `ps-step-compact` tight-list-item treatment and a small
        intent-icon placeholder tile; a step that DID produce something gets
        `ps-step-rich` and either a substantially larger image/gif/video
        preview or its actual text rendered inline — see each other's
        docstring for exactly what "larger"/"inline" mean.
        """
        has_content = bool(step.artifact_path or step.text_content)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.add_css_class("ps-step")
        row.add_css_class("ps-step-rich" if has_content else "ps-step-compact")

        n_label = Gtk.Label(label=str(index))
        n_label.add_css_class("ps-step-n")
        n_label.set_valign(Gtk.Align.START)
        row.append(n_label)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        main.set_hexpand(True)
        main.set_valign(Gtk.Align.CENTER)

        intent_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        intent_label = Gtk.Label(label=f"{step.intent.verb} {step.intent.noun}")
        intent_label.set_xalign(0)
        intent_label.add_css_class("ps-step-intent")
        intent_row.append(intent_label)

        status_label = Gtk.Label(label=self._STATUS_GLYPH.get(step.status, "•"))
        status_label.add_css_class(self._STATUS_CSS.get(step.status, "ps-status-pending"))
        intent_row.append(status_label)
        main.append(intent_row)

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

        if len(step.artifact_paths) > 1:
            # Fan-out step (e.g. one still per lore fragment): show the WHOLE
            # series as a wrapped grid, not just one image — the review should
            # reflect everything the step made (matches the showcase gallery).
            grid = Gtk.FlowBox()
            grid.set_selection_mode(Gtk.SelectionMode.NONE)
            grid.set_max_children_per_line(3)
            grid.set_column_spacing(8)
            grid.set_row_spacing(8)
            grid.set_halign(Gtk.Align.END)
            grid.set_size_request(self.PREVIEW_W, -1)
            for p in step.artifact_paths:
                grid.insert(
                    _build_thumb_frame(p, self.FANOUT_THUMB, self.FANOUT_THUMB,
                                        "ps-card-thumb", step.intent), -1)
            right.append(grid)
            self._step_thumb_frames[step.node_id] = grid
        elif step.artifact_path:
            # Showing beats talking: a produced image/gif/video is the fullest
            # thing to show, so render it FIRST (substantially larger than the
            # old flat 150×92 thumb — big enough to actually see what was made).
            thumb = _build_thumb_frame(step.artifact_path, self.PREVIEW_W, self.PREVIEW_H,
                                        "ps-card-thumb", step.intent)
            right.append(thumb)
            self._step_thumb_frames[step.node_id] = thumb
        elif step.text_content:
            # No file artifact, but the step produced real TEXT (a caption,
            # prompt, poem) — show the actual words, not a placeholder icon
            # standing in for them.
            text_block, text_label = self._build_text_block(step.text_content)
            right.append(text_block)
            self._step_text_blocks[step.node_id] = text_label
        else:
            # Fix #2/#4: nothing produced yet — a small, honest intent-icon
            # tile, not an oversized empty box.
            thumb = _build_thumb_frame(None, self.STEP_THUMB_W, self.STEP_THUMB_H,
                                        "ps-card-thumb", step.intent)
            right.append(thumb)
            self._step_thumb_frames[step.node_id] = thumb

        row.append(right)
        return row

    def _build_text_block(self, text: str) -> "tuple[Gtk.Widget, Gtk.Label]":
        """A readable inline text block for a text-producing step (fix #6).

        Returns (container, label) — the container is what gets appended to
        the row; the label is what `_step_text_blocks` keeps a reference to,
        so tests/callers can read the rendered text back directly without
        walking into the container.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("ps-step-text-block")
        box.set_size_request(self.PREVIEW_W, -1)

        label = Gtk.Label(label=text)
        label.set_wrap(True)
        label.set_xalign(0)
        label.set_valign(Gtk.Align.START)
        label.add_css_class("ps-step-text")
        box.append(label)
        return box, label

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
        # Field-role zoning (Task 2 of "pipeline field roles"): each node's
        # editable fields are classified brief/direction/control
        # (`field_roles.classify_pipeline_field`) and rendered in that order;
        # `_field_order` records the resulting DISPLAY order of keys per node
        # so tests (and any future caller) can check ordering/zone without
        # walking the widget tree. `_controls_expanders` holds the collapsed
        # "Controls (N)" `Gtk.Expander` per node — only present when that
        # node has at least one control-role field.
        self._field_order: "dict[str, list[str]]" = {}
        self._controls_expanders: "dict[str, Gtk.Expander]" = {}
        # Contextual ModifierPills (Task 3 of "pipeline field roles"): a
        # brief TEXT field on a node whose output_kind maps to a chip bank
        # (`_bank_kind_for_output`) gets a `ModifierPills` widget rendered
        # directly under its field row. Keyed node_id -> field key -> the
        # `ModifierPills` instance, so `_collect_edits` can fold each field's
        # `applied_text()` into that field's collected value at Run time.
        self._field_pills: "dict[str, dict[str, ModifierPills]]" = {}
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
        # Fix #5 (user feedback): constrain to a comfortable gallery-column
        # width instead of sprawling across the whole window.
        self._content_wrapper = _wrap_centered(self._steps_box)
        scroller.set_child(self._content_wrapper)

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

    def load_seed_spec(self, spec_path: str, title: str) -> None:
        """Load a freshly-written SEED spec (from the Muse) — like `set_run`
        but with no `RunView`, since a seed spec has no run history yet.

        `title` is caller-supplied prose (e.g. "a new pipeline" or "your
        image" — see `PipelineStudio._on_muse_goal_chosen`) rather than a
        `RunView.title`, because there IS no run yet to name this from.
        Otherwise identical to `set_run`: main-thread only, repeat-safe.
        """
        self._spec_path = spec_path
        self._title_label.set_label(f"Composing · {title}")
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

    def _bank_kind_for_output(self, output_kind: "str | None") -> "str | None":
        """Which `create_param_panels.ModifierPills` chip bank (if any) fits
        a node whose `Intent.output_kind` is *output_kind*.

        Only "image"/"video"/"gif" (chip_config's animate bank) have a bank —
        "text"/"playlist"/None/anything unrecognized get no pills at all
        (`_build_step_card` skips building a `ModifierPills` in that case).
        The "gif" -> "animate" rename matches `chip_config`'s tab naming
        (AnimateDiff's output_kind is "gif"; its chip bank is called
        "animate").
        """
        return {"image": "image", "video": "video", "gif": "animate"}.get(output_kind)

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
        self._field_order = {}
        self._field_pills = {}
        self._controls_expanders = {}
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

        # Fix #1 (cohesive intent label): one combined "verb noun" label
        # instead of two stacked lines, matching OpenView/LiveRunView's
        # identical treatment of the same intent vocabulary.
        verb_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        verb_col.set_hexpand(True)
        intent_label = Gtk.Label(label=f"{intent.verb} {intent.noun}")
        intent_label.set_xalign(0)
        intent_label.add_css_class("ps-step-intent")
        verb_col.append(intent_label)

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

        # Classify every field by role (Task 2 of "pipeline field roles"):
        # brief (creative words), direction (interpreted/exact choices), or
        # control (deterministic knobs the model never reads). Partitioning
        # preserves each field's ORIGINAL relative order within its zone —
        # only the zone grouping is new, not a re-sort within a zone.
        brief: "list[tuple]" = []
        direction: "list[tuple]" = []
        control: "list[tuple]" = []
        for field in fields:
            role = classify_pipeline_field(field.kind, field.value, field.key)
            if role.role == ROLE_BRIEF:
                brief.append((field, role))
            elif role.role == ROLE_DIRECTION:
                direction.append((field, role))
            else:
                control.append((field, role))

        node_widgets: "dict[str, Gtk.Widget]" = {}
        node_meta: "dict[str, tuple]" = {}
        field_order: "list[str]" = []

        # Brief then direction rows go straight on the card body, in that
        # order — the "what to say, then how" reading order the brief calls
        # for. Every field's widget/meta is recorded here regardless of
        # which zone it lands in, so `_collect_edits` (unchanged by this
        # task) keeps finding every field exactly as before.
        bank_kind = self._bank_kind_for_output(intent.output_kind)
        for field, role in brief + direction:
            row, widget = self._build_field_row(field, role)
            card.append(row)
            node_widgets[field.key] = widget
            node_meta[field.key] = (field.kind, field.value)
            field_order.append(field.key)

            # Contextual ModifierPills (Task 3 of "pipeline field roles"):
            # only a BRIEF (creative-words) TEXT field on a node whose
            # output feeds a chip bank gets one — number/bool brief fields
            # don't exist today, but the kind check keeps this from ever
            # attaching pills to the wrong widget type if one did. Appended
            # directly under the field's own row so it reads as "this field's
            # modifiers", not a card-wide control.
            if role.role == ROLE_BRIEF and field.kind == "text" and bank_kind is not None:
                pills = ModifierPills(bank_kind)
                card.append(pills)
                self._field_pills.setdefault(node_id, {})[field.key] = pills

        # Control (⚙) fields are deterministic knobs, not creative choices —
        # tuck them under a collapsed per-card expander so the primary
        # reading path is brief/direction only. Only built when the node
        # actually has control fields (e.g. node "3"'s bool `loop` field is
        # direction-role, not control, so it never gets one).
        if control:
            exp = Gtk.Expander(label=f"Controls ({len(control)})")
            exp.set_expanded(False)
            exp.add_css_class("ps-controls-expander")
            control_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            for field, role in control:
                row, widget = self._build_field_row(field, role)
                control_box.append(row)
                node_widgets[field.key] = widget
                node_meta[field.key] = (field.kind, field.value)
                field_order.append(field.key)
            exp.set_child(control_box)
            card.append(exp)
            self._controls_expanders[node_id] = exp

        if node_widgets:
            self._field_widgets[node_id] = node_widgets
            self._field_meta[node_id] = node_meta
        self._field_order[node_id] = field_order

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

    def _build_field_row(self, field, role) -> "tuple[Gtk.Widget, Gtk.Widget]":
        """One label + editable widget row for a single ParamField.

        *role* is the `field_roles.FieldRole` `_build_step_card` already
        classified this field as — the label is prefixed with its marker
        glyph (`field_roles.marker_prefix`) and gets a tooltip explaining the
        marker (`field_roles.MARKER_TIP`), so every field visibly declares
        how its value is used (creative words / model-interpreted / exact
        setting) without changing the field's editable widget at all.

        Returns (row, widget) — the caller keeps the widget reference (keyed
        by node_id/key) so Run-time diffing can read its current value; the
        row is just what gets appended to the step card.
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        key_label = Gtk.Label(label=marker_prefix(role.marker) + field.label)
        key_label.set_xalign(0)
        key_label.add_css_class("ps-field-key")
        key_label.set_size_request(120, -1)
        key_label.set_tooltip_text(MARKER_TIP[role.marker])
        row.append(key_label)

        widget = self._build_field_widget(field)
        widget.set_hexpand(True)
        row.append(widget)
        return row, widget

    def _node_field_order(self, node_id: str) -> "list[str]":
        """Field keys for *node_id* in DISPLAY order — brief then direction
        rows (card body), then control rows (inside the collapsed
        expander) — as built by `_build_step_card`. A test seam: cheaper
        than walking the widget tree to recover ordering, and exercised by
        `_ordered_field_roles_for_node` in tests/test_pipeline_studio.py."""
        return list(self._field_order.get(node_id, []))

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
                # Fold this field's applied ModifierPills text (if any) into
                # its collected value BEFORE the changed/unchanged diff below
                # — an untouched field with no pills applied is unaffected
                # (new_value == orig_value, still excluded from edits), while
                # an applied pill always counts as a change even if the
                # text itself wasn't retyped.
                pills = self._field_pills.get(node_id, {}).get(key)
                if pills is not None and pills.applied_text():
                    new_value = f"{new_value} {pills.applied_text()}".strip()
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


class MuseView(Gtk.Box):
    """The Muse: goal-first "start from scratch" creative wizard (SP-C Phase
    2b-3 Task 4). See module docstring for the full picture.

    Data arrives ONLY through `set_context(seed_artifact=None)` — same rule
    as every other view in this module: no PipelineStore/disk access of its
    own beyond the injected seams below.
    """

    __gsignals__ = {
        # (spec dict,) — the built seed spec, from whichever of the three
        # paths (goal card / Surprise me / free text) produced one.
        "goal-chosen": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    THUMB_W, THUMB_H = 160, 100

    def __init__(
        self,
        *,
        goals_fn: "Callable[[Optional[str]], list] | None" = None,
        wingit_pipeline_fn: "Callable[[str, Optional[str]], object] | None" = None,
        seed_spec_fn: "Callable[..., dict] | None" = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("ps-discover")  # same dark-teal page background
        _apply_css()

        # `goals_fn(seed_output_kind) -> list[recipes.Goal]`. Real default
        # wraps `recipes.goals_for` (keyword-only `seed_output_kind` there —
        # this closure calls it positionally so tests can inject a plain
        # `lambda kind: [...]` fake without matching that signature exactly).
        self._goals_fn = goals_fn or (
            lambda seed_output_kind: recipes.goals_for(seed_output_kind=seed_output_kind)
        )

        # `wingit_pipeline_fn(text, seed_output_kind) -> list[(class_type,
        # params)] | None`. Real default wraps `wingit.map_freeform_to_pipeline`
        # + `capability_discovery.default_capabilities` + `wingit.default_llm_fn`
        # — the same "real closure" pattern RemixView's `wingit_fn` uses. Blank
        # mode (`seed_output_kind is None`) asks `default_capabilities` for
        # "text"-consuming capabilities: the user's typed sentence IS the text
        # artifact wingit's "bare text seed" allowance expects (see wingit.py's
        # `_kind_fits` docstring) — every plugin's seed capability (kind_in
        # None) is always included regardless, so this never under-offers.
        self._wingit_pipeline_fn = wingit_pipeline_fn or (
            lambda text, seed_output_kind: wingit.map_freeform_to_pipeline(
                text,
                seed_output_kind=seed_output_kind,
                capabilities=capability_discovery.default_capabilities(
                    seed_output_kind or "text",
                ),
                llm_fn=wingit.default_llm_fn,
            )
        )

        # `seed_spec_fn(steps, seed_artifact=...) -> dict`. Only the free-text
        # path uses this seam — the goal-card/Surprise path always calls the
        # real `recipes.build_seed_spec` directly (see class docstring in the
        # module header and `_choose_goal` below).
        self._seed_spec_fn = seed_spec_fn or seed_spec

        # Set by set_context(); (path, kind, thumb_path) or None.
        self._seed_artifact: "tuple[str, str, str | None] | None" = None
        # The Goal list set_context() most recently rendered — Surprise me's
        # deterministic middle-index pick reads from exactly this list, not a
        # fresh goals_fn() call, so it always matches what's on screen.
        self._goals: list = []

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        header.set_margin_top(18)
        header.set_margin_start(18)
        header.set_margin_end(18)

        self._thumb_holder = Gtk.Box()
        header.append(self._thumb_holder)

        self._heading_label = Gtk.Label(label="")
        self._heading_label.set_xalign(0)
        self._heading_label.set_wrap(True)
        self._heading_label.add_css_class("ps-hero-title")
        self._heading_label.set_hexpand(True)
        self._heading_label.set_valign(Gtk.Align.CENTER)
        header.append(self._heading_label)

        self.append(header)

        # Hidden until a guard rejects a goal/free-text choice (see
        # _show_message/_hide_message) — same gentle-inline-message pattern
        # RemixView's composer guards use.
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

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.set_margin_top(10)
        body.set_margin_bottom(18)
        body.set_margin_start(18)
        body.set_margin_end(18)
        scroller.set_child(body)

        self._cards_box = Gtk.FlowBox()
        self._cards_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._cards_box.set_max_children_per_line(3)
        self._cards_box.set_min_children_per_line(1)
        self._cards_box.set_row_spacing(12)
        self._cards_box.set_column_spacing(12)
        self._cards_box.set_homogeneous(True)
        body.append(self._cards_box)
        # Keyed by Goal.id — kept around purely so tests/callers can find a
        # specific goal's card button without walking the widget tree.
        self._goal_buttons: "dict[str, Gtk.Button]" = {}

        self._surprise_button = Gtk.Button(label="✨ Surprise me")
        self._surprise_button.add_css_class("ps-btn-ghost")
        self._surprise_button.set_halign(Gtk.Align.START)
        self._surprise_button.connect("clicked", self._on_surprise_clicked)
        body.append(self._surprise_button)

        wing_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._freeform_entry = Gtk.Entry()
        self._freeform_entry.set_placeholder_text(
            "Or describe what you want to make, in your own words…"
        )
        self._freeform_entry.add_css_class("ps-field-entry")
        self._freeform_entry.set_hexpand(True)
        wing_row.append(self._freeform_entry)

        self._dream_button = Gtk.Button(label="Dream it up →")
        self._dream_button.add_css_class("ps-wingit-compose")
        self._dream_button.connect("clicked", self._on_dream_clicked)
        wing_row.append(self._dream_button)
        body.append(wing_row)

        self.set_context(None)

    # ── Public API ───────────────────────────────────────────────────────────

    def set_context(self, seed_artifact: "tuple[str, str, str | None] | None" = None) -> None:
        """(Re)build the whole wizard for blank canvas (`None`) or "starting
        from this artifact" (`(path, kind, thumb_path)`). Main-thread only,
        repeat-safe.
        """
        self._seed_artifact = seed_artifact
        self._hide_message()

        while child := self._thumb_holder.get_first_child():
            self._thumb_holder.remove(child)

        if seed_artifact is None:
            self._heading_label.set_label("What do you want to make?")
            seed_output_kind = None
        else:
            path, kind, thumb_path = seed_artifact
            del path  # not needed for display — only kind/thumb_path are
            self._heading_label.set_label(f"Make this {kind} into…")
            self._thumb_holder.append(
                _build_thumb_frame(thumb_path, self.THUMB_W, self.THUMB_H, "ps-card-thumb")
            )
            seed_output_kind = kind

        try:
            goals = self._goals_fn(seed_output_kind)
        except Exception:
            # A raising goals_fn (e.g. a genuinely broken plugin manifest
            # deep in recipes.discover_goals) must never crash the wizard —
            # degrade to "no goals right now" rather than take down the page.
            goals = []
        self._goals = list(goals or [])

        while child := self._cards_box.get_first_child():
            self._cards_box.remove(child)
        self._goal_buttons = {}
        for goal in self._goals:
            self._cards_box.insert(self._build_goal_card(goal), -1)

        # Scoped mode (starting from an existing artifact) has no guaranteed
        # curated coverage — every curated scoped goal consumes "image", so a
        # video/gif seed legitimately yields zero cards. Blank mode always has
        # curated goals (see recipes._CURATED), so this branch is scoped-only.
        # Degrade gracefully per the design spec: a gentle message, "Surprise
        # me" hidden (nothing to surprise with), free-text entry untouched.
        no_scoped_goals = seed_artifact is not None and not self._goals
        self._surprise_button.set_visible(not no_scoped_goals)
        if no_scoped_goals:
            self._show_message(
                "No ready-made recipes for this yet — describe what you'd "
                "like below."
            )

    # ── Goal cards / Surprise me ─────────────────────────────────────────────

    def _build_goal_card(self, goal: "recipes.Goal") -> Gtk.Widget:
        button = Gtk.Button()
        button.add_css_class("ps-card")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content.set_valign(Gtk.Align.CENTER)

        icon_label = Gtk.Label(label=goal.icon)
        content.append(icon_label)

        text_label = Gtk.Label(label=goal.label)
        text_label.set_wrap(True)
        text_label.add_css_class("ps-card-title")
        content.append(text_label)

        button.set_child(content)
        button.connect("clicked", self._on_goal_card_clicked, goal)
        self._goal_buttons[goal.id] = button
        return button

    def _on_goal_card_clicked(self, _button: Gtk.Button, goal: "recipes.Goal") -> None:
        self._choose_goal(goal)

    def _on_surprise_clicked(self, _button: Gtk.Button) -> None:
        """Deterministic pick — index `len(goals) // 2` of the currently
        rendered goal list, NOT `random`/time (this repo's test/reproducibility
        discipline — see class docstring). A no-op when nothing is on offer."""
        if not self._goals:
            return
        self._choose_goal(self._goals[len(self._goals) // 2])

    def _choose_goal(self, goal: "recipes.Goal") -> None:
        """Synchronous path shared by goal-card clicks and Surprise me: build
        the seed spec via the REAL `recipes.build_seed_spec` (not an injected
        seam — see class docstring) and emit `goal-chosen`, or show a gentle
        message on a genuine kind mismatch instead of crashing."""
        seed_artifact_pair = None
        if self._seed_artifact is not None:
            path, kind, _thumb_path = self._seed_artifact
            seed_artifact_pair = (path, kind)

        try:
            spec = recipes.build_seed_spec(goal, seed_artifact=seed_artifact_pair)
        except ValueError as exc:
            self._show_message(f"Couldn't build that pipeline: {exc}")
            return
        self._hide_message()
        self.emit("goal-chosen", spec)

    # ── Free text ("Dream it up") ────────────────────────────────────────────

    def _on_dream_clicked(self, _button: Gtk.Button) -> None:
        """Map the free-text entry to a draft pipeline via `wingit_pipeline_fn`.

        An empty/whitespace-only entry is a no-op (mirrors RemixView's
        wing-it Compose button) rather than a spurious "couldn't compose"
        message. Otherwise `wingit_pipeline_fn` runs on a daemon thread (the
        default closure's LLM call may hit the network — GTK threading rule,
        see CLAUDE.md) and the result is applied back on the main thread via
        `GLib.idle_add(self._apply_freeform_result, ...)`. A raising
        `wingit_pipeline_fn` is treated the same as a `None` result (gentle
        message, nothing emitted) rather than crashing the worker thread.

        Busy-guard: the Dream-it-up button is disabled synchronously here
        (main thread, before the worker thread even starts) so a second click
        while a mapping is in flight can't spawn a second worker.
        `_apply_freeform_result` re-enables it.
        """
        text = self._freeform_entry.get_text().strip()
        if not text:
            return

        self._dream_button.set_sensitive(False)
        seed_output_kind = self._seed_artifact[1] if self._seed_artifact is not None else None
        wingit_pipeline_fn = self._wingit_pipeline_fn

        def worker() -> None:
            try:
                steps = wingit_pipeline_fn(text, seed_output_kind)
            except Exception:
                steps = None
            GLib.idle_add(self._apply_freeform_result, steps)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_freeform_result(self, steps: "list | None") -> None:
        """Apply a `wingit_pipeline_fn` outcome from `_on_dream_clicked`.
        Always runs on the main thread (posted via `GLib.idle_add`).

        `None`/empty means nothing could be drafted at all — shown as a
        gentle inline message, nothing emitted, matching RemixView's wing-it
        `None` handling. A real step list is turned into a spec via
        `seed_spec_fn` (the injectable seam — see class docstring); a
        `ValueError` from that call (a genuine kind mismatch) gets the same
        gentle message rather than crashing.
        """
        try:
            if not steps:
                self._show_message("Couldn't compose that — try rephrasing.")
                return

            seed_artifact_pair = None
            if self._seed_artifact is not None:
                path, kind, _thumb_path = self._seed_artifact
                seed_artifact_pair = (path, kind)

            try:
                spec = self._seed_spec_fn(steps, seed_artifact=seed_artifact_pair)
            except ValueError:
                self._show_message("Couldn't compose that — try rephrasing.")
                return
            self._hide_message()
            self.emit("goal-chosen", spec)
        finally:
            try:
                self._dream_button.set_sensitive(True)
            except Exception:
                pass

    # ── Shared message helpers ───────────────────────────────────────────────

    def _show_message(self, text: str) -> None:
        self._message_label.set_label(text)
        self._message_label.set_visible(True)

    def _hide_message(self) -> None:
        self._message_label.set_label("")
        self._message_label.set_visible(False)


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
        can keep the label reference for later in-place glyph updates.

        Fix #1 (cohesive intent label): one combined "verb noun" label with
        the status glyph inline, matching OpenView/RemixView's identical
        treatment of the same intent vocabulary — see OpenView._build_step_
        row's docstring for the full rationale.
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.add_css_class("ps-step")

        n_label = Gtk.Label(label=str(index))
        n_label.add_css_class("ps-step-n")
        n_label.set_valign(Gtk.Align.START)
        row.append(n_label)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        main.set_hexpand(True)
        main.set_valign(Gtk.Align.CENTER)

        intent_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        intent_label = Gtk.Label(label=f"{step.intent.verb} {step.intent.noun}")
        intent_label.set_xalign(0)
        intent_label.add_css_class("ps-step-intent")
        intent_row.append(intent_label)

        status_label = Gtk.Label(label=self._STATUS_GLYPH["pending"])
        status_label.add_css_class(self._STATUS_CSS["pending"])
        intent_row.append(status_label)
        main.append(intent_row)

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

        # The seed_artifact `show_muse()` was last called with — remembered
        # purely so `_on_muse_goal_chosen` can pick the right remix title
        # ("a new pipeline" vs. f"your {kind}") without MuseView itself
        # needing to echo it back through the "goal-chosen" signal.
        self._muse_seed_artifact: "Optional[tuple]" = None

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.append(self.stack)

        self.discover = DiscoverView()
        self.discover.connect("open-run", self._on_open_run)
        self.discover.connect("start-from-scratch", lambda _w: self.show_muse())
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

        # "muse" page: a back-to-discover bar wrapped around MuseView (SP-C
        # Phase 2b-3 Task 4) — the "start from scratch" wizard's back control
        # returns to Discover (its own front door), not Open (there is no run
        # open yet when arriving here from "✦ Start from scratch").
        muse_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        muse_back_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        muse_back_bar.set_margin_top(10)
        muse_back_bar.set_margin_start(18)
        muse_back_btn = Gtk.Button(label="← Discover")
        muse_back_btn.add_css_class("ps-open-back")
        muse_back_btn.add_css_class("ps-btn-ghost")
        muse_back_btn.connect("clicked", self._on_back_to_discover)
        muse_back_bar.append(muse_back_btn)
        muse_page.append(muse_back_bar)

        self.muse = MuseView()
        self.muse.connect("goal-chosen", self._on_muse_goal_chosen)
        self.muse.set_vexpand(True)
        muse_page.append(self.muse)

        self.stack.add_titled(muse_page, "muse", "Muse")

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

    def show_muse(self, seed_artifact: "Optional[tuple]" = None) -> None:
        """Open the Muse wizard: blank canvas (`seed_artifact=None`, the
        default — reached via Discover's "✦ Start from scratch") or scoped to
        an existing artifact (`(path, kind, thumb_path)`). Main-thread only.

        Remembers *seed_artifact* (see `self._muse_seed_artifact`'s docstring)
        so `_on_muse_goal_chosen` can title the resulting remix correctly.
        """
        self._muse_seed_artifact = seed_artifact
        self.muse.set_context(seed_artifact)
        self.stack.set_visible_child_name("muse")

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

    def _on_muse_goal_chosen(self, _widget: "MuseView", spec: dict) -> None:
        """MuseView's "goal-chosen" (spec dict) → write it and open RemixView.

        Unlike `_on_run_remix` below, there is no base run/spec file to
        re-merge top-level ``_``-metadata from — a Muse-built seed spec is
        brand new, so it's written as-is via `spec_remix.write_spec` under a
        fixed `"muse"` base name (every seed spec shares this base; multiple
        seeds in a session just get distinct `remix_muse_<n>.json` files).

        Title mirrors `show_muse`'s `seed_artifact`: "a new pipeline" for
        blank mode, or f"your {kind}" for scoped mode — recipes.Goal.label
        already describes the OUTCOME ("A poster"), not the starting point,
        so this titles the Remix page around what the user started FROM
        instead, matching RemixView's "Remixing · <run title>" convention.
        """
        REMIXES_DIR.mkdir(parents=True, exist_ok=True)
        derived_path = write_spec(spec, "muse", str(REMIXES_DIR))

        if self._muse_seed_artifact is None:
            title = "a new pipeline"
        else:
            kind = self._muse_seed_artifact[1]
            title = f"your {kind}"

        self.remix_view.load_seed_spec(derived_path, title)
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
