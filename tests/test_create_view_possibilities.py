# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for mounting `PossibilitiesWall` (app/possibilities.py, SP-2 Task 1)
atop the Create surface and seeding the EXISTING composer from a tile pick
(SP-2 Task 2).

Reuses the CreateView test harness convention from tests/test_create_view.py
(`_make_view(monkeypatch, **kwargs)`, `_fake_mediums`, `_sync_create_view_threading`)
rather than importing across test modules — every existing CreateView test
file duplicates this small harness instead of sharing it via conftest.py, and
this file follows the same convention.

The critical invariant under test: picking a wall tile is PURE composer-
seeding (select the medium chip + switch to the idea door + fill the prompt
entry) and must never affect `_collect_params()` — picking a tile must
produce the exact same params dict as doing the same three steps by hand.
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

from create_mediums import Medium


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


class _ImmediateThread:
    """threading.Thread stand-in that runs its target synchronously on
    start() — mirrors tests/test_create_view.py's own helper so the health-
    strip background refresh (CreateView's one off-thread seam) never races a
    real daemon thread in these tests."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _sync_create_view_threading(monkeypatch):
    import create_view
    monkeypatch.setattr(create_view.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(create_view.GLib, "idle_add", lambda fn, *a: fn(*a))


def _make_view(monkeypatch, **kwargs):
    _sync_create_view_threading(monkeypatch)
    from create_view import CreateView
    kwargs.setdefault("mediums_fn", _fake_mediums)
    kwargs.setdefault("health_fn", _fake_health)
    return CreateView(**kwargs)


def test_createview_has_possibilities_wall(monkeypatch):
    view = _make_view(monkeypatch)
    assert getattr(view, "_possibilities", None) is not None
    assert view._possibilities.card_count() >= 1


def test_pick_selects_medium_and_seeds_prompt(monkeypatch):
    view = _make_view(monkeypatch)
    meds = view._mediums_fn()
    target = meds[1]  # "video" -- distinct from the default-active first chip
    view._on_possibility_picked(target, "a test idea")
    assert view._active_medium.id == target.id
    assert view._prompt_entry.get_text() == "a test idea"


def test_collect_params_unchanged_by_pick(monkeypatch):
    # Picking a tile must equal manually selecting that medium + typing that
    # prompt — the wall must never become a second, divergent path into the
    # params dict `_on_create`/`on_theme_set` consume.
    view = _make_view(monkeypatch)
    m = view._mediums_fn()[0]
    view._on_possibility_picked(m, "abc")
    picked = view._collect_params()

    view2 = _make_view(monkeypatch)
    view2._chip_buttons[m.id].set_active(True)
    view2._prompt_entry.set_text("abc")
    view2._doors["idea"].set_active(True)
    manual = view2._collect_params()

    assert picked == manual


def test_collect_params_unchanged_by_pick_non_default_medium(monkeypatch):
    # Same invariant, but for a medium that is NOT the default-active first chip,
    # so the pick actually fires the button "toggled" -> _select_medium path
    # (meds[0] is already active at construction, a no-op toggle).
    view = _make_view(monkeypatch)
    m = view._mediums_fn()[1]
    view._on_possibility_picked(m, "xyz")
    picked = view._collect_params()

    view2 = _make_view(monkeypatch)
    view2._chip_buttons[m.id].set_active(True)
    view2._prompt_entry.set_text("xyz")
    view2._doors["idea"].set_active(True)
    manual = view2._collect_params()

    assert picked == manual
    assert view._active_medium.id == m.id


def test_refresh_possibilities_calls_wall_refresh(monkeypatch):
    """`refresh_possibilities` rebuilds the wall so a star made elsewhere
    (Discover/Library) shows up on Create's tiles without an app restart."""
    view = _make_view(monkeypatch)

    calls = []
    real_wall = view._possibilities
    assert real_wall is not None
    monkeypatch.setattr(real_wall, "refresh", lambda: calls.append(True))

    view.refresh_possibilities()
    assert calls == [True]


def test_refresh_possibilities_is_noop_without_a_wall(monkeypatch):
    """A view whose wall failed to construct (or was never built) must not
    raise — refresh_possibilities is a fail-soft convenience call."""
    view = _make_view(monkeypatch)
    view._possibilities = None
    view.refresh_possibilities()  # must not raise
