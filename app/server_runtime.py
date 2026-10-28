"""Real-dependency wiring for ServerControl, shared by ``tt-ctl`` and the GUI.

GTK-free: standard library plus sibling GTK-free modules. ``server_control``
takes every dependency by injection; this module is the one place that fills
them in with the real ``tt``, ``gozer``, ``docker`` and boutique-script
helpers, so ``tt-ctl`` (Task 7) and the Servers menu (Task 8) cannot drift.

* ``build_server_control(confirm_fn, ...)``: a ready ``ServerControl``.
* ``snapshot()``: ``(registry, running_servers, ps_ok)``, the merged view the
  ``servers --all`` command and the GUI list show. ``snapshot_detail()`` also
  returns the reason ``tt model ps`` failed.

Known limits, on purpose:

* Discovery inputs the GUI has (the artgen port sweep, music) are not
  gathered here: they need GTK-side state or are slow sweeps. ``merge`` gets
  an empty list for them, so a model served by a process that is neither a
  ``tt model`` container nor one of our scripts is not listed. Saved remote
  entries are merged only when the caller passes ``saved=`` (the GUI does).
* When ``tt model ps`` fails or ``tt`` is missing the view holds the script
  servers only. A ``tt``-started container is then invisible to the running
  check, so a start of the same model in that state is not caught as a duplicate.
* ``reset_fn`` is ``pipeline_engine._tt_smi_reset``, imported lazily. That
  module imports no GTK at the time of writing; if the import fails for any
  reason ``reset_fn`` is None and a switch that needs a reset skips it (the
  start then reports it).
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

import gozer_client
import server_manager
import server_registry
import tt_backend
from server_control import ServerControl
from server_view import RunningServer, ServerRecords, merge

log = logging.getLogger(__name__)

# status_of is asked many times per start (the switch plan walks every
# candidate); one snapshot (a `tt model ps` plus a health probe per script
# server) is reused for this long.
_STATUS_TTL_S = 2.0


class RecordingBoutique:
    """The ``server_manager`` start/stop calls, remembering each key's results.

    ServerControl turns a script's results into one ``TtResult``; ``tt-ctl``
    still wants the script's own stdout/stderr tail and exit code for its old
    per-service lines, so the last results per key are kept here.
    ``blocking`` maps ``tt-ctl --blocking``: the script runs without ``--gui``.
    """

    def __init__(self, blocking: bool = False) -> None:
        self.blocking = blocking
        self.last_start: dict[str, list] = {}
        self.last_stop: dict[str, list] = {}

    def start(self, key: str, gui: bool = True, **kw):
        # Looked up on the module at call time so tests can patch server_manager.
        results = server_manager.start(key, gui=gui and not self.blocking, **kw)
        self.last_start[key] = list(results or [])
        return results

    def stop(self, key: str, **kw):
        results = server_manager.stop(key, **kw)
        self.last_stop[key] = list(results or [])
        return results


def _script_health() -> dict[str, bool]:
    try:
        return server_manager.status_all(timeout=2.0)
    except Exception as exc:
        log.warning("script health check failed: %s", exc)
        return {}


def snapshot_detail(records_path: Path | None = None,
                    script_health: dict[str, bool] | None = None,
                    registry: dict | None = None,
                    saved: list | None = None):
    """``(registry, servers, ps_ok, ps_reason)``. Never raises.

    ``saved``: remembered network servers (dicts with name/url, as the GUI's
    Connect dialog stores them, minus any token) merged as ``saved`` rows.

    ``servers`` is ``server_view.merge`` of ``tt model ps``, the script health
    checks and the ownership records. ``ps_ok`` is False when ``tt`` is missing
    or ``ps`` failed (``ps_reason`` says why); ``servers`` then holds script
    servers only.
    """
    try:
        tt_rows, reason = tt_backend.ps()
    except Exception as exc:  # ps() should not raise
        tt_rows, reason = [], f"tt model ps failed: {exc}"
    if script_health is None:
        script_health = _script_health()
    try:
        if registry is None:
            registry = server_registry.build_registry()
    except Exception as exc:
        log.warning("model registry unavailable: %s", exc)
        registry = {}
    try:
        records = ServerRecords(records_path).load()
        servers = merge(tt_rows, script_health, [], list(saved or []), records, registry)
    except Exception as exc:
        log.warning("could not merge the running servers: %s", exc)
        servers = []
    return registry, servers, not reason, reason


def snapshot(records_path: Path | None = None, script_health: dict[str, bool] | None = None):
    """``(registry, running_servers, ps_ok)``; see ``snapshot_detail``."""
    registry, servers, ok, _reason = snapshot_detail(records_path, script_health)
    return registry, servers, ok


def _lazy_reset_fn() -> Callable[[], object] | None:
    try:
        import pipeline_engine
        return pipeline_engine._tt_smi_reset
    except Exception as exc:
        log.warning("chip reset unavailable (%s); a switch that needs one skips it", exc)
        return None


def build_server_control(confirm_fn: Callable[[str, str], object], *,
                         records_path: Path | None = None,
                         blocking: bool = False,
                         boutique=None, **control_kw) -> ServerControl:
    """A ``ServerControl`` on the real ``tt``, ``gozer``, docker and scripts.

    ``confirm_fn(kind, text)`` must return the literal ``True`` for yes (see
    the ServerControl docstring). ``boutique`` lets the caller pass its own
    ``RecordingBoutique``; ``control_kw`` reaches ``ServerControl`` (clock,
    sleep, poll_s, timeouts) so tests can run without real waiting.
    """
    boutique = boutique if boutique is not None else RecordingBoutique(blocking)
    records = ServerRecords(records_path)
    cache: dict = {"t": None, "reg": None, "servers": None}
    lock = threading.Lock()

    def fresh() -> tuple[dict, list[RunningServer]]:
        registry, servers, _ok, _why = snapshot_detail(records_path)
        with lock:
            cache.update(t=time.monotonic(), reg=registry, servers=servers)
        return registry, servers

    def registry_fn() -> dict:
        return server_registry.build_registry()

    def view_fn() -> list[RunningServer]:
        return fresh()[1]

    def status_of(key: str) -> str:
        with lock:
            t, servers = cache["t"], cache["servers"]
        if t is None or time.monotonic() - t > _STATUS_TTL_S:
            servers = fresh()[1]
        rows = [s for s in servers if s.key == key]
        if any(s.health in ("healthy", "ready", "ok") for s in rows):
            return "ready"
        return "starting" if rows else "off"

    return ServerControl(
        registry_fn=registry_fn, view_fn=view_fn, tt=tt_backend, gozer=gozer_client,
        records=records, boutique=boutique, status_of=status_of, confirm_fn=confirm_fn,
        reset_fn=_lazy_reset_fn(), **control_kw)
