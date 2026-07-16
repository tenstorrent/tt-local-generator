# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""
model_status.py — ModelStatusService: unifies "is a model on" across every
managed backend into one polled, subscribable status map.

This module is GUI-FREE: it must be importable on its own, with nothing else
loaded (no `gi`, no GTK). It is consumed by both the CLI (tt-ctl) and the GUI,
so it must not assume either is present.

Why this exists
----------------
Today, "is a model on" is answered three different ways depending on which
model: `server_manager.status_all()` (health_url ping) for the managed
video/image servers, `artgen.detect_artgen_endpoint()` (port sweep + /v1/models
probe) for chat-LLM backends, and ad-hoc port checks for anything started
outside the app. `ModelStatusService` is the single place that reconciles all
three into one `Status` per key, polled on a background thread (added in a
later task) and exposed via `snapshot()`/`status()` for any UI to render.

Task scope (SP-1 Task 1)
-------------------------
This first task lays the *pure* foundation only:
  - the `Status` enum,
  - `ModelStatusService.__init__` with injected dependencies (so tests never
    touch real subprocesses, sockets, or the network),
  - the bookkeeping methods `note_starting` / `note_stopping` / `snapshot` /
    `status`,
  - the static, side-effect-free `_resolve()` state machine.

Task scope (SP-1 Task 2)
-------------------------
This task adds the single-tick reconciliation logic that actually drives
`_resolve()`:
  - `_tick()` — merges `health_fn()` (per-key health map), `detect_fn()`
    (artgen/chat-LLM endpoint detection — marks every `artgen`/`prompt`
    capability key healthy when a chat endpoint is found, since those
    backends share one port and one detector), and the port probe, then
    stores one `Status` per `server_manager.SERVERS` key.
  - `_default_port_probe()` — a real (if best-effort) TCP `connect_ex` probe
    against the host/port parsed out of each server's `health_url`.
  - `_notify()` — fans a fresh `snapshot()` out to any subscribers. No
    subscribers exist yet (Task 3 adds `subscribe()`), so today this is a
    harmless no-op called once per tick; change-only gating (only notify when
    the resolved map actually differs from the prior tick) is also Task 3's
    job, not this one's.

The background poll thread, `subscribe()`, and capability-aware helpers are
still not implemented — they land in Tasks 3-4.

