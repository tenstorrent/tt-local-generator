#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generic pipeline engine — executes a ComfyUI-API-v1 spec.

Replaces the hardcoded run_workflow.sh stub. Loads a spec, topologically orders
nodes by their wire dependencies, dispatches each node's class_type to a handler,
resolves wired inputs from prior outputs, and emits NODE:/LOG:/PLAYLIST: signals
that app/pipeline_runner.py parses. --dry-run runs the whole graph with placeholder
outputs (no hardware/API), for CI.

Backend server-switching (mirrors bin/run_worlds_fair.sh)
---------------------------------------------------------
Different nodes need different inference backends, and several of them share
port 8000 (FLUX, SkyReels, Wan2.2, Mochi) so only one can run at a time. Before
dispatching each node, ``run()`` calls :func:`_backend_for` to decide which
backend that node needs (a server_manager key, a chips-free sentinel, or None
for CPU-only nodes). When the required backend differs from the one currently
active it stops the running containers, resets the boards (only when switching
*from* a prior backend), and starts the new server — exactly as the bash driver
did. In ``dry_run`` mode NONE of this touches docker/tt-smi/tt-ctl: it only
emits ``[dry-run]`` log lines so CI stays 100% hardware-free.

Optional-node failures
----------------------
A handler that raises no longer aborts the whole pipeline. The node's
``optional`` status comes from ``workflow_compat.COMPATIBILITY_MAP`` (the single
source of truth): optional nodes are logged as failed and skipped (their outputs
left absent) so the run continues; required nodes still fail-fast.
"""
from __future__ import annotations
import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import server_manager as sm
from workflow_compat import COMPATIBILITY_MAP

# Repo root: app/ -> repo root (same resolution as server_manager). Used to
# locate plugins/<name>/plugin.py exactly as bin/run_workflow.sh does.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _is_wire(v) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)


def _wire_deps(v) -> "set[str]":
    """Recursively collect source-node ids from wires nested in *v*.

    Some node inputs (e.g. TTLGAddToPlaylist's ``artifacts``/``metadata``)
    nest wires inside lists/dicts rather than at the top level. Walk the
    value tree and collect every wire's source id, checking `_is_wire` BEFORE
    the generic list branch since a wire is itself a 2-element list.
    """
    if _is_wire(v):
        return {v[0]}
    if isinstance(v, list):
        deps: "set[str]" = set()
        for item in v:
            deps |= _wire_deps(item)
        return deps
    if isinstance(v, dict):
        deps = set()
        for item in v.values():
            deps |= _wire_deps(item)
        return deps
    return set()


def _resolve_value(v, results: dict):
    """Recursively rebuild *v* with every nested wire replaced by its value.

    Mirrors `_wire_deps`'s traversal order/precedence exactly (wire check
    before the generic list branch) and never mutates the input structure.
    """
    if _is_wire(v):
        return results[v[0]][v[1]]
    if isinstance(v, list):
        return [_resolve_value(item, results) for item in v]
    if isinstance(v, dict):
        return {k: _resolve_value(item, results) for k, item in v.items()}
    return v


def load_spec(path: str) -> dict:
    raw = json.loads(Path(path).read_text())
    return {k: v for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, dict) and "class_type" in v}


def topo_order(spec: dict) -> "list[str]":
    # Kahn's algorithm over wire edges (src -> node).
    deps = {nid: set() for nid in spec}
    for nid, node in spec.items():
        for v in node.get("inputs", {}).values():
            for src in _wire_deps(v):
                if src not in spec:
                    raise ValueError(f"node {nid} wires to missing node {src}")
                deps[nid].add(src)
    order, ready = [], sorted(n for n, d in deps.items() if not d)
    seen = set(ready)
    while ready:
        n = ready.pop(0); order.append(n)
        for m in spec:
            if n in deps[m]:
                deps[m].discard(n)
                if not deps[m] and m not in seen:
                    seen.add(m); ready.append(m); ready.sort()
    if len(order) != len(spec):
        raise ValueError("cycle detected in pipeline spec")
    return order


def resolve_inputs(inputs: dict, results: dict) -> dict:
    return {k: _resolve_value(v, results) for k, v in inputs.items()}


class _Ctx:
    """Runtime context passed to handlers: output dir, dry_run flag, and emit.

    Handlers receive this so they can write artifacts under ``output_dir``,
    short-circuit to placeholder outputs when ``dry_run`` is set, and emit
    NODE:/LOG:/PLAYLIST: signal lines via ``emit``. Backend server-switching is
    NOT a handler concern — ``run()`` orchestrates it around each dispatch (see
    :func:`_backend_for` / :func:`_stop_and_reset` / :func:`_start_server`), so
    handlers just POST to the ``server`` URL their inputs carry and can assume
    the right backend is already up.
    """
    def __init__(self, output_dir: Path, dry_run: bool, emit: Callable[[str], None]):
        self.output_dir = output_dir
        self.dry_run = dry_run
        self.emit = emit


# Dispatch table: class_type -> handler(node_id, inputs, ctx) -> dict of outputs.
# Handlers are registered by the handler modules (Task 2). Task 1 registers only
# stubs so the dry-run path is exercisable before real handlers land.
HANDLERS: "dict[str, Callable]" = {}


def register(class_type: str):
    def deco(fn):
        HANDLERS[class_type] = fn
        return fn
    return deco


# ── Testable helpers (mockable by unit tests) ────────────────────────────────
#
# Handlers below never talk to the network/plugins/store directly — they route
# through these four (+ playlist) helpers so unit tests can monkeypatch them.
# The bodies port the working logic from bin/run_workflow.sh verbatim in spirit.


def _post_json(url: str, payload: dict, timeout: int = 30) -> dict:
    """POST a JSON body and return the decoded JSON response (stdlib only)."""
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get_json(url: str, timeout: int = 30) -> dict:
    """GET a JSON body and return the decoded JSON response (stdlib only)."""
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _download(url: str, out_path: str, timeout: int = 300) -> None:
    """Stream a URL to a local file."""
    with urllib.request.urlopen(url, timeout=timeout) as r, open(out_path, "wb") as f:
        f.write(r.read())


def _media_image_request(*, server, prompt, width, height, steps, seed,
                         out_path, negative_prompt=None) -> str:
    """Submit a media-server image job, poll to completion, download the result.

    Ports node_text_to_image (run_workflow.sh:188-263): 2 s pre-sleep, up to 3
    submit retries with 10 s backoff, then poll the job (30 s cadence, ≤40 polls)
    and download the finished image to *out_path*. Returns *out_path* on success;
    raises RuntimeError otherwise.
    """
    # Fix 1 (bash): brief pre-sleep so back-to-back image nodes don't 429.
    time.sleep(2)

    payload = {"prompt": prompt, "width": int(width), "height": int(height),
               "num_inference_steps": int(steps), "seed": int(seed)}
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt

    # Fix 4 (bash): retry submission up to 3× with 10 s backoff on ANY
    # non-success outcome — empty response, missing "id", or a raised
    # exception — not just exceptions (bin/run_workflow.sh:218-223).
    job = None
    for attempt in (1, 2, 3):
        try:
            resp = _post_json(f"{server}/v1/images/generations", payload)
            job = resp.get("id")
        except Exception:  # noqa: BLE001 — mirror bash's broad catch
            job = None
        if job:
            break
        if attempt < 3:
            time.sleep(10)
    if not job:
        raise RuntimeError("image job submission failed after 3 attempts")

    status = ""
    for _ in range(40):
        time.sleep(30)
        try:
            status = _get_json(f"{server}/v1/images/generations/{job}").get("status", "?")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(60)
            continue
        except Exception:  # noqa: BLE001
            continue
        if status == "completed":
            break
        if status == "failed":
            raise RuntimeError("image generation failed (server reported failure)")
    if status != "completed":
        raise RuntimeError(f"image job did not complete (final status: {status})")

    _download(f"{server}/v1/images/generations/{job}/download", out_path)
    return out_path


def _media_video_request(*, server, model, prompt, image, width, height,
                         num_frames, steps, seed, out_path) -> str:
    """Submit a media-server i2v job, poll to completion, download the video.

    Ports node_image_to_video (run_workflow.sh:265-312): base64-encode the source
    image, POST the i2v job, poll (30 s cadence, ≤40 polls), download to
    *out_path*. Returns *out_path*; raises RuntimeError on failure.
    """
    b64 = base64.b64encode(Path(image).read_bytes()).decode()
    payload = {
        "prompt": prompt,
        "image_prompts": [{"image": b64, "frame_pos": 0}],
        "width": int(width), "height": int(height),
        "num_frames": int(num_frames), "num_inference_steps": int(steps),
        "seed": int(seed),
    }
    resp = _post_json(f"{server}/v1/videos/generations/i2v", payload)
    job = resp.get("id")
    if not job:
        raise RuntimeError(f"video job submission failed: {resp}")

    status = ""
    for _ in range(40):
        time.sleep(30)
        try:
            status = _get_json(f"{server}/v1/videos/generations/{job}").get("status", "?")
        except Exception:  # noqa: BLE001
            continue
        if status == "completed":
            break
        if status == "failed":
            raise RuntimeError("video generation failed (server reported failure)")
    if status != "completed":
        raise RuntimeError(f"video job did not complete (final status: {status})")

    _download(f"{server}/v1/videos/generations/{job}/download", out_path)
    return out_path


def _call_llm(*, server, model, prompt, max_tokens) -> str:
    """POST a chat-completion and return the assistant message text.

    Ports node_generate_text's curl (run_workflow.sh:368-371): honours the node's
    explicit *server* + *model*, disables Qwen-style thinking so the whole token
    budget goes to the answer.
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(max_tokens),
        "chat_template_kwargs": {"enable_thinking": False},
    }
    resp = _post_json(f"{server}/v1/chat/completions", payload, timeout=600)
    return resp["choices"][0]["message"]["content"] or ""


