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
        "output_dir":     str,       # workflow-runs/<timestamp>/ for results.json
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
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_RUNS_DIR = Path.home() / ".local" / "share" / "tt-local-generator" / "workflow-runs"
_INDEX_PATH = _RUNS_DIR / "pipeline-index.json"


class PipelineStore:
    """JSON-backed list of pipeline run records."""

    def __init__(self) -> None:
        # Capture paths at construction time so this instance always writes to
        # the same location, even if module-level globals are later monkeypatched
        # by tests and then restored during test teardown. Without this, a daemon
        # thread that outlives the monkeypatch context would write to the restored
        # (production) path instead of the test's tmp_path.
        self._index_path = _INDEX_PATH
        self._runs_dir = _RUNS_DIR
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        # Protects all _load/_save pairs against concurrent access from the
        # background _watch_stdout thread and the GTK main thread.
        self._lock = threading.Lock()

    # ── Read ──────────────────────────────────────────────────────────────────

    def _load(self) -> list[dict]:
        try:
            return json.loads(self._index_path.read_text())
        except Exception:
            return []

    def _save(self, records: list[dict]) -> None:
        self._index_path.write_text(json.dumps(records, indent=2))

    def get_run(self, run_id: str) -> Optional[dict]:
        with self._lock:
            return next((r for r in self._load() if r["id"] == run_id), None)

    def list_runs(self, spec_path: Optional[str] = None, limit: int = 50) -> list[dict]:
        with self._lock:
            records = self._load()
            if spec_path:
                records = [r for r in records if r.get("spec_path") == spec_path]
            return records[:limit]

    def find_interrupted_runs(self) -> list[dict]:
        """Return runs with status 'running' whose PID is no longer alive."""
        with self._lock:
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
            "output_dir": "",
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "job_states": {j["name"]: {} for j in jobs},
            "playlist_ids": {j["name"]: None for j in jobs},
        }
        with self._lock:
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
        with self._lock:
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
        with self._lock:
            records = self._load()
            for r in records:
                if r["id"] == run_id:
                    r.setdefault("playlist_ids", {})[job_name] = playlist_id
                    break
            self._save(records)

    def finish_run(self, run_id: str, success: bool) -> None:
        with self._lock:
            records = self._load()
            for r in records:
                if r["id"] == run_id:
                    r["status"] = "done" if success else "failed"
                    r["finished_at"] = datetime.now(timezone.utc).isoformat()
                    break
            self._save(records)

    def mark_interrupted(self, run_id: str) -> None:
        with self._lock:
            records = self._load()
            for r in records:
                if r["id"] == run_id:
                    r["status"] = "interrupted"
                    break
            self._save(records)

    def update_log_file(self, run_id: str, log_file: str) -> None:
        """Persist a discovered log_file path for an existing run record.

        Called by reattach() when the log file was found by scanning the logs
        directory rather than from the stored record directly — ensures future
        restarts can find the log without re-scanning.
        """
        with self._lock:
            records = self._load()
            for r in records:
                if r["id"] == run_id:
                    r["log_file"] = log_file
                    break
            self._save(records)

    def update_pid(self, run_id: str, pid: int) -> None:
        """Persist a discovered/adopted PID for an existing run record.

        Called by PipelineRunner.start(run_id=...) when the caller supplies
        an already-created run id (e.g. PipelineStudio._on_run_remix's
        provisional record) rather than minting a new one via create_run().
        The subprocess's real PID is only known once Popen() returns, so the
        record's pid=0 placeholder is patched in here — mirrors
        update_log_file/update_output_dir's load-find-set-save pattern.
        """
        with self._lock:
            records = self._load()
            for r in records:
                if r["id"] == run_id:
                    r["pid"] = pid
                    break
            self._save(records)

    def update_output_dir(self, run_id: str, output_dir: str) -> None:
        """Persist the workflow output directory path for an existing run record.

        Called by PipelineRunner._parse_line() when a LOG: signal arrives —
        the timestamp in the log filename is used to derive the matching
        workflow-runs/<timestamp>/ directory where results.json is written.
        Required by retry_node() to locate results.json without a full
        directory scan.
        """
        with self._lock:
            records = self._load()
            for r in records:
                if r["id"] == run_id:
                    r["output_dir"] = output_dir
                    break
            self._save(records)
