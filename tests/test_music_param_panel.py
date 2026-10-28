"""MusicParamPanel (Task 4): what each profile shows, what collect() sends, what validate() refuses.

Servers are handed in as MusicServer values; nothing here touches the network, docker or a chip."""
import sys
from pathlib import Path

import pytest

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)

import create_param_panels as cpp
import music_runtime as mr
import prompt_client
from chip_config import ChipEntry
from create_mediums import Medium


@pytest.fixture(autouse=True)
def _no_real_settings(monkeypatch):
    """Choosing a server remembers it in settings; keep every test off the real settings file."""
    saved = []
    monkeypatch.setattr(cpp, "_remember_music_server", saved.append)
    monkeypatch.setattr(cpp, "_last_music_server", lambda: "")
    monkeypatch.setattr(cpp, "INSTRUMENTAL_SWITCH_CONFIRMED", True)
    return saved


def _profile(model_hint):
    for hints, kw in mr.PROFILE_TABLE:
        if any(h in model_hint for h in hints):
            return mr.profile_for({"model": model_hint})
    raise AssertionError(model_hint)


def _server(model_hint, url="http://localhost:8795", up=True):
    return mr.MusicServer(url=url, profile=_profile(model_hint), up=up, saved=False, label=model_hint)


def _panel(model_hint="stable-audio", **kw):
    p = cpp.MusicParamPanel(**kw)
    p.build()
    p.set_server(_server(model_hint))
    return p


def _visible(p, key):
    row = p._row_for(key)
    return row is not None and row.get_visible()


def _music_medium():
    return Medium(id="music", label="Music", icon="🎵", kind="audio", source="music", generator=None, uses_llm=False)


def test_stable_audio_profile_hides_lyrics_and_shows_cfg():
    p = _panel("stable-audio")
    assert not _visible(p, "lyrics")
    assert _visible(p, "cfg_scale") and _visible(p, "steps")
    assert not p._instrumental_switch.get_visible()


def test_minimax_profile_shows_lyrics_hides_cfg():
    p = _panel("fiona-crapple")
    assert _visible(p, "lyrics")
    assert not _visible(p, "cfg_scale")
    assert p._instrumental_switch.get_visible()


def test_collect_omits_hidden_fields():
    p = _panel("stable-audio")
    p._prompt_entry.set_text("lofi beat")
    got = p.collect()
    assert "lyrics" not in got and "instrumental" not in got and "reference_clip" not in got
    assert got["prompt"] == "lofi beat" and got["server_url"] == "http://localhost:8795"
    assert "cfg_scale" in got and "steps" in got and "seconds" in got
    m = _panel("fiona-crapple")
    assert "cfg_scale" not in m.collect()
    assert set(m.collect()) <= {s.key for s in m.field_specs()}


def test_hidden_control_never_collected_even_with_a_value():
    """A value left in a control that the profile hides must not leak into the request."""
    p = _panel("stable-audio")
    p._lyrics_buffer.set_text("[Verse]\nla la")
    p._cfg_adj.set_value(7.0)
    p.set_server(_server("fiona-crapple"))
    got = p.collect()
    assert "cfg_scale" not in got and got["lyrics"].startswith("[Verse]")
    p.set_server(_server("stable-audio"))
    assert "lyrics" not in p.collect()


def test_duration_bounded_by_profile():
    p = _panel("stable-audio")
    assert (p._seconds_adj.get_lower(), p._seconds_adj.get_upper()) == (1, 120)
    p._seconds_adj.set_value(500)
    assert p.collect()["seconds"] == 120
    p.set_server(_server("fiona-crapple"))
    assert (p._seconds_adj.get_lower(), p._seconds_adj.get_upper()) == (5, 60)
    assert "ends when its lyrics do" in p._seconds_hint.get_label()


def test_instrumental_switch_hides_lyrics_and_sets_flag():
    p = _panel("fiona-crapple")
    assert p.collect()["instrumental"] is False and "lyrics" in p.collect()
    p._instrumental_switch.set_active(True)
    assert not _visible(p, "lyrics") or not p._lyrics_box.get_visible()
    got = p.collect()
    assert got["instrumental"] is True and "lyrics" not in got


def test_instrumental_switch_hidden_while_unconfirmed(monkeypatch):
    monkeypatch.setattr(cpp, "INSTRUMENTAL_SWITCH_CONFIRMED", False)
    p = _panel("fiona-crapple")
    assert not p._instrumental_switch.get_visible()
    assert "instrumental" not in p.collect()
    p._prompt_entry.set_text("rock")
    assert "lyrics" in p.validate().lower()          # still refused: no way to ask for an instrumental


