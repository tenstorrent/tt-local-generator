# Pipeline Mode — Plan 3: Retry + Workflow Compatibility

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the Pipeline feature with two capabilities: (1) working retry_node/retry_job so failed cells can be re-run from the UI, and (2) a workflow compatibility layer (`workflow_compat.py`) that maps ComfyUI node types to tt-local-generator equivalents, shows preflight warnings for skippable nodes, and blocks on truly unknown required nodes.

**Architecture:** `retry_node` builds a minimal single-node spec from the existing results.json, launches run_workflow.sh with that micro-spec, and wires the output back into the phase grid. `workflow_compat.py` is a pure-Python module (no GTK) with a `COMPATIBILITY_MAP` and `validate_spec()` function; PipelinePanel calls it before enabling the Run button and shows a preflight warning widget.

**Tech Stack:** Python 3.12, stdlib only. No new dependencies.

---

## File structure

| File | Role |
|---|---|
| `app/workflow_compat.py` | `COMPATIBILITY_MAP`, `validate_spec()`, `ValidationResult` |
| `app/pipeline_runner.py` | Implement `retry_node()` and `retry_job()` (remove NotImplementedError stubs) |
| `app/pipeline_panel.py` | Wire preflight validation — show warning before Run, disable Run on blocking errors |
| `bin/run_single_node.sh` | Minimal shell script: run one node from an existing results.json context |
| `tests/test_workflow_compat.py` | Unit tests for COMPATIBILITY_MAP and validate_spec() |
| `tests/test_pipeline_runner_retry.py` | Unit tests for retry_node and retry_job |

---

## Task 1: workflow_compat.py — compatibility map and preflight validation

**Files:**
- Create: `app/workflow_compat.py`
- Create: `tests/test_workflow_compat.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_workflow_compat.py`:

```python
"""Tests for workflow compatibility layer."""
from __future__ import annotations
import sys, json, tempfile
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def write_spec(nodes: dict) -> str:
    spec = {"_description": "test", "_spec_version": "comfyui-api-v1"}
    spec.update(nodes)
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    json.dump(spec, f); f.close()
    return f.name


def test_all_native_nodes_valid():
    from workflow_compat import validate_spec
    path = write_spec({
        "1": {"class_type": "TTLGTextToImage",  "inputs": {}, "outputs": ["image_path"]},
        "4": {"class_type": "TTLGImageToVideo",  "inputs": {}, "outputs": ["video_path"]},
        "9": {"class_type": "TTLGAddToPlaylist", "inputs": {}, "outputs": []},
    })
    result = validate_spec(path)
    assert result.ok is True
    assert result.warnings == []
    assert result.blocking == []


def test_skippable_node_produces_warning():
    from workflow_compat import validate_spec
    path = write_spec({
        "1": {"class_type": "TTLGTextToImage",  "inputs": {}, "outputs": ["image_path"]},
        "2": {"class_type": "ControlNetApply",   "inputs": {}, "outputs": ["conditioning"]},
        "9": {"class_type": "TTLGAddToPlaylist", "inputs": {}, "outputs": []},
    })
    result = validate_spec(path)
    assert result.ok is True  # still runnable
    assert len(result.warnings) == 1
    assert "ControlNetApply" in result.warnings[0]


def test_unknown_required_node_blocks():
    from workflow_compat import validate_spec
    path = write_spec({
        "1": {"class_type": "TTLGTextToImage", "inputs": {}, "outputs": ["image_path"]},
        "2": {"class_type": "MyCustomNode",    "inputs": {}, "outputs": ["out"],
              "_required": True},
    })
    result = validate_spec(path)
    assert result.ok is False
    assert len(result.blocking) >= 1
    assert "MyCustomNode" in result.blocking[0]


def test_unknown_node_without_required_flag_is_skippable():
    from workflow_compat import validate_spec
    path = write_spec({
        "1": {"class_type": "TTLGTextToImage", "inputs": {}, "outputs": ["image_path"]},
        "2": {"class_type": "UnknownOptional", "inputs": {}, "outputs": []},
    })
    result = validate_spec(path)
    # Unknown nodes without _required=True are treated as skippable
    assert result.ok is True
    assert any("UnknownOptional" in w for w in result.warnings)


def test_mapped_node_produces_mapping_note():
    from workflow_compat import validate_spec
    path = write_spec({
        "1": {"class_type": "KSampler",          "inputs": {"steps": 20}, "outputs": ["latent"]},
        "9": {"class_type": "TTLGAddToPlaylist",  "inputs": {}, "outputs": []},
    })
    result = validate_spec(path)
    assert result.ok is True
    assert any("KSampler" in m for m in result.mappings)


def test_empty_spec_is_valid():
    from workflow_compat import validate_spec
    path = write_spec({})
    result = validate_spec(path)
    assert result.ok is True


def test_missing_spec_file_returns_not_ok():
    from workflow_compat import validate_spec
    result = validate_spec("/nonexistent/spec.json")
    assert result.ok is False
    assert len(result.blocking) == 1


def test_validate_result_summary_string():
    from workflow_compat import validate_spec
    path = write_spec({
        "1": {"class_type": "TTLGTextToImage", "inputs": {}, "outputs": []},
        "2": {"class_type": "ControlNetApply", "inputs": {}, "outputs": []},
    })
    result = validate_spec(path)
    summary = result.summary()
    assert "skip" in summary.lower() or "warn" in summary.lower()
```

