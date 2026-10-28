# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""translate.py - typed text to a prompt (and lyrics) for one music model.

Public API:
    Translation      - frozen dataclass, the result
    translate(text, profile, *, include_names=False, library=None, smooth=False,
              smooth_fn=None, lyrics_fn=None) -> Translation
    reload_library() - forget the cached default library (after cards change)
    skeleton(themes) - the lyrics skeleton used without an LLM

Steps (spec 4.3): parse the text into layers and card matches, build a `Plan`
(the user's content words, base parts, layer parts, tempo, key and the base
card's details), compose it for the dialect, trim it to the soft budget and the
hard limit, then optionally smooth it and write lyrics. Deterministic: no LLM
call unless the caller passes `smooth=True` with a `smooth_fn`, or a `lyrics_fn`.

The user's own words. Every leftover phrase from the parser is either content
or glue. A phrase is glue when every word in it is in `dialects.GLUE_WORDS`:
stopwords, generic piece nouns ("song", "track", "piece"), the markers, and
(only when the parser made a vocal layer) the vocal cues it read ("someone",
"singing"). Glue phrases and markers that started no layer (`Parsed.dropped`)
are left out of the prompt and named in one note, so nothing is lost silently.
Content phrases ("rainy Tuesday", "très lent", "instrumental") are kept
verbatim and in order; only the first character of the whole prompt may be
capitalised. Text with no match at all passes through exactly as typed.

Names (spec 6, plan Review Focus 1). Matched names never reach the prompt:
a match is replaced by its card's description, and an artist card shows its
parent style's name as its label (`_label`). Unless `include_names=True`:
* an `inspiration` entry typed outside any match (no card of its own) is
  replaced by its card's style, matched case-sensitively as the card writes it,
  and never when the entry is also an alias of an ambiguous card;
* card text is scrubbed the same way, guarding against a user card that names
  an artist in its `sound`;
* smoothed text and LLM lyrics that add a name are refused.
`inspired_by` always carries the names for the UI.

`avoid` never reaches the prompt; it is listed in `notes` ("Not asked for:").
When a vocal is asked for, entries about singing are left out of that note.

Tempo: the middle of the base card's range, leaning to the middle of the
lower third when the base card's mood or the user's words say patient, slow,
unhurried (and similar), or the upper third when they say fast or driving.
Key: the base card's `key`, else a vocal card's.

Instrumental: `Translation.instrumental` is True when no lyrics are produced
(the user typed "instrumental", nothing sings, or the model cannot sing), so
request-building code can send the server's instrumental form.

Triggers: a profile's trigger phrase ("fiona crapple") is never added by the
library. If the user typed it, it is a content phrase and stays.

The default library is loaded once per process and cached (`_DEFAULT_LIBRARY`);
call `reload_library()` after user cards change. Callers that pass `library`
are not affected.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

from . import dialects as D
from .cards import Library, StyleCard, load_library, normalise_alias
from .parse import Parsed, parse

log = logging.getLogger(__name__)

_WORD = re.compile(r"[^\W_]+")
_INSTRUMENTAL = re.compile(r"\binstrumental\b", re.I)
# `avoid` entries about singing are not listed when the user asks for a vocal.
_VOICE_WORDS = re.compile(r"\b(vocals?|sung|sing\w*|voices?|verse\w*|chorus\w*|lyric\w*)\b", re.I)
_SENTENCE_END = ".!?…\n"
_SLOW_WORDS = frozenset("patient slow slowly unhurried calm gentle lazy languid relaxed sleepy hushed".split())
_FAST_WORDS = frozenset("fast driving energetic frantic upbeat urgent rapid quick frenetic".split())

EMPTY_NOTE = ("Type a style, an artist, a scene or a mood to start, for example "
              "'tortoise-like song but someone is singing a bluegrass song on top'.")
PASS_THROUGH_NOTE = "No style in the library matched, so your text is passed through unchanged."
SKELETON_NOTE = ("The lyrics are topic lines from the vocal style's themes, not finished lyrics. "
                 "A MiniMax song ends when its lyrics do, so a thin skeleton makes a short clip: "
                 "write the lines out for a longer one.")


@dataclass(frozen=True)
class Translation:
    prompt: str
    lyrics: str | None
    notes: tuple[str, ...]
    inspired_by: tuple[str, ...]
    used_cards: tuple[str, ...]
    dialect: str
    added: tuple[str, ...]
    instrumental: bool = False


_DEFAULT_LIBRARY: Library | None = None


def _default_library() -> Library:
    global _DEFAULT_LIBRARY
    if _DEFAULT_LIBRARY is None:
        _DEFAULT_LIBRARY = load_library()
    return _DEFAULT_LIBRARY


def reload_library() -> None:
    """Forget the cached default library; the next translate() loads the cards again."""
    global _DEFAULT_LIBRARY
    _DEFAULT_LIBRARY = None


# ── names ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Name:
    words: tuple[str, ...]   # as the card writes them
    text: str
    label: str               # the style shown instead


def _label(card: StyleCard, lib: Library) -> str:
    """The style name shown for a card: an artist card shows its parent style, never its own name."""
    if card.kind == "artist":
        p = lib.cards.get(card.parent) if card.parent else None
        return p.name if p is not None and p.kind != "artist" else card.family
    return card.name


def _names(lib: Library) -> list[_Name]:
    """Every inspiration entry, and every artist card's name and aliases; longest first."""
    out: dict[tuple[str, ...], _Name] = {}
    for c in lib.cards.values():
        label = _label(c, lib)
        entries = list(c.inspiration) + ([c.name, *c.aliases] if c.kind == "artist" else [])
        for e in entries:
            w = tuple(_WORD.findall(e))
            if w and w not in out:
                out[w] = _Name(w, e, label)
    return sorted(out.values(), key=lambda n: -len(n.words))


def _ambiguous_aliases(lib: Library) -> set[str]:
    return {normalise_alias(a) for c in lib.cards.values() if c.ambiguous for a in (c.name, *c.aliases)}


def _name_spans(text: str, names: list[_Name], *, case_sensitive: bool, skip=frozenset()):
    """Non-overlapping (start, end, _Name) for each name found in text, left to right."""
    toks = [(m.start(), m.end(), m.group()) for m in _WORD.finditer(text)]
    key = (lambda s: s) if case_sensitive else str.lower
    words = [key(t[2]) for t in toks]
    spans = []
    i = 0
    while i < len(toks):
        for n in names:
            k = len(n.words)
            if normalise_alias(n.text) in skip:
                continue
            if tuple(words[i:i + k]) == tuple(key(w) for w in n.words):
                spans.append((toks[i][0], toks[i + k - 1][1], n))
                i += k
                break
        else:
            i += 1
    return spans


def _count_names(text: str, names: list[_Name]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _, _, n in _name_spans(text, names, case_sensitive=False):
        counts[n.text] = counts.get(n.text, 0) + 1
    return counts


def _adds_names(before: str, after: str, names: list[_Name]) -> bool:
    b = _count_names(before, names)
    return any(v > b.get(k, 0) for k, v in _count_names(after, names).items())


def _starts_sentence(text: str, pos: int) -> bool:
    before = text[:pos].rstrip()
    return not before or before[-1] in _SENTENCE_END


def _scrub(text: str, names: list[_Name], *, case_sensitive: bool, skip=frozenset(), replaced=None) -> str:
    """Replace each name with its style label; record (name, label) pairs in `replaced`."""
    out, last = [], 0
    for s, e, n in _name_spans(text, names, case_sensitive=case_sensitive, skip=skip):
        out.append(text[last:s])
        label = n.label
        out.append(label[:1].upper() + label[1:] if _starts_sentence(text, s) else D._lc_first(label))
        if replaced is not None:
            replaced.append((text[s:e], n.label))
        last = e
    out.append(text[last:])
    return "".join(out)


def _name_notes(replaced, notes: list[str]) -> None:
    for typed, label in dict.fromkeys(replaced):
        notes.append(f"'{typed}' became its style ({D._lc_first(label)}); "
                     "names stay out of the prompt unless you ask for them.")


# ── the user's words ─────────────────────────────────────────────────────────

def _split_words(parsed: Parsed) -> tuple[list[str], list[str]]:
    """Leftover phrases as (content, glue), each in text order. Punctuation-only phrases are neither."""
    glue_words = set(D.GLUE_WORDS)
    if not any(layer.vocal for layer in parsed.layers):
        glue_words -= D.VOCAL_CUES - D.STOPWORDS
    content, glue = [], []
    for phrase in parsed.leftover:
        words = [w.lower() for w in _WORD.findall(phrase)]
        if not words:
            continue
        if all(w in glue_words for w in words):
            glue.append(phrase.strip(" ,;:"))
        else:
            content.append(phrase.strip().strip(",;:").strip())
    return content, glue


# ── the plan ─────────────────────────────────────────────────────────────────

def _part(card: StyleCard, lib: Library, scrub) -> D.Part:
    kind = {"vocal": "vocal", "texture": "texture"}.get(card.kind, "sound")
    return D.Part(kind=kind, label=scrub(_label(card, lib)), sound=scrub(card.sound),
                  tags=tuple(scrub(t) for t in card.tags), vocal=scrub(card.vocal),
                  lead_instrument=scrub(card.lead_instrument))


def _bpm(cards, extra_text: str) -> int | None:
    """The tempo from the first card with a range, leaning with the mood words (module docstring)."""
    for c in cards:
        if c.tempo_bpm:
            lo, hi = c.tempo_bpm
            words = {w.lower() for w in _WORD.findall(c.mood + " " + extra_text)}
            slow, fast = bool(words & _SLOW_WORDS), bool(words & _FAST_WORDS)
            if slow and not fast:
                return lo + (hi - lo) // 6
            if fast and not slow:
                return hi - (hi - lo) // 6
            return (lo + hi) // 2
    return None


def _join_field(cards, attr: str) -> str:
    texts = [getattr(c, attr).strip() for c in cards if getattr(c, attr).strip()]
    return " ".join(D._sentence(D._cap(t)) for t in texts)


def translate(text: str, profile, *, include_names: bool = False, library: Library | None = None,
              smooth: bool = False, smooth_fn: Callable[[str], str | None] | None = None,
              lyrics_fn: Callable[[str, tuple[str, ...]], str | None] | None = None) -> Translation:
    lib = library if library is not None else _default_library()
    dialect = D.dialect_for(profile)
    limit = D.limit_of(getattr(profile, "max_chars", None))
    text = text if isinstance(text, str) else ""
    if not text.strip():
        return Translation("", None, (EMPTY_NOTE,), (), (), dialect, (), True)

    parsed = parse(text, lib)
    names = _names(lib)
    notes: list[str] = []
    matches = [m for layer in parsed.layers for m in layer.matches if m.card_id in lib.cards]

    if not matches:
        prompt = text.strip()
        if not include_names:
            replaced: list = []
            prompt = _scrub(prompt, names, case_sensitive=True, skip=_ambiguous_aliases(lib), replaced=replaced)
            _name_notes(replaced, notes)
        notes.insert(0, PASS_THROUGH_NOTE)
        if len(prompt) > limit:
            notes.append(D.over_limit_note(len(prompt), limit))
        return Translation(prompt, None, tuple(notes), (), (), dialect, (), True)

    def scrub(s: str) -> str:
        return s if include_names else _scrub(s, names, case_sensitive=True)

    used: list[StyleCard] = []
    for m in matches:
        c = lib.cards[m.card_id]
        if c not in used:
            used.append(c)
        if m.alternatives:
            others = ", ".join(lib.cards[a].name for a in m.alternatives if a in lib.cards)
            notes.append(f"'{m.text}' could also mean {others}; {c.name} was used.")
        if c.kind == "artist" and not include_names:
            notes.append(f"'{m.text}' became the description of its sound; the name is listed under Inspired by.")

    # The user's words: content kept verbatim, glue named in a note.
    content, glue = _split_words(parsed)
    if not include_names:
        replaced = []
        skip = _ambiguous_aliases(lib)
        content = [_scrub(p, names, case_sensitive=True, skip=skip, replaced=replaced) for p in content]
        _name_notes(replaced, notes)
    instrumental_asked = bool(_INSTRUMENTAL.search(text))
    if instrumental_asked and not any(_INSTRUMENTAL.search(p) for p in content):
        content.append("instrumental")
    glue_all = list(dict.fromkeys(glue + list(parsed.dropped)))

    # Base and layers. A layer whose only signal is singing words (no vocal card) still gets a voice.
    layers = list(parsed.layers)
    base_cards = [lib.cards[m.card_id] for m in (layers[0].matches if layers else ()) if m.card_id in lib.cards]
    plan_layers = []
    vocal_cards: list[StyleCard] = [c for c in base_cards if c.kind == "vocal"]
    vocal_texts: list[str] = []
    for layer in layers[1:]:
        cs = [lib.cards[m.card_id] for m in layer.matches if m.card_id in lib.cards]
        parts = [_part(c, lib, scrub) for c in cs]
        vocal_cards += [c for c in cs if c.kind == "vocal"]
        if layer.vocal and not any(c.kind == "vocal" for c in cs):
            parts.append(D.Part(kind="vocal", label="lead vocal", sound="", vocal=""))
            vocal_texts.append(layer.text)
        if parts:
            plan_layers.append((layer.marker, tuple(parts)))
    has_vocal = bool(vocal_cards) or bool(vocal_texts)
    if glue_all:
        what = "the layout of the request (a vocal layer)" if has_vocal else "the layout of the request"
        quoted = D._and([f"'{g}'" for g in glue_all])
        verb = "was" if len(glue_all) == 1 else "were"
        notes.append(f"{quoted} {verb} read as {what} and not repeated in the prompt.")

    sound_cards = [c for c in used if c.kind != "vocal"]
    primary = [c for c in base_cards if c.kind not in ("vocal", "texture")]
    if not primary:
        for layer in layers[1:]:
            primary = [lib.cards[m.card_id] for m in layer.matches
                       if m.card_id in lib.cards and lib.cards[m.card_id].kind not in ("vocal", "texture")]
            if primary:
                break
    tempo_cards = primary + [c for c in used if c not in primary]
    key = next((c.key for c in primary if c.key), "") or next((c.key for c in vocal_cards if c.key), "") \
        or next((c.key for c in used if c.key), "")

    avoid: list[str] = []
    for c in used:
        for a in c.avoid:
            if (has_vocal or instrumental_asked) and _VOICE_WORDS.search(a):
                continue
            if a not in avoid:
                avoid.append(scrub(a))
    if avoid:
        notes.append(f"Not asked for: {D._and(avoid)}. These stay out of the prompt, because music models "
                     "tend to hear a 'no X' as 'X'.")
    instruments: list[str] = []
    for c in sound_cards:
        for i in c.instruments:
            if i not in instruments:
                instruments.append(i)

    inspired: list[str] = []
    for c in used:
        for n in ((c.name,) if c.kind == "artist" else ()) + c.inspiration:
            if n not in inspired:
                inspired.append(n)

    can_sing = bool(getattr(profile, "has_lyrics", False))
    plan = D.Plan(
        user_words=tuple(content),
        base=tuple(_part(c, lib, scrub) for c in base_cards),
        layers=tuple(plan_layers),
        names=tuple(inspired) if include_names else (),
        instruments=tuple(scrub(i) for i in instruments),
        rhythm=scrub(_join_field(primary, "rhythm")),
        harmony=scrub(_join_field(primary, "harmony")),
        production=scrub(_join_field(primary, "production")),
        mood=scrub(_join_field(primary, "mood")),
        bpm=_bpm(tempo_cards, " ".join(content)),
        key=scrub(key),
        can_sing=can_sing,
        instrumental=instrumental_asked,
    )
    clauses, compose_notes = D.compose(plan, dialect)
    kept, trim_notes = D.trim(clauses, dialect, limit)
    prompt = D._join(kept, dialect)
    # The prompt's first character is capitalised even when it is the user's (only that one
    # character), except when the user's words open with the profile's trigger phrase, which
    # must reach the model exactly as typed. Tags stay lowercase.
    triggers = tuple(getattr(profile, "triggers", ()) or ())
    if dialect != D.TAGS and kept and kept[0].kind == "user" and not any(
            prompt.lower().startswith(t.lower()) for t in triggers if t):
        prompt = prompt[:1].upper() + prompt[1:]
    notes += list(compose_notes) + list(trim_notes)
    added = tuple(c.text for c in kept if c.kind != "user")

    if smooth:
        prompt = _smooth(prompt, smooth_fn, content, names, limit, notes)

    lyrics = None
    if dialect == D.CAPTION and can_sing:
        if instrumental_asked:
            notes.append("You asked for an instrumental, so no vocal or lyrics were written.")
        elif not has_vocal:
            notes.append("No singing was asked for, so no lyrics were written; the piece is instrumental.")
        else:
            card = vocal_cards[0] if vocal_cards else None
            vocal_text = (card.vocal or card.sound) if card else (vocal_texts[0] if vocal_texts else "")
            themes = card.lyric_themes if card else ()
            lyrics = _lyrics(vocal_text, themes, lyrics_fn, names, include_names, notes)

    return Translation(prompt, lyrics, tuple(dict.fromkeys(notes)), tuple(inspired),
                       tuple(c.id for c in used), dialect, added, lyrics is None)


# ── optional steps ───────────────────────────────────────────────────────────

def _smooth(prompt, smooth_fn, content, names, limit, notes) -> str:
    """Run the smoother; keep its text only if it keeps the user's content words, adds no names and fits."""
    if smooth_fn is None:
        notes.append("Smoothing was asked for but no smoother is available; the prompt is unsmoothed.")
        return prompt
    try:
        out = smooth_fn(prompt)
    except Exception as exc:                       # an LLM failure never costs the user their prompt
        log.warning("music prompt smoother failed: %s", exc)
        out = None
    if not isinstance(out, str) or not out.strip():
        notes.append("Smoothing gave nothing back; the prompt is unsmoothed.")
        return prompt
    out = out.strip()
    if _adds_names(prompt, out, names):
        notes.append("Smoothing added an artist name, so the unsmoothed prompt was kept.")
        return prompt
    # Compare without the first character, which the prompt may have capitalised.
    if any(p[1:] in prompt and p[1:] not in out for p in content if len(p) > 1):
        notes.append("Smoothing changed your own words, so the unsmoothed prompt was kept.")
        return prompt
    if len(out) > max(limit, len(prompt)):
        notes.append("Smoothing made the prompt too long, so the unsmoothed prompt was kept.")
        return prompt
    return out


def skeleton(themes) -> str:
    """[Verse]/[Chorus] topic lines from a vocal card's themes; tags on their own lines.

    The chorus is two lines: its theme, then the theme with the first theme
    after it ("A lost love" / "A lost love, a long road home").
    """
    lines = [t.strip()[:1].upper() + t.strip()[1:] for t in themes if t and t.strip()]
    if not lines:
        return "[Verse]\n\n[Chorus]\n"
    hook = lines[2] if len(lines) > 2 else lines[0]
    other = lines[0] if hook != lines[0] else (lines[1] if len(lines) > 1 else "")
    chorus = [hook] + ([f"{hook}, {D._lc_first(other)}"] if other else [])
    verse1, verse2 = lines[:2], lines[3:5]
    out = ["[Verse]", *verse1, "", "[Chorus]", *chorus]
    if verse2:
        out += ["", "[Verse]", *verse2, "", "[Chorus]", *chorus]
    return "\n".join(out) + "\n"


def _lyrics(vocal_text, themes, lyrics_fn, names, include_names, notes) -> str:
    if lyrics_fn is not None:
        try:
            got = lyrics_fn(vocal_text, tuple(themes))
        except Exception as exc:
            log.warning("music lyrics writer failed: %s", exc)
            got = None
        if isinstance(got, str) and got.strip():
            if include_names or not _name_spans(got, names, case_sensitive=False):
                return got
            notes.append("The written lyrics named an artist, so a skeleton was used instead.")
    notes.append(SKELETON_NOTE)
    return skeleton(themes)
