"""Create surface wiring for the Music medium (Task 5): collect(), dispatch, progress, terminal paths, and what every
site that switches on Medium.kind / Medium.source does for it. Servers are tests/fake_music_server.py on loopback."""
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).parent))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)

import create_param_panels as cpp
import create_view
import music_runtime as mr
from create_mediums import Medium
from create_view import CreateResultPanel
from fake_music_server import FakeMusicServer

MUSIC = Medium(id="music", label="Music", icon="🎵", kind="audio", source="music", generator=None, uses_llm=False)


def _mediums():
    return [
        Medium(id="image", label="Image", icon="🖼", kind="image", source="native", generator=None),
        Medium(id="video", label="Video", icon="🎥", kind="video", source="native", generator=None),
        Medium(id="verse", label="Verse", icon="✍", kind="text", source="artgen", generator="verse"),
        MUSIC,
    ]


@pytest.fixture(autouse=True)
def _no_real_settings(monkeypatch):
    monkeypatch.setattr(cpp, "_remember_music_server", lambda url: None)
    monkeypatch.setattr(cpp, "_last_music_server", lambda: "")
    monkeypatch.setattr(cpp, "INSTRUMENTAL_SWITCH_CONFIRMED", True)


class _ImmediateThread:
    def __init__(self, target=None, daemon=None):
        self._t = target

    def start(self):
        self._t()


def _view(monkeypatch, **kw):
    monkeypatch.setattr(create_view.threading, "Thread", _ImmediateThread)     # the health refresh runs inline
    monkeypatch.setattr(create_view.GLib, "idle_add", lambda fn, *a: fn(*a))
    kw.setdefault("mediums_fn", _mediums)
    kw.setdefault("health_fn", lambda: {})
    return create_view.CreateView(**kw)


def _server(fake_health=None, url="http://127.0.0.1:1", model="stable-audio"):
    return mr.MusicServer(url=url, profile=mr.profile_for(fake_health or {"model": model}), up=True, saved=False,
                          label="Stable Audio")


def _pick_music(view, servers=None):
    view._chip_buttons["music"].set_active(True)
    view.set_music_servers(servers if servers is not None else [_server()])
    return view._active_panel._panel


def _collect(view, medium_id):
    view._chip_buttons[medium_id].set_active(True)
    return view._collect_params()


# ── CreateView ──────────────────────────────────────────────────────────────

def test_create_view_music_collect_equals_panel_collect(monkeypatch):
    view = _view(monkeypatch)
    panel = _pick_music(view)
    view._prompt_entry.set_text("warm synth pads")
    params = view._collect_params()
    own = panel.collect()
    assert params == {**own, "prompt": "warm synth pads"}
    assert params["server_url"] == "http://127.0.0.1:1" and params["seconds"] > 0
    assert "model" not in params


def test_prompt_getter_is_wired_to_the_top_prompt_entry(monkeypatch):
    view = _view(monkeypatch)
    panel = _pick_music(view)
    view._prompt_entry.set_text("lofi drums")
    assert panel.validate(prompt=None) is None          # server up, prompt read from the top entry, no lyrics needed
    view._prompt_entry.set_text("")
    assert "Describe the music" in panel.validate(prompt=None)


def test_other_mediums_collect_unchanged(monkeypatch):
    view = _view(monkeypatch)
    before = {m: _collect(view, m) for m in ("image", "video", "verse")}
    _pick_music(view)
    after = {m: _collect(view, m) for m in ("image", "video", "verse")}
    assert before == after
    assert "server_url" not in after["image"]


def test_music_sites_have_defined_behaviour(monkeypatch):
    """Ruling 1: every site that switches on kind/source does something sane for Music."""
    import activity_viz
    import possibilities
    from create_mediums import sort_mediums_visual_first
    view = _view(monkeypatch)
    _pick_music(view)
    # model dropdown: no native models, and its row is hidden (the panel has its own server dropdown)
    assert view._scoped_model_keys(MUSIC) == []
    assert view._model_dropdown_row.get_visible() is False
    view._chip_buttons["image"].set_active(True)
    assert view._model_dropdown_row.get_visible() is True
    # the Model door lists server_manager keys only: no music entry
    assert all(not k.startswith("music") for keys in view._model_door_groups().values() for k in keys)
    # Watch viz, Inspire prompt type, sort order
    assert activity_viz.mode_for_medium(MUSIC) == "inference"
    view._chip_buttons["music"].set_active(True)
    assert view._inspire_prompt_type() == "music"
    assert sort_mediums_visual_first(_mediums())[-1].id == "music"
    # the Start-something wall builds a tile for it (gradient + icon) without raising
    assert possibilities.example_idea_for(MUSIC)
    assert view._possibilities is not None


