"""Whole-branch review fixes for music generation (final review round).

Each test names the finding it guards. None touches a real server, docker or a chip."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, GLib
    Gtk.Entry()
except Exception:  # pragma: no cover
    pytest.skip("no GTK display available", allow_module_level=True)

import create_param_panels as cpp
import music_runtime as mr
from media_store import MediaRecord


def _server(hint="stable-audio", url="http://localhost:8795", up=True):
    return mr.MusicServer(url=url, profile=mr.profile_for({"model": hint}), up=up, saved=False, label=hint)


def _walk(w):
    yield w
    c = w.get_first_child()
    while c is not None:
        yield from _walk(c)
        c = c.get_next_sibling()


def _pump(settle=0.0):
    """Run pending GLib callbacks; `settle` waits that long first so a background check can post its result."""
    import time
    time.sleep(settle)
    ctx = GLib.MainContext.default()
    for _ in range(20):
        if not ctx.iteration(False):
            break


# ── Important 1: the last-used server really goes through app settings ───────────────────────────────

def test_last_used_server_round_trips_through_real_settings():
    """No helper is monkeypatched: the pick must reach AppSettings and come back on a rebuilt panel."""
    a, b = _server("stable-audio", "http://localhost:1"), _server("fiona-crapple", "http://localhost:2")
    p = cpp.MusicParamPanel()
    p.build()
    p.servers_changed([a, b])
    assert p.selected_server().url == a.url
    p._server_dd.set_selected(1)
    assert cpp._settings.get("music_last_server") == b.url
    p2 = cpp.MusicParamPanel()
    p2.build()
    p2.servers_changed([a, b])
    assert p2.selected_server().url == b.url


# ── Important 3: the Connect dialog says where the token is kept ─────────────────────────────────────

def test_connect_dialog_says_token_is_stored_as_plain_text():
    dlg = cpp.MusicConnectDialog()
    texts = " ".join(x.get_label() for x in _walk(dlg.get_child()) if isinstance(x, Gtk.Label)).lower()
    assert "plain text" in texts and "settings file" in texts


# ── Important 4: the Instrumental switch ships off ───────────────────────────────────────────────────

def test_instrumental_switch_ships_off_and_empty_lyrics_are_refused():
    assert cpp.INSTRUMENTAL_SWITCH_CONFIRMED is False
    p = cpp.MusicParamPanel()
    p.build()
    p.set_server(_server("fiona-crapple"))
    assert not p._instrumental_switch.get_visible()
    p._prompt_entry.set_text("rock")
    msg = p.validate()
    assert msg and "lyrics" in msg.lower()
    assert "instrumental" not in p.collect()


# ── Minor a: localhost and 127.0.0.1 are one server ──────────────────────────────────────────────────

def test_loopback_names_merge_into_one_server_keeping_saved_token_and_label():
    seen = []

    def probe(url, token):
        seen.append(url)
        return mr.profile_for({"model": "stable-audio"})

    saved = [{"url": "http://localhost:8795", "token": "tok", "label": "mine"}]
    out = mr.discover_music_servers(saved, ports=(8795,), probe=probe, docker_ports=lambda: [])
    assert len(out) == 1 and len(seen) == 1
    assert out[0].token == "tok" and out[0].label == "mine" and out[0].saved


def test_scanned_127_and_saved_ipv6_loopback_also_merge():
    probe = lambda url, token: mr.profile_for({"model": "stable-audio"})   # noqa: E731
    out = mr.discover_music_servers([{"url": "http://[::1]:8795"}], ports=(8795,), probe=probe,
                                    docker_ports=lambda: [])
    assert len(out) == 1


# ── Minor b: the no-LLM hint tells the truth ─────────────────────────────────────────────────────────

def test_no_llm_hint_does_not_promise_it_turns_on_by_itself():
    assert "by itself" not in cpp._NO_LLM_HINT
    assert "pick Music again" in cpp._NO_LLM_HINT


# ── Minor c: no test can run real discovery ──────────────────────────────────────────────────────────

def test_discovery_stub_is_installed_even_before_main_window_was_imported():
    src = (Path(__file__).parent / "conftest.py").read_text()
    block = src[src.index("def _no_real_music_discovery"):]
    assert "sys.modules.get" not in block.split("\n@pytest.fixture")[0]


# ── Minor d: a 403 is not always a bad token ─────────────────────────────────────────────────────────

def test_403_message_names_token_and_host_rule_without_the_token(monkeypatch):
    import io
    import urllib.error

    class _Op:
        def open(self, req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"detail":"bad tok-secret"}'))

    monkeypatch.setattr(mr, "_opener", lambda: _Op())
    client = mr.MusicClient("http://localhost:1", mr.profile_for({"model": "stable-audio"}), token="tok-secret")
    with pytest.raises(mr.MusicServerError) as ei:
        client._call("GET", "/v1/health")
    msg = str(ei.value)
    assert "token" in msg and "Host" in msg and "tok-secret" not in msg


# ── Minor e: Connect reaches the main window's list at once ──────────────────────────────────────────

def test_connect_success_tells_the_owner_and_main_window_lists_the_server():
    import main_window as mw
    fake = SimpleNamespace(_music_servers=[_server("stable-audio", "http://localhost:1")], refreshed=0,
                           painted=0)
    fake._refresh_music_servers = lambda: setattr(fake, "refreshed", fake.refreshed + 1)
    fake._paint_music_segment = lambda: setattr(fake, "painted", fake.painted + 1)
    new = _server("fiona-crapple", "http://localhost:2")
    mw.MainWindow._on_music_connected(fake, new)
    assert [s.url for s in fake._music_servers] == ["http://localhost:1", "http://localhost:2"]
    assert fake.refreshed == 1


def test_create_view_forwards_connect_to_its_owner(monkeypatch):
    import create_view
    from create_mediums import Medium
    got = []
    music = Medium(id="music", label="Music", icon="m", kind="audio", source="music", generator=None, uses_llm=False)
    monkeypatch.setattr(create_view.GLib, "idle_add", lambda fn, *a: fn(*a))
    view = create_view.CreateView(mediums_fn=lambda: [music], health_fn=lambda: {},
                                  on_music_connected=got.append)
    view._chip_buttons["music"].set_active(True)
    panel = view._active_panel._panel
    panel.on_connect_saved(_server())
    assert [s.url for s in got] == ["http://localhost:8795"]


# ── Minor f: no LLM must not copy the style prompt into the lyrics box ───────────────────────────────

def test_lyrics_helper_discards_an_echo_of_the_style_prompt():
    def echo(prompt_type, seed, on_result, on_error):
        on_result(seed)                                  # what prompt_client does with no LLM: hand the seed back

    p = cpp.MusicParamPanel(inspire_fn=echo, llm_available_fn=lambda: True)
    p.build()
    p.set_server(_server("fiona-crapple"))
    p._prompt_entry.set_text("sad folk song")
    _pump(0.3)                                           # the start-up chat-model check has reported by now
    p._lyrics_inspire_btn.emit("clicked")
    _pump()
    assert p._lyrics_text().strip() == ""
    assert p._lyrics_helper_hint.get_visible() and not p._lyrics_inspire_btn.get_sensitive()


def test_lyrics_helper_keeps_polished_lyrics_the_user_typed():
    p = cpp.MusicParamPanel(inspire_fn=lambda t, s, ok, err: ok("[Verse]\nbetter " + s),
                            llm_available_fn=lambda: True)
    p.build()
    p.set_server(_server("fiona-crapple"))
    p._lyrics_buffer.set_text("mine")
    p._lyrics_inspire_btn.emit("clicked")
    _pump()
    assert p._lyrics_text() == "[Verse]\nbetter mine"


# ── Important 2: a clip can be asked for again ───────────────────────────────────────────────────────

def _audio_rec(tmp_path, **params):
    f = tmp_path / "s.flac"
    f.write_bytes(b"fLaC")
    p = {"prompt": "p", "seconds_got": 10.0, "seconds_asked": 10.0}
    p.update(params)
    r = MediaRecord(id="a1", media_type="audio", created_at="2026-10-28T00:00:00", file_path=str(f),
                       thumbnail_path="", prompt="x", model_id="m", generator_type="music",
                       params=json.dumps(p), starred=0)
    r.media_file_path = str(f)
    return r


def test_audio_widget_shows_selectable_seed_and_use_button(tmp_path):
    from audio_gallery import build_audio_widget
    rec = _audio_rec(tmp_path, seed=123456)
    got = []
    w = build_audio_widget(rec, rec.file_path, True, on_use_seed=got.append)
    seed = next(x for x in _walk(w) if isinstance(x, Gtk.Label) and x.get_label() == "Seed 123456")
    assert seed.get_selectable()
    btn = next(x for x in _walk(w) if isinstance(x, Gtk.Button) and x.get_label() == "Use this seed")
    btn.emit("clicked")
    assert got == [123456]


def test_audio_widget_without_callback_shows_seed_but_no_button(tmp_path):
    from audio_gallery import build_audio_widget
    rec = _audio_rec(tmp_path, seed=7)
    w = build_audio_widget(rec, rec.file_path, True)
    assert any(isinstance(x, Gtk.Label) and x.get_label() == "Seed 7" for x in _walk(w))
    assert not any(isinstance(x, Gtk.Button) and x.get_label() == "Use this seed" for x in _walk(w))
    rec2 = _audio_rec(tmp_path)                           # an older record with no seed
    w2 = build_audio_widget(rec2, rec2.file_path, True, on_use_seed=lambda s: None)
    assert not any(isinstance(x, Gtk.Label) and (x.get_label() or "").startswith("Seed") for x in _walk(w2))
    assert not any(isinstance(x, Gtk.Button) and x.get_label() == "Use this seed" for x in _walk(w2))


def test_use_this_seed_in_create_pins_it_in_the_music_panel(tmp_path, monkeypatch):
    import create_view
    from create_mediums import Medium
    music = Medium(id="music", label="Music", icon="m", kind="audio", source="music", generator=None, uses_llm=False)
    monkeypatch.setattr(create_view.GLib, "idle_add", lambda fn, *a: fn(*a))
    view = create_view.CreateView(mediums_fn=lambda: [music], health_fn=lambda: {})
    view._chip_buttons["music"].set_active(True)
    view.set_music_servers([_server()])
    panel = view._active_panel._panel
    assert panel._seed_entry.get_text() == ""
    rec = _audio_rec(tmp_path, seed=424242)
    w = view._result_panel._build_artifact_widget(rec)
    btn = next(x for x in _walk(w) if isinstance(x, Gtk.Button) and x.get_label() == "Use this seed")
    btn.emit("clicked")
    assert panel._seed_entry.get_text() == "424242"
    assert panel.collect()["seed"] == 424242


# ── Minor g: deleting a song removes its files, only inside the library ──────────────────────────────

def _stored(tmp_path, monkeypatch, rid, inside=True):
    import media_store
    root = Path(media_store.STORAGE_DIR) / "music" if inside else tmp_path / "elsewhere"
    root.mkdir(parents=True, exist_ok=True)
    audio, thumb = root / f"{rid}.flac", root / f"{rid}.png"
    audio.write_bytes(b"fLaC")
    thumb.write_bytes(b"png")
    rec = MediaRecord(id=rid, media_type="audio", created_at="2026-10-28T00:00:00", file_path=str(audio),
                      thumbnail_path=str(thumb), prompt="x", model_id="m", generator_type="music",
                      params="{}", starred=0)
    media_store.media_store.add(rec)
    return audio, thumb


def test_gallery_card_delete_removes_files_inside_storage(tmp_path, monkeypatch):
    import audio_gallery
    audio, thumb = _stored(tmp_path, monkeypatch, "in1")
    g = audio_gallery.AudioGallery()
    g.refresh()
    g._delete_card("in1")
    assert not audio.exists() and not thumb.exists() and audio_gallery._ms.get("in1") is None


def test_detail_delete_removes_files_inside_storage(tmp_path, monkeypatch):
    import audio_gallery
    audio, thumb = _stored(tmp_path, monkeypatch, "in2")
    d = audio_gallery.AudioDetail()
    d.show_record("in2", [audio_gallery._ms.get("in2")])
    d._delete_confirmed_for("in2")
    assert not audio.exists() and not thumb.exists()


def test_delete_leaves_files_outside_storage_alone(tmp_path, monkeypatch):
    import audio_gallery
    audio, thumb = _stored(tmp_path, monkeypatch, "out1", inside=False)
    audio_gallery.remove_song_files(audio_gallery._ms.get("out1"))
    assert audio.exists() and thumb.exists()


def test_delete_of_missing_files_does_not_raise(tmp_path, monkeypatch):
    import audio_gallery
    audio, thumb = _stored(tmp_path, monkeypatch, "gone")
    audio.unlink()
    thumb.unlink()
    audio_gallery.remove_song_files(audio_gallery._ms.get("gone"))
