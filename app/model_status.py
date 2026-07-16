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

The background poll thread, `subscribe()`, and capability-aware helpers are
deliberately NOT implemented here — they land in Tasks 2-4. `_tick` is not
implemented yet either; nothing currently calls `_resolve` except tests and
(eventually) `_tick`.

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

import threading
import time
from enum import Enum
from typing import Callable, Optional


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
        self.health_fn = health_fn or (
            lambda: __import__("server_manager").status_all()
        )
        # Lazy-default detect_fn: same reasoning for the artgen package.
        self.detect_fn = detect_fn or (
            lambda: __import__("artgen").detect_artgen_endpoint()
        )
        self.clock = clock or time.monotonic
        self.port_probe = port_probe or self._default_port_probe
        self.poll_interval = poll_interval
        self.start_timeout = start_timeout

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
            self._starting[key] = self.clock()

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
        """Best-effort default port_probe: no port table exists yet in this
        task, so nothing can be inferred from a key alone. Returns False
        (never infers STARTING-by-port) until a later task wires this up to
        real per-service port numbers.

        Kept as a plain TCP-connect stub (rather than a `NotImplementedError`)
        so the service is still fully constructible and usable — with the
        port-inference branch of `_resolve` simply never triggering — before
        that wiring lands.
        """
        return False
