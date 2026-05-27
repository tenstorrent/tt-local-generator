# Plugin System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded `_load_generators()` artgen registry with an MCP-native plugin system that makes every generator (LLM artgen, hardware video/image, external MCP servers) discoverable from a `plugins/` directory, exposes them all as an MCP server endpoint, and renames all user-facing "ArtGen" strings to "Generative Art".

**Architecture:** Each plugin is a directory containing `mcp.json` (standard MCP tool manifest with `x-ttlg` extension fields) and optionally `plugin.py` (Python executor). A new `app/plugin_loader.py` discovers plugins from `plugins/` and `~/.config/tt-local-gen/plugins/`, builds `PluginDef` objects, and replaces the old `@register` + `_load_generators()` pattern. A new `app/mcp_server.py` serves all loaded plugins as an MCP endpoint on port 8003, making them callable from Claude Code or any MCP client.

**Tech Stack:** Python 3.12, FastAPI (already used by `prompt_server.py`), standard MCP over HTTP+SSE, pytest for tests.

---

## File Map

**New files:**
- `app/plugin_loader.py` — discovers plugins, builds `PluginDef`, replaces `_load_generators()`
- `app/mcp_server.py` — FastAPI MCP server endpoint (port 8003)
- `plugins/<name>/mcp.json` — for each of 13 existing generators + midi stub
- `plugins/<name>/plugin.py` — moved from `app/artgen/generators/<name>.py`
- `tests/test_plugin_loader.py` — loader discovery and error handling tests
- `tests/test_artgen_generators.py` — per-generator build_prompt/parse_output/generate_artifact
- `tests/test_mcp_server.py` — MCP endpoint protocol tests
- `tests/test_remix_graph.py` — remix edge derivation from manifests

**Modified files:**
- `app/artgen/__init__.py` — add `RemixContext`, `accepts_remix_from`/`can_remix_to` to `ArtGenerator`, update `_load_generators()` to delegate to plugin_loader
- `app/artgen/generators/__init__.py` — shrinks to empty as generators migrate
- `app/artgen_panel.py` — update tooltip "Artgen server" → "Generative Art server"; remove `_HIDDEN_GENERATORS` hardcode (use manifest flag instead)
- `app/main_window.py` — change `label="🎨 Art"` → `label="🎨 Generative Art"` and any other user-visible "artgen" strings; update `_SOURCE_TO_CAP` if needed
- `tt-ctl` — update `artgen` subcommand description string from "ArtGen" to "Generative Art"; add `plugin list`, `mcp-config` subcommands
- `docs/index.html` — rename "Artgen" → "Generative Art" in all visible text (not CSS class names or asset paths which are internal)

**Not renamed (internal identifiers, too costly now):**
- Python class names (`ArtGenerator`, `ArtgenPanel`, `ArtgenGallery`, etc.)
- CSS class names (`.artgen-*`)
- Python module names (`artgen/`, `artgen_panel.py`, etc.)
- Internal string keys (`"artgen"` in `_SOURCE_TO_CAP`, stack names, etc.)
- Asset paths (`assets/artgen/`)

---

## Task 1: Bootstrap — `plugin_loader.py` with discovery and `PluginDef`

**Files:**
- Create: `app/plugin_loader.py`
- Create: `tests/test_plugin_loader.py`

- [ ] **Step 1: Write failing tests for plugin discovery**

```python
# tests/test_plugin_loader.py
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/ttuser/code/tt-local-generator
/usr/bin/python3 -m pytest tests/test_plugin_loader.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'plugin_loader'`

- [ ] **Step 3: Write `app/plugin_loader.py`**

```python
# app/plugin_loader.py
"""
Plugin loader — discovers generator plugins from the filesystem.

Search paths (in order, later entries override earlier on name collision):
  1. <repo_root>/plugins/
  2. ~/.config/tt-local-gen/plugins/  (user-installed)

Each plugin directory must contain mcp.json.  Optionally contains plugin.py
with an ArtGenerator subclass (local plugin).  Without plugin.py but with
x-ttlg.mcp_server declared, a McpDelegateGenerator is instantiated.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from artgen import ArtGenerator

_LOG = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_USER_DIR = Path.home() / ".config" / "tt-local-gen" / "plugins"

# Mutable for test injection via monkeypatch
_SEARCH_PATHS: list[Path] = [_REPO_ROOT / "plugins", _USER_DIR]


@dataclass
class PluginDef:
    path: Path
    manifest: dict
    name: str
    tools: list[dict]
    generator: "ArtGenerator"
    accepts_remix_from: tuple[str, ...] = field(default_factory=tuple)
    can_remix_to: tuple[str, ...] = field(default_factory=tuple)


_PLUGINS: dict[str, PluginDef] = {}


def load_plugins() -> None:
    """Scan all search paths and populate _PLUGINS. Later paths override earlier."""
    for search_path in _SEARCH_PATHS:
        if not search_path.is_dir():
            continue
        for plugin_dir in sorted(search_path.iterdir()):
            if not plugin_dir.is_dir():
                continue
            manifest_path = plugin_dir / "mcp.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text())
            except Exception as e:
                _LOG.warning("plugin_loader: skipping %s — bad mcp.json: %s", plugin_dir.name, e)
                continue

            tools = manifest.get("tools", [])
            if not tools:
                _LOG.warning("plugin_loader: skipping %s — no tools declared", plugin_dir.name)
                continue

            # Primary name = first artifact_tool, or first tool overall
            primary = next(
                (t for t in tools if t.get("x-ttlg", {}).get("artifact_tool", True)),
                tools[0],
            )
            name = primary["name"]

            generator = _load_generator(plugin_dir, manifest, name)
            if generator is None:
                continue

            xttlg = manifest.get("x-ttlg", {})
            _PLUGINS[name] = PluginDef(
                path=plugin_dir,
                manifest=manifest,
                name=name,
                tools=tools,
                generator=generator,
                accepts_remix_from=tuple(xttlg.get("accepts_remix_from", [])),
                can_remix_to=tuple(xttlg.get("can_remix_to", [])),
            )
            _LOG.debug("plugin_loader: loaded plugin %s from %s", name, plugin_dir)


def _load_generator(plugin_dir: Path, manifest: dict, name: str) -> "ArtGenerator | None":
    plugin_py = plugin_dir / "plugin.py"
    if plugin_py.exists():
        return _load_local_generator(plugin_py, name)
    xttlg = manifest.get("x-ttlg", {})
    if xttlg.get("mcp_server"):
        from artgen import ArtGenerator
        # Placeholder: McpDelegateGenerator implemented in Task 8
        class _Stub(ArtGenerator):
            def build_prompt(self, args): raise NotImplementedError
        stub = _Stub()
        stub.name = name
        stub.description = manifest["tools"][0].get("description", name)
        stub.output_ext = xttlg.get("output_ext", ".txt")
        return stub
    _LOG.warning("plugin_loader: skipping %s — no plugin.py and no mcp_server", name)
    return None


def _load_local_generator(plugin_py: Path, expected_name: str) -> "ArtGenerator | None":
    from artgen import ArtGenerator
    module_name = f"_plugin_{expected_name}"
    spec = importlib.util.spec_from_file_location(module_name, plugin_py)
    if spec is None or spec.loader is None:
        _LOG.warning("plugin_loader: cannot load %s", plugin_py)
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        _LOG.warning("plugin_loader: error loading %s: %s", plugin_py, e)
        return None
    for attr in vars(mod).values():
        if (
            isinstance(attr, type)
            and issubclass(attr, ArtGenerator)
            and attr is not ArtGenerator
        ):
            return attr()
    _LOG.warning("plugin_loader: no ArtGenerator subclass found in %s", plugin_py)
    return None


def all_plugins() -> list[PluginDef]:
    """Sorted list of all loaded PluginDef objects."""
    return [_PLUGINS[n] for n in sorted(_PLUGINS)]


def get(name: str) -> PluginDef:
    """Return PluginDef for name, or raise KeyError."""
    return _PLUGINS[name]


def all_names() -> list[str]:
    return sorted(_PLUGINS)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
/usr/bin/python3 -m pytest tests/test_plugin_loader.py -v
```

Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/plugin_loader.py tests/test_plugin_loader.py
git commit -m "feat(plugins): add plugin_loader — discovers plugins from plugins/ via mcp.json"
```

---

## Task 2: `RemixContext` and remix graph helpers

**Files:**
- Modify: `app/artgen/__init__.py`
- Create: `tests/test_remix_graph.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_remix_graph.py
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _setup_plugins(tmp_path, monkeypatch, specs):
    """specs: list of (name, accepts_remix_from, can_remix_to)"""
    import plugin_loader
    plugin_loader._PLUGINS.clear()
    monkeypatch.setattr(plugin_loader, "_SEARCH_PATHS", [tmp_path])

    from artgen import ArtGenerator

    for name, afrom, ato in specs:
        d = tmp_path / name
        d.mkdir(exist_ok=True)
        manifest = {
            "x-ttlg": {
                "output_ext": ".txt",
                "media_type": "text",
                "accepts_remix_from": afrom,
                "can_remix_to": ato,
                "tab": "generative-art",
                "hardware": None,
            },
            "tools": [{
                "name": name,
                "description": name,
                "inputSchema": {"type": "object", "properties": {}, "required": []},
                "examples": [],
                "x-ttlg": {"streaming": None, "artifact_tool": True},
            }],
        }
        (d / "mcp.json").write_text(json.dumps(manifest))

    plugin_loader.load_plugins()