def _run_plugin(plugin_name: str, fn_name: str, *args):
    """Load plugins/<plugin_name>/plugin.py and call fn_name(*args).

    Same importlib dynamic-load pattern the bash uses (run_workflow.sh:319-328);
    returns whatever the plugin function returns (a string for caption/rmbg/depth,
    None for svg/composite which write to disk).
    """
    import importlib.util
    plugin_path = _REPO_ROOT / "plugins" / plugin_name / "plugin.py"
    spec = importlib.util.spec_from_file_location(f"{plugin_name}_plugin", plugin_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, fn_name)(*args)


def _import_artifact(src, media_type, prompt_text, model="workflow"):
    """Copy an artifact into the app media store and return its record id.

    Ports the _import() closure from node-9 (run_workflow.sh:568-623): dedup on a
    previously-imported source_path, copy the file, build a thumbnail, insert a
    MediaRecord. Returns None if the source is missing. Only used on the real
    path (Task 5 QB2 validation) — imports are lazy so dry-run/CI never need the
    store dependencies.
    """
    import shutil
    import sqlite3
    import subprocess
    import uuid
    from datetime import datetime, timezone
    from media_store import media_store as _ms, MediaRecord

    src = Path(src)
    if not src.exists():
        return None

    app_dir = Path.home() / ".local" / "share" / "tt-local-generator"
    images_dir, videos_dir, thumbs_dir = (app_dir / "images",
                                          app_dir / "videos", app_dir / "thumbnails")
    for d in (images_dir, videos_dir, thumbs_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Bug #8 dedup: reuse an existing record if this source was already imported.
    try:
        conn = sqlite3.connect(str(app_dir / "media.db"))
        escaped = str(src).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        row = conn.execute(
            "SELECT id, file_path FROM media WHERE params LIKE ? ESCAPE '\\'",
            (f'%"source_path": "{escaped}"%',),
        ).fetchone()
        conn.close()
        if row and Path(row[1]).exists():
            return row[0]
    except Exception:
        pass  # dedup failure is non-fatal

    ts = datetime.now(timezone.utc)
    rid = str(uuid.uuid4())
    ts_str = ts.strftime("%Y%m%d_%H%M%S")
    dest_dir = videos_dir if media_type == "video" else images_dir
    dest = dest_dir / f"{ts_str}_{rid[:8]}{src.suffix}"
    shutil.copy2(src, dest)

    thumb = thumbs_dir / f"{ts_str}_{rid[:8]}.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(dest), "-vf",
             "scale=200:112:force_original_aspect_ratio=decrease,"
             "pad=200:112:(ow-iw)/2:(oh-ih)/2",
             "-frames:v", "1", "-update", "1", "-q:v", "3", str(thumb)],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
        )
    except Exception:
        shutil.copy2(dest, thumb)

    _ms.add(MediaRecord(
        id=rid, file_path=str(dest), thumbnail_path=str(thumb), prompt=prompt_text,
        media_type=media_type, created_at=ts.isoformat(), model_id=model,
        generator_type=None, starred=0,
        params=json.dumps({
            "workflow": "pipeline",
            "source_path": str(src),
            "video_path": str(dest) if media_type == "video" else "",
            "image_path": str(dest) if media_type != "video" else "",
        }),
    ))
    return rid


