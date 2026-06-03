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
