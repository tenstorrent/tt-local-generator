# Per-Artgen-Type Modifier Pills — Design

**Date:** 2026-08-05
**Branch:** `feat/pipeline-editor`
**Status:** Approved (brainstorm), pending implementation plan

## Problem

In Create, the Direction-zone `ModifierPills` bank is chosen by the medium's
generic output *kind* (`chip_config.load_chips(medium.kind)` / a
`_bank_kind_for_output` that maps to image/video/animate). For **artgen**
mediums this is wrong:

- An image-output artgen (e.g. **palette**, **landscape**) gets the photo/video
  pills — *cinematic, 35mm, aerial drone, bokeh* — which are meaningless for a
  color scheme and crowd out directions that would actually spark ideas.
- A text-output artgen (e.g. **verse**) gets **no** pills at all (`text/None →
  no bank`).

So the pill palette either misleads or is empty for artgen, instead of widening
the creative space per art form.

## Goal

Give each artgen type its **own** curated pill vocabulary, plus a shared
cross-type mood bank and a 🎲 Surprise chip, so tapping a pill suggests
directions the user wouldn't have typed — per art form. Native photo/video
mediums are untouched.

## Decisions (locked in brainstorm)

- **Shared 'mood' bank + a 🎲 Surprise chip** on every artgen type, in addition
  to that type's own curated banks. A type with no hand-curated bank still gets
  the shared mood bank + Surprise (never nothing).
- Data-driven: banks live in the existing `app/config/prompt_chips.yaml`
  (curators can add banks without touching code).

## Global Constraints

- **`collect()` / `_collect_params()` output stays byte-for-byte identical** —
  pills only append their `applied_text()` to the brief at generate time, exactly
  as today; the bank a medium shows is display-only. (Existing invariant, guarded
  by tests.)
- Native mediums (image/video/animate) show the SAME banks as today — the
  photo/video categories in the YAML are untouched, and their `for:`-default
  behavior is preserved.
- YAML schema stays backward-compatible: new fields are additive
  (`for: [<artgen-type>]`, `surprise: true`); existing chips/categories parse
  unchanged.
- `_CSS`/byte literals ASCII-only; chip glyphs live in the YAML `label` strings
  (UTF-8 YAML, not a byte literal — fine).
- System `/usr/bin/python3`; GTK tests under `xvfb`; pure `chip_config` tests
  headless. Version bump + changelog.

## Key mechanism (why this is mostly data)

`chip_config.load_chips(tab)` keeps a category when `tab` is in the category's
`for:` set, and a category with **no** `for:` defaults to
`_ALL_TABS = {video, image, animate}`. Therefore:

- A category tagged `for: [palette]` shows ONLY for the `palette` tab.
- The existing photo/video categories (no `for:`, or `for: [video, animate]`)
  **never leak into an artgen type**, because an artgen-type key like `palette`
  is not in their `for:` set.

So the whole feature hinges on **keying artgen mediums by their type** (e.g.
`palette`) instead of by `medium.kind`, and adding `for: [<type>]` categories.

## Components

### 1. YAML — per-type banks + shared mood bank (`app/config/prompt_chips.yaml`)

Add categories tagged with artgen-type keys. Curate the shipped artgen types
(the visual/expressive ones first). Examples (illustrative; final copy in the
plan):

- `for: [palette]` — **Mood** (moody / sun-bleached / neon-noir / pastel /
  jewel-tone / earthy), **Era** (Y2K / art-deco / vaporwave / 70s film),
  **Source** (coral reef / autumn forest / city-at-dusk / desert dawn),
  **Harmony** (monochrome / complementary / triadic).
- `for: [verse]` — **Form** (haiku / sonnet / free verse / litany), **Tone**
  (elegiac / playful / ominous / tender), **Voice** (first-person / oracular /
  a letter / overheard).
- `for: [ansi]` — **Scene** (demoscene / cyberpunk terminal / 90s BBS / amber
  CRT), **Subject** (dragon / skull / spaceship / logo), **Look** (minimal /
  dense / neon-on-black).
- `for: [landscape]` — **Biome** (alpine / tundra / archipelago / dunes),
  **Light** (golden hour / blue hour / storm-front), **Style** (ukiyo-e /
  matte-painting).
- `for: [codeart]`, `for: [freeform]` — structure/theme wildcards.

Plus a **shared mood bank** tagged `for: [artgen]` (cross-type feeling words —
serene / chaotic / nostalgic / dreamlike / stark / lush) that every artgen type
receives, and each bank carries one **Surprise** chip (`surprise: true`).

### 2. `chip_config.py` — artgen tabs, combined loader, Surprise chip

- Recognize artgen-type keys + `artgen` as valid `for:` tabs (extend the
  accepted set; do NOT add them to `_ALL_TABS`, so no-`for:` photo categories
  still default to photo tabs only and never leak into artgen).
