"""Where AnimateDiff can run: a local tt-metal checkout, or a served container.

GUI-free and stdlib-only. AnimateDiff has two ways to execute on Blackhole:

  * **local**  -- ``examples/generate.py`` exec'd under a tt-metal ``python_env``
    (``$TT_METAL_HOME`` or ``~/tt-metal``). Needs a built tt-metal tree.
  * **served** -- the tt-model-manager package ``episod/tt-animatediff-container-v5.1``
    (``kind: tt-dit-server``): a container with its own tt-metal build and an
    HTTP surface (``POST /v1/videos/generations``). Needs only docker and the
    ``tt-model`` CLI, so it works on a fresh box with no ``~/tt-metal``.

Before this module the generator hardcoded ``~/tt-metal/python_env``; a fresh
QB2 without that directory could not generate at all and got a build
instruction instead of a working path. Local stays first (it supports every
option: multi-chip, motion adapter, lightning, chaining); served is the
fallback and supports the single-chip basics.

Everything here must never raise into the caller: discovery failing means
"not found", not a crash.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

#: tt-model-manager package that serves AnimateDiff.
SERVED_PACKAGE = "episod/tt-animatediff-container-v5.1"

#: Substrings of the served model id (``/v1/models``) that identify an
#: AnimateDiff server. The tt-animatediff server reports the SD 1.4 repo it
#: loads. Matching on id matters: tt-media-server (Wan2.2 etc.) answers the
#: same ``/v1/videos/generations`` route on another port and must not be taken
#: for AnimateDiff.
_SERVED_MODEL_MARKERS = ("stable-diffusion-v1-4", "animatediff")


def find_tt_metal(env: dict | None = None, home: Path | None = None) -> Path | None:
    """Return a tt-metal tree whose ``python_env`` exists, or None.

    Order: ``$TT_METAL_HOME``, then ``~/tt-metal``.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    candidates = []
    if env.get("TT_METAL_HOME"):
        candidates.append(Path(env["TT_METAL_HOME"]).expanduser())
    candidates.append(home / "tt-metal")
    for c in candidates:
        if (c / "python_env" / "bin" / "python").exists():
            return c
    return None


def _http_json(url: str, timeout: float = 2.0, data: bytes | None = None):
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"} if data else {}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - local URL
        return json.loads(r.read().decode("utf-8"))


def _is_animatediff_server(base_url: str) -> bool:
    # Everything that touches the response stays inside the try: any labelled
    # tt-model container may answer with an unexpected shape, and that must read
    # as "not AnimateDiff", not stop discovery of a later valid server.
    try:
        models = _http_json(base_url + "/v1/models").get("data", [])
        return any(
            isinstance(entry, dict)
            and any(m in str(entry.get("id", "")).lower() for m in _SERVED_MODEL_MARKERS)
            for entry in models
        )
    except Exception:
        return False


def _candidate_ports() -> list[int]:
    # Reuse the artgen docker-label discovery rather than a second copy.
    try:
        from artgen import _tt_model_host_ports
        return _tt_model_host_ports()
    except Exception:
        return []


def find_served(
    host: str = "localhost",
    ports_fn: Callable[[], list[int]] = _candidate_ports,
    probe: Callable[[str], bool] = _is_animatediff_server,
) -> str | None:
    """Base URL of a running tt-animatediff server, or None."""
    for port in ports_fn():
        url = f"http://{host}:{port}"
        if probe(url):
            return url
    return None


def served_launcher_available() -> bool:
    """True when this box can start the served package itself."""
    return shutil.which("tt-model") is not None and shutil.which("docker") is not None


