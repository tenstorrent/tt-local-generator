# The Muse — creative wizard + image→pipeline bridge (SP-C entry points)

**Date:** 2026-07-12
**Status:** Approved design (brainstorm complete)
**Branch:** `feat/pipeline-editor`

## Positioning

tt-local-generator's strength is being a **creative solution, not a technical tool**
(contrast ComfyUI / InvokeAI). Every decision here optimizes for imagination and
momentum over control surface. The user never faces a blank node canvas — they
start from an *intention*. See `project-creative-solution-positioning` and
`project-pipeline-ux-philosophy` memories.

## Goal

Close the SP-C create arc by adding its two missing front doors, unified under one
component (**the Muse**):

1. **From-scratch wizard** — "Start from scratch" opens a goal-first muse
   ("What do you want to make?"); picking a goal drops a curated, wired starter
   recipe into the existing composer.
2. **Main-app image→pipeline bridge** — "🧩 Remix as pipeline…" on any gallery
   artifact opens the SAME muse, scoped to that artifact ("Make this image into…"),
   seeding the recipe with the artifact as starting material.

Both flows end in the existing intent-oriented composer (`RemixView`) → Run →
Showcase.

## Architecture

### The Muse (one component, two modes)

A single `MuseView` is the creative entry point:
- **Blank mode** — "What do you want to make?" (no seed artifact).
- **Scoped mode** — "Make this <kind> into…" with the artifact thumbnail shown and
  goal cards phrased as transformations of that artifact.

Both modes present: a grid of **goal cards** + an always-present **"✨ Surprise me"**
and a **free-text "…or tell me your idea"** box. Choosing any of these produces a
**seed spec** loaded into the existing `RemixView` composer.

### New pure layers (no GTK imports; unit-tested with fakes)

**1. `spec_remix.seed_spec(steps, *, seed_artifact=None) -> dict`** — the missing
primitive. Builds a fresh ComfyUI-API-v1 spec from an ordered list of intent steps
(`(class_type, params)`), wiring each step's primary output → the next step's
primary input (kind-aware, reusing the same wiring logic as `add_step`). With
`seed_artifact` (a path + kind), the artifact becomes step-1's starting material,
represented as a minimal source input so the engine can resolve it (wired as the
first step's input param; if a step consumes an image, the artifact path is that
step's image input). Returns a spec that `add_step`/`derive_spec`/`write_spec` all
accept unchanged.

**2. `app/recipes.py`** — the goal catalog + hybrid discovery:
- `@dataclass Goal`: `id: str`, `label: str`, `icon: str`, `output_kind: str`,
  `applies_to: str` (`"blank"` | `"scoped"` | `"both"`), `recipe_steps:
  list[tuple[str, dict]]` (intent class_types + default params), `via: str`
  (`"curated"` | `"discovered"`).
- `curated_goals() -> list[Goal]` — the shipped, tested core set (poster, looping
  animation, illustrated poem, short film, explorable world). Each recipe is a real,
  runnable intent sequence using native intents + available plugins.
- `discover_goals() -> list[Goal]` — additional goal cards discovered from MCP
  prompt collections' `x-ttlg` metadata, latent-aware (skips `x-ttlg.utility`
  plugins), same mechanism/spirit as `capability_discovery`.
- `all_goals() -> list[Goal]` — curated + discovered, de-duplicated by `id`
  (curated wins on collision).
- `goals_for(*, seed_output_kind=None) -> list[Goal]` — **blank**
  (`seed_output_kind is None`): every goal with `applies_to in {"blank","both"}`.
  **scoped**: goals with `applies_to in {"scoped","both"}` whose FIRST recipe step
  can consume `seed_output_kind` (kind-safe, reusing `intent_vocab.compatible_intents`
  / the capability kind metadata). Guarantees the muse never offers an impossible
  transformation.
- `build_seed_spec(goal, *, seed_artifact=None) -> dict` — materializes a goal's
  `recipe_steps` via `spec_remix.seed_spec`.

**3. `wingit.map_freeform_to_pipeline(text, *, seed_output_kind=None, capabilities,
llm_fn) -> list[tuple[str, dict]] | None`** — extends single-step wing-it to draft a
*multi-step* pipeline (an ordered intent list) from one sentence. LLM-assisted
(lenient JSON, kind-constrained to the provided live capabilities), with a
deterministic heuristic fallback (e.g. generate-an-image → the described
transform, or the identity single step). Never crashes; returns `None` only when
no compatible capability exists. Powers the "…or tell me your idea" box in both
muse modes. `default_llm_fn` reused from single-step wing-it.

### GTK + wiring

**4. `MuseView` (in `app/pipeline_studio.py`)** — a goal-card grid + free-text entry
+ "Surprise me". Constructed with injectable seams: `goals_fn(seed_output_kind) ->
list[Goal]` (default `recipes.goals_for`) and `wingit_pipeline_fn(text,
seed_output_kind) -> list[tuple[str,dict]]|None` (default wraps
`wingit.map_freeform_to_pipeline` + `default_llm_fn` + `capability_discovery`).
Emits `goal-chosen` with the resulting **seed spec** (goal path builds it
synchronously; free-text path runs the LLM OFF the main thread → `GLib.idle_add`).
Intent language throughout; model stays a quiet detail. Scoped mode shows the seed
artifact's thumbnail + "Make this <kind> into…" heading.

