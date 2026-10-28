"""ServerControl: start, stop and restart model servers on a shared machine.

GTK-free orchestrator (spec docs/superpowers/specs/2026-10-28-tt-cli-server-control-design.md,
sections 4.4, 4.5 and 9; wire notes 2026-10-28-tt-cli-wire-notes.md). Standard
library plus sibling GTK-free modules only. Every call that can block (tt,
gozer, docker, the boutique scripts) is made on the caller's thread, so the
GUI must call this from a worker thread; ``confirm_fn`` is called from that
thread too and the GUI bridges it to a dialog (the CLI to a prompt or --yes).

Every dependency is injected through the constructor so tests run against
fakes: the model registry, the merged running-server view, the ``tt`` wrapper,
the ``gozer`` wrapper, the ownership records, the boutique script runner, the
status lookup used by the switch plan, the confirm callback, the two docker
helpers, and the clock/sleep pair.

Start flow (``start``):

1. Already running anywhere (any matching row of ``view_fn()``) and healthy:
   ``already_running`` with its URL; nothing is called. Still booting
   elsewhere: wait (bounded, clock-injected) and report ``already_running``
   once healthy, ``failed`` on timeout, ``starting_elsewhere`` if the caller
   cancels the wait. A matching row that is neither healthy nor booting is
   ``failed``: starting a second copy is an anti-goal.
2. ``ready_to_run.plan_switch(..., registry=...)``. A conflict asks
   ``confirm_fn("switch", ...)``; an unknown hardware group asks
   ``confirm_fn("unknown_hardware", ...)``. A decline starts nothing.
3. Chip count from ``server_registry.chips_for(entry, tt.plan_serve(...))``.
   When leases apply and the count is unknown, ask ``confirm_fn("unknown_chips", ...)``.
   All questions are asked before anything is stopped or started.
4. Leases, one flow per case. No lease logic when gozer is absent, when the
   ``tt`` in use takes its own lease (``tt_takes_lease``), or for a boutique
   model with no hardware group (CPU only, e.g. the prompt server).
   * PINNABLE (a tt-model bundle; ``-- --device-id`` pins it): ``gozer
     acquire`` first (a queued ticket is waited on; cancel/timeout cleans the
     ticket up), start pinned to the leased chips, wait healthy, then read
     ``gozer status``: CLAIMED logs a reap warning, HELD-FOREIGN is normal.
   * UNPINNABLE (a catalog model, any boutique script: ``start_artgen.sh``
     pins to its own fixed chips, which are not the leased ones): no
     acquire. Check free chips, start, read the chips the container opened
     (``docker inspect``), ``gozer adopt`` each. An adopt refused by another
     lease stops the new server (``chips_held``); a refusal by our own lease
     is not a conflict; an adopt that returns None is a failure and stops the
     server.
5. Record the started server in ``ServerRecords`` with its lease id(s).
6. On any failure: stop what was started, then release the leases taken by
   this start (gozer resets the chips on release). If the stop itself fails
   the lease is NOT released (a reset under a running container would break
   it); the server is recorded with its lease so Stop or the next reap can
   clean up.

Lease ``who``: ``"tt-local-generator:<model key>"`` (``who_for``). One
``who`` per model key, not one for the whole app, so ``find_lease(who)``
after a cancel race can only return this model's lease and never the lease
of another server the app runs.

A record may hold several lease ids (an unpinnable start on two boards adopts
one lease per board); they are stored comma-separated in ``lease_id``.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Sequence

import ready_to_run
import server_registry
from gozer_client import AdoptRefused, Lease, Queued
from server_view import RunningServer
from tt_backend import TtResult

log = logging.getLogger(__name__)

WHO = "tt-local-generator"

# Health words that mean "answering requests" and "still booting". Anything
# else (unhealthy, unknown, exited) is neither.
_HEALTHY = frozenset({"healthy", "ready", "ok", "running-healthy"})
_BOOTING = frozenset({"starting", "booting", "loading", "created", "restarting"})

_TT_DEV_RE = re.compile(r"^/dev/tenstorrent/(\d+)$")
_TT_DEV_DIR = "/dev/tenstorrent"

_DOCKER_INSPECT_TIMEOUT_S = 30
# `docker stop --time` grace before SIGKILL. tt model stop uses docker's 10 s
# default, which may be too short for a 14B diffusion server to close its mesh
# (wire notes item 4), so ours is long.
_DOCKER_STOP_GRACE_S = 120


# --------------------------------------------------------------------------
# Outcomes
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class StartOutcome:
    """kind: started | already_running | starting_elsewhere | declined |
    waiting_for_chips | chips_held | failed."""
    kind: str
    url: str | None
    message: str
    lease_id: str | None


@dataclass(frozen=True)
class StopOutcome:
    """kind: stopped | declined | not_allowed | failed."""
    kind: str
    message: str


# --------------------------------------------------------------------------
# Default docker helpers (read-only inspect; stop). Argv lists, timeouts.
# --------------------------------------------------------------------------

def docker_devices(container_id: str) -> list[str] | None:
    """Host device paths a container was given: ``HostConfig.Devices``
    PathOnHost entries, plus ``/dev/tenstorrent`` when the whole directory is
    bind-mounted (tt-inference-server ``run.py`` without ``--device-id``).
    ``[]`` when it has none; None when docker could not be asked (the caller
    treats None as "unknown", never as "no chips")."""
    cid = str(container_id or "")
    if not cid or cid.startswith("-"):
        return None
    try:
        r = subprocess.run(["docker", "inspect", cid], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True,
                           timeout=_DOCKER_INSPECT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("docker inspect %s failed: %s", cid, exc)
        return None
    if r.returncode != 0:
        log.warning("docker inspect %s exited %s: %s", cid, r.returncode,
                    (r.stderr or "").strip()[:200])
        return None
    try:
        data = json.loads(r.stdout or "null")
    except ValueError:
        return None
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    info = data[0]
    host = info.get("HostConfig") if isinstance(info.get("HostConfig"), dict) else {}
    out: list[str] = []

    def add(p) -> None:
        if isinstance(p, str) and p and p not in out:
            out.append(p)

    for d in host.get("Devices") or []:
        if isinstance(d, dict):
            add(d.get("PathOnHost"))
    for b in host.get("Binds") or []:
        if isinstance(b, str):
            src = b.split(":", 1)[0].rstrip("/")
            if src == _TT_DEV_DIR or src.startswith(_TT_DEV_DIR + "/"):
                add(src)
    for m in info.get("Mounts") or []:
        if isinstance(m, dict):
            src = str(m.get("Source") or "").rstrip("/")
            if src == _TT_DEV_DIR or src.startswith(_TT_DEV_DIR + "/"):
                add(src)
    return out


def docker_stop(container_id: str) -> TtResult:
    """``docker stop --time 120 <id>`` with a timeout longer than the grace."""
    cid = str(container_id or "")
    if not cid or cid.startswith("-"):
        return TtResult(False, f"bad container id {cid!r}")
    try:
        r = subprocess.run(["docker", "stop", "--time", str(_DOCKER_STOP_GRACE_S), cid],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=_DOCKER_STOP_GRACE_S + 60)
    except (OSError, subprocess.SubprocessError) as exc:
        return TtResult(False, f"docker stop failed: {exc}")
    lines = tuple(((r.stdout or "") + (r.stderr or "")).splitlines())
    if r.returncode != 0:
        tail = [x for x in lines if x.strip()][-1:] or [""]
        return TtResult(False, f"docker stop exited {r.returncode}: {tail[0][:200]}", lines)
    return TtResult(True, "", lines)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _uptime(s: int | None) -> str:
    if s is None:
        return "unknown"
    s = int(s)
    h, rem = divmod(s, 3600)
    m = rem // 60
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m"
    return f"{s}s"


def _tail(lines: Sequence[str], n: int = 5) -> str:
    keep = [x for x in lines if str(x).strip()][-n:]
    return ("\nLast output:\n  " + "\n  ".join(keep)) if keep else ""


def _lease_ids(value) -> list[str]:
    """A record's ``lease_id`` as a list (stored comma-separated)."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return [p for p in str(value).split(",") if p]


