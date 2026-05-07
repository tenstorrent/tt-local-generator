# artgen — Generative Art for tt-local-generator

**Date:** 2026-04-29  
**Branch:** `feature/artgen`  
**Status:** Approved for implementation

---

## Context

`06_landscape_svg.py` in `tt-agents` is a standalone demo that asks an LLM to write a layered SVG landscape from scratch — no diffusion model, no GPU driver, just an OpenAI-compatible endpoint and a structured prompt. It works with whatever model is currently running on port 8000.

The goal is to generalise this pattern into a multi-purpose generative art system living in `tt-local-generator`, invokable as `tt-ctl artgen`, and designed to grow to as many artifact types as we care to add.

---

## Design

### Package location

```
app/artgen/
  __init__.py          ← ArtGenerator base class, registry, call_llm(), detect_model()
  cli.py               ← cmd_artgen(), _build_artgen_parser()
  generators/
    __init__.py
    landscape.py       ← ported from 06_landscape_svg.py (palette, mountains, glitch)
    skyline.py         ← procedural city skyline SVG
    constellation.py   ← invented star charts with named stars and connecting lines
    geometric.py       ← abstract tiled geometry — Mondrian-meets-circuit
    ansi.py            ← block-character ANSI art (256-color or 16-color)
    palette.py         ← evocative named color palette + prose lore
    verse.py           ← structured text forms: haiku, lore fragments, epitaphs
    circuit.py         ← logic/wiring diagram SVG (gates, nodes, labelled edges)
    freeform.py        ← raw --freeform "..." pass-through, auto-detects output type
```

### Base class protocol

```python
class ArtGenerator(ABC):
    name: str        # CLI name: "landscape", "skyline", …
    description: str
    output_ext: str  # ".svg", ".txt", ".ans"

    def add_args(self, parser) -> None: ...       # optional; extend argparse
    @abstractmethod
    def build_prompt(self, args) -> str: ...      # returns the LLM user message
    def parse_output(self, raw: str, args) -> str: ...  # default: strip fences
    def post_process(self, artifact: str, args) -> str: ...  # default: passthrough
    def default_output(self) -> Path: ...         # default: <name><ext>
```

Decorating a class with `@register` adds it to the global registry keyed by `name`.

### LLM backend

- Uses `server_config.base_url("artgen")` — a new key added to `DEFAULTS` pointing to the same `localhost:8000` as the other video services
- `detect_model(base_url)` hits `/v1/models`, returns the first model ID (same logic as `06_landscape_svg.py`)
- `call_llm()` uses the `openai` Python client with `api_key="none"` — works with vLLM, Ollama, or any OpenAI-compatible server
- `--model` flag overrides auto-detection
- `--simulate` prints the prompt and exits without calling the LLM

### tt-ctl integration

In `tt-ctl`:
- Import `app.artgen` (`sys.path` already includes `app/`)
- Add `artgen` parser to the existing `sub` subparsers
- Each generator's `add_args()` is called at parse-time to register its flags
- `cmd_artgen(args)` is added to the dispatch table
- Docstring updated with `artgen` usage

### Generator flag inventory (Phase 1)

| Generator | Key flags |
|-----------|-----------|
| landscape | `--palette sunset\|blue\|purple\|red\|orange` `--mountains` `--clouds` `--stars` `--glitch` `--glitch-seed N` |
| skyline | `--era modern\|retro\|futuristic` `--density low\|medium\|high` `--sky day\|dusk\|night` |
| constellation | `--culture random\|norse\|greek\|invented` `--stars N` `--lore` (adds mythological fragment) |
| geometric | `--style mondrian\|circuit\|recursive` `--palette <name>` `--complexity low\|high` |
| ansi | `--subject "..."` `--width 60` `--colors 16\|256` |
| palette | `--mood "..."` `--count 6` `--export-css` |
| verse | `--form haiku\|lore\|epitaph\|couplet` `--theme "..."` `--count N` |
| circuit | `--inputs A\|B\|C` `--gates and\|or\|not\|xor` `--depth 2\|3` |
| freeform | `--freeform "describe anything"` `--output file.svg\|.txt\|.ans` |

### Extensibility roadmap

**Phase 1 (this implementation):** All 8 built-in generators + `--freeform`.  
**Phase 2:** User-defined generators as YAML in `~/.config/tt-artgen/generators/` — the registry loads them at startup via a thin YAML adapter.  
**Phase 3:** Composable flags — `--verse-caption` overlays a generated verse onto any SVG output.

---

## Output

- Files written to the current working directory by default
- `--output <path>` overrides
- Glitch variants (landscape) save as `<name>_glitch.svg` alongside the clean version
- ANSI art saves as `.ans` (raw escape codes) — open in any terminal with `cat`
- Palette saves as `.json` (hex codes + lore) and optionally `.css` (CSS custom properties)

---

## Verification

1. `python3 -c "import artgen; print(artgen.all_names())"` — lists all generators
2. `tt-ctl artgen --type landscape --simulate` — prints prompt, no LLM
3. `tt-ctl artgen --type landscape --palette purple --glitch` — full pipeline
4. `tt-ctl artgen --type skyline --era retro --night` — second generator
5. `tt-ctl artgen --freeform "a circuit diagram of a sad robot as SVG"` — freeform
6. `tt-ctl artgen --type verse --form haiku --theme "winter forges"` — text output
7. All existing `tt-ctl` commands still work — regression check

---

## Files to create / modify

**New files:**
- `app/artgen/__init__.py`
- `app/artgen/cli.py`
- `app/artgen/generators/__init__.py`
- `app/artgen/generators/landscape.py`
- `app/artgen/generators/skyline.py`
- `app/artgen/generators/constellation.py`
- `app/artgen/generators/geometric.py`
- `app/artgen/generators/ansi.py`
- `app/artgen/generators/palette.py`
- `app/artgen/generators/verse.py`
- `app/artgen/generators/circuit.py`
- `app/artgen/generators/freeform.py`

**Modified files:**
- `app/server_config.py` — add `"artgen"` key to `DEFAULTS`
- `tt-ctl` — import `artgen`, add `artgen` subparser and `cmd_artgen`, update docstring
