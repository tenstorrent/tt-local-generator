# Plugin System Design

> Branch: `feat/remix` | Date: 2026-05-27 | Status: approved

## Overview

Replace the hardcoded `_load_generators()` import list with a discoverable,
MCP-native plugin system. Every generator — LLM artgen, hardware video/image
models, external MCP servers like tt-midi-maker — is a plugin. The app ships
a `plugins/` directory full of first-party examples. External authors drop a
new directory in `~/.config/tt-local-gen/plugins/` and it appears in the UI
with no code changes.

The app also exposes its own MCP server endpoint so any MCP client (Claude
Code, Cursor, etc.) can call all loaded plugins as tools directly.

**Alternatives considered and noted for future exploration:**
- B: Python class with auto-MCP export (cleaner for Python-native plugins, but
  external MCP richness must be manually transcribed)
- C: Hybrid `plugin.toml` manifest with MCP import tooling (more human-readable
  authoring, but adds a translation layer and extra format to maintain)

---

## 1. Plugin Directory Structure

Each plugin is a self-contained directory. The loader scans two locations in
order:

1. `plugins/` at the repo root — first-party, always loaded
2. `~/.config/tt-local-gen/plugins/` — user-installed, loaded if present

```
plugins/
  landscape/
    mcp.json        ← MCP tool manifest (required)
    plugin.py       ← Python executor (optional)
  verse/
    mcp.json
    plugin.py
  animatediff/
    mcp.json
    plugin.py
  skyreels/
    mcp.json
    plugin.py
  wan2/
    mcp.json
    plugin.py
  flux/
    mcp.json
    plugin.py
  palette/
    mcp.json
    plugin.py
  circuit/
    mcp.json
    plugin.py
  constellation/
    mcp.json
    plugin.py
  geometric/
    mcp.json
    plugin.py
  skyline/
    mcp.json
    plugin.py
  freeform/
    mcp.json
    plugin.py
  ansi/
    mcp.json
    plugin.py
  midi/
    mcp.json        ← absorbed from tt-midi-maker MCP definition
                    ← no plugin.py — delegates to live MCP server
```

**Plugin types:**

- **Local plugin** — has `plugin.py` with an `ArtGenerator` subclass. Python
  executes directly; no network call required at generation time.
- **Delegating plugin** — has only `mcp.json` with an `x-ttlg.mcp_server`
  block. The loader instantiates a generic `McpDelegateGenerator` that speaks
  MCP wire protocol to the declared server at runtime.

Both types are identical from the UI's and Claude Code's perspective.

---

## 2. `mcp.json` Manifest Format

Standard MCP tool schema. App-specific metadata lives in an `x-ttlg` extension
namespace. A plugin declares one or more tools in a `tools` array.

### Single-tool local plugin (e.g. landscape)

```json
{
  "x-ttlg": {
    "output_ext": ".svg",
    "media_type": "image",
    "accepts_remix_from": ["verse", "palette"],
    "can_remix_to": ["video", "image"],
    "tab": "artgen",
    "hardware": null
  },
  "tools": [
    {
      "name": "landscape",
      "description": "Generate an SVG landscape scene with sky, terrain, and atmospheric effects",
      "inputSchema": {
        "type": "object",
        "properties": {
          "palette": {
            "type": "string",
            "enum": ["sunset", "arctic", "toxic", "midnight", "desert"],
            "description": "Color palette for the scene"
          },
          "style": {
            "type": "string",
            "enum": ["minimal", "detailed", "glitch"],
            "default": "detailed"
          }
        },
        "required": []
      },
      "examples": [
        {"palette": "sunset", "style": "glitch"},
        {"palette": "arctic", "style": "minimal"}
      ],
      "x-ttlg": {
        "streaming": null,
        "artifact_tool": true
      }
    }
  ]
}
```

### Multi-tool delegating plugin with streaming (e.g. midi)

```json
{
  "x-ttlg": {
    "output_ext": ".mid",
    "media_type": "midi",
    "accepts_remix_from": ["verse", "palette"],
    "can_remix_to": [],
    "tab": "artgen",
    "mcp_server": {
      "command": "npx",
      "args": ["-y", "tt-midi-maker"]
    }
  },
  "tools": [
    {
      "name": "generate_midi",
      "description": "Generate a complete MIDI file from a prompt or remix context",
      "inputSchema": {
        "type": "object",
        "properties": {
          "prompt": {"type": "string"},
          "tempo": {"type": "integer", "default": 120},
          "bars": {"type": "integer", "default": 16}
        },
        "required": ["prompt"]
      },
      "examples": [],
      "x-ttlg": {
        "streaming": "progress",
        "artifact_tool": true
      }
    },
    {
      "name": "stream_midi",
      "description": "Open a continuous live MIDI event stream — runs until cancelled",
      "inputSchema": {
        "type": "object",
        "properties": {
          "prompt": {"type": "string"},
          "tempo": {"type": "integer", "default": 120}
        },
        "required": ["prompt"]
      },
      "examples": [],
      "x-ttlg": {
        "streaming": "continuous",
        "artifact_tool": false
      }
    }
  ]
}
```

