# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""dialects.py - turn a translation plan into prompt text for one model dialect.

Public API:
    CAPTION, TAGS, GENERIC, DIALECTS
    dialect_for(profile) -> str
    Clause(kind, text, priority)
    render(clauses, dialect, max_chars) -> (prompt, notes)
    Part, Plan, compose(plan, dialect) -> (clauses, notes)   (used by translate.py)

Three dialects (spec 4.4):
* caption+lyrics (MiniMax): descriptive sentences. Lyrics are written by translate.py.
* tags (Stable Audio): one comma-separated line of short tags.
* generic (unknown model): one plain sentence, parts joined with "; ".

A prompt is a list of `Clause`s in display order. Each clause has a kind and a
priority; `render` joins them for the dialect and, when the result is over the
limit, drops whole clauses (never part of one, so never mid-word). Trimming
order (spec 10, plan Global Constraints), dropped from the end of this list:
user's own words, base style sound, vocal layer (and any other layer), names,
instruments (with rhythm), tempo and key (with harmony), production, mood,
avoid, texture. The first two are never dropped: if they alone exceed the
limit the prompt is returned over the limit with a note. Within one priority
the clause shown last goes first.

Standard library only (plus `MusicProfile` for typing); no GTK, no LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CAPTION, TAGS, GENERIC = "caption+lyrics", "tags", "generic"
DIALECTS = (CAPTION, TAGS, GENERIC)
DEFAULT_LIMIT = 1000

# Lower number = kept longer. Priorities 0 and 1 are never dropped.
PRIORITY = {
    "user": 0, "base": 1,
    "vocal": 2, "layer": 2,
    "names": 3,
    "instruments": 4,
    "tempo": 5,
    "production": 6,
    "mood": 7,
    "avoid": 8,
    "texture": 9,
}
_PROTECTED = 1

# How a dropped clause is named in the note.
_KIND_WORDS = {
    "vocal": "the vocal layer", "layer": "a layer", "names": "the inspiration names",
    "instruments": "instrument and rhythm details", "tempo": "the tempo and harmony",
    "production": "the production notes", "mood": "the mood", "avoid": "the things to leave out",
    "texture": "the texture",
}

CANNOT_SING = "This model cannot sing; the vocal became a lead line."


@dataclass(frozen=True)
class Clause:
    kind: str
    text: str
    priority: int


def clause(kind: str, text: str) -> Clause:
    return Clause(kind, text, PRIORITY[kind])


def dialect_for(profile) -> str:
    """profile.dialect if set, else caption+lyrics for a model that sings, else generic."""
    if profile is None:
        return GENERIC
    d = getattr(profile, "dialect", "") or ""
    if d in DIALECTS:
        return d
    return CAPTION if getattr(profile, "has_lyrics", False) else GENERIC


# ── joining and trimming ─────────────────────────────────────────────────────

def _sentence(t: str) -> str:
    return t if t[-1] in ".!?…" else t + "."


def _join(clauses, dialect: str) -> str:
    texts = [(c.kind, c.text.strip()) for c in clauses if c.text.strip()]
    if not texts:
        return ""
    if dialect == TAGS:
        return ", ".join(t.rstrip(" ,;.") for _, t in texts)
    if dialect == GENERIC:
        s = "; ".join(t.rstrip(" .,;:") for _, t in texts)
        if texts[0][0] != "user":           # the user's words are never re-cased
            s = s[:1].upper() + s[1:]
        return s + "."
    return " ".join(_sentence(t) for _, t in texts)


def limit_of(max_chars) -> int:
    return max_chars if isinstance(max_chars, int) and max_chars > 0 else DEFAULT_LIMIT


def over_limit_note(n: int, limit: int) -> str:
    return (f"Your own words and the base style come to {n} characters, over the {limit}-character limit. "
            "Nothing of yours was cut; shorten the text to fit.")


def trim(clauses, dialect: str, max_chars) -> tuple[list[Clause], tuple[str, ...]]:
    """Drop whole clauses, lowest priority first, until the joined text fits."""
    limit = limit_of(max_chars)
    kept = list(clauses)
    dropped: list[str] = []
    notes: list[str] = []
    while len(_join(kept, dialect)) > limit:
        cands = [i for i, c in enumerate(kept) if c.priority > _PROTECTED]
        if not cands:
            notes.append(over_limit_note(len(_join(kept, dialect)), limit))
            break
        i = max(cands, key=lambda k: (kept[k].priority, k))   # highest priority number, shown last
        kind = kept.pop(i).kind
        if _KIND_WORDS.get(kind, kind) not in dropped:
            dropped.append(_KIND_WORDS.get(kind, kind))
    if dropped:
        notes.insert(0, f"To fit the {limit}-character limit, dropped {_and(dropped)}.")
    return kept, tuple(notes)


def render(clauses, dialect: str, max_chars) -> tuple[str, tuple[str, ...]]:
    """The prompt for `dialect` within `max_chars` (None: 1000), and notes on what was dropped."""
    kept, notes = trim(clauses, dialect, max_chars)
    return _join(kept, dialect), notes


