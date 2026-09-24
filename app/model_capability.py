"""model_capability.py — a pure (no GTK, no network) heuristic for scaling
prompt strategy to what a serving LLM can actually follow.

Parses a rough parameter-count tier from a model id, downgraded when the id
(or a manually-characterized override) indicates heavy quantization or
speculative decoding — both observed live to degrade structured/repetitive
generation tasks (e.g. ANSI art) far more than raw model size alone would
predict. See docs/superpowers/specs/
2026-09-21-artgen-model-capability-tiering-design.md for the investigation.
"""
from __future__ import annotations

import re
from typing import Literal

Tier = Literal["small", "medium", "large"]

_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[Bb](?![a-zA-Z])")

_CONSTRAINED_HINTS = (
    "q4", "q8", "int4", "int8", "awq", "gptq", "fp8",
    "dflash", "speculative", "draft",
)

# Model ids we've manually characterized as behaving like a lower tier than
# their size alone would suggest. The served /v1/models id often carries no
# quantization/speculative-decoding signal at all — that information can
# live only in the serving container's own metadata (e.g. a tt-model-manager
# docker label), which this module never reads. Add entries here as they're
# found; see the design doc's Section 1 for the Qwen/Qwen3.8-27B case
# (served 2026-09-21 via profile qwen3.8-27b-dflash2-vision-p300x2-q4kv —
# q4kv quantization + dflash2 speculative decoding, invisible in the id).
_KNOWN_CONSTRAINED_IDS = frozenset({
    "Qwen/Qwen3.8-27B",
})

# Lowercased once at module load so the override list matches regardless of
# how the serving container happens to case its /v1/models id — the served
# id's casing is not something this module controls, and a case mismatch
# would silently stop catching the very model this list exists for.
_KNOWN_CONSTRAINED_IDS_LOWER = frozenset(s.lower() for s in _KNOWN_CONSTRAINED_IDS)

_TIER_ORDER: tuple[Tier, ...] = ("small", "medium", "large")


def parse_model_scale_b(model_id: str) -> float | None:
    """Extract a <number>B parameter-count token from a model id.

    Handles "Qwen3-32B", "Qwen/Qwen3.8-27B", "Llama-3.3-70B-Instruct",
    "Qwen3-0.6B". Returns None if nothing matches.
    """
    match = _SIZE_RE.search(model_id)
    if not match:
        return None
    return float(match.group(1))


def has_constrained_hint(model_id: str) -> bool:
    """True if the model id itself self-reports quantization or
    speculative decoding (e.g. a community repo with -GPTQ/-AWQ in its
    name). Does NOT see serving-container-only metadata — see
    _KNOWN_CONSTRAINED_IDS for that gap."""
    lowered = model_id.lower()
    return any(hint in lowered for hint in _CONSTRAINED_HINTS)


def _base_tier(scale_b: float | None) -> Tier:
    if scale_b is None:
        return "large"
    if scale_b >= 30:
        return "large"
    if scale_b >= 8:
        return "medium"
    return "small"


def _downgrade(tier: Tier) -> Tier:
    idx = _TIER_ORDER.index(tier)
    return _TIER_ORDER[max(0, idx - 1)]


def model_tier(model_id: str) -> Tier:
    """Capability tier for the given served model id: "small", "medium", or
    "large". Never raises — an unparseable id defaults to "large" (assume
    capable; never punish a model we haven't characterized), and a
    non-string input (e.g. None, from a caller that couldn't resolve an id)
    is treated the same as unparseable rather than raising."""
    if not isinstance(model_id, str):
        return "large"
    tier = _base_tier(parse_model_scale_b(model_id))
    if has_constrained_hint(model_id) or model_id.lower() in _KNOWN_CONSTRAINED_IDS_LOWER:
        tier = _downgrade(tier)
    return tier
