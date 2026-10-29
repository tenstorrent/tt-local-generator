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
A typed "a"/"an" right before a replaced name agrees with the style that
replaces it ("a Kraftwerk groove" -> "an electropop groove"; `D.fix_article`).
`inspired_by` always carries the names for the UI.

`avoid` never reaches the prompt; it is listed in `notes` ("Not asked for:").
When a vocal is asked for, entries about singing are left out of that note.

Tempo: the middle of the base card's range, leaning to the middle of the
lower third when the base card's mood or the user's words say patient, slow,
unhurried (and similar), or the upper third when they say fast or driving.
Key: the base card's `key`, else a vocal card's.

Instrumental: `Translation.instrumental` means the rendered prompt asks for no
singing: the user typed "instrumental", nothing in the request sings, or the
vocal became a lead line (tags dialect, a model that cannot sing). A prompt
with a vocal clause is not instrumental even when no lyrics were written
(generic dialect, or no lyric themes). Request-building code uses it to send
the server's instrumental form (MiniMax refuses a job with no lyrics otherwise).

Words next to a match form one phrase with it (see `_user_words`), so
"bluegrass band doing a Tortoise-like thing" keeps "bluegrass band doing a
post-rock thing". Singing typed in any layer, the base included, makes a vocal;
with no vocal card the vocal uses the user's words ("a vocal singing about the
sea") and the sung topic becomes the lyric theme. With no theme at all no
skeleton is written and a note asks for lyrics or a vocal style.

When the base layer has no card and exactly one later layer does, that layer
becomes the base.

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
from .parse import _SINGING, Layer, Match, Parsed, parse

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
NO_THEMES_NOTE = "No lyric themes were given: write the lyrics, or pick a vocal style."
SKELETON_TOPIC_NOTE = ("The lyrics are topic lines from the topic you gave, not finished lyrics. "
                       "A MiniMax song ends when its lyrics do, so a thin skeleton makes a short clip: "
                       "write the lines out for a longer one.")
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
    # Set only by `wand`: the seed that made this result, so a surprise can be reproduced.
    seed_used: int | None = None


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
        label = n.label
        label = label[:1].upper() + label[1:] if _starts_sentence(text, s) else D._lc_first(label)
        out.append(text[last:s])
        out[:] = [D.fix_article("".join(out), label)]      # "a Kraftwerk groove" -> "an electropop groove"
        out.append(label)
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

_SING = re.compile(r"\b(singing|sings|sing|sang|sung)\b", re.I)
_TOPIC_LEAD = re.compile(r"^(about|of|for|to|on)\s+", re.I)
_HYPHENS = "-‐‑‒–—―−"
_FORM = re.compile(r"^(?P<pre>like\s+)?(?P<core>.*?)(?P<suf>\s*[" + _HYPHENS + r"]\s*(?:like|ish|style)|\s+style)?$",
                   re.I | re.S)


@dataclass
class _Words:
    """One piece of the user's own words: a content phrase, possibly built around a match."""
    text: str
    layer: int
    has_match: bool


def _find_spans(text: str, pieces, blocked) -> list[tuple[int, int]]:
    """Positions of `pieces` (in text order), skipping spans that overlap `blocked`."""
    out, cursor = [], 0
    for piece in pieces:
        pos = text.find(piece, cursor)
        while pos >= 0 and any(pos < b and pos + len(piece) > a for a, b in blocked):
            pos = text.find(piece, pos + 1)
        if pos < 0:                      # cannot happen for parser output; keep going safely
            out.append((cursor, cursor))
            continue
        out.append((pos, pos + len(piece)))
        cursor = pos + len(piece)
    return out


def _match_words(m, card: StyleCard, lib: Library, names, include_names: bool, text: str) -> str:
    """How a match reads inside the user's own phrase: its words, or its style for a name."""
    named = card.kind == "artist" or _name_spans(m.text, names, case_sensitive=False)
    if include_names or not named:
        return m.text
    f = _FORM.match(m.text)
    pre = f.group("pre") or ""
    label = _label(card, lib)
    core = (f.group("core") or m.text).lstrip()
    if not pre and core[:1].isupper() and _starts_sentence(text, m.start):
        return label[:1].upper() + label[1:]
    return pre + D._lc_first(label)


