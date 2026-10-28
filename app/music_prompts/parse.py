# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""parse.py - find style cards in a sentence and split it into layers.

Public API:
    parse(text, library) -> Parsed
    Match, Layer, Parsed, LAYER_MARKERS

Standard library plus cards.py only; no GTK, no LLM. Parsing is a single left
to right pass over word tokens that carry character offsets into the user's
text, so every match and every leftover phrase is the user's exact words.

Rules (spec 4.3 and section 10):
* Aliases match ignoring case, punctuation and hyphens ("Parliament-Funkadelic"
  and "parliament funkadelic" are the same alias). The longest alias at a
  position wins.
* Form words belong to the match: "X-like", "X-ish", "X-style", "X style" and
  "like X". They are not left over.
* An `ambiguous` card's single-word alias matches only with a signal: one of
  its `cues` within five words, a form word (above), or a capital letter on a
  word that is not the first word of the text. An alias inside a longer alias
  needs no signal because the longer alias is what matches. Aliases of two or
  more words are specific enough to skip the check.
* Two cards can share an alias. `Match.card_id` is the first card in card
  order (cards.py keeps that order) and `Match.alternatives` lists the others.
* Layer markers split the text. Words inside a matched alias are never marker
  words ("The Sea and Cake"). Marker words are structure and are not leftover.
* Text before the first marker is the base layer; each later segment is a
  layer. A layer with no text (a trailing "on top") is dropped. The base layer
  is kept, even when empty, whenever a later layer exists, so `layers[0]` is
  always the base.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .cards import Library, normalise_alias

LAYER_MARKERS: tuple[str, ...] = (
    "but", "over", "on top", "with", "plus", "and someone is singing",
    "meets", "mixed with", "layered with",
)

_CUE_WINDOW = 5
_SINGING = frozenset({"singing", "sung", "vocal", "vocals", "voice"})
_TOKEN = re.compile(r"[^\W_]+")


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


def _words(s: str) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(s.lower()))


def _marker_table() -> list[tuple[tuple[str, ...], str]]:
    # Longest first so "mixed with" is tried before "with".
    return sorted(((_words(m), m) for m in LAYER_MARKERS), key=lambda t: -len(t[0]))


def _alias_index(library: Library):
    """token tuple -> card ids (card order), plus the longest alias length."""
    index: dict[tuple[str, ...], list[str]] = {}
    for key, ids in library.by_alias.items():
        toks = _words(normalise_alias(key))
        if not toks:
            continue
        bucket = index.setdefault(toks, [])
        for i in ids:
            if i not in bucket:
                bucket.append(i)
    longest = max((len(k) for k in index), default=0)
    return index, longest


def _has_cue(norm, lo, hi, cue_tuples) -> bool:
    """Is any cue (a word tuple) wholly inside norm[lo:hi]?"""
    for cue in cue_tuples:
        n = len(cue)
        for s in range(lo, hi - n + 1):
            if tuple(norm[s:s + n]) == cue:
                return True
    return False


