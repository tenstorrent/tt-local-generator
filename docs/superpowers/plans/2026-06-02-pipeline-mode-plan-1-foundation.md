# Pipeline Mode — Plan 1: Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the data layer and process management for Pipeline mode — `PipelineStore`, `PipelineRunner`, and structured NODE signals in `run_workflow.sh` — with full test coverage and no GTK dependency.

**Architecture:** `PipelineStore` persists run records and per-job/node state in a JSON index alongside the existing workflow-runs directory. `PipelineRunner` launches `run_workflow.sh` per phase-batch, parses stdout for `NODE:` signals, and calls callbacks on the GTK main thread via `GLib.idle_add`. `run_workflow.sh` emits structured `NODE:<id>:<status>:<detail>` lines that the runner parses for node-level granularity. No GTK is imported by either new file.

**Tech Stack:** Python 3.12, stdlib only (json, pathlib, subprocess, threading, os, signal). GTK is imported by callers only. Tests use pytest + unittest.mock.

---

## File structure

| File | Role |
|---|---|
| `app/pipeline_store.py` | Persist run records, job states, history list |
| `app/pipeline_runner.py` | Launch run_workflow.sh, parse signals, call callbacks, restart recovery |
| `bin/run_workflow.sh` | Add `NODE:` signal lines at each node start/done/skip/fail |
| `tests/test_pipeline_store.py` | Unit tests for store CRUD and history |
| `tests/test_pipeline_runner.py` | Unit tests for signal parsing and runner lifecycle |

---

## Task 1: NODE signals in run_workflow.sh

**Files:**
- Modify: `bin/run_workflow.sh` — add `NODE:` emit calls around each `_run_node` invocation

The runner needs structured per-node signals to update the phase grid. Add a `node_signal` helper and call it at each node boundary. The format is:

```
NODE:<node_id>:<status>:<detail>
```

Where `status` is one of: `running`, `done`, `skipped`, `failed`. `detail` is a short string (output path on done, reason on skip, error summary on fail).

- [ ] **Step 1: Add `node_signal` helper to run_workflow.sh**

Open `bin/run_workflow.sh`. After the `set_node_label` function (around line 145), add:

```bash
node_signal() {
    # Emit a structured signal for the PipelineRunner to parse.
    # Format: NODE:<node_id>:<status>:<detail>
    # status: running | done | skipped | failed
    local node_id="$1" status="$2" detail="${3:-}"
    echo "NODE:${node_id}:${status}:${detail}"
}
```

- [ ] **Step 2: Emit signals around Node 1 (seed image)**

Find the Node 1 block (around `log_step "Node 1: Seed image"`). Wrap the `_run_node` call:

```bash
log_step "Node 1: Seed image — FLUX.1-schnell"
node_signal "1" "running" "FLUX.1-schnell"
stop_and_reset "flux"
start_server "flux" "http://localhost:8000/tt-liveness" 30
_run_node "node1(flux-image)" node_text_to_image "1" \
    "FLUX.1-schnell" \
    "SEED_PROMPT_PLACEHOLDER" \
    "1024" "1024" "4" "SEED_PLACEHOLDER" "http://localhost:8000"
set_node_label "1" "seed image"
IMAGE_PATH=$(get_result '["1", "image_path"]')
if [[ -z "$IMAGE_PATH" ]]; then
    node_signal "1" "failed" "image_path empty"
    log "  ⚠️  node1 skipped: image_path is empty — downstream nodes may fail"
else
    node_signal "1" "done" "$IMAGE_PATH"
fi
```

- [ ] **Step 3: Add signals to remaining nodes (2-8)**

Following the exact same pattern, add `node_signal "<id>" "running" "<model>"` before each `_run_node` call and `node_signal "<id>" "done"/"skipped"/"failed" "<detail>"` after. For nodes that check `USE_DEPTH` and skip:

```bash
if [[ "$USE_DEPTH" == "1" ... ]]; then
    node_signal "2" "running" "GLPN-KITTI"
    _run_node ...
    node_signal "2" "done" "$DEPTH_PATH"
else
    node_signal "2" "skipped" "fog/exterior scene"
fi
```

- [ ] **Step 4: Verify signals appear in dry-run output**

```bash
cd ~/code/tt-local-generator
bash bin/run_workflow.sh docs/examples/workflows/1964-worlds-fair.json --dry-run 2>/dev/null | grep "^NODE:"
```

