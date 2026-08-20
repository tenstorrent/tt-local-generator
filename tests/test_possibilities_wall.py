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
        if starred:
            recs = [r for r in recs if getattr(r, "starred", 0)]
        return recs[:limit] if limit else recs

    def list_playlists(self):
        return self._playlists

    def playlist_records(self, pid):
        return self._playlist_recs.get(pid, [])


def _rec(mt, gt=None, thumb="/x.png", starred=0):
    return SimpleNamespace(media_type=mt, generator_type=gt, thumbnail_path=thumb, file_path=thumb, prompt="p", starred=starred)


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


def test_art_uses_curated_not_arbitrary_recent(tmp_path):
    """Tile art comes from a CURATED playlist, never an arbitrary recent
    generation — recents put unflattering/test images on the medium tiles.
    Even when a recent exists, the curated pick is used (and the recent isn't)."""
    from possibilities import PossibilitiesWall
    # Video has no bundled tile art, so curated-vs-recent applies cleanly here.
    meds = [_medium("video", kind="video")]
    recent = tmp_path / "recent.png"; recent.write_bytes(b"\x89PNG\r\n")
    curated = tmp_path / "curated.png"; curated.write_bytes(b"\x89PNG\r\n")
    store = _FakeStore(
        latest={("video", None): [_rec("video", thumb=str(recent))]},   # would-be recent
        playlists=[{"id": "p1", "name": "The Demo"}],                    # demo = curated
        playlist_recs={"p1": [_rec("video", thumb=str(curated))]},
    )
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=store)
    assert wall._resolve_tile_art(meds[0]) == ("thumb", str(curated))


def test_art_per_type_playlist_matches_by_name(tmp_path):
    """A per-type playlist (name = plural of the generator, e.g. "Ansis") is a
    curated source even though it doesn't match the demo/favorite name pattern."""
    from possibilities import PossibilitiesWall
    # `palette` has no bundled default (unlike ansi/verse/etc.), so the
    # per-type playlist tier is what's under test here.
    meds = [_medium("palette", kind="image", source="artgen", generator="palette")]
    thumb = tmp_path / "a.png"; thumb.write_bytes(b"\x89PNG\r\n")
    store = _FakeStore(playlists=[{"id": "p", "name": "Palettes"}],
                       playlist_recs={"p": [_rec("artgen", "palette", str(thumb))]})
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=store)
    assert wall._resolve_tile_art(meds[0]) == ("thumb", str(thumb))


def test_image_tile_uses_bundled_default_when_nothing_starred(tmp_path):
    """The Image tile shows its bundled default image over curated playlists and
    over your latest image (a starred pick would still override it — tier 1)."""
    import os
    import possibilities
    from possibilities import PossibilitiesWall
    meds = [_medium("image")]
    recent = tmp_path / "r.png"; recent.write_bytes(b"\x89PNG\r\n")
    store = _FakeStore(latest={("image", None): [_rec("image", thumb=str(recent))]})
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=store)
    kind, path = wall._resolve_tile_art(meds[0])
    assert kind == "thumb"
    assert path == os.path.join(possibilities._ASSETS_DIR,
                                possibilities._BUNDLED_TILE_ART["image"])
    assert os.path.exists(path)   # the asset actually ships


def test_native_medium_falls_back_to_latest_when_no_curated(tmp_path):
    """A NATIVE medium (image/video/animate) with no curated playlist falls
    back to your most recent piece of that medium — so the tile shows a real
    example instead of a bare gradient+icon once you've created anything.
    (Uses video, which has no bundled tile art overriding the fallback.)"""
    from possibilities import PossibilitiesWall
    meds = [_medium("video", kind="video")]
    recent = tmp_path / "r.png"; recent.write_bytes(b"\x89PNG\r\n")
    store = _FakeStore(latest={("video", None): [_rec("video", thumb=str(recent))]})
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=store)
    assert wall._resolve_tile_art(meds[0]) == ("thumb", str(recent))


def test_artgen_medium_stays_gradient_when_no_curated_even_with_recent(tmp_path):
    """ARTGEN mediums remain curated-ONLY: an arbitrary recent artgen must NOT
    surface on the tile (that put unflattering/test artifacts there) — no
    curated -> gradient, even if a recent exists."""
    from possibilities import PossibilitiesWall
    # `palette` has no bundled default, so with no curated playlist + only a
    # recent (not starred) it must fall through to the gradient.
    meds = [_medium("palette", kind="image", source="artgen", generator="palette")]
    recent = tmp_path / "r.png"; recent.write_bytes(b"\x89PNG\r\n")
    store = _FakeStore(latest={("artgen", "palette"): [_rec("artgen", "palette", str(recent))]})
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=store)
    assert wall._resolve_tile_art(meds[0])[0] == "gradient"


def test_art_prefers_starred_within_curated(tmp_path):
    from possibilities import PossibilitiesWall
    # `palette` has no bundled default, so the curated-playlist tier (and its
    # starred-first ordering) is what's under test.
    meds = [_medium("palette", kind="image", source="artgen", generator="palette")]
    plain = tmp_path / "plain.png"; plain.write_bytes(b"\x89PNG\r\n")
    star = tmp_path / "star.png"; star.write_bytes(b"\x89PNG\r\n")
    r_plain = _rec("artgen", "palette", str(plain)); r_plain.starred = 0
    r_star = _rec("artgen", "palette", str(star)); r_star.starred = 1
    store = _FakeStore(playlists=[{"id": "p", "name": "Palettes"}],
                       playlist_recs={"p": [r_plain, r_star]})
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=store)
    assert wall._resolve_tile_art(meds[0]) == ("thumb", str(star))