def test_remix_targets_for_verse(tmp_path, monkeypatch):
    """remix_targets_for('verse') returns plugins accepting verse as input."""
    _setup_plugins(tmp_path, monkeypatch, [
        ("video_gen", ["verse", "palette"], []),
        ("image_gen", ["verse"], ["video"]),
        ("midi_gen", ["palette"], []),
    ])
    from artgen import remix_targets_for
    targets = remix_targets_for("verse")
    names = [p.name for p in targets]
    assert "video_gen" in names
    assert "image_gen" in names
    assert "midi_gen" not in names


def test_remix_targets_for_palette(tmp_path, monkeypatch):
    _setup_plugins(tmp_path, monkeypatch, [
        ("video_gen", ["verse", "palette"], []),
        ("midi_gen", ["palette"], []),
        ("image_gen", ["verse"], []),
    ])
    from artgen import remix_targets_for
    names = [p.name for p in remix_targets_for("palette")]
    assert "video_gen" in names
    assert "midi_gen" in names
    assert "image_gen" not in names


def test_remix_context_fields():
    from artgen import RemixContext
    record = {"id": "abc", "prompt": "a blue fox", "media_type": "verse"}
    ctx = RemixContext(
        source_record=record,
        source_type="verse",
        target_type="video",
        hint="a blue fox",
    )
    assert ctx.source_type == "verse"
    assert ctx.target_type == "video"
    assert ctx.hint == "a blue fox"


def test_extract_remix_hint_default():
    """Default hint extraction returns the prompt field from a history record."""
    from artgen import extract_remix_hint
    record = {"prompt": "misty mountains at dawn", "media_type": "verse"}
    assert extract_remix_hint(record) == "misty mountains at dawn"


def test_extract_remix_hint_missing_prompt():
    from artgen import extract_remix_hint
    record = {"media_type": "palette"}
    assert extract_remix_hint(record) == ""
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
/usr/bin/python3 -m pytest tests/test_remix_graph.py -v 2>&1 | head -20
```

Expected: `ImportError` — `remix_targets_for` not yet defined.

- [ ] **Step 3: Add `RemixContext`, `remix_targets_for`, `extract_remix_hint` to `app/artgen/__init__.py`**

Add after the `ArtGenerator` class definition and before the `# ── Registry` section:

```python
# ── Remix support ─────────────────────────────────────────────────────────────

from dataclasses import dataclass as _dataclass


@_dataclass
class RemixContext:
    source_record: dict
    source_type: str
    target_type: str
    hint: str


def remix_targets_for(source_type: str) -> list:
    """Return PluginDef list for plugins that accept source_type as remix input."""
    import plugin_loader
    return [
        p for p in plugin_loader.all_plugins()
        if source_type in p.accepts_remix_from
    ]


def extract_remix_hint(record: dict) -> str:
    """Default remix hint extractor — returns prompt text from a history record."""
    return record.get("prompt", "")
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_remix_graph.py -v
```

Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/artgen/__init__.py tests/test_remix_graph.py
git commit -m "feat(remix): add RemixContext, remix_targets_for, extract_remix_hint"
```

---

## Task 3: Migrate `verse` and `freeform` generators to `plugins/`

**Files:**
- Create: `plugins/verse/mcp.json`, `plugins/verse/plugin.py`
- Create: `plugins/freeform/mcp.json`, `plugins/freeform/plugin.py`
- Create: `tests/test_artgen_generators.py` (first two generator test classes)

- [ ] **Step 1: Write failing tests for verse and freeform**

```python
# tests/test_artgen_generators.py
"""Per-generator tests. Each class exercises build_prompt, parse_output,
generate_artifact with a mocked call_fn."""
import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "verse"))
sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "freeform"))


def _args(**kw):
    ns = argparse.Namespace()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _mock_call_fn(response="mock output"):
    fn = MagicMock(return_value=response)
    return fn


class TestVerseGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        from plugin import VerseGenerator
        self.g = VerseGenerator()

    def test_build_prompt_returns_string(self):
        args = _args(form="haiku", theme="winter", count=3)
        result = self.g.build_prompt(args)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_build_prompt_includes_theme(self):
        args = _args(form="haiku", theme="neon forests", count=2)
        assert "neon forests" in self.g.build_prompt(args)

    def test_parse_output_strips_fences(self):
        raw = "```\nfrost on wire\nsilver morning holds its breath\ncrows wait\n```"
        result = self.g.parse_output(raw, _args())
        assert "```" not in result
        assert "frost on wire" in result

    def test_parse_output_strips_think_blocks(self):
        raw = "<think>thinking</think>\nfrost on wire\nsilver morning"
        result = self.g.parse_output(raw, _args())
        assert "<think>" not in result
        assert "frost on wire" in result

    def test_generate_artifact_calls_call_fn(self):
        fn = _mock_call_fn("three lines\nof winter\nand silence")
        args = _args(form="haiku", theme="ice", count=1)
        result = self.g.generate_artifact(args, fn)
        fn.assert_called_once()
        assert "three lines" in result

    def test_default_output_extension(self):
        assert self.g.default_output().suffix == ".txt"

    def test_all_forms_produce_prompts(self):
        for form in ("haiku", "lore", "epitaph", "couplet"):
            args = _args(form=form, theme="test", count=1)
            prompt = self.g.build_prompt(args)
            assert len(prompt) > 10


class TestFreeformGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        from plugin import FreeformGenerator
        self.g = FreeformGenerator()

    def test_build_prompt_includes_freeform_text(self):
        args = _args(freeform="a robot weeping in the rain", output=None)
        result = self.g.build_prompt(args)
        assert "a robot weeping in the rain" in result

    def test_build_prompt_raises_on_empty(self):
        args = _args(freeform="", output=None)
        with pytest.raises(ValueError, match="--freeform"):
            self.g.build_prompt(args)

    def test_parse_output_svg_extracted(self):
        raw = "Here is your SVG:\n<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>"
        args = _args(output="out.svg")
        result = self.g.parse_output(raw, args)
        assert result.startswith("<svg")

    def test_parse_output_strips_fences_for_txt(self):
        raw = "```\nhello world\n```"
        args = _args(output="out.txt")
        result = self.g.parse_output(raw, args)
        assert "hello world" in result
        assert "```" not in result

    def test_generate_artifact_calls_call_fn(self):
        fn = _mock_call_fn("some output text")
        args = _args(freeform="draw something", output=None)
        result = self.g.generate_artifact(args, fn)
        fn.assert_called_once()
        assert "some output text" in result
```

- [ ] **Step 2: Run to confirm failure**

```bash
/usr/bin/python3 -m pytest tests/test_artgen_generators.py::TestVerseGenerator tests/test_artgen_generators.py::TestFreeformGenerator -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError` — plugins don't exist yet.

- [ ] **Step 3: Create `plugins/verse/` directory and files**

```bash
mkdir -p /home/ttuser/code/tt-local-generator/plugins/verse
```

Create `plugins/verse/mcp.json`:
```json
{
  "x-ttlg": {
    "output_ext": ".txt",
    "media_type": "text",
    "accepts_remix_from": ["palette"],
    "can_remix_to": ["video", "image"],
    "tab": "generative-art",
    "hardware": null
  },
  "tools": [
    {
      "name": "verse",
      "description": "Structured text art: haiku sequences, lore fragments, epitaphs, and rhyming couplets",
      "inputSchema": {
        "type": "object",
        "properties": {
          "form": {
            "type": "string",
            "enum": ["haiku", "lore", "epitaph", "couplet"],
            "default": "haiku",
            "description": "Verse form"
          },
          "theme": {
            "type": "string",
            "default": "the passage of time",
            "description": "Thematic seed"
          },
          "count": {
            "type": "integer",
            "default": 3,
            "description": "Number of verses to generate"
          }
        },
        "required": []
      },
      "examples": [
        {"form": "haiku", "theme": "winter forges"},
        {"form": "lore", "theme": "abandoned space station"},
        {"form": "epitaph", "theme": "a forgotten compiler"},
        {"form": "couplet", "theme": "electric dreams"}
      ],
      "x-ttlg": {
        "streaming": null,
        "artifact_tool": true
      }
    }
  ]
}
```

Copy the verse generator to plugin.py (drop `@register` decorator):

```bash
cp /home/ttuser/code/tt-local-generator/app/artgen/generators/verse.py \
   /home/ttuser/code/tt-local-generator/plugins/verse/plugin.py
```

Then edit `plugins/verse/plugin.py` to remove `@register` from above `class VerseGenerator`:

The file currently has `@register` on line 74. Change:
```python
@register
class VerseGenerator(ArtGenerator):
```
to:
```python
class VerseGenerator(ArtGenerator):
```

- [ ] **Step 4: Create `plugins/freeform/` directory and files**

```bash
mkdir -p /home/ttuser/code/tt-local-generator/plugins/freeform
```

Create `plugins/freeform/mcp.json`:
```json
{
  "x-ttlg": {
    "output_ext": ".txt",
    "media_type": "text",
    "accepts_remix_from": [],
    "can_remix_to": [],
    "tab": "generative-art",
    "hardware": null
  },
  "tools": [
    {
      "name": "freeform",
      "description": "Pass any prompt directly to the LLM — output format inferred from file extension (.svg, .json, .ans, .txt)",
      "inputSchema": {
        "type": "object",
        "properties": {
          "freeform": {
            "type": "string",
            "description": "Freeform prompt — describe anything you want generated"
          },
          "output": {
            "type": "string",
            "description": "Output filename — extension determines format (.svg, .json, .ans, .txt)"
          }
        },
        "required": ["freeform"]
      },
      "examples": [
        {"freeform": "a sad robot circuit diagram", "output": "robot.svg"},
        {"freeform": "a haiku about silicon", "output": "silicon.txt"}
      ],
      "x-ttlg": {
        "streaming": null,
        "artifact_tool": true
      }
    }
  ]
}
```

```bash
cp /home/ttuser/code/tt-local-generator/app/artgen/generators/freeform.py \
   /home/ttuser/code/tt-local-generator/plugins/freeform/plugin.py
```

Edit `plugins/freeform/plugin.py` — remove `@register` from above `class FreeformGenerator`.

- [ ] **Step 5: Run generator tests**

```bash
/usr/bin/python3 -m pytest tests/test_artgen_generators.py::TestVerseGenerator tests/test_artgen_generators.py::TestFreeformGenerator -v
```

Expected: all tests PASS

- [ ] **Step 6: Run full suite to confirm no regressions**

```bash
/usr/bin/python3 -m pytest tests/ -q
```

Expected: same pass count as before this task (existing tests unaffected — old generators still live in `app/artgen/generators/` during migration).

- [ ] **Step 7: Commit**

```bash
git add plugins/verse/ plugins/freeform/ tests/test_artgen_generators.py
git commit -m "feat(plugins): migrate verse and freeform generators to plugins/"
```

---

## Task 4: Migrate `palette`, `constellation`, `geometric`, `circuit`, `skyline` generators

**Files:**
- Create: `plugins/<name>/mcp.json` and `plugins/<name>/plugin.py` for each
- Modify: `tests/test_artgen_generators.py` (add test classes)

- [ ] **Step 1: Add test classes for these 5 generators**

Append to `tests/test_artgen_generators.py`:

```python
# Add these imports at top if not present:
# sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "palette"))
# etc — handled by the path inserts below

class TestPaletteGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "palette"))
        from plugin import PaletteGenerator
        self.g = PaletteGenerator()

    def test_build_prompt_includes_mood(self):
        args = _args(mood="volcanic", count=6)
        assert "volcanic" in self.g.build_prompt(args)

    def test_parse_output_returns_valid_json(self):
        raw = '{"name": "Ember", "colors": [{"hex": "#FF6600", "role": "accent"}], "lore": "Hot."}'
        result = self.g.parse_output(raw, _args())
        import json
        data = json.loads(result)
        assert data["name"] == "Ember"

    def test_parse_output_raises_on_missing_fields(self):
        raw = '{"colors": []}'
        with pytest.raises(ValueError, match="missing required fields"):
            self.g.parse_output(raw, _args())

    def test_generate_artifact_calls_call_fn(self):
        response = '{"name": "Test", "colors": [{"hex": "#000000", "role": "bg"}], "lore": "Dark."}'
        fn = _mock_call_fn(response)
        result = self.g.generate_artifact(_args(mood="test", count=1, export_css=False), fn)
        fn.assert_called_once()
        assert "Test" in result


class TestConstellationGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "constellation"))
        from plugin import ConstellationGenerator
        self.g = ConstellationGenerator()

    def test_build_prompt_returns_string(self):
        args = _args(culture="greek", stars=7, lore=False)
        result = self.g.build_prompt(args)
        assert isinstance(result, str) and len(result) > 0

    def test_default_output_is_svg(self):
        assert self.g.default_output().suffix == ".svg"

    def test_generate_artifact_calls_call_fn(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><circle r="2"/></svg>'
        fn = _mock_call_fn(svg)
        result = self.g.generate_artifact(_args(culture="greek", stars=5, lore=False), fn)
        fn.assert_called_once()


class TestGeometricGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "geometric"))
        from plugin import GeometricGenerator
        self.g = GeometricGenerator()

    def test_build_prompt_returns_string(self):
        args = _args(style="mondrian", palette="teal", complexity="low")
        result = self.g.build_prompt(args)
        assert "mondrian" in result.lower() or len(result) > 0

    def test_default_output_is_svg(self):
        assert self.g.default_output().suffix == ".svg"


class TestCircuitGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "circuit"))
        from plugin import CircuitGenerator
        self.g = CircuitGenerator()

    def test_build_prompt_returns_string(self):
        args = _args(inputs=["A", "B"], gates=["and", "or"], depth=2, style="clean")
        result = self.g.build_prompt(args)
        assert isinstance(result, str) and len(result) > 0

    def test_default_output_is_svg(self):
        assert self.g.default_output().suffix == ".svg"


class TestSkylineGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "skyline"))
        from plugin import SkylineGenerator
        self.g = SkylineGenerator()

    def test_build_prompt_returns_string(self):
        args = _args(era="retro", sky="dusk", density="medium")
        result = self.g.build_prompt(args)
        assert isinstance(result, str) and len(result) > 0

    def test_default_output_is_svg(self):
        assert self.g.default_output().suffix == ".svg"
```

- [ ] **Step 2: Run to confirm failure (plugins not yet created)**

```bash
/usr/bin/python3 -m pytest tests/test_artgen_generators.py::TestPaletteGenerator -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create plugin directories and copy generators**

```bash
for name in palette constellation geometric circuit skyline; do
  mkdir -p /home/ttuser/code/tt-local-generator/plugins/$name
  cp /home/ttuser/code/tt-local-generator/app/artgen/generators/$name.py \
     /home/ttuser/code/tt-local-generator/plugins/$name/plugin.py
done
```

- [ ] **Step 4: Remove `@register` from each `plugin.py`**

In each of the 5 `plugins/<name>/plugin.py` files, find the `@register` line immediately above the class definition and delete it. The class itself stays unchanged.

For `plugins/palette/plugin.py` — find `@register` above `class PaletteGenerator` and remove it.
For `plugins/constellation/plugin.py` — find `@register` above `class ConstellationGenerator` and remove it.
For `plugins/geometric/plugin.py` — find `@register` above `class GeometricGenerator` and remove it.
For `plugins/circuit/plugin.py` — find `@register` above `class CircuitGenerator` and remove it.
For `plugins/skyline/plugin.py` — find `@register` above `class SkylineGenerator` and remove it.

- [ ] **Step 5: Create `mcp.json` for each plugin**

Create `plugins/palette/mcp.json`:
```json
{
  "x-ttlg": {
    "output_ext": ".json",
    "media_type": "palette",
    "accepts_remix_from": [],
    "can_remix_to": ["image", "video"],
    "tab": "generative-art",
    "hardware": null
  },
  "tools": [{
    "name": "palette",
    "description": "Named color palette with evocative prose lore — outputs JSON (and optional CSS)",
    "inputSchema": {
      "type": "object",
      "properties": {
        "mood": {"type": "string", "default": "volcanic", "description": "Mood/theme seed"},
        "count": {"type": "integer", "default": 6, "description": "Number of colors"},
        "export_css": {"type": "boolean", "default": false, "description": "Also write CSS custom properties"}
      },
      "required": []
    },
    "examples": [
      {"mood": "drowned empire", "count": 6},
      {"mood": "iron winter", "count": 5},
      {"mood": "fever dream", "count": 7}
    ],
    "x-ttlg": {"streaming": null, "artifact_tool": true}
  }]
}
```

Create `plugins/constellation/mcp.json`:
```json
{
  "x-ttlg": {
    "output_ext": ".svg",
    "media_type": "image",
    "accepts_remix_from": ["verse"],
    "can_remix_to": [],
    "tab": "generative-art",
    "hardware": null
  },
  "tools": [{
    "name": "constellation",
    "description": "Star map SVG: named constellation with connecting lines and optional lore text",
    "inputSchema": {
      "type": "object",
      "properties": {
        "culture": {"type": "string", "default": "greek", "description": "Mythology tradition"},
        "stars": {"type": "integer", "default": 7, "description": "Number of stars"},
        "lore": {"type": "boolean", "default": false, "description": "Add lore text"}
      },
      "required": []
    },
    "examples": [
      {"culture": "norse", "stars": 8, "lore": true},
      {"culture": "greek", "stars": 12}
    ],
    "x-ttlg": {"streaming": null, "artifact_tool": true}
  }]
}
```

Create `plugins/geometric/mcp.json`:
```json
{
  "x-ttlg": {
    "output_ext": ".svg",
    "media_type": "image",
    "accepts_remix_from": ["palette"],
    "can_remix_to": [],
    "tab": "generative-art",
    "hardware": null
  },
  "tools": [{
    "name": "geometric",
    "description": "Abstract tiled SVG geometry: Mondrian grids, recursive subdivision, circuit traces, woven lattice",
    "inputSchema": {
      "type": "object",
      "properties": {
        "style": {"type": "string", "enum": ["mondrian", "circuit", "recursive", "weave"], "default": "mondrian"},
        "palette": {"type": "string", "enum": ["teal", "mono", "ember", "forest"], "default": "teal"},
        "complexity": {"type": "string", "enum": ["low", "high"], "default": "low"}
      },
      "required": []
    },
    "examples": [
      {"style": "mondrian", "palette": "teal"},
      {"style": "recursive", "palette": "ember", "complexity": "high"}
    ],
    "x-ttlg": {"streaming": null, "artifact_tool": true}
  }]
}
```

Create `plugins/circuit/mcp.json`:
```json
{
  "x-ttlg": {
    "output_ext": ".svg",
    "media_type": "image",
    "accepts_remix_from": [],
    "can_remix_to": [],
    "tab": "generative-art",
    "hardware": null
  },
  "tools": [{
    "name": "circuit",
    "description": "Logic gate wiring diagram SVG: AND/OR/NOT/XOR gates with labelled inputs and output",
    "inputSchema": {
      "type": "object",
      "properties": {
        "inputs": {"type": "array", "items": {"type": "string"}, "default": ["A", "B", "C"]},
        "gates": {"type": "array", "items": {"type": "string"}, "default": ["and", "or", "not"]},
        "depth": {"type": "integer", "default": 2},
        "style": {"type": "string", "enum": ["clean", "neon", "paper"], "default": "clean"}
      },
      "required": []
    },
    "examples": [
      {"inputs": ["A", "B"], "gates": ["and", "not"], "style": "neon"},
      {"inputs": ["X", "Y", "Z"], "gates": ["xor", "and"], "depth": 3}
    ],
    "x-ttlg": {"streaming": null, "artifact_tool": true}
  }]
}
```

Create `plugins/skyline/mcp.json`:
```json
{
  "x-ttlg": {
    "output_ext": ".svg",
    "media_type": "image",
    "accepts_remix_from": ["verse", "palette"],
    "can_remix_to": ["video"],
    "tab": "generative-art",
    "hardware": null
  },
  "tools": [{
    "name": "skyline",
    "description": "City skyline SVG: buildings, windows, atmospheric sky, era-appropriate silhouettes",
    "inputSchema": {
      "type": "object",
      "properties": {
        "era": {"type": "string", "default": "retro"},
        "sky": {"type": "string", "default": "dusk"},
        "density": {"type": "string", "enum": ["sparse", "medium", "dense"], "default": "medium"}
      },
      "required": []
    },
    "examples": [
      {"era": "retro", "sky": "dusk"},
      {"era": "futuristic", "sky": "night", "density": "dense"}
    ],
    "x-ttlg": {"streaming": null, "artifact_tool": true}
  }]
}
```

- [ ] **Step 6: Run generator tests**

