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


def thumb_size(density: str) -> "tuple[int, int]":
    """Return (width, height) for the media/thumbnail sub-zone at density.

    Scaled by the exact same factor as `tile_size()` (`_COMPACT_SCALE`) so
    the thumbnail area shrinks/grows in lockstep with the whole card tile —
    a card whose OUTER size is pinned to the compact tile but whose inner
    thumbnail zone stayed at the comfortable 200x112 would either overflow
    the pinned area or leave a lopsided gap next to the text rows.
    """
    if density == "compact":
        return (round(THUMB_W * _COMPACT_SCALE), round(THUMB_H * _COMPACT_SCALE))
    return (THUMB_W, THUMB_H)


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

    Note: this MUTATES `child`'s own hexpand/vexpand/halign/valign
    properties (forced to True/True/FILL/FILL so it fills the pinned area)
    — callers should not assume `child`'s expand/align state is whatever it
    was before this call.

    The anchor is stashed on the returned zone (`zone._pin_anchor`) so
    `set_pinned_size()` below can resize an already-built zone in place —
    see its docstring for why that must target the ANCHOR, not the zone.
    """
    zone = Gtk.Overlay()
    anchor = Gtk.Box()
    anchor.set_size_request(width, height)
    zone.set_child(anchor)
    zone._pin_anchor = anchor

    child.set_hexpand(True)
    child.set_vexpand(True)
    child.set_halign(Gtk.Align.FILL)
    child.set_valign(Gtk.Align.FILL)
    zone.add_overlay(child)
    return zone


def set_pinned_size(zone: Gtk.Overlay, width: int, height: int) -> None:
    """
    Resize an existing `pin_fixed_zone()` zone IN PLACE so its MEASURED size
    (both minimum and natural — verify with `.measure()`, not
    `.get_size_request()`) becomes exactly width x height on the next
    layout pass.

    Root-cause fix for the gallery-density regression: `MainWindow.
    _apply_gallery_density` and `ArtgenGallery.set_tile_size` used to call
    `set_size_request()` on the OUTER card/zone widget to "resize" an
    already-built card. That only raises the widget's minimum-size FLOOR —
    `Gtk.Widget.set_size_request()` can never shrink a widget below what its
    content already needs — so it was a complete no-op for shrinking
    (comfortable → compact) and did nothing to restore the larger size
    either (compact → comfortable), because the pinned zone's ANCHOR (built
    at the original density) still dominated the measured size.

    Because `pin_fixed_zone`'s anchor is a plain, childless `Gtk.Box`, its
    own measured minimum AND natural size are always EXACTLY its
    size_request — there's no content of its own that could ever exceed it.
    Re-issuing `set_size_request` on THIS anchor (which is what actually
    changes what the *whole zone* reports) is therefore both necessary and
    sufficient; nothing else needs to move or rebuild.
    """
    anchor = getattr(zone, "_pin_anchor", None)
    if anchor is None:
        raise ValueError(
            "set_pinned_size: zone was not created via gallery_layout.pin_fixed_zone()"
        )
    anchor.set_size_request(width, height)
