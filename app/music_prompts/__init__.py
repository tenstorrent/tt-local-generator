# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""Music prompt library: style cards that turn names into sound descriptions.

GTK-free; imports only the standard library, PyYAML and `music_runtime` (for
`MusicProfile`). Public API: `load_library`, `translate`, `Translation`,
`dialect_for` (the wand and the menu tree arrive in a later task).
"""
from .cards import Library, StyleCard, load_library, validate_card
from .dialects import dialect_for
from .translate import Translation, translate

__all__ = ["Library", "StyleCard", "Translation", "dialect_for", "load_library", "translate", "validate_card"]
