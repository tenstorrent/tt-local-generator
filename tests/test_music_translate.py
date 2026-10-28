"""Tests for app/music_prompts/translate.py and dialects.py.

Golden tests for the operator's example sentence (spec section 5) use the
shipped cards and check meaning, not exact wording. Rule tests use small
fixture libraries written to a tmp dir, so a card edit never changes what a
rule test means. Tests never read the real home directory.
"""
import dataclasses
import re

import pytest
import yaml

from music_prompts import cards as C
from music_prompts import Translation, dialect_for, load_library, translate
from music_prompts.dialects import Clause, render
from music_runtime import GENERIC, MusicProfile, profile_for

EXAMPLE = "tortoise-like song but someone is singing a bluegrass song on top"
_WORD = re.compile(r"[^\W_]+")

MINIMAX = profile_for({"status": "ok", "model": "fiona-crapple-merged"})
STABLE = profile_for({"status": "ok", "model": "stable-audio-3 small-music"})
UNKNOWN = profile_for({"status": "ok", "model": "mystery-synth"})


# ── fixtures ─────────────────────────────────────────────────────────────────

def _card(id, **kw):
    d = {"id": id, "kind": "style", "name": id, "family": "fam", "sound": "A plain sound."}
    d.update(kw)
    return d


def _lib(tmp_path, *raw_cards):
    d = tmp_path / f"cards{len(list(tmp_path.glob('cards*')))}"
    d.mkdir()
    (d / "c.yaml").write_text(yaml.safe_dump(list(raw_cards)), encoding="utf-8")
    return C.load_library(shipped_dir=d, user_dir=tmp_path / "none")


def _shipped(tmp_path):
    return C.load_library(user_dir=tmp_path / "no-user-cards")


def _band(tmp_path):
    """A small library with every field filled, an artist, a vocal and a texture."""
    return _lib(
        tmp_path,
        _card("slowcore", name="Slowcore", family="rock",
              sound="Slow, quiet rock with sparse electric guitar and soft drums.",
              instruments=["electric guitar", "soft drums", "bass guitar", "glockenspiel"],
              rhythm="Very slow, steady pulse.",
              harmony="Simple minor chords held for a long time.",
              mood="Sad, calm and patient.",
              production="Dry and close, with natural room tone.",
              tempo_bpm=[60, 80],
              avoid=["fast drum fills", "shouted vocals"],
              tags=["slowcore", "quiet", "sparse"],
              inspiration=["Night Tide", "Harbor Lamps"]),
        _card("drifters", kind="artist", parent="slowcore", name="Drifters", family="rock",
              sound="Hushed electric guitar and brushed snare over a long, slow pulse.",
              instruments=["electric guitar", "brushed snare", "organ"],
              mood="Hushed and tender.",
              tempo_bpm=[55, 70],
              avoid=["distortion"],
              tags=["hushed", "brushed snare"],
              inspiration=["Drifters", "Low Harbor Sessions"]),
        _card("choir", kind="vocal", name="Choir vocal", aliases=["choir"], family="folk",
              sound="A hushed small choir.",
              vocal="a hushed small choir singing in unison",
              lyric_themes=["snow on the roof", "an empty station", "the last train"],
              tags=["choir", "unison"],
              lead_instrument="bowed saw"),
        _card("hiss", kind="texture", name="tape hiss", family="texture",
              sound="A soft bed of tape hiss under everything."),
    )


def _limit(profile, n):
    return dataclasses.replace(profile, max_chars=n)


def _words(s):
    return [w.lower() for w in _WORD.findall(s)]


def _contains_name(text, name):
    """Case-insensitive whole-word scan: does `name` (as a word sequence) occur in text?"""
    w, n = _words(text), _words(name)
    return bool(n) and any(w[i:i + len(n)] == n for i in range(len(w) - len(n) + 1))


# ── golden: the operator's example (spec section 5) ──────────────────────────