def _add_artifacts_to_playlist(playlist_name, artifacts, metadata, emit) -> str:
    """Create/reuse a playlist and add the resolved artifacts. Returns playlist id.

    Ports the node-9 block (run_workflow.sh:534-642). *artifacts* is a list of
    dicts with resolved string ``path`` + ``type`` (+ optional ``label``);
    *metadata* carries the resolved caption/poem for record prompts. Emits a
    ``PLAYLIST:<count>:<name>`` signal for pipeline_runner, matching the bash.
    """
    from playlist_store import PlaylistStore

    caption = str(metadata.get("caption", "")) if metadata else ""
    ps = PlaylistStore()
    pl = ps.get_or_create(playlist_name)

    record_ids = []
    for art in artifacts:
        path = art.get("path")
        # Skip anything not yet resolved to a real string path (e.g. a leftover
        # ['node','key'] wire the engine did not flatten — see report concern).
        if not isinstance(path, str) or not path:
            continue
        mtype = art.get("type", "image")
        label = art.get("label") or (f"{playlist_name}: {caption[:80]}" if caption else playlist_name)
        rid = _import_artifact(path, mtype, label)
        if rid:
            record_ids.append(rid)

    if record_ids:
        ps.add_records(pl.id, record_ids)
    emit(f"PLAYLIST:{len(record_ids)}:{playlist_name}")
    return pl.id


