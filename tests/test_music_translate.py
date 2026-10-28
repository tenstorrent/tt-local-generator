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
from music_prompts import dialects as D
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


def _repeated_trigram(text):
    w = _words(text)
    seen = set()
    for g in zip(w, w[1:], w[2:]):
        if g in seen:
            return g
        seen.add(g)
    return None


def _contains_name(text, name):
    """Case-insensitive whole-word scan: does `name` (as a word sequence) occur in text?"""
    w, n = _words(text), _words(name)
    return bool(n) and any(w[i:i + len(n)] == n for i in range(len(w) - len(n) + 1))


# ── golden: the operator's example (spec section 5) ──────────────────────────

def test_example_sentence_caption_dialect(tmp_path):
    t = translate(EXAMPLE, MINIMAX, library=_shipped(tmp_path))
    assert isinstance(t, Translation) and t.dialect == "caption+lyrics"
    p = t.prompt.lower()
    for term in ("vibraphone", "dub", "jazz-inflected drums", "crescendo", "warm analog", "post-rock"):
        assert term in p, term
    for term in ("high lonesome", "close harmony", "twang"):
        assert term in p, term
    assert "over it, a bluegrass vocal" in p
    assert re.search(r"\b\d{2,3} bpm\b", p)
    assert len(t.prompt) <= 600
    assert not _repeated_trigram(t.prompt), _repeated_trigram(t.prompt)
    # the vocal layer comes right after the base sound (the base clause is the first thing added)
    assert t.prompt.index("Over it, a bluegrass vocal") == len(t.added[0]) + 1
    assert t.prompt[0].isupper()
    assert not _contains_name(t.prompt, "Tortoise")
    assert t.lyrics and t.lyrics.startswith("[Verse")
    assert "long road home" in t.lyrics.lower()
    assert "[Chorus]" in t.lyrics
    assert t.used_cards == ("tortoise", "bluegrass-vocal")
    assert "Tortoise" in t.inspired_by and "Bill Monroe" in t.inspired_by
    assert len(t.prompt) <= 1000
    assert "sung lead vocals" not in p and "leave out" not in p
    assert t.added and all(a in t.prompt for a in t.added)
    assert t.instrumental is False
    for glue in ("someone is singing", "on top", "song but"):
        assert glue not in p, glue


def test_example_sentence_tags_dialect_turns_the_vocal_into_a_lead_line_with_a_note(tmp_path):
    t = translate(EXAMPLE, STABLE, library=_shipped(tmp_path))
    assert t.dialect == "tags" and t.lyrics is None
    p = t.prompt.lower()
    assert "\n" not in t.prompt and p.count(",") >= 5
    for term in ("post-rock", "vibraphone", "dub", "jazz-inflected drums", "bpm"):
        assert term in p, term
    assert "dobro" in p                      # bluegrass lead instrument (was pedal steel; corrected in Task 5)
    assert "close harmony" not in p          # the voice description is gone; the lead line replaces it
    for debris in ("singing", "someone", "on top", "song"):
        assert not re.search(r"\b" + debris + r"\b", p), debris
    assert len(t.prompt) <= 300
    assert "major key" in p
    assert "chicago post-rock" in p and not re.search(r"(^|, )post-rock(,|$)", p)   # the more specific tag wins
    assert p.count("dub") == 1 and p.count("instrumental") <= 1
    assert t.instrumental is True
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
    assert len(t.prompt) <= 350 and t.prompt[0].isupper()
    assert "someone is singing" not in t.prompt
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


def test_artist_label_is_its_parent_style(tmp_path):
    """The artist card shows its parent style as its label (no other card lists 'Drifters')."""
    lib = _band(tmp_path)
    for profile in (MINIMAX, STABLE, None):
        t = translate("Drifters song", profile, library=lib)
        assert "drifters" in t.used_cards
        assert not _contains_name(t.prompt, "Drifters"), t.prompt
        assert "slowcore" in t.prompt.lower() or t.dialect != "tags"


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
        assert "ake it feel like a rainy Tuesday" in t.prompt           # only the first letter may be capitalised
        assert t.prompt[0] == ("m" if t.dialect == "tags" else "M")
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
    """A marker that made no layer is glue: left out of the prompt and named in a note."""
    lib = _band(tmp_path)
    for text, marker in (("slowcore but", "but"), ("slowcore on top", "on top")):
        for profile in (MINIMAX, STABLE, None):
            t = translate(text, profile, library=lib)
            assert not re.search(r"\b" + marker + r"\b", t.prompt.lower()), (text, t.prompt)
            assert any(f"'{marker}'" in n for n in t.notes)


