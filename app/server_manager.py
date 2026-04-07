"""
server_manager.py — Unified control surface for all tt-local-generator services.

Every service is described by a ServerDef: a short key, human label, the shell
script in bin/ that manages it, and a health-check URL.

For port-8000 services (the TT inference server), multiple model runners share
the same health URL.  The optional `runner_key` field holds the expected
`runner_in_use` value returned by /tt-liveness.  When set, is_healthy() fetches
the JSON body and confirms the right model is actually loaded — so wan2.2 won't
show green just because mochi is running on port 8000.

Imported by both tt-ctl (CLI) and the GUI (main_window.py).  No GTK dependency.

Usage examples
--------------
    from server_manager import start, stop, restart, health, status_all, SERVERS

    start("wan2.2")           # launch Wan2.2 server (--gui, non-blocking)
    stop("prompt-server")     # send --stop to the prompt-gen script
    restart("wan2.2")         # stop then start
    health("wan2.2")          # {"wan2.2": True/False}
    status_all()              # {"wan2.2": True, "prompt-server": False, ...}
    start("all")              # start the default "best experience" set
"""

import json
import subprocess
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Repo root is two levels up: app/server_manager.py → app/ → repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_BIN = _REPO_ROOT / "bin"

# ---------------------------------------------------------------------------
# Server definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ServerDef:
    """Describes one managed service.

    runner_key — optional: the value of `runner_in_use` returned by /tt-liveness
                 when this specific model is loaded.  Only set for port-8000
                 services.  When present, is_healthy() confirms both that the
                 server is up AND that the correct model is loaded.  Services
                 without runner_key (e.g. prompt-server) are checked by HTTP 2xx
                 alone.
    """
    key: str          # short CLI name: "wan2.2", "prompt-server"
    label: str        # human-readable display label
    script: str       # filename inside bin/ (no path prefix)
    health_url: str   # URL for health check — GET must return 2xx when ready
    stop_flag: str = "--stop"  # flag the script accepts to stop the service
    runner_key: Optional[str] = None  # expected runner_in_use value (port-8000 only)


# Ordered: "all" starts these in sequence.
_ALL_KEYS = ["wan2.2", "prompt-server"]

SERVERS: dict[str, ServerDef] = {
    s.key: s
    for s in [
        ServerDef(
            key="wan2.2",
            label="Wan2.2-T2V-A14B  (P300X2)",
            script="start_wan_qb2.sh",
            health_url="http://localhost:8000/tt-liveness",
            runner_key="tt-wan2.2",
        ),
        ServerDef(
            key="mochi",
            label="Mochi-1",
            script="start_mochi.sh",
            health_url="http://localhost:8000/tt-liveness",
            runner_key="tt-mochi-1",
        ),
        ServerDef(
            key="flux",
            label="FLUX Image",
            script="start_flux.sh",
            health_url="http://localhost:8000/tt-liveness",
            runner_key="tt-flux.1-dev",
        ),
        ServerDef(
            key="animate",
            label="Wan2.2-Animate-14B",
            script="start_animate.sh",
            health_url="http://localhost:8000/tt-liveness",
            runner_key="tt-wan2.2-animate",
        ),
        ServerDef(
            key="prompt-server",
            label="Prompt Generator  (Qwen3-0.6B)",
            script="start_prompt_gen.sh",
            health_url="http://localhost:8001/health",
            # No runner_key — health is checked by HTTP 2xx alone.
        ),
    ]
}

# "all" = the recommended everyday set.
ALL_KEY = "all"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve(key: str) -> list[ServerDef]:
    """Expand key → list[ServerDef].  Raises KeyError for unknown keys."""
    if key == ALL_KEY:
        return [SERVERS[k] for k in _ALL_KEYS]
    if key not in SERVERS:
        known = ", ".join(sorted(SERVERS.keys()) + [ALL_KEY])
        raise KeyError(f"Unknown server: {key!r}.  Known: {known}")
    return [SERVERS[key]]


def _script_path(sdef: ServerDef) -> Path:
    return _BIN / sdef.script


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start(
    key: str,
    gui: bool = True,
    timeout: Optional[int] = None,
) -> list[subprocess.CompletedProcess]:
    """Start server(s) identified by key (or 'all').

    gui=True  — passes --gui so the script is non-blocking and skips the
                 interactive tail.  Set to False for blocking CLI use.
    timeout   — seconds before giving up (None = no limit, only for blocking mode).
    """
    results = []
    for sdef in _resolve(key):
        cmd = ["bash", str(_script_path(sdef))]
        if gui:
            cmd.append("--gui")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        results.append(result)
    return results


def stop(key: str, timeout: Optional[int] = 30) -> list[subprocess.CompletedProcess]:
    """Stop server(s) identified by key (or 'all')."""
    results = []
    for sdef in _resolve(key):
        cmd = ["bash", str(_script_path(sdef)), sdef.stop_flag]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        results.append(result)
    return results


def restart(
    key: str,
    gui: bool = True,
    stop_timeout: Optional[int] = 30,
    start_timeout: Optional[int] = None,
) -> list[subprocess.CompletedProcess]:
    """Stop then start server(s).  Returns the start results."""
    stop(key, timeout=stop_timeout)
    return start(key, gui=gui, timeout=start_timeout)


def _check_sdef(sdef: ServerDef, timeout: float) -> bool:
    """Return True if sdef's service is up and (when runner_key is set) the
    correct model is loaded.

    For port-8000 services with a runner_key we parse the JSON liveness body
    and confirm runner_in_use matches — so wan2.2 won't show green when mochi
    is actually loaded on port 8000.

    The health URL host/port is resolved from server_config at call time so
    changes made in Preferences take effect on the next health check without
    restarting the app.
    """
    from server_config import server_config as _sc
    url = _sc.health_url(sdef.key, sdef.health_url)
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        if sdef.runner_key is None:
            return True
        body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        return data.get("runner_in_use") == sdef.runner_key
    except Exception:
        return False


def is_healthy(key: str, timeout: float = 2.0) -> bool:
    """Return True if the single named server responds to its health URL and
    (for port-8000 services) the correct model is loaded.

    Raises KeyError for unknown key.  Does not accept 'all'.
    """
    if key == ALL_KEY:
        raise ValueError("is_healthy() does not accept 'all'; use health() instead")
    return _check_sdef(SERVERS[key], timeout)


def health(key: str, timeout: float = 2.0) -> dict[str, bool]:
    """Return {server_key: is_alive} for key or 'all'."""
    return {sdef.key: _check_sdef(sdef, timeout) for sdef in _resolve(key)}


def status_all(timeout: float = 2.0) -> dict[str, bool]:
    """Return {server_key: is_alive} for every known server."""
    return {sdef.key: _check_sdef(sdef, timeout) for sdef in SERVERS.values()}
