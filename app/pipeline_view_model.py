# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Run view-model builder for Pipeline Studio (SP-C Phase 1, Task 2).

Turns a raw `PipelineStore` run record (see `pipeline_store.py`'s module
docstring for the exact dict shape) into `RunView`/`StepView` dataclasses the
Discover/Open GTK views render. This module is PURE — no GTK imports — so it
is unit-tested without a display and safely importable from any context
(CLI, GTK views, future web surfaces).

Design
------
`build_run_view()` re-loads and re-topo-orders the run's spec (via
`pipeline_engine.load_spec`/`topo_order`) rather than trusting any node order
stored on the record itself — the record only stores *results*
(`job_states`), never the graph shape. Each node's `class_type` maps to an
`Intent` via `intent_vocab.intent_for`, so the view speaks the same
verb+noun language as the rest of Pipeline Studio.

Status resolution prefers `job_states` (the engine's own NODE:<id>:<status>
signal, persisted by `PipelineStore.update_node`) since it is authoritative
about *why* a step has no artifact yet (still running vs. failed vs. never
started). Artifact existence is only a fallback for older/partial records
that predate a given node's job_states entry (or for output types — like
`caption`/`text`/`prompt` — that never produce a file, but the record still
predates this being handled at all). See `_resolve_status`.

Artifact resolution mirrors the engine handlers' naming exactly
(`ctx.output_dir / f"node{nid}_<suffix>"`, see `pipeline_engine._h_*`): glob
`output_dir` for `node{id}_*` and `node{id}.*` at the top level AND one
directory down (`output_dir/*/node{id}_*`), filter to the extensions that
match the node's primary output kind, and — since a retried node can leave
behind more than one candidate file (e.g. a fixed re-render), or a multi-job
run can leave one candidate per job subdirectory (`bin/run_worlds_fair.sh`
writes each job's artifacts into its own `output_dir/<job-name>/` rather than
flat) — prefer the most recently modified match.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from intent_vocab import Intent, intent_for, label
from pipeline_engine import load_spec, topo_order

log = logging.getLogger(__name__)


# ── Output-kind classification ────────────────────────────────────────────────
#
# Maps an Intent's *primary* (first) output key to the file-extension family
# the engine writes for it, so artifact resolution and hero selection can
# reason about "is this an image?" without hardcoding class_types. Output
# keys not listed here (caption/text/prompt/playlist_id/...) never correspond
# to a file on disk — the node's result lives only in `results[nid][key]`
# in-process, so those steps never have an `artifact_path`.
_IMAGE_EXTS = (".png", ".jpg", ".jpeg")
_VIDEO_EXTS = (".mp4",)
_GIF_EXTS = (".gif",)

# output key -> (kind label, allowed extensions | None meaning "any").
_OUTPUT_KIND: dict[str, tuple[str, "tuple[str, ...] | None"]] = {
    "image_path": ("image", _IMAGE_EXTS),
    "fg_path": ("image", _IMAGE_EXTS),
    "depth_path": ("image", _IMAGE_EXTS),
    "png_path": ("image", _IMAGE_EXTS),
    "video_path": ("video", _VIDEO_EXTS),
    "gif_path": ("gif", _GIF_EXTS),
    # TTLGArtgenGenerate's artifact_path can be a raster image, a GIF, or a
    # text/code file depending on the plugin — accept any extension.
    "artifact_path": ("any", None),
}

# Kinds counted as "heroable" for RunView.hero_path (brief: first image/video
# artifact — a GIF or arbitrary artifact does not qualify as a hero image).
_HERO_KINDS = {"image", "video"}

# Extensions that mark a string as a media-file PATH (a thing to render, never
# to show as text). Superset of the engine's own suffixes so a drifted/unknown
# output key holding an image/video path can't leak into the text block.
_MEDIA_PATH_EXTS = _IMAGE_EXTS + _VIDEO_EXTS + _GIF_EXTS + (".webp", ".mov", ".svg")


def _looks_like_media_path(value: str) -> bool:
    """True if *value* ends in a known media extension — i.e. it is a file to
    RENDER (via _resolve_artifact), not display text. See _resolve_text_content."""
    return value.strip().lower().endswith(_MEDIA_PATH_EXTS)


