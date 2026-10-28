# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""dialects.py - turn a translation plan into prompt text for one model dialect.

Public API:
    CAPTION, TAGS, GENERIC, DIALECTS
    CAPTION_BUDGET, TAGS_BUDGET, GENERIC_BUDGET   soft length targets
    GLUE_WORDS, STOPWORDS                          word lists (easy to extend)
    dialect_for(profile) -> str
    Clause(kind, text, priority), clause(kind, text)
    render(clauses, dialect, max_chars, budget=None) -> (prompt, notes)
    Part, Plan, compose(plan, dialect) -> (clauses, notes)   (used by translate.py)
    stems(text), redundant(phrase, kept_stems), has_word(haystack, needle)

Three dialects (spec 4.4):
* caption+lyrics (MiniMax): sentences. The base sound, then the layers (the
  vocal right after the base), then instruments the sound has not named, then
  rhythm, harmony and production while the budget allows, the mood, the tempo
  and key. Lyrics are written by translate.py.
* tags (Stable Audio): one comma-separated line: the style label, the card
  tags, the layers, instruments, mood words, tempo and key.
* generic (unknown model): one plain sentence, parts joined with "; ": the
  first sentence of the sound, the layers, the mood and the tempo.

Nothing is written in negative form. A card's `avoid` list never reaches the
prompt (models tend to read "no X" as "X"); translate.py lists it in `notes`
as "Not asked for: ...". A future profile with a negative-prompt field could
carry it.

Dedupe: a candidate phrase or list item is dropped when more than half of its
content words (stopwords removed, simple stems) already appear in the text
kept so far. Stems: lowercase, a plural "s" removed, and a hyphenated word
with a modifier tail ("dub-style", "dub-weighted", "dub-influenced") reduced
to its head ("dub"); other hyphenated words are joined ("post-rock" ->
"postrock"). In the tags dialect a tag that contains an earlier tag replaces
it ("chicago post-rock" over "post-rock"), and a tag that an earlier tag
already contains is dropped. The user's own words are never dropped by dedupe;
card text that repeats them is.

Length. A prompt is a list of `Clause`s in display order, each with a kind
and a priority. `render` joins them for the dialect and drops whole clauses
(never part of one, so never mid-word), highest priority number first, and
within one priority the clause shown last first. Two passes use the same
order: first down to the hard limit (`max_chars`; None means 1000), with a
note naming what was dropped; then down to the dialect's soft budget, without
a note (it only shortens card details). Priority, from kept longest to dropped
first (spec 10 and the plan's Global Constraints; `avoid` is no longer in the
prompt): the user's content words and the base sound (never dropped: when they
alone exceed the hard limit the prompt is returned over it with a note), the
layers (vocal or other), inspiration names, instruments, tempo and key,
production, mood, texture, and last the caption's spare details (rhythm,
harmony, and the caption's production sentence), which the spec's list does
not name and which go first.

Standard library only; no GTK, no LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CAPTION, TAGS, GENERIC = "caption+lyrics", "tags", "generic"
DIALECTS = (CAPTION, TAGS, GENERIC)
DEFAULT_LIMIT = 1000

# Soft length targets (characters), applied after the hard limit.
CAPTION_BUDGET = 450
TAGS_BUDGET = 250
GENERIC_BUDGET = 300
_BUDGETS = {CAPTION: CAPTION_BUDGET, TAGS: TAGS_BUDGET, GENERIC: GENERIC_BUDGET}

# Lower number = kept longer. Priorities 0 and 1 are never dropped.
PRIORITY = {
    "user": 0, "base": 1,
    "vocal": 2, "layer": 2,
    "names": 3,
    "instruments": 4,
    "tempo": 5,
    "production": 6,
    "mood": 7,
    "texture": 9,
    "detail": 10,
}
_PROTECTED = 1
_BASE_TAGS = 4          # tags dialect: card tags kept with the protected base label

