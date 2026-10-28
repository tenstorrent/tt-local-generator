"""tt_backend against a fake `tt` script. No test runs the real tt, docker or gozer."""
import json
import logging
import os
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
    assert not r.ok and r.reason.startswith("cancelled") and seen == ["start"]


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


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie still answers kill(0); treat it as dead
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


def test_serve_sends_expected_argv(tmp_path):
    d = _install(tmp_path, {"serve m --port 8011 --device p300x2": {"lines": ["ok"]}})
    assert tb.serve("m", port=8011, device="p300x2").ok
    assert ["serve", "m", "--port", "8011", "--device", "p300x2"] in calls(d)


def test_serve_passes_detach_only_for_a_bundle(tmp_path):
    d = _install(tmp_path, {
        "serve raahemnabeel/qwen3-coder-next --dry-run --offline":
            {"stdout": _fx("serve_dry_run.json")},
        "serve Qwen3.8-27B --dry-run --offline":
            {"stdout": _fx("serve_dry_run_catalog.json")},
        "serve": {"lines": ["ok"]}})
    assert tb.serve("raahemnabeel/qwen3-coder-next", detach=True).ok
    assert tb.serve("Qwen3.8-27B", detach=True).ok
    assert tb.serve("unknown/x", detach=True).ok  # plan fails -> flag left off
    real = [c for c in calls(d) if "--dry-run" not in c and "--version" not in c]
    by = {c[1]: c for c in real}
    assert "--detach" in by["raahemnabeel/qwen3-coder-next"]
    assert "--detach" not in by["Qwen3.8-27B"]
    assert "--detach" not in by["unknown/x"]


def test_serve_cancel_kills_the_whole_process_group(tmp_path):
    pidfile = tmp_path / "child.pid"
    _install(tmp_path, {"serve m": {"lines": ["start"], "child_pidfile": str(pidfile),
                                    "hang": True}})
    flag = threading.Event()
    r = tb.serve("m", on_line=lambda ln: flag.set(), cancel=flag.is_set, timeout=60)
    assert r.reason.startswith("cancelled") and "tt model ps" in r.reason
    child = int(pidfile.read_text())
    time.sleep(0.3)
    assert not _pid_alive(child)


def test_serve_cancel_works_with_chatty_output(tmp_path):
    _install(tmp_path, {"serve m": {"chatty": True}})
    flag = threading.Event()
    threading.Timer(0.5, flag.set).start()
    t0 = time.monotonic()
    r = tb.serve("m", cancel=flag.is_set, timeout=60)
    assert time.monotonic() - t0 < 6
    assert r.reason.startswith("cancelled")


def test_serve_timeout_applies_with_chatty_output(tmp_path):
    _install(tmp_path, {"serve m": {"chatty": True}})
    t0 = time.monotonic()
    r = tb.serve("m", timeout=0.5)
    assert time.monotonic() - t0 < 6 and r.reason.startswith("timed out")


def test_find_tt_does_not_cache_a_timeout(tmp_path, monkeypatch):
    _install(tmp_path, {})
    calls_made = []

    def slow_run(argv, timeout=0):
        calls_made.append(argv)
        return None, "", "timed out"

    assert tb.find_tt(run=slow_run) is None
    assert tb.find_tt() == tmp_path / "bin" / "tt"  # not stuck on the cached None


def test_find_tt_caches_a_definite_no(tmp_path):
    _install(tmp_path, {}, version="something else")
    assert tb.find_tt() is None
    make_fake_tt(tmp_path / "bin", {"version": "tt 1.2.0"})
    assert tb.find_tt() is None  # definite answer stays cached until reset_cache


def test_stop_rejects_names_that_look_like_options(tmp_path):
    d = _install(tmp_path, {"model stop": {"stdout": "x"}})
    r = tb.stop(["a/b", "--profile"])
    assert not r.ok and "'-'" in r.reason
    assert not [c for c in calls(d) if "stop" in c]