def test_example_sentence_caption_dialect(tmp_path):
    t = translate(EXAMPLE, MINIMAX, library=_shipped(tmp_path))
    assert isinstance(t, Translation) and t.dialect == "caption+lyrics"
    p = t.prompt.lower()
    for term in ("vibraphone", "dub", "brushed drums", "crescendo", "warm analog", "post-rock"):
        assert term in p, term
    for term in ("high lonesome", "close harmony", "twang"):
        assert term in p, term
    assert "over it, a bluegrass vocal" in p
    assert re.search(r"\b\d{2,3} bpm\b", p)
    assert not _contains_name(t.prompt, "Tortoise")
    assert t.lyrics and t.lyrics.startswith("[Verse")
    assert "long road home" in t.lyrics.lower()
    assert "[Chorus]" in t.lyrics
    assert t.used_cards == ("tortoise", "bluegrass-vocal")
    assert "Tortoise" in t.inspired_by and "Bill Monroe" in t.inspired_by
    assert len(t.prompt) <= 1000
    # a vocal on top contradicts the base card's "avoid sung lead vocals": it is left out
    assert "sung lead vocals" not in p
    assert t.added and all(a in t.prompt for a in t.added)


def test_example_sentence_tags_dialect_turns_the_vocal_into_a_lead_line_with_a_note(tmp_path):
    t = translate(EXAMPLE, STABLE, library=_shipped(tmp_path))
    assert t.dialect == "tags" and t.lyrics is None
    p = t.prompt.lower()
    assert "\n" not in t.prompt and p.count(",") >= 5
    for term in ("post-rock", "vibraphone", "dub", "brushed drums", "bpm"):
        assert term in p, term
    assert "pedal steel" in p
    assert "close harmony" not in p          # the voice description is gone; the lead line replaces it
    assert any("this model cannot sing; the vocal became a lead line" in n.lower() for n in t.notes)
    assert not _contains_name(t.prompt, "Tortoise")
    assert len(t.prompt) <= 2000


def test_generic_dialect_is_one_sentence(tmp_path):
    t = translate(EXAMPLE, None, library=_shipped(tmp_path))
    assert t.dialect == "generic" and t.lyrics is None
    assert t.prompt.endswith(".")
    assert not re.search(r"[.!?]\s", t.prompt)       # no sentence break inside
    assert "\n" not in t.prompt
    assert "vibraphone" in t.prompt.lower() and "bluegrass vocal" in t.prompt.lower()
    assert translate(EXAMPLE, UNKNOWN, library=_shipped(tmp_path)).dialect == "generic"


# ── names (Review Focus 1) ───────────────────────────────────────────────────

def _forbidden_names(lib):
    names = set()
    for c in lib.cards.values():
        names.update(c.inspiration)
        if c.kind == "artist":
            names.add(c.name)
            names.update(c.aliases)
    return sorted(n for n in names if _words(n))


def test_no_artist_name_in_any_rendered_prompt(tmp_path):
    lib = _shipped(tmp_path)
    forbidden = _forbidden_names(lib)
    assert "Tortoise" in forbidden and "Bill Monroe" in forbidden
    checked = 0
    for card in lib.cards.values():
        for alias in (card.name, *card.aliases):
            for text in (f"{alias} song", f"slow drums but {alias} on top"):
                for profile in (MINIMAX, STABLE, None):
                    t = translate(text, profile, library=lib)
                    assert card.id in t.used_cards, (text, t.used_cards)   # not hollow: the card was used
                    for out in (t.prompt, t.lyrics or ""):
                        for name in forbidden:
                            assert not _contains_name(out, name), (name, text, t.dialect, out)
                    checked += 1
    assert checked >= 3 * 2 * 3


def test_include_names_true_puts_inspiration_in_the_prompt(tmp_path):
    lib = _shipped(tmp_path)
    t = translate(EXAMPLE, MINIMAX, library=lib, include_names=True)
    assert "Tortoise" in t.prompt and "Bill Monroe" in t.prompt
    off = translate(EXAMPLE, MINIMAX, library=lib)
    assert "Bill Monroe" not in off.prompt and "Bill Monroe" in off.inspired_by


def test_a_typed_inspiration_name_with_no_card_is_replaced_by_its_style(tmp_path):
    lib = _band(tmp_path)
    t = translate("rain on a window, Harbor Lamps vibes", None, library=lib)
    assert not _contains_name(t.prompt, "Harbor Lamps")
    assert "rain on a window" in t.prompt and "slowcore vibes" in t.prompt.lower()
    assert any("Harbor Lamps" in n for n in t.notes)