```bash
/usr/bin/python3 -m pytest tests/test_artgen_generators.py -v
```

Expected: all tests in TestVerseGenerator, TestFreeformGenerator, TestPaletteGenerator, TestConstellationGenerator, TestGeometricGenerator, TestCircuitGenerator, TestSkylineGenerator PASS

- [ ] **Step 7: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ -q
```

Expected: same or better pass count, no regressions.

- [ ] **Step 8: Commit**

```bash
git add plugins/palette/ plugins/constellation/ plugins/geometric/ plugins/circuit/ plugins/skyline/ tests/test_artgen_generators.py
git commit -m "feat(plugins): migrate palette, constellation, geometric, circuit, skyline to plugins/"
```

---

## Task 5: Migrate `landscape` and `ansi` generators

**Files:**
- Create: `plugins/landscape/mcp.json`, `plugins/landscape/plugin.py`
- Create: `plugins/ansi/mcp.json`, `plugins/ansi/plugin.py`
- Modify: `tests/test_artgen_generators.py`

- [ ] **Step 1: Add test classes for landscape and ansi**

Append to `tests/test_artgen_generators.py`:

```python
class TestLandscapeGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "landscape"))
        from plugin import LandscapeGenerator
        self.g = LandscapeGenerator()

    def test_build_prompt_includes_palette_colors(self):
        args = _args(palette="sunset", mountains=True, clouds=False, stars=False)
        result = self.g.build_prompt(args)
        assert "#FF6B35" in result or "sunset" in result.lower()

    def test_build_prompt_random_palette_works(self):
        args = _args(palette="random", mountains=True, clouds=False, stars=False)
        result = self.g.build_prompt(args)
        assert isinstance(result, str) and len(result) > 0

    def test_parse_output_valid_svg(self):
        raw = '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450"><rect/></svg>'
        result = self.g.parse_output(raw, _args())
        assert result.startswith("<svg")

    def test_parse_output_raises_on_no_svg(self):
        with pytest.raises(ValueError, match="SVG"):
            self.g.parse_output("no svg here at all", _args())

    def test_default_output_is_svg(self):
        assert self.g.default_output().suffix == ".svg"

    def test_generate_artifact_calls_call_fn(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        fn = _mock_call_fn(svg)
        args = _args(palette="sunset", mountains=True, clouds=False, stars=False, glitch=False)
        result = self.g.generate_artifact(args, fn)
        fn.assert_called_once()
        assert result.startswith("<svg")


class TestAnsiGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "ansi"))
        from plugin import AnsiGenerator
        self.g = AnsiGenerator()

    def test_build_prompt_returns_pass1_ascii_prompt(self):
        args = _args(ansi_style="bbs", subject="a dragon", width=40, height=20)
        result = self.g.build_prompt(args)
        assert isinstance(result, str) and len(result) > 0

    def test_default_output_is_ans(self):
        assert self.g.default_output().suffix == ".ans"

    def test_generate_artifact_makes_three_llm_calls(self):
        # AnsiGenerator is multi-pass — call_fn should be called 3 times
        calls = []
        def fn(prompt, system=None, max_tokens=None):
            calls.append({"prompt": prompt, "max_tokens": max_tokens})
            if len(calls) == 1:
                return "A B C\nD E F"  # pass 1: ASCII
            if len(calls) == 2:
                return "█ ░ ▒\n▓ ▀ ▄"  # pass 2: blocks
            return "\033[38;5;51m█\033[0m \033[38;5;82m▒\033[0m"  # pass 3: color
        args = _args(ansi_style="bbs", subject="test", width=40, height=20)
        self.g.generate_artifact(args, fn)
        assert len(calls) == 3
```

- [ ] **Step 2: Run to confirm failure**

```bash
/usr/bin/python3 -m pytest tests/test_artgen_generators.py::TestLandscapeGenerator tests/test_artgen_generators.py::TestAnsiGenerator -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `plugins/landscape/`**

```bash
mkdir -p /home/ttuser/code/tt-local-generator/plugins/landscape
cp /home/ttuser/code/tt-local-generator/app/artgen/generators/landscape.py \
   /home/ttuser/code/tt-local-generator/plugins/landscape/plugin.py
```

Remove `@register` above `class LandscapeGenerator` in `plugins/landscape/plugin.py`.

Create `plugins/landscape/mcp.json`:
```json
{
  "x-ttlg": {
    "output_ext": ".svg",
    "media_type": "image",
    "accepts_remix_from": ["verse", "palette"],
    "can_remix_to": ["video", "image"],
    "tab": "generative-art",
    "hardware": null
  },
  "tools": [{
    "name": "landscape",
    "description": "Layered SVG landscape: sky gradients, mountain ridges, atmospheric glow, sun/moon, optional glitch effects",
    "inputSchema": {
      "type": "object",
      "properties": {
        "palette": {"type": "string", "enum": ["sunset", "blue", "purple", "red", "orange", "random"], "default": "random"},
        "mountains": {"type": "boolean", "default": true},
        "clouds": {"type": "boolean", "default": false},
        "stars": {"type": "boolean", "default": false},
        "glitch": {"type": "boolean", "default": false},
        "glitch_seed": {"type": "integer", "description": "Seed for reproducible glitch"}
      },
      "required": []
    },
    "examples": [
      {"palette": "sunset", "glitch": true},
      {"palette": "blue", "stars": true, "clouds": true},
      {"palette": "random", "mountains": true}
    ],
    "x-ttlg": {"streaming": null, "artifact_tool": true}
  }]
}
```

- [ ] **Step 4: Create `plugins/ansi/`**

```bash
mkdir -p /home/ttuser/code/tt-local-generator/plugins/ansi
cp /home/ttuser/code/tt-local-generator/app/artgen/generators/ansi.py \
   /home/ttuser/code/tt-local-generator/plugins/ansi/plugin.py
```

Remove `@register` above `class AnsiGenerator` in `plugins/ansi/plugin.py`.

Create `plugins/ansi/mcp.json`:
```json
{
  "x-ttlg": {
    "output_ext": ".ans",
    "media_type": "text",
    "accepts_remix_from": ["verse", "palette"],
    "can_remix_to": [],
    "tab": "generative-art",
    "hardware": null
  },
  "tools": [{
    "name": "ansi",
    "description": "ANSI block-character art via 3-pass LLM pipeline: ASCII layout → block character refinement → 256-color colorization",
    "inputSchema": {
      "type": "object",
      "properties": {
        "ansi_style": {"type": "string", "enum": ["bbs", "scene", "landscape"], "default": "bbs"},
        "subject": {"type": "string", "default": "a neon dragon"},
        "width": {"type": "integer", "default": 40},
        "height": {"type": "integer", "default": 20}
      },
      "required": []
    },
    "examples": [
      {"ansi_style": "bbs", "subject": "electric skull"},
      {"ansi_style": "scene", "subject": "moonlit forest"},
      {"ansi_style": "landscape", "subject": "volcanic crater"}
    ],
    "x-ttlg": {"streaming": null, "artifact_tool": true}
  }]
}
```

