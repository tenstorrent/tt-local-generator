"""
Tests for server_manager.py.

These tests do NOT invoke real shell scripts or make real network calls.
subprocess.run and urllib.request.urlopen are monkeypatched throughout.
"""
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import server_manager as sm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_result(stdout="", stderr=""):
    """Fake CompletedProcess with returncode 0."""
    r = subprocess.CompletedProcess(args=[], returncode=0)
    r.stdout = stdout
    r.stderr = stderr
    return r


def _liveness_response(runner_key: str | None = None) -> MagicMock:
    """Return a fake urlopen response.

    If runner_key is given, the response body is a JSON liveness payload with
    runner_in_use set to that value (simulating a port-8000 service).
    Otherwise, the body is a minimal JSON health payload (port-8001 style),
    and runner_in_use is absent.
    """
    import json as _json
    if runner_key is not None:
        body = _json.dumps({"status": "alive", "model_ready": True, "runner_in_use": runner_key}).encode()
    else:
        body = _json.dumps({"status": "ok", "model_ready": True}).encode()
    mock = MagicMock()
    mock.read.return_value = body
    return mock


def _liveness_response_for(key: str) -> MagicMock:
    """Return a liveness mock that correctly matches the given server key."""
    sdef = sm.SERVERS[key]
    return _liveness_response(runner_key=sdef.runner_key)


def _fail_result(code=1, stderr="error"):
    r = subprocess.CompletedProcess(args=[], returncode=code)
    r.stdout = ""
    r.stderr = stderr
    return r


# ---------------------------------------------------------------------------
# _resolve
# ---------------------------------------------------------------------------

class TestResolve:
    def test_single_known_key(self):
        result = sm._resolve("wan2.2")
        assert len(result) == 1
        assert result[0].key == "wan2.2"

    def test_all_expands_to_default_set(self):
        result = sm._resolve("all")
        keys = [s.key for s in result]
        # "all" must include wan2.2 and prompt-server (the everyday set)
        assert "wan2.2" in keys
        assert "prompt-server" in keys

    def test_unknown_key_raises(self):
        with pytest.raises(KeyError, match="Unknown server"):
            sm._resolve("nonexistent-server")

    def test_all_servers_have_unique_keys(self):
        keys = list(sm.SERVERS.keys())
        assert len(keys) == len(set(keys))

    def test_all_servers_have_scripts(self):
        for sdef in sm.SERVERS.values():
            assert sdef.script.endswith(".sh"), f"{sdef.key} script should be a .sh file"
            assert sdef.health_url.startswith("http"), f"{sdef.key} needs http health_url"


# ---------------------------------------------------------------------------
# start / stop / restart
# ---------------------------------------------------------------------------

class TestStart:
    def test_start_single_passes_gui_flag(self):
        with patch("subprocess.run", return_value=_ok_result()) as mock_run:
            results = sm.start("wan2.2", gui=True)
        assert len(results) == 1
        cmd = mock_run.call_args[0][0]
        assert "--gui" in cmd
        assert "start_wan_qb2.sh" in cmd[-2] or "start_wan_qb2.sh" in " ".join(cmd)

    def test_start_blocking_omits_gui_flag(self):
        with patch("subprocess.run", return_value=_ok_result()) as mock_run:
            sm.start("wan2.2", gui=False)
        cmd = mock_run.call_args[0][0]
        assert "--gui" not in cmd

    def test_start_all_starts_multiple_servers(self):
        with patch("subprocess.run", return_value=_ok_result()) as mock_run:
            results = sm.start("all", gui=True)
        # "all" must invoke at least 2 servers
        assert mock_run.call_count >= 2
        assert len(results) >= 2

    def test_start_prompt_server(self):
        with patch("subprocess.run", return_value=_ok_result()) as mock_run:
            sm.start("prompt-server", gui=True)
        cmd = mock_run.call_args[0][0]
        assert "start_prompt_gen.sh" in " ".join(cmd)

    def test_start_unknown_key_raises(self):
        with pytest.raises(KeyError):
            sm.start("nope")


