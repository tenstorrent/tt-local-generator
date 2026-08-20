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

Phase 2b-1 (Task 2) adds pure GRAPH SURGERY on top of the param-edit layer
above — the composer's "add a step" / "remove a step" operations:

``add_step(spec, after_node_id, class_type, params=None)``
    Append a brand-new node (fresh id) after an existing one, wiring its
    canonical input (``intent_vocab.intent_for(class_type).input_key``) to
    the after-node's primary output. Raises ``ValueError`` if the new node's
    ``input_kind`` doesn't match what the after-node produces — composer UI
    is expected to only ever offer kind-compatible choices (see
    ``intent_vocab.compatible_intents``), so this is a defensive guard, not
    the primary UX gate.

``remove_step(spec, node_id)``
    Delete a node and splice its consumers back onto whatever it was wired
    to, so the graph never dangles. See that function's docstring for the
    exact (and, in the branching/nested case, deliberately chosen) rewiring
    rule.

Both operations — and ``write_spec`` below — validate the result via
``pipeline_engine.topo_order`` before returning/writing (acyclic + every wire
resolves to a node that still exists) and raise ``ValueError`` rather than
ever handing back a broken spec.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from intent_vocab import intent_for
from pipeline_engine import _is_wire, topo_order


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


def apply_edits(spec: dict, edits: "dict[str, dict[str, Any]]") -> dict:
    """Apply *edits* over *spec* and return a NEW dict — *spec* is never mutated.

    *edits* is ``{node_id: {input_key: new_value}}``. An edit is applied only
    when the target node exists, has an ``inputs`` dict, the key already
    exists in that dict, AND the existing value is not a wire — any other
    edit (unknown node id, unknown key, or a wired input) is silently
    ignored rather than raising, so a stale/partial edits dict from a UI
    never crashes a remix.

    Shared by ``derive_spec`` (file-based remix: base spec read fresh off
    disk) and the composer's ``RemixView.current_spec()`` (Phase 2b-1 Task 3:
    edits applied over an in-memory working spec dict that may already carry
    structural add/remove changes) — one edit-application rule for both.
    """
    new_spec = copy.deepcopy(spec)
    for node_id, node_edits in edits.items():
        node = new_spec.get(node_id)
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key, new_value in node_edits.items():
            if key not in inputs:
                # A "model" edit is a legitimate INSERT: the per-step model
                # picker owns a node's backend selection, and Muse-built specs
                # carry no literal "model" key to pre-seed it — so requiring the
                # key to pre-exist silently dropped every picker choice on a
                # freshly-composed pipeline (whole-branch review C1). Every OTHER
                # unknown key is still ignored (guards stale/typo'd UI edits).
                if key == "model":
                    inputs[key] = new_value
                continue  # unknown key — ignore
            if _is_wire(inputs[key]):
                continue  # never overwrite a wire
            inputs[key] = new_value
    return new_spec


def derive_spec(base_spec_path: str, edits: "dict[str, dict[str, Any]]",
                dest_dir: str) -> str:
    """Apply *edits* over the base spec and write the result to a new file.

    Returns the path written, under
    ``dest_dir/remix_<base_stem>_<n>.json`` where *n* is chosen by counting
    pre-existing ``remix_<base_stem>_*.json`` files in *dest_dir* (no
    date/random component, per the SP-C Phase 2a spec) and bumped further if
    that candidate somehow already exists. The actual write is delegated to
    ``write_spec`` (Phase 2b-1 Task 2) so both entry points share one
    collision-safe naming/validation implementation.
    """
    # Raw json.loads (NOT pipeline_engine.load_spec) so `_`-prefixed metadata
    # keys and every node's wires survive untouched in the derived file.
    raw = json.loads(Path(base_spec_path).read_text())
    new_spec = apply_edits(raw, edits)
    return write_spec(new_spec, Path(base_spec_path).stem, dest_dir)


