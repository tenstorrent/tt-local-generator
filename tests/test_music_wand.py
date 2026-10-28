"""Tests for app/music_prompts/wand.py and menu.py.

Rule tests use a fixture library (12 cards, 5 families) written to a tmp dir, so
a shipped-card edit never changes what a rule test means.
"""
import dataclasses
import random
import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from music_prompts import cards as C
from music_prompts import MenuGroup, MenuItem, menu_tree, translate, wand
wand_mod = sys.modules["music_prompts.wand"]
from music_prompts.translate import translate_cards
from music_runtime import profile_for

MINIMAX = profile_for({"status": "ok", "model": "fiona-crapple-merged"})
STABLE = profile_for({"status": "ok", "model": "stable-audio-3 small-music"})
UNKNOWN = profile_for({"status": "ok", "model": "mystery-synth"})
PROFILES = (MINIMAX, STABLE, UNKNOWN)
ARTIST_NAMES = ("Drifters", "Low Harbor Sessions", "Night Tide", "Harbor Lamps")


def _card(id, **kw):
    d = {"id": id, "kind": "style", "name": id.title(), "family": "fam", "sound": f"A {id} sound. Second sentence."}
    d.update(kw)
    return d


def _lib(tmp_path, *raw):
    d = tmp_path / f"cards{len(list(tmp_path.glob('cards*')))}"
    d.mkdir()
    (d / "c.yaml").write_text(yaml.safe_dump(list(raw)), encoding="utf-8")
    return C.load_library(shipped_dir=d, user_dir=tmp_path / "none")


def _fixture(tmp_path):
    return _lib(
        tmp_path,
        _card("slowcore", family="rock", sound="Slow, quiet rock with sparse guitar. It is hushed.",
              tempo_bpm=[60, 80], production="Dry and close.", inspiration=["Night Tide", "Harbor Lamps"],
              clashes_with=["techno"], goes_with=["folk"]),
        _card("drifters", kind="artist", parent="slowcore", family="rock", name="Drifters",
              sound="Hushed guitar in the manner of Drifters. Slow pulse.", inspiration=["Drifters", "Low Harbor Sessions"]),
        _card("techno", family="electronic", sound="Pounding four-on-the-floor synths.", tempo_bpm=[125, 135],
              production="Compressed and loud."),
        _card("ambient", family="electronic", sound="Slow evolving synth pads."),
        _card("bluegrass", family="folk", sound="Banjo and fiddle drive a fast strum.", tempo_bpm=[100, 160]),
        _card("choir", kind="vocal", family="folk", name="Choir vocal", aliases=["choir"],
              sound="A hushed small choir.", vocal="a hushed small choir singing in unison",
              lyric_themes=["snow on the roof", "the last train"], lead_instrument="bowed saw"),
        _card("opera", kind="vocal", family="classical", name="Opera vocal", aliases=["opera"],
              sound="A soaring soprano.", vocal="a soaring soprano", lyric_themes=["a lost king"],
              clashes_with=["slowcore"]),
        _card("baroque", family="classical", sound="Harpsichord counterpoint."),
        _card("jazz", family="jazz", sound="Swung brushed drums and walking bass.", tempo_bpm=[90, 140]),
        _card("rain", kind="scene", family="nature", name="Rain", sound="Steady rain on a window."),
        _card("hiss", kind="texture", family="texture", name="Tape hiss", sound="A soft bed of tape hiss."),
        _card("cello", kind="instrument", family="strings", name="Cello", sound="A warm solo cello."),
    )


def _vocal_ids(lib):
    return {c.id for c in lib.cards.values() if c.kind == "vocal"}


# ── wand ─────────────────────────────────────────────────────────────────────

def test_wand_is_repeatable_with_a_seed_and_differs_without(tmp_path):
    lib = _fixture(tmp_path)
    a = wand(MINIMAX, seed=7, library=lib)
    b = wand(MINIMAX, seed=7, library=lib)
    assert a == b
    assert len({wand(MINIMAX, seed=s, library=lib).prompt for s in range(30)}) > 5
    assert len({wand(MINIMAX, library=lib).seed_used for _ in range(20)}) > 5


def test_wand_picks_at_least_two_families(tmp_path):
    lib = _fixture(tmp_path)
    for seed in range(100):
        t = wand(MINIMAX, seed=seed, library=lib)
        main = [lib.cards[i] for i in t.used_cards if lib.cards[i].kind in ("style", "artist", "era", "instrument", "vocal")]
        assert len(main) >= 2 and len({c.family for c in main}) == len(main), (seed, t.used_cards)


