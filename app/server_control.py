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
status lookup used by the switch plan, the confirm callback, the docker
helpers, the endpoint probe, the chip reset, and the clock/sleep pair.

Start flow (``start``; one start per model key at a time, by a per-key lock):

1. Already running anywhere (any matching row of ``view_fn()``) and healthy:
   ``already_running`` with its URL; nothing is called. Still booting
   elsewhere: wait (bounded, clock-injected) and report ``already_running``
   once healthy, ``failed`` on timeout (nothing is stopped), and
   ``starting_elsewhere`` if the caller cancels the wait. A matching row that
   is neither healthy nor booting is ``failed``: starting a second copy is an
   anti-goal. A row whose health is empty, ``unknown`` or ``running`` (real
   ``tt model ps`` rows may carry a docker status and no health) counts as
   booting, and as healthy once ``probe_fn(url)`` gets an answer.
2. ``ready_to_run.plan_switch(..., registry=...)``. A conflict asks
   ``confirm_fn("switch", ...)``; the question says when the incumbent was not
   started by this app. An incumbent seen only on another machine is not
   stopped (``failed``). An unknown hardware group asks
   ``confirm_fn("unknown_hardware", ...)``. A decline starts nothing.
3. Chip count from ``server_registry.chips_for(entry, tt.plan_serve(...))``.
   When leases apply and the count is unknown, ask ``confirm_fn("unknown_chips", ...)``.
   This includes a boutique entry with ``chips=None`` on a gozer machine.
   All questions are asked before anything is stopped or started.
4. An accepted switch stops the incumbent. When ``plan.needs_reset`` and
   there is no gozer on the machine, ``reset_fn()`` runs next (the old
   Ready-to-Run ``tt-smi -r``), but only when no other healthy or booting
   local server (outside the stopped incumbent) is in the view: the reset
   hits every board. When one is, the reset is skipped and the outcome message says
   "Chips not reset because <name> is running on this machine." On a gozer
   machine it never runs: a manual reset is forbidden there, and releasing an
   app-held lease already resets.
