import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _make_plugin(tmp_path, name, manifest_extra=None, with_py=True):
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
            f"class TestGen(ArtGenerator):\n"
            f"    name = {name!r}\n"
            "    description = 'stub'\n"
            "    output_ext = '.txt'\n"
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
    (user_dir / "shared" / "plugin.py").write_text(
        "from artgen import ArtGenerator\n"
        "class _G(ArtGenerator):\n"
        "    name = 'shared'\n"
        "    description = 'user override'\n"
        "    output_ext = '.svg'\n"
        "    def build_prompt(self, args): return 'prompt'\n"
    )
    import plugin_loader
    monkeypatch.setattr(plugin_loader, "_SEARCH_PATHS", [repo_dir, user_dir])
    plugin_loader._PLUGINS.clear()
    plugin_loader.load_plugins()
    assert plugin_loader._PLUGINS["shared"].manifest["x-ttlg"]["output_ext"] == ".svg"


def test_runnable_true_for_local_plugin(tmp_path, monkeypatch):
    """Local plugins with plugin.py get runnable=True."""
    _make_plugin(tmp_path, "local")
    import plugin_loader
    monkeypatch.setattr(plugin_loader, "_SEARCH_PATHS", [tmp_path])
    plugin_loader._PLUGINS.clear()
    plugin_loader.load_plugins()
    assert plugin_loader._PLUGINS["local"].runnable is True


def test_runnable_false_for_mcp_server_stub(tmp_path, monkeypatch):
    """MCP-server-only plugins (no plugin.py) get runnable=False."""
    d = tmp_path / "midi"
    d.mkdir()
    manifest = {
        "x-ttlg": {
            "output_ext": ".mid",
            "media_type": "midi",
            "accepts_remix_from": [],
            "can_remix_to": [],
            "tab": "generative-art",
            "hardware": None,
            "mcp_server": {"command": "npx", "args": ["-y", "tt-midi-maker"]},
        },
        "tools": [{
            "name": "midi",
            "description": "midi gen",
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
    assert "midi" in plugin_loader._PLUGINS
    assert plugin_loader._PLUGINS["midi"].runnable is False


def test_utility_plugin_not_registered(tmp_path, monkeypatch):
    """Utility plugins (utility:true) are skipped entirely."""
    d = tmp_path / "ffmpeg"
    d.mkdir()
    manifest = {
        "x-ttlg": {
            "output_ext": None,
            "media_type": None,
            "accepts_remix_from": [],
            "can_remix_to": [],
            "tab": None,
            "hardware": None,
            "utility": True,
        },
        "tools": [{
            "name": "ffmpeg_extract_frame",
            "description": "extract a frame",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
            "examples": [],
            "x-ttlg": {"streaming": None, "artifact_tool": False},
        }],
    }
    (d / "mcp.json").write_text(json.dumps(manifest))
    import plugin_loader
    monkeypatch.setattr(plugin_loader, "_SEARCH_PATHS", [tmp_path])
    plugin_loader._PLUGINS.clear()
    plugin_loader.load_plugins()
    assert "ffmpeg_extract_frame" not in plugin_loader._PLUGINS
    assert "ffmpeg" not in plugin_loader._PLUGINS


def test_manifest_only_no_mcp_server_skipped(tmp_path, monkeypatch):
    """Plugin with no plugin.py AND no mcp_server is skipped (not runnable)."""
    d = tmp_path / "ghost"
    d.mkdir()
    manifest = {
        "x-ttlg": {
            "output_ext": ".txt", "media_type": "text",
            "accepts_remix_from": [], "can_remix_to": [],
            "tab": "generative-art", "hardware": None,
        },
        "tools": [{
            "name": "ghost",
            "description": "ghost",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
            "examples": [],
            "x-ttlg": {"streaming": None, "artifact_tool": True},
        }],
    }
    (d / "mcp.json").write_text(json.dumps(manifest))
    # No plugin.py, no mcp_server
    import plugin_loader
    monkeypatch.setattr(plugin_loader, "_SEARCH_PATHS", [tmp_path])
    plugin_loader._PLUGINS.clear()
    plugin_loader.load_plugins()
    assert "ghost" not in plugin_loader._PLUGINS


def test_artgen_back_fill_excludes_stubs(tmp_path, monkeypatch):
    """artgen._GENERATORS back-fill skips runnable=False stubs."""
    # Create a runnable plugin and a stub plugin
    _make_plugin(tmp_path, "runnable_gen")
    stub_dir = tmp_path / "stub_gen"
    stub_dir.mkdir()
    stub_manifest = {
        "x-ttlg": {
            "output_ext": ".mid", "media_type": "midi",
            "accepts_remix_from": [], "can_remix_to": [],
            "tab": "generative-art", "hardware": None,
            "mcp_server": {"command": "npx", "args": ["-y", "stub"]},
        },
        "tools": [{
            "name": "stub_gen",
            "description": "stub",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
            "examples": [],
            "x-ttlg": {"streaming": None, "artifact_tool": True},
        }],
    }
    (stub_dir / "mcp.json").write_text(json.dumps(stub_manifest))

    import plugin_loader
    import artgen
    monkeypatch.setattr(plugin_loader, "_SEARCH_PATHS", [tmp_path])
    plugin_loader._PLUGINS.clear()
    artgen._GENERATORS.clear()
    plugin_loader.load_plugins()
    # Manually trigger back-fill as _load_generators() does
    for name, pdef in plugin_loader._PLUGINS.items():
        if pdef.runnable:
            artgen._GENERATORS[name] = pdef.generator

    assert "runnable_gen" in artgen._GENERATORS
    assert "stub_gen" not in artgen._GENERATORS


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
    (d / "plugin.py").write_text(
        "from artgen import ArtGenerator\n"
        "class _G(ArtGenerator):\n"
        "    name = 'img'\n"
        "    description = 'image gen'\n"
        "    output_ext = '.svg'\n"
        "    def build_prompt(self, args): return 'prompt'\n"
    )
    import plugin_loader
    monkeypatch.setattr(plugin_loader, "_SEARCH_PATHS", [tmp_path])
    plugin_loader._PLUGINS.clear()
    plugin_loader.load_plugins()
    p = plugin_loader._PLUGINS["img"]
    assert "verse" in p.accepts_remix_from
    assert "palette" in p.accepts_remix_from
    assert "video" in p.can_remix_to
