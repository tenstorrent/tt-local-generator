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


# ── Resizable split (form fills; result docked right) ─────────────────────

def test_form_is_not_width_clamped_anymore(make_create_view):
    """The form is deliberately UN-clamped now: the old MaxWidthBin that capped
    it (and floated the result pane in dead space on a wide window) is gone.
    The form fills the left of a resizable Gtk.Paned up to the divider — "let
    the left side show it all". No ancestor MaxWidthBin remains."""
    cv = make_create_view()
    assert cv._is_width_clamped() is False
    child = cv.get_first_child()
    while child is not None:
        assert not isinstance(child, gtk_layout.MaxWidthBin)
        child = child.get_next_sibling()


def test_form_fills_left_of_a_resizable_paned(make_create_view):
    """Structure of the new split: a horizontal Gtk.Paned whose start child is
    the scrolling form and whose end child docks the result detail pane at a
    fixed default width (draggable). Replaces the FlowBox+MaxWidthBin two-pane."""
    cv = make_create_view()
    assert isinstance(cv._create_paned, Gtk.Paned)
    assert cv._create_paned.get_orientation() == Gtk.Orientation.HORIZONTAL
    assert isinstance(cv._create_paned.get_start_child(), Gtk.ScrolledWindow)
    assert cv._create_paned.get_shrink_end_child() is False


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
