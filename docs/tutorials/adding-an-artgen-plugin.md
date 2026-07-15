# Tutorial: Add an artgen plugin that reuses an existing model

This walks through building a **new generative-art generator that ships no new
infrastructure** — it reuses whatever chat LLM is already running on the artgen
server. Our worked example is **Emoji Storyteller**: it tells a little story
almost entirely in emoji and symbols. The finished plugin lives at
`plugins/emoji-storyteller/` and its tests at
`tests/test_emoji_storyteller_plugin.py`; this guide is how it was built.

By the end you'll have a generator that is automatically available as:

- a CLI subcommand — `tt-ctl artgen emoji-storyteller`
- a medium chip on the **Create** surface
- a node/step other pipelines can remix into (per its manifest)

with no edits to the app itself.

## What an artgen plugin is

A generator is a small `ArtGenerator` subclass. The base class
(`app/artgen/__init__.py`) gives you a single-pass pipeline for free:

```
build_prompt(args) -> call_fn(prompt) -> parse_output(raw) -> post_process(art)
```

The important idea for *reusing a model*: you never load, start, or even name a
model. The caller (the CLI, the Create panel, or the pipeline engine) hands your
generator a **`call_fn`** already wired to the running chat server:

```python
call_fn(prompt, system=None, max_tokens=None) -> raw_text
```

Set `uses_llm = True` (the default) and your generator simply crafts a good
prompt and returns the model's text. That's the whole contract. Purely
algorithmic plugins (no model) set `uses_llm = False` so the pipeline engine
knows not to start a chat backend for them — but Emoji Storyteller wants the
model, so it leaves the default alone.

## Anatomy of a plugin

A plugin is a directory the loader discovers under `plugins/` (or
`~/.config/tt-local-gen/plugins/`):

```
plugins/emoji-storyteller/
  mcp.json     # manifest (required) — declares the tool, schema, remix wiring
  plugin.py    # optional — an ArtGenerator subclass (this is the "local" case)
```

`app/plugin_loader.py` scans each directory, reads `mcp.json`, and — when a
`plugin.py` is present — imports it and instantiates the first `ArtGenerator`
subclass it finds. No decorator or registration call is needed for a plugin;
being in the directory with a valid manifest is enough.

## Step 1 — the manifest (`mcp.json`)

The manifest names the tool, describes its inputs, and declares how it fits into
the remix graph. The **primary tool's `name` is the registry key** — it becomes
the `tt-ctl artgen <name>` subcommand and the id everything else uses.

```json
{
  "x-ttlg": {
    "output_ext": ".txt",
    "media_type": "text",
    "accepts_remix_from": ["verse"],
    "can_remix_to": ["image", "video"],
    "tab": "generative-art",
    "hardware": null
  },
  "tools": [
    {
      "name": "emoji-storyteller",
      "description": "Tells a story almost entirely in emoji and symbols",
      "inputSchema": {
        "type": "object",
        "properties": {
          "theme":  {"type": "string",  "default": "a hero's journey", "description": "Story seed"},
          "scenes": {"type": "integer", "default": 6, "description": "Number of story beats, one per line"},
          "words":  {"type": "integer", "default": 0, "description": "Max real words allowed; 0 = pure emoji/symbols"}
        },
        "required": []
      },
      "examples": [
        {"theme": "a cat who wants to fly", "scenes": 5},
        {"theme": "the birth and death of a star", "scenes": 7}
      ],
      "x-ttlg": {"streaming": null, "artifact_tool": true}
    }
  ]
}
```

Notes:

- **`x-ttlg.artifact_tool: true`** marks the tool that produces the artifact —
  the loader picks it as the plugin's primary generator. (Without it, the first
  tool is used.)
- **`media_type` / `output_ext`** describe what comes out (`text` here).
- **`accepts_remix_from` / `can_remix_to`** wire the remix graph: an emoji story
  can be *seeded from* a `verse` piece and *remixed into* `image`/`video`
  (imagine turning the story into an illustrated series). List only what makes
  sense; an empty list is fine.
- **`hardware: null`** — this generator needs no specific accelerator; it uses
  whatever chat model is up.
- The `inputSchema` mirrors your `add_args` flags (Step 2). Keep the two in
  sync — same names, same defaults.

## Step 2 — the generator (`plugin.py`)

