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
    runner._idle_add = lambda fn, *a: fn(*a)  # direct-call shim for headless tests
    runner._on_node_update = on_node_update or MagicMock()
    runner._on_run_finished = on_run_finished or MagicMock()
    runner._run_id = "test-run-id"
    runner._active_jobs = {"1964-ny": {}, "1939-ny": {}}
    runner._store = MagicMock()
    runner._log_file = None
    runner._cancelled = False
    return runner


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
    """Detail field may contain colons (e.g. file paths). split(:,3) handles this."""
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
    runner._parse_line("LOG:/tmp/pipeline_run.log", "1964-ny")
    assert runner._log_file == "/tmp/pipeline_run.log"


def test_malformed_node_signal_no_crash(monkeypatch):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    runner = make_runner()
    runner._parse_line("NODE:bad", "job")
    runner._parse_line("NODE::running:", "job")
    runner._on_node_update.assert_not_called()


# ── Subprocess management ────────────────────────────────────────────────────

def test_cancel_terminates_process(monkeypatch):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    from pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    runner._run_id = "x"
    runner._store = MagicMock()
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    runner._proc = mock_proc
    runner.cancel()
    mock_proc.terminate.assert_called_once()
    assert runner._cancelled is True


def test_cancel_when_no_proc_does_not_crash(monkeypatch):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    from pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    runner._proc = None
    runner.cancel()


def test_start_creates_run_record(monkeypatch, tmp_path):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    monkeypatch.setattr("pipeline_store._INDEX_PATH", tmp_path / "idx.json")
    monkeypatch.setattr("pipeline_store._RUNS_DIR", tmp_path)
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


def test_start_without_run_id_calls_create_run_not_update_pid(monkeypatch, tmp_path):
    """Backward-compat: start() with no run_id= kwarg must behave exactly as
    before -- mint a new record via create_run() and never touch update_pid
    (that method only exists to adopt a CALLER-provided id). Regression guard
    for the SP-C dual-run-record bug fix: existing callers that don't pass
    run_id= must see zero behavior change."""
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    mock_popen = MagicMock()
    mock_popen.return_value.pid = 12345
    mock_popen.return_value.stdout = iter([])
    monkeypatch.setattr("subprocess.Popen", mock_popen)
    from pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    mock_store = MagicMock()
    mock_store.create_run.return_value = "minted-id"
    runner._store = mock_store

    runner.start(
        spec_path=str(tmp_path / "spec.json"),
        jobs=[{"name": "test-job", "prompt": "a test prompt"}],
        param_overrides={},
        on_node_update=MagicMock(),
        on_run_finished=MagicMock(),
    )

    mock_store.create_run.assert_called_once()
    mock_store.update_pid.assert_not_called()
    assert runner._run_id == "minted-id"


def test_start_with_run_id_adopts_record_instead_of_creating_new(monkeypatch, tmp_path):
    """start(run_id=...) must ADOPT the given id -- set self._run_id = run_id
    and call self._store.update_pid(run_id, pid) -- rather than minting a new
    record via create_run(). This is the fix for the SP-C Remix->Run
    dual-run-record bug: PipelineStudio._on_run_remix already creates a
    provisional record and hands its RunView to LiveRunView.begin(); if
    start() ALSO called create_run() internally (the old behavior), every
    node/output/finish update from this run would land on a second, different
    record that LiveRunView never saw -- Open's rebuild-on-finish would then
    read the never-updated provisional record (all steps pending, no
    artifacts) instead of the real results."""
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    mock_popen = MagicMock()
    mock_popen.return_value.pid = 55555
    mock_popen.return_value.stdout = iter([])
    monkeypatch.setattr("subprocess.Popen", mock_popen)
    from pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    mock_store = MagicMock()
    runner._store = mock_store

    runner.start(
        spec_path=str(tmp_path / "spec.json"),
        jobs=[{"name": "test-job", "prompt": "a test prompt"}],
        param_overrides={},
        on_node_update=MagicMock(),
        on_run_finished=MagicMock(),
        run_id="fixed-id",
    )

    mock_store.create_run.assert_not_called()
    mock_store.update_pid.assert_called_once_with("fixed-id", 55555)
    assert runner._run_id == "fixed-id"