def test_trigger_phrase_only_when_the_user_typed_it(tmp_path):
    lib = _band(tmp_path)
    assert "fiona crapple" in MINIMAX.triggers
    plain = translate("slowcore", MINIMAX, library=lib)
    assert "fiona crapple" not in plain.prompt.lower()
    named = translate("slowcore", MINIMAX, library=lib, include_names=True)
    assert "fiona crapple" not in named.prompt.lower()
    typed = translate("fiona crapple, slowcore", MINIMAX, library=lib)
    assert typed.prompt.startswith("fiona crapple")       # a trigger is never re-cased


# ── length limits (Review Focus 6) ───────────────────────────────────────────

def test_trim_never_drops_user_words_or_base_sound(tmp_path):
    lib = _band(tmp_path)
    text = "a long walk home in the snow, slowcore"
    for profile in (MINIMAX, STABLE, None):
        prof = _limit(profile or UNKNOWN, 10)
        t = translate(text, prof, library=lib)
        assert "a long walk home in the snow" in t.prompt.lower()     # first letter may be capitalised
        assert "slowcore" in t.prompt.lower()
        assert len(t.prompt) > 10
        assert any("over" in n.lower() and "limit" in n.lower() for n in t.notes)


def test_result_over_limit_when_user_words_alone_exceed_it_has_a_note(tmp_path):
    lib = _band(tmp_path)
    words = "a very long description of my own " * 10
    t = translate(words + "slowcore", _limit(MINIMAX, 100), library=lib)
    assert words.strip()[1:] in t.prompt and len(t.prompt) > 100
    assert any("100-character limit" in n for n in t.notes)
    # unknown text alone over the limit also gets the note
    t2 = translate(words.strip(), _limit(MINIMAX, 50), library=lib)
    assert t2.prompt == words.strip() and any("50-character limit" in n for n in t2.notes)


def test_trim_order_drops_avoid_and_texture_first():
    """Render drops whole clauses from the end of the spec's list: details, texture, mood, production,
    tempo, instruments, names, layers; the user's words and the base sound never go."""
    kinds = ["user", "base", "vocal", "names", "instruments", "tempo", "mood", "texture", "detail"]
    clauses = [D.clause(k, f"{k} words here") for k in kinds]
    expected = ["detail", "texture", "mood", "tempo", "instruments", "names", "vocal"]
    assert "production" not in D.PRIORITY        # production is a caption detail, not a kind of its own
    for k in range(1, len(expected) + 1):
        keep = [c for c in clauses if c.kind not in expected[:k]]
        target = len(D._join(keep, "caption+lyrics"))
        prompt, notes = render(clauses, "caption+lyrics", target, budget=0)
        assert prompt == D._join(keep, "caption+lyrics"), (k, prompt)
        assert notes and "dropped" in notes[0]
    prompt, notes = render(clauses, "caption+lyrics", 5, budget=0)
    assert prompt == "user words here. base words here." and any("limit" in n for n in notes)


def test_avoid_is_never_in_a_prompt_and_is_listed_in_the_notes(tmp_path):
    lib = _shipped(tmp_path)
    avoid = {a for c in lib.cards.values() for a in c.avoid}
    for text in (EXAMPLE, "make it feel like a rainy Tuesday, Tortoise-like", "post-rock", "instrumental tortoise-like piece"):
        for profile in (MINIMAX, STABLE, None):
            t = translate(text, profile, library=lib)
            for a in avoid:
                assert a.lower() not in t.prompt.lower(), (a, text, t.dialect)
    t = translate("post-rock", MINIMAX, library=lib)
    assert any(n.startswith("Not asked for:") and "short pop hooks" in n for n in t.notes)