def start_served(
    on_progress: Callable[[str], None] | None = None,
    wait_s: float = 900.0,
    poll_s: float = 5.0,
    ports_fn: Callable[[], list[int]] = _candidate_ports,
    probe: Callable[[str], bool] = _is_animatediff_server,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[str | None, str]:
    """Start ``tt-model serve episod/tt-animatediff-container-v5.1 --detach`` and wait for it.

    Returns ``(base_url, "")`` on success or ``(None, reason)``. The first
    start pulls the image and weights and warms the pipeline, so ``wait_s`` is
    generous; progress is reported so the UI is not silent.
    """
    if not served_launcher_available():
        return None, "tt-model and docker are required to serve AnimateDiff"
    if on_progress:
        on_progress(f"Starting {SERVED_PACKAGE} via tt-model (first run downloads the image)")
    try:
        proc = subprocess.run(
            ["tt-model", "serve", SERVED_PACKAGE, "--detach"],
            capture_output=True, text=True, timeout=wait_s,
        )
    except Exception as e:  # timeout, OSError
        return None, f"tt-model serve failed: {e}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        return None, "tt-model serve failed: " + " | ".join(tail)

    deadline = clock() + wait_s
    while clock() < deadline:
        url = find_served(ports_fn=ports_fn, probe=probe)
        if url:
            return url, ""
        if on_progress:
            on_progress("Waiting for the AnimateDiff server to finish loading")
        sleep(poll_s)
    return None, f"AnimateDiff server did not become ready within {int(wait_s)}s"


def chain_name_for(path: str | os.PathLike) -> str:
    """Server-side chain name for a client-side chain file path.

    The server keeps chain state under names matching
    ``[A-Za-z0-9][A-Za-z0-9._-]{0,63}``, never paths. The name is derived from
    the resolved path, so the same ``--chain-save``/``--chain-from`` path maps
    to the same name on every call with no stored mapping. The server holds
    only the newest 32 and drops them when it stops, so a ``chain_from`` whose
    producer is gone is refused by the server ("unknown chain").
    """
    full = str(Path(path).expanduser().resolve())
    stem = "".join(c if (c.isascii() and c.isalnum()) or c in "._-" else "-" for c in Path(full).stem)[:40]
    return f"{stem.lstrip('._-') or 'chain'}-{hashlib.sha1(full.encode()).hexdigest()[:12]}"


def motion_adapter_enabled(base_url: str) -> bool:
    """Whether the operator started the server with a motion adapter."""
    try:
        return bool(_http_json(base_url + "/v1/models").get("motion_adapter"))
    except Exception:
        return False


def served_loras(base_url: str) -> list[str]:
    """LoRA variant names the served model offers (``/v1/models`` -> ``loras``).

    The operator's catalog is fixed when the package is built (a serve profile in
    its manifest), and a request names a variant; it never carries a repo id or a
    path. An old server, a plain profile, an unreachable server or a malformed
    answer all give an empty list.
    """
    try:
        names = _http_json(base_url + "/v1/models").get("loras")
    except Exception:
        return []
    return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []


def _write_atomic(path: Path, data: bytes) -> None:
    """Temp file + rename, so a consumer polling `path` never sees a partial GIF."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def poll_previews(
    base_url: str,
    preview_path: Path,
    on_progress: Callable[[str], None],
    stop: threading.Event,
    interval_s: float = 2.0,
    fetch: Callable[[str], dict] = _http_json,
) -> None:
    """Relay the server's rolling latent preview as ``PREVIEW:`` progress lines.

    The served model exposes ``GET /v1/videos/preview`` ->
    ``{"step", "total", "gif_b64", "running"}`` (step is 1-based; ``total`` is
    the loop's real count, e.g. 21 for a 20-step request, so it is reported
    as given). The last frame of a finished run stays readable until the next
    request starts, so frames seen while ``running`` is false are ignored: they
    may belong to the previous request. Each new step is written to
    ``preview_path`` and announced in the same line format the local runner
    prints (``animatediff_ttnn.preview``), so the Create panel needs no
    changes. A server without the endpoint (older package) makes the first
    request fail; polling then stops quietly, since a missing preview must
    never affect the generation. Any other failure just skips that tick.
    """
    last_step = 0
    while not stop.wait(interval_s):
        try:
            info = fetch(base_url + "/v1/videos/preview")
        except urllib.error.HTTPError as e:
            if e.code in (404, 405):
                return
            continue
        except Exception:
            continue
        try:
            step, total = int(info.get("step") or 0), int(info.get("total") or 0)
            b64 = info.get("gif_b64")
            if not info.get("running") or not b64 or step <= last_step:
                continue
            _write_atomic(preview_path, base64.b64decode(b64))
            last_step = step
            on_progress(f"PREVIEW: {step}/{total} {preview_path}")
        except Exception:
            continue


def generate_served(
    base_url: str,
    out_path: Path,
    prompt: str,
    negative_prompt: str,
    frames: int,
    steps: int,
    seed: int,
    temporal_alpha: float,
    timeout: float = 1800.0,
    on_progress: Callable[[str], None] | None = None,
    preview_path: Path | None = None,
    prompt_schedule: "list[tuple[int, str]] | None" = None,
    extra: dict | None = None,
) -> tuple[bool, str]:
    """POST one generation to the served model and write the GIF to out_path.

    With ``on_progress`` and ``preview_path`` given, a background thread relays
    the server's per-step previews while the (blocking) POST runs.
    """
    stop = threading.Event()
    poller = None
    if on_progress and preview_path is not None:
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        poller = threading.Thread(
            target=poll_previews, args=(base_url, preview_path, on_progress, stop),
            daemon=True,
        )
        poller.start()
    try:
        return _post_generation(base_url, out_path, prompt, negative_prompt, frames,
                                steps, seed, temporal_alpha, timeout,
                                want_preview=poller is not None,
                                prompt_schedule=prompt_schedule, extra=extra)
    finally:
        stop.set()
        if poller is not None:
            poller.join(timeout=5)


def _post_generation(base_url, out_path, prompt, negative_prompt, frames, steps,
                     seed, temporal_alpha, timeout, want_preview=False,
                     prompt_schedule=None, extra=None) -> tuple[bool, str]:
    payload = {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "num_frames": frames,
        "num_inference_steps": steps,
        "seed": seed,
        "temporal_alpha": temporal_alpha,
        "response_format": "b64_json",
    }
    # The server only renders preview GIFs on request (each costs ~0.4 s per
    # rendered step), so ask only when someone is watching.
    if want_preview:
        payload["preview"] = True
    if prompt_schedule:
        payload["prompt_schedule"] = [
            {"frame": int(f), "prompt": p} for f, p in prompt_schedule
        ]
    # sampler / chain_* / motion_adapter* fields, built by the caller.
    payload.update(extra or {})
    body = json.dumps(payload).encode("utf-8")
    try:
        resp = _http_json(base_url + "/v1/videos/generations", timeout=timeout, data=body)
        gif = base64.b64decode(resp["data"][0]["b64_json"])
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        return False, f"AnimateDiff server returned HTTP {e.code}: {detail}"
    except Exception as e:
        return False, f"AnimateDiff server request failed: {e}"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(gif)
    except OSError as e:
        return False, f"could not write {out_path}: {e}"
    return True, ""


#: Operators who accept that a run may start the container themselves (headless
#: use, no confirmation dialog) set this to "1".
AUTOSTART_ENV = "TTLG_ANIMATEDIFF_AUTOSTART"

#: Shown when the served package is the right runtime but is not running.
NOT_RUNNING_MESSAGE = (
    f"The AnimateDiff server is not running. Start it first: tt-model serve {SERVED_PACKAGE}\n"
    f"(or set {AUTOSTART_ENV}=1 to let a run start it; that switches the chips' "
    "backend without asking, so stop any other model server first).\n"
    "Alternatively install tt-metal so ~/tt-metal/python_env exists."
)

#: What the generator tells the user when neither runtime exists and the
#: served package cannot be started either.
MISSING_MESSAGE = (
    "AnimateDiff has no runtime on this machine. Either:\n"
    "  1. install tt-metal (https://github.com/tenstorrent/tt-installer) so that\n"
    "     ~/tt-metal/python_env exists (or set TT_METAL_HOME), or\n"
    f"  2. install tt-model and docker, then run: tt-model serve {SERVED_PACKAGE}"
)