@dataclass
class StepView:
    """One pipeline node as the UI renders it.

    status ∈ {"done", "running", "pending", "failed"}.

    text_content — this node's resolved TEXT output (caption/poem/prompt/...),
    read from the run's `output_dir/results.json` (see `_resolve_text_content`),
    for steps whose primary output has no file counterpart at all (so
    `artifact_path` is always None for them — e.g. TTLGCaptionImage/
    TTLGGenerateText/TTLGPromptCompose). None whenever there's no results.json,
    it's malformed, or this node/key just isn't in it — same "never crash,
    degrade to an honest absence" discipline as `_resolve_artifact`. Always
    None when `artifact_path` is set (build_run_view only looks for text when
    there's no file artifact to show instead — see its call site).
    """
    node_id: str
    intent: Intent
    status: str
    artifact_path: "str | None"
    text_content: "str | None" = None
    # artifact_paths — ALL of this step's on-disk file artifacts, in order. For
    # an ordinary single-output step this is (artifact_path,) (or empty); for a
    # FAN-OUT step (e.g. a list-aware TTLGTextToImage that generated one image
    # per lore fragment) it's every produced file, read from the run's
    # results.json list output. Consumers that want the whole series (the
    # showcase gallery) iterate this; artifact_path remains the single hero/
    # representative artifact for backward compatibility + hero selection.
    artifact_paths: "tuple[str, ...]" = ()


@dataclass
class RunView:
    """A whole pipeline run as the Discover/Open views render it."""
    run_id: str
    title: str
    created_at: str
    hero_path: "str | None"
    steps: "list[StepView]"
    recipe: "list[str]"


# Job-state status strings the engine actually emits (pipeline_engine.run's
# NODE:<id>:<status>: lines are only ever "running"/"done"/"failed" today).
# Anything else — including a hypothetical future "queued" state, or a
# missing/garbled status field — degrades to "pending" rather than crashing
# the view or inventing a status the UI doesn't know how to render.
_KNOWN_STATUSES = {"done", "running", "failed"}


def _merged_job_states(record: dict) -> dict:
    """Flatten `record["job_states"]` (per-job) into one {node_id: state} map.

    A run record can carry multiple jobs (one pipeline spec fanned out over
    several jobs, e.g. one per prompt); this view only needs "has this node
    id reached a terminal/most-recent state at all", so later jobs' entries
    win on a node_id collision (last-write-wins is adequate here — in
    practice each job drives disjoint node ids since they're separate runs
    of the same graph).
    """
    merged: dict = {}
    for _job_name, states in (record.get("job_states") or {}).items():
        merged.update(states or {})
    return merged


def _resolve_status(node_id: str, job_states: dict, artifact_path: "str | None") -> str:
    """Prefer the engine's own job_states status; fall back to artifact existence."""
    entry = job_states.get(node_id)
    if entry is not None:
        raw = entry.get("status")
        return raw if raw in _KNOWN_STATUSES else "pending"
    return "done" if artifact_path else "pending"


def _resolve_artifact(output_dir: "Path | None", node_id: str, intent: Intent) -> "str | None":
    """Find the on-disk artifact for *node_id*, mirroring the engine's naming.

    Handlers write `ctx.output_dir / f"node{nid}_<suffix><ext>"` (e.g.
    `node1_image.png`) or, for the AnimateDiff case, plain `node{nid}.<ext>`
    (e.g. `node6.gif`) — so both glob patterns are checked. Output keys with
    no file counterpart (caption/text/prompt/playlist_id/...) always resolve
    to None without touching the filesystem.

    `output_dir` is `None` when the record's `output_dir` was empty/falsy
    (e.g. an old/partial record) — globbing `Path("")` would silently
    resolve to "." and scan the process's current working directory, so an
    absent output_dir short-circuits to "no artifact" instead.

    Real historical runs (`bin/run_worlds_fair.sh`, multi-job) don't write
    artifacts flat into `output_dir` — they fan the same spec out over one
    subdirectory per job (e.g. `<output_dir>/1964-ny/node1_image.png`), so a
    top-level-only glob finds nothing for those records. Search both the top
    level AND exactly one directory down (`output_dir/*/node{id}_*`) — one
    level is as deep as any known layout goes, and bounding the recursion
    (rather than `rglob`) keeps this cheap even if `output_dir` is huge.
    """
    if output_dir is None:
        return None
    if not intent.outputs:
        return None
    kind_info = _OUTPUT_KIND.get(intent.outputs[0])
    if kind_info is None:
        return None
    _kind, allowed_exts = kind_info

    candidates = (
        list(output_dir.glob(f"node{node_id}_*"))
        + list(output_dir.glob(f"node{node_id}.*"))
        + list(output_dir.glob(f"*/node{node_id}_*"))
        + list(output_dir.glob(f"*/node{node_id}.*"))
    )
    candidates = [c for c in candidates if c.is_file()]
    if allowed_exts is not None:
        candidates = [c for c in candidates if c.suffix.lower() in allowed_exts]
    if not candidates:
        return None
    # Prefer the most recently modified match (a retried node can leave more
    # than one candidate behind, e.g. node6_video.mp4 + node6_video_fixed.mp4,
    # or — for a multi-job nested run — one candidate per job subdir; picking
    # the newest resolves to a real artifact rather than leaving it blank).
    best = max(candidates, key=lambda p: p.stat().st_mtime)
    return str(best)


