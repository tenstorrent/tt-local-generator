"""Tests for app/music_prompts/parse.py (the alias matcher and layer splitter).

The rule tests use a small fixture library written to a tmp dir, not the
shipped cards, so a card edit never changes what a rule test means.
"""
import time

import pytest
import yaml

from music_prompts import cards as C
from music_prompts.parse import LAYER_MARKERS, Layer, Match, Parsed, parse


def _card(id, **kw):
    d = {"id": id, "kind": "style", "name": id, "family": "fam", "sound": "A plain sound."}
    d.update(kw)
    return d


def _lib(tmp_path, *raw_cards):
    d = tmp_path / f"cards{len(list(tmp_path.glob('cards*')))}"
    d.mkdir()
    (d / "c.yaml").write_text(yaml.safe_dump(list(raw_cards)), encoding="utf-8")
    return C.load_library(shipped_dir=d, user_dir=tmp_path / "none")


def _funk(tmp_path):
    return _lib(
        tmp_path,
        _card("parliament", name="Parliament"),
        _card("funkadelic", name="Funkadelic"),
        _card("p-funk", name="Parliament-Funkadelic", aliases=["P-Funk"]),
        _card("jazz", name="Jazz"),
    )


def _tortoise(tmp_path):
    return _lib(
        tmp_path,
        _card("tortoise", kind="artist", parent="post-rock", name="Tortoise",
              ambiguous=True, cues=["band", "album", "record", "music video"]),
        _card("post-rock", name="post-rock"),
        _card("bluegrass", kind="vocal", name="Bluegrass", aliases=["bluegrass song"]),
    )


def _ids(layer):
    return [m.card_id for m in layer.matches]


def test_example_sentence_splits_into_base_and_vocal_layer(tmp_path):
    lib = _lib(
        tmp_path,
        _card("tortoise", name="Tortoise"),
        _card("bluegrass", kind="vocal", name="Bluegrass", aliases=["bluegrass song"]),
    )
    p = parse("tortoise-like song but someone is singing a bluegrass song on top", lib)
    assert [l.role for l in p.layers] == ["base", "layer"]
    base, vocal = p.layers
    assert _ids(base) == ["tortoise"]
    assert base.marker is None and not base.vocal
    assert _ids(vocal) == ["bluegrass"]
    assert vocal.marker == "but" and vocal.vocal
    assert "song" in p.leftover


def test_example_sentence_on_the_shipped_library():
    lib = C.load_library(user_dir=C._USER_DIR / "does-not-exist")
    p = parse("tortoise-like song but someone is singing a bluegrass song on top", lib)
    assert _ids(p.layers[0]) == ["tortoise"]
    assert [i for l in p.layers[1:] for i in _ids(l)] == ["bluegrass-vocal"]
    assert p.layers[1].vocal


@pytest.mark.parametrize("text", ["Tortoise", "tortoise", "TORTOISE", "tortoise-ish",
                                  "like Tortoise", "Tortoise-like", "tortoise style",
                                  "tortoise-style"])
def test_alias_matching_ignores_case_and_hyphens(tmp_path, text):
    lib = _lib(tmp_path, _card("tortoise", name="Tortoise"))
    p = parse(text, lib)
    assert _ids(p.layers[0]) == ["tortoise"]
    assert p.leftover == ()  # the like/ish/style words belong to the match


def test_hyphenated_alias_matches_spaced_and_vice_versa(tmp_path):
    lib = _lib(tmp_path, _card("pr", name="post-rock", aliases=["postrock"]))
    assert _ids(parse("some post rock", lib).layers[0]) == ["pr"]
    assert _ids(parse("POST-ROCK!", lib).layers[0]) == ["pr"]


def test_longest_match_wins(tmp_path):
    lib = _funk(tmp_path)
    for text in ("Parliament-Funkadelic", "parliament funkadelic", "P-Funk"):
        p = parse(text, lib)
        assert _ids(p.layers[0]) == ["p-funk"], text
    p = parse("Parliament and Funkadelic", lib)
    assert _ids(p.layers[0]) == ["parliament", "funkadelic"]


def test_longest_match_wins_over_a_contained_shorter_alias(tmp_path):
    p = parse("a Parliament-Funkadelic groove", _funk(tmp_path))
    m = p.layers[0].matches
    assert [x.card_id for x in m] == ["p-funk"]
    assert m[0].text == "Parliament-Funkadelic"


def test_ambiguous_word_without_cue_does_not_match(tmp_path):
    lib = _tortoise(tmp_path)
    for text in ("the tortoise crossed the road", "Tortoise crossed the road",
                 "a slow tortoise on the path"):
        p = parse(text, lib)
        assert all(not l.matches for l in p.layers), text
        assert p.leftover == (text,)


def test_ambiguous_word_with_cue_or_capital_matches(tmp_path):
    lib = _tortoise(tmp_path)
    for text in ("a Tortoise record", "a tortoise record", "I like Tortoise",
                 "tortoise-like", "tortoise style", "we saw a Tortoise tonight",
                 "the tortoise music video", "album by tortoise"):
        p = parse(text, lib)
        assert _ids(p.layers[0]) == ["tortoise"], text


def test_ambiguous_cue_must_be_within_five_words(tmp_path):
    lib = _tortoise(tmp_path)
    near = parse("the tortoise one two three four album", lib)
    far = parse("the tortoise one two three four five album", lib)
    assert _ids(near.layers[0]) == ["tortoise"]
    assert not far.layers[0].matches


