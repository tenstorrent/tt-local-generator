# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Wing-it mapper — free-form text -> a concrete pipeline step (SP-C Phase 2b-3
Task 1).

The composer's "wing it" affordance lets a user type what they want the next
step to do in their own words ("make it look like a watercolor", "turn this
into a short clip") instead of picking a capability off the "add a step"
list by hand. This module is the pure core that turns that text into a
concrete ``{class_type, params}`` pair the engine can actually run — plus a
deterministic fallback for when no LLM is available (or it misbehaves), so
"wing it" never just fails.

Design mirrors ``capability_discovery.py``'s pure-core-plus-thin-wrapper
split: `map_freeform_to_step` takes an injected `llm_fn` and does zero
network/subprocess I/O itself, so it's fully unit-testable with a fake.
`default_llm_fn` at the bottom is the thin real wrapper the composer UI
actually passes in — it resolves the live artgen chat endpoint
(`artgen.detect_artgen_endpoint`) and calls it (`artgen.call_llm`), collapsing
any failure (no endpoint, network error, malformed response) to `None` so the
core's fallback path takes over exactly the same way it would for a fake
`llm_fn` in tests.

Only LIVE capabilities (`Capability.live`) are ever offered to the LLM or
considered by the fallback — offering a latent capability (needs a server
that isn't running) would produce a step that can't actually execute.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Optional

from capability_discovery import Capability
from intent_vocab import intent_for


@dataclass
class WingitResult:
    """The mapped-to step: which node to run, with what params.

    class_type    — the engine class_type to instantiate (see
                     ``capability_discovery.Capability.class_type``).
    params        — literal inputs to merge onto the new node (already
                     includes ``{"plugin": ...}`` for plugin capabilities).
    capability_id — the ``Capability.id`` that was matched, for UI display
                     ("wing-it picked: Make generative art").
    via           — "llm" when the injected `llm_fn` produced a valid,
                     in-vocabulary answer; "fallback" when it didn't (or
                     wasn't given at all) and the deterministic path ran.
    """
    class_type: str
    params: dict
    capability_id: str
    via: str  # "llm" | "fallback"


# ── LLM response cleanup ──────────────────────────────────────────────────────
#
# Small local models routinely wrap JSON in <think>...</think> reasoning
# blocks and/or markdown code fences, and sometimes add a sentence of prose
# before/after the JSON itself. None of that is a parse failure — only an
# actually-malformed or missing JSON object is.

_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)

# Param values plausible to carry over from an LLM's JSON verbatim. Anything
# else (nested list/dict/null) is dropped rather than guessed at — "lenient"
# means "don't reject the whole answer over one weird param", not "accept
# arbitrary structure".
_PLAUSIBLE_PARAM_TYPES = (str, int, float, bool)


def _strip_noise(raw: str) -> str:
    """Remove think-blocks, then unwrap the first fenced code block if any."""
    text = _THINK_RE.sub("", raw)
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1)
    return text


def _extract_first_balanced(text: str, open_ch: str, close_ch: str):
    """Return the first balanced ``open_ch...close_ch`` substring in *text*,
    parsed as JSON — or None if there's no opener, the delimiters never
    balance, or the matched substring isn't valid JSON.

    Shared by `_extract_first_json_object` (``{``/``}``, single-step wing-it)
    and `_extract_first_json_array` (``[``/``]``, multi-step pipeline draft):
    both tolerate arbitrary prose before/after the JSON by scanning for the
    first opener and tracking nesting depth rather than assuming the whole
    string is JSON.
    """
    start = text.find(open_ch)
    if start == -1:
        return None

    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    return json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    return None
    return None  # delimiters never balanced


def _extract_first_json_object(text: str) -> Optional[dict]:
    """Return the first balanced ``{...}`` substring in *text*, parsed as
    JSON, or None if it isn't a JSON object (see `_extract_first_balanced`).
    """
    obj = _extract_first_balanced(text, "{", "}")
    return obj if isinstance(obj, dict) else None


def _extract_first_json_array(text: str) -> Optional[list]:
    """Return the first balanced ``[...]`` substring in *text*, parsed as
    JSON, or None if it isn't a JSON array (see `_extract_first_balanced`).
    """
    arr = _extract_first_balanced(text, "[", "]")
    return arr if isinstance(arr, list) else None


# ── Prompt building ────────────────────────────────────────────────────────────


