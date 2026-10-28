"""Merged view of every running model server, plus ownership records.

GUI-free, no subprocess: ``merge`` combines data its caller already gathered
(``tt model ps`` rows, script health checks, discovery results, saved
entries). Standard library plus sibling GTK-free modules only.

Sources, in the order they are merged (the first one found supplies the
container fields):

* ``tt``: a row of ``tt model ps``. Carries container, container id, lowest
  published host port and the served id. It has NO runner key (wire notes,
  item 2).
* ``script``: a ``server_manager`` health check that passed. The only place a
  runner key comes from.
* ``discovered``: ``(url, model_id)`` found by the artgen port sweep.
* ``saved``: a remembered entry (dict with name/url/port/served_id/container_id).

Matching (one server seen by two sources becomes one row): same port, and at
least one of served id, container id or name/repo name equal. A runner key
from the script check stands in for the served id through the registry. Two
entries on one port whose served ids are both known and differ are two
different models on a shared port: never merged, and ``conflicts`` reports them.

Ownership: ``app`` when a ServerRecords record matches a live server (same
container id, or same name and port when the record has no container id);
``external`` when local and unrecorded (a record whose container is gone does
not count); ``unknown`` for a network server.
"""
from __future__ import annotations

import contextlib
import fcntl
import functools
import json
import logging
import os
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_DEFAULT_RECORDS = Path.home() / ".local" / "share" / "tt-local-generator" / "servers.json"


@dataclass(frozen=True)
class RunningServer:
    key: str
    name: str
    source: str                 # "tt" | "script" | "discovered" | "saved"
    url: str | None
    host_is_local: bool
    port: int | None
    health: str
    uptime_s: int | None
    container: str | None
    container_id: str | None
    served_id: str | None
    owner: str                  # "app" | "external" | "unknown"


# --------------------------------------------------------------------------
# Ownership records
# --------------------------------------------------------------------------

