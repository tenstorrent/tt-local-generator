"""Tests for the second batch of shipped style cards (Task 6) and the wand's variety.

These read the real shipped cards in app/music_prompts/cards/. They check that
the library is big and broad enough for the wand to surprise, that the wand's
picks vary from seed to seed and reach every family, that no artist name ever
leaks into a wand prompt (Review Focus 1), and that every alias of every card
renders cleanly in all three dialects.

Tests never read the real home directory: every library is loaded with a user
folder that does not exist.
"""
import logging
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).parent))

from music_prompts import cards as C
from music_prompts import translate, wand
wand_module = sys.modules["music_prompts.wand"]
from music_prompts.parse import parse
from test_music_shipped_cards import (MINIMAX, PROFILES, STABLE, VOICE_WORDS, _all_names, _contains_name,
                                      _publishers, _shows)

# The second batch (Task 6). Each must name two distinct publishers in `notes`
# unless it is listed in UNVERIFIED (the review sheet lists those first).
NEW_CARDS = {
    "dub", "shoegaze", "math-rock", "afrobeat", "bossa-nova", "surf-rock", "spaghetti-western", "gamelan",
    "celtic-folk", "appalachian-old-time", "ambient-drone", "vaporwave", "italo-disco", "trip-hop", "hyperpop",
    "big-band-swing", "gospel-choir", "mellotron-strings", "theremin", "steel-drums", "harp", "cello-quartet",
    "rain-on-a-tin-roof", "lighthouse-at-dusk", "city-street-at-night", "tape-hiss",
}
UNVERIFIED: set[str] = set()
DEBRIS = re.compile(r"\band with\b|\bwith and\b|,\s*,|\band and\b|\s[,.;]", re.I)
DOUBLED = re.compile(r"\b(\w+) \1\b", re.I)


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    return C.load_library(user_dir=tmp_path_factory.mktemp("no-user-cards") / "none")


def _families(lib, t):
    return frozenset(lib.card(cid).family for cid in t.used_cards)


# ── size and breadth ─────────────────────────────────────────────────────────

def test_library_has_at_least_forty_cards_in_at_least_ten_families(lib):
    assert len(lib.cards) >= 40, len(lib.cards)
    assert len(lib.families()) >= 10, lib.families()
    assert NEW_CARDS <= set(lib.cards), sorted(NEW_CARDS - set(lib.cards))


def test_all_shipped_cards_still_load_with_no_log_warnings(tmp_path, caplog):
    with caplog.at_level(logging.DEBUG, logger=C.log.name):
        lib = C.load_library(user_dir=tmp_path / "none")
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert len(lib.cards) >= 40


def test_goes_with_and_clashes_with_reference_real_ids_or_families(lib):
    families = set(lib.families())
    for c in lib.cards.values():
        for ref in c.goes_with + c.clashes_with:
            assert ref in lib.cards or ref in families, (c.id, ref)
        # a card never both favours and avoids the same thing
        assert not set(c.goes_with) & set(c.clashes_with), c.id
    # clashes are kept rare: most cards clash with nothing
    assert sum(1 for c in lib.cards.values() if c.clashes_with) <= len(lib.cards) // 4


def test_new_cards_have_two_distinct_publishers(lib):
    for cid in sorted(NEW_CARDS):
        notes = lib.card(cid).notes.strip()
        if cid in UNVERIFIED:
            assert notes == "unverified", cid
            continue
        pubs = _publishers(notes)
        assert pubs is not None, (cid, "each source must read 'Publisher: title' or be a URL", notes)
        assert len(set(pubs)) >= 2, (cid, "two distinct publishers needed", pubs)


def test_new_card_sounds_leave_room_for_the_wand_layers(lib):
    """A `sound` over 200 characters fills the 450-character caption budget and pushes the layers out."""
    for cid in sorted(NEW_CARDS):
        assert len(lib.card(cid).sound) <= 200, (cid, len(lib.card(cid).sound))


# ── the wand ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("profile", [MINIMAX, STABLE], ids=["minimax", "stable-audio"])
def test_wand_produces_thirty_distinct_prompts_in_fifty_seeds(lib, profile):
    prompts = {wand(profile, seed=s, library=lib).prompt for s in range(1, 51)}
    assert len(prompts) >= 30, len(prompts)


def test_no_two_adjacent_wand_seeds_share_the_same_family_set(lib):
    # Fix round 2: every scene card is in the one "scene" family, and a scene is now the layer about a
    # quarter of the time, so two neighbouring seeds can both be "<family> + a scene". The spot check
    # allows two such pairs in 39, and two neighbours never pick the same cards.
    runs = [wand(MINIMAX, seed=s, library=lib) for s in range(1, 41)]
    fams = [_families(lib, t) for t in runs]
    same = [(s, sorted(a)) for s, (a, b) in enumerate(zip(fams, fams[1:]), start=1) if a == b]
    assert len(same) <= 2, same
    assert not any(set(a.used_cards) == set(b.used_cards) for a, b in zip(runs, runs[1:]))


