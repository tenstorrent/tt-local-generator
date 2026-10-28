"""Discovery of music servers and the saved-server list. Only fake servers on ephemeral loopback ports; no chip is touched."""
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).parent))

import music_runtime as mr  # noqa: E402
from fake_music_server import FakeMusicServer  # noqa: E402


def _no_docker():
    return []


def _port(server):
    return int(server.url.rsplit(":", 1)[1])


def _discover(saved, ports=(), **kw):
    return mr.discover_music_servers(saved, ports=tuple(ports), docker_ports=_no_docker, **kw)


class _FakeSettings:
    def __init__(self):
        self.d = {"music_servers": [], "music_last_server": ""}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = v


def test_discovers_two_fake_servers_and_builds_profiles():
    with FakeMusicServer("music") as a, FakeMusicServer("audio") as b:
        found = _discover([], ports=(_port(a), _port(b)))
        by_url = {s.url: s for s in found}
        assert set(by_url) == {a.url, b.url}
        assert by_url[a.url].profile.jobs_prefix == "/v1/music" and by_url[a.url].profile.has_lyrics
        assert by_url[b.url].profile.jobs_prefix == "/v1/audio" and by_url[b.url].profile.max_seconds == 120
        assert all(s.up and not s.saved for s in found)


def test_saved_down_server_is_listed_not_dropped():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead = s.getsockname()[1]
    s.close()
    found = _discover([{"url": f"http://127.0.0.1:{dead}", "label": "studio"}])
    assert len(found) == 1
    assert found[0].up is False and found[0].saved is True and found[0].profile is None and found[0].label == "studio"


def test_trailing_slash_and_case_normalise_to_one_entry():
    assert mr.normalize_url("HTTP://Host:8795/") == mr.normalize_url("http://host:8795") == "http://host:8795"
    assert mr.normalize_url("Host:8795/v1/x") == "http://host:8795"
    with FakeMusicServer("audio") as a:
        found = _discover([{"url": a.url + "/"}, {"url": a.url.upper().replace("HTTP", "http")}], ports=(_port(a),))
        assert len(found) == 1 and found[0].saved and found[0].up


class _Plain(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/v1/health":
            body, code = b'{"status": "ok", "model": "some-llm"}', 200
        else:
            body, code = b'{"detail": "Not Found"}', 404
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_non_music_http_server_is_ignored():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Plain)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        assert _discover([], ports=(httpd.server_address[1],)) == []
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_probe_timeout_does_not_block():
    hung = socket.socket()
    hung.bind(("127.0.0.1", 0))
    hung.listen(5)                                  # accepts connections in the kernel, never answers
    try:
        t0 = time.monotonic()
        found = _discover([], ports=(hung.getsockname()[1],))
        assert found == [] and time.monotonic() - t0 < 3
        t0 = time.monotonic()
        saved = _discover([{"url": f"http://127.0.0.1:{hung.getsockname()[1]}"}])
        assert saved[0].up is False and time.monotonic() - t0 < 3
    finally:
        hung.close()


def test_saved_token_reaches_the_probe_header():
    with FakeMusicServer("audio", require_token="s3cret") as a:
        without = _discover([{"url": a.url}])
        assert without[0].up is False
        with_tok = _discover([{"url": a.url, "token": "s3cret"}])
        assert with_tok[0].up and with_tok[0].token == "s3cret"
        assert repr(with_tok[0]).count("s3cret") == 0    # the token stays out of the repr


def test_docker_ports_are_scanned_and_deduplicated():
    with FakeMusicServer("music") as a:
        p = _port(a)
        found = mr.discover_music_servers([], ports=(p,), docker_ports=lambda: [p])
        assert [s.url for s in found] == [a.url]


def test_env_ports_extend_the_default(monkeypatch):
    monkeypatch.setenv("TTLG_MUSIC_SCAN_PORTS", "9001, 9002,bad")
    assert mr.scan_ports() == mr.DEFAULT_PORTS + (9001, 9002)


def test_save_and_remove_server(monkeypatch):
    import app_settings
    fake = _FakeSettings()
    monkeypatch.setattr(app_settings, "settings", fake)
    mr.save_server("HTTP://Box:8795/", token="tok", label="lab")
    mr.save_server("http://box:8795", label="renamed")            # same server: replaced, not duplicated; the token is kept
    assert fake.d["music_servers"] == [{"url": "http://box:8795", "token": "tok", "label": "renamed"}]
    mr.remove_server("http://BOX:8795/")
    assert fake.d["music_servers"] == []
    assert "music_servers" in __import__("app_settings").DEFAULTS and "music_last_server" in app_settings.DEFAULTS


def test_bad_saved_url_does_not_stop_discovery():
    with FakeMusicServer("audio") as a:
        saved = [{"url": "http://host:abc"}, {"url": "http://[::1"}, {"url": ""}, {"url": a.url}]
        found = _discover(saved)
        assert [s.url for s in found] == [a.url] and found[0].up


def test_normalize_url_raises_clear_error_and_save_server_reports_it(monkeypatch):
    import pytest
    import app_settings
    fake = _FakeSettings()
    monkeypatch.setattr(app_settings, "settings", fake)
    for bad in ("http://host:abc", "http://[::1", "http://"):
        with pytest.raises(ValueError, match="not a valid server address"):
            mr.normalize_url(bad)
        with pytest.raises(ValueError, match="not a valid server address"):
            mr.save_server(bad)
    assert fake.d["music_servers"] == []
    mr.remove_server("http://host:abc")                      # removing a bad entry must not raise


def test_save_server_without_token_keeps_the_saved_one(monkeypatch):
    import app_settings
    fake = _FakeSettings()
    monkeypatch.setattr(app_settings, "settings", fake)
    mr.save_server("http://box:1", token="tok", label="a")
    mr.save_server("http://box:1", label="b")
    assert fake.d["music_servers"][0]["token"] == "tok" and fake.d["music_servers"][0]["label"] == "b"
    mr.save_server("http://box:1", token="new")
    assert fake.d["music_servers"][0]["token"] == "new"
