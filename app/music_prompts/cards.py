# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""cards.py - style cards (YAML data) and the loader.

Public API:
    StyleCard       - frozen dataclass, one card
    Library         - cards by id plus a normalised alias index
    validate_card(raw) -> list[str]
    load_library(shipped_dir=None, user_dir=None) -> Library
    normalise_alias(text) -> str

Shipped cards live in app/music_prompts/cards/*.yaml. User cards live in
~/.config/tt-local-generator/music_styles/*.yaml; a user card with the same
`id` replaces the shipped one. A bad file or card is logged and skipped, so one
broken card never stops the rest from loading. See CARDS.md for the schema.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

_SHIPPED_DIR = Path(__file__).parent / "cards"
_USER_DIR = Path.home() / ".config" / "tt-local-generator" / "music_styles"

KINDS = ("style", "artist", "scene", "instrument", "vocal", "texture", "era")
_REQUIRED = ("id", "kind", "name", "family", "sound")
_LIST_FIELDS = ("aliases", "inspiration", "instruments", "avoid", "lyric_themes",
                "tags", "goes_with", "clashes_with", "cues")
_TEXT_FIELDS = ("rhythm", "harmony", "mood", "production", "vocal", "notes", "lead_instrument")


@dataclass(frozen=True)
class StyleCard:
    id: str
    kind: str
    name: str
    aliases: tuple[str, ...]
    family: str
    inspiration: tuple[str, ...]
    sound: str
    instruments: tuple[str, ...]
    rhythm: str
    harmony: str
    mood: str
    production: str
    tempo_bpm: tuple[int, int] | None
    avoid: tuple[str, ...]
    lyric_themes: tuple[str, ...]
    vocal: str
    tags: tuple[str, ...]
    goes_with: tuple[str, ...]
    clashes_with: tuple[str, ...]
    ambiguous: bool
    cues: tuple[str, ...]
    parent: str | None
    notes: str
    # Vocal cards: the instrument that carries the melody when a model cannot sing.
    lead_instrument: str = ""


@dataclass
class Library:
    cards: dict[str, StyleCard] = field(default_factory=dict)
    # normalised alias -> ids of the cards that carry it
    by_alias: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def card(self, id: str) -> StyleCard | None:
        return self.cards.get(id)

    def families(self) -> tuple[str, ...]:
        return tuple(sorted({c.family for c in self.cards.values()}))

    def of_kind(self, kind: str) -> list[StyleCard]:
        return [c for c in self.cards.values() if c.kind == kind]


def normalise_alias(text: str) -> str:
    """Lowercase; hyphens and underscores read as spaces; spaces collapsed."""
    return re.sub(r"\s+", " ", re.sub(r"[-_]", " ", str(text).lower())).strip()


def validate_card(raw: dict) -> list[str]:
    """Return error messages for a raw card dict; empty means valid."""
    if not isinstance(raw, dict):
        return ["card is not a mapping"]
    errs: list[str] = []
    for f in _REQUIRED:
        v = raw.get(f)
        if not isinstance(v, str) or not v.strip():
            errs.append(f"missing required field '{f}'")
    kind = raw.get("kind")
    if isinstance(kind, str) and kind and kind not in KINDS:
        errs.append(f"unknown kind '{kind}' (expected one of {', '.join(KINDS)})")
    if kind == "artist" and not (isinstance(raw.get("parent"), str) and raw["parent"].strip()):
        errs.append("artist card requires 'parent' (the id of its style card)")
    for f in _LIST_FIELDS:
        v = raw.get(f)
        if v is not None and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            errs.append(f"{f} must be a list of strings")
    for f in _TEXT_FIELDS:
        v = raw.get(f)
        if v is not None and not isinstance(v, str):
            errs.append(f"{f} must be text")
    if raw.get("ambiguous") is not None and not isinstance(raw["ambiguous"], bool):
        errs.append("ambiguous must be true or false")
    t = raw.get("tempo_bpm")
    if t is not None:
        ok = (
            isinstance(t, (list, tuple))
            and len(t) == 2
            and all(isinstance(x, int) and not isinstance(x, bool) for x in t)
            and 0 < t[0] <= t[1]
        )
        if not ok:
            errs.append("tempo_bpm must be [low, high] whole numbers with 0 < low <= high")
    return errs


def _tuple(v) -> tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, str):
        return (v,)
    return tuple(str(x) for x in v)


def _build(raw: dict) -> StyleCard:
    t = raw.get("tempo_bpm")
    return StyleCard(
        id=raw["id"].strip(),
        kind=raw["kind"],
        name=raw["name"].strip(),
        aliases=_tuple(raw.get("aliases")),
        family=raw["family"].strip(),
        inspiration=_tuple(raw.get("inspiration")),
        sound=raw["sound"].strip(),
        instruments=_tuple(raw.get("instruments")),
        rhythm=str(raw.get("rhythm") or ""),
        harmony=str(raw.get("harmony") or ""),
        mood=str(raw.get("mood") or ""),
        production=str(raw.get("production") or ""),
        tempo_bpm=(int(t[0]), int(t[1])) if t else None,
        avoid=_tuple(raw.get("avoid")),
        lyric_themes=_tuple(raw.get("lyric_themes")),
        vocal=str(raw.get("vocal") or ""),
        tags=_tuple(raw.get("tags")),
        goes_with=_tuple(raw.get("goes_with")),
        clashes_with=_tuple(raw.get("clashes_with")),
        ambiguous=bool(raw.get("ambiguous", False)),
        cues=_tuple(raw.get("cues")),
        parent=(raw.get("parent") or None),
        notes=str(raw.get("notes") or ""),
        lead_instrument=str(raw.get("lead_instrument") or "").strip(),
    )


def _read_dir(directory: Path) -> dict[str, StyleCard]:
    """Read every *.yaml in one directory (sorted by file name).

    A file may hold one card (a mapping) or a list of cards. Bad files and bad
    cards are logged and skipped; a repeated id keeps the first.
    """
    out: dict[str, StyleCard] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            log.warning("music card file %s skipped: %s", path.name, exc)
            continue
        items = data if isinstance(data, list) else [data]
        for raw in items:
            errs = validate_card(raw)
            if errs:
                label = raw.get("id") if isinstance(raw, dict) else None
                log.warning("music card %r in %s skipped: %s", label or "?", path.name, "; ".join(errs))
                continue
            try:
                card = _build(raw)
            except (TypeError, ValueError) as exc:
                log.warning("music card %r in %s skipped: %s", raw.get("id"), path.name, exc)
                continue
            if card.id in out:
                log.warning("duplicate music card id %r in %s ignored (first one kept)", card.id, path.name)
                continue
            out[card.id] = card
    return out


def load_library(shipped_dir: Path | None = None, user_dir: Path | None = None) -> Library:
    """Load shipped then user cards; a user card with the same id overrides."""
    shipped = Path(shipped_dir) if shipped_dir is not None else _SHIPPED_DIR
    user = Path(user_dir) if user_dir is not None else _USER_DIR
    cards = _read_dir(shipped)
    if not cards:
        log.warning("no shipped music cards found in %s (broken install?)", shipped)
    cards.update(_read_dir(user))  # user wins on same id

    # goes_with / clashes_with name card ids or family tags. Anything that
    # names neither is logged and kept as a plain family tag.
    families = {c.family for c in cards.values()}
    for c in cards.values():
        for ref in c.goes_with + c.clashes_with:
            if ref not in cards and ref not in families:
                log.warning("music card %r: %r names no card id or family; kept as a plain tag", c.id, ref)

    for c in cards.values():
        if c.kind == "artist":
            p = cards.get(c.parent) if c.parent else None
            if p is None or p.kind != "style":
                log.warning("music card %r: parent %r is not a style card; card kept", c.id, c.parent)

    alias_ids: dict[str, list[str]] = {}
    for c in cards.values():
        for a in (c.name, *c.aliases):
            key = normalise_alias(a)
            if key and c.id not in alias_ids.setdefault(key, []):
                alias_ids[key].append(c.id)
    return Library(cards=cards, by_alias={k: tuple(v) for k, v in alias_ids.items()})