def test_validate_refuses_empty_lyrics_for_minimax():
    p = _panel("fiona-crapple")
    p._prompt_entry.set_text("sad folk song")
    msg = p.validate()
    assert msg and "lyrics" in msg.lower()
    p._lyrics_buffer.set_text("[Verse]\nhello")
    assert p.validate() is None
    p._lyrics_buffer.set_text("")
    p._instrumental_switch.set_active(True)
    assert p.validate() is None


def test_validate_other_refusals():
    p = _panel("stable-audio")
    assert "describe" in p.validate().lower()          # empty prompt
    p._prompt_entry.set_text("x")
    assert p.validate() is None
    assert p.validate(prompt="") is not None            # an override replaces the panel's own box
    p.set_server(None)
    assert "server" in p.validate().lower()
    p.set_server(_server("stable-audio", up=False))
    assert "server" in p.validate().lower()
    p.set_server(_server("stable-audio"))
    p._seed_entry.set_text("abc")
    assert "seed" in p.validate().lower()


def test_audio_in_row_hidden_by_default_and_shown_for_edit_profile():
    p = _panel("stable-audio")
    assert not _visible(p, "reference_clip") and "reference_clip" not in p.collect()
    import dataclasses
    edit = mr.MusicServer(url="http://x:1", profile=dataclasses.replace(_profile("stable-audio"), audio_in="edit"),
                          up=True, saved=False)
    p.set_server(edit)
    assert _visible(p, "reference_clip")
    p._reference_path = "/tmp/clip.wav"
    assert p.collect()["reference_clip"] == "/tmp/clip.wav"


def test_trigger_phrase_chip_offered_for_fiona_profile():
    p = _panel("fiona-crapple")
    assert [c.text for c in p.trigger_chips()] == ["fiona crapple"]
    assert _panel("stable-audio").trigger_chips() == []
    # RoleZonePanel shows it among the pills and follows a server switch
    p2 = cpp.MusicParamPanel()
    rz = cpp.RoleZonePanel(p2, _music_medium())
    p2.set_server(_server("fiona-crapple"))
    pills = rz._modifier_pills
    assert [e.text for e in pills._extra_entries] == ["fiona crapple"]
    pills._apply_entry(pills._extra_entries[0])
    assert pills.applied_text() == "fiona crapple"
    p2.set_server(_server("stable-audio"))
    assert pills._extra_entries == []


def test_role_zone_shows_music_chip_bank_and_zones_fields():
    p = cpp.MusicParamPanel()
    rz = cpp.RoleZonePanel(p, _music_medium())
    cats = [c.name for c in rz._modifier_pills._categories]
    assert "Genre" in cats and "In the style of" in cats
    p.set_server(_server("stable-audio"))
    roles = {s.key: s.role.role for s in p.field_specs()}
    assert roles["prompt"] == roles["lyrics"] == "brief"
    assert roles["seconds"] == "direction"
    assert all(roles[k] == "control" for k in ("seed", "steps", "cfg_scale", "server_url", "reference_clip", "instrumental"))


def test_freetext_chip_applies_typed_value_and_ignores_empty():
    pills = cpp.ModifierPills("music", artgen=True)
    chip = next(e for c in pills._categories for e in c.chips if e.freetext)
    entry_widget, button = pills._freetext_widgets[id(chip)]
    button.emit("clicked")
    assert pills.applied_text() == ""                  # empty value applies nothing
    entry_widget.set_text("Nina Simone")
    button.emit("clicked")
    assert pills.applied_text() == "in the style of Nina Simone"
    other = next(e for c in pills._categories for e in c.chips if not e.freetext and not e.surprise)
    pills._apply_entry(other)
    assert pills.applied_text().endswith(other.text)


def test_server_dropdown_lists_down_servers_with_down_dot():
    p = _panel("stable-audio")
    up, down = _server("stable-audio", "http://localhost:8795"), _server("fiona-crapple", "http://localhost:20000", up=False)
    p.servers_changed([up, down])
    labels = p._server_labels()
    assert len(labels) == 2
    assert labels[0].startswith("●") and labels[1].startswith("○") and "down" in labels[1]
    assert p.selected_server().url == up.url
    # the picked server survives a refresh that reorders the list
    p._server_dd.set_selected(1)
    p.servers_changed([down, up])
    assert p.selected_server().url == down.url
    p.servers_changed([])
    assert p.selected_server() is None and "server" in p.validate().lower()


def test_default_server_is_last_used_else_first_up(monkeypatch):
    a, b = _server("stable-audio", "http://localhost:1"), _server("fiona-crapple", "http://localhost:2")
    p = cpp.MusicParamPanel()
    p.build()
    p.servers_changed([a, b])
    assert p.selected_server().url == a.url
    monkeypatch.setattr(cpp, "_last_music_server", lambda: "http://localhost:2")
    p2 = cpp.MusicParamPanel()
    p2.build()
    p2.servers_changed([a, b])
    assert p2.selected_server().url == b.url


