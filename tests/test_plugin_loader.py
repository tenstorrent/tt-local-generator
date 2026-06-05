import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _make_plugin(tmp_path, name, manifest_extra=None, with_py=False):
    """Helper: write a minimal valid plugin directory."""
    d = tmp_path / name
    d.mkdir()
    manifest = {
        "x-ttlg": {
            "output_ext": ".txt",
            "media_type": "text",
            "accepts_remix_from": [],
            "can_remix_to": [],
            "tab": "generative-art",
            "hardware": None,
        },
        "tools": [
            {
                "name": name,
                "description": f"Test generator {name}",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
                "examples": [],
                "x-ttlg": {"streaming": None, "artifact_tool": True},
            }
        ],
    }
    if manifest_extra:
        manifest.update(manifest_extra)
    (d / "mcp.json").write_text(json.dumps(manifest))
    if with_py:
        (d / "plugin.py").write_text(
            "from artgen import ArtGenerator\n"
            "class TestGen(ArtGenerator):\n"
            "    name = 'stub'\n"
            "    description = 'stub'\n"
            "    def build_prompt(self, args): return 'prompt'\n"
        )
    return d


def test_discover_plugins_from_directory(tmp_path, monkeypatch):
    """Loader finds all subdirectories with mcp.json."""
    _make_plugin(tmp_path, "alpha")
    _make_plugin(tmp_path, "beta")
    import plugin_loader
    monkeypatch.setattr(plugin_loader, "_SEARCH_PATHS", [tmp_path])
    plugin_loader._PLUGINS.clear()
    plugin_loader.load_plugins()
    assert "alpha" in plugin_loader._PLUGINS
    assert "beta" in plugin_loader._PLUGINS


def test_malformed_manifest_skipped(tmp_path, monkeypatch, capsys):
    """Plugin with invalid JSON in mcp.json is skipped; others still load."""
    _make_plugin(tmp_path, "good")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "mcp.json").write_text("not json {{{")
    import plugin_loader
    monkeypatch.setattr(plugin_loader, "_SEARCH_PATHS", [tmp_path])
    plugin_loader._PLUGINS.clear()
    plugin_loader.load_plugins()
    assert "good" in plugin_loader._PLUGINS
    assert "bad" not in plugin_loader._PLUGINS


def test_plugin_def_fields_populated(tmp_path, monkeypatch):
    """PluginDef has name, tools, manifest fields from mcp.json."""
    _make_plugin(tmp_path, "myplugin")
    import plugin_loader
    monkeypatch.setattr(plugin_loader, "_SEARCH_PATHS", [tmp_path])
    plugin_loader._PLUGINS.clear()
    plugin_loader.load_plugins()
    p = plugin_loader._PLUGINS["myplugin"]
    assert p.name == "myplugin"
    assert len(p.tools) == 1
    assert p.tools[0]["name"] == "myplugin"
    assert p.manifest["x-ttlg"]["output_ext"] == ".txt"


def test_user_dir_overrides_repo_dir(tmp_path, monkeypatch):
    """Plugin in second search path overrides same-name plugin in first."""
    repo_dir = tmp_path / "repo_plugins"
    user_dir = tmp_path / "user_plugins"
    repo_dir.mkdir(); user_dir.mkdir()
    _make_plugin(repo_dir, "shared")
    user_manifest = {
        "x-ttlg": {
            "output_ext": ".svg",
            "media_type": "image",
            "accepts_remix_from": [],
            "can_remix_to": [],
            "tab": "generative-art",
            "hardware": None,
        },
        "tools": [{
            "name": "shared",
            "description": "user override",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
            "examples": [],
            "x-ttlg": {"streaming": None, "artifact_tool": True},
        }],
    }
    (user_dir / "shared").mkdir()
    (user_dir / "shared" / "mcp.json").write_text(json.dumps(user_manifest))
    import plugin_loader
    monkeypatch.setattr(plugin_loader, "_SEARCH_PATHS", [repo_dir, user_dir])
    plugin_loader._PLUGINS.clear()
    plugin_loader.load_plugins()
    assert plugin_loader._PLUGINS["shared"].manifest["x-ttlg"]["output_ext"] == ".svg"


def test_accepts_remix_from_populated(tmp_path, monkeypatch):
    """accepts_remix_from and can_remix_to come from manifest x-ttlg."""
    d = tmp_path / "img"
    d.mkdir()
    manifest = {
        "x-ttlg": {
            "output_ext": ".svg",
            "media_type": "image",
            "accepts_remix_from": ["verse", "palette"],
            "can_remix_to": ["video"],
            "tab": "generative-art",
            "hardware": None,
        },
        "tools": [{
            "name": "img",
            "description": "image gen",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
            "examples": [],
            "x-ttlg": {"streaming": None, "artifact_tool": True},
        }],
    }
    (d / "mcp.json").write_text(json.dumps(manifest))
    import plugin_loader
    monkeypatch.setattr(plugin_loader, "_SEARCH_PATHS", [tmp_path])
    plugin_loader._PLUGINS.clear()
    plugin_loader.load_plugins()
    p = plugin_loader._PLUGINS["img"]
    assert "verse" in p.accepts_remix_from
    assert "palette" in p.accepts_remix_from
    assert "video" in p.can_remix_to