def test_audio_record_renders_a_player_not_file_not_found(monkeypatch, tmp_path):
    f = tmp_path / "song.flac"
    f.write_bytes(b"fLaC-fake")
    rec = MagicMock()
    rec.media_file_path = str(f)
    rec.generator_type = "music"
    rec.prompt = "slow pads"
    rec.params_dict = {"lyrics": "la la", "credit": "MiniMax-Music3", "seconds_got": 5.2, "seconds_asked": 30, "ended": True}
    assert create_view._artifact_kind(str(f), "music") == "audio"
    panel = CreateResultPanel()
    widget = panel._build_artifact_widget(rec)

    def labels(w):
        out, c = [], w.get_first_child()
        while c is not None:
            if isinstance(c, Gtk.Label):
                out.append(c.get_label())
            out += labels(c)
            c = c.get_next_sibling()
        return out
    text = " | ".join(labels(widget))
    assert "not found" not in text and "la la" in text and "Made with MiniMax-Music3" in text
    assert "ended before" in text
    panel._clear_current()          # tears the stream down without raising


def test_inspire_for_music_runs_off_the_gtk_thread(monkeypatch):
    """Ruling 3: the Inspire / lyrics buttons go through MainWindow._create_inspire_fn, which must not block."""
    import main_window as mw
    started = []

    class _T:
        def __init__(self, target=None, daemon=None):
            started.append(target)

        def start(self):
            pass

    monkeypatch.setattr(mw.threading, "Thread", _T)
    called = []
    monkeypatch.setattr(mw.prompt_client, "generate_prompt", lambda *a, **k: called.append(a) or "x")
    obj = mw.MainWindow.__new__(mw.MainWindow)
    obj._prompt_gen_system_prompt = ""
    obj._create_inspire_fn = mw.MainWindow._create_inspire_fn.__get__(obj)
    obj._create_inspire_fn("lyrics", "a seed", lambda t: None, lambda e: None)
    assert called == [] and len(started) == 1       # nothing ran on the calling thread; a thread was only created


# ── MainWindow ──────────────────────────────────────────────────────────────

_RealThread = threading.Thread


def _only_window_threads_inline(target=None, daemon=None, **kw):
    """threading is one module for the whole process: run MainWindow's own job thread inline, and leave every other
    thread (the fake server's request threads) real."""
    if "MainWindow" in getattr(target, "__qualname__", ""):
        class _Inline:
            def start(self_inner):
                target()
        return _Inline()
    return _RealThread(target=target, daemon=daemon, **kw)


def _mw(monkeypatch, tmp_path, servers):
    import main_window as mw
    import media_store
    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)
    monkeypatch.setattr(mw.threading, "Thread", _only_window_threads_inline)
    monkeypatch.setattr(mw.GLib, "idle_add", lambda fn, *a: fn(*a))
    monkeypatch.setattr(mw, "_MUSIC_POLL_S", 0.01)
    monkeypatch.setattr(media_store, "STORAGE_DIR", tmp_path / "store")
    saved = {}
    monkeypatch.setattr(mw._settings, "set", lambda k, v: saved.__setitem__(k, v))
    obj._saved_settings = saved
    obj._set_status = MagicMock()
    obj._clear_local_busy = MagicMock()
    obj._create_job_active = False
    obj._music_servers = servers
    obj._alive = True
    monkeypatch.setattr(view_mod := create_view, "_apply_css", lambda: None, raising=False)
    obj._create_view = MagicMock()
    obj._create_view._result_panel = CreateResultPanel()
    for name in ("_on_create_generate", "_launch_create_job", "_begin_create_job", "_fail_create_job",
                 "_create_generate_music", "_on_create_music_progress", "_on_create_music_finished",
                 "_on_create_music_error"):
        setattr(obj, name, getattr(mw.MainWindow, name).__get__(obj))
    return obj


def _params(url, **kw):
    base = {"prompt": "slow synth pads", "seconds": 10.0, "server_url": url}
    base.update(kw)
    return base


def test_launch_create_job_music_bypasses_server_gate(monkeypatch, tmp_path):
    obj = _mw(monkeypatch, tmp_path, [])
    obj._ensure_server_ready_then = MagicMock(side_effect=AssertionError("the server gate must not run for Music"))
    obj._create_generate_music = MagicMock()
    obj._on_create_generate(MUSIC, _params("http://x"))
    obj._ensure_server_ready_then.assert_not_called()
    obj._create_generate_music.assert_called_once()
    assert obj._create_job_active is True


