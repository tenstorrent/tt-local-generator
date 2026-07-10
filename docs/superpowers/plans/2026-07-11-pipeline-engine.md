# Pipeline Engine (SP-A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build a generic Python pipeline engine that actually executes any ComfyUI-API-v1 spec (today's `run_workflow.sh` is a hardcoded stub), prove it on the real 1964 World's Fair spec, then add artgen + AnimateDiff node coverage.

**Architecture:** New `app/pipeline_engine.py` — `load_spec` → `topo_order` → `run(dry_run, emit)` dispatching each node's `class_type` to a handler in a `HANDLERS` table; wires resolved from prior nodes' outputs; emits the `NODE:`/`LOG:`/`PLAYLIST:` signals `pipeline_runner.py` already parses. `run_workflow.sh` becomes a thin shim over it. Handlers port the working logic from `run_workflow.sh:188-420`.

**Tech Stack:** Python 3.12 (`/usr/bin/python3` for tests; the tt-inference venv `~/.tenstorrent-venv/bin/python3` at runtime, as the shim uses), pytest. Shells out to the media server API, `call_llm`, `plugins/*/plugin.py`, and `app/artgen/cli.py`. Hardware only for the acceptance runs.

## Global Constraints

- Engine lives in `app/pipeline_engine.py`, **no GTK imports** (like `pipeline_runner.py`).
- Signal strings must match `pipeline_runner.py`'s parser EXACTLY: `NODE:<id>:<status>:<detail>`, `LOG:<path>`, `PLAYLIST:<count>:<name>` (see `app/pipeline_runner.py:69-90`).
- Spec format = ComfyUI-API-v1: top-level `_`-prefixed metadata + numbered node keys; each node `{ "class_type": str, "inputs": { key: value | [src_id, out_key] } }`. A list value is a wire.
- `--dry-run` must run the whole graph with zero hardware/API/network (placeholder outputs), so the engine is CI-testable.
- Preserve `pipeline_runner.py`'s interface: it launches `bash bin/run_workflow.sh <spec>` (`pipeline_runner.py:147`) and reads stdout — do not change that call site.
- Handler output keys are the contract in `docs/superpowers/specs/2026-07-11-pipeline-node-coverage-design.md` — do not rename.
- No version bump (infrastructure on the 0.12.0 `feat/pipeline-editor` branch); changelog note only.
- Milestone 1 (engine proven on the 1964 spec) is the gate before Milestone 2 (new node types).

## File Structure

- Create: `app/pipeline_engine.py` — the engine (load/topo/run/dispatch/handlers/signals/server-switch).
- Modify: `bin/run_workflow.sh` — replace the hardcoded body with a shim that execs the engine.
- Modify: `app/artgen/generators/animatediff.py` (`AnimateDiffGenerator.add_args`) + `app/artgen/cli.py` (animatediff branch) — multichip flags (Milestone 2).
- Modify: `app/workflow_compat.py` — register new class_types (Milestone 2).
- Create: `tests/test_pipeline_engine.py` — topo/wire/dispatch/dry-run/signal tests.
- Create: `tests/fixtures/mini_pipeline.json` — a tiny dry-run fixture spec.

---

## MILESTONE 1 — engine, proven on World's Fair

### Task 1: Engine core — load_spec, topo_order, run/emit, dispatch table (all handlers stubbed to dry-run)

**Files:** Create `app/pipeline_engine.py`; Create `tests/test_pipeline_engine.py`, `tests/fixtures/mini_pipeline.json`

**Interfaces produced:**
- `load_spec(path: str) -> dict` (returns `{node_id: {"class_type","inputs"}}`, metadata stripped)
- `topo_order(spec: dict) -> list[str]` (raises `ValueError` on cycle / dangling wire)
- `resolve_inputs(inputs: dict, results: dict) -> dict` (wires `[id,key]` → `results[id][key]`)
- `run(spec, *, dry_run=False, emit=print) -> dict` (results by node)
- `HANDLERS: dict[str, callable]` — `class_type → handle(node_id, inputs, ctx) -> dict`
- `_is_wire(v) -> bool` (`isinstance(v, list) and len(v)==2 and isinstance(v[0], str)`)

- [ ] **Step 1: Write the failing test**

Create `tests/fixtures/mini_pipeline.json`:
```json
{
  "_spec_version": "comfyui-api-v1",
  "1": {"class_type": "TTLGTextToImage", "inputs": {"model": "FLUX.1-schnell", "prompt": "a test", "width": 512, "height": 512}},
  "2": {"class_type": "TTLGPromptCompose", "inputs": {"template": "{caption}, cinematic", "caption": ["1", "image_path"]}},
  "3": {"class_type": "TTLGGenerateText", "inputs": {"prompt": "poem", "caption": ["2", "prompt"]}}
}
```

