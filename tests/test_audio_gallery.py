"""Library gallery for audio (Task 7): routing, filters that must not see audio, the card, the detail, and the
converter leaving audio rows alone."""
import json
import sqlite3
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
    Gtk.Entry()
except Exception:  # pragma: no cover
    pytest.skip("no GTK display available", allow_module_level=True)

from media_store import MediaRecord


class _Store:
    """Resolve the store per use, past the module proxy: another test monkeypatches `media_store.star` on the proxy
    and the undo leaves a stale bound method on it, so reading attributes through the proxy can hit a dead store."""

    def __getattr__(self, name):
        import media_store
        return getattr(media_store._get_media_store(), name)


ms = _Store()


def _walk(w):
    yield w
    c = w.get_first_child()
    while c is not None:
        yield from _walk(c)
        c = c.get_next_sibling()


def _texts(w):
    return [x.get_label() for x in _walk(w) if isinstance(x, Gtk.Label)]


def _audio(tmp_path, rid="a1", *, created="2026-10-28T10:00:00", thumb=False, exists=True, starred=0, **params):
    f = tmp_path / f"{rid}.flac"
    if exists:
        f.write_bytes(b"fLaC")
    t = ""
    if thumb:
        from PIL import Image
        t = str(tmp_path / f"{rid}.png")
        Image.new("RGB", (320, 180), (15, 42, 53)).save(t)
    p = {"prompt": "p", "lyrics": None, "seconds_asked": 30.0, "seconds_got": 30.0, "ended": False,
         "credit": ""}
    p.update(params)
    return MediaRecord(id=rid, media_type="audio", created_at=created, file_path=str(f), thumbnail_path=t,
                       prompt="slow synth pads", model_id="m", generator_type="music", params=json.dumps(p),
                       starred=starred)


def _other(rid, mt, gt=None, created="2026-10-27T00:00:00"):
    return MediaRecord(id=rid, media_type=mt, created_at=created, file_path="/nonexistent/" + rid,
                       thumbnail_path="", prompt="x", model_id="m", generator_type=gt, params="{}", starred=0)


def test_audio_records_route_to_audio_gallery_only(tmp_path):
    from audio_gallery import AudioGallery
    ms.add(_audio(tmp_path, "a1", created="2026-10-28T10:00:00"))
    ms.add(_audio(tmp_path, "a2", created="2026-10-28T11:00:00"))
    ms.add(_other("v1", "video"))
    ms.add(_other("g1", "artgen", "ansi"))
    g = AudioGallery()
    g.refresh()
    ids = [r.id for r in g._records]
    assert ids == ["a2", "a1"], "audio only, newest first"
    assert len(g.cards()) == 2


def test_audio_never_reaches_video_or_image_gallery(tmp_path):
    from history_store import HistoryStore
    ms.add(_audio(tmp_path))
    ms.add(_other("v1", "video"))
    ms.add(_other("i1", "image"))
    h = HistoryStore()
    assert sorted(r.id for r in h.all_records()) == ["i1", "v1"]
    assert h.artgen_records() == []
    assert h.delete("a1") is None, "the native store must not delete (or convert) an audio record"
    assert ms.get("a1") is not None


def test_existing_library_filters_unchanged(tmp_path):
    from history_store import HistoryStore
    ms.add(_other("v1", "video"))
    ms.add(_other("i1", "image"))
    ms.add(_other("g1", "artgen", "ansi"))
    before = (len(HistoryStore()), ms.count_non_artgen(), ms.count("artgen"), ms.count("video"))
    ms.add(_audio(tmp_path))
    after = (len(HistoryStore()), ms.count_non_artgen(), ms.count("artgen"), ms.count("video"))
    assert before == after == (2, 2, 1, 1)
    assert ms.count("audio") == 1


def test_audio_not_a_start_something_tile_for_other_mediums(tmp_path):
    from create_mediums import Medium
    from possibilities import PossibilitiesWall
    ms.add(_audio(tmp_path, thumb=True, starred=1))
    mediums = [Medium(id="video", label="Video", icon="v", kind="video", source="native", generator=None),
               Medium(id="image", label="Image", icon="i", kind="image", source="native", generator=None),
               Medium(id="verse", label="Verse", icon="s", kind="text", source="artgen", generator="verse")]
    wall = PossibilitiesWall(mediums_fn=lambda: mediums, on_pick=lambda *_: None, store=ms)
    for m in mediums:
        assert wall._starred_record_for(m) is None
        art = wall._resolve_tile_art(m)
        assert str(tmp_path) not in str(art), "an audio waveform must never be another medium's tile"


def test_audio_card_has_no_remix_button(tmp_path):
    from audio_gallery import AudioGallery
    ms.add(_audio(tmp_path, thumb=True))
    g = AudioGallery()
    g.refresh()
    labels = [x.get_label() for c in g.cards() for x in _walk(c) if isinstance(x, Gtk.Button)]
    assert not any("Remix" in (l or "") for l in labels)
    assert "☆" in labels and "🗑" in labels


