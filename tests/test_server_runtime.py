"""build_server_control and snapshot with fakes (no real tt, gozer or docker)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).parent))

import gozer_client
import server_manager as sm
import server_runtime as rt
import tt_backend as tb
from fake_tt import make_fake_tt
from server_control import ServerControl
from server_view import ServerRecords

ROW = {"name": "ns/bundle", "backend": "tt-model", "container": "c", "container_id": "60e0b238c988",
       "port": 20000, "base_url": "http://127.0.0.1:20000", "status": "running",
       "health": "healthy", "uptime_s": 5, "served_id": "ns/bundle"}
CAT = {"models": [{"name": "ns/bundle", "kind": "container", "engines": ["vllm"],
                   "hardware": ["p300x2"], "installed": True, "backends": ["tt-model"]}]}


@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    tb.reset_cache()
    monkeypatch.setattr(sm, "status_all", lambda timeout=2.0: {"prompt-server": True})
    return tmp_path


def _tt(box, ps):
    make_fake_tt(box / "bin", {"commands": {
        "model ps": ps, "--offline model list": {"stdout": json.dumps(CAT)}}})
    tb.reset_cache()


def test_snapshot_merges_tt_rows_and_script_health(box):
    _tt(box, {"stdout": json.dumps({"served": [ROW]})})
    reg, servers, ok = rt.snapshot(box / "servers.json")
    assert ok and "ns/bundle" in reg
    by = {s.name: s for s in servers}
    assert by["ns/bundle"].source == "tt" and by["ns/bundle"].owner == "external"
    assert any(s.source == "script" and s.port == 8001 for s in servers)


def test_snapshot_marks_app_owned_from_records(box):
    _tt(box, {"stdout": json.dumps({"served": [ROW]})})
    ServerRecords(box / "servers.json").add("ns/bundle", 20000, "60e0b238c988", None, 1.0)
    _, servers, _ = rt.snapshot(box / "servers.json")
    assert [s.owner for s in servers if s.name == "ns/bundle"] == ["app"]


def test_snapshot_ps_failure_degrades_to_script_servers(box):
    _tt(box, {"stderr": "boom", "exit": 3})
    reg, servers, ok = rt.snapshot_detail(box / "servers.json")[:3]
    assert not ok and all(s.source == "script" for s in servers) and servers
    assert rt.snapshot_detail(box / "servers.json")[3]


def test_snapshot_without_tt(box):
    reg, servers, ok = rt.snapshot(box / "servers.json")
    assert not ok and servers


def test_build_wires_the_real_modules_and_confirm(box):
    seen = []
    ctl = rt.build_server_control(lambda k, t: seen.append(k) or True,
                                  records_path=box / "servers.json")
    assert isinstance(ctl, ServerControl)
    assert ctl._tt is tb and ctl._gozer is gozer_client
    assert ctl._records.path == box / "servers.json"
    assert isinstance(ctl._boutique, rt.RecordingBoutique)
    import pipeline_engine
    assert ctl._reset_fn is pipeline_engine._tt_smi_reset
    assert ctl._confirm("switch", "q") is True and seen == ["switch"]


def test_status_of_reads_the_merged_view(box):
    _tt(box, {"stdout": json.dumps({"served": [ROW]})})
    ctl = rt.build_server_control(lambda k, t: False, records_path=box / "servers.json")
    assert ctl._status_of("ns/bundle") == "ready"
    assert ctl._status_of("prompt-server") == "ready"
    assert ctl._status_of("flux") == "off"


def test_view_fn_refreshes_each_call(box):
    _tt(box, {"stdout": json.dumps({"served": []})})
    ctl = rt.build_server_control(lambda k, t: False, records_path=box / "servers.json")
    assert not [s for s in ctl._view_fn() if s.name == "ns/bundle"]
    _tt(box, {"stdout": json.dumps({"served": [ROW]})})
    assert [s for s in ctl._view_fn() if s.name == "ns/bundle"]


def test_recording_boutique_remembers_results_and_blocking(monkeypatch):
    calls = []
    monkeypatch.setattr(sm, "start", lambda key, gui=True, **kw: calls.append(gui) or ["r"])
    monkeypatch.setattr(sm, "stop", lambda key, **kw: ["s"])
    b = rt.RecordingBoutique(blocking=True)
    b.start("flux", gui=True); b.stop("flux")
    assert calls == [False] and b.last_start["flux"] == ["r"] and b.last_stop["flux"] == ["s"]