def parse(text: str, library: Library) -> Parsed:
    toks = [(m.start(), m.end(), m.group().lower(), m.group()) for m in _TOKEN.finditer(text)]
    if not toks:
        return Parsed(text, (), ())
    norm = [t[2] for t in toks]
    n = len(toks)
    index, longest = _alias_index(library)
    cue_cache: dict[str, tuple[tuple[str, ...], ...]] = {}

    def cues_of(card_id: str):
        if card_id not in cue_cache:
            card = library.cards.get(card_id)
            cue_cache[card_id] = tuple(c for c in (_words(x) for x in (card.cues if card else ())) if c)
        return cue_cache[card_id]

    covered = [False] * n  # token is part of a match (or its form words)
    matches: list[Match] = []

    def hyphen_joined(a: int, b: int) -> bool:
        """Tokens a and b (a < b) are separated by exactly one '-'."""
        return text[toks[a][1]:toks[b][0]] == "-"

    def only_space(a: int, b: int) -> bool:
        return text[toks[a][1]:toks[b][0]].isspace()

    i = 0
    while i < n:
        hit = None
        for ln in range(min(longest, n - i), 0, -1):
            ids = index.get(tuple(norm[i:i + ln]))
            if not ids:
                continue
            end = i + ln  # exclusive token index
            # Form words: "like X" before, "X-like"/"X-ish"/"X-style"/"X style" after.
            pre = i > 0 and not covered[i - 1] and norm[i - 1] == "like" and only_space(i - 1, i)
            suf = False
            if end < n:
                nxt = norm[end]
                if nxt in ("like", "ish") and hyphen_joined(end - 1, end):
                    suf = True
                elif nxt == "style" and (hyphen_joined(end - 1, end) or only_space(end - 1, end)):
                    suf = True
            ok = []
            for cid in ids:
                card = library.cards.get(cid)
                if card is None:
                    continue
                if not card.ambiguous or ln > 1 or pre or suf:
                    ok.append(cid)
                    continue
                capital = i > 0 and toks[i][3][:1].isupper()
                near = _has_cue(norm, max(0, i - _CUE_WINDOW), i, cues_of(cid)) or _has_cue(
                    norm, end, min(n, end + _CUE_WINDOW), cues_of(cid))
                if capital or near:
                    ok.append(cid)
            if ok:
                hit = (ln, tuple(ok), pre, suf)
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

    # Marker words: any marker occurrence not touching a matched token. Tokens
    # of a marker are "structure" and also split the text into segments.
    markers: list[tuple[int, int, str]] = []  # (first token, end token excl, marker)
    table = _marker_table()
    taken = [False] * n
    i = 0
    while i < n:
        found = None
        if not covered[i]:
            for words, name in table:
                ln = len(words)
                if tuple(norm[i:i + ln]) == words and not any(covered[i:i + ln]):
                    found = (ln, name)
                    break
        if found:
            ln, name = found
            markers.append((i, i + ln, name))
            for k in range(i, i + ln):
                taken[k] = True
            i += ln
        else:
            i += 1

    # Segments: base is [0, first marker); each marker opens the next segment.
    bounds = [(0, markers[0][0] if markers else n, None, 0)]
    for mi, (a, b, name) in enumerate(markers):
        nxt = markers[mi + 1][0] if mi + 1 < len(markers) else n
        bounds.append((b, nxt, name, a))

    layers: list[Layer] = []
    leftover: list[str] = []
    mi = 0  # matches are in text order; walk them with the segments
    for si, (t0, t1, name, marker_tok) in enumerate(bounds):
        seg_matches: list[Match] = []
        # character range of the segment text (marker words excluded)
        c0 = toks[t0][0] if t0 < n else len(text)
        c1 = toks[t1][0] if t1 < n else len(text)
        while mi < len(matches) and matches[mi].start < c1:
            if matches[mi].start >= c0:
                seg_matches.append(matches[mi])
            mi += 1
        # Leftover phrases: runs of uncovered, non-marker tokens, each cut
        # from the user's text between the neighbouring covered tokens.
        k = t0
        while k < t1:
            if covered[k]:
                k += 1
                continue
            run_start = k
            while k < t1 and not covered[k]:
                k += 1
            lo, hi = _with_punctuation(text, toks[run_start][0], toks[k - 1][1])
            phrase = text[lo:hi]
            if phrase:
                leftover.append(phrase)
        seg_text = text[c0:c1].strip()
        # singing words count whether in the text or in the marker itself
        vocal = any(library.cards.get(m.card_id) and library.cards[m.card_id].kind == "vocal"
                    for m in seg_matches)
        if not vocal:
            first_tok = marker_tok if name else t0
            vocal = any(w in _SINGING for w in norm[first_tok:t1])
        layers.append(Layer("base" if si == 0 else "layer", seg_text, tuple(seg_matches), name, vocal))

    # Drop empty non-base layers; keep an empty base only when a layer follows.
    kept = [l for l in layers if l.role == "base" or l.text or l.matches]
    if len(kept) == 1 and kept[0].role == "base" and not kept[0].text and not kept[0].matches:
        kept = []
    return Parsed(text, tuple(kept), tuple(leftover))


def _with_punctuation(text: str, lo: int, hi: int) -> tuple[int, int]:
    """Widen a run of words to the punctuation stuck to its ends.

    "rainy Tuesday," keeps its comma and "(slow" its bracket, as long as the
    punctuation is set off by whitespace or the text edge; a hyphen joining the
    run to a neighbouring match ("rainy-Tortoise") stays outside.
    """
    def punct(ch: str) -> bool:
        return not ch.isspace() and _TOKEN.match(ch) is None

    j = hi
    while j < len(text) and punct(text[j]):
        j += 1
    if j == len(text) or text[j].isspace():
        hi = j
    j = lo
    while j > 0 and punct(text[j - 1]):
        j -= 1
    if j == 0 or text[j - 1].isspace():
        lo = j
    return lo, hi
