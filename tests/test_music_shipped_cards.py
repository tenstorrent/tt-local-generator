"""Tests for the shipped style cards (Task 5: the first card set, reviewed by the operator).

These tests read the real shipped cards in app/music_prompts/cards/. They check
that every name in the operator's original request finds its card, that card
text never names an artist, that each card records its sources (or says it is
unverified), that references between cards resolve, and that every card renders
in all three dialects. Rule tests for the parser and translator live in their
own files and use fixture libraries, so a card edit never changes them.

Tests never read the real home directory: every library is loaded with a user
folder that does not exist.
"""
import logging
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from music_prompts import cards as C
from music_prompts import translate, wand
from music_prompts.parse import parse
from music_runtime import profile_for

MINIMAX = profile_for({"status": "ok", "model": "fiona-crapple-merged"})
STABLE = profile_for({"status": "ok", "model": "stable-audio-3 small-music"})
PROFILES = (("caption+lyrics", MINIMAX), ("tags", STABLE), ("generic", None))
EXAMPLE = "tortoise-like song but someone is singing a bluegrass song on top"
_WORD = re.compile(r"[^\W_]+")

# The operator's request (spec, opening prompt), phrased the way it was typed,
# and the card each phrase must find. "Parliament-Funkadelic" must beat the two
# single names (longest match).
OPERATOR_NAMES = {
    "post-rock like Tortoise": ["post-rock", "tortoise"],
    "Tortoise-like": ["tortoise"],
    "Yume Bitsu": ["yume-bitsu"],
    "The Sea and Cake": ["the-sea-and-cake"],
    "jazz with a be-bop flavor": ["bebop"],
    "bebop": ["bebop"],
    "Funk like Parliament": ["funk", "parliament"],
    "Funk like Funkadelic": ["funk", "funkadelic"],
    "Funk like Parliament-Funkadelic": ["funk", "p-funk"],
    "Parliament": ["parliament"],
    "Funkadelic": ["funkadelic"],
    "Parliament-Funkadelic": ["p-funk"],
    "Tangerine Dream": ["tangerine-dream"],
    "Ash Ra Tempel": ["ash-ra-tempel"],
    "Kraftwerk": ["kraftwerk"],
    "Ratcheting synthesizers": ["ratcheting-synths"],
    "Chiptunes": ["chiptunes"],
    "Synthpop": ["synthpop"],
    "The songbook of the grateful dead": ["grateful-dead-songbook"],
    "someone playing an accordion in the moonlight": ["accordion-in-the-moonlight"],
    "someone playing the bag pipes on the beach at sunset": ["bagpipes-on-the-beach-at-sunset"],
    "a bluegrass song": ["bluegrass-vocal"],
}
# Further aliases people type.
MORE_ALIASES = {
    "be-bop": "bebop", "funky": "funk", "P-Funk": "p-funk", "parliament funkadelic": "p-funk",
    "chiptune": "chiptunes", "8-bit music": "chiptunes", "synth-pop": "synthpop",
    "Berlin School": "berlin-school", "krautrock": "krautrock", "motorik": "krautrock",
    "electropop": "electropop", "jam band": "jam-band", "Grateful Dead": "grateful-dead-songbook",
    "moonlit accordion": "accordion-in-the-moonlight", "bag pipes": "bagpipes-on-the-beach-at-sunset",
    "bagpipes on the beach": "bagpipes-on-the-beach-at-sunset", "Sea and Cake": "the-sea-and-cake",
    # second batch (Task 6)
    "dub reggae": "dub", "shoegaze": "shoegaze", "math rock": "math-rock", "afrobeat": "afrobeat",
    "bossa nova": "bossa-nova", "surf rock": "surf-rock", "spaghetti western": "spaghetti-western",
    "gamelan": "gamelan", "irish folk": "celtic-folk", "old-time music": "appalachian-old-time",
    "drone music": "ambient-drone", "vaporwave": "vaporwave", "italo disco": "italo-disco", "trip-hop": "trip-hop",
    "hyperpop": "hyperpop", "big band": "big-band-swing", "gospel choir": "gospel-choir",
    "mellotron": "mellotron-strings", "theremin": "theremin", "steelpan": "steel-drums", "concert harp": "harp",
    "four cellos": "cello-quartet", "rain on a tin roof": "rain-on-a-tin-roof", "lighthouse at dusk": "lighthouse-at-dusk",
    "city street at night": "city-street-at-night", "cassette hiss": "tape-hiss",
}
# Card kinds that may leave out a tempo (spec 4.2: scenes and instruments may omit it).
TEMPO_OPTIONAL_KINDS = {"scene", "instrument", "texture", "vocal"}
# Cards that are instrumental forms by definition. None of the first set is:
# "instrumental" comes from the user's request or the dialect, never from a card.
INSTRUMENTAL_FORMS: set[str] = set()


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    return C.load_library(user_dir=tmp_path_factory.mktemp("no-user-cards") / "none")