- [ ] **Step 2: Run to confirm ModuleNotFoundError**

```bash
/usr/bin/python3 -m pytest tests/test_workflow_compat.py -v 2>&1 | head -5
```

- [ ] **Step 3: Implement `app/workflow_compat.py`**

```python
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Workflow compatibility layer.

Maps ComfyUI node class_types to tt-local-generator equivalents.
validate_spec() runs a preflight check before a pipeline run, returning
a ValidationResult with warnings (skippable nodes) and blocking errors
(unknown required nodes).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Compatibility map ─────────────────────────────────────────────────────────
#
# Three tiers:
#   ttlg_type set  → exact native node, runs as-is
#   ttlg_type str  → mapped to a different TTLG node (with input translation)
#   ttlg_type None → node is skippable (omit with warning, downstream may fail)
#
# optional=True  → safe to skip; pipeline continues without this node's output
# optional=False → required; if ttlg_type is None the pipeline cannot run

COMPATIBILITY_MAP: dict[str, dict] = {
    # ── Tier 1: Native tt-local-generator nodes ───────────────────────────────
    "TTLGTextToImage":      {"ttlg": "TTLGTextToImage",      "optional": False},
    "TTLGImageToVideo":     {"ttlg": "TTLGImageToVideo",     "optional": False},
    "TTLGGenerateText":     {"ttlg": "TTLGGenerateText",     "optional": False},
    "TTLGCaptionImage":     {"ttlg": "TTLGCaptionImage",     "optional": True},
    "TTLGRemoveBackground": {"ttlg": "TTLGRemoveBackground", "optional": True},
    "TTLGEstimateDepth":    {"ttlg": "TTLGEstimateDepth",    "optional": True},
    "TTLGPromptCompose":    {"ttlg": "TTLGPromptCompose",    "optional": False},
    "TTLGAddToPlaylist":    {"ttlg": "TTLGAddToPlaylist",    "optional": False},
    "TTLGComposite":        {"ttlg": "TTLGComposite",        "optional": True},
    "TTLGSVGRender":        {"ttlg": "TTLGSVGRender",        "optional": True},

    # ── Tier 2: Mapped ComfyUI standard nodes ─────────────────────────────────
    "KSampler": {
        "ttlg": "TTLGTextToImage", "optional": False,
        "note": "KSampler mapped to TTLGTextToImage — seed/steps/cfg adapted",
    },
    "KSamplerAdvanced": {
        "ttlg": "TTLGTextToImage", "optional": False,
        "note": "KSamplerAdvanced mapped to TTLGTextToImage",
    },
    "CLIPTextEncode": {
        "ttlg": "TTLGPromptCompose", "optional": True,
        "note": "prompt text passed through directly",
    },
    "VAEDecode": {
        "ttlg": None, "optional": True,
        "note": "VAE decode is internal to the TTNN pipeline — node skipped",
    },
    "VAEEncode": {
        "ttlg": None, "optional": True,
        "note": "VAE encode is internal — node skipped",
    },
    "LoadImage": {
        "ttlg": None, "optional": True,
        "note": "use input_image param on the job instead",
    },
    "SaveImage": {
        "ttlg": "TTLGAddToPlaylist", "optional": True,
        "note": "mapped to TTLGAddToPlaylist",
    },

    # ── Tier 3: Skippable — not supported, safe to omit ───────────────────────
    "ControlNetApply": {
        "ttlg": None, "optional": True,
        "note": "ControlNet not supported — node skipped, base model used",
    },
    "ControlNetLoader": {
        "ttlg": None, "optional": True,
        "note": "ControlNet not supported",
    },
    "IPAdapterApply": {
        "ttlg": None, "optional": True,
        "note": "IP-Adapter not supported — node skipped",
    },
    "UpscaleImage": {
        "ttlg": None, "optional": True,
        "note": "upscaling not supported — original resolution kept",
    },
    "ImageScale": {
        "ttlg": None, "optional": True,
        "note": "image scaling not supported — original size kept",
    },
    "LoraLoader": {
        "ttlg": None, "optional": True,
        "note": "LoRA not supported — base model weights used",
    },
    "CheckpointLoaderSimple": {
        "ttlg": None, "optional": True,
        "note": "checkpoint loading handled by tt-inference-server — node skipped",
    },
}


# ── Validation result ─────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    """Result of a preflight spec validation.

    ok=True means the pipeline can run (possibly with skipped nodes).
    ok=False means a required node is unknown — the pipeline cannot run.
    """
    ok: bool = True
    warnings: list[str] = field(default_factory=list)   # skippable unknown nodes
    mappings: list[str] = field(default_factory=list)   # nodes substituted
    blocking: list[str] = field(default_factory=list)   # unknown required nodes

    def summary(self) -> str:
        parts = []
        if self.blocking:
            parts.append(
                f"❌ {len(self.blocking)} required node(s) not supported: "
                + ", ".join(self.blocking)
            )
        if self.warnings:
            parts.append(
                f"⚠️ {len(self.warnings)} optional node(s) will be skipped: "
                + ", ".join(self.warnings)
            )
        if self.mappings:
            parts.append(
                f"↔ {len(self.mappings)} node(s) mapped to TTLG equivalents: "
                + ", ".join(self.mappings)
            )
        if not parts:
            return "✅ All nodes supported."
        return "\n".join(parts)


# ── validate_spec ─────────────────────────────────────────────────────────────

def validate_spec(spec_path: str) -> ValidationResult:
    """Run a preflight check on a workflow spec file.

    Returns a ValidationResult indicating whether the spec can run,
    which nodes will be skipped (with warnings), and which nodes block
    the run entirely (unknown + required).
    """
    result = ValidationResult()

    try:
        data = json.loads(Path(spec_path).read_text())
    except Exception as e:
        result.ok = False
        result.blocking.append(f"Cannot read spec: {e}")
        return result

    for node_id, node in data.items():
        if node_id.startswith("_") or not isinstance(node, dict):
            continue

        class_type = node.get("class_type", "")
        if not class_type:
            continue

        # Check if we know this node type
        entry = COMPATIBILITY_MAP.get(class_type)
        is_required = node.get("_required", False)

        if entry is None:
            # Unknown node type
            if is_required:
                result.ok = False
                result.blocking.append(f"{class_type} (node {node_id})")
            else:
                # Unknown optional nodes are treated as skippable
                result.warnings.append(
                    f"{class_type} (node {node_id}) — unknown type, will be skipped"
                )
        elif entry["ttlg"] is None:
            # Known skippable
            if not entry.get("optional", True):
                result.ok = False
                result.blocking.append(
                    f"{class_type} (node {node_id}) — {entry.get('note','not supported')}"
                )
            else:
                result.warnings.append(
                    f"{class_type} (node {node_id}) — {entry.get('note','skipped')}"
                )
        elif entry["ttlg"] != class_type:
            # Mapped to different type
            result.mappings.append(
                f"{class_type} → {entry['ttlg']} (node {node_id})"
            )
        # else: exact native match — no warning needed

    return result
```

