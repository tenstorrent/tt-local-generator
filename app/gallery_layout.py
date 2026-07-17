#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
gallery_layout.py — single source of truth for gallery-card tile sizing.

Before this module existed, the native video/image/animate galleries
(`GalleryWidget` in main_window.py) and the artgen gallery (`ArtgenGallery` in
artgen_gallery.py) each hardcoded their own card tile size AND their own
Gtk.FlowBox grid settings — so the same conceptual "media entry box" looked
different depending on which Discover tab you were on, and cards WITHIN a
single gallery grew or shrank to match thumbnail content (a square image vs.
a 16:9 video poster vs. a tall text preview each requested a different
natural height, and Gtk.FlowBox — non-homogeneous — honors that natural
size). See CLAUDE.md / the SP "media-entry card uniform size" writeup.

This module is imported by BOTH galleries and is the ONLY place the tile
size / grid constants live. It also exposes `pin_fixed_zone()`, the single
GTK trick that makes a zone's MEASURED size follow an invisible anchor
widget instead of its real (variable-aspect) content — see its docstring.
"""
from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

# ── Card tile (the whole card box) — IDENTICAL across every gallery tab ───────
# 220 matches the historical GenerationCard width (_THUMB_W + 20px padding),
# so the native galleries don't visually jump on this change. TILE_H = 240 was
# measured empirically (xvfb) as the worst-case natural height of a
# GenerationCard — 16:9 thumbnail zone (112) + a full 2-line wrapped prompt +
# meta row + button row + card padding/spacing — so it's the smallest value
# that still acts as a true CEILING (every card's measured min==nat==TILE_H,
# confirmed for short/long prompts and with/without a model badge) without
# leaving a large empty gap under short-prompt cards.
TILE_W = 220
TILE_H = 240

# ── Thumbnail/media zone inside the tile — 16:9, letterboxed (CONTAIN fit) ────
THUMB_W = 200
THUMB_H = 112

# ── Gtk.FlowBox grid settings — IDENTICAL across every gallery tab ────────────
FLOW_MIN_CHILDREN_PER_LINE = 2
FLOW_MAX_CHILDREN_PER_LINE = 8
FLOW_ROW_SPACING = 12
FLOW_COLUMN_SPACING = 12

# ── Density presets (Preferences → gallery density) ───────────────────────────
# "compact" scales the WHOLE tile (both dimensions), not just width, by the
# same factor as the historical compact-width value (160px) relative to
# TILE_W — so switching density scales every gallery tab uniformly and
# identically instead of only shrinking width while height stays natural.
_COMPACT_W = 160
_COMPACT_SCALE = _COMPACT_W / TILE_W


def tile_size(density: str) -> "tuple[int, int]":
    """Return (width, height) for the card tile at the given density.

    Both `MainWindow._apply_gallery_density` (native galleries) and
    `ArtgenGallery.set_tile_size` (artgen gallery) call this, so a density
    change always renders identically on every Discover tab.
    """
    if density == "compact":
        return (_COMPACT_W, round(TILE_H * _COMPACT_SCALE))
    return (TILE_W, TILE_H)


def pin_fixed_zone(child: Gtk.Widget, width: int, height: int) -> Gtk.Overlay:
    """
    Wrap `child` in a Gtk.Overlay whose MEASURED size is exactly
    width x height, no matter what `child` itself would naturally request.

    The trick: a Gtk.Overlay's own size negotiation is driven ONLY by its
    "main child" (the one set via `set_child()`); widgets added via
    `add_overlay()` are positioned on top but — by default — do NOT
    contribute to the Overlay's measured size (GtkOverlay's per-child
    "measure" flag defaults to False). So an invisible, explicitly-sized
    anchor Box is made the main child (this alone pins the Overlay's own
    size), and `child` is layered on top as an overlay child, filling the
    pinned area (`Gtk.Align.FILL` on both axes, which is the default for
    most widgets but set explicitly here to be sure).

    This is what turns "child gets a size_request MINIMUM" (which still let
    e.g. a square image's own aspect-ratio-driven natural size win, forcing
    the card taller than a 16:9 card) into "child renders inside a FIXED
    area, period" — the fix for the ragged/inconsistent gallery-card bug.
    """
    zone = Gtk.Overlay()
    anchor = Gtk.Box()
    anchor.set_size_request(width, height)
    zone.set_child(anchor)

    child.set_hexpand(True)
    child.set_vexpand(True)
    child.set_halign(Gtk.Align.FILL)
    child.set_valign(Gtk.Align.FILL)
    zone.add_overlay(child)
    return zone