def _ids(parsed):
    return [m.card_id for layer in parsed.layers for m in layer.matches]


def _words(text):
    return [w.lower() for w in _WORD.findall(text)]


def _contains_name(text, name):
    w, n = _words(text), _words(name)
    return bool(n) and any(w[i:i + len(n)] == n for i in range(len(w) - len(n) + 1))


def _all_names(lib):
    """Every artist card's name and aliases, and every inspiration entry of every card."""
    names = set()
    for c in lib.cards.values():
        names.update(c.inspiration)
        if c.kind == "artist":
            names.update((c.name, *c.aliases))
    return sorted(n for n in names if n.strip())


def _card_text(c):
    """Every card field that can reach a prompt."""
    return [c.sound, c.rhythm, c.harmony, c.mood, c.production, c.vocal, c.lead_instrument, c.key,
            *c.tags, *c.instruments]


# ── names and aliases ────────────────────────────────────────────────────────

def test_all_named_cards_exist_with_the_expected_aliases(lib):
    for text, want in OPERATOR_NAMES.items():
        assert _ids(parse(text, lib)) == want, (text, _ids(parse(text, lib)))
    for text, want in MORE_ALIASES.items():
        assert _ids(parse(text, lib)) == [want], (text, _ids(parse(text, lib)))
    # longest match: the collective beats its two bands, and the bands still match alone
    p = parse("a Parliament-Funkadelic groove", lib)
    assert _ids(p) == ["p-funk"] and p.layers[0].matches[0].text == "Parliament-Funkadelic"


def test_artist_cards_have_parents_that_exist_and_are_styles(lib):
    artists = lib.of_kind("artist")
    assert len(artists) >= 9
    for c in artists:
        parent = lib.card(c.parent)
        assert parent is not None and parent.kind == "style", (c.id, c.parent)


# ── sources and honesty of card text ─────────────────────────────────────────

_SOURCE = re.compile(r"^\s*(?P<url>https?://\S+)\s*$|^\s*(?P<publisher>[^:;()]+?)\s*:\s*\S.*$", re.S)


def _publishers(notes):
    """Publishers of a `notes` source list ("Wikipedia: Funk; PopMatters: review"), or None if one is malformed."""
    out = []
    for src in (s for s in notes.split(";") if s.strip()):
        m = _SOURCE.match(src)
        if not m:
            return None
        out.append((m.group("publisher") or m.group("url").split("/")[2]).strip().lower())
    return out


def test_every_shipped_card_has_two_sources_or_is_marked_unverified(lib):
    for c in lib.cards.values():
        notes = c.notes.strip()
        if notes == "unverified":
            continue
        pubs = _publishers(notes)
        assert pubs is not None, (c.id, "each source must read 'Publisher: title' or be a URL", notes)
        assert len(set(pubs)) >= 2, (c.id, "two distinct publishers needed", pubs)


def test_the_sources_check_rejects_weak_notes():
    assert _publishers("Wikipedia: Funk; Wikipedia: Parliament") == ["wikipedia", "wikipedia"]
    assert _publishers("Wikipedia: Funk; some reviews I saw") is None
    assert _publishers("Wikipedia: Funk; https://example.org/funk") == ["wikipedia", "example.org"]


def test_no_card_sound_contains_an_artist_or_inspiration_name(lib):
    names = _all_names(lib)
    assert "Tortoise" in names and "Kraftwerk" in names and "Charlie Parker" in names
    for c in lib.cards.values():
        for field in _card_text(c):
            for n in names:
                assert not _contains_name(field, n), (c.id, n, field)


def test_no_card_text_claims_instrumental_unless_it_is_an_instrumental_form(lib):
    for c in lib.cards.values():
        if c.id in INSTRUMENTAL_FORMS:
            continue
        for field in (*_card_text(c), *c.avoid):
            assert not re.search(r"\binstrumental\b", field, re.I), (c.id, field)


def test_every_card_has_tags_and_tempo_or_a_scene_reason(lib):
    for c in lib.cards.values():
        assert len(c.tags) >= 3, c.id
        assert c.instruments and c.mood and c.avoid, c.id
        if c.kind not in TEMPO_OPTIONAL_KINDS:
            assert c.tempo_bpm is not None, c.id
        if c.tempo_bpm:
            lo, hi = c.tempo_bpm
            assert 40 <= lo <= hi <= 320, (c.id, c.tempo_bpm)
        n = len(re.findall(r"[.!?](\s|$)", c.sound))
        assert 1 <= n <= 3, (c.id, "sound must be one to three sentences")


def test_goes_with_and_clashes_with_reference_real_ids_or_families(lib):
    families = set(lib.families())
    for c in lib.cards.values():
        for ref in c.goes_with + c.clashes_with:
            assert ref in lib.cards or ref in families, (c.id, ref)


