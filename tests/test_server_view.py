# SPDX-License-Identifier: Apache-2.0
"""Tests for app/server_view.py: merged server view and ownership records."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import server_view as sv
import tt_backend
from server_registry import ModelEntry


def _tt(name="Wan2.2-T2V-A14B-Diffusers", port=8000, served_id=None,
        container_id="c1", base_url=None, health="healthy"):
    return tt_backend.TtServer(
        name=name, backend="model-manager", container="tt-" + name, container_id=container_id,
        image=None, port=port, base_url=base_url or f"http://127.0.0.1:{port}/v1",
        status="running", health=health, started_at=None, uptime_s=60, profile=None,
        served_id=served_id)


def _entry(key="wan2.2", port=8000, served_id=None, runner_key="tt-wan2.2",
           tt_name="Wan2.2-T2V-A14B-Diffusers"):
    return ModelEntry(key=key, name="Wan2.2", capabilities=("video",), backend="boutique",
                      tt_name=tt_name, sm_key=key, hardware_group="media", chips=4,
                      port=port, served_id=served_id, runner_key=runner_key,
                      installed=True, benefit="")


def _reg(*entries):
    return {e.key: e for e in entries}


def test_same_server_in_ps_and_health_check_is_one_row():
    rows = sv.merge([_tt()], {"wan2.2": True}, [], [], [], _reg(_entry()))
    assert len(rows) == 1
    assert rows[0].source == "tt" and rows[0].key == "wan2.2"
    assert rows[0].container_id == "c1"


def test_port_and_served_id_must_both_match_else_conflict():
    reg = _reg(_entry(served_id="model-B"))
    rows = sv.merge([_tt(served_id="model-A", name="Other", container_id="c9")],
                    {"wan2.2": True}, [], [], [], reg)
    assert len(rows) == 2
    pairs = sv.conflicts(rows, reg)
    assert len(pairs) == 1
    assert {pairs[0][0].served_id, pairs[0][1].served_id} == {"model-A", "model-B"}


def test_conflicts_empty_when_one_server():
    rows = sv.merge([_tt()], {"wan2.2": True}, [], [], [], _reg(_entry()))
    assert sv.conflicts(rows, _reg(_entry())) == []


def test_runner_key_can_stand_in_for_served_id():
    # ps row has no repo-name match for the script's entry; only the runner key
    # (as the row's served id) ties them together on the script's port.
    reg = _reg(_entry(tt_name=None, served_id=None, runner_key="tt-wan2.2"))
    rows = sv.merge([_tt(name="whatever", served_id="tt-wan2.2")],
                    {"wan2.2": True}, [], [], [], reg)
    assert len(rows) == 1


def test_remote_host_is_not_local_and_owner_unknown():
    rows = sv.merge([], {}, [("http://10.254.254.1:8002/v1", "Qwen/Qwen3-8B")], [], [], {})
    assert len(rows) == 1
    assert rows[0].host_is_local is False and rows[0].owner == "unknown"
    assert rows[0].source == "discovered" and rows[0].port == 8002


def test_record_with_live_container_is_app_owned():
    rec = [{"name": "x", "port": 8000, "container_id": "c1", "lease_id": "L", "started_at": "t"}]
    rows = sv.merge([_tt()], {}, [], [], rec, {})
    assert rows[0].host_is_local and rows[0].owner == "app"


def test_record_without_container_id_matches_on_name_and_port():
    rec = [{"name": "wan2.2", "port": 8000, "container_id": None}]
    rows = sv.merge([], {"wan2.2": True}, [], [], rec, _reg(_entry()))
    assert rows[0].owner == "app"


def test_record_with_vanished_container_makes_the_server_external():
    rec = [{"name": "x", "port": 8000, "container_id": "gone", "lease_id": "L", "started_at": "t"}]
    rows = sv.merge([_tt(container_id="c1")], {}, [], [], rec, {})
    assert rows[0].owner == "external"


def test_prune_returns_dropped_records(tmp_path):
    r = sv.ServerRecords(tmp_path / "s.json")
    r.add("a", 8000, "c1", "L1", "t")
    r.add("b", 8001, "c2", "L2", "t")
    dropped = r.prune({"c1"})
    assert [d["name"] for d in dropped] == ["b"] and dropped[0]["lease_id"] == "L2"
    assert [x["name"] for x in r.load()] == ["a"]
    r.remove("a")
    assert r.load() == []


def test_records_written_atomically_and_survive_a_corrupt_file(tmp_path, monkeypatch):
    p = tmp_path / "sub" / "s.json"
    r = sv.ServerRecords(p)
    assert r.load() == []                      # missing
    calls = []
    real = sv.os.replace
    monkeypatch.setattr(sv.os, "replace", lambda a, b: (calls.append((a, b)), real(a, b))[1])
    r.add("a", 1, "c", "L", "t")
    assert calls and calls[0][1] == p and calls[0][0] != p
    assert not [f for f in p.parent.iterdir() if f.name != "s.json"]   # no temp left
    p.write_text("{not json")
    assert r.load() == []                      # corrupt
    r.add("b", 2, "c2", "L2", "t")             # recovers
    assert [x["name"] for x in r.load()] == ["b"]


def test_default_path():
    assert str(sv.ServerRecords().path).endswith(
        ".local/share/tt-local-generator/servers.json")


def test_merge_with_real_ps_fixture():
    fx = Path(__file__).parent / "fixtures" / "tt" / "ps.json"
    row = json.loads(fx.read_text())["served"][0]
    t = tt_backend.TtServer(**{k: row.get(k) for k in tt_backend.TtServer.__dataclass_fields__})
    rows = sv.merge([t], {}, [], [], [], {})
    assert len(rows) == 1
    assert rows[0].owner == "external" and rows[0].port == 8011
    assert rows[0].served_id == "Qwen/Qwen3-Coder-Next"