def _build_prompt(text: str, live_caps: "list[Capability]") -> str:
    lines = [
        "You are choosing the next step in a media-generation pipeline based "
        "on what the user typed.",
        "",
        "Available capabilities (pick exactly one, by id):",
    ]
    for cap in live_caps:
        lines.append(
            f"- id={cap.id!r} label={cap.label!r} "
            f"kind_in={cap.kind_in!r} kind_out={cap.kind_out!r}"
        )
    lines += [
        "",
        f'User request: "{text}"',
        "",
        "Respond with ONLY a JSON object, no other text: "
        '{"capability_id": "<one of the ids above>", "params": {...}}',
    ]
    return "\n".join(lines)


def _build_pipeline_prompt(
    text: str,
    live_caps: "list[Capability]",
    seed_output_kind: Optional[str],
    max_steps: int,
) -> str:
    """Prompt asking for an ORDERED LIST of steps rather than a single one —
    the multi-step analogue of `_build_prompt`, for `map_freeform_to_pipeline`.
    """
    lines = [
        "You are drafting a short pipeline (an ordered list of steps) for a "
        "media-generation tool, based on what the user typed.",
        "",
        "Available capabilities (each step must name one, by id):",
    ]
    for cap in live_caps:
        lines.append(
            f"- id={cap.id!r} label={cap.label!r} "
            f"kind_in={cap.kind_in!r} kind_out={cap.kind_out!r}"
        )
    seed_desc = (
        seed_output_kind if seed_output_kind is not None
        else "nothing yet -- just this text"
    )
    lines += [
        "",
        f'User request: "{text}"',
        f"Current seed kind: {seed_desc}",
        "",
        f"Respond with ONLY a JSON array of up to {max_steps} steps, no "
        'other text: [{"capability_id": "<one of the ids above>", '
        '"params": {...}}, ...]. Each step after the first must be able to '
        "consume the previous step's output as its input.",
    ]
    return "\n".join(lines)


# ── Fallback selection ─────────────────────────────────────────────────────────


def _primary_text_param_key(cap: Capability) -> str:
    """The input key that should receive the user's raw text for *cap*.

    Native intents: use the intent's own `input_key` only when it's declared
    a TEXT input (`input_kind == "text"`) — wiring free text into e.g.
    TTLGImageToVideo's `image` key would be nonsensical. Anything else
    (including every plugin, which runs through the generic
    TTLGArtgenGenerate node) falls back to "prompt", the conventional
    seed-text key artgen-style generators expect.
    """
    if cap.source != "plugin":
        intent = intent_for(cap.class_type)
        if intent.input_key and intent.input_kind == "text":
            return intent.input_key
    return "prompt"


def _kind_fits(
    kind_in: Optional[str], target_kind: Optional[str], *, seed_text_ok: bool = False
) -> bool:
    """Would a capability with this `kind_in` accept an artifact of
    `target_kind`?

    A capability with `kind_in is None` is a source/seed capability (e.g.
    every plugin) that doesn't wire an upstream artifact in at all, so it
    always fits. Otherwise the kinds must match exactly -- except when
    `seed_text_ok` is set and `target_kind` is None: that's
    `map_freeform_to_pipeline`'s very first step, drafted from bare free
    text with no artifact seed at all yet, and a capability whose primary
    input is itself "text" also fits there, because the user's typed
    sentence *is* that text artifact. `seed_text_ok` defaults False so
    `map_freeform_to_step`'s existing single-step behavior (where a `None`
    `prior_output_kind` means "only seed/loose capabilities fit") is
    unchanged.
    """
    if kind_in is None:
        return True
    if kind_in == target_kind:
        return True
    return seed_text_ok and target_kind is None and kind_in == "text"


def _pick_fallback_capability(
    prior_output_kind: Optional[str],
    live_caps: "list[Capability]",
    *,
    seed_text_ok: bool = False,
) -> Optional[Capability]:
    """Deterministically choose which live capability "wing it" targets.

    A capability is a candidate when it can plausibly follow a step that
    produced `prior_output_kind` (see `_kind_fits`). Mirrors
    `capability_discovery.discover_capabilities`'s own "what can follow this
    output_kind" rule.

    Among candidates, a text-primary-input one is preferred (the user's
    free text maps onto it most directly); otherwise the first candidate in
    the given order wins, so repeated calls are stable.
    """
    candidates = [
        c for c in live_caps
        if _kind_fits(c.kind_in, prior_output_kind, seed_text_ok=seed_text_ok)
    ]
    if not candidates:
        return None
    text_first = [c for c in candidates if c.kind_in == "text"]
    return text_first[0] if text_first else candidates[0]


