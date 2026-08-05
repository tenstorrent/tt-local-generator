# Remix → Pipelines Unification + Cross-Type Adapters — Design

**Date:** 2026-08-05
**Branch:** `feat/pipeline-editor`
**Status:** Approved (brainstorm), pending implementation plan

## Problem

The app has two remix affordances on every gallery card / detail pane —
**🔀 Remix** (a quick popover, `app/remix_popover.py` → `_dispatch_remix`) and
**🧩 Remix as pipeline…** (`MainWindow._remix_as_pipeline`). Both were already
rewired (SP-3d-5) to open **Pipeline Studio's Muse**, so they are redundant, and
🔀 is the *worse* of the two: `RemixPopover._build_hint` computes a
palette→prompt hint (colors + lore text) that `_dispatch_remix`
(`app/main_window.py:7414`) then **discards**, opening a blank Muse from only a
seed image / ref video.

Two concrete gaps follow:

1. **"Everything is a pipeline" is only half-true.** Two buttons remain, and
   non-image/video/text artifacts can't seed a pipeline at all
   (`artgen_kind.artgen_seed_kind(".json")` → `None`, so a palette → **blank
   Muse** via either button).
2. **Cross-type remix is impossible.** Going from a **color palette** to
   **AnimateDiff** requires converting the palette into the *text prompt*
   AnimateDiff needs (`TTLGAnimateDiff.input_key="prompt"`,
   `input_kind="text"`, `app/intent_vocab.py:192–202`). The conversion logic
   *exists* (`remix_popover._build_hint`, `app/remix_popover.py:103–120`) but is
   trapped in the dead-end popover; the pipeline/intent engine has **no palette
   node and no palette→text adapter**.

## Goal

One coherent remixing model: **remix = open a pipeline seeded from this
artifact.** Any artifact can flow into any next step; when the seed's kind
doesn't match what the next step needs, the Muse **auto-inserts a visible,
editable adapter step** that converts it. The driving case:

> Click a color scheme → 🔀 Remix → pick AnimateDiff → the Muse shows a
> `Palette → Prompt` step pre-filled with a prompt built from the palette's
> colors + lore → tweak it → Run → animated GIF.

## Decisions (locked in brainstorm)

- **Consolidate to one remix path, always a pipeline.** Drop the 🔀 popover.
- **Cross-type conversion is an auto-inserted, visible, editable step** (not an
  invisible fold-in, not a manual add).
- **The `Palette → Prompt` adapter is LLM-written with a literal fallback:** ask
  the prompt LLM (Qwen prompt-server) to turn colors + lore into an evocative
  prompt for the target medium; if no LLM server is up, fall back to the literal
  "palette: #hex #hex… + lore" string so it always produces something.

## Global Constraints

- Display/behavior only where it touches Create/galleries; **`collect()` /
  `_collect_params()` output must stay byte-for-byte identical** where those
  widgets are unchanged (existing invariant, guarded by tests).