def test_card_click_activates_and_missing_thumbnail_still_builds(tmp_path):
    from audio_gallery import AudioGallery
    ms.add(_audio(tmp_path, "w", thumb=True, created="2026-10-28T12:00:00"))
    ms.add(_audio(tmp_path, "n", thumb=False, created="2026-10-28T11:00:00"))
    g = AudioGallery()
    seen = []
    g.on_card_activated = seen.append
    g.refresh()
    assert len(g.cards()) == 2
    pics = [[x for x in _walk(c) if isinstance(x, Gtk.Picture)] for c in g.cards()]
    assert len(pics[0]) == 1 and len(pics[1]) == 0, "the waveform card has a picture; the other a placeholder"
    g.activate("n")
    assert seen == ["n"]


def test_detail_shows_player_lyrics_credit_star_delete_and_no_remix(tmp_path):
    from audio_gallery import AudioDetail
    r = _audio(tmp_path, thumb=True, lyrics="[verse]\nla la", credit="MiniMax-Music3")
    d = AudioDetail()
    d.show_record(r.id, [r])
    kinds = [type(x) for x in _walk(d)]
    assert Gtk.MediaControls in kinds
    text = " ".join(t for t in _texts(d) if t)
    assert "Made with MiniMax-Music3" in text
    assert any(isinstance(x, Gtk.TextView) and "la la" in x.get_buffer().get_text(
        x.get_buffer().get_start_iter(), x.get_buffer().get_end_iter(), False) for x in _walk(d))
    buttons = [x.get_label() for x in _walk(d) if isinstance(x, (Gtk.Button, Gtk.ToggleButton))]
    assert any("Star" in (b or "") for b in buttons) and any("Delete" in (b or "") or "🗑" in (b or "") for b in buttons)
    assert not any("Remix" in (b or "") for b in buttons)
    d.pause()


def test_detail_missing_file_says_not_found_and_disables_open(tmp_path):
    from audio_gallery import AudioDetail
    r = _audio(tmp_path, exists=False, credit="MiniMax-Music3")
    d = AudioDetail()
    d.show_record(r.id, [r])
    assert any("Audio file not found" in (t or "") for t in _texts(d))
    assert Gtk.MediaControls not in [type(x) for x in _walk(d)]
    op = next(x for x in _walk(d) if isinstance(x, Gtk.Button) and x.get_label() == "Open externally")
    assert not op.get_sensitive()
    assert "Made with MiniMax-Music3" in " ".join(t for t in _texts(d) if t)


def test_detail_star_and_delete_update_store_and_callbacks(tmp_path):
    from audio_gallery import AudioDetail
    a, b = _audio(tmp_path, "a"), _audio(tmp_path, "b", created="2026-10-28T09:00:00")
    ms.add(a); ms.add(b)
    d = AudioDetail()
    starred, deleted = [], []
    d.on_starred = lambda i, s: starred.append((i, s))
    d.on_deleted = deleted.append
    d.show_record("a", [a, b])
    d._star_btn.set_active(True)
    assert starred == [("a", True)] and ms.get("a").starred == 1
    d._delete_confirmed_for("a")
    assert deleted == ["a"] and ms.get("a") is None and ms.get("b") is not None
    assert d.current_id() == "b"


def test_convert_library_leaves_audio_rows_alone(tmp_path):
    import library_convert as lc
    from media_store import _SCHEMA
    db = tmp_path / "media.db"
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    rec = _audio(tmp_path, thumb=True)
    conn.execute("INSERT INTO media (id,media_type,created_at,file_path,thumbnail_path,prompt,model_id,"
                 "generator_type,params,starred) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (rec.id, rec.media_type, rec.created_at, rec.file_path, rec.thumbnail_path, rec.prompt,
                  rec.model_id, rec.generator_type, rec.params, rec.starred))
    conn.commit()
    row_before = conn.execute("SELECT * FROM media").fetchall()
    thumb_bytes = Path(rec.thumbnail_path).read_bytes()
    conn.close()
    assert lc.analyze(db) == {"fold_pending": 0, "thumbs_pending": 0}
    report = lc.apply(db)
    assert report["folded"] == 0 and report["thumbs_regenerated"] == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT * FROM media").fetchall() == row_before
    conn.close()
    assert Path(rec.thumbnail_path).read_bytes() == thumb_bytes


def test_music_source_selects_the_audio_page_not_video():
    import main_window as mw
    assert mw._gallery_page_for_source("music") == "audio"
    assert mw._gallery_page_for_source("audio") == "audio"
    assert mw._gallery_page_for_source("artgen") == "artgen"
    assert mw._gallery_page_for_source("video") == "video"


def test_sync_gallery_to_source_music_shows_audio_page_and_unknown_falls_back():
    import main_window as mw

    class _Stack:
        def __init__(self): self.pages = {"video", "audio", "artgen"}; self.shown = None
        def get_child_by_name(self, n): return object() if n in self.pages else None
        def set_visible_child_name(self, n): self.shown = n

    class _Fake:
        _gallery_stack = _Stack()
        menu = None
        def _uncheck_pipelines_toggle_if_active(self): pass
        def _rebuild_context_menu(self, s): self.menu = s
        def _reset_detail_split(self): pass

    f = _Fake()
    mw.MainWindow._sync_gallery_to_source(f, "music")
    assert (f._gallery_stack.shown, f.menu) == ("audio", "music")
    mw.MainWindow._sync_gallery_to_source(f, "nonesuch")
    assert (f._gallery_stack.shown, f.menu) == ("video", "video")