# How a dropped clause is named in the note.
_KIND_WORDS = {
    "vocal": "the vocal layer", "layer": "a layer", "names": "the inspiration names",
    "instruments": "the instrument list", "tempo": "the tempo and key",
    "production": "the production notes", "mood": "the mood", "texture": "the texture",
    "detail": "rhythm, harmony and production details",
}

CANNOT_SING = "This model cannot sing; the vocal became a lead line."

# Words with no musical content of their own.
STOPWORDS = frozenset("""
a an the and or but nor of to in on at for with by from as into onto over under about around
is are was were be been being am it its this that these those there here
some any very really quite just so too more most less much many lot lots bit little
i me my we our you your he she they them their his her
make makes making made feel feels let lets please want wants need like ish style kind sort
up down out off top
""".split())
# Leftover words that only describe the shape of the request ("song", "someone is singing", "on top").
# A leftover phrase made only of glue words is not repeated in the prompt and is named in a note.
# Vocal cues count as glue only when the parser made a vocal layer from them (translate.py).
PIECE_NOUNS = frozenset("song songs track tracks piece pieces tune tunes number jam record album music".split())
VOCAL_CUES = frozenset("singing sung sings sing vocal vocals voice voices someone somebody singer".split())
MARKER_WORDS = frozenset("but over on top with plus meets mixed layered and".split())
GLUE_WORDS = STOPWORDS | PIECE_NOUNS | VOCAL_CUES | MARKER_WORDS


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


# ── small text helpers ───────────────────────────────────────────────────────

_HYPHENS = "-‐‑‒–—―−"
_COMPOUND = re.compile(r"[^\W_]+(?:[" + _HYPHENS + r"][^\W_]+)*")
_MOD_TAILS = frozenset("style styled weighted influenced like ish based driven tinged flavored flavoured "
                       "inflected heavy laden infused".split())


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def stems(text: str) -> list[str]:
    """Content-word stems of text, in order (see the module docstring)."""
    out = []
    for raw in _COMPOUND.findall(text.lower()):
        parts = re.split("[" + _HYPHENS + "]", raw)
        word = (parts[0] if parts[-1] in _MOD_TAILS else "".join(parts)) if len(parts) > 1 else raw
        if word not in STOPWORDS:
            out.append(_stem(word))
    return out


def redundant(phrase: str, kept: set) -> bool:
    """True when more than half the phrase's content words are already in `kept` (or it has none)."""
    s = stems(phrase)
    return not s or sum(w in kept for w in s) * 2 > len(s)


def has_word(haystack: str, needle: str) -> bool:
    """Whole-word, case-insensitive: does `needle` occur in `haystack`?"""
    n = needle.strip().lower()
    return bool(n) and re.search(r"(?<![^\W_])" + re.escape(n) + r"(?![^\W_])", haystack.lower()) is not None


def _sentence(t: str) -> str:
    return t if t[-1] in ".!?…" else t + "."


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


def _phrases(text: str) -> list[str]:
    """A comma/semicolon/'and' list split into its items ("Cool, wistful and unhurried.")."""
    return [b for b in re.split(r"\s*(?:[,;]|\band\b)\s*", text.strip().rstrip(".")) if b]


def _filter_list(text: str, kept: set) -> str:
    """The items of a list field that add something new, rejoined ("" when none do)."""
    return _and([_lc_first(p) for p in _phrases(text) if not redundant(p, kept)])


def _contains(longer: list[str], shorter: list[str]) -> bool:
    """Is `shorter` a contiguous run inside `longer` (stem lists)?"""
    n = len(shorter)
    return bool(n) and any(longer[i:i + n] == shorter for i in range(len(longer) - n + 1))


# ── joining and trimming ─────────────────────────────────────────────────────