def test_empty_store_builds_all_gradient_no_exception(tmp_path):
    from possibilities import PossibilitiesWall
    meds = [_medium("image"), _medium("video", kind="video"), _medium("ansi", kind="text", source="artgen", generator="ansi")]
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=_FakeStore())
    assert wall.card_count() == 3


def test_starred_piece_is_top_priority_over_bundled(tmp_path):
    """An explicitly STARRED piece of a medium beats even the bundled default —
    starring IS curation, so it wins over the hardcoded Montreal '67 Image tile
    art. Regression for: 'I starred an image for the Image tile, but the tile
    didn't switch to it.'"""
    import os
    import possibilities
    from possibilities import PossibilitiesWall
    meds = [_medium("image")]
    starred_thumb = tmp_path / "starred.png"
    starred_thumb.write_bytes(b"\x89PNG\r\n")
    store = _FakeStore(latest={("image", None): [_rec("image", thumb=str(starred_thumb), starred=1)]})
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=store)
    kind, path = wall._resolve_tile_art(meds[0])
    assert kind == "thumb"
    assert path == str(starred_thumb)
    # sanity: this really did beat the bundled asset, not just coincide with it
    bundled = os.path.join(possibilities._ASSETS_DIR, "tile-image-montreal-1967.jpg")
    assert path != bundled


def test_starred_video_typed_gif_is_video_tile_art(tmp_path):
    """AnimateDiff/Wan2.2-Animate media is now media_type="video" (the
    animatediff-is-video migration folds it in, keeping the .gif file as-is).
    A starred one of those .gif records should surface on the Video tile
    exactly like any other starred video piece — pins the knock-on benefit
    that a starred AnimateDiff gif can be the Video "Start Something" tile."""
    from possibilities import PossibilitiesWall
    meds = [_medium("video", kind="video")]
    starred_gif = tmp_path / "starred.gif"
    starred_gif.write_bytes(b"GIF89a")
    store = _FakeStore(latest={("video", None): [_rec("video", thumb=str(starred_gif), starred=1)]})
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=store)
    assert wall._resolve_tile_art(meds[0]) == ("thumb", str(starred_gif))


def test_example_ideas_are_distinct_per_medium(tmp_path):
    """Every medium suggests something specific to its own art type -- no two
    tiles collapse to the same generic line (the old per-kind fallback made
    image/skyline/geometric/circuit all read 'a Moog Minimoog...')."""
    from possibilities import PossibilitiesWall
    meds = [
        _medium("image"),
        _medium("video", kind="video"),
        _medium("skyline", kind="image", source="artgen", generator="skyline"),
        _medium("geometric", kind="image", source="artgen", generator="geometric"),
        _medium("circuit", kind="image", source="artgen", generator="circuit"),
        _medium("palette", kind="image", source="artgen", generator="palette"),
        _medium("verse", kind="text", source="artgen", generator="verse"),
        _medium("freeform", kind="text", source="artgen", generator="freeform"),
    ]
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=_FakeStore())
    lines = [wall._example_for(m) for m in meds]
    assert len(set(lines)) == len(lines), f"duplicate example lines: {lines}"


def test_pool_rotates_so_repeat_builds_vary(tmp_path):
    """A medium's pool rotates, so a rebuilt/re-shown tile doesn't always show
    the same suggestion."""
    from possibilities import PossibilitiesWall
    meds = [_medium("image")]
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=_FakeStore())
    first = wall._pick_from_pool(meds[0])
    second = wall._pick_from_pool(meds[0])
    assert first != second


def test_example_ties_to_shown_starred_prompt(tmp_path):
    """When a tile shows an explicitly STARRED piece, the example line is that
    piece's own prompt -- the copy describes exactly what's on the tile."""
    from possibilities import PossibilitiesWall
    meds = [_medium("image")]
    thumb = tmp_path / "s.png"; thumb.write_bytes(b"\x89PNG\r\n")
    r = _rec("image", thumb=str(thumb), starred=1)
    r.prompt = "a heron mid-stride on wet slate"
    store = _FakeStore(latest={("image", None): [r]})
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=store)
    assert wall._example_for(meds[0]) == "a heron mid-stride on wet slate"


def test_no_starred_still_uses_existing_tiers(tmp_path):
    """With nothing starred, resolution is unchanged: bundled tier still wins
    for the Image tile (regression guard alongside
    test_image_tile_uses_bundled_worlds_fair_art)."""
    import os
    import possibilities
    from possibilities import PossibilitiesWall
    meds = [_medium("image")]
    recent = tmp_path / "r.png"; recent.write_bytes(b"\x89PNG\r\n")
    store = _FakeStore(latest={("image", None): [_rec("image", thumb=str(recent))]})
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=store)
    kind, path = wall._resolve_tile_art(meds[0])
    assert kind == "thumb"
    assert path == os.path.join(possibilities._ASSETS_DIR,
                                possibilities._BUNDLED_TILE_ART["image"])
