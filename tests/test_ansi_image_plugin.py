"""Tests for the ansi-image utility plugin: pure-Pillow image -> ANSI-art
converter (Effort B Task 1).

Mirrors the `_load_plugin` importlib pattern from test_forge_plugins.py.
Unlike rmbg/blip/depth (which shell out to a venv python for torch), this
plugin is pure Pillow + the app's own xterm-256 palette table
(`artgen_render._XTERM256_HEX`) run fully in-process -- no subprocess, no
LLM, fully deterministic.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
PLUGINS = REPO_ROOT / "plugins"


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_plugin(name: str):
    """Load a plugin by file path to avoid Python module-cache collisions."""
    spec = importlib.util.spec_from_file_location(
        f"forge_plugin_{name}", PLUGINS / name / "plugin.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_solid_png(tmp_path: Path, color=(220, 20, 20), size=(40, 20)) -> Path:
    Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    img = Image.new("RGB", size, color=color)
    p = tmp_path / "solid.png"
    img.save(p)
    return p


def _make_wide_png(tmp_path: Path, size=(200, 20)) -> Path:
    Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    img = Image.new("RGB", size, color=(10, 200, 30))
    p = tmp_path / "wide.png"
    img.save(p)
    return p


# ── mcp.json schema ──────────────────────────────────────────────────────────

def test_mcp_json_valid():
    mcp = json.loads((PLUGINS / "ansi-image" / "mcp.json").read_text())
    xttlg = mcp["x-ttlg"]
    assert xttlg["utility"] is True
    assert xttlg["output_ext"] == ".ans"
    assert xttlg["media_type"] is None
    assert xttlg["accepts_remix_from"] == ["image"]
    assert xttlg["can_remix_to"] == []
    assert xttlg["tab"] is None
    assert xttlg["hardware"] is None

    names = [t["name"] for t in mcp["tools"]]
    assert "ansi_image_to_ansi" in names
    assert "ansi_image_is_available" in names
    assert len(names) == 2

    for tool in mcp["tools"]:
        assert "description" in tool
        assert "inputSchema" in tool
        assert tool["x-ttlg"]["artifact_tool"] is False

    primary = next(t for t in mcp["tools"] if t["name"] == "ansi_image_to_ansi")
    assert "src" in primary["inputSchema"]["properties"]
    assert primary["inputSchema"]["required"] == ["src"]


# ── is_available ─────────────────────────────────────────────────────────────

def test_is_available_true_when_pillow_importable():
    mod = _load_plugin("ansi-image")
    mod._available = None
    assert mod.is_available() is True


# ── image_to_ansi ─────────────────────────────────────────────────────────────

def test_missing_file_raises(tmp_path):
    mod = _load_plugin("ansi-image")
    with pytest.raises(FileNotFoundError):
        mod.image_to_ansi(str(tmp_path / "nope.png"))


def test_returns_nonempty_ansi_str_with_expected_escapes(tmp_path):
    mod = _load_plugin("ansi-image")
    src = _make_solid_png(tmp_path)

    result = mod.image_to_ansi(str(src), cols=16)

    assert isinstance(result, str)
    assert result.strip() != ""
    assert "\x1b[38;5;" in result
    assert "█" in result  # full block character


def test_parse_ansi_grid_yields_matching_dims(tmp_path):
    sys.path.insert(0, str(REPO_ROOT / "app"))
    import artgen_render

    mod = _load_plugin("ansi-image")
    src = _make_solid_png(tmp_path, size=(40, 20))

    cols = 20
    result = mod.image_to_ansi(str(src), cols=cols)
    grid = artgen_render.parse_ansi_grid(result)

    assert len(grid) > 0
    # every row should have `cols` cells
    for row in grid:
        assert len(row) == cols
    # rows should roughly match the aspect-corrected height (h/w * 0.5 * cols)
    expected_rows = round(cols * (20 / 40) * 0.5)
    assert abs(len(grid) - expected_rows) <= 1


def test_red_image_quantizes_to_reddish_index(tmp_path):
    sys.path.insert(0, str(REPO_ROOT / "app"))
    import artgen_render

    mod = _load_plugin("ansi-image")
    src = _make_solid_png(tmp_path, color=(230, 15, 15), size=(20, 10))

    result = mod.image_to_ansi(str(src), cols=8)
    grid = artgen_render.parse_ansi_grid(result)

    fg = grid[0][0][1]
    assert fg is not None
    r = int(fg[1:3], 16)
    g = int(fg[3:5], 16)
    b = int(fg[5:7], 16)
    assert r > g and r > b, f"expected reddish nearest-match, got {fg}"


def test_aspect_ratio_not_stretched_for_wide_image(tmp_path):
    mod = _load_plugin("ansi-image")
    src = _make_wide_png(tmp_path, size=(200, 20))

    sys.path.insert(0, str(REPO_ROOT / "app"))
    import artgen_render

    cols = 80
    result = mod.image_to_ansi(str(src), cols=cols)
    grid = artgen_render.parse_ansi_grid(result)

    rows = len(grid)
    # aspect-corrected: rows = cols * (h/w) * 0.5 = 80 * (20/200) * 0.5 = 4
    assert rows < cols
    assert 2 <= rows <= 6


def test_cols_and_rows_clamped_to_sane_max(tmp_path):
    Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    p = tmp_path / "tall.png"
    Image.new("RGB", (10, 1000), color=(0, 0, 200)).save(p)

    mod = _load_plugin("ansi-image")
    sys.path.insert(0, str(REPO_ROOT / "app"))
    import artgen_render

    result = mod.image_to_ansi(str(p), cols=200)
    grid = artgen_render.parse_ansi_grid(result)

    # cols should be clamped to <= 120
    assert all(len(row) <= 120 for row in grid)
    # rows should be clamped to <= 60
    assert len(grid) <= 60


def test_colors_16_supports_dos_palette(tmp_path):
    mod = _load_plugin("ansi-image")
    src = _make_solid_png(tmp_path, color=(230, 15, 15), size=(20, 10))

    result = mod.image_to_ansi(str(src), cols=8, colors=16)
    assert isinstance(result, str) and result.strip() != ""

    sys.path.insert(0, str(REPO_ROOT / "app"))
    import artgen_render
    grid = artgen_render.parse_ansi_grid(result)
    assert len(grid) > 0


# ── plugin_loader sees it as a utility plugin (no regression) ───────────────

def test_utility_plugin_not_in_generator_registry():
    sys.path.insert(0, str(REPO_ROOT / "app"))
    import plugin_loader
    import importlib
    importlib.reload(plugin_loader)

    orig_paths = plugin_loader._SEARCH_PATHS[:]
    try:
        plugin_loader._SEARCH_PATHS[:] = [PLUGINS]
        plugin_loader.load_plugins()
        assert "ansi-image" not in plugin_loader._PLUGINS
    finally:
        plugin_loader._SEARCH_PATHS[:] = orig_paths
        plugin_loader._PLUGINS.clear()
