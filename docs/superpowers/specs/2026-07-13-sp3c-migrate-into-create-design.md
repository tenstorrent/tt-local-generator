# SP-3c — Migrate remaining capabilities into Create

**Date:** 2026-07-13
**Branch:** `feat/pipeline-editor` (local; not merged)
**Status:** design self-approved (user directive "keep on going")
**Program:** coherent shell, SP-3 (retire vestiges), stage **c of a-d**. Moves the
last vestige-only capabilities into Create so SP-3d can delete ControlPanel/
ArtgenPanel without losing anything.

## Goal

Give the Create surface everything the legacy panels still uniquely provide, so
generation of every kind is possible from Create alone: seed-image/i2i, native
AnimateDiff, "Inspire me" prompt-gen, the generation queue, and the attractor/
TT-TV launch. Each is an independent migration (its own task, own review, own
ship). Generation internals (`_on_generate`, workers) unchanged — Create supplies
the params.

## Scope — five migrations (most-critical-first)

### 3c-1 · Seed image / image-to-image
- Extract ControlPanel's seed-image well (`_seed_thumb_box` — click-to-pick via
  `Gtk.FileDialog`, drop a gallery frame, right-click clear, thumbnail) into a
  reusable `SeedImageWell` widget.
- Add it to `ImageParamPanel` and `VideoParamPanel`; `collect()` returns
  `seed_image_path`. `_create_generate_native` passes it through to `_on_generate`
  (already accepts `seed_image_path=`).
- **Re-enable SkyReels-I2V** in `VideoParamPanel`'s model list (removed in v0.27.1
  precisely because Create couldn't supply a conditioning image — now it can).

### 3c-2 · Native AnimateDiff (migrate, per Taylor — never drop)
- Add a native **AnimateDiff** path to Create's Video medium (a model option, or
  a sibling medium) carrying the full `get_animatediff_args` config: mode
  (blackhole/cpu/sim), negative_prompt, temporal_alpha, chain_save, per-chip-
  prompt/prompt-schedule, and any other fields ControlPanel's AnimateDiff box
  exposes.
- Its `collect()`/routing produces `video_model_key="animatediff"` +
  `animatediff_args={...}` into `_on_generate` (whose animatediff branch already
  consumes them, defaulted by `_ANIMATEDIFF_DEFAULTS`). Distinct from the artgen
  `animatediff` plugin (which stays as an artgen medium).

### 3c-3 · "Inspire me" prompt-gen
- A one-tap prompt-generator button in Create's brief zone that fills the prompt
  (reuse the existing prompt-gen path — `generate_prompt.py` subprocess / the
  prompt-server — with the same async+`GLib.idle_add` pattern ControlPanel used).
- Distinct from the "inspiration" door (→ Muse): this fills the current brief in
  place.

### 3c-4 · Generation queue
- Surface the queue in Create: when a job is running, a second Create enqueues
  (instead of the current no-op guard), and Create shows the pending list with
  cancel. Reuse `_QueueItem`/`_queue`/`_persist_queue`/`_update_queue_display`
  and `_on_enqueue` — render the queue in the Create result pane (near the
  recents strip) rather than the ControlPanel queue display.

### 3c-5 · Attractor / TT-TV auto-gen launch
- Move the "Start Endless" attractor launch into the surviving shell (a control
  in Create or the top bar) so TT-TV auto-gen can be started without ControlPanel.
  The AttractorWindow itself and `_on_attractor_generate` stay; only the launch
  affordance migrates. (Also give the attractor a non-ControlPanel model source —
  SP-3a noted `_on_attractor_generate` still reads `_controls.get_*`; resolve it
  here or defer the model-source part to SP-3d — but the launch button migrates
  here.)

## Global constraints

- **Generation unchanged:** `_on_generate`/workers untouched; Create supplies
  params (seed_image_path, animatediff_args, model keys). Each migration keeps
  the existing generation path byte-identical.
- **Migration-safe / incremental:** each of 3c-1..3c-5 ships independently; the
  legacy panels still exist (SP-3d deletes them), so nothing breaks mid-stage.
- GTK threading: prompt-gen/attractor async work via `GLib.idle_add`. FileDialog
  async per the CLAUDE.md pattern. Palette tt-vscode-toolkit; `_CSS` ASCII-only.
- System python; version bump + changelog per shipped sub-stage; local only.
  Deselect the two known flakes in full-suite runs.

## Testing (per sub-stage)

- 3c-1: `SeedImageWell` (pick/drop/clear/thumbnail); Image/Video `collect()`
  includes `seed_image_path`; `_create_generate_native` passes it; SkyReels-I2V
  back in the video list and only generatable with a seed image.
- 3c-2: native AnimateDiff option present in Create; its collect() yields
  `video_model_key="animatediff"` + a complete `animatediff_args`; `_on_generate`
  builds the AnimateDiff worker with those args.
- 3c-3: Inspire-me fills the brief (async, idle_add); errors fail-soft.
- 3c-4: enqueue-when-busy from Create; pending list shown + cancellable; queue
  replay faithful.
- 3c-5: attractor launch reachable from the shell; starts TT-TV; model source
  resolved without ControlPanel (or explicitly deferred to SP-3d with a note).

## File summary (per sub-stage)

| Sub-stage | Files |
|---|---|
| 3c-1 | `app/create_param_panels.py` (SeedImageWell + Image/Video panels + SkyReels-I2V), `app/main_window.py` (`_create_generate_native` passes seed_image_path) |
| 3c-2 | `app/create_param_panels.py` (AnimateDiff option + args), `app/create_view.py`/`main_window.py` routing |
| 3c-3 | `app/create_view.py` (Inspire-me in brief zone), reuse prompt-gen path |
| 3c-4 | `app/create_view.py` (queue display in result pane), `app/main_window.py` (enqueue-from-Create) |
| 3c-5 | `app/main_window.py`/`app/create_view.py` (attractor launch), attractor model source |
