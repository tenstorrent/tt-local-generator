"""CreateResultPanel's audio branch: player, waveform, lyrics, credit, length note, missing file, teardown."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, GLib
    Gtk.Entry()
except Exception:  # pragma: no cover
    pytest.skip("no GTK display available", allow_module_level=True)

import create_view as cv
from media_store import MediaRecord


def _walk(w):
    yield w
    c = w.get_first_child()
    while c is not None:
        yield from _walk(c)
        c = c.get_next_sibling()


def _texts(w):
    return [x.get_label() for x in _walk(w) if isinstance(x, Gtk.Label)]


def _rec(tmp_path, *, exists=True, thumb=False, **params):
    audio = tmp_path / "song.flac"
    if exists:
        audio.write_bytes(b"fLaC")
    t = ""
    if thumb:
        from PIL import Image
        t = str(tmp_path / "song.png")
        Image.new("RGB", (320, 180), (15, 42, 53)).save(t)
    p = {"prompt": "p", "lyrics": None, "seconds_asked": 30.0, "seconds_got": 30.0, "ended": False, "credit": ""}
    p.update(params)
    r = MediaRecord(id="a1", media_type="audio", created_at="2026-10-28T00:00:00", file_path=str(audio),
                    thumbnail_path=t, prompt="slow synth pads", model_id="m", generator_type="music",
                    params=json.dumps(p), starred=0)
    r.media_file_path = str(audio)
    return r


def _button(w, label):
    return next(x for x in _walk(w) if isinstance(x, Gtk.Button) and x.get_label() == label)


def test_audio_record_renders_player_lyrics_and_credit(tmp_path):
    p = cv.CreateResultPanel()
    w = p._build_artifact_widget(_rec(tmp_path, thumb=True, lyrics="[verse]\nla la la", credit="MiniMax Music 2.0"))
    kinds = [type(x) for x in _walk(w)]
    assert Gtk.MediaControls in kinds
    assert Gtk.Picture in kinds, "the waveform picture is missing"
    tv = next(x for x in _walk(w) if isinstance(x, Gtk.TextView))
    buf = tv.get_buffer()
    assert buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False) == "[verse]\nla la la"
    assert not tv.get_editable()
    assert any("MiniMax Music 2.0" in t for t in _texts(w))
    assert _button(w, "Open externally").get_sensitive()


def test_no_lyrics_means_no_lyrics_view(tmp_path):
    w = cv.CreateResultPanel()._build_artifact_widget(_rec(tmp_path))
    assert not any(isinstance(x, Gtk.TextView) for x in _walk(w))


def test_missing_audio_file_shows_not_found_and_does_not_raise(tmp_path):
    p = cv.CreateResultPanel()
    p.show_finished(_rec(tmp_path, exists=False, lyrics="la"))
    w = p._current_box.get_first_child()
    assert any("not found" in t.lower() for t in _texts(w))
    assert not _button(w, "Open externally").get_sensitive()
    assert p._result_audio is None
    assert not any(isinstance(x, Gtk.MediaControls) for x in _walk(w))


def test_short_song_says_it_ended_when_the_lyrics_did(tmp_path):
    w = cv.CreateResultPanel()._build_artifact_widget(
        _rec(tmp_path, seconds_asked=120.0, seconds_got=48.0, ended=True, lyrics="la"))
    assert any("lyrics" in t.lower() and "48" in t and "120" in t for t in _texts(w))


def test_full_length_song_has_no_short_note(tmp_path):
    w = cv.CreateResultPanel()._build_artifact_widget(_rec(tmp_path))
    assert not any("ended" in t.lower() for t in _texts(w))


def test_swap_result_releases_media_stream(tmp_path):
    p = cv.CreateResultPanel()
    p.show_finished(_rec(tmp_path))
    stream = p._result_audio
    assert stream is not None
    calls = []
    stream.pause = lambda: calls.append("pause")        # instance attribute shadows the GObject method
    stream.get_playing = lambda: True
    p.show_error("boom")
    assert p._result_audio is None
    assert calls == ["pause"], "the playing stream was dropped without being paused"


def test_stream_error_present_at_construction_shows_the_note(tmp_path, monkeypatch):
    """GStreamer can set the error while the MediaFile is built, before notify::error is connected."""
    real = Gtk.MediaFile.new_for_filename

    def failing(path):
        s = real(path)
        s.gerror(GLib.Error.new_literal(GLib.quark_from_string("t"), "no decoder", 0))
        return s

    monkeypatch.setattr(Gtk.MediaFile, "new_for_filename", staticmethod(failing))
    w = cv.CreateResultPanel()._build_artifact_widget(_rec(tmp_path))
    assert not any(isinstance(x, Gtk.MediaControls) and x.get_visible() for x in _walk(w))
    note = next(x for x in _walk(w) if isinstance(x, Gtk.Label) and "could not play" in x.get_label())
    assert note.get_visible()


def test_recent_chip_for_a_song_is_not_a_bare_question_mark():
    assert cv._recent_chip_label("audio", "music") != "?"
