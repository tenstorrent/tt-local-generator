# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""translate.py - typed text to a prompt (and lyrics) for one music model.

Public API:
    Translation   - frozen dataclass, the result
    translate(text, profile, *, include_names=False, library=None, smooth=False,
              smooth_fn=None, lyrics_fn=None) -> Translation

Steps (spec 4.3): parse the text into layers and card matches, build a `Plan`
(the user's own line, base parts, layer parts, tempo and the base card's
details), compose it for the dialect, trim it to the model's limit, then
optionally smooth it and write lyrics. Deterministic: no LLM call unless the
caller passes `smooth=True` with a `smooth_fn`, or a `lyrics_fn`.

The user's own words. Every phrase the parser leaves unmatched
(`Parsed.leftover`) is kept verbatim and in order, in a "user line": the typed
text with only the recognised artist names replaced. The user line is emitted
whenever there is a leftover phrase or a marker that created no layer
(`Parsed.dropped`, for example a trailing "but" or "on top"); those markers
therefore stay in the prompt, and a note says they started no layer. When the
text is all matched names and layer markers, the descriptions say everything
and no user line is needed. Text with no match at all passes through exactly
as typed, with a note.

Names (spec 6, plan Review Focus 1). Unless `include_names=True`:
* a match on an artist card, or a match whose words contain an artist or
  inspiration name, becomes the name of the style it points to
  ("Tortoise-like song" -> "post-rock song"; "like Tortoise" -> "like post-rock");
* an `inspiration` entry typed outside any match (it has no card of its own)
  is replaced by its card's style, matched case-sensitively as the card writes
  it, and never when the entry is also an alias of an ambiguous card (the
  parser decides those: "the tortoise crossed the road" stays as typed);
* card text is scrubbed the same way, as a guard against a user card that
  names an artist in its `sound`;
* smoothed text and LLM lyrics that add a name are refused.
`inspired_by` always carries the names for the UI.

Triggers: a profile's trigger phrase ("fiona crapple") is never added by the
library. If the user typed it, it is part of their own words and stays.
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
# An `avoid` entry that argues with a requested vocal is left out when one exists.
_VOICE_WORDS = re.compile(r"\b(vocals?|sung|sing\w*|voices?|verse\w*|chorus\w*|lyric\w*)\b", re.I)
# Form words around a matched name (parse.py's forms); the name is the middle.
_HYPHENS = "-‐‑‒–—―−"
_FORM = re.compile(r"^(?P<pre>like\s+)?(?P<core>.*?)(?P<suf>\s*[" + _HYPHENS + r"]\s*(?:like|ish|style)|\s+style)?$",
                   re.I | re.S)
_SENTENCE_END = ".!?…\n"

EMPTY_NOTE = ("Type a style, an artist, a scene or a mood to start, for example "
              "'tortoise-like song but someone is singing a bluegrass song on top'.")
PASS_THROUGH_NOTE = "No style in the library matched, so your text is passed through unchanged."


@dataclass(frozen=True)
class Translation:
    prompt: str
    lyrics: str | None
    notes: tuple[str, ...]
    inspired_by: tuple[str, ...]
    used_cards: tuple[str, ...]
    dialect: str
    added: tuple[str, ...]


_DEFAULT_LIBRARY: Library | None = None


def _default_library() -> Library:
    global _DEFAULT_LIBRARY
    if _DEFAULT_LIBRARY is None:
        _DEFAULT_LIBRARY = load_library()
    return _DEFAULT_LIBRARY


# ── names ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Name:
    words: tuple[str, ...]   # as the card writes them
    text: str
    label: str               # the style shown instead


def _label(card: StyleCard, lib: Library) -> str:
    """The style name shown for a card: an artist card shows its parent style."""
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


def _case_like(label: str, text: str, pos: int) -> str:
    """A label cased for where it lands: capital only where a sentence starts."""
    return label[:1].upper() + label[1:] if _starts_sentence(text, pos) else D._lc_first(label)


def _scrub(text: str, names: list[_Name], *, case_sensitive: bool, skip=frozenset(), replaced=None) -> str:
    """Replace each name with its style label; record (name, label) pairs in `replaced`."""
    out, last = [], 0
    for s, e, n in _name_spans(text, names, case_sensitive=case_sensitive, skip=skip):
        out.append(text[last:s])
        out.append(_case_like(n.label, text, s))
        if replaced is not None:
            replaced.append((text[s:e], n.label))
        last = e
    out.append(text[last:])
    return "".join(out)