# ── Node handlers ────────────────────────────────────────────────────────────
# Every handler guards ``if ctx.dry_run`` FIRST (identical placeholder to the
# bash script) so the Task-1 dry-run path stays hardware-free. Output keys follow
# the generic per-type contract (image_path/video_path/caption/…), NOT the
# instance names the bash used (poem/composite_path/video_prompt).


@register("TTLGTextToImage")
def _h_text_to_image(nid, inp, ctx):
    out = str(ctx.output_dir / f"node{nid}_image.png")
    if ctx.dry_run:
        return {"image_path": out}
    path = _media_image_request(
        server=inp.get("server", "http://localhost:8000"),
        prompt=inp.get("prompt", ""),
        width=inp.get("width", 1024), height=inp.get("height", 1024),
        steps=inp.get("steps", 4), seed=inp.get("seed", 0),
        negative_prompt=inp.get("negative_prompt"), out_path=out,
    )
    return {"image_path": path}


@register("TTLGImageToVideo")
def _h_image_to_video(nid, inp, ctx):
    out = str(ctx.output_dir / f"node{nid}_video.mp4")
    if ctx.dry_run:
        return {"video_path": out}
    path = _media_video_request(
        server=inp.get("server", "http://localhost:8000"),
        model=inp.get("model", ""), prompt=inp.get("prompt", ""),
        image=inp.get("image"), width=inp.get("width", 960),
        height=inp.get("height", 544), num_frames=inp.get("num_frames", 33),
        steps=inp.get("steps", 20), seed=inp.get("seed", 0), out_path=out,
    )
    return {"video_path": path}


@register("TTLGCaptionImage")
def _h_caption_image(nid, inp, ctx):
    if ctx.dry_run:
        return {"caption": "The 1964 World's Fair Unisphere stands tall against a bright sky."}
    caption = _run_plugin("blip", "caption_image", inp.get("src"), inp.get("prompt", ""))
    return {"caption": caption}


@register("TTLGRemoveBackground")
def _h_remove_background(nid, inp, ctx):
    dest = str(ctx.output_dir / f"node{nid}_fg.png")
    if ctx.dry_run:
        return {"fg_path": dest}
    _run_plugin("rmbg", "remove_background", inp.get("src"), dest)
    return {"fg_path": dest}


@register("TTLGEstimateDepth")
def _h_estimate_depth(nid, inp, ctx):
    dest = str(ctx.output_dir / f"node{nid}_depth.png")
    if ctx.dry_run:
        return {"depth_path": dest}
    _run_plugin("depth", "estimate_depth", inp.get("src"), dest)
    return {"depth_path": dest}


@register("TTLGPromptCompose")
def _h_prompt_compose(nid, inp, ctx):
    tmpl = inp.get("template", "")
    # substitute {key} for every non-template input
    for k, v in inp.items():
        if k != "template":
            tmpl = tmpl.replace("{" + k + "}", str(v))
    return {"prompt": tmpl}


@register("TTLGGenerateText")
def _h_generate_text(nid, inp, ctx):
    if ctx.dry_run:
        return {"text": "The Unisphere gleams in silver light, / "
                        "Tomorrow's promise etched in steel."}
    # Substitute {var} in the prompt for every non-reserved input (e.g. {caption}).
    reserved = {"model", "prompt", "max_tokens", "server"}
    prompt = inp.get("prompt", "")
    for k, v in inp.items():
        if k not in reserved:
            prompt = prompt.replace("{" + k + "}", str(v))
    text = _call_llm(
        server=inp.get("server", "http://localhost:8002"),
        model=inp.get("model", ""), prompt=prompt,
        max_tokens=inp.get("max_tokens", 120),
    )
    return {"text": text}


@register("TTLGSVGRender")
def _h_svg_render(nid, inp, ctx):
    out = str(ctx.output_dir / f"node{nid}_logo.png")
    if ctx.dry_run:
        return {"png_path": out}
    _run_plugin("svg_render", "svg_to_png", inp.get("src"), out,
                int(inp.get("size", 1024)))
    return {"png_path": out}


@register("TTLGComposite")
def _h_composite(nid, inp, ctx):
    out = str(ctx.output_dir / f"node{nid}_composite.jpg")
    if ctx.dry_run:
        return {"image_path": out}
    _run_plugin("composite", "composite_images", inp.get("background_path"),
                inp.get("foreground_path"), out, float(inp.get("scale", 0.72)))
    return {"image_path": out}


@register("TTLGAddToPlaylist")
def _h_add_to_playlist(nid, inp, ctx):
    name = inp.get("playlist_name", "playlist")
    if ctx.dry_run:
        return {"playlist_id": f"dryrun-{nid}"}
    pid = _add_artifacts_to_playlist(
        name, inp.get("artifacts", []), inp.get("metadata", {}) or {}, ctx.emit)
    return {"playlist_id": pid}