### `x-ttlg` field reference

**Top-level (plugin-wide):**

| Field | Type | Description |
|---|---|---|
| `output_ext` | string | Default file extension for the primary artifact tool |
| `media_type` | string | `"image"`, `"video"`, `"midi"`, `"text"`, `"gif"`, etc. |
| `accepts_remix_from` | string[] | Media types this plugin can receive as remix input |
| `can_remix_to` | string[] | Media types this plugin can produce as remix output |
| `tab` | string | UI tab to place this plugin in (`"artgen"`, `"video"`, `"image"`) |
| `hardware` | string\|null | Required hardware tag: `"blackhole"`, `"p300x2"`, or `null` |
| `mcp_server` | object\|null | MCP server launch config for delegating plugins |

**Per-tool:**

| Field | Type | Description |
|---|---|---|
| `streaming` | `null` \| `"progress"` \| `"continuous"` | Streaming contract (see below) |
| `artifact_tool` | bool | Whether this tool produces an artifact to store in history |

### Streaming contracts

- **`null`** — request/response. Tool call returns the complete artifact.
  UI shows a spinner during generation.
- **`"progress"`** — SSE stream during generation, terminates with the
  complete artifact. UI shows a progress bar. History stores the finished file.
- **`"continuous"`** — subscription stream, never terminates. UI shows a live
  playback control with a stop button. Nothing is written to history. Cancelled
  via MCP's standard cancellation notification.

---

## 3. Plugin Loader

**File:** `app/plugin_loader.py` — zero GUI imports.

```python
@dataclass
class PluginDef:
    path: Path                  # plugin directory
    manifest: dict              # parsed mcp.json
    name: str                   # primary tool name (first artifact_tool in tools[])
    tools: list[dict]           # all tools declared
    generator: ArtGenerator     # local class OR McpDelegateGenerator instance
```

**Discovery algorithm:**

```
for each search_path in [repo/plugins/, ~/.config/tt-local-gen/plugins/]:
    for each subdirectory in search_path:
        read mcp.json → parse → build PluginDef
        if plugin.py exists:
            import plugin.py, find ArtGenerator subclass, instantiate
        else if x-ttlg.mcp_server declared:
            instantiate McpDelegateGenerator(manifest)
        register PluginDef in _PLUGINS dict keyed by name
```

**`ArtGenerator` base class additions:**

```python
# Populated from mcp.json at load time — not declared in Python
accepts_remix_from: tuple[str, ...] = ()
can_remix_to: tuple[str, ...] = ()
```

**`McpDelegateGenerator`** — a generic `ArtGenerator` subclass that:
- Launches the declared MCP server process on first call (lazy, one per plugin)
- Routes `generate_artifact()` to the correct MCP tool via JSON-RPC
- For `streaming: "progress"` tools: reads SSE stream, forwards progress events
  via the existing `on_progress` callback pattern
- For `streaming: "continuous"` tools: returns a `LiveSession` handle instead
  of an artifact string

**Error handling:**
- Malformed `mcp.json` — logged as warning, plugin skipped, loader continues
- Missing `plugin.py` class — logged as warning, plugin skipped
- MCP server fails to start — `generate_artifact()` raises `PluginRuntimeError`
  which the UI surfaces as a generation error (same path as existing API errors)

---

## 4. App MCP Server Endpoint

**File:** `app/mcp_server.py` — FastAPI app, no GUI imports.

Mounted at `/mcp` on port 8003 (configurable via `TTLG_MCP_PORT` env var or
Preferences). At startup it reads all registered `PluginDef`s and synthesizes
a standard MCP server manifest from their `mcp.json` files.

**Protocol:** MCP over HTTP+SSE (standard). Implements:
- `GET /mcp` — server manifest (all tools from all plugins)
- `POST /mcp` — `initialize`, `tools/list`, `tools/call`
- SSE stream on `tools/call` for `streaming: "progress"` and
  `streaming: "continuous"` tools

**Claude Code integration:**

```bash
tt-ctl mcp-config >> ~/.claude/mcp.json
# Appends: {"tt-local-gen": {"url": "http://localhost:8003/mcp"}}
```

All first-party local plugins (landscape, verse, etc.) are exposed as MCP
tools that execute Python locally — indistinguishable from tt-midi-maker from
Claude Code's perspective.

**`tt-ctl` additions:**
- `tt-ctl mcp-config` — emit ready-to-use Claude Code MCP config JSON
- `tt-ctl plugin list` — list all loaded plugins with name, type, media_type
- `tt-ctl plugin install <path>` — copy a plugin directory to
  `~/.config/tt-local-gen/plugins/` and validate its manifest

---

## 5. Remix Integration

The remix graph (from `remix-mode-planning.md`) is derived entirely from plugin
manifests — no hardcoded compatibility table anywhere in the UI.

`RemixContext` dataclass added to `app/artgen/__init__.py`:

```python
@dataclass
class RemixContext:
    source_record: dict       # history record being remixed
    source_type: str          # media type of the source
    target_type: str          # media type of the target
    hint: str                 # extracted text/data (verse text, hex palette, etc.)
```

The detail panel Remix button list is built at render time by walking all
loaded `PluginDef`s:

```python
def remix_targets_for(source_type: str) -> list[PluginDef]:
    return [
        p for p in plugin_loader.all_plugins()
        if source_type in p.manifest["x-ttlg"]["accepts_remix_from"]
    ]
```

Each plugin's `plugin.py` may optionally implement `extract_remix_hint(record) -> str`.
Default implementation returns the stored prompt text from the history record.

History records gain a `remix_source_id` field (nullable string) so generation
lineage is traceable.

---

## 6. Migration Path

Existing generators migrate from `app/artgen/generators/foo.py` to
`plugins/foo/plugin.py` + `plugins/foo/mcp.json`. Migration is mechanical and
each generator is independent.

**Gate rule:** a generator does not move until its tests pass.

**Migration steps per generator:**
1. Write `tests/test_plugin_foo.py` covering `build_prompt`, `parse_output`,
   `generate_artifact` with mocked `call_fn`
2. Create `plugins/foo/mcp.json` with correct schema, remix edges, examples
3. Move `app/artgen/generators/foo.py` → `plugins/foo/plugin.py`
   - Adjust import path: `from artgen import ArtGenerator, register`
   - Drop `@register` — the new loader finds the `ArtGenerator` subclass by
     class inspection, not by decorator. The decorator is a no-op in `plugin.py`
     and can be removed.
4. Confirm tests pass against new location
5. Remove from `app/artgen/generators/__init__.py` import list

The old `@register` decorator and `_load_generators()` are removed in a final
cleanup PR once all generators have plugin dirs.

**Migration order** (lowest risk first):
1. verse, freeform (pure LLM, no special post-processing)
2. landscape, skyline, palette, constellation, geometric, circuit (LLM + SVG)
3. ansi (multi-pass LLM)
4. animatediff (subprocess, no LLM)
5. skyreels, wan2, flux (video/image, hardware-gated)

---

## 7. Test Plan

All new tests live in `tests/`. Mocking discipline follows existing suite
patterns (mock subprocess, network, no real hardware or LLM calls).

### New test files

**`tests/test_plugin_loader.py`**
- Discovery finds plugins in `plugins/` directory
- Discovery finds plugins in `~/.config/tt-local-gen/plugins/`
- Malformed `mcp.json` is skipped with warning, other plugins still load
- Plugin with `plugin.py` gets a local `ArtGenerator` instance
- Plugin without `plugin.py` but with `mcp_server` gets `McpDelegateGenerator`
- `accepts_remix_from` / `can_remix_to` populated from manifest
- Duplicate plugin names: last-loaded wins (user dir overrides repo dir)

**`tests/test_artgen_generators.py`**
- One test class per migrated generator
- `build_prompt(args)` returns non-empty string
- `parse_output(raw, args)` strips fences, think blocks, returns artifact
- `generate_artifact(args, call_fn)` calls `call_fn` with correct args,
  returns processed artifact (mocked `call_fn` returns fixture string)
- `default_output()` returns path with correct extension
- Generator-specific: verse forms, ansi three-pass call sequence, landscape
  SVG repair, animatediff subprocess invocation

**`tests/test_mcp_delegate.py`**
- `McpDelegateGenerator` sends correct JSON-RPC to mock MCP server
- `streaming: "progress"` — SSE events forwarded via `on_progress` callback
- `streaming: "continuous"` — returns `LiveSession`, stop cancels correctly
- MCP server process failure raises `PluginRuntimeError`

**`tests/test_mcp_server.py`**
- `GET /mcp` returns manifest with all loaded plugins as tools
- `tools/list` response matches plugins in loader registry
- `tools/call` for local plugin routes to correct `ArtGenerator.generate_artifact`
- `tools/call` for delegating plugin routes to `McpDelegateGenerator`
- SSE stream on `tools/call` with `streaming: "progress"` tool

**`tests/test_remix_graph.py`**
- `remix_targets_for("verse")` returns plugins with `"verse"` in
  `accepts_remix_from`
- `remix_targets_for("image")` returns correct subset
- `extract_remix_hint` default returns prompt text from history record
- `RemixContext` dataclass fields populated correctly

---

## Open Questions

These are deferred — not blockers for the initial implementation:

- Does Remix allow same-type remixing (verse → verse)? Initial answer: no, but
  trivial to enable later by adding self-references in the manifest edges.
- Should the Remix action show a confirmation/preview step before switching
  tabs? Defer to UX iteration after first working implementation.
- `streaming: "continuous"` LiveSession — should the UI write a partial
  artifact to history on cancel/timeout? Initial answer: no, stateless.
- MCP server port conflict resolution — what if 8003 is taken? Expose
  `TTLG_MCP_PORT` env var; document in README; no auto-discovery for v1.
- Plugin signing / trust model for user-installed plugins — deferred, not
  needed for first-party-only initial release.
