"""`tt-ctl servers|start|stop|restart|logs` through ServerControl.

tt-ctl is loaded in-process (it has no .py suffix) with a fake `tt` (and, where
a test needs one, a fake `gozer`) on a PATH that holds nothing else, a temp
ownership-records file, and fakes for the boutique start/stop scripts and the
script health probe. Nothing here runs the real tt, gozer, docker or a script.
"""
import argparse
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(Path(__file__).parent))

import server_manager as sm
import server_view
import tt_backend as tb
from fake_tt import calls as tt_calls, make_fake_tt

NOTE = ("note: server commands in tt-ctl are deprecated; "
        "use `tt serve` / `tt model stop` or the Servers menu.")


def _load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cli():
    return _load(ROOT / "tt-ctl", "ttctl_under_test")


@pytest.fixture(scope="module")
def main_cli(tmp_path_factory):
    """tt-ctl exactly as it is on `main`, for byte comparisons."""
    d = tmp_path_factory.mktemp("ttctl_main")
    src = subprocess.run(["git", "show", "main:tt-ctl"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    (d / "tt-ctl").write_text(src)
    (d / "app").symlink_to(ROOT / "app")
    (d / "VERSION").write_text((ROOT / "VERSION").read_text())
    return _load(d / "tt-ctl", "ttctl_main")


PS_ROW = {"name": "ns/bundle", "backend": "tt-model", "container": "c-bundle",
          "container_id": "60e0b238c988", "port": 20000,
          "base_url": "http://127.0.0.1:20000", "status": "running",
          "health": "healthy", "uptime_s": 125, "served_id": "ns/bundle"}
CATALOG = {"models": [{"name": "ns/bundle", "kind": "container", "engines": ["vllm"],
                       "hardware": ["p300x2"], "installed": True, "backends": ["tt-model"]}]}


class Env:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.alive: dict[str, bool] = {}
        self.start_rc = 0
        self.script_calls: list[tuple] = []
        monkeypatch.setenv("PATH", str(self.bin))
        monkeypatch.delenv("TTLG_NO_DEPRECATION_NOTE", raising=False)
        monkeypatch.setattr(server_view, "_DEFAULT_RECORDS", tmp_path / "servers.json")
        tb.reset_cache()
        monkeypatch.setattr(sm, "status_all", lambda timeout=2.0: {
            k: self.alive.get(k, False) for k in sm.SERVERS})

        def fake_start(key, gui=True, timeout=None):
            self.script_calls.append(("start", key, gui))
            out = []
            for sdef in sm._resolve(key):
                if self.start_rc == 0:
                    self.alive[sdef.key] = True
                out.append(subprocess.CompletedProcess(
                    [], self.start_rc, stdout="launched ok\n",
                    stderr="boom\n" if self.start_rc else ""))
            return out

        def fake_stop(key, timeout=30):
            self.script_calls.append(("stop", key))
            out = []
            for sdef in sm._resolve(key):
                self.alive[sdef.key] = False
                out.append(subprocess.CompletedProcess([], 0, stdout="", stderr=""))
            return out

        monkeypatch.setattr(sm, "start", fake_start)
        monkeypatch.setattr(sm, "stop", fake_stop)
        self.set_tt({})

    def set_tt(self, extra, ps=None, catalog=CATALOG):
        cmds = {"model ps": {"stdout": json.dumps({"served": ps or []})},
                "--offline model list": {"stdout": json.dumps(catalog)},
                "model stop": {"stdout": "stopped\n"}}
        cmds.update(extra)
        make_fake_tt(self.bin, {"commands": cmds})
        tb.reset_cache()

    def tt_argv(self):
        return tt_calls(self.bin)


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


@pytest.fixture
def run(cli, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)

    def _run(*argv, module=None, tty=False, answers=()):
        mod = module or cli
        monkeypatch.setattr(sys, "argv", ["tt-ctl", *argv])
        if module is None:
            monkeypatch.setattr(cli, "_stdin_is_tty", lambda: tty)
        it = iter(answers)
        monkeypatch.setattr("builtins.input", lambda prompt="": next(it))
        code = 0
        try:
            mod.main()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        out = capsys.readouterr()
        return code, out.out, out.err
    return _run


# ---------------------------------------------------------------- servers

def test_servers_text_output_is_byte_identical_to_today(env, run, main_cli):
    env.alive = {"prompt-server": True, "wan2.2": True}
    code, out, _ = run("servers")
    mcode, mout, _ = run("servers", module=main_cli)
    assert code == mcode == 0
    assert out == mout and "Managed services" in out


def test_servers_all_lists_a_terminal_started_model(env, run):
    env.set_tt({}, ps=[PS_ROW])
    code, out, _ = run("servers", "--all")
    assert code == 0
    assert "Managed services" in out
    tail = out.split("All running servers")[1]
    assert "ns/bundle" in tail and "20000" in tail and "external" in tail
    assert "http://127.0.0.1:20000" in tail and "2m05s" in tail


def test_servers_all_tags_remote_and_lists_held_leases(env, run):
    env.set_tt({}, ps=[])
    rec = server_view.ServerRecords()
    rec.add("flux#unreapable-1", None, None, "L-77", 1.0, unreapable=True)
    code, out, _ = run("servers", "--all")
    assert "Held leases" in out and "flux#unreapable-1" in out and "L-77" in out
    assert "(none running)" in out


def test_servers_json_keeps_services_and_adds_merged_only_with_all(env, run):
    env.set_tt({}, ps=[PS_ROW])
    _, out, _ = run("servers", "--json")
    d = json.loads(out)
    assert set(d) == {"services"}
    assert d["services"][0].keys() >= {"key", "label", "running", "state"}
    _, out, _ = run("servers", "--json", "--all")
    d = json.loads(out)
    assert set(d) >= {"services", "merged"}
    assert any(m["name"] == "ns/bundle" for m in d["merged"])


def test_servers_all_degrades_without_tt(env, run):
    env.alive = {"prompt-server": True}
    (env.bin / "tt").unlink()
    code, out, _ = run("servers", "--all")
    assert code == 0
    assert "tt data unavailable" in out and "official tt CLI was not found" in out
    assert "8001" in out.split("All running servers")[1]


# ----------------------------------------------------- start and the note

def test_start_old_key_still_works_and_note_goes_to_stderr_only(env, run):
    code, out, err = run("start", "prompt-server")
    assert code == 0
    assert "Starting prompt-server" in out and "✓  prompt-server started" in out
    assert "launched ok" in out
    assert "deprecated" not in out and NOTE in err
    assert ("start", "prompt-server", True) in env.script_calls


def test_blocking_maps_to_the_scripts_blocking_mode(env, run):
    run("start", "prompt-server", "--blocking")
    assert ("start", "prompt-server", False) in env.script_calls


def test_note_silenced_by_env(env, run, monkeypatch):
    monkeypatch.setenv("TTLG_NO_DEPRECATION_NOTE", "1")
    _, out, err = run("start", "prompt-server")
    assert "deprecated" not in out + err


def test_note_not_printed_by_servers(env, run):
    _, out, err = run("servers")
    assert "deprecated" not in out + err


def test_logs_gains_only_the_note(env, run, main_cli, cli, monkeypatch):
    # no container and no log file: both versions print the same stdout
    monkeypatch.setattr(cli, "_running_container_id", lambda: None)
    monkeypatch.setattr(main_cli, "_running_container_id", lambda: None)
    code, out, err = run("logs")
    mcode, mout, merr = run("logs", module=main_cli)
    assert (code, out) == (mcode, mout)
    assert merr == "" and err == NOTE + "\n"


def test_exit_codes_unchanged_for_failed_start(env, run):
    env.start_rc = 3
    code, out, _ = run("start", "prompt-server")
    assert code == 1
    assert "✗  prompt-server failed (exit 3)" in out and "boom" in out


# -------------------------------------------------------- stop / prompts

def test_stop_external_prompts_and_no_leaves_it_running(env, run):
    env.set_tt({}, ps=[PS_ROW])
    code, out, _ = run("stop", "ns/bundle", tty=True, answers=["n"])
    assert code == 1
    assert "was left running" in out
    assert not any(c[:2] == ["model", "stop"] for c in env.tt_argv())


def test_stop_yes_at_the_prompt_stops_it(env, run):
    env.set_tt({}, ps=[PS_ROW])
    code, out, _ = run("stop", "ns/bundle", tty=True, answers=["YES"])
    assert code == 0 and "✓  ns/bundle stopped" in out
    assert ["model", "stop", "ns/bundle"] in env.tt_argv()


def test_stop_external_needs_the_explicit_flag_even_with_yes(env, run):
    env.set_tt({}, ps=[PS_ROW])
    code, out, _ = run("stop", "ns/bundle", "--yes")
    assert code == 1
    assert "--yes-stop-external" in out
    assert not any(c[:2] == ["model", "stop"] for c in env.tt_argv())


def test_stop_yes_stop_external_flag_stops_it(env, run):
    env.set_tt({}, ps=[PS_ROW])
    code, out, _ = run("stop", "ns/bundle", "--yes-stop-external")
    assert code == 0
    assert ["model", "stop", "ns/bundle"] in env.tt_argv()


def test_no_tty_declines_every_prompt_and_says_which_flag_would_allow_it(env, run):
    env.set_tt({}, ps=[PS_ROW])
    code, out, _ = run("stop", "ns/bundle")
    assert code == 1
    assert "Declined (no terminal)" in out and "Re-run with --yes-stop-external" in out
    # a switch question names --yes
    env.alive = {"wan2.2": True}
    code, out, _ = run("start", "flux")
    assert "Re-run with --yes to allow it" in out and code == 1
    assert ("start", "flux", True) not in env.script_calls


def test_confirm_returns_only_literal_true(cli, monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: True)
    for typed, want in (("y", True), ("Yes", True), ("yes ", True), ("", False),
                        ("yep", False), ("no", False), ("1", False)):
        monkeypatch.setattr("builtins.input", lambda p="", t=typed: t)
        got = cli._make_confirm()("switch", "q")
        assert got is want, typed
    monkeypatch.setattr("builtins.input", lambda p="": (_ for _ in ()).throw(EOFError()))
    assert cli._make_confirm()("switch", "q") is False
    assert cli._make_confirm(yes=True)("switch", "q") is True
    monkeypatch.setattr(cli, "_stdin_is_tty", lambda: False)
    assert cli._make_confirm(yes=True)("stop_external", "q") is False
    assert cli._make_confirm(yes_stop_external=True)("stop_external", "q") is True


# -------------------------------------------------------- names and keys

def test_unknown_key_exits_two_with_the_argparse_message(env, run, main_cli):
    code, out, err = run("start", "no-such-thing")
    mcode, mout, merr = run("start", "no-such-thing", module=main_cli)
    assert code == mcode == 2
    # the usage line gains the new flags; the error line is argparse's, unchanged
    assert err.splitlines()[-1] == merr.splitlines()[-1]
    assert err.startswith("usage: tt-ctl start")
    assert "invalid choice: 'no-such-thing'" in err and out == mout == ""
    assert NOTE not in err  # argparse failed before any work


def test_tt_model_name_accepted_as_a_key(env, run):
    env.set_tt({"model stop": {"stdout": "ok"}}, ps=[PS_ROW])
    code, out, _ = run("stop", "ns/bundle", "--yes-stop-external")
    assert code == 0
    code, out, _ = run("start", "ns/bundle")
    assert "already running" in out and code == 0
    assert "http://127.0.0.1:20000" in out


def test_restart_stops_then_starts_with_old_lines(env, run):
    env.alive = {"prompt-server": True}
    env.script_calls.clear()
    code, out, _ = run("restart", "prompt-server", "--yes-stop-external")
    assert code == 0
    assert "Restarting prompt-server" in out and "stopped — starting…" in out
    assert [c[0] for c in env.script_calls] == ["stop", "start"]
