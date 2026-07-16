# ModelStatusService — one source of truth for server/model state

**Date:** 2026-07-13
**Branch:** `feat/pipeline-editor` (local; not merged)
**Status:** design approved (self-approved per user instruction)
**Program:** "one coherent shell" — this is **SP-1** (the foundation). SP-2 wires
surfaces to it (loading dots + Create auto-select); SP-3 retires the vestiges.

## Problem

Server/model "is it on?" is answered by **four independent pollers** today, two
using different truth mechanisms, so surfaces disagree:

- `MainWindow._health_loop` (10s) → footer server row + statusbar dot — only the
  *selected* medium's server.
- `MainWindow._refresh_servers_popover` → `server_manager.status_all()` — the
  "Servers ▾" dots.
- `CreateView._refresh_model_health_async` → `status_all()` → `_model_health` —
  scoped dropdown + Model door dots.
- `artgen_panel._check_health_bg` → `artgen.detect_artgen_endpoint()` (a **port
  sweep**) — the artgen panel dot.

There is also no first-class **loading/starting** state (only the inspire dot has
an ad-hoc `starting` style), and Create defaults its model dropdown to index 0
rather than to whatever is actually running.

## Goal (SP-1 only)

Build ONE GUI-free service that is the single source of truth for each managed
server's state, so every surface can read the same answer. This sub-project
delivers the **service + its state model + one poll loop + a subscribe API**. It
changes **no UI** and **no generation** — SP-2 rewires the surfaces to it.

## Non-goals (SP-1)

- No UI changes (SP-2). No auto-select (SP-2). No vestige deletion (SP-3).
- No change to `server_manager` start/stop *scripts* or generation.

## Global constraints

- **GUI-free:** `app/model_status.py` must not import `gi`/GTK (like
  `server_manager`/`worker`/`api_client`). GTK consumers marshal callbacks via
  `GLib.idle_add` themselves (SP-2).
- **Thread-safe:** one daemon poll thread; snapshot reads are atomic (a lock or
  immutable-snapshot swap).
- **System python** `/usr/bin/python3`; tests `xvfb-run … pytest` (this module's
  own tests need no display).
- **Version discipline:** bump `VERSION` + changelog on landing.
- **Local only:** no push/merge.
- Known flake to deselect in full-suite runs:
  `tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`.

## Architecture — `app/model_status.py`

### State model

```python
class Status(str, Enum):
    OFF = "off"
    STARTING = "starting"
    READY = "ready"
    ERROR = "error"
```

Per `server_manager.SERVERS` key, the service tracks a `Status`. A snapshot is
`dict[str, Status]`.

### The service

```python
class ModelStatusService:
    def __init__(self, *, health_fn=None, detect_fn=None, poll_interval=5.0,
                 start_timeout=180.0, clock=None): ...
    # health_fn()  -> dict[str,bool]   default: server_manager.status_all
    # detect_fn()  -> tuple|None       default: artgen.detect_artgen_endpoint
    # clock()      -> float            default: time.monotonic (injectable for tests)

    def start(self) -> None            # launch the daemon poll thread (idempotent)
    def stop(self) -> None             # stop the thread (for app shutdown / tests)
    def snapshot(self) -> dict[str, Status]
    def status(self, key: str) -> Status
    def note_starting(self, key: str) -> None   # call when the app starts a server
    def note_stopping(self, key: str) -> None   # call when the app stops a server
    def subscribe(self, cb) -> Callable[[], None]   # cb(snapshot); returns unsubscribe
    # capability helpers (used by SP-2's Create auto-select):
    def ready_keys(self, capability: str) -> list[str]        # most-recently-ready first
    def starting_keys(self, capability: str) -> list[str]
    def running_or_starting(self, capability: str) -> str | None  # ready wins; else starting
```

### One poll loop, merged truth

Each tick (`poll_interval`, default 5s):

1. `raw = health_fn()` → `{key: bool}` over managed `SERVERS` (fixed health
   ports; artgen rows share port 8002 per `status_all`'s existing behavior).
2. **Artgen/chat merge:** call `detect_fn()`. If it returns a live endpoint, mark
   every server whose `capabilities` include `"artgen"` (and `"prompt"`) as
   healthy too — this folds in chat models the port-sweep finds that a fixed-port
   check might miss. (Preserves the CLAUDE.md "detect_artgen_endpoint is the
   single source for is-a-chat-model-on" invariant.)
3. Resolve each key's `Status` from the merged health + the starting registry:
   - health True → `READY` (and clear any starting entry).
   - health False + key in starting registry:
     - within `start_timeout` → `STARTING`.
     - past `start_timeout` → `ERROR` (leave the entry so the surface can show
       it; cleared on next READY or an explicit `note_stopping`).
   - health False + NOT in registry:
     - **inferred starting** (user's choice): if a cheap port-reachable check
       says the port is open but health failed → `STARTING`; else `OFF`. Port
       reachability is a fast `socket.connect_ex` on the server's port with a
       short timeout; failures degrade to `OFF` (never raise).
4. If the snapshot changed from last tick, notify subscribers with the new
   snapshot.

`note_starting(key)` records `clock()`; `note_stopping(key)` removes the entry
and forces `OFF` on the next tick. `server_manager.start()/stop()` call these
(SP-2 wires the app's Start/Stop buttons through them; SP-1 just provides them).

### Threading & lifecycle

- One `threading.Thread(daemon=True)` runs the loop; a `threading.Event` stops
  it. `start()` is idempotent (no second thread). `stop()` joins with a short
  timeout.
- Snapshot is stored behind a `threading.Lock`; `snapshot()` returns a copy.
- Subscriber callbacks are invoked from the poll thread — **consumers are
  responsible for `GLib.idle_add`** (documented; SP-2 does it). A raising
  subscriber is caught and logged, never kills the loop.

## Data flow

`health_fn` + `detect_fn` + starting registry → (poll loop) → `dict[str,Status]`
snapshot → subscribers. Nothing reads the service in SP-1; SP-2's surfaces will.

## Error handling

- `health_fn`/`detect_fn`/port-probe exceptions are caught per-tick → treated as
  "unhealthy/unknown", never crash the loop.
- Unknown key → `status()` returns `OFF`.
- No servers / empty SERVERS → empty snapshot, no crash.

## Testing (all pure, injected fakes — no network, no threads-in-tests where possible)

Use an injected `clock` and call a private `_tick()` directly (don't sleep):

- OFF when health False and port closed and not starting.
- `note_starting` → STARTING; then health True → READY (registry cleared).
- `note_starting` + clock advanced past `start_timeout`, health still False →
  ERROR.
- Inferred STARTING: not in registry, health False, port **open** → STARTING;
  port closed → OFF (inject a fake port-probe).
- Artgen merge: `detect_fn` returns an endpoint → all `artgen`/`prompt`-capable
  keys READY even if `health_fn` reported them False.
- `subscribe` fires only on change; returns a working unsubscribe; a raising
  subscriber doesn't stop other subscribers or the loop.
- `ready_keys("video")` / `running_or_starting("image")` return the right keys
  (ready before starting; capability-filtered via `SERVERS`).
- `start()` idempotent (one thread); `stop()` ends it.

## File summary

| File | Change |
|---|---|
| `app/model_status.py` | NEW — `Status` enum + `ModelStatusService` (poll loop, starting registry, artgen merge, subscribe, capability helpers) |
| `tests/test_model_status.py` | NEW — the tick/state/subscribe/capability tests above |
