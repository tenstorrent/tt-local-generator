# SP-2 — Wire ModelStatusService into Create (loading dots + auto-select)

**Date:** 2026-07-13
**Branch:** `feat/pipeline-editor` (local; not merged)
**Status:** design self-approved (per user instruction "continue with sp-1 to 2 and 3")
**Program:** "coherent shell." SP-1 built `ModelStatusService` (v0.32.0). This is
SP-2. SP-3 retires the vestiges and gives the surviving shell its one status
control.

## Problem / goal

The status service exists but nothing consumes it. Wire it into the **surviving**
Create surface so Create shows an at-a-glance loading state (◌ off / ◐ starting /
● ready) and **auto-selects the model that's actually running** for the active
medium — and make the service's `starting` state accurate for app-initiated
starts.

## Scope decision (important)

The earlier "unify ALL surfaces" answer predates the decision to retire the
vestiges. Since **SP-3 deletes** the footer server row, the "Servers ▾" popover,
ControlPanel, and ArtgenPanel, rewiring those doomed surfaces here would be
throwaway. SP-2 therefore wires only:
1. **Service lifecycle** — construct + start on app open, stop on close.
2. **Start/stop hooks** — route app-initiated server start/stop through
   `note_starting`/`note_stopping` (inferred-starting is the backstop for any
   path missed).
3. **CreateView** — scoped model dropdown + Model door read the service
   (3-state dots) and auto-select the running/starting model.

