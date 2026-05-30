"""Tests for the forge utility plugins: rmbg, blip, depth.

All three follow the ffmpeg plugin pattern — utility:true, no ArtGenerator,
subprocess-based inference via the tenstorrent venv python.

Tests mock subprocess.run so the suite runs without the venv, GPU, or
internet access.
"""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
PLUGINS = REPO_ROOT / "plugins"


# ── helpers ──────────────────────────────────────────────────────────────────

import importlib.util


def _load_plugin(name: str):
    """Load a plugin by file path to avoid Python module-cache collisions."""
    spec = importlib.util.spec_from_file_location(
        f"forge_plugin_{name}", PLUGINS / name / "plugin.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_test_png(tmp_path: Path) -> Path:
    from PIL import Image
    img = Image.new("RGB", (4, 4), color=(128, 64, 32))
    p = tmp_path / "test.png"
    img.save(p)
    return p


def _ok_run(**kwargs):
    """Return a CompletedProcess with returncode=0."""
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def _fail_run(**kwargs):
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="error")


# ── mcp.json schema ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("plugin", ["rmbg", "blip", "depth"])
def test_mcp_json_valid(plugin):
    mcp = json.loads((PLUGINS / plugin / "mcp.json").read_text())
    assert mcp["x-ttlg"]["utility"] is True
    assert mcp["x-ttlg"]["tab"] is None
    assert len(mcp["tools"]) >= 1
    for tool in mcp["tools"]:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool


@pytest.mark.parametrize("plugin", ["rmbg", "blip", "depth"])
def test_mcp_json_no_generator_fields(plugin):
    mcp = json.loads((PLUGINS / plugin / "mcp.json").read_text())
    assert mcp["x-ttlg"].get("media_type") is None
    assert mcp["x-ttlg"].get("tab") is None


# ── is_available probes subprocess ──────────────────────────────────────────

@pytest.mark.parametrize("plugin_name", ["rmbg", "blip", "depth"])
def test_is_available_returns_bool(plugin_name, tmp_path):
    mod = _load_plugin(plugin_name)
    # Reset cached state
    mod._available = None
    with patch("subprocess.run", return_value=_ok_run()):
        result = mod.is_available()
    assert result is True
    mod._available = None


@pytest.mark.parametrize("plugin_name", ["rmbg", "blip", "depth"])
def test_is_available_returns_false_on_import_failure(plugin_name):
    mod = _load_plugin(plugin_name)
    mod._available = None
    with patch("subprocess.run", return_value=_fail_run()):
        result = mod.is_available()
    assert result is False
    mod._available = None


# ── rmbg ─────────────────────────────────────────────────────────────────────

def test_rmbg_missing_file_raises(tmp_path):
    mod = _load_plugin("rmbg")
    mod._available = True
    with pytest.raises(FileNotFoundError):
        mod.remove_background(str(tmp_path / "nope.png"))


def test_rmbg_writes_output(tmp_path):
    mod = _load_plugin("rmbg")
    mod._available = True

    src = _make_test_png(tmp_path)
    dest = str(tmp_path / "out.png")

    # Create a real output file so the function can return it
    from PIL import Image
    Image.new("RGBA", (4, 4)).save(dest)

    with patch("subprocess.run", return_value=subprocess.CompletedProcess(
        args=[], returncode=0, stdout=dest + "\n", stderr=""
    )):
        result = mod.remove_background(str(src), dest)

    assert result == dest


def test_rmbg_raises_on_subprocess_failure(tmp_path):
    mod = _load_plugin("rmbg")
    mod._available = True

    src = _make_test_png(tmp_path)
    with patch("subprocess.run", return_value=_fail_run()):
        with pytest.raises(RuntimeError, match="rmbg inference failed"):
            mod.remove_background(str(src), str(tmp_path / "out.png"))


def test_rmbg_raises_when_unavailable(tmp_path):
    mod = _load_plugin("rmbg")
    mod._available = False
    src = _make_test_png(tmp_path)
    with pytest.raises(RuntimeError, match="rmbg plugin requires"):
        mod.remove_background(str(src))


# ── blip ─────────────────────────────────────────────────────────────────────

