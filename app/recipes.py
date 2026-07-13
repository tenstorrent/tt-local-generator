# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Goal catalog for the Muse — Pipeline Studio's creative wizard (SP-C Phase 2b-3
Task 2).

Where `intent_vocab.py` names what a single native node *does* and
`spec_remix.py` wires nodes into a runnable spec, this module answers the
Muse's actual question: "what could you MAKE?" A `Goal` is a hand-curated (or
plugin-discovered) starter pipeline — a short ordered list of
(class_type, params) steps — described in pure intent language ("A looping
animation") rather than tool/model names, exactly like every other SP-C
surface.

Two sources feed the catalog:

  1. **Curated** (`_CURATED` / `curated_goals()`) — a fixed, hand-verified
     list of goals the app ships with. Every entry's step chain has already
     been checked kind-safe against `intent_vocab.intent_for(...).input_kind`
     (see the task brief this module was built from), so `build_seed_spec`
     never raises for a curated goal.

  2. **Discovered** (`discover_goals()`) — plugins can advertise their own
     goal via an `x-ttlg.goal` block in `plugins/<name>/mcp.json`. This
     module reads plugin manifests DIRECTLY (via an injected `mcp_reader`),
     not through `capability_discovery.load_plugin_capabilities()` — that
     function maps every manifest into a fixed `Capability`-shaped dict and
     drops any keys it doesn't recognize, `x-ttlg.goal` included. Reading raw
     manifests here is the only way to see that block.

Both sources merge in `all_goals()`, curated winning any `id` collision, and
`goals_for()` is the one entry point the Muse UI actually calls: it filters
by wizard mode (blank canvas vs. "starting from this artifact") and, in the
scoped case, by whether the goal's first step can actually consume the seed
artifact's kind.

This module has **zero GTK imports** and does no I/O of its own beyond the
injected `mcp_reader` — `discover_goals`/`all_goals`/`goals_for` all accept
`mcp_reader=None` and lazily default to the real reader
(`capability_discovery._read_all_plugin_mcp`) only when actually called
with no override, keeping this module importable and unit-testable without
ever touching disk.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

from intent_vocab import intent_for
from spec_remix import seed_spec


@dataclass(frozen=True)
class Goal:
    """One starter pipeline the Muse can offer.

    id           — stable slug (e.g. "looping-animation"). Used for dedup in
                   `all_goals()` and as a widget/history key by the UI.
    label        — intent-language card text (e.g. "A looping animation").
                   Never a raw class_type or model name — same rule as
                   `intent_vocab.label()`.
    icon         — one emoji for the goal's card.
    output_kind  — the artifact kind the finished pipeline is meant to
                   produce, for display purposes (a short descriptive tag,
                   not necessarily identical to the literal `output_kind` of
                   the recipe's final step — e.g. a goal ending in
                   TTLGAddToPlaylist is still described by the kind of thing
                   collected into it).
    applies_to   — "blank" (only offered with no seed artifact), "scoped"
                   (only offered when starting from an existing artifact), or
                   "both".
    recipe_steps — ordered `(class_type, params)` pairs, exactly the shape
                   `spec_remix.seed_spec()` consumes.
    via          — "curated" (shipped with the app) or "discovered" (read
                   from a plugin manifest's `x-ttlg.goal` block).
    """
    id: str
    label: str
    icon: str
    output_kind: str
    applies_to: str
    recipe_steps: "tuple[tuple[str, dict], ...]"
    via: str = "curated"


# ── Curated core ─────────────────────────────────────────────────────────────
#
# Every recipe below has been hand-verified kind-safe: each step's
# `intent_for(class_type).input_kind` matches the previous step's
# `output_kind` (or, for "scoped" goals, the seed artifact's kind at step 0).
# `build_seed_spec` / `spec_remix.seed_spec` re-validates this at call time
# (raises ValueError on a real mismatch) so a mistake here would fail loudly
# in `test_curated_goals_nonempty_and_kind_safe`, not silently.
_CURATED: "list[Goal]" = [
    # Every BLANK goal's first step carries a short, evocative default
    # literal on its intent's canonical input_key (see intent_vocab.intent_for)
    # so the composer always seeds an editable field — the user rewrites the
    # placeholder rather than typing into a blank box. Scoped goals below
    # deliberately have NO such literal: their first step's canonical input is
    # filled by the seed artifact at build_seed_spec() time instead.
    Goal("poster", "A poster", "🖼", "image", "blank",
         (("TTLGPromptCompose", {"caption": "a striking poster"}),
          ("TTLGTextToImage", {}))),
    Goal("looping-animation", "A looping animation", "🔁", "gif", "blank",
         (("TTLGAnimateDiff", {"seamless_loop": True,
                               "prompt": "a dreamy, seamlessly looping scene"}),)),
    Goal("illustrated-poem", "An illustrated poem", "📜", "image", "blank",
         (("TTLGGenerateText", {"caption": "a short, evocative poem"}),
          ("TTLGTextToImage", {}), ("TTLGAddToPlaylist", {}))),
    Goal("short-film", "A short film", "🎬", "video", "blank",
         (("TTLGPromptCompose", {"caption": "a cinematic short film"}),
          ("TTLGTextToImage", {}), ("TTLGImageToVideo", {}))),
    Goal("explorable-world", "An explorable world", "🌍", "image", "blank",
         (("TTLGTextToImage", {"prompt": "a vast, explorable world"}),
          ("TTLGEstimateDepth", {}), ("TTLGAddToPlaylist", {}))),
    # scoped — first step consumes the seed artifact
    Goal("animate-this", "A looping animation", "🔁", "video", "scoped",
         (("TTLGImageToVideo", {}),)),
    Goal("poem-about-this", "A poem about it", "📜", "text", "scoped",
         (("TTLGCaptionImage", {}), ("TTLGGenerateText", {}))),
    Goal("depth-scene", "A depth scene", "🌀", "image", "scoped",
         (("TTLGEstimateDepth", {}),)),
    Goal("variations", "Variations", "🎨", "image", "scoped",
         (("TTLGCaptionImage", {}), ("TTLGPromptCompose", {}), ("TTLGTextToImage", {}))),
    Goal("film-this", "A short film", "🎬", "video", "scoped",
         (("TTLGImageToVideo", {}),)),
    # scoped, text-consuming — "Make this lore into…" (Muse text-seed bridge).
    # illustrated-series is a DAG: TTLGMontage and TTLGAddToPlaylist both
    # consume node 2's image batch, expressed purely via [node_id, key] wires
    # in params (seed_spec's own auto-wire only chains consecutive steps).
    Goal("illustrated-series", "An illustrated series", "📽", "video", "scoped",
         (("TTLGSplitText", {"mode": "paragraphs", "max_items": 8}),
          ("TTLGTextToImage", {"style_suffix": ", cinematic, richly detailed, atmospheric"}),
          ("TTLGMontage", {"captions": ["2", "prompts"], "seconds_per": 2.5}),
          ("TTLGAddToPlaylist", {"artifacts": ["2", "image_path"], "captions": ["2", "prompts"],
                                 "playlist_name": "lore series"}))),
    Goal("illustrate-it", "An illustration", "🖼", "image", "scoped",
         (("TTLGTextToImage", {"style_suffix": ", cinematic, richly detailed"}),)),
    Goal("lore-poster", "A poster", "🖼", "image", "scoped",
         (("TTLGTextToImage", {"style_suffix": ", bold poster art, dramatic composition"}),)),
]


def curated_goals() -> "list[Goal]":
    """The fixed, hand-verified goal list this app ships with, in
    declaration order."""
    return list(_CURATED)


# ── Discovery ────────────────────────────────────────────────────────────────


def _slug(label: str) -> str:
    """Filesystem/id-safe slug for a discovered goal missing an explicit
    `id` (lowercase, hyphenated — same shape as showcase._slugify)."""
    slug = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    return slug or "goal"


def discover_goals(*, mcp_reader: "Callable[[], dict] | None" = None) -> "list[Goal]":
    """Read every plugin's RAW manifest (not via
    `capability_discovery.load_plugin_capabilities`, which drops the
    `x-ttlg.goal` block entirely) and build a `Goal` for each one that
    advertises one.

    `mcp_reader()` -> `{plugin_name: manifest_dict}`, the same shape
    `capability_discovery._read_all_plugin_mcp` produces (that's the real
    reader used when `mcp_reader` is None). Never raises: a `mcp_reader` that
    raises, or returns something falsy, yields `[]`; a single malformed
    manifest is skipped (via its own try/except) without taking down the rest
    of the list — mirroring `load_plugin_capabilities`'s own robustness rules.

    Utility plugins (`x-ttlg.utility: true` — blip/depth/ffmpeg/rmbg, whose
    functionality is already exposed as native intents) are skipped, same as
    `load_plugin_capabilities`.
    """
    if mcp_reader is None:
        from capability_discovery import _read_all_plugin_mcp as mcp_reader

    try:
        raw_map = mcp_reader() or {}
    except Exception:
        return []

    goals: "list[Goal]" = []
    for manifest in raw_map.values():
        try:
            xt = manifest.get("x-ttlg", {}) or {}
            if xt.get("utility"):
                continue
            goal = xt.get("goal")
            if not goal:
                continue

            recipe_steps = tuple((ct, {}) for ct in goal["recipe"])
            goals.append(Goal(
                id=goal.get("id") or _slug(goal["label"]),
                label=goal["label"],
                icon=goal["icon"],
                output_kind=goal["output_kind"],
                applies_to="both",
                recipe_steps=recipe_steps,
                via="discovered",
            ))
        except Exception:
            # One malformed manifest must not take down the whole list.
            continue
    return goals


def all_goals(*, mcp_reader: "Callable[[], dict] | None" = None) -> "list[Goal]":
    """Curated + discovered goals, deduplicated by `id` (curated wins).

    Deterministic order: curated goals first (declaration order), then any
    discovered goal whose `id` isn't already claimed by a curated one (also
    in the order `discover_goals` returned them).
    """
    curated = curated_goals()
    curated_ids = {g.id for g in curated}
    discovered = [g for g in discover_goals(mcp_reader=mcp_reader)
                  if g.id not in curated_ids]
    return curated + discovered


def goals_for(*, seed_output_kind: "Optional[str]" = None,
              mcp_reader: "Callable[[], dict] | None" = None) -> "list[Goal]":
    """The goals the Muse should actually offer for the current wizard mode.

    `seed_output_kind is None` — "blank canvas" mode: every goal whose
    `applies_to` is "blank" or "both".

    `seed_output_kind` set — "starting from an artifact of this kind" mode:
    every goal whose `applies_to` is "scoped" or "both" AND whose first
    recipe step can actually consume that kind
    (`intent_vocab.intent_for(first_class_type).input_kind == seed_output_kind`)
    — kind-unsafe goals are never offered rather than being offered and
    failing later in `build_seed_spec`.

    Order: `all_goals()`'s order (curated before discovered, each in
    declaration order), filtered in place.
    """
    goals = all_goals(mcp_reader=mcp_reader)
    if seed_output_kind is None:
        return [g for g in goals if g.applies_to in ("blank", "both")]

    result = []
    for g in goals:
        if g.applies_to not in ("scoped", "both"):
            continue
        first_ct = g.recipe_steps[0][0]
        if intent_for(first_ct).input_kind == seed_output_kind:
            result.append(g)
    return result


def build_seed_spec(goal: Goal, *,
                     seed_artifact: "tuple[str, str] | None" = None) -> dict:
    """Materialize *goal* into a runnable spec via `spec_remix.seed_spec`.

    Thin delegation — `Goal.recipe_steps` is already exactly the
    `list[tuple[class_type, params]]` shape `seed_spec` expects. Raises
    `ValueError` (propagated from `seed_spec`) if the goal's steps or the
    seed artifact's kind aren't actually kind-compatible — should never
    happen for a curated goal (see `_CURATED`'s docstring) or a goal reached
    through `goals_for`'s kind filter, but is not re-checked here so a
    caller that bypasses `goals_for` still gets a loud failure instead of a
    silently broken spec.
    """
    return seed_spec(list(goal.recipe_steps), seed_artifact=seed_artifact)
