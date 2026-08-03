# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Shared GTK layout primitives — the width-capping container.

`MaxWidthBin` + `wrap_centered` started life inside `pipeline_studio.py`
(Pipeline Studio's Discover/Open/Remix views, fix #5 there: user feedback
that content was sprawling edge-to-edge on wide screens). Extracted here so
other surfaces (the Create surface redesign) can clamp their own content to a
comfortable reading/gallery column width using the same battle-tested
container, without importing pipeline_studio's much larger module.

`pipeline_studio.py` keeps `_MaxWidthBin = MaxWidthBin` and
`_wrap_centered = wrap_centered` module-level aliases so its existing code
and tests are untouched.
"""

from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

# Comfortable reading/gallery column width (fix #5, user feedback: Discover/
# Open/Remix content was sprawling across the whole window on a wide screen).
CONTENT_MAX_WIDTH = 960


class MaxWidthBin(Gtk.Widget):
    """A single-child container that CAPS its child's width at *max_width*.

    Fix #5 (user feedback), corrected after review: an earlier version used a
    plain `Gtk.Box` + `set_size_request(960, -1)`, but `set_size_request`
    only raises a widget's MINIMUM size — GTK is still free to allocate it
    MORE. A child whose own natural width exceeds 960 (e.g. an unwrapped
    recipe chip row for a long pipeline) would blow the wrapper wide open,
    reproducing the exact full-width sprawl this was meant to fix.

    This module has no `Adw` dependency (grep the module — no `import Adw`),
    so rather than `Adw.Clamp` this is a minimal custom widget that enforces a
    REAL ceiling by overriding measurement/allocation:

    - `do_measure` (horizontal): report the child's natural width clamped
      DOWN to `max_width`, so an over-wide child never inflates this bin's
      requested width. Minimum is likewise clamped so the bin can still
      shrink below `max_width` on a narrow window (it's a ceiling, not a
      fixed width). Vertical measurement passes the child's request through
      at the allocated width so wrapping children (FlowBox/wrapped labels)
      report the correct height.
    - `do_size_allocate`: give the child at most `max_width` px, centered
      within whatever width this bin actually received.

    The child is set `hexpand` so it fills up to the cap; callers keep their
    own reference to it unchanged (this just becomes its new parent).
    """

    def __init__(self, child: Gtk.Widget, max_width: int = CONTENT_MAX_WIDTH,
                 align: str = "center") -> None:
        super().__init__()
        self._max_width = max_width
        self._align = align  # "center" (default) or "start" (flush-left)
        self._child = child
        child.set_parent(self)

    def do_measure(self, orientation, for_size):
        # Horizontal: cap the reported widths at max_width so an over-wide
        # child can never make this bin (and thus the column) wider than the
        # cap. Vertical: measure the child at the capped width so wrapping
        # content reports the right height.
        if orientation == Gtk.Orientation.HORIZONTAL:
            child_min, child_nat, _mb, _nb = self._child.measure(orientation, for_size)
            minimum = min(child_min, self._max_width)
            natural = min(child_nat, self._max_width)
            return (minimum, natural, -1, -1)
        capped_for_size = for_size
        if for_size > self._max_width:
            capped_for_size = self._max_width
        child_min, child_nat, _mb, _nb = self._child.measure(orientation, capped_for_size)
        return (child_min, child_nat, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        child_width = min(width, self._max_width)
        # Position the (possibly narrower-than-allocated) child within the full
        # width this bin received: centered by default, or flush-left when
        # align="start" (so a wide window doesn't leave a big LEFT gutter).
        if self._align == "start":
            x = 0
        else:
            x = max(0, (width - child_width) // 2)
        allocation = Gdk.Rectangle()
        allocation.x = x
        allocation.y = 0
        allocation.width = child_width
        allocation.height = height
        self._child.size_allocate(allocation, baseline)

    def do_dispose(self):
        # A widget that calls set_parent() must unparent its child before
        # disposal or GTK warns/leaks — the documented teardown for a custom
        # container (see the Gtk.Widget subclassing docs).
        if self._child is not None:
            self._child.unparent()
            self._child = None
        Gtk.Widget.do_dispose(self)


def wrap_centered(content: Gtk.Widget, max_width: int = CONTENT_MAX_WIDTH,
                  align: str = "center") -> Gtk.Widget:
    """Constrain *content* to a centered column no wider than *max_width*.

    Wraps *content* in a `MaxWidthBin`, which enforces a genuine width
    CEILING (see its docstring for why `set_size_request` alone was
    insufficient). *content* is set `hexpand` so it fills the column up to
    the cap; callers keep their own reference to it unchanged — this just
    inserts a new parent between it and whatever used to hold it (a
    `Gtk.ScrolledWindow`, typically), so existing code/tests that walk
    *content*'s children are unaffected.
    """
    content.set_hexpand(True)
    wrapper = MaxWidthBin(content, max_width, align=align)
    wrapper.add_css_class("ps-content-column")
    wrapper.set_halign(Gtk.Align.FILL)
    wrapper.set_hexpand(True)
    return wrapper