def test_glue_words_are_left_out_and_named_in_the_notes(tmp_path):
    t = translate(EXAMPLE, STABLE, library=_shipped(tmp_path))
    glue_note = next(n for n in t.notes if "not repeated in the prompt" in n)
    for phrase in ("song", "someone is singing a", "on top"):
        assert f"'{phrase}'" in glue_note
    lib = _band(tmp_path)
    t2 = translate("slowcore but very slow, with rain sounds", MINIMAX, library=lib)
    assert "very slow" in t2.prompt.lower() and "rain sounds" in t2.prompt
    assert t2.prompt.lower().index("very slow") < t2.prompt.index("rain sounds")
    assert "rainy" not in D.GLUE_WORDS and "song" in D.GLUE_WORDS


def test_rainy_tuesday_carries_no_glue(tmp_path):
    for profile in (MINIMAX, STABLE, None):
        t = translate("make it feel like a rainy Tuesday, Tortoise-like", profile, library=_shipped(tmp_path))
        assert "rainy Tuesday" in t.prompt
        assert "Tuesday, post-rock" not in t.prompt and "-like" not in t.prompt


def test_instrumental_request_is_flagged_and_has_no_vocal_words(tmp_path):
    lib = _shipped(tmp_path)
    for profile in (MINIMAX, STABLE, None):
        t = translate("instrumental tortoise-like piece", profile, library=lib)
        assert t.instrumental is True and t.lyrics is None
        assert "instrumental" in t.prompt.lower()
        assert t.prompt.lower().count("instrumental") == 1
        # "piece" stays: next to the match it is part of the user's phrase ("instrumental post-rock piece")
        for w in ("vocal", "sung", "singing", "voice", "lyric"):
            assert w not in t.prompt.lower(), (w, t.dialect)
    # instrumental with a vocal layer on a singing model: no vocal in the caption
    t = translate("instrumental tortoise-like piece but a bluegrass song on top", MINIMAX, library=lib)
    assert "vocal" not in t.prompt.lower() and t.lyrics is None and t.instrumental


def test_dedupe_collapses_near_repeats(tmp_path):
    lib = _lib(tmp_path, _card("dubby", name="Dubby", sound="Echoing drums.",
                               tags=["dub bass", "echo"], instruments=["dub-style bass", "organ", "echoing drums"],
                               mood="Heavy, dub-weighted and echoing."))
    for profile in (STABLE, MINIMAX, None):
        p = translate("dubby", profile, library=lib).prompt.lower()
        assert len(re.findall(r"\bdub\b", p)) <= 1, (profile, p)
        assert p.count("bass") <= 1, p
    tags = translate("dubby", STABLE, library=lib).prompt
    assert "organ" in tags and len(re.findall(r"\becho\b", tags)) == 1


def test_soft_budget_then_hard_limit(tmp_path):
    long = "Slow rolling guitar figures repeat and grow over a steady pulse"
    lib = _lib(tmp_path, _card("big", name="Big", sound="A big, slow sound.", family="rock",
                               instruments=[f"instrument number {i}" for i in range(40)],
                               rhythm=long + ".", harmony=long + ".", production=long + ".",
                               mood="Calm, warm and steady.", tags=[f"tag{i}" for i in range(60)],
                               tempo_bpm=[60, 90]))
    budgets = {"caption+lyrics": D.CAPTION_BUDGET, "tags": D.TAGS_BUDGET, "generic": D.GENERIC_BUDGET}
    for profile in (MINIMAX, STABLE, None):
        t = translate("my own quiet words, big", profile, library=lib)
        assert len(t.prompt) <= budgets[t.dialect], (t.dialect, len(t.prompt))
        assert "my own quiet words" in t.prompt.lower()
        tight = translate("my own quiet words, big", _limit(profile or UNKNOWN, 120), library=lib)
        assert len(tight.prompt) <= 120 or any("limit" in n for n in tight.notes)
        assert "my own quiet words" in tight.prompt.lower()


def test_tempo_leans_with_the_mood(tmp_path):
    lib = _lib(tmp_path,
               _card("calm", name="Calmcore", sound="Soft.", mood="Patient and unhurried.", tempo_bpm=[60, 120]),
               _card("fast", name="Fastcore", sound="Loud.", mood="Driving and urgent.", tempo_bpm=[60, 120]),
               _card("plain", name="Plaincore", sound="Even.", mood="Bright.", tempo_bpm=[60, 120]))
    bpm = lambda text: int(re.search(r"(\d+) bpm", translate(text, STABLE, library=lib).prompt).group(1))
    assert bpm("calmcore") < 80 and bpm("fastcore") > 100 and bpm("plaincore") == 90
    assert bpm("a slow plaincore") < 80