# ── Task 7: artgen-plugin + AnimateDiff node handlers ────────────────────────
#
# Both node types shell out to the repo-root `tt-ctl` CLI (a subprocess call)
# rather than importing artgen/animatediff internals directly. This keeps the
# engine decoupled from argparse plumbing and guarantees the node behaves
# identically to running the same `tt-ctl artgen ...` command by hand.

TT_CTL = _REPO_ROOT / "tt-ctl"

# Output-extension buckets that decide which extra key(s) TTLGArtgenGenerate
# publishes alongside artifact_path (raster -> png_path, text-like -> text).
_RASTER_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_TEXT_EXTS = {".txt", ".py", ".ans", ".json", ".svg", ".md"}


def _flag_from_key(key: str) -> str:
    """Node-input key -> CLI flag name: underscores become hyphens.

    e.g. ``negative_prompt`` -> ``--negative-prompt``, matching how every
    artgen/animatediff `add_args` declares its `dest=` (see app/artgen/cli.py
    and app/artgen/generators/animatediff.py).
    """
    return "--" + key.replace("_", "-")


def _append_flag_value(argv: "list[str]", flag: str, value) -> None:
    """Append *flag* (+ value) to *argv* per the shared node-input convention:

      - ``bool True``  -> bare flag (mirrors an argparse ``store_true`` switch)
      - ``bool False`` -> omitted entirely (there is no "--no-foo" negation)
      - ``list``/``tuple`` -> the flag repeated once per item (argparse
        ``action="append"``, e.g. --per-chip-prompt/--tags-style inputs)
      - anything else -> ``flag str(value)``

    bool is checked before the generic branch since ``bool`` is an ``int``
    subclass in Python and would otherwise fall through to ``str(value)``.
    """
    if isinstance(value, bool):
        if value:
            argv.append(flag)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            argv.append(flag)
            argv.append(str(item))
        return
    argv.append(flag)
    argv.append(str(value))


def _run_tt_ctl(argv: "list[str]", timeout: int = 600):
    """Run ``tt-ctl <argv...>`` as a subprocess, capturing output.

    Mirrors the subprocess.run pattern already used by `_import_artifact`'s
    ffmpeg call: `stdin=DEVNULL` so a stalled CLI can never block waiting on
    terminal input, `capture_output=True` so stdout/stderr are available for
    a useful error message. Raises RuntimeError on nonzero exit so callers
    don't have to repeat the check.
    """
    import subprocess
    result = subprocess.run(
        [str(TT_CTL), *argv],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"tt-ctl {' '.join(argv)} failed (exit {result.returncode}): "
            f"{(result.stderr or result.stdout).strip()}"
        )
    return result


@register("TTLGArtgenGenerate")
def _h_artgen_generate(nid, inp, ctx):
    """Generic artgen-plugin node.

    Shells out to ``tt-ctl artgen <plugin> --output <out> [--flag value...]``.
    Every input except ``plugin`` (the artgen TYPE, e.g. verse/palette/ansi)
    and ``ext`` (output extension, default ``.txt``) is mapped to a CLI flag
    via `_flag_from_key`/`_append_flag_value`, so any plugin-specific flag the
    generator's `add_args` declares can be wired without an engine change.

    Output contract: ``artifact_path`` always; ``png_path`` added when the
    extension is a raster image; ``text`` added (best-effort file read) when
    the extension is text-like.
    """
    plugin = inp.get("plugin")
    ext = str(inp.get("ext", ".txt"))
    if not ext.startswith("."):
        ext = "." + ext
    ext_l = ext.lower()
    out = str(ctx.output_dir / f"node{nid}_artifact{ext}")

    if ctx.dry_run:
        # Mirror the real branch's key contract exactly (Nit 5): artifact_path
        # always; png_path only for raster exts; text only for text-like exts.
        result = {"artifact_path": out}
        if ext_l in _RASTER_EXTS:
            result["png_path"] = out
        if ext_l in _TEXT_EXTS:
            result["text"] = "placeholder artifact text"
        return result

    argv = ["artgen", plugin, "--output", out]
    for key, value in inp.items():
        if key in ("plugin", "ext") or value is None:
            continue
        _append_flag_value(argv, _flag_from_key(key), value)
    _run_tt_ctl(argv)

    result = {"artifact_path": out}
    if ext_l in _RASTER_EXTS:
        result["png_path"] = out
    if ext_l in _TEXT_EXTS:
        try:
            result["text"] = Path(out).read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 — best-effort read-back
            result["text"] = ""
    return result


def _normalize_prompt_schedule_entry(item) -> str:
    """Normalize one prompt_schedule entry to a ``FRAME:PROMPT`` string.

    Accepts either an already-formed ``"FRAME:PROMPT"`` string (passed
    through verbatim — a wired upstream node may already produce this form)
    or a ``[frame, prompt]``/``(frame, prompt)`` pair (the more natural shape
    for a spec author to wire by hand).
    """
    if isinstance(item, str):
        return item
    if isinstance(item, (list, tuple)) and len(item) == 2:
        frame, prompt = item
        return f"{frame}:{prompt}"
    raise ValueError(f"invalid prompt_schedule entry: {item!r}")


