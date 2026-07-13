# Lore → a series of stills + a montage movie (text-seeded pipelines + engine fan-out)

**Date:** 2026-07-13
**Status:** Approved design (brainstorm complete)
**Branch:** `feat/pipeline-editor`
**Builds on:** the Muse (2026-07-12) — `seed_spec`, `MuseView`, `recipes`, the image→pipeline bridge.

## Positioning

Creative solution, not a technical tool. This turns a generated **text "lore"** artifact
(e.g. a `verse` "study on tetris") into a **series** of images and a montage movie in a
couple of clicks — imagination-first, no node graph. See
`project-creative-solution-positioning`, `project-pipeline-ux-philosophy`.

## Goal

From a text artifact in the Generative Art gallery: **🧩 Remix as pipeline** → scoped muse
*"Make this lore into…"* → pick **"An illustrated series"** → a **fan-out** pipeline
generates one image per lore fragment (one FLUX batch), collects them into a playlist, and
stitches a captioned montage `.mp4` → Run → Showcase.

## Architecture — two layers

### Layer 1 — Text → pipeline bridge (foundation)

1. **Generative-art gallery bridge.** `ArtgenPanel` cards and the artgen detail view gain a
   "🧩 Remix as pipeline…" action *alongside* the existing single-shot remix
   (`_on_remix_record`/`RemixPopover` — left untouched). It routes a `media_store.MediaRecord`
   into `MainWindow` → `PipelineStudio.show_muse(seed_artifact=…)` (scoped mode), reusing the
   Task-5 bridge machinery (`remix_as_pipeline_cb` seam, Pipelines-area activation).
2. **Artgen kind classifier** — `app/artgen_kind.py` (pure): `artgen_seed_kind(record) ->
   str|None` maps a `MediaRecord` to a pipeline kind by file extension (primary) /
   `generator_type`: `.txt` → `"text"`, `.png/.jpg/.jpeg/.svg/.ans` → `"image"`, `.gif` →
   `"gif"`, else `None`. THIS feature wires **text**; image/gif fall out for free.
3. **Text-content seed.** For a text seed the bridge reads the `.txt` **content** and seeds
   the pipeline with the *text*, not a path. `spec_remix.seed_spec`'s `seed_artifact`
   parameter becomes `(value, kind)`: for `kind == "text"`, `value` is the literal text →
   placed on step 1's `input_key` (the prompt); for media kinds, `value` is the file path
   (unchanged behavior). A tiny `MediaRecord` → `(content_or_path, kind)` resolver in the
   bridge reads the file for text.

### Layer 2 — Fan-out (the series)

No new engine execution model — **list-valued dataflow + vectorized nodes**, which also
batches by backend (kind to the boards):

1. **`TTLGSplitText`** (new intent + engine handler) — input `text` (kind text); params
   `mode` (`"paragraphs"` default | `"lines"` | `"numbered"`), `max_items` (default 8);
   output `fragments` (a Python **list** of strings). CPU only, no board. Splits on
   blank-line paragraphs / numbered list items, trims blanks, caps at `max_items` and
   `log()`s if it truncated.
2. **`TTLGTextToImage` becomes list-aware** — when its resolved `prompt` input is a list,
   the handler generates one image **per element in a single FLUX session** (one backend
   start, no per-item board switch) and returns `image_path` as a **list** of paths
   (`nodeN_image_0.png`, `_1`, …). A scalar prompt keeps the current single-image behavior.
   Each element's prompt = `fragment + style-suffix template` (from the recipe).