# ── Structural graph surgery (Phase 2b-1 Task 2) ─────────────────────────────
#
# A spec is ``{node_id: {"class_type": str, "inputs": {key: literal | wire}}}``
# plus optional ``_``-prefixed metadata, where a "wire" is a 2-element
# ``[src_node_id, output_key]`` list (``pipeline_engine._is_wire``). These two
# functions are the composer's "add a step" / "remove a step" primitives: pure
# functions that deep-copy their input, never mutate it, and validate the
# result (acyclic, every wire resolves) before returning.

# Sentinel returned by `_rewire_wires` for a nested wire that matched the
# removed node with no upstream to reconnect to: it tells the caller to drop
# that specific entry from its containing list/dict entirely, rather than
# leaving a dangling reference. See `remove_step`'s docstring for the
# rationale (this is the "genuinely ambiguous" edge case called out in the
# task brief — the chosen behavior is documented there).
_DROP = object()


def _rewire_wires(value: Any, node_id: str, up: "list | None") -> Any:
    """Recursively replace every wire referencing *node_id* within *value*.

    Mirrors the traversal order of ``pipeline_engine._wire_deps`` /
    ``_resolve_value`` (wire-check BEFORE the generic list branch, since a
    wire is itself a 2-element list) so nested wires inside list/dict-shaped
    inputs — e.g. TTLGAddToPlaylist's ``artifacts`` (list of dicts) and
    ``metadata`` (dict) — are rewired exactly like top-level ones instead of
    being silently left dangling.

    A wire matching *node_id* becomes *up* when *up* is not None (splicing the
    consumer straight onto node_id's own upstream source); otherwise it
    becomes the ``_DROP`` sentinel, telling the caller to remove that
    particular entry from its container. Values with no matching wire pass
    through unchanged (and unmodified — this never mutates *value*).
    """
    if _is_wire(value):
        if value[0] == node_id:
            return up if up is not None else _DROP
        return value
    if isinstance(value, list):
        out = []
        for item in value:
            rewired = _rewire_wires(item, node_id, up)
            if rewired is not _DROP:
                out.append(rewired)
        return out
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            rewired = _rewire_wires(v, node_id, up)
            if rewired is not _DROP:
                out[k] = rewired
        return out
    return value


def _validate(spec: dict) -> None:
    """Raise ``ValueError`` unless *spec* is an acyclic graph with every wire
    resolving to a node that exists.

    Delegates entirely to ``pipeline_engine.topo_order``, which already
    raises ``ValueError`` for both a cycle and a wire pointing at a missing
    node id — this just narrows *spec* to the node-only view topo_order
    expects (dropping ``_``-prefixed top-level metadata), matching
    ``pipeline_engine.load_spec``'s own filter.
    """
    nodes_only = {k: v for k, v in spec.items()
                 if not k.startswith("_") and isinstance(v, dict)
                 and "class_type" in v}
    topo_order(nodes_only)