Create `tests/test_pipeline_engine.py`:
```python
import importlib.util, sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
_ENGINE = Path(__file__).parent.parent / "app" / "pipeline_engine.py"

def _load():
    spec = importlib.util.spec_from_file_location("pipeline_engine", _ENGINE)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

eng = _load()
_FIX = Path(__file__).parent / "fixtures" / "mini_pipeline.json"

def test_load_spec_strips_metadata_keeps_nodes():
    spec = eng.load_spec(str(_FIX))
    assert set(spec) == {"1", "2", "3"}
    assert spec["1"]["class_type"] == "TTLGTextToImage"

def test_topo_order_respects_wires():
    spec = eng.load_spec(str(_FIX))
    order = eng.topo_order(spec)
    assert order.index("1") < order.index("2") < order.index("3")

def test_topo_order_detects_cycle():
    spec = {"1": {"class_type": "X", "inputs": {"a": ["2", "k"]}},
            "2": {"class_type": "X", "inputs": {"a": ["1", "k"]}}}
    with pytest.raises(ValueError):
        eng.topo_order(spec)

def test_topo_order_detects_dangling_wire():
    spec = {"1": {"class_type": "X", "inputs": {"a": ["99", "k"]}}}
    with pytest.raises(ValueError):
        eng.topo_order(spec)

def test_resolve_inputs_substitutes_wires():
    results = {"1": {"image_path": "/tmp/x.png"}}
    out = eng.resolve_inputs({"caption": ["1", "image_path"], "lit": 5}, results)
    assert out == {"caption": "/tmp/x.png", "lit": 5}

def test_dry_run_emits_signals_and_publishes_keys():
    spec = eng.load_spec(str(_FIX))
    lines = []
    results = eng.run(spec, dry_run=True, emit=lines.append)
    # every node ran and published its documented dry-run output
    assert "image_path" in results["1"]
    assert results["2"]["prompt"]           # PromptCompose fills prompt
    assert "text" in results["3"]
    # signals: a running+done per node, in topo order
    assert any(l == "NODE:1:running:" or l.startswith("NODE:1:running") for l in lines)
    assert any(l.startswith("NODE:3:done") for l in lines)
```

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_pipeline_engine.py -q`
Expected: FAIL — `app/pipeline_engine.py` doesn't exist.

- [ ] **Step 3: Implement the engine core**

Create `app/pipeline_engine.py`:
```python
#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generic pipeline engine — executes a ComfyUI-API-v1 spec.

Replaces the hardcoded run_workflow.sh stub. Loads a spec, topologically orders
nodes by their wire dependencies, dispatches each node's class_type to a handler,
resolves wired inputs from prior outputs, and emits NODE:/LOG:/PLAYLIST: signals
that app/pipeline_runner.py parses. --dry-run runs the whole graph with placeholder
outputs (no hardware/API), for CI.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import Callable


def _is_wire(v) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)


def load_spec(path: str) -> dict:
    raw = json.loads(Path(path).read_text())
    return {k: v for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, dict) and "class_type" in v}


def topo_order(spec: dict) -> "list[str]":
    # Kahn's algorithm over wire edges (src -> node).
    deps = {nid: set() for nid in spec}
    for nid, node in spec.items():
        for v in node.get("inputs", {}).values():
            if _is_wire(v):
                src = v[0]
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
    out = {}
    for k, v in inputs.items():
        out[k] = results[v[0]][v[1]] if _is_wire(v) else v
    return out


class _Ctx:
    """Runtime context passed to handlers: output dir, dry_run, emit, server switch."""
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


# Minimal dry-run-capable stubs so Task 1 tests pass; Task 2 replaces the bodies
# with real work (keeping the same output keys). PromptCompose is real here (pure).
@register("TTLGTextToImage")
def _h_text_to_image(nid, inp, ctx):
    return {"image_path": str(ctx.output_dir / f"node{nid}_image.png")}

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
    return {"text": "placeholder text" if ctx.dry_run else ""}


def run(spec: dict, *, dry_run: bool = False, emit: Callable[[str], None] = print,
        output_dir: "str | None" = None) -> dict:
    out_dir = Path(output_dir) if output_dir else Path("/tmp/tt-pipeline-dryrun")
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx = _Ctx(out_dir, dry_run, emit)
    results: dict = {}
    for nid in topo_order(spec):
        node = spec[nid]
        ct = node["class_type"]
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
            raise
    return results


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(load_spec(a.spec), dry_run=a.dry_run, emit=lambda s: print(s, flush=True))
```

- [ ] **Step 4: Run to verify pass**

Run: `/usr/bin/python3 -m pytest tests/test_pipeline_engine.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**
```bash
git add app/pipeline_engine.py tests/test_pipeline_engine.py tests/fixtures/mini_pipeline.json
git commit -m "feat(pipeline): generic engine core (load/topo/dispatch/dry-run)"
```