- `load_chips_for_artgen(artgen_type: str) -> list[ChipCategory]`: returns
  `load_chips(artgen_type) + load_chips("artgen")` (type banks then the shared
  mood bank), deduped by category name. A type with no curated banks yields just
  the shared mood bank (+ its Surprise chip).
- `ChipEntry` gains an optional `surprise: bool = False`. A `surprise` chip has
  no fixed `text`; `chip_config` exposes a `surprise_pool(category)` = the
  `text` values of the non-surprise chips in that category, so the widget can
  pick a random one.

### 3. `ModifierPills` — render the Surprise chip

- When a category contains a `surprise` chip, render it as a distinct "🎲
  Surprise" add-chip; tapping it applies a pill whose text is a random pick from
  that category's `surprise_pool` (fall back to the whole bank's pool if the
  category has only the surprise chip). Otherwise pills behave exactly as today
  (collapsible categories, add-chip → removable pill, `applied_text()` appended
  to the brief).
- Randomness: `random.choice` at tap time (GTK main thread) — no determinism
  requirement; tests inject a stub picker.

### 4. Create wiring — key artgen mediums by type

- Where the artgen medium's Direction `ModifierPills` bank is currently chosen
  by output kind (`create_view`/`create_param_panels` `_bank_kind_for_output` /
  the artgen panel path), route artgen mediums through
  `load_chips_for_artgen(medium.id)` instead. Native mediums keep
  `load_chips(medium.kind)`.
- Threading: the artgen panel already knows its medium; pass `medium.id` (the
  artgen type) to the pills constructor for artgen mediums. Text-output artgen
  (verse) now gets pills (its own + shared) where it had none.

## Data flow

```
Create → pick "palette" medium
   → Direction pills = load_chips_for_artgen("palette")
      = [palette Mood/Era/Source/Harmony banks] + [shared 'mood' bank] (+ 🎲 Surprise each)
   → tap "🎲 Surprise" in Source → applies e.g. "city-at-dusk" as a pill
   → Create → the pill text is appended to the brief (collect() unchanged)
```

## Error handling

- **Type with no curated banks:** `load_chips_for_artgen` returns just the shared
  mood bank — never empty, never an error.
- **Surprise chip in a category with only itself:** falls back to the bank-wide
  pool; if the whole bank is empty of real chips, the Surprise chip is omitted.
- **Missing/!malformed YAML category:** `chip_config` already raises `ValueError`
  on schema errors at load; the Create surface's existing fail-soft around pill
  construction stays (a bank failure must never break Create).
- Native mediums: zero behavior change (regression-guarded).

## Testing

- **`chip_config` (pure):** `load_chips_for_artgen("palette")` returns the
  palette banks + the shared `artgen` mood bank and NOT the photo/video
  categories; `load_chips_for_artgen("verse")` (a text type) returns its banks +
  shared; a type with no banks returns only the shared bank; `load_chips("image")`
  (native) is unchanged and excludes artgen-only categories; a `surprise` chip
  parses with `surprise=True` and `surprise_pool` returns the category's real
  texts.
- **`ModifierPills` (xvfb):** a bank with a surprise chip renders a "🎲 Surprise"
  add-chip; tapping it (with a stubbed picker) applies a pill whose text is from
  the pool; `applied_text()` includes it.
- **Create wiring (xvfb):** a palette medium's Direction pills come from the
  palette banks (assert a palette-specific chip label present, a photo-specific
  one absent); a native image medium's pills are unchanged; a `collect()`
  equality test proves the params dict is byte-identical whether or not a pill
  was applied vs typed.
- Full suite green with the three documented flake deselects.

## Out of scope (YAGNI)

- Rewriting or reorganizing the existing photo/video chip banks.
- A chip-editor UI / user-defined banks at runtime.
- Per-type pills in Remix/Pipeline step cards (Create only for now; the pipeline
  step-card pills are a separate surface and can adopt `load_chips_for_artgen`
  later via the same loader).
- LLM-generated pills (the Surprise chip is a random pick from curated text, not
  a model call).

## Critical files

- `app/config/prompt_chips.yaml` — new `for: [<artgen-type>]` + `for: [artgen]`
  categories, `surprise: true` chips.
- `app/chip_config.py` — artgen tab keys, `load_chips_for_artgen`,
  `ChipEntry.surprise` + `surprise_pool`.
- `app/create_param_panels.py` / `app/create_view.py` — key artgen mediums'
  Direction pills by `medium.id` via `load_chips_for_artgen`; render the Surprise
  chip in `ModifierPills`.
- Tests: `tests/test_chip_config.py` (extend), `tests/test_modifier_pills*.py`
  (surprise chip), a Create-wiring test.
