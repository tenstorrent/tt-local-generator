"""Tests for spec_remix — pure helper: editable_params() + derive_spec().

Fixture: tests/fixtures/remix_fixture_spec.json — 3 nodes:
  "1" TTLGTextToImage — text (prompt), number (steps), text (negative_prompt)
      literals + a structural "_hint" key that must be excluded.
  "2" TTLGImageToVideo — a WIRED input (image_path -> ["1","image_path"]) that
      must be excluded, plus a number literal (num_frames) that must be kept.
  "3" TTLGAnimateDiff — a bool literal (loop).
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import pipeline_engine
import spec_remix

_FIX = Path(__file__).parent / "fixtures" / "remix_fixture_spec.json"


def _raw_fixture() -> dict:
    return json.loads(_FIX.read_text())


# ── editable_params ──────────────────────────────────────────────────────────

def test_editable_params_returns_scalar_literals_with_labels():
    spec = _raw_fixture()
    params = spec_remix.editable_params(spec)

    by_key = {f.key: f for f in params["1"]}
    assert set(by_key) == {"prompt", "steps", "negative_prompt"}

    assert by_key["prompt"].kind == "text"
    assert by_key["prompt"].value == "a test prompt"
    assert by_key["prompt"].label == "Prompt"
    assert by_key["prompt"].node_id == "1"

    assert by_key["steps"].kind == "number"
    assert by_key["steps"].value == 4
    assert by_key["steps"].label == "Steps"

    assert by_key["negative_prompt"].kind == "text"
    assert by_key["negative_prompt"].label == "Negative prompt"


def test_editable_params_excludes_wired_input():
    spec = _raw_fixture()
    params = spec_remix.editable_params(spec)

    keys = {f.key for f in params["2"]}
    assert "image_path" not in keys          # wired -> not editable
    assert "num_frames" in keys              # scalar literal -> editable

    num_frames = next(f for f in params["2"] if f.key == "num_frames")
    assert num_frames.kind == "number"
    assert num_frames.value == 33
    assert num_frames.label == "Num frames"


def test_editable_params_excludes_structural_underscore_keys():
    spec = _raw_fixture()
    params = spec_remix.editable_params(spec)
    keys = {f.key for f in params["1"]}
    assert "_hint" not in keys


def test_editable_params_handles_bool_kind():
    spec = _raw_fixture()
    params = spec_remix.editable_params(spec)
    by_key = {f.key: f for f in params["3"]}
    assert by_key["loop"].kind == "bool"
    assert by_key["loop"].value is True
    assert by_key["loop"].label == "Loop"


# ── derive_spec ───────────────────────────────────────────────────────────────

def test_derive_spec_applies_edits_and_returns_new_path(tmp_path):
    dest_dir = tmp_path / "remixes"
    new_path = spec_remix.derive_spec(
        str(_FIX), {"1": {"prompt": "new prompt", "steps": 8}}, str(dest_dir)
    )

    assert new_path != str(_FIX)
    assert Path(new_path).exists()
    assert Path(new_path).parent == dest_dir

    # filename pattern: remix_<base_stem>_<n>.json
    assert re.match(r"remix_remix_fixture_spec_\d+\.json$", Path(new_path).name)


def test_derive_spec_does_not_mutate_base_file(tmp_path):
    before = _FIX.read_text()
    spec_remix.derive_spec(
        str(_FIX), {"1": {"prompt": "mutated?", "steps": 999}}, str(tmp_path / "out")
    )
    after = _FIX.read_text()
    assert before == after

    raw = _raw_fixture()
    assert raw["1"]["inputs"]["prompt"] == "a test prompt"
    assert raw["1"]["inputs"]["steps"] == 4


def test_derive_spec_output_loads_via_pipeline_engine_with_new_values(tmp_path):
    new_path = spec_remix.derive_spec(
        str(_FIX), {"1": {"prompt": "new prompt", "steps": 8}}, str(tmp_path / "out")
    )
    loaded = pipeline_engine.load_spec(new_path)
    assert loaded["1"]["inputs"]["prompt"] == "new prompt"
    assert loaded["1"]["inputs"]["steps"] == 8


def test_derive_spec_preserves_metadata_and_wires():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        new_path = spec_remix.derive_spec(
            str(_FIX), {"1": {"prompt": "new prompt"}}, td
        )
        raw = json.loads(Path(new_path).read_text())
        assert raw["_spec_version"] == "comfyui-api-v1"
        assert "_comment" in raw
        # wire on node "2" must be untouched
        assert raw["2"]["inputs"]["image_path"] == ["1", "image_path"]


def test_derive_spec_ignores_unknown_node_and_key_no_crash(tmp_path):
    new_path = spec_remix.derive_spec(
        str(_FIX),
        {
            "1": {"unknown_key": "should be ignored"},
            "99": {"prompt": "no such node"},
        },
        str(tmp_path / "out"),
    )
    raw = json.loads(Path(new_path).read_text())
    assert "unknown_key" not in raw["1"]["inputs"]
    assert "99" not in raw


def test_derive_spec_refuses_to_overwrite_a_wire(tmp_path):
    new_path = spec_remix.derive_spec(
        str(_FIX),
        {"2": {"image_path": "not-a-wire-anymore"}},
        str(tmp_path / "out"),
    )
    raw = json.loads(Path(new_path).read_text())
    # Wire must remain untouched — editing a wired input is a silent no-op.
    assert raw["2"]["inputs"]["image_path"] == ["1", "image_path"]


def test_derive_spec_collision_yields_distinct_files(tmp_path):
    dest_dir = tmp_path / "out"
    path1 = spec_remix.derive_spec(str(_FIX), {"1": {"steps": 1}}, str(dest_dir))
    path2 = spec_remix.derive_spec(str(_FIX), {"1": {"steps": 2}}, str(dest_dir))
    assert path1 != path2
    assert Path(path1).exists()
    assert Path(path2).exists()


def test_derive_spec_creates_dest_dir_if_missing(tmp_path):
    dest_dir = tmp_path / "does" / "not" / "exist"
    assert not dest_dir.exists()
    new_path = spec_remix.derive_spec(str(_FIX), {"1": {"steps": 1}}, str(dest_dir))
    assert Path(new_path).exists()


# ── add_step / remove_step / write_spec ──────────────────────────────────────
#
# add_step tests exercise the real 1964 World's Fair example workflow (a much
# richer graph than the 3-node remix fixture) per the task brief's suggestion
# that "the 1964 spec is a good real graph". remove_step tests use a small
# dedicated linear fixture (tests/fixtures/remix_structural_fixture.json) so
# the rewiring assertions can be exact and unambiguous (image->image->image,
# no kind-mismatch noise).

_1964 = (Path(__file__).parent.parent / "docs" / "examples" / "workflows"
        / "1964-worlds-fair.json")
_STRUCT_FIX = Path(__file__).parent / "fixtures" / "remix_structural_fixture.json"


def _raw_1964() -> dict:
    return json.loads(_1964.read_text())


def _raw_struct() -> dict:
    return json.loads(_STRUCT_FIX.read_text())


def _nodes_only(spec: dict) -> dict:
    return {k: v for k, v in spec.items()
            if not k.startswith("_") and isinstance(v, dict) and "class_type" in v}


def _no_wire_references(spec: dict, node_id: str) -> bool:
    """True iff no wire anywhere in *spec* (however nested) points at node_id."""
    def walk(v) -> bool:
        if spec_remix._is_wire(v):
            return v[0] != node_id
        if isinstance(v, list):
            return all(walk(item) for item in v)
        if isinstance(v, dict):
            return all(walk(item) for item in v.values())
        return True
    for nid, node in _nodes_only(spec).items():
        if nid == node_id:
            continue
        if not walk(node.get("inputs", {})):
            return False
    return True


# ── add_step ──────────────────────────────────────────────────────────────────

def test_add_step_wires_new_node_to_after_nodes_primary_output():
    spec = _raw_1964()
    before = json.loads(json.dumps(spec))  # deep copy for the unchanged-base check

    result = spec_remix.add_step(spec, "1", "TTLGCaptionImage")

    # base spec untouched
    assert spec == before

    # new node exists, wired src <- [after_node, primary_output]
    new_ids = set(result) - set(spec)
    assert len(new_ids) == 1
    new_id = new_ids.pop()
    new_node = result[new_id]
    assert new_node["class_type"] == "TTLGCaptionImage"
    assert new_node["inputs"]["src"] == ["1", "image_path"]

    # result is still a valid, topo-orderable spec
    pipeline_engine.topo_order(_nodes_only(result))


def test_add_step_incompatible_intent_raises():
    spec = _raw_1964()
    # TTLGGenerateText wants a "text" input; node "1" (TTLGTextToImage) produces
    # "image" — incompatible.
    with pytest.raises(ValueError):
        spec_remix.add_step(spec, "1", "TTLGGenerateText")


def test_add_step_applies_literal_params_to_new_node():
    spec = _raw_1964()
    # Node "2" (TTLGCaptionImage) produces "text" (caption); TTLGTextToImage
    # consumes "text" via its "prompt" input — compatible.
    result = spec_remix.add_step(spec, "2", "TTLGTextToImage", params={"steps": 8})

    new_ids = set(result) - set(spec)
    new_id = new_ids.pop()
    new_node = result[new_id]
    assert new_node["inputs"]["prompt"] == ["2", "caption"]
    assert new_node["inputs"]["steps"] == 8


def test_add_step_mints_fresh_unique_node_id():
    spec = _raw_1964()
    numbered = [int(k) for k in spec if not k.startswith("_") and k.isdigit()]
    result = spec_remix.add_step(spec, "1", "TTLGCaptionImage")
    new_ids = set(result) - set(spec)
    assert new_ids == {str(max(numbered) + 1)}


# ── remove_step ───────────────────────────────────────────────────────────────

def test_remove_step_rewires_consumer_to_removed_nodes_upstream():
    spec = _raw_struct()
    before = json.loads(json.dumps(spec))

    result = spec_remix.remove_step(spec, "2")

    assert spec == before          # base unchanged
    assert "2" not in result       # node gone
    # node 3 ("src" <- ["2","fg_path"]) now wired straight to node 2's own
    # upstream (["1","image_path"]) — exact rewire.
    assert result["3"]["inputs"]["src"] == ["1", "image_path"]
    assert _no_wire_references(result, "2")
    pipeline_engine.topo_order(_nodes_only(result))


def test_remove_step_with_no_upstream_drops_consumer_wire_key():
    spec = _raw_struct()
    result = spec_remix.remove_step(spec, "1")

    assert "1" not in result
    # node 1's own "prompt" input was a literal, not a wire -> no upstream to
    # reconnect through -> node 2's "src" key is dropped entirely.
    assert "src" not in result["2"]["inputs"]
    assert _no_wire_references(result, "1")
    pipeline_engine.topo_order(_nodes_only(result))


def test_remove_step_rewires_nested_wires_in_real_graph():
    """The 1964 spec's TTLGAddToPlaylist node (9) nests wires inside a
    list-of-dicts ("artifacts") and a dict ("metadata"). Removing a node those
    nested wires reference must not leave any dangling reference — mirrors the
    nested-wire handling pipeline_engine already relies on for topo_order/
    resolve_inputs (see tests/test_pipeline_engine.py's "Fix 1" nested-wire
    coverage).

    Node 9's nested wire is NOT a canonical input (TTLGAddToPlaylist's
    intent has no single canonical input_key at all — it's a collector), so
    the documented STRUCTURAL fallback applies: it still gets spliced onto
    node 2's own upstream (node 1's image_path) with no kind check, exactly
    as before this fix."""
    spec = _raw_1964()
    result = spec_remix.remove_step(spec, "2")  # TTLGCaptionImage

    assert "2" not in result
    assert _no_wire_references(result, "2")
    # Structural fallback: the nested metadata.caption wire is spliced onto
    # node 2's own upstream ([1, image_path]) exactly like the pre-fix
    # behavior — no kind check applies to non-canonical/nested wires.
    assert result["9"]["inputs"]["metadata"]["caption"] == ["1", "image_path"]
    pipeline_engine.topo_order(_nodes_only(result))


def test_remove_step_kind_mismatch_drops_canonical_input_instead_of_rewiring():
    """Regression for the Medium correctness bug: removing a kind-TRANSFORMING
    node (image -> text) must not leave a text-expecting consumer's canonical
    input silently wired to an image producer.

    Graph: A (TTLGTextToImage, produces image) -> B (TTLGCaptionImage,
    image -> text) -> C (TTLGGenerateText, its canonical "caption" input,
    which expects "text", wired to B's caption output).

    remove_step(spec, B) must NOT rewire C's "caption" to A's image_path
    (structurally valid — topo_order would pass — but semantically wrong,
    silently). Instead the canonical "caption" key must be dropped entirely,
    so C falls back to its literal/default prompt."""
    spec = {
        "1": {
            "class_type": "TTLGTextToImage",
            "inputs": {"prompt": "a photo"},
        },
        "2": {
            "class_type": "TTLGCaptionImage",
            "inputs": {"src": ["1", "image_path"]},
        },
        "3": {
            "class_type": "TTLGGenerateText",
            "inputs": {
                "prompt": "fallback prompt",
                "caption": ["2", "caption"],
            },
        },
    }

    result = spec_remix.remove_step(spec, "2")

    assert "2" not in result
    # The canonical "caption" key is dropped, NOT rewired to node 1 (image).
    assert "caption" not in result["3"]["inputs"]
    assert result["3"]["inputs"]["prompt"] == "fallback prompt"
    assert _no_wire_references(result, "2")
    pipeline_engine.topo_order(_nodes_only(result))


def test_remove_step_kind_match_still_rewires_canonical_input():
    """Companion to the kind-mismatch regression test above: when the removed
    node's upstream DOES produce the kind the consumer's canonical input
    expects, the rewire still happens (this is not a blanket "always drop"
    change — only mismatched kinds are dropped).

    Graph: A (TTLGTextToImage, produces image) -> B (TTLGRemoveBackground,
    image -> image) -> C (TTLGEstimateDepth, canonical "src" input expects
    "image", wired to B's fg_path output). Removing B must rewire C's "src"
    straight to A's image_path, since both are "image" kind."""
    spec = {
        "1": {
            "class_type": "TTLGTextToImage",
            "inputs": {"prompt": "a photo"},
        },
        "2": {
            "class_type": "TTLGRemoveBackground",
            "inputs": {"src": ["1", "image_path"]},
        },
        "3": {
            "class_type": "TTLGEstimateDepth",
            "inputs": {"src": ["2", "fg_path"]},
        },
    }

    result = spec_remix.remove_step(spec, "2")

    assert "2" not in result
    assert result["3"]["inputs"]["src"] == ["1", "image_path"]
    assert _no_wire_references(result, "2")
    pipeline_engine.topo_order(_nodes_only(result))


# ── write_spec ────────────────────────────────────────────────────────────────

def test_write_spec_round_trips(tmp_path):
    spec = _raw_struct()
    path = spec_remix.write_spec(spec, "mybase", str(tmp_path))
    assert Path(path).name == "remix_mybase_1.json"
    assert json.loads(Path(path).read_text()) == spec


def test_write_spec_collision_safe(tmp_path):
    spec = _raw_struct()
    path1 = spec_remix.write_spec(spec, "mybase", str(tmp_path))
    path2 = spec_remix.write_spec(spec, "mybase", str(tmp_path))
    assert path1 != path2
    assert Path(path1).exists()
    assert Path(path2).exists()


def test_write_spec_creates_dest_dir(tmp_path):
    dest_dir = tmp_path / "nested" / "dir"
    assert not dest_dir.exists()
    path = spec_remix.write_spec(_raw_struct(), "mybase", str(dest_dir))
    assert Path(path).exists()


def test_write_spec_rejects_invalid_spec(tmp_path):
    broken = {"1": {"class_type": "X", "inputs": {"a": ["99", "k"]}}}  # dangling wire
    with pytest.raises(ValueError):
        spec_remix.write_spec(broken, "broken", str(tmp_path))


# ── seed_spec ────────────────────────────────────────────────────────────────

def test_seed_spec_single_step():
    spec = spec_remix.seed_spec([("TTLGTextToImage", {"prompt": "a fox"})])
    assert spec["1"]["class_type"] == "TTLGTextToImage"
    assert spec["1"]["inputs"]["prompt"] == "a fox"


def test_seed_spec_wires_adjacent_steps():
    spec = spec_remix.seed_spec([
        ("TTLGTextToImage", {"prompt": "a fox"}),
        ("TTLGImageToVideo", {}),
    ])
    # step 2 (Film it) consumes step 1's image_path via its "image" key
    assert spec["2"]["class_type"] == "TTLGImageToVideo"
    assert spec["2"]["inputs"]["image"] == ["1", "image_path"]


def test_seed_spec_kind_mismatch_raises():
    with pytest.raises(ValueError):
        # Film it (needs image) cannot follow Compose a prompt (produces text)
        spec_remix.seed_spec([("TTLGPromptCompose", {}), ("TTLGImageToVideo", {})])


def test_seed_spec_with_seed_artifact_places_literal_path():
    spec = spec_remix.seed_spec(
        [("TTLGImageToVideo", {})],
        seed_artifact=("/tmp/pic.png", "image"),
    )
    assert spec["1"]["inputs"]["image"] == "/tmp/pic.png"


def test_seed_spec_seed_artifact_kind_mismatch_raises():
    with pytest.raises(ValueError):
        # Compose a prompt needs text, not an image seed
        spec_remix.seed_spec([("TTLGPromptCompose", {})], seed_artifact=("/tmp/pic.png", "image"))


def test_seed_spec_empty_raises():
    with pytest.raises(ValueError):
        spec_remix.seed_spec([])