- [ ] **Step 4: Run tests — all 8 must pass**

```bash
/usr/bin/python3 -m pytest tests/test_workflow_compat.py -v
```

- [ ] **Step 5: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ -q --tb=short 2>/dev/null | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add app/workflow_compat.py tests/test_workflow_compat.py
git commit -m "feat: workflow_compat.py — COMPATIBILITY_MAP + validate_spec() preflight for ComfyUI nodes"
```

---

## Task 2: Wire preflight validation into PipelinePanel

**Files:**
- Modify: `app/pipeline_panel.py` — call `validate_spec()` when spec changes, show warning label, disable Run on blocking errors

- [ ] **Step 1: Add preflight validation to spec change handler**

In `app/pipeline_panel.py`, modify `_on_spec_changed()`:

```python
        def _on_spec_changed(self, dd: Gtk.DropDown, _pspec) -> None:
            idx = dd.get_selected()
            if idx < len(self._specs):
                self._spec_path = self._specs[idx]["path"]
                self._run_preflight()

        def _run_preflight(self) -> None:
            """Validate the selected spec and update the preflight warning label."""
            if not self._spec_path or not hasattr(self, "_preflight_lbl"):
                return
            from workflow_compat import validate_spec
            result = validate_spec(self._spec_path)
            if result.blocking:
                self._preflight_lbl.set_label(f"❌ {result.blocking[0]}")
                self._preflight_lbl.add_css_class("error")
                self._preflight_lbl.remove_css_class("muted")
                self._run_btn.set_sensitive(False)
            elif result.warnings:
                self._preflight_lbl.set_label(
                    f"⚠️ {len(result.warnings)} node(s) will be skipped"
                )
                self._preflight_lbl.remove_css_class("error")
                self._preflight_lbl.add_css_class("muted")
                self._run_btn.set_sensitive(True)
            else:
                self._preflight_lbl.set_label("")
                self._run_btn.set_sensitive(True)