@register("TTLGAnimateDiff")
def _h_animatediff(nid, inp, ctx):
    """AnimateDiff node.

    Shells out to ``tt-ctl artgen animatediff --output <gif> [--flag value...]``.
    ``per_chip_prompts`` and ``prompt_schedule`` need special serialization
    (repeated ``--per-chip-prompt``, and ``--prompt-schedule FRAME:PROMPT``
    respectively) so they're excluded from the generic mapping and handled
    explicitly; every other provided input (prompt/frames/steps/seed/
    negative_prompt/multichip_mode/seed_spread/ramp/ramp_lo/ramp_hi/
    stitch_order/loop/… ) maps generically via `_flag_from_key`/
    `_append_flag_value`, exactly like TTLGArtgenGenerate.
    """
    gif = str(ctx.output_dir / f"node{nid}.gif")
    if ctx.dry_run:
        return {"gif_path": gif}

    special = {"per_chip_prompts", "prompt_schedule"}
    argv = ["artgen", "animatediff", "--output", gif]
    for key, value in inp.items():
        if key in special or value is None:
            continue
        _append_flag_value(argv, _flag_from_key(key), value)

    for item in inp.get("per_chip_prompts") or []:
        argv += ["--per-chip-prompt", str(item)]

    for item in inp.get("prompt_schedule") or []:
        argv += ["--prompt-schedule", _normalize_prompt_schedule_entry(item)]

    _run_tt_ctl(argv, timeout=1800)  # animatediff generation can run long
    return {"gif_path": gif}


# ── Backend server-switching (Issue 1) ───────────────────────────────────────
#
# Ports the hardware-management logic from bin/run_worlds_fair.sh
# (stop_and_reset ~L120, start_server ~L139). Node handlers still just POST to
# the ``server`` URL their inputs carry; run() makes sure the right backend is
# up *before* dispatching each node, switching servers only when the backend
# a node needs differs from the one currently active.

# Sentinel backend keys (not real server_manager keys):
#   CHIPS_FREE    — AnimateDiff: runs generate.py directly on the chips (no
#                   media server). Stop+reset any running server but start none.
#   ARTGEN_DETECT — an LLM node whose model we couldn't confidently map to a
#                   start key. Resolved at runtime by probing for an already-up
#                   OpenAI-compatible endpoint; only starts a default artgen LLM
#                   if none is found. (See _real_start_server.)
CHIPS_FREE = "__chips_free__"
ARTGEN_DETECT = "__artgen_detect__"

# Default artgen LLM to start when ARTGEN_DETECT finds nothing already running.
_ARTGEN_DEFAULT_KEY = "artgen-qwen3-8b"

# max_wait (minutes) per backend family. These are NOT stored in
# server_manager.SERVERS (which only knows health_url/runner_key), so they are
# chosen here to mirror the values bin/run_worlds_fair.sh passed to start_server
# (flux 30, skyreels 60, artgen 30).
_MAX_WAIT_IMAGE = 30
_MAX_WAIT_VIDEO = 60
_MAX_WAIT_LLM = 30


@dataclass(frozen=True)
class BackendSpec:
    """The backend a node needs.

    key         — server_manager start key, or a sentinel (CHIPS_FREE /
                  ARTGEN_DETECT).
    health_url  — URL to poll for readiness after start (None when nothing is
                  started, e.g. CHIPS_FREE).
    max_wait    — minutes to wait for readiness.
    start       — whether a server should be started after stop/reset (False for
                  CHIPS_FREE, which only stops/resets).
    """
    key: str
    health_url: "str | None"
    max_wait: int = 30
    start: bool = True


def _artgen_uses_llm(plugin_name: "str | None") -> bool:
    """Return True if the named artgen plugin drives the chat LLM backend.

    Consults the artgen generator registry's ``uses_llm`` flag (all built-in
    generators are LLM-backed). Defaults to True on any failure — assuming a
    node needs the LLM is the safe choice (it ensures a backend is up rather
    than silently running against nothing). Isolated in its own helper so unit
    tests can monkeypatch it without importing the whole artgen package.
    """
    try:
        import artgen
        return bool(getattr(artgen.get(plugin_name), "uses_llm", True))
    except Exception:  # noqa: BLE001 — unknown plugin / import failure → assume LLM
        return True


