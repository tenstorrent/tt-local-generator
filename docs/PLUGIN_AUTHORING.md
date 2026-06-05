# Writing a Plugin for tt-local-generator

Plugins extend the app with new generation types — a new art style, a new model,
a new media format. Every plugin is a self-contained directory that the app
discovers automatically at startup.

---

## Quick start: copy an existing plugin

The fastest way to write a plugin is to copy one that does something similar:

```bash
cp -r plugins/verse/ plugins/my-plugin/
# then edit plugins/my-plugin/mcp.json and plugins/my-plugin/plugin.py
```

---

## Directory structure

Every plugin lives in one of two locations:

| Location | When to use |
|---|---|
| `plugins/<name>/` | Ships with the app (commit to the repo) |
| `~/.config/tt-local-gen/plugins/<name>/` | User-installed, not committed |

Both locations are scanned at startup. A user-installed plugin with the same name
as a built-in plugin overrides the built-in.

Each plugin directory must contain:

```
my-plugin/
  mcp.json        ← required: MCP tool manifest + app metadata
  plugin.py       ← required for local generators; absent for MCP-server-backed plugins
```

---

## Part 1: `mcp.json` — the manifest

Every plugin declares its identity, tools, and app integration in `mcp.json`.
This is a standard MCP tool manifest extended with an `x-ttlg` namespace for
app-specific fields.

### Minimal example

```json
{
  "x-ttlg": {
    "output_ext": ".txt",
    "media_type": "text",
    "accepts_remix_from": ["palette", "verse"],
    "can_remix_to": ["video", "image"],
    "tab": "generative-art",
    "hardware": null
  },
  "tools": [
    {
      "name": "my-generator",
      "description": "One-sentence description shown in the UI and to MCP clients",
      "inputSchema": {
        "type": "object",
        "properties": {
          "theme": {
            "type": "string",
            "default": "the void",
            "description": "Thematic seed for generation"
          },
          "count": {
            "type": "integer",
            "default": 1,
            "description": "How many to generate"
          }
        },
        "required": []
      },
      "examples": [
        {"theme": "volcanic winter", "count": 3},
        {"theme": "neon monastery at 4am"}
      ],
      "x-ttlg": {
        "streaming": null,
        "artifact_tool": true
      }
    }
  ]
}
```

### `x-ttlg` fields

| Field | Type | Description |
|---|---|---|
| `output_ext` | `string` | File extension for the generated artifact (`.txt`, `.svg`, `.gif`, `.json`) |
| `media_type` | `string` | How the app classifies this artifact (`"text"`, `"image"`, `"video"`, `"midi"`) |
| `accepts_remix_from` | `string[]` | Source artifact types this generator can receive as remix input (e.g. `["verse", "palette"]`) |
| `can_remix_to` | `string[]` | Target artifact types this generator's output can be remixed into |
| `tab` | `string` | Which app tab shows this generator — use `"generative-art"` for the Art tab |
| `hardware` | `string\|null` | Hardware requirement: `"blackhole"`, `"wormhole"`, or `null` (CPU/any) |
| `utility` | `bool` | Set `true` for utility plugins (like ffmpeg) that are not content generators and should not appear in the Art tab picker |
| `mcp_server` | `object` | Present only for MCP-server-backed plugins — see Part 3 |

### `x-ttlg` on the tool entry

| Field | Type | Description |
|---|---|---|
| `streaming` | `null\|"progress"\|"continuous"` | `null` = one-shot; `"progress"` = streams progress events; `"continuous"` = runs until cancelled |
| `artifact_tool` | `bool` | `true` if this tool produces an artifact (the primary tool); `false` for utility tools in the same plugin |

---

## Part 2: `plugin.py` — local generator

For generators that run locally in Python, provide a `plugin.py` with a class
that extends `ArtGenerator`.

### Minimal implementation

```python
"""
My generator — short description of what it produces.
"""
from __future__ import annotations
from artgen import ArtGenerator, register


@register
class MyGenerator(ArtGenerator):
    name = "my-generator"           # must match the tool name in mcp.json
    description = "One-line description"
    output_ext = ".txt"

    def add_args(self, parser) -> None:
        """Declare CLI arguments that map to the inputSchema in mcp.json."""
        parser.add_argument("--theme", default="the void")
        parser.add_argument("--count", type=int, default=1)

    def build_prompt(self, args) -> str:
        """Construct the LLM prompt from args. Used by the three-tier pipeline."""
        return f"Write {args.count} piece(s) about: {args.theme}"

    def parse_output(self, raw: str, args) -> str:
        """Clean the raw LLM response. Strip fences, think-blocks, etc."""
        import re
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        return cleaned.strip()
```

