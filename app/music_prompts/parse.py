# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""parse.py - find style cards in a sentence and split it into layers.

Public API:
    parse(text, library) -> Parsed
    Match, Layer, Parsed, LAYER_MARKERS

Standard library plus cards.py only; no GTK, no LLM. Parsing is a single left
to right pass over word tokens that carry character offsets into the user's
text, so every match and every leftover phrase is the user's exact words.
`parse` is a thin driver over small private helpers (`_tokenise`,
`_find_matches`, `_find_markers`, `_split_layers`, `_leftover`).

Rules (spec 4.3 and section 10):
* Aliases match ignoring case, punctuation and hyphens ("Parliament-Funkadelic"
  and "parliament funkadelic" are the same alias; any Unicode hyphen or dash,
  U+2010 to U+2015 and U+2212, counts as a hyphen). The longest alias at a
  position wins.
* Form words belong to the match: "X-like", "X-ish", "X-style", "X style" and
  "like X". Spaces around the hyphen are fine ("X - like"). They are not
  leftover.
* An `ambiguous` card's single-word alias matches only with a signal: one of
  its `cues` within five words, a hyphenated form word ("X-like", "X-ish",
  "X-style") or "like X", or a capital letter on a word that is not the first
  word of the text and does not follow a sentence end (. ! ? ... or a newline).
  A bare "X style" is not a signal by itself. An alias inside a longer alias
  needs no signal because the longer alias is what matches. Aliases of two or
  more words are specific enough to skip the check.
