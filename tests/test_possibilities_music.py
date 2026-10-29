"""Music tile on the Start-something wall."""
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
    from gi.repository import Gtk
    Gtk.Entry()
except Exception:  # pragma: no cover
    pytest.skip("no GTK display available", allow_module_level=True)

from test_possibilities_wall import _FakeStore, _rec  # noqa: E402


def _music():
    return SimpleNamespace(id="music", label="Music", icon="\U0001f3b5", kind="audio", source="music",
                           generator=None, uses_llm=False)


def test_music_tile_present_with_distinct_example_lines():
    import possibilities as p
    pool = p._EXAMPLE_POOLS_BY_ID["music"]
    assert len(pool) >= 4 and len(set(pool)) == len(pool)
    assert any("chorus" in line or "lyrics" in line or "verse about" in line for line in pool)
    wall = p.PossibilitiesWall(mediums_fn=lambda: [_music()], on_pick=lambda m, i: None, store=_FakeStore())
    assert wall.card_count() == 1


def test_music_tile_art_prefers_latest_waveform(tmp_path):
    from possibilities import PossibilitiesWall
    wave = tmp_path / "w.png"
    wave.write_bytes(b"x")
    store = _FakeStore(latest={("audio", None): [_rec("audio", "music", thumb=str(wave))]})
    wall = PossibilitiesWall(mediums_fn=lambda: [_music()], on_pick=lambda m, i: None, store=store)
    assert wall._resolve_tile_art(_music()) == ("thumb", str(wave))


def test_music_tile_art_skips_record_without_thumbnail(tmp_path):
    from possibilities import PossibilitiesWall
    store = _FakeStore(latest={("audio", None): [_rec("audio", "music", thumb="")]})
    wall = PossibilitiesWall(mediums_fn=lambda: [_music()], on_pick=lambda m, i: None, store=store)
    assert wall._resolve_tile_art(_music()) == ("gradient", None)


def test_music_gradient_class_is_audio():
    import possibilities as p
    assert p._GRADIENT_CLASS_BY_KIND["audio"] == "poss-grad-audio"


def test_music_example_never_surfaces_a_stored_prompt(tmp_path):
    import possibilities as p
    wave = tmp_path / "w.png"
    wave.write_bytes(b"x")
    r = _rec("audio", "music", thumb=str(wave), starred=1)
    r.prompt = "STORED PROMPT WITH SECRET LYRICS"
    store = _FakeStore(latest={("audio", None): [r]})
    wall = p.PossibilitiesWall(mediums_fn=lambda: [_music()], on_pick=lambda m, i: None, store=store)
    assert wall._example_for(_music()) in p._EXAMPLE_POOLS_BY_ID["music"]
