# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Plugin loader — discovers generator plugins from the filesystem.

Search paths (in order, later entries override earlier on name collision):
  1. <repo_root>/plugins/
  2. ~/.config/tt-local-gen/plugins/  (user-installed)

Each plugin directory must contain mcp.json.  Optionally contains plugin.py
with an ArtGenerator subclass (local plugin).  Without plugin.py but with
x-ttlg.mcp_server declared, a McpDelegateGenerator stub is instantiated
(full implementation pending Task 8).

Plugin format (mcp.json top-level keys):
  - x-ttlg         : plugin-level metadata (output_ext, media_type,
                     accepts_remix_from, can_remix_to, tab, hardware,
                     mcp_server)
  - tools          : list of MCP tool definitions; the first tool with
                     artifact_tool=True (or the first tool overall) names
                     the plugin and is its primary entry point

Loading behaviour:
  - Plugins whose mcp.json is missing or unparseable are skipped with a
    warning logged at WARNING level.
  - Plugins with no tools declared are also skipped.
  - Plugins with neither a plugin.py ArtGenerator subclass nor an
    mcp_server declaration are skipped (no generator to run).
  - Later search paths override earlier ones when names collide, enabling
    user-installed plugins to shadow repo-bundled ones.
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

# Mutable list so tests can override via monkeypatch.setattr.
# Entries are processed left-to-right; later entries win on name collision.
_SEARCH_PATHS: list[Path] = [_REPO_ROOT / "plugins", _USER_DIR]


@dataclass
class PluginDef:
    """
    Fully-resolved descriptor for a discovered plugin.

    Attributes:
        path              : absolute Path to the plugin directory
        manifest          : parsed mcp.json contents
        name              : canonical plugin name (from first artifact_tool)
        tools             : list of MCP tool dicts from mcp.json
        generator         : instantiated ArtGenerator for this plugin
        accepts_remix_from: artifact types this plugin can accept as remix input
        can_remix_to      : artifact types this plugin's output can be remixed into
    """

    path: Path
    manifest: dict
    name: str
    tools: list[dict]
    generator: "ArtGenerator"
    accepts_remix_from: tuple[str, ...] = field(default_factory=tuple)
    can_remix_to: tuple[str, ...] = field(default_factory=tuple)
    runnable: bool = True  # False for MCP-server stubs not yet delegating


# Module-level registry populated by load_plugins().
# Key = plugin name; value = PluginDef.
_PLUGINS: dict[str, PluginDef] = {}


# ── Public API ────────────────────────────────────────────────────────────────


def load_plugins() -> None:
    """
    Scan all search paths and populate _PLUGINS.

    Iterates _SEARCH_PATHS left-to-right.  Later paths override earlier ones
    when the same plugin name is discovered twice, so user plugins shadow
    repo-bundled plugins.

    Idempotent: clears and rebuilds each time it is called so that plugins
    removed from the search paths (or left over from monkeypatched test paths)
    do not remain registered after a later load.
    """
    _PLUGINS.clear()
    for search_path in _SEARCH_PATHS:
        if not search_path.is_dir():
            continue
        for plugin_dir in sorted(search_path.iterdir()):
            if not plugin_dir.is_dir():
                continue
            manifest_path = plugin_dir / "mcp.json"
            if not manifest_path.exists():
                continue

            # Parse manifest — skip on any JSON error
            try:
                manifest = json.loads(manifest_path.read_text())
            except Exception as exc:
                _LOG.warning(
                    "plugin_loader: skipping %s — bad mcp.json: %s",
                    plugin_dir.name, exc,
                )
                continue

            tools = manifest.get("tools", [])
            if not tools:
                _LOG.warning(
                    "plugin_loader: skipping %s — no tools declared",
                    plugin_dir.name,
                )
                continue

            # Primary name = first tool with artifact_tool=True, or first tool overall.
            # Default is False: tools that don't explicitly declare artifact_tool=True
            # should not be treated as the primary generator — they must opt in.
            primary = next(
                (t for t in tools if t.get("x-ttlg", {}).get("artifact_tool", False)),
                tools[0],
            )
            name = primary["name"]
            xttlg = manifest.get("x-ttlg", {})

            # Utility plugins (ffmpeg etc.) are not generators — skip registration.
            if xttlg.get("utility"):
                _LOG.debug("plugin_loader: skipping utility plugin %s", plugin_dir.name)
                continue

            # Resolve a runnable generator instance or skip
            generator = _load_generator(plugin_dir, manifest, name)
            if generator is None:
                continue
            _PLUGINS[name] = PluginDef(
                path=plugin_dir,
                manifest=manifest,
                name=name,
                tools=tools,
                generator=generator,
                accepts_remix_from=tuple(xttlg.get("accepts_remix_from", [])),
                can_remix_to=tuple(xttlg.get("can_remix_to", [])),
                runnable=not getattr(generator, "_is_mcp_stub", False),
            )
            _LOG.debug("plugin_loader: loaded plugin %s from %s", name, plugin_dir)