def _join(clauses, dialect: str) -> str:
    """Join clause texts for the dialect.

    Only the very first character of a caption or generic prompt is capitalised, and only when
    the prompt starts with card text: the user's words are never re-cased (a profile trigger
    phrase such as "fiona crapple" must reach the model as typed). Tags stay as written.
    """
    live = [c for c in clauses if c.text.strip()]
    if not live:
        return ""
    texts = [c.text.strip() for c in live]
    cap = (lambda s: s) if live[0].kind == "user" else _cap
    if dialect == TAGS:
        return ", ".join(t.rstrip(" ,;.") for t in texts)
    if dialect == GENERIC:
        return cap("; ".join(t.rstrip(" .,;:") for t in texts)) + "."
    return cap(" ".join(_sentence(t) for t in texts))


def limit_of(max_chars) -> int:
    return max_chars if isinstance(max_chars, int) and max_chars > 0 else DEFAULT_LIMIT


def over_limit_note(n: int, limit: int) -> str:
    return (f"Your own words and the base style come to {n} characters, over the {limit}-character limit. "
            "Nothing of yours was cut; shorten the text to fit.")


def _drop_to(kept: list, dialect: str, target: int) -> tuple[list, list[str], bool]:
    """Drop clauses (highest priority number, shown last, first) until the join fits `target`."""
    dropped: list[str] = []
    while len(_join(kept, dialect)) > target:
        cands = [i for i, c in enumerate(kept) if c.priority > _PROTECTED]
        if not cands:
            return kept, dropped, False
        i = max(cands, key=lambda k: (kept[k].priority, k))
        word = _KIND_WORDS.get(kept[i].kind, kept[i].kind)
        kept.pop(i)
        if word not in dropped:
            dropped.append(word)
    return kept, dropped, True


def trim(clauses, dialect: str, max_chars, budget=None) -> tuple[list[Clause], tuple[str, ...]]:
    """Trim to the hard limit (with notes), then to the soft budget (silently).

    `budget` None means the dialect's soft budget; 0 means no soft budget.
    """
    limit = limit_of(max_chars)
    kept, dropped, fits = _drop_to(list(clauses), dialect, limit)
    notes: list[str] = []
    if dropped:
        notes.append(f"To fit the {limit}-character limit, dropped {_and(dropped)}.")
    if not fits:
        notes.append(over_limit_note(len(_join(kept, dialect)), limit))
        return kept, tuple(notes)
    soft = _BUDGETS.get(dialect, DEFAULT_LIMIT) if budget is None else budget
    if soft and soft < limit:
        kept, _, _ = _drop_to(kept, dialect, soft)
    return kept, tuple(notes)


def render(clauses, dialect: str, max_chars, budget=None) -> tuple[str, tuple[str, ...]]:
    """The prompt for `dialect` within `max_chars` (None: 1000) and the soft budget, plus notes."""
    kept, notes = trim(clauses, dialect, max_chars, budget)
    return _join(kept, dialect), notes


# ── composing a plan ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Part:
    """One recognised card in a layer, already scrubbed of names."""
    kind: str                 # "sound" | "vocal" | "texture"
    label: str                # style name shown in the prompt ("Post-rock", "Bluegrass vocal")
    sound: str
    tags: tuple[str, ...] = ()
    vocal: str = ""           # vocal cards: how the voice sounds
    lead_instrument: str = ""  # vocal cards: melody instrument when the model cannot sing


@dataclass(frozen=True)
class Plan:
    user_words: tuple[str, ...]          # the user's content phrases, verbatim, in order
    base: tuple[Part, ...]
    layers: tuple[tuple[str | None, tuple[Part, ...]], ...]   # (marker, parts)
    names: tuple[str, ...] = ()
    instruments: tuple[str, ...] = ()
    rhythm: str = ""
    harmony: str = ""
    production: str = ""
    mood: str = ""
    bpm: int | None = None
    key: str = ""
    can_sing: bool = True
    instrumental: bool = False           # the user asked for an instrumental: no vocal anywhere


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


_ALONGSIDE = {"with", "plus", "mixed with", "layered with", "meets"}


