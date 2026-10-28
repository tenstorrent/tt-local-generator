"""tt_backend against a fake `tt` script. No test runs the real tt, docker or gozer."""
import json
import logging
import re
import threading
import time
from pathlib import Path

import pytest

import tt_backend as tb
from fake_tt import calls, make_fake_tt

FIX = Path(__file__).parent / "fixtures" / "tt"


def _fx(name):
    return (FIX / name).read_text()


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    tb.reset_cache()
    # PATH holds only the fake dir (plus nothing else): the real tt is unreachable.
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    yield
    tb.reset_cache()


def _install(tmp_path, commands, version="tt 1.2.0"):
    d = tmp_path / "bin"
    make_fake_tt(d, {"version": version, "commands": commands})
    return d


def test_find_tt_accepts_the_real_cli(tmp_path):
    d = _install(tmp_path, {})
    assert tb.find_tt() == d / "tt"


def test_find_tt_rejects_a_binary_that_is_not_tt(tmp_path):
    _install(tmp_path, {}, version="tt-lang toolchain")
    assert tb.find_tt() is None


def test_find_tt_skips_shadowing_binary_and_warns(tmp_path, monkeypatch, caplog):
    other = tmp_path / "other"
    make_fake_tt(other, {"version": "not the cli"})
    real = tmp_path / "bin"
    make_fake_tt(real, {"version": "tt 1.2.0"})
    monkeypatch.setenv("PATH", f"{other}:{real}")
    with caplog.at_level(logging.WARNING):
        assert tb.find_tt() == real / "tt"
    assert any("shadow" in r.message for r in caplog.records)


def test_ps_parses_fixture_and_ignores_unknown_fields(tmp_path):
    data = json.loads(_fx("ps.json"))
    data["served"][0]["brand_new_field"] = {"x": 1}
    del data["served"][0]["profile"]
    _install(tmp_path, {"model ps": {"stdout": json.dumps(data)}})
    servers, reason = tb.ps()
    assert reason == ""
    assert len(servers) == 1
    s = servers[0]
    assert s.name == "raahemnabeel/qwen3-coder-next"
    assert s.port == 8011 and s.health == "healthy" and s.uptime_s == 916
    assert s.profile is None
    assert s.served_id == "Qwen/Qwen3-Coder-Next"


def test_ps_missing_tt_returns_empty_with_reason():
    servers, reason = tb.ps()
    assert servers == [] and "not found" in reason


def test_ps_hung_tt_times_out_without_raising(tmp_path):
    _install(tmp_path, {"model ps": {"stdout": "{}", "delay": 30}})
    t0 = time.monotonic()
    servers, reason = tb.ps(timeout=0.5)
    assert time.monotonic() - t0 < 10
    assert servers == [] and "timed out" in reason


def test_ps_old_tt_without_served_key_degrades(tmp_path):
    _install(tmp_path, {"model ps": {"stdout": json.dumps({"running": []})}})
    servers, reason = tb.ps()
    assert servers == [] and "served" in reason


def test_catalog_caches_within_ttl_and_passes_offline(tmp_path):
    d = _install(tmp_path, {"--offline model list": {"stdout": _fx("list.json")}})
    first = tb.catalog()
    again = tb.catalog()
    assert first == again and len(first) > 10
    lists = [c for c in calls(d) if "list" in c]
    assert len(lists) == 1 and "--offline" in lists[0]
    # Row shapes: bundle has installed; catalog row has none -> False.
    by = {m.name: m for m in first}
    assert by["raahemnabeel/qwen3-coder-next"].installed is True
    assert by["Qwen3.8-27B"].installed is False and by["Qwen3.8-27B"].kind is None
    assert "inference-server" in by["Qwen3.8-27B"].backends


def test_plan_serve_reads_dry_run(tmp_path):
    d = _install(tmp_path, {
        "serve raahemnabeel/qwen3-coder-next --dry-run --offline":
            {"stdout": _fx("serve_dry_run.json")},
        "serve Qwen3.8-27B --dry-run --offline":
            {"stdout": _fx("serve_dry_run_catalog.json")}})
    b = tb.plan_serve("raahemnabeel/qwen3-coder-next")
    assert b.backend == "tt-model" and b.chips == 4 and b.device == "p300x2"
    assert b.command[-1] == "--local-only" and "serve" in b.command
    c = tb.plan_serve("Qwen3.8-27B")
    assert c.backend == "inference-server" and c.chips is None and c.device == "p300x2"
    assert c.port == 20000 and "--model" in c.command
    for call in calls(d):
        if "serve" in call:
            assert "--dry-run" in call and "--offline" in call
    assert tb.plan_serve("nonexistent/thing") is None


def test_serve_streams_lines_to_on_line_and_returns_ok(tmp_path):
    _install(tmp_path, {"serve m": {"lines": ["pulling image", "warmup", "ready"]}})
    got = []
    r = tb.serve("m", on_line=got.append)
    assert r.ok and got == ["pulling image", "warmup", "ready"]
    assert r.lines == tuple(got)


def test_serve_nonzero_exit_returns_last_lines_in_reason(tmp_path):
    _install(tmp_path, {"serve m": {"lines": ["a", "b", "boom: no chips"], "exit": 3}})
    r = tb.serve("m")
    assert not r.ok and "exited 3" in r.reason and "boom: no chips" in r.reason


def test_serve_cancel_terminates_the_process(tmp_path):
    _install(tmp_path, {"serve m": {"lines": ["start"], "hang": True}})
    flag = threading.Event()
    seen = []

    def on_line(ln):
        seen.append(ln)
        flag.set()

    t0 = time.monotonic()
    r = tb.serve("m", on_line=on_line, cancel=flag.is_set, timeout=60)
    assert time.monotonic() - t0 < 15
    assert not r.ok and r.reason == "cancelled" and seen == ["start"]


def test_stop_passes_names_as_separate_args(tmp_path):
    d = _install(tmp_path, {"model stop": {"stdout": "stopped"}})
    weird = "my model; rm -rf /"
    r = tb.stop(["a/b", weird])
    assert r.ok
    stop_calls = [c for c in calls(d) if "stop" in c]
    assert stop_calls[0][-2:] == ["a/b", weird]
    assert tb.stop([]).ok is False


def test_logs_returns_text(tmp_path):
    d = _install(tmp_path, {"model logs": {"stdout": "line1\nline2\n"}})
    assert tb.logs("a/b", 5) == "line1\nline2\n"
    assert ["model", "logs", "a/b", "--tail", "5"] in calls(d)


def test_no_shell_true_anywhere():
    src = (Path(__file__).parent.parent / "app" / "tt_backend.py").read_text()
    assert not re.search(r"shell\s*=\s*True", src)
