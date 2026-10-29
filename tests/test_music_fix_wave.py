"""Final whole-branch review of the music prompt library: the fix wave (GTK-free parts).

Each test is a repro input from the review. The panel parts live in test_music_prompt_panel.py."""
import importlib.machinery
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from music_prompts import cards as C  # noqa: E402
from music_prompts import dialects as D  # noqa: E402
from music_prompts import translate  # noqa: E402  (the function; the package attribute shadows the module)
from music_runtime import profile_for  # noqa: E402

T = sys.modules["music_prompts.translate"]   # the module

MINIMAX = profile_for({"status": "ok", "model": "fiona-crapple-merged"})
STABLE = profile_for({"status": "ok", "model": "stable-audio-3 small-music"})
UNKNOWN = profile_for({"status": "ok", "model": "mystery-synth"})
PROFILES = (MINIMAX, STABLE, UNKNOWN)


def _shipped(tmp_path):
    return C.load_library(user_dir=tmp_path / "no-user-cards")


def _lib(tmp_path, *raw):
    d = tmp_path / "cards"
    d.mkdir()
    (d / "c.yaml").write_text(yaml.safe_dump(list(raw)), encoding="utf-8")
    return C.load_library(shipped_dir=d, user_dir=tmp_path / "none")


# ── 1. a card typed twice is described once ─────────────────────────────────


@pytest.mark.parametrize("text, phrase", [
    ("post-rock, Tortoise, post rock", "rock played for texture"),
    ("dub with dub", "reggae stripped down"),
    ("dub, dub", "reggae stripped down"),
])
@pytest.mark.parametrize("profile", PROFILES, ids=["minimax", "stable", "generic"])
def test_a_card_typed_twice_is_described_once(tmp_path, text, phrase, profile):
    t = translate(text, profile, library=_shipped(tmp_path))
    assert t.prompt.lower().count(phrase) <= 1, t.prompt


@pytest.mark.parametrize("text", ["dub, " * 100, "dub with " * 60 + "dub", "post-rock and " * 80 + "post-rock"],
                         ids=["comma", "with", "and"])
def test_repeated_input_is_bounded(tmp_path, text):
    lib = _shipped(tmp_path)
    for p in PROFILES:
        t0 = time.monotonic()
        t = translate(text, p, library=lib)
        assert time.monotonic() - t0 < 0.5
        assert len(t.prompt) <= 1000
        assert t.prompt.lower().count("reggae stripped down") <= 1


# ── 3. a trimmed card sets nothing ──────────────────────────────────────────


def test_a_trimmed_card_sets_no_tempo_key_mood_rhythm_or_production(tmp_path):
    lib = _lib(
        tmp_path,
        {"id": "croon", "kind": "vocal", "name": "Croon", "family": "vox", "sound": "A soft crooned voice.",
         "lyric_themes": ["the moon"]},
        {"id": "zoom", "kind": "style", "name": "Zoom", "family": "fast", "sound": "Zoomy noise. " * 70,
         "tempo_bpm": [170, 180], "key": "F sharp minor", "mood": "Frantic and wild.",
         "rhythm": "Blast beats everywhere.", "production": "Crushed to bits."},
    )
    t = translate("croon with zoom", MINIMAX, library=lib)
    assert "zoom" not in t.used_cards
    for gone in ("bpm", "F sharp minor", "rantic", "Blast beats", "Crushed"):
        assert gone not in t.prompt, (gone, t.prompt)


# ── 4. a marker that holds the singing word keeps the sung topic ────────────


@pytest.mark.parametrize("text", ["post-rock and someone is singing about the sea",
                                  "post-rock, someone is singing about the sea"])
def test_singing_marker_keeps_the_sung_topic(tmp_path, text):
    t = translate(text, MINIMAX, library=_shipped(tmp_path))
    assert t.lyrics and "sea" in t.lyrics.lower()
    assert "singing about the sea" in t.prompt
    assert not t.prompt.lower().startswith("about the sea")


# ── 5. names ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["surf rock with Alison Krauss singing about the sea",
                                  "surf rock with alison krauss singing about the sea"])