**5. `PipelineStudio` shell** — new `"muse"` stack page wrapping `MuseView` (with a
back control). `show_muse(seed_artifact=None)` builds the muse in the right mode and
switches to it; on `goal-chosen` it loads the seed spec into the existing
`RemixView` and switches to `"remix"`. Discover view gains a prominent
**"✨ Start from scratch"** affordance → `show_muse()` (blank).

**6. `main_window.py` bridge** — the card hover action bar AND the detail panel
action row gain **"🧩 Remix as pipeline…"** (distinct from, and alongside, the
existing single-shot "🔀 Remix" popover). It activates the Pipelines area and calls
`PipelineStudio.show_muse(seed_artifact=<record's primary artifact>)` (scoped mode).

### Folded-in visual polish

Because users now land in Muse / composer / Open, fix the rendering issues flagged
in `ttlg-p1.png` / `ttlg-p2.png` within this arc:
- **Cohesive intent labels** — render "Generate an image" as one label (kill the
  verb / noun / model three-line fragmentation); model as a small muted detail.
- **Compact step rows** — reduce the oversized, sparse Open-view step cards.
- **Hero image fills its frame** in Discover (no small off-center image beside empty
  boxes).
- **Honest-but-tidy placeholders** — a missing artifact renders as an intentional
  intent-icon chip, not a large empty dark box with a stray tiny icon.

## Data flow

- **From scratch:** Discover → "✨ Start from scratch" → `MuseView` (blank) → pick
  goal / Surprise me / free-text → seed spec → `RemixView` → Run → Showcase.
- **From artifact:** gallery card / detail → "🧩 Remix as pipeline…" → Pipelines
  area → `MuseView` (scoped to the artifact) → pick transformation → seed spec (with
  the artifact as starting material) → `RemixView` → Run → Showcase.

## Error handling

- Free-text / LLM path never crashes: `map_freeform_to_pipeline` falls back to a
  heuristic; a `None` result shows a gentle "couldn't compose that — try rephrasing"
  and adds nothing.
- Scoped muse with an unsupported seed kind shows only the goals that fit; if none
  fit, it shows the free-text box + a gentle "no ready-made recipes for this yet"
  (never an empty broken screen).
- Missing/unreadable seed artifact → the bridge degrades to blank muse rather than
  failing.
- All GTK work stays on the main thread; LLM/discovery off-thread → `GLib.idle_add`.

## Testing

- **Pure layers** (`seed_spec`, `recipes`, `map_freeform_to_pipeline`) unit-tested
  with fake `llm_fn` / fake capabilities / synthetic artifacts — no network, no GTK.
  Assert kind-safe wiring, curated-wins de-dup, blank vs scoped filtering, and the
  fallback / `None` paths.
- **GTK** (`MuseView`, `PipelineStudio.show_muse`, the main-app bridge) via
  `xvfb-run` with injected `goals_fn` / `wingit_pipeline_fn` seams; assert the muse
  renders the expected goals for a mode, a choice produces a seed spec loaded into
  the composer (`current_spec()` grew), and the bridge opens scoped mode with the
  artifact.
- Full suite (`xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q`) — no
  NEW failures beyond the two documented pre-existing ones.

## Phasing (subagent-driven tasks)

1. `spec_remix.seed_spec` primitive (fresh spec from an intent list + optional seed
   artifact).
2. `app/recipes.py` — curated core + `discover_goals` (MCP x-ttlg) + `goals_for` +
   `build_seed_spec`.
3. `wingit.map_freeform_to_pipeline` (multi-step draft + heuristic fallback).
4. `MuseView` + `PipelineStudio` muse page + Discover "✨ Start from scratch".
5. Main-app bridge — card/detail "🧩 Remix as pipeline…" → scoped muse.
6. Visual polish pass (cohesive labels, compact rows, hero fill, tidy placeholders).
7. Version bump (minor) + `debian/changelog` stanza.

## Global constraints

- Pure layers have **zero GTK imports**; all LLM/discovery behind injected seams.
- **Never fail hard** — every free-text/LLM/discovery path has a graceful fallback.
- **Kind-safe** — the muse never offers a transformation the seed kind can't feed;
  `seed_spec` wiring and `add_step` guards are the backstops.
- **Intent language** in all copy; model is a quiet detail; no tool/model names in
  labels.
- **Reuse, don't fork** — recipes build specs via `seed_spec`; the composer,
  `add_step`, capability discovery, and single-step wing-it are reused unchanged.
- No regression to existing SP-C phases. Use system `/usr/bin/python3`; tests via
  `xvfb-run`. Version discipline per repo CLAUDE.md.
- Everything stays local on `feat/pipeline-editor` — no push/merge/PR without
  explicit instruction.