def test_card_text_that_names_an_artist_is_scrubbed(tmp_path):
    """Guard for a user card that breaks the CARDS.md rule and names an artist in its own text."""
    lib = _lib(tmp_path, _card("dream", name="Dreamwave", sound="Hazy synths, a bit like Night Tide.",
                               tags=["Night Tide", "hazy"], inspiration=["Night Tide"]))
    for profile in (MINIMAX, STABLE, None):
        t = translate("dreamwave", profile, library=lib)
        assert "dream" in t.used_cards and "hazy" in t.prompt.lower()
        assert not _contains_name(t.prompt, "Night Tide"), t.prompt


# ── the user's own words (Review Focus 2) ────────────────────────────────────

def test_users_own_words_are_kept_verbatim(tmp_path):
    lib = _shipped(tmp_path)
    for profile in (MINIMAX, STABLE, None):
        t = translate("make it feel like a rainy Tuesday, Tortoise-like", profile, library=lib)
        assert "make it feel like a rainy Tuesday" in t.prompt
        assert "tortoise" in t.used_cards
        assert not _contains_name(t.prompt, "Tortoise")


def test_rainy_tuesday_example(tmp_path):
    t = translate("make it feel like a rainy Tuesday, Tortoise-like", MINIMAX, library=_shipped(tmp_path))
    assert "rainy Tuesday" in t.prompt
    assert t.prompt.index("rainy Tuesday") < t.prompt.lower().index("vibraphone")
    assert "vibraphone" in t.prompt.lower()


def test_unicode_text_is_kept(tmp_path):
    lib = _band(tmp_path)
    text = "slowcore — très lent, comme la pluie ☔ 雨"
    for profile in (MINIMAX, STABLE, None):
        t = translate(text, profile, library=lib)
        for phrase in ("très lent, comme la pluie", "☔", "雨"):
            assert phrase in t.prompt, (phrase, t.dialect)


def test_unknown_text_passes_through_unchanged_with_a_note(tmp_path):
    lib = _band(tmp_path)
    text = "a quiet song about my cat, très doux"
    for profile in (MINIMAX, STABLE, None):
        t = translate(text, profile, library=lib)
        assert t.prompt == text and t.used_cards == () and t.added == ()
        assert t.notes and any("passed through unchanged" in n for n in t.notes)


def test_empty_text_returns_a_helpful_note_and_empty_prompt(tmp_path):
    for text in ("", "   "):
        t = translate(text, MINIMAX, library=_band(tmp_path))
        assert t.prompt == "" and t.lyrics is None and t.used_cards == ()
        assert t.notes and "type" in t.notes[0].lower()


def test_parsed_dropped_marker_words_are_not_silently_lost(tmp_path):
    """Choice: a marker that made no layer stays in the prompt (in the user's own words) and gets a note."""
    lib = _band(tmp_path)
    for text, marker in (("slowcore but", "but"), ("slowcore on top", "on top")):
        for profile in (MINIMAX, STABLE, None):
            t = translate(text, profile, library=lib)
            assert f"slowcore {marker}" in t.prompt, (text, t.prompt)
            assert any(f"'{marker}'" in n for n in t.notes)


def test_trigger_phrase_only_when_the_user_typed_it(tmp_path):
    lib = _band(tmp_path)
    assert "fiona crapple" in MINIMAX.triggers
    plain = translate("slowcore", MINIMAX, library=lib)
    assert "fiona crapple" not in plain.prompt.lower()
    named = translate("slowcore", MINIMAX, library=lib, include_names=True)
    assert "fiona crapple" not in named.prompt.lower()
    typed = translate("fiona crapple, slowcore", MINIMAX, library=lib)
    assert "fiona crapple" in typed.prompt


# ── length limits (Review Focus 6) ───────────────────────────────────────────

def test_trim_never_drops_user_words_or_base_sound(tmp_path):
    lib = _band(tmp_path)
    text = "a long walk home in the snow, slowcore"
    for profile in (MINIMAX, STABLE, None):
        prof = _limit(profile or UNKNOWN, 10)
        t = translate(text, prof, library=lib)
        assert "a long walk home in the snow" in t.prompt
        assert "slowcore" in t.prompt.lower()
        assert len(t.prompt) > 10
        assert any("over" in n.lower() and "limit" in n.lower() for n in t.notes)