def _intro(marker: str | None) -> str:
    return "Alongside it" if marker in _ALONGSIDE else "Over it"


def _parts(plan: Plan, which: str):
    """(marker, part) for the base or the layers, leaving vocals out of an instrumental."""
    groups = ((None, plan.base),) if which == "base" else plan.layers
    for marker, parts in groups:
        for p in parts:
            if not (p.kind == "vocal" and plan.instrumental):
                yield marker, p


def _names_style(p: Part, plan: Plan) -> bool:
    """Does the text already name the part's style (whole word)? Then the label is not repeated."""
    return has_word(" ".join(plan.user_words), p.label) or has_word(p.sound, p.label)


def _tempo_bits(plan: Plan) -> list[str]:
    return ([f"around {plan.bpm} bpm"] if plan.bpm else []) + ([plan.key] if plan.key else [])


def compose(plan: Plan, dialect: str) -> tuple[list[Clause], tuple[str, ...]]:
    if dialect == TAGS:
        return _compose_tags(plan)
    if dialect == GENERIC:
        return _compose_generic(plan)
    return _compose_caption(plan)


class _Kept:
    """The stems of the text kept so far, for dedupe."""

    def __init__(self):
        self.stems: set[str] = set()

    def add(self, text: str) -> str:
        self.stems.update(stems(text))
        return text


def _compose_caption(plan: Plan) -> tuple[list[Clause], tuple[str, ...]]:
    out: list[Clause] = []
    kept = _Kept()
    if plan.user_words:
        out.append(clause("user", kept.add(", ".join(plan.user_words))))
    base = []
    for _, p in _parts(plan, "base"):
        if p.kind == "vocal":
            base.append(f"A {vocal_style(p.label)} vocal: {p.vocal or _lc_first(p.sound)}")
        elif p.kind == "texture" or _names_style(p, plan):
            base.append(p.sound)
        else:
            base.append(f"{_cap(p.label)}: {_lc_first(p.sound)}")
    if base:
        out.append(clause("base", kept.add(" ".join(_sentence(b) for b in base))))
    for marker, p in _parts(plan, "layers"):
        if p.kind == "vocal":
            out.append(clause("vocal", kept.add(f"{_intro(marker)}, a {vocal_style(p.label)} vocal: "
                                                f"{p.vocal or _lc_first(p.sound)}")))
        elif p.kind == "texture":
            out.append(clause("texture", kept.add(p.sound)))
        else:
            out.append(clause("layer", kept.add(f"{_intro(marker)}, {_lc_first(p.label)}: {_lc_first(p.sound)}")))
    if plan.names:
        out.append(clause("names", kept.add(f"Inspired by {_and(plan.names)}.")))
    extra: list[str] = []
    for i in plan.instruments:
        if not redundant(i, kept.stems | {w for e in extra for w in stems(e)}):
            extra.append(i)
    if extra:
        out.append(clause("instruments", kept.add(f"Also featuring {_and(extra)}.")))
    for text in (plan.rhythm, plan.harmony):
        if text and not redundant(text, kept.stems):
            out.append(clause("detail", kept.add(text)))
    prod = _filter_list(plan.production, kept.stems) if plan.production else ""
    if prod:
        out.append(clause("detail", kept.add(_cap(prod) + ".")))
    mood = _filter_list(plan.mood, kept.stems) if plan.mood else ""
    if mood:
        out.append(clause("mood", kept.add(f"The mood is {mood}.")))
    if _tempo_bits(plan):
        out.append(clause("tempo", _cap(", ".join(_tempo_bits(plan))) + "."))
    return out, ()