def add_step(spec: dict, after_node_id: str, class_type: str,
            params: "dict[str, Any] | None" = None) -> dict:
    """Return a NEW spec with a fresh node of *class_type* appended after
    *after_node_id*, wired to its primary output.

    *spec* is never mutated — a deep copy is built and returned.

    The new node's id is minted as ``str(max(existing numeric ids) + 1)`` (so
    it never collides) and the node is simply added under that new key — it
    is NOT inserted positionally relative to *after_node_id*. That's fine:
    execution order is derived from wires via ``pipeline_engine.topo_order``,
    never from key order.

    Wiring: ``intent_vocab.intent_for(class_type)`` supplies ``input_key``
    (the payload key an upstream artifact wires into) and ``input_kind`` (the
    artifact kind it expects). When both are set, this REQUIRES
    ``input_kind`` to match ``after_node_id``'s primary ``output_kind``
    (also via ``intent_for``) — mismatched kinds raise ``ValueError`` rather
    than silently wiring an incompatible pair (e.g. wiring a text-only input
    to an image producer). When it matches (or when the new intent has no
    canonical input at all, e.g. TTLGAddToPlaylist/TTLGArtgenGenerate), the
    new node's ``inputs[input_key]`` is set to
    ``[after_node_id, after_node's primary output key]`` — the primary output
    being ``intent_for(after_node_class_type).outputs[0]``.

    *params*, if given, is a dict of literal inputs (e.g. ``{"steps": 8}``)
    merged onto the new node's inputs after the wire — a caller-supplied
    param with the same key as the canonical input would win, though no
    built-in intent's input_key collides with a typical literal param name.

    Raises ``ValueError`` if *after_node_id* doesn't exist, if the new node
    has a canonical input but the after-node has no primary output to offer,
    if the intents are kind-incompatible, or if the resulting spec fails
    validation (should not happen given the checks above, but guards against
    a caller passing an already-broken *spec*).
    """
    new_spec = copy.deepcopy(spec)
    if not isinstance(new_spec.get(after_node_id), dict):
        raise ValueError(f"after_node_id {after_node_id!r} not found in spec")

    after_node = new_spec[after_node_id]
    intent = intent_for(class_type)

    new_inputs: "dict[str, Any]" = {}
    if intent.input_key:
        after_intent = intent_for(after_node.get("class_type", ""))
        if intent.input_kind and intent.input_kind != after_intent.output_kind:
            raise ValueError(
                f"cannot add {class_type!r} (needs a {intent.input_kind!r} "
                f"input) after node {after_node_id!r} "
                f"({after_node.get('class_type')!r} produces "
                f"{after_intent.output_kind!r})"
            )
        if not after_intent.outputs:
            raise ValueError(
                f"node {after_node_id!r} ({after_node.get('class_type')!r}) "
                "has no primary output to wire the new step from"
            )
        new_inputs[intent.input_key] = [after_node_id, after_intent.outputs[0]]

    if params:
        new_inputs.update(params)

    numbered_ids = [int(k) for k in new_spec
                    if not k.startswith("_") and k.isdigit()]
    new_id = str(max(numbered_ids) + 1) if numbered_ids else "1"

    new_spec[new_id] = {"class_type": class_type, "inputs": new_inputs}

    _validate(new_spec)
    return new_spec