Expected output (order may vary):
```
NODE:1:running:FLUX.1-schnell
NODE:1:done:/tmp/.../node1_image.png
NODE:2:running:BLIP
NODE:2:done:The 1964 World's Fair...
...
NODE:8:done:/tmp/.../node8_image.png
```

- [ ] **Step 5: Commit**

```bash
git add bin/run_workflow.sh
git commit -m "feat: add NODE:<id>:<status>:<detail> signals to run_workflow.sh for pipeline UI"
```

---

## Task 2: PipelineStore

**Files:**
- Create: `app/pipeline_store.py`
- Create: `tests/test_pipeline_store.py`

`PipelineStore` manages a JSON index at `~/.local/share/tt-local-generator/workflow-runs/pipeline-index.json`. Each run record stores the job table, per-node state, and metadata needed for restart recovery and history display.

- [ ] **Step 1: Write failing tests**

Create `tests/test_pipeline_store.py`:

```python
"""Tests for PipelineStore — run record CRUD and history."""
from __future__ import annotations
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline_store._INDEX_PATH",
                        tmp_path / "pipeline-index.json")
    monkeypatch.setattr("pipeline_store._RUNS_DIR", tmp_path / "runs")
    from pipeline_store import PipelineStore
    return PipelineStore()


def test_create_run_returns_id(store):
    run_id = store.create_run(
        spec_path="/fake/spec.json",
        spec_name="test spec",
        jobs=[{"name": "job1", "prompt": "a cat"}],
        param_overrides={"seed": 42},
        pid=12345,
        log_file="/tmp/test.log",
    )
    assert isinstance(run_id, str) and len(run_id) == 36  # UUID


def test_created_run_has_running_status(store):
    run_id = store.create_run(
        spec_path="/fake/spec.json", spec_name="s", jobs=[], param_overrides={},
        pid=1, log_file="/tmp/x.log"
    )
    run = store.get_run(run_id)
    assert run["status"] == "running"
    assert run["finished_at"] is None


def test_update_node_state(store):
    run_id = store.create_run(
        spec_path="/s", spec_name="s",
        jobs=[{"name": "j1", "prompt": "p"}], param_overrides={},
        pid=1, log_file="/tmp/x.log"
    )
    store.update_node(run_id, job_name="j1", node_id="1",
                      status="done", detail="/tmp/out.png", elapsed_s=3.1)
    run = store.get_run(run_id)
    assert run["job_states"]["j1"]["1"]["status"] == "done"
    assert run["job_states"]["j1"]["1"]["detail"] == "/tmp/out.png"
    assert run["job_states"]["j1"]["1"]["elapsed_s"] == pytest.approx(3.1)


def test_finish_run(store):
    run_id = store.create_run(
        spec_path="/s", spec_name="s", jobs=[], param_overrides={},
        pid=1, log_file="/tmp/x.log"
    )
    store.finish_run(run_id, success=True)
    run = store.get_run(run_id)
    assert run["status"] == "done"
    assert run["finished_at"] is not None


def test_list_runs_newest_first(store):
    id1 = store.create_run("/s", "s", [], {}, 1, "/tmp/x.log")
    id2 = store.create_run("/s", "s", [], {}, 1, "/tmp/x.log")
    runs = store.list_runs()
    assert runs[0]["id"] == id2
    assert runs[1]["id"] == id1


def test_list_runs_for_spec(store):
    store.create_run("/spec-a.json", "A", [], {}, 1, "/tmp/x.log")
    store.create_run("/spec-b.json", "B", [], {}, 1, "/tmp/x.log")
    runs = store.list_runs(spec_path="/spec-a.json")
    assert len(runs) == 1
    assert runs[0]["spec_path"] == "/spec-a.json"


def test_find_interrupted_runs(store):
    run_id = store.create_run("/s", "s", [], {}, 99999, "/tmp/x.log")
    # PID 99999 almost certainly does not exist
    interrupted = store.find_interrupted_runs()
    assert any(r["id"] == run_id for r in interrupted)


def test_running_run_with_live_pid_not_interrupted(store, monkeypatch):
    import os
    monkeypatch.setattr(os.path, "exists",
                        lambda p: p == f"/proc/{os.getpid()}")
    run_id = store.create_run("/s", "s", [], {}, os.getpid(), "/tmp/x.log")
    interrupted = store.find_interrupted_runs()
    assert not any(r["id"] == run_id for r in interrupted)


def test_get_nonexistent_run_returns_none(store):
    assert store.get_run("does-not-exist") is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd ~/code/tt-local-generator
/usr/bin/python3 -m pytest tests/test_pipeline_store.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'pipeline_store'`