---

### Task 2: Port the 10 real node handlers (bash → Python)

**Files:** Modify `app/pipeline_engine.py`; add handler tests to `tests/test_pipeline_engine.py`.

**Port source (verbatim logic, `bin/run_workflow.sh`):** `node_text_to_image` (188-264), `node_image_to_video` (265-314), `node_caption_image` (315-330), `node_remove_background` (331-346), `node_estimate_depth` (347-362), `node_generate_text` (363-375), `node_svg_render` (376-391), `node_composite` (392-420). PromptCompose is already real (Task 1). AddToPlaylist: port the media-store playlist add (grep `AddToPlaylist` / `ensure_auto_playlists`).

**Interfaces produced:** real bodies for each `HANDLERS[...]` entry, preserving the output keys in the spec's contract table (`image_path`/`video_path`/`text`/`caption`/`fg_path`/`depth_path`/`prompt`/`png_path`/`image_path`/`playlist_id`). Each handler: `if ctx.dry_run: return <placeholder dict>` first (so Task-1 dry-run still works), else do the real work.

- [ ] **Step 1: Write failing tests** — for each CPU-plugin handler, a test that (dry_run=True) returns the right key, and (real, with the plugin subprocess monkeypatched) shells out to the correct `plugins/<x>/plugin.py`. Example:
```python
def test_caption_handler_dry_run_key():
    ctx = eng._Ctx(Path("/tmp"), True, lambda s: None)
    assert "caption" in eng.HANDLERS["TTLGCaptionImage"]("2", {"src": "/tmp/a.png"}, ctx)

def test_text_to_image_builds_media_request(monkeypatch):
    calls = {}
    monkeypatch.setattr(eng, "_media_image_request",
                        lambda **k: calls.setdefault("req", k) or "/tmp/out.png")
    ctx = eng._Ctx(Path("/tmp"), False, lambda s: None)
    out = eng.HANDLERS["TTLGTextToImage"]("1",
        {"model": "FLUX.1-schnell", "prompt": "x", "width": 1024, "height": 1024}, ctx)
    assert out["image_path"] == "/tmp/out.png"
    assert calls["req"]["prompt"] == "x"
```
(Extract the API/plugin calls into small helper fns — `_media_image_request`, `_media_video_request`, `_call_llm`, `_run_plugin` — so handlers are unit-testable with those helpers mocked.)

- [ ] **Step 2: Run → fail** (`pytest tests/test_pipeline_engine.py -q`; handlers still stubs).
- [ ] **Step 3: Implement** — port each handler; factor the shared HTTP/JWT/poll logic (from `run_workflow.sh:188-314`) into `_media_image_request` / `_media_video_request`; LLM via `from artgen import call_llm, detect_artgen_endpoint`; CPU plugins via `_run_plugin(plugin_name, *args)` that spawns `plugins/<name>/plugin.py`. Keep DRY_RUN placeholders identical to the bash script's.
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** `feat(pipeline): port real node handlers to the engine`.

---

### Task 3: run_workflow.sh → thin shim over the engine

**Files:** Modify `bin/run_workflow.sh`.

- [ ] **Step 1** Write a test (`tests/test_pipeline_engine.py`) asserting the shim contains no hardcoded node sequence and execs the engine:
```python
def test_run_workflow_is_a_shim():
    sh = (Path(__file__).parent.parent / "bin" / "run_workflow.sh").read_text()
    assert "pipeline_engine.py" in sh
    assert "SEED_PROMPT_PLACEHOLDER" not in sh   # the stub is gone
```
- [ ] **Step 2** Run → fail.
- [ ] **Step 3** Replace the body of `bin/run_workflow.sh` (keep the arg parse + `PYTHON3` resolution at lines 20-38) with:
```bash
exec "$PYTHON3" "$REPO_ROOT/app/pipeline_engine.py" "$WORKFLOW" ${DRY_RUN:+--dry-run}
```
(The engine emits `LOG:`/`NODE:` itself; move the `LOG:` tee into the engine or keep a wrapper that tees — preserve the `LOG:<path>` line `pipeline_runner.py` expects.)
- [ ] **Step 4** Run → pass; also run `tests/test_pipeline_runner.py` (unchanged, must stay green — it tests signal parsing).
- [ ] **Step 5** Commit `refactor(pipeline): run_workflow.sh becomes a shim over pipeline_engine`.

---

### Task 4: 1964 World's Fair dry-run + wiring assertion

**Files:** add tests to `tests/test_pipeline_engine.py`.