def test_start_with_run_id_node_updates_target_adopted_id(monkeypatch, tmp_path):
    """Once start(run_id=...) has adopted the id, node-signal parsing (which
    reads self._run_id) must write updates to THAT id, not a runner-minted
    one -- this is what makes the adopted record the single live record."""
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    mock_popen = MagicMock()
    mock_popen.return_value.pid = 55555
    mock_popen.return_value.stdout = iter([])
    monkeypatch.setattr("subprocess.Popen", mock_popen)
    from pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    mock_store = MagicMock()
    runner._store = mock_store

    runner.start(
        spec_path=str(tmp_path / "spec.json"),
        jobs=[{"name": "test-job", "prompt": "a test prompt"}],
        param_overrides={},
        on_node_update=MagicMock(),
        on_run_finished=MagicMock(),
        run_id="fixed-id",
    )

    # _watch_stdout/_parse_line dispatch node updates using self._run_id --
    # confirm it's the adopted id, then confirm a node update actually lands
    # on that id via the store.
    runner._parse_line("NODE:1:running:FLUX.1-schnell", "test-job")
    mock_store.update_node.assert_called_once_with(
        "fixed-id", "test-job", "1", "running", "FLUX.1-schnell"
    )


# ── Restart recovery ──────────────────────────────────────────────────────────

def test_reattach_marks_interrupted_if_proc_dead(monkeypatch, tmp_path):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    monkeypatch.setattr("pipeline_store._INDEX_PATH", tmp_path / "idx.json")
    monkeypatch.setattr("pipeline_store._RUNS_DIR", tmp_path)
    monkeypatch.setattr("os.path.exists", lambda p: False)
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
    """reattach() returns False when log_file is set but the file does not exist
    and no candidate log is found in the pipeline logs directory.  The run must
    NOT be marked interrupted because the process is still alive."""
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    monkeypatch.setattr("pipeline_store._INDEX_PATH", tmp_path / "idx.json")
    monkeypatch.setattr("pipeline_store._RUNS_DIR", tmp_path)
    import os
    monkeypatch.setattr(os.path, "exists",
                        lambda p: p == f"/proc/{os.getpid()}")
    from pipeline_runner import PipelineRunner
    from pipeline_store import PipelineStore
    store = PipelineStore()
    run_id = store.create_run("/s", "s", [{"name": "j"}], {}, os.getpid(),
                              "/nonexistent/log/file.log")
    runner = PipelineRunner()
    runner._store = store
    result = runner.reattach(run_id, on_node_update=MagicMock(), on_run_finished=MagicMock())
    assert result is False
    # Run must remain "running" — not interrupted — because the PID is alive.
    assert store.get_run(run_id)["status"] == "running"


def test_reattach_dispatches_warn_when_no_log_found(monkeypatch, tmp_path):
    """When the PID is alive but no log file can be found (neither the stored
    path nor any candidate in the logs directory), reattach() must dispatch a
    synthetic warn node-update rather than silently returning False."""
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    monkeypatch.setattr("pipeline_store._INDEX_PATH", tmp_path / "idx.json")
    monkeypatch.setattr("pipeline_store._RUNS_DIR", tmp_path)
    import os
    monkeypatch.setattr(os.path, "exists",
                        lambda p: p == f"/proc/{os.getpid()}")
    from pipeline_runner import PipelineRunner
    from pipeline_store import PipelineStore
    store = PipelineStore()
    run_id = store.create_run("/s", "s", [{"name": "j"}], {}, os.getpid(), "")
    runner = PipelineRunner()
    runner._store = store
    on_node_update = MagicMock()
    on_run_finished = MagicMock()
    result = runner.reattach(run_id, on_node_update=on_node_update,
                             on_run_finished=on_run_finished)
    assert result is False
    # A warn synthetic signal must be dispatched to on_node_update.
    on_node_update.assert_called_once()
    args = on_node_update.call_args[0]
    assert args[0] == "__health__"
    assert args[1] == "__reattach__"
    assert args[2] == "warn"
    # on_run_finished must NOT be called — the run is still live.
    on_run_finished.assert_not_called()