def test_result_over_limit_when_user_words_alone_exceed_it_has_a_note(tmp_path):
    lib = _band(tmp_path)
    words = "a very long description of my own " * 10
    t = translate(words + "slowcore", _limit(MINIMAX, 100), library=lib)
    assert words.strip() in t.prompt and len(t.prompt) > 100
    assert any("100-character limit" in n for n in t.notes)
    # unknown text alone over the limit also gets the note
    t2 = translate(words.strip(), _limit(MINIMAX, 50), library=lib)
    assert t2.prompt == words.strip() and any("50-character limit" in n for n in t2.notes)


def test_trim_order_drops_avoid_and_texture_first(tmp_path):
    lib = _band(tmp_path)
    text = "slowcore with tape hiss"
    full = translate(text, _limit(MINIMAX, 5000), library=lib)
    texture = next(a for a in full.added if "tape hiss" in a)
    avoid = next(a for a in full.added if "fast drum fills" in a)
    mood = next(a for a in full.added if "patient" in a.lower())
    # just enough room once the texture sentence (and its space) is gone
    t1 = translate(text, _limit(MINIMAX, len(full.prompt) - len(texture) - 1), library=lib)
    assert texture not in t1.prompt and avoid in t1.prompt and mood in t1.prompt
    # one character less: avoid goes next, mood stays
    t2 = translate(text, _limit(MINIMAX, len(full.prompt) - len(texture) - 2), library=lib)
    assert texture not in t2.prompt and avoid not in t2.prompt and mood in t2.prompt
    assert len(t2.prompt) <= len(full.prompt) - len(texture) - 2
    assert any("dropped" in n.lower() for n in t2.notes)


def test_trim_never_cuts_mid_word(tmp_path):
    lib = _band(tmp_path)
    text = "slowcore with tape hiss but a choir on top"
    for profile in (MINIMAX, STABLE, None):
        full = translate(text, _limit(profile or UNKNOWN, 5000), library=lib)
        full_words = set(_words(full.prompt))
        for n in range(20, len(full.prompt), 11):
            t = translate(text, _limit(profile or UNKNOWN, n), library=lib)
            assert set(_words(t.prompt)) <= full_words
            assert len(t.prompt) <= n or any("limit" in x for x in t.notes)
    # render itself drops whole clauses
    clauses = [Clause("user", "my words", 0), Clause("base", "a base sound", 1),
               Clause("mood", "extraordinarily melancholic", 7)]
    prompt, notes = render(clauses, "caption+lyrics", 30)
    assert prompt == "my words. a base sound." and notes


def test_unknown_limit_uses_1000(tmp_path):
    long_mood = ", ".join(["slow rolling and patient"] * 50)        # about 1250 characters; every dialect shows mood
    lib = _lib(tmp_path, _card("big", name="Big", sound="A big sound.", mood=long_mood, tempo_bpm=[90, 100]))
    for profile in (None, UNKNOWN):
        assert profile is None or profile.max_chars is None
        t = translate("big", profile, library=lib)
        assert len(t.prompt) <= 1000
        assert any("1000-character limit" in n for n in t.notes)
    roomy = translate("big", _limit(UNKNOWN, 5000), library=lib)
    assert len(roomy.prompt) > 1000


# ── determinism, smoothing, lyrics ───────────────────────────────────────────

def test_translate_is_deterministic_without_smooth(tmp_path):
    lib = _shipped(tmp_path)
    for profile in (MINIMAX, STABLE, None):
        a = translate(EXAMPLE, profile, library=lib)
        b = translate(EXAMPLE, profile, library=lib)
        assert a == b


