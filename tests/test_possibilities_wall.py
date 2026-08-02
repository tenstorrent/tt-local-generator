"""
Tests for `PossibilitiesWall` (app/possibilities.py) — the "Start something"
wall for the Create surface (SP-2 Task 1 of the Unified Stage redesign).

This widget NEVER generates and never reads generation params; it only
resolves per-medium tile art (your-latest -> curated playlist -> gradient)
and fires `on_pick(medium, example_idea)` on tap. Mirrors the GTK-probe/skip
header used by tests/test_main_window_loop_nav.py (lines 22-40).
"""
from __future__ import annotations

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
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)


def _medium(id, label="X", icon="*", kind="image", source="native", generator=None):
    return SimpleNamespace(id=id, label=label, icon=icon, kind=kind, source=source, generator=generator, uses_llm=True)


class _FakeStore:
    def __init__(self, latest=None, playlists=None, playlist_recs=None):
        self._latest = latest or {}          # (media_type, generator_type) -> [MediaRecord-likes]
        self._playlists = playlists or []    # list of {"id","name"}
        self._playlist_recs = playlist_recs or {}  # id -> [records]

    def query(self, media_type=None, generator_type=None, starred=None, limit=None):
        recs = self._latest.get((media_type, generator_type), [])
        return recs[:limit] if limit else recs

    def list_playlists(self):
        return self._playlists

    def playlist_records(self, pid):
        return self._playlist_recs.get(pid, [])


def _rec(mt, gt=None, thumb="/x.png"):
    return SimpleNamespace(media_type=mt, generator_type=gt, thumbnail_path=thumb, file_path=thumb, prompt="p")


def test_wall_builds_one_card_per_medium(tmp_path, monkeypatch):
    from possibilities import PossibilitiesWall
    meds = [_medium("image"), _medium("verse", kind="text", source="artgen", generator="verse")]
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=_FakeStore())
    # count Gtk.Button descendants tagged as medium cards
    assert wall.card_count() == 2


def test_pick_fires_on_pick_with_medium_and_idea(tmp_path):
    from possibilities import PossibilitiesWall
    picked = []
    meds = [_medium("image")]
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: picked.append((m.id, i)), store=_FakeStore())
    wall._activate_card(meds[0])          # simulate a tile click
    assert picked and picked[0][0] == "image" and isinstance(picked[0][1], str) and picked[0][1]


def test_art_resolution_prefers_your_latest(tmp_path):
    from possibilities import PossibilitiesWall
    meds = [_medium("image")]
    store = _FakeStore(latest={("image", None): [_rec("image", thumb=str(tmp_path/'mine.png'))]})
    (tmp_path/'mine.png').write_bytes(b"\x89PNG\r\n")   # exists on disk
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=store)
    kind, payload = wall._resolve_tile_art(meds[0])
    assert kind == "thumb" and payload == str(tmp_path/'mine.png')


def test_art_resolution_falls_back_to_curated_then_gradient(tmp_path):
    from possibilities import PossibilitiesWall
    meds = [_medium("verse", kind="text", source="artgen", generator="verse")]
    # no personal work; a curated playlist named "demo" holds a matching artgen/verse rec
    thumb = tmp_path/'curated.png'; thumb.write_bytes(b"\x89PNG\r\n")
    store = _FakeStore(playlists=[{"id": "p1", "name": "The Demo"}],
                       playlist_recs={"p1": [_rec("artgen", "verse", str(thumb))]})
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=store)
    assert wall._resolve_tile_art(meds[0]) == ("thumb", str(thumb))
    # with NOTHING anywhere -> gradient tier, never raises
    bare = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=_FakeStore())
    assert bare._resolve_tile_art(meds[0])[0] == "gradient"


def test_empty_store_builds_all_gradient_no_exception(tmp_path):
    from possibilities import PossibilitiesWall
    meds = [_medium("image"), _medium("video", kind="video"), _medium("ansi", kind="text", source="artgen", generator="ansi")]
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=_FakeStore())
    assert wall.card_count() == 3


def test_your_latest_beats_curated_when_both_present(tmp_path):
    """Tier-1 (your latest) wins over tier-2 (curated playlist) when BOTH have a
    usable thumbnail for the same medium — proves the priority order by putting
    the tiers in direct competition, not in isolation."""
    from possibilities import PossibilitiesWall
    meds = [_medium("image")]
    mine = tmp_path / "mine.png"; mine.write_bytes(b"\x89PNG\r\n")
    curated = tmp_path / "curated.png"; curated.write_bytes(b"\x89PNG\r\n")
    store = _FakeStore(
        latest={("image", None): [_rec("image", thumb=str(mine))]},
        playlists=[{"id": "p1", "name": "The Demo"}],
        playlist_recs={"p1": [_rec("image", thumb=str(curated))]},
    )
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=store)
    assert wall._resolve_tile_art(meds[0]) == ("thumb", str(mine))