```

- [ ] **Step 2: Add `_preflight_lbl` to `_build_configure_tab()`**

After the spec dropdown, add:

```python
            self._preflight_lbl = Gtk.Label(label="")
            self._preflight_lbl.set_xalign(0)
            self._preflight_lbl.set_wrap(True)
            self._preflight_lbl.set_max_width_chars(36)
            self._preflight_lbl.add_css_class("muted")
            box.append(self._preflight_lbl)
```

And call `_run_preflight()` at the end of `_populate_specs()`:
```python
            if self._specs:
                self._spec_dd.set_selected(0)
                self._spec_path = self._specs[0]["path"]
                self._run_preflight()
```

- [ ] **Step 3: Run tests**

```bash
/usr/bin/python3 -m pytest tests/ -q --tb=short 2>/dev/null | tail -3
```

- [ ] **Step 4: Commit**

```bash
git add app/pipeline_panel.py
git commit -m "feat: wire preflight validation into PipelinePanel — block Run on incompatible specs"
```

---

## Task 3: run_single_node.sh + retry_node/retry_job

**Files:**
- Create: `bin/run_single_node.sh` — run one node given existing results.json context
- Modify: `app/pipeline_runner.py` — implement retry_node() and retry_job()
- Create: `tests/test_pipeline_runner_retry.py` — unit tests

- [ ] **Step 1: Create `bin/run_single_node.sh`**

```bash
#!/usr/bin/env bash
# run_single_node.sh — Re-run a single node from an existing pipeline run.
#
# Reads the existing results.json for input context, executes the specified
# node function, and writes the new output back to results.json.
#
# Usage:
#   ./bin/run_single_node.sh <results_json_path> <node_id>
#   ./bin/run_single_node.sh /path/to/results.json 6

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULTS_JSON="${1:-}"
NODE_ID="${2:-}"

if [[ -z "$RESULTS_JSON" || -z "$NODE_ID" ]]; then
    echo "Usage: $0 <results.json> <node_id>"
    exit 1
fi

if [[ ! -f "$RESULTS_JSON" ]]; then
    echo "ERROR: results.json not found: $RESULTS_JSON"
    exit 1
fi

