# SP-3d — Delete the vestiges — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax. Each task ships independently (version bump + changelog folded into its final step). CONSULT the dependency audit `.superpowers/sdd/sp3d-audit.md` — it is the authoritative map.

**Goal:** Migrate the last two ControlPanel-only features into Create, rehome every surviving `_controls.*` hook, restructure the window layout, then delete ControlPanel + ArtgenPanel's sidebar + the medium-tab toggle + the Gen-Art tab, and retire the legacy health pollers (re-pointing the bottom status dot at `ModelStatusService`).

**Tech Stack:** Python 3, GTK4/PyGObject, pytest (xvfb).

## Global Constraints

- Nothing lost: Theme Set + Repeat-Last migrated BEFORE any deletion. Generation/Discover unchanged. The three native galleries + the artgen gallery keep working.
- DELETE only after 3d-1/3d-2 (migrations) + 3d-3 (rehome) are green — order matters.
- Single source of truth: the surviving status dot reads `ModelStatusService`; legacy pollers deleted, not left disagreeing.
- System python; per-task version bump + changelog; local only. Deselect known flakes: `tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`, `tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`.

---

### Task 1 (3d-1): Migrate Theme Set into Create
**Files:** `app/create_view.py` (+ `app/create_param_panels.py` if needed), `app/main_window.py`. Consult audit §1 (theme path: `set_theme_result`/`set_theme_error`, `_on_theme_queue_shots`, `get_generation_defaults`).
Add a "Theme Set" action to Create that generates the same N-shot themed batch, reusing `_on_theme_queue_shots`/the enqueue path (only the launch UI moves to Create; the theme backend is reused, not reimplemented). Wire theme result/error to a Create surface (status/result panel).
- [ ] Failing tests (Theme Set from Create enqueues the same N-shot themed batch; result/error surface in Create). → FAIL → implement → PASS → full suite.
- [ ] `VERSION` → `0.41.0`; changelog; commit `feat(create): Theme Set (N-shot themed batch) in Create (SP-3d-1)`.

---

### Task 2 (3d-2): Migrate Repeat-Last seed mode into Create
**Files:** `app/create_param_panels.py` (Controls/seed), `app/create_view.py`. Consult audit (seed modes, `_apply_seed_mode_from_settings`).
Add a seed-mode control to Create's Controls zone (random / fixed / repeat-last) that reproduces the previous seed; wire it into the seed value `collect()` supplies (repeat-last → the last-used seed persisted in settings, same source ControlPanel used).
- [ ] Failing tests (repeat-last reproduces the prior seed via Create's Controls; random/fixed unchanged). → FAIL → implement → PASS → full suite.
- [ ] `VERSION` → `0.42.0`; changelog; commit `feat(create): repeat-last seed mode in Create Controls (SP-3d-2)`.

---

### Task 3 (3d-3): Rehome surviving `_controls.*` hooks
**Files:** `app/main_window.py`, `app/create_view.py`. Consult audit §1 (the SURVIVING-vs-LEGACY classification).
Resolve every SURVIVING `_controls.*` read so nothing references ControlPanel:
- Add a `medium → source/capability` helper (Medium.id is the generator name for artgen → maps to `"artgen"`). Point `_active_gallery`, `_on_loop_nav_discover`, `_hide_pipelines`, `_on_open_attractor`, `_update_attractor_btn`, `_on_start_server`, `_on_stop_server`, `_hw_statusbar` start-btn at `CreateView._active_medium` (via the helper) instead of `get_model_source`/`get_video_model`/`get_image_model`.
- Delete the 6 `set_busy` calls (dead per audit).
- Resolve `_set_model`/`_video_model`/`_server_ready`/`_running_model`/`_server_launching` + the theme/inspire/prompt-gen setters to their surviving equivalents (ServersControl / status service / the new Create Theme-Set & Inspire surfaces), or delete if legacy-only.
- [ ] Failing tests (grep-assert zero SURVIVING `self._controls.<member>` reads remain; `_active_gallery`/Discover/attractor/servers resolve source/model without ControlPanel; full Create generation parity per medium). → FAIL → implement → PASS → full suite.
- [ ] `VERSION` → `0.43.0`; changelog; commit `refactor(shell): rehome surviving ControlPanel hooks off _controls (SP-3d-3)`.

---

### Task 4 (3d-4): Window-layout restructure
**Files:** `app/main_window.py`. Consult audit §2.
Fold Watch-TT-TV / Pipelines / Servers ▾ into `_build_loop_nav()`'s row (surviving top bar). Stop mounting `_controls.toolbar_box()`/`footer_box()`. Collapse the 3-pane `outer_paned`/`_ctrl_wrapper` to a 2-pane gallery|detail split. (ControlPanel still constructed until 3d-5 — just not mounted; if that's awkward, sequence 3d-4 into 3d-5.)
- [ ] Failing tests (window builds without mounting ControlPanel's toolbar/footer; loop-nav row carries Watch-TT-TV/Pipelines/Servers; 2-pane layout; no missing controls). → FAIL → implement → PASS → full suite.
- [ ] `VERSION` → `0.44.0`; changelog; commit `refactor(shell): loop-nav top bar + 2-pane layout; ControlPanel toolbar/footer unmounted (SP-3d-4)`.

---

### Task 5 (3d-5): Delete ControlPanel + ArtgenPanel sidebar + medium-tab toggle + Gen-Art tab
**Files:** `app/main_window.py`. Consult audit §3.
- Delete the `ControlPanel` class + construction + medium-tab source toggle + Generative-Art tab (all remaining `_controls.*` refs are now legacy-only and go with it).
- Swap `ArtgenPanel()` in the gallery stack for the standalone `ArtgenGallery()` (artgen_gallery.py) so Discover keeps browsing artgen media; delete ArtgenPanel's generation sidebar + its `_check_health_bg` poller.
- [ ] Failing tests (ControlPanel class gone — grep; `ArtgenGallery` in the stack, Discover browses artgen; generation of every medium works from Create; no dangling refs). → FAIL → implement → PASS → full suite.
- [ ] `VERSION` → `0.45.0`; changelog; commit `feat(shell): delete ControlPanel + ArtgenPanel sidebar + legacy tabs (SP-3d-5)`.

---

### Task 6 (3d-6): Retire legacy health pollers + re-point the status dot
**Files:** `app/main_window.py`. Consult audit §4.
Delete `_health_loop`, `_artgen_health_loop`, `_prompt_gen_health_loop`. Re-point `_hw_statusbar`'s server dot at `ModelStatusService` (subscribe; reuse the aggregation `ServersControl.status_bar` implements — the `TODO(SP-3d)` marker).
- [ ] Failing tests (`_hw_statusbar` dot reflects a pushed service snapshot: ready/starting/off; legacy poller loops removed — grep; one status dot on the service). → FAIL → implement → PASS → full suite.
- [ ] `VERSION` → `0.46.0`; changelog; CLAUDE.md: mark SP-3/the vestige retirement DONE; commit `feat(status): retire legacy health pollers; bottom status dot on ModelStatusService (SP-3d-6)`.

---

## Notes for the executor
- The audit `.superpowers/sdd/sp3d-audit.md` is authoritative — consult it per task.
- Order is load-bearing: migrations (3d-1/2) + rehome (3d-3) BEFORE any delete (3d-5).
- Generation internals (`_on_generate`/workers) stay untouched throughout; a full generation of every medium from Create must keep working after each task.