def _result_for(cap: Capability, params: dict, via: str) -> WingitResult:
    if cap.source == "plugin":
        params = {**params, "plugin": cap.plugin}
    return WingitResult(
        class_type=cap.class_type, params=params, capability_id=cap.id, via=via,
    )


def _fallback(
    text: str, prior_output_kind: Optional[str], live_caps: "list[Capability]"
) -> Optional[WingitResult]:
    cap = _pick_fallback_capability(prior_output_kind, live_caps)
    if cap is None:
        return None
    key = _primary_text_param_key(cap)
    return _result_for(cap, {key: text}, via="fallback")


# ── LLM-answer parsing ─────────────────────────────────────────────────────────


def _parse_llm_answer(
    raw: str, by_id: "dict[str, Capability]"
) -> Optional[WingitResult]:
    obj = _extract_first_json_object(_strip_noise(raw))
    if obj is None:
        return None

    cap_id = obj.get("capability_id")
    if not isinstance(cap_id, str) or cap_id not in by_id:
        return None  # unknown/latent id -> caller falls back
    cap = by_id[cap_id]

    raw_params = obj.get("params")
    params: dict = {}
    if isinstance(raw_params, dict):
        for key, value in raw_params.items():
            if isinstance(key, str) and isinstance(value, _PLAUSIBLE_PARAM_TYPES):
                params[key] = value

    return _result_for(cap, params, via="llm")


def _parse_llm_pipeline(
    raw: str,
    by_id: "dict[str, Capability]",
    seed_output_kind: Optional[str],
    max_steps: int,
) -> "list[tuple[str, dict]]":
    """Parse an LLM's multi-step answer into a validated, chain-checked list
    of ``(class_type, params)`` steps for `map_freeform_to_pipeline`.

    Generalizes `_parse_llm_answer` from one JSON object to a JSON array of
    ``{"capability_id", "params"}`` objects (via `_extract_first_json_array`,
    tolerating the same fence/think-block noise). Steps are walked in order
    and validated against BOTH the live-capability set and the adjacent-kind
    chain (`_kind_fits`, with the "bare text seed" exception only on step 0);
    the first invalid step (unknown/latent id, wrong shape, or a kind that
    doesn't chain) truncates the list rather than failing the whole draft —
    an LLM that gets step 3 wrong shouldn't cost the user steps 1 and 2.
    Also caps the result at `max_steps`. Returns `[]` (never None) when
    nothing survives, so the caller's fallback path is a single clean check.
    """
    arr = _extract_first_json_array(_strip_noise(raw))
    if not arr:
        return []

    steps: "list[tuple[str, dict]]" = []
    prior_kind = seed_output_kind
    for idx, item in enumerate(arr):
        if len(steps) >= max_steps:
            break
        if not isinstance(item, dict):
            break

        cap_id = item.get("capability_id")
        if not isinstance(cap_id, str) or cap_id not in by_id:
            break  # unknown/latent id -> drop this step and everything after
        cap = by_id[cap_id]

        if not _kind_fits(cap.kind_in, prior_kind, seed_text_ok=(idx == 0)):
            break  # doesn't chain -> drop this step and everything after

        raw_params = item.get("params")
        params: dict = {}
        if isinstance(raw_params, dict):
            for key, value in raw_params.items():
                if isinstance(key, str) and isinstance(value, _PLAUSIBLE_PARAM_TYPES):
                    params[key] = value

        result = _result_for(cap, params, via="llm")
        steps.append((result.class_type, result.params))
        prior_kind = cap.kind_out

    return steps


# ── Public entry point ─────────────────────────────────────────────────────────