# ── composing a plan ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Part:
    """One recognised card in a layer, already scrubbed of names."""
    kind: str                 # "sound" | "vocal" | "texture"
    label: str                # style name shown in the prompt ("post-rock", "Bluegrass vocal")
    sound: str
    tags: tuple[str, ...] = ()
    vocal: str = ""           # vocal cards: how the voice sounds
    lead_instrument: str = ""  # vocal cards: melody instrument when the model cannot sing


@dataclass(frozen=True)
class Plan:
    user_line: str | None
    base: tuple[Part, ...]
    layers: tuple[tuple[str | None, tuple[Part, ...]], ...]   # (marker, parts)
    names: tuple[str, ...] = ()
    instruments: tuple[str, ...] = ()
    rhythm: str = ""
    harmony: str = ""
    production: str = ""
    mood: str = ""
    bpm: int | None = None
    avoid: tuple[str, ...] = ()
    can_sing: bool = True


def _lc_first(s: str) -> str:
    """Lowercase the first letter unless the first word looks like an acronym ("TB-303")."""
    s = s.strip()
    if len(s) > 1 and s[1].isupper():
        return s
    return s[:1].lower() + s[1:]


def _cap(s: str) -> str:
    s = s.strip()
    return s[:1].upper() + s[1:]


def _and(items) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _first_sentence(s: str) -> str:
    m = re.match(r"(.+?[.!?])(\s|$)", s.strip(), re.S)
    return (m.group(1) if m else s.strip()).rstrip(".!?")


def vocal_style(label: str) -> str:
    """'Bluegrass vocal' -> 'bluegrass'; the style word of a vocal card."""
    s = re.sub(r"\s*\b(vocals?|singing|singers?|voices?)\s*$", "", label.strip(), flags=re.I)
    return (s or label).strip().lower()


def lead_line(part: Part) -> str:
    """A vocal rewritten for a model that cannot sing (documented in CARDS.md, `lead_instrument`)."""
    style = vocal_style(part.label)
    if part.lead_instrument:
        return f"plaintive {part.lead_instrument} lead line phrased like a {style} vocal"
    return f"plaintive lead line phrased like a {style} vocal"


def _mood_words(mood: str) -> list[str]:
    bits = re.split(r"\s*(?:[,;]|\band\b)\s*", mood.strip().rstrip("."))
    return [b.lower() for b in bits if b]


def _not_mentioned(instruments, text: str) -> list[str]:
    """Instruments the descriptions do not already name (case-insensitive), in order, without repeats."""
    low = text.lower()
    out: list[str] = []
    for i in instruments:
        k = i.strip().lower()
        if k and k not in low and k not in (o.lower() for o in out):
            out.append(i.strip())
    return out


def _layer_text(plan: Plan) -> str:
    return " ".join(p.sound + " " + p.vocal for _, parts in plan.layers for p in parts)


_ALONGSIDE = {"with", "plus", "mixed with", "layered with", "meets"}


def _intro(marker: str | None) -> str:
    return "Alongside it" if marker in _ALONGSIDE else "Over it"


def compose(plan: Plan, dialect: str) -> tuple[list[Clause], tuple[str, ...]]:
    if dialect == TAGS:
        return _compose_tags(plan)
    if dialect == GENERIC:
        return _compose_generic(plan)
    return _compose_caption(plan)


def _compose_caption(plan: Plan) -> tuple[list[Clause], tuple[str, ...]]:
    out: list[Clause] = []
    user_low = (plan.user_line or "").lower()
    if plan.user_line:
        out.append(clause("user", plan.user_line))
    base = []
    for p in plan.base:
        if p.kind == "vocal":
            base.append(f"A {vocal_style(p.label)} vocal: {p.vocal or _lc_first(p.sound)}")
        elif p.kind == "texture" or p.label.lower() in user_low:
            base.append(p.sound)
        else:
            base.append(f"{_cap(p.label)}: {_lc_first(p.sound)}")
    if base:
        out.append(clause("base", " ".join(_sentence(b) for b in base)))
    if plan.names:
        out.append(clause("names", f"Inspired by {_and(plan.names)}."))
    extra = _not_mentioned(plan.instruments, " ".join(c.text for c in out) + " " + _layer_text(plan))
    if extra:
        out.append(clause("instruments", f"Also featuring {_and(extra)}."))
    if plan.rhythm:
        out.append(clause("instruments", plan.rhythm))
    if plan.harmony:
        out.append(clause("tempo", plan.harmony))
    if plan.production:
        out.append(clause("production", plan.production))
    if plan.mood:
        out.append(clause("mood", f"The mood is {_lc_first(plan.mood)}"))
    for marker, parts in plan.layers:
        for p in parts:
            if p.kind == "vocal":
                out.append(clause("vocal", f"{_intro(marker)}, a {vocal_style(p.label)} vocal: "
                                           f"{p.vocal or _lc_first(p.sound)}"))
            elif p.kind == "texture":
                out.append(clause("texture", p.sound))
            else:
                out.append(clause("layer", f"{_intro(marker)}, {_lc_first(p.label)}: {_lc_first(p.sound)}"))
    if plan.bpm:
        out.append(clause("tempo", f"Around {plan.bpm} bpm."))
    if plan.avoid:
        out.append(clause("avoid", f"Leave out {_and(plan.avoid)}."))
    return out, ()


