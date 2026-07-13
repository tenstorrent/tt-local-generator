# Create — the unified generation surface (first slice of the loop)

**Date:** 2026-07-13
**Status:** Design spec (mockups approved: artifact 25f39296; north-star:
2026-07-13-modes-as-a-creative-loop-northstar.md)
**Branch:** `feat/pipeline-editor`

## Goal

Replace the four medium tabs (Video / Animate / Image / Generative Art) with **one Create
surface** where the medium is a *property*, not the top-level division — dissolving the
Generative-Art overload. Create has three doors in (idea / model / inspiration), shows each
art type's own controls on a shared humane scaffold, and is honest about what hardware is
running. This is the first slice of the Create·Curate·Discover·Remix loop; Discover/Curate/
Remix reorganization are later slices (much already exists as the gallery + Pipeline Studio).

## Resolutions to the three open questions (from the mockup)

1. **Loop nav = three movements, not tabs.** A top-level nav (Create · Discover · Remix)
   styled as movements, with cross-links so it reads as a loop, not silos. Create is the
   default landing. **Curate folded into Discover** (refined 2026-07-13): browsing and
   collecting are one act — you star / playlist / thread as you find things — so Discover is
   "browse + collect," not a separate destination.
2. **Doors = a persistent, lightweight row**, with **"Start with an idea" default-active**.
   Entry mode varies per task, so switching doors is always one tap (not a locked first
   choice).
3. **Medium = visible chips, context-driven.** Chips are always shown (you can always
   override), but pre-selected by context: the idea door defaults to a sensible medium; the
   model door sets it from the chosen model. Honors "sometimes the model decides, sometimes
   you choose."

## Architecture

A new **`CreateView` (Gtk.Box)** in `app/` (its own module, e.g. `create_view.py`), mounted
in `MainWindow` where the medium-tab gallery stack lives today. It is a shell around:

- **The doors row** — three toggle affordances (idea / model / inspiration); switching sets
  the entry mode. Default: idea.
- **The medium chips** — one chip per art type discovered from the SAME sources the Muse's
  capability list uses (`capability_discovery` / `artgen.all_names()` + native intents), so
  new plugins appear automatically. Selecting a chip sets the medium.
- **The per-type param panel** — a `CreateParamPanel` protocol: each art type contributes a
  panel builder (`build_params() -> Gtk.Widget`, `collect() -> dict`) so image/video/animate/
  verse/… keep their real controls on a shared scaffold. Existing per-medium control code
  (today's tab control panels) is refactored into these panel builders — reuse, don't rewrite.
- **The live-model strip** — reuses `server_manager` health: shows running models (one-tap)
  and runnable ones (with start cost + honest board-reset note). Doubles as the model door.
- **The Create CTA** — routes to the existing `GenerationWorker`/`api_client` path for the
  chosen medium (the generation backend is unchanged; only the surface that gathers params
  is new).

**Data flow (unchanged backends):** door + medium + params → the same worker/api_client the
current tabs use. Model door: pick a running/runnable model (`server_manager`) → medium is
set from the model's family → its param panel shows. Idea door: prompt + medium chip →
params. Inspiration door: hands off to the existing Muse/`show_muse` bridge (Remix seam) —
Create doesn't reimplement remix.

## Scope / non-goals (this slice)

- **In:** the Create surface (doors, chips, per-type panels, model strip, CTA), wiring the
  existing generation backends to it, and the top-level loop nav with Create active. The old
  four medium tabs are replaced by Create; the medium becomes a chip.
- **Out (later slices):** Discover/Curate/Remix reorganization (the galleries + Pipeline
  Studio stay reachable and keep working; the loop nav routes to them as they are for now),
  and any new theming/settings feature.
- **Reuse, don't rewrite:** the per-medium control panels, `GenerationWorker`, `api_client`,
  `server_manager`, `capability_discovery`, and the Muse bridge are reused. This slice is a
  new *organizing surface* over existing machinery, not new generation code.

## Constraints

- GTK single-thread; generation + health off the main thread → `GLib.idle_add` (existing
  patterns). Brand dark forest-teal (already unified). Intent/creative language, humane
  per-type panels (no flattened generic form). No regression to generation behavior. System
  `/usr/bin/python3`; xvfb tests. Local on `feat/pipeline-editor`.

## Testing

- Pure/logic: the medium-chip discovery (from capability sources), the door→medium→param
  routing, model→medium mapping — unit-tested with fakes.
- GTK (xvfb): CreateView builds; selecting a medium chip swaps to that art type's param
  panel; the model door sets the medium from a (fake) running model; the CTA collects the
  right params and calls the (injected) generation seam; per-type panels render their own
  controls. Full suite no-regression.

## Risk note

This touches `MainWindow`'s core navigation (replacing the medium-tab stack). It's the
biggest single slice so far. Mitigation: build `CreateView` alongside the existing tabs
first (behind the new loop nav), port one medium at a time into its param panel, and only
remove the old tabs once every medium is covered + verified — so the app is never broken
mid-migration.
