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
    # A discovered /v1/models id equal to the script's runner key ties the two
    # together on the script's port; no repo name or served id is involved.
    reg = _reg(_entry(tt_name=None, served_id=None, runner_key="tt-wan2.2"))
    rows = sv.merge([], {"wan2.2": True}, [("http://127.0.0.1:8000/v1", "tt-wan2.2")],
                    [], [], reg)
    assert len(rows) == 1 and rows[0].key == "wan2.2"


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
    assert not [f for f in p.parent.iterdir() if f.name not in ("s.json", "s.json.lock")]   # no temp left
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


# ---- fix round 1 ----

def test_same_port_different_host_is_two_rows():
    rows = sv.merge([], {}, [("http://127.0.0.1:8002/v1", "Qwen/Qwen3-8B"),
                             ("http://10.254.254.1:8002/v1", "Qwen/Qwen3-8B")], [], [], {})
    assert len(rows) == 2
    assert sorted(r.host_is_local for r in rows) == [False, True]


def test_loopback_aliases_are_one_host():
    rows = sv.merge([], {}, [("http://127.0.0.1:8002/v1", "m"), ("http://localhost:8002/v1", "m")],
                    [], [], {})
    assert len(rows) == 1


def test_conflicts_ignores_other_hosts():
    reg = _reg(_entry(served_id="model-B"))
    rows = sv.merge([], {"wan2.2": True}, [], [{"name": "r", "url": "http://10.254.254.1:8000/v1",
                                                 "served_id": "other"}], [], reg)
    assert len(rows) == 2
    assert sv.conflicts(rows, reg) == []


def test_shared_port_different_served_id_never_merges_despite_shared_name():
    # Same tt_name/name on one host and port, different served ids: two rows and one conflict.
    reg = _reg(_entry(served_id="model-B"))
    rows = sv.merge([_tt(served_id="model-A")], {"wan2.2": True}, [], [], [], reg)
    assert len(rows) == 2
    assert len(sv.conflicts(rows, reg)) == 1


def test_conflicts_reports_unknown_served_id():
    reg = _reg(_entry(served_id=None, tt_name=None, runner_key=None))
    rows = sv.merge([_tt(name="Other", container_id="c9")], {"wan2.2": True}, [], [], [], reg)
    assert len(rows) == 2
    assert len(sv.conflicts(rows, reg)) == 1


def test_no_container_record_does_not_claim_external_tt_row():
    rec = [{"name": "wan2.2", "port": 8000, "container_id": None, "lease_id": "L"}]
    rows = sv.merge([_tt(container_id="c1")], {}, [], [], rec, _reg(_entry()))
    assert len(rows) == 1 and rows[0].owner == "external"


def test_prune_drops_no_container_record_when_endpoint_not_live(tmp_path):
    r = sv.ServerRecords(tmp_path / "s.json")
    r.add("wan2.2", 8000, None, "L1", "t")
    r.add("flux", 8000, None, "L2", "t")
    assert r.prune(set()) == []          # live_endpoints None: kept
    dropped = r.prune(set(), {("flux", 8000)})
    assert [d["name"] for d in dropped] == ["wan2.2"]
    assert [x["name"] for x in r.load()] == ["flux"]


def test_prune_with_unknown_ps_prunes_nothing(tmp_path):
    r = sv.ServerRecords(tmp_path / "s.json")
    r.add("a", 8000, "c1", "L1", "t")
    assert r.prune(None, {("a", 8000)}) == []
    assert len(r.load()) == 1
    assert [d["name"] for d in r.prune(set())] == ["a"]   # empty set = ps worked


def test_two_instances_adding_keep_both(tmp_path):
    p = tmp_path / "s.json"
    sv.ServerRecords(p).add("a", 1, "c1", "L", "t")
    sv.ServerRecords(p).add("b", 2, "c2", "L", "t")
    assert sorted(x["name"] for x in sv.ServerRecords(p).load()) == ["a", "b"]


def test_tt_row_without_base_url_is_local_and_external():
    t = _tt(base_url="")
    rows = sv.merge([t], {}, [], [], [], {})
    assert rows[0].host_is_local and rows[0].owner == "external"


def test_saved_entry_uses_label_and_never_copies_token():
    rows = sv.merge([], {}, [], [{"label": "Lab box", "url": "http://10.254.254.9:8000/v1",
                                  "token": "SECRET"}], [], {})
    assert rows[0].name == "Lab box"
    assert "SECRET" not in repr(rows[0])