3. **`TTLGMontage`** (new intent + engine handler) — input `images` (a list of image paths,
   kind image) + optional `captions` (list of strings); param `seconds_per` (default 2.5);
   produces one ffmpeg slideshow `.mp4` with the fragment text as caption overlays. Output
   `video_path`. Reuses the existing ffmpeg wrapper style; fails soft (no ffmpeg → returns
   no video, the run's stills still stand).
4. **`TTLGAddToPlaylist` accepts a list** — when its input resolves to a list of artifact
   paths, it adds all N to the playlist (currently single). Output `playlist_id` unchanged.

**Kind system unchanged:** list-ness is orthogonal to kind — a list of image paths is still
kind `"image"`, a list of fragment strings is still `"text"`. So `seed_spec`/`add_step`
kind-safety needs no new "list" kind; scalar-vs-list is a runtime concern of the handlers.

### Text-seeded muse goals (recipes)

Added to `app/recipes.py` as **scoped, text-consuming** curated goals (first step
`input_kind == "text"`), surfaced when the scoped muse's `seed_output_kind == "text"`:

- **`illustrated-series`** (headline, fan-out): `TTLGSplitText → TTLGTextToImage →
  TTLGAddToPlaylist → TTLGMontage`.
- **`illustrate-it`** (single): `TTLGTextToImage`.
- **`lore-poster`** (single): `TTLGTextToImage` (poster style suffix).

`goals_for(seed_output_kind="text")` returns these; the image-seeded scoped goals are
unaffected. Free-text "tell me your idea" escape hatch remains.

## Data flow

lore `.txt` content → (seed) → `TTLGSplitText` → list[str] fragments → `TTLGTextToImage`
(vectorized, one FLUX batch) → list[image_path] → `TTLGAddToPlaylist` (all N) **and**
`TTLGMontage` (captioned slideshow → one `.mp4`) → the run's Open view shows the stills +
the montage (video poster frame via v0.22.0) + Showcase.

## Error handling / hardware

- **Board-friendly:** one FLUX session for all N stills + CPU split/montage — a single
  backend the whole run, no per-item switches (deliberate, given card-924055 fragility per
  `reference-qb2-card924055-fragility`).
- **Cap + surface:** `max_items` (default 8) caps generation; `log()` when a longer lore is
  truncated (no silent drop).
- **Fail soft:** missing/empty lore → gentle muse message, no crash; ffmpeg absent → montage
  yields no video but the stills/playlist still deliver; a single failed image in the batch
  is skipped, the rest proceed.
- Never fail hard; intent language in all copy; GTK work off-thread → `GLib.idle_add`.

## Testing

- **Pure:** `split_text` (modes, cap, trim, empty), `artgen_seed_kind` (ext/generator_type →
  kind), `seed_spec` text-content seed (content on prompt, not a path), recipe
  materialization + `goals_for("text")` — all with fakes, no GTK/ffmpeg/board.
- **Engine (dry-run):** `TTLGSplitText` list output; `TTLGTextToImage` list-in → list-out
  (vectorized, dry-run placeholders); `TTLGMontage` with a fake ffmpeg (asserts it's invoked
  with N frames + captions, fails soft when absent); `TTLGAddToPlaylist` list input.
- **GTK (xvfb):** the artgen-gallery bridge calls `remix_as_pipeline_cb` with the record and
  opens the scoped text muse; injected seams (no network/board).
- Full suite (`xvfb-run … pytest tests/ -q`) — no NEW failures beyond the documented ones.

## Global constraints

- Pure layers have **zero GTK imports**; all LLM/discovery/ffmpeg behind injected seams.
- **Never fail hard** — every split/kind/seed/montage/fan-out path degrades gracefully.
- **Kind-safe** — no new "list" kind; list-ness is a runtime handler concern; `seed_spec`/
  `add_step` guards unchanged and remain the backstop.
- **Board-friendly** — fan-out batches all image generation in one FLUX session; no per-item
  backend switch. Cap N and log truncation.
- **Reuse, don't fork** — bridge reuses the Task-5 `remix_as_pipeline_cb` path + `show_muse`;
  recipes build via `seed_spec`; montage reuses the existing ffmpeg wrapper; video previews
  reuse the v0.22.0 poster-frame path.
- Intent language everywhere; model/tool a quiet detail. No regression to prior SP-C phases.
- System `/usr/bin/python3`; tests via `xvfb-run`. Version discipline per repo CLAUDE.md.
- Everything stays local on `feat/pipeline-editor` — no push/merge/PR without instruction.

## Phasing (subagent-driven tasks)

1. `spec_remix.seed_spec` text-content seed (`seed_artifact=(value, kind)`; text → prompt literal).
2. `app/artgen_kind.py` — `artgen_seed_kind(record)` classifier.
3. Engine: `TTLGSplitText` (text → list) + intent_vocab entry.
4. Engine: `TTLGTextToImage` list-aware (vectorized, one FLUX session, list output).
5. Engine: `TTLGMontage` (list images + captions → one ffmpeg slideshow) + intent_vocab entry.
6. Engine: `TTLGAddToPlaylist` list-aware.
7. `app/recipes.py` — text-seeded goals (`illustrated-series` fan-out + `illustrate-it` +
   `lore-poster`); `goals_for("text")`.
8. GTK: `ArtgenPanel` "🧩 Remix as pipeline…" bridge → scoped text muse (+ `MediaRecord` →
   seed resolver reading `.txt` content).
9. Version bump (minor) + `debian/changelog`.