def _resolve_text_content(output_dir: "Path | None", node_id: str, intent: Intent) -> "str | None":
    """Find node_id's resolved TEXT output in the run's results.json, or None.

    Mirrors `_resolve_artifact`'s discipline exactly, just against a
    different file: `output_dir` absent short-circuits to None (never globs
    Path("")); both the top level AND one directory down are searched (the
    same `bin/run_worlds_fair.sh`-style nested-per-job layout `_resolve_
    artifact` already accounts for — see its docstring); a missing, unreadable,
    or malformed results.json, a non-dict payload, a node_id absent from it,
    or a value that isn't a non-empty string are all treated as "no text
    here" rather than raised — a bad/partial/pre-this-feature run record must
    never crash the Open view, only render an honest gap.

    Checks every one of `intent.outputs` (not just the first, unlike
    `_resolve_artifact`) since results.json is a flat {key: value} dict per
    node — e.g. `{"caption": "..."}`  for TTLGCaptionImage, `{"text": "..."}`
    for TTLGGenerateText — and returns the first key that resolves to a real
    string.
    """
    if output_dir is None:
        return None

    candidates = (
        list(output_dir.glob("results.json"))
        + list(output_dir.glob("*/results.json"))
    )
    candidates = [c for c in candidates if c.is_file()]
    if not candidates:
        return None
    best = max(candidates, key=lambda p: p.stat().st_mtime)

    try:
        results = json.loads(best.read_text())
    except Exception:  # noqa: BLE001 — malformed/unreadable results.json -> None
        return None
    if not isinstance(results, dict):
        return None
    node_data = results.get(node_id)
    if not isinstance(node_data, dict):
        return None

    def _is_genuine_text(key: str, value) -> bool:
        # A value a person READS — never a file path. Reject: metadata
        # (`_`-prefixed) keys; file-artifact output keys (those in
        # _OUTPUT_KIND — image/video/gif *_path outputs, rendered by
        # _resolve_artifact instead — this is the fix for the original bug
        # that showed '/…/node3_fg.png' as a step's "text"); non-strings /
        # blanks; and any value that merely looks like a media path (guards a
        # drifted/unknown key whose value is actually an image/video path).
        if key.startswith("_") or key in _OUTPUT_KIND:
            return False
        if not isinstance(value, str) or not value.strip():
            return False
        return not _looks_like_media_path(value)

    # Canonical text output keys first (skip any that are file-artifact keys),
    # then a drift-tolerant scan of the node's remaining values — real
    # historical runs record text under non-canonical keys ('poem' vs 'text',
    # 'video_prompt' vs 'prompt'), and review mode should still SHOW that text
    # rather than fall back to a bare intent icon.
    for key in intent.outputs:
        if _is_genuine_text(key, node_data.get(key)):
            return node_data[key]
    for key, value in node_data.items():
        if _is_genuine_text(key, value):
            return value
    return None


def _artifact_kind(intent: Intent) -> "str | None":
    if not intent.outputs:
        return None
    kind_info = _OUTPUT_KIND.get(intent.outputs[0])
    return kind_info[0] if kind_info else None


def _resolve_artifact_list(output_dir: "Path | None", node_id: str, intent: Intent) -> "tuple[str, ...]":
    """All of node_id's on-disk file artifacts (in order), for a fan-out step.

    A list-aware node (e.g. TTLGTextToImage given a list of prompts) records
    its primary output as a LIST of paths in results.json. Read that list and
    keep the entries that are real files with an allowed extension for the
    intent's kind — so the showcase can render every still in a series, not
    just the one `_resolve_artifact` globs. Returns () when there's no
    results.json, the node's primary output isn't a list, or nothing survives
    the existence/extension filter (same never-crash discipline as
    `_resolve_artifact`/`_resolve_text_content`). A single (scalar) output is
    NOT handled here — build_run_view falls back to `(artifact_path,)`.
    """
    if output_dir is None or not intent.outputs:
        return ()
    kind_info = _OUTPUT_KIND.get(intent.outputs[0])
    if kind_info is None:
        return ()
    _kind, allowed_exts = kind_info

    candidates = (list(output_dir.glob("results.json"))
                  + list(output_dir.glob("*/results.json")))
    candidates = [c for c in candidates if c.is_file()]
    if not candidates:
        return ()
    best = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        results = json.loads(best.read_text())
        node_data = results[node_id]
        value = node_data[intent.outputs[0]]
    except Exception:  # noqa: BLE001 — missing/malformed/absent -> no list
        return ()
    if not isinstance(value, list):
        return ()

    out: "list[str]" = []
    for p in value:
        if not isinstance(p, str) or not p:
            continue
        fp = Path(p)
        if not fp.is_file():
            continue
        if allowed_exts is not None and fp.suffix.lower() not in allowed_exts:
            continue
        out.append(p)
    return tuple(out)


