# SP-2 — Status wiring + Create auto-select — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Consume `ModelStatusService` in the surviving Create surface — 3-state loading dots (◌/◐/●) and auto-selecting the running model — and make `starting` accurate for app-initiated starts.

**Architecture:** MainWindow owns one `ModelStatusService` (start on open, stop on close), routes server start/stop through `note_starting`/`note_stopping`, and injects the service into CreateView. CreateView subscribes, renders 3-state dots on the scoped dropdown + Model door, and auto-selects `running_or_starting(capability)`. Legacy pollers/surfaces are untouched (SP-3 deletes them).

**Tech Stack:** Python 3, GTK4/PyGObject, pytest (xvfb).

## Global Constraints

- One service instance (MainWindow-owned), injected into CreateView via a new optional `status_service=None` seam; `None` keeps CreateView's existing `status_all`-polling fallback (existing tests/standalone unaffected).
- GTK threading: the service notifies on its poll thread; CreateView's callback marshals via `GLib.idle_add` before touching widgets.
- Preserve the v0.28.1 fix: health-refresh repopulation never clobbers a manual model pick; auto-select applies on medium-switch/first-populate, not every refresh.
- Palette tt-vscode-toolkit; dot glyphs (◌ ◐ ●) in Python strings, never in a `b"""` CSS literal.
- No legacy-poller/surface deletion (SP-3). No generation-internals change.
- System python; version bump + changelog on landing; local only. Deselect known flakes in full-suite runs: `tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`, `tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`.

---

### Task 1: Service lifecycle + start/stop hooks (MainWindow)

**Files:** Modify `app/main_window.py`; Test `tests/test_main_window_status_service.py` (new, source/behavioral).

**Interfaces — Produces:** `self._status_service: ModelStatusService` started at app open, stopped in `do_close_request`; `note_starting(key)` / `note_stopping(key)` called at the app's server start/stop sites; `self._create_view` constructed with `status_service=self._status_service`.