* An ambiguous name with no signal still matches when the text is plainly a
  request for it (`_bare_request`): the name is the whole text, ignoring
  punctuation ("Tortoise", "Tortoise!"); the text is the name plus only
  stopwords, glue and form words ("Tortoise style", "some Tortoise", "Tortoise
  please"); or another card already matched in the text ("post-rock,
  tortoise"). A sentence with other words in it ("the tortoise crossed the
  road") still needs a signal.
* Two cards can share an alias. `Match.card_id` is the first card in card
  order (cards.py keeps that order) and `Match.alternatives` lists the others.
* Layer markers split the text. Words inside a matched alias are never marker
  words ("The Sea and Cake").
* Text before the first marker is the base layer; each later segment is a
  layer. The base layer is kept, even when empty, whenever a later layer
  exists, so `layers[0]` is always the base.

Nothing the user typed is silently lost. Every alphanumeric character ends up
exactly once in: a `Match.text`, a `Layer.marker`, `Parsed.dropped` or a
`Parsed.leftover` phrase. A marker that created no layer (trailing, leading
with nothing after it, or doubled: "jazz but over loud") is listed in
`Parsed.dropped`, in order. TASK 3 NOTE: the translator must consume
`Parsed.dropped` or `Parsed.text` for anything marker-related; the layers alone
do not carry those markers.

Punctuation: a `,` or `;` stuck to a matched name or a marker belongs to that
match or marker and is not repeated in leftover (it stays in `Layer.text`).
Punctuation anywhere else, including standalone dashes between matches, is kept
in a leftover phrase.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .cards import Library
from .dialects import GLUE_WORDS, STOPWORDS

LAYER_MARKERS: tuple[str, ...] = (
    "but", "over", "on top", "with", "plus", "and someone is singing",
    "meets", "mixed with", "layered with",
)

_CUE_WINDOW = 5
_SINGING = frozenset({"singing", "sung", "sing", "sings", "sang", "singer", "vocal", "vocals", "voice"})
_TOKEN = re.compile(r"[^\W_]+")
_HYPHENS = "-‐‑‒–—―−"
_HYPHEN_GAP = re.compile(r"\s*[" + _HYPHENS + r"]\s*")
_SENTENCE_END = ".!?…\n"
# Words that leave a text a bare request for a name ("Tortoise please", "some Tortoise").
_REQUEST_GLUE = GLUE_WORDS | STOPWORDS | frozenset({"please", "ish", "style", "like"})


@dataclass(frozen=True)
class Match:
    card_id: str
    start: int
    end: int
    text: str
    # Other cards that carry the same alias, in card order (empty when unique).
    alternatives: tuple[str, ...] = ()


@dataclass(frozen=True)
class Layer:
    role: str  # "base" or "layer"
    text: str
    matches: tuple[Match, ...]
    marker: str | None
    vocal: bool


@dataclass(frozen=True)
class Parsed:
    text: str
    layers: tuple[Layer, ...]
    leftover: tuple[str, ...]
    # Markers that created no layer, in order (see the module docstring).
    dropped: tuple[str, ...] = ()


# A token is (start, end, lowercase text, original text).
Tok = tuple[int, int, str, str]


def _tokenise(text: str) -> list[Tok]:
    return [(m.start(), m.end(), m.group().lower(), m.group()) for m in _TOKEN.finditer(text)]


def _marker_table() -> list[tuple[tuple[str, ...], str]]:
    # Longest first so "mixed with" is tried before "with".
    return sorted(((tuple(_TOKEN.findall(m)), m) for m in LAYER_MARKERS), key=lambda t: -len(t[0]))


_MARKERS = _marker_table()


def _alias_index(library: Library):
    """token tuple -> card ids (card order), plus the longest alias length.

    `by_alias` keys are already normalised (lowercase), so they are only split
    into words here, not normalised again.
    """
    index: dict[tuple[str, ...], list[str]] = {}
    for key, ids in library.by_alias.items():
        toks = tuple(_TOKEN.findall(key))
        if not toks:
            continue
        bucket = index.setdefault(toks, [])
        for i in ids:
            if i not in bucket:
                bucket.append(i)
    return index, max((len(k) for k in index), default=0)


def _has_cue(norm, lo, hi, cue_tuples) -> bool:
    """Is any cue (a word tuple) wholly inside norm[lo:hi]?"""
    for cue in cue_tuples:
        n = len(cue)
        for s in range(lo, hi - n + 1):
            if tuple(norm[s:s + n]) == cue:
                return True
    return False


def _starts_sentence(text: str, toks: list[Tok], i: int) -> bool:
    """Does token i open a sentence (a sentence end sits between it and the last token)?"""
    gap = text[toks[i - 1][1]:toks[i][0]]
    return any(c in gap for c in _SENTENCE_END)


def _find_matches(text: str, toks: list[Tok], library: Library,
                  relaxed: bool = False) -> tuple[list[Match], list[bool]]:
    """Longest-alias matches, left to right. Returns (matches, covered flags).

    `relaxed` lets an ambiguous single-word alias match with no signal (see `_bare_request`).
    """
    n = len(toks)
    norm = [t[2] for t in toks]
    index, longest = _alias_index(library)
    cue_cache: dict[str, tuple[tuple[str, ...], ...]] = {}

    def cues_of(cid: str):
        if cid not in cue_cache:
            card = library.cards.get(cid)
            cue_cache[cid] = tuple(
                c for c in (tuple(_TOKEN.findall(x.lower())) for x in (card.cues if card else ())) if c)
        return cue_cache[cid]

    def gap(a: int, b: int) -> str:
        return text[toks[a][1]:toks[b][0]]

    covered = [False] * n
    matches: list[Match] = []
    i = 0
    while i < n:
        hit = None
        for ln in range(min(longest, n - i), 0, -1):
            ids = index.get(tuple(norm[i:i + ln]))
            if not ids:
                continue
            end = i + ln  # exclusive token index
            pre = i > 0 and not covered[i - 1] and norm[i - 1] == "like" and gap(i - 1, i).isspace()
            suf_h = bare = False
            if end < n:
                if norm[end] in ("like", "ish", "style") and _HYPHEN_GAP.fullmatch(gap(end - 1, end)):
                    suf_h = True
                elif norm[end] == "style" and gap(end - 1, end).isspace():
                    bare = True
            capital = i > 0 and toks[i][3][:1].isupper() and not _starts_sentence(text, toks, i)
            ok: list[str] = []
            takes_bare = False  # a card that accepts a bare "X style" as a form
            for cid in ids:
                card = library.cards.get(cid)
                if card is None:
                    continue
                if not card.ambiguous or ln > 1:
                    ok.append(cid)
                    takes_bare = True
                elif relaxed or pre or suf_h or capital or _has_cue(
                        norm, max(0, i - _CUE_WINDOW), i, cues_of(cid)) or _has_cue(
                        norm, end, min(n, end + _CUE_WINDOW), cues_of(cid)):
                    ok.append(cid)
            if ok:
                hit = (ln, tuple(ok), pre, suf_h or (bare and takes_bare))
                break
        if hit is None:
            i += 1
            continue
        ln, ok, pre, suf = hit
        first = i - 1 if pre else i
        last = i + ln - 1 + (1 if suf else 0)
        for k in range(first, last + 1):
            covered[k] = True
        s, e = toks[first][0], toks[last][1]
        matches.append(Match(ok[0], s, e, text[s:e], ok[1:]))
        i = last + 1
    return matches, covered


def _find_markers(toks: list[Tok], covered: list[bool]) -> list[tuple[int, int, str]]:
    """Layer markers as (first token, end token exclusive, canonical marker).

    A marker never overlaps a matched token, so words inside an alias are
    never split points.
    """
    norm = [t[2] for t in toks]
    n = len(toks)
    out: list[tuple[int, int, str]] = []
    i = 0
    while i < n:
        found = None
        if not covered[i]:
            for words, name in _MARKERS:
                ln = len(words)
                if tuple(norm[i:i + ln]) == words and not any(covered[i:i + ln]):
                    found = (ln, name)
                    break
        if found:
            out.append((i, i + found[0], found[1]))
            i += found[0]
        else:
            i += 1
    return out


def _split_layers(text, toks, library, matches, markers) -> tuple[list[Layer], list[str]]:
    """Cut the text at the markers into layers; list markers that made none."""
    n = len(toks)
    norm = [t[2] for t in toks]
    # segment: (char start, char end, token start, token end, marker name, marker first token)
    segs = [(0, toks[markers[0][0]][0] if markers else len(text), 0,
             markers[0][0] if markers else n, None, 0)]
    for mi, (a, b, name) in enumerate(markers):
        nxt = markers[mi + 1][0] if mi + 1 < len(markers) else n
        segs.append((toks[b - 1][1], toks[nxt][0] if nxt < n else len(text), b, nxt, name, a))

    layers: list[Layer] = []
    dropped: list[str] = []
    mi = 0  # matches are in text order
    for si, (c0, c1, t0, t1, name, marker_tok) in enumerate(segs):
        seg_matches: list[Match] = []
        while mi < len(matches) and matches[mi].start < c1:
            if matches[mi].start >= c0:
                seg_matches.append(matches[mi])
            mi += 1
        seg_text = text[c0:c1].strip()
        if si > 0 and not seg_text and not seg_matches:
            dropped.append(name)
            continue
        vocal = any(library.cards[m.card_id].kind == "vocal" for m in seg_matches if m.card_id in library.cards)
        if not vocal:  # singing words count in the text or in the marker itself
            vocal = any(w in _SINGING for w in norm[marker_tok if name else t0:t1])
        layers.append(Layer("base" if si == 0 else "layer", seg_text, tuple(seg_matches), name, vocal))
    # An empty base with nothing after it carries nothing.
    if len(layers) == 1 and not layers[0].text and not layers[0].matches:
        layers = []
    return layers, dropped


def _leftover(text, toks, covered, matches, markers) -> tuple[str, ...]:
    """The user's words that no match or marker covers, as phrases in order.

    A phrase is a run of uncovered words cut from the text, widened over the
    punctuation set off by whitespace at its ends (stuck-on punctuation
    belongs to the neighbouring match or marker). Standalone punctuation that
    no run claims ("jazz - parliament") becomes a phrase of its own.
    """
    n = len(toks)
    blocked = list(covered)
    used = bytearray(len(text))  # characters already accounted for
    for m in matches:
        used[m.start:m.end] = b"\x01" * (m.end - m.start)
    for a, b, _ in markers:
        for k in range(a, b):
            blocked[k] = True
        s, e = toks[a][0], toks[b - 1][1]
        used[s:e] = b"\x01" * (e - s)

    found: list[tuple[int, str]] = []
    k = 0
    while k < n:
        if blocked[k]:
            k += 1
            continue
        rs = k
        while k < n and not blocked[k]:
            k += 1
        re_ = k  # exclusive
        if rs == 0:
            lo = 0
        else:
            before = text[toks[rs - 1][1]:toks[rs][0]]
            ws = next((i for i, c in enumerate(before) if c.isspace()), -1)
            lo = toks[rs - 1][1] + ws if ws >= 0 else toks[rs][0]
        tail_end = toks[re_ - 1][1]
        if re_ == n:
            hi = len(text)
        else:
            after = text[tail_end:toks[re_][0]]
            ws = max((i for i, c in enumerate(after) if c.isspace()), default=-1)
            hi = tail_end + ws + 1 if ws >= 0 else tail_end
        used[lo:hi] = b"\x01" * (hi - lo)
        phrase = text[lo:hi].strip()
        if phrase:
            found.append((lo, phrase))

    for m in re.finditer(r"\S+", text):
        chunk = m.group()
        if not any(c.isalnum() for c in chunk) and used.find(1, m.start(), m.end()) == -1:
            found.append((m.start(), chunk))
    found.sort(key=lambda t: t[0])
    return tuple(p for _, p in found)


def _ambiguous_spans(toks: list[Tok], library: Library) -> list[int]:
    """Token positions holding a single-word alias of an ambiguous card."""
    out = []
    for i, t in enumerate(toks):
        for cid in library.by_alias.get(t[2], ()):
            card = library.cards.get(cid)
            if card is not None and card.ambiguous:
                out.append(i)
                break
    return out


def _bare_request(toks: list[Tok], library: Library, matches: list[Match], covered: list[bool]) -> bool:
    """Is an unmatched ambiguous name plainly what the text asks for? (module docstring)"""
    amb = [i for i in _ambiguous_spans(toks, library) if not covered[i]]
    if not amb:
        return False
    if matches:
        return True
    rest = [t[2] for i, t in enumerate(toks) if i not in amb]
    return all(w in _REQUEST_GLUE for w in rest)


def parse(text: str, library: Library) -> Parsed:
    toks = _tokenise(text)
    if not toks:
        return Parsed(text, (), (), ())
    matches, covered = _find_matches(text, toks, library)
    if _bare_request(toks, library, matches, covered):
        matches, covered = _find_matches(text, toks, library, relaxed=True)
    markers = _find_markers(toks, covered)
    layers, dropped = _split_layers(text, toks, library, matches, markers)
    return Parsed(text, tuple(layers), _leftover(text, toks, covered, matches, markers), tuple(dropped))