```python
from __future__ import annotations
from artgen import ArtGenerator

_SYSTEM = (
    "You are Emoji Storyteller. You tell a complete little story using ONLY "
    "emoji, symbols, arrows, and punctuation ... (house style) ..."
)

def _build_user_message(theme: str, scenes: int, words: int) -> str:
    word_rule = (
        "Use NO real words at all — emoji, symbols, arrows, and punctuation only."
        if words <= 0 else
        f"Use at most {words} real word(s) in the whole story; everything else "
        "must be emoji, symbols, arrows, or punctuation."
    )
    return (
        f"Tell a story on the theme: {theme}\n"
        f"Give it {scenes} scene(s), one scene per line, in order. {word_rule} "
        "Output only the scenes."
    )

class EmojiStorytellerGenerator(ArtGenerator):
    name = "emoji-storyteller"          # MUST equal the manifest's primary tool name
    description = "Tells a story almost entirely in emoji and symbols"
    output_ext = ".txt"
    # uses_llm = True inherited — reuses the running chat model; no new backend.

    def add_args(self, parser) -> None:
        parser.add_argument("--theme", default="a hero's journey", help="Story seed")
        parser.add_argument("--scenes", type=int, default=6, metavar="N",
                            help="Number of story beats, one per line")
        parser.add_argument("--words", type=int, default=0, metavar="N",
                            help="Max real words allowed; 0 = pure emoji/symbols")

    def build_prompt(self, args) -> str:
        # Returned as-is by --simulate, so make it meaningful.
        return _build_user_message(
            getattr(args, "theme", "a hero's journey"),
            getattr(args, "scenes", 6),
            getattr(args, "words", 0),
        )

    def generate_artifact(self, args, call_fn) -> str:
        # One call, with our house-style system prompt. We never name the model.
        raw = call_fn(self.build_prompt(args), system=_SYSTEM)
        return self.post_process(self.parse_output(raw, args), args)

    def parse_output(self, raw: str, args) -> str:
        import re
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        return re.sub(r"```\w*\s*|```", "", cleaned).strip()

    def default_output(self):
        from pathlib import Path
        return Path("emoji-storyteller.txt")
```

The load-bearing choices:

- **`name` must match the manifest's primary tool name** (`emoji-storyteller`).
  The loader keys the registry off the manifest, but `default_output()` and the
  `tt-ctl` subcommand use this attribute — keep them identical to avoid
  confusion.
- **`add_args` ↔ `inputSchema`** stay in lockstep (names + defaults).
- **`build_prompt` returns the user turn.** `--simulate` prints exactly this, so
  it's your dry-run preview.
- **`generate_artifact` is where you reuse the model.** Passing `system=` shapes
  the model's behavior without changing which model runs. If you didn't need a
  separate system prompt you could skip this override entirely and let the base
  class do `build_prompt -> call_fn -> parse_output -> post_process`.
- **`parse_output`** defends against chat-model habits — stray `<think>` blocks
  and ```` ``` ```` fences. The base default only strips fences; we add think
  blocks.

### Multi-pass generators (when one call isn't enough)

If a single call can't do the job, override `generate_artifact` to make several
`call_fn` calls, each with its own `max_tokens`. The ANSI generator
(`app/artgen/generators/ansi.py`) is the reference: it runs an ASCII-layout pass,
a block-character refinement pass, and a colorization pass. Emoji Storyteller
doesn't need this — one well-aimed prompt is plenty.

## Step 3 — test it

**Dry run (no model needed):**

```bash
./tt-ctl artgen emoji-storyteller --simulate --theme "a cat who wants to fly" --scenes 5
# prints the exact prompt build_prompt() produced
```

**Unit test with a fake `call_fn`** — this is why reusing a model is so easy to
test: you never touch the network or hardware. From
`tests/test_emoji_storyteller_plugin.py`:

```python
def test_generate_artifact_passes_house_system_prompt_and_parses():
    g = EmojiStorytellerGenerator()
    captured = {}
    def fake_call(prompt, system=None, max_tokens=None):
        captured["system"] = system
        return "<think>plan</think>```\n🐱 -> ✈️ -> 🌈\n```"
    out = g.generate_artifact(_args(), fake_call)
    assert out == "🐱 -> ✈️ -> 🌈"           # think-block + fences stripped
    assert "Emoji Storyteller" in captured["system"]
```

Run the suite (xvfb gives GTK widget tests a display; this plugin's tests don't
need it but the rest of the suite does):

```bash
xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_emoji_storyteller_plugin.py -q
```

**Real run** (with a chat model up — e.g. `./tt-ctl start --single-chip` for the
8B artgen model, or any OpenAI-compatible chat server the endpoint sweep finds):

```bash
./tt-ctl artgen emoji-storyteller --theme "first contact" --scenes 8
```

## Where it shows up automatically

Once the directory exists with a valid `mcp.json` + `plugin.py`:

- **CLI:** `tt-ctl artgen emoji-storyteller` (its flags come from `add_args`).
- **Create surface:** a medium chip appears (via `create_mediums.discover_mediums`,
  which lists every registered generator). Its parameter panel is built by
  introspecting `add_args` — no UI code to write. Fields are role-classified
  (brief/direction/control) automatically; `--theme` reads as a brief field.
- **Pipelines / Remix:** the manifest's `accepts_remix_from` / `can_remix_to`
  wire it into the remix graph, so it can be composed with other steps.

## Checklist for your own plugin

1. `plugins/<name>/mcp.json` — one tool with `x-ttlg.artifact_tool: true`;
   `name` is your generator id; fill `inputSchema`, `media_type`, `output_ext`,
   and the remix wiring.
2. `plugins/<name>/plugin.py` — an `ArtGenerator` subclass whose `name` matches
   the tool name; `add_args` (mirroring the schema), `build_prompt`, and — if you
   need a system prompt or multiple passes — `generate_artifact`.
3. Leave `uses_llm = True` to reuse the running chat model; set it `False` only
   for a purely algorithmic generator.
4. A test that drives `generate_artifact` with a fake `call_fn` (no network), and
   a `--simulate` sanity check.
5. Bump `VERSION` + add a `debian/changelog` stanza (see the Version discipline
   section in `CLAUDE.md`).
