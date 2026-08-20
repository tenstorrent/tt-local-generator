# SP-3a — Decouple `_on_generate` from ControlPanel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** `_on_generate` selects the model from explicit params, not `self._controls` — with byte-identical generation for every caller — so ControlPanel can later be deleted (SP-3d).

**Architecture:** Add `video_model_key`/`image_model_key`/`animatediff_args` params to `_on_generate`; remove its `self._controls.get_video_model/get_image_model/get_animatediff_args` reads; update all callers (legacy ControlPanel generate/enqueue, Create `_create_generate_native`, queue `_start_next_queued`, attractor) to pass them; drop the Create `_controls._video_model` sync hack.

**Tech Stack:** Python 3, GTK4/PyGObject, pytest (xvfb).

## Global Constraints

- Generation byte-identical per caller (parity is the invariant). No surface deleted (SP-3d). No `gi`-thread violations. System python. Version bump + changelog on landing. Local only. Deselect known flakes: `tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`, `tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`.

---

### Task 1: Parameterize `_on_generate`'s model selection + update callers

**Files:** Modify `app/main_window.py`; Test `tests/test_main_window_create_generate.py` (extend) + `tests/test_main_window_decouple.py` (new).

**Interfaces — Produces:** `_on_generate(..., model_id="", video_model_key=None, image_model_key=None, animatediff_args=None)` that reads NO `self._controls.get_*` for model selection; all callers pass model params.

- [ ] **Step 1: failing tests**
```python
# _on_generate must not read model selection off _controls
def test_on_generate_does_not_read_controls_model(mw):
    mw._controls.get_video_model = MagicMock(side_effect=AssertionError("must not read"))
    mw._controls.get_image_model = MagicMock(side_effect=AssertionError("must not read"))
    mw._controls.get_animatediff_args = MagicMock(side_effect=AssertionError("must not read"))
    # image job with explicit model
    mw._on_generate("p","",20,-1, model_source="image", model_id="flux.1-schnell")
    # (assert the ImageGenerationWorker got model "flux.1-schnell" via the worker-capture harness)
def test_video_uses_explicit_key_not_controls(mw):
    # video job with video_model_key="mochi" -> mochi worker, no _controls read
    ...
def test_create_video_no_longer_syncs_controls_video_model(mw):
    # _create_generate_native video branch does NOT call _controls._set_model / set _controls._video_model
    ...
def test_queue_replay_uses_item_model(mw):
    # a _QueueItem with model_id -> _start_next_queued passes it through so the right worker is built
    ...
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement per the spec:
  - Add the three params to `_on_generate`. Image: `img_model_key = model_id or image_model_key or _DEFAULT_IMAGE_KEY`. Video: `vk = video_model_key or _VIDEO_MODEL_ID_TO_KEY.get(model_id) or _DEFAULT_VIDEO_KEY` (keep the `vk == "animatediff"` branch). AnimateDiff branch: use the `animatediff_args` param. Remove all three `self._controls.get_*` model reads.
  - Legacy ControlPanel generate/enqueue call site: pass `video_model_key=self._controls.get_video_model()`, `image_model_key=self._controls.get_image_model()`, `animatediff_args=self._controls.get_animatediff_args()`.
  - `_create_generate_native`: pass `video_model_key`/`image_model_key` from the medium's chosen key; DELETE the `self._controls._set_model(...)`/`self._controls._video_model = ...` sync (v0.27.1 hack).
  - `_start_next_queued` + `_on_attractor_generate`: pass `video_model_key`/`image_model_key` derived from `item.model_id` (or add `video_model_key` to `_QueueItem` if the `model_id`→key mapping isn't total; prefer derive).
  - Keep `set_busy` and other non-model `_controls` calls untouched.
- [ ] **Step 4:** run → PASS; full suite green (per-caller parity intact).
- [ ] **Step 5:** commit `refactor(generate): _on_generate takes model params; drop ControlPanel model reads + Create sync hack`.

---

### Task 2: Version, changelog, CLAUDE.md

**Files:** `VERSION`, `debian/changelog`, `CLAUDE.md`.

- [ ] **Step 1:** `VERSION` → `0.34.0`.
- [ ] **Step 2:** changelog 0.34.0 stanza: internal refactor — `_on_generate` now takes the model as an explicit parameter instead of reading it from the (soon-to-be-retired) ControlPanel; the Create video path no longer needs its model-sync workaround. No user-visible behavior change; prerequisite for retiring the legacy panels.
- [ ] **Step 3:** CLAUDE.md: note under "Model status"/Create that `_on_generate` is decoupled from ControlPanel (SP-3a) — model passed explicitly; the one remaining ControlPanel model read is the legacy generate call site, which goes with ControlPanel in SP-3d.
- [ ] **Step 4:** full suite green (deselect the two known flakes).
- [ ] **Step 5:** commit `chore: release v0.34.0 -- decouple generation from ControlPanel (SP-3a)`.

---

## Notes for the executor
- Parity is the whole game: for EVERY caller the worker must be built with the same model as before. The `test_main_window_create_generate` harness already captures worker construction — reuse it.
- Do not delete ControlPanel or any surface (SP-3d). Only the *model reads* move out of `_on_generate`.
- The legacy generate call site legitimately still reads `_controls` (it IS ControlPanel's button) — that's expected and removed in SP-3d.