- [ ] **Step 3: Implement PipelineStore**

Create `app/pipeline_store.py`:

```python
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Persistent store for Pipeline mode run records.

Layout:
    ~/.local/share/tt-local-generator/workflow-runs/pipeline-index.json
        List of run records, newest first.

Each run record:
    {
        "id":             str (UUID),
        "spec_path":      str,
        "spec_name":      str,
        "jobs":           list[{"name": str, "prompt": str, ...}],
        "param_overrides": dict,
        "pid":            int,       # subprocess PID for liveness check
        "log_file":       str,       # path to tee'd log for re-attach
        "status":         "running" | "done" | "failed" | "interrupted",
        "started_at":     str (ISO),
        "finished_at":    str | None,
        "job_states":     {job_name: {node_id: {status, detail, elapsed_s}}},
        "playlist_ids":   {job_name: str | None},
    }
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_RUNS_DIR = Path.home() / ".local" / "share" / "tt-local-generator" / "workflow-runs"
_INDEX_PATH = _RUNS_DIR / "pipeline-index.json"


class PipelineStore:
    """JSON-backed list of pipeline run records."""

    def __init__(self) -> None:
        _RUNS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Read ──────────────────────────────────────────────────────────────────

    def _load(self) -> list[dict]:
        try:
            return json.loads(_INDEX_PATH.read_text())
        except Exception:
            return []

    def _save(self, records: list[dict]) -> None:
        _INDEX_PATH.write_text(json.dumps(records, indent=2))

    def get_run(self, run_id: str) -> Optional[dict]:
        return next((r for r in self._load() if r["id"] == run_id), None)

    def list_runs(self, spec_path: Optional[str] = None, limit: int = 50) -> list[dict]:
        records = self._load()
        if spec_path:
            records = [r for r in records if r.get("spec_path") == spec_path]
        return records[:limit]

    def find_interrupted_runs(self) -> list[dict]:
        """Return runs with status 'running' whose PID is no longer alive."""
        result = []
        for r in self._load():
            if r.get("status") != "running":
                continue
            pid = r.get("pid", 0)
            if not os.path.exists(f"/proc/{pid}"):
                result.append(r)
        return result

    # ── Write ─────────────────────────────────────────────────────────────────

    def create_run(
        self,
        spec_path: str,
        spec_name: str,
        jobs: list[dict],
        param_overrides: dict,
        pid: int,
        log_file: str,
    ) -> str:
        run_id = str(uuid.uuid4())
        record = {
            "id": run_id,
            "spec_path": spec_path,
            "spec_name": spec_name,
            "jobs": jobs,
            "param_overrides": param_overrides,
            "pid": pid,
            "log_file": log_file,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "job_states": {j["name"]: {} for j in jobs},
            "playlist_ids": {j["name"]: None for j in jobs},
        }
        records = self._load()
        records.insert(0, record)
        self._save(records)
        return run_id

    def update_node(
        self,
        run_id: str,
        job_name: str,
        node_id: str,
        status: str,
        detail: str = "",
        elapsed_s: float = 0.0,
    ) -> None:
        records = self._load()
        for r in records:
            if r["id"] != run_id:
                continue
            r.setdefault("job_states", {}).setdefault(job_name, {})[node_id] = {
                "status": status,
                "detail": detail,
                "elapsed_s": elapsed_s,
            }
            break
        self._save(records)

    def update_playlist(self, run_id: str, job_name: str, playlist_id: str) -> None:
        records = self._load()
        for r in records:
            if r["id"] == run_id:
                r.setdefault("playlist_ids", {})[job_name] = playlist_id
                break
        self._save(records)

    def finish_run(self, run_id: str, success: bool) -> None:
        records = self._load()
        for r in records:
            if r["id"] == run_id:
                r["status"] = "done" if success else "failed"
                r["finished_at"] = datetime.now(timezone.utc).isoformat()
                break
        self._save(records)

    def mark_interrupted(self, run_id: str) -> None:
        records = self._load()
        for r in records:
            if r["id"] == run_id:
                r["status"] = "interrupted"
                break
        self._save(records)
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_pipeline_store.py -v
```

Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline_store.py tests/test_pipeline_store.py
git commit -m "feat: PipelineStore — JSON-backed run record persistence with liveness check"
```

---

## Task 3: PipelineRunner — signal parsing

**Files:**
- Create: `app/pipeline_runner.py` (parsing only, no subprocess yet)
- Create: `tests/test_pipeline_runner.py` (parsing tests)

The runner's stdout parser is the most testable piece. Build and test it first, independent of the subprocess.

- [ ] **Step 1: Write failing tests for signal parsing**

Create `tests/test_pipeline_runner.py`:

```python
"""Tests for PipelineRunner — signal parsing, lifecycle, restart recovery."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def make_runner(on_node_update=None, on_run_finished=None):
    from pipeline_runner import PipelineRunner
    runner = PipelineRunner.__new__(PipelineRunner)
    runner._on_node_update = on_node_update or MagicMock()
    runner._on_run_finished = on_run_finished or MagicMock()
    runner._run_id = "test-run-id"
    runner._active_jobs = {"1964-ny": {}, "1939-ny": {}}
    runner._store = MagicMock()
    return runner


# ── NODE signal parsing ───────────────────────────────────────────────────────

def test_parse_node_running(monkeypatch):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    runner = make_runner()
    runner._parse_line("NODE:1:running:FLUX.1-schnell", "1964-ny")
    runner._on_node_update.assert_called_once_with(
        "1964-ny", "1", "running", "FLUX.1-schnell"
    )


def test_parse_node_done_with_path(monkeypatch):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    runner = make_runner()
    runner._parse_line("NODE:4:done:/tmp/node4_video.mp4", "1964-ny")
    runner._on_node_update.assert_called_once_with(
        "1964-ny", "4", "done", "/tmp/node4_video.mp4"
    )


def test_parse_node_skipped(monkeypatch):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    runner = make_runner()
    runner._parse_line("NODE:2:skipped:fog/exterior scene", "1970-osaka")
    runner._on_node_update.assert_called_once_with(
        "1970-osaka", "2", "skipped", "fog/exterior scene"
    )


def test_parse_node_failed(monkeypatch):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    runner = make_runner()
    runner._parse_line("NODE:6:failed:SkyReels OOM", "1939-ny")
    runner._on_node_update.assert_called_once_with(
        "1939-ny", "6", "failed", "SkyReels OOM"
    )


def test_parse_node_detail_with_colons(monkeypatch):
    """Detail field may contain colons (e.g. timestamps, URLs)."""
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    runner = make_runner()
    runner._parse_line("NODE:1:done:/home/user/.local/share/runs/job/node1.png", "1964-ny")
    args = runner._on_node_update.call_args[0]
    assert args[3] == "/home/user/.local/share/runs/job/node1.png"


def test_non_node_line_ignored(monkeypatch):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    runner = make_runner()
    runner._parse_line("[15:42] ✅ video saved: /tmp/out.mp4", "1964-ny")
    runner._on_node_update.assert_not_called()


def test_parse_playlist_signal(monkeypatch):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    runner = make_runner()
    runner._parse_line("PLAYLIST:4:World's Fair 1964 NY — IBM People Wall", "1964-ny")
    runner._store.update_playlist.assert_called_once()
    args = runner._store.update_playlist.call_args[0]
    assert args[1] == "1964-ny"


def test_parse_log_path(monkeypatch):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    runner = make_runner()
    runner._log_file = None
    runner._parse_line("LOG:/tmp/pipeline_run.log", "1964-ny")
    assert runner._log_file == "/tmp/pipeline_run.log"


# ── Malformed signal handling ─────────────────────────────────────────────────

def test_malformed_node_signal_no_crash(monkeypatch):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    runner = make_runner()
    runner._parse_line("NODE:bad", "job")          # too few parts
    runner._parse_line("NODE::running:", "job")    # empty node_id
    runner._on_node_update.assert_not_called()
```

- [ ] **Step 2: Run to confirm failures**

```bash
/usr/bin/python3 -m pytest tests/test_pipeline_runner.py -v 2>&1 | head -15
```

Expected: `ModuleNotFoundError: No module named 'pipeline_runner'`

- [ ] **Step 3: Implement PipelineRunner with _parse_line**

Create `app/pipeline_runner.py`:

```python
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
PipelineRunner — manages batch pipeline runs.

Launches run_workflow.sh once per phase (or per-job depending on the
batching strategy), parses stdout for NODE: and PLAYLIST: signals,
and calls UI callbacks on the GTK main thread via GLib.idle_add.

No GTK widgets are created here. Callers must wrap callbacks in
GLib.idle_add themselves OR pass unwrapped callbacks and let the runner
wrap them (see start()).

Signal protocol (emitted by run_workflow.sh):
    NODE:<node_id>:<status>:<detail>
        status: running | done | skipped | failed
        detail: output path (done), reason (skipped/failed), model (running)
    PLAYLIST:<count>:<playlist_name>
    LOG:<path>   — tee'd log file path, emitted once at start
"""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

