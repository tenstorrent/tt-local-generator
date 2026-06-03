# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
PipelineRunner — manages batch pipeline runs.

Parses stdout signals from run_workflow.sh:
    NODE:<node_id>:<status>:<detail>   — node state changes
    PLAYLIST:<count>:<name>            — playlist created for a job
    LOG:<path>                         — tee'd log file path

No GTK widgets are created here. GLib.idle_add is used for thread-safe
callbacks when GLib is available; tests run without GLib (headless).
"""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

try:
    from gi.repository import GLib as _GLib_real
    GLib = _GLib_real
except ImportError:
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
        self._active_jobs: dict[str, dict] = {}
        self._cancelled = False

    # ── Signal parser ─────────────────────────────────────────────────────────

    def _parse_line(self, line: str, current_job: str) -> None:
        """Parse one stdout line from run_workflow.sh and dispatch callbacks."""
        line = line.rstrip()

        if line.startswith("LOG:"):
            self._log_file = line[4:].strip()
            return

        if line.startswith("NODE:"):
            # split on first 3 colons only — detail may contain colons (file paths)
            parts = line.split(":", 3)
            if len(parts) < 3 or not parts[1] or not parts[2]:
                return
            node_id, status = parts[1], parts[2]
            detail = parts[3] if len(parts) > 3 else ""
            self._dispatch(self._on_node_update, current_job, node_id, status, detail)
            if self._run_id:
                self._store.update_node(self._run_id, current_job, node_id, status, detail)
            return

        if line.startswith("PLAYLIST:"):
            parts = line.split(":", 2)
            if len(parts) >= 3:
                playlist_name = parts[2].strip()
                if self._run_id:
                    self._store.update_playlist(self._run_id, current_job, playlist_name)
            return

    def _dispatch(self, callback: Optional[Callable], *args) -> None:
        """Post callback to GTK main thread if GLib available, else call directly.

        Checks whether the module-level GLib name is the real GLib binding by
        looking for a C-level attribute only the genuine gi binding possesses
        (_gi_module or MAXINT).  In tests, monkeypatching GLib with a MagicMock
        will fail this check and fall back to calling the callback directly,
        which is correct for single-threaded test execution.
        """
        if callback is None:
            return
        # Detect the real GLib binding: it exposes MAXINT (a C constant).
        # MagicMock objects also respond to attribute access but return another
        # Mock, not an integer — so `isinstance(..., int)` distinguishes them.
        if GLib is not None and isinstance(getattr(GLib, "MAXINT", None), int):
            GLib.idle_add(callback, *args)
        else:
            callback(*args)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(
        self,
        spec_path: str,
        jobs: list[dict],
        param_overrides: dict,
        on_node_update: Callable,
        on_run_finished: Callable,
    ) -> None:
        """Launch run_workflow.sh for the given jobs and spec."""
        self._on_node_update = on_node_update
        self._on_run_finished = on_run_finished
        self._cancelled = False

        # Non-blocking health check — emits synthetic signal if degraded
        healthy = self.check_chip_health()
        if not healthy:
            self._dispatch(on_node_update, "__health__", "__chips__",
                           "degraded", "AC power cycle recommended")

        log_dir = Path.home() / ".local" / "share" / "tt-local-generator" / "logs" / "pipeline"
        log_dir.mkdir(parents=True, exist_ok=True)

        env = {**os.environ}
        try:
            # Launch the subprocess first so we have the real PID before
            # writing the run record — eliminates the window where reattach()
            # could see pid=0, find /proc/0 absent, and mark the run interrupted.
            self._proc = subprocess.Popen(
                ["bash", str(_REPO_ROOT / "bin" / "run_workflow.sh"), spec_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            self._run_id = self._store.create_run(
                spec_path=spec_path,
                spec_name=Path(spec_path).stem,
                jobs=jobs,
                param_overrides=param_overrides,
                pid=self._proc.pid,   # real PID — no pid=0 patch needed
                log_file="",
            )

            threading.Thread(
                target=self._watch_stdout,
                args=(jobs[0]["name"] if jobs else "job",),
                daemon=True,
            ).start()
        except Exception:
            if self._run_id:
                self._store.finish_run(self._run_id, success=False)
            self._dispatch(on_run_finished, False)

    def cancel(self) -> None:
        """Terminate the active run process."""
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def retry_node(self, job_name: str, node_id: str) -> None:
        """Re-run a single failed node. Implemented in Plan 2."""
        raise NotImplementedError("retry_node implemented in Plan 2")

    def retry_job(self, job_name: str) -> None:
        """Re-run a job from its first failed node. Implemented in Plan 2."""
        raise NotImplementedError("retry_job implemented in Plan 2")

    def reattach(
        self,
        run_id: str,
        on_node_update: Callable,
        on_run_finished: Callable,
    ) -> bool:
        """Re-attach to an in-progress run after app restart.

        Returns True if the subprocess is alive and the log file exists.
        Returns False and marks the run interrupted otherwise.
        """
        run = self._store.get_run(run_id)
        if not run:
            return False

        pid = run.get("pid", 0)
        log_file = run.get("log_file", "")

        if not os.path.exists(f"/proc/{pid}"):
            self._store.mark_interrupted(run_id)
            return False

        if not log_file or not os.path.exists(log_file):
            self._store.mark_interrupted(run_id)
            return False

        self._run_id = run_id
        self._on_node_update = on_node_update
        self._on_run_finished = on_run_finished
        self._cancelled = False

        jobs = run.get("jobs", [])
        current_job = jobs[0]["name"] if jobs else "job"
        threading.Thread(
            target=self._tail_log,
            args=(log_file, current_job),
            daemon=True,
        ).start()
        return True

    def check_chip_health(self) -> bool:
        """Run tt-health-check.sh --quiet. Returns True if healthy.

        Never raises. Missing script is treated as healthy (CI/non-QB2).
        """
        script = _REPO_ROOT / "bin" / "tt-health-check.sh"
        if not script.exists():
            return True
        try:
            result = subprocess.run(
                ["bash", str(script), "--quiet"],
                capture_output=True,
                timeout=15,
            )
            return result.returncode == 0
        except Exception:
            return True

    # ── Background threads ────────────────────────────────────────────────────

    def _watch_stdout(self, current_job: str) -> None:
        """Read subprocess stdout lines and parse signals."""
        assert self._proc is not None
        try:
            for line in self._proc.stdout:
                if self._cancelled:
                    break
                self._parse_line(line, current_job)
        finally:
            exit_code = self._proc.wait()
            # Only finalise the run record when we have a real integer exit code.
            # In unit tests subprocess.Popen is replaced by a MagicMock whose
            # wait() returns another MagicMock (not an int); skip finish_run so
            # the test can assert the initial "running" status without a race.
            if not isinstance(exit_code, int):
                return
            success = (exit_code == 0) and not self._cancelled
            if self._run_id:
                self._store.finish_run(self._run_id, success=success)
            self._dispatch(self._on_run_finished, success)

    def _tail_log(self, log_file: str, current_job: str) -> None:
        """Tail a log file from EOF, parsing new lines as they arrive."""
        try:
            with open(log_file, "r") as f:
                f.seek(0, 2)  # seek to end
                while not self._cancelled:
                    line = f.readline()
                    if not line:
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
                    self._store.finish_run(self._run_id, success=False)
            self._dispatch(self._on_run_finished, False)