# ── the user line ────────────────────────────────────────────────────────────

def _user_line(text: str, parsed: Parsed, lib: Library, names: list[_Name], include_names: bool,
               notes: list[str]) -> str:
    """The typed text with artist matches (and stray inspiration names) replaced by their style."""
    if include_names:
        return text.strip()
    skip = _ambiguous_aliases(lib)
    replaced: list[tuple[str, str]] = []
    out, last = [], 0
    matches = sorted((m for layer in parsed.layers for m in layer.matches), key=lambda m: m.start)
    for m in matches:
        card = lib.cards.get(m.card_id)
        if card is None:
            continue
        out.append(_scrub(text[last:m.start], names, case_sensitive=True, skip=skip, replaced=replaced))
        named = card.kind == "artist" or _name_spans(m.text, names, case_sensitive=False)
        if named:
            f = _FORM.match(m.text)
            pre = f.group("pre") or ""
            label = _label(card, lib)
            core = (f.group("core") or m.text).lstrip()
            if not pre and core[:1].isupper() and _starts_sentence(text, m.start):
                out.append(label[:1].upper() + label[1:])      # "Tortoise-like song" opening a sentence
            else:
                out.append(pre + D._lc_first(label))           # follows the typed lowercase or mid-sentence
            replaced.append((m.text, label))
        else:
            out.append(m.text)
        last = m.end
    out.append(_scrub(text[last:], names, case_sensitive=True, skip=skip, replaced=replaced))
    for typed, label in dict.fromkeys(replaced):
        notes.append(f"'{typed}' became its style ({D._lc_first(label)}) and the description of its sound. "
                     "Names stay out of the prompt unless you ask for them; see Inspired by.")
    return "".join(out).strip()


# ── the plan ─────────────────────────────────────────────────────────────────

def _part(card: StyleCard, lib: Library, scrub) -> D.Part:
    if card.kind == "vocal":
        kind = "vocal"
    elif card.kind == "texture":
        kind = "texture"
    else:
        kind = "sound"
    return D.Part(kind=kind, label=scrub(_label(card, lib)), sound=scrub(card.sound),
                  tags=tuple(scrub(t) for t in card.tags), vocal=scrub(card.vocal),
                  lead_instrument=scrub(card.lead_instrument))


def _bpm(cards) -> int | None:
    for c in cards:
        if c.tempo_bpm:
            return (c.tempo_bpm[0] + c.tempo_bpm[1]) // 2
    return None


def _join_field(cards, attr: str) -> str:
    return " ".join(D._cap(getattr(c, attr).strip()) if getattr(c, attr).strip()[-1:] in ".!?"
                    else D._cap(getattr(c, attr).strip()) + "."
                    for c in cards if getattr(c, attr).strip())