def test_label_match_is_word_bounded(tmp_path):
    lib = _lib(tmp_path, _card("rock", name="Rock", sound="Guitars and drums, rocking hard."))
    t = translate("rocking chair vibes, rock", MINIMAX, library=lib)
    assert "Rock: guitars" in t.prompt      # "rocking" (typed or in the sound) does not count as naming the style


def test_reload_library_drops_the_cache(tmp_path, monkeypatch):
    import importlib
    T = importlib.import_module("music_prompts.translate")
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setattr(C, "_USER_DIR", user)
    try:
        T.reload_library()
        assert "zydeco-x" not in translate("zydecoxx", None).used_cards
        (user / "z.yaml").write_text(yaml.safe_dump(_card("zydeco-x", name="Zydecoxx", sound="Accordion and rubboard.")),
                                     encoding="utf-8")
        assert translate("zydecoxx", None).used_cards == ()          # still the cached library
        T.reload_library()
        assert translate("zydecoxx", None).used_cards == ("zydeco-x",)
    finally:
        monkeypatch.undo()
        T.reload_library()


def test_instrumental_flag_means_the_prompt_asks_for_no_singing(tmp_path):
    lib = _shipped(tmp_path)
    assert translate(EXAMPLE, STABLE, library=lib).instrumental is True        # vocal became a lead line
    assert translate(EXAMPLE, None, library=lib).instrumental is False         # generic prompt has a vocal clause
    assert translate(EXAMPLE, MINIMAX, library=lib).instrumental is False      # vocal and lyrics
    assert translate("tortoise-like song", MINIMAX, library=lib).instrumental is True
    assert translate("instrumental tortoise-like piece", MINIMAX, library=lib).instrumental is True
    assert translate("tortoise-like song", None, library=lib).instrumental is True


BAND = "bluegrass band doing a Tortoise-like thing"


def test_words_around_a_match_stay_one_phrase(tmp_path):
    lib = _shipped(tmp_path)
    for profile in (MINIMAX, STABLE, None):
        t = translate(BAND, profile, library=lib)
        p = t.prompt
        assert "bluegrass band doing a post-rock thing" in p.lower(), p
        for debris in (", thing", "doing a,", "doing a.", "Band doing a"):
            assert debris not in p, (debris, p)
        assert not _contains_name(p, "Tortoise")
    cap = translate(BAND, MINIMAX, library=lib).prompt
    assert cap.lower().index("vibraphone") < cap.index("bluegrass vocal:")     # the style's sound before the vocal
    t = translate("a song for her, Tortoise-like", MINIMAX, library=lib)
    assert "a song for her" in t.prompt.lower()
    t = translate("tortoise-like song", STABLE, library=lib)
    assert not re.search(r"\bsong\b", t.prompt) and any("'song'" in n for n in t.notes)
    assert "thing" in D.GLUE_WORDS and "stuff" in D.GLUE_WORDS and "for" not in D.GLUE_WORDS


def test_singing_in_the_base_layer_counts(tmp_path):
    lib = _shipped(tmp_path)
    text = "someone is singing about the sea, tortoise-like"
    t = translate(text, MINIMAX, library=lib)
    assert t.instrumental is False and t.lyrics and "The sea" in t.lyrics
    assert not any("No singing was asked" in n for n in t.notes)
    assert "instrumental" not in t.prompt.lower()
    assert "a vocal singing about the sea" in t.prompt
    assert t.prompt.lower().index("vibraphone") < t.prompt.index("a vocal singing about the sea")
    assert not any("sung lead vocals" in n for n in t.notes)
    tags = translate(text, STABLE, library=lib)
    assert "plaintive lead line carrying a melody about the sea" in tags.prompt and tags.instrumental


