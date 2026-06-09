"""Tests for workflow compatibility layer."""
from __future__ import annotations
import sys, json, tempfile
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def write_spec(nodes: dict) -> str:
    spec = {"_description": "test", "_spec_version": "comfyui-api-v1"}
    spec.update(nodes)
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    json.dump(spec, f); f.close()
    return f.name


def test_all_native_nodes_valid():
    from workflow_compat import validate_spec
    path = write_spec({
        "1": {"class_type": "TTLGTextToImage",  "inputs": {}, "outputs": ["image_path"]},
        "4": {"class_type": "TTLGImageToVideo",  "inputs": {}, "outputs": ["video_path"]},
        "9": {"class_type": "TTLGAddToPlaylist", "inputs": {}, "outputs": []},
    })
    result = validate_spec(path)
    assert result.ok is True
    assert result.warnings == []
    assert result.blocking == []


def test_skippable_node_produces_warning():
    from workflow_compat import validate_spec
    path = write_spec({
        "1": {"class_type": "TTLGTextToImage",  "inputs": {}, "outputs": ["image_path"]},
        "2": {"class_type": "ControlNetApply",   "inputs": {}, "outputs": ["conditioning"]},
        "9": {"class_type": "TTLGAddToPlaylist", "inputs": {}, "outputs": []},
    })
    result = validate_spec(path)
    assert result.ok is True  # still runnable
    assert len(result.warnings) == 1
    assert "ControlNetApply" in result.warnings[0]


def test_unknown_required_node_blocks():
    from workflow_compat import validate_spec
    path = write_spec({
        "1": {"class_type": "TTLGTextToImage", "inputs": {}, "outputs": ["image_path"]},
        "2": {"class_type": "MyCustomNode",    "inputs": {}, "outputs": ["out"],
              "_required": True},
    })
    result = validate_spec(path)
    assert result.ok is False
    assert len(result.blocking) >= 1
    assert "MyCustomNode" in result.blocking[0]


def test_unknown_node_without_required_flag_is_skippable():
    from workflow_compat import validate_spec
    path = write_spec({
        "1": {"class_type": "TTLGTextToImage", "inputs": {}, "outputs": ["image_path"]},
        "2": {"class_type": "UnknownOptional", "inputs": {}, "outputs": []},
    })
    result = validate_spec(path)
    assert result.ok is True
    assert any("UnknownOptional" in w for w in result.warnings)


def test_mapped_node_produces_mapping_note():
    from workflow_compat import validate_spec
    path = write_spec({
        "1": {"class_type": "KSampler",          "inputs": {"steps": 20}, "outputs": ["latent"]},
        "9": {"class_type": "TTLGAddToPlaylist",  "inputs": {}, "outputs": []},
    })
    result = validate_spec(path)
    assert result.ok is True
    assert any("KSampler" in m for m in result.mappings)


def test_empty_spec_is_valid():
    from workflow_compat import validate_spec
    path = write_spec({})
    result = validate_spec(path)
    assert result.ok is True


def test_missing_spec_file_returns_not_ok():
    from workflow_compat import validate_spec
    result = validate_spec("/nonexistent/spec.json")
    assert result.ok is False
    assert len(result.blocking) == 1


def test_validate_result_summary_string():
    from workflow_compat import validate_spec
    path = write_spec({
        "1": {"class_type": "TTLGTextToImage", "inputs": {}, "outputs": []},
        "2": {"class_type": "ControlNetApply", "inputs": {}, "outputs": []},
    })
    result = validate_spec(path)
    summary = result.summary()
    assert "skip" in summary.lower() or "warn" in summary.lower()