- [ ] **Step 5: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_artgen_generators.py -v
```

Expected: all tests PASS including TestLandscapeGenerator and TestAnsiGenerator

- [ ] **Step 6: Commit**

```bash
git add plugins/landscape/ plugins/ansi/ tests/test_artgen_generators.py
git commit -m "feat(plugins): migrate landscape and ansi generators to plugins/"
```

---

## Task 6: Migrate `animatediff` generator; create `midi` stub plugin

**Files:**
- Create: `plugins/animatediff/mcp.json`, `plugins/animatediff/plugin.py`
- Create: `plugins/midi/mcp.json` (delegating, no plugin.py)
- Modify: `tests/test_artgen_generators.py`

- [ ] **Step 1: Add test class for animatediff**

Append to `tests/test_artgen_generators.py`:

```python
class TestAnimateDiffGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "animatediff"))
        from plugin import AnimateDiffGenerator
        self.g = AnimateDiffGenerator()

    def test_build_prompt_raises(self):
        """AnimateDiff bypasses the LLM pipeline — build_prompt must raise."""
        with pytest.raises(RuntimeError, match="does not use build_prompt"):
            self.g.build_prompt(_args())

    def test_default_output_is_gif(self):
        assert self.g.default_output().suffix == ".gif"

    def test_name_is_animatediff(self):
        assert self.g.name == "animatediff"
```

- [ ] **Step 2: Run to confirm failure**

```bash
/usr/bin/python3 -m pytest tests/test_artgen_generators.py::TestAnimateDiffGenerator -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `plugins/animatediff/`**

```bash
mkdir -p /home/ttuser/code/tt-local-generator/plugins/animatediff
cp /home/ttuser/code/tt-local-generator/app/artgen/generators/animatediff.py \
   /home/ttuser/code/tt-local-generator/plugins/animatediff/plugin.py
```

Remove `@register` above `class AnimateDiffGenerator` in `plugins/animatediff/plugin.py`.

Create `plugins/animatediff/mcp.json`:
```json
{
  "x-ttlg": {
    "output_ext": ".gif",
    "media_type": "gif",
    "accepts_remix_from": ["image"],
    "can_remix_to": ["video"],
    "tab": "generative-art",
    "hardware": "blackhole"
  },
  "tools": [{
    "name": "animatediff",
    "description": "Blackhole-accelerated animated GIF via TTNN UNet with cross-frame temporal attention (requires Blackhole hardware)",
    "inputSchema": {
      "type": "object",
      "properties": {
        "prompt": {"type": "string", "description": "Generation prompt"},
        "negative_prompt": {"type": "string", "default": ""},
        "steps": {"type": "integer", "default": 20},
        "frames": {"type": "integer", "default": 16},
        "temporal_alpha": {"type": "number", "default": 0.5}
      },
      "required": ["prompt"]
    },
    "examples": [
      {"prompt": "a cyberpunk city at night, rain, neon reflections", "frames": 16},
      {"prompt": "slow motion waterfall in a crystal cave", "steps": 25}
    ],
    "x-ttlg": {"streaming": "progress", "artifact_tool": true}
  }]
}
```

- [ ] **Step 4: Create `plugins/midi/` (delegating plugin, no plugin.py)**

```bash
mkdir -p /home/ttuser/code/tt-local-generator/plugins/midi
```

Create `plugins/midi/mcp.json`:
```json
{
  "x-ttlg": {
    "output_ext": ".mid",
    "media_type": "midi",
    "accepts_remix_from": ["verse", "palette"],
    "can_remix_to": [],
    "tab": "generative-art",
    "hardware": null,
    "mcp_server": {
      "command": "npx",
      "args": ["-y", "tt-midi-maker"]
    }
  },
  "tools": [
    {
      "name": "generate_midi",
      "description": "Generate a complete MIDI file from a text prompt, verse, or palette hint",
      "inputSchema": {
        "type": "object",
        "properties": {
          "prompt": {"type": "string", "description": "Musical description or mood"},
          "tempo": {"type": "integer", "default": 120, "description": "BPM"},
          "bars": {"type": "integer", "default": 16, "description": "Number of bars"}
        },
        "required": ["prompt"]
      },
      "examples": [
        {"prompt": "melancholic late-night jazz, muted trumpet", "tempo": 80, "bars": 32},
        {"prompt": "volcanic percussion, tribal rhythm", "tempo": 140}
      ],
      "x-ttlg": {"streaming": "progress", "artifact_tool": true}
    },
    {
      "name": "stream_midi",
      "description": "Open a continuous live MIDI event stream — runs until cancelled",
      "inputSchema": {
        "type": "object",
        "properties": {
          "prompt": {"type": "string", "description": "Musical description or mood"},
          "tempo": {"type": "integer", "default": 120}
        },
        "required": ["prompt"]
      },
      "examples": [
        {"prompt": "ambient generative drone, slowly evolving", "tempo": 60}
      ],
      "x-ttlg": {"streaming": "continuous", "artifact_tool": false}
    }
  ]
}
```

- [ ] **Step 5: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_artgen_generators.py -v
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add plugins/animatediff/ plugins/midi/ tests/test_artgen_generators.py
git commit -m "feat(plugins): migrate animatediff, add midi delegating plugin stub"
```

---

## Task 7: Wire plugin_loader into artgen — replace `_load_generators()`

**Files:**
- Modify: `app/artgen/__init__.py`
- Modify: `app/artgen/generators/__init__.py`

- [ ] **Step 1: Update `_load_generators()` in `app/artgen/__init__.py`**

Replace the current `_load_generators()` function and its call at the bottom of the file:

Current:
```python
def _load_generators() -> None:
    from artgen.generators import (  # noqa: F401
        landscape, skyline, constellation, geometric,
        ansi, palette, verse, circuit, freeform, animatediff,
    )


_load_generators()
```

Replace with:
```python
def _load_generators() -> None:
    import plugin_loader
    plugin_loader.load_plugins()
    # Back-fill the old _GENERATORS registry so existing code using artgen.get()
    # and artgen.all_names() continues to work during the transition.
    for name, pdef in plugin_loader._PLUGINS.items():
        _GENERATORS[name] = pdef.generator


_load_generators()
```

- [ ] **Step 2: Run the full test suite**

```bash
/usr/bin/python3 -m pytest tests/ -q
```

Expected: all existing tests pass. The plugin loader now drives generator discovery.

- [ ] **Step 3: Verify CLI still works (smoke test)**

```bash
cd /home/ttuser/code/tt-local-generator
/usr/bin/python3 tt-ctl artgen --help 2>&1 | head -20
```

Expected: help text lists all generator subcommands (landscape, verse, ansi, etc.)

- [ ] **Step 4: Commit**

```bash
git add app/artgen/__init__.py
git commit -m "feat(plugins): wire plugin_loader into artgen._load_generators — plugin-driven registry"
```

---

## Task 8: MCP server endpoint (`app/mcp_server.py`)

**Files:**
- Create: `app/mcp_server.py`
- Create: `tests/test_mcp_server.py`

- [ ] **Step 1: Write failing MCP server tests**

```python
# tests/test_mcp_server.py
"""Tests for the MCP server endpoint served by app/mcp_server.py."""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """FastAPI test client with two fake plugins loaded."""
    from fastapi.testclient import TestClient
    import plugin_loader

    # Build two minimal PluginDef stubs
    from artgen import ArtGenerator

    class _FakeGen(ArtGenerator):
        name = "fake"
        description = "Fake generator for tests"
        output_ext = ".txt"
        def build_prompt(self, args): return "prompt"

    from plugin_loader import PluginDef
    plugin_loader._PLUGINS.clear()
    plugin_loader._PLUGINS["fake"] = PluginDef(
        path=tmp_path / "fake",
        manifest={
            "x-ttlg": {"output_ext": ".txt", "media_type": "text",
                        "accepts_remix_from": [], "can_remix_to": [],
                        "tab": "generative-art", "hardware": None},
            "tools": [{
                "name": "fake",
                "description": "Fake generator for tests",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
                "examples": [],
                "x-ttlg": {"streaming": None, "artifact_tool": True},
            }],
        },
        name="fake",
        tools=[{
            "name": "fake",
            "description": "Fake generator for tests",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
            "examples": [],
            "x-ttlg": {"streaming": None, "artifact_tool": True},
        }],
        generator=_FakeGen(),
        accepts_remix_from=(),
        can_remix_to=(),
    )

    import importlib
    import mcp_server
    importlib.reload(mcp_server)
    return TestClient(mcp_server.app)