class TestStop:
    def test_stop_passes_stop_flag(self):
        with patch("subprocess.run", return_value=_ok_result()) as mock_run:
            sm.stop("wan2.2")
        cmd = mock_run.call_args[0][0]
        assert "--stop" in cmd

    def test_stop_all(self):
        with patch("subprocess.run", return_value=_ok_result()) as mock_run:
            results = sm.stop("all")
        assert mock_run.call_count >= 2

    def test_stop_unknown_key_raises(self):
        with pytest.raises(KeyError):
            sm.stop("nope")


class TestRestart:
    def test_restart_calls_stop_then_start(self):
        with patch("subprocess.run", return_value=_ok_result()) as mock_run:
            sm.restart("prompt-server", gui=True)
        # First call: stop (has --stop), second call: start (has --gui)
        calls = mock_run.call_args_list
        assert len(calls) == 2
        stop_cmd = calls[0][0][0]
        start_cmd = calls[1][0][0]
        assert "--stop" in stop_cmd
        assert "--gui" in start_cmd


# ---------------------------------------------------------------------------
# health / is_healthy / status_all
# ---------------------------------------------------------------------------

class TestHealth:
    def test_healthy_server_returns_true(self):
        with patch("urllib.request.urlopen", return_value=_liveness_response_for("wan2.2")):
            result = sm.health("wan2.2")
        assert result == {"wan2.2": True}

    def test_wrong_runner_returns_false(self):
        # Port-8000 is up but loaded with mochi, not wan2.2 — should report wan2.2 as down.
        with patch("urllib.request.urlopen", return_value=_liveness_response(runner_key="tt-mochi-1")):
            result = sm.health("wan2.2")
        assert result == {"wan2.2": False}

    def test_unreachable_server_returns_false(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = sm.health("wan2.2")
        assert result == {"wan2.2": False}

    def test_health_all_returns_all_keys(self):
        # prompt-server has no runner_key; wan2.2 does.  Mock a response that satisfies wan2.2.
        with patch("urllib.request.urlopen", return_value=_liveness_response_for("wan2.2")):
            result = sm.health("all")
        # Must include at least wan2.2 and prompt-server
        assert "wan2.2" in result
        assert "prompt-server" in result

    def test_health_unknown_key_raises(self):
        with pytest.raises(KeyError):
            sm.health("nonexistent")

    def test_is_healthy_true(self):
        with patch("urllib.request.urlopen", return_value=_liveness_response_for("wan2.2")):
            assert sm.is_healthy("wan2.2") is True

    def test_is_healthy_false_when_wrong_runner(self):
        # Server is up but running a different model.
        with patch("urllib.request.urlopen", return_value=_liveness_response(runner_key="tt-mochi-1")):
            assert sm.is_healthy("wan2.2") is False

    def test_is_healthy_false_on_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            assert sm.is_healthy("wan2.2") is False

    def test_is_healthy_prompt_server(self):
        # prompt-server has no runner_key — only HTTP 2xx required.
        with patch("urllib.request.urlopen", return_value=_liveness_response(runner_key=None)):
            assert sm.is_healthy("prompt-server") is True

    def test_is_healthy_rejects_all_key(self):
        with pytest.raises(ValueError, match="does not accept 'all'"):
            sm.is_healthy("all")

    def test_status_all_returns_every_server(self):
        with patch("urllib.request.urlopen", return_value=_liveness_response_for("wan2.2")):
            result = sm.status_all()
        assert set(result.keys()) == set(sm.SERVERS.keys())


# ---------------------------------------------------------------------------
# Script path resolution
# ---------------------------------------------------------------------------

class TestScriptPaths:
    def test_scripts_live_in_bin(self):
        for sdef in sm.SERVERS.values():
            path = sm._script_path(sdef)
            assert path.parent.name == "bin", (
                f"{sdef.key}: expected script in bin/, got {path.parent}"
            )

    def test_repo_root_is_project_root(self):
        # server_manager.py is at project root; _REPO_ROOT should contain bin/
        assert (sm._REPO_ROOT / "bin").is_dir(), (
            f"_REPO_ROOT ({sm._REPO_ROOT}) should contain a bin/ directory"
        )