def _artgen_key_for_model(model: "str | None") -> "str | None":
    """Map an LLM model string to its artgen server_manager start key.

    Matches the node's model against each artgen ServerDef's ``--model``
    extra_arg, normalising away any HuggingFace org prefix
    (``meta-llama/Llama-3.3-70B-Instruct`` → ``llama-3.3-70b-instruct``) and
    case. Returns None when *model* is empty or matches no artgen server — the
    caller then falls back to ARTGEN_DETECT.
    """
    if not model:
        return None
    want = str(model).rsplit("/", 1)[-1].strip().lower()
    for sdef in sm.SERVERS.values():
        if "artgen" not in sdef.capabilities:
            continue
        extra = sdef.extra_args
        # extra_args is a flat tuple like ("--model", "Qwen3-8B").
        for i, tok in enumerate(extra):
            if tok == "--model" and i + 1 < len(extra):
                if extra[i + 1].strip().lower() == want:
                    return sdef.key
    return None


def _artgen_backend(model: "str | None") -> BackendSpec:
    """BackendSpec for an artgen-LLM node given its (maybe-None) model string."""
    key = _artgen_key_for_model(model)
    if key is not None:
        return BackendSpec(key, sm.SERVERS[key].health_url, _MAX_WAIT_LLM)
    # Unmapped: resolve at runtime by detecting an already-running endpoint.
    return BackendSpec(ARTGEN_DETECT, sm.SERVERS[_ARTGEN_DEFAULT_KEY].health_url,
                       _MAX_WAIT_LLM)


def _backend_for(class_type: str, inputs: dict) -> "BackendSpec | None":
    """Return the backend a node needs, or None when no switch is required.

    None means "CPU-only node — leave whatever backend is currently up alone"
    (caption/rmbg/depth/prompt-compose/svg/composite/playlist, and non-LLM
    artgen plugins).
    """
    model = inputs.get("model")

    if class_type == "TTLGTextToImage":
        m = str(model or "").lower()
        if "flux" in m:
            key = "flux"
        elif "sdxl" in m or "sd" in m:
            key = "sdxl" if "sdxl" in sm.SERVERS else "flux"
        else:
            key = "flux"  # default image backend
        return BackendSpec(key, sm.SERVERS[key].health_url, _MAX_WAIT_IMAGE)

    if class_type == "TTLGImageToVideo":
        m = str(model or "").lower()
        if "skyreels" in m:
            key = "skyreels"
        elif "wan" in m:
            key = "wan2.2"
        elif "mochi" in m:
            key = "mochi"
        else:
            key = "wan2.2"  # default video backend
        return BackendSpec(key, sm.SERVERS[key].health_url, _MAX_WAIT_VIDEO)

    if class_type == "TTLGGenerateText":
        return _artgen_backend(model)

    if class_type == "TTLGArtgenGenerate":
        if not _artgen_uses_llm(inputs.get("plugin")):
            return None  # purely algorithmic plugin — no LLM backend needed
        return _artgen_backend(model)

    if class_type == "TTLGAnimateDiff":
        # Chips-free: stop+reset any media server but start none — AnimateDiff
        # drives the chips itself via `tt-ctl artgen animatediff`.
        return BackendSpec(CHIPS_FREE, None, 0, start=False)

    # Everything else (caption/rmbg/depth/prompt-compose/svg/composite/playlist,
    # unknown types) → no backend switch.
    return None


# ── Low-level side-effecting primitives (mockable by unit tests) ──────────────

def _docker_stop_all() -> None:
    """Stop every running docker container (bash: ``docker ps -q | xargs docker stop``)."""
    import subprocess
    ids = subprocess.run(["docker", "ps", "-q"], stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, timeout=30).stdout.split()
    if ids:
        subprocess.run(["docker", "stop", *ids], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=120)
        time.sleep(3)


def _tt_smi_reset() -> None:
    """Reset the TT boards (bash: ``tt-smi -r``)."""
    import subprocess
    subprocess.run(["tt-smi", "-r"], stdin=subprocess.DEVNULL,
                   capture_output=True, text=True, timeout=120)
    time.sleep(8)


def _real_start_server(key: str, health_url: "str | None", max_wait: int,
                       emit: Callable[[str], None]) -> None:
    """Start a backend server and poll its health URL until ready.

    Ports start_server() from bin/run_worlds_fair.sh. For the ARTGEN_DETECT
    sentinel it first probes for an already-running OpenAI-compatible endpoint
    (any port) via artgen.detect_artgen_endpoint and, only if none is up, starts
    the default artgen LLM. Raises RuntimeError if the server never becomes
    ready within *max_wait* minutes.
    """
    key_to_start = key
    if key == ARTGEN_DETECT:
        try:
            from artgen import detect_artgen_endpoint
            base, model = detect_artgen_endpoint()
        except Exception:  # noqa: BLE001
            base, model = None, None
        if base:
            emit(f"LOG:  LLM already up at {base} ({model}) — no start needed")
            return
        key_to_start = _ARTGEN_DEFAULT_KEY
        emit(f"LOG:  no LLM detected — starting default {key_to_start}")

    emit(f"LOG:  starting server {key_to_start}")
    sm.start(key_to_start)  # non-blocking --gui start

    if not health_url:
        return
    deadline = time.time() + max_wait * 60
    while time.time() < deadline:
        time.sleep(30)
        try:
            data = _get_json(health_url, timeout=10)
        except Exception:  # noqa: BLE001
            continue
        if data.get("model_ready") or data.get("data"):
            emit(f"LOG:  ✅ {key_to_start} ready")
            return
    raise RuntimeError(f"{key_to_start} did not become ready within {max_wait} min")