- GTK is single-threaded: any LLM call runs off the main thread; UI updates via
  `GLib.idle_add`. The prompt LLM call already has an async client pattern
  (`prompt_client.generate_prompt`, used by Create's ✨ Inspire).
- `_CSS`/`b"""…"""` byte literals are **ASCII-only**; glyphs live in Python `str`
  labels.
- System `/usr/bin/python3`; tests via
  `xvfb-run --auto-servernum /usr/bin/python3 -m pytest`.
- Version discipline: bump `VERSION` + prepend a `debian/changelog` stanza.
- Do not break the pipeline spec contract: a spec is
  `{node_id: {"class_type", "inputs"}}`, wires are `[src_node_id, output_key]`
  (`app/spec_remix.py:193–197`); `editable_params` excludes wires
  (`app/spec_remix.py:107–137`).

## Components

### 1. One remix affordance (drop the popover)

- Every card/detail keeps a single **"🔀 Remix"** button routed to the existing
  `MainWindow._remix_as_pipeline` (`app/main_window.py:7209`) →
  `PipelineStudio.show_muse(seed_artifact=…)`.
- Remove the wiring of `🔀 RemixPopover` / `_dispatch_remix`
  (`app/main_window.py:7372–7425`) and the second button:
  - Native cards/detail: `remix_cb` / the `🔀` button at
    `app/main_window.py:2224`, `:3276`, `:3582`, `:5692`, `:5705`.
  - Artgen gallery: `on_remix` + `🔀 Remix` button
    (`app/artgen_gallery.py:666–671`).
- Keep exactly one button per surface (the label stays "🔀 Remix"; it now
  *always* means "seed a pipeline"). The `🧩 Remix as pipeline…` label collapses
  into it.
- `remix_popover.py` / `remix_dispatch.py` become unreferenced by the UI. Leave
  the modules in-tree (their unit tests still pass) but assert in a guard test
  that `MainWindow` no longer imports/wires them.

### 2. Seedable artgen artifacts (palette → a real kind)

- `app/artgen_kind.py::artgen_seed_kind(ext)` currently maps `.json` → `None`
  (`:39`). Introduce a **`"palette"`** seed kind for palette JSON so
  `_resolve_artgen_media_seed` (`app/main_window.py:7276`) returns a real seed
  tuple `(path, "palette", thumb)` instead of `None`.
- Resolution of *which* artgen type a `.json` is uses the record's
  `generator_type` (e.g. `"palette"`), not the bare extension, so unrelated
  `.json` artgen outputs are unaffected.
- Only `palette` is made seedable in this spec (YAGNI); the mechanism generalizes
  (§3) so other types register later.

### 3. Adapter registry + `Palette → Prompt`

**Adapter registry (the seam).** A small pure mapping
`(seed_kind, needed_input_kind) → adapter_class_type`, e.g.:

```
("palette", "text") -> "TTLGPaletteToPrompt"     # new (this spec)
("image",   "text") -> "TTLGCaptionImage"        # already exists in intent_vocab
```

Lives beside `intent_vocab` (a new small module or a dict in `spec_remix`),
queried by the Muse (§4). It does NOT replace `input_kind`/`output_kind`
matching — it augments it: an adapter is only consulted when the seed kind does
not directly match.

**`TTLGPaletteToPrompt` — a materialize-at-seed-time text step.** Because
RemixView can edit literals but not wires (`app/spec_remix.py:107–137`,
`:166`), the adapter is realized so its generated prompt is an **editable
literal**, not a run-time wire:

- Add a `Palette → Prompt` `Intent` to `intent_vocab.INTENTS`
  (`input_kind="palette"`, `output_kind="text"`, `input_key` = the palette
  path/artifact, a distinct verb/icon so it reads clearly in the step card).
- At **seed time** (when the goal is chosen in the Muse), the conversion runs
  once: read the palette JSON (`colors[].hex`, `lore`; schema
  `app/artgen/generators/palette.py:22–43`), build a base description, and call
  `prompt_client.generate_prompt(...)` to produce an evocative prompt for the
  target medium. The result pre-fills the adapter step's editable `prompt`
  literal.
- **Literal fallback:** if the LLM call fails / no server, pre-fill with the
  deterministic string produced by the extracted-and-shared helper (moved out of
  `remix_popover._build_hint` into a reusable pure function, e.g.
  `palette_prompt.literal_prompt(palette_json) -> str`). So the step always has
  content.
- The adapter step's `prompt` literal is **wired into** the downstream step's
  `input_key` (AnimateDiff's `prompt`). Editing the literal in RemixView changes
  what flows downstream (per `apply_edits`, `app/spec_remix.py:140–169`).

**Note on "LLM at seed time":** the LLM call happens once, when the user picks
the goal (not on every keystroke, not at Run). This keeps the generated prompt
editable *before* Run and avoids a run-time dependency on the LLM server
(AnimateDiff itself needs no server — `pipeline_engine` marks it `CHIPS_FREE`,
`:1175`). The call is made off the GTK main thread; the Muse shows a brief
"composing prompt…" state and reveals the seeded pipeline when it returns.

### 4. Muse: offer adapter-reachable goals + insert the adapter

- **Goal filtering.** `MuseView.set_context(seed_artifact=(path,kind,thumb))`
  (`app/pipeline_studio.py:2376`) currently offers only goals whose step-0 input
  kind directly equals the seed kind (`seed_output_kind = kind`, `:2397`).
  Extend: also offer a goal if the **adapter registry** (§3) has an entry
  `(seed_kind → goal_step0_input_kind)`. So a `palette` seed now surfaces
  text-input goals like AnimateDiff.
- **Adapter insertion.** `spec_remix.seed_spec(steps, seed_artifact)`
  (`app/spec_remix.py:335–399`) / `recipes.build_seed_spec` currently consume the
  seed on step 0 only when kinds match (`:382–390`). Extend: when the seed kind
  does not match step 0's `input_kind` but an adapter exists, **prepend the
  adapter step** as the new step 0 — it consumes the seed (`input_kind="palette"`)
  and its `text` output wires into the original step 0's `input_key`. The rest of
  the chain wiring is unchanged.
- Resulting spec for the driving case:
  `palette → [TTLGPaletteToPrompt] → [TTLGAnimateDiff] → gif`, with the adapter's
  `prompt` pre-filled + editable.

## Data flow (driving case)

```
[palette .json record]
      │  🔀 Remix  →  _remix_as_pipeline  →  show_muse(seed=(path,"palette",thumb))
      ▼
[Muse]  offers AnimateDiff (palette→text adapter exists)
      │  pick goal → seed-time: palette JSON → prompt LLM (fallback: literal)
      ▼
[RemixView] pipeline:
   palette ─▶ (Palette → Prompt)  ─prompt(text, editable literal)▶  (AnimateDiff) ─▶ gif
      │  user tweaks the pre-filled prompt
      ▼  Run  →  pipeline_engine.run(spec)  →  animated GIF
```

## Error handling

- **No LLM server at seed time:** literal fallback (§3) — the step still
  pre-fills; no failure surfaced.
- **Malformed palette JSON** (missing `colors`): the shared literal helper
  returns a best-effort string (name/lore only, or a neutral prompt); never
  raises into the Muse.
- **Seed artifact missing on disk:** existing `_remix_as_pipeline` /
  `_resolve_artgen_media_seed` guards apply (they already return `None`/skip);
  palette seedability doesn't change that.
- **Unknown seed kind with no adapter and no direct match:** the goal simply
  isn't offered (current behavior preserved).

## Testing

All GTK-widget tests under `xvfb`; pure logic tested headless.

- **`palette_prompt.literal_prompt`** (pure): valid palette → `"palette: #hex…
  + lore"`; missing `colors` → best-effort, no raise.
- **Adapter registry** (pure): `("palette","text")` resolves to
  `TTLGPaletteToPrompt`; `("image","text")` resolves to the existing
  `TTLGCaptionImage`; unknown pair → `None`.
- **`artgen_seed_kind` / `_resolve_artgen_media_seed`**: a palette record →
  `(path, "palette", thumb)` (was `None`).
- **`seed_spec` adapter insertion** (pure): palette seed + a `text`-input goal →
  spec has the adapter as step 0 consuming the palette, wired into the goal's
  `input_key`; a direct-match seed is unchanged (regression).
- **LLM path** (mocked `generate_prompt`): seed-time conversion pre-fills the
  adapter's `prompt` literal from the LLM result; on raised/empty LLM →
  literal-fallback pre-fill.
- **Muse goal filtering** (xvfb): a `palette` seed offers AnimateDiff (and other
  text-input goals) via the adapter; a direct-match seed's goal list is
  unchanged.
- **Consolidation guard**: `MainWindow` wires exactly one remix affordance per
  surface and no longer references `RemixPopover` / `_dispatch_remix`;
  `_remix_as_pipeline` is the single handler.
- **`collect()` invariant**: RemixView `_collect_edits` for a pipeline with an
  adapter step reproduces an untouched run exactly (no retype + no applied pill →
  the pre-filled prompt flows unchanged).

## Out of scope (YAGNI)

- Adapters for other artgen types (ansi / verse / svg / codeart). The registry
  makes them cheap to add later; only `palette` ships now.
- Editing pipeline *wires* in RemixView (still literal-only).
- Re-adding the popover's old inline single-step "regenerate as X" or its
  target-type switch — superseded by the Muse.
- Moving/removing `remix_popover.py` / `remix_dispatch.py` source (only their UI
  wiring is removed).

## Critical files

- `app/main_window.py` — remove `_dispatch_remix`/`RemixPopover` wiring; keep
  `_remix_as_pipeline` as the one handler; `_resolve_artgen_media_seed`.
- `app/artgen_kind.py` — `artgen_seed_kind` palette mapping.
- `app/artgen_gallery.py` — single remix button.
- `app/intent_vocab.py` — `TTLGPaletteToPrompt` Intent (+ reuse `TTLGCaptionImage`).
- `app/spec_remix.py` — adapter registry + `seed_spec` adapter insertion.
- `app/pipeline_studio.py` — `MuseView.set_context` goal filtering via adapters.
- `app/recipes.py` — `build_seed_spec` adapter-aware seeding (if goals route
  through recipes).
- `app/palette_prompt.py` (**new**) — shared pure `literal_prompt(...)` extracted
  from `remix_popover._build_hint`, + the seed-time LLM conversion helper.
- `app/pipeline_engine.py` — `TTLGPaletteToPrompt` handler only if the adapter
  needs a run-time no-op (it materializes to a literal text node at seed time, so
  likely reuses the existing text-node handler; confirm during planning).

## Open implementation question (resolve in planning, not blocking)

Whether `TTLGPaletteToPrompt` is a **distinct engine handler** or a **relabeled
text node pre-filled at seed time**. The design intent is the latter (materialize
to an editable literal), which needs no new run-time handler; the planning step
should confirm the existing text-node handler + a distinct intent label achieves
the visible-step UX without a bespoke engine node.
