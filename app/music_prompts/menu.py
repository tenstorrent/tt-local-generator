# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""menu.py - the card library as a menu: groups of items the UI can list.

Public API:
    MenuItem(card_id, label, tip, parent_id=None)
    MenuGroup(title, items)
    menu_tree(library=None) -> list[MenuGroup]

Styles are grouped by family (the family title-cased); scenes, instruments, vocals,
textures and eras each get one group. An artist card sits right after its parent
style as an "inspired by" sub-item (`parent_id` is the parent's id); its `label` is
the artist's name, which the click inserts and Write the prompt later expands to the
description. A `tip` is the first sentence of the card's `sound` that names no
artist or inspiration entry (empty when every sentence does). Groups and items are
sorted, so the menu never reshuffles. Standard library only; no GTK.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .cards import Library
from .translate import _default_library

_KIND_GROUPS = (("scene", "Scenes"), ("instrument", "Instruments"), ("vocal", "Vocals"),
                ("texture", "Textures"), ("era", "Eras"))
_SENTENCES = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class MenuItem:
    card_id: str
    label: str
    tip: str
    parent_id: str | None = None      # set on an artist ("inspired by") sub-item


@dataclass(frozen=True)
class MenuGroup:
    title: str
    items: tuple[MenuItem, ...]


def _all_names(lib: Library) -> list[re.Pattern]:
    names = set()
    for c in lib.cards.values():
        names.update(c.inspiration)
        if c.kind == "artist":
            names.update((c.name, *c.aliases))
    return [re.compile(r"(?<!\w)" + re.escape(n) + r"(?!\w)", re.I) for n in sorted(names) if n.strip()]


def _tip(sound: str, names: list[re.Pattern]) -> str:
    for sentence in _SENTENCES.split(" ".join(sound.split())):
        if sentence and not any(p.search(sentence) for p in names):
            return sentence
    return ""


def menu_tree(library: Library | None = None) -> list[MenuGroup]:
    lib = library if library is not None else _default_library()
    names = _all_names(lib)
    cards = sorted(lib.cards.values(), key=lambda c: (c.name.casefold(), c.id))

    def item(c, parent=None):
        return MenuItem(c.id, c.name, _tip(c.sound, names), parent)

    groups: dict[str, list] = {}                       # title -> [(card, [sub-items])]
    by_id: dict[str, tuple] = {}
    for c in cards:
        if c.kind == "style":
            entry = (c, [])
            groups.setdefault(c.family.title(), []).append(entry)
            by_id[c.id] = entry
    for c in cards:
        if c.kind == "artist":
            parent = by_id.get(c.parent or "")
            if parent is not None:
                parent[1].append(item(c, c.parent))
            else:                                      # no style parent: a top-level entry of its family
                groups.setdefault(c.family.title(), []).append((c, []))
    out = [MenuGroup(t, tuple(i for entry in groups[t] for i in (item(entry[0]), *entry[1])))
           for t in sorted(groups, key=str.casefold)]
    for kind, title in _KIND_GROUPS:
        items = tuple(item(c) for c in cards if c.kind == kind)
        if items:
            out.append(MenuGroup(title, items))
    return out
