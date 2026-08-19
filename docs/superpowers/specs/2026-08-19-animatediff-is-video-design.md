# AnimateDiff is Video — unify the media taxonomy — Design

**Date:** 2026-08-19
**Branch:** `feat/pipeline-editor`
**Status:** Draft (design) — pending user review → implementation plan

## Problem / goal

AnimateDiff-generated media is stored under **three inconsistent conventions**,
so it keeps getting "relegated back to artgen" and can't simply be Video:

| how it's stored today | count | lands in |
|---|---|---|
| `media_type="animatediff"`, gen `None`, `.gif` | 55 | the Video gallery (via `media_type in ("video","animatediff")` filters) |
| `media_type="artgen"`, gen `"animatediff"`, `.gif` | 73 | the **Artgen** gallery ← the "relegated" ones |
| `media_type="animate"`, gen `None`, `.mp4` | 1 | Wan2.2-Animate — **now in scope**, folded into video (decision below) |

**Goal:** all AnimateDiff **and Wan2.2-Animate** media is **`media_type="video"`**
— past, present, and future — while keeping each one's identity
(`model_id`/`generator_type`) and its stats, and without a rendering change (a
`.gif` is still a `.gif`, an `.mp4` is still an `.mp4`).

## Key facts (from investigation — these de-risk the change)

1. **Rendering already keys off the file extension, not the media_type.** Every
   gif-render site is `is_gif = media_type=="animatediff" OR video_path.endswith(".gif")`
   (`main_window.py` ~2272/2386/2881/3226/3612). A `media_type="video"` record
   whose file is `.gif` therefore renders as an animated gif with **no rendering
   change** — the `media_type=="animatediff"` clause is already redundant.
2. **The Video surfaces already accept `"animatediff"`.** `main_window.py:7978`
   (`video_recs = [r for r in records if r.media_type in ("video","animatediff")]`)
   and `history_store.py:335` already group them — the 55 native ones already
   appear under Video. Only the 73 artgen-typed ones are misfiled.
3. **Identity survives.** All AnimateDiff records carry
   `model_id="animatediff-blackhole"` (the field the model label reads), so under
   Video they still read as **AnimateDiff**. We additionally stamp
   `generator_type="animatediff"` so they're filterable/identifiable as such.
4. **Stats.** The 55 native records store the **same param shape as a real
   Wan2.2/Mochi/SkyReels video** (`duration_s, guidance_scale,
   num_inference_steps, negative_prompt, seed, …`) → identical stat display. The
   73 artgen-path records store a **leaner artgen shape** (`frames, steps, seed,
   temporal_alpha`) → their real stats, fewer fields. **Decision: leave the 73
   as-is** — no back-filling fields that were never recorded. Future gens use the
   native (video-shaped) record, so everything new is fully consistent.

## Design

### A. One-time, idempotent DB migration (past + any straggler)
`media_store.__init__` already runs migrations (`_migrate_from_json`). Add a
versioned migration (gated by `PRAGMA user_version` so it runs once per DB, but
written idempotently regardless):

```sql
-- AnimateDiff (.gif) -> video
UPDATE media SET media_type='video', generator_type='animatediff'
 WHERE media_type='animatediff' OR generator_type='animatediff';
-- Wan2.2-Animate (.mp4) -> video
UPDATE media SET media_type='video', generator_type='animate'
 WHERE media_type='animate' OR generator_type='animate';
```

- Flips all 128 AnimateDiff records + the Wan2.2-Animate record to
  `media_type="video"`, stamping `generator_type="animatediff"` / `"animate"`
  respectively (records that had `generator_type=None` gain the provenance
  label). Files are untouched (gifs stay `.gif`, the animate mp4 stays `.mp4`).
- **Params untouched** (per decision). No schema change.
- Runs automatically on every install's `media_store` load, so all past data
  self-heals with no user action.

### B. Future generation path → `media_type="video"`
- `history_store.GenerationRecord.new_animatediff(...)` currently sets
  `media_type="animatediff"` (`history_store.py:183`). Change it to
  `media_type="video"`, `generator_type="animatediff"` (keep the `.gif`
  `video_path` and the video-shaped params it already records). Likewise
  `new_animate(...)` (`history_store.py:150`, `media_type="animate"`) →
  `media_type="video"`, `generator_type="animate"` (keep the `.mp4`).
