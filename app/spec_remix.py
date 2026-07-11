#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Pure spec-remix helper: expose a spec's editable params and derive a modified
spec file from a user's edits.

SP-C Phase 2a Task 1. The pipeline engine (``app/pipeline_engine.py``) runs a
ComfyUI-API-v1 spec FILE — there is no "override" argument. So "remixing" a run
means DERIVING a brand-new spec file from a base spec + a dict of per-node
param edits, then handing that derived file's path to the engine exactly like
any other spec. This module has zero GTK/engine-execution dependencies — it
only reads/writes JSON — so it is usable from the CLI, tests, and (in a later
task) the GTK remix UI without pulling in any of those.

Two entry points:

``editable_params(spec)``
    For every node, list the inputs a human could sensibly edit: scalar
    literals only. A wired input (``["node_id", "output_key"]`` — see
    ``pipeline_engine._is_wire``) is excluded because its value is computed by
    another node at run time, not something a user can type over. Structural/
    metadata keys (leading ``_``) are excluded too.

``derive_spec(base_spec_path, edits, dest_dir)``
    Load the RAW base spec (raw ``json.loads`` of the file — deliberately NOT
    ``pipeline_engine.load_spec``, which strips ``_``-prefixed metadata keys;
    a remix must preserve those verbatim), apply *edits* only to inputs that
    already exist and are not wires, and write the result to a fresh path
    under *dest_dir*. The base file is never touched — a new dict is built
    entirely from data read into memory, and only the new path is written.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline_engine import _is_wire


@dataclass
class ParamField:
    """One editable input on one node, ready for a UI to render + edit.

    kind is one of "text" | "number" | "choice" | "bool". This module only
    ever infers "text" / "number" / "bool" from the Python type of the base
    spec's literal value — "choice" is reserved for a future enum-aware caller
    (e.g. a UI that knows a given key's value should render as a dropdown) and
    is never produced here.
    """
    node_id: str
    key: str
    label: str
    kind: str
    value: Any


def _label_for(key: str) -> str:
    """Plain-language label for an input key: underscores -> spaces, first
    letter capitalized, rest lowercased (matches "num_frames" -> "Num frames",
    "negative_prompt" -> "Negative prompt", "prompt" -> "Prompt")."""
    return key.replace("_", " ").capitalize()


def _kind_for(value: Any) -> "str | None":
    """Infer a ParamField ``kind`` from a literal's Python type.

    bool is checked before int/float because ``bool`` is an ``int`` subclass
    in Python — checking int first would misclassify every bool as "number".
    Returns None for anything that isn't a plain scalar (list/dict/None/etc.),
    signalling to the caller that this value isn't editable-as-a-simple-field.
    """
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    return None


def editable_params(spec: dict) -> "dict[str, list[ParamField]]":
    """Return, per node id, the list of ParamFields a user could edit.

    *spec* is expected in the same shape ``pipeline_engine.load_spec``
    produces (or the raw file — top-level ``_``-prefixed keys and any entry
    that isn't a ``{"class_type": ..., "inputs": {...}}`` node dict are
    skipped defensively either way).
    """
    result: "dict[str, list[ParamField]]" = {}
    for node_id, node in spec.items():
        if node_id.startswith("_") or not isinstance(node, dict):
            continue
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue

        fields: "list[ParamField]" = []
        for key, value in inputs.items():
            if key.startswith("_"):
                continue  # structural/metadata key, not a real input
            if _is_wire(value):
                continue  # computed by another node — not user-editable
            kind = _kind_for(value)
            if kind is None:
                continue  # not a plain scalar literal (list/dict/None/...)
            fields.append(ParamField(
                node_id=node_id, key=key, label=_label_for(key),
                kind=kind, value=value,
            ))
        result[node_id] = fields
    return result


def derive_spec(base_spec_path: str, edits: "dict[str, dict[str, Any]]",
                dest_dir: str) -> str:
    """Apply *edits* over the base spec and write the result to a new file.

    *edits* is ``{node_id: {input_key: new_value}}``. An edit is applied only
    when the target node exists, has an ``inputs`` dict, the key already
    exists in that dict, AND the existing value is not a wire — any other
    edit (unknown node id, unknown key, or a wired input) is silently
    ignored rather than raising, so a stale/partial edits dict from a UI
    never crashes a remix.

    Returns the path written, under
    ``dest_dir/remix_<base_stem>_<n>.json`` where *n* is chosen by counting
    pre-existing ``remix_<base_stem>_*.json`` files in *dest_dir* (no
    date/random component, per the SP-C Phase 2a spec) and bumped further if
    that candidate somehow already exists.
    """
    # Raw json.loads (NOT pipeline_engine.load_spec) so `_`-prefixed metadata
    # keys and every node's wires survive untouched in the derived file.
    raw = json.loads(Path(base_spec_path).read_text())

    for node_id, node_edits in edits.items():
        node = raw.get(node_id)
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key, new_value in node_edits.items():
            if key not in inputs:
                continue  # unknown key — ignore
            if _is_wire(inputs[key]):
                continue  # never overwrite a wire
            inputs[key] = new_value

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    base_stem = Path(base_spec_path).stem
    existing = list(dest.glob(f"remix_{base_stem}_*.json"))
    n = len(existing) + 1
    out_path = dest / f"remix_{base_stem}_{n}.json"
    while out_path.exists():  # belt-and-suspenders against a gap in the count
        n += 1
        out_path = dest / f"remix_{base_stem}_{n}.json"

    out_path.write_text(json.dumps(raw, indent=2))
    return str(out_path)