def test_tools_list_returns_all_plugins(client):
    """POST /mcp with tools/list returns all loaded plugins as tools."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    tools = data["result"]["tools"]
    names = [t["name"] for t in tools]
    assert "fake" in names


def test_initialize_returns_server_info(client):
    """POST /mcp with initialize returns server name and protocol version."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2024-11-05", "capabilities": {}}}
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert "serverInfo" in result
    assert result["serverInfo"]["name"] == "tt-local-gen"


def test_tools_call_routes_to_generator(client, monkeypatch):
    """tools/call dispatches to the correct generator's generate_artifact."""
    import plugin_loader
    gen_mock = MagicMock(return_value="generated output")
    plugin_loader._PLUGINS["fake"].generator.generate_artifact = gen_mock

    payload = {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "fake", "arguments": {}}
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    gen_mock.assert_called_once()


def test_tools_call_unknown_tool_returns_error(client):
    """tools/call for unknown tool returns JSON-RPC error."""
    payload = {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "nonexistent", "arguments": {}}
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data


def test_get_mcp_returns_manifest(client):
    """GET /mcp returns the server manifest JSON."""
    resp = client.get("/mcp")
    assert resp.status_code == 200
    data = resp.json()
    assert "tools" in data
```

- [ ] **Step 2: Run to confirm failure**

```bash
/usr/bin/python3 -m pytest tests/test_mcp_server.py -v 2>&1 | head -15
```

Expected: `ModuleNotFoundError: No module named 'mcp_server'`

- [ ] **Step 3: Write `app/mcp_server.py`**

```python
# app/mcp_server.py
"""
MCP server — exposes all loaded plugins as MCP tools over HTTP+SSE.

Protocol: MCP over HTTP (JSON-RPC 2.0) — standard MCP client compatible.
Port: 8003 (configurable via TTLG_MCP_PORT env var).

Start standalone:
    python3 app/mcp_server.py
Or via tt-ctl:
    tt-ctl mcp-server start

Claude Code integration:
    tt-ctl mcp-config >> ~/.claude/mcp.json
"""
from __future__ import annotations

import argparse
import os
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

import plugin_loader

app = FastAPI(title="tt-local-gen MCP server", version="1.0.0")

_SERVER_INFO = {
    "name": "tt-local-gen",
    "version": "1.0.0",
}
_PROTOCOL_VERSION = "2024-11-05"


def _all_tools() -> list[dict]:
    """Build MCP tools/list payload from all loaded plugins."""
    tools = []
    for pdef in plugin_loader.all_plugins():
        for tool in pdef.tools:
            tools.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "inputSchema": tool.get("inputSchema", {"type": "object", "properties": {}}),
            })
    return tools


@app.get("/mcp")
async def get_manifest():
    """Return MCP server manifest — lists all tools."""
    return {"tools": _all_tools(), "serverInfo": _SERVER_INFO}


@app.post("/mcp")
async def handle_rpc(body: dict) -> JSONResponse:
    """Handle JSON-RPC 2.0 MCP messages."""
    rpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": _SERVER_INFO,
            },
        })

    if method == "tools/list":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {"tools": _all_tools()},
        })

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        try:
            pdef = plugin_loader.get(tool_name)
        except KeyError:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32601, "message": f"Tool not found: {tool_name}"},
            })
        try:
            import argparse as _ap
            args = _ap.Namespace(**arguments)
            result = pdef.generator.generate_artifact(args, _noop_call_fn)
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "content": [{"type": "text", "text": str(result)}],
                    "isError": False,
                },
            })
        except Exception as e:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "content": [{"type": "text", "text": str(e)}],
                    "isError": True,
                },
            })

    return JSONResponse({
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    })


def _noop_call_fn(prompt: str, system=None, max_tokens=None) -> str:
    """Placeholder call_fn used when MCP server is invoked without a live LLM.
    Real invocations wire in the artgen LLM endpoint via the plugin's call context."""
    raise RuntimeError(
        "This plugin requires a live LLM or MCP server — start the appropriate server first."
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("TTLG_MCP_PORT", "8003"))
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=port)
    args = parser.parse_args()
    uvicorn.run(app, host="0.0.0.0", port=args.port)
```

- [ ] **Step 4: Run MCP server tests**

```bash
/usr/bin/python3 -m pytest tests/test_mcp_server.py -v
```

Expected: all 5 tests PASS

- [ ] **Step 5: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ -q
```

Expected: no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add MCP server endpoint serving all plugins as tools on port 8003"
```

---

## Task 9: Add `tt-ctl plugin list` and `tt-ctl mcp-config` subcommands

**Files:**
- Modify: `tt-ctl`

- [ ] **Step 1: Find where to add the new subcommands in `tt-ctl`**

The subcommand registration is near line 996 in `tt-ctl`:
```python
# artgen
_build_artgen_parser(sub)
```

And the dispatch table is around line 1083:
```python
"artgen":           cmd_artgen,
```

- [ ] **Step 2: Add `cmd_plugin_list` and `cmd_mcp_config` functions to `tt-ctl`**

Find the block that imports from `artgen.cli` (around line 75) and after it add:

```python
def cmd_plugin_list(args):
    """List all loaded plugins."""
    import plugin_loader
    plugin_loader.load_plugins()
    plugins = plugin_loader.all_plugins()
    if not plugins:
        print("No plugins loaded.")
        return
    print(f"{'NAME':<20} {'MEDIA':<10} {'TAB':<16} {'HARDWARE':<12} {'TOOLS'}")
    print("-" * 72)
    for p in plugins:
        xttlg = p.manifest.get("x-ttlg", {})
        hw = xttlg.get("hardware") or "—"
        media = xttlg.get("media_type", "—")
        tab = xttlg.get("tab", "—")
        tool_names = ", ".join(t["name"] for t in p.tools)
        print(f"{p.name:<20} {media:<10} {tab:<16} {hw:<12} {tool_names}")


def cmd_mcp_config(args):
    """Emit Claude Code MCP config JSON for tt-local-gen."""
    import json
    port = int(os.environ.get("TTLG_MCP_PORT", "8003"))
    config = {"tt-local-gen": {"url": f"http://localhost:{port}/mcp"}}
    print(json.dumps(config, indent=2))
    print("\n# Append to ~/.claude/mcp.json or run:")
    print(f"#   tt-ctl mcp-config >> ~/.claude/mcp.json")
```

- [ ] **Step 3: Register the new subcommands in `_build_subparsers()`**

After the `_build_artgen_parser(sub)` call, add:

```python
# plugin management
plugin_p = sub.add_parser("plugin", help="Plugin management")
plugin_sub = plugin_p.add_subparsers(dest="plugin_cmd")
plugin_sub.add_parser("list", help="List all loaded plugins")