def test_no_artist_name_in_any_wand_output_across_200_seeds_and_all_dialects(lib):
    names = _all_names(lib)
    for s in range(1, 201):
        for dialect, profile in PROFILES:
            t = wand(profile, seed=s, library=lib)
            for text in (t.prompt, t.lyrics or ""):
                for n in names:
                    assert not _contains_name(text, n), (s, dialect, n, text)


def test_every_family_is_reachable_by_the_wand(lib):
    seen = set()
    for s in range(1, 401):
        seen |= _families(lib, wand(MINIMAX, seed=s, library=lib))
    assert seen == set(lib.families()), sorted(set(lib.families()) - seen)


def test_wand_on_stable_audio_keeps_a_tag_from_every_used_card(lib):
    for s in range(1, 61):
        t = wand(STABLE, seed=s, library=lib)
        missing = [cid for cid in t.used_cards if not _shows(lib.card(cid), t.prompt, lib)]
        assert not missing, (s, missing, t.prompt)


def test_wand_prompts_are_clean(lib):
    """No doubled words, no list debris, nothing past the length limit, over 100 seeds."""
    for s in range(1, 101):
        for dialect, profile in PROFILES:
            p = wand(profile, seed=s, library=lib).prompt
            assert not DOUBLED.search(p), (s, dialect, p)
            assert not DEBRIS.search(p), (s, dialect, DEBRIS.search(p).group(), p)
            assert len(p) <= 1000, (s, dialect)


# ── every alias, every dialect ───────────────────────────────────────────────

def test_every_card_is_reachable_by_a_typed_alias_and_translates_in_all_dialects(lib):
    names = _all_names(lib)
    for c in lib.cards.values():
        for alias in (c.name, *c.aliases):
            p = parse(alias, lib)
            matches = [m for layer in p.layers for m in layer.matches]
            assert any(m.card_id == c.id or c.id in m.alternatives for m in matches), (c.id, alias, matches)
            # an ambiguous one-word alias needs a signal in a sentence: a capital letter is one
            mid = alias.capitalize() if c.ambiguous and len(alias.split()) == 1 else alias
            for text in (alias, f"{alias}-like song", f"instrumental {mid} piece"):
                for dialect, profile in PROFILES:
                    t = translate(text, profile, library=lib)
                    assert t.prompt.strip() and t.used_cards, (c.id, text, dialect)
                    for n in names:
                        assert not _contains_name(t.prompt, n), (c.id, text, dialect, n, t.prompt)
                    if t.instrumental and not VOICE_WORDS.search(alias):
                        assert not VOICE_WORDS.search(t.prompt), (c.id, text, dialect, t.prompt)
                    assert not DOUBLED.search(t.prompt), (c.id, text, dialect, t.prompt)
                    assert not DEBRIS.search(t.prompt), (c.id, text, dialect, t.prompt)


def test_no_voice_words_in_any_instrumental_prompt(lib):
    """The wand on a model that cannot sing, and every instrumental request, describe no voice."""
    for s in range(1, 101):
        t = wand(STABLE, seed=s, library=lib)
        assert t.instrumental and not VOICE_WORDS.search(t.prompt), (s, t.prompt)
    for c in lib.cards.values():
        name = next(n for n in (c.name, *c.aliases) if not VOICE_WORDS.search(n))
        for profile in (MINIMAX, STABLE, None):
            t = translate(f"instrumental {name}-like piece", profile, library=lib)
            assert not VOICE_WORDS.search(t.prompt), (c.id, t.dialect, t.prompt)


def test_ambiguous_aliases_need_a_signal_in_a_sentence(lib):
    """Ordinary-word names in the second batch do not fire inside everyday sentences."""
    for text in ("we will dub it later", "the gospel truth about prices", "a swing in the park",
                 "she kept her celtic cross"):
        assert translate(text, MINIMAX, library=lib).used_cards == (), text
    for text, want in (("a dub track", "dub"), ("big band swing", "big-band-swing"), ("swing music", "big-band-swing"),
                       ("a gospel choir", "gospel-choir"), ("celtic fiddle tunes", "celtic-folk"),
                       ("steelpan", "steel-drums"), ("bossa nova", "bossa-nova"), ("Mellotron", "mellotron-strings")):
        assert translate(text, MINIMAX, library=lib).used_cards[:1] == (want,), text


# ── fix round 1: the mix described is the mix delivered ─────────────────────

def _opening(text, n=5):
    return " ".join(text.lower().split()[:n]).rstrip(",.;")


