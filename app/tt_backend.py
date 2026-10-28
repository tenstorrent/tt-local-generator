"""Thin, GUI-free wrapper over the official ``tt`` CLI (``tenstorrent/tt-cli``).

Rules this module keeps (see docs/superpowers/specs/2026-10-28-tt-cli-server-control-design.md):

* Standard library only. No GTK, no requests.
* No shell invocation. Every argument is its own argv element, so a model name
  containing spaces or ``;`` stays one argument.
* Every subprocess has a timeout and ``stdin=DEVNULL``.
* No public function raises into its caller. Failures come back as a result
  object (``TtResult``) or an empty value plus a reason string.
* ``find_tt()`` only accepts a binary whose ``--version`` prints ``tt <digit>...``.
  Another program called ``tt`` earlier on PATH is skipped, with a warning.
* Nothing here starts a server unless ``serve()`` is called. ``plan_serve()``
  always passes ``--dry-run --offline``.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

log = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"^tt \d")
# How long a terminated serve gets to exit before it is killed.
_KILL_GRACE_S = 3.0
_VERSION_TIMEOUT_S = 10.0
_DEFAULT_TIMEOUT_S = 30.0


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TtServer:
    """One row of ``tt --json model ps`` -> ``served``. Missing keys become None."""
    name: str
    backend: str
    container: str | None
    container_id: str | None
    image: str | None
    port: int | None
    base_url: str | None
    status: str
    health: str
    started_at: str | None
    uptime_s: int | None
    profile: str | None
    served_id: str | None


@dataclass(frozen=True)
class TtModel:
    """One row of ``tt --json model list``.

    Container bundles carry ``installed``. Catalog rows have no ``installed``
    key at all; ``installed`` is then False, which means "not known to be
    installed", not "known absent". ``kind`` is None for catalog rows.
    """
    name: str
    kind: str | None
    engines: tuple[str, ...]
    hardware: tuple[str, ...]
    installed: bool
    backends: tuple[str, ...]


@dataclass(frozen=True)
class ServePlan:
    """What ``tt serve <model>`` would run, read from ``--dry-run``."""
    model: str
    backend: str | None
    command: tuple[str, ...]
    device: str | None
    port: int | None
    chips: int | None


@dataclass(frozen=True)
class TtResult:
    ok: bool
    reason: str
    lines: tuple[str, ...] = ()


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


def _run(argv: Sequence[str], timeout: float = _DEFAULT_TIMEOUT_S,
         env: dict | None = None) -> tuple[int | None, str, str]:
    """Run argv to completion. Returns (returncode, stdout, stderr).

    returncode is None when the program could not start or timed out; stderr
    then holds the reason. Never raises.
    """
    try:
        proc = subprocess.Popen(
            list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, errors="replace",
            start_new_session=True, env=env)
    except (OSError, ValueError) as exc:
        return None, "", f"could not start {argv[0]}: {exc}"
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc, signal.SIGKILL)
        try:
            proc.communicate(timeout=2)
        except Exception:
            pass
        return None, "", f"timed out after {timeout:g}s"
    except Exception as exc:  # pragma: no cover - defensive
        _kill_group(proc, signal.SIGKILL)
        return None, "", f"failed: {exc}"
    return proc.returncode, out or "", err or ""


# --------------------------------------------------------------------------
# Locating the official CLI
# --------------------------------------------------------------------------

_tt_cache: Path | None = None
_tt_cached = False
_cache_lock = threading.Lock()
_catalog_cache: tuple[float, bool, list[TtModel]] | None = None


def reset_cache() -> None:
    """Forget the located binary and the cached catalog (for tests)."""
    global _tt_cache, _tt_cached, _catalog_cache
    with _cache_lock:
        _tt_cache, _tt_cached, _catalog_cache = None, False, None


def _path_candidates() -> list[str]:
    """Every executable named ``tt`` on PATH, in order (``which -a`` semantics)."""
    seen: list[str] = []
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        p = os.path.join(d, "tt")
        if os.path.isfile(p) and os.access(p, os.X_OK) and p not in seen:
            seen.append(p)
    return seen


def find_tt(which: Callable = shutil.which, run: Callable = _run) -> Path | None:
    """Return the official ``tt`` binary, or None. Cached until reset_cache()."""
    global _tt_cache, _tt_cached
    with _cache_lock:
        if _tt_cached:
            return _tt_cache
    cands = _path_candidates()
    if not cands:
        try:
            w = which("tt")
        except Exception:
            w = None
        if w:
            cands = [w]
    found: Path | None = None
    skipped: list[str] = []
    for c in cands:
        try:
            rc, out, _err = run([c, "--version"], _VERSION_TIMEOUT_S)
        except Exception:
            rc, out = None, ""
        if rc == 0 and _VERSION_RE.match((out or "").strip()):
            found = Path(c)
            break
        skipped.append(c)
    if found is not None and skipped:
        log.warning("tt on PATH is shadowed: %s is not the official tt CLI; using %s",
                    ", ".join(skipped), found)
    elif found is None and cands:
        log.warning("no official tt CLI found; rejected: %s", ", ".join(skipped))
    with _cache_lock:
        _tt_cache, _tt_cached = found, True
    return found


def _tt_json(args: Sequence[str], timeout: float) -> tuple[object | None, str]:
    """Run ``tt --json <args>`` and parse stdout. Returns (data, reason)."""
    tt = find_tt()
    if tt is None:
        return None, "the official tt CLI was not found on PATH"
    rc, out, err = _run([str(tt), "--json", *args], timeout)
    if rc is None:
        return None, f"tt {' '.join(args[:2])} {err}"
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        tail = (err or out).strip().splitlines()[-1:] or ["no output"]
        return None, f"tt exited {rc} without JSON: {tail[0][:200]}"
    if rc != 0:
        why = data.get("why") if isinstance(data, dict) else None
        return None, f"tt exited {rc}: {why or (err.strip().splitlines() or [''])[-1][:200]}"
    return data, ""


def _s(v) -> str | None:
    return None if v is None else str(v)


def _i(v) -> int | None:
    try:
        return None if v is None or isinstance(v, bool) else int(v)
    except (TypeError, ValueError):
        return None


def _tup(v) -> tuple[str, ...]:
    return tuple(str(x) for x in v) if isinstance(v, (list, tuple)) else ()


# --------------------------------------------------------------------------
# ps / catalog / plan
# --------------------------------------------------------------------------

def ps(timeout: float = 20.0) -> tuple[list[TtServer], str]:
    """Running servers as ``tt`` sees them. Returns (servers, reason-if-empty-by-failure)."""
    data, reason = _tt_json(["model", "ps"], timeout)
    if data is None:
        return [], reason
    if not isinstance(data, dict) or not isinstance(data.get("served"), list):
        return [], "this tt has no 'served' list in `model ps` (older tt?)"
    rows: list[TtServer] = []
    for r in data["served"]:
        if not isinstance(r, dict):
            continue
        rows.append(TtServer(
            name=str(r.get("name") or ""), backend=str(r.get("backend") or ""),
            container=_s(r.get("container")), container_id=_s(r.get("container_id")),
            image=_s(r.get("image")), port=_i(r.get("port")),
            base_url=_s(r.get("base_url")), status=str(r.get("status") or ""),
            health=str(r.get("health") or ""), started_at=_s(r.get("started_at")),
            uptime_s=_i(r.get("uptime_s")), profile=_s(r.get("profile")),
            served_id=_s(r.get("served_id"))))
    return rows, ""


def catalog(ttl: float = 300.0, offline: bool = True) -> list[TtModel]:
    """Models ``tt`` can serve. Cached for ``ttl`` seconds. [] on any failure."""
    global _catalog_cache
    now = time.monotonic()
    with _cache_lock:
        c = _catalog_cache
    if c and c[1] == offline and now - c[0] < ttl:
        return list(c[2])
    # --offline is a global flag of tt (`model list` has none of its own).
    args = (["--offline"] if offline else []) + ["model", "list"]
    tt = find_tt()
    if tt is None:
        return []
    rc, out, _err = _run([str(tt), "--json", *args], 60.0)
    try:
        data = json.loads(out) if rc is not None else None
    except (ValueError, TypeError):
        data = None
    rows = data.get("models") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    models = [TtModel(
        name=str(r.get("name") or ""), kind=_s(r.get("kind")),
        engines=_tup(r.get("engines")), hardware=_tup(r.get("hardware")),
        installed=bool(r.get("installed")), backends=_tup(r.get("backends")))
        for r in rows if isinstance(r, dict) and r.get("name")]
    with _cache_lock:
        _catalog_cache = (now, offline, models)
    return list(models)


def _serve_args(model: str, port: int | None, device: str | None) -> list[str]:
    a = ["serve", model]
    if port is not None:
        a += ["--port", str(int(port))]
    if device:
        a += ["--device", device]
    return a


def plan_serve(model: str, *, port: int | None = None,
               device: str | None = None) -> ServePlan | None:
    """What ``tt serve`` would do for ``model``. Never starts anything."""
    data, _reason = _tt_json(_serve_args(model, port, device) + ["--dry-run", "--offline"], 60.0)
    if not isinstance(data, dict):
        return None
    argv = data.get("argv")
    command = _tup(argv)
    bundle = data.get("bundle")
    if isinstance(bundle, dict):  # tt-model bundle shape
        backend = _s(data.get("backend"))
        dev = _s(bundle.get("default_profile"))
        chips = _i(bundle.get("device_count"))
        p = _i(data.get("port"))
    else:  # tt-inference-server catalog shape
        backend = "inference-server"
        dev = _s(data.get("device_requested"))
        chips = None
        p = _i(data.get("port")) or _i(data.get("default_port"))
    return ServePlan(model=str(data.get("model") or model), backend=backend,
                     command=command, device=dev, port=p, chips=chips)


# --------------------------------------------------------------------------
# serve / stop / logs
# --------------------------------------------------------------------------

def serve(model: str, *, port: int | None = None, device: str | None = None,
          detach: bool = False, on_line: Callable[[str], None] | None = None,
          cancel: Callable[[], bool] | None = None, env: dict | None = None,
          timeout: float = 3600.0) -> TtResult:
    """Run ``tt serve`` and stream its output to ``on_line``.

    ``tt serve`` returns once the endpoint answers, so ``detach`` has no flag of
    its own here; it is accepted for interface stability and ignored (a bundle's
    argv already detaches). ``cancel`` is polled; when it returns True the
    process group gets SIGTERM, then SIGKILL after a short grace.
    """
    tt = find_tt()
    if tt is None:
        return TtResult(False, "the official tt CLI was not found on PATH")
    argv = [str(tt), "serve", model] + _serve_args(model, port, device)[2:]
    full_env = None if env is None else {**os.environ, **env}
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1,
            start_new_session=True, env=full_env)
    except (OSError, ValueError) as exc:
        return TtResult(False, f"could not start tt: {exc}")

    q: "queue.Queue[str | None]" = queue.Queue()

    def _pump() -> None:
        try:
            for ln in proc.stdout:  # type: ignore[union-attr]
                q.put(ln.rstrip("\r\n"))
        except Exception:
            pass
        finally:
            q.put(None)

    threading.Thread(target=_pump, daemon=True).start()
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    stopped = ""
    eof = False
    while not eof:
        try:
            ln = q.get(timeout=0.1)
        except queue.Empty:
            ln = ""  # no data this tick
            if cancel is not None and not stopped:
                try:
                    if cancel():
                        stopped = "cancelled"
                except Exception:
                    pass
            if not stopped and time.monotonic() > deadline:
                stopped = f"timed out after {timeout:g}s"
            if stopped:
                _terminate(proc)
            continue
        if ln is None:
            eof = True
            break
        lines.append(ln)
        if on_line is not None:
            try:
                on_line(ln)
            except Exception:
                log.debug("on_line callback raised", exc_info=True)
    try:
        proc.wait(timeout=_KILL_GRACE_S)
    except subprocess.TimeoutExpired:
        _kill_group(proc, signal.SIGKILL)
        proc.wait()
    rc = proc.returncode
    if stopped:
        return TtResult(False, stopped, tuple(lines))
    if rc == 0:
        return TtResult(True, "", tuple(lines))
    tail = " | ".join(x for x in lines[-5:] if x.strip())
    return TtResult(False, f"tt serve exited {rc}: {tail}" if tail else f"tt serve exited {rc}",
                    tuple(lines))


def _terminate(proc: subprocess.Popen) -> None:
    """SIGTERM the group, SIGKILL if it is still alive after the grace period."""
    if proc.poll() is not None:
        return
    _kill_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=_KILL_GRACE_S)
    except subprocess.TimeoutExpired:
        _kill_group(proc, signal.SIGKILL)


def stop(names: Sequence[str]) -> TtResult:
    """``tt model stop <name>...``; each name is its own argv element."""
    names = [str(n) for n in names]
    if not names:
        return TtResult(False, "no server names given")
    tt = find_tt()
    if tt is None:
        return TtResult(False, "the official tt CLI was not found on PATH")
    rc, out, err = _run([str(tt), "model", "stop", *names], 300.0)
    lines = tuple((out + ("\n" + err if err else "")).splitlines())
    if rc is None:
        return TtResult(False, f"tt model stop {err}", lines)
    if rc != 0:
        tail = [x for x in lines if x.strip()][-1:] or [""]
        return TtResult(False, f"tt model stop exited {rc}: {tail[0][:200]}", lines)
    return TtResult(True, "", lines)


def logs(name: str, lines: int = 200) -> str:
    """The last ``lines`` lines of a served model's output, or "" on failure."""
    tt = find_tt()
    if tt is None:
        return ""
    rc, out, err = _run([str(tt), "model", "logs", str(name), "--tail", str(int(lines))], 30.0)
    if rc is None:
        return ""
    return out if out else err
