# SP-3c — Migrate capabilities into Create — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax. Each task is an independent feature migration that ships on its own (version bump + changelog folded into each task's final step).

**Goal:** Move seed-image/i2i, native AnimateDiff, "Inspire me", the queue, and the attractor launch into Create so SP-3d can delete the legacy panels without losing anything. Generation internals unchanged — Create supplies the params.

**Tech Stack:** Python 3, GTK4/PyGObject, pytest (xvfb).

## Global Constraints

- Generation unchanged (`_on_generate`/workers); Create supplies params. Each migration keeps the generation path byte-identical.
- Legacy panels still exist (SP-3d deletes them) — don't break them. GTK threading via `GLib.idle_add`; FileDialog async per CLAUDE.md. Palette tt-vscode-toolkit; `_CSS` ASCII-only.
- System python; per-task version bump + changelog stanza; local only. Deselect known flakes: `tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`, `tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`.

---

### Task 1 (3c-1): Seed image / i2i into Create + re-enable SkyReels-I2V

**Files:** Modify `app/create_param_panels.py` (new `SeedImageWell`; add to `ImageParamPanel`/`VideoParamPanel`; SkyReels-I2V back in `_VIDEO_MODEL_CHOICES`/`_VIDEO_MODEL_IDS`), `app/main_window.py` (`_create_generate_native` passes `seed_image_path`); Test `tests/test_seed_image_well.py` + extend create-view/create-generate tests.

Extract ControlPanel's seed-well behavior (`_seed_thumb_box`: click→`Gtk.FileDialog` open; drop-target accepts a gallery frame / image file; right-click clear; 40×40 thumbnail; `_seed_image_path`) into a reusable `SeedImageWell(Gtk.Box)` with `path() -> str` and `set_path(p)`/`clear()`. Add one to Image + Video panels; each panel's `collect()` adds `"seed_image_path": self._seed_well.path()`. `_create_generate_native` passes `seed_image_path=params.get("seed_image_path","")` to `_on_generate`. Re-add `("skyreels", "SkyReels-V2-I2V ...")` to the video model list + its id mapping (now that a seed image can be supplied).

- [ ] Write failing tests (SeedImageWell pick/set/clear/path; Image+Video collect() includes seed_image_path; `_create_generate_native` forwards it; SkyReels-I2V present in video list). Run → FAIL.
- [ ] Implement `SeedImageWell` (adapt ControlPanel logic; FileDialog async try/except per CLAUDE.md); wire into panels + collect + `_create_generate_native`; re-add SkyReels-I2V. Run → PASS; full suite green.
- [ ] `VERSION` → `0.36.0`; changelog stanza (Create gains a seed-image well for i2i / image-conditioned models; SkyReels-I2V re-enabled); commit `feat(create): seed-image well (i2i) in Create + re-enable SkyReels-I2V (SP-3c-1)`.

---

### Task 2 (3c-2): Native AnimateDiff into Create

**Files:** Modify `app/create_param_panels.py` (AnimateDiff option + args controls), `app/create_view.py`/`app/main_window.py` (routing); Test accordingly.

Add native AnimateDiff to Create's Video medium — a model option `"animatediff"` whose panel exposes the full `get_animatediff_args` config (mode blackhole/cpu/sim, negative_prompt, temporal_alpha, chain_save, per-chip-prompt/prompt-schedule; READ ControlPanel's `_build_animatediff_box`/`get_animatediff_args` for the exact fields + defaults, mirror them). Selecting it makes `_create_generate_native` route with `video_model_key="animatediff"` + `animatediff_args={...}` into `_on_generate` (its animatediff branch already consumes them via `_ANIMATEDIFF_DEFAULTS`). Distinct from the artgen `animatediff` plugin.

- [ ] Failing tests (AnimateDiff selectable in Create video; collect()/routing yields `video_model_key=="animatediff"` + complete `animatediff_args`; `_on_generate` builds the AnimateDiff worker with those args). → FAIL.
- [ ] Implement the AnimateDiff option + args UI + routing. → PASS; full suite green.
- [ ] `VERSION` → `0.37.0`; changelog (native AnimateDiff generatable from Create with its full args); commit `feat(create): native AnimateDiff in Create (SP-3c-2)`.

---

### Task 3 (3c-3): "Inspire me" prompt-gen in Create

**Files:** Modify `app/create_view.py` (Inspire-me button in the brief zone); reuse the existing prompt-gen path; Test extend `tests/test_create_view.py`.

Add an "Inspire me" button near the brief that fills the prompt via the existing prompt generator (the `generate_prompt.py` subprocess / prompt-server path ControlPanel used), async with `GLib.idle_add`, fail-soft (button re-enables, no crash if the server is down). Distinct from the inspiration→Muse door.

- [ ] Failing tests (Inspire-me triggers the prompt-gen fn (injected/faked); on result fills the brief via idle_add; failure fail-soft). → FAIL.
- [ ] Implement (reuse prompt-gen seam; inject the fn for tests). → PASS; full suite green.
- [ ] `VERSION` → `0.38.0`; changelog (Inspire-me fills the brief in Create); commit `feat(create): Inspire-me prompt-gen in the brief zone (SP-3c-3)`.

---

### Task 4 (3c-4): Generation queue in Create

**Files:** Modify `app/create_view.py` (queue display in the result pane) + `app/main_window.py` (enqueue-from-Create). Test extend create-generate tests.

When a job is running, a Create click ENQUEUES (via the existing `_QueueItem`/`_on_enqueue`/`_persist_queue`) instead of the current no-op guard, and Create shows the pending list (with cancel) in the result pane near the recents strip (reuse `_update_queue_display` data). Replace the SP-3-era re-entrancy no-op with enqueue.

- [ ] Failing tests (Create-while-busy enqueues a faithful `_QueueItem`; pending list rendered + cancellable; replay faithful). → FAIL.
- [ ] Implement. → PASS; full suite green.
- [ ] `VERSION` → `0.39.0`; changelog (Create queues jobs when busy + shows pending); commit `feat(create): generation queue in Create (SP-3c-4)`.

---

### Task 5 (3c-5): Attractor / TT-TV launch in the shell

**Files:** Modify `app/main_window.py`/`app/create_view.py` (attractor launch affordance) + attractor model source. Test accordingly.

Migrate the "Start Endless" attractor launch out of ControlPanel into the surviving shell (a control in Create or the top bar). Keep AttractorWindow + `_on_attractor_generate`. Resolve `_on_attractor_generate`'s `_controls.get_*` model reads to a non-ControlPanel source (or explicitly defer to SP-3d with a code note if it needs the deleted UI's state).

- [ ] Failing tests (attractor launch reachable from the shell; starts TT-TV; model source resolved without ControlPanel OR deferral noted). → FAIL.
- [ ] Implement. → PASS; full suite green.
- [ ] `VERSION` → `0.40.0`; changelog (TT-TV auto-gen launchable from the shell); commit `feat(create): attractor/TT-TV launch in the shell (SP-3c-5)`.

---

## Notes for the executor
- Each task ships independently (own version bump). Generation internals unchanged — Create supplies params.
- Don't break the legacy panels (SP-3d deletes them). For native AnimateDiff (Task 2), READ ControlPanel's real `get_animatediff_args` fields — don't guess.
- After all five, SP-3d can delete ControlPanel/ArtgenPanel/medium-tabs and retire the remaining pollers.