def seed_spec(steps: "list[tuple[str, dict]]", *,
              seed_artifact: "tuple[str, str] | None" = None) -> dict:
    """Build a fresh ComfyUI-API-v1 spec from an ordered list of
    (class_type, params) steps, wiring each step's primary output into the
    next step's canonical input (same kind rules as ``add_step``).

    Node ids are minted ``"1"``, ``"2"``, ``"3"``, ... in list order. Step
    i+1's ``intent.input_key`` is wired to ``[str(i+1), prev_intent.outputs[0]]``
    when that intent declares a canonical input; caller-supplied *params* are
    merged on top, EXCEPT a param on the canonical input_key does NOT overwrite
    a wire/seed already placed there (the wire/seed wins, so seeded content is
    never silently discarded — see whole-branch review C2). A param still
    provides the canonical value in blank-canvas mode where no wire/seed exists.

    ``seed_artifact=(path, kind)``: the FIRST step consumes it as starting
    material. If step 0's intent has a canonical input whose ``input_kind``
    matches *kind*, the literal *path* is placed on that ``input_key`` — a
    literal file-path string, exactly what a wire from an upstream ``*_path``
    output would deliver, so the engine resolves it unchanged. Raises
    ``ValueError`` if *kind* is incompatible with step 0's ``input_kind``.

    *steps* is never mutated; a brand-new spec dict is built from scratch.

    Raises ``ValueError`` on an empty *steps* list, a kind mismatch between
    adjacent steps (or between *seed_artifact* and step 0), or a resulting
    spec that fails ``_validate`` (should not happen given the checks above,
    but guards against an unexpected intent_vocab configuration).
    """
    if not steps:
        raise ValueError("seed_spec requires at least one step")

    spec: "dict[str, Any]" = {}
    prev_id = None
    prev_intent = None
    for idx, (class_type, params) in enumerate(steps):
        node_id = str(idx + 1)
        intent = intent_for(class_type)
        inputs: "dict[str, Any]" = {}

        if intent.input_key and prev_id is not None:
            if intent.input_kind and prev_intent is not None \
                    and intent.input_kind != prev_intent.output_kind:
                raise ValueError(
                    f"cannot follow {prev_intent.output_kind!r} with "
                    f"{class_type!r} (needs {intent.input_kind!r})"
                )
            if prev_intent is not None and prev_intent.outputs:
                inputs[intent.input_key] = [prev_id, prev_intent.outputs[0]]

        if idx == 0 and seed_artifact is not None:
            path, kind = seed_artifact
            if intent.input_key and intent.input_kind and intent.input_kind != kind:
                raise ValueError(
                    f"seed artifact kind {kind!r} incompatible with "
                    f"{class_type!r} (needs {intent.input_kind!r})"
                )
            if intent.input_key:
                inputs[intent.input_key] = path

        # A wire (from the previous step) or a seed literal may already occupy
        # the canonical input_key. A caller param on that SAME key must NOT
        # clobber it — doing so silently discards the seed/prepended content
        # (whole-branch review C2: the "looping-animation" goal's placeholder
        # "prompt" literal was overwriting the palette/text seed wire, so every
        # seeded run produced the generic default). Params still apply freely to
        # every other key, and still provide the canonical value in blank-canvas
        # mode (no wire/seed → the key is absent here → param fills it).
        wired_key = intent.input_key if intent.input_key in inputs else None
        if params:
            for k, v in params.items():
                if k == wired_key:
                    continue  # wire/seed wins over a literal on the wired input
                inputs[k] = v

        spec[node_id] = {"class_type": class_type, "inputs": inputs}
        prev_id, prev_intent = node_id, intent

    _validate(spec)
    return spec


