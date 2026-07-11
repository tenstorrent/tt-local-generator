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