@pytest.mark.parametrize("profile", PROFILES, ids=["minimax", "stable", "generic"])
def test_sung_phrase_and_lowercase_multiword_names_are_scrubbed(tmp_path, text, profile):
    t = translate(text, profile, library=_shipped(tmp_path))
    assert "krauss" not in t.prompt.lower(), t.prompt
    assert "krauss" not in (t.lyrics or "").lower()
    if profile is MINIMAX:
        assert "a bluegrass vocal singing about the sea" in t.prompt


def test_single_word_names_stay_case_sensitive(tmp_path):
    t = translate("a slow song about faust and the sea", UNKNOWN, library=_shipped(tmp_path))
    assert "faust" in t.prompt


def test_lyrics_with_a_lowercase_single_word_name_are_not_refused(tmp_path):
    lib = _shipped(tmp_path)
    ok = "[Verse]\nA tortoise crossed the road\nparliament sat late\n"
    t = translate("a bluegrass song", MINIMAX, library=lib, lyrics_fn=lambda v, th: ok)
    assert t.lyrics == ok
    bad = "[Verse]\nlike alison krauss on the radio\n"
    t = translate("a bluegrass song", MINIMAX, library=lib, lyrics_fn=lambda v, th: bad)
    assert t.lyrics != bad
    bad = "[Verse]\nTortoise on the radio\n"
    t = translate("a bluegrass song", MINIMAX, library=lib, lyrics_fn=lambda v, th: bad)
    assert t.lyrics != bad


# ── 8. "In the style of" describes the sound only ──────────────────────────


def test_style_description_has_no_tempo_mood_or_key(tmp_path):
    lib = _shipped(tmp_path)
    for p in PROFILES:
        d = T.style_description("Tortoise", p, library=lib)
        assert d and "bpm" not in d and "mood" not in d.lower() and "tortoise" not in d.lower(), d
    assert T.style_description("Tortoise", STABLE, library=lib).startswith("chicago post-rock")
    assert "vibraphone" in T.style_description("Tortoise", MINIMAX, library=lib)
    assert T.style_description("Zorblax Q", MINIMAX, library=lib) is None


# ── 9. parliament and bluegrass need a signal ──────────────────────────────


def test_parliament_is_ambiguous(tmp_path):
    lib = _shipped(tmp_path)
    t = translate("a folk protest song about parliament", MINIMAX, library=lib)
    assert "parliament" not in t.used_cards and "parliament" in t.prompt
    assert "parliament" in translate("parliament-like funk", MINIMAX, library=lib).used_cards
    assert "parliament" in translate("p-funk in the style of parliament with bootsy", MINIMAX,
                                     library=lib).used_cards


@pytest.mark.parametrize("text", ["bluegrass band", "bluegrass band instrumental jam"])
def test_bluegrass_band_is_not_a_vocal(tmp_path, text):
    t = translate(text, MINIMAX, library=_shipped(tmp_path))
    assert "bluegrass-vocal" not in t.used_cards


@pytest.mark.parametrize("text", ["tortoise-like song but someone is singing a bluegrass song on top",
                                  "post-rock with bluegrass singing", "bluegrass"])
def test_bluegrass_with_a_vocal_cue_still_matches(tmp_path, text):
    assert "bluegrass-vocal" in translate(text, MINIMAX, library=_shipped(tmp_path)).used_cards


# ── cheap fixes ─────────────────────────────────────────────────────────────


def test_tags_mood_split_on_full_stops(tmp_path):
    assert D._phrases("Dark. Brooding and slow.") == ["Dark", "Brooding", "slow"]
    lib = _lib(tmp_path, {"id": "murk", "kind": "style", "name": "Murk", "family": "m", "sound": "Murky sound.",
                          "mood": "Dark. Brooding and slow."})
    t = translate("murk", STABLE, library=lib)
    assert all(". " not in tag for tag in t.prompt.split(", ")), t.prompt


def test_inspire_does_not_retranslate_a_written_prompt(monkeypatch):
    import prompt_client
    written = translate("Tortoise", None).prompt
    monkeypatch.setattr(prompt_client, "_music_llm_text", lambda s, t: "FROM LLM")
    assert prompt_client.generate_prompt("music", written) == "FROM LLM"


_loader = importlib.machinery.SourceFileLoader("tt_ctl_fix_wave", str(Path(__file__).parent.parent / "tt-ctl"))
_spec = importlib.util.spec_from_loader("tt_ctl_fix_wave", _loader)
tt_ctl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tt_ctl)


