"""field_roles.py — the shared field-role vocabulary for the Create surface
and (later) pipeline field configuration.

Two orthogonal axes describe every configurable field:
  ROLE_*  — which zone it belongs to (brief / direction / control)
  MARK_*  — how its value is used (words / interpreted / exact)

This module is pure (no GTK) so both create_param_panels and pipeline field
editors can classify fields identically — a field means the same thing
everywhere.
"""
from __future__ import annotations
from dataclasses import dataclass

ROLE_BRIEF = "brief"
ROLE_DIRECTION = "direction"
ROLE_CONTROL = "control"

MARK_WORDS = "words"
MARK_INTERPRETED = "interpreted"
MARK_EXACT = "exact"

MARKER_GLYPH = {MARK_WORDS: "✎", MARK_INTERPRETED: "✨", MARK_EXACT: "⚙"}
MARKER_TIP = {
    MARK_WORDS: "Your words — the model turns this into art.",
    MARK_INTERPRETED: "The model chooses based on this value.",
    MARK_EXACT: "Exact setting — the model never reads it.",
}


@dataclass(frozen=True)
class FieldRole:
    role: str
    marker: str


# Raw creative text the model renders.
_NATIVE_BRIEF = {"prompt", "negative_prompt", "avoid", "theme"}
# Deterministic knobs the model never interprets.
_NATIVE_CONTROL = {
    "num_inference_steps", "steps", "seed", "guidance_scale",
    "num_frames", "size", "resolution",
}
# Values that hand the choice to the model/generator.
_INTERPRETED_VALUES = {None, "random", "auto"}


def classify_native(field_key: str) -> FieldRole:
    if field_key in _NATIVE_BRIEF:
        return FieldRole(ROLE_BRIEF, MARK_WORDS)
    if field_key in _NATIVE_CONTROL:
        return FieldRole(ROLE_CONTROL, MARK_EXACT)
    # Safest default: treat an unknown key as an exact control, so it is never
    # mistaken for creative input.
    return FieldRole(ROLE_CONTROL, MARK_EXACT)


def classify_artgen(spec) -> FieldRole:
    dest = getattr(spec, "dest", "")
    kind = getattr(spec, "kind", "str")
    default = getattr(spec, "default", None)
    if dest in {"subject", "text", "prompt", "theme", "board_name", "tagline"}:
        return FieldRole(ROLE_BRIEF, MARK_WORDS)
    if kind in ("int", "float"):
        return FieldRole(ROLE_CONTROL, MARK_EXACT)
    if kind == "bool":
        return FieldRole(ROLE_DIRECTION, MARK_EXACT)
    # choice / str: does the default hand the decision to the model?
    if default in _INTERPRETED_VALUES:
        return FieldRole(ROLE_DIRECTION, MARK_INTERPRETED)
    return FieldRole(ROLE_DIRECTION, MARK_EXACT)


_PIPELINE_BRIEF_KEYS = frozenset({
    "prompt", "text", "negative_prompt", "subject",
    "theme", "caption", "description", "lore",
})


def classify_pipeline_field(kind: str, default=None, key: str = "") -> FieldRole:
    """Classify one editable pipeline ParamField (kind/value/key)."""
    if key in _PIPELINE_BRIEF_KEYS or kind == "prompt":
        return FieldRole(ROLE_BRIEF, MARK_WORDS)
    if kind == "number":
        return FieldRole(ROLE_CONTROL, MARK_EXACT)
    if kind == "bool":
        return FieldRole(ROLE_DIRECTION, MARK_EXACT)
    if kind in ("text", "choice"):
        if default in _INTERPRETED_VALUES:
            return FieldRole(ROLE_DIRECTION, MARK_INTERPRETED)
        return FieldRole(ROLE_DIRECTION, MARK_EXACT)
    return FieldRole(ROLE_CONTROL, MARK_EXACT)


def marker_prefix(marker: str) -> str:
    """Glyph + trailing space for a marker, or "" for an unknown marker.

    A pure formatter so RoleZonePanel (Create) and RemixView (pipeline)
    decorate field labels identically without importing each other.
    """
    glyph = MARKER_GLYPH.get(marker)
    return f"{glyph} " if glyph else ""