OUTPUT_DIR="$(dirname "$RESULTS_JSON")"
PYTHON3="${HOME}/.tenstorrent-venv/bin/python3"
[[ ! -f "$PYTHON3" ]] && PYTHON3=/usr/bin/python3

LOG_FILE="${HOME}/.local/share/tt-local-generator/logs/workflow/$(date +%Y%m%d_%H%M%S)_retry_node${NODE_ID}.log"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "LOG:$LOG_FILE"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# Source the helper functions from run_workflow.sh without running its pipeline
# We do this by including just the function definitions we need
set_result() {
    local node_id="$1" key="$2" value="$3"
    python3 -c "
import json, sys
with open('$RESULTS_JSON') as f: d = json.load(f)
d.setdefault('$node_id', {})['$key'] = sys.argv[1]
with open('$RESULTS_JSON', 'w') as f: json.dump(d, f, indent=2)
" "$value"
}

get_result() {
    local ref="$1"
    python3 -c "
import json, sys
ref = json.loads(sys.argv[1])
node_id, key = ref[0], ref[1]
with open('$RESULTS_JSON') as f: d = json.load(f)
print(d.get(node_id, {}).get(key, ''))
" "$ref"
}

node_signal() {
    local node_id="$1" status="$2" detail="${3:-}"
    echo "NODE:${node_id}:${status}:${detail}" 2>/dev/null || true
    if [[ "$status" == "running" ]]; then _current_node="$node_id"; fi
}

_current_node=""
trap '[[ -n "$_current_node" ]] && node_signal "$_current_node" "failed" "retry exited unexpectedly"' ERR

log "Retrying node $NODE_ID from results: $RESULTS_JSON"

# Source the node implementations from run_workflow.sh
# This re-uses all the existing node functions without duplicating them
source <(sed -n '/^node_text_to_image/,/^node_generate_text/p' "$REPO_ROOT/bin/run_workflow.sh" 2>/dev/null || true)

case "$NODE_ID" in
    1|8)
        # Text-to-image (FLUX)
        PROMPT=$(get_result '["1", "prompt"]' 2>/dev/null || echo "")
        [[ -z "$PROMPT" ]] && { log "ERROR: no prompt in results.json"; exit 1; }
        node_signal "$NODE_ID" "running" "FLUX.1-schnell"
        node_text_to_image "$NODE_ID" "FLUX.1-schnell" "$PROMPT" "1024" "1024" "4" "1964" "http://localhost:8000" || true
        OUT=$(get_result "[\"$NODE_ID\", \"image_path\"]")
        [[ -n "$OUT" ]] && node_signal "$NODE_ID" "done" "$OUT" || node_signal "$NODE_ID" "failed" "no output"
        ;;
    6)
        # Image-to-video (SkyReels)
        IMAGE_PATH=$(get_result '["1", "image_path"]')
        VIDEO_PROMPT=$(get_result '["5", "video_prompt"]')
        [[ -z "$IMAGE_PATH" ]] && { log "ERROR: no seed image"; exit 1; }
        node_signal "6" "running" "SkyReels-V2-I2V"
        python3 "$REPO_ROOT/bin/_submit_video.py" "$VIDEO_PROMPT" "$IMAGE_PATH" "1964" > /tmp/videojob.txt 2>/dev/null || true
        JOB=$(cat /tmp/videojob.txt 2>/dev/null || echo "")
        if [[ -n "$JOB" && "$JOB" != ERROR* ]]; then
            OUT="$OUTPUT_DIR/node${NODE_ID}_video.mp4"
            for i in $(seq 1 60); do
                sleep 30
                STATUS=$(curl -s "http://localhost:8000/v1/videos/generations/$JOB" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "?")
                [[ "$STATUS" == "completed" ]] && break
            done
            curl -sf "http://localhost:8000/v1/videos/generations/$JOB/download" -o "$OUT" && \
                set_result "6" "video_path" "$OUT" && node_signal "6" "done" "$OUT" || node_signal "6" "failed" "download failed"
        else
            node_signal "6" "failed" "submission failed: $JOB"
        fi
        ;;
    7)
        # Generate text (poem)
        CAPTION=$(get_result '["2", "caption"]' 2>/dev/null || echo "")
        node_signal "7" "running" "Llama-3.3-70B"
        TEXT=$(python3 "$REPO_ROOT/bin/_gen_poem.py" "$CAPTION" 2>/dev/null) || TEXT=""
        [[ -n "$TEXT" ]] && set_result "7" "poem" "$TEXT" && node_signal "7" "done" "${TEXT:0:80}" || node_signal "7" "failed" "empty response"
        ;;
    *)
        log "Node $NODE_ID retry not implemented for standalone re-run"
        exit 1
        ;;