def test_a_vocal_layer_without_a_card_uses_the_users_words(tmp_path):
    lib = _shipped(tmp_path)
    text = "tortoise-like but someone is singing about the sea on top"
    t = translate(text, MINIMAX, library=lib)
    assert "Over it, a vocal singing about the sea." in t.prompt
    assert ":." not in t.prompt and "lead vocal" not in t.prompt
    assert not t.prompt.lower().startswith("someone")
    assert t.lyrics.startswith("[Verse]") and "The sea" in t.lyrics and t.instrumental is False
    tags = translate(text, STABLE, library=lib)
    assert "plaintive lead line carrying a melody about the sea" in tags.prompt
    assert "lead vocal" not in tags.prompt and "a vocal" not in tags.prompt
    gen = translate(text, None, library=lib)
    assert "a vocal singing about the sea" in gen.prompt and gen.instrumental is False
    bare = translate("tortoise-like but someone is singing on top", MINIMAX, library=lib)
    assert bare.lyrics is None and bare.instrumental is False
    assert any("no lyric themes were given" in n.lower() for n in bare.notes)
    assert "Over it, a vocal." in bare.prompt


def test_tags_detail_items_follow_the_layers(tmp_path):
    lib = _lib(tmp_path, _card("many", name="Manytag", sound="Busy.", tags=[f"t{i}x" for i in range(8)]),
               _card("choir", kind="vocal", name="Choir vocal", aliases=["choir"], vocal="a choir",
                     tags=["choirtag"]))
    singing = dataclasses.replace(STABLE, has_lyrics=True)
    p = translate("manytag but a choir on top", singing, library=lib).prompt
    assert p.index("choirtag") < p.index("t5x"), p
    assert p.index("t2x") < p.index("choirtag")


def test_a_style_only_in_a_later_layer_becomes_the_base(tmp_path):
    lib = _shipped(tmp_path)
    text = "very slow, with rain sounds, Tortoise-ish"
    for profile in (MINIMAX, STABLE, None):
        t = translate(text, profile, library=lib)
        p = t.prompt
        assert "Alongside it" not in p and "alongside" not in p.lower()
        assert p.lower().index("very slow") < p.index("rain sounds") < p.lower().index("vibraphone")
        assert len(re.findall(r"post-rock", p.lower())) <= 1, p


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
    clauses = [D.clause("base", "a base sound"), D.clause("mood", long_mood)]
    prompt, notes = render(clauses, "generic", None, budget=0)
    assert prompt == "A base sound." and any("1000-character limit" in n for n in notes)
    assert len(render(clauses, "generic", 5000, budget=0)[0]) > 1000


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
    # the smoother loses the user's own content words: rejected, falls back
    rainy = "make it feel like a rainy Tuesday, Tortoise-like"
    kept = translate(rainy, MINIMAX, library=lib)
    lost = translate(rainy, MINIMAX, library=lib, smooth=True, smooth_fn=smoother)
    assert calls and lost.prompt == kept.prompt and lost.notes != kept.notes

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
    chorus = t.lyrics.split("[Chorus]\n")[1].split("\n\n")[0].strip().splitlines()
    assert len(chorus) == 2 and chorus[0] == "The last train" and chorus[1].startswith("The last train, ")
    assert any("topic lines" in n for n in t.notes)
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


# ── fix round 3: promotion with a vocal, markers never silently lost ────────

def _vocal_clauses(t):
    """Vocal clauses in a prompt: a lead line ("... lead line phrased like a bluegrass vocal") is one."""
    p = re.sub(r"lead line[^,.;]*", "LEADLINE", t.prompt.lower())
    return len(re.findall(r"\bvocal\b", p)) + p.count("LEADLINE")


@pytest.mark.parametrize("text", ["very slow, with a bluegrass vocal, tortoise-like",
                                  "very slow, but someone singing a bluegrass song, tortoise-like"])
def test_promotion_with_a_vocal_card_has_exactly_one_vocal(tmp_path, text):
    lib = _shipped(tmp_path)
    for profile in (MINIMAX, STABLE, None):
        t = translate(text, profile, library=lib)
        assert "bluegrass-vocal" in t.used_cards
        assert _vocal_clauses(t) == 1, (t.dialect, t.prompt)
        assert not re.search(r"\ba vocal\b(?! singing)", t.prompt), t.prompt
        assert "Alongside it, a vocal." not in t.prompt