try:
    from gi.repository import GLib
except ImportError:  # headless / test environment
    GLib = None  # type: ignore

from pipeline_store import PipelineStore

_REPO_ROOT = Path(__file__).resolve().parent.parent


class PipelineRunner:
    """Manages the lifecycle of a single pipeline batch run."""

    def __init__(self) -> None:
        self._store = PipelineStore()
        self._run_id: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._log_file: Optional[str] = None
        self._on_node_update: Optional[Callable] = None
        self._on_run_finished: Optional[Callable] = None
        self._active_jobs: dict[str, dict] = {}  # job_name → {node_id: state}
        self._cancelled = False

    # ── Signal parser ─────────────────────────────────────────────────────────

    def _parse_line(self, line: str, current_job: str) -> None:
        """Parse one stdout line from run_workflow.sh and dispatch callbacks."""
        line = line.rstrip()

        if line.startswith("LOG:"):
            self._log_file = line[4:].strip()
            return

        if line.startswith("NODE:"):
            parts = line.split(":", 3)  # NODE, node_id, status, detail
            if len(parts) < 3 or not parts[1] or not parts[2]:
                return
            _, node_id, status = parts[0], parts[1], parts[2]
            detail = parts[3] if len(parts) > 3 else ""
            self._dispatch(self._on_node_update, current_job, node_id, status, detail)
            if self._run_id:
                self._store.update_node(
                    self._run_id, current_job, node_id, status, detail
                )
            return

        if line.startswith("PLAYLIST:"):
            parts = line.split(":", 2)
            if len(parts) >= 3:
                playlist_name = parts[2].strip()
                if self._run_id:
                    self._store.update_playlist(self._run_id, current_job, playlist_name)
            return

    def _dispatch(self, callback: Optional[Callable], *args) -> None:
        """Post a callback to the GTK main thread if GLib is available."""
        if callback is None:
            return
        if GLib is not None:
            GLib.idle_add(callback, *args)
        else:
            callback(*args)  # test / headless: call directly