# ── Switch helpers (mockable by unit tests) ───────────────────────────────────

def _stop_and_reset(next_key: str, current: str, *, dry_run: bool,
                    emit: Callable[[str], None]) -> None:
    """Stop running containers and (when switching from a prior backend) reset
    the boards, in preparation for *next_key*.

    Mirrors bin/run_worlds_fair.sh:stop_and_reset. In dry_run it only emits an
    intended-switch log line — no docker / tt-smi calls. tt-smi -r runs ONLY
    when *current* is truthy (i.e. we are switching away from a real backend),
    matching the bash guard.
    """
    if dry_run:
        note = f" + tt-smi -r (switch {current or 'none'} → {next_key})" if current else ""
        emit(f"LOG:  [dry-run] docker stop all{note}")
        return
    _docker_stop_all()
    if current:
        emit(f"LOG:  resetting boards (switch {current} → {next_key})")
        _tt_smi_reset()


def _start_server(key: str, health_url: "str | None", max_wait: int, *,
                  dry_run: bool, emit: Callable[[str], None]) -> None:
    """Start backend *key* and wait for readiness.

    In dry_run it only emits an intended-start log line — no tt-ctl / network.
    """
    if dry_run:
        emit(f"LOG:  [dry-run] tt-ctl start {key}")
        return
    _real_start_server(key, health_url, max_wait, emit)


def run(spec: dict, *, dry_run: bool = False, emit: Callable[[str], None] = print,
        output_dir: "str | None" = None) -> dict:
    out_dir = Path(output_dir) if output_dir else Path("/tmp/tt-pipeline-dryrun")
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx = _Ctx(out_dir, dry_run, emit)
    results: dict = {}
    # Backend currently active across the node loop ("" = none started yet).
    # ARTGEN_DETECT is handled out-of-band (it never stops/resets — it reuses
    # whatever LLM happens to be up) so it does not participate in this tracking.
    current_backend = ""
    for nid in topo_order(spec):
        node = spec[nid]
        ct = node["class_type"]

        # ── Backend switch (before dispatch) ─────────────────────────────────
        backend = _backend_for(ct, node.get("inputs", {}))
        if backend is not None:
            if backend.key == ARTGEN_DETECT:
                # Reuse any already-running LLM; only start one if none is up.
                # Deliberately does NOT stop/reset the current backend, so a
                # live LLM elsewhere is never killed.
                _start_server(backend.key, backend.health_url, backend.max_wait,
                              dry_run=dry_run, emit=emit)
            elif backend.key != current_backend:
                _stop_and_reset(backend.key, current_backend,
                                dry_run=dry_run, emit=emit)
                if backend.start:
                    _start_server(backend.key, backend.health_url,
                                  backend.max_wait, dry_run=dry_run, emit=emit)
                current_backend = backend.key

        emit(f"NODE:{nid}:running:{ct}")
        handler = HANDLERS.get(ct)
        if handler is None:
            emit(f"NODE:{nid}:failed:unknown class_type {ct}")
            raise ValueError(f"no handler for class_type {ct}")
        try:
            inputs = resolve_inputs(node.get("inputs", {}), results)
            results[nid] = handler(nid, inputs, ctx) or {}
            emit(f"NODE:{nid}:done:{ct}")
        except Exception as e:  # noqa: BLE001
            emit(f"NODE:{nid}:failed:{e}")
            # Issue 2: honor the optional flag. COMPATIBILITY_MAP is the single
            # source of truth; unknown class_types are treated as required.
            entry = COMPATIBILITY_MAP.get(ct)
            optional = bool(entry and entry.get("optional", False))
            if optional:
                results[nid] = {}   # leave outputs absent; continue the run
                continue
            raise
    return results


def main(argv: "list[str] | None" = None) -> dict:
    """CLI entry point: parse args, load the spec, and run the engine.

    Split out from the ``if __name__ == "__main__"`` guard so it is directly
    unit-testable (monkeypatch ``run`` and call ``main([...])``) without
    spawning a subprocess. ``--output-dir`` lets bin/run_workflow.sh (the thin
    shim) forward its timestamped output directory through to the engine so
    node artifacts land next to the run's log/results files instead of the
    engine's own default (``/tmp/tt-pipeline-dryrun``).
    """
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--output-dir", default=None)
    a = ap.parse_args(argv)
    return run(load_spec(a.spec), dry_run=a.dry_run,
               emit=lambda s: print(s, flush=True), output_dir=a.output_dir)


if __name__ == "__main__":
    main()
