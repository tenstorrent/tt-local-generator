# SP-3d — Delete the vestiges (retire ControlPanel/ArtgenPanel/legacy tabs)

**Date:** 2026-07-17
**Branch:** `feat/pipeline-editor` (local; not merged)
**Status:** design self-approved (user directive; Theme-Set/Repeat-Last decision made)
**Program:** coherent shell, SP-3 stage **d (final)**. Grounded in the dependency
audit: `.superpowers/sdd/sp3d-audit.md`. SP-3a/b/c migrated every capability into
the Create/Discover/Remix shell; this deletes the legacy surfaces.

## Goal

Remove the legacy ControlPanel, ArtgenPanel's redundant generation sidebar, the
medium-tab source toggle, and the Generative-Art tab; retire the legacy health
pollers (re-pointing the bottom status dot at `ModelStatusService`); and rest
the window on the Create/Discover/Remix shell alone — after first migrating the
two remaining ControlPanel-only features (Theme Set, Repeat-Last) into Create so
nothing is lost.

## Global constraints

- **Nothing lost:** Theme Set + Repeat-Last are migrated BEFORE any deletion
  (user: never drop). Every other capability already lives in the shell.
- **Generation/Discover unchanged:** `_on_generate`/workers untouched; the three
  native galleries + the artgen gallery keep working for Discover; a full
  generation of each medium still works from Create.
- **Single source of truth:** the surviving status dot reads `ModelStatusService`
  (the legacy pollers are deleted, not left disagreeing).
- **Incremental + green:** each sub-stage ships independently with the full suite
  green (2 known flakes deselected). System python; version bump + changelog per
  sub-stage; local only.

## Staging (ordered; delete only after the migrations)

### 3d-1 · Migrate Theme Set into Create
Theme Set generates a themed multi-shot batch (see ControlPanel's theme path:
`set_theme_result`/`set_theme_error`, `_on_theme_queue_shots`,
`get_generation_defaults`). Add a "Theme Set" action to Create (a button in the
Create surface, or a Direction/Controls affordance) that generates the same
N-shot themed batch, reusing `_on_theme_queue_shots`/the enqueue path. Reuse the
existing theme-generation backend; only the launch UI moves to Create.

### 3d-2 · Migrate Repeat-Last seed mode into Create
ControlPanel's seed modes include "repeat last" (reuse the previous seed) via
`_apply_seed_mode_from_settings`. Add a seed-mode option to Create's Controls
zone (the seed field): a small control (random / fixed / repeat-last) so a user
can reproduce the last seed. Wire it into the seed value `collect()` supplies.

### 3d-3 · Rehome surviving `_controls.*` hooks
Per the audit, resolve every SURVIVING `_controls.*` read so nothing references
ControlPanel:
- `get_model_source`/`get_video_model`/`get_image_model` (surviving callers:
  `_active_gallery`, `_on_loop_nav_discover`, `_hide_pipelines`,
  `_on_open_attractor`, `_update_attractor_btn`, `_on_start_server`,
  `_on_stop_server`, `_hw_statusbar` start btn) → derive from
  `CreateView._active_medium` via a small `medium → capability/source` helper
  (Medium.id is the generator name for artgen mediums, not `"artgen"` — the
  helper maps it). Add the helper on MainWindow/CreateView.
- `set_busy` (×6) → DELETE (dead: only drives ControlPanel's own buttons; nothing
  surviving reads it — audit-confirmed).
- `_set_model`/`_video_model`/`_server_ready`/`_running_model`/`_server_launching`
  → resolve to their surviving equivalents (ServersControl / status service /
  MainWindow fields) or delete if legacy-only.
- theme/inspire/prompt-gen setters (`set_theme_result/error`,
  `set_inspire_result/error`, `set_prompt_gen_state`) → point at the new Create
  Theme-Set/Inspire surfaces (3d-1 / SP-3c-3).
This sub-stage ends with ZERO surviving `self._controls.<member>` reads (only the
legacy-only ones remain, and they're deleted with ControlPanel in 3d-5).

### 3d-4 · Window-layout restructure
`toolbar_box`/`footer_box` are ControlPanel-built but MainWindow appends
Watch-TT-TV / Pipelines / Servers ▾ onto them → fold those into
`_build_loop_nav()`'s row (the surviving top bar). The 3-pane
`outer_paned`/`_ctrl_wrapper` layout collapses to a 2-pane gallery|detail split
(already flagged in a code comment). After this, MainWindow no longer mounts
`_controls.toolbar_box()`/`footer_box()`.

### 3d-5 · Delete ControlPanel + ArtgenPanel sidebar + medium-tab toggle + Gen-Art tab
- Delete the `ControlPanel` class + its construction + the medium-tab source
  toggle + the Generative-Art tab. All remaining `_controls.*` references are now
  legacy-only and go with it.
- ArtgenPanel: swap `ArtgenPanel()` in the gallery stack for the standalone
  `ArtgenGallery()` (artgen_gallery.py) so Discover keeps browsing artgen media;
  delete ArtgenPanel's generation sidebar (redundant with Create's artgen
  mediums) and its `_check_health_bg` poller (goes with the class).

### 3d-6 · Retire legacy health pollers + re-point the status dot
Delete `_health_loop`, `_artgen_health_loop`, `_prompt_gen_health_loop`
(redundant with `ModelStatusService`). Re-point `_hw_statusbar`'s server dot at
the service (subscribe, using the same aggregation `ServersControl.status_bar`
implements — the `TODO(SP-3d)` marker). The single status dot now reads the one
source of truth.

## Testing (per sub-stage)

- 3d-1: Theme Set from Create enqueues the same N-shot themed batch as before.
- 3d-2: Repeat-Last seed mode reproduces the previous seed via Create's Controls.
- 3d-3: grep proves zero surviving `self._controls.<member>` reads; `_active_gallery`/
  Discover/attractor/servers resolve source/model without ControlPanel; a full
  Create generation of each medium still works (parity harness).
- 3d-4: window builds without mounting ControlPanel's toolbar/footer; loop-nav row
  carries Watch-TT-TV/Pipelines/Servers; 2-pane layout; no missing controls.
- 3d-5: ControlPanel class gone (grep); ArtgenGallery in the stack (Discover browses
  artgen); full suite green; generation of every medium works from Create.
- 3d-6: `_hw_statusbar` dot reflects the service (ready/starting/off); legacy
  pollers removed; one status dot, on the service.

## Notes
- The audit (`.superpowers/sdd/sp3d-audit.md`) is the authoritative dependency map
  — each sub-stage's implementer must consult it.
- Delete ONLY after 3d-1/3d-2 migrations + 3d-3 rehoming are green. Order matters.