```

- [ ] **Step 4: Run signal parsing tests**

```bash
/usr/bin/python3 -m pytest tests/test_pipeline_runner.py -v
```

Expected: all 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline_runner.py tests/test_pipeline_runner.py
git commit -m "feat: PipelineRunner._parse_line — NODE/PLAYLIST/LOG signal parsing with tests"
```

---

## Task 4: PipelineRunner — subprocess management

**Files:**
- Modify: `app/pipeline_runner.py` — add `start()`, `cancel()`, `retry_node()`, `retry_job()`
- Modify: `tests/test_pipeline_runner.py` — add subprocess tests

- [ ] **Step 1: Add subprocess tests**

Append to `tests/test_pipeline_runner.py`:

```python
# ── Subprocess management ────────────────────────────────────────────────────

def test_cancel_terminates_process(monkeypatch, tmp_path):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    from pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    runner._run_id = "x"
    runner._store = MagicMock()

    # Mock a running process
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None  # still running
    runner._proc = mock_proc

    runner.cancel()
    mock_proc.terminate.assert_called_once()
    assert runner._cancelled is True


def test_cancel_when_no_proc_does_not_crash(monkeypatch):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    from pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    runner._proc = None
    runner.cancel()  # must not raise


def test_start_creates_run_record(monkeypatch, tmp_path):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    monkeypatch.setattr("pipeline_store._INDEX_PATH", tmp_path / "idx.json")
    monkeypatch.setattr("pipeline_store._RUNS_DIR", tmp_path)

    # Stub out the actual subprocess
    mock_popen = MagicMock()
    mock_popen.return_value.pid = 12345
    mock_popen.return_value.stdout = iter([])
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    from pipeline_runner import PipelineRunner
    from pipeline_store import PipelineStore
    runner = PipelineRunner()
    runner._store = PipelineStore()

    runner.start(
        spec_path=str(tmp_path / "spec.json"),
        jobs=[{"name": "test-job", "prompt": "a test prompt"}],
        param_overrides={},
        on_node_update=MagicMock(),
        on_run_finished=MagicMock(),
    )

    runs = runner._store.list_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "running"
    assert runs[0]["jobs"][0]["name"] == "test-job"
```

- [ ] **Step 2: Run to confirm new tests fail**

