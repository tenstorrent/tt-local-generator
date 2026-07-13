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

**Task 4** ports the native "image" medium to a real `ImageParamPanel`
(`create_param_panels.py`) — this file's Task 3 assertions that assumed every
medium showed the stub (`params == {}` for the default-active "image" medium)
are updated in place to assert against `ImageParamPanel`'s real defaults
instead; every other Task 3 assertion (chips, doors, model strip, stub for
non-ported mediums) is untouched. New tests below cover `ImageParamPanel`
itself and the CreateView-level wiring (swap-on-select, CTA routes through
`collect()`).
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
from create_param_panels import CreateParamPanel, ImageParamPanel

# The exact default dict `ImageParamPanel.collect()` returns for a freshly
# built, unmodified panel — mirrors ControlPanel's image defaults
# (main_window.py: `_steps=20`, `_seed=-1`, `_guidance=3.5`,
# `_image_model="flux"` -> server id "flux.1-schnell", `_neg=""`).
_IMAGE_DEFAULTS = {
    "negative_prompt": "",
    "num_inference_steps": 20,
    "seed": -1,
    "guidance_scale": 3.5,
    "model": "flux.1-schnell",
}


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

    # "verse" has no ported panel yet (Task 6), so it falls back to the
    # Task 3 stub label — its presence is the observable signal.
    child = view._panel_host.get_first_child()
    assert child is not None
    assert view._panel_host.get_first_child() is not None
    assert view._active_panel is None


def test_swapping_chips_replaces_panel_host_contents(monkeypatch):
    view = _make_view(monkeypatch)
    first_panel = view._panel_host.get_first_child()

    view._chip_buttons["video"].set_active(True)

    second_panel = view._panel_host.get_first_child()
    assert second_panel is not first_panel


# ── Image param panel wiring (Task 4) ────────────────────────────────────

def test_default_active_image_medium_mounts_real_image_param_panel(monkeypatch):
    """"image" is the default-active chip (see test_first_medium_is_active_by_
    default) and is a native medium -> CreateView must mount a real
    ImageParamPanel for it immediately on construction, not the stub."""
    view = _make_view(monkeypatch)

    assert isinstance(view._active_panel, ImageParamPanel)
    child = view._panel_host.get_first_child()
    assert child is not None
    assert not child.has_css_class("create-panel-stub-label")


def test_switching_away_from_image_and_back_remounts_a_fresh_panel(monkeypatch):
    view = _make_view(monkeypatch)
    first_image_panel = view._active_panel

    view._chip_buttons["verse"].set_active(True)
    assert view._active_panel is None

    view._chip_buttons["image"].set_active(True)
    assert isinstance(view._active_panel, ImageParamPanel)
    # A fresh panel instance is built on every swap (not cached/reused).
    assert view._active_panel is not first_image_panel


def test_cta_calls_on_create_with_image_param_panel_collect_output(monkeypatch):
    """CTA click for the default-active "image" medium must route through the
    mounted ImageParamPanel's real `collect()` output, not `{}`."""
    calls = []
    view = _make_view(monkeypatch, on_create=lambda medium, params: calls.append((medium, params)))

    view._cta_btn.emit("clicked")

    assert len(calls) == 1
    medium, params = calls[0]
    assert medium.id == "image"
    assert params == _IMAGE_DEFAULTS


def test_cta_reflects_edited_image_param_panel_widgets(monkeypatch):
    """Changing a widget on the mounted ImageParamPanel before clicking
    Create must be reflected in the params `on_create` receives — proves the
    CTA reads live widget state via `collect()`, not a stale snapshot."""
    calls = []
    view = _make_view(monkeypatch, on_create=lambda medium, params: calls.append((medium, params)))

    panel = view._active_panel
    assert isinstance(panel, ImageParamPanel)
    panel._neg_entry.set_text("blurry, extra limbs")
    panel._steps_adj.set_value(35)
    panel._seed_adj.set_value(42)
    panel._guidance_adj.set_value(7.0)
    panel._model_dropdown.set_selected(1)  # sdxl

    view._cta_btn.emit("clicked")

    assert calls[0][1] == {
        "negative_prompt": "blurry, extra limbs",
        "num_inference_steps": 35,
        "seed": 42,
        "guidance_scale": 7.0,
        "model": "stable-diffusion-xl-base-1.0",
    }


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
    # "image" is ported to a real ImageParamPanel this task (Task 4) — see
    # test_cta_calls_on_create_with_image_param_panel_collect_output for the
    # dedicated assertion on its exact contents. Non-ported mediums (e.g.
    # "verse", covered by test_cta_uses_currently_selected_medium below)
    # still collect `{}` from the Task 3 stub.
    assert params == _IMAGE_DEFAULTS


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


# ── ImageParamPanel (standalone, no CreateView needed) ───────────────────

def test_image_param_panel_is_a_create_param_panel():
    assert isinstance(ImageParamPanel(), CreateParamPanel)


def test_image_param_panel_build_returns_a_widget_with_controls():
    panel = ImageParamPanel()
    widget = panel.build()

    assert isinstance(widget, Gtk.Widget)
    # Five rows: steps, seed, guidance scale, model, negative prompt.
    rows = []
    child = widget.get_first_child()
    while child is not None:
        rows.append(child)
        child = child.get_next_sibling()
    assert len(rows) == 5
    # The widgets collect() reads must actually exist after build().
    assert panel._steps_adj is not None
    assert panel._seed_adj is not None
    assert panel._guidance_adj is not None
    assert panel._model_dropdown is not None
    assert panel._neg_entry is not None


