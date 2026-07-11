# Pipeline Studio (SP-C) — design

Date: 2026-07-11
Status: design (brainstormed with the visual companion; 7 mockups validated)
Depends on: SP-A pipeline engine (`app/pipeline_engine.py`, done + hardware-proven)
Mockups: `.superpowers/brainstorm/988333-1783804257/content/*.html`
Live proof: real published showcase (an actual QB2 run) — the capstone pattern, real.

## The problem this solves

Every past attempt at the "dedicated pipeline editor" produced a beautiful mock and a
built UI that "felt like a toy" — because it led with an **empty, tool-labeled node
editor** over **no working backend**. Both conditions changed: SP-A gives a real engine
(12 node types, wire model, live `NODE:/LOG:/PLAYLIST:` signals, backend switching,
proven on hardware), and we now have **real already-run pipelines** (World's Fair runs,
today's 1964 run with a genuine generated image + video). SP-C is therefore not "invent
an editor" — it's "faithfully present a design we have, over a backend that now exists,
seeded by real work."

## Core philosophy (the memories are the source of truth)

See memories: `project-pipeline-ux-philosophy`, `project-showcase-finale`,
`project-director-agent-node`, `project-remix-mode`.

1. **Intent-oriented, not tool/model-oriented.** Every step reads as a verb + noun
   ("Generate an image", "Animate it", "Write a poem about it"). The model is a quiet,
   openable detail — never the headline. This single reframe is most of the difference
   between "toy" and "real app."
2. **Learn → remix → create.** You start by *seeing* a finished example end-to-end
   (its steps + real artifacts), feel safe changing **one** step ("change one thing, the
   rest still runs"), and only then build from scratch. Remix is the on-ramp; the blank
   canvas is the summit, not the entrance.
3. **Composable narrative, not rigid templates.** Building reads like a recipe:
   "Start with X → then feed it to Y to achieve ABC → then send ABC to Z." Templates as
   starting points, never locks.
4. **The main app is the front door.** Most creation begins from an artifact you're
   already viewing (image/video/GIF/SVG/poem/code) → a **Remix** action → a pipeline
   seeded by that artifact. Blank-canvas creation is the rare path.
5. **Dynamic capabilities from live MCP/plugins.** The "add a step" / remix options are
   generated at runtime from what's actually on (plugin registry + MCP prompt
   collections), greying out latent capabilities as "start a model →". Not a hardcoded
   menu.
6. **Always an escape hatch.** A prominent, imagination-first "describe what you want"
   freeform is always present — you can wing it beyond any listed option.
7. **The showcase capstone.** A run culminates in a standard offer to build a shareable
   **showcase site** presenting the results *and* the pipeline recipe behind them
   (one-click remix). "Fireworks" is metaphorical — quiet, refined delight through
   composition and type; **no literal effects**, avoid even the word in-UI.

## The five modes (woven into the main app, not a separate destination)

Validated as mockups; each maps to real backend concepts.

| Mode | What it is | Backend it rides on |
|---|---|---|
| **Discover** | Browse already-run pipelines + their real outputs; featured hero + grid, each card showing its intent-recipe | `history_store` / `pipeline_store` records + run artifacts |
| **Open** | One run, end-to-end: every step + its real artifact, "remix from here" per step | a run's spec + its stored outputs |
| **Compose / Remix** | The intent-composer: the recipe as editable intent cards; "＋ do something else" (wing-it + live capabilities) | edits a ComfyUI-API-v1 spec; node↔intent mapping |
| **Run** | Watch it execute live — intent steps with ✓/⟳/queued, board-switching surfaced, log tail | `pipeline_runner.py` parsing engine `NODE:/LOG:/PLAYLIST:` |
| **Showcase** | The capstone: shareable site of results + the pipeline recipe | a showcase generator over the run's artifacts |

**Entry:** a **Remix →** affordance on any artifact in the existing gallery seeds a
pipeline with that artifact as node X. This is how Compose is normally reached.

## Architecture

- **Reuse, don't rebuild the runtime.** SP-C is a GTK front end over the existing
  `pipeline_engine.py` (execution), `pipeline_runner.py` (signal parsing → GTK via
  `GLib.idle_add`), `pipeline_store.py` (specs/runs), `workflow_compat.py` (node
  registry + output-key contract), and `history_store.py` (artifacts). No new runtime.
- **Intent-vocabulary layer (new, small).** A pure mapping module: `class_type ↔ intent`
  (verb+noun label, icon, plain-language param labels, which inputs are "the model /
  quiet detail"). Drives every screen's language. Single source so Compose, Open,
  Discover, Run, Showcase all speak the same intents. Ships with the 12 SP-A node types;
  extensible per plugin.
- **Dynamic capability discovery (new, small).** Build the "add a step" list at runtime
  from `artgen.all_names()` (plugins) + MCP prompt collections + live server health, so
  the shelf reflects what's on. Latent capabilities shown greyed with "start a model →".
- **Showcase generator (new).** Given a run (spec + artifacts), emit a self-contained
  HTML page (results + intent-recipe + remix link), inlining assets as data URIs — the
  exact pattern proven by the published 1964 artifact. A standard capstone offered on
  run completion (sibling to AddToPlaylist).
- **GTK discipline:** all engine/run work off the main thread; UI updates via
  `GLib.idle_add` (see CLAUDE.md GTK threading rules). `pipeline_runner.py` already
  follows this.

## Build strategy — phased, anchored on "feels real first"

Each phase is a working, shippable increment. Order deliberately leads with the
real-feeling parts (browse/learn) before the historically-hard editor.

- **Phase 1 — Discover + Open (browse & learn).** Beautiful browse of real runs + the
  end-to-end "Open" view, intent-labeled, real artifacts. This alone makes it feel like a
  product. Wires up the intent-vocabulary layer + reads existing run records. Lowest
  interaction risk, highest "not a toy" payoff. (Mockups: `discover-gallery`, `open-run`.)
- **Phase 2 — Compose/Remix + Run.** The intent-composer (edit a spec as intent cards),
  the Remix-from-artifact entry, the "＋ do something else" (wing-it + dynamic
  capabilities), and the live Run view over `pipeline_runner`. The craft-heavy editor —
  entered only after Phase 1 makes examples browsable to learn from.
  (Mockups: `entry-remix`, `intent-composer`, `add-step-wingit`, `run-watch`.)
- **Phase 3 — Showcase capstone.** The showcase generator + the standard end-of-run
  offer. (Mockup: `showcase-finale-v2`; live proof already published.)

Each phase gets its own spec → plan → implementation cycle (this doc is the umbrella).

## Non-goals / deferred (tracked)

- **Fan-out construct** ("a series of N videos" — one step → N variations). The engine is
  a linear DAG today; fan-out is a real, worthwhile engine addition, deferred to its own
  effort (surfaced by the Pisa remix example).
- **Director agent node** — the CPU prompt-gen LLM as an in-pipeline "director" content
  node (`project-director-agent-node`). A great fit for the narrative model; a later node
  type, not core SP-C.
- **Context windows / long video, coherent prompt-travel** — AnimateDiff-Evolved
  follow-ons already tracked from SP-A Milestone 2.
- Not replacing `run_worlds_fair.sh`; not a new execution runtime.

## Testing

- Intent-vocabulary + capability-discovery layers: pure unit tests (no GTK) — mapping
  completeness for all 12 node types, capability list reflects a mocked plugin/MCP set,
  latent-vs-live partitioning.
- Showcase generator: unit test that a run (fixture spec + fixture artifacts) produces
  self-contained HTML with data-URI assets + the recipe (no external refs).
- GTK widget tests under `xvfb-run` per the existing test setup; run/signal flow reuses
  `pipeline_runner`'s already-tested parser.
- Each phase’s acceptance includes a real end-to-end pass on a genuine run’s artifacts.

## Why this will land where past attempts didn't

Real backend + real seed content + intent language + learn-before-create + a capstone
that makes finishing feel like something. The mockups and the live published showcase
already demonstrate the target feel; SP-C is the disciplined, phased build to it.