def _user_words(text: str, parsed: Parsed, lib: Library, names, include_names: bool):
    """The user's leftover words as content pieces and glue phrases, in text order.

    A leftover phrase directly next to a match (whitespace only, no punctuation, same layer)
    joins it in one phrase, with the match read as its style ("a bluegrass band doing a
    post-rock thing"). Such a run is content when any of its phrases is content; a run whose
    phrases are all glue ("tortoise-like song") is glue. Phrases on their own are content or
    glue by `GLUE_WORDS` (vocal cues count as glue only when the parser made a vocal layer).
    """
    glue_words = set(D.GLUE_WORDS)
    if not any(layer.vocal for layer in parsed.layers):
        glue_words -= D.VOCAL_CUES

    def is_glue(phrase: str) -> bool:
        words = [w.lower() for w in _WORD.findall(phrase)]
        return all(w in glue_words for w in words)

    layer_spans = _find_spans(text, [l.text for l in parsed.layers], [])
    match_items = [(m.start, m.end, "m", m) for l in parsed.layers for m in l.matches if m.card_id in lib.cards]
    phrases = [p for p in parsed.leftover if _WORD.search(p)]           # punctuation alone is neither
    spans = _find_spans(text, phrases, [(a, b) for a, b, _, _ in match_items])
    items = sorted(match_items + [(a, b, "p", p) for (a, b), p in zip(spans, phrases)], key=lambda t: t[0])

    def layer_of(pos: int) -> int:
        best = 0
        for i, (a, _) in enumerate(layer_spans):
            if a <= pos:
                best = i
        return best

    runs: list[list] = []
    for it in items:
        prev = runs[-1][-1] if runs else None
        if prev is not None:
            gap = text[prev[1]:it[0]]
            joined = (gap == "" or gap.isspace()) and text[prev[1] - 1].isalnum() and text[it[0]].isalnum() \
                and layer_of(prev[0]) == layer_of(it[0])
            if joined:
                runs[-1].append(it)
                continue
        runs.append([it])

    content: list[_Words] = []
    glue: list[str] = []
    for run in runs:
        ps = [it for it in run if it[2] == "p"]
        if not ps:
            continue
        if any(it[2] == "m" for it in run) and not all(is_glue(it[3]) for it in ps):
            out, last = [], run[0][0]
            for a, b, kind, obj in run:
                out.append(text[last:a])
                if kind == "m":
                    shown = _match_words(obj, lib.cards[obj.card_id], lib, names, include_names, text)
                    if shown != obj.text:               # a replaced name: a typed "a"/"an" agrees with it
                        out[:] = [D.fix_article("".join(out), shown)]
                    out.append(shown)
                else:
                    out.append(text[a:b])
                last = b
            content.append(_Words("".join(out).strip().strip(",;:").strip(), layer_of(run[0][0]), True))
            continue
        for a, b, _, phrase in ps:
            if is_glue(phrase):
                glue.append(phrase.strip(" ,;:"))
            else:
                content.append(_Words(phrase.strip().strip(",;:").strip(), layer_of(a), False))
    return content, glue, is_glue


def _sung_part(words: _Words, is_glue) -> tuple[D.Part, list[str], str]:
    """A vocal from the user's own words ("someone is singing about the sea").

    Returns the part, glue words it consumed ("someone is") and the sung topic ("the sea").
    """
    m = _SING.search(words.text)
    before, after = words.text[:m.start()].strip(), words.text[m.end():].strip(" ,;:.")
    if before and not is_glue(before):
        desc = words.text.strip(" ,;:.")
        glue = []
    else:
        desc = "a vocal singing" + (f" {after}" if after else "")
        glue = [before] if before else []
    topic = _TOPIC_LEAD.sub("", after).strip()
    return D.Part(kind="vocal", label="", sound="", vocal=desc, sung=after), glue, topic


# ── the plan ─────────────────────────────────────────────────────────────────

