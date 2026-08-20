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

    def __init__(self, idle_add=None) -> None:
        """
        Args:
            idle_add: callable(fn, *args) that schedules fn(*args) on the GTK
                      main loop.  Pass GLib.idle_add in production.  When None,
                      defaults to GLib.idle_add if GLib is available, or a
                      direct-call shim when running headless (tests/CI).
        """
        if idle_add is not None:
            self._idle_add = idle_add
        elif GLib is not None and isinstance(getattr(GLib, "MAXINT", None), int):
            # Real GLib binding confirmed (MAXINT is a C integer constant that
            # MagicMocks and other fakes do not expose as an int).
            self._idle_add = GLib.idle_add
        else:
            # Headless / test environment — call directly on the current thread.
            self._idle_add = lambda fn, *a: fn(*a)

        self._store = PipelineStore()
        self._run_id: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._log_file: Optional[str] = None
        self._on_node_update: Optional[Callable] = None
        self._on_run_finished: Optional[Callable] = None
        self._on_log: Optional[Callable] = None
        self._active_jobs: dict[str, dict] = {}
        self._cancelled = False
        # _retry_mode: set True by retry_node() so _watch_stdout does not call
        # finish_run() and overwrite the original run record's status/job_states.
        self._retry_mode = False

    # ── Signal parser ─────────────────────────────────────────────────────────

    def _parse_line(self, line: str, current_job: str) -> None:
        """Parse one stdout line from run_workflow.sh and dispatch callbacks."""
        line = line.rstrip()

        if line.startswith("LOG:"):
            self._log_file = line[4:].strip()
            # Derive output_dir from the log filename timestamp.
            # Log path:    .../logs/workflow/YYYYMMDD_HHMMSS_*.log
            # Output dir:  .../workflow-runs/YYYYMMDD_HHMMSS/
            # Both are written by run_workflow.sh (and run_single_node.sh for
            # retries) using the same timestamp prefix, so the pairing is exact.
            import re as _re
            m = _re.search(r'(\d{8}_\d{6})_', self._log_file)
            if m and self._run_id:
                ts = m.group(1)
                output_dir = str(
                    Path.home() / ".local" / "share" / "tt-local-generator"
                    / "workflow-runs" / ts
                )
                self._store.update_log_file(self._run_id, self._log_file)
                self._store.update_output_dir(self._run_id, output_dir)
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
        """Schedule callback(*args) on the GTK main thread via self._idle_add."""
        if callback is None:
            return
        self._idle_add(callback, *args)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(
        self,
        spec_path: str,
        jobs: list[dict],
        param_overrides: dict,
        on_node_update: Callable,
        on_run_finished: Callable,
        on_log: Optional[Callable] = None,
        run_id: Optional[str] = None,
    ) -> None:
        """Launch run_workflow.sh for the given jobs and spec.

        on_log, if given, is called with every raw stdout line the subprocess
        emits (verbatim, including the trailing newline) — e.g. so a live-run
        view can tail the log alongside the parsed NODE:/LOG: signals. It is
        optional and defaults to None so existing callers (and every test that
        predates this parameter) are unaffected.

        run_id, if given, is an ALREADY-CREATED PipelineStore run id (e.g. a
        provisional record PipelineStudio._on_run_remix created up front so
        LiveRunView.begin() has a RunView to paint immediately). When
        provided, start() adopts it — self._run_id = run_id and the store's
        pid=0 placeholder is patched via update_pid() — instead of minting a
        brand-new record via create_run(). This is what makes the adopted
        record the SINGLE record that receives node/output/finish updates;
        previously start() unconditionally called create_run() itself,
        producing a second, divergent record the caller's provisional one
        never shared (the SP-C Remix→Run dual-run-record bug). When run_id is
        None (every pre-existing caller), behavior is unchanged: a new record
        is minted here as before.
        """
        self._on_node_update = on_node_update
        self._on_run_finished = on_run_finished
        self._on_log = on_log
        self._cancelled = False

        # Non-blocking health check — emits synthetic signal if degraded
        healthy = self.check_chip_health()
        if not healthy:
            self._dispatch(on_node_update, "__health__", "__chips__",
                           "degraded", "AC power cycle recommended")

        log_dir = Path.home() / ".local" / "share" / "tt-local-generator" / "logs" / "pipeline"
        log_dir.mkdir(parents=True, exist_ok=True)

        env = {**os.environ}
        if run_id is not None:
            # Adopt the caller's existing record BEFORE Popen runs (not
            # after) so that if Popen raises, the except block below still
            # sees self._run_id set and can mark that record failed instead
            # of orphaning it in "running" state forever. The PID isn't
            # known yet at this point — update_pid() is called after a
            # successful Popen, below.
            self._run_id = run_id
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
            if run_id is not None:
                # Patch in the real PID now that we have it.
                self._store.update_pid(run_id, self._proc.pid)
            else:
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

    def retry_node(
        self,
        job_name: str,
        node_id: str,
        on_node_update: Callable,
        on_run_finished: Callable,
    ) -> None:
        """Re-run a single failed node using the existing results.json as context.

        Launches bin/run_single_node.sh <results.json> <node_id> as a new
        subprocess and streams its output through the normal _watch_stdout /
        _parse_line pipeline so node-state callbacks and the store are updated
        identically to a fresh run.

        Raises:
            ValueError: if no active run, run not in store, output_dir not set,
                        or results.json missing at the expected path.
        """
        if not self._run_id:
            raise ValueError("No active run — call start() or reattach() first")

        store_run = self._store.get_run(self._run_id)
        if not store_run:
            raise ValueError(f"Run {self._run_id} not found in store")

        output_dir = store_run.get("output_dir", "")
        if not output_dir:
            raise ValueError(
                "output_dir not set — run may not have emitted a LOG: signal yet"
            )

        results_json = Path(output_dir) / "results.json"
        if not results_json.exists():
            raise ValueError(f"results.json not found at {results_json}")

        self._on_node_update = on_node_update
        self._on_run_finished = on_run_finished
        self._cancelled = False
        # Flag _watch_stdout to skip finish_run() so the original run record's
        # status and job_states are not overwritten by the single-node retry.
        self._retry_mode = True

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
        except Exception:
            self._retry_mode = False
            self._dispatch(on_run_finished, False)

    def retry_job(
        self,
        job_name: str,
        on_node_update: Callable,
        on_run_finished: Callable,
    ) -> None:
        """Re-run a job starting from its first failed node.

        Finds the lowest-numbered node with status "failed" for *job_name*
        in the stored run record and delegates to retry_node().  If no failed
        nodes exist this is a no-op (idempotent — safe to call defensively).

        Raises the same errors as retry_node() when a failed node is found.
        """
        if not self._run_id:
            raise ValueError("No active run")

        store_run = self._store.get_run(self._run_id)
        if not store_run:
            raise ValueError(f"Run {self._run_id} not found")

        job_states = store_run.get("job_states", {}).get(job_name, {})
        failed_nodes = [
            nid for nid, state in job_states.items()
            if state.get("status") == "failed"
        ]
        if not failed_nodes:
            # Nothing to retry — treat as success (caller need not handle this).
            return

        # Sort numerically where possible; non-numeric node IDs sort last.
        first_failed = sorted(
            failed_nodes, key=lambda n: int(n) if n.isdigit() else 999
        )[0]
        self.retry_node(job_name, first_failed, on_node_update, on_run_finished)

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
            # The process is still alive but we don't have a log path.  This
            # happens when the app crashed before run_workflow.sh emitted its
            # first LOG: signal.  Rather than marking the run interrupted and
            # orphaning a potentially long job, try to locate the log file by
            # scanning the pipeline logs directory for any file created on or
            # after the run's start timestamp.
            log_dir = Path.home() / ".local" / "share" / "tt-local-generator" / "logs" / "pipeline"
            candidate = None
            if log_dir.exists():
                from datetime import datetime, timezone as _tz
                started_ts = run.get("started_at", "")
                try:
                    started_epoch = (
                        datetime.fromisoformat(started_ts).timestamp()
                        if started_ts
                        else 0.0
                    )
                    # Sort newest-first so we pick the most recent log for the
                    # run; allow a 5 s grace window for filesystem timestamp skew.
                    candidates = sorted(
                        log_dir.glob("*.log"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    for c in candidates:
                        if c.stat().st_mtime >= started_epoch - 5:
                            candidate = str(c)
                            break
                except Exception:
                    pass

            if candidate:
                log_file = candidate
                # Persist the discovered path so future restarts find it directly.
                self._store.update_log_file(run_id, log_file)
            else:
                # No log file found even though the process is running.  We
                # cannot show progress, but we must not mark the run interrupted
                # — the job is still alive and may complete.  Emit a warning
                # synthetic node signal so the UI can surface the situation.
                self._dispatch(
                    on_node_update,
                    "__health__", "__reattach__",
                    "warn",
                    f"Live process (pid={pid}) but no log file — cannot show progress",
                )
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
                self._dispatch(self._on_log, line)
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
            # Only update the persistent run record for full runs, not single-node
            # retries.  retry_node() sets _retry_mode=True to prevent overwriting
            # the original run's status and job_states on retry completion.
            if self._run_id and not self._retry_mode:
                self._store.finish_run(self._run_id, success=success)
            self._retry_mode = False
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
                if run:
                    if run.get("status") == "running":
                        # Process ended while we were watching — mark as failed.
                        self._store.finish_run(self._run_id, success=False)
                        self._dispatch(self._on_run_finished, False)
                    else:
                        # Run already has a terminal status (done/failed/interrupted)
                        # that was set before reattach was called (e.g. it completed
                        # during app downtime).  Report the actual outcome truthfully
                        # rather than always reporting False.
                        success = run.get("status") == "done"
                        self._dispatch(self._on_run_finished, success)