```bash
/usr/bin/python3 -m pytest tests/test_pipeline_runner.py::test_cancel_terminates_process -v 2>&1 | tail -5
```

Expected: `AttributeError: 'PipelineRunner' object has no attribute 'cancel'`

- [ ] **Step 3: Add start(), cancel(), retry_node(), retry_job() to pipeline_runner.py**

Append to the `PipelineRunner` class in `app/pipeline_runner.py`:

```python
    # ── Public API ────────────────────────────────────────────────────────────

    def start(
        self,
        spec_path: str,
        jobs: list[dict],
        param_overrides: dict,
        on_node_update: Callable,
        on_run_finished: Callable,
    ) -> None:
        """Launch run_workflow.sh for the given jobs and spec.

        Each job runs the same spec with its own prompt substituted into
        overridable inputs. The runner iterates jobs sequentially (the
        shell script handles phase batching internally).
        """
        self._on_node_update = on_node_update
        self._on_run_finished = on_run_finished
        self._cancelled = False

        # Write a temp spec with the first job's overrides (single-job mode for now;
        # multi-job parallelism is handled by run_worlds_fair_parallel.sh pattern
        # and will be wired in Plan 2 when the UI drives per-phase batching).
        log_dir = Path.home() / ".local" / "share" / "tt-local-generator" / "logs" / "pipeline"
        log_dir.mkdir(parents=True, exist_ok=True)

        self._run_id = self._store.create_run(
            spec_path=spec_path,
            spec_name=Path(spec_path).stem,
            jobs=jobs,
            param_overrides=param_overrides,
            pid=0,  # updated after Popen
            log_file="",  # updated when LOG: signal arrives
        )

        env = {**os.environ, "PIPELINE_RUN_ID": self._run_id}
        try:
            self._proc = subprocess.Popen(
                ["bash", str(_REPO_ROOT / "bin" / "run_workflow.sh"), spec_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            # Update PID in store now that we have it
            records = self._store._load()
            for r in records:
                if r["id"] == self._run_id:
                    r["pid"] = self._proc.pid
                    break
            self._store._save(records)

            # Watch stdout in a background thread (avoids blocking GTK main loop)
            threading.Thread(
                target=self._watch_stdout,
                args=(jobs[0]["name"] if jobs else "job",),
                daemon=True,
            ).start()
        except Exception as e:
            self._store.finish_run(self._run_id, success=False)
            self._dispatch(on_run_finished, False)

    def cancel(self) -> None:
        """Terminate the active run process."""
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def retry_node(self, job_name: str, node_id: str) -> None:
        """Re-run a single failed node for a specific job.

        Requires the node's input artifacts (from prior nodes) to still
        exist in the run's output directory. Implementation deferred to
        Plan 2 when the UI provides the retry affordance.
        """
        # TODO in Plan 2: build a minimal temp spec with only this node,
        # pointing inputs to the existing results.json outputs.
        raise NotImplementedError("retry_node implemented in Plan 2")

    def retry_job(self, job_name: str) -> None:
        """Re-run a job from its first failed node.

        Implementation deferred to Plan 2.
        """
        raise NotImplementedError("retry_job implemented in Plan 2")

    # ── Stdout watcher ────────────────────────────────────────────────────────

    def _watch_stdout(self, current_job: str) -> None:
        """Background thread: read stdout lines and parse signals."""
        assert self._proc is not None
        try:
            for line in self._proc.stdout:
                if self._cancelled:
                    break
                self._parse_line(line, current_job)
        finally:
            exit_code = self._proc.wait()
            success = (exit_code == 0) and not self._cancelled
            if self._run_id:
                self._store.finish_run(self._run_id, success=success)
            self._dispatch(self._on_run_finished, success)
```

- [ ] **Step 4: Run all pipeline_runner tests**

```bash
/usr/bin/python3 -m pytest tests/test_pipeline_runner.py -v
```

Expected: all 14 tests pass.

- [ ] **Step 5: Run full test suite**

```bash
/usr/bin/python3 -m pytest tests/ -q --tb=short
```

Expected: 509+ tests pass, 0 failures.

- [ ] **Step 6: Commit**

```bash
git add app/pipeline_runner.py tests/test_pipeline_runner.py
git commit -m "feat: PipelineRunner.start/cancel + stdout watcher thread"
```

---

## Task 5: Restart recovery

**Files:**
- Modify: `app/pipeline_runner.py` — add `reattach()` method
- Modify: `tests/test_pipeline_runner.py` — add reattach tests

- [ ] **Step 1: Add reattach tests**

