# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""Music prompt library: style cards that turn names into sound descriptions.

GTK-free; imports only the standard library, PyYAML and `music_runtime` (for
`MusicProfile`). Public API: `load_library`, `translate`, `Translation`,
`dialect_for`, `wand` (a surprise prompt from cards the user did not name) and
`menu_tree` (the library as menu groups).
"""
from .cards import Library, StyleCard, load_library, validate_card
from .dialects import dialect_for
from .menu import MenuGroup, MenuItem, menu_tree
from .translate import Translation, reload_library, translate
from .wand import wand

__all__ = ["Library", "MenuGroup", "MenuItem", "StyleCard", "Translation", "dialect_for", "load_library", "menu_tree", "reload_library", "translate", "validate_card", "wand"]