@pytest.mark.parametrize("profile", [MINIMAX, STABLE, None], ids=["minimax", "stable-audio", "generic"])
def test_wand_reports_exactly_the_cards_it_renders(lib, profile):
    for s in range(1, 61):
        t = wand(profile, seed=s, library=lib)
        if profile is not None:     # the generic 300-character budget often fits only the base card
            assert len(t.used_cards) >= 2, (s, t.used_cards, t.notes)
        for cid in t.used_cards:
            card = lib.card(cid)
            if profile is STABLE:
                assert _shows(card, t.prompt, lib), (s, cid, t.prompt)
            else:
                # caption: the whole sound; generic: its first sentence. Five opening words are distinctive.
                text = card.vocal if card.kind == "vocal" and card.vocal else card.sound   # a vocal card prints its voice
                assert _opening(text) in t.prompt.lower(), (s, cid, t.prompt)
        if profile is not STABLE:                   # and nothing rendered goes unreported
            for c in lib.cards.values():
                if c.id not in t.used_cards and c.kind != "vocal":
                    assert _opening(c.sound) not in t.prompt.lower(), (s, c.id, t.prompt)
        assert not any(n.startswith("Left out to fit the length:") and any(
            lib.card(cid).name.lower() in n.lower() for cid in t.used_cards) for n in t.notes), (s, t.notes)


def test_wand_is_deterministic_per_seed(lib):
    for profile in (MINIMAX, STABLE, None):
        for s in (1, 7, 42, 99):
            a, b = wand(profile, seed=s, library=lib), wand(profile, seed=s, library=lib)
            assert (a.prompt, a.used_cards, a.notes, a.lyrics) == (b.prompt, b.used_cards, b.notes, b.lyrics)


def test_stable_audio_wand_keeps_at_most_twelve_tags(lib):
    from music_prompts.dialects import TAGS_CAP
    for s in range(1, 61):
        t = wand(STABLE, seed=s, library=lib)
        assert len([x for x in t.prompt.split(", ") if x]) <= TAGS_CAP, (s, t.prompt)


@pytest.mark.parametrize("profile", [MINIMAX, STABLE], ids=["minimax", "stable-audio"])
def test_no_single_card_dominates_the_wand(lib, profile):
    """A card (the bagpipes scene was a worry) appears in at most a quarter of 60 wand prompts."""
    from collections import Counter
    seen = Counter(cid for s in range(1, 61) for cid in wand(profile, seed=s, library=lib).used_cards)
    assert max(seen.values()) <= 15, seen.most_common(5)


def test_beatless_drone_never_meets_a_groove_in_the_wand(lib):
    groove = {"afrobeat", "italo-disco", "funk", "p-funk", "parliament", "funkadelic", "big-band-swing",
              "hyperpop", "chiptunes"}
    for s in range(1, 401):
        used = set(wand(MINIMAX, seed=s, library=lib).used_cards)
        assert not ("ambient-drone" in used and used & groove), (s, used)


# ── fix round 2: scenes and textures are real wand picks ────────────────────

COLOUR = {"scene", "texture"}
# Expected share: the layer is a scene 25% or a texture 10% of the time, and an optional third card
# (about 35% of picks) adds more, so about 21-25 of 60 prompts. 15 leaves room for seed noise.
MIN_COLOUR_PROMPTS = 15


@pytest.mark.parametrize("profile", [MINIMAX, STABLE], ids=["minimax", "stable-audio"])
def test_scenes_and_textures_appear_in_wand_prompts(lib, profile):
    n = sum(any(lib.card(c).kind in COLOUR for c in wand(profile, seed=s, library=lib).used_cards)
            for s in range(1, 61))
    assert n >= MIN_COLOUR_PROMPTS, n


def test_wand_draws_the_layer_by_kind_weights():
    w = wand_module.layer_kind_weights(can_sing=True, available={"style", "scene", "instrument", "texture", "vocal"})
    assert w == {"style": 45, "scene": 25, "instrument": 15, "texture": 10, "vocal": 5}
    w = wand_module.layer_kind_weights(can_sing=False, available={"style", "scene", "instrument", "texture", "vocal"})
    assert w == {"style": 50, "scene": 25, "instrument": 15, "texture": 10}
    assert wand_module.layer_kind_weights(can_sing=True, available={"style", "scene"}) == {"style": 45, "scene": 25}


def test_a_scene_layer_outlasts_a_second_style_layer_when_trimming():
    from music_prompts import dialects as D
    style = D.Part("sound", "Funk", "Tight funk. More words here.", card_id="funk", card_name="Funk", card_kind="style")
    scene = D.Part("sound", "Rain on a roof", "Rain on a roof, pinging. A second sentence about gutters.",
                   card_id="rain", card_name="Rain on a roof", card_kind="scene")
    plan = D.Plan(user_words=(), base=(D.Part("sound", "Bebop", "Fast jazz.", card_id="bebop", card_name="Bebop"),),
                  layers=(("with", (style,)), ("with", (scene,))))   # the scene is shown last
    clauses, _ = D.compose(plan, D.CAPTION)
    full = D._join(clauses, D.CAPTION)
    kept, notes = D.trim(clauses, D.CAPTION, 1000, budget=len(full) - 10)
    out = D._join(kept, D.CAPTION)
    assert "pinging" in out and "Tight funk" not in out, out            # the style layer went first
    kept, notes = D.trim(clauses, D.CAPTION, 1000, budget=len("Bebop: fast jazz. Alongside it, rain on a roof, pinging.") + 2)
    out = D._join(kept, D.CAPTION)
    assert "pinging" in out and "gutters" not in out, out               # shortened to its first sentence first