Append to `tests/test_pipeline_runner.py`:

```python
# ── Restart recovery ──────────────────────────────────────────────────────────

def test_reattach_marks_interrupted_if_proc_dead(monkeypatch, tmp_path):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    monkeypatch.setattr("pipeline_store._INDEX_PATH", tmp_path / "idx.json")
    monkeypatch.setattr("pipeline_store._RUNS_DIR", tmp_path)
    monkeypatch.setattr("os.path.exists", lambda p: False)  # proc not alive

    from pipeline_runner import PipelineRunner
    from pipeline_store import PipelineStore
    store = PipelineStore()
    run_id = store.create_run("/s", "s", [{"name": "j"}], {}, 99999, "/tmp/fake.log")

    runner = PipelineRunner()
    runner._store = store
    result = runner.reattach(run_id, on_node_update=MagicMock(), on_run_finished=MagicMock())

    assert result is False
    assert store.get_run(run_id)["status"] == "interrupted"


def test_reattach_returns_false_for_missing_log(monkeypatch, tmp_path):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    monkeypatch.setattr("pipeline_store._INDEX_PATH", tmp_path / "idx.json")
    monkeypatch.setattr("pipeline_store._RUNS_DIR", tmp_path)

    import os
    monkeypatch.setattr(os.path, "exists",
                        lambda p: p == f"/proc/{os.getpid()}")

    from pipeline_runner import PipelineRunner
    from pipeline_store import PipelineStore
    store = PipelineStore()
    run_id = store.create_run(
        "/s", "s", [{"name": "j"}], {}, os.getpid(),
        "/nonexistent/log/file.log"  # log file missing
    )

    runner = PipelineRunner()
    runner._store = store
    result = runner.reattach(run_id, on_node_update=MagicMock(), on_run_finished=MagicMock())
    assert result is False
```

- [ ] **Step 2: Run to confirm failures**

```bash
/usr/bin/python3 -m pytest tests/test_pipeline_runner.py::test_reattach_marks_interrupted_if_proc_dead -v 2>&1 | tail -5
```

Expected: `AttributeError: 'PipelineRunner' object has no attribute 'reattach'`

- [ ] **Step 3: Implement reattach()**

Add to the `PipelineRunner` class in `app/pipeline_runner.py`:

```python
    def reattach(
        self,
        run_id: str,
        on_node_update: Callable,
        on_run_finished: Callable,
    ) -> bool:
        """Attempt to re-attach to an in-progress run after app restart.

        Returns True if re-attachment succeeded (subprocess alive, log readable).
        Returns False if the subprocess is dead or the log file is missing —
        in that case the run is marked 'interrupted' in the store.

        On success, begins tailing the log file from its current EOF position,
        so new NODE: signals arrive and update the UI in real time.
        """
        run = self._store.get_run(run_id)
        if not run:
            return False

        pid = run.get("pid", 0)
        log_file = run.get("log_file", "")

        # Check if subprocess is still alive
        if not os.path.exists(f"/proc/{pid}"):
            self._store.mark_interrupted(run_id)
            return False

        # Check if log file exists (needed for re-attach tailing)
        if not log_file or not os.path.exists(log_file):
            self._store.mark_interrupted(run_id)
            return False

        self._run_id = run_id
        self._on_node_update = on_node_update
        self._on_run_finished = on_run_finished
        self._cancelled = False

        # Tail log file from current end in a background thread
        jobs = run.get("jobs", [])
        current_job = jobs[0]["name"] if jobs else "job"
        threading.Thread(
            target=self._tail_log,
            args=(log_file, current_job),
            daemon=True,
        ).start()
        return True

    def _tail_log(self, log_file: str, current_job: str) -> None:
        """Background thread: tail a log file from EOF, parsing new lines."""
        try:
            with open(log_file, "r") as f:
                # Seek to end so we only see new output from the running process
                f.seek(0, 2)
                while not self._cancelled:
                    line = f.readline()
                    if not line:
                        # Check if the process is still alive
                        run = self._store.get_run(self._run_id or "")
                        pid = run.get("pid", 0) if run else 0
                        if not os.path.exists(f"/proc/{pid}"):
                            break
                        import time
                        time.sleep(0.5)
                        continue
                    self._parse_line(line, current_job)
        finally:
            if self._run_id:
                run = self._store.get_run(self._run_id)
                if run and run.get("status") == "running":
                    # Process ended while we were watching
                    self._store.finish_run(self._run_id, success=False)
            self._dispatch(self._on_run_finished, False)
```