def map_freeform_to_step(
    text: str,
    prior_output_kind: Optional[str],
    capabilities: "list[Capability]",
    *,
    llm_fn: "Optional[Callable[[str], Optional[str]]]",
) -> Optional[WingitResult]:
    """Map a user's free-form *text* to a concrete pipeline step.

    Only `capabilities` with `.live == True` are ever offered to the LLM or
    considered by the fallback. Tries `llm_fn` first (when given); any
    failure mode there — `llm_fn` is None, it raises, its output doesn't
    parse, or it names a capability that isn't in the live set — falls
    through to the deterministic fallback rather than propagating an
    exception or returning nothing. Returns None only when NO live
    capability fits `prior_output_kind` at all — there is nothing sensible
    to map to regardless of what the LLM says.
    """
    live_caps = [c for c in capabilities if c.live]
    by_id = {c.id: c for c in live_caps}

    if llm_fn is not None and live_caps:
        try:
            raw = llm_fn(_build_prompt(text, live_caps))
        except Exception:
            raw = None
        if raw:
            result = _parse_llm_answer(raw, by_id)
            if result is not None:
                return result

    return _fallback(text, prior_output_kind, live_caps)


def map_freeform_to_pipeline(
    text: str,
    *,
    seed_output_kind: Optional[str],
    capabilities: "list[Capability]",
    llm_fn: "Optional[Callable[[str], Optional[str]]]",
    max_steps: int = 4,
) -> "Optional[list[tuple[str, dict]]]":
    """Draft an ordered ``[(class_type, params), ...]`` pipeline (<=
    `max_steps` long) from one free-form sentence, for the Muse wizard's
    "tell me your idea" box.

    The multi-step sibling of `map_freeform_to_step` — same shape of
    contract, generalized from "map to one step" to "draft a short chain of
    steps". Only `capabilities` with `.live == True` are ever offered to the
    LLM or considered by the fallback.

    Tries `llm_fn` first (when given), asking for a JSON array of
    ``{"capability_id", "params"}`` steps (`_build_pipeline_prompt`) and
    parsing the reply leniently (`_parse_llm_pipeline`): each step is
    validated against the live-capability set AND that its `kind_in` chains
    from the previous step's `kind_out` (step 0 chains from
    `seed_output_kind`, with a "bare text seed" allowance — see
    `_kind_fits`). Invalid TRAILING steps are dropped rather than failing
    the whole draft, and the result is capped at `max_steps`.

    Falls back to a single deterministic step (mirroring `_fallback`: the
    best live capability that can consume `seed_output_kind` -- or a
    source/seed capability, or a bare-text-consuming one, when it's None --
    with `text` filled into its primary text param) whenever `llm_fn` is
    None, raises, returns something unparseable, or yields no valid steps at
    all. Returns None only when NO live capability fits `seed_output_kind`
    at all -- there is nothing sensible to draft regardless of what the LLM
    says.
    """
    live_caps = [c for c in capabilities if c.live]
    by_id = {c.id: c for c in live_caps}

    if llm_fn is not None and live_caps:
        try:
            raw = llm_fn(
                _build_pipeline_prompt(text, live_caps, seed_output_kind, max_steps)
            )
        except Exception:
            raw = None
        if raw:
            steps = _parse_llm_pipeline(raw, by_id, seed_output_kind, max_steps)
            if steps:
                return steps

    cap = _pick_fallback_capability(seed_output_kind, live_caps, seed_text_ok=True)
    if cap is None:
        return None
    key = _primary_text_param_key(cap)
    result = _result_for(cap, {key: text}, via="fallback")
    return [(result.class_type, result.params)]


# ── Real-deps wrapper ──────────────────────────────────────────────────────────


def default_llm_fn(prompt: str) -> Optional[str]:
    """The real `llm_fn` the composer UI passes to `map_freeform_to_step`.

    Thin by design — resolves whichever chat-capable model is actually
    reachable right now (`artgen.detect_artgen_endpoint`, the same function
    the artgen panel's health dot and MCP server call, so "wing it" can never
    disagree with what generation requests actually use) and sends the
    prompt to it (`artgen.call_llm`). Collapses every failure mode (no
    endpoint discovered, network error, malformed response) to None so the
    pure core's fallback path takes over exactly as it would in tests.
    """
    try:
        import artgen

        base_url, model = artgen.detect_artgen_endpoint()
        if not base_url:
            return None
        # Wing-it only ever needs a tiny JSON reply (one capability_id + a
        # handful of params) — `call_llm`'s 600s default is sized for full
        # artifact generation and would let a stalled/slow chat server hang
        # the compose worker for up to 10 minutes. A short timeout here means
        # a bad server fails fast into the deterministic fallback instead.
        text, _usage = artgen.call_llm(
            prompt, model, base_url=base_url, max_tokens=512, timeout=45,
        )
        return text
    except Exception:
        return None