- [ ] **Step 1: failing tests** — assert (source-level, mirroring `test_main_window_create_view_mount.py`'s `_SRC` style, since MainWindow is hard to fully construct in tests):
```python
# _SRC = Path("app/main_window.py").read_text()
def test_constructs_and_starts_status_service():
    assert "from model_status import ModelStatusService" in _SRC
    assert "self._status_service = ModelStatusService(" in _SRC
    assert "self._status_service.start()" in _SRC
def test_stops_service_on_close():
    assert "self._status_service.stop()" in _SRC
def test_create_view_gets_status_service():
    assert "status_service=self._status_service" in _SRC
def test_start_stop_hooks_present():
    assert "self._status_service.note_starting(" in _SRC
    assert "self._status_service.note_stopping(" in _SRC
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement — `from model_status import ModelStatusService` (top). In `__init__`/`_build_ui` near `_start_health_worker`: `self._status_service = ModelStatusService(); self._status_service.start()`. In `do_close_request` near `self._health_stop.set()`: `self._status_service.stop()`. Construct `self._create_view = CreateView(..., status_service=self._status_service)`. Add `self._status_service.note_starting(key)` after the Servers-popover `_sm.start(key, gui=True)` (~5533) and in `_on_start_server` (resolve the server key from `model_source` the same way that method already does, then note it). Add `self._status_service.note_stopping(key)` after the popover `_sm.stop(key)` (~5535) and in `_on_stop_server`. Wrap each note call so a failure can't break start/stop.
- [ ] **Step 4:** run → PASS; full suite green.
- [ ] **Step 5:** commit `feat(status): MainWindow owns ModelStatusService + start/stop hooks`.

---

### Task 2: CreateView subscribes + 3-state dots (dropdown + Model door)

**Files:** Modify `app/create_view.py`; Test `tests/test_create_view.py` (extend).

**Interfaces — Consumes:** `ModelStatusService` (`snapshot()`, `subscribe()`, `Status`). **Produces:** `CreateView.__init__(..., status_service=None)`; `_status_glyph(status)->str`; `_on_status_snapshot(snapshot)`; dropdown + Model-door dots render from the service when present, else the existing boolean fallback.

- [ ] **Step 1: failing tests** (fake service exposing `snapshot()`/`subscribe()`; use real `model_status.Status`)
```python
def test_status_glyph_mapping(make_create_view):
    import model_status as msmod
    cv = make_create_view()
    assert cv._status_glyph(msmod.Status.READY) == "●"
    assert cv._status_glyph(msmod.Status.STARTING) == "◐"
    assert cv._status_glyph(msmod.Status.OFF) == "◌"
def test_subscribes_when_service_present():
    # construct CreateView with a fake service; assert subscribe was called
    ...
def test_snapshot_updates_dropdown_dot_glyphs():
    # push a snapshot {"flux": READY, "sdxl": STARTING}; the image dropdown rows show ●/◐
    ...
def test_no_service_uses_boolean_fallback(make_create_view):
    cv = make_create_view()  # status_service=None
    assert cv._status_service is None  # existing _model_health path intact
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement — add `status_service=None` param; store `self._status_service`. `_status_glyph(status)`: READY→"●", STARTING→"◐", else "◌". If a service is present: subscribe with `cb = lambda snap: GLib.idle_add(self._on_status_snapshot, snap)`; store the unsubscribe; do NOT start `_refresh_model_health_async`. `_on_status_snapshot(snap)`: store `self._status_snapshot = snap`; re-render the scoped-dropdown dots + Model-door card dots. Route the dropdown/Model-door dot rendering through a single helper that returns the glyph: service present → `_status_glyph(snap.get(key, Status.OFF))`; else the existing boolean (running→●, else ○). Unsubscribe on `unrealize`/destroy. Keep the None path byte-identical to today.
- [ ] **Step 4:** run → PASS (existing CreateView tests green).
- [ ] **Step 5:** commit `feat(create): CreateView reads ModelStatusService for 3-state model dots`.

---

### Task 3: Create auto-selects the running model

**Files:** Modify `app/create_view.py`; Test `tests/test_create_view.py` (extend).

**Interfaces — Consumes:** `service.running_or_starting(capability)`. **Produces:** `_populate_model_dropdown(medium)` defaults its selection to the running/starting model for the medium's capability, preserving a manual pick across refreshes.

- [ ] **Step 1: failing tests**
```python
_CAP = {"image":"image","video":"video","animate":"animate"}
def test_autoselect_running_model_on_medium_populate():
    # service.running_or_starting("video") -> "wan2.2"; populate video medium
    # -> dropdown default resolves to wan2.2-t2v (not index 0/mochi)
    ...
def test_autoselect_starting_when_none_ready():
    # running_or_starting returns a STARTING key -> that model selected
    ...
def test_autoselect_falls_back_to_default_when_nothing_running():
    # running_or_starting -> None -> medium default (index 0)
    ...
def test_manual_pick_preserved_across_refresh():
    # user selects mochi; a later health-refresh repopulation keeps mochi (v0.28.1)
    ...
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement — in `_populate_model_dropdown(medium)`, when `self._status_service` is present AND this is a fresh populate (medium-switch / first build, not a same-medium refresh that must preserve a manual pick): compute `cap = {"image":"image","video":"video","animate":"animate"}.get(medium.kind)`; `key = self._status_service.running_or_starting(cap)` if cap; if `key` maps (via the existing `_scoped_model_keys`/alias logic) to one of this medium's scoped model keys, make that the default selection; else the existing medium default. The v0.28.1 preserve-selected-key path still governs same-medium refresh repopulation. (Distinguish "fresh populate" vs "refresh" using the existing mechanism the v0.28.1 fix uses — e.g. whether a prior selection key for this medium exists.)
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5:** commit `feat(create): auto-select the running/starting model for the active medium`.

---

### Task 4: Version, changelog, CLAUDE.md

**Files:** `VERSION`, `debian/changelog`, `CLAUDE.md`.

- [ ] **Step 1:** `VERSION` → `0.33.0`.
- [ ] **Step 2:** prepend a `debian/changelog` 0.33.0 stanza: Create now reads the single source of truth — model rows/cards show a 3-state loading dot (off / starting / ready), and Create auto-selects whatever model is running (or starting) for the active medium, defaulting sensibly and never overriding a manual pick. App-initiated server starts/stops feed the service's starting state. (Legacy surfaces still on their own indicators until they're retired.)
- [ ] **Step 3:** extend CLAUDE.md's "Model status" section: MainWindow owns/starts/stops the service, hooks note_starting/stopping, injects it into CreateView; CreateView 3-state dots + auto-select; legacy pollers retired in SP-3.
- [ ] **Step 4:** full suite green (deselect the two known flakes).
- [ ] **Step 5:** commit `chore: release v0.33.0 -- Create loading dots + auto-select running model`.

---

## Notes for the executor
- CreateView with `status_service=None` must behave exactly as today (existing tests are the guard).
- Never touch widgets from the poll thread — `GLib.idle_add` only.
- Do not delete or rewire the legacy pollers/surfaces (SP-3).