def test_tail_log_finally_reports_true_for_already_completed_run(monkeypatch, tmp_path):
    """_tail_log finally block must call on_run_finished(True) when the run
    record already has status 'done' before tailing begins (i.e. the run
    completed during app downtime and reattach is catching up)."""
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    monkeypatch.setattr("pipeline_store._INDEX_PATH", tmp_path / "idx.json")
    monkeypatch.setattr("pipeline_store._RUNS_DIR", tmp_path)

    # Create a log file that is already at EOF so _tail_log exits immediately.
    log_file = tmp_path / "run.log"
    log_file.write_text("")

    import os
    # PID does not exist → the inner polling loop exits on the first readline()
    monkeypatch.setattr(os.path, "exists", lambda p: False)

    from pipeline_runner import PipelineRunner
    from pipeline_store import PipelineStore
    store = PipelineStore()
    run_id = store.create_run("/s", "s", [{"name": "j"}], {}, 99999,
                              str(log_file))
    # Pre-set the run to 'done' to simulate a run that finished during downtime.
    store.finish_run(run_id, success=True)

    on_run_finished = MagicMock()
    runner = PipelineRunner()
    runner._store = store
    runner._run_id = run_id
    runner._on_node_update = MagicMock()
    runner._on_run_finished = on_run_finished
    runner._cancelled = False

    # Run _tail_log synchronously (it will exit immediately).
    runner._tail_log(str(log_file), "j")

    on_run_finished.assert_called_once_with(True)


def test_tail_log_finally_reports_false_when_run_was_still_running(monkeypatch, tmp_path):
    """_tail_log finally block must call on_run_finished(False) and mark the
    run failed when the process ended while we were watching (status is still
    'running' at finally time)."""
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    monkeypatch.setattr("pipeline_store._INDEX_PATH", tmp_path / "idx.json")
    monkeypatch.setattr("pipeline_store._RUNS_DIR", tmp_path)

    log_file = tmp_path / "run.log"
    log_file.write_text("")

    import os
    monkeypatch.setattr(os.path, "exists", lambda p: False)

    from pipeline_runner import PipelineRunner
    from pipeline_store import PipelineStore
    store = PipelineStore()
    run_id = store.create_run("/s", "s", [{"name": "j"}], {}, 99999,
                              str(log_file))
    # Leave status as 'running' — simulates the process dying unexpectedly.

    on_run_finished = MagicMock()
    runner = PipelineRunner()
    runner._store = store
    runner._run_id = run_id
    runner._on_node_update = MagicMock()
    runner._on_run_finished = on_run_finished
    runner._cancelled = False

    runner._tail_log(str(log_file), "j")

    on_run_finished.assert_called_once_with(False)
    assert store.get_run(run_id)["status"] == "failed"


# ── Live log tail (SP-C Phase 2a Task 4) ─────────────────────────────────────

def test_watch_stdout_forwards_each_line_to_on_log(monkeypatch):
    """_watch_stdout must dispatch every raw stdout line to on_log, verbatim,
    in addition to the existing NODE:/LOG:/PLAYLIST: parsing — this is the
    only source LiveRunView.on_log has for its live log tail (see
    pipeline_studio.py's LiveRunView docstring)."""
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    runner = make_runner()
    on_log = MagicMock()
    runner._on_log = on_log

    lines = ["NODE:1:running:FLUX.1-schnell\n", "LOG:/tmp/pipeline.log\n",
             "some plain progress line\n"]
    mock_proc = MagicMock()
    mock_proc.stdout = iter(lines)
    mock_proc.wait.return_value = MagicMock()  # non-int -> finally returns early
    runner._proc = mock_proc

    runner._watch_stdout("test-job")

    assert on_log.call_args_list == [call(line) for line in lines]


def test_start_default_on_log_is_none_backward_compatible(monkeypatch, tmp_path):
    """start() without on_log= must not raise — every pre-existing caller/test
    omits it, so the parameter must be fully optional."""
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    monkeypatch.setattr("pipeline_store._INDEX_PATH", tmp_path / "idx.json")
    monkeypatch.setattr("pipeline_store._RUNS_DIR", tmp_path)
    mock_popen = MagicMock()
    mock_popen.return_value.pid = 12345
    mock_popen.return_value.stdout = iter(["a line\n"])
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
    assert runner._on_log is None


# ── Health check ──────────────────────────────────────────────────────────────

def test_health_check_result_passed_to_callback(monkeypatch):
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    mock_run = MagicMock()
    mock_run.return_value.returncode = 1
    monkeypatch.setattr("subprocess.run", mock_run)
    from pipeline_runner import PipelineRunner
    runner = PipelineRunner.__new__(PipelineRunner)
    result = runner.check_chip_health()
    assert result is False
