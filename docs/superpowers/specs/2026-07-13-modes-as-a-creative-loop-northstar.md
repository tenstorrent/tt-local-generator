# North star — the app as a creative loop (Create · Curate · Discover · Remix)

**Date:** 2026-07-13
**Status:** North-star direction (agreed in brainstorm; individual slices get their own spec)
**Branch:** `feat/pipeline-editor`

## The problem

The app is divided by **medium** — Video / Animate / Image / Generative Art tabs — a
*technical-tool* axis. But **it's all generative art**. That division bloats the Generative
Art tab (the catch-all for everything not the other three) and fragments one creative
practice into four tool-shaped rooms. Cf. `project-creative-solution-positioning`,
`project-pipeline-ux-philosophy`: we're a creative *solution*, organized by what you're
*doing*, not by which model runs.

## The reframe: verbs, as a loop — not tabs

The top level is three **activities** (Curate merged into Discover — see below), but they are
**stations in a loop**, not walled rooms:

- **Create** — make something new.
- **Discover** — browse *and collect*: get inspired, and curate as you go (star, playlist,
  thread) — across individual artifacts AND whole pipelines/projects/packages.
- **Remix** — transform what exists.

**Curate folds into Discover** (refined 2026-07-13): browsing and collecting are the same
act — you make playlists as you find things, star them, thread them together; and you can
discover at any granularity (a single artifact or a whole package). So there is no separate
Curate destination; curation is *inline* in Discover.

They flow into each other (the user's own words):
- *Create* is what you do to have something to *Discover*.
- *Discover* is what you do when you need inspiration to *Create*.
- Collecting/threading happens *while* discovering, because **the threads between things
  reveal themselves later**, not up front.
- Some acts are **both at once** — a pipeline is Create *and* a curated collection.

So the UI must make **moving between verbs natural**, and must not force an act into one box.

## Principles (the measuring stick for every slice)

1. **The loop.** Verbs interconnect; the design surfaces the next natural move (Discover→
   Create, Create→Curate, Remix from anything) rather than dead-ending in a tab.
2. **Three doors into Create.** Entry is not always intent-first:
   - **Intent** — "what do you want to make?" (the Muse's front door).
   - **Model** — you start from what's *running/runnable*; if it's a video model, you're
     making video. Hardware reality is first-class, not hidden.
   - **Inspiration** — from something you Discovered → carry it into Create/Remix.
3. **Humane per-type richness.** Video, image, animate, verse, ANSI each have genuinely
   different params and needs. The answer is a **consistent scaffold with room for each art
   type's own soul** — NOT a flattened generic form. The failure mode to avoid is
   "inconsistent or messy, or worse, boring and inhumane."
4. **Medium is a property, not the top-level division.** "image vs video vs verse" lives
   *inside* Create/Remix as a choice/consequence, not as four tabs.

## The map (where today's surfaces land)

- **Create** — all generation: image, video, animate, and every artgen generator
  (verse/ansi/landscape/…). The Generative-Art overload dissolves here: those generators
  become options in Create, not a crammed tab. Prompt tools live here. Three doors
  (intent/model/inspiration). Per-art-type controls on a shared scaffold.
- **Discover** (browse + collect) — the gallery of everything made · Pipeline Studio's
  Discover (past runs/projects) · **Watch / TT-TV** (the live stream) · examples — WITH
  inline curation: star/favorites, playlists, showcases, threading. Browse at any
  granularity: a single artifact or a whole package/pipeline.
- **Remix** — the Muse · remix-as-pipeline · the composer (pipelines live at this seam — a
  create-and-collect hybrid).
- **Global chrome** — server/model controls stay ambient, but they are effectively the
  **model door** into Create (start a model → Create adapts to its medium).

## Sequencing

1. **Palette unification (now, orthogonal):** re-skin the main app from the old
   tt-vscode-toolkit palette (`#0F2A35`/`#4FD1C5`) to the brand dark forest-teal that
   Pipeline Studio already uses — safe regardless of structure. (Its own small change.)
2. **Create first (the first real slice):** it's the root ("you must create to have
   something to discover"), where the Generative-Art overload dissolves, and where the two
   hardest constraints live — per-art-type params *and* model/hardware-led entry. Get Create
   right and Discover/Curate/Remix (much already built as the gallery + Pipeline Studio)
   reorganize around it. Design Create in depth with **visual mockups** before building.
3. Later slices: Discover / Curate / Remix reorganization; the loop-navigation chrome.

## Non-goals (for now)

- Not a settings/theme feature (this is structure + polish, not a preference surface).
- Not a big-bang rewrite — decompose into slices, each its own spec → plan → build, each
  measured against the four principles above.
