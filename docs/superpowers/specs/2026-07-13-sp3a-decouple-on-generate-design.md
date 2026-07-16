# SP-3a — Decouple `_on_generate` from ControlPanel

**Date:** 2026-07-13
**Branch:** `feat/pipeline-editor` (local; not merged)
**Status:** design self-approved (user directive "continue with sp-1 to 2 and 3")
**Program:** coherent shell, SP-3 (retire vestiges), stage **a of a-d**. This is the
safe, non-destructive prerequisite: nothing is deleted; generation is unchanged.

## Problem

`_on_generate` reads model selection off `self._controls` (ControlPanel):
- image branch: `img_model_key = model_id or self._controls.get_image_model()`
- video branch: `video_model_key = self._controls.get_video_model()` (ignores the
  passed `model_id` — the root of an earlier bug class; CreateView works around it
  by syncing `self._controls._video_model` before calling — the v0.27.1 hack)
- animatediff sub-branch: `self._controls.get_animatediff_args()`

This coupling blocks deleting ControlPanel (SP-3d). Decouple it: `_on_generate`
takes model selection as explicit params; every caller passes them.

## Goal

`_on_generate` no longer reads `self._controls` for **model selection**. It
takes `video_model_key` / `image_model_key` / `animatediff_args` params; callers
supply them. Generation behavior is byte-for-byte identical for every existing
path (legacy ControlPanel, Create, queue, attractor). The CreateView
`_controls._video_model` sync hack is removed (Create passes the key directly).

## Non-goals

- No deletion of ControlPanel/any surface (SP-3d).
- No change to `_on_generate`'s *set*-side ControlPanel calls (`set_busy`, etc.) —
  those are ControlPanel UI state, removed when ControlPanel is deleted (SP-3d).
- Native-AnimateDiff's ultimate fate (migrate vs drop) is an SP-3c/3d decision;
  here it keeps working exactly as today via the passed `animatediff_args`.

## Global constraints

- **Generation unchanged:** every caller (legacy ControlPanel generate/enqueue,
  Create `_create_generate_native`, queue `_start_next_queued`, attractor) must
  produce the identical worker call as today. Guarded by tests.
- No `gi`-thread violations introduced. Palette/CSS untouched. System python.
  Version bump + changelog on landing. Local only. Deselect the two known flakes
  in full-suite runs.

## Architecture

### `_on_generate` signature + body

Add params (keyword, defaulted so partial callers still work during the change):
```python
def _on_generate(self, prompt, neg, steps, seed, seed_image_path="",
                 model_source="video", guidance_scale=3.5,
                 ref_video_path="", ref_char_path="",
                 animate_mode="animation", model_id="",
                 video_model_key=None, image_model_key=None,
                 animatediff_args=None) -> None:
```
Body changes (model selection only):
- **image:** `img_model_key = model_id or image_model_key or _DEFAULT_IMAGE_KEY`
  (drop `self._controls.get_image_model()`).
- **video:** `vk = video_model_key or _VIDEO_MODEL_ID_TO_KEY.get(model_id) or _DEFAULT_VIDEO_KEY`
  (drop `self._controls.get_video_model()`). Keep the existing
  `vk == "animatediff"` branch.
- **animatediff branch:** use the `animatediff_args` param (drop
  `self._controls.get_animatediff_args()`).

### Callers pass model params

- **Legacy ControlPanel generate/enqueue** (the call site that today relies on
  `_on_generate` reading `_controls`): resolve at the call site —
  `video_model_key=self._controls.get_video_model()`,
  `image_model_key=self._controls.get_image_model()`,
  `animatediff_args=self._controls.get_animatediff_args()` — and pass them. This
  is the ONE place that still reads ControlPanel (legitimately — it IS
  ControlPanel's generate button); it goes away with ControlPanel in SP-3d.
- **Create `_create_generate_native`** (image/video/animate branches): pass
  `video_model_key`/`image_model_key` derived from the medium's chosen model
  (Create already has `model_id`/the scoped dropdown key) and **remove the
  `self._controls._set_model(...)` / `self._controls._video_model = ...` sync
  hack** (v0.27.1) — no longer needed.
- **Queue `_start_next_queued`** and **attractor `_on_attractor_generate`**: these
  already carry `model_id` on `_QueueItem`; pass `video_model_key` derived from
  `model_id` (or store the key on `_QueueItem` if cleaner) so replay is faithful.
  `_QueueItem` already has `model_id`; add `video_model_key`/`image_model_key`/
  `animatediff_args` fields ONLY if needed for faithful replay (prefer deriving
  from `model_id` where the mapping is total).

### Fallback safety

During SP-3a, if a param is `None` AND `model_id` is empty (a caller not yet
updated), the branch may fall back to the medium default (NOT to `_controls`) —
so `_on_generate` has ZERO `self._controls.get_*` model reads after this task.
The legacy generate call site is updated to pass the ControlPanel values, so the
legacy path is unaffected.

## Testing

- `_on_generate` uses the passed `video_model_key`/`image_model_key`/
  `animatediff_args` and does NOT call `self._controls.get_video_model`/
  `get_image_model`/`get_animatediff_args` (assert via a fake `_controls` whose
  those methods raise / are MagicMocks asserted not-called).
- Parity: for each caller (legacy, Create, queue, attractor) the resulting worker
  is constructed with the same model as before this task (reuse the existing
  `test_main_window_create_generate` parity harness; extend for legacy/queue).
- Create video path no longer syncs `_controls._video_model` (assert the sync
  call is gone / `_controls` untouched) yet the correct video worker is chosen.
- A queued item replays with its stored model (faithful replay).
- Full suite green (deselect the two known flakes).

## File summary

| File | Change |
|---|---|
| `app/main_window.py` | `_on_generate` model params + drop `_controls` model reads; update legacy/Create/queue/attractor callers to pass model; remove the Create `_video_model` sync hack; `_QueueItem` fields only if needed |
| `tests/…` | no-`_controls`-model-read assertion; per-caller model parity; sync-hack removed; queue replay faithful |