def all_plugins() -> list[PluginDef]:
    """Return a sorted list of all loaded PluginDef objects."""
    return [_PLUGINS[n] for n in sorted(_PLUGINS)]


def get(name: str) -> PluginDef:
    """Return the PluginDef for *name*, or raise KeyError if not found."""
    return _PLUGINS[name]


def all_names() -> list[str]:
    """Return a sorted list of all loaded plugin names."""
    return sorted(_PLUGINS)


# ── Generator resolution ─────────────────────────────────────────────────────


def _load_generator(
    plugin_dir: Path, manifest: dict, name: str
) -> "ArtGenerator | None":
    """
    Resolve the ArtGenerator instance for a plugin directory.

    Resolution order:
      1. plugin.py present → dynamically import and find ArtGenerator subclass
      2. x-ttlg.mcp_server declared → return a lightweight MCP stub
         (full McpDelegateGenerator implemented in Task 8)
      3. Neither → skip (return None); the plugin is not runnable and must
         not appear in the generator picker or MCP tool list.
    """
    plugin_py = plugin_dir / "plugin.py"
    if plugin_py.exists():
        return _load_local_generator(plugin_py, name)

    xttlg = manifest.get("x-ttlg", {})
    if xttlg.get("mcp_server"):
        # Declared MCP-server-backed plugin — stub that delegates at runtime.
        return _make_mcp_stub(manifest, name)

    # No plugin.py and no mcp_server — not runnable; skip entirely.
    _LOG.debug("plugin_loader: skipping %s — no plugin.py and no mcp_server", name)
    return None


def _make_mcp_stub(manifest: dict, name: str) -> "ArtGenerator":
    """
    Create a minimal ArtGenerator stub for MCP-server-backed plugins.

    The stub satisfies the ArtGenerator interface so PluginDef can be
    constructed.  It will be replaced by McpDelegateGenerator in Task 8;
    until then, calling generate_artifact() raises NotImplementedError.
    """
    from artgen import ArtGenerator

    xttlg = manifest.get("x-ttlg", {})
    description = manifest["tools"][0].get("description", name)
    output_ext = xttlg.get("output_ext", ".txt")

    class _McpStub(ArtGenerator):
        """Placeholder for a remote MCP-server plugin (Task 8 will replace this)."""

        def build_prompt(self, args):  # noqa: D102
            raise NotImplementedError(
                f"Plugin '{name}' is backed by an MCP server; "
                "McpDelegateGenerator not yet implemented."
            )

    stub = _McpStub()
    stub.name = name
    stub.description = description
    stub.output_ext = output_ext
    stub._is_mcp_stub = True  # signals PluginDef.runnable = False
    return stub


def _load_local_generator(
    plugin_py: Path, expected_name: str
) -> "ArtGenerator | None":
    """
    Dynamically import *plugin_py* and return the first ArtGenerator subclass found.

    The module is registered in sys.modules under '_plugin_<expected_name>' so it
    survives the importlib machinery and can be reloaded cleanly in tests.

    Returns None (with a warning) if:
      - importlib cannot build a spec for the file
      - the module raises an exception on import
      - no ArtGenerator subclass is defined in the module
    """
    from artgen import ArtGenerator

    module_name = f"_plugin_{expected_name}"
    spec = importlib.util.spec_from_file_location(module_name, plugin_py)
    if spec is None or spec.loader is None:
        _LOG.warning("plugin_loader: cannot build module spec for %s", plugin_py)
        return None

    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        # Remove the poisoned partial module from sys.modules so that a
        # subsequent load_plugins() call (or test re-import) does not retrieve
        # the broken module instead of retrying the import from scratch.
        sys.modules.pop(module_name, None)
        _LOG.warning("plugin_loader: error importing %s: %s", plugin_py, exc)
        return None

    # Find the first concrete ArtGenerator subclass (skip the base class itself)
    for attr in vars(mod).values():
        if (
            isinstance(attr, type)
            and issubclass(attr, ArtGenerator)
            and attr is not ArtGenerator
        ):
            return attr()

    _LOG.warning(
        "plugin_loader: no ArtGenerator subclass found in %s", plugin_py
    )
    return None