- [ ] **Step 1** Test that the real spec loads, topo-orders (1→{2,3,4}→5→6, 2→7→8), dry-runs, and publishes every documented key:
```python
def test_1964_worlds_fair_dry_run():
    spec = eng.load_spec("docs/examples/workflows/1964-worlds-fair.json")
    order = eng.topo_order(spec)
    assert order.index("1") < order.index("2") < order.index("5") < order.index("6")
    r = eng.run(spec, dry_run=True, emit=lambda s: None)
    assert r["1"]["image_path"] and r["6"]["video_path"] and r["8"]["image_path"]
    assert r["7"]["text"]        # the poem
```
- [ ] **Steps 2-4** Run → (should pass once Task 2 handlers exist) → confirm.
- [ ] **Step 5** Commit `test(pipeline): 1964 World's Fair dry-run + wiring`.

---

### Task 5: QB2 acceptance run of the 1964 spec (Milestone 1 gate — controller-run)

Not TDD — a hardware validation the controller runs (like earlier QB2 smokes).
- [ ] Run `bash bin/run_workflow.sh docs/examples/workflows/1964-worlds-fair.json` on QB2. Confirm it produces node1 seed image → node6 video → node8 poem image (files in the run's OUTPUT_DIR), server-switching FLUX→(cpu)→SkyReels→artgen-LLM cleanly. Record artifacts + timing in the build report. **This is the gate before Milestone 2.**

---

## MILESTONE 2 — node coverage (artgen + AnimateDiff)

### Task 6: CLI multichip flags (animatediff)
**Files:** `app/artgen/generators/animatediff.py` (`add_args`), `app/artgen/cli.py` (animatediff branch ~147-169), `tests/test_animatediff_multichip.py`.
- [ ] TDD: add `--mode`, `--per-chip-prompt` (append, repeatable), `--seed-spread`, `--ramp`, `--stitch-order` to `add_args`; forward them from the cli animatediff branch into `run_subprocess(multichip_mode=…, per_chip_prompts=…, seed_spread=…, ramp=…, stitch_order=…)`. Unit-test the flag→kwarg mapping (run_subprocess mocked). Commit.

### Task 7: `TTLGArtgenGenerate` + `TTLGAnimateDiff` handlers
**Files:** `app/pipeline_engine.py`, `tests/test_pipeline_engine.py`.
- [ ] TDD: `HANDLERS["TTLGArtgenGenerate"]` → `app/artgen/cli.py <plugin> … --output <out>`; outputs `text`/`artifact_path`/`png_path` (raster plugins). `HANDLERS["TTLGAnimateDiff"]` → `app/artgen/cli.py animatediff --output <gif> --mode …`; output `gif_path`. Dry-run placeholders. Tests: dry-run keys + that the built CLI argv carries the right plugin/params (subprocess mocked). Commit.

### Task 8: workflow_compat registration + validation tests
**Files:** `app/workflow_compat.py`, `tests/test_workflow_compat.py`.
- [ ] Add `TTLGArtgenGenerate`, `TTLGAnimateDiff` to `COMPATIBILITY_MAP` (native tier) + document the output-key contract. Tests: `validate_spec` accepts specs using them + each artgen plugin; rejects an unknown plugin. Commit.

### Task 9: small QB2 run (controller-run)
- [ ] Author a 3-node throwaway spec (artgen `verse` → PromptCompose → `TTLGAnimateDiff` mode=remix) and run it on QB2; confirm a text artifact + a stitched GIF. Record. (Full funky pipelines are SP-B.)

### Task 10: Changelog note
- [ ] Add a bullet to the 0.12.0 changelog stanza: "pipeline: real generic execution engine (app/pipeline_engine.py) replacing the run_workflow.sh stub; artgen + AnimateDiff node coverage; CLI gains multichip flags." No version bump. Commit.

---

## Self-Review

**Spec coverage:** engine (load/topo/run/dispatch/signals/server-switch) → Tasks 1-3; 10 handlers ported → Task 2; 1964 proof (dry + real) → Tasks 4-5; artgen+animatediff nodes → Tasks 6-8; validation → Task 8; QB2 acceptance → Tasks 5,9; changelog → Task 10. ✓
**Placeholder scan:** engine core is complete code; handler ports cite exact bash source line ranges + give signatures/tests (the bash IS the reference — reproducing 230 lines of bash-with-heredocs as prose would be less accurate than porting from source). Milestone-2 tasks are concrete (design fixed in the spec). No TBD/TODO.
**Type consistency:** `load_spec`/`topo_order`/`resolve_inputs`/`run`/`HANDLERS`/`register`/`_Ctx`/`_is_wire` names identical across tasks; output keys match the spec's contract table; signal strings match `pipeline_runner.py`.
