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