def test_blip_missing_file_raises(tmp_path):
    mod = _load_plugin("blip")
    mod._available = True
    with pytest.raises(FileNotFoundError):
        mod.caption_image(str(tmp_path / "nope.png"))


def test_blip_returns_caption(tmp_path):
    mod = _load_plugin("blip")
    mod._available = True

    src = _make_test_png(tmp_path)
    with patch("subprocess.run", return_value=subprocess.CompletedProcess(
        args=[], returncode=0, stdout='"a cat on a table"\n', stderr=""
    )):
        caption = mod.caption_image(str(src))

    assert caption == "a cat on a table"


def test_blip_raises_on_failure(tmp_path):
    mod = _load_plugin("blip")
    mod._available = True

    src = _make_test_png(tmp_path)
    with patch("subprocess.run", return_value=_fail_run()):
        with pytest.raises(RuntimeError, match="blip inference failed"):
            mod.caption_image(str(src))


def test_blip_raises_when_unavailable(tmp_path):
    mod = _load_plugin("blip")
    mod._available = False
    src = _make_test_png(tmp_path)
    with pytest.raises(RuntimeError):
        mod.caption_image(str(src))


# ── depth ─────────────────────────────────────────────────────────────────────

def test_depth_missing_file_raises(tmp_path):
    mod = _load_plugin("depth")
    mod._available = True
    with pytest.raises(FileNotFoundError):
        mod.estimate_depth(str(tmp_path / "nope.png"))


def test_depth_writes_output(tmp_path):
    mod = _load_plugin("depth")
    mod._available = True

    src = _make_test_png(tmp_path)
    dest = str(tmp_path / "depth.png")

    from PIL import Image
    Image.new("L", (4, 4)).save(dest)

    with patch("subprocess.run", return_value=subprocess.CompletedProcess(
        args=[], returncode=0, stdout=dest + "\n", stderr=""
    )):
        result = mod.estimate_depth(str(src), dest)

    assert result == dest


def test_depth_raises_on_failure(tmp_path):
    mod = _load_plugin("depth")
    mod._available = True

    src = _make_test_png(tmp_path)
    with patch("subprocess.run", return_value=_fail_run()):
        with pytest.raises(RuntimeError, match="depth inference failed"):
            mod.estimate_depth(str(src), str(tmp_path / "depth.png"))


def test_depth_raises_when_unavailable(tmp_path):
    mod = _load_plugin("depth")
    mod._available = False
    src = _make_test_png(tmp_path)
    with pytest.raises(RuntimeError):
        mod.estimate_depth(str(src))


# ── plugin_loader sees utility plugins ───────────────────────────────────────

def test_mcp_json_utility_plugins_not_in_generator_registry():
    """plugin_loader skips utility plugins — they must never appear in _PLUGINS
    (the generator registry). They are loaded on demand by the MCP server."""
    sys.path.insert(0, str(REPO_ROOT / "app"))
    import plugin_loader
    import importlib
    importlib.reload(plugin_loader)

    orig_paths = plugin_loader._SEARCH_PATHS[:]
    try:
        plugin_loader._SEARCH_PATHS[:] = [PLUGINS]
        plugin_loader.load_plugins()
        # Utility plugins must NOT be in the generator registry
        for name in ("rmbg", "blip", "depth", "ffmpeg"):
            assert name not in plugin_loader._PLUGINS, (
                f"Utility plugin '{name}' should not appear in generator registry"
            )
    finally:
        plugin_loader._SEARCH_PATHS[:] = orig_paths
        plugin_loader._PLUGINS.clear()


@pytest.mark.parametrize("plugin", ["rmbg", "blip", "depth"])
def test_utility_plugin_has_valid_tool_names(plugin):
    """Each tool name must be unique and follow snake_case convention."""
    mcp = json.loads((PLUGINS / plugin / "mcp.json").read_text())
    names = [t["name"] for t in mcp["tools"]]
    assert len(names) == len(set(names)), f"Duplicate tool names in {plugin}"
    for name in names:
        assert name.replace("_", "").isalnum(), (
            f"Tool name {name!r} in {plugin} should be snake_case alphanumeric"
        )