The legacy pollers (`MainWindow._health_loop`, `_refresh_servers_popover`,
`artgen_panel._check_health_bg`) are **left as-is** and deleted in SP-3, which
also stands up the single surviving status control on the service. (Transiently,
those legacy surfaces keep their own polling — fine, they're about to go.)

## Non-goals

- No deletion of legacy surfaces/pollers (SP-3).
- No generation-internals change.

## Global constraints

- **One service instance**, owned by `MainWindow`, injected into `CreateView`
  (new optional `status_service` seam; default `None` → CreateView keeps its
  current `status_all`-polling fallback so existing tests/standalone construction
  are unaffected).
- **GTK threading:** the service notifies subscribers on its poll thread —
  CreateView's subscriber callback MUST marshal to the main thread via
  `GLib.idle_add` before touching widgets.
- **Palette:** tt-vscode-toolkit; dot glyphs (◌ ◐ ●) are Python strings, never in
  a `b"""` CSS literal.
- **Preserve the v0.28.1 fix:** a health-refresh repopulation must not clobber a
  manual model pick; auto-select applies on medium-switch / first populate, not
  on every refresh.
- System python; tests via `xvfb-run … pytest`. Version bump + changelog on
  landing. Local only. Known flakes deselected in full-suite runs
  (`test_pipeline_engine::test_run_plugin_loads_and_calls_real_module`,
  `test_forge_transforms::test_on_transform_finished_appends_and_refreshes`).

## Architecture

### 1. Service lifecycle (MainWindow)

- In `MainWindow` init (where `_start_health_worker` is set up), construct
  `self._status_service = ModelStatusService()` and `self._status_service.start()`.
- In `do_close_request` (where `_health_stop` is set), call
  `self._status_service.stop()`.

### 2. Start/stop hooks

- At the app's server-start sites — the Servers-popover `_sm.start(key, gui=True)`
  (~`main_window.py:5533`) and `_on_start_server(model_source)` (~10788) — call
  `self._status_service.note_starting(<key>)` right after issuing the start.
- At the stop sites — the popover `_sm.stop(key)` (~5535) and `_on_stop_server`
  (~10898) — call `self._status_service.note_stopping(<key>)`.
  (`_on_start_server`/`_on_stop_server` resolve a server key from `model_source`;
  reuse that resolution for the note call.)

### 3. CreateView wiring

- **Inject:** `CreateView.__init__(..., status_service=None)`; store it. When
  present, CreateView uses it as the source of truth; when None, keep the
  existing `_health_fn`/`_model_health`/`_refresh_model_health_async` fallback
  unchanged (tests/standalone).
- **Subscribe:** if a service is present, `service.subscribe(cb)` where `cb`
  does `GLib.idle_add(self._on_status_snapshot, snapshot)`; `_on_status_snapshot`
  stores the snapshot and refreshes the scoped-dropdown dots + Model-door dots.
  Unsubscribe on unrealize/destroy. When a service is injected, do NOT start the
  old `_refresh_model_health_async` polling.
- **3-state dots:** a small helper `_status_glyph(status) -> str` maps
  `Status.READY→"●"`, `STARTING→"◐"`, `OFF/ERROR→"◌"` (ERROR may use a distinct
  marker later; ◌ is fine for SP-2). The scoped dropdown's per-model rows and the
  Model-door cards render this glyph from the snapshot (replacing the current
  boolean `_model_health` ● / ○). With no service, fall back to the boolean
  mapping (running→●, else ○).
- **Auto-select:** in `_populate_model_dropdown(medium)`, when a service is
  present and this is a medium-switch / fresh populate (not a same-medium
  refresh preserving a manual pick), choose the default selection as:
  `service.running_or_starting(<capability for medium.kind>)` if it maps to one
  of the medium's scoped model keys, else the existing medium default (index 0).
  The v0.28.1 preserve-selected-key logic still governs health-refresh
  repopulation (a running model becoming ready must not yank a manual pick).
  Capability map: `image→"image"`, `video→"video"`, `animate→"animate"`
  (artgen mediums have no model dropdown → no auto-select).
  Note the service returns a **server key** (e.g. `"wan2.2"`); map it to the
  dropdown's model key via the existing `_scoped_model_keys`/alias logic (a key
  not in the medium's scoped list → ignore, fall back to default).

## Data flow

poll thread → snapshot → (idle_add) → CreateView `_on_status_snapshot` → dropdown
+ Model-door dots refreshed; `_populate_model_dropdown` reads
`running_or_starting(cap)` for its default. App Start/Stop → `note_starting`/
`note_stopping` → next tick reflects STARTING/OFF.

## Error handling

- No service injected → existing behavior (fallback), no crash.
- `running_or_starting` returns a key not in the medium's scoped list → ignore,
  use medium default.
- Subscriber callback marshals via idle_add; a snapshot for an unknown key →
  dot defaults to ◌.

## Testing

- MainWindow: constructs + starts the service; `do_close_request` stops it;
  a start action calls `note_starting(key)`, a stop calls `note_stopping(key)`
  (assert against a fake service).
- CreateView with an injected fake service:
  - subscribes; a pushed snapshot updates the dropdown/door dot glyphs
    (READY→●, STARTING→◐, OFF→◌).
  - `_populate_model_dropdown` for a video medium with the service reporting
    `wan2.2` running → dropdown defaults to Wan2.2 (auto-select), not index 0.
  - service reporting a STARTING model (none ready) → that model auto-selected +
    shown ◐.
  - service reporting nothing for the medium → falls back to the medium default.
  - a manual pick is preserved across a health-refresh snapshot (v0.28.1 intact).
  - with `status_service=None` → old boolean fallback path unchanged (existing
    CreateView tests stay green).

## File summary

| File | Change |
|---|---|
| `app/main_window.py` | construct/start/stop `ModelStatusService`; `note_starting`/`note_stopping` at start/stop sites; inject into CreateView |
| `app/create_view.py` | `status_service` seam; subscribe + `_on_status_snapshot`; `_status_glyph` 3-state dots on dropdown + Model door; auto-select `running_or_starting` in `_populate_model_dropdown` (preserve manual pick) |
| `tests/…` | service lifecycle + start/stop hooks; CreateView dots + auto-select + fallback + manual-pick preservation |
