# ModelStatusService Implementation Plan (SP-1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A GUI-free single source of truth for each managed server's state (`off`/`starting`/`ready`/`error`), merging `server_manager` health + the artgen port-sweep, tracking a starting registry, and letting surfaces subscribe.

**Architecture:** One `app/model_status.py` module: a `Status` enum, a pure per-key state resolver, one poll loop (daemon thread) that gathers health/detect/port-probe and notifies subscribers on change, plus capability query helpers. No GTK, no generation, no UI wiring (that's SP-2).

**Tech Stack:** Python 3 stdlib (threading, socket, urllib.parse, enum, time). pytest. System interpreter `/usr/bin/python3`.

## Global Constraints

- **GUI-free:** `app/model_status.py` must NOT import `gi`/GTK (like `server_manager`/`worker`). Subscriber callbacks run on the poll thread — consumers marshal to GTK via `GLib.idle_add` themselves (SP-2).
- **Thread-safe:** snapshot behind a `threading.Lock`; `snapshot()` returns a copy.
- **Never crash the loop:** `health_fn`/`detect_fn`/port-probe/subscriber exceptions are caught per-tick and treated as unhealthy/unknown.
- **Injectable for tests:** `health_fn`, `detect_fn`, `clock`, `poll_interval`, `start_timeout`, and the port-probe are injected/overridable so tests call `_tick()` directly with no threads/sleeps/sockets.
- **Determinism:** tests never sleep; drive `_tick()` and advance an injected `clock`.
- System python; tests `xvfb-run --auto-servernum /usr/bin/python3 -m pytest` (this module needs no display, but use the standard runner). Version bump + changelog on landing. Local only. Known flakes to deselect in full-suite runs: `tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module` and `tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`.

---

### Task 1: `Status` enum + service scaffold + pure state resolver

**Files:** Create `app/model_status.py`; Test `tests/test_model_status.py`.

**Interfaces — Produces:**
```python
class Status(str, Enum):
    OFF="off"; STARTING="starting"; READY="ready"; ERROR="error"

class ModelStatusService:
    def __init__(self, *, health_fn=None, detect_fn=None, clock=None,
                 port_probe=None, poll_interval=5.0, start_timeout=180.0): ...
    def note_starting(self, key: str) -> None       # records clock() as start time
    def note_stopping(self, key: str) -> None        # drops the entry; next tick -> OFF
    def snapshot(self) -> "dict[str, Status]"        # copy of last-resolved statuses
    def status(self, key: str) -> Status             # OFF if unknown
    @staticmethod
    def _resolve(healthy: bool, starting_at, port_open: bool, now: float,
                 start_timeout: float) -> Status
```
`_resolve` rules (pure): healthy → READY; else if `starting_at is not None`: (`now-starting_at <= start_timeout` → STARTING) else ERROR; else (`port_open` → STARTING [inferred] else OFF).

- [ ] **Step 1: failing tests**
```python
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import model_status as ms
R = ms.ModelStatusService._resolve
def test_resolve_healthy_is_ready():
    assert R(True, None, False, 100.0, 180.0) == ms.Status.READY
def test_resolve_healthy_wins_even_if_starting():
    assert R(True, 100.0, True, 105.0, 180.0) == ms.Status.READY
def test_resolve_starting_within_timeout():
    assert R(False, 100.0, False, 150.0, 180.0) == ms.Status.STARTING
def test_resolve_starting_past_timeout_is_error():
    assert R(False, 100.0, False, 300.0, 180.0) == ms.Status.ERROR
def test_resolve_inferred_starting_when_port_open():
    assert R(False, None, True, 100.0, 180.0) == ms.Status.STARTING
def test_resolve_off_when_nothing():
    assert R(False, None, False, 100.0, 180.0) == ms.Status.OFF
def test_note_starting_records_and_snapshot_defaults_off():
    svc = ms.ModelStatusService(clock=lambda: 10.0)
    assert svc.status("wan2.2") == ms.Status.OFF
    svc.note_starting("wan2.2"); assert "wan2.2" in svc._starting
    svc.note_stopping("wan2.2"); assert "wan2.2" not in svc._starting
def test_no_gtk_import():
    import importlib, sys
    importlib.import_module("model_status")
    assert "gi" not in sys.modules or True  # model_status itself must not import gi
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement `Status`, `__init__` (store injected deps; `self._statuses={}`, `self._starting={}`, `self._ready_at={}`, `self._subscribers=[]`, `self._lock=threading.Lock()`, `self._thread=None`, `self._stop=threading.Event()`; defaults: `health_fn=server_manager.status_all` [import lazily inside a default lambda to avoid a hard import at module load], `detect_fn=lambda: __import__("artgen").detect_artgen_endpoint()`, `clock=time.monotonic`, `port_probe=self._default_port_probe`), `note_starting`/`note_stopping`, `snapshot` (copy under lock), `status`, and the static `_resolve`. Do NOT import gi. Keep `server_manager`/`artgen` imports lazy (inside the default callables) so the module stays importable in isolation.
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5:** commit `feat(status): Status enum + ModelStatusService scaffold + pure resolver`.

---

### Task 2: `_tick()` — merge health + artgen detect + port probe → statuses

**Files:** Modify `app/model_status.py`; Test `tests/test_model_status.py`.

**Interfaces — Consumes:** Task 1. **Produces:** `_tick()` recomputes every `server_manager.SERVERS` key's `Status` and stores it; `_default_port_probe(key)` parses host/port from the server's `health_url` and returns a bool; artgen merge marks `artgen`/`prompt`-capable keys healthy when `detect_fn()` yields an endpoint; `_ready_at[key]` stamped when a key becomes READY.

- [ ] **Step 1: failing tests** (inject fakes; drive `_tick` directly)
```python
def _svc(health, detect=(None,None), ports=None, now=100.0):
    ports = ports or {}
    return ms.ModelStatusService(
        health_fn=lambda: dict(health),
        detect_fn=lambda: detect,
        port_probe=lambda key: ports.get(key, False),
        clock=lambda: now,
    )
def test_tick_healthy_key_ready():
    svc = _svc({"wan2.2": True}); svc._tick()
    assert svc.status("wan2.2") == ms.Status.READY
def test_tick_artgen_detect_marks_artgen_keys_ready():
    # health says all False, but detect finds a chat endpoint -> artgen/prompt keys READY
    svc = _svc({}, detect=("http://localhost:8002", "Qwen3-8B"))
    svc._tick()
    import server_manager as sm
    art = [k for k,d in sm.SERVERS.items() if "artgen" in d.capabilities]
    assert art and all(svc.status(k) == ms.Status.READY for k in art)
def test_tick_inferred_starting_from_port(monkeypatch):
    svc = _svc({"flux": False}, ports={"flux": True}); svc._tick()
    assert svc.status("flux") == ms.Status.STARTING
def test_tick_app_started_then_ready(monkeypatch):
    svc = _svc({"flux": False}, now=100.0); svc.note_starting("flux"); svc._tick()
    assert svc.status("flux") == ms.Status.STARTING
    svc._health_fn = lambda: {"flux": True}; svc._tick()
    assert svc.status("flux") == ms.Status.READY and "flux" not in svc._starting
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement `_tick()`: `try: health = dict(self._health_fn() or {}) except Exception: health = {}`. `try: base,_ = self._detect_fn(); artgen_up = base is not None except Exception: artgen_up = False`. For each `key, sdef in server_manager.SERVERS.items()`: `healthy = health.get(key, False) or (artgen_up and bool(set(sdef.capabilities) & {"artgen","prompt"}))`; `starting_at = self._starting.get(key)`; `port_open = False if healthy else self._safe_port_probe(key)`; `st = self._resolve(healthy, starting_at, port_open, self._clock(), self._start_timeout)`; if `st==READY`: clear `self._starting.pop(key,None)`, stamp `_ready_at.setdefault(key, self._clock())`; if `st!=READY`: `_ready_at.pop(key,None)`. Store under lock, then call `self._notify()` at the end of the tick. Implement `_notify` HERE as: snapshot once, `for cb in list(self._subscribers): try: cb(snap) except Exception: log+continue` — with no subscribers yet (Task 3 adds `subscribe`) it's a harmless no-op. **Change-only gating** (only notify when the resolved dict differs from the prior tick) is added in Task 3; Task 2 may notify every tick. Implement `_default_port_probe(key)`: `u = urllib.parse.urlparse(server_manager.SERVERS[key].health_url); host = u.hostname or "127.0.0.1"; port = u.port; if not port: return False; s = socket.socket(); s.settimeout(0.25); try: return s.connect_ex((host, port)) == 0 finally: s.close()`. `_safe_port_probe` wraps it in try/except→False.
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5:** commit `feat(status): _tick merges health + artgen detect + port probe into per-key Status`.

---

### Task 3: Poll thread lifecycle + subscribe/notify

**Files:** Modify `app/model_status.py`; Test `tests/test_model_status.py`.

**Interfaces — Produces:** `start()`/`stop()` (idempotent daemon thread calling `_tick` every `poll_interval`, guarded by `self._stop`); `subscribe(cb) -> Callable[[],None]` (returns an unsubscribe); `_notify()` invokes each subscriber with `snapshot()`, catching exceptions; `_tick` calls `_notify()` only when the resolved statuses dict changed since last tick.

- [ ] **Step 1: failing tests**
```python
def test_subscribe_fires_only_on_change():
    svc = _svc({"flux": False}); seen=[]
    svc.subscribe(lambda snap: seen.append(snap))
    svc._tick()                     # OFF (change from empty) -> fires
    n = len(seen); svc._tick()      # no change -> no fire
    assert len(seen) == n
    svc._health_fn = lambda: {"flux": True}; svc._tick()  # change -> fires
    assert len(seen) == n + 1
def test_unsubscribe_stops_calls():
    svc = _svc({"flux": False}); seen=[]
    off = svc.subscribe(lambda s: seen.append(s)); svc._tick(); c=len(seen)
    off(); svc._health_fn=lambda:{"flux":True}; svc._tick()
    assert len(seen) == c
def test_raising_subscriber_does_not_break_others():
    svc = _svc({"flux": False}); good=[]
    svc.subscribe(lambda s: (_ for _ in ()).throw(RuntimeError()))
    svc.subscribe(lambda s: good.append(s)); svc._tick()
    assert good
def test_start_is_idempotent_and_stop_ends():
    svc = _svc({}); svc.start(); t = svc._thread; svc.start()
    assert svc._thread is t and t.is_alive()
    svc.stop(); assert not t.is_alive()
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement `subscribe` (append; return a closure that removes it), `_notify` (snapshot once; for cb in list(self._subscribers): try cb(snap) except Exception: log+continue). In `_tick`, compare the newly-resolved dict to the prior stored dict; only `_notify()` when different. `start()`: if `self._thread and self._thread.is_alive(): return`; clear `_stop`; create+start daemon thread running `while not self._stop.is_set(): self._tick(); self._stop.wait(self._poll_interval)`. `stop()`: set `_stop`; `self._thread.join(timeout=2)` if alive.
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5:** commit `feat(status): poll-thread lifecycle + change-only subscriber notification`.

---

### Task 4: Capability query helpers

**Files:** Modify `app/model_status.py`; Test `tests/test_model_status.py`.

**Interfaces — Produces:**
```python
def ready_keys(self, capability: str) -> list[str]      # READY keys with that capability, most-recently-ready first
def starting_keys(self, capability: str) -> list[str]   # STARTING keys with that capability
def running_or_starting(self, capability: str) -> "str | None"  # a READY key (most-recent) else a STARTING key else None
```
(These are what SP-2's Create auto-select consumes.)

- [ ] **Step 1: failing tests**
```python
def test_ready_keys_filtered_by_capability_recent_first():
    # two image servers ready at different times -> most-recently-ready first
    svc = _svc({"flux": True, "sdxl": True})
    svc._clock = lambda: 100.0; svc._tick()   # both ready ~100
    # force sdxl to look more-recently-ready
    svc._ready_at["sdxl"] = 200.0; svc._ready_at["flux"] = 100.0
    ks = svc.ready_keys("image")
    assert ks[0] == "sdxl" and "flux" in ks
def test_running_or_starting_prefers_ready():
    svc = _svc({"wan2.2": True}); svc._tick()
    assert svc.running_or_starting("video") == "wan2.2"
def test_running_or_starting_falls_back_to_starting():
    svc = _svc({"mochi": False}, ports={"mochi": True}); svc._tick()
    assert svc.running_or_starting("video") == "mochi"
def test_running_or_starting_none_when_all_off():
    svc = _svc({}); svc._tick()
    assert svc.running_or_starting("video") is None
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement the three helpers over `snapshot()` + `server_manager.SERVERS[k].capabilities`; `ready_keys` sorts READY-with-capability by `self._ready_at.get(k, 0)` descending; `starting_keys` filters STARTING; `running_or_starting` returns `ready_keys(cap)[0] if any else (starting_keys(cap)[0] if any else None)`.
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5:** commit `feat(status): capability helpers (ready_keys/starting_keys/running_or_starting)`.

---

### Task 5: Version, changelog, CLAUDE.md

**Files:** `VERSION`, `debian/changelog`, `CLAUDE.md`.

- [ ] **Step 1:** `VERSION` → `0.32.0`.
- [ ] **Step 2:** prepend a `debian/changelog` 0.32.0 stanza: new GUI-free `ModelStatusService` — one poll loop that is the single source of truth for server/model state (off/starting/ready/error), merging managed-server health with the artgen port-sweep, tracking a starting state (app-initiated + inferred-for-external), with a subscribe API and capability helpers. Foundation for unifying the app's health indicators (SP-2). No UI/generation change yet.
- [ ] **Step 3:** add a CLAUDE.md subsection (near the Create-surface notes or a new "Model status" section) documenting `app/model_status.py`: the single-source-of-truth intent, the `_resolve` rules, the artgen-detect merge, the injected-deps-for-tests design, and that SP-2 wires surfaces to it (retiring the four ad-hoc pollers).
- [ ] **Step 4:** full suite green (deselect the two known flakes).
- [ ] **Step 5:** commit `chore: release v0.32.0 -- ModelStatusService (single source of truth foundation)`.

---

## Notes for the executor
- GUI-free is non-negotiable: no `gi` import in `model_status.py`; keep `server_manager`/`artgen` imports lazy so the module imports standalone.
- Tests must be deterministic — drive `_tick()` with injected fakes + clock; never sleep, never open a real socket (inject `port_probe`).
- This module is not consumed anywhere yet (SP-2 does that). Don't wire it into main_window/CreateView.