class ServerRecords:
    """Servers this app started: name, port, container id, lease id, start time."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else _DEFAULT_RECORDS

    def load(self) -> list[dict]:
        """Saved records. A missing, unreadable or corrupt file gives []."""
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [r for r in data if isinstance(r, dict)]

    @contextlib.contextmanager
    def _locked(self):
        """Exclusive flock on a sidecar file around a read-modify-write, so two
        instances (or processes) cannot lose each other's update."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_name(self.path.name + ".lock")
        with open(lock, "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def _save(self, records: list[dict]) -> None:
        """Write a temp file in the same directory, fsync it, then os.replace it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".servers.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(records, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def add(self, name, port, container_id, lease_id, started_at,
            unreapable: bool = False) -> None:
        """Record a server (replaces any record with the same name).

        ``lease_id`` may hold several ids separated by commas (one adopted
        lease per board); readers must split it.

        ``unreapable=True`` marks a record whose server could not be identified
        (no container id, no port: a start whose stop also failed). ``prune``
        never treats such a record as stale, because releasing its lease resets
        the chips under a container that may still run; ``gozer reconcile`` or
        the user releases it. The key is written only when True.
        """
        with self._locked():
            recs = [r for r in self.load() if r.get("name") != name]
            rec = {"name": name, "port": port, "container_id": container_id,
                   "lease_id": lease_id, "started_at": started_at}
            if unreapable:
                rec["unreapable"] = True
            recs.append(rec)
            self._save(recs)

    def remove(self, name) -> None:
        with self._locked():
            recs = self.load()
            kept = [r for r in recs if r.get("name") != name]
            if len(kept) != len(recs):
                self._save(kept)

    def prune(self, live_container_ids: set[str] | None,
              live_endpoints: set[tuple] | None = None) -> list[dict]:
        """Drop stale records; return what was dropped so the caller can reap
        their leases.

        ``live_container_ids=None`` means "no information" (callers pass None
        when ``tt model ps`` failed): nothing is pruned and [] is returned. An
        empty set means "ps worked and nothing runs". Records must store the
        12-char short container id exactly as ``tt model ps`` reports it. The
        two-poll confirmation (a record must look stale on two consecutive
        polls) belongs to the caller (Task 6), not here.

        A record marked ``unreapable`` is never dropped. A record with a
        container id is dropped when that id is not live. A
        record with no container id (script-started) is dropped when
        ``(name, port)`` is not in ``live_endpoints``; with
        ``live_endpoints=None`` such records are kept.
        """
        if live_container_ids is None:
            return []
        with self._locked():
            recs = self.load()
            kept, dropped = [], []
            for r in recs:
                cid = r.get("container_id")
                if r.get("unreapable"):
                    stale = False
                elif cid:
                    stale = cid not in live_container_ids
                elif live_endpoints is None:
                    stale = False
                else:
                    stale = (r.get("name"), r.get("port")) not in live_endpoints
                (dropped if stale else kept).append(r)
            if dropped:
                self._save(kept)
            return dropped


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _machine_addresses() -> frozenset:
    """This machine's own names and addresses (cached once per process)."""
    out: set[str] = set()
    try:
        name = socket.gethostname()
        out.add(name.lower())
        try:
            out.add(socket.getfqdn().lower())
        except Exception:
            pass
        out.update(socket.gethostbyname_ex(name)[2])
    except Exception:
        pass
    return frozenset(out)


def _host_of(url: str | None) -> str | None:
    try:
        h = urlparse(url).hostname if url else None
    except ValueError:
        return None
    return h.lower() if h else None


def _host_key(url: str | None, local_addrs) -> str | None:
    """"local" for loopback names and this machine's addresses, else the URL
    hostname (None when there is no URL)."""
    h = _host_of(url)
    if h is None:
        return None
    return "local" if (h in _LOOPBACK or h in local_addrs) else h


def _host_is_local(url: str | None, local_addrs) -> bool:
    return _host_key(url, local_addrs) == "local"


def _port_of(url: str | None) -> int | None:
    try:
        return urlparse(url).port if url else None
    except ValueError:
        return None


def _entries(registry) -> list:
    if registry is None:
        return []
    return list(registry.values()) if hasattr(registry, "values") else list(registry)


def _registry_for_tt(entries, name, served_id):
    for e in entries:
        if e.tt_name and e.tt_name == name:
            return e
    if served_id:
        for e in entries:
            if e.served_id and e.served_id == served_id:
                return e
    return None


def _same_server(a: dict, b: dict) -> bool:
    """Port first, then served id, then container id or name."""
    if a["port"] is None or a["port"] != b["port"]:
        return False
    if a["host"] != b["host"]:
        return False          # same port on a different machine
    sa, sb = a["served_id"], b["served_id"]
    if sa and sb and sa != sb:
        return False          # different model on a shared port
    return bool(a["ids"] & b["ids"])


def _fill(dst: dict, src: dict) -> None:
    for k in ("url", "uptime_s", "container", "container_id", "served_id", "key"):
        if dst.get(k) is None and src.get(k) is not None:
            dst[k] = src[k]
    dst["ids"] |= src["ids"]


# --------------------------------------------------------------------------
# merge / conflicts
# --------------------------------------------------------------------------

def merge(tt_servers, script_health: dict[str, bool], discovered, saved,
          records, registry) -> list[RunningServer]:
    """One RunningServer per distinct running server. Never raises on odd input."""
    entries = _entries(registry)
    by_key = {e.key: e for e in entries}
    local_addrs = _machine_addresses()
    cands: list[dict] = []

    for t in tt_servers or []:
        reg = _registry_for_tt(entries, t.name, t.served_id)
        ids = {i for i in (t.served_id, t.name, t.container, t.container_id) if i}
        cands.append(dict(key=reg.key if reg else t.name, name=t.name, source="tt",
                          url=t.base_url, port=t.port, health=t.health or t.status,
                          uptime_s=t.uptime_s, container=t.container,
                          container_id=t.container_id, served_id=t.served_id, ids=ids,
                          host="local"))

    for key, ok in (script_health or {}).items():
        e = by_key.get(key)
        if not ok or e is None or e.port is None:
            continue
        # The runner key stands in for the served id: it is what the script's
        # health check confirmed, and `tt model ps` never reports one.
        ids = {i for i in (e.served_id, e.runner_key, e.tt_name, e.name, key) if i}
        cands.append(dict(key=key, name=e.name, source="script",
                          url=f"http://127.0.0.1:{e.port}", port=e.port, health="healthy",
                          uptime_s=None, container=None, container_id=None,
                          served_id=e.served_id, ids=ids, host="local"))

    for url, model_id in discovered or []:
        reg = next((e for e in entries if model_id and e.served_id == model_id), None)
        ids = {i for i in (model_id, reg.tt_name if reg else None) if i}
        cands.append(dict(key=reg.key if reg else (model_id or url), name=model_id or url,
                          source="discovered", url=url, port=_port_of(url), health="healthy",
                          uptime_s=None, container=None, container_id=None,
                          served_id=model_id, ids=ids,
                          host=_host_key(url, local_addrs)))

    for s in saved or []:
        if not isinstance(s, dict):
            continue
        name = s.get("name") or s.get("label") or s.get("url") or "saved"
        url = s.get("url")
        ids = {i for i in (s.get("served_id"), name, s.get("container_id")) if i}
        cands.append(dict(key=s.get("key") or name, name=name, source="saved", url=url,
                          port=s.get("port") or _port_of(url), health="unknown",
                          uptime_s=None, container=None,
                          container_id=s.get("container_id"),
                          served_id=s.get("served_id"), ids=ids,
                          host=_host_key(url, local_addrs)))

    groups: list[dict] = []
    for c in cands:
        for g in groups:
            if _same_server(g, c):
                _fill(g, c)
                break
        else:
            groups.append(c)

    recs = [r for r in (records or []) if isinstance(r, dict)]
    out = []
    for g in groups:
        # tt and script rows come from this machine by definition.
        local = g["host"] == "local" or g["source"] in ("tt", "script")
        owner = "unknown"
        if local:
            owner = "external"
            for r in recs:
                rcid = r.get("container_id")
                if rcid:
                    hit = rcid == g["container_id"]
                elif g["container_id"] is not None:
                    hit = False      # only a script-sourced row can match a no-container record
                else:
                    hit = (r.get("name") in (g["name"], g["key"])
                           and r.get("port") == g["port"])
                if hit:
                    owner = "app"
                    break
        out.append(RunningServer(
            key=g["key"], name=g["name"], source=g["source"], url=g["url"],
            host_is_local=local, port=g["port"], health=g["health"],
            uptime_s=g["uptime_s"], container=g["container"],
            container_id=g["container_id"], served_id=g["served_id"], owner=owner))
    return out


def conflicts(servers: list[RunningServer], registry) -> list[tuple[RunningServer, RunningServer]]:
    """Pairs of rows on one host and port that are not the same server: served
    ids that differ, or one id unknown (reported, the safer choice: two rows on
    one port cannot both be running). The served id falls back to the
    registry's for that key. Different hosts never conflict."""
    by_key = {e.key: e for e in _entries(registry)}
    local_addrs = _machine_addresses()

    def sid(s: RunningServer):
        if s.served_id:
            return s.served_id
        e = by_key.get(s.key)
        return e.served_id if e else None

    def host(s: RunningServer):
        return "local" if s.host_is_local else _host_key(s.url, local_addrs)

    out = []
    for i, a in enumerate(servers):
        for b in servers[i + 1:]:
            if a.port is None or a.port != b.port or host(a) != host(b):
                continue
            sa, sb = sid(a), sid(b)
            if not sa or not sb or sa != sb:
                out.append((a, b))
    return out
