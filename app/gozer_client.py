"""Thin, GUI-free wrapper over the ``gozer`` chip-lease CLI (``~/code/tt-gozer``).

Rules this module keeps (see docs/superpowers/specs/2026-10-28-tt-cli-server-control-design.md):

* Standard library only. No GTK, no requests, no import of other app modules.
* No shell invocation. Every argument is its own argv element.
* Every subprocess has a timeout and ``stdin=DEVNULL``, and runs in its own
  session so a timeout or cancel kills the whole process group.
* No public function raises into its caller. Failures return None / False / an
  empty value, with the reason logged.
* ``status()``, ``holders()``, ``free_chips()`` and ``available()`` only run
  ``gozer status`` and ``gozer --version``. They never change lease state.

How gozer leases behave (wire notes, item 3):

* A bare ``gozer acquire`` is *detached*: it prints the grant and exits, and the
  lease is CLAIMED. gozer reaps it as STALE after 900 s unless some process has
  a visible fd open on the chip. ``adopt`` is the call whose lifetime follows
  the process that really holds the chip.
* ``release`` RESETS the chips. Do not call it while another agent might be
  using them; ``release`` only ever takes a lease id this module handed out.
* ``gozer acquire --json`` prints ``{"granted": true, "lease_id", "chips": [BDF..],
  "dev_indices": [..]}`` on success (exit 0) or ``{"granted": false, "queued":
  true, "ticket"}`` when queued (exit 10). Chip ids in this module are BDF
  strings such as ``0000:01:00.0``; ``holders()`` also accepts a device index
  (``"1"``) or a board id.
* ``gozer adopt --json`` reports success as ``{"adopted": true, "lease_id", "pids"}``
  with no chip list (``adopt()`` fills it from ``status()``) and failure as a bare ``{"adopted": false}`` with no
  reason, so ``adopt()`` asks ``status()`` who holds the target to tell
  "already leased" (``AdoptRefused``) from "nothing holds it" (None).
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Sequence

log = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"^gozer\b", re.I)
_VERSION_TIMEOUT_S = 10.0
# Timeout for every gozer call except wait(). Tests lower it.
_CALL_TIMEOUT_S = 30.0
# Release resets chips, which can take a while.
_RELEASE_TIMEOUT_S = 180.0
_POLL_S = 0.1


@dataclass(frozen=True)
class Lease:
    id: str
    chips: tuple[str, ...]


@dataclass(frozen=True)
class Queued:
    ticket: str


@dataclass(frozen=True)
class AdoptRefused:
    """``adopt`` failed because the chips are already leased to ``holder``."""
    holder: str


# --------------------------------------------------------------------------
# Subprocess helpers
# --------------------------------------------------------------------------

def _kill_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.send_signal(sig)
        except Exception:
            pass


def _run(argv: Sequence[str], timeout: float,
         cancel: Callable[[], bool] | None = None) -> tuple[int | None, str, str]:
    """Run argv to completion. Returns (returncode, stdout, stderr).

    returncode is None when the program could not start, timed out or was
    cancelled; stderr then holds the reason. Never raises.
    """
    try:
        proc = subprocess.Popen(
            list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, errors="replace",
            start_new_session=True)
    except (OSError, ValueError) as exc:
        return None, "", f"could not start {argv[0]}: {exc}"
    deadline = time.monotonic() + timeout
    try:
        while True:
            step = _POLL_S if cancel is not None else min(1.0, max(0.01, deadline - time.monotonic()))
            try:
                out, err = proc.communicate(timeout=step)
                return proc.returncode, out or "", err or ""
            except subprocess.TimeoutExpired:
                pass
            reason = ""
            if cancel is not None:
                try:
                    if cancel():
                        reason = "cancelled"
                except Exception:
                    pass
            if not reason and time.monotonic() >= deadline:
                reason = f"timed out after {timeout:g}s"
            if reason:
                _kill_group(proc, signal.SIGKILL)
                try:
                    proc.communicate(timeout=2)
                except Exception:
                    pass
                return None, "", reason
    except Exception as exc:  # pragma: no cover - defensive
        _kill_group(proc, signal.SIGKILL)
        return None, "", f"failed: {exc}"


def _find() -> str | None:
    """First executable named ``gozer`` on PATH whose ``--version`` says gozer."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        p = os.path.join(d, "gozer")
        if not (os.path.isfile(p) and os.access(p, os.X_OK)):
            continue
        rc, out, _ = _run([p, "--version"], _VERSION_TIMEOUT_S)
        if rc == 0 and _VERSION_RE.match((out or "").strip()):
            return p
        log.warning("%s is not the gozer CLI (version said %r)", p, (out or "").strip()[:60])
    return None


def available() -> bool:
    """True when a real ``gozer`` is on PATH."""
    try:
        return _find() is not None
    except Exception:
        return False