5. Leases, one flow per case. No lease logic when gozer is absent, when the
   ``tt`` in use takes its own lease (``tt_takes_lease``), or for a boutique
   model with no hardware group (CPU only, e.g. the prompt server).
   * PINNABLE (a tt-model bundle; ``-- --device-id`` pins it): ``gozer
     acquire`` first (a queued ticket is waited on; cancel or timeout cleans
     the ticket up), start pinned to the leased chips (``serve`` gets the
     plan, and fails closed if the pin cannot be applied), wait healthy, then
     read ``gozer status``: CLAIMED logs a reap warning, HELD-FOREIGN is normal.
   * UNPINNABLE (a catalog model, any boutique script: ``start_artgen.sh``
     pins to its own fixed chips, which are not the leased ones): no
     acquire. Check free chips, start, find the container (the view's
     container id, else ``docker_container_for_port(port)``), read the chips
     it opened (``docker inspect``), ``gozer adopt`` each. An adopt refused by
     another lease first asks ``confirm_fn("stop_external", ...)`` naming the
     container and the other lease holder (the container may be someone
     else's that appeared during our boot): a yes stops it (``chips_held``),
     a no leaves it running and unrecorded (``chips_held``, leases kept as
     below); a refusal by our own
     lease is not a conflict (``find_lease`` recovers that lease id); an adopt
     that returns None, or a container that cannot be found, is a failure
     and stops the server (fail closed: an unleased server on a shared box is
     worse than a refused start).
6. Right before launching, the running check (1) is run again: another party
   may have started the model while we waited for chips or for the user. The
   matching rows at that moment are the pre-launch snapshot; an unreadable
   view launches nothing. The health wait and the failure cleanup consider
   only rows NOT in the snapshot. ``tt serve`` blocks through the whole boot,
   so another party's server can still appear during it; three guards keep
   it from being taken for ours:
   * more than one new row of the model: adopt none, stop nothing, keep the
     leases (see "kept leases" below);
   * pinnable: a new row counts as ours only when ``docker_devices`` of its
     container lies within the leased device ids (one inspect); otherwise
     the same treatment;
   * unpinnable: an adopt refused by another lease asks before stopping (5).
   The by-name fallback stop (``tt model stop <name>`` or the script's
   ``--stop``) is used only when the snapshot had no row of this model and
   the view is readable; otherwise nothing is stopped and the leases are
   kept.
   Failure cleanup stops a row without asking only when it is proven ours:
   on the pinnable path, its container opened only the leased chips. Every
   other row (all unpinnable rows, a pinned row on other chips) is stopped
   only after ``confirm_fn("stop_external", ...)`` ("a server of this model
   appeared during this start and may not be ours"); a no keeps everything.
   Unpinnable adoption: a row whose ``uptime_s`` exceeds the time since our
   launch plus 30 s predates it, so ``confirm_fn("adopt_unknown", ...)`` is
   asked before it is leased and recorded; a no keeps everything. Remaining
   window (accepted, best effort): a foreign container of the same model that
   starts AFTER our launch, on the unpinnable path, cannot be told apart from
   ours and is adopted and recorded as app-owned.
7. Record the started server in ``ServerRecords`` with its lease id(s),
   merged with any lease ids an older record of the same key held, except an
   unreapable one.
8. On any failure: stop what was started, then release the leases taken by
   this start (gozer resets the chips on release). Kept leases: when the stop
   fails, or nothing may be stopped (the guards in 6), the leases are NOT
   released (a reset under a running container would break it). A failed
   stop of an identified server is recorded with its port/container id and
   leases, so Stop or the next reap can clean up. Anything else is recorded
   ``unreapable`` under its own name ``<key>#unreapable-<n>``: never pruned
   by a reap, never replaced by a later record of the key, and its leases
   never merged into one. The outcome message then names the lease and says
   to run ``gozer release <ids>`` once nothing of that start runs. A failed
   stop never replaces an older record of the same key either (it gets
   ``<key>#kept-<n>``).

Legacy mode (``start_legacy`` / ``stop_legacy``): ``tt-ctl`` keeps its old
behaviour for the old ``server_manager`` keys. ``start_legacy`` runs the
boutique script and returns its raw results: no switch question, no incumbent
stopped, no duplicate check, no health wait, no lease. ``stop_legacy`` treats
the explicit command as consent to stop a script-managed service (no
``stop_external`` question); after a successful stop it releases the lease
recorded for that service, if any, and removes the record, silently when there
is nothing to do. The default ``start``/``stop`` above keep all their checks.

Outcome kinds. StartOutcome: started, already_running, starting_elsewhere,
declined (the user said no), cancelled (the caller's ``cancel()`` fired during
the chip queue or the boot), waiting_for_chips (the chip queue timed out),
chips_held, failed. StopOutcome: stopped, declined, not_allowed, failed.

Notes for callers (Tasks 7 and 8):

* Lease ``who`` is ``"tt-local-generator:<model key>"`` (``who_for``), one per
  model key, so ``find_lease(who)`` after a cancel race can only return this
  model's lease, never the lease of another server the app runs.
* A record may hold several lease ids (an unpinnable start on two boards
  adopts one lease per board); they are stored comma-separated in
  ``lease_id``. Anything reading records must split it.
* ``reap_stale_leases`` must be passed the 12-char short container ids that
  ``tt model ps`` reports, and None when ``ps`` failed.
  It never releases a lease whose chips gozer shows held by a process (a
  server stopped outside ServerControl may have left its record while a new
  server boots on those chips), and releases nothing when ``gozer status``
  cannot be read. ``forget(key)`` drops the record of a server stopped by such
  an outside path, under the same guard. Both release with ``reset=False``
  (``gozer release --no-reset``): the server is gone, and a reset could hit an
  unleased server booting on those chips before it opens them. A normal
  ``stop`` of a server the app is stopping still releases with a reset.
* ``view_fn`` must refresh on each call (not a value cached for a whole poll
  interval), or the health waits slow down to that interval.
* ``reset_fn`` is MainWindow's existing reset (``pipeline_engine._tt_smi_reset``);
  None returns count as success, only ``False`` is a failure.
* Records named ``<key>#unreapable-<n>`` or ``<key>#kept-<n>`` are not the
  key's current server; the Servers menu should show unreapable ones as
  "needs a manual `gozer release`", not as a running server.
* ``confirm_fn`` must return the literal ``True`` for yes; any other value
  or an exception is a no.
* Every unpinnable failure cleanup that finds a server row asks
  ``stop_external``, also on a machine without gozer and for CPU-only
  boutique servers, so the GUI/CLI bridge must answer that question on
  failure paths of a start, not only on Stop.
* A cancel that arrives while a question is open is not re-checked after
  the answer (UX limit): an accepted question goes ahead.
* ``confirm_fn`` kinds: ``switch``, ``unknown_hardware``, ``unknown_chips``,
  ``stop_external`` (a Stop of an external server, an unpinnable start whose
  chips another lease holds, and a failure cleanup of a row not proven ours),
  ``adopt_unknown`` (an unpinnable row older than our launch).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field, replace
from typing import Callable, Sequence
from urllib.parse import urlparse

import ready_to_run
import server_registry
from gozer_client import AdoptRefused, Lease, Queued
from server_view import RunningServer
from tt_backend import TtResult

log = logging.getLogger(__name__)

WHO = "tt-local-generator"

# Health words. _HEALTHY answers requests; _BOOTING is on its way. A row whose
# health is in _MAYBE (docker status, no health check) is booting until the
# endpoint probe answers. Anything else (unhealthy, exited) is neither.
_HEALTHY = frozenset({"healthy", "ready", "ok"})
_BOOTING = frozenset({"starting", "booting", "loading", "created", "restarting"})
_MAYBE = frozenset({"", "unknown", "running", "none"})

_TT_DEV_RE = re.compile(r"^/dev/tenstorrent/(\d+)$")
_TT_DEV_DIR = "/dev/tenstorrent"
_CID_RE = re.compile(r"^[0-9a-f]{6,64}$")

_DOCKER_INSPECT_TIMEOUT_S = 30
# `docker stop --time` grace before SIGKILL. tt model stop uses docker's 10 s
# default, which may be too short for a 14B diffusion server to close its mesh
# (wire notes item 4), so ours is long.
_DOCKER_STOP_GRACE_S = 120
_PROBE_TIMEOUT_S = 3.0
# A row up longer than (now - our launch + slack) predates our launch.
_UPTIME_SLACK_S = 30.0
# gozer chip states that mean a process holds the chip.
_HELD_STATES = frozenset({"HELD", "HELD-FOREIGN"})
_CHIPS_IN_USE_NOTE = "chips are in use by another process"


# --------------------------------------------------------------------------
# Outcomes
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class StartOutcome:
    """kind: started | already_running | starting_elsewhere | declined |
    cancelled | waiting_for_chips | chips_held | failed."""
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
# Default helpers (read-only docker inspect/ps; docker stop; endpoint probe).
# Argv lists, timeouts, stdin=DEVNULL, odd input refused.
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


def docker_container_for_port(port) -> str | None:
    """The one running container publishing host ``port``
    (``docker ps --filter publish=<port> --format {{.ID}}``), or None when
    there is none, more than one, or docker could not be asked. Read-only."""
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        return None
    try:
        r = subprocess.run(["docker", "ps", "--filter", f"publish={port}",
                            "--format", "{{.ID}}"], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True,
                           timeout=_DOCKER_INSPECT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("docker ps for port %s failed: %s", port, exc)
        return None
    if r.returncode != 0:
        return None
    ids = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
    if len(ids) != 1 or not _CID_RE.match(ids[0]):
        return None
    return ids[0]


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


def probe_endpoint(url: str) -> bool:
    """True when the server at ``url`` answers ``/v1/models`` or ``/tt-liveness``
    with 2xx. Only http(s) URLs; short timeout; never raises."""
    try:
        u = urlparse(str(url or ""))
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    base = f"{u.scheme}://{u.netloc}"
    for path in ("/v1/models", "/tt-liveness"):
        try:
            with urllib.request.urlopen(base + path, timeout=_PROBE_TIMEOUT_S) as resp:
                if 200 <= resp.status < 300:
                    return True
        except Exception:
            continue
    return False


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


def _describe(server: RunningServer) -> str:
    """Who seems to have started a server: source, container, port, uptime."""
    via = {"tt": "tt", "script": "a start script", "discovered": "a port scan",
           "saved": "a saved entry"}.get(server.source, server.source)
    return (f"It was found through {via} (source: {server.source}), "
            f"container {server.container or server.container_id or 'unknown'}, "
            f"port {server.port}, up {_uptime(server.uptime_s)}.")


def _row_id(s: RunningServer) -> tuple:
    """Identity of a view row, for the pre-launch snapshot."""
    if s.container_id:
        return ("cid", _short(s.container_id))
    return ("ep", s.source, s.url, s.port, s.name)


@dataclass
class _Run:
    """What one start has done so far, so a failure can undo exactly that."""
    launched: bool = False
    lease_ids: list[str] = field(default_factory=list)
    server: RunningServer | None = None
    # Row ids of this model in the view right before launch. None until then.
    snapshot: set | None = None
    # Sentences added to the final outcome message (e.g. a skipped reset).
    notes: list[str] = field(default_factory=list)
    # Pinnable path: the leased device ids; a row is proven ours when its
    # container opened only these.
    pin_ids: list[str] | None = None
    # True when run.server was proven ours (pinned and on the leased chips).
    server_proven: bool = False
    launched_at: float | None = None


# --------------------------------------------------------------------------
# ServerControl
# --------------------------------------------------------------------------

class ServerControl:
    def __init__(self, *, registry_fn, view_fn, tt, gozer, records, boutique,
                 status_of, confirm_fn, docker_devices: Callable = docker_devices,
                 docker_stop: Callable = docker_stop,
                 docker_container_for_port: Callable = docker_container_for_port,
                 probe_fn: Callable[[str], bool] = probe_endpoint,
                 reset_fn: Callable[[], object] | None = None,
                 clock: Callable[[], float] = time.monotonic,
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
        self._container_for_port = docker_container_for_port
        self._probe_fn = probe_fn
        self._reset_fn = reset_fn
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
        self._reap_lock = threading.Lock()
        self._locks_guard = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------------ util

    def who_for(self, key: str) -> str:
        """The gozer ``who`` for leases of this model."""
        return f"{self._who}:{key}"

    def _key_lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._key_locks.setdefault(key, threading.Lock())

    def _confirm(self, kind: str, text: str) -> bool:
        """Only a literal ``True`` is a yes. Anything else (False, None, a
        string such as "no" or even "yes", 1, an exception) is a no, so a
        buggy bridge can never approve a stop."""
        try:
            return self._confirm_fn(kind, text) is True
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

    def _probe(self, url: str | None) -> bool:
        if not url:
            return False
        try:
            return bool(self._probe_fn(url))
        except Exception:
            return False

    def _is_healthy(self, s: RunningServer) -> bool:
        h = self._health(s)
        if h in _HEALTHY:
            return True
        return s.source != "saved" and h in _MAYBE and self._probe(s.url)

    def _is_booting(self, s: RunningServer) -> bool:
        h = self._health(s)
        return h in _BOOTING or (s.source != "saved" and h in _MAYBE)

    def _gozer_present(self) -> bool:
        try:
            return bool(self._gozer.available())
        except Exception:
            return False

    def _lease_active(self, entry) -> bool:
        """True when this start must take or adopt a gozer lease."""
        if self._tt_takes_lease:
            return False
        if entry.backend != "tt" and entry.hardware_group is None:
            return False          # a CPU-only boutique server opens no chips
        return self._gozer_present()

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

    def _release_all(self, ids: Sequence[str]) -> None:
        for lid in ids:
            if not self._gozer.release(lid):
                log.warning("release of lease %s failed", lid)

    def registry(self) -> dict:
        """The model registry as ServerControl sees it (public accessor)."""
        return self._registry()

    # ------------------------------------------------------- legacy old-key mode

    def start_legacy(self, sm_key: str, gui: bool = True) -> list:
        """Legacy-compatible start for an OLD ``server_manager`` key (or ``all``
        / ``single-chip``): run the boutique script and return its raw results.

        This is what ``tt-ctl start <old key>`` always did, kept for callers that
        promise unchanged behaviour. It never stops an incumbent, never asks
        ``confirm_fn``, never waits for health and takes no lease. All the new
        strictness (switch questions, duplicate check, leases, failure cleanup)
        stays in ``start`` for every other caller."""
        return list(self._boutique.start(sm_key, gui=gui) or [])

    def stop_legacy(self, sm_key: str, names: Sequence[str] = ()) -> list:
        """Legacy-compatible stop for an OLD key: an explicit command naming a
        script-managed service is consent to stop it. Runs the script's stop,
        never asks ``confirm_fn``, and, when every script exited 0, releases the
        lease of any record named in ``names`` and removes that record."""
        results = list(self._boutique.stop(sm_key) or [])
        if results and all(getattr(r, "returncode", 1) == 0 for r in results):
            for name in names:
                try:
                    recs = [r for r in self._records.load() if r.get("name") == name]
                    for rec in recs:
                        failed = [lid for lid in _lease_ids(rec.get("lease_id"))
                                  if not self._gozer.release(lid)]
                        if failed:
                            # The one line a legacy stop may add, and only on stderr.
                            print(f"note: lease {', '.join(failed)} of {name} could not be "
                                  "released; run `gozer release` once nothing of it runs.",
                                  file=sys.stderr)
                    if recs:
                        self._records.remove(name)
                except Exception:
                    log.exception("lease cleanup after legacy stop of %s failed", name)
        return results

    # ----------------------------------------------------------------- start

    def start(self, model_key: str, *, port: int | None = None,
              on_progress: Callable[[str], None] | None = None,
              cancel: Callable[[], bool] | None = None) -> StartOutcome:
        """Start ``model_key``. Blocks; call off the GTK thread. Two starts of
        one key run one after the other: the second sees the first's server."""
        with self._key_lock(str(model_key)):
            return self._start_locked(model_key, port, on_progress, cancel)

    def _start_locked(self, model_key, port, on_progress, cancel) -> StartOutcome:
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
        run = _Run()
        try:
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
                out = self._ask_switch(entry, incumbent, registry)
                if out is not None:
                    return out

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
                text = (f"tt-local-generator does not know how many chips {name} needs, so "
                        "it cannot check that enough are free before starting. It will lease "
                        "the chips the server opens after it starts. Start it anyway?")
                if not self._confirm("unknown_chips", text):
                    return StartOutcome("declined", None, f"{name} was not started.", None)
            pinnable = (leasing and entry.backend == "tt" and tt_plan is not None
                        and tt_plan.backend == "tt-model" and bool(chips))

            # 4. Act. Anything unexpected undoes what this start did.
            if incumbent:
                inc_ids = {_row_id(r) for r in self._incumbent_rows(incumbent, registry)}
                err = self._stop_incumbent(incumbent, registry, say)
                if not err and plan.needs_reset:
                    err = self._reset_after_switch(say, inc_ids, run)
                if err:
                    return StartOutcome("failed", None, f"{name} was not started: {err}", None)
            if pinnable:
                out = self._start_pinnable(entry, registry, chips, tt_plan, port, run, say,
                                           cancel, cancelled)
            else:
                out = self._start_unpinnable(entry, registry, chips, port, leasing, run,
                                             say, cancel, cancelled)
            if run.notes:
                out = replace(out, message=out.message + " " + " ".join(run.notes))
            return out
        except Exception as exc:
            log.exception("start of %s failed", entry.key)
            try:
                return self._abort(entry, registry, run, "failed",
                                   f"Starting {name} failed unexpectedly: {exc}.")
            except Exception as exc2:
                log.exception("cleanup after the failed start of %s failed", entry.key)
                return StartOutcome("failed", None,
                                    f"Starting {name} failed unexpectedly ({exc}) and the "
                                    f"cleanup failed too ({exc2}). Check `tt model ps` and "
                                    "`gozer status`.", None)

    def _check_running(self, entry, say, cancelled) -> StartOutcome | None:
        view = self._view()
        if view is None:
            return StartOutcome("failed", None,
                                f"{entry.name} was not started: tt-local-generator could not "
                                "read which servers are running, so it cannot tell whether "
                                "it is already loaded.", None)
        matches = [s for s in view if self._is_entry(s, entry)]
        healthy = [s for s in matches if self._is_healthy(s)]
        if healthy:
            s = sorted(healthy, key=lambda x: not x.host_is_local)[0]
            where = "" if s.host_is_local else " on another machine"
            return StartOutcome("already_running", s.url,
                                f"{entry.name} is already running{where} at {s.url}.", None)
        if any(self._is_booting(s) for s in matches):
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
                                        "become ready in time. It was left alone; check its "
                                        "logs.", None)
                self._sleep(self._poll_s)
                view = self._view() or []
                matches = [s for s in view if self._is_entry(s, entry)]
                healthy = [s for s in matches if self._is_healthy(s)]
                if healthy:
                    s = sorted(healthy, key=lambda x: not x.host_is_local)[0]
                    return StartOutcome("already_running", s.url,
                                        f"{entry.name} was started elsewhere and is ready at "
                                        f"{s.url}.", None)
                if not any(self._is_booting(s) for s in matches):
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

    def _incumbent_rows(self, key: str, registry: dict) -> list[RunningServer]:
        inc = registry.get(key)
        view = self._view() or []
        return [s for s in view if s.key == key or (inc is not None and self._is_entry(s, inc))]

    def _ask_switch(self, entry, incumbent: str, registry: dict) -> StartOutcome | None:
        """Ask before a switch. Returns an outcome to stop at, or None to go on."""
        name = entry.name
        inc = registry.get(incumbent)
        inc_name = inc.name if inc is not None else incumbent
        rows = self._incumbent_rows(incumbent, registry)
        local = [s for s in rows if s.host_is_local]
        if rows and not local:
            return StartOutcome("failed", None,
                                f"{name} was not started: {inc_name} shares its hardware group "
                                f"but runs on another machine ({rows[0].url}); "
                                "tt-local-generator cannot stop it from here.", None)
        text = (f"Starting {name} needs the chips that {inc_name} ({incumbent}) is using. "
                f"{inc_name} will be stopped first, then {name} ({entry.key}) will start.")
        foreign = [s for s in local if s.owner != "app"]
        if foreign:
            text += (f" {inc_name} was not started by tt-local-generator; someone may be "
                     f"using it. {_describe(foreign[0])}")
        text += " Continue?"
        if not self._confirm("switch", text):
            return StartOutcome("declined", None,
                                f"{name} was not started; {inc_name} is still running.", None)
        return None

    def _stop_incumbent(self, key: str, registry: dict, say) -> str:
        """Stop the server the user agreed to switch away from. Returns an
        error string, or "" on success."""
        inc = registry.get(key)
        rows = self._incumbent_rows(key, registry)
        local = [s for s in rows if s.host_is_local]
        if rows and not local:
            return (f"{inc.name if inc else key} runs on another machine ({rows[0].url}); "
                    "tt-local-generator cannot stop it from here.")
        say(f"Stopping {inc.name if inc else key}.")
        if local:
            for s in local:
                out = self._stop_server(s, registry)
                if out.kind != "stopped":
                    return out.message
            return ""
        # Not in the view (the status lookup saw it): stop it by its entry.
        if inc is None:
            return f"tt-local-generator does not know how to stop {key}."
        if inc.backend == "boutique" and inc.sm_key:
            res = self._call(lambda: self._boutique_result(self._boutique.stop(inc.sm_key)))
        elif inc.tt_name:
            res = self._call(lambda: self._tt.stop([inc.tt_name]))
        elif inc.sm_key:
            res = self._call(lambda: self._boutique_result(self._boutique.stop(inc.sm_key)))
        else:
            return f"tt-local-generator does not know how to stop {inc.name}."
        if not res.ok:
            return f"stopping {inc.name} failed ({res.reason}).{_tail(res.lines)}"
        for r in self._records.load():
            if r.get("name") == key and not r.get("unreapable"):
                self._release_all(_lease_ids(r.get("lease_id")))
                self._records.remove(key)
        return ""

    def _reset_after_switch(self, say, inc_ids: set, run: _Run) -> str:
        """Reset the chips after an accepted switch, only on a machine with no
        gozer, and only when no other healthy local server (outside the
        stopped incumbent) is running: the reset hits every board. Returns an
        error string, or ""; a skip adds a note to ``run.notes``."""
        if self._gozer_present():
            log.info("chip reset after the switch skipped: gozer manages this machine, a "
                     "manual reset is not allowed there (releasing an app-held lease resets)")
            return ""
        if self._reset_fn is None:
            log.info("chip reset after the switch skipped: no reset function was given")
            return ""
        view = self._view()
        if view is None:
            run.notes.append("Chips not reset because tt-local-generator could not read "
                             "which servers are running on this machine.")
            return ""
        others = [s for s in view if s.host_is_local and _row_id(s) not in inc_ids
                  and (self._is_healthy(s) or self._is_booting(s))]
        if others:
            names = ", ".join(dict.fromkeys(s.name for s in others))
            run.notes.append(f"Chips not reset because {names} is running on this machine.")
            log.info("chip reset after the switch skipped: %s is running", names)
            return ""
        say("Resetting the boards.")
        try:
            ok = self._reset_fn()
        except Exception as exc:
            return f"the boards could not be reset after the switch ({exc})."
        if ok is False:
            return "the boards could not be reset after the switch."
        return ""

    def _launch(self, entry, port, extra: Sequence[str], plan, run: _Run, say,
                cancel) -> TtResult:
        """Start the server. Takes the pre-launch snapshot first; an unreadable
        view launches nothing (fail closed)."""
        view = self._view()
        if view is None:
            return TtResult(False, "tt-local-generator could not read which servers are "
                                   "running, so it did not launch it")
        run.snapshot = {_row_id(s) for s in view if self._is_entry(s, entry)}
        run.launched = True
        run.launched_at = self._clock()
        if entry.backend == "tt":
            kw = {"plan": plan} if plan is not None else {}
            return self._tt.serve(entry.tt_name, port=port, extra_args=tuple(extra),
                                  on_line=say, cancel=cancel, **kw)
        res = self._boutique_result(self._boutique.start(entry.sm_key, gui=True))
        for ln in res.lines:
            say(ln)
        return res

    def _new_rows(self, entry, run: _Run, view=None) -> list[RunningServer] | None:
        """Local rows of this model that were not there before launch. None
        when the view cannot be read (unknown, not "none")."""
        if view is None:
            view = self._view()
            if view is None:
                return None
        snap = run.snapshot or set()
        return [s for s in view if self._is_entry(s, entry) and s.host_is_local
                and _row_id(s) not in snap]

    def _wait_healthy(self, entry, run: _Run, cancelled) -> tuple[RunningServer | None, str]:
        deadline = self._clock() + self._start_timeout
        while True:
            rows = self._new_rows(entry, run) or []
            if len(rows) > 1:
                ids = ", ".join(s.container or s.container_id or s.url or s.name for s in rows)
                return None, f"ambiguous: {len(rows)} servers of it appeared ({ids})"
            ok = [s for s in rows if self._is_healthy(s)]
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

    def _start_pinnable(self, entry, registry, chips, tt_plan, port, run, say, cancel,
                        cancelled):
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
                if cancelled():
                    return StartOutcome("cancelled", None,
                                        f"{name} was not started: the wait for chips was "
                                        "cancelled. Nothing was left queued.", None)
                return StartOutcome("waiting_for_chips", None,
                                    f"{name} was not started: its chips stayed busy and the "
                                    "wait gave up. Nothing was left queued.", None)
            got = lease
        if not isinstance(got, Lease):
            return StartOutcome("failed", None,
                                f"{name} was not started: gozer did not grant {chips} chip(s).",
                                None)
        run.lease_ids.append(got.id)
        # The model may have been started elsewhere while we waited for chips.
        again = self._check_running(entry, say, cancelled)
        if again is not None:
            self._release_all(run.lease_ids)
            return again
        ids = self._dev_indices(got.chips)
        if not ids:
            return self._abort(entry, registry, run, "failed",
                               f"{name} was not started: could not map the leased chips "
                               f"{', '.join(got.chips)} to device ids.")
        run.pin_ids = list(ids)
        say(f"Leased chips {','.join(ids)} ({got.id}); starting {name} on them.")
        res = self._launch(entry, port, ["--device-id", ",".join(ids)], tt_plan, run, say,
                           cancel)
        if not res.ok:
            return self._abort(entry, registry, run, "cancelled" if cancelled() else "failed",
                               f"{name} failed to start ({res.reason}).", res.lines)
        server, why = self._wait_healthy(entry, run, cancelled)
        if server is None:
            return self._after_wait_failure(entry, registry, run, why, res.lines)
        # Ours only if its container opened exactly within the leased chips.
        if not self._on_leased_chips(server, run):
            return self._keep(entry, run, "failed",
                              f"A {name} server became ready (container "
                              f"{server.container or server.container_id or 'unknown'}), but "
                              f"its container does not use the leased chips {','.join(ids)}, "
                              "so it may be someone else's. It was not adopted and nothing "
                              "was stopped; check `tt model ps`.",
                              container=server.container_id)
        run.server = server
        run.server_proven = True
        self._check_lease_state(got, say)
        return self._finish(entry, run, server)

    def _after_wait_failure(self, entry, registry, run: _Run, why: str,
                            lines: Sequence[str]) -> StartOutcome:
        if why.startswith("ambiguous"):
            # More than one new row: cannot tell which is ours. Adopt none,
            # stop none, keep this start's leases.
            return self._keep(entry, run, "failed",
                              f"{entry.name} did not start cleanly: {why[len('ambiguous: '):]}. "
                              "tt-local-generator cannot tell which one it started, so none "
                              "was adopted and nothing was stopped; check `tt model ps`.",
                              lines)
        return self._abort(entry, registry, run, "cancelled" if why == "cancelled" else "failed",
                           f"{entry.name} did not start: {why}.", lines)

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
        # The model may have been started elsewhere while the user answered.
        again = self._check_running(entry, say, cancelled)
        if again is not None:
            return again
        res = self._launch(entry, port, (), None, run, say, cancel)
        if not res.ok:
            return self._abort(entry, registry, run, "cancelled" if cancelled() else "failed",
                               f"{name} failed to start ({res.reason}).", res.lines)
        server, why = self._wait_healthy(entry, run, cancelled)
        if server is None:
            return self._after_wait_failure(entry, registry, run, why, res.lines)
        # No device proof is possible here. A row that has been up longer than
        # our launch predates it: ask before taking it as ours.
        if (server.uptime_s is not None and run.launched_at is not None
                and server.uptime_s > self._clock() - run.launched_at + _UPTIME_SLACK_S):
            text = (f"A {name} server became ready, but it has been up for "
                    f"{_uptime(server.uptime_s)}, longer than this start has been running, so "
                    f"it may not be the one tt-local-generator launched. {_describe(server)} "
                    "Treat it as started by tt-local-generator (lease and record it)?")
            if not self._confirm("adopt_unknown", text):
                return self._keep(entry, run, "failed",
                                  f"{name} did not start cleanly: the {name} server that became "
                                  f"ready (container {server.container_id or 'unknown'}) was "
                                  "up before this start launched, so it was not adopted and "
                                  "nothing was stopped; check `tt model ps`.",
                                  res.lines, container=server.container_id)
        run.server = server
        if leasing:
            bad = self._adopt_opened(entry, registry, server, run, say)
            if bad is not None:
                return bad
        return self._finish(entry, run, server)

    def _adopt_opened(self, entry, registry, server, run, say) -> StartOutcome | None:
        name = entry.name
        who = self.who_for(entry.key)
        cid = server.container_id
        if not cid and server.port:
            cid = self._container_for_port(server.port)
        if not cid:
            return self._abort(entry, registry, run, "failed",
                               f"{name} started on port {server.port}, but tt-local-generator "
                               "could not find its container (`tt model ps` gave no container "
                               f"id and `docker ps --filter publish={server.port}` found none), "
                               "so it could not tell which chips it opened or lease them. A "
                               "server must not run without a lease on this shared machine.")
        devs = self._docker_devices(cid)
        targets = self._targets(devs) if devs is not None else None
        if targets is None:
            return self._abort(entry, registry, run, "failed",
                               f"{name} started, but tt-local-generator could not tell which "
                               f"chips its container {cid} opened, so it could not lease them. "
                               "A server must not run without a lease on this shared machine.")
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
                # The container may be someone else's that appeared during our
                # boot: never stop it without asking.
                what = (f"chip {t} that the {name} container {cid} opened is leased by "
                        f"{r.holder}.")
                text = (f"{name} started, but {what} The container may be someone else's "
                        f"server. {_describe(server)} Stop it?")
                if not self._confirm("stop_external", text):
                    return self._keep(entry, run, "chips_held",
                                      f"{name} is not usable here: {what} It was left running "
                                      "and not recorded." + self._alternative(entry),
                                      container=cid)
                return self._abort(entry, registry, run, "chips_held",
                                   f"{name} could not be leased: {what}"
                                   + self._alternative(entry), confirmed=True)
            else:
                return self._abort(entry, registry, run, "failed",
                                   f"{name} could not be leased: gozer could not lease chip "
                                   f"{t} it opened.")
        if own_refused:
            # Our own lease (possibly an older one of this model) holds a chip:
            # keep its id with this server so it is not orphaned.
            lease = self._gozer.find_lease(who)
            if lease is not None:
                if lease.id not in run.lease_ids:
                    run.lease_ids.append(lease.id)
            else:
                log.warning("adopt for %s was refused by our own lease, but find_lease "
                            "found none", entry.key)
        if run.lease_ids:
            say(f"Leased the chips {name} opened ({', '.join(run.lease_ids)}).")
        return None

    def _finish(self, entry, run: _Run, server: RunningServer) -> StartOutcome:
        ids = list(run.lease_ids)
        # An older record of this key would be replaced by name: keep its leases.
        # Never from an unreapable record: its lease may sit under a zombie
        # container and must not follow the new one.
        for r in self._records.load():
            if r.get("name") == entry.key and not r.get("unreapable"):
                for lid in _lease_ids(r.get("lease_id")):
                    if lid not in ids:
                        ids.append(lid)
        lease = ",".join(ids) or None
        self._add_record(entry.key, server.port, _short(server.container_id), lease,
                         replace_same_key=True)
        return StartOutcome("started", server.url,
                            f"{entry.name} started at {server.url}.", lease)

    @staticmethod
    def _free_name(key: str, names: set, tag: str) -> str:
        n = 1
        while f"{key}#{tag}-{n}" in names:
            n += 1
        return f"{key}#{tag}-{n}"

    def _add_record(self, key: str, port, cid, leases, *, unreapable: bool = False,
                    replace_same_key: bool = False) -> str:
        """Write a record without losing another one's leases. Returns its name.

        * An unreapable record always gets its own name ``<key>#unreapable-<n>``,
          so nothing replaces it by name.
        * An older unreapable record named exactly ``key`` (written before
          that rule) is first copied to such a name, then the new record goes in.
        * A reapable record replaces an older same-key record only with
          ``replace_same_key`` (``_finish``, which merged its leases first);
          otherwise it gets ``<key>#kept-<n>``, and with no container id it
          is unreapable (a reap by endpoint would never match that name).
        """
        recs = self._records.load()
        names = {str(r.get("name")) for r in recs}
        old = next((r for r in recs if r.get("name") == key), None)
        if old is not None and old.get("unreapable"):
            moved = self._free_name(key, names, "unreapable")
            self._records.add(moved, old.get("port"), old.get("container_id"),
                              old.get("lease_id"), old.get("started_at"), unreapable=True,
                              note=old.get("note"))
            self._records.remove(key)
            names.add(moved)
            names.discard(key)
            old = None
        if unreapable:
            name = self._free_name(key, names, "unreapable")
        elif old is not None and not replace_same_key:
            if cid is None:
                unreapable = True
                name = self._free_name(key, names, "unreapable")
            else:
                name = self._free_name(key, names, "kept")
        else:
            name = key
        self._records.add(name, port, cid, leases, time.time(), unreapable=unreapable)
        return name

    def _keep(self, entry, run: _Run, kind: str, message: str,
              lines: Sequence[str] = (), container: str | None = None) -> StartOutcome:
        """Stop nothing and release nothing: this start's leases are kept in an
        unreapable record and named in the message for a manual release."""
        note = ""
        if run.lease_ids:
            leases = ",".join(run.lease_ids)
            ids = " ".join(run.lease_ids)
            self._add_record(entry.key, None, None, leases, unreapable=True)
            where = f"container {container}" if container else "a container of this start"
            note = (f" This start's chip lease {leases} was kept, because releasing it would "
                    f"reset chips that a container may still use. Do not release {leases} "
                    f"while {where} is running; once it is gone, run `gozer release {ids}`.")
        return StartOutcome(kind, None, message + note + _tail(lines), None)

    def _on_leased_chips(self, row: RunningServer, run: _Run) -> bool:
        """True when ``row``'s container opened only chips this start leased
        (pinnable path; one docker inspect)."""
        if not run.pin_ids or not row.container_id:
            return False
        devs = self._docker_devices(row.container_id)
        opened = self._targets(devs) if devs is not None else None
        return bool(opened) and set(opened) <= set(run.pin_ids)

    def _abort(self, entry, registry, run: _Run, kind: str, message: str,
               lines: Sequence[str] = (), confirmed: bool = False) -> StartOutcome:
        """Undo a failed start: stop what this start launched, then release its
        leases. Rows that were listed before the launch are never touched; when
        what this start launched cannot be told apart, nothing is stopped and
        the leases are kept (``_keep``). A failed stop keeps the leases and
        records them."""
        server = run.server
        lines = tuple(lines)
        if not run.launched:
            self._release_all(run.lease_ids)
            if run.lease_ids:
                message += " Its chip lease was released (gozer resets the chips)."
            return StartOutcome(kind, None, message + _tail(lines), None)
        if server is None:
            new = self._new_rows(entry, run)
            if new is None or len(new) > 1 or (not new and run.snapshot):
                # Unknown view, several new rows, or a row of this model listed
                # before launch: a stop could hit a server this start did not
                # launch, and a release could reset chips under our own.
                why = ("the running servers could not be read" if new is None else
                       "several servers of it appeared" if len(new) > 1 else
                       f"a server of {entry.name} that this start did not launch is listed "
                       "(started elsewhere, possibly meanwhile)")
                return self._keep(entry, run, kind,
                                  message + f" Nothing was stopped: {why}; it was left alone.",
                                  lines)
            server = new[0] if new else None
        if server is not None and not confirmed and not (
                (server is run.server and run.server_proven)
                or self._on_leased_chips(server, run)):
            # Not proven ours: another party's server may have appeared during
            # this start. Never stop it without asking.
            cid = server.container_id or server.container or "unknown"
            text = (f"A server of {entry.name} appeared during this start and may not be "
                    f"ours: stop {server.name} (container {cid}, up "
                    f"{_uptime(server.uptime_s)})? {_describe(server)}")
            if not self._confirm("stop_external", text):
                return self._keep(entry, run, kind,
                                  message + f" Nothing was stopped: the {entry.name} server in "
                                  f"container {cid} may not be ours, so it was left running.",
                                  lines, container=cid)
        if server is not None:
            res = self._stop_impl(server, registry)
        elif entry.backend == "tt" and entry.tt_name:
            res = self._call(lambda: self._tt.stop([entry.tt_name]))
        elif entry.sm_key:
            res = self._call(lambda: self._boutique_result(self._boutique.stop(entry.sm_key)))
        else:
            res = TtResult(False, "no way known to stop it")
        if res.ok:
            self._release_all(run.lease_ids)
            message += " What was started has been stopped."
            if run.lease_ids:
                message += " Its chip lease was released (gozer resets the chips)."
            return StartOutcome(kind, None, message + _tail(lines), None)
        # The stop failed: keep the leases, record the server as well as it is known.
        lines = lines + tuple(res.lines)
        port = server.port if server else None
        cid = _short(server.container_id) if server else None
        if port is None and cid is None:
            again = self._new_rows(entry, run) or []
            if len(again) == 1:
                port, cid = again[0].port, _short(again[0].container_id)
        unreapable = port is None and cid is None
        leases = ",".join(run.lease_ids) or None
        name = self._add_record(entry.key, port, cid, leases, unreapable=unreapable)
        unreapable = unreapable or name.startswith(f"{entry.key}#unreapable-")
        note = (f" Stopping it failed too ({res.reason}); it is recorded as started by "
                "tt-local-generator.")
        if leases:
            note += (f" Its chip lease {leases} was kept, because releasing it would reset "
                     "the chips under a container that may still run.")
        if unreapable:
            note += (" Its container could not be identified, so it will not be cleaned up "
                     "automatically: check `tt model ps`, stop it, then "
                     + (f"run `gozer release {' '.join(run.lease_ids)}`." if leases
                        else "check `gozer status`."))
        else:
            note += (" Stop in the Servers menu, or the next refresh once it is gone, can "
                     "clean it up.")
        return StartOutcome(kind, None, message + note + _tail(lines), None)

    # ------------------------------------------------------------------ stop

    @staticmethod
    def _call(fn) -> TtResult:
        """Run a stop call; an exception becomes a failed TtResult."""
        try:
            return fn()
        except Exception as exc:
            log.warning("stop call failed: %s", exc)
            return TtResult(False, str(exc))

    def _stop_impl(self, server: RunningServer, registry: dict) -> TtResult:
        """Stop by the rules in the wire notes: a catalog name or bundle id goes
        to ``tt model stop``; anything else to our script's --stop when the
        registry knows the key, else ``docker stop <container id>``."""
        tt_names = {e.tt_name for e in registry.values() if getattr(e, "tt_name", None)}
        if server.name in tt_names:
            return self._call(lambda: self._tt.stop([server.name]))
        e = registry.get(server.key)
        if e is not None and e.sm_key:
            return self._call(lambda: self._boutique_result(self._boutique.stop(e.sm_key)))
        if server.container_id:
            return self._call(lambda: self._docker_stop(server.container_id))
        return TtResult(False, f"tt-local-generator does not know how to stop {server.name}")

    def _record_for(self, server: RunningServer) -> dict | None:
        cid = _short(server.container_id)
        for r in self._records.load():
            if r.get("unreapable"):
                continue      # a zombie's lease: never released by a Stop
            rcid = r.get("container_id")
            if rcid:
                if cid and rcid == cid:
                    return r
            elif server.container_id is None and r.get("name") in (server.key, server.name) \
                    and r.get("port") == server.port:
                return r
        return None

    def stop(self, server: RunningServer) -> StopOutcome:
        """Stop a server. Blocks; call off the GTK thread. Never raises."""
        try:
            if not server.host_is_local:
                return StopOutcome("not_allowed",
                                   f"{server.name} runs on another machine ({server.url}); "
                                   "tt-local-generator only uses it and cannot stop it.")
            if server.owner != "app":
                text = (f"{server.name} was not started by tt-local-generator. "
                        f"{_describe(server)} Someone else may be using it. Stop it?")
                if not self._confirm("stop_external", text):
                    return StopOutcome("declined", f"{server.name} was left running.")
            return self._stop_server(server, self._registry())
        except Exception as exc:
            log.exception("stop of %s failed", getattr(server, "name", server))
            return StopOutcome("failed", f"Stopping {getattr(server, 'name', server)} "
                                         f"failed: {exc}.")

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
        """Stop (asking first if the app did not start it), then start. Never raises."""
        try:
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
        except Exception as exc:
            log.exception("restart failed")
            return StartOutcome("failed", None, f"Restart failed: {exc}.", None)

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
        """Release the leases of recorded servers that are gone. Never raises.

        ``live_container_ids=None`` means ``tt model ps`` failed: nothing is
        pruned. Ids must be the 12-char short ids ``ps`` reports. A record is
        dropped only when it looks stale on two consecutive calls (the one
        confirming poll); the first miss only marks it. A record marked
        ``unreapable`` is never dropped. Only leases held in records are
        released. Before a release, ``gozer.status()`` is read: when a chip of
        the lease shows a holder process (HELD, HELD-FOREIGN or a pid) the
        lease is kept and the record marked unreapable ("chips are in use by
        another process"); when the status cannot be read nothing is released
        and the record stays for the next cycle. Returns the released lease ids.
        """
        if live_container_ids is None:
            return []
        with self._reap_lock:
            try:
                return self._reap(live_container_ids, live_endpoints)
            except Exception:
                log.exception("lease reap failed")
                return []

    def _reap(self, live_container_ids, live_endpoints) -> list[str]:
        live_ids = set(live_container_ids)
        eps = None if live_endpoints is None else set(live_endpoints)
        stale_now: set[tuple] = set()
        stale_recs: dict[tuple, dict] = {}
        for r in self._records.load():
            if r.get("unreapable"):
                continue
            cid = r.get("container_id")
            if cid:
                stale = cid not in live_ids
            elif eps is None:
                stale = False
            else:
                stale = (r.get("name"), r.get("port")) not in eps
            if stale:
                ident = self._rec_ident(r)
                stale_now.add(ident)
                stale_recs[ident] = r
        first_miss = stale_now - self._suspects
        self._suspects = set(first_miss)
        shield = set(first_miss)
        # Second miss: before a lease is released (which resets its chips),
        # ask gozer whether a process holds them. A server stopped outside
        # ServerControl (the status bar's Stop, the Create switch) leaves its
        # record behind while a new server may already be booting on those
        # chips. Held: keep the lease, mark the record unreapable. Status
        # unreadable: release nothing this cycle (fail closed).
        status = None
        for ident in stale_now - first_miss:
            r = stale_recs[ident]
            if not _lease_ids(r.get("lease_id")):
                continue
            if status is None:
                status = self._gozer_status()
            verdict = self._lease_chips_in_use(r, status)
            if verdict is None:
                shield.add(ident)
                self._suspects.add(ident)     # still stale: check again next cycle
            elif verdict:
                shield.add(ident)
                self._suspects.discard(ident)
                self._records.add(r.get("name"), r.get("port"), r.get("container_id"),
                                  r.get("lease_id"), r.get("started_at"), unreapable=True,
                                  note=_CHIPS_IN_USE_NOTE)
                log.warning("lease %s of %s not released: %s", r.get("lease_id"),
                            r.get("name"), _CHIPS_IN_USE_NOTE)
        # Shield first-time misses (and the guarded records) from prune by
        # presenting them as live.
        for ident in shield:
            if ident[0] == "cid":
                live_ids.add(ident[1])
            elif eps is not None:
                eps.add((ident[1], ident[2]))
        released: list[str] = []
        for r in self._records.prune(live_ids, eps):
            for lid in _lease_ids(r.get("lease_id")):
                # No reset: the server is gone by definition, and a reset could
                # hit a server that is booting on those chips unleased (a
                # script server, anything started outside ServerControl) and
                # has not opened the device yet, which no guard can see.
                if self._gozer.release(lid, reset=False):
                    released.append(lid)
                else:
                    log.warning("could not release stale lease %s of %s; gozer reconcile "
                                "is the backstop", lid, r.get("name"))
        return released

    def _gozer_status(self) -> dict:
        try:
            st = self._gozer.status()
        except Exception:
            log.exception("gozer status failed")
            return {}
        return st if isinstance(st, dict) else {}

    def _lease_chips_in_use(self, rec: dict, status: dict) -> bool | None:
        """True when a chip of this record's lease has a holder process, False
        when none does, None when gozer's status cannot be read (fail closed).

        ``gozer status`` does not print lease ids; a lease's chips are the rows
        whose ``who`` is ours for this model (``who_for(<key>)``). No such row
        means gozer no longer has the lease: releasing it resets nothing."""
        chips = status.get("chips") if isinstance(status, dict) else None
        if not isinstance(chips, list) or not chips:
            return None
        who = self.who_for(str(rec.get("name") or "").split("#", 1)[0])
        for row in chips:
            if not isinstance(row, dict) or row.get("who") != who:
                continue
            if str(row.get("state") or "").upper() in _HELD_STATES:
                return True
            if row.get("pids_holding") or row.get("pids"):
                return True
        return False

    def forget(self, key: str) -> str:
        """Drop the app's record for ``key`` after the server was stopped by a
        path outside ServerControl. Its lease is released only when gozer shows
        no process on the lease's chips; when they are held the record is kept
        and marked unreapable; when gozer cannot be read the record is kept for
        the next reap. Blocks (gozer); call off the GTK thread. Never raises.
        Returns "none", "released", "kept" or "error".

        The release runs with ``reset=False`` (the server is stopped; a reset
        could hit another server booting unleased on those chips). It takes the
        reap lock, so a concurrent reap cannot write back a record that this
        call has just removed. A record already marked unreapable is released
        too when its chips read free: forget is only called right after a stop
        of that key, and gozer itself refuses a release while a device is open.
        """
        with self._reap_lock:
            return self._forget_locked(key)

    def _forget_locked(self, key: str) -> str:
        try:
            rec = next((r for r in self._records.load() if r.get("name") == key), None)
            if rec is None:
                return "none"
            ids = _lease_ids(rec.get("lease_id"))
            if not ids:
                self._records.remove(key)
                return "released"
            verdict = self._lease_chips_in_use(rec, self._gozer_status())
            if verdict is None:
                log.warning("forget %s: gozer status unreadable; record kept", key)
                return "kept"
            if verdict:
                self._records.add(rec.get("name"), rec.get("port"), rec.get("container_id"),
                                  rec.get("lease_id"), rec.get("started_at"),
                                  unreapable=True, note=_CHIPS_IN_USE_NOTE)
                log.warning("forget %s: %s; lease kept", key, _CHIPS_IN_USE_NOTE)
                return "kept"
            for lid in ids:
                if not self._gozer.release(lid, reset=False):
                    log.warning("forget %s: release of lease %s failed", key, lid)
            self._records.remove(key)
            return "released"
        except Exception:
            log.exception("forget %s failed", key)
            return "error"