# MCP config
sub.add_parser("mcp-config", help="Emit Claude Code MCP config JSON for tt-local-gen")
```

- [ ] **Step 4: Add to dispatch table**

In the dispatch dict (around line 1083), add:

```python
"mcp-config":       cmd_mcp_config,
```

And handle the `plugin` subcommand:

```python
"plugin":           lambda args: cmd_plugin_list(args) if getattr(args, "plugin_cmd", None) == "list" else None,
```

- [ ] **Step 5: Smoke test the new commands**

```bash
cd /home/ttuser/code/tt-local-generator
/usr/bin/python3 tt-ctl plugin list
/usr/bin/python3 tt-ctl mcp-config
```

Expected: `plugin list` prints a table of all plugins; `mcp-config` prints JSON config.

- [ ] **Step 6: Run full test suite**

```bash
/usr/bin/python3 -m pytest tests/ -q
```

Expected: no regressions.

- [ ] **Step 7: Commit**

```bash
git add tt-ctl
git commit -m "feat(cli): add 'tt-ctl plugin list' and 'tt-ctl mcp-config' subcommands"
```

---

## Task 10: Rename "ArtGen" → "Generative Art" in all user-facing strings

**Files:**
- Modify: `app/main_window.py` — tab button label
- Modify: `app/artgen_panel.py` — tooltip and section label strings
- Modify: `tt-ctl` — `artgen` subcommand help description
- Modify: `docs/index.html` — visible website text (not CSS classes or asset paths)

- [ ] **Step 1: Update `app/main_window.py` — source button label**

Find line 3646:
```python
self._src_art_btn = Gtk.ToggleButton(label="🎨 Art")
```
Change to:
```python
self._src_art_btn = Gtk.ToggleButton(label="🎨 Generative Art")
```

- [ ] **Step 2: Update `app/artgen_panel.py` — tooltip and section label**

Find line 299:
```python
srv_menu_btn.set_tooltip_text("Artgen server controls (model, start/stop, health)")
```
Change to:
```python
srv_menu_btn.set_tooltip_text("Generative Art server controls (model, start/stop, health)")
```

Find the `_section_lbl("artgen server")` call (around line 263):
```python
srv_pop_content.append(_section_lbl("artgen server"))
```
Change to:
```python
srv_pop_content.append(_section_lbl("Generative Art server"))
```

- [ ] **Step 3: Update `tt-ctl` — subcommand description**

Find the artgen subcommand description string in `tt-ctl` (around line 35):
```
tt-ctl artgen                   Generate art artifacts via LLM — SVG landscapes,
```

Update the help string for the `artgen` subparser to read:
```
Generate generative art artifacts via LLM — SVG landscapes,
```

And update the epilog/description in `_build_artgen_parser` to say "Generative Art" instead of "ArtGen" or "artgen" where it appears in user-visible help text.

- [ ] **Step 4: Update `docs/index.html` — visible text only**

Make the following targeted substitutions in `docs/index.html` (visible text strings, not CSS classes, IDs, or asset paths):

1. Line 826: `<li><a href="#artgen">Artgen</a></li>` → `<li><a href="#artgen">Generative Art</a></li>`
2. Line 1040: `<div class="model-type artgen">Artgen · Animated GIF</div>` → `<div class="model-type artgen">Generative Art · Animated GIF</div>`
3. Line 1053: `<h2 class="section-title">Artgen — generative art, entirely on-device.</h2>` → `<h2 class="section-title">Generative Art — entirely on-device.</h2>`
4. Line 1061: `<strong>Works from day one.</strong> Artgen automatically` → `<strong>Works from day one.</strong> The Generative Art tab automatically`
5. Line 1062: `a dedicated artgen model` → `a dedicated Generative Art model`

Do NOT change:
- `id="artgen"` anchor (would break the nav link)
- `class="model-type artgen"` (CSS class)
- `assets/artgen/` paths (would break image loading)
- `og:image:alt` and twitter meta tags (lower priority, update if time)

- [ ] **Step 5: Run full test suite**

```bash
/usr/bin/python3 -m pytest tests/ -q
```

Expected: all tests pass. UI string changes don't affect test behavior.

- [ ] **Step 6: Commit**

```bash
git add app/main_window.py app/artgen_panel.py tt-ctl docs/index.html
git commit -m "feat(ux): rename ArtGen → Generative Art in all user-facing strings"
```

---

## Task 11: Clean up old generator imports and run full validation

**Files:**
- Modify: `app/artgen/generators/__init__.py`
- Modify: `app/artgen/__init__.py` (remove legacy `@register` support note)

- [ ] **Step 1: Empty `app/artgen/generators/__init__.py`**

The file currently imports all generators. Now that plugin_loader drives discovery, these imports are redundant. Replace the file contents with:

```python
# Generators have moved to plugins/<name>/plugin.py
# Discovery is handled by plugin_loader — see app/plugin_loader.py
```

- [ ] **Step 2: Run full test suite**

```bash
/usr/bin/python3 -m pytest tests/ -q
```

Expected: all tests pass — the old `app/artgen/generators/` classes are no longer imported, but that's fine since tests now import directly from `plugins/<name>/plugin.py`.

- [ ] **Step 3: Verify CLI smoke test**

```bash
/usr/bin/python3 tt-ctl artgen verse --form haiku --theme "silicon dreams" --simulate
/usr/bin/python3 tt-ctl plugin list
```

Expected: verse prompt printed (simulate mode); plugin list shows all migrated generators.

- [ ] **Step 4: Verify all plugins load without errors**

```bash
/usr/bin/python3 -c "
import sys; sys.path.insert(0, 'app')
import plugin_loader
plugin_loader.load_plugins()
for name, p in sorted(plugin_loader._PLUGINS.items()):
    print(f'  {name:<20} {type(p.generator).__name__}')
"
```

Expected: all 13+ plugins listed with their generator class names.

- [ ] **Step 5: Commit**

```bash
git add app/artgen/generators/__init__.py app/artgen/__init__.py
git commit -m "chore(plugins): empty legacy generator imports — discovery now via plugin_loader"
```

---

## Self-Review

**Spec coverage check:**

| Spec section | Covered by task |
|---|---|
| §1 Plugin directory structure | Tasks 3–6 (create `plugins/<name>/`) |
| §2 `mcp.json` manifest format | Tasks 3–6 (all `mcp.json` files written with full schema) |
| §2 Streaming contracts declared | Task 6 (animatediff `"progress"`, midi `"progress"` + `"continuous"`) |
| §3 Plugin loader discovery | Task 1 |
| §3 `PluginDef` dataclass | Task 1 |
| §3 `accepts_remix_from`/`can_remix_to` | Task 1 (loader) + Task 2 (artgen) |
| §3 `McpDelegateGenerator` stub | Task 1 (stub placeholder — full impl deferred as open question) |
| §4 MCP server endpoint | Task 8 |
| §4 `tt-ctl mcp-config` | Task 9 |
| §4 `tt-ctl plugin list` | Task 9 |
| §5 `RemixContext` dataclass | Task 2 |
| §5 `remix_targets_for()` | Task 2 |
| §5 `extract_remix_hint()` | Task 2 |
| §6 Migration path (all 13 generators) | Tasks 3–6 |
| §6 Gate rule (tests before migration) | Tasks 3–6 (TDD throughout) |
| §7 Test plan — loader tests | Task 1 |
| §7 Test plan — generator tests | Tasks 3–6 |
| §7 Test plan — MCP server tests | Task 8 |
| §7 Test plan — remix graph tests | Task 2 |
| §8 Terminology rename | Task 10 |

**Deferred (open questions in spec):** `McpDelegateGenerator` full implementation (SSE forwarding for streaming tools) — the stub in Task 1 satisfies the loader contract; the full MCP wire protocol for delegating is a follow-up task once tt-midi-maker is ready to connect.

**No placeholders found.** All test code, implementation code, and commands are fully written out.

**Type consistency verified:** `PluginDef`, `ArtGenerator`, `RemixContext`, `remix_targets_for`, `extract_remix_hint`, `plugin_loader._PLUGINS`, `plugin_loader.all_plugins()`, `plugin_loader.get()` — all consistent across Tasks 1–11.
