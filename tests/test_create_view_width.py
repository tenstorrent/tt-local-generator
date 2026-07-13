# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Width-clamp tests for CreateView (Create-surface redesign, Task 6:
`.superpowers/sdd/task-6-brief.md`).

User-reported bug this task fixes: the old persistent flat "live-model strip"
(and, more generally, unwrapped horizontal rows) could sprawl edge-to-edge and
overflow the window on a wide screen. The fix has two parts:

  1. `CreateView.__init__` wraps its entire content column in
     `gtk_layout.wrap_centered(...)` — a real `MaxWidthBin` ceiling (not just
     `set_size_request`, which only raises a MINIMUM — see `gtk_layout.py`'s
     docstring for why that distinction matters).
  2. Every multi-item row that could otherwise grow unbounded (the medium
     chip row; ModifierPills' add-chip/applied-pill rows, covered by
     `tests/test_modifier_pills.py`) is a wrapping `Gtk.FlowBox`, not a plain
     horizontal `Gtk.Box`.

These tests only cover CreateView's own top-level clamp + its chip row;
ModifierPills/RoleZonePanel's own FlowBox usage has its own coverage in
`tests/test_modifier_pills.py` / `tests/test_role_zone_panel.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

# Skip the whole module if a GTK display/widget cannot be created (headless).
try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)

import gtk_layout
from create_mediums import Medium


class _ImmediateThread:
    """threading.Thread stand-in that runs its target synchronously on
    start() — mirrors the pattern in tests/test_create_view.py so
    CreateView's health-refresh thread never races a real daemon thread."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _fake_mediums():
    return [
        Medium(id="image", label="Image", icon="\U0001f5bc️", kind="image",
               source="native", generator=None),
        Medium(id="video", label="Video", icon="\U0001f3a5", kind="video",
               source="native", generator=None),
        Medium(id="animate", label="Animate", icon="\U0001f483", kind="gif",
               source="native", generator=None),
        Medium(id="verse", label="Verse", icon="✍", kind="text",
               source="artgen", generator="verse"),
    ]


def _fake_health():
    return {"wan2.2": True, "flux": False}


@pytest.fixture
def make_create_view(monkeypatch):
    """Factory fixture the brief's tests are written against: returns a
    zero-arg (or kwargs-accepting) callable that builds a fully-injected,
    hermetic CreateView — no real generation, network, or plugin discovery."""
    import create_view
    monkeypatch.setattr(create_view.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(create_view.GLib, "idle_add", lambda fn, *a: fn(*a))

    def _factory(**kwargs):
        from create_view import CreateView
        kwargs.setdefault("mediums_fn", _fake_mediums)
        kwargs.setdefault("health_fn", _fake_health)
        return CreateView(**kwargs)

    return _factory


# ── Step-1 brief test, verbatim ──────────────────────────────────────────

def test_surface_is_width_clamped(make_create_view):
    cv = make_create_view()
    # some ancestor in the built tree is a MaxWidthBin
    assert cv._is_width_clamped()


# ── Additional coverage ───────────────────────────────────────────────────

def test_width_clamp_wraps_a_max_width_bin_with_the_shared_content_ceiling(make_create_view):
    """The wrapper CreateView installs must actually be a `MaxWidthBin`
    carrying the shared `gtk_layout.CONTENT_MAX_WIDTH` ceiling, not merely
    something that happens to satisfy `_is_width_clamped`'s isinstance check
    via an unrelated subclass."""
    cv = make_create_view()

    child = cv.get_first_child()
    found = None
    while child is not None:
        if isinstance(child, gtk_layout.MaxWidthBin):
            found = child
        child = child.get_next_sibling()

    assert found is not None
    assert found._max_width == gtk_layout.CONTENT_MAX_WIDTH


def test_create_view_remains_a_plain_gtk_box_for_existing_mount_callers(make_create_view):
    """main_window.py mounts `self._create_view` directly via
    `self._gallery_stack.add_named(self._create_view, "create")`
    (tests/test_main_window_create_view_mount.py) — the width clamp must be
    installed INSIDE CreateView, not by changing what type CreateView itself
    is, so that mount call keeps working unmodified."""
    cv = make_create_view()
    assert isinstance(cv, Gtk.Box)


def test_medium_chip_row_is_a_wrapping_flowbox(make_create_view):
    """The chip row is the one CreateView-owned multi-item row most likely to
    overflow (3 native + N artgen generator chips) — it must be a
    `Gtk.FlowBox` (which wraps), not a fixed-direction `Gtk.Box`."""
    cv = make_create_view()
    assert isinstance(cv._chip_row, Gtk.FlowBox)


def test_chip_row_still_holds_every_medium_button(make_create_view):
    """Converting the chip row to a FlowBox must not drop or hide any chip —
    every button from `_chip_buttons` must still be reachable inside it."""
    cv = make_create_view()

    found = set()
    child = cv._chip_row.get_first_child()
    while child is not None:
        btn = child.get_child() if hasattr(child, "get_child") else child
        for medium_id, chip_btn in cv._chip_buttons.items():
            if btn is chip_btn:
                found.add(medium_id)
        child = child.get_next_sibling()

    assert found == set(cv._chip_buttons.keys())