Lazy imports (CRITICAL)
-----------------------
`server_manager` and `artgen` are real, heavier modules (subprocess, urllib,
in `server_manager`'s case; the artgen package pulls in more). Importing them
at module load time would mean `import model_status` transitively imports
whatever they import. To keep this module standalone-importable — and to keep
it honest about being GUI-free even though its *defaults* reference
server-management code — those imports are deferred until the default
callables actually run (i.e. only if the caller doesn't inject their own
`health_fn`/`detect_fn`).
"""

import logging
import socket
import threading
import time
import urllib.parse
from enum import Enum
from typing import Callable, Optional

log = logging.getLogger(__name__)


class Status(str, Enum):
    """Lifecycle state of a single managed model/backend.

    Subclasses `str` so values compare equal to their plain string form
    (`Status.READY == "ready"`) — convenient for logging, JSON, and GTK
    CSS-class lookups without an extra `.value` everywhere.
    """

    OFF = "off"
    STARTING = "starting"
    READY = "ready"
    ERROR = "error"


class ModelStatusService:
    """Polls and reconciles model/server health into a single status map.

    All external effects (health checks, endpoint detection, wall-clock time,
    port probing) are injected as callables so the resolution logic
    (`_resolve`) and the bookkeeping methods can be tested without touching
    real servers, sockets, or subprocesses.

    Parameters
    ----------
    health_fn : Callable[[], dict[str, bool]], optional
        Returns e.g. `server_manager.status_all()` — a map of managed-service
        key -> healthy bool. Defaults to a lazy call into `server_manager`.
    detect_fn : Callable[[], tuple[str | None, str | None]], optional
        Returns `(base_url, model_id)` for the best available chat-LLM
        backend, mirroring `artgen.detect_artgen_endpoint()`. Defaults to a
        lazy call into the `artgen` package.
    clock : Callable[[], float], optional
        Wall-clock source for timing "how long has this been starting".
        Defaults to `time.monotonic` (never goes backwards, unaffected by
        system clock changes — the right choice for elapsed-time math).
    port_probe : Callable[[str], bool], optional
        Given a key, returns whether *some* process appears to be listening
        for it (used to infer STARTING before a health check ever succeeds).
        Defaults to `self._default_port_probe` (not yet implemented in this
        task — a later task wires it to real port numbers).
    poll_interval : float
        Seconds between background poll ticks (consumed by the poll thread,
        added in a later task).
    start_timeout : float
        Seconds after `note_starting()` before an unhealthy, still-starting
        key is considered ERROR instead of STARTING.
    """

    def __init__(
        self,
        *,
        health_fn: Optional[Callable[[], "dict[str, bool]"]] = None,
        detect_fn: Optional[Callable[[], "tuple[Optional[str], Optional[str]]"]] = None,
        clock: Optional[Callable[[], float]] = None,
        port_probe: Optional[Callable[[str], bool]] = None,
        poll_interval: float = 5.0,
        start_timeout: float = 180.0,
    ) -> None:
        # Lazy-default health_fn: import server_manager only when this
        # default is actually invoked, not at __init__ time — so a caller
        # who injects their own health_fn never triggers the import at all,
        # and even the default only imports on first call, not on
        # construction.
        self._health_fn = health_fn or (
            lambda: __import__("server_manager").status_all()
        )
        # Lazy-default detect_fn: same reasoning for the artgen package.
        self._detect_fn = detect_fn or (
            lambda: __import__("artgen").detect_artgen_endpoint()
        )
        self._clock = clock or time.monotonic
        self._port_probe = port_probe or self._default_port_probe
        self._poll_interval = poll_interval
        self._start_timeout = start_timeout

        # -- internal state --------------------------------------------
        # Last-resolved status per key, refreshed each poll tick (added in
        # a later task). Read via snapshot()/status().
        self._statuses: "dict[str, Status]" = {}
        # key -> clock() timestamp when note_starting() was called; absence
        # means "not currently known to be starting" (per _resolve's
        # `starting_at is None` branch).
        self._starting: "dict[str, float]" = {}
        # key -> clock() timestamp when the key was first observed READY;
        # reserved for later tasks (e.g. "just became ready" transitions).
        self._ready_at: "dict[str, float]" = {}
        # Callbacks registered via subscribe() (Task 2+); not populated or
        # invoked by this task.
        self._subscribers: list = []
        # Guards _statuses/_starting/_ready_at against concurrent access
        # from the poll thread (added later) and the calling thread.
        self._lock = threading.Lock()
        # Background poll thread handle; created by a later task's start()
        # method. None until then.
        self._thread: Optional[threading.Thread] = None
        # Signaled to ask the (not-yet-implemented) poll thread to exit.
        self._stop = threading.Event()

    # ------------------------------------------------------------------
    # Bookkeeping
    # ------------------------------------------------------------------
    def note_starting(self, key: str) -> None:
        """Record that `key` was just told to start.

        Stores `clock()` as the start time so `_resolve` can tell "still
        within start_timeout" (STARTING) from "took too long" (ERROR) on the
        next poll tick, before any health check has had a chance to succeed.
        """
        with self._lock:
            self._starting[key] = self._clock()

    def note_stopping(self, key: str) -> None:
        """Record that `key` was just told to stop.

        Drops any starting-bookkeeping for `key` so it doesn't linger as
        STARTING/ERROR after a deliberate stop; the next poll tick will
        resolve it to OFF (no health, no starting_at, and — once the caller
        stops the underlying process — no open port either).
        """
        with self._lock:
            self._starting.pop(key, None)

    def snapshot(self) -> "dict[str, Status]":
        """Return a copy of the last-resolved status map.

        A copy (not the live dict) so callers can iterate/hold it without
        racing the poll thread's next update.
        """
        with self._lock:
            return dict(self._statuses)

    def status(self, key: str) -> Status:
        """Return the last-resolved status for `key`, or OFF if unknown.

        Unknown keys default to OFF rather than raising — a key that has
        never been polled yet is indistinguishable from one that's off.
        """
        with self._lock:
            return self._statuses.get(key, Status.OFF)

    # ------------------------------------------------------------------
    # Pure resolver
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve(
        healthy: bool,
        starting_at: Optional[float],
        port_open: bool,
        now: float,
        start_timeout: float,
    ) -> Status:
        """Pure state machine — no I/O, no side effects, fully deterministic.

        Rules (in priority order):
          1. `healthy` always wins: READY, regardless of starting_at/port_open.
             A model that answers its health check is ready even if we still
             have a stale `starting_at` bookkeeping entry for it.
          2. Else, if we know when it started (`starting_at is not None`):
             within `start_timeout` -> STARTING; past it -> ERROR (it should
             have come up by now and hasn't).
          3. Else (no start bookkeeping at all): fall back to the port probe.
             An open port with no health response yet and no recorded start
             time means something is listening but not answering health
             checks yet — infer STARTING. Nothing at all -> OFF.
        """
        if healthy:
            return Status.READY
        if starting_at is not None:
            if now - starting_at <= start_timeout:
                return Status.STARTING
            return Status.ERROR
        if port_open:
            return Status.STARTING
        return Status.OFF

    # ------------------------------------------------------------------
    # Default port probe
    # ------------------------------------------------------------------
    def _default_port_probe(self, key: str) -> bool:
        """Best-effort default port_probe: real TCP `connect_ex` against the
        host/port parsed out of `server_manager.SERVERS[key].health_url`.

        This is deliberately *not* an HTTP request — a bare TCP connect is
        enough to infer "something is listening" (the STARTING-by-port branch
        of `_resolve`) without waiting on a slow/hanging HTTP response from a
        server that's still loading weights. A 250ms timeout keeps a single
        tick fast even if the target host silently drops SYNs (firewalled,
        wrong host, etc.) rather than actively refusing the connection.

        Returns False if `health_url` has no explicit port (nothing to probe)
        or if any exception occurs while resolving/connecting — a probe
        failure must never propagate and break the whole tick.
        """
        import server_manager

        u = urllib.parse.urlparse(server_manager.SERVERS[key].health_url)
        host = u.hostname or "127.0.0.1"
        port = u.port
        if not port:
            return False
        s = socket.socket()
        s.settimeout(0.25)
        try:
            return s.connect_ex((host, port)) == 0
        finally:
            s.close()

    def _safe_port_probe(self, key: str) -> bool:
        """Wraps the (possibly injected) `port_probe` callable so a bad probe
        — real or fake — can never raise out of `_tick()` and abort the whole
        reconciliation pass for every other key.
        """
        try:
            return self._port_probe(key)
        except Exception:
            log.debug("port_probe failed for %r", key, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Reconciliation tick
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        """Recompute every `server_manager.SERVERS` key's `Status` and store
        the result. This is the method the poll thread (`_run`) calls on a
        timer; it is also safe to call directly (as the tests do) for a
        synchronous, deterministic single reconciliation pass.

        `server_manager` is imported lazily here (not at module load time) to
        keep this module standalone-importable per the module docstring.

        Locking discipline (Task 3): all the I/O-shaped work above this
        method's final block — `health_fn()`, `detect_fn()`, and every
        `_safe_port_probe()` call inside the loop — happens with *no* lock
        held. Those calls can be slow (HTTP requests, socket connects) or, in
        a real deployment, block on the network; holding `self._lock` across
        them would stall `note_starting()`/`note_stopping()`/`snapshot()`
        calls from any other thread for the duration. Only the final
        read-compare-mutate-swap of `_starting`/`_ready_at`/`_statuses` needs
        the lock, since that's the only part touching shared state.
        """
        import server_manager

        try:
            health = dict(self._health_fn() or {})
        except Exception:
            # A broken health_fn (network hiccup, bad server, etc.) degrades
            # to "nothing is healthy" rather than crashing the tick — every
            # key falls through to the starting-bookkeeping/port-probe
            # branches of _resolve() instead.
            log.debug("health_fn failed", exc_info=True)
            health = {}

        try:
            base, _model_id = self._detect_fn()
            artgen_up = base is not None
        except Exception:
            # Same reasoning: a broken artgen detector just means "no chat
            # endpoint found this tick", not a crash.
            log.debug("detect_fn failed", exc_info=True)
            artgen_up = False

        now = self._clock()

        # -- I/O phase: no lock held -----------------------------------
        # `healthy`/`port_open` per key depend only on `health` (already
        # fetched above) and `sdef.capabilities` (static) — none of that is
        # shared mutable state, so this loop, including every
        # `_safe_port_probe()` socket connect, runs lock-free. Probing is
        # skipped once a key is already known healthy (matches the prior
        # behavior: `False if healthy else self._safe_port_probe(key)`).
        per_key: "dict[str, tuple[bool, bool]]" = {}
        for key, sdef in server_manager.SERVERS.items():
            # artgen/prompt backends share one port (8002) and are detected
            # via detect_fn's /v1/models sweep rather than server_manager's
            # per-key health_url ping (see artgen.detect_artgen_endpoint() and
            # the module docstring's "Why this exists" section) — so a found
            # chat endpoint marks every artgen/prompt-capability key healthy,
            # regardless of what health_fn itself reported for that key.
            healthy = health.get(key, False) or (
                artgen_up and bool(set(sdef.capabilities) & {"artgen", "prompt"})
            )
            port_open = False if healthy else self._safe_port_probe(key)
            per_key[key] = (healthy, port_open)

        # -- state phase: lock held, no I/O -----------------------------
        # Only `self._starting`/`self._ready_at` (read+mutate) and
        # `self._statuses` (compare+swap) are shared state; everything here
        # is pure dict bookkeeping plus the side-effect-free `_resolve()`
        # call, so it's safe (and fast) to do under the lock.
        changed = False
        with self._lock:
            new_statuses: "dict[str, Status]" = {}
            for key, (healthy, port_open) in per_key.items():
                starting_at = self._starting.get(key)
                st = self._resolve(
                    healthy, starting_at, port_open, now, self._start_timeout
                )

                if st == Status.READY:
                    self._starting.pop(key, None)
                    self._ready_at.setdefault(key, now)
                else:
                    self._ready_at.pop(key, None)

                new_statuses[key] = st

            if new_statuses != self._statuses:
                changed = True
            self._statuses = new_statuses

        # `_notify()` must run after the lock is released: it calls
        # `snapshot()`, which re-acquires `self._lock` — holding it here
        # would deadlock (or, for an RLock, just be needless nesting).
        if changed:
            self._notify()

    # ------------------------------------------------------------------
    # Subscriber fan-out
    # ------------------------------------------------------------------
    def subscribe(self, cb: "Callable[[dict], None]") -> "Callable[[], None]":
        """Register `cb` to be called with `snapshot()` whenever the resolved
        status map changes. Returns an unsubscribe closure.

        The returned closure is idempotent: calling it more than once (e.g.
        once from a widget's cleanup handler and again from a defensive
        `finally`) is safe — `list.remove` only runs if `cb` is still
        present.
        """
        self._subscribers.append(cb)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(cb)
            except ValueError:
                pass  # already unsubscribed -- idempotent no-op

        return _unsubscribe

    def _notify(self) -> None:
        """Push a fresh snapshot to every registered subscriber.

        Taking the snapshot once (rather than per-callback) means every
        subscriber sees the same consistent view even if `_tick()` runs again
        concurrently on the poll thread. Each callback is isolated in its own
        try/except so one raising subscriber can't prevent the others from
        being notified.
        """
        snap = self.snapshot()
        for cb in list(self._subscribers):
            try:
                cb(snap)
            except Exception:
                log.exception("model_status subscriber callback raised")

    # ------------------------------------------------------------------
    # Poll-thread lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start the background poll thread, calling `_tick()` every
        `poll_interval` seconds.

        Idempotent: if a thread already exists and is alive, this is a
        no-op — calling `start()` twice (e.g. once from app startup and once
        from a settings-change handler) never spawns a second poller.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the poll thread to exit and wait (briefly) for it to do so.

        Safe to call even if `start()` was never called, or if the thread has
        already exited on its own — `_thread` is checked for both existence
        and liveness before joining.
        """
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        """Poll-thread body: tick, then sleep-or-wake on `_stop`.

        `self._stop.wait(timeout)` is used instead of `time.sleep(timeout)`
        so `stop()` interrupts the wait immediately rather than leaving the
        thread asleep for up to `poll_interval` seconds after being asked to
        exit.
        """
        while not self._stop.is_set():
            self._tick()
            self._stop.wait(self._poll_interval)