def test_smooth_runs_only_when_asked_and_never_adds_names(tmp_path):
    lib = _shipped(tmp_path)
    calls = []

    def smoother(p):
        calls.append(p)
        return "A smoothed prompt."

    plain = translate(EXAMPLE, MINIMAX, library=lib, smooth_fn=smoother)
    assert calls == []
    # the smoother loses the user's own words: rejected, falls back
    lost = translate(EXAMPLE, MINIMAX, library=lib, smooth=True, smooth_fn=smoother)
    assert calls and lost.prompt == plain.prompt and lost.notes != plain.notes

    keep = lambda p: p.replace("Around", "At about")
    good = translate(EXAMPLE, MINIMAX, library=lib, smooth=True, smooth_fn=keep)
    assert "At about" in good.prompt
    named = translate(EXAMPLE, MINIMAX, library=lib, smooth=True, smooth_fn=lambda p: p + " Like Tortoise.")
    assert named.prompt == plain.prompt and not _contains_name(named.prompt, "Tortoise")
    assert any("smooth" in n.lower() for n in named.notes)
    none = translate(EXAMPLE, MINIMAX, library=lib, smooth=True, smooth_fn=lambda p: None)
    assert none.prompt == plain.prompt


def test_lyrics_fn_none_falls_back_to_a_skeleton_from_lyric_themes(tmp_path):
    lib = _band(tmp_path)
    text = "slowcore but a choir on top"
    t = translate(text, MINIMAX, library=lib)
    assert t.lyrics.startswith("[Verse]\n")
    assert "\n[Chorus]\n" in t.lyrics
    assert "Snow on the roof" in t.lyrics and "An empty station" in t.lyrics
    seen = []

    def writer(vocal, themes):
        seen.append((vocal, themes))
        return None

    t2 = translate(text, MINIMAX, library=lib, lyrics_fn=writer)
    assert t2.lyrics == t.lyrics
    assert seen == [("a hushed small choir singing in unison",
                     ("snow on the roof", "an empty station", "the last train"))]
    t3 = translate(text, MINIMAX, library=lib, lyrics_fn=lambda v, th: "[Verse]\nla la\n")
    assert t3.lyrics == "[Verse]\nla la\n"
    # lyrics that name an artist are refused
    t4 = translate(text, MINIMAX, library=lib, lyrics_fn=lambda v, th: "[Verse]\nlike Night Tide\n")
    assert "Night Tide" not in t4.lyrics and t4.lyrics == t.lyrics


def test_instrumental_request_has_no_lyrics(tmp_path):
    lib = _band(tmp_path)
    called = []
    t = translate("instrumental slowcore but a choir on top", MINIMAX, library=lib,
                  lyrics_fn=lambda v, th: called.append(1) or "[Verse]\nx\n")
    assert t.lyrics is None and called == []
    assert any("instrumental" in n.lower() for n in t.notes)
    # no vocal at all: no lyrics either
    assert translate("slowcore", MINIMAX, library=lib).lyrics is None
    # a model without lyrics never gets them
    assert translate("slowcore but a choir on top", STABLE, library=lib).lyrics is None


def test_lead_line_without_a_lead_instrument_names_the_vocal_style(tmp_path):
    lib = _lib(tmp_path, _card("slowcore", name="Slowcore"),
               _card("croon", kind="vocal", name="Crooner vocal", aliases=["crooner"], vocal="a warm low croon"))
    t = translate("slowcore but a crooner on top", STABLE, library=lib)
    assert "lead line phrased like a crooner vocal" in t.prompt
    assert "warm low croon" not in t.prompt


# ── dialect selection and profile fields ─────────────────────────────────────

def test_dialect_for_uses_profile_dialect_then_has_lyrics_then_generic():
    assert dialect_for(None) == "generic"
    assert dialect_for(MINIMAX) == "caption+lyrics"
    assert dialect_for(STABLE) == "tags"
    assert dialect_for(UNKNOWN) == "generic"
    sings = dataclasses.replace(UNKNOWN, has_lyrics=True)
    assert dialect_for(sings) == "caption+lyrics"
    assert dialect_for(dataclasses.replace(sings, dialect="tags")) == "tags"


def test_public_api():
    import music_prompts
    for name in ("load_library", "translate", "Translation", "dialect_for"):
        assert hasattr(music_prompts, name)
    assert load_library is C.load_library
    assert GENERIC.get("dialect") is None and GENERIC.get("max_chars") is None
    assert MusicProfile.__dataclass_fields__["max_chars"].default is None
