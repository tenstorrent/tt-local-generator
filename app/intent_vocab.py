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
    input_key   — the request-payload key that an upstream artifact wires
                  into (e.g. "prompt", "src", "image"). None for source nodes
                  (nothing upstream to wire — a from-scratch generator) and
                  for collector/plugin-driven nodes that don't have a single
                  canonical artifact input (TTLGAddToPlaylist, TTLGArtgenGenerate).
    input_kind  — the artifact KIND that `input_key` consumes: "image" | "text"
                  | "video" | "gif" | "svg" | "playlist" | None. None whenever
                  `input_key` is None.
    output_kind — the artifact KIND this node's primary output produces, using
                  the same vocabulary as `input_kind`. Used by the composer
                  (`compatible_intents`) to find valid "add a step after this
                  one" candidates.
    """
    class_type: str
    verb: str
    noun: str
    icon: str
    outputs: tuple[str, ...]
    model_label: str | None
    input_key: str | None = None
    input_kind: str | None = None
    output_kind: str | None = None


# ── The 14 native class_types the pipeline engine supports ───────────────────
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
        input_key="prompt",
        input_kind="text",
        output_kind="image",
    ),
    "TTLGImageToVideo": Intent(
        class_type="TTLGImageToVideo",
        verb="Film",
        noun="it",
        icon="🎬",
        outputs=("video_path",),
        model_label="SkyReels",
        input_key="image",
        input_kind="image",
        output_kind="video",
    ),
    "TTLGGenerateText": Intent(
        class_type="TTLGGenerateText",
        verb="Write",
        noun="about it",
        icon="✍️",
        outputs=("text",),
        model_label="Llama",
        input_key="caption",
        input_kind="text",
        output_kind="text",
    ),
    "TTLGCaptionImage": Intent(
        class_type="TTLGCaptionImage",
        verb="Describe",
        noun="it",
        icon="📝",
        outputs=("caption",),
        model_label=None,
        input_key="src",
        input_kind="image",
        output_kind="text",
    ),
    "TTLGRemoveBackground": Intent(
        class_type="TTLGRemoveBackground",
        verb="Cut out",
        noun="the subject",
        icon="✂️",
        outputs=("fg_path",),
        model_label=None,
        input_key="src",
        input_kind="image",
        output_kind="image",
    ),
    "TTLGEstimateDepth": Intent(
        class_type="TTLGEstimateDepth",
        verb="Read",
        noun="its depth",
        icon="🗺️",
        outputs=("depth_path",),
        model_label=None,
        input_key="src",
        input_kind="image",
        output_kind="image",
    ),
    "TTLGPromptCompose": Intent(
        class_type="TTLGPromptCompose",
        verb="Compose",
        noun="a prompt",
        icon="🧩",
        outputs=("prompt",),
        model_label=None,
        input_key="caption",
        input_kind="text",
        output_kind="text",
    ),
    "TTLGPaletteToPrompt": Intent(
        class_type="TTLGPaletteToPrompt",
        verb="Describe",
        noun="a palette",
        icon="🎨",
        outputs=("prompt",),
        model_label=None,
        input_key=None,      # source-style: prompt is computed at seed time
        input_kind=None,
        output_kind="text",
    ),
    "TTLGSVGRender": Intent(
        class_type="TTLGSVGRender",
        verb="Render",
        noun="a drawing",
        icon="🖊️",
        outputs=("png_path",),
        model_label=None,
        input_key="src",
        input_kind="text",
        output_kind="image",
    ),
    "TTLGComposite": Intent(
        class_type="TTLGComposite",
        verb="Combine",
        noun="them",
        icon="🧷",
        outputs=("image_path",),
        model_label=None,
        input_key="background_path",
        input_kind="image",
        output_kind="image",
    ),
    "TTLGAddToPlaylist": Intent(
        class_type="TTLGAddToPlaylist",
        verb="Collect",
        noun="the results",
        icon="📼",
        outputs=("playlist_id",),
        model_label=None,
        input_key=None,
        input_kind=None,
        output_kind="playlist",
    ),
    "TTLGArtgenGenerate": Intent(
        class_type="TTLGArtgenGenerate",
        verb="Make",
        noun="generative art",
        icon="🎨",
        outputs=("artifact_path", "text", "png_path"),
        model_label=None,
        input_key=None,
        input_kind=None,
        output_kind="text",
    ),
    "TTLGAnimateDiff": Intent(
        class_type="TTLGAnimateDiff",
        verb="Animate",
        noun="a prompt",
        icon="🕺",
        outputs=("gif_path",),
        model_label=None,
        input_key="prompt",
        input_kind="text",
        output_kind="gif",
    ),
    "TTLGSplitText": Intent(
        class_type="TTLGSplitText",
        verb="Break",
        noun="into fragments",
        icon="📑",
        outputs=("fragments",),
        model_label=None,
        input_key="text",
        input_kind="text",
        output_kind="text",
    ),
    "TTLGMontage": Intent(
        class_type="TTLGMontage",
        verb="Stitch",
        noun="a montage",
        icon="🎞️",
        outputs=("video_path",),
        model_label=None,
        input_key="images",
        input_kind="image",
        output_kind="video",
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
    get a generic "Run this step" intent rather than raising, so UI code
    can always render *something* for any node in a loaded spec — without
    ever leaking the raw class_type into a user-facing label.
    """
    if class_type in INTENTS:
        return INTENTS[class_type]
    # The label ("Run this step") must never contain the raw class_type —
    # that's exactly the tool-name leak this vocabulary layer exists to
    # prevent (reachable when an imported spec has a non-native/optional
    # passthrough node). class_type is still preserved on the Intent itself
    # for lookups/detail views; it's only kept out of verb/noun.
    return Intent(class_type, _GENERIC_VERB, "this step", _GENERIC_ICON, (), None)


def compatible_intents(output_kind: str) -> list[Intent]:
    """All native intents that can consume an artifact of `output_kind` as
    their next-step input — i.e. `intent.input_kind == output_kind`.

    Used by the composer (later SP-C tasks) to offer "add a step after this
    one" choices: given the kind of artifact the current step just produced,
    which intents could wire it in as their input?

    Order is deterministic — INTENTS insertion order (the dict literal order
    above), which is itself the fixed native class_type list — so repeated
    calls with the same `output_kind` always return the same sequence.
    """
    return [i for i in INTENTS.values() if i.input_kind == output_kind]


# Cross-type adapters: (seed_kind, needed_input_kind) -> converter class_type.
# When a remix seed's kind doesn't directly match a goal's first-step input,
# the Muse consults this to offer the goal and prepend the converter. Ships
# palette->text only; more entries (e.g. ("image","text"):"TTLGCaptionImage")
# can be added later without touching call sites.
ADAPTERS: "dict[tuple[str, str], str]" = {
    ("palette", "text"): "TTLGPaletteToPrompt",
}


def adapter_for(seed_kind: "str | None", input_kind: "str | None") -> "str | None":
    """The converter class_type that turns a `seed_kind` artifact into an
    `input_kind` input, or None if no adapter is registered."""
    if not seed_kind or not input_kind:
        return None
    return ADAPTERS.get((seed_kind, input_kind))


def label(class_type: str) -> str:
    """Human, tool-agnostic "verb noun" label for a class_type.

    e.g. label("TTLGTextToImage") -> "Generate an image". Never includes the
    class_type prefix ("TTLG") or a model name — those live in
    Intent.class_type / Intent.model_label for detail views only.
    """
    i = intent_for(class_type)
    return f"{i.verb} {i.noun}"