def test_shipped_library_loads_without_warnings(tmp_path, caplog):
    with caplog.at_level(logging.DEBUG, logger=C.log.name):
        C.load_library(user_dir=tmp_path / "none")
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


# ── rendering ────────────────────────────────────────────────────────────────

def test_each_named_style_translates_in_all_three_dialects_without_error(lib):
    names = _all_names(lib)
    inputs = [f"{c.name}-like song" for c in lib.cards.values()] + list(OPERATOR_NAMES)
    for text in inputs:
        for dialect, profile in PROFILES:
            t = translate(text, profile, library=lib)
            assert t.dialect == dialect and t.prompt.strip(), (text, dialect)
            assert t.used_cards, (text, dialect, "no card matched")
            for n in names:
                assert not _contains_name(t.prompt, n), (text, dialect, n, t.prompt)
            assert "-like" not in t.prompt and "  " not in t.prompt, (text, t.prompt)
            assert not re.search(r"\b(\w+) \1\b", t.prompt, re.I), (text, t.prompt)
            assert len(t.prompt) <= 1000, (text, dialect)


def test_the_example_sentence_still_translates(lib):
    cap = translate(EXAMPLE, MINIMAX, library=lib)
    p = cap.prompt.lower()
    for term in ("vibraphone", "dub", "crescendo", "warm analog", "post-rock"):
        assert term in p, term
    assert p.count("bluegrass vocal") == 1 and "high lonesome" in p
    assert cap.used_cards == ("tortoise", "bluegrass-vocal")
    assert cap.lyrics and cap.lyrics.startswith("[Verse")
    assert not _contains_name(cap.prompt, "Tortoise")
    tags = translate(EXAMPLE, STABLE, library=lib).prompt.lower()
    for debris in ("singing", "someone"):
        assert not re.search(r"\b" + debris + r"\b", tags), debris
    assert "dobro" in tags and "vibraphone" in tags
    gen = translate(EXAMPLE, None, library=lib).prompt.lower()
    assert "vibraphone" in gen and "bluegrass vocal" in gen


def _shows(card, prompt, lib):
    """Does a tags prompt carry at least one tag (or the label) of this card?"""
    label = lib.card(card.parent).name if card.kind == "artist" else card.name
    p = prompt.lower()
    return any(t.lower() in p for t in (label, *card.tags))


def test_wand_on_the_shipped_library_is_varied(lib):
    # The larger checks (50 and 60 seeds, every family) live in test_music_variety.py.
    mm = {wand(MINIMAX, seed=s, library=lib).prompt for s in range(1, 31)}
    sa = [wand(STABLE, seed=s, library=lib) for s in range(1, 31)]
    assert len(mm) >= 10, len(mm)
    assert len({t.prompt for t in sa}) >= 6
    for s, t in enumerate(sa, start=1):
        missing = [cid for cid in t.used_cards if not _shows(lib.card(cid), t.prompt, lib)]
        assert not missing, (s, missing, t.prompt)


VOICE_WORDS = re.compile(r"\b(vocals?|voices?|singing|sung|choirs?|chants?|vocoder|harmony vocals|spoken|lyrics)\b", re.I)


def _typed_name(c):
    """A name for the card with no voice word in it, so only card text can bring one in."""
    return next(n for n in (c.name, *c.aliases) if not VOICE_WORDS.search(n))


def test_no_voice_words_in_any_instrumental_prompt(lib):
    for c in lib.cards.values():
        name = _typed_name(c)
        for text, profile in ((f"{name}-like song", STABLE), (f"instrumental {name}-like piece", MINIMAX),
                              (f"{name}-like song", MINIMAX)):
            t = translate(text, profile, library=lib)
            if not t.instrumental:                      # a vocal card on MiniMax sings: that is not this test
                continue
            assert not VOICE_WORDS.search(t.prompt), (c.id, t.dialect, VOICE_WORDS.search(t.prompt).group(), t.prompt)
    # the voice-defined cards keep their voice for a prompt that sings
    sung = translate("Kraftwerk-like but someone is singing about trains on top", MINIMAX, library=lib)
    assert "vocoder" in sung.prompt.lower() and not sung.instrumental


def test_bare_artist_names_are_matched_and_never_reach_the_prompt(lib):
    for text in ("Tortoise", "Tortoise style", "Tortoise!", "tortoise"):
        for _, profile in PROFILES:
            t = translate(text, profile, library=lib)
            assert t.used_cards == ("tortoise",), (text, t.used_cards)
            assert not _contains_name(t.prompt, "Tortoise"), (text, t.prompt)
    for text in ("the tortoise crossed the road", "A tortoise is slow. Tortoise soup is a dish."):
        assert translate(text, MINIMAX, library=lib).used_cards == ()