def translate(text: str, profile, *, include_names: bool = False, library: Library | None = None,
              smooth: bool = False, smooth_fn: Callable[[str], str | None] | None = None,
              lyrics_fn: Callable[[str, tuple[str, ...]], str | None] | None = None) -> Translation:
    lib = library if library is not None else _default_library()
    dialect = D.dialect_for(profile)
    limit = D.limit_of(getattr(profile, "max_chars", None))
    text = text if isinstance(text, str) else ""
    if not text.strip():
        return Translation("", None, (EMPTY_NOTE,), (), (), dialect, ())

    parsed = parse(text, lib)
    names = _names(lib)
    notes: list[str] = []
    matches = [m for layer in parsed.layers for m in layer.matches if m.card_id in lib.cards]

    if not matches:
        prompt = text.strip()
        if not include_names:
            replaced: list = []
            prompt = _scrub(prompt, names, case_sensitive=True, skip=_ambiguous_aliases(lib), replaced=replaced)
            for typed, label in dict.fromkeys(replaced):
                notes.append(f"'{typed}' became its style ({D._lc_first(label)}); "
                             "names stay out of the prompt unless you ask for them.")
        notes.insert(0, PASS_THROUGH_NOTE)
        if len(prompt) > limit:
            notes.append(D.over_limit_note(len(prompt), limit))
        return Translation(prompt, None, tuple(notes), (), (), dialect, ())

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

    sound_cards = [c for c in used if c.kind != "vocal"]
    primary = [c for c in base_cards if c.kind not in ("vocal", "texture")]
    if not primary:
        for layer in layers[1:]:
            primary = [lib.cards[m.card_id] for m in layer.matches
                       if m.card_id in lib.cards and lib.cards[m.card_id].kind not in ("vocal", "texture")]
            if primary:
                break
    tempo_cards = primary + [c for c in used if c not in primary]

    avoid: list[str] = []
    for c in used:
        for a in c.avoid:
            if has_vocal and _VOICE_WORDS.search(a):
                continue
            if a not in avoid:
                avoid.append(a)
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

    show_line = bool(parsed.leftover) or bool(parsed.dropped)
    user_line = _user_line(text, parsed, lib, names, include_names, notes) if show_line else None
    for marker in dict.fromkeys(parsed.dropped):
        notes.append(f"'{marker}' started no new layer (nothing came after it); it stays in your words.")

    can_sing = bool(getattr(profile, "has_lyrics", False))
    plan = D.Plan(
        user_line=user_line,
        base=tuple(_part(c, lib, scrub) for c in base_cards),
        layers=tuple(plan_layers),
        names=tuple(inspired) if include_names else (),
        instruments=tuple(scrub(i) for i in instruments),
        rhythm=scrub(_join_field(primary, "rhythm")),
        harmony=scrub(_join_field(primary, "harmony")),
        production=scrub(_join_field(primary, "production")),
        mood=scrub(_join_field(primary, "mood")),
        bpm=_bpm(tempo_cards),
        avoid=tuple(scrub(a) for a in avoid),
        can_sing=can_sing,
    )
    clauses, compose_notes = D.compose(plan, dialect)
    kept, trim_notes = D.trim(clauses, dialect, limit)
    prompt = D._join(kept, dialect)
    notes += list(compose_notes) + list(trim_notes)
    added = tuple(c.text for c in kept if c.kind != "user")

    if smooth:
        prompt = _smooth(prompt, smooth_fn, parsed, user_line, names, limit, notes)

    lyrics = None
    instrumental = bool(_INSTRUMENTAL.search(text))
    if dialect == D.CAPTION and can_sing:
        if not has_vocal:
            notes.append("No singing was asked for, so no lyrics were written; the piece is instrumental.")
        elif instrumental:
            notes.append("You asked for an instrumental, so no lyrics were written.")
        else:
            card = vocal_cards[0] if vocal_cards else None
            vocal_text = (card.vocal or card.sound) if card else (vocal_texts[0] if vocal_texts else "")
            themes = card.lyric_themes if card else ()
            lyrics = _lyrics(vocal_text, themes, lyrics_fn, names, include_names, notes)

    return Translation(prompt, lyrics, tuple(dict.fromkeys(notes)), tuple(inspired),
                       tuple(c.id for c in used), dialect, added)


# ── optional steps ───────────────────────────────────────────────────────────

def _smooth(prompt, smooth_fn, parsed, user_line, names, limit, notes) -> str:
    """Run the smoother; keep its text only if it keeps the user's words, adds no names and fits."""
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
    keep = [p for p in parsed.leftover if p in prompt]
    if user_line and user_line in prompt:
        keep.append(user_line)
    if any(p not in out for p in keep):
        notes.append("Smoothing changed your own words, so the unsmoothed prompt was kept.")
        return prompt
    if len(out) > max(limit, len(prompt)):
        notes.append("Smoothing made the prompt too long, so the unsmoothed prompt was kept.")
        return prompt
    return out


def skeleton(themes) -> str:
    """[Verse]/[Chorus] lyrics from a vocal card's themes, one theme per line, tags on their own lines."""
    lines = [t.strip()[:1].upper() + t.strip()[1:] for t in themes if t and t.strip()]
    if not lines:
        return "[Verse]\n\n[Chorus]\n"
    verse1, chorus, verse2 = lines[:2], lines[2:3] or lines[:1], lines[3:5]
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
    notes.append("The lyrics are a skeleton from the vocal style's themes: replace each line with your own words.")
    return skeleton(themes)
