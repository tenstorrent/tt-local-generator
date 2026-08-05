# Remix → Pipelines Unification + Cross-Type Adapters — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "remix" mean exactly one thing — seed a pipeline from an artifact — and let a color palette flow into AnimateDiff via an auto-inserted, editable "Palette → Prompt" adapter step.

**Architecture:** One remix button per surface routes to the existing `_remix_as_pipeline` → Pipeline Studio Muse (the redundant `🔀 RemixPopover`/`_dispatch_remix` path is removed). Palettes become a seedable kind. A tiny adapter registry `(seed_kind, needed_input_kind) → class_type` lets the Muse offer goals reachable via a converter and prepend a `TTLGPaletteToPrompt` source-node whose editable `prompt` literal is computed at seed time (LLM-polished with a deterministic colors+lore fallback). Because that node's `output_kind` is `text`, the untouched `spec_remix.seed_spec` already wires it into AnimateDiff's `prompt`.

**Tech Stack:** Python 3 (system `/usr/bin/python3`), GTK4/PyGObject, pytest under `xvfb`, ComfyUI-API-v1 pipeline specs.

## Global Constraints

- `collect()` / `_collect_params()` output stays byte-for-byte identical where widgets are unchanged (existing invariant; guarded by tests).
- GTK is single-threaded: LLM/network calls run off the main thread; UI updates via `GLib.idle_add` (mirror `_create_inspire_fn`, `app/main_window.py:8084`).
- `_CSS` / `b"""…"""` byte literals are ASCII-only; glyphs live in Python `str` labels only.
- Pipeline spec contract: `{node_id: {"class_type", "inputs"}}`; a wire is `[src_node_id, output_key]` (`_is_wire`, `pipeline_engine.py:49`); `editable_params` excludes wires and `_`-prefixed keys (`spec_remix.py:107`).
- System `/usr/bin/python3`; GTK tests via `xvfb-run --auto-servernum /usr/bin/python3 -m pytest`; pure-logic tests run headless.
- Version discipline: bump `VERSION` (0.72.1 → 0.73.0) + prepend a `debian/changelog` stanza in the finalize task.
- Ship **palette → text** only. The registry is designed to accept more entries later (e.g. image→text via the existing `TTLGCaptionImage`), but that is out of scope here (YAGNI).

## File Structure

- **`app/palette_prompt.py`** (new) — pure palette→text helpers: `load_palette(path)`, `literal_prompt(palette)`. Leaf module, no GTK.
- **`app/prompt_client.py`** (modify) — add `llm_polish_or_none(source, seed_text)`: LLM polish or `None` when unavailable, so callers get a deterministic literal fallback.
- **`app/artgen_kind.py`** (modify) — a palette artgen record classifies as seed kind `"palette"`.
- **`app/intent_vocab.py`** (modify) — add the `TTLGPaletteToPrompt` `Intent`, the `ADAPTERS` registry, and `adapter_for(seed_kind, input_kind)`.
- **`app/pipeline_engine.py`** (modify) — register the `TTLGPaletteToPrompt` handler (pure passthrough).
- **`app/recipes.py`** (modify) — `goals_for` offers adapter-reachable goals; `build_seed_spec` gains a `prepend_steps` param.
- **`app/pipeline_studio.py`** (modify) — `MuseView` gains a `compose_fn` seam; `_choose_goal` prepends the adapter (computing its prompt at seed time); `PipelineStudio` supplies `compose_fn`.
- **`app/main_window.py`** + **`app/artgen_gallery.py`** (modify) — collapse to one remix affordance per surface; drop `RemixPopover`/`_dispatch_remix` wiring.
- Tests live beside existing suites in `tests/`.

---

### Task 1: `palette_prompt` pure helpers

**Files:**
- Create: `app/palette_prompt.py`
- Test: `tests/test_palette_prompt.py`

**Interfaces:**
- Produces: `load_palette(path: str) -> dict | None`; `literal_prompt(palette: dict) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_palette_prompt.py
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import palette_prompt as pp


def test_literal_prompt_uses_hexes_and_lore():
    palette = {"name": "Dusk", "colors": [{"hex": "#1a2b3c"}, {"hex": "#ffcc00"}],
               "lore": "a moody teal-and-amber dusk"}
    out = pp.literal_prompt(palette)
    assert "#1a2b3c" in out and "#ffcc00" in out
    assert "palette:" in out
    assert "a moody teal-and-amber dusk" in out


def test_literal_prompt_caps_hexes_at_six():
    palette = {"colors": [{"hex": f"#00000{i}"} for i in range(9)]}
    assert pp.literal_prompt(palette).count("#") == 6


def test_literal_prompt_missing_colors_is_best_effort_not_raise():
    assert pp.literal_prompt({"lore": "just vibes"}) == "just vibes"
    assert pp.literal_prompt({}) == ""


def test_load_palette_reads_json(tmp_path):
    p = tmp_path / "pal.json"
    p.write_text(json.dumps({"colors": [{"hex": "#fff"}], "lore": "x"}))
    assert pp.load_palette(str(p))["lore"] == "x"


def test_load_palette_none_on_bad_input(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert pp.load_palette(str(bad)) is None
    assert pp.load_palette("") is None
    assert pp.load_palette(str(tmp_path / "missing.json")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_palette_prompt.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'palette_prompt'`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/palette_prompt.py
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""Pure palette -> text-prompt helpers (no GTK).

