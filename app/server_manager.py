# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
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
    start("all")              # start the default "best experience" set (QB2/P300X2)
    start("single-chip")      # artgen + prompt-server — single Blackhole card or CPU-only
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

    runner_key   — optional: the value of `runner_in_use` returned by /tt-liveness
                   when this specific model is loaded.  Only set for port-8000
                   services.  When present, is_healthy() confirms both that the
                   server is up AND that the correct model is loaded.  Services
                   without runner_key (e.g. prompt-server, artgen) are checked by
                   HTTP 2xx alone.
    extra_args   — additional CLI args appended to every start/stop invocation of
                   the script (e.g. ["--model", "Qwen3-8B"] for artgen services).
    capabilities — user-facing capability strings this server provides.  Used to
                   group servers in the UI and label status indicators in
                   capability terms ("Video generation") rather than server terms
                   ("localhost:8000").
    model_id     — optional: the exact id this server's `/v1/models` endpoint is
                   expected to report (e.g. "Qwen/Qwen3-8B"). Only meaningful for
                   the shared-port-8002 artgen chat servers and prompt-server —
                   `model_status.match_model_id` uses this (falling back to
                   `label` when unset) to tell which *specific* chat model is
                   actually running, since all artgen entries share one port and
                   one detector. Defaults to None so existing ServerDef
                   construction elsewhere (tests, other call sites) is unaffected.
    """
    key: str          # short CLI name: "wan2.2", "prompt-server"
    label: str        # human-readable display label
    script: str       # filename inside bin/ (no path prefix)
    health_url: str   # URL for health check — GET must return 2xx when ready
    stop_flag: str = "--stop"  # flag the script accepts to stop the service
    runner_key: Optional[str] = None  # expected runner_in_use value (port-8000 only)
    extra_args: tuple = field(default_factory=tuple)  # model-specific args for start/stop
    capabilities: tuple = field(default_factory=tuple)  # e.g. ("video",), ("artgen",)
    model_id: Optional[str] = None  # served /v1/models id, e.g. "Qwen/Qwen3-8B"


# Human-readable labels for each capability key.
# "animatediff" is hardware-only (no server) — included here so the UI can
# use one dict for all capability labels.
CAPABILITY_LABELS: dict = {
    "video":       "Video generation",
    "animate":     "Character animation",
    "image":       "Image generation",
    "artgen":      "Generative art",
    "prompt":      "Prompt AI",
    "animatediff": "AnimateDiff  (Blackhole)",
}


# Ordered: "all" starts these in sequence (QB2 / P300X2 recommended set).
_ALL_KEYS = ["wan2.2", "prompt-server"]

# "single-chip" = services that run on a single Blackhole card (or CPU-only).
# Wan2.2 needs 4+ chips; skip it. AnimateDiff runs standalone (no server).
# Artgen + prompt-server give the full generative art + prompt experience.
_ONE_CHIP_KEYS = ["artgen-qwen3-8b", "prompt-server"]

SERVERS: dict[str, ServerDef] = {
    s.key: s
    for s in [
        ServerDef(
            key="wan2.2",
            label="Wan2.2-T2V-A14B  (P300X2)",
            script="start_wan_qb2.sh",
            health_url="http://localhost:8000/tt-liveness",
            runner_key="tt-wan2.2",
            capabilities=("video",),
        ),
        ServerDef(
            key="mochi",
            label="Mochi-1",
            script="start_mochi.sh",
            health_url="http://localhost:8000/tt-liveness",
            runner_key="tt-mochi-1",
            capabilities=("video",),
        ),
        ServerDef(
            key="flux",
            label="FLUX.1-schnell",
            script="start_flux.sh",
            health_url="http://localhost:8000/tt-liveness",
            runner_key="tt-flux.1-schnell",
            capabilities=("image",),
        ),
        ServerDef(
            key="sdxl",
            label="SDXL  (cpp_server)",
            script="start_sdxl.sh",
            health_url="http://localhost:8000/tt-liveness",
            runner_key="tt-sdxl-generate",
            capabilities=("image",),
        ),
        ServerDef(
            key="z-image-turbo",
            label="Z-Image-Turbo  (P150X4)",
            script="start_z_image_turbo.sh",
            health_url="http://localhost:8000/tt-liveness",
            runner_key="tt-z-image-turbo",
            capabilities=("image",),
        ),
        ServerDef(
            key="motif",
            label="Motif-Image-6B-Preview  (P300X2)",
            script="start_motif.sh",
            health_url="http://localhost:8000/tt-liveness",
            runner_key="tt-motif-image-6b-preview",
            capabilities=("image",),
        ),
        ServerDef(
            key="animate",
            label="Wan2.2-Animate-14B",
            script="start_animate.sh",
            health_url="http://localhost:8000/tt-liveness",
            runner_key="tt-wan2.2-animate",
            capabilities=("animate",),
        ),
        ServerDef(
            key="skyreels",
            label="SkyReels-V2-I2V-14B-540P  (Blackhole)",
            script="start_skyreels_i2v.sh",
            health_url="http://localhost:8000/tt-liveness",
            runner_key="tt-skyreels-v2-i2v",
            capabilities=("video",),
        ),
        ServerDef(
            key="prompt-server",
            label="Prompt Generator  (Qwen3-0.6B)",
            script="start_prompt_gen.sh",
            health_url="http://localhost:8001/health",
            capabilities=("prompt",),
            model_id="Qwen/Qwen3-0.6B",
        ),
        # Artgen chat/text LLMs — all share port 8002.  Only one runs at a time.
        # Health checked via the OpenAI-compatible /v1/models endpoint (any 2xx = up).
        # The start script's --model flag selects which weights to load.
        ServerDef(
            key="artgen-qwen3-8b",
            label="Qwen3-8B",
            script="start_artgen.sh",
            health_url="http://localhost:8002/v1/models",
            extra_args=("--model", "Qwen3-8B"),
            capabilities=("artgen",),
            model_id="Qwen/Qwen3-8B",
        ),
        ServerDef(
            key="artgen-llama-3.1-8b",
            label="Llama-3.1-8B-Instruct",
            script="start_artgen.sh",
            health_url="http://localhost:8002/v1/models",
            extra_args=("--model", "Llama-3.1-8B-Instruct"),
            capabilities=("artgen",),
            model_id="meta-llama/Llama-3.1-8B-Instruct",
        ),
        ServerDef(
            key="artgen-qwen2.5-7b",
            label="Qwen2.5-7B-Instruct",
            script="start_artgen.sh",
            health_url="http://localhost:8002/v1/models",
            extra_args=("--model", "Qwen2.5-7B-Instruct"),
            capabilities=("artgen",),
            model_id="Qwen/Qwen2.5-7B-Instruct",
        ),
        ServerDef(
            key="artgen-llama-3.3-70b",
            label="Llama-3.3-70B-Instruct",
            script="start_artgen.sh",
            health_url="http://localhost:8002/v1/models",
            extra_args=("--model", "Llama-3.3-70B-Instruct"),
            capabilities=("artgen",),
            model_id="meta-llama/Llama-3.3-70B-Instruct",
        ),
        ServerDef(
            key="artgen-qwen3-32b",
            label="Qwen3-32B",
            script="start_artgen.sh",
            health_url="http://localhost:8002/v1/models",
            extra_args=("--model", "Qwen3-32B"),
            capabilities=("artgen",),
            model_id="Qwen/Qwen3-32B",
        ),
        ServerDef(
            key="artgen-deepseek-r1-70b",
            label="DeepSeek-R1-Distill-70B",
            script="start_artgen.sh",
            health_url="http://localhost:8002/v1/models",
            extra_args=("--model", "DeepSeek-R1-Distill-Llama-70B"),
            capabilities=("artgen",),
            model_id="deepseek-ai/DeepSeek-R1-Distill-Llama-70B",
        ),
    ]
}


def servers_for_capability(cap: str) -> "list[ServerDef]":
    """Return all ServerDef entries that provide the given capability."""
    return [s for s in SERVERS.values() if cap in s.capabilities]

# "all" = the recommended everyday set (QB2 / P300X2).
ALL_KEY = "all"
# "single-chip" = artgen + prompt-server only — works on a single Blackhole card.
ONE_CHIP_KEY = "single-chip"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve(key: str) -> list[ServerDef]:
    """Expand key → list[ServerDef].  Raises KeyError for unknown keys."""
    if key == ALL_KEY:
        return [SERVERS[k] for k in _ALL_KEYS]
    if key == ONE_CHIP_KEY:
        return [SERVERS[k] for k in _ONE_CHIP_KEYS]
    if key not in SERVERS:
        known = ", ".join(sorted(SERVERS.keys()) + [ALL_KEY, "--single-chip"])
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
        cmd = ["bash", str(_script_path(sdef)), *sdef.extra_args]
        if gui:
            cmd.append("--gui")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        results.append(result)
    return results


def stop(key: str, timeout: Optional[int] = 30) -> list[subprocess.CompletedProcess]:
    """Stop server(s) identified by key (or 'all')."""
    results = []
    for sdef in _resolve(key):
        cmd = ["bash", str(_script_path(sdef)), *sdef.extra_args, sdef.stop_flag]
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