def test_image_param_panel_collect_returns_exact_worker_kwargs_with_defaults():
    panel = ImageParamPanel()
    panel.build()

    assert panel.collect() == _IMAGE_DEFAULTS
    # Exactly the keys ImageGenerationWorker takes (minus `prompt`) — no more,
    # no less.
    assert set(panel.collect().keys()) == {
        "negative_prompt", "num_inference_steps", "seed", "guidance_scale", "model",
    }


def test_image_param_panel_collect_reflects_changed_widget_values():
    panel = ImageParamPanel()
    panel.build()

    panel._neg_entry.set_text("watermark, text")
    panel._steps_adj.set_value(12)
    panel._seed_adj.set_value(777)
    panel._guidance_adj.set_value(9.5)
    panel._model_dropdown.set_selected(3)  # motif

    assert panel.collect() == {
        "negative_prompt": "watermark, text",
        "num_inference_steps": 12,
        "seed": 777,
        "guidance_scale": 9.5,
        "model": "motif-image-6b-preview",
    }


def test_image_param_panel_model_dropdown_covers_all_four_choices():
    panel = ImageParamPanel()
    panel.build()

    expected = {
        0: "flux.1-schnell",
        1: "stable-diffusion-xl-base-1.0",
        2: "z-image-turbo",
        3: "motif-image-6b-preview",
    }
    for idx, model_id in expected.items():
        panel._model_dropdown.set_selected(idx)
        assert panel.collect()["model"] == model_id


def test_image_param_panel_collect_before_build_degrades_to_defaults():
    """collect() must never raise, even if called before build() — a caller
    bug shouldn't crash the Create CTA."""
    panel = ImageParamPanel()
    assert panel.collect() == _IMAGE_DEFAULTS


# ── Guard: generation entry points intact ────────────────────────────────
#
# CRITICAL STRATEGY (task-4-brief.md): ImageParamPanel is a FRESH widget, not
# an extraction from ControlPanel, and Task 4 must not break the existing
# generation path. Rather than pinning whole-file bytes (brittle — later
# tasks 5 and 8 legitimately edit main_window.py, and a whole-file hash would
# false-fail on every unrelated change), these guards assert the two contracts
# that actually matter and SHOULD stay stable:
#
#   1. `worker.ImageGenerationWorker.__init__` still accepts the kwargs
#      `ImageParamPanel.collect()` targets — the real contract between this
#      task's panel and the existing worker.
#   2. `main_window.MainWindow._on_generate` still exists as a callable — the
#      generation entry point CreateView will eventually route through, and
#      the path today's UI already uses.
#
# Importing these modules (not constructing MainWindow, which is heavy — see
# tests/test_main_window_create_view_mount.py) is enough to introspect them.

_APP_DIR = Path(__file__).parent.parent / "app"


def test_image_generation_worker_accepts_expected_kwargs():
    """The worker contract ImageParamPanel.collect() targets must stay stable.

    Introspects the live signature rather than the file bytes so unrelated
    edits elsewhere in worker.py don't false-fail this guard.
    """
    import inspect

    import worker

    params = inspect.signature(worker.ImageGenerationWorker.__init__).parameters
    for name in (
        "prompt",
        "negative_prompt",
        "num_inference_steps",
        "seed",
        "guidance_scale",
        "model",
    ):
        assert name in params, (
            f"worker.ImageGenerationWorker.__init__ no longer accepts {name!r} — "
            "ImageParamPanel.collect() targets exactly these kwargs; the "
            "generation contract must stay intact."
        )


def test_main_window_on_generate_entry_point_still_exists():
    """The generation entry point must remain a callable on MainWindow.

    A `hasattr`/`callable` check — NOT a source/hash pin — so legitimate
    edits to main_window.py (tasks 5, 8) don't false-fail this guard.
    """
    import main_window

    assert hasattr(main_window.MainWindow, "_on_generate")
    assert callable(main_window.MainWindow._on_generate)


def _import_lines(src: str) -> list:
    """Every non-comment `import X` / `from X import ...` line in `src`,
    stripped of leading whitespace. Deliberately line-based (not an AST
    walk) — plenty for an import-check guard and keeps the test simple."""
    lines = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            lines.append(stripped)
    return lines


def test_create_param_panels_module_does_not_import_main_window_or_worker():
    """Import-check companion to the hash guards above: create_param_panels.py
    (and create_view.py) must not import main_window's `ControlPanel` or
    `worker.py` at all — reinforcing that ImageParamPanel is fully
    independent, not a disguised extraction. Prose *mentioning* those module
    names in comments/docstrings (explaining the CRITICAL STRATEGY) is fine
    and expected — only actual `import`/`from ... import` statements count."""
    for filename in ("create_param_panels.py", "create_view.py"):
        src = (_APP_DIR / filename).read_text()
        for line in _import_lines(src):
            assert "main_window" not in line, f"{filename}: {line!r}"
            assert "worker" not in line, f"{filename}: {line!r}"
            assert "ControlPanel" not in line, f"{filename}: {line!r}"