def _part(card: StyleCard, lib: Library, scrub) -> D.Part:
    kind = {"vocal": "vocal", "texture": "texture"}.get(card.kind, "sound")
    label = scrub(_label(card, lib))
    return D.Part(kind=kind, label=label, sound=scrub(card.sound),
                  tags=tuple(scrub(t) for t in card.tags), vocal=scrub(card.vocal),
                  lead_instrument=scrub(card.lead_instrument), card_id=card.id,
                  # an artist card is named by its style in notes, like everywhere else
                  card_name=label if card.kind == "artist" else card.name)


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
    text = text if isinstance(text, str) else ""
    if not text.strip():
        return Translation("", None, (EMPTY_NOTE,), (), (), dialect, (), True)
    return _render(text, parse(text, lib), profile, lib, include_names=include_names,
                   smooth=smooth, smooth_fn=smooth_fn, lyrics_fn=lyrics_fn)


def translate_cards(base_ids, layer_ids=(), user_text: str = "", profile=None, *,
                    include_names: bool = False, library: Library | None = None,
                    smooth: bool = False, smooth_fn=None, lyrics_fn=None) -> Translation:
    """Render chosen cards exactly as typed text naming them would (the wand's path).

    `base_ids` are the base layer's card ids; `layer_ids` is a sequence of
    `(marker, ids)` layers after it ("with", "over", "and someone is singing", ...).
    `user_text` is optional typed text: it is parsed as usual and the card layers
    are appended after its own layers (when it has no layer at all, `base_ids`
    becomes the base). The cards go through the same `_render` as `translate`, so
    the result equals `translate("A with B over C")` for the same cards. Ids that
    are not in the library are skipped.
    """
    lib = library if library is not None else _default_library()
    user_text = user_text if isinstance(user_text, str) else ""
    parsed = parse(user_text, lib) if user_text.strip() else Parsed(user_text, (), (), ())
    text, layers = parsed.text, list(parsed.layers)

    def add(marker, ids):
        nonlocal text
        cards = [lib.cards[i] for i in ids if i in lib.cards]
        if not cards:
            return
        if text.strip():
            text += f" {marker} " if marker else ", "
        elif marker:
            marker = None                       # the first layer is the base: no marker
        matches = []
        start = len(text)
        for k, c in enumerate(cards):
            if k:
                text += ", "
            matches.append(Match(c.id, len(text), len(text) + len(c.name), c.name))
            text += c.name
        vocal = any(c.kind == "vocal" for c in cards) or bool(
            marker and any(w in _SINGING for w in _WORD.findall(marker.lower())))
        layers.append(Layer("layer" if layers else "base", text[start:], tuple(matches),
                            marker if layers else None, vocal))

    add("with" if layers else None, base_ids)    # the user's own layers come first and are the base
    for marker, ids in layer_ids:
        add(marker, ids)
    if not layers:
        dialect = D.dialect_for(profile)
        return Translation("", None, (EMPTY_NOTE,), (), (), dialect, (), True)
    return _render(text, Parsed(text, tuple(layers), parsed.leftover, parsed.dropped), profile, lib,
                   include_names=include_names, smooth=smooth, smooth_fn=smooth_fn, lyrics_fn=lyrics_fn,
                   typed=bool(user_text.strip()))