def build_run_view(record: dict) -> RunView:
    """Build a `RunView` from a raw `PipelineStore` run record.

    Raises whatever `pipeline_engine.load_spec`/`topo_order` raise if the
    spec is missing/malformed/cyclic — callers that need to tolerate a bad
    record (e.g. `list_run_views`) should catch around this call.
    """
    spec = load_spec(record["spec_path"])
    order = topo_order(spec)
    # An empty/falsy output_dir (e.g. an old/partial record) must not become
    # Path("") — that normalizes to "." and would glob the current working
    # directory in _resolve_artifact. None short-circuits every step to "no
    # artifact" instead of scanning cwd. See _resolve_artifact.
    raw_output_dir = record["output_dir"]
    output_dir = Path(raw_output_dir) if raw_output_dir else None
    job_states = _merged_job_states(record)

    steps: list[StepView] = []
    hero_path: "str | None" = None
    for node_id in order:
        class_type = spec[node_id]["class_type"]
        intent = intent_for(class_type)
        artifact_path = _resolve_artifact(output_dir, node_id, intent)
        status = _resolve_status(node_id, job_states, artifact_path)
        # Only look for a text output when there's no file artifact to show
        # instead — a step with both would be unusual, and the artifact
        # (image/gif/video) is always the more informative thing to render.
        text_content = (
            None if artifact_path else _resolve_text_content(output_dir, node_id, intent)
        )
        # Fan-out steps record a LIST of artifacts in results.json; fall back to
        # the single globbed artifact_path (wrapped) for ordinary steps.
        artifact_paths = _resolve_artifact_list(output_dir, node_id, intent)
        if not artifact_paths and artifact_path:
            artifact_paths = (artifact_path,)
        steps.append(StepView(node_id=node_id, intent=intent, status=status,
                              artifact_path=artifact_path, text_content=text_content,
                              artifact_paths=artifact_paths))
        if hero_path is None and artifact_path and _artifact_kind(intent) in _HERO_KINDS:
            hero_path = artifact_path

    title = record.get("spec_name") or Path(record["spec_path"]).stem
    recipe = [label(spec[node_id]["class_type"]) for node_id in order]

    return RunView(
        run_id=record["id"],
        title=title,
        created_at=record.get("started_at"),
        hero_path=hero_path,
        steps=steps,
        recipe=recipe,
    )


def final_index_for(run: RunView) -> "int | None":
    """Index into `run.steps` of the step that produced `run.hero_path`.

    Promotes the previously-unused `hero_path` field (set by `build_run_view`
    to the first heroable image/video artifact) into "which STEP is the
    deliverable" — OpenView uses this to render that step as a large
    "Here's what you made" hero instead of just another row in the list.

    Pure/GTK-free, like the rest of this module. Returns None whenever there
    is nothing to point at: `hero_path` itself is None (no heroable artifact
    was produced at all — deliberately NOT matched against a step whose own
    `artifact_path` also happens to be None, which would be a false
    positive), or `hero_path` doesn't equal any current step's
    `artifact_path` (e.g. a stale value from a since-pruned/changed run).
    """
    if run.hero_path is None:
        return None
    for index, step in enumerate(run.steps):
        if step.artifact_path == run.hero_path:
            return index
    return None


def list_run_views(store, limit: int = 50) -> "list[RunView]":
    """Build `RunView`s for `store.list_runs()`, skipping unloadable records.

    A record whose spec is missing/moved/malformed must not take down the
    whole Discover/Open view — it's logged and dropped instead.
    """
    views: list[RunView] = []
    for record in store.list_runs(limit=limit):
        try:
            views.append(build_run_view(record))
        except Exception:  # noqa: BLE001 — any load/parse failure is skippable
            log.warning("skipping run %s: could not build view",
                       record.get("id"), exc_info=True)
    return views