esac

log "✅ Node $NODE_ID retry complete"
```

- [ ] **Step 2: Write retry tests**

Create `tests/test_pipeline_runner_retry.py`:

```python
"""Tests for PipelineRunner retry_node and retry_job."""
from __future__ import annotations
import sys, json, os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def make_store_with_run(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline_store._INDEX_PATH", tmp_path / "idx.json")
    monkeypatch.setattr("pipeline_store._RUNS_DIR", tmp_path)
    from pipeline_store import PipelineStore
    store = PipelineStore()
    run_id = store.create_run(
        "/fake/spec.json", "test",
        [{"name": "1964-ny"}, {"name": "1939-ny"}],
        {}, 1, "/tmp/fake.log"
    )
    # Mark a node as failed
    store.update_node(run_id, "1964-ny", "6", "failed", "SkyReels OOM")
    store.update_node(run_id, "1964-ny", "1", "done", "/tmp/node1.png")
    store.update_node(run_id, "1939-ny", "1", "done", "/tmp/node1b.png")
    store.update_node(run_id, "1939-ny", "6", "done", "/tmp/video.mp4")
    return store, run_id


def test_retry_node_launches_subprocess(monkeypatch, tmp_path):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    store, run_id = make_store_with_run(tmp_path, monkeypatch)

    mock_popen = MagicMock()
    mock_popen.return_value.pid = 9999
    mock_popen.return_value.stdout = iter([
        "LOG:/tmp/retry.log\n",
        "NODE:6:running:SkyReels\n",
        "NODE:6:done:/tmp/new_video.mp4\n",
    ])
    mock_popen.return_value.wait.return_value = 0
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    from pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    runner._store = store
    runner._run_id = run_id
    on_update = MagicMock()
    on_done = MagicMock()

    # Write a fake results.json for the run
    results_dir = tmp_path / run_id[:8]
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "results.json").write_text(json.dumps({
        "1": {"image_path": "/tmp/node1.png"},
        "5": {"video_prompt": "test prompt"},
    }))

    runner.retry_node("1964-ny", "6", on_update, on_done)
    mock_popen.assert_called_once()
    args = mock_popen.call_args[0][0]
    assert "run_single_node.sh" in " ".join(args)
    assert "6" in args


def test_retry_job_finds_first_failed_node(monkeypatch, tmp_path):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    store, run_id = make_store_with_run(tmp_path, monkeypatch)

    mock_popen = MagicMock()
    mock_popen.return_value.pid = 9999
    mock_popen.return_value.stdout = iter([])
    mock_popen.return_value.wait.return_value = 0
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    from pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    runner._store = store
    runner._run_id = run_id
    on_update = MagicMock()
    on_done = MagicMock()

    results_dir = tmp_path / run_id[:8]
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "results.json").write_text(json.dumps({}))

    runner.retry_job("1964-ny", on_update, on_done)
    mock_popen.assert_called_once()
    # Should retry node 6 (the failed one)
    args = mock_popen.call_args[0][0]
    assert "6" in args


def test_retry_node_no_run_id_raises(monkeypatch):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    from pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    runner._run_id = None
    with pytest.raises(ValueError, match="No active run"):
        runner.retry_node("job", "6", MagicMock(), MagicMock())