- **Audit every other AnimateDiff generation entry point** and make each write
  `media_type="video", generator_type="animatediff"` — in particular the artgen
  plugin path (`_create_generate_artgen`'s animatediff branch, `main_window.py`)
  if it is still reachable for AnimateDiff after "Video is Video". Confirm at
  plan time whether that path still fires for AnimateDiff; if it doesn't, note it
  and skip; if it does, fix its `media_type`.
- Net: every NEW AnimateDiff artifact is Video with AnimateDiff provenance and
  video-shaped stats.

### C. Rendering + routing cleanup (retire the bespoke type)
Now that no record is `media_type="animatediff"`:
- Replace the `media_type == "animatediff"` clauses with the extension check
  (`video_path.endswith(".gif")`) they already fall back to, at every `is_gif`
  site (`main_window.py` DetailPanel/GenerationCard/VideoPlayerWindow ~2272,
  2386, 2881, 3226, 3612). A gif is a gif **by file**, full stop.
- Drop both `"animatediff"` and `"animate"` from the media_type filters
  `in ("video","animate","animatediff")` (`main_window.py:7978`,
  `history_store.py:335`) → leave `("video",)`.
- `GenerationRecord.new_animatediff` no longer implies a distinct media_type; its
  contract becomes "a Video record made by AnimateDiff (`.gif`)".

### D. Knock-on benefit
Once AnimateDiff is `media_type="video"`, a **starred AnimateDiff gif becomes
eligible for the Video "Start Something" tile** (`possibilities` queries
`media_type="video"`) — closing the "the Video tile is a bare gradient" gap noted
earlier. (No extra work; it just follows from the type.)

## Resolved decisions
- **Fold `media_type="animate"` (Wan2.2-Animate) into `"video"`: YES** (Taylor).
  Included above — the migration's second UPDATE, the `new_animate` factory
  change, and dropping `"animate"` from the filters. It's an `.mp4`, so it
  renders through the normal `Gtk.Video` path (no gif concern); `generator_type=
  "animate"` + `model_id="wan2.2-animate-14b"` preserve its identity.
- **Params for the 73 older artgen-path AnimateDiff records: leave as-is**
  (Taylor) — no back-filling fields that were never recorded.

## Risks / invariants
- **Idempotent, reversible-by-data migration.** Files are never modified (gifs
  stay gifs). No thumbnails regenerated. Params untouched.
- **No rendering path change** — gif detection is already extension-based; we're
  removing a now-dead `media_type` special-case, not adding a renderer.
- **Audit video-consuming code for gif-safety BEFORE removing the media_type
  checks:** the attractor/TT-TV player, the GIF↔MP4 convert button, and export.
  The 55 native records already flow through Video, so most of this is proven;
  the 73 moving artgen→Video is the new exposure — verify the Video gallery card,
  detail pane, and attractor all handle a `.gif` video record. If any Video path
  assumes `.mp4`, guard it on `is_gif` (extension) rather than reintroducing the
  media_type.
- `collect()` / generation logic untouched except the `media_type` **value**
  written for AnimateDiff records.

## Testing (mostly pure / store-level)
- **Migration** (`test_media_store`): seed a fixture DB with rows
  (`media_type="animatediff"`, `media_type="artgen"/gen="animatediff"`, a real
  `video`, an `image`, an `artgen/verse`). After `media_store` init: both
  AnimateDiff rows are `media_type="video"`, `generator_type="animatediff"`; the
  video/image/verse rows are **untouched**; second init is a no-op (idempotent).
- **Factory** (`test_history_store`): `GenerationRecord.new_animatediff(...)` →
  `media_type="video"`, `generator_type="animatediff"`, `.gif` `video_path`.
- **Rendering** (widget): a `media_type="video"` record with a `.gif` `video_path`
  → `is_gif` True in the DetailPanel/GenerationCard/VideoPlayerWindow paths (they
  render an `AnimatedGifWidget`, not `Gtk.Video`).
- **Routing**: a `media_type="video"` `.gif` record is included by the Video
  gallery filter and NOT by the artgen (`media_type="artgen"`) query.
- **Tile**: a starred `media_type="video"` `.gif` resolves as the Video "Start
  Something" tile art.
- Regression: existing video/animate/artgen records still route + render as
  before; the removed `media_type=="animatediff"` clauses don't change any
  currently-passing gif test (they were `X or .gif` — the `.gif` half remains).

## Critical files
- `app/media_store.py` — the versioned AnimateDiff→video migration.
- `app/history_store.py` — `new_animatediff` factory → video; filter cleanup.
- `app/main_window.py` — `is_gif` cleanup (5 sites), Video-gallery media_type
  filters, and the future generation-path `media_type` for AnimateDiff.
- `app/worker.py` — the AnimateDiff worker's record `media_type`, if it sets one
  directly rather than via the factory (confirm at plan time).
- Tests: `tests/test_media_store*.py`, `tests/test_history_store*.py`, the
  gallery/detail widget tests, `tests/test_possibilities_wall.py`.