def test_ambiguous_alias_inside_a_longer_alias_matches(tmp_path):
    lib = _lib(tmp_path, _card("sea", name="Sea", ambiguous=True, cues=["band"]),
               _card("seacake", name="The Sea and Cake"))
    p = parse("the sea and cake", lib)
    assert _ids(p.layers[0]) == ["seacake"]


def test_marker_inside_an_alias_is_not_a_split(tmp_path):
    lib = _lib(tmp_path, _card("seacake", name="The Sea and Cake"))
    p = parse("The Sea and Cake", lib)
    assert len(p.layers) == 1 and _ids(p.layers[0]) == ["seacake"]
    # "and someone is singing" is a marker; a plain "and" is not, and "with"
    # inside an alias is not one either.
    lib2 = _lib(tmp_path, _card("sw", name="Sweet with Sour"))
    p2 = parse("sweet with sour but loud", lib2)
    assert [l.role for l in p2.layers] == ["base", "layer"]
    assert _ids(p2.layers[0]) == ["sw"] and p2.layers[1].marker == "but"


@pytest.mark.parametrize("marker", LAYER_MARKERS)
def test_each_marker_splits(tmp_path, marker):
    lib = _funk(tmp_path)
    p = parse(f"jazz {marker} parliament-funkadelic", lib)
    assert [l.role for l in p.layers] == ["base", "layer"]
    assert p.layers[1].marker == marker
    assert _ids(p.layers[0]) == ["jazz"]
    assert _ids(p.layers[1]) == ["p-funk"]
    assert p.leftover == ()  # marker words are structure, not user content


def test_marker_matching_is_case_insensitive_and_longest_first(tmp_path):
    p = parse("jazz MIXED WITH funkadelic", _funk(tmp_path))
    assert len(p.layers) == 2 and p.layers[1].marker == "mixed with"


def test_unknown_text_has_no_matches_and_is_all_leftover(tmp_path):
    p = parse("rainy Tuesday afternoon", _funk(tmp_path))
    assert len(p.layers) == 1 and p.layers[0].matches == ()
    assert p.leftover == ("rainy Tuesday afternoon",)
    assert p.text == "rainy Tuesday afternoon"


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_empty_and_whitespace_input(tmp_path, text):
    p = parse(text, _funk(tmp_path))
    assert p.layers == () and p.leftover == ()


def test_unicode_and_punctuation_are_kept_in_leftover(tmp_path):
    p = parse("café – rainy, 5/4 groove!", _funk(tmp_path))
    assert p.leftover == ("café – rainy, 5/4 groove!",)
    p = parse("(naïve) jazz, très chic!", _funk(tmp_path))
    assert _ids(p.layers[0]) == ["jazz"]
    assert p.leftover == ("(naïve)", "très chic!")


def test_two_names_in_one_layer(tmp_path):
    p = parse("jazz and parliament", _funk(tmp_path))
    assert _ids(p.layers[0]) == ["jazz", "parliament"]
    assert len(p.layers) == 1
    assert p.leftover == ("and",)


def test_user_words_order_is_preserved_in_leftover(tmp_path):
    p = parse("rainy jazz Tuesday, slow  parliament   with sticky mornings", _funk(tmp_path))
    assert p.leftover == ("rainy", "Tuesday, slow", "sticky mornings")


def test_leftover_is_the_users_exact_text_not_respaced(tmp_path):
    p = parse("A  Strange   SPACING jazz", _funk(tmp_path))
    assert p.leftover == ("A  Strange   SPACING",)


def test_match_offsets_index_the_original_text(tmp_path):
    text = "Some P-Funk, then Jazz"
    p = parse(text, _funk(tmp_path))
    for m in p.layers[0].matches:
        assert text[m.start:m.end] == m.text
    assert [m.text for m in p.layers[0].matches] == ["P-Funk", "Jazz"]


def test_shared_alias_returns_first_card_and_lists_alternatives(tmp_path):
    lib = _lib(tmp_path, _card("a-funk", name="Funk", family="f1"),
               _card("b-funk", name="Funk", family="f2"))
    m = parse("funk", lib).layers[0].matches[0]
    assert m.card_id == "a-funk" and m.alternatives == ("b-funk",)
    assert Match("x", 0, 1, "x").alternatives == ()


def test_vocal_is_set_by_a_vocal_card_or_singing_words(tmp_path):
    lib = _tortoise(tmp_path)
    assert parse("bluegrass song", lib).layers[0].vocal
    assert parse("jazz with a sung chorus", _funk(tmp_path)).layers[1].vocal
    assert parse("Some VOICE over jazz", _funk(tmp_path)).layers[0].vocal
    assert not parse("jazz", _funk(tmp_path)).layers[0].vocal


@pytest.mark.parametrize("text", ["but loud", "loud but", "but", "on top"])
def test_marker_at_the_start_or_end_of_text_does_not_crash(tmp_path, text):
    p = parse(text, _funk(tmp_path))
    assert isinstance(p, Parsed)
    assert p.layers == () or p.layers[0].role == "base"
    if text == "but loud":
        assert [l.role for l in p.layers] == ["base", "layer"]
        assert p.layers[0].text == "" and p.layers[1].text == "loud"
    if text == "loud but":
        assert [l.text for l in p.layers] == ["loud"]
    assert Layer("base", "", (), None, False).role == "base"


def test_very_long_input_is_linear_enough(tmp_path):
    lib = _funk(tmp_path)
    text = "parliament-funkadelic over slow jazz, rainy mornings but loud. " * 330
    assert len(text) >= 20000
    t = time.perf_counter()
    p = parse(text, lib)
    assert time.perf_counter() - t < 2.0
    assert len(p.layers) > 100