def test_user_pick_is_remembered(_no_real_settings):
    p = _panel("stable-audio")
    p.servers_changed([_server("stable-audio", "http://localhost:1"), _server("fiona-crapple", "http://localhost:2")])
    assert _no_real_settings == []                      # a refresh alone does not write settings
    p._server_dd.set_selected(1)
    assert _no_real_settings == ["http://localhost:2"]


def test_connect_dialog_normalises_and_warns_on_non_local_http():
    url, warn, err = cpp.check_connect_address("Localhost:8795")
    assert (url, warn, err) == ("http://localhost:8795", "", "")
    url, warn, err = cpp.check_connect_address("http://10.1.2.3:20000")
    assert url == "http://10.1.2.3:20000" and "not encrypted" in warn and err == ""
    url, warn, err = cpp.check_connect_address("https://music.example.com")
    assert warn == "" and err == ""
    url, warn, err = cpp.check_connect_address("http://:99999999")
    assert url is None and err


def test_connect_dialog_probes_then_saves(monkeypatch):
    saved, got = [], []
    monkeypatch.setattr(cpp, "save_server", lambda url, token=None, label="": saved.append((url, token, label)))
    prof = _profile("stable-audio")
    dlg = cpp.MusicConnectDialog(probe_fn=lambda url, token=None: prof, run_async=lambda fn, done: done(fn()),
                                 on_connected=got.append)
    dlg._url_entry.set_text("localhost:8795")
    dlg._token_entry.set_text("tok")
    dlg._on_connect_clicked(None)
    assert saved == [("http://localhost:8795", "tok", prof.label)]
    assert got[0].url == "http://localhost:8795" and got[0].up and got[0].saved and got[0].profile is prof
    # nothing answering: not saved, plain message
    saved.clear()
    dlg2 = cpp.MusicConnectDialog(probe_fn=lambda url, token=None: None, run_async=lambda fn, done: done(fn()),
                                  on_connected=got.append)
    dlg2._url_entry.set_text("localhost:9")
    dlg2._on_connect_clicked(None)
    assert saved == [] and "No music server" in dlg2._status.get_label()


def test_lyrics_helper_disabled_message_when_no_llm():
    calls = []
    p = cpp.MusicParamPanel(inspire_fn=lambda *a: calls.append(a), prompt_type_getter=lambda: "image",
                            llm_available_fn=lambda: True)
    p.build()
    p.set_server(_server("fiona-crapple"))
    p.set_llm_available(False)
    assert not p._lyrics_inspire_btn.get_sensitive()
    assert "chat model" in p._lyrics_helper_hint.get_label().lower()
    assert p._lyrics_helper_hint.get_visible()
    p.set_llm_available(True)
    assert p._lyrics_inspire_btn.get_sensitive() and not p._lyrics_helper_hint.get_visible()


def test_inspire_buttons_use_music_and_lyrics_prompt_types():
    calls = []
    p = cpp.MusicParamPanel(inspire_fn=lambda t, s, ok, err: calls.append((t, s)), prompt_type_getter=lambda: "image",
                            llm_available_fn=lambda: True)
    p.build()
    p.set_server(_server("fiona-crapple"))
    p._prompt_entry.set_text("sad folk")
    p._prompt_inspire_btn.emit("clicked")
    assert calls[-1] == ("music", "sad folk")
    p._lyrics_inspire_btn.emit("clicked")                # empty lyrics: seeded from the prompt
    assert calls[-1] == ("lyrics", "sad folk")
    p2 = cpp.MusicParamPanel(inspire_fn=lambda t, s, ok, err: calls.append((t, s)), llm_available_fn=lambda: True)
    p2.build()
    p2.set_server(_server("fiona-crapple"))
    p2._prompt_entry.set_text("ignored while lyrics exist")
    p2._lyrics_buffer.set_text("[Verse]\nmine")
    p2._lyrics_inspire_btn.emit("clicked")
    assert calls[-1] == ("lyrics", "[Verse]\nmine")


def test_insert_tag_puts_structure_tag_on_its_own_line():
    p = _panel("fiona-crapple")
    p.insert_tag("[Chorus]")
    assert "[Chorus]" in p._lyrics_text()
    assert p._lyrics_text().strip().startswith("[Chorus]")


def test_music_inspire_keeps_seed_without_llm(monkeypatch):
    import artgen
    monkeypatch.setattr(artgen, "detect_artgen_endpoint", lambda *a, **k: (None, None))
    assert prompt_client.generate_prompt("music", "slow blues in D") == "slow blues in D"
    assert prompt_client.generate_prompt("music", "")           # no seed: a canned line, as before
