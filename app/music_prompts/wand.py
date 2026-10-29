# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""wand.py - "surprise me": a prompt made from cards the user did not name.

Public API:
    wand(profile, *, seed=None, base=None, library=None, smooth=False,
         smooth_fn=None, lyrics_fn=None) -> Translation

Standard library only (plus this package); no GTK, no LLM unless one is injected.

Rendering. The wand picks card ids and hands them to `translate.translate_cards`,
the same back half `translate` uses, so a wand prompt is exactly what a typed
"A with B and someone is singing C" would give for the same cards, trimmed to the
same length limit and scrubbed of artist names the same way.

Scoring (no embeddings; only the cards' own data):
* Family distance. The main picks must come from different `family` values
  (distance 0 or 1: same family is out, different is in).
* Clash lists. Two cards never go together when either one's `clashes_with`
  names the other's id or family.
* `goes_with`. A candidate whose id or family is named by a card already chosen
  (or that names one of them) weighs `1 + GOES_WITH_BOOST`, otherwise 1. It makes
  a pairing likelier and is never required.
* Kinds. The base is a style, artist or era card. The layer card's kind is drawn
  by weight: another style 45, scene 25, instrument 15, texture 10, vocal 5
  (LAYER_KIND_WEIGHTS), among the kinds that have a compatible card. A second
  layer is added with probability THIRD_CARD_P, drawn the same way. At most one
  vocal. A model that cannot sing (no lyrics and not the caption+lyrics dialect)
  never gets a vocal card; the vocal weight goes to style.
* Tempo and the production note come from the base card through `translate`.
* Delivered mix. `translate` reports in `used_cards` only cards with a clause in the
  prompt. When a pick was trimmed to fit the length budget, the wand drops its last
  pick and renders again (at most MAX_RETRIES times, never below two cards), so the
  cards it reports are the cards the prompt describes. If only the base is left (its
  partner did not fit beside it), up to MAX_PARTNER_TRIES other compatible partners
  are tried in a seeded order, keeping the two-card floor where the budget allows.

Repeatable: every random choice uses a private `random.Random(seed)`; the global
random state is never touched. With `seed=None` a fresh seed is drawn and the
result carries it in `Translation.seed_used`, so "seed N" can be shown and the
same result asked for again.

With `base` text: the text is parsed as usual (its words stay in the prompt) and
exactly one more layer is added, from a family not already in the text and not
clashing with it.
"""
from __future__ import annotations

import dataclasses
import random

from . import dialects as D
from .cards import Library, StyleCard
from .parse import parse
from .translate import EMPTY_NOTE, Translation, _default_library, translate_cards

GOES_WITH_BOOST = 3.0
# The kind of a layer card (fix round 2 of Task 6): scenes and textures are real picks.
LAYER_KIND_WEIGHTS = (("style", 45), ("scene", 25), ("instrument", 15), ("texture", 10), ("vocal", 5))
LAYER_KINDS = tuple(k for k, _ in LAYER_KIND_WEIGHTS)
THIRD_CARD_P = 0.35     # chance of a second layer card, drawn the same way; it is the first to go if it does not fit
MAX_RETRIES = 2         # re-renders after dropping a pick that did not fit (see wand())
MAX_PARTNER_TRIES = 8   # other partners tried when the base and its partner do not fit together
BASE_KINDS = ("style", "artist", "era")
MAIN_KINDS = ("style", "instrument", "vocal")
COLOUR_KINDS = ("scene", "texture")
ONLY_ONE_NOTE = "Only one style available, so the wand used it."
NO_PARTNER_NOTE = "No compatible second card was found, so the wand used one card."
VOCAL_MARKER = "and someone is singing"
_GLUE = "{} was read as the layout of the request{} and not repeated in the prompt."
_OWN_GLUE_NOTES = {_GLUE.format("'with'", ""), _GLUE.format("'with'", " (a vocal layer)"),
                   _GLUE.format("'and someone is singing'", " (a vocal layer)")}


def clashes(a: StyleCard, b: StyleCard) -> bool:
    """True when either card's `clashes_with` names the other's id or family."""
    return (b.id in a.clashes_with or b.family in a.clashes_with
            or a.id in b.clashes_with or a.family in b.clashes_with)


def _linked(a: StyleCard, b: StyleCard) -> bool:
    """True when either card's `goes_with` names the other's id or family."""
    return (b.id in a.goes_with or b.family in a.goes_with
            or a.id in b.goes_with or a.family in b.goes_with)


def _can_sing(profile) -> bool:
    return bool(getattr(profile, "has_lyrics", False)) or D.dialect_for(profile) == D.CAPTION


def _pick(rng: random.Random, cands: list[StyleCard], chosen: list[StyleCard]) -> StyleCard | None:
    """Weighted choice (see the module docstring); candidates are sorted by id first."""
    if not cands:
        return None
    cands = sorted(cands, key=lambda c: c.id)
    weights = [1.0 + (GOES_WITH_BOOST if any(_linked(c, o) for o in chosen) else 0.0) for c in cands]
    x = rng.random() * sum(weights)
    for c, w in zip(cands, weights):
        x -= w
        if x < 0:
            return c
    return cands[-1]


def _compatible(c: StyleCard, chosen: list[StyleCard], *, new_family: bool) -> bool:
    return not any(clashes(c, o) or (new_family and c.family == o.family) for o in chosen)


def layer_kind_weights(*, can_sing: bool, available) -> dict[str, int]:
    """The weights (percent) for the kind of a layer card, limited to the kinds in `available`.

    A model that cannot sing gets no vocal; the vocal weight goes to style.
    """
    w = dict(LAYER_KIND_WEIGHTS)
    if not can_sing:
        w["style"] += w.pop("vocal")
    return {k: v for k, v in w.items() if k in available}


def _pick_layer(rng: random.Random, cards: list[StyleCard], chosen: list[StyleCard], can_sing: bool):
    """One layer card: its kind by LAYER_KIND_WEIGHTS, then a card of that kind by goes_with weight.

    Only kinds with a compatible candidate count (a family new to the pick, no clash, at most one vocal).
    """
    has_vocal = any(c.kind == "vocal" for c in chosen)
    by_kind: dict[str, list[StyleCard]] = {}
    for c in cards:
        if c.kind in LAYER_KINDS and c not in chosen and not (c.kind == "vocal" and has_vocal) \
                and _compatible(c, chosen, new_family=True):
            by_kind.setdefault(c.kind, []).append(c)
    weights = layer_kind_weights(can_sing=can_sing, available=set(by_kind))
    if not weights:
        return None
    kinds = [k for k, _ in LAYER_KIND_WEIGHTS if k in weights]
    x = rng.random() * sum(weights.values())
    kind = kinds[-1]
    for k in kinds:
        x -= weights[k]
        if x < 0:
            kind = k
            break
    return _pick(rng, by_kind[kind], chosen)


def _build(rng: random.Random, lib: Library, can_sing: bool):
    """Pick (base, layers) card lists, or None when no pairing exists.

    The base is a style, artist or era card. The first layer is drawn by kind (LAYER_KIND_WEIGHTS);
    with probability THIRD_CARD_P a second layer is drawn the same way. Every layer comes from a
    family not yet in the pick and clashes with nothing in it; goes_with makes a card likelier.
    """
    cards = sorted(lib.cards.values(), key=lambda c: c.id)
    bases = [c for c in cards if c.kind in BASE_KINDS]
    rng.shuffle(bases)
    for base in bases:
        chosen = [base]
        first = _pick_layer(rng, cards, chosen, can_sing)
        if first is None:
            continue
        chosen.append(first)
        if rng.random() < THIRD_CARD_P:
            third = _pick_layer(rng, cards, chosen, can_sing)
            if third is not None:
                chosen.append(third)
        return base, chosen[1:]
    return None


def _layers(cards: list[StyleCard]):
    return [(VOCAL_MARKER if c.kind == "vocal" else "with", (c.id,)) for c in cards]


def wand(profile, *, seed: int | None = None, base: str | None = None, library: Library | None = None,
         smooth: bool = False, smooth_fn=None, lyrics_fn=None) -> Translation:
    lib = library if library is not None else _default_library()
    seed_used = seed if seed is not None else random.SystemRandom().randrange(2 ** 32)
    rng = random.Random(seed_used)
    can_sing = _can_sing(profile)
    kw = dict(library=lib, smooth=smooth, smooth_fn=smooth_fn, lyrics_fn=lyrics_fn)

    def done(t: Translation, *notes: str) -> Translation:
        return dataclasses.replace(t, seed_used=seed_used, notes=tuple(dict.fromkeys(t.notes + notes)))

    if not lib.cards:
        return done(Translation("", None, (EMPTY_NOTE,), (), (), D.dialect_for(profile), (), True))

    if base is not None and base.strip():
        # One surprising layer from a family the text does not have, clashing with nothing in it.
        parsed = parse(base, lib)
        present = [lib.cards[m.card_id] for l in parsed.layers for m in l.matches if m.card_id in lib.cards]
        has_vocal = any(c.kind == "vocal" for c in present) or any(l.vocal for l in parsed.layers)
        cards = sorted(lib.cards.values(), key=lambda c: c.id)
        eligible = [c for c in cards if c.kind in MAIN_KINDS + COLOUR_KINDS and c not in present
                    and (can_sing or c.kind != "vocal") and not (c.kind == "vocal" and has_vocal)]
        pool = [c for c in eligible if _compatible(c, present, new_family=True)] \
            or [c for c in eligible if _compatible(c, present, new_family=False)]
        pick = _pick(rng, pool, present)
        layers = _layers([pick]) if pick else []
        t = translate_cards((), layers, base, profile, **kw)
        # The "with" joining the added card is ours, not typed: do not report it as glue.
        t = dataclasses.replace(t, notes=tuple(n for n in t.notes if n not in _OWN_GLUE_NOTES))
        return done(t, *(() if pick else ("No compatible card was found to add to your text.",)))

    built = _build(rng, lib, can_sing)
    if built is not None:
        head, rest = built
        t = translate_cards((head.id,), _layers(rest), "", profile, **kw)
        # The mix described is the mix delivered. translate reports only cards it rendered; when a
        # pick did not fit the length budget, drop the last pick and render again, so the notes do
        # not announce a card the user was never shown. Keep at least two cards, retry at most twice.
        for _ in range(MAX_RETRIES):
            if set(c.id for c in [head, *rest]) <= set(t.used_cards) or len(rest) <= 1:
                break
            rest = rest[:-1]
            t = translate_cards((head.id,), _layers(rest), "", profile, **kw)
        # Still one card: the partner did not fit beside the base. Try other compatible partners
        # (in a seeded order) so the wand keeps its two-card floor when the library allows.
        if len(t.used_cards) < 2:
            pool = sorted((c for c in lib.cards.values() if c.kind in MAIN_KINDS + COLOUR_KINDS
                           and c.id != head.id and (can_sing or c.kind != "vocal")
                           and _compatible(c, [head], new_family=c.kind in MAIN_KINDS)), key=lambda c: c.id)
            rng.shuffle(pool)
            for c in pool[:MAX_PARTNER_TRIES]:
                alt = translate_cards((head.id,), _layers([c]), "", profile, **kw)
                if len(alt.used_cards) >= 2:
                    t = alt
                    break
        return done(t)

    # One card (or no compatible pair): fall back to a single card, with a note.
    singles = sorted((c for c in lib.cards.values() if c.kind in BASE_KINDS + COLOUR_KINDS), key=lambda c: c.id)
    single = rng.choice(singles) if singles else None
    if single is None:
        single = sorted(lib.cards.values(), key=lambda c: c.id)[0]
        if single.kind == "vocal" and not can_sing:
            return done(Translation("", None, (EMPTY_NOTE,), (), (), D.dialect_for(profile), (), True))
    only = len(lib.cards) == 1
    return done(translate_cards((single.id,), (), "", profile, **kw), ONLY_ONE_NOTE if only else NO_PARTNER_NOTE)