def _compose_tags(plan: Plan) -> tuple[list[Clause], tuple[str, ...]]:
    out: list[Clause] = []
    notes: list[str] = []
    seen: set[str] = set()

    def fresh(items):
        got = []
        for i in items:
            k = i.strip().lower().rstrip(".")
            if k and k not in seen:
                seen.add(k)
                got.append(i.strip().rstrip("."))
        return got

    user_low = (plan.user_line or "").lower()
    if plan.user_line:
        out.append(clause("user", plan.user_line))
    all_parts = list(plan.base) + [p for _, parts in plan.layers for p in parts]
    if plan.can_sing and any(p.kind == "vocal" for p in all_parts):
        seen.add("instrumental")            # a sung piece is not tagged instrumental

    def label_tag(p: Part) -> list[str]:
        return [] if p.label.lower() in user_low else [_lc_first(p.label)]

    base: list[str] = []
    for p in plan.base:
        if p.kind == "vocal":
            if plan.can_sing:
                base += fresh([f"{vocal_style(p.label)} vocal", *p.tags])
            else:
                base += fresh([lead_line(p)])
                notes.append(CANNOT_SING)
        elif p.kind == "texture":
            base += fresh(p.tags or [_lc_first(_first_sentence(p.sound))])
        else:
            base += fresh([*label_tag(p), *p.tags])
    if base:
        out.append(clause("base", ", ".join(base)))
    if plan.names:
        out.append(clause("names", ", ".join(fresh(plan.names))))
    for marker, parts in plan.layers:
        for p in parts:
            if p.kind == "vocal":
                if plan.can_sing:
                    got = fresh([f"{vocal_style(p.label)} vocal", *p.tags])
                    if got:
                        out.append(clause("vocal", ", ".join(got)))
                else:
                    got = fresh([lead_line(p)])
                    if got:
                        out.append(clause("vocal", got[0]))
                    if CANNOT_SING not in notes:
                        notes.append(CANNOT_SING)
            elif p.kind == "texture":
                got = fresh(p.tags or [_lc_first(_first_sentence(p.sound))])
                if got:
                    out.append(clause("texture", ", ".join(got)))
            else:
                got = fresh([*label_tag(p), *p.tags])
                if got:
                    out.append(clause("layer", ", ".join(got)))
    inst = fresh(plan.instruments)
    if inst:
        out.append(clause("instruments", ", ".join(inst)))
    mood = fresh(_mood_words(plan.mood))
    if mood:
        out.append(clause("mood", ", ".join(mood)))
    if plan.bpm:
        out.append(clause("tempo", f"{plan.bpm} bpm"))
    return out, tuple(dict.fromkeys(notes))


def _compose_generic(plan: Plan) -> tuple[list[Clause], tuple[str, ...]]:
    out: list[Clause] = []
    if plan.user_line:
        out.append(clause("user", plan.user_line))
    base = []
    for p in plan.base:
        if p.kind == "vocal":
            base.append(f"a {vocal_style(p.label)} vocal, {p.vocal or _lc_first(_first_sentence(p.sound))}")
        elif p.kind == "texture":
            base.append(_lc_first(_first_sentence(p.sound)))
        elif p.label.lower() in (plan.user_line or "").lower():
            base.append(_lc_first(_first_sentence(p.sound)))
        else:
            base.append(f"{_lc_first(p.label)}, {_lc_first(_first_sentence(p.sound))}")
    if base:
        out.append(clause("base", "; ".join(base)))
    if plan.names:
        out.append(clause("names", f"inspired by {_and(plan.names)}"))
    for marker, parts in plan.layers:
        for p in parts:
            if p.kind == "vocal":
                where = "alongside" if marker in _ALONGSIDE else "on top"
                out.append(clause("vocal", f"with a {vocal_style(p.label)} vocal {where}, "
                                           f"{p.vocal or _lc_first(_first_sentence(p.sound))}"))
            elif p.kind == "texture":
                out.append(clause("texture", _lc_first(_first_sentence(p.sound))))
            else:
                out.append(clause("layer", f"with {_lc_first(p.label)} layered in, "
                                           f"{_lc_first(_first_sentence(p.sound))}"))
    extra = _not_mentioned(plan.instruments, " ".join(c.text for c in out))
    if extra:
        out.append(clause("instruments", f"also {_and(extra)}"))
    if plan.mood:
        out.append(clause("mood", f"a {_lc_first(plan.mood.rstrip('.'))} mood"))
    if plan.bpm:
        out.append(clause("tempo", f"around {plan.bpm} bpm"))
    return out, ()