def test_promotion_without_a_vocal_still_works(tmp_path):
    t = translate("very slow, with rain sounds, Tortoise-ish", MINIMAX, library=_shipped(tmp_path))
    assert "vocal" not in t.prompt.lower() and "vibraphone" in t.prompt.lower() and t.instrumental


def _promoted(parsed):
    later = [i for i in range(1, len(parsed.layers)) if parsed.layers[i].matches]
    return later[0] if parsed.layers and not parsed.layers[0].matches and len(later) == 1 else None


def _markers_accounted(t, parsed):
    """Every marker is in the prompt, named in a note, or carried by a layer clause that rendered."""
    notes = " ".join(t.notes)
    promoted = _promoted(parsed)
    missing = []
    markers = [(i, l.marker) for i, l in enumerate(parsed.layers) if l.marker] + [(None, d) for d in parsed.dropped]
    for i, marker in markers:
        if f"'{marker}'" in notes or re.search(r"\b" + re.escape(marker) + r"\b", t.prompt.lower()):
            continue
        layer = parsed.layers[i] if i is not None else None
        if layer is not None and i != promoted and (layer.matches or layer.vocal):
            continue                                  # rendered as a layer clause (Over it / Alongside it / tags)
        missing.append(marker)
    return missing


MARKER_CASES = [
    "very slow, with rain sounds, Tortoise-ish",
    "someone is singing about the sea, with tortoise-like",
    "loud, with jazz",
    "jazz but loud",
    "jazz but over loud",
    "Tortoise-like, but louder",
    "Tortoise record but",
    "plus plus jazz",
]


@pytest.mark.parametrize("text", MARKER_CASES)
def test_every_marker_is_in_the_prompt_or_the_notes(tmp_path, text):
    lib = _shipped(tmp_path)
    jazz = _lib(tmp_path, _card("jazz", name="Jazz", sound="Swinging bebop.", tempo_bpm=[120, 200]))
    lib.cards.update(jazz.cards)
    lib.by_alias.update(jazz.by_alias)
    from music_prompts.parse import parse
    parsed = parse(text, lib)
    for profile in (MINIMAX, STABLE, None):
        t = translate(text, profile, library=lib)
        assert not _markers_accounted(t, parsed), (text, t.dialect, _markers_accounted(t, parsed), t.notes)


PROPERTY_CASES = MARKER_CASES + [
    "post-rock meets jazz",
    "jazz mixed with rain on top",
    "very slow layered with tortoise-like",
    "jazz over post-rock plus a bluegrass song",
    "rain, mixed with tortoise-like, but loud on top",
    "with with jazz",
    "jazz on top on top",
    "quiet, over tortoise-like but someone is singing a bluegrass song on top",
    "meets post-rock",
    "slow plus soft, with jazz meets tortoise-like",
]


def test_property_no_marker_is_silently_lost(tmp_path):
    lib = _shipped(tmp_path)
    jazz = _lib(tmp_path, _card("jazz", name="Jazz", sound="Swinging bebop.", tempo_bpm=[120, 200]))
    lib.cards.update(jazz.cards)
    lib.by_alias.update(jazz.by_alias)
    from music_prompts.parse import parse
    assert len(PROPERTY_CASES) >= 12
    seen = set()
    for text in PROPERTY_CASES:
        parsed = parse(text, lib)
        seen.update(l.marker for l in parsed.layers if l.marker)
        seen.update(parsed.dropped)
        for profile in (MINIMAX, STABLE, None):
            t = translate(text, profile, library=lib)
            assert not _markers_accounted(t, parsed), (text, t.dialect, t.prompt, t.notes)
    assert {"but", "over", "on top", "with", "plus", "meets", "mixed with", "layered with"} <= seen


def test_skeleton_note_names_the_users_topic_for_a_cardless_vocal(tmp_path):
    lib = _shipped(tmp_path)
    t = translate("tortoise-like but someone is singing about the sea on top", MINIMAX, library=lib)
    assert any("from the topic you gave" in n for n in t.notes)
    assert not any("vocal style's themes" in n for n in t.notes)
    card = translate(EXAMPLE, MINIMAX, library=lib)
    assert any("vocal style's themes" in n for n in card.notes)
