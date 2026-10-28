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

    def _save(self, records: list[dict]) -> None:
        """Write a temp file in the same directory, then os.replace it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".servers.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(records, f, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def add(self, name, port, container_id, lease_id, started_at) -> None:
        """Record a server (replaces any record with the same name)."""
        recs = [r for r in self.load() if r.get("name") != name]
        recs.append({"name": name, "port": port, "container_id": container_id,
                     "lease_id": lease_id, "started_at": started_at})
        self._save(recs)

    def remove(self, name) -> None:
        recs = self.load()
        kept = [r for r in recs if r.get("name") != name]
        if len(kept) != len(recs):
            self._save(kept)

    def prune(self, live_container_ids: set[str]) -> list[dict]:
        """Drop records whose container id is not live; return what was dropped
        so the caller can reap their leases. Records with no container id
        (script-started servers) are kept: there is nothing to compare."""
        recs = self.load()
        kept, dropped = [], []
        for r in recs:
            cid = r.get("container_id")
            (dropped if cid and cid not in live_container_ids else kept).append(r)
        if dropped:
            self._save(kept)
        return dropped


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _machine_addresses() -> set[str]:
    try:
        return set(socket.gethostbyname_ex(socket.gethostname())[2])
    except Exception:
        return set()


def _host_is_local(url: str | None, local_addrs: set[str]) -> bool:
    if not url:
        return False
    try:
        host = urlparse(url).hostname
    except ValueError:
        return False
    return bool(host) and (host in _LOOPBACK or host in local_addrs)


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
                          container_id=t.container_id, served_id=t.served_id, ids=ids))

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
                          served_id=e.served_id, ids=ids))

    for url, model_id in discovered or []:
        reg = next((e for e in entries if model_id and e.served_id == model_id), None)
        ids = {i for i in (model_id, reg.tt_name if reg else None) if i}
        cands.append(dict(key=reg.key if reg else (model_id or url), name=model_id or url,
                          source="discovered", url=url, port=_port_of(url), health="healthy",
                          uptime_s=None, container=None, container_id=None,
                          served_id=model_id, ids=ids))

    for s in saved or []:
        if not isinstance(s, dict):
            continue
        name = s.get("name") or s.get("url") or "saved"
        url = s.get("url")
        ids = {i for i in (s.get("served_id"), name, s.get("container_id")) if i}
        cands.append(dict(key=s.get("key") or name, name=name, source="saved", url=url,
                          port=s.get("port") or _port_of(url), health="unknown",
                          uptime_s=None, container=None,
                          container_id=s.get("container_id"),
                          served_id=s.get("served_id"), ids=ids))

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
        local = _host_is_local(g["url"], local_addrs)
        owner = "unknown"
        if local:
            owner = "external"
            for r in recs:
                rcid = r.get("container_id")
                if rcid:
                    hit = rcid == g["container_id"]
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
    """Pairs on one port whose served ids differ: a different model on a shared
    port. The served id falls back to the registry's for that key."""
    by_key = {e.key: e for e in _entries(registry)}

    def sid(s: RunningServer):
        if s.served_id:
            return s.served_id
        e = by_key.get(s.key)
        return e.served_id if e else None

    out = []
    for i, a in enumerate(servers):
        for b in servers[i + 1:]:
            if a.port is None or a.port != b.port:
                continue
            sa, sb = sid(a), sid(b)
            if sa and sb and sa != sb:
                out.append((a, b))
    return out