def _short(cid: str | None) -> str | None:
    return str(cid)[:12] if cid else None


@dataclass
class _Run:
    """What one start has done so far, so a failure can undo exactly that."""
    launched: bool = False
    lease_ids: list[str] = field(default_factory=list)
    server: RunningServer | None = None
    port: int | None = None


# --------------------------------------------------------------------------
# ServerControl
# --------------------------------------------------------------------------

class ServerControl:
    def __init__(self, *, registry_fn, view_fn, tt, gozer, records, boutique,
                 status_of, confirm_fn, docker_devices: Callable = docker_devices,
                 docker_stop: Callable = docker_stop, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, who: str = WHO,
                 tt_takes_lease: bool = False, start_timeout: float = 1800.0,
                 elsewhere_timeout: float = 1800.0, chip_wait_timeout: float = 3600.0,
                 stop_wait_timeout: float = 120.0, poll_s: float = 5.0,
                 lease_expect: str = "12h") -> None:
        self._registry_fn = registry_fn
        self._view_fn = view_fn
        self._tt = tt
        self._gozer = gozer
        self._records = records
        self._boutique = boutique
        self._status_of = status_of
        self._confirm_fn = confirm_fn
        self._docker_devices = docker_devices
        self._docker_stop = docker_stop
        self._clock = clock
        self._sleep = sleep
        self._who = who
        self._tt_takes_lease = tt_takes_lease
        self._start_timeout = start_timeout
        self._elsewhere_timeout = elsewhere_timeout
        self._chip_wait_timeout = chip_wait_timeout
        self._stop_wait_timeout = stop_wait_timeout
        self._poll_s = poll_s
        self._lease_expect = lease_expect
        # Records that looked stale on the previous reap (the "one confirming poll").
        self._suspects: set[tuple] = set()

    # ------------------------------------------------------------------ util

    def who_for(self, key: str) -> str:
        """The gozer ``who`` for leases of this model."""
        return f"{self._who}:{key}"

    def _confirm(self, kind: str, text: str) -> bool:
        try:
            return bool(self._confirm_fn(kind, text))
        except Exception:
            log.exception("confirm_fn(%s) raised; treating it as no", kind)
            return False

    def _status_str(self, key: str) -> str:
        try:
            v = self._status_of(key)
        except Exception:
            return "off"
        return str(getattr(v, "value", v) or "off")

    def _view(self) -> list[RunningServer] | None:
        try:
            return list(self._view_fn() or [])
        except Exception as exc:
            log.warning("could not read the running servers: %s", exc)
            return None

    def _registry(self) -> dict:
        try:
            return dict(self._registry_fn() or {})
        except Exception as exc:
            log.warning("model registry unavailable: %s", exc)
            return {}

    @staticmethod
    def _is_entry(s: RunningServer, e) -> bool:
        return (s.key == e.key or (bool(e.tt_name) and s.name == e.tt_name)
                or (bool(e.served_id) and s.served_id == e.served_id))

    @staticmethod
    def _health(s: RunningServer) -> str:
        return str(s.health or "").lower()

    def _lease_active(self, entry) -> bool:
        """True when this start must take or adopt a gozer lease."""
        if self._tt_takes_lease:
            return False
        if entry.backend != "tt" and entry.hardware_group is None:
            return False          # a CPU-only boutique server opens no chips
        try:
            return bool(self._gozer.available())
        except Exception:
            return False

    def _recorded_lease_ids(self) -> set[str]:
        out: set[str] = set()
        for r in self._records.load():
            out.update(_lease_ids(r.get("lease_id")))
        return out

    @staticmethod
    def _boutique_result(results) -> TtResult:
        results = list(results or [])
        lines: list[str] = []
        for r in results:
            lines += (getattr(r, "stdout", "") or "").splitlines()
            lines += (getattr(r, "stderr", "") or "").splitlines()
        bad = [r for r in results if getattr(r, "returncode", 1) != 0]
        if not results:
            return TtResult(False, "the script did not run", tuple(lines))
        if bad:
            return TtResult(False, f"the script exited {bad[0].returncode}", tuple(lines))
        return TtResult(True, "", tuple(lines))

    @staticmethod
    def _alternative(entry) -> str:
        if entry.backend == "tt" and entry.sm_key:
            return (f" tt-local-generator's own start script can serve {entry.name} instead "
                    f"(model key {entry.sm_key!r}, set prefer: boutique in "
                    "config/server_overrides.yaml); it runs on its own fixed chips.")
        return ""

    # ----------------------------------------------------------------- start

    def start(self, model_key: str, *, port: int | None = None,
              on_progress: Callable[[str], None] | None = None,
              cancel: Callable[[], bool] | None = None) -> StartOutcome:
        def say(msg: str) -> None:
            if on_progress is not None:
                try:
                    on_progress(msg)
                except Exception:
                    log.debug("on_progress raised", exc_info=True)

        def cancelled() -> bool:
            try:
                return bool(cancel and cancel())
            except Exception:
                return False

        registry = self._registry()
        entry = registry.get(model_key)
        if entry is None:
            return StartOutcome("failed", None,
                                f"{model_key} is not a model tt-local-generator knows how to start.",
                                None)
        name = entry.name

        # 1. Running anywhere already?
        early = self._check_running(entry, say, cancelled)
        if early is not None:
            return early

        # 2. What would it conflict with?
        plan = ready_to_run.plan_switch(entry.key, self._status_str, registry=registry)
        if plan.unknown:
            text = (f"tt-local-generator does not know which chips {name} uses or which "
                    "running models share them. Starting it may disturb another model on "
                    "this machine. Start it anyway?")
            if not self._confirm("unknown_hardware", text):
                return StartOutcome("declined", None, f"{name} was not started.", None)
        incumbent = plan.conflict
        if incumbent:
            inc = registry.get(incumbent)
            inc_name = inc.name if inc is not None else incumbent
            text = (f"Starting {name} needs the chips that {inc_name} ({incumbent}) is using. "
                    f"{inc_name} will be stopped first, then {name} ({entry.key}) will start. "
                    "Continue?")
            if not self._confirm("switch", text):
                return StartOutcome("declined", None,
                                    f"{name} was not started; {inc_name} is still running.", None)

        # 3. Chip count and lease mode. Every question is asked before acting.
        tt_plan = None
        if entry.backend == "tt" and entry.tt_name:
            try:
                tt_plan = self._tt.plan_serve(entry.tt_name, port=port)
            except Exception:
                tt_plan = None
        chips = server_registry.chips_for(entry, tt_plan)
        leasing = self._lease_active(entry)
        if leasing and chips is None:
            text = (f"tt-local-generator does not know how many chips {name} needs, so it "
                    "cannot check that enough are free before starting. It will lease the "
                    "chips the server opens after it starts. Start it anyway?")
            if not self._confirm("unknown_chips", text):
                return StartOutcome("declined", None, f"{name} was not started.", None)
        pinnable = (leasing and entry.backend == "tt" and tt_plan is not None
                    and tt_plan.backend == "tt-model" and bool(chips))

        # 4-6. Act. Anything unexpected undoes what this start did.
        run = _Run()
        try:
            if incumbent:
                err = self._stop_incumbent(incumbent, registry, say)
                if err:
                    return StartOutcome("failed", None,
                                        f"{name} was not started: {err}", None)
            if pinnable:
                return self._start_pinnable(entry, registry, chips, port, run, say,
                                            cancel, cancelled)
            return self._start_unpinnable(entry, registry, chips, port, leasing, run,
                                          say, cancel, cancelled)
        except Exception as exc:
            log.exception("start of %s failed", entry.key)
            return self._abort(entry, registry, run, "failed",
                               f"Starting {name} failed unexpectedly: {exc}.")

    def _check_running(self, entry, say, cancelled) -> StartOutcome | None:
        view = self._view()
        if view is None:
            return StartOutcome("failed", None,
                                f"{entry.name} was not started: tt-local-generator could not "
                                "read which servers are running, so it cannot tell whether "
                                "it is already loaded.", None)
        matches = [s for s in view if self._is_entry(s, entry)]
        healthy = [s for s in matches if self._health(s) in _HEALTHY]
        if healthy:
            s = sorted(healthy, key=lambda x: not x.host_is_local)[0]
            where = "" if s.host_is_local else " on another machine"
            return StartOutcome("already_running", s.url,
                                f"{entry.name} is already running{where} at {s.url}.", None)
        if any(self._health(s) in _BOOTING for s in matches):
            say(f"{entry.name} is being started elsewhere (by someone else); "
                "waiting for it to be ready.")
            deadline = self._clock() + self._elsewhere_timeout
            while True:
                if cancelled():
                    return StartOutcome("starting_elsewhere", None,
                                        f"{entry.name} is still being started elsewhere.", None)
                if self._clock() >= deadline:
                    return StartOutcome("failed", None,
                                        f"{entry.name} was being started elsewhere and did not "
                                        "become ready in time. Check its logs.", None)
                self._sleep(self._poll_s)
                view = self._view() or []
                matches = [s for s in view if self._is_entry(s, entry)]
                healthy = [s for s in matches if self._health(s) in _HEALTHY]
                if healthy:
                    s = sorted(healthy, key=lambda x: not x.host_is_local)[0]
                    return StartOutcome("already_running", s.url,
                                        f"{entry.name} was started elsewhere and is ready at "
                                        f"{s.url}.", None)
                if not any(self._health(s) in _BOOTING for s in matches):
                    return StartOutcome("failed", None,
                                        f"{entry.name} was being started elsewhere and stopped "
                                        "before it was ready. Try again.", None)
        listed = [s for s in matches if s.source != "saved"]
        if listed:
            s = listed[0]
            return StartOutcome("failed", None,
                                f"{entry.name} is listed as running ({s.container or s.url}) "
                                f"but its health is {s.health or 'unknown'}. A second copy was "
                                "not started; stop that one first or check its logs.", None)
        return None

    def _stop_incumbent(self, key: str, registry: dict, say) -> str:
        """Stop the server the user agreed to switch away from. Returns an
        error string, or "" on success."""
        inc = registry.get(key)
        view = self._view() or []
        rows = [s for s in view if (s.key == key or (inc is not None and self._is_entry(s, inc)))
                and s.host_is_local]
        say(f"Stopping {inc.name if inc else key}.")
        if rows:
            for s in rows:
                out = self._stop_server(s, registry)
                if out.kind != "stopped":
                    return out.message
            return ""
        # Not in the view (the status lookup saw it): stop it by its entry.
        if inc is None:
            return f"tt-local-generator does not know how to stop {key}."
        if inc.backend == "boutique" and inc.sm_key:
            res = self._boutique_result(self._boutique.stop(inc.sm_key))
        elif inc.tt_name:
            res = self._tt.stop([inc.tt_name])
        elif inc.sm_key:
            res = self._boutique_result(self._boutique.stop(inc.sm_key))
        else:
            return f"tt-local-generator does not know how to stop {inc.name}."
        if not res.ok:
            return f"stopping {inc.name} failed ({res.reason}).{_tail(res.lines)}"
        for r in self._records.load():
            if r.get("name") == key:
                for lid in _lease_ids(r.get("lease_id")):
                    self._gozer.release(lid)
                self._records.remove(key)
        return ""

    def _launch(self, entry, port, extra: Sequence[str], run: _Run, say, cancel) -> TtResult:
        run.launched = True
        if entry.backend == "tt":
            return self._tt.serve(entry.tt_name, port=port, extra_args=tuple(extra),
                                  on_line=say, cancel=cancel)
        res = self._boutique_result(self._boutique.start(entry.sm_key, gui=True))
        for ln in res.lines:
            say(ln)
        return res

    def _wait_healthy(self, entry, say, cancelled) -> tuple[RunningServer | None, str]:
        deadline = self._clock() + self._start_timeout
        while True:
            view = self._view() or []
            ok = [s for s in view if self._is_entry(s, entry) and s.host_is_local
                  and self._health(s) in _HEALTHY]
            if ok:
                return ok[0], ""
            if cancelled():
                return None, "cancelled"
            if self._clock() >= deadline:
                return None, f"it did not become healthy within {self._start_timeout:g} s"
            self._sleep(self._poll_s)

    def _dev_indices(self, bdfs: Sequence[str]) -> list[str] | None:
        """Device indices (for --device-id) of leased chips, from gozer status."""
        rows = self._gozer.status().get("chips") or []
        by_bdf = {str(r.get("bdf")): r.get("dev_index") for r in rows if isinstance(r, dict)}
        out = []
        for b in bdfs:
            idx = by_bdf.get(str(b))
            if idx is None:
                return None
            out.append(str(idx))
        return out

    def _cleanup_ticket(self, ticket: str, who: str) -> None:
        """After a cancelled or timed-out wait: drop the ticket, or, if it was
        already consumed, release the lease it became (only if it is not a
        lease another recorded server holds)."""
        if self._gozer.cancel_ticket(ticket):
            return
        lease = self._gozer.find_lease(who)
        if lease is None:
            return
        if lease.id in self._recorded_lease_ids():
            log.warning("find_lease(%s) returned %s, which a recorded server holds; "
                        "not releasing it", who, lease.id)
            return
        self._gozer.release(lease.id)

    def _start_pinnable(self, entry, registry, chips, port, run, say, cancel, cancelled):
        name = entry.name
        who = self.who_for(entry.key)
        got = self._gozer.acquire(chips, who, f"serve {entry.key} (tt-local-generator)",
                                  expect=self._lease_expect)
        if isinstance(got, Queued):
            say(f"Waiting for chips: {chips} chip(s) for {name} are busy; queued as "
                f"{got.ticket}. Cancel to give up.")
            lease = self._gozer.wait(got.ticket, self._chip_wait_timeout, cancel=cancel)
            if lease is None:
                self._cleanup_ticket(got.ticket, who)
                why = "cancelled" if cancelled() else "gave up"
                return StartOutcome("waiting_for_chips", None,
                                    f"{name} was not started: it was waiting for chips and "
                                    f"{why}. Nothing was left queued.", None)
            got = lease
        if not isinstance(got, Lease):
            return StartOutcome("failed", None,
                                f"{name} was not started: gozer did not grant {chips} chip(s).",
                                None)
        run.lease_ids.append(got.id)
        ids = self._dev_indices(got.chips)
        if not ids:
            return self._abort(entry, registry, run, "failed",
                               f"{name} was not started: could not map the leased chips "
                               f"{', '.join(got.chips)} to device ids.")
        say(f"Leased chips {','.join(ids)} ({got.id}); starting {name} on them.")
        res = self._launch(entry, port, ["--device-id", ",".join(ids)], run, say, cancel)
        if not res.ok:
            return self._abort(entry, registry, run, "failed",
                               f"{name} failed to start ({res.reason}).", res.lines)
        server, why = self._wait_healthy(entry, say, cancelled)
        if server is None:
            return self._abort(entry, registry, run,
                               "declined" if why == "cancelled" else "failed",
                               f"{name} did not start: {why}.", res.lines)
        run.server = server
        self._check_lease_state(got, say)
        return self._finish(entry, run, server)

    def _check_lease_state(self, lease: Lease, say) -> None:
        rows = self._gozer.status().get("chips") or []
        for r in rows:
            if not isinstance(r, dict) or str(r.get("bdf")) not in lease.chips:
                continue
            if r.get("state") == "CLAIMED":
                msg = (f"lease {lease.id} on {r.get('bdf')} is CLAIMED: gozer sees no process "
                       "holding the chip and will reap the lease 900 s after it was granted "
                       "while the server keeps running")
                log.warning(msg)
                say("Warning: " + msg + ".")

    def _targets(self, devices: Sequence[str]) -> list[str] | None:
        """Device indices a container opened. The whole directory means every chip."""
        out: set[int] = set()
        for p in devices:
            p = str(p).rstrip("/")
            if p == _TT_DEV_DIR:
                rows = self._gozer.status().get("chips") or []
                idx = [r.get("dev_index") for r in rows if isinstance(r, dict)]
                if not idx or any(i is None for i in idx):
                    return None
                out.update(int(i) for i in idx)
                continue
            m = _TT_DEV_RE.match(p)
            if m:
                out.add(int(m.group(1)))
        return [str(i) for i in sorted(out)]

    def _start_unpinnable(self, entry, registry, chips, port, leasing, run, say, cancel,
                          cancelled):
        name = entry.name
        if leasing:
            free = self._gozer.free_chips()
            if chips is not None and len(free) < chips:
                return StartOutcome("chips_held", None,
                                    f"{name} was not started: it needs {chips} chip(s) and "
                                    f"only {len(free)} are free; other leases hold the rest."
                                    + self._alternative(entry), None)
            say(f"{len(free)} chip(s) free; starting {name}.")
        res = self._launch(entry, port, (), run, say, cancel)
        if not res.ok:
            return self._abort(entry, registry, run, "failed",
                               f"{name} failed to start ({res.reason}).", res.lines)
        server, why = self._wait_healthy(entry, say, cancelled)
        if server is None:
            return self._abort(entry, registry, run,
                               "declined" if why == "cancelled" else "failed",
                               f"{name} did not start: {why}.", res.lines)
        run.server = server
        if leasing:
            bad = self._adopt_opened(entry, registry, server, run, say)
            if bad is not None:
                return bad
        return self._finish(entry, run, server)

    def _adopt_opened(self, entry, registry, server, run, say) -> StartOutcome | None:
        name = entry.name
        who = self.who_for(entry.key)
        devs = self._docker_devices(server.container_id) if server.container_id else None
        targets = self._targets(devs) if devs is not None else None
        if targets is None:
            return self._abort(entry, registry, run, "failed",
                               f"{name} started, but tt-local-generator could not tell which "
                               "chips its container opened, so it could not lease them. It "
                               "was stopped so it does not run without a lease.")
        own_refused = False
        for t in targets:
            r = self._gozer.adopt(t, who, f"serve {entry.key} (tt-local-generator)")
            if isinstance(r, Lease):
                if r.id not in run.lease_ids:
                    run.lease_ids.append(r.id)
            elif isinstance(r, AdoptRefused):
                if r.holder == who:
                    own_refused = True     # our own lease already covers it
                    continue
                return self._abort(entry, registry, run, "chips_held",
                                   f"{name} was stopped: chip {t} it opened is leased by "
                                   f"{r.holder}." + self._alternative(entry))
            else:
                return self._abort(entry, registry, run, "failed",
                                   f"{name} was stopped: gozer could not lease chip {t} it "
                                   "opened.")
        if own_refused and not run.lease_ids:
            lease = self._gozer.find_lease(who)
            if lease is not None:
                run.lease_ids.append(lease.id)
            else:
                log.warning("adopt for %s was refused by our own lease, but find_lease "
                            "found none", entry.key)
        if run.lease_ids:
            say(f"Leased the chips {name} opened ({', '.join(run.lease_ids)}).")
        return None

    def _finish(self, entry, run: _Run, server: RunningServer) -> StartOutcome:
        lease = ",".join(run.lease_ids) or None
        self._records.add(entry.key, server.port, _short(server.container_id), lease,
                          time.time())
        return StartOutcome("started", server.url,
                            f"{entry.name} started at {server.url}.", lease)

    def _abort(self, entry, registry, run: _Run, kind: str, message: str,
               lines: Sequence[str] = ()) -> StartOutcome:
        """Undo a failed start: stop what was launched, then release this
        start's leases. A failed stop keeps the leases and records them."""
        stopped = True
        stop_note = ""
        server = run.server
        if run.launched:
            if server is None:
                view = self._view() or []
                rows = [s for s in view if self._is_entry(s, entry) and s.host_is_local]
                server = rows[0] if rows else None
            if server is not None:
                res = self._stop_impl(server, registry)
            elif entry.backend == "tt" and entry.tt_name:
                res = self._tt.stop([entry.tt_name])
            elif entry.sm_key:
                res = self._boutique_result(self._boutique.stop(entry.sm_key))
            else:
                res = TtResult(False, "no way known to stop it")
            if not res.ok:
                stopped = False
                stop_note = (f" Stopping it failed too ({res.reason}); it is recorded as "
                             "started by tt-local-generator so Stop in the Servers menu, or "
                             "the next refresh once it is gone, can clean it up.")
                if run.lease_ids:
                    stop_note += (" Its chip lease was kept, because releasing it would "
                                  "reset the chips under the running container.")
                lines = tuple(lines) + tuple(res.lines)
        if stopped:
            for lid in run.lease_ids:
                if not self._gozer.release(lid):
                    log.warning("release of lease %s failed", lid)
        else:
            self._records.add(entry.key, server.port if server else None,
                              _short(server.container_id) if server else None,
                              ",".join(run.lease_ids) or None, time.time())
        if run.launched and stopped:
            message += " What was started has been stopped."
        if run.lease_ids and stopped:
            message += " Its chip lease was released (gozer resets the chips)."
        return StartOutcome(kind, None, message + stop_note + _tail(lines), None)

    # ------------------------------------------------------------------ stop

    def _stop_impl(self, server: RunningServer, registry: dict) -> TtResult:
        """Stop by the rules in the wire notes: a catalog name or bundle id goes
        to ``tt model stop``; anything else to our script's --stop when the
        registry knows the key, else ``docker stop <container id>``."""
        tt_names = {e.tt_name for e in registry.values() if getattr(e, "tt_name", None)}
        if server.name in tt_names:
            return self._tt.stop([server.name])
        e = registry.get(server.key)
        if e is not None and e.sm_key:
            return self._boutique_result(self._boutique.stop(e.sm_key))
        if server.container_id:
            return self._docker_stop(server.container_id)
        return TtResult(False, f"tt-local-generator does not know how to stop {server.name}")

    def _record_for(self, server: RunningServer) -> dict | None:
        cid = _short(server.container_id)
        for r in self._records.load():
            rcid = r.get("container_id")
            if rcid:
                if cid and rcid == cid:
                    return r
            elif server.container_id is None and r.get("name") in (server.key, server.name) \
                    and r.get("port") == server.port:
                return r
        return None

    def stop(self, server: RunningServer) -> StopOutcome:
        if not server.host_is_local:
            return StopOutcome("not_allowed",
                               f"{server.name} runs on another machine ({server.url}); "
                               "tt-local-generator only uses it and cannot stop it.")
        if server.owner != "app":
            via = {"tt": "tt", "script": "a start script", "discovered": "unknown means",
                   "saved": "a saved entry"}.get(server.source, server.source)
            text = (f"{server.name} was not started by tt-local-generator. "
                    f"It was found through {via} (source: {server.source}), "
                    f"container {server.container or server.container_id or 'unknown'}, "
                    f"port {server.port}, up {_uptime(server.uptime_s)}. "
                    "Someone else may be using it. Stop it?")
            if not self._confirm("stop_external", text):
                return StopOutcome("declined", f"{server.name} was left running.")
        return self._stop_server(server, self._registry())

    def _stop_server(self, server: RunningServer, registry: dict) -> StopOutcome:
        """Stop without asking (the caller has the user's answer), then release
        the lease only if the app's own record for this server holds one."""
        res = self._stop_impl(server, registry)
        if not res.ok:
            return StopOutcome("failed", f"Stopping {server.name} failed ({res.reason})."
                               + _tail(res.lines))
        msg = f"{server.name} was stopped."
        if server.owner == "app":
            rec = self._record_for(server)
            if rec is not None:
                for lid in _lease_ids(rec.get("lease_id")):
                    if self._gozer.release(lid):
                        msg += f" Lease {lid} was released."
                    else:
                        log.warning("release of lease %s failed", lid)
                self._records.remove(rec.get("name"))
        return StopOutcome("stopped", msg)

    # --------------------------------------------------------------- restart

    def restart(self, server_or_key, *, port: int | None = None,
                on_progress: Callable[[str], None] | None = None,
                cancel: Callable[[], bool] | None = None) -> StartOutcome:
        """Stop (asking first if the app did not start it), then start."""
        registry = self._registry()
        if isinstance(server_or_key, RunningServer):
            server = server_or_key
            key = server.key
        else:
            key = str(server_or_key)
            e = registry.get(key)
            view = self._view() or []
            rows = [s for s in view if e is not None and self._is_entry(s, e)
                    and s.host_is_local]
            server = rows[0] if rows else None
        if server is not None:
            out = self.stop(server)
            if out.kind == "declined":
                return StartOutcome("declined", None, out.message, None)
            if out.kind != "stopped":
                return StartOutcome("failed", None, out.message, None)
            if not self._wait_gone(server):
                return StartOutcome("failed", None,
                                    f"{server.name} was stopped but is still listed as "
                                    "running, so it was not started again.", None)
        return self.start(key, port=port, on_progress=on_progress, cancel=cancel)

    def _wait_gone(self, server: RunningServer) -> bool:
        deadline = self._clock() + self._stop_wait_timeout
        while True:
            view = self._view()
            if view is not None and not any(
                    s.host_is_local and s.port == server.port
                    and (s.key == server.key or s.name == server.name
                         or (server.container_id and s.container_id == server.container_id))
                    for s in view):
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(self._poll_s)

    # ------------------------------------------------------------------ reap

    @staticmethod
    def _rec_ident(r: dict) -> tuple:
        cid = r.get("container_id")
        return ("cid", cid) if cid else ("ep", r.get("name"), r.get("port"))

    def reap_stale_leases(self, live_container_ids: set[str] | None,
                          live_endpoints: set[tuple[str, int]] | None) -> list[str]:
        """Release the leases of recorded servers that are gone.

        ``live_container_ids=None`` means ``tt model ps`` failed: nothing is
        pruned. A record is dropped only when it looks stale on two consecutive
        calls (the one confirming poll); the first miss only marks it. Only
        leases held in records are released. Returns the released lease ids.
        """
        if live_container_ids is None:
            return []
        live_ids = set(live_container_ids)
        eps = None if live_endpoints is None else set(live_endpoints)
        stale_now: set[tuple] = set()
        for r in self._records.load():
            cid = r.get("container_id")
            if cid:
                stale = cid not in live_ids
            elif eps is None:
                stale = False
            else:
                stale = (r.get("name"), r.get("port")) not in eps
            if stale:
                stale_now.add(self._rec_ident(r))
        first_miss = stale_now - self._suspects
        self._suspects = set(first_miss)
        # Shield first-time misses from prune by presenting them as live.
        for ident in first_miss:
            if ident[0] == "cid":
                live_ids.add(ident[1])
            elif eps is not None:
                eps.add((ident[1], ident[2]))
        released: list[str] = []
        for r in self._records.prune(live_ids, eps):
            for lid in _lease_ids(r.get("lease_id")):
                if self._gozer.release(lid):
                    released.append(lid)
                else:
                    log.warning("could not release stale lease %s of %s; gozer reconcile "
                                "is the backstop", lid, r.get("name"))
        return released