def test_progress_line_reaches_result_panel(monkeypatch, tmp_path):
    """Pipe lesson: worker -> GLib.idle_add wrapper -> CreateResultPanel.show_progress, the real path end to end."""
    with FakeMusicServer(steps_before_done=3) as fake:
        server = mr.MusicServer(url=fake.url, profile=mr.profile_for(fake.health_body), up=True, saved=False)
        obj = _mw(monkeypatch, tmp_path, [server])
        panel = obj._create_view._result_panel
        seen = []
        real = panel.show_progress
        panel.show_progress = lambda m: (real(m), seen.append((m, panel._pending_last_status)))
        obj._on_create_generate(MUSIC, _params(fake.url))
    assert any("sampling" in m for m, _ in seen), seen
    # after each real show_progress call the panel holds that very line as its status
    assert all(m == status for m, status in seen) and seen[0][0].startswith("Sending")
    assert obj._saved_settings["music_last_server"] == fake.url
    assert obj._create_job_active is False and panel._state == "finished"


@pytest.mark.parametrize("fake_kw, needle", [
    (dict(busy=True), "busy"),
    (dict(down_after_submit=True), "not reachable"),
])
def test_failure_leaves_pending_and_says_why(monkeypatch, tmp_path, fake_kw, needle):
    """Review focus 3: server full / gone mid-job: plain message, panel not pending, flag cleared."""
    with FakeMusicServer(**fake_kw) as fake:
        server = mr.MusicServer(url=fake.url, profile=mr.profile_for(fake.health_body), up=True, saved=False)
        obj = _mw(monkeypatch, tmp_path, [server])
        obj._on_create_generate(MUSIC, _params(fake.url))
        if fake_kw.get("down_after_submit"):
            assert fake.cancelled
    panel = obj._create_view._result_panel
    assert obj._create_job_active is False
    assert panel._state == "error" and panel._pending_active is False
    status = obj._set_status.call_args_list[-1][0][0]
    assert needle in status.lower()


def test_unknown_server_and_bad_settings_fail_without_a_worker(monkeypatch, tmp_path):
    obj = _mw(monkeypatch, tmp_path, [])
    obj._on_create_generate(MUSIC, _params("http://nowhere"))
    assert obj._create_job_active is False and obj._create_view._result_panel._state == "error"
    server = _server(url="http://127.0.0.1:1")
    obj2 = _mw(monkeypatch, tmp_path, [server])
    obj2._on_create_generate(MUSIC, _params(server.url, seconds="abc"))
    assert obj2._create_job_active is False and obj2._create_view._result_panel._state == "error"


def test_token_is_in_no_status_error_or_record(monkeypatch, tmp_path):
    token = "tok-9f8e7d6c"
    with FakeMusicServer(require_token="the-real-one", error_500_echoes_headers=True) as fake:
        server = mr.MusicServer(url=fake.url, profile=mr.profile_for(fake.health_body), up=True, saved=False, token=token)
        obj = _mw(monkeypatch, tmp_path, [server])
        obj._on_create_generate(MUSIC, _params(fake.url))
    everything = repr(obj._set_status.call_args_list) + repr(obj._saved_settings) + str(obj._create_view._result_panel._pending_last_status)
    assert token not in everything


def test_discovery_runs_off_the_gtk_thread_and_reaches_the_panel(monkeypatch, tmp_path):
    import main_window as mw
    obj = _mw(monkeypatch, tmp_path, [])
    obj._music_discovering = False
    main_thread = threading.get_ident()
    seen = {}
    s = _server()

    def discover(saved):
        seen["thread"] = threading.get_ident()
        return [s]

    def _real_and_joined(target=None, daemon=None, **kw):
        thr = _RealThread(target=target, daemon=daemon)
        real_start = thr.start
        thr.start = lambda: (real_start(), thr.join())
        return thr

    monkeypatch.setattr(mw.threading, "Thread", _real_and_joined)
    monkeypatch.setattr(mw._settings, "get", lambda k, d=None: [] if k == "music_servers" else d)
    obj._discover_music_servers = discover
    obj._refresh_music_servers = mw.MainWindow._refresh_music_servers.__get__(obj)
    obj._apply_music_servers = mw.MainWindow._apply_music_servers.__get__(obj)
    obj._refresh_music_servers()
    assert seen["thread"] != main_thread
    obj._create_view.set_music_servers.assert_called_once_with([s])
    assert obj._music_discovering is False
