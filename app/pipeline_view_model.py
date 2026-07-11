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
`output_dir` for `node{id}_*` and `node{id}.*`, filter to the extensions that
match the node's primary output kind, and — since a retried node can leave
behind more than one candidate file (e.g. a fixed re-render) — prefer the
most recently modified match.
"""
from __future__ import annotations

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


@dataclass
class StepView:
    """One pipeline node as the UI renders it.

    status ∈ {"done", "running", "pending", "failed"}.
    """
    node_id: str
    intent: Intent
    status: str
    artifact_path: "str | None"


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


def _resolve_artifact(output_dir: Path, node_id: str, intent: Intent) -> "str | None":
    """Find the on-disk artifact for *node_id*, mirroring the engine's naming.

    Handlers write `ctx.output_dir / f"node{nid}_<suffix><ext>"` (e.g.
    `node1_image.png`) or, for the SVG-render/AnimateDiff cases, plain
    `node{nid}.<ext>` — so both glob patterns are checked. Output keys with
    no file counterpart (caption/text/prompt/playlist_id/...) always resolve
    to None without touching the filesystem.
    """
    if not intent.outputs:
        return None
    kind_info = _OUTPUT_KIND.get(intent.outputs[0])
    if kind_info is None:
        return None
    _kind, allowed_exts = kind_info

    candidates = list(output_dir.glob(f"node{node_id}_*")) + \
        list(output_dir.glob(f"node{node_id}.*"))
    if allowed_exts is not None:
        candidates = [c for c in candidates if c.suffix.lower() in allowed_exts]
    if not candidates:
        return None
    # Prefer the most recently modified match (a retried node can leave more
    # than one candidate behind, e.g. node6_video.mp4 + node6_video_fixed.mp4).
    best = max(candidates, key=lambda p: p.stat().st_mtime)
    return str(best)


def _artifact_kind(intent: Intent) -> "str | None":
    if not intent.outputs:
        return None
    kind_info = _OUTPUT_KIND.get(intent.outputs[0])
    return kind_info[0] if kind_info else None


def build_run_view(record: dict) -> RunView:
    """Build a `RunView` from a raw `PipelineStore` run record.

    Raises whatever `pipeline_engine.load_spec`/`topo_order` raise if the
    spec is missing/malformed/cyclic — callers that need to tolerate a bad
    record (e.g. `list_run_views`) should catch around this call.
    """
    spec = load_spec(record["spec_path"])
    order = topo_order(spec)
    output_dir = Path(record["output_dir"])
    job_states = _merged_job_states(record)

    steps: list[StepView] = []
    hero_path: "str | None" = None
    for node_id in order:
        class_type = spec[node_id]["class_type"]
        intent = intent_for(class_type)
        artifact_path = _resolve_artifact(output_dir, node_id, intent)
        status = _resolve_status(node_id, job_states, artifact_path)
        steps.append(StepView(node_id=node_id, intent=intent, status=status,
                              artifact_path=artifact_path))
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
