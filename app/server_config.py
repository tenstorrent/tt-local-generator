#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
server_config.py — Per-service host / port / token configuration.

Stored in ~/.config/tt-video-gen/servers.json so it is separate from the
generation history/settings and can be edited by scripts:

    # Read a value
    python3 -c "
    import json, pathlib
    cfg = json.loads((pathlib.Path.home() / '.config/tt-video-gen/servers.json').read_text())
    print(cfg['wan2.2']['token'])
    "

    # Update a token
    python3 -c "
    import json, pathlib
    p = pathlib.Path.home() / '.config/tt-video-gen/servers.json'
    cfg = json.loads(p.read_text())
    cfg['wan2.2']['token'] = 'my-new-secret'
    p.write_text(json.dumps(cfg, indent=2))
    "

    # Point wan2.2 at a remote host
    python3 -c "
    import json, pathlib
    p = pathlib.Path.home() / '.config/tt-video-gen/servers.json'
    cfg = json.loads(p.read_text())
    cfg['wan2.2']['host'] = '192.168.1.42'
    cfg['wan2.2']['port'] = 8000
    p.write_text(json.dumps(cfg, indent=2))
    "

Host and port changes take effect on the next app launch (the API client and
health-check URLs are constructed at startup).  Token changes take effect
immediately — the API client reads the token on every request.
"""

import json
import logging
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

CONFIG_DIR  = Path.home() / ".config" / "tt-video-gen"
CONFIG_FILE = CONFIG_DIR / "servers.json"

# ── Defaults — match the local single-machine setup out of the box ─────────────

DEFAULTS: dict[str, dict] = {
    "wan2.2": {
        "host":  "localhost",
        "port":  8000,
        "token": "your-secret-key",
    },
    "mochi": {
        "host":  "localhost",
        "port":  8000,
        "token": "your-secret-key",
    },
    "flux": {
        "host":  "localhost",
        "port":  8000,
        "token": "your-secret-key",
    },
    "animate": {
        "host":  "localhost",
        "port":  8000,
        "token": "your-secret-key",
    },
    "skyreels": {
        "host":  "localhost",
        "port":  8000,
        "token": "your-secret-key",
    },
    "prompt-server": {
        "host":  "localhost",
        "port":  8001,
        "token": "",          # prompt server has no auth
    },
}


class ServerConfig:
    """
    Persistent per-service configuration store backed by
    ~/.config/tt-video-gen/servers.json.

    Every call to set() writes through to disk immediately so the file is
    always up to date.  Reads are in-memory only.
    """

    def __init__(self) -> None:
        # Start from deep-copied defaults so mutations don't affect DEFAULTS.
        self._data: dict[str, dict] = {k: dict(v) for k, v in DEFAULTS.items()}
        self._load()

    # ── Public API ─────────────────────────────────────────────────────────────

    def get(self, service_key: str, field: str):
        """Return the value for (service_key, field), falling back to DEFAULTS."""
        svc = self._data.get(service_key) or DEFAULTS.get(service_key) or {}
        if field in svc:
            return svc[field]
        return (DEFAULTS.get(service_key) or {}).get(field)

    def set(self, service_key: str, field: str, value) -> None:
        """Update (service_key, field) in memory and persist to disk."""
        if service_key not in self._data:
            self._data[service_key] = dict(DEFAULTS.get(service_key) or {})
        self._data[service_key][field] = value
        self._save()

    def base_url(self, service_key: str) -> str:
        """Return http://host:port for the given service."""
        host = self.get(service_key, "host") or "localhost"
        port = self.get(service_key, "port") or 8000
        return f"http://{host}:{port}"

    def health_url(self, service_key: str, static_health_url: str) -> str:
        """
        Return the health-check URL for service_key, using the configured
        host/port but preserving the path from static_health_url.

        e.g. static "http://localhost:8000/tt-liveness" + config host "mybox"
             → "http://mybox:8000/tt-liveness"
        """
        path = urlparse(static_health_url).path
        return self.base_url(service_key) + path

    def token(self, service_key: str) -> str:
        """Return the bearer token for service_key, or '' if none configured."""
        return self.get(service_key, "token") or ""

    def apply_remote_host(self, host: str) -> None:
        """
        Update every service entry whose host is currently localhost or 127.0.0.1
        to use host instead.  Writes through to disk so Preferences reflects the
        change immediately.

        Called at startup when --server points at a non-local machine, so all
        health checks, the Preferences dialog, and the prompt server URL all
        agree on the remote host without manual configuration.
        """
        _local = {"localhost", "127.0.0.1"}
        changed = False
        for svc in self._data.values():
            if svc.get("host") in _local:
                svc["host"] = host
                changed = True
        if changed:
            self._save()
            log.info("server_config: updated all localhost entries → %s", host)

    def all_services(self) -> dict[str, dict]:
        """Return a shallow copy of the full config dict."""
        return {k: dict(v) for k, v in self._data.items()}

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if CONFIG_FILE.exists():
                raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for key, defaults in DEFAULTS.items():
                        if key in raw and isinstance(raw[key], dict):
                            # Overlay saved values so new default fields still appear.
                            self._data[key] = {**defaults, **raw[key]}
        except Exception as exc:
            log.warning("server_config: could not load %s: %s", CONFIG_FILE, exc)

    def _save(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("server_config: could not save %s: %s", CONFIG_FILE, exc)


# ── Module-level singleton ─────────────────────────────────────────────────────

server_config = ServerConfig()