### Using the LLM

`ArtGenerator` subclasses get `generate_artifact(args, call_fn)` for free.
`call_fn(prompt, max_tokens=None)` calls the active LLM (artgen server or
prompt server). Override `generate_artifact` for multi-pass pipelines:

```python
def generate_artifact(self, args, call_fn) -> str:
    # Pass 1: structure
    raw1 = call_fn(self.build_prompt(args), max_tokens=512)
    structure = self.parse_output(raw1, args)

    # Pass 2: polish
    polish_prompt = f"Refine this:\n\n{structure}"
    raw2 = call_fn(polish_prompt, max_tokens=256)
    return self.parse_output(raw2, args)
```

### The `@register` decorator

`@register` adds the class to the `artgen._GENERATORS` dict so the CLI
(`tt-ctl artgen`) and the plugin loader can find it. It is optional if you only
use the plugin via the plugin loader — but keep it for backward compatibility.

### Full example: verse plugin

See `plugins/verse/plugin.py` for a complete local plugin that implements
four form variants (haiku, lore, epitaph, couplet) with per-form system prompts.

---

## Part 3: MCP-server-backed plugin

If your generator runs in a separate process (Node.js, a remote API, another
language), you don't need `plugin.py`. Instead, declare `mcp_server` in the
`x-ttlg` section of `mcp.json`:

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
  "tools": [ ... ]
}
```

The `mcp_server.command` and `mcp_server.args` are used by the app to launch
the server process. The app communicates with it via JSON-RPC over stdio
(standard MCP wire protocol).

**Full example:** see `plugins/midi/mcp.json` — the tt-midi-maker plugin
launches `npx -y tt-midi-maker` and streams progress events via the
`streaming: "progress"` annotation.

### MCP server requirements

Your server process must:
- Accept JSON-RPC 2.0 messages on stdin
- Respond on stdout
- Implement: `initialize`, `tools/list`, `tools/call`
- Return artifacts as base64 or plain text in the `tools/call` response

For SSE streaming (`streaming: "progress"`), emit progress events as:
```json
{"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 0.5, "message": "Generating bar 8 of 16"}}
```

---

## Part 4: Remix graph wiring

The Remix UI uses `accepts_remix_from` and `can_remix_to` to build the list of
valid transformation targets for any artifact. These should be set thoughtfully:

```json
"accepts_remix_from": ["palette", "verse", "landscape"],
"can_remix_to":       ["video", "image"]
```

- `accepts_remix_from`: what source artifact types can feed INTO your generator
- `can_remix_to`: what the OUTPUT of your generator can be remixed into

If your generator produces something that makes sense as a prompt for video
generation, add `"video"` to `can_remix_to`. If it can take a verse as creative
input, add `"verse"` to `accepts_remix_from`.

---

## Part 5: Exposing your plugin to Claude Code (MCP)

The app's built-in MCP server (port 8003) automatically exposes all loaded
plugins as MCP tools. After installing your plugin:

```bash
# Start the MCP server
python3 app/mcp_server.py

# Print the Claude Code configuration snippet
tt-ctl mcp-config
# → {"tt-local-gen": {"url": "http://localhost:8003/mcp"}}

# Add it to Claude Code
tt-ctl mcp-config   # outputs JSON; merge into ~/.claude/mcp.json (don't use >>)
```

Your tool will then be available in Claude Code sessions as
`tt-local-gen:my-generator`. Claude can call it directly to generate artifacts,
pass prompts, and chain generations.

---

## Part 6: Testing your plugin

```bash
# Test via the CLI
tt-ctl artgen my-generator --theme "frozen empire" --count 2

# Simulate (shows the built prompt without calling the LLM)
tt-ctl artgen my-generator --theme "frozen empire" --simulate

# Run the app's test suite (your plugin should not break existing tests)
/usr/bin/python3 -m pytest tests/ -q

# Verify the plugin loads
/usr/bin/python3 -c "
import sys; sys.path.insert(0, 'app')
import plugin_loader
plugin_loader.load_plugins()
print([p.name for p in plugin_loader.all_plugins()])
"
```

---

## Part 6b: Worked example — Vale prose-style plugin

This example shows a plugin that applies a [Vale](https://vale.sh) prose style
guide to text artifacts — useful for remixing verse or freeform text through
a style filter before it feeds into downstream generators.

### What it does

Takes a text artifact (verse, lore, freeform) and runs it through Vale with
a chosen style guide (Chicago, Microsoft, or a custom `.ini`). Returns the
annotated or corrected text. Wires naturally into the remix graph: accepts
`verse` and `freeform` as inputs, can produce `text` that re-feeds into
video or image generators as a polished prompt seed.

### `plugins/vale-style/mcp.json`

```json
{
  "x-ttlg": {
    "output_ext": ".txt",
    "media_type": "text",
    "accepts_remix_from": ["verse", "freeform"],
    "can_remix_to": ["video", "image", "verse"],
    "tab": "generative-art",
    "hardware": null
  },
  "tools": [
    {
      "name": "vale-style",
      "description": "Apply a Vale prose style guide to text — tighten, lint, or reformat for downstream use",
      "inputSchema": {
        "type": "object",
        "properties": {
          "text": {
            "type": "string",
            "description": "Input text to process"
          },
          "style": {
            "type": "string",
            "enum": ["Chicago", "Microsoft", "Google", "custom"],
            "default": "Chicago",
            "description": "Vale style guide to apply"
          },
          "mode": {
            "type": "string",
            "enum": ["annotate", "suggest", "rewrite"],
            "default": "annotate",
            "description": "annotate = show issues inline; suggest = Vale suggestions only; rewrite = LLM applies suggestions"
          }
        },
        "required": ["text"]
      },
      "examples": [
        {"text": "The very unique thing about this is that it's very special.", "style": "Microsoft", "mode": "rewrite"},
        {"text": "He walked slowly to the end of the pier.", "style": "Chicago", "mode": "annotate"}
      ],
      "x-ttlg": {"streaming": null, "artifact_tool": true}
    }
  ]
}
```

### `plugins/vale-style/plugin.py`

```python
"""
Vale style plugin — runs Vale prose linter on text artifacts.

Requires Vale to be installed: https://vale.sh/docs/vale-cli/installation/
  brew install vale          # macOS
  snap install vale          # Linux
  sudo apt install vale      # Ubuntu (if packaged)

For 'rewrite' mode, also requires a running LLM server (artgen or prompt-gen).
"""
from __future__ import annotations

import subprocess
import tempfile
import json
from pathlib import Path
from artgen import ArtGenerator


class ValeStyleGenerator(ArtGenerator):
    name = "vale-style"
    description = "Apply Vale prose style guide to text artifacts"
    output_ext = ".txt"

    def add_args(self, parser) -> None:
        parser.add_argument("--text", default="", help="Text to process")
        parser.add_argument("--style", default="Chicago",
                            choices=["Chicago", "Microsoft", "Google", "custom"])
        parser.add_argument("--mode", default="annotate",
                            choices=["annotate", "suggest", "rewrite"])

    def build_prompt(self, args) -> str:
        # In 'rewrite' mode, this prompt goes to the LLM.
        # In annotate/suggest, build_prompt is not used — generate_artifact
        # calls Vale directly instead.
        return (
            f"Rewrite the following text applying {args.style} style guide rules. "
            f"Fix passive voice, wordiness, and hedging language. "
            f"Preserve all meaning and any line breaks that are intentional.\n\n"
            f"{args.text}"
        )

    def parse_output(self, raw: str, args) -> str:
        return raw.strip()

    def generate_artifact(self, args, call_fn) -> str:
        text = getattr(args, "text", "") or ""
        style = getattr(args, "style", "Chicago")
        mode = getattr(args, "mode", "annotate")

        if mode == "rewrite":
            # LLM path: ask the model to apply style rules
            raw = call_fn(self.build_prompt(args), max_tokens=512)
            return self.parse_output(raw, args)

        # Vale path: run the linter, return annotated/suggestion output
        vale = _find_vale()
        if vale is None:
            return f"[Vale not found — install from https://vale.sh]\n\n{text}"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False, encoding="utf-8") as tmp:
            tmp.write(text)
            tmp_path = tmp.name

        try:
            result = subprocess.run(
                [vale, "--output=JSON", f"--config={_style_config(style)}", tmp_path],
                capture_output=True, text=True, timeout=30,
                stdin=subprocess.DEVNULL,
            )
            if mode == "suggest":
                return _format_suggestions(result.stdout, text)
            else:  # annotate
                return _format_annotations(result.stdout, text)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return f"[Vale error: {e}]\n\n{text}"
        finally:
            Path(tmp_path).unlink(missing_ok=True)