def test_wand_never_picks_a_clashes_with_pair(tmp_path):
    lib = _fixture(tmp_path)
    seen = set()
    for seed in range(200):
        used = [lib.cards[i] for i in wand(MINIMAX, seed=seed, library=lib).used_cards]
        seen.add(tuple(c.id for c in used))
        for a in used:
            for b in used:
                if a is not b:
                    assert not wand_mod.clashes(a, b), (seed, a.id, b.id)
                    # by id and by family, both directions, checked independently of the helper
                    assert b.id not in a.clashes_with and b.family not in a.clashes_with
    assert len(seen) > 10


def test_wand_on_a_no_lyrics_model_never_includes_a_vocal_card(tmp_path):
    lib = _fixture(tmp_path)
    vocals = _vocal_ids(lib)
    for seed in range(200):
        t = wand(STABLE, seed=seed, library=lib)
        assert not vocals & set(t.used_cards), seed
        assert "choir" not in t.prompt.lower() and "singing" not in t.prompt.lower(), t.prompt
        assert t.lyrics is None
    for seed in range(50):                      # a profile of no model at all cannot sing either
        assert not vocals & set(wand(None, seed=seed, library=lib).used_cards)
    assert any(vocals & set(wand(MINIMAX, seed=s, library=lib).used_cards) for s in range(100))


def test_wand_with_base_keeps_the_users_words_and_adds_one_layer(tmp_path):
    lib = _fixture(tmp_path)
    for seed in range(60):
        t = wand(STABLE, seed=seed, base="rainy Tuesday slowcore", library=lib)
        assert "rainy Tuesday" in t.prompt, t.prompt
        assert t.used_cards[0] == "slowcore" and len(t.used_cards) == 2
        added = lib.cards[t.used_cards[1]]
        assert added.family != "rock" and not wand_mod.clashes(added, lib.cards["slowcore"])
    # a base with no card keeps its words and still gets one card
    t = wand(STABLE, seed=1, base="very slow and lonely", library=lib)
    assert "very slow and lonely" in t.prompt and len(t.used_cards) == 1


def test_wand_always_non_empty_even_with_a_one_card_library(tmp_path):
    lib = _lib(tmp_path, _card("solo", name="Solo", sound="A lone sound."))
    for profile in PROFILES:
        t = wand(profile, seed=3, library=lib)
        assert t.prompt.strip() and t.used_cards == ("solo",)
        assert any("Only one style available" in n for n in t.notes)
    t = wand(MINIMAX, seed=3, base="a walk", library=lib)
    assert "a walk" in t.prompt.lower() and not any("'with'" in n for n in t.notes)


def test_wand_respects_the_length_limit(tmp_path):
    lib = _fixture(tmp_path)
    for profile in PROFILES:
        small = dataclasses.replace(profile, max_chars=120)
        for seed in range(40):
            t = wand(small, seed=seed, library=lib)
            assert t.prompt.strip() and len(t.prompt) <= 120, (len(t.prompt), t.prompt)


def test_wand_output_has_no_artist_names(tmp_path):
    lib = _fixture(tmp_path)
    saw_artist = False
    for profile in PROFILES:
        for seed in range(200):
            t = wand(profile, seed=seed, library=lib)
            saw_artist |= "drifters" in t.used_cards
            for text in (t.prompt, t.lyrics or "", " ".join(t.notes)):
                for name in ARTIST_NAMES:
                    assert name.lower() not in text.lower(), (profile.label, seed, name, text)
    assert saw_artist


def test_wand_equals_translate_for_the_same_cards(tmp_path):
    lib = _fixture(tmp_path)
    checked = 0
    for profile in PROFILES:
        for seed in range(60):
            t = wand(profile, seed=seed, library=lib)
            cards = [lib.cards[i] for i in t.used_cards]
            typed = cards[0].name
            for c in cards[1:]:
                typed += (" and someone is singing " if c.kind == "vocal" else " with ") + c.name
            want = translate(typed, profile, library=lib)
            strip = lambda tr: dataclasses.replace(            # noqa: E731
                tr, seed_used=None, notes=tuple(n for n in tr.notes if "became the description" not in n))
            assert strip(t) == strip(want), (seed, typed)
            checked += 1
    assert checked == 180
    # the shared path itself: translate_cards on parsed-equivalent input
    assert translate_cards(("slowcore",), [("with", ("rain",))], "", MINIMAX, library=lib).prompt == \
        translate("Slowcore with Rain", MINIMAX, library=lib).prompt


