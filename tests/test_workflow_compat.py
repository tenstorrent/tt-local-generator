"""Tests for workflow compatibility layer."""
from __future__ import annotations
import sys, json, tempfile
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

# artgen's generator registry back-fills at import time (app/artgen/__init__.py
# calls _load_generators() at module scope), so a plain import is enough to
# populate all_names(). Guard against an empty registry anyway (e.g. plugin
# discovery failing in a stripped-down test env) by forcing a reload — this
# keeps the "reject unknown plugin" test meaningful instead of vacuously
# passing because the plugin check gets skipped when the registry is empty.
import artgen
from artgen import all_names as _artgen_all_names

ARTGEN_NAMES = _artgen_all_names()
if not ARTGEN_NAMES:
    artgen._load_generators()
    ARTGEN_NAMES = _artgen_all_names()


@pytest.fixture(autouse=True)
def _reload_real_artgen_registry():
    """
    Reload the genuine artgen generator registry before each test in this file.

    tests/test_plugin_loader.py's tests monkeypatch plugin_loader._SEARCH_PATHS
    to point at temporary fixture directories, then clear+repopulate
    plugin_loader._PLUGINS / artgen._GENERATORS from those temp plugins.
    monkeypatch reverts the _SEARCH_PATHS *attribute* at teardown, but the
    dict mutations are not attribute assignments, so they survive into later
    test modules. When this file runs after test_plugin_loader.py in the same
    session, artgen._GENERATORS can be left holding whatever fixture plugin
    that module's last test loaded (e.g. just "runnable_gen") instead of the
    real generators (verse/palette/ansi/…) — which would make our plugin-name
    validation tests wrong for reasons that have nothing to do with
    workflow_compat. Reload for real before each test here so these tests are
    immune to load order / pollution from other test modules.
    """
    artgen._load_generators()
    yield


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


# ── TTLGArtgenGenerate / TTLGAnimateDiff (Task 8) ─────────────────────────────

def test_artgen_generate_and_animatediff_are_native():
    from workflow_compat import validate_spec
    assert ARTGEN_NAMES, "artgen registry must be populated for this test to mean anything"
    path = write_spec({
        "1": {
            "class_type": "TTLGArtgenGenerate",
            "inputs": {"plugin": ARTGEN_NAMES[0]},
            "outputs": ["artifact_path"],
        },
        "2": {
            "class_type": "TTLGAnimateDiff",
            "inputs": {"prompt": "a dancer", "frames": 24, "steps": 20, "seed": 1},
            "outputs": ["gif_path"],
        },
    })
    result = validate_spec(path)
    assert result.ok is True
    assert result.blocking == []
    assert result.warnings == []
    assert result.mappings == []


@pytest.mark.parametrize("plugin_name", ARTGEN_NAMES)
def test_artgen_generate_accepts_each_known_plugin(plugin_name):
    from workflow_compat import validate_spec
    path = write_spec({
        "1": {
            "class_type": "TTLGArtgenGenerate",
            "inputs": {"plugin": plugin_name},
            "outputs": ["artifact_path"],
        },
    })
    result = validate_spec(path)
    assert result.ok is True
    assert result.blocking == []


def test_artgen_generate_rejects_unknown_plugin():
    from workflow_compat import validate_spec
    assert ARTGEN_NAMES, "registry must be populated so the unknown-plugin check is actually exercised"
    path = write_spec({
        "1": {
            "class_type": "TTLGArtgenGenerate",
            "inputs": {"plugin": "not_a_real_plugin"},
            "outputs": ["artifact_path"],
        },
    })
    result = validate_spec(path)
    assert result.ok is False
    assert len(result.blocking) == 1
    assert "not_a_real_plugin" in result.blocking[0]


def test_artgen_generate_missing_plugin_input_blocks():
    from workflow_compat import validate_spec
    path = write_spec({
        "1": {
            "class_type": "TTLGArtgenGenerate",
            "inputs": {},
            "outputs": ["artifact_path"],
        },
    })
    result = validate_spec(path)
    assert result.ok is False
    assert len(result.blocking) == 1
    assert "missing required 'plugin'" in result.blocking[0]
