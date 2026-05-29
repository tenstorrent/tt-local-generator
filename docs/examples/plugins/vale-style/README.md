# vale-style — example plugin for tt-local-generator

An example plugin demonstrating subprocess execution, multi-mode operation,
and remix graph wiring. Applies the [Vale](https://vale.sh) prose linter to
text artifacts.

## Install

```bash
# 1. Install Vale
snap install vale        # Ubuntu
brew install vale        # macOS

# 2. Install Vale style packages (e.g. Microsoft)
vale sync

# 3. Install this plugin
cp -r ~/code/tt-local-generator/docs/examples/plugins/vale-style/ \
       ~/.config/tt-local-gen/plugins/

# 4. Launch tt-local-generator — the plugin appears automatically
./tt-gen
```

## Usage

In the **Generative Art** tab, select **vale-style** from the type picker.

Or via the CLI:

```bash
# Annotate — show issues inline
tt-ctl artgen vale-style \
  --text "The very unique thing about this is really very special." \
  --style Microsoft --mode annotate

# Suggest — list of issues only
tt-ctl artgen vale-style \
  --text "He walked very slowly towards the end of the pier." \
  --style Chicago --mode suggest

# Rewrite — LLM applies the suggestions (requires prompt server)
tt-ctl start prompt-server
tt-ctl artgen vale-style \
  --text "He walked very slowly towards the end of the pier." \
  --style Chicago --mode rewrite
```

## Remix graph

```
verse ──→ vale-style (tighten) ──→ video seed
freeform ─→ vale-style (annotate) ──→ verse (iterate)
```

Because `accepts_remix_from: ["verse", "freeform"]` and
`can_remix_to: ["video", "image", "verse"]`, clicking 🔀 Remix on a verse
card in the gallery shows vale-style as a target. The processed text can then
be remixed again into a video prompt — a multi-step pipeline without writing
any code.

## Modes

| Mode | What it does |
|---|---|
| `annotate` | Returns the original text with Vale suggestions appended as a comment block |
| `suggest` | Returns only the list of Vale issues (line numbers + messages) |
| `rewrite` | Sends the original text + Vale style rules to the LLM, which rewrites the text applying the suggestions |

## MCP tool

Once installed, the plugin is also exposed as an MCP tool:

```bash
python3 app/mcp_server.py &
# Then in Claude Code:
# tt-local-gen:vale-style {"text": "...", "style": "Microsoft", "mode": "rewrite"}
```

## What this example demonstrates

- **Subprocess execution** — calling an external CLI tool (Vale) with `subprocess.run`
- **Multi-mode operation** — same plugin, three different behaviours driven by one arg
- **LLM fallback path** — `rewrite` mode uses `call_fn` to pass through to the live LLM
- **Remix graph** — `accepts_remix_from` / `can_remix_to` wire it into the Remix popover
- **User-installable** — lives in `docs/examples/`, not `plugins/`, so it never
  auto-loads; the user opts in by copying it to `~/.config/tt-local-gen/plugins/`