def remove_step(spec: dict, node_id: str) -> dict:
    """Return a NEW spec with *node_id* removed and every consumer wire that
    pointed at it spliced onto *node_id*'s own upstream source (or dropped).

    *spec* is never mutated — a deep copy is built and returned.

    Rewiring rule: *node_id*'s own primary input — the wire (if any) on
    ``intent_vocab.intent_for(node_id's class_type).input_key`` — is captured
    as ``up`` BEFORE the node is deleted. Then, for every remaining node's
    every input that wires to ``[node_id, some_output_key]`` (at any nesting
    depth — see ``_rewire_wires``), that reference becomes ``up`` if ``up``
    exists, or is removed entirely (the whole dict key, or the specific
    list/dict entry when nested) if it doesn't. This guarantees the result
    never contains a wire pointing at the removed node.

    Canonical-vs-nested distinction (the "genuinely ambiguous" case called
    out in the task brief): a consumer's CANONICAL input — the key equal to
    ``intent_for(consumer's class_type).input_key``, when that intent also
    declares an ``input_kind`` — is where we actually know what KIND of
    artifact the consumer expects, so it's rewired KIND-AWARE: it is spliced
    onto ``up`` only when ``up`` delivers the SAME kind the consumer wants
    (``intent_for(up's source node's class_type).output_kind ==
    consumer_intent.input_kind``). If ``up`` is ``None`` (nothing to
    reconnect to) or the kind doesn't match (e.g. removing an image->caption
    node whose caption fed a text prompt elsewhere would otherwise leave that
    consumer wired to an image path instead of text), the canonical key is
    DROPPED entirely rather than wired to something semantically wrong — the
    consumer falls back to its own literal/default value.

    Every OTHER wire referencing *node_id* — a non-canonical top-level key,
    or a wire nested inside a list/dict-shaped input (e.g.
    TTLGAddToPlaylist's ``artifacts``/``metadata``) — keeps the prior, purely
    STRUCTURAL behavior: it is spliced onto ``up`` (or dropped if ``up`` is
    ``None``) with no kind check, because intent_vocab has no per-key kind
    information for those positions to check against. The composer UI is
    expected to warn/preview before removal in a case like this; this
    function only guarantees structural validity (acyclic, no dangling wire)
    for the nested/non-canonical case, not semantic validity — canonical
    inputs get the stronger, semantically-correct guarantee described above.

    Raises ``ValueError`` if *node_id* doesn't exist, or if the resulting
    spec fails validation (should not happen given the rewiring above, but
    guards against a caller passing an already-broken *spec*).
    """
    new_spec = copy.deepcopy(spec)
    if not isinstance(new_spec.get(node_id), dict):
        raise ValueError(f"node_id {node_id!r} not found in spec")

    node = new_spec[node_id]
    intent = intent_for(node.get("class_type", ""))
    up = None
    if intent.input_key:
        candidate = node.get("inputs", {}).get(intent.input_key)
        if _is_wire(candidate):
            up = candidate

    # The KIND `up` would deliver if spliced in — the output_kind of up's
    # OWN source node. Looked up now, before node_id is deleted, while
    # new_spec still holds every other node untouched (up's source node is
    # never node_id itself — it's node_id's own upstream). None when there's
    # no upstream at all, or (defensively) if up points at a node that's
    # somehow missing from the spec.
    up_kind = None
    if up is not None:
        up_src_node = new_spec.get(up[0])
        if isinstance(up_src_node, dict):
            up_kind = intent_for(up_src_node.get("class_type", "")).output_kind

    del new_spec[node_id]

    for nid, n in new_spec.items():
        if nid.startswith("_") or not isinstance(n, dict):
            continue
        inputs = n.get("inputs")
        if not isinstance(inputs, dict):
            continue
        consumer_intent = intent_for(n.get("class_type", ""))
        new_inputs: "dict[str, Any]" = {}
        for key, value in inputs.items():
            is_canonical_ref = (
                key == consumer_intent.input_key
                and consumer_intent.input_kind is not None
                and _is_wire(value)
                and value[0] == node_id
            )
            if is_canonical_ref:
                if up is not None and up_kind == consumer_intent.input_kind:
                    new_inputs[key] = up
                # else: kind mismatch (or no upstream at all) — drop the
                # canonical key entirely rather than wire something wrong.
                continue
            rewired = _rewire_wires(value, node_id, up)
            if rewired is not _DROP:
                new_inputs[key] = rewired
        n["inputs"] = new_inputs

    _validate(new_spec)
    return new_spec


def write_spec(spec: dict, base_name: str, dest_dir: str) -> str:
    """Validate and write *spec* to ``dest_dir/remix_<base_name>_<n>.json``.

    *n* is chosen by counting pre-existing ``remix_<base_name>_*.json`` files
    in *dest_dir* (no date/random component) and bumped further if that
    candidate somehow already exists (belt-and-suspenders against a gap in
    the count, e.g. a file deleted out of sequence). *dest_dir* is created if
    missing. Raises ``ValueError`` (via ``_validate``) rather than writing an
    invalid spec — a cycle or a wire pointing at a node that doesn't exist.

    Shared by ``derive_spec`` (param edits) and the composer's
    ``add_step``/``remove_step`` callers so there is exactly one collision-
    safe naming scheme for every kind of remix.
    """
    _validate(spec)

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    existing = list(dest.glob(f"remix_{base_name}_*.json"))
    n = len(existing) + 1
    out_path = dest / f"remix_{base_name}_{n}.json"
    while out_path.exists():  # belt-and-suspenders against a gap in the count
        n += 1
        out_path = dest / f"remix_{base_name}_{n}.json"

    out_path.write_text(json.dumps(spec, indent=2))
    return str(out_path)