def _json_call(args: Sequence[str], timeout: float | None = None,
               cancel: Callable[[], bool] | None = None) -> tuple[dict | None, int | None]:
    """Run ``gozer <args> --json``. Returns (parsed dict or None, exit code).

    gozer prints its JSON on stdout for refusals too (queued is exit 10), so the
    JSON is parsed whatever the exit code is.
    """
    try:
        g = _find()
        if g is None:
            log.warning("gozer not found on PATH")
            return None, None
        rc, out, err = _run([g, *args, "--json"],
                            _CALL_TIMEOUT_S if timeout is None else timeout, cancel)
        if rc is None:
            log.warning("gozer %s: %s", args[0], err)
            return None, None
        try:
            data = json.loads(out)
        except (ValueError, TypeError):
            tail = (err or out).strip().splitlines()[-1:] or ["no output"]
            log.warning("gozer %s exited %s without JSON: %s", args[0], rc, tail[0][:200])
            return None, rc
        if not isinstance(data, dict):
            log.warning("gozer %s returned %s, expected an object", args[0], type(data).__name__)
            return None, rc
        return data, rc
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("gozer %s failed: %s", args[0] if args else "?", exc)
        return None, None


# --------------------------------------------------------------------------
# Read-only: status
# --------------------------------------------------------------------------

def status() -> dict:
    """``gozer status --json`` as a dict, or ``{}`` on any failure. Read-only."""
    data, _rc = _json_call(["status"])
    return data or {}


def _chips_of(st: dict) -> list[dict]:
    rows = st.get("chips")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _matches(row: dict, ident: str) -> bool:
    return ident in (str(row.get("bdf")), str(row.get("dev_index")), str(row.get("board")))


def holders(chips: Sequence[str]) -> list[str]:
    """The ``who`` strings of everyone holding any of ``chips`` (BDF, device
    index or board id), in chip order, without duplicates. Read-only."""
    wanted = [str(c) for c in chips]
    out: list[str] = []
    for row in _chips_of(status()):
        who = row.get("who")
        if who and row.get("state") != "FREE" and any(_matches(row, w) for w in wanted):
            if who not in out:
                out.append(str(who))
    return out


def free_chips() -> list[str]:
    """BDFs of chips whose state is FREE. Read-only."""
    return [str(r["bdf"]) for r in _chips_of(status())
            if r.get("state") == "FREE" and r.get("bdf")]


# --------------------------------------------------------------------------
# Mutating: acquire / wait / release / adopt
# --------------------------------------------------------------------------

def _lease_from(data: dict) -> Lease | None:
    lid = data.get("lease_id")
    chips = data.get("chips")
    if data.get("granted") is not True and data.get("adopted") is not True:
        return None
    if not lid:
        return None
    return Lease(str(lid), tuple(str(c) for c in chips) if isinstance(chips, list) else ())


def acquire(chips: int, who: str, reason: str, expect: str = "2h",
            queue: bool = True) -> Lease | Queued | None:
    """Ask for ``chips`` chips. A Lease when granted, Queued(ticket) when the
    chips are busy and ``queue`` is True, None on refusal or error.

    The lease is detached: gozer reaps it at 900 s if nothing holds the chip
    open. Use ``adopt`` once the server is up so the lease follows its process.
    """
    args = ["acquire", "--chips", str(int(chips)), "--who", who, "--reason", reason,
            "--expect", expect]
    if not queue:
        args.append("--no-queue")
    data, _rc = _json_call(args)
    if data is None:
        return None
    lease = _lease_from(data)
    if lease is not None:
        return lease
    if data.get("queued") and data.get("ticket"):
        return Queued(str(data["ticket"]))
    log.info("gozer acquire not granted: %s", data.get("error") or data)
    return None


def wait(ticket: str, timeout: float,
         cancel: Callable[[], bool] | None = None) -> Lease | None:
    """Block on ``gozer wait <ticket>`` for up to ``timeout`` seconds.

    Returns the Lease when granted; None on timeout, cancel (``cancel()`` is
    checked every 0.1 s and kills the child process group), or error. A
    cancelled wait leaves the ticket queued; cancel it with ``gozer cancel``.
    """
    secs = max(1, int(timeout))
    data, _rc = _json_call(["wait", str(ticket), "--timeout", f"{secs}s"],
                           timeout=secs + 15.0, cancel=cancel)
    return _lease_from(data) if data else None


def release(lease_id: str) -> bool:
    """``gozer release <lease>``. True on success.

    WARNING: the real call resets the lease's chips.
    """
    data, _rc = _json_call(["release", str(lease_id)], timeout=_RELEASE_TIMEOUT_S)
    return bool(data and data.get("released") is True)


def adopt(target: str, who: str, reason: str) -> Lease | AdoptRefused | None:
    """Wrap a lease around a process that already holds ``target`` (BDF, device
    index or board). Returns the Lease; ``AdoptRefused(holder)`` when somebody
    else already has a lease on it; None for any other failure (nothing holds
    the chip, unknown chip, gozer error)."""
    data, _rc = _json_call(["adopt", str(target), "--who", who, "--reason", reason])
    if data is None:
        return None
    lease = _lease_from(data)
    if lease is not None:
        if not lease.chips:
            # adopt's JSON has no chip list; read it back from status.
            tgt = str(target)
            chips = tuple(str(r["bdf"]) for r in _chips_of(status())
                          if r.get("bdf") and _matches(r, tgt))
            lease = Lease(lease.id, chips or (tgt,))
        return lease
    # JSON mode drops gozer's reason, so look at who holds it now.
    held = holders([str(target)])
    if held:
        return AdoptRefused(held[0])
    log.info("gozer adopt %s failed: %s", target, data)
    return None
