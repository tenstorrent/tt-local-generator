# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""Tests for PipelineRunner retry_node and retry_job.

Verifies:
- retry_node launches run_single_node.sh with correct arguments
- retry_job identifies the first failed node and delegates to retry_node
- retry_node raises ValueError when no active run is set
- retry_job is a no-op when the named job has no failed nodes
- update_output_dir stores and retrieves the output_dir field correctly
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_store_with_run(tmp_path, monkeypatch):
    """Create a PipelineStore with one run that has mixed node states.

    Run layout:
        job "1964-ny": node 1 done, node 6 failed
        job "1939-ny": node 1 done, node 6 done  (no failures)

    The output_dir is set to tmp_path/output, which contains a minimal
    results.json so retry_node can locate it.
    """
    monkeypatch.setattr("pipeline_store._INDEX_PATH", tmp_path / "idx.json")
    monkeypatch.setattr("pipeline_store._RUNS_DIR", tmp_path)

    from pipeline_store import PipelineStore

    store = PipelineStore()
    run_id = store.create_run(
        "/fake/spec.json",
        "test",
        [{"name": "1964-ny"}, {"name": "1939-ny"}],
        {},
        1,
        "/tmp/fake.log",
    )

    # Create an output directory with a minimal results.json
    output_dir = str(tmp_path / "output")
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "results.json").write_text(
        json.dumps({
            "1": {"image_path": "/tmp/node1.png", "_label": "seed image"},
            "5": {"video_prompt": "test prompt", "_label": "video prompt"},
        })
    )
    store.update_output_dir(run_id, output_dir)

    # Mark nodes: 1964-ny has a failure on node 6; 1939-ny is all green
    store.update_node(run_id, "1964-ny", "1", "done", "/tmp/node1.png")
    store.update_node(run_id, "1964-ny", "6", "failed", "SkyReels OOM")
    store.update_node(run_id, "1939-ny", "1", "done", "/tmp/node1b.png")
    store.update_node(run_id, "1939-ny", "6", "done", "/tmp/video.mp4")

    return store, run_id, output_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_retry_node_launches_run_single_node_sh(monkeypatch, tmp_path):
    """retry_node should Popen run_single_node.sh with results.json and node_id."""
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    store, run_id, output_dir = make_store_with_run(tmp_path, monkeypatch)

    mock_popen = MagicMock()
    mock_popen.return_value.pid = 9999
    # Provide a minimal stdout that terminates quickly
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

    runner.retry_node("1964-ny", "6", MagicMock(), MagicMock())

    mock_popen.assert_called_once()
    cmd = mock_popen.call_args[0][0]
    assert "run_single_node.sh" in " ".join(cmd), f"Expected run_single_node.sh in {cmd}"
    assert "6" in cmd, f"Expected node_id '6' in command {cmd}"


def test_retry_job_finds_first_failed_node(monkeypatch, tmp_path):
    """retry_job should identify the first failed node and pass it to Popen."""
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    store, run_id, output_dir = make_store_with_run(tmp_path, monkeypatch)

    mock_popen = MagicMock()
    mock_popen.return_value.pid = 9999
    mock_popen.return_value.stdout = iter([])
    mock_popen.return_value.wait.return_value = 0
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    from pipeline_runner import PipelineRunner

    runner = PipelineRunner()
    runner._store = store
    runner._run_id = run_id

    runner.retry_job("1964-ny", MagicMock(), MagicMock())

    mock_popen.assert_called_once()
    cmd = mock_popen.call_args[0][0]
    # node 6 is the only failed node for 1964-ny
    assert "6" in cmd, f"Expected node_id '6' in command {cmd}"


def test_retry_node_no_run_id_raises(monkeypatch):
    """retry_node should raise ValueError when no run_id is active."""
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())

    from pipeline_runner import PipelineRunner

    runner = PipelineRunner()
    runner._run_id = None

    with pytest.raises(ValueError, match="No active run"):
        runner.retry_node("job", "6", MagicMock(), MagicMock())


def test_retry_job_no_failures_is_noop(monkeypatch, tmp_path):
    """retry_job should be a no-op when the named job has no failed nodes."""
    monkeypatch.setattr("pipeline_runner.GLib", MagicMock())
    store, run_id, output_dir = make_store_with_run(tmp_path, monkeypatch)

    mock_popen = MagicMock()
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    from pipeline_runner import PipelineRunner

    runner = PipelineRunner()
    runner._store = store
    runner._run_id = run_id

    # 1939-ny has no failed nodes — Popen must not be called
    runner.retry_job("1939-ny", MagicMock(), MagicMock())
    mock_popen.assert_not_called()


def test_update_output_dir_stored_and_retrieved(monkeypatch, tmp_path):
    """update_output_dir should persist the path and get_run should return it."""
    monkeypatch.setattr("pipeline_store._INDEX_PATH", tmp_path / "idx.json")
    monkeypatch.setattr("pipeline_store._RUNS_DIR", tmp_path)

    from pipeline_store import PipelineStore

    store = PipelineStore()
    run_id = store.create_run("/s", "s", [], {}, 1, "/tmp/x.log")

    store.update_output_dir(run_id, "/tmp/output_dir")

    run = store.get_run(run_id)
    assert run is not None
    assert run["output_dir"] == "/tmp/output_dir"