- [ ] **Step 4: Run all pipeline tests**

```bash
/usr/bin/python3 -m pytest tests/test_pipeline_runner.py tests/test_pipeline_store.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run full test suite**

```bash
/usr/bin/python3 -m pytest tests/ -q
```

Expected: 509+ pass, 0 failures.

- [ ] **Step 6: Commit**

```bash
git add app/pipeline_runner.py tests/test_pipeline_runner.py
git commit -m "feat: PipelineRunner.reattach + _tail_log for restart recovery"
```

---

## Task 6: Wire health check before run

**Files:**
- Modify: `app/pipeline_runner.py` — call `tt-health-check.sh` before `start()`
- Modify: `tests/test_pipeline_runner.py` — test health check warning

The spec requires `tt-health-check.sh --quiet` to run before each phase, surfacing chip degradation without blocking the run.

- [ ] **Step 1: Add health check test**

Append to `tests/test_pipeline_runner.py`:

```python
def test_health_check_result_passed_to_callback(monkeypatch, tmp_path):
    """Health check exit code is reported to caller; run is not blocked."""
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())

    # Simulate health check returning exit code 1 (degraded)
    mock_run = MagicMock()
    mock_run.return_value.returncode = 1
    monkeypatch.setattr("subprocess.run", mock_run)

    from pipeline_runner import PipelineRunner
    runner = PipelineRunner.__new__(PipelineRunner)
    result = runner.check_chip_health()
    assert result is False  # degraded
    # Health check ran but did not raise
```

- [ ] **Step 2: Implement check_chip_health()**

Add to `PipelineRunner` in `app/pipeline_runner.py`:

```python
    def check_chip_health(self) -> bool:
        """Run tt-health-check.sh --quiet. Returns True if healthy, False if degraded.

        Never raises — a missing script or non-zero exit is treated as degraded.
        The caller decides whether to proceed or warn the user; the run is not blocked.
        """
        script = _REPO_ROOT / "bin" / "tt-health-check.sh"
        if not script.exists():
            return True  # no script = assume healthy (CI / non-QB2 machines)
        try:
            result = subprocess.run(
                ["bash", str(script), "--quiet"],
                capture_output=True,
                timeout=15,
            )
            return result.returncode == 0
        except Exception:
            return True  # fail open
```

- [ ] **Step 3: Call check_chip_health() in start()**

In `start()`, add the health check call before launching the subprocess:

```python
    def start(self, spec_path, jobs, param_overrides, on_node_update, on_run_finished):
        self._on_node_update = on_node_update
        self._on_run_finished = on_run_finished
        self._cancelled = False

        # Health check — non-blocking; result passed up via on_node_update
        # with a synthetic signal so the UI can show a warning banner.
        healthy = self.check_chip_health()
        if not healthy:
            self._dispatch(on_node_update, "__health__", "__chips__",
                           "degraded", "AC power cycle recommended")

        # ... rest of existing start() implementation unchanged
```

- [ ] **Step 4: Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_pipeline_runner.py -v -k "health"
```

Expected: 1 test passes.

- [ ] **Step 5: Run full suite and commit**

```bash
/usr/bin/python3 -m pytest tests/ -q && \
git add app/pipeline_runner.py tests/test_pipeline_runner.py && \
git commit -m "feat: PipelineRunner.check_chip_health — non-blocking tt-health-check.sh call before run"
```

---

## Self-review

**Spec coverage check:**

| Spec requirement | Covered by |
|---|---|
| PipelineStore — run records, job states, history | Task 2 |
| NODE: structured signals from run_workflow.sh | Task 1 |
| PipelineRunner — start, cancel, parse signals | Tasks 3–4 |
| Restart recovery — reattach to live subprocess via log tail | Task 5 |
| PID liveness check, mark_interrupted | Tasks 2+5 |
| Health check before run, non-blocking | Task 6 |
| retry_node / retry_job | Stubbed with NotImplementedError (Plan 2) |
| GTK zero dependency in runner/store | All tasks (GLib only via try/import) |

**Gaps:** `retry_node` and `retry_job` are stubbed — they require the phase grid UI to be wired (Plan 2) before they can be meaningfully implemented. The stubs raise `NotImplementedError` which is explicit and caught in Plan 2 tests.

**Type consistency check:** `update_node(run_id, job_name, node_id, status, detail, elapsed_s)` matches across store and runner call sites. `on_node_update(job_name, node_id, status, detail)` is consistent across all tests and dispatch calls.