A palette artgen artifact is JSON: {"name", "colors":[{"hex","role"}], "lore"}.
`literal_prompt` turns it into a deterministic, palette-faithful prompt string
(the same colors+lore extraction previously trapped in
remix_popover._build_hint) — used as the seed for LLM polishing and as the
guaranteed fallback when no prompt LLM is running.
"""
from __future__ import annotations

import json


def load_palette(path: str) -> "dict | None":
    """Parse a palette JSON file. None on any failure (missing / unreadable /
    not an object)."""
    if not path:
        return None
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def literal_prompt(palette: dict) -> str:
    """Deterministic prompt from a palette dict: up to the first 6 hex colors
    plus the lore sentence(s). Best-effort — returns "" if there's nothing
    usable, never raises."""
    parts = []
    try:
        hexes = " ".join(
            c["hex"] for c in (palette.get("colors") or [])[:6]
            if isinstance(c, dict) and c.get("hex")
        )
        if hexes:
            parts.append(f"palette: {hexes}")
    except Exception:
        pass
    lore = (palette.get("lore") or "").strip() if isinstance(palette, dict) else ""
    if lore:
        parts.append(lore)
    return ", ".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_palette_prompt.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/palette_prompt.py tests/test_palette_prompt.py
git commit -m "feat(remix): palette_prompt pure helpers (colors+lore -> prompt text)"
```

---

### Task 2: `prompt_client.llm_polish_or_none`

**Files:**
- Modify: `app/prompt_client.py`
- Test: `tests/test_prompt_client_polish.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `llm_polish_or_none(source: str, seed_text: str) -> str | None` — the LLM-polished prompt when the prompt LLM is reachable and returns non-empty text; `None` otherwise (so the caller falls back to `palette_prompt.literal_prompt`).

**Why not reuse `generate_prompt(source, seed_text)`:** with a seed and the LLM *down*, `generate_prompt` ignores the seed and returns an unrelated algorithmic prompt (`app/prompt_client.py:60-119`), which would not be palette-faithful. `llm_polish_or_none` returns `None` in that case so the caller keeps the literal.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompt_client_polish.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import prompt_client


def test_polish_returns_none_when_llm_unavailable(monkeypatch):
    import generate_prompt as gp
    monkeypatch.setattr(gp, "_llm_available", lambda: False)
    assert prompt_client.llm_polish_or_none("video", "palette: #fff, calm") is None


def test_polish_returns_text_when_available(monkeypatch):
    import generate_prompt as gp
    monkeypatch.setattr(gp, "_llm_available", lambda: True)
    monkeypatch.setattr(gp, "_llm_polish", lambda seed, source: "a calm white dawn, drifting")
    assert prompt_client.llm_polish_or_none("video", "palette: #fff, calm") == \
        "a calm white dawn, drifting"


def test_polish_none_when_polish_empty(monkeypatch):
    import generate_prompt as gp
    monkeypatch.setattr(gp, "_llm_available", lambda: True)
    monkeypatch.setattr(gp, "_llm_polish", lambda seed, source: "")
    assert prompt_client.llm_polish_or_none("video", "x") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_prompt_client_polish.py -q`
Expected: FAIL with `AttributeError: module 'prompt_client' has no attribute 'llm_polish_or_none'`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/prompt_client.py`:

```python
def llm_polish_or_none(source: str, seed_text: str) -> "str | None":
    """Polish *seed_text* into a prompt for *source* USING THE LLM ONLY.

    Returns the polished string, or None when the prompt LLM isn't reachable
    or produces nothing — so callers can fall back to a deterministic literal
    (e.g. palette_prompt.literal_prompt) instead of `generate_prompt`'s
    seed-ignoring algorithmic fallback. Never raises.
    """
    try:
        import generate_prompt as _gp  # lazy: no GTK/network at import
        if not seed_text.strip() or not _gp._llm_available():
            return None
        polished = _gp._llm_polish(seed_text.strip(), source)
        return polished or None
    except Exception:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_prompt_client_polish.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/prompt_client.py tests/test_prompt_client_polish.py
git commit -m "feat(remix): prompt_client.llm_polish_or_none (LLM polish, None when down)"
```

---

### Task 3: Palette is a seedable kind

**Files:**
- Modify: `app/artgen_kind.py:18-41` (`artgen_seed_kind`)
- Test: `tests/test_artgen_kind.py` (add cases; create if absent)

**Interfaces:**
- Produces: `artgen_seed_kind(file_path, generator_type="palette")` returns `"palette"`; other `.json` still returns `None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_artgen_kind.py  (add these; keep any existing tests)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import artgen_kind


def test_palette_json_is_palette_kind():
    assert artgen_kind.artgen_seed_kind("/x/pal.json", "palette") == "palette"


def test_other_json_still_none():
    assert artgen_kind.artgen_seed_kind("/x/data.json", "somethingelse") is None
    assert artgen_kind.artgen_seed_kind("/x/data.json") is None


def test_existing_kinds_unchanged():
    assert artgen_kind.artgen_seed_kind("/x/a.png") == "image"
    assert artgen_kind.artgen_seed_kind("/x/a.gif") == "gif"
    assert artgen_kind.artgen_seed_kind("/x/a.md") == "text"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_artgen_kind.py -q`
Expected: FAIL on `test_palette_json_is_palette_kind` (`None != "palette"`).

- [ ] **Step 3: Write minimal implementation**

In `app/artgen_kind.py::artgen_seed_kind`, add a `generator_type` check **before** the extension checks (so `generator_type` — currently accepted but unused — now drives the palette kind):

```python
def artgen_seed_kind(file_path, generator_type=None):
    try:
        if not file_path:
            return None
        # A palette artgen record (JSON of colors+lore) is its own seed kind so
        # the Muse can offer palette-aware goals / adapters. Keyed on the
        # generator, not the bare .json ext, so unrelated JSON isn't miscast.
        if generator_type == "palette":
            return "palette"
        ext = PurePath(str(file_path)).suffix.lower()
        if not ext:
            return None
        if ext in _TEXT_EXTS:
            return "text"
        if ext in _IMAGE_EXTS:
            return "image"
        if ext in _GIF_EXTS:
            return "gif"
        return None
    except Exception:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_artgen_kind.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/artgen_kind.py tests/test_artgen_kind.py
git commit -m "feat(remix): classify palette artgen records as seed kind 'palette'"
```

---

### Task 4: `TTLGPaletteToPrompt` intent + adapter registry

**Files:**
- Modify: `app/intent_vocab.py` (add the Intent to `INTENTS`, add `ADAPTERS` + `adapter_for`)
- Test: `tests/test_intent_vocab_adapters.py`

**Interfaces:**
- Consumes: the `Intent` dataclass + `INTENTS` (`app/intent_vocab.py:27-70`).
- Produces:
  - `INTENTS["TTLGPaletteToPrompt"]` = `Intent(class_type="TTLGPaletteToPrompt", verb="Describe", noun="a palette", icon="🎨", outputs=("prompt",), model_label=None, input_key=None, input_kind=None, output_kind="text")`. **Source-style node** (`input_key=None`) — it holds a build-time-computed prompt and emits `text`.
  - `ADAPTERS: dict[tuple[str, str], str] = {("palette", "text"): "TTLGPaletteToPrompt"}`.
  - `adapter_for(seed_kind: str | None, input_kind: str | None) -> str | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intent_vocab_adapters.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import intent_vocab as iv


def test_palette_to_prompt_intent_shape():
    i = iv.intent_for("TTLGPaletteToPrompt")
    assert i.output_kind == "text"
    assert i.outputs == ("prompt",)
    assert i.input_key is None and i.input_kind is None  # source-style node


def test_adapter_for_palette_text():
    assert iv.adapter_for("palette", "text") == "TTLGPaletteToPrompt"


def test_adapter_for_unknown_is_none():
    assert iv.adapter_for("palette", "image") is None
    assert iv.adapter_for("image", "text") is None   # not shipped yet (YAGNI)
    assert iv.adapter_for(None, "text") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_intent_vocab_adapters.py -q`
Expected: FAIL — `intent_for` returns the generic fallback (`output_kind` is `None`), and `adapter_for` doesn't exist.

- [ ] **Step 3: Write minimal implementation**

Add the intent to the `INTENTS` dict literal (place it near the other text producers, e.g. after `TTLGPromptCompose`):

```python
    "TTLGPaletteToPrompt": Intent(
        class_type="TTLGPaletteToPrompt",
        verb="Describe",
        noun="a palette",
        icon="🎨",
        outputs=("prompt",),
        model_label=None,
        input_key=None,      # source-style: prompt is computed at seed time
        input_kind=None,
        output_kind="text",
    ),
```

Add near the lookups (after `compatible_intents`):

```python
# Cross-type adapters: (seed_kind, needed_input_kind) -> converter class_type.
# When a remix seed's kind doesn't directly match a goal's first-step input,
# the Muse consults this to offer the goal and prepend the converter. Ships
# palette->text only; more entries (e.g. ("image","text"):"TTLGCaptionImage")
# can be added later without touching call sites.
ADAPTERS: "dict[tuple[str, str], str]" = {
    ("palette", "text"): "TTLGPaletteToPrompt",
}


def adapter_for(seed_kind: "str | None", input_kind: "str | None") -> "str | None":
    """The converter class_type that turns a `seed_kind` artifact into an
    `input_kind` input, or None if no adapter is registered."""
    if not seed_kind or not input_kind:
        return None
    return ADAPTERS.get((seed_kind, input_kind))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_intent_vocab_adapters.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/intent_vocab.py tests/test_intent_vocab_adapters.py
git commit -m "feat(remix): TTLGPaletteToPrompt intent + adapter registry (adapter_for)"
```

---

### Task 5: `TTLGPaletteToPrompt` engine handler

**Files:**
- Modify: `app/pipeline_engine.py` (register the handler near `_h_prompt_compose`, `:647`)
- Test: `tests/test_pipeline_engine_palette_adapter.py`

**Interfaces:**
- Consumes: the `@register` decorator + `HANDLERS` (`app/pipeline_engine.py:141-151`).
- Produces: handler for `"TTLGPaletteToPrompt"` — a pure passthrough returning `{"prompt": inp.get("prompt", "")}` (the seed-time-computed literal, editable in RemixView). No `_backend_for` entry (pure, like `TTLGPromptCompose`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_engine_palette_adapter.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pipeline_engine as pe


def test_palette_to_prompt_handler_passes_prompt_through():
    assert "TTLGPaletteToPrompt" in pe.HANDLERS
    out = pe.HANDLERS["TTLGPaletteToPrompt"]("1", {"prompt": "a moody dusk"}, None)
    assert out == {"prompt": "a moody dusk"}


def test_palette_to_prompt_handler_empty_default():
    assert pe.HANDLERS["TTLGPaletteToPrompt"]("1", {}, None) == {"prompt": ""}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_pipeline_engine_palette_adapter.py -q`
Expected: FAIL — `"TTLGPaletteToPrompt" not in pe.HANDLERS`.

- [ ] **Step 3: Write minimal implementation**

Add beside `_h_prompt_compose` (`app/pipeline_engine.py:647`):

```python
@register("TTLGPaletteToPrompt")
def _h_palette_to_prompt(nid, inp, ctx):
    """Adapter node: emits the prompt that was composed from a palette at seed
    time (LLM-polished or the deterministic colors+lore literal). Pure — the
    palette was consumed when the pipeline was built, so there's no run-time
    LLM/backend dependency here (mirrors TTLGPromptCompose)."""
    return {"prompt": inp.get("prompt", "")}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_pipeline_engine_palette_adapter.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline_engine.py tests/test_pipeline_engine_palette_adapter.py
git commit -m "feat(remix): TTLGPaletteToPrompt engine handler (pure prompt passthrough)"
```

---

### Task 6: `recipes` — offer adapter-reachable goals + `prepend_steps`

**Files:**
- Modify: `app/recipes.py` (`goals_for` scoped filter `:226-254`; `build_seed_spec` `:257-270`)
- Test: `tests/test_recipes_adapters.py`

**Interfaces:**
- Consumes: `intent_vocab.adapter_for` (Task 4); `spec_remix.seed_spec` (unchanged).
- Produces:
  - `goals_for(seed_output_kind=...)` also returns a scoped goal when `adapter_for(seed_output_kind, first_step_input_kind)` is not None.
  - `build_seed_spec(goal, *, seed_artifact=None, prepend_steps=())` — `prepend_steps` is a `tuple[tuple[str, dict], ...]` prepended before `goal.recipe_steps` (used to insert the adapter node); default `()` preserves current behavior.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_recipes_adapters.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import recipes


def _animatediff_goal():
    # A scoped goal whose first step needs text (like AnimateDiff).
    return recipes.Goal("anim", "An animation", "🕺", "gif", "scoped",
                        (("TTLGAnimateDiff", {}),))


def test_goals_for_offers_palette_reachable_goal(monkeypatch):
    monkeypatch.setattr(recipes, "_CURATED", (_animatediff_goal(),))
    ids = {g.id for g in recipes.goals_for(seed_output_kind="palette")}
    assert "anim" in ids   # offered via the palette->text adapter


def test_goals_for_still_excludes_unreachable(monkeypatch):
    # A goal whose first step needs 'image' is not reachable from a palette.
    g = recipes.Goal("vid", "A video", "🎬", "video", "scoped",
                    (("TTLGImageToVideo", {}),))
    monkeypatch.setattr(recipes, "_CURATED", (g,))
    ids = {x.id for x in recipes.goals_for(seed_output_kind="palette")}
    assert "vid" not in ids


def test_build_seed_spec_prepends_adapter_and_wires():
    goal = _animatediff_goal()
    spec = recipes.build_seed_spec(
        goal, seed_artifact=None,
        prepend_steps=(("TTLGPaletteToPrompt", {"prompt": "a moody dusk"}),),
    )
    # Node 1 is the adapter holding the editable prompt; node 2 (AnimateDiff)
    # takes its prompt wired from node 1's "prompt" output.
    assert spec["1"]["class_type"] == "TTLGPaletteToPrompt"
    assert spec["1"]["inputs"]["prompt"] == "a moody dusk"
    assert spec["2"]["class_type"] == "TTLGAnimateDiff"
    assert spec["2"]["inputs"]["prompt"] == ["1", "prompt"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_recipes_adapters.py -q`
Expected: FAIL — `goals_for` doesn't offer "anim" for a palette; `build_seed_spec` has no `prepend_steps` kwarg (`TypeError`).

- [ ] **Step 3: Write minimal implementation**

In `app/recipes.py`, import `adapter_for` (alongside the existing `from intent_vocab import intent_for`):

```python
from intent_vocab import intent_for, adapter_for
```

In `goals_for`, change the scoped filter (currently
`if intent_for(first_ct).input_kind == seed_output_kind:`):

```python
        first_ct = g.recipe_steps[0][0]
        first_input = intent_for(first_ct).input_kind
        if first_input == seed_output_kind or \
                adapter_for(seed_output_kind, first_input) is not None:
            result.append(g)
```

Replace `build_seed_spec`:

```python
def build_seed_spec(goal: Goal, *,
                     seed_artifact: "tuple[str, str] | None" = None,
                     prepend_steps: "tuple[tuple[str, dict], ...]" = ()) -> dict:
    """Materialize *goal* into a runnable spec via `spec_remix.seed_spec`.

    `prepend_steps` are inserted before the goal's own steps (used to prepend a
    cross-type adapter node, e.g. TTLGPaletteToPrompt). `seed_spec` chains each
    step's primary output into the next step's input, so a text-output adapter
    wires straight into a text-input first goal step.
    """
    steps = list(prepend_steps) + list(goal.recipe_steps)
    return seed_spec(steps, seed_artifact=seed_artifact)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_recipes_adapters.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/recipes.py tests/test_recipes_adapters.py
git commit -m "feat(remix): recipes offers adapter-reachable goals + prepend_steps seeding"
```

---

### Task 7: Muse — compose the palette prompt + prepend the adapter

**Files:**
- Modify: `app/pipeline_studio.py` (`MuseView.__init__` add `compose_fn`; `MuseView._choose_goal` `:2460-2476`; `PipelineStudio` supplies `compose_fn`)
- Test: `tests/test_muse_palette_adapter.py`

**Interfaces:**
- Consumes: `palette_prompt.load_palette`/`literal_prompt` (Task 1); `intent_vocab.adapter_for` + `intent_for` (Task 4); `recipes.build_seed_spec(..., prepend_steps=)` (Task 6).
- Produces: `MuseView(..., compose_fn=None)`. `compose_fn(medium: str, literal: str, on_done: Callable[[str], None]) -> None` — runs off-thread, calls `on_done` with the best prompt (LLM-polished or the literal). `None` → synchronous literal-only path (test/standalone safe).
  - `MuseView._prompt_source_for_output(output_kind)` maps `gif`/`video`→`"video"`, `image`→`"image"`, else `"video"`.

- [ ] **Step 1: Write the failing test** (headless — `compose_fn=None`, no threads)

```python
# tests/test_muse_palette_adapter.py
import json, sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
try:
    import gi; gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()
except Exception:
    pytest.skip("no GTK display", allow_module_level=True)

import pipeline_studio, recipes


def _palette_file(tmp_path):
    p = tmp_path / "pal.json"
    p.write_text(json.dumps({"colors": [{"hex": "#1a2b3c"}], "lore": "moody dusk"}))
    return str(p)


def test_choose_goal_palette_prepends_adapter(tmp_path, monkeypatch):
    goal = recipes.Goal("anim", "An animation", "🕺", "gif", "scoped",
                        (("TTLGAnimateDiff", {}),))
    captured = {}
    muse = pipeline_studio.MuseView(goals_fn=lambda k: [goal])   # compose_fn=None -> sync literal
    muse.connect("goal-chosen", lambda _w, spec: captured.update(spec=spec))
    muse.set_context(seed_artifact=(_palette_file(tmp_path), "palette", None))

    muse._choose_goal(goal)

    spec = captured["spec"]
    assert spec["1"]["class_type"] == "TTLGPaletteToPrompt"
    # literal fallback carries the palette colors + lore into the prompt
    assert "#1a2b3c" in spec["1"]["inputs"]["prompt"]
    assert "moody dusk" in spec["1"]["inputs"]["prompt"]
    assert spec["2"]["class_type"] == "TTLGAnimateDiff"
    assert spec["2"]["inputs"]["prompt"] == ["1", "prompt"]


def test_choose_goal_compose_fn_supplies_llm_prompt(tmp_path):
    goal = recipes.Goal("anim", "An animation", "🕺", "gif", "scoped",
                        (("TTLGAnimateDiff", {}),))
    # compose_fn that "polishes" synchronously for the test.
    def compose(medium, literal, on_done):
        on_done(f"LLM[{medium}]: shimmering {literal}")
    captured = {}
    muse = pipeline_studio.MuseView(goals_fn=lambda k: [goal], compose_fn=compose)
    muse.connect("goal-chosen", lambda _w, spec: captured.update(spec=spec))
    muse.set_context(seed_artifact=(_palette_file(tmp_path), "palette", None))

    muse._choose_goal(goal)

    assert captured["spec"]["1"]["inputs"]["prompt"].startswith("LLM[video]: shimmering")


def test_choose_goal_non_palette_unchanged(tmp_path):
    # A direct-match text seed still goes through the normal (no-adapter) path.
    goal = recipes.Goal("illus", "An illustration", "🖼", "image", "scoped",
                        (("TTLGTextToImage", {}),))
    captured = {}
    muse = pipeline_studio.MuseView(goals_fn=lambda k: [goal])
    muse.connect("goal-chosen", lambda _w, spec: captured.update(spec=spec))
    muse.set_context(seed_artifact=("some lore text", "text", None))
    muse._choose_goal(goal)
    assert "TTLGPaletteToPrompt" not in {n.get("class_type") for n in captured["spec"].values() if isinstance(n, dict)}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_muse_palette_adapter.py -q`
Expected: FAIL — `MuseView` has no `compose_fn` kwarg / no adapter prepend (the palette seed raises or produces a blank/mismatched spec).

- [ ] **Step 3: Write minimal implementation**

`MuseView.__init__`: accept and store `compose_fn=None` (add to the signature and `self._compose_fn = compose_fn`). Add the source-mapping helper and rewrite `_choose_goal`:

```python
    def _prompt_source_for_output(self, output_kind):
        return {"gif": "video", "video": "video", "image": "image"}.get(
            output_kind, "video")

    def _choose_goal(self, goal: "recipes.Goal") -> None:
        """Build the seed spec and emit `goal-chosen`. When the seed's kind
        doesn't match the goal's first step but an adapter exists (e.g. a
        palette feeding a text-input goal), compose the adapter's prompt from
        the palette (LLM-polished via compose_fn, deterministic literal
        fallback) and prepend a TTLGPaletteToPrompt step."""
        import palette_prompt
        from intent_vocab import adapter_for, intent_for

        seed_pair = None
        seed_kind = None
        if self._seed_artifact is not None:
            path, seed_kind, _thumb = self._seed_artifact
            seed_pair = (path, seed_kind)

        first_ct = goal.recipe_steps[0][0]
        first_input = intent_for(first_ct).input_kind
        needs_adapter = (
            seed_kind is not None
            and seed_kind != first_input
            and adapter_for(seed_kind, first_input) == "TTLGPaletteToPrompt"
        )

        if not needs_adapter:
            try:
                spec = recipes.build_seed_spec(goal, seed_artifact=seed_pair)
            except ValueError as exc:
                self._show_message(f"Couldn't build that pipeline: {exc}")
                return
            self._hide_message()
            self.emit("goal-chosen", spec)
            return

        # Palette -> prompt adapter path.
        palette = palette_prompt.load_palette(self._seed_artifact[0]) or {}
        literal = palette_prompt.literal_prompt(palette)
        medium = self._prompt_source_for_output(goal.output_kind)

        def _emit(prompt_text):
            step = ("TTLGPaletteToPrompt",
                    {"prompt": prompt_text, "_source_palette": self._seed_artifact[0]})
            spec = recipes.build_seed_spec(goal, seed_artifact=None,
                                            prepend_steps=(step,))
            self._hide_message()
            self.emit("goal-chosen", spec)

        if self._compose_fn is None:
            _emit(literal)                      # sync literal-only (tests/standalone)
        else:
            self._show_message("Composing a prompt from your palette…")
            self._compose_fn(medium, literal, _emit)
```

In `PipelineStudio.__init__`, build `compose_fn` from `prompt_client` off-thread and pass it to `MuseView(...)`:

```python
        def _compose_fn(medium, literal, on_done):
            def run():
                try:
                    import prompt_client
                    polished = prompt_client.llm_polish_or_none(medium, literal)
                except Exception:
                    polished = None
                GLib.idle_add(on_done, polished or literal)
            threading.Thread(target=run, daemon=True).start()
        # ... pass compose_fn=_compose_fn where MuseView is constructed
```

(Find the existing `MuseView(...)` construction in `PipelineStudio.__init__` and add `compose_fn=_compose_fn`. `threading` and `GLib` are already imported in `pipeline_studio.py`; confirm and add if missing.)

- [ ] **Step 4: Run test to verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_muse_palette_adapter.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline_studio.py tests/test_muse_palette_adapter.py
git commit -m "feat(remix): Muse composes palette->prompt + prepends adapter step (LLM, literal fallback)"
```

---

### Task 8: One remix affordance (drop the popover)

**Files:**
- Modify: `app/main_window.py` (native card buttons `:2218-2239` + handlers `:2680-2688`; detail buttons `:3273-3286` + handlers `:3579-3590`; card-select wiring `:7313-7314`; galleries wiring `:5689-5706`; artgen-detail wiring `:5885-5887`; remove `_on_remix_card` `:7372` + `_dispatch_remix` `:7387`)
- Modify: `app/artgen_gallery.py` (buttons `:666-687`; drop `on_remix`, keep one)
- Test: `tests/test_remix_single_affordance.py`

**Interfaces:**
- Consumes: `_remix_as_pipeline` (`app/main_window.py:7209`, unchanged — now the single handler; benefits from Task 3 so a palette resolves to a real seed).
- Produces: exactly one remix button per surface, wired to `_remix_as_pipeline`; `MainWindow` no longer references `RemixPopover` / `_dispatch_remix` / `_on_remix_card`.

**Change detail (per surface):** keep the single button labeled **"🔀 Remix"**, tooltip "Remix this into a pipeline", wired to the pipeline handler; delete the second `🧩` button and the popover path.
- Native `GenerationCard` (`:2218-2239`): remove the `🔀 Remix`→`_on_remix_clicked` popover button; relabel the remaining `_remix_as_pipeline_btn` to `"🔀 Remix"` with the pipeline tooltip. Its handler `_on_remix_as_pipeline_clicked` (`:2685`) stays; delete `_on_remix_clicked` (`:2680`).
- `DetailPanel` (`:3273-3286`, handlers `:3579-3590`): same — keep the pipeline button, relabel to `"🔀 Remix"`, delete the popover button + `DetailPanel._on_remix_clicked`. `show_record(record, remix_cb, remix_as_pipeline_cb=None)` keeps its signature; native card-select (`:7313`) passes `remix_cb=None` (or drops it) and keeps `remix_as_pipeline_cb=self._remix_as_pipeline`.
- `ArtgenGallery` (`:666-687`): remove the `🔀 Remix`→`on_remix` button; relabel the `🧩` button to `"🔀 Remix"` (pipeline tooltip). Drop the `on_remix` attribute (`:367`); keep `on_remix_as_pipeline`.
- MainWindow wiring: drop `remix_cb=self._on_remix_card` (`:5692`) and `self._artgen_gallery.on_remix = ...` (`:5705`) and `self._artgen_detail.on_remix = ...` (`:5885`). Delete `_on_remix_card` and `_dispatch_remix`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_remix_single_affordance.py
import inspect, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import main_window as mw


def test_mainwindow_no_popover_remix_symbols():
    # The 🔀 popover path is gone; only the pipeline path remains.
    assert not hasattr(mw.MainWindow, "_on_remix_card")
    assert not hasattr(mw.MainWindow, "_dispatch_remix")
    assert hasattr(mw.MainWindow, "_remix_as_pipeline")


def test_mainwindow_source_does_not_wire_remixpopover():
    src = inspect.getsource(mw)
    # No live construction of RemixPopover from main_window anymore.
    assert "RemixPopover(" not in src


def test_artgen_gallery_has_single_remix_callback():
    import artgen_gallery as ag
    g = ag.ArtgenGallery.__new__(ag.ArtgenGallery)
    # on_remix (popover) attribute is gone; on_remix_as_pipeline remains the seam.
    assert not hasattr(ag.ArtgenGallery, "on_remix") or "on_remix_as_pipeline" in inspect.getsource(ag)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_remix_single_affordance.py -q`
Expected: FAIL — `_on_remix_card`/`_dispatch_remix` still exist and `RemixPopover(` still appears in `main_window.py`.

- [ ] **Step 3: Write minimal implementation**

Apply the per-surface changes above. Concretely:
- Native card (`:2218-2239`): delete the first button block (the `🔀 Remix` / `_on_remix_clicked` one). Change `self._remix_as_pipeline_btn = Gtk.Button(label="🧩 Pipeline")` → `label="🔀 Remix"` and its tooltip → `"Remix this into a pipeline"`.
- Delete `GenerationCard._on_remix_clicked` (`:2680-2683`).
- Detail (`:3273-3286`): delete the `remix_btn`(`🔀 Remix`)→`_on_remix_clicked` block; change `self._remix_as_pipeline_btn = Gtk.Button(label="🧩 Remix as pipeline…")` → `label="🔀 Remix"`, tooltip `"Remix this into a pipeline"`.
- Delete `DetailPanel._on_remix_clicked` (`:3579-3588`, the one that builds `RemixPopover`).
- `:7313-7314`: change to `self._detail.show_record(record, None, remix_as_pipeline_cb=self._remix_as_pipeline)`.
- `:5689-5706`: remove `remix_cb=self._on_remix_card,` from `shared_cbs`; remove `self._artgen_gallery.on_remix = self._on_remix_card`.
- `:5885-5887`: remove `self._artgen_detail.on_remix = self._on_remix_card`.
- Delete `MainWindow._on_remix_card` (`:7372-7385`) and `MainWindow._dispatch_remix` (`:7387-7426`).
- `app/artgen_gallery.py`: delete the `seed_btn` (`🔀 Remix`/`on_remix`) block (`:666-675`); relabel `pipeline_btn` to `Gtk.Button(label="🔀 Remix")` with tooltip `"Remix this into a pipeline"`; remove the `self.on_remix` attribute at `:367`.

If `GalleryWidget.__init__`/`show_record` still declare `remix_cb`, leave the parameter (default `None`) to avoid a wider signature change — just stop passing a real callback. Confirm no remaining caller passes `remix_cb=` a non-None value.

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_remix_single_affordance.py -q`
Then GTK regression: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_gallery_double_click_fullscreen.py tests/test_artgen_gallery_preview.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main_window.py app/artgen_gallery.py tests/test_remix_single_affordance.py
git commit -m "feat(remix): collapse to one remix affordance (pipeline); drop RemixPopover wiring"
```

---

### Task 9: End-to-end guard + finalize

**Files:**
- Test: `tests/test_palette_to_animatediff_e2e.py`
- Modify: `VERSION`, `debian/changelog`, `CLAUDE.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the failing test** (headless — pure spec assembly, no GTK)

```python
# tests/test_palette_to_animatediff_e2e.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import recipes, palette_prompt, pipeline_engine as pe


def test_palette_to_animatediff_pipeline_assembles_and_runs_dry():
    goal = recipes.Goal("anim", "An animation", "🕺", "gif", "scoped",
                        (("TTLGAnimateDiff", {}),))
    literal = palette_prompt.literal_prompt(
        {"colors": [{"hex": "#1a2b3c"}], "lore": "moody dusk"})
    spec = recipes.build_seed_spec(
        goal, seed_artifact=None,
        prepend_steps=(("TTLGPaletteToPrompt", {"prompt": literal}),))
    # The adapter's prompt reaches AnimateDiff via a wire.
    assert spec["2"]["inputs"]["prompt"] == ["1", "prompt"]
    # And the assembled pipeline resolves the wire when run (dry).
    order = pe.topo_order({k: v for k, v in spec.items() if not k.startswith("_")})
    assert order == ["1", "2"]
    out1 = pe.HANDLERS["TTLGPaletteToPrompt"]("1", spec["1"]["inputs"], None)
    assert "#1a2b3c" in out1["prompt"] and "moody dusk" in out1["prompt"]
```

- [ ] **Step 2: Run test to verify it fails, then passes**

Run: `/usr/bin/python3 -m pytest tests/test_palette_to_animatediff_e2e.py -q`
(Should PASS immediately given Tasks 1/4/5/6 — this is a regression lock, not new code. If it fails, a prior task regressed.)

- [ ] **Step 3: Full suite**

Run:
```bash
xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q \
  --deselect tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module \
  --deselect tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes \
  --deselect tests/test_role_zone_panel.py::test_prompt_field_hidden_but_still_collected_for_artgen
```
Expected: all pass (the three deselects are the known environment-order flakes documented in CLAUDE.md).

- [ ] **Step 4: Version + docs**

- `VERSION`: `0.72.1` → `0.73.0`.
- Prepend a `debian/changelog` stanza (0.73.0) describing: one remix path (pipelines), palette seedable, `Palette → Prompt` adapter (LLM-written, literal fallback), Muse offers adapter-reachable goals.
- Add a CLAUDE.md section "Remix is pipelines (v0.73.0)": one affordance → Muse; adapter registry (`intent_vocab.adapter_for`) + `TTLGPaletteToPrompt` (source node, seed-time-composed prompt, pure engine handler); `palette_prompt` + `prompt_client.llm_polish_or_none`; `recipes.build_seed_spec(prepend_steps=)`; Muse `compose_fn` (off-thread LLM, literal fallback); note the `RemixPopover`/`remix_dispatch` modules remain in-tree but unwired.

- [ ] **Step 5: Commit**

```bash
git add VERSION debian/changelog CLAUDE.md tests/test_palette_to_animatediff_e2e.py
git commit -m "feat(remix): palette->AnimateDiff e2e guard; VERSION 0.73.0 + docs"
```

---

## Self-Review

**Spec coverage:**
- One remix path / drop popover → Task 8. ✓
- Palette seedable → Task 3. ✓
- Adapter registry + `Palette → Prompt` (source node, editable literal, LLM at seed time, literal fallback) → Tasks 1, 2, 4, 5, 7. ✓
- Muse offers adapter-reachable goals + auto-inserts adapter → Tasks 6, 7. ✓
- Error handling (no LLM → literal; bad JSON → best-effort; missing seed → existing guards) → Tasks 1, 2, 7 (`load_palette` None-safe; `_choose_goal` uses `or {}`). ✓
- YAGNI (palette only; caption a future registry entry) → Task 4 note. ✓
- `collect()` invariant → not touched by these tasks (RemixView field editing unchanged; adapter prompt is a normal editable literal). ✓

**Placeholder scan:** No TBD/TODO; every code step has real code. Task 7's "find the existing `MuseView(...)` construction" is a locate-then-edit instruction with the exact kwarg to add, not a placeholder.

**Type consistency:** `adapter_for(seed_kind, input_kind) -> str | None` used identically in Tasks 4/6/7. `compose_fn(medium, literal, on_done)` defined in Task 7 and matched by `PipelineStudio._compose_fn`. `build_seed_spec(..., prepend_steps=())` defined in Task 6, called with the same kwarg in Tasks 6/7/9. `TTLGPaletteToPrompt` intent (`output_kind="text"`, `outputs=("prompt",)`, `input_key=None`) is consistent with the handler returning `{"prompt": ...}` and `seed_spec` wiring `["1", "prompt"]`.

**Note for executor:** Task 7 assumes `MuseView.__init__` currently takes `goals_fn` and `PipelineStudio.__init__` constructs `MuseView`. Confirm the exact `MuseView(...)` call site and that `threading`/`GLib` are imported in `pipeline_studio.py` before adding `compose_fn` (add the import if missing). If `MuseView`'s signature differs, thread `compose_fn` through the same way `goals_fn` is passed.
