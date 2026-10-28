# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""Music prompt library: style cards that turn names into sound descriptions.

GTK-free; imports only the standard library and PyYAML. The public API grows
in later tasks (translate, wand, menu_tree); for now it exposes the loader.
"""
from .cards import Library, StyleCard, load_library, validate_card

__all__ = ["Library", "StyleCard", "load_library", "validate_card"]
