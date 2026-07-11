# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Intent-vocabulary layer for Pipeline Studio (SP-C).

Pipeline Studio presents pipelines to users in INTENT language — a plain
verb + noun description of what a node *does* ("Generate an image") — rather
than the underlying tool/model/class_type name ("TTLGTextToImage" running on
"FLUX"). This module is the single source of truth mapping every native
node's class_type to its Intent.

This module has NO GTK imports and is unit-tested without a display. Later
GTK views (Discover/Open in Pipeline Studio) import `INTENTS`/`intent_for`/
`label` from here so every screen speaks the same intents — add a class_type
here once and every view picks it up.

`outputs` mirrors the SP-A output-key contract (see
docs/superpowers/specs/2026-07-11-pipeline-node-coverage-design.md and the
COMPATIBILITY_MAP comments in workflow_compat.py) so downstream wiring code
can validate/introspect a node's produced keys without importing the engine.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Intent:
    """A human-facing description of what a native node type does.

    class_type  — the engine's node class_type (e.g. "TTLGTextToImage").
    verb        — action word, e.g. "Generate".
    noun        — object of the action, e.g. "an image".
    icon        — a single tasteful emoji for compact UI chips.
    outputs     — the exact output keys this node publishes (must match the
                  engine's real output-key contract — see module docstring).
    model_label — the underlying model/tool name (e.g. "FLUX"), shown only in
                  a secondary/detail context — never folded into `verb`/`noun`
                  or the `label()` string, which stay tool-agnostic.
    """
    class_type: str
    verb: str
    noun: str
    icon: str
    outputs: tuple[str, ...]
    model_label: str | None


# ── The 12 native class_types the pipeline engine supports ───────────────────
#
# One Intent per native class_type from workflow_compat.COMPATIBILITY_MAP
# (entries where ttlg == class_type, i.e. exact native nodes — not the
# ComfyUI-mapped or skippable tiers, which aren't "native" and have no
# single canonical intent of their own).
INTENTS: dict[str, Intent] = {
    "TTLGTextToImage": Intent(
        class_type="TTLGTextToImage",
        verb="Generate",
        noun="an image",
        icon="🖼️",
        outputs=("image_path",),
        model_label="FLUX",
    ),
    "TTLGImageToVideo": Intent(
        class_type="TTLGImageToVideo",
        verb="Film",
        noun="it",
        icon="🎬",
        outputs=("video_path",),
        model_label="SkyReels",
    ),
    "TTLGGenerateText": Intent(
        class_type="TTLGGenerateText",
        verb="Write",
        noun="about it",
        icon="✍️",
        outputs=("text",),
        model_label="Llama",
    ),
    "TTLGCaptionImage": Intent(
        class_type="TTLGCaptionImage",
        verb="Describe",
        noun="it",
        icon="📝",
        outputs=("caption",),
        model_label=None,
    ),
    "TTLGRemoveBackground": Intent(
        class_type="TTLGRemoveBackground",
        verb="Cut out",
        noun="the subject",
        icon="✂️",
        outputs=("fg_path",),
        model_label=None,
    ),
    "TTLGEstimateDepth": Intent(
        class_type="TTLGEstimateDepth",
        verb="Read",
        noun="its depth",
        icon="🗺️",
        outputs=("depth_path",),
        model_label=None,
    ),
    "TTLGPromptCompose": Intent(
        class_type="TTLGPromptCompose",
        verb="Compose",
        noun="a prompt",
        icon="🧩",
        outputs=("prompt",),
        model_label=None,
    ),
    "TTLGSVGRender": Intent(
        class_type="TTLGSVGRender",
        verb="Render",
        noun="a drawing",
        icon="🖊️",
        outputs=("png_path",),
        model_label=None,
    ),
    "TTLGComposite": Intent(
        class_type="TTLGComposite",
        verb="Combine",
        noun="them",
        icon="🧷",
        outputs=("image_path",),
        model_label=None,
    ),
    "TTLGAddToPlaylist": Intent(
        class_type="TTLGAddToPlaylist",
        verb="Collect",
        noun="the results",
        icon="📼",
        outputs=("playlist_id",),
        model_label=None,
    ),
    "TTLGArtgenGenerate": Intent(
        class_type="TTLGArtgenGenerate",
        verb="Make",
        noun="generative art",
        icon="🎨",
        outputs=("artifact_path", "text", "png_path"),
        model_label=None,
    ),
    "TTLGAnimateDiff": Intent(
        class_type="TTLGAnimateDiff",
        verb="Animate",
        noun="a prompt",
        icon="🕺",
        outputs=("gif_path",),
        model_label=None,
    ),
}


# Generic fallback for any class_type not (yet) in the vocabulary — keeps
# unrecognized/experimental nodes render-able instead of crashing a view.
_GENERIC_VERB = "Run"
_GENERIC_ICON = "•"


def intent_for(class_type: str) -> Intent:
    """Look up the Intent for a class_type, falling back to a generic one.

    Unknown class_types (not yet in INTENTS — e.g. a brand-new experimental
    node type, or a mapped/skippable ComfyUI class_type that isn't native)
    get a generic "Run <class_type>" intent rather than raising, so UI code
    can always render *something* for any node in a loaded spec.
    """
    if class_type in INTENTS:
        return INTENTS[class_type]
    return Intent(class_type, _GENERIC_VERB, class_type, _GENERIC_ICON, (), None)


def label(class_type: str) -> str:
    """Human, tool-agnostic "verb noun" label for a class_type.

    e.g. label("TTLGTextToImage") -> "Generate an image". Never includes the
    class_type prefix ("TTLG") or a model name — those live in
    Intent.class_type / Intent.model_label for detail views only.
    """
    i = intent_for(class_type)
    return f"{i.verb} {i.noun}"