def test_cli_wand_passes_include_names(monkeypatch, capsys):
    import music_prompts
    seen = {}

    def fake(profile, **kw):
        seen.update(kw)
        return music_prompts.Translation("p", None, (), (), (), "generic", ())
    monkeypatch.setattr(music_prompts, "wand", fake)
    tt_ctl.cmd_music_prompt(tt_ctl._build_parser().parse_args(["music-prompt", "--wand", "--include-names"]))
    assert seen.get("include_names") is True


def test_wand_include_names_reaches_the_prompt(tmp_path):
    from music_prompts import wand
    lib = _shipped(tmp_path)
    names = {n.text for n in T._names(lib)}
    hit = False
    for seed in range(40):
        t = wand(MINIMAX, seed=seed, library=lib, include_names=True)
        if any(n in t.prompt for n in t.inspired_by if n in names):
            hit = True
            break
    assert hit


def test_cli_seed_without_wand_exits_two(capsys):
    with pytest.raises(SystemExit) as e:
        tt_ctl.cmd_music_prompt(tt_ctl._build_parser().parse_args(["music-prompt", "jazz", "--seed", "3"]))
    assert e.value.code == 2
    assert "--seed" in capsys.readouterr().err


# ── fix wave 2 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("phrase", ["a one bedroom apartment", "the meters running out", "american beauty and blue lines",
                                    "a massive attack of drums", "my bloody valentine"])
def test_ordinary_phrases_that_spell_a_title_pass_through(tmp_path, phrase):
    lib = _shipped(tmp_path)
    for p in PROFILES:
        t = translate(f"a jazz ballad about {phrase}", p, library=lib)
        assert phrase in t.prompt.lower(), (phrase, t.prompt)


@pytest.mark.parametrize("name", ["alison krauss", "alice coltrane", "fela kuti"])
def test_lowercase_distinctive_names_are_still_scrubbed(tmp_path, name):
    t = translate(f"surf rock, a tribute to {name}", MINIMAX, library=_shipped(tmp_path))
    assert name not in t.prompt.lower(), t.prompt


def test_title_case_common_word_names_are_still_scrubbed(tmp_path):
    t = translate("surf rock, a tribute to Massive Attack and My Bloody Valentine", MINIMAX, library=_shipped(tmp_path))
    assert "Massive Attack" not in t.prompt and "Bloody Valentine" not in t.prompt, t.prompt


@pytest.mark.parametrize("text, lyric", [
    ("post-rock and someone is singing about the sea", "The sea"),
    ("post-rock and someone is singing softly about the sea", "The sea"),
])
def test_marker_topic_skips_direction_words(tmp_path, text, lyric):
    t = translate(text, MINIMAX, library=_shipped(tmp_path))
    assert t.lyrics and t.lyrics.splitlines()[1] == lyric, t.lyrics
    assert "softly" not in (t.lyrics or "").lower()


def test_marker_does_not_take_a_direction_after_a_comma(tmp_path):
    t = translate("post-rock and someone is singing, very slow", MINIMAX, library=_shipped(tmp_path))
    assert "very slow" not in (t.lyrics or "").lower()
    assert "very slow" in t.prompt.lower()
    assert "singing very slow" not in t.prompt.lower()          # a direction, not something sung


@pytest.mark.parametrize("text", ["gospel choir, and someone is singing gospel",
                                  "bluegrass song and someone is singing a bluegrass song"])
def test_a_repeated_vocal_card_makes_no_second_generic_vocal(tmp_path, text):
    t = translate(text, MINIMAX, library=_shipped(tmp_path))
    assert "a vocal." not in t.prompt and "it, a vocal" not in t.prompt, t.prompt


def test_article_before_a_style_that_replaced_a_name(tmp_path):
    t = translate("post-rock, Bill Monroe singing about the sea", MINIMAX, library=_shipped(tmp_path))
    assert "a bluegrass vocal singing about the sea" in t.prompt, t.prompt


@pytest.mark.parametrize("value", ["Massive Attack", "alison krauss"])
def test_style_description_for_an_inspiration_only_name(tmp_path, value):
    lib = _shipped(tmp_path)
    for p in PROFILES:
        d = T.style_description(value, p, library=lib)
        assert d and value.lower() not in d.lower(), (value, d)