def _compose_tags(plan: Plan) -> tuple[list[Clause], tuple[str, ...]]:
    items: list[list] = []          # [kind, text], in display order
    notes: list[str] = []

    def add(kind: str, tag: str):
        tag = tag.strip().rstrip(".")
        s = stems(tag)
        if not s:
            return
        for it in items:
            ks = stems(it[1])
            if _contains(s, ks):            # the new tag says the old one and more: keep the specific one
                if len(s) > len(ks) and it[0] != "user":
                    it[1] = tag
                return
            if _contains(ks, s):            # an earlier tag already says it
                return
        if kind != "user" and redundant(tag, {w for it in items for w in stems(it[1])}):
            return
        items.append([kind, tag])

    def vocal(kind: str, p: Part):
        if plan.can_sing:
            for t in (f"{vocal_style(p.label)} vocal", *p.tags):
                add(kind, t)
        else:
            add(kind, lead_line(p))
            if CANNOT_SING not in notes:
                notes.append(CANNOT_SING)

    for w in plan.user_words:
        add("user", w)
    for kind, group in (("base", "base"), (None, "layers")):
        for _, p in _parts(plan, group):
            k = kind or {"vocal": "vocal", "texture": "texture"}.get(p.kind, "layer")
            if p.kind == "vocal":
                vocal(k, p)
            elif p.kind == "texture":
                for t in (p.tags or (_lc_first(_first_sentence(p.sound)),)):
                    add(k, t)
            else:
                for i, t in enumerate((_lc_first(p.label), *p.tags)):
                    # In the base, the label and the first _BASE_TAGS card tags are the protected
                    # base sound; further tags are spare details, trimmed first.
                    add(k if (k != "base" or i <= _BASE_TAGS) else "detail", t)
    for n in plan.names:
        add("names", n)
    for i in plan.instruments:
        add("instruments", i)
    for m in _phrases(plan.mood):
        add("mood", m.lower())
    if plan.bpm:
        items.append(["tempo", f"{plan.bpm} bpm"])
    if plan.key:
        add("tempo", plan.key)
    sings = plan.can_sing and any(p.kind == "vocal" for g in ("base", "layers") for _, p in _parts(plan, g))
    if sings:                       # a sung piece is not tagged instrumental by a card
        items[:] = [it for it in items if it[0] == "user" or it[1].lower() != "instrumental"]
    # Consecutive items of one kind form one clause, so trimming drops a whole group.
    out: list[Clause] = []
    for kind, text in items:
        if out and out[-1].kind == kind:
            out[-1] = clause(kind, out[-1].text + ", " + text)
        else:
            out.append(clause(kind, text))
    return out, tuple(notes)


def _compose_generic(plan: Plan) -> tuple[list[Clause], tuple[str, ...]]:
    out: list[Clause] = []
    kept = _Kept()
    if plan.user_words:
        out.append(clause("user", kept.add(", ".join(plan.user_words))))
    base = []
    for _, p in _parts(plan, "base"):
        first = _lc_first(_first_sentence(p.sound))
        if p.kind == "vocal":
            base.append(f"a {vocal_style(p.label)} vocal, {p.vocal or first}")
        elif p.kind == "texture" or _names_style(p, plan):
            base.append(first)
        else:
            base.append(f"{_lc_first(p.label)}, {first}")
    if base:
        out.append(clause("base", kept.add("; ".join(base))))
    for marker, p in _parts(plan, "layers"):
        first = _lc_first(_first_sentence(p.sound))
        if p.kind == "vocal":
            where = "alongside" if marker in _ALONGSIDE else "on top"
            out.append(clause("vocal", kept.add(f"with a {vocal_style(p.label)} vocal {where}, {p.vocal or first}")))
        elif p.kind == "texture":
            out.append(clause("texture", kept.add(first)))
        else:
            out.append(clause("layer", kept.add(f"with {_lc_first(p.label)} layered in, {first}")))
    if plan.names:
        out.append(clause("names", kept.add(f"inspired by {_and(plan.names)}")))
    mood = _filter_list(plan.mood, kept.stems) if plan.mood else ""
    if mood:
        out.append(clause("mood", kept.add(f"a {mood} mood")))
    if _tempo_bits(plan):
        out.append(clause("tempo", ", ".join(_tempo_bits(plan))))
    return out, ()