def _find_vale() -> "str | None":
    """Return the path to the vale binary, or None if not installed."""
    import shutil
    return shutil.which("vale")


def _style_config(style: str) -> str:
    """Return path to a minimal Vale config for the given style."""
    # For a real plugin, bundle .vale.ini files in plugins/vale-style/styles/
    # This returns an inline config string written to a temp file.
    import tempfile, os
    cfg = f"""
StylesPath = {Path(__file__).parent / 'styles'}
MinAlertLevel = suggestion

[*.txt]
BasedOnStyles = {style}
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini",
                                     delete=False, encoding="utf-8") as f:
        f.write(cfg)
        return f.name


def _format_annotations(vale_json: str, original: str) -> str:
    """Inline Vale suggestions as comments in the original text."""
    try:
        data = json.loads(vale_json) if vale_json.strip() else {}
    except json.JSONDecodeError:
        return original

    annotations = []
    for _file, issues in data.items():
        for issue in issues:
            line = issue.get("Line", 0)
            msg = issue.get("Message", "")
            level = issue.get("Severity", "suggestion")
            annotations.append(f"  Line {line} [{level}]: {msg}")

    if not annotations:
        return original + "\n\n[No style issues found]"
    return original + "\n\n[Vale suggestions]\n" + "\n".join(annotations)


def _format_suggestions(vale_json: str, original: str) -> str:
    """Return only the Vale suggestions, not the original text."""
    try:
        data = json.loads(vale_json) if vale_json.strip() else {}
    except json.JSONDecodeError:
        return "[No output from Vale]"

    lines = []
    for _file, issues in data.items():
        for issue in issues:
            lines.append(
                f"Line {issue.get('Line', '?')}: "
                f"[{issue.get('Severity', 'info')}] {issue.get('Message', '')}"
            )
    return "\n".join(lines) if lines else "[No style issues found]"
```

### How it wires into Remix

Because `accepts_remix_from: ["verse", "freeform"]`, when a user opens Remix
on a verse artifact, **Vale style** appears as a target. The ingredient model
carries the verse text as the `text` ingredient. The mode dropdown in the Art
tab controls whether Vale annotates, lists suggestions, or rewrites via LLM.

The output (`can_remix_to: ["video", "image", "verse"]`) means a Vale-processed
text can then be remixed into a video prompt or a new verse iteration — enabling
a multi-step pipeline: *verse → Vale tighten → use as video seed*.

### Installing and testing

```bash
# Install Vale
snap install vale   # or: brew install vale

# Test the plugin
tt-ctl artgen vale-style \
  --text "The very unique thing about this is really very special." \
  --style Microsoft --mode suggest

# Rewrite mode (needs LLM server)
tt-ctl artgen vale-style \
  --text "He walked very slowly towards the end of the pier." \
  --style Chicago --mode rewrite
```

---

## Part 7: Plugin checklist

Before submitting a plugin:

- [ ] `mcp.json` has valid JSON and all required `x-ttlg` fields
- [ ] Tool `name` in `mcp.json` matches `ArtGenerator.name` in `plugin.py`
- [ ] `output_ext` matches what `default_output()` returns
- [ ] `accepts_remix_from` and `can_remix_to` are accurate
- [ ] `build_prompt` returns a non-empty string for default args
- [ ] `parse_output` strips markdown fences and `<think>` blocks
- [ ] At least one `examples` entry in the tool schema
- [ ] Plugin loads without errors: `tt-ctl plugin list`
- [ ] Generation succeeds end-to-end: `tt-ctl artgen <name> --theme "test"`
- [ ] If hardware-gated, `"hardware"` field is set correctly and the plugin
      degrades gracefully when the hardware is absent

---

## Reference: `ArtGenerator` base class

```python
class ArtGenerator:
    name: str           # unique plugin identifier, matches tool name in mcp.json
    description: str    # shown in the Art tab picker
    output_ext: str     # file extension for artifacts

    def add_args(self, parser) -> None: ...
    def build_prompt(self, args) -> str: ...
    def parse_output(self, raw: str, args) -> str: ...
    def post_process(self, artifact: str, args) -> str: ...  # default: identity
    def default_output(self) -> Path: ...                   # auto-derived from name + ext
    def generate_artifact(self, args, call_fn) -> str: ...  # default: single-pass
```

Override `generate_artifact` for multi-pass pipelines (see ANSI generator in
`plugins/ansi/plugin.py` for a three-pass example: structure → block chars →
colorization).