```

- [ ] **Step 3: Run — confirm failures**

```bash
/usr/bin/python3 -m pytest tests/test_pipeline_runner_retry.py -v 2>&1 | head -10
```

- [ ] **Step 4: Implement retry_node and retry_job in `app/pipeline_runner.py`**

Replace the `NotImplementedError` stubs with:

```python
    def retry_node(
        self,
        job_name: str,
        node_id: str,
        on_node_update: Callable,
        on_run_finished: Callable,
    ) -> None:
        """Re-run a single failed node using the existing results.json for input context.

        Launches bin/run_single_node.sh with the results.json path and node_id.
        The shell script reads prior outputs from results.json, re-runs the node,
        and writes the new output back — the same GLib.idle_add watcher picks up
        the NODE: signals.
        """
        if not self._run_id:
            raise ValueError("No active run — call start() or reattach() first")

        # Find the results.json for this job in the run's output directory
        store_run = self._store.get_run(self._run_id)
        if not store_run:
            raise ValueError(f"Run {self._run_id} not found in store")

        output_dir = Path(store_run.get("output_dir", "")) if store_run.get("output_dir") else None
        if not output_dir:
            # Derive from the run's log_file directory or runs dir
            log_file = store_run.get("log_file", "")
            if log_file:
                output_dir = Path(log_file).parent
            else:
                raise ValueError("Cannot determine output directory for retry")

        results_json = output_dir / "results.json"
        if not results_json.exists():
            raise ValueError(f"results.json not found at {results_json}")

        self._on_node_update = on_node_update
        self._on_run_finished = on_run_finished
        self._cancelled = False

        script = _REPO_ROOT / "bin" / "run_single_node.sh"
        env = {**os.environ}
        try:
            self._proc = subprocess.Popen(
                ["bash", str(script), str(results_json), node_id],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            threading.Thread(
                target=self._watch_stdout,
                args=(job_name,),
                daemon=True,
            ).start()
        except Exception as e:
            self._dispatch(on_run_finished, False)

    def retry_job(
        self,
        job_name: str,
        on_node_update: Callable,
        on_run_finished: Callable,
    ) -> None:
        """Re-run a job from its first failed node forward.

        Finds the first node with status 'failed' for this job and calls retry_node.
        """
        if not self._run_id:
            raise ValueError("No active run")

        store_run = self._store.get_run(self._run_id)
        if not store_run:
            raise ValueError(f"Run {self._run_id} not found")

        job_states = store_run.get("job_states", {}).get(job_name, {})
        # Find the lowest node_id with status 'failed'
        failed_nodes = [
            nid for nid, state in job_states.items()
            if state.get("status") == "failed"
        ]
        if not failed_nodes:
            return  # Nothing to retry

        # Sort numerically, retry from the first failure
        first_failed = sorted(failed_nodes, key=lambda n: int(n) if n.isdigit() else 999)[0]
        self.retry_node(job_name, first_failed, on_node_update, on_run_finished)
```

Also add `output_dir` to the run record. In `PipelineStore.create_run()`, add `"output_dir": ""` to the record dict, and add an `update_output_dir(run_id, output_dir)` method. In `PipelineRunner.start()`, after the subprocess launches and the LOG: signal arrives (in `_parse_line`), extract the output dir from the log path and update the store.

Actually simpler: add the output dir to the run record at create time. In `PipelineRunner.start()`:

```python
        # Derive output_dir from the run_workflow.sh convention
        # The script creates OUTPUT_DIR = ~/.local/.../workflow-runs/YYYYMMDDHHMMSS
        # We can't know this before launch — update it when LOG: signal arrives
        # _parse_line already stores the log_file; derive output_dir from it:
```

Add to `_parse_line` when LOG: is received:
```python
        if line.startswith("LOG:"):
            self._log_file = line[4:].strip()
            # Derive output_dir: log is at .../logs/workflow/YYYYMMDD_HHMMSS_run.log
            # Results are at .../workflow-runs/YYYYMMDD_HHMMSS/
            # Extract timestamp from log filename and construct output_dir
            import re
            m = re.search(r'(\d{8}_\d{6})_run\.log$', self._log_file)
            if m and self._run_id:
                ts = m.group(1)
                output_dir = str(
                    Path.home() / ".local" / "share" / "tt-local-generator" / "workflow-runs" / ts
                )
                records = self._store._load()
                for r in records:
                    if r["id"] == self._run_id:
                        r["log_file"] = self._log_file
                        r["output_dir"] = output_dir
                        break
                self._store._save(records)
            return
```

Also update `PipelineStore` to include `"output_dir": ""` in the record template in `create_run()`.

- [ ] **Step 5: Run retry tests**

```bash
/usr/bin/python3 -m pytest tests/test_pipeline_runner_retry.py -v
```

- [ ] **Step 6: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ -q --tb=short 2>/dev/null | tail -3
```

- [ ] **Step 7: Commit**

```bash
chmod +x bin/run_single_node.sh
git add bin/run_single_node.sh app/pipeline_runner.py app/pipeline_store.py \
    tests/test_pipeline_runner_retry.py
git commit -m "feat: retry_node/retry_job + run_single_node.sh for in-UI pipeline repair"
```

---

## Task 4: Wire retry into PhaseGridWidget and PipelinePanel

**Files:**
- Modify: `app/phase_grid_widget.py` — enable per-job retry button when any cell is failed; add "retry this node" to failed cell click handler
- Modify: `app/pipeline_panel.py` — pass runner reference through to phase grid; enable retry buttons

- [ ] **Step 1: Update PhaseGridWidget to enable retry buttons**

In `PhaseGridWidget.update_cell()`, after updating the cell status, check if ANY cell in that job's row is failed and enable/disable the per-job retry button:

```python
        # Update per-job retry button sensitivity
        job_has_failure = any(
            self._state.cell(job_name, p["id"]).get("status") == "failed"
            for p in self._state.phases
        )
        # Find the retry button for this job row
        # (We need to store a reference to it — add self._retry_buttons dict)
```

Add `self._retry_buttons: dict[str, Gtk.Button] = {}` to `__init__` and populate it in `_build()`:

```python
                retry_btn.job_name = job_name
                retry_btn.connect("clicked", self._on_retry_job_clicked)
                self._retry_buttons[job_name] = retry_btn  # ADD THIS
                grid.attach(retry_btn, ...)
```

In `update_cell()`, after updating:
```python
            retry_btn = self._retry_buttons.get(job_name)
            if retry_btn:
                retry_btn.set_sensitive(job_has_failure)
```

Also reset `_retry_buttons` in `_build()`:
```python
        def _build(self) -> None:
            ...
            self._retry_buttons = {}
```

- [ ] **Step 2: Pass runner to PhaseGridWidget retry callbacks**

In `MainWindow._on_pipeline_retry_node()` and `_on_pipeline_retry_job()`, remove the `try/except NotImplementedError` and wire the real runner:

```python
    def _on_pipeline_retry_node(self, job_name: str, node_id: str) -> None:
        if hasattr(self, "_pipeline_runner") and self._pipeline_runner:
            self._pipeline_runner.retry_node(
                job_name, node_id,
                on_node_update=self._on_pipeline_node_update,
                on_run_finished=self._on_pipeline_run_finished,
            )

    def _on_pipeline_retry_job(self, job_name: str) -> None:
        if hasattr(self, "_pipeline_runner") and self._pipeline_runner:
            self._pipeline_runner.retry_job(
                job_name,
                on_node_update=self._on_pipeline_node_update,
                on_run_finished=self._on_pipeline_run_finished,
            )
```

- [ ] **Step 3: Run tests**

```bash
/usr/bin/python3 -m pytest tests/ -q --tb=short 2>/dev/null | tail -3
```

- [ ] **Step 4: Commit**

```bash
git add app/phase_grid_widget.py app/main_window.py
git commit -m "feat: enable per-job retry buttons in phase grid; wire real retry callbacks"
```

---

## Self-review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| `workflow_compat.py` with `COMPATIBILITY_MAP` | Task 1 |
| `validate_spec()` returning `ValidationResult` | Task 1 |
| Preflight in PipelinePanel — warn/block before Run | Task 2 |
| `retry_node()` implemented (not NotImplementedError) | Task 3 |
| `retry_job()` — finds first failure, delegates to retry_node | Task 3 |
| `run_single_node.sh` — reruns one node in isolation | Task 3 |
| Per-job retry buttons enabled on failure | Task 4 |
| Real retry callbacks in MainWindow (not pass) | Task 4 |
| All existing tests pass | Every task |