def test_wand_seed_used_is_reported_and_reproduces_the_result(tmp_path):
    lib = _fixture(tmp_path)
    first = wand(MINIMAX, library=lib)
    assert isinstance(first.seed_used, int)
    again = wand(MINIMAX, seed=first.seed_used, library=lib)
    assert again == first
    assert wand(MINIMAX, seed=5, library=lib).seed_used == 5


def test_wand_does_not_touch_global_random_state(tmp_path):
    lib = _fixture(tmp_path)
    random.seed(1234)
    state = random.getstate()
    wand(MINIMAX, seed=9, library=lib)
    wand(MINIMAX, library=lib, base="slowcore")
    assert random.getstate() == state


def test_wand_on_the_shipped_library_returns_a_prompt_for_every_dialect(tmp_path):
    lib = C.load_library(user_dir=tmp_path / "no-user-cards")
    for profile in PROFILES:
        for seed in range(10):
            t = wand(profile, seed=seed, library=lib)
            assert t.prompt.strip() and t.dialect and t.seed_used == seed
            assert "Tortoise" not in t.prompt and "Bill Monroe" not in t.prompt


def test_wand_goes_with_raises_the_pairing_probability(tmp_path):
    lib = _lib(
        tmp_path,
        _card("a", family="fa", goes_with=["b"]),
        _card("b", family="fb"),
        _card("c", family="fc"),
        _card("d", family="fd"),
        _card("e", family="fe"),
    )
    pair = lambda t, x, y: {x, y} <= set(t.used_cards)             # noqa: E731
    runs = [wand(STABLE, seed=s, library=lib) for s in range(300)]
    linked = sum(pair(t, "a", "b") for t in runs)
    neutral = sum(pair(t, "a", "c") for t in runs)
    assert linked > neutral * 1.3, (linked, neutral)
    assert neutral > 0                                              # never required, never exclusive


def test_wand_on_an_empty_library_says_so(tmp_path):
    t = wand(MINIMAX, seed=1, library=C.Library())
    assert t.prompt == "" and t.notes and t.seed_used == 1


# ── menu ─────────────────────────────────────────────────────────────────────

def test_menu_tree_groups_by_kind_and_family(tmp_path):
    tree = menu_tree(_fixture(tmp_path))
    titles = [g.title for g in tree]
    assert titles == ["Classical", "Electronic", "Folk", "Jazz", "Rock", "Scenes", "Instruments", "Vocals", "Textures"]
    assert all(isinstance(g, MenuGroup) and all(isinstance(i, MenuItem) for i in g.items) for g in tree)
    by = {g.title: [i.card_id for i in g.items] for g in tree}
    assert by["Electronic"] == ["ambient", "techno"]
    assert by["Vocals"] == ["choir", "opera"] and by["Scenes"] == ["rain"]
    assert tree == menu_tree(_fixture(tmp_path))                    # deterministic


def test_menu_tree_artist_cards_sit_under_their_parent_style(tmp_path):
    rock = next(g for g in menu_tree(_fixture(tmp_path)) if g.title == "Rock")
    assert [(i.card_id, i.label, i.parent_id) for i in rock.items] == [
        ("slowcore", "Slowcore", None), ("drifters", "Drifters", "slowcore")]
    all_ids = [i.card_id for g in menu_tree(_fixture(tmp_path)) for i in g.items]
    assert all_ids.count("drifters") == 1


def test_menu_tips_never_contain_an_artist_name(tmp_path):
    items = [i for g in menu_tree(_fixture(tmp_path)) for i in g.items]
    for i in items:
        for name in ARTIST_NAMES:
            assert name.lower() not in i.tip.lower(), (i.card_id, i.tip)
    tips = {i.card_id: i.tip for i in items}
    assert tips["slowcore"] == "Slow, quiet rock with sparse guitar."
    assert tips["drifters"] == "Slow pulse."                        # the sentence naming the artist is skipped


def test_menu_tree_on_empty_library():
    assert menu_tree(C.Library()) == []


def test_menu_tree_includes_user_cards(tmp_path):
    shipped = tmp_path / "s"
    shipped.mkdir()
    (shipped / "a.yaml").write_text(yaml.safe_dump([_card("one", family="rock")]), encoding="utf-8")
    user = tmp_path / "u"
    user.mkdir()
    (user / "b.yaml").write_text(yaml.safe_dump([_card("mine", family="mystic")]), encoding="utf-8")
    tree = menu_tree(C.load_library(shipped_dir=shipped, user_dir=user))
    assert {g.title for g in tree} == {"Rock", "Mystic"}
