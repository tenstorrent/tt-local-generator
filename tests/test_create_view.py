# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
GTK widget tests for CreateView — the unified Create-surface shell
(Create-surface plan, Task 3: docs/superpowers/specs/2026-07-13-create-surface-design.md).

CreateView is built ALONGSIDE the existing medium-tab generation UI this task
(not yet wired into the loop nav / mounted as the reachable Create page) —
see tests/test_main_window_create_view_mount.py for the mount guard. Every
external dependency (`mediums_fn`, `health_fn`, `on_create`, `on_inspiration`)
is injected, so these tests never touch real generation, real server health,
or real artgen plugin discovery — matching the pure-core-plus-thin-wrapper
house style already established by create_mediums.py / capability_discovery.py.

Creating GTK widgets needs a display; the full suite runs under xvfb. When no
display is available this module skips itself (matches the repo's headless
fallback, see test_pipeline_studio.py).
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


class _ImmediateThread:
    """threading.Thread stand-in that runs its target synchronously on start().

    Mirrors the pattern established in tests/test_pipeline_studio.py so the
    health-strip background refresh (CreateView's one off-thread seam) never
    races a real daemon thread in these tests.
    """

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _sync_create_view_threading(monkeypatch):
    """Make CreateView's health-refresh thread + GLib.idle_add run inline."""
    import create_view
    monkeypatch.setattr(create_view.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(create_view.GLib, "idle_add", lambda fn, *a: fn(*a))


def _fake_mediums():
    return [
        Medium(id="image", label="Image", icon="\U0001f5bc️", kind="image",
               source="native", generator=None),
        Medium(id="video", label="Video", icon="\U0001f3a5", kind="video",
               source="native", generator=None),
        Medium(id="verse", label="Verse", icon="✍", kind="text",
               source="artgen", generator="verse"),
    ]


def _fake_health():
    return {"wan2.2": True, "flux": False}


def _make_view(monkeypatch, **kwargs):
    _sync_create_view_threading(monkeypatch)
    from create_view import CreateView
    kwargs.setdefault("mediums_fn", _fake_mediums)
    kwargs.setdefault("health_fn", _fake_health)
    return CreateView(**kwargs)


# ── Construction ──────────────────────────────────────────────────────────

def test_create_view_builds(monkeypatch):
    view = _make_view(monkeypatch)
    assert isinstance(view, Gtk.Box)


# ── Medium chips ──────────────────────────────────────────────────────────

def test_chips_render_one_per_medium(monkeypatch):
    view = _make_view(monkeypatch)
    assert set(view._chip_buttons.keys()) == {"image", "video", "verse"}
    for medium_id, btn in view._chip_buttons.items():
        assert isinstance(btn, Gtk.ToggleButton)


def test_first_medium_is_active_by_default(monkeypatch):
    view = _make_view(monkeypatch)
    assert view._active_medium is not None
    assert view._active_medium.id == "image"
    assert view._chip_buttons["image"].get_active() is True


def test_selecting_a_chip_sets_active_medium_and_swaps_panel(monkeypatch):
    view = _make_view(monkeypatch)

    view._chip_buttons["verse"].set_active(True)

    assert view._active_medium is not None
    assert view._active_medium.id == "verse"
    # Only one chip stays active (shared radio group).
    assert view._chip_buttons["image"].get_active() is False

    # The panel host was rebuilt to reflect the newly-selected medium — this
    # task's stub panel labels itself by medium, so its presence is the
    # observable signal Task 4+ will replace with a real per-type panel.
    child = view._panel_host.get_first_child()
    assert child is not None
    assert view._panel_host.get_first_child() is not None


def test_swapping_chips_replaces_panel_host_contents(monkeypatch):
    view = _make_view(monkeypatch)
    first_panel = view._panel_host.get_first_child()

    view._chip_buttons["video"].set_active(True)

    second_panel = view._panel_host.get_first_child()
    assert second_panel is not first_panel


# ── Doors ─────────────────────────────────────────────────────────────────

def test_idea_door_is_default_active(monkeypatch):
    view = _make_view(monkeypatch)
    assert view._entry_mode == "idea"
    assert view._doors["idea"].get_active() is True
    assert view._doors["model"].get_active() is False
    assert view._doors["inspiration"].get_active() is False


def test_doors_are_mutually_exclusive_and_switch_entry_mode(monkeypatch):
    view = _make_view(monkeypatch)

    view._doors["model"].set_active(True)
    assert view._entry_mode == "model"
    assert view._doors["idea"].get_active() is False

    view._doors["idea"].set_active(True)
    assert view._entry_mode == "idea"
    assert view._doors["model"].get_active() is False


def test_inspiration_door_calls_on_inspiration(monkeypatch):
    calls = []
    view = _make_view(monkeypatch, on_inspiration=lambda: calls.append(True))

    view._doors["inspiration"].set_active(True)

    assert view._entry_mode == "inspiration"
    assert calls == [True]


def test_inspiration_door_is_safe_with_no_callback(monkeypatch):
    """No on_inspiration injected -> switching to that door must not raise."""
    view = _make_view(monkeypatch)
    view._doors["inspiration"].set_active(True)  # must not raise
    assert view._entry_mode == "inspiration"


# ── Model strip ───────────────────────────────────────────────────────────

def test_model_strip_renders_from_fake_health_fn(monkeypatch):
    view = _make_view(monkeypatch)

    children = []
    child = view._model_strip.get_first_child()
    while child is not None:
        children.append(child)
        child = child.get_next_sibling()

    assert len(children) == 2  # one row per key in _fake_health()


def test_model_strip_reflects_running_vs_not(monkeypatch):
    view = _make_view(monkeypatch)
    assert view._model_health == {"wan2.2": True, "flux": False}


# ── CTA ───────────────────────────────────────────────────────────────────

def test_cta_calls_on_create_with_active_medium_and_params(monkeypatch):
    calls = []
    view = _make_view(monkeypatch, on_create=lambda medium, params: calls.append((medium, params)))

    view._cta_btn.emit("clicked")

    assert len(calls) == 1
    medium, params = calls[0]
    assert medium.id == "image"  # the default-active medium
    assert params == {}  # stub panel this task; real collect() arrives Task 4+


def test_cta_uses_currently_selected_medium(monkeypatch):
    calls = []
    view = _make_view(monkeypatch, on_create=lambda medium, params: calls.append((medium, params)))

    view._chip_buttons["video"].set_active(True)
    view._cta_btn.emit("clicked")

    assert calls[0][0].id == "video"


def test_cta_is_safe_with_no_callback(monkeypatch):
    view = _make_view(monkeypatch, on_create=None)
    view._cta_btn.emit("clicked")  # must not raise


# ── No real generation invoked ───────────────────────────────────────────

def test_construction_never_calls_real_server_manager_or_create_mediums(monkeypatch):
    """Guard: this task's tests must be fully hermetic — real defaults are
    never reached when mediums_fn/health_fn are injected."""
    import create_mediums
    import server_manager

    def _boom_mediums():
        raise AssertionError("real default_mediums() must not be called")

    def _boom_health(*a, **kw):
        raise AssertionError("real server_manager.status_all() must not be called")

    monkeypatch.setattr(create_mediums, "default_mediums", _boom_mediums)
    monkeypatch.setattr(server_manager, "status_all", _boom_health)

    _make_view(monkeypatch)  # must not raise