def _render(text: str, parsed: Parsed, profile, lib: Library, *, include_names: bool, smooth: bool,
            smooth_fn, lyrics_fn, typed: bool = True) -> Translation:
    """The back half of translate(): a parsed request to a Translation (one rendering path).

    `typed` is False when only code chose the cards (the wand with no text): the user typed no artist name, so the
    note "'Name' became the description of its sound" is not written (it would only show a name).
    """
    dialect = D.dialect_for(profile)
    limit = D.limit_of(getattr(profile, "max_chars", None))
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
        if c.kind == "artist" and not include_names and typed:
            notes.append(f"'{m.text}' became the description of its sound; the name is listed under Inspired by.")

    # The user's words: content kept verbatim (a phrase next to a match joins it), glue named in a note.
    words, glue, is_glue = _user_words(text, parsed, lib, names, include_names)
    instrumental_asked = bool(_INSTRUMENTAL.search(text))

    # Cards per layer. When the base has none and exactly one later layer has some, that layer
    # is the base ("very slow, with rain sounds, Tortoise-ish" is a post-rock base).
    layers = list(parsed.layers)
    layer_cards = [[lib.cards[m.card_id] for m in l.matches if m.card_id in lib.cards] for l in layers]
    orig_cards = [list(cs) for cs in layer_cards]      # before promotion: "does layer i have a vocal card?"
    promoted = None
    if layer_cards and not layer_cards[0]:
        with_cards = [i for i in range(1, len(layers)) if layer_cards[i]]
        if len(with_cards) == 1:
            promoted = with_cards[0]
            layer_cards[0], layer_cards[promoted] = layer_cards[promoted], []
    base_cards = layer_cards[0] if layer_cards else []
    vocal_cards: list[StyleCard] = [c for cs in layer_cards for c in cs if c.kind == "vocal"]

    # A vocal layer (the base included) with no vocal card takes its voice from the user's words.
    sung: dict[int, tuple[D.Part, str]] = {}
    for i, layer in enumerate(layers):
        if not layer.vocal or any(c.kind == "vocal" for c in orig_cards[i] + layer_cards[i]):
            continue
        w = next((w for w in words if w.layer == i and not w.has_match and _SING.search(w.text)), None)
        if w is not None:
            part, consumed, topic = _sung_part(w, is_glue)
            words.remove(w)
            glue += consumed
        else:
            part, topic = D.Part(kind="vocal", label="", sound="", vocal="a vocal"), ""
        sung[i] = (part, topic)
    has_vocal = bool(vocal_cards) or bool(sung)

    # A marker whose layer renders no clause of its own (promoted to the base, or nothing in it)
    # is not in the prompt: name it in the glue note so no typed word is silently lost.
    for i in range(1, len(layers)):
        if layers[i].marker and (i == promoted or not (layer_cards[i] or i in sung)):
            glue.append(layers[i].marker)

    content = [w.text for w in words]
    if not include_names:
        replaced = []
        skip = _ambiguous_aliases(lib)
        content = [_scrub(p, names, case_sensitive=True, skip=skip, replaced=replaced) for p in content]
        _name_notes(replaced, notes)
    if instrumental_asked and not any(_INSTRUMENTAL.search(p) for p in content):
        content.append("instrumental")
    glue_all = list(dict.fromkeys(glue + list(parsed.dropped)))

    if glue_all:
        what = "the layout of the request (a vocal layer)" if has_vocal else "the layout of the request"
        quoted = D._and([f"'{g}'" for g in glue_all])
        verb = "was" if len(glue_all) == 1 else "were"
        notes.append(f"{quoted} {verb} read as {what} and not repeated in the prompt.")

    all_used, all_layer_cards, all_vocal_cards = list(used), layer_cards, vocal_cards

    def render_once(excluded: set, left_out: list):
        """Compose and trim without the `excluded` cards (ones an earlier pass trimmed away)."""
        nonlocal used, vocal_cards, has_vocal
        used = [c for c in all_used if c.id not in excluded]
        vocal_cards = [c for c in all_vocal_cards if c.id not in excluded]
        has_vocal = bool(vocal_cards) or bool(sung)
        plan_layers = []
        if 0 in sung:                                # singing typed in the base sits on top of it
            plan_layers.append((None, (sung[0][0],)))
        for i in range(1, len(layers)):
            parts = [_part(c, lib, scrub) for c in all_layer_cards[i] if c.id not in excluded]
            if i in sung:
                parts.append(sung[i][0])
            if parts:
                plan_layers.append((layers[i].marker, tuple(parts)))
        sound_cards = [c for c in used if c.kind != "vocal"]
        primary = [c for c in base_cards if c.kind not in ("vocal", "texture")]
        if not primary:
            primary = next(([c for c in cs if c.kind not in ("vocal", "texture")] for cs in layer_cards[1:]
                            if any(c.kind not in ("vocal", "texture") for c in cs)), [])
        tempo_cards = primary + [c for c in used if c not in primary]
        key = next((c.key for c in primary if c.key), "") or next((c.key for c in vocal_cards if c.key), "") \
            or next((c.key for c in used if c.key), "")

        pass_notes: list[str] = []
        avoid: list[str] = []
        for c in used:
            for a in c.avoid:
                if (has_vocal or instrumental_asked) and _VOICE_WORDS.search(a):
                    continue
                if a not in avoid:
                    avoid.append(scrub(a))
        if avoid:
            pass_notes.append(f"Not asked for: {D._and(avoid)}. These stay out of the prompt, because music models "
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
        # The rendered prompt asks for no singing: an instrumental was asked for, nothing sings,
        # or the vocal became a lead line (tags for a model that cannot sing). A card's own voice
        # (`vocal` on a style or artist card) is described only when the prompt sings.
        instrumental = instrumental_asked or not has_vocal or (dialect == D.TAGS and not can_sing)
        plan = D.Plan(
            user_words=tuple(content),
            # the style's sound first, a vocal card in the base after it
            base=tuple(_part(c, lib, scrub) for c in sorted(base_cards, key=lambda c: c.kind == "vocal")),
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
            voices=not instrumental,
        )
        clauses, compose_notes = D.compose(plan, dialect)
        kept, trim_notes = D.trim(clauses, dialect, limit, left_out=left_out)
        prompt = D._join(kept, dialect)
        # The prompt's first character is capitalised even when it is the user's (only that one
        # character), except when the user's words open with the profile's trigger phrase, which
        # must reach the model exactly as typed. Tags stay lowercase.
        triggers = tuple(getattr(profile, "triggers", ()) or ())
        if dialect != D.TAGS and kept and kept[0].kind == "user" and not any(
                prompt.lower().startswith(t.lower()) for t in triggers if t):
            prompt = prompt[:1].upper() + prompt[1:]
        return locals()

    # Truthful reporting: a card counts as used only when at least one of its clauses is in the
    # prompt. A card that trimming removed completely is taken out and the prompt is rendered
    # again without it, so none of its instruments, tempo or names linger; the note names it.
    excluded: set = set()
    left_out: list = []
    for _ in range(len(all_used) + 1):
        r = render_once(excluded, left_out)
        kept_ids = {cid for c in r["kept"] for cid in c.cards}
        composed = {cid: n for c in r["clauses"] for cid, n in zip(c.cards, c.card_names)}
        gone = [cid for cid in composed if cid not in kept_ids and cid not in excluded]
        if not gone:
            break
        excluded |= set(gone)
        left_out += [n for n in (D._lc_first(composed[cid]) for cid in gone) if n not in left_out]
    kept, prompt = r["kept"], r["prompt"]
    instrumental, can_sing = r["instrumental"], r["can_sing"]
    # Only cards the prompt describes: a kept clause of their own, or the user's own words that
    # stay in the prompt and name them ("rainy Tuesday slowcore" keeps "slowcore" as typed).
    user_text = " ".join(c.text for c in kept if c.kind == "user").lower()
    typed_in = {m.card_id for m in matches if m.text.strip() and m.text.lower() in user_text}
    used = [c for c in used if c.id in kept_ids or c.id in typed_in]
    inspired = list(dict.fromkeys(n for c in used for n in ((c.name,) if c.kind == "artist" else ()) + c.inspiration))
    notes += r["pass_notes"] + list(r["compose_notes"]) + list(r["trim_notes"])
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
            if card:
                vocal_text, themes = card.vocal or card.sound, card.lyric_themes
            else:
                part, topic = next(iter(sung.values()))
                vocal_text, themes = part.vocal, ((topic,) if topic else ())
            lyrics = _lyrics(vocal_text, themes, lyrics_fn, names, include_names, notes,
                             SKELETON_NOTE if card else SKELETON_TOPIC_NOTE)

    return Translation(prompt, lyrics, tuple(dict.fromkeys(notes)), tuple(inspired),
                       tuple(c.id for c in used), dialect, added, instrumental)


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


def _lyrics(vocal_text, themes, lyrics_fn, names, include_names, notes, skeleton_note=None) -> str | None:
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
    if not themes:
        notes.append(NO_THEMES_NOTE)
        return None
    notes.append(skeleton_note or SKELETON_NOTE)
    return skeleton(themes)
