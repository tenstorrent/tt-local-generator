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

**Task 6** wraps every real panel in `RoleZonePanel` before mounting — so
`view._active_panel` is now a `RoleZonePanel`, not the bare
Image/Video/Animate/ArtgenParamPanel. Every assertion below that used to
check `isinstance(view._active_panel, XParamPanel)` now checks
`isinstance(view._active_panel, RoleZonePanel)` and unwraps the real panel
via `_panel_of(view)` (== `view._active_panel._panel`) to reach its widgets.
Task 6 also retires the flat, always-visible "live-model strip" (and its
click-to-select-medium cards) in favor of a `_model_dropdown` SCOPED to the
active medium — the old `test_model_strip_*`/`test_*_model_card_*` tests
are replaced by the scoped-dropdown tests near the bottom of this file;
`_server_key_to_medium_id`'s own tests are untouched (it's a pure helper with
no GTK ties, kept for a future grouped model-door task).
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
from create_param_panels import (
    _ANIMATEDIFF_DEFAULTS,
    _VIDEO_MODEL_IDS,
    AnimateParamPanel,
    ArtgenParamPanel,
    CreateParamPanel,
    ImageParamPanel,
    RoleZonePanel,
    VideoParamPanel,
)
from create_view import CreateResultPanel
from model_status import Status


def _panel_of(view):
    """Unwrap `view._active_panel` (a `RoleZonePanel` since Task 6) down to
    the real Image/Video/Animate/ArtgenParamPanel instance it wraps, so a
    test can reach that panel's own widgets (`_neg_entry`, `_steps_adj`, …).
    Raises AttributeError (via `._panel`) if `_active_panel` isn't a
    RoleZonePanel — a deliberately loud failure rather than returning None,
    since every non-stub medium is wrapped as of this task."""
    return view._active_panel._panel

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
    "seed_image_path": "",
}

# The exact default dict `VideoParamPanel.collect()` returns for a freshly
# built, unmodified panel — matches `worker.GenerationWorker`'s kwargs (minus
# `prompt`, which CreateView's idea-door prompt entry owns). `num_frames`
# defaults to None (server/runner default) via a 0="auto" spin sentinel,
# mirroring the seed field's -1="random" sentinel already used by
# ImageParamPanel. Steps clamp (12-50) and default (20) mirror
# `api_client.APIClient.submit`'s server-side clamp. `animatediff_args`
# (SP-3c-2, migration-safe addition) is ALWAYS a complete
# `_ANIMATEDIFF_DEFAULTS` dict regardless of which model is selected — see
# `VideoParamPanel.collect()`'s docstring.
_VIDEO_DEFAULTS = {
    "negative_prompt": "",
    "num_inference_steps": 20,
    "seed": -1,
    "model": "wan2.2-t2v",
    "num_frames": None,
    "seed_image_path": "",
    "animatediff_args": dict(_ANIMATEDIFF_DEFAULTS),
}

# The exact default dict `AnimateParamPanel.collect()` returns for a freshly
# built, unmodified panel — matches `worker.AnimateGenerationWorker`'s kwargs
# (minus `prompt`). Empty ref paths are valid (see module docstring): the
# panel never validates, only collects widget state.
_ANIMATE_DEFAULTS = {
    "reference_video_path": "",
    "reference_image_path": "",
    "num_inference_steps": 20,
    "seed": -1,
    "animate_mode": "animation",
    "model": "wan2.2-animate-14b",
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
        Medium(id="animate", label="Animate", icon="\U0001f483", kind="gif",
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


@pytest.fixture
def make_create_view(monkeypatch):
    """Factory fixture matching the one in tests/test_create_view_width.py —
    duplicated here (rather than shared via conftest.py) to match this
    file's existing `_make_view` helper convention; both build a fully-
    injected, hermetic CreateView via the same `_make_view` helper so there
    is exactly one code path for "how do I build a test CreateView"."""
    def _factory(**kwargs):
        return _make_view(monkeypatch, **kwargs)
    return _factory


# ── Construction ──────────────────────────────────────────────────────────

def test_create_view_builds(monkeypatch):
    view = _make_view(monkeypatch)
    assert isinstance(view, Gtk.Box)


class _FakeStatusService:
    """Minimal stand-in for `model_status.ModelStatusService` exposing
    exactly the surface CreateView consumes (`snapshot()`/`subscribe(cb)`),
    plus a `push()` test helper that mimics `_notify()` fanning a fresh
    snapshot out to subscribers. Never touches real health checks, sockets,
    or subprocesses — matches the fake-service discipline task-2-brief.md
    calls for.

    `running` (SP-2 Task 3): a `capability -> server_key` map backing
    `running_or_starting(capability)`. Defaults to `{}` so every pre-Task-3
    test (which never calls this method) is unaffected; a capability absent
    from the map returns `None`, matching the real service's "nothing
    running/starting" case.
    """

    def __init__(self, initial: "dict | None" = None, running: "dict | None" = None):
        self._snapshot = dict(initial or {})
        self.subscribers: list = []
        self.unsubscribed: list = []
        self._running = dict(running or {})

    def running_or_starting(self, capability: str):
        return self._running.get(capability)

    def snapshot(self) -> dict:
        return dict(self._snapshot)

    def subscribe(self, cb):
        self.subscribers.append(cb)

        def _unsub() -> None:
            self.unsubscribed.append(cb)
            if cb in self.subscribers:
                self.subscribers.remove(cb)

        return _unsub

    def push(self, snap: dict) -> None:
        """Simulate a poll tick landing: store the new snapshot and fan it
        out to every subscriber, exactly like the real service's `_tick()`
        -> `_notify()`."""
        self._snapshot = dict(snap)
        for cb in list(self.subscribers):
            cb(self.snapshot())


def test_create_view_accepts_and_stores_status_service(monkeypatch):
    """SP-2 Task 1/2: MainWindow injects its single ModelStatusService
    instance via `status_service=`. Uses the fake service (not a bare
    `object()`) since Task 2 now subscribes to it during construction."""
    fake_service = _FakeStatusService()
    view = _make_view(monkeypatch, status_service=fake_service)
    assert view._status_service is fake_service


def test_create_view_status_service_defaults_to_none(monkeypatch):
    view = _make_view(monkeypatch)
    assert view._status_service is None


# ── SP-2 Task 2: 3-state status dots from ModelStatusService ────────────

def test_status_glyph_mapping(make_create_view):
    cv = make_create_view()
    assert cv._status_glyph(Status.READY) == "●"      # ●
    assert cv._status_glyph(Status.STARTING) == "◐"   # ◐
    assert cv._status_glyph(Status.OFF) == "◌"        # ◌
    assert cv._status_glyph(Status.ERROR) == "◌"       # ◌ (ERROR folds to the same "not ready" glyph)


def test_subscribes_when_service_present(monkeypatch):
    fake_service = _FakeStatusService({"flux": Status.READY})
    view = _make_view(monkeypatch, status_service=fake_service)
    assert len(fake_service.subscribers) == 1
    # Seeded from snapshot() at construction time, before any push().
    assert view._status_snapshot == {"flux": Status.READY}


def test_service_present_skips_boolean_health_poller(monkeypatch):
    """When a status_service is injected, the legacy boolean `health_fn`
    poller must never run — the service is the single source of truth."""
    calls = []

    def _health():
        calls.append(1)
        return {"wan2.2": True}

    fake_service = _FakeStatusService()
    _make_view(monkeypatch, status_service=fake_service, health_fn=_health)
    assert calls == []


def test_snapshot_updates_dropdown_dot_glyphs(monkeypatch):
    """Pushing a fresh snapshot re-renders the scoped dropdown's dots (image
    medium is default-active; "flux" is one of its keys)."""
    fake_service = _FakeStatusService({"flux": Status.STARTING})
    view = _make_view(monkeypatch, status_service=fake_service)

    model = view._model_dropdown.get_model()
    labels = [model.get_string(i) for i in range(model.get_n_items())]
    assert any(label.startswith("◐ ") for label in labels)  # ◐

    fake_service.push({"flux": Status.READY, "sdxl": Status.STARTING})

    model2 = view._model_dropdown.get_model()
    labels2 = [model2.get_string(i) for i in range(model2.get_n_items())]
    assert any(label.startswith("● ") for label in labels2)  # ●


def test_model_dot_glyph_routes_dropdown_and_door_through_one_helper(monkeypatch):
    fake_service = _FakeStatusService({"flux": Status.READY, "sdxl": Status.OFF})
    view = _make_view(monkeypatch, status_service=fake_service)
    assert view._model_dot_glyph("flux") == "●"
    assert view._model_dot_glyph("sdxl") == "◌"
    assert view._model_dot_glyph("never-seen-key") == "◌"  # defaults to OFF


def test_no_service_uses_boolean_fallback(make_create_view):
    cv = make_create_view()  # status_service=None
    assert cv._status_service is None  # existing _model_health path intact
    assert cv._model_health == {"wan2.2": True, "flux": False}
    assert cv._model_dot_glyph("wan2.2") == "●"
    assert cv._model_dot_glyph("flux") == "○"  # ○ boolean-off, not ◌


def test_unrealize_unsubscribes_from_status_service(monkeypatch):
    fake_service = _FakeStatusService()
    view = _make_view(monkeypatch, status_service=fake_service)
    assert len(fake_service.subscribers) == 1

    view.emit("unrealize")

    assert len(fake_service.unsubscribed) == 1
    assert len(fake_service.subscribers) == 0

    # Idempotent: firing unrealize again must not raise (no double-unsub
    # crash on an already-cleared `_status_unsub`).
    view.emit("unrealize")


def test_unrealize_with_no_status_service_is_a_noop(make_create_view):
    cv = make_create_view()
    cv.emit("unrealize")  # must not raise


# ── Medium chips ──────────────────────────────────────────────────────────

def test_chips_render_one_per_medium(monkeypatch):
    view = _make_view(monkeypatch)
    assert set(view._chip_buttons.keys()) == {"image", "video", "animate", "verse"}
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

    # "verse" is an artgen medium -> mounts a real ArtgenParamPanel introspected
    # from the verse generator's own add_args (Task 6), not the Task 3 stub —
    # wrapped in a RoleZonePanel (Task 6), not mounted bare.
    child = view._panel_host.get_first_child()
    assert child is not None
    assert view._panel_host.get_first_child() is not None
    assert isinstance(view._active_panel, RoleZonePanel)
    assert isinstance(_panel_of(view), ArtgenParamPanel)


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

    assert isinstance(view._active_panel, RoleZonePanel)
    assert isinstance(_panel_of(view), ImageParamPanel)
    child = view._panel_host.get_first_child()
    assert child is not None
    assert not child.has_css_class("create-panel-stub-label")


def test_switching_away_from_image_and_back_remounts_a_fresh_panel(monkeypatch):
    view = _make_view(monkeypatch)
    first_image_panel = _panel_of(view)

    # "verse" also mounts a real panel now (ArtgenParamPanel, Task 6) — the
    # meaningful assertion is that the type changed, not that the panel host
    # went blank.
    view._chip_buttons["verse"].set_active(True)
    assert isinstance(_panel_of(view), ArtgenParamPanel)

    view._chip_buttons["image"].set_active(True)
    assert isinstance(_panel_of(view), ImageParamPanel)
    # A fresh panel instance is built on every swap (not cached/reused).
    assert _panel_of(view) is not first_image_panel


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

    panel = _panel_of(view)
    assert isinstance(panel, ImageParamPanel)
    panel._neg_entry.set_text("blurry, extra limbs")
    panel._steps_adj.set_value(35)
    panel._seed_adj.set_value(42)
    panel._guidance_adj.set_value(7.0)
    # Model selection now lives in the SCOPED dropdown (Task 6), not the
    # panel's own (unmounted, invisible) model row — see _panel_of's docstring
    # and RoleZonePanel's "model field is never placed in any zone" contract.
    view._model_dropdown.set_selected(1)  # sdxl (server_manager order: flux, sdxl, ...)

    view._cta_btn.emit("clicked")

    assert calls[0][1] == {
        "negative_prompt": "blurry, extra limbs",
        "num_inference_steps": 35,
        "seed": 42,
        "guidance_scale": 7.0,
        "model": "stable-diffusion-xl-base-1.0",
        "seed_image_path": "",
    }


# ── Seed-mode selector in the Controls zone (SP-3d-2) ────────────────────────
#
# Migrates ControlPanel's random/repeat-last/keep seed toggle into Create —
# `.superpowers/sdd/task-2-brief.md`. The control itself
# (`create_param_panels.SeedModeControl`) is unit-tested in
# `test_create_param_panels.py`; these tests confirm it actually reaches the
# user through CreateView's real mounting path (RoleZonePanel's collapsed
# Controls expander), not just in isolation.

def test_seed_mode_selector_reaches_the_controls_expander_when_mounted(monkeypatch):
    """The mounted (RoleZonePanel-wrapped) ImageParamPanel's seed-mode
    dropdown must be reachable inside the real Controls expander CreateView
    shows — not just present on the unwrapped panel."""
    from create_param_panels import RoleZonePanel

    view = _make_view(monkeypatch)
    zone = view._active_panel
    assert isinstance(zone, RoleZonePanel)
    panel = _panel_of(view)
    assert isinstance(panel, ImageParamPanel)
    assert panel._seed_mode is not None

    controls_child = zone._controls_expander.get_child()

    def _contains(container, target) -> bool:
        if container is target:
            return True
        child = container.get_first_child() if hasattr(container, "get_first_child") else None
        while child is not None:
            if _contains(child, target):
                return True
            child = child.get_next_sibling()
        return False

    assert _contains(controls_child, panel._seed_mode)


def test_cta_repeat_last_seed_mode_reproduces_the_last_generated_seed(monkeypatch):
    """End-to-end through CreateView's real CTA: picking "Repeat last" then
    clicking Create must forward the most recently generated seed, the SAME
    history-derived value ControlPanel's own "repeat" mode resolves to."""
    from history_store import GenerationRecord, HistoryStore
    from create_param_panels import _SEED_MODE_KEYS

    HistoryStore().append(
        GenerationRecord.new(
            job_id="job-cta-1", prompt="a lighthouse", negative_prompt="",
            num_inference_steps=20, seed=13131,
        )
    )

    calls = []
    view = _make_view(monkeypatch, on_create=lambda medium, params: calls.append((medium, params)))
    panel = _panel_of(view)
    assert isinstance(panel, ImageParamPanel)

    panel._seed_mode._dropdown.set_selected(_SEED_MODE_KEYS.index("repeat"))

    view._cta_btn.emit("clicked")

    assert calls[0][1]["seed"] == 13131


# ── Video param panel wiring (Task 5) ────────────────────────────────────

def test_selecting_video_medium_mounts_real_video_param_panel(monkeypatch):
    view = _make_view(monkeypatch)

    view._chip_buttons["video"].set_active(True)

    assert isinstance(view._active_panel, RoleZonePanel)
    assert isinstance(_panel_of(view), VideoParamPanel)
    child = view._panel_host.get_first_child()
    assert child is not None
    assert not child.has_css_class("create-panel-stub-label")


def test_switching_away_from_video_and_back_remounts_a_fresh_panel(monkeypatch):
    view = _make_view(monkeypatch)
    view._chip_buttons["video"].set_active(True)
    first_video_panel = _panel_of(view)

    view._chip_buttons["verse"].set_active(True)
    assert isinstance(_panel_of(view), ArtgenParamPanel)

    view._chip_buttons["video"].set_active(True)
    assert isinstance(_panel_of(view), VideoParamPanel)
    assert _panel_of(view) is not first_video_panel


def test_cta_calls_on_create_with_video_param_panel_collect_output(monkeypatch):
    """`_VIDEO_DEFAULTS` mirrors `VideoParamPanel.collect()`'s own wan2.2-
    default output verbatim (see the constant's docstring and the direct
    `test_video_param_panel_collect_returns_exact_worker_kwargs_with_defaults`
    test) — this test exercises that same plain-wan2.2 video path end-to-end
    through CreateView's CTA. AnimateDiff is now the scoped dropdown's
    default (index 0, SP-3c-2 reordering), so wan2.2 must be selected
    explicitly to keep exercising the path this test was written for,
    rather than silently switching to asserting AnimateDiff's defaults."""
    calls = []
    view = _make_view(monkeypatch, on_create=lambda medium, params: calls.append((medium, params)))

    view._chip_buttons["video"].set_active(True)
    view._model_dropdown.set_selected(_wan22_index(view))
    view._cta_btn.emit("clicked")

    assert len(calls) == 1
    medium, params = calls[0]
    assert medium.id == "video"
    assert params == _VIDEO_DEFAULTS


def test_cta_reflects_edited_video_param_panel_widgets(monkeypatch):
    """Exercises the Mochi model path with edited widget values (negative
    prompt / steps / seed / frames) flowing through collect() -> on_create.
    Mochi's slot in the scoped dropdown is looked up by canonical id
    (`_mochi_index`) rather than assumed to be a fixed index — SP-3c-2's
    AnimateDiff-first reordering (`["animatediff", "wan2.2", "mochi",
    "skyreels", "animate"]`) moved Mochi from index 1 to index 2, but the
    edited-widgets intent of this test is unchanged."""
    calls = []
    view = _make_view(monkeypatch, on_create=lambda medium, params: calls.append((medium, params)))

    view._chip_buttons["video"].set_active(True)
    panel = _panel_of(view)
    assert isinstance(panel, VideoParamPanel)
    panel._neg_entry.set_text("blurry, watermark")
    panel._steps_adj.set_value(40)
    panel._seed_adj.set_value(101)
    view._model_dropdown.set_selected(_mochi_index(view))  # mochi
    panel._frames_adj.set_value(65)

    view._cta_btn.emit("clicked")

    assert calls[0][1] == {
        "negative_prompt": "blurry, watermark",
        "num_inference_steps": 40,
        "seed": 101,
        "model": "mochi-1-preview",
        "num_frames": 65,
        "seed_image_path": "",
        "animatediff_args": dict(_ANIMATEDIFF_DEFAULTS),
    }


# ── Animate param panel wiring (Task 5) ──────────────────────────────────

def test_selecting_animate_medium_mounts_real_animate_param_panel(monkeypatch):
    view = _make_view(monkeypatch)

    view._chip_buttons["animate"].set_active(True)

    assert isinstance(view._active_panel, RoleZonePanel)
    assert isinstance(_panel_of(view), AnimateParamPanel)
    child = view._panel_host.get_first_child()
    assert child is not None
    assert not child.has_css_class("create-panel-stub-label")


def test_switching_away_from_animate_and_back_remounts_a_fresh_panel(monkeypatch):
    view = _make_view(monkeypatch)
    view._chip_buttons["animate"].set_active(True)
    first_animate_panel = _panel_of(view)

    view._chip_buttons["verse"].set_active(True)
    assert isinstance(_panel_of(view), ArtgenParamPanel)

    view._chip_buttons["animate"].set_active(True)
    assert isinstance(_panel_of(view), AnimateParamPanel)
    assert _panel_of(view) is not first_animate_panel


def test_cta_calls_on_create_with_animate_param_panel_collect_output(monkeypatch):
    calls = []
    view = _make_view(monkeypatch, on_create=lambda medium, params: calls.append((medium, params)))

    view._chip_buttons["animate"].set_active(True)
    view._cta_btn.emit("clicked")

    assert len(calls) == 1
    medium, params = calls[0]
    assert medium.id == "animate"
    assert params == _ANIMATE_DEFAULTS


def test_cta_reflects_edited_animate_param_panel_widgets(monkeypatch):
    calls = []
    view = _make_view(monkeypatch, on_create=lambda medium, params: calls.append((medium, params)))

    view._chip_buttons["animate"].set_active(True)
    panel = _panel_of(view)
    assert isinstance(panel, AnimateParamPanel)
    panel._ref_video_entry.set_text("/tmp/motion.mp4")
    panel._ref_image_entry.set_text("/tmp/character.png")
    panel._steps_adj.set_value(28)
    panel._seed_adj.set_value(7)
    panel._mode_repl_btn.set_active(True)

    view._cta_btn.emit("clicked")

    assert calls[0][1] == {
        "reference_video_path": "/tmp/motion.mp4",
        "reference_image_path": "/tmp/character.png",
        "num_inference_steps": 28,
        "seed": 7,
        "animate_mode": "replacement",
        "model": "wan2.2-animate-14b",
    }


# ── Artgen param panel wiring (Task 6) ───────────────────────────────────

def test_selecting_artgen_medium_mounts_artgen_param_panel_for_its_generator(monkeypatch):
    """Selecting "verse" (source="artgen", generator="verse") must mount an
    ArtgenParamPanel built for the "verse" generator — not the Task 3 stub,
    and not one of the native panel classes."""
    view = _make_view(monkeypatch)

    view._chip_buttons["verse"].set_active(True)

    assert isinstance(view._active_panel, RoleZonePanel)
    verse_panel = _panel_of(view)
    assert isinstance(verse_panel, ArtgenParamPanel)
    assert verse_panel._generator_name == "verse"
    child = view._panel_host.get_first_child()
    assert child is not None
    assert not child.has_css_class("create-panel-stub-label")


def test_switching_away_from_artgen_and_back_remounts_a_fresh_panel(monkeypatch):
    view = _make_view(monkeypatch)
    view._chip_buttons["verse"].set_active(True)
    first_verse_panel = _panel_of(view)

    view._chip_buttons["image"].set_active(True)
    assert isinstance(_panel_of(view), ImageParamPanel)

    view._chip_buttons["verse"].set_active(True)
    assert isinstance(_panel_of(view), ArtgenParamPanel)
    assert _panel_of(view) is not first_verse_panel


def test_cta_calls_on_create_with_artgen_param_panel_collect_output(monkeypatch):
    """CTA click for an artgen medium must route through the mounted
    ArtgenParamPanel's real `collect()` output — the verse generator's own
    argparse defaults (form/theme/count), not `{}`."""
    calls = []
    view = _make_view(monkeypatch, on_create=lambda medium, params: calls.append((medium, params)))

    view._chip_buttons["verse"].set_active(True)
    view._cta_btn.emit("clicked")

    assert len(calls) == 1
    medium, params = calls[0]
    assert medium.id == "verse"
    assert params == {"form": "haiku", "theme": "the passage of time", "count": 3}


def test_cta_reflects_edited_artgen_param_panel_widgets(monkeypatch):
    calls = []
    view = _make_view(monkeypatch, on_create=lambda medium, params: calls.append((medium, params)))

    view._chip_buttons["verse"].set_active(True)
    panel = _panel_of(view)
    assert isinstance(panel, ArtgenParamPanel)
    controls = {c.dest: c for c in panel._controls}
    controls["theme"].widget.set_text("winter forges")
    controls["count"].widget.get_adjustment().set_value(5)
    # "form" is a choice dropdown; select "lore" (index 1 of haiku/lore/epitaph/couplet).
    controls["form"].widget.set_selected(1)

    view._cta_btn.emit("clicked")

    assert calls[0][1] == {"form": "lore", "theme": "winter forges", "count": 5}


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


# ── Inspire-me prompt-gen (SP-3c-3, two-mode restored — regression fix 1/2) ──
#
# Distinct from the "inspiration" door above (-> Muse hand-off): this is a
# one-tap button in the brief zone (the idea door's `_prompt_entry`) that
# fills the CURRENT brief via the existing prompt-gen path
# (generate_prompt.py / prompt-server), injected as `inspire_fn` so these
# tests never touch a real subprocess, network call, or thread. Migration-
# safe: `inspire_fn=None` (the default) means no button at all.
#
# TWO-MODE seam: `inspire_fn(prompt_type, seed_text, on_result, on_error)` —
# `seed_text` is `_prompt_entry`'s CURRENT text at click time (empty ->
# fresh-generate; non-empty -> the backend polishes/remixes it). This is the
# fix for the regression where the deleted ControlPanel's two-mode Inspire
# button lost its seed-threading when Create's own button was built
# (`_on_inspire_clicked` used to hardcode nothing at all, just never read the
# entry).

class _FakeInspire:
    """Records `inspire_fn(prompt_type, seed_text, on_result, on_error)` calls
    without firing either callback — lets a test drive the loading state and
    then manually invoke the callback it wants, mirroring how the real seam
    (MainWindow._create_inspire_fn) calls back asynchronously from a
    background thread."""

    def __init__(self):
        self.calls = []  # list of (prompt_type, seed_text, on_result, on_error)

    def __call__(self, prompt_type, seed_text, on_result, on_error):
        self.calls.append((prompt_type, seed_text, on_result, on_error))


def test_inspire_button_absent_without_inspire_fn(monkeypatch):
    view = _make_view(monkeypatch)
    assert getattr(view, "_inspire_btn", None) is None


def test_inspire_button_present_with_inspire_fn(monkeypatch):
    view = _make_view(monkeypatch, inspire_fn=_FakeInspire())
    assert view._inspire_btn is not None
    assert isinstance(view._inspire_btn, Gtk.Button)


def test_inspire_click_calls_inspire_fn_with_prompt_type_for_active_medium(monkeypatch):
    """Default-active medium in `_fake_mediums` is "image" -> prompt_type "image"."""
    fake = _FakeInspire()
    view = _make_view(monkeypatch, inspire_fn=fake)

    view._inspire_btn.emit("clicked")

    assert len(fake.calls) == 1
    prompt_type, _seed_text, _on_result, _on_error = fake.calls[0]
    assert prompt_type == "image"


def test_inspire_click_derives_prompt_type_from_video_medium(monkeypatch):
    fake = _FakeInspire()
    view = _make_view(monkeypatch, inspire_fn=fake)

    view._chip_buttons["video"].set_active(True)
    view._inspire_btn.emit("clicked")

    assert fake.calls[-1][0] == "video"
    assert fake.calls[-1][1] == ""  # entry started empty -> fresh mode


def test_inspire_click_falls_back_to_a_sensible_default_for_artgen(monkeypatch):
    """Artgen mediums (e.g. "verse") have no image/video/animate id -- the
    prompt-gen source must still be one `generate_prompt.py` understands."""
    fake = _FakeInspire()
    view = _make_view(monkeypatch, inspire_fn=fake)

    view._chip_buttons["verse"].set_active(True)
    view._inspire_btn.emit("clicked")

    assert fake.calls[-1][0] in ("video", "image", "animate")


def test_inspire_result_fills_prompt_entry_and_reenables_button(monkeypatch):
    fake = _FakeInspire()
    view = _make_view(monkeypatch, inspire_fn=fake)

    view._inspire_btn.emit("clicked")
    assert view._inspire_btn.get_sensitive() is False  # loading state

    _prompt_type, _seed_text, on_result, _on_error = fake.calls[0]
    on_result("a golden fox in a neon forest")

    assert view._prompt_entry.get_text() == "a golden fox in a neon forest"
    assert view._inspire_btn.get_sensitive() is True


def test_inspire_error_is_fail_soft(monkeypatch):
    """A prompt-gen failure must re-enable the button and never raise --
    same fail-soft contract ControlPanel's `set_inspire_error` upholds."""
    fake = _FakeInspire()
    view = _make_view(monkeypatch, inspire_fn=fake)

    view._inspire_btn.emit("clicked")
    _prompt_type, _seed_text, _on_result, on_error = fake.calls[0]

    on_error("prompt server is down")  # must not raise

    assert view._inspire_btn.get_sensitive() is True
    assert view._prompt_entry.get_text() == ""  # untouched on error


def test_inspire_click_is_a_noop_without_inspire_fn(monkeypatch):
    """inspire_fn=None -> no button exists, so there is nothing to click; a
    direct call to the click handler (defensive) must also never raise."""
    view = _make_view(monkeypatch)
    view._on_inspire_clicked(None)  # must not raise


def test_inspire_fn_raising_synchronously_is_fail_soft(monkeypatch):
    """If the injected inspire_fn itself raises before ever calling a
    callback (e.g. thread spawn failure), the button must still recover."""
    def _boom(prompt_type, seed_text, on_result, on_error):
        raise RuntimeError("boom")

    view = _make_view(monkeypatch, inspire_fn=_boom)
    view._inspire_btn.emit("clicked")  # must not raise

    assert view._inspire_btn.get_sensitive() is True


# ── Two-mode restoration (regression fix 1/2) ────────────────────────────────
#
# The deleted ControlPanel/ArtgenPanel Inspire buttons read the field's
# EXISTING text and threaded it through as a seed: empty field -> fresh
# generation; non-empty field -> the backend (`prompt_client.generate_prompt`)
# polishes/remixes those words instead. Create's own button lost this when it
# was built (`_on_inspire_clicked` never read `_prompt_entry` at all) — these
# tests pin the restored behavior.

def test_inspire_click_with_empty_entry_passes_empty_seed_text(monkeypatch):
    fake = _FakeInspire()
    view = _make_view(monkeypatch, inspire_fn=fake)
    assert view._prompt_entry.get_text() == ""

    view._inspire_btn.emit("clicked")

    assert len(fake.calls) == 1
    _prompt_type, seed_text, _on_result, _on_error = fake.calls[0]
    assert seed_text == ""


def test_inspire_click_with_existing_text_passes_it_as_seed(monkeypatch):
    """Non-empty `_prompt_entry` text at click time -> remix mode: the exact
    existing words are threaded through as `seed_text`."""
    fake = _FakeInspire()
    view = _make_view(monkeypatch, inspire_fn=fake)
    view._prompt_entry.set_text("a castle")

    view._inspire_btn.emit("clicked")

    assert len(fake.calls) == 1
    _prompt_type, seed_text, _on_result, _on_error = fake.calls[0]
    assert seed_text == "a castle"


def test_inspire_click_strips_whitespace_from_existing_text(monkeypatch):
    fake = _FakeInspire()
    view = _make_view(monkeypatch, inspire_fn=fake)
    view._prompt_entry.set_text("  a castle  ")

    view._inspire_btn.emit("clicked")

    assert fake.calls[0][1] == "a castle"


# ── Theme Set (SP-3d-1) ──────────────────────────────────────────────────
#
# Migrated from ControlPanel's own "🎬 Theme Set" button (never dropped, per
# CLAUDE.md's "user: never drop" note). `on_theme_set(medium, params)` fires
# SYNCHRONOUSLY (same shape as `on_create`) — MainWindow's real seam
# (`_on_create_theme_set`) owns the background thread + the eventual
# `set_theme_queued`/`set_theme_error` callback itself, so these tests only
# need a recording fake, not an async callback pair. Migration-safe:
# `on_theme_set=None` (the default) means no button at all.

class _FakeThemeSet:
    """Records `on_theme_set(medium, params)` calls without doing anything
    else — lets a test drive the button's busy state and then manually call
    `set_theme_queued`/`set_theme_error` back, mirroring how the real seam
    (MainWindow._on_create_theme_set) calls back asynchronously once its
    background thread finishes."""

    def __init__(self):
        self.calls = []  # list of (medium, params)

    def __call__(self, medium, params):
        self.calls.append((medium, params))


def test_theme_set_button_absent_without_on_theme_set(monkeypatch):
    view = _make_view(monkeypatch)
    assert getattr(view, "_theme_set_btn", None) is None


def test_theme_set_button_present_with_on_theme_set(monkeypatch):
    view = _make_view(monkeypatch, on_theme_set=_FakeThemeSet())
    assert view._theme_set_btn is not None
    assert isinstance(view._theme_set_btn, Gtk.Button)


def test_theme_set_click_calls_seam_with_active_medium_and_collected_params(monkeypatch):
    """Default-active medium in `_fake_mediums` is "image"."""
    fake = _FakeThemeSet()
    view = _make_view(monkeypatch, on_theme_set=fake)

    view._theme_set_btn.emit("clicked")

    assert len(fake.calls) == 1
    medium, params = fake.calls[0]
    assert medium.id == "image"
    assert isinstance(params, dict)


def test_theme_set_click_shows_busy_state(monkeypatch):
    fake = _FakeThemeSet()
    view = _make_view(monkeypatch, on_theme_set=fake)

    view._theme_set_btn.emit("clicked")

    assert view._theme_set_btn.get_sensitive() is False
    assert view._theme_generating is True


def test_theme_set_click_is_a_noop_without_on_theme_set(monkeypatch):
    """on_theme_set=None -> no button exists; a direct call to the click
    handler (defensive) must also never raise."""
    view = _make_view(monkeypatch)
    view._on_theme_set_clicked(None)  # must not raise


def test_theme_set_click_while_already_generating_does_not_re_fire(monkeypatch):
    fake = _FakeThemeSet()
    view = _make_view(monkeypatch, on_theme_set=fake)

    view._theme_set_btn.emit("clicked")
    view._theme_set_btn.emit("clicked")  # second click while busy

    assert len(fake.calls) == 1


def test_theme_set_seam_raising_synchronously_is_fail_soft(monkeypatch):
    """If the injected seam itself raises before ever calling back (e.g. a
    thread failing to spawn), the button must still recover and the error
    must surface through the result panel."""
    def _boom(medium, params):
        raise RuntimeError("boom")

    view = _make_view(monkeypatch, on_theme_set=_boom)
    view._theme_set_btn.emit("clicked")  # must not raise

    assert view._theme_set_btn.get_sensitive() is True
    assert view._result_panel._state == "error"


def test_set_theme_queued_resets_busy_state(monkeypatch):
    fake = _FakeThemeSet()
    view = _make_view(monkeypatch, on_theme_set=fake)

    view._theme_set_btn.emit("clicked")
    assert view._theme_generating is True

    view.set_theme_queued(5, "Hitchcock: Rear Window")

    assert view._theme_generating is False
    assert view._theme_set_btn.get_sensitive() is True


def test_set_theme_error_resets_busy_state_and_shows_result_panel_error(monkeypatch):
    fake = _FakeThemeSet()
    view = _make_view(monkeypatch, on_theme_set=fake)

    view._theme_set_btn.emit("clicked")
    view.set_theme_error("prompt server is down")

    assert view._theme_generating is False
    assert view._theme_set_btn.get_sensitive() is True
    assert view._result_panel._state == "error"


# ── Scoped model dropdown (Task 6 — replaces the retired model strip) ───
#
# The flat, always-visible "live-model strip" (and the Task 7 model-door
# cards built on top of it) is retired this task — it was a non-wrapping
# `Gtk.Box` that overflowed the window. `_model_dropdown` replaces it: a
# single dropdown, scoped to the ACTIVE medium's own models, mounted above
# `_panel_host`.

def test_model_strip_is_retired(monkeypatch):
    view = _make_view(monkeypatch)
    assert not hasattr(view, "_model_strip") or view._model_strip is None
    assert not hasattr(view, "_model_cards")


def test_no_persistent_model_strip(monkeypatch):
    """Step-1 brief test, verbatim (task-6-brief.md)."""
    view = _make_view(monkeypatch)
    assert not hasattr(view, "_model_strip") or view._model_strip is None


def test_scoped_dropdown_lists_only_active_medium_models(monkeypatch):
    """Step-1 brief test, verbatim (task-6-brief.md): default-active medium
    is "image" -> only image-capable server_manager keys are offered."""
    view = _make_view(monkeypatch)
    keys = view._scoped_model_keys()
    assert "flux" in keys and "wan2.2" not in keys


def test_scoped_dropdown_switches_contents_with_active_medium(monkeypatch):
    view = _make_view(monkeypatch)

    view._chip_buttons["video"].set_active(True)

    keys = view._scoped_model_keys()
    assert "wan2.2" in keys and "mochi" in keys
    assert "flux" not in keys


def test_model_dropdown_widget_model_rebuilds_on_medium_swap(monkeypatch):
    view = _make_view(monkeypatch)
    image_list = view._model_dropdown.get_model()

    view._chip_buttons["video"].set_active(True)

    assert view._model_dropdown.get_model() is not image_list


def test_model_dropdown_default_selection_yields_image_default_model_id(monkeypatch):
    """Index 0 of the default-active "image" medium's scoped dropdown must
    translate to the exact canonical id ImageParamPanel's OWN (now-hidden)
    model dropdown would have produced by default — the migration invariant
    this task's `_canonical_model_id_for` exists to preserve."""
    view = _make_view(monkeypatch)
    idx = view._model_dropdown.get_selected()
    _key, canonical, _label = view._model_dropdown_entries[idx]
    assert canonical == "flux.1-schnell"


def test_video_scoped_dropdown_includes_skyreels(monkeypatch):
    """SP-3c-1 re-enables SkyReels-I2V: VideoParamPanel now owns a
    `SeedImageWell` (same widget as ImageParamPanel), so the I2V model can be
    supplied a conditioning image and is no longer a guaranteed-fail trap —
    the scoped dropdown must offer it like any other video model."""
    view = _make_view(monkeypatch)
    view._chip_buttons["video"].set_active(True)

    canonicals = {c for _k, c, _l in view._model_dropdown_entries}
    assert "skyreels-v2-i2v-14b-540p" in canonicals


# ── Native AnimateDiff in the scoped Video dropdown (SP-3c-2) ────────────────
#
# AnimateDiff v0.9 is hardware-only (no `server_manager.ServerDef` — see that
# module's `CAPABILITY_LABELS` comment), so `servers_for_capability("video")`
# never lists it; `_scoped_model_keys` appends it as a synthetic entry so it's
# still reachable from the ONE model picker the user actually sees.


def test_video_scoped_dropdown_includes_animatediff(monkeypatch):
    view = _make_view(monkeypatch)
    view._chip_buttons["video"].set_active(True)

    canonicals = {c for _k, c, _l in view._model_dropdown_entries}
    assert "animatediff-blackhole" in canonicals


def test_animatediff_scoped_dropdown_entry_has_a_readable_label(monkeypatch):
    """No `ServerDef` backs "animatediff" — the label must still fall back to
    something human-readable (`server_manager.CAPABILITY_LABELS`), not the
    bare key."""
    view = _make_view(monkeypatch)
    view._chip_buttons["video"].set_active(True)

    labels = {k: label for k, _c, label in view._model_dropdown_entries}
    assert labels["animatediff"] == "AnimateDiff  (Blackhole)"


def test_animatediff_scoped_dropdown_dot_always_ready(monkeypatch):
    """AnimateDiff needs no server — its dot must read "ready", never
    "offline", regardless of what `_model_health`/the status service report
    for every other key (which default an unrecognized key to OFF)."""
    view = _make_view(monkeypatch)
    assert view._model_dot_glyph("animatediff") == "●"


def _animatediff_index(view):
    for idx, (key, _canonical, _label) in enumerate(view._model_dropdown_entries):
        if key == "animatediff":
            return idx
    raise AssertionError("animatediff entry not found in video scoped dropdown")


def _wan22_index(view):
    """Index of the wan2.2 entry in the video scoped dropdown (server key
    "wan2.2", canonical id "wan2.2-t2v") — looked up by key rather than
    assumed to be a fixed index, since AnimateDiff now occupies index 0
    (SP-3c-2 reordering, video-medium-default change)."""
    for idx, (key, _canonical, _label) in enumerate(view._model_dropdown_entries):
        if key == "wan2.2":
            return idx
    raise AssertionError("wan2.2 entry not found in video scoped dropdown")


def test_collect_params_model_animatediff(monkeypatch):
    """Selecting AnimateDiff in the SCOPED dropdown (the one the user sees)
    must produce the "animatediff-blackhole" canonical id in collect_params'
    "model" — exactly like every other video model choice."""
    view = _make_view(monkeypatch)
    view._chip_buttons["video"].set_active(True)

    view._model_dropdown.set_selected(_animatediff_index(view))

    assert view._collect_params()["model"] == "animatediff-blackhole"


def test_selecting_animatediff_in_scoped_dropdown_reveals_panel_options(monkeypatch):
    """The whole point of `_sync_panel_model_selection`: VideoParamPanel's
    own AnimateDiff-options box (invisible to RoleZonePanel's zone-building —
    it never renders a `kind == "model"` row) must become visible once the
    user picks AnimateDiff in the ONE dropdown they actually see.

    AnimateDiff is now the video medium's scoped-dropdown DEFAULT (index 0,
    SP-3c-2 reordering), so a fresh mount already syncs the panel to it and
    the options row starts visible. Switch away to wan2.2 first to get a
    clean hidden baseline, then switch back to AnimateDiff and confirm the
    reveal — preserving the original "selecting AnimateDiff reveals the
    options row" assertion."""
    view = _make_view(monkeypatch)
    view._chip_buttons["video"].set_active(True)
    panel = _panel_of(view)
    assert panel._ad_options_row.get_visible() is True  # AnimateDiff is the new default

    view._model_dropdown.set_selected(_wan22_index(view))
    assert panel._ad_options_row.get_visible() is False

    view._model_dropdown.set_selected(_animatediff_index(view))

    assert panel._ad_options_row.get_visible() is True


def test_switching_away_from_animatediff_hides_panel_options_again(monkeypatch):
    view = _make_view(monkeypatch)
    view._chip_buttons["video"].set_active(True)
    panel = _panel_of(view)
    view._model_dropdown.set_selected(_animatediff_index(view))
    assert panel._ad_options_row.get_visible() is True

    # Index 0 is now AnimateDiff itself (SP-3c-2 reordering) — look up wan2.2
    # by key instead of assuming a fixed index, so this actually switches
    # away rather than re-selecting the same entry.
    view._model_dropdown.set_selected(_wan22_index(view))  # back to wan2.2

    assert panel._ad_options_row.get_visible() is False


def test_animatediff_selection_reaches_on_create_via_cta(monkeypatch):
    """Full round trip: pick AnimateDiff in the scoped dropdown, click
    Create, and confirm the params handed to `on_create` carry the
    AnimateDiff canonical id (main_window._create_generate_native turns this
    into `video_model_key="animatediff"` — covered in
    tests/test_main_window_create_generate.py)."""
    calls = []
    view = _make_view(monkeypatch, on_create=lambda medium, params: calls.append((medium, params)))
    view._chip_buttons["video"].set_active(True)

    view._model_dropdown.set_selected(_animatediff_index(view))
    view._cta_btn.emit("clicked")

    assert calls[-1][1]["model"] == "animatediff-blackhole"


# ── LLM-free ARTGEN mediums self-select as their own model ───────────────────
#
# Bug report: "in generate if you select animatediff, it asks for a model but
# it should just select animatediff as the model." Root cause: AnimateDiff is
# its own artgen medium (source="artgen", generator="animatediff"), and
# `_scoped_model_keys` used to treat EVERY artgen medium as chat-LLM-backed,
# listing the chat-LLM servers (Qwen3-8B, Llama-3.3-70B, …) in its scoped
# dropdown — but AnimateDiff's generator (`uses_llm=False`) bypasses the chat
# LLM entirely; it IS the model. Generalized via `Medium.uses_llm`: any
# artgen medium with `uses_llm=False` gets a single self-entry (keyed by its
# own medium id) instead of the chat-server list, and that entry auto-selects
# at index 0 (the only entry) — no more asking. `verse` (uses_llm=True,
# unchanged) is the regression control: its dropdown must still list the
# chat servers exactly as before.
#
# This fixture mirrors the REAL registered "animatediff" artgen generator
# (plugins/animatediff/plugin.py, loaded into the real artgen registry at
# import time) so ArtgenParamPanel introspection — and therefore
# `collect()` — exercises the actual add_args() contract, not a stand-in.

def _fake_mediums_with_llm_free_animatediff():
    return [
        Medium(id="image", label="Image", icon="\U0001f5bc️", kind="image",
               source="native", generator=None),
        Medium(id="video", label="Video", icon="\U0001f3a5", kind="video",
               source="native", generator=None),
        Medium(id="animate", label="Animate", icon="\U0001f483", kind="gif",
               source="native", generator=None),
        Medium(id="verse", label="Verse", icon="✍", kind="text",
               source="artgen", generator="verse"),
        Medium(id="animatediff", label="AnimateDiff", icon="\U0001f57a",
               kind="gif", source="artgen", generator="animatediff",
               uses_llm=False),
    ]


def test_llm_free_artgen_medium_scoped_dropdown_has_exactly_one_self_entry(monkeypatch):
    view = _make_view(monkeypatch, mediums_fn=_fake_mediums_with_llm_free_animatediff)
    view._chip_buttons["animatediff"].set_active(True)

    entries = view._model_dropdown_entries
    assert len(entries) == 1
    key, canonical, label = entries[0]
    assert key == "animatediff"
    assert canonical is None          # no "model" field for collect() to override
    assert label == "AnimateDiff"     # medium.label, not a server_manager label


def test_llm_free_artgen_medium_scoped_dropdown_excludes_chat_servers(monkeypatch):
    view = _make_view(monkeypatch, mediums_fn=_fake_mediums_with_llm_free_animatediff)
    view._chip_buttons["animatediff"].set_active(True)

    keys = [k for k, _c, _l in view._model_dropdown_entries]
    labels = [label for _k, _c, label in view._model_dropdown_entries]
    assert "artgen-qwen3-8b" not in keys
    assert not any("Qwen3-8B" in label for label in labels)


def test_llm_free_artgen_medium_self_entry_dot_is_always_ready(monkeypatch):
    """`_model_dropdown_entries`' third element is the bare label (no dot —
    matches the existing "animatediff"/detected-key entries' convention);
    the rendered dot lives in the Gtk.StringList strings actually shown in
    the dropdown, so check those (mirrors how the pre-existing native
    AnimateDiff dot test would, if it inspected the widget instead of
    calling `_model_dot_glyph` directly)."""
    view = _make_view(monkeypatch, mediums_fn=_fake_mediums_with_llm_free_animatediff)
    view._chip_buttons["animatediff"].set_active(True)

    model = view._model_dropdown.get_model()
    rendered = [model.get_string(i) for i in range(model.get_n_items())]
    assert rendered == ["● AnimateDiff"]


def test_llm_free_artgen_medium_self_entry_auto_selected(monkeypatch):
    """Being the only entry, it auto-selects at index 0 -- this is what
    satisfies "just select animatediff as the model" (no picker, no choice
    to make)."""
    view = _make_view(monkeypatch, mediums_fn=_fake_mediums_with_llm_free_animatediff)
    view._chip_buttons["animatediff"].set_active(True)

    assert view._model_dropdown.get_selected() == 0


def test_llm_backed_artgen_medium_scoped_dropdown_still_lists_chat_servers(monkeypatch):
    """Regression control: verse (uses_llm=True, the pre-existing default)
    must be completely unaffected -- its dropdown still lists the chat-LLM
    servers exactly as before this fix."""
    view = _make_view(monkeypatch, mediums_fn=_fake_mediums_with_llm_free_animatediff)
    view._chip_buttons["verse"].set_active(True)

    keys = [k for k, _c, _l in view._model_dropdown_entries]
    labels = [label for _k, _c, label in view._model_dropdown_entries]
    assert "artgen-qwen3-8b" in keys
    assert any("Qwen3-8B" in label for label in labels)


def test_collect_params_unchanged_for_llm_free_artgen_medium(monkeypatch):
    """collect() for AnimateDiff must be byte-for-byte identical to before
    this fix: no "model" key is ever introduced (AnimateDiffGenerator's real
    add_args() never declares one, and the self-entry's canonical is None,
    so `_collect_params`'s "model" override is a no-op)."""
    view = _make_view(monkeypatch, mediums_fn=_fake_mediums_with_llm_free_animatediff)
    view._chip_buttons["animatediff"].set_active(True)

    params = view._collect_params()
    assert "model" not in params


def test_model_dot_glyph_does_not_crash_for_llm_free_artgen_self_key(monkeypatch):
    """No `server_manager.SERVERS` entry (or CAPABILITY_LABELS entry) backs
    an artgen generator's own name -- `_model_dot_glyph` must handle the
    self key defensively (mirroring the existing "animatediff"/detected-key
    special cases) rather than crash or silently read "offline"."""
    view = _make_view(monkeypatch, mediums_fn=_fake_mediums_with_llm_free_animatediff)
    medium = next(
        m for m in _fake_mediums_with_llm_free_animatediff() if m.id == "animatediff"
    )

    assert view._model_dot_glyph("animatediff", medium=medium) == "●"


def test_model_health_reflects_running_vs_not(monkeypatch):
    view = _make_view(monkeypatch)
    assert view._model_health == {"wan2.2": True, "flux": False}


def _mochi_index(view):
    """Index of the Mochi entry in the video scoped dropdown (canonical id
    "mochi-1-preview") — asserts it exists so the test fails loudly if the
    dropdown contents change out from under it."""
    for idx, (_key, canonical, _label) in enumerate(view._model_dropdown_entries):
        if canonical == "mochi-1-preview":
            return idx
    raise AssertionError("Mochi entry not found in video scoped dropdown")


def test_health_refresh_preserves_scoped_model_selection(monkeypatch):
    """Regression guard (whole-slice review, Important): the async health
    refresh must NOT snap the scoped dropdown back to index 0 and silently
    discard the user's model choice.

    Repro: active medium = video, user picks Mochi (not the default index 0
    Wan2.2), THEN the initial `status_all` health check completes (it races
    the view's appearance, landing seconds later when servers are down) and
    `_apply_model_health` repopulates the dropdown. Before the fix this reset
    the selection to Wan2.2 -> Create generated "wan2.2-t2v" instead of the
    chosen "mochi-1-preview". After the fix the selection is preserved by
    server key across repopulation.

    Fails against 839f80a (resets to wan2.2-t2v); passes after.
    """
    calls = []
    view = _make_view(monkeypatch, on_create=lambda medium, params: calls.append((medium, params)))
    view._chip_buttons["video"].set_active(True)

    view._model_dropdown.set_selected(_mochi_index(view))
    assert view._collect_params()["model"] == "mochi-1-preview"

    # The async health result lands (same active medium) -> repopulation.
    view._apply_model_health({"wan2.2": True, "mochi": False})

    # Selection preserved: still Mochi, not snapped back to Wan2.2 (index 0).
    idx = view._model_dropdown.get_selected()
    _key, canonical, _label = view._model_dropdown_entries[idx]
    assert canonical == "mochi-1-preview"
    view._cta_btn.emit("clicked")
    assert calls[-1][1]["model"] == "mochi-1-preview"


def test_medium_swap_resets_scoped_model_selection_to_default(monkeypatch):
    """The flip side of preservation: swapping to a DIFFERENT medium must
    reset to that medium's default (index 0) — the previously-selected key
    belongs to another medium and isn't in the new list, so it naturally
    falls back to 0 rather than being (wrongly) preserved across mediums."""
    view = _make_view(monkeypatch)
    view._chip_buttons["video"].set_active(True)
    view._model_dropdown.set_selected(_mochi_index(view))
    assert view._collect_params()["model"] == "mochi-1-preview"

    # Swap video -> image: a different medium, different key set.
    view._chip_buttons["image"].set_active(True)

    assert view._model_dropdown.get_selected() == 0
    assert view._collect_params()["model"] == "flux.1-schnell"


def test_repopulation_preserves_selection_after_preselect_model_key(monkeypatch):
    """`_preselect_model_key` (used by the Model-door card path) selects a
    non-default entry; a subsequent health refresh must not undo it."""
    view = _make_view(monkeypatch)
    view._chip_buttons["video"].set_active(True)
    view._preselect_model_key("mochi")
    assert view._collect_params()["model"] == "mochi-1-preview"

    view._apply_model_health({"wan2.2": False, "mochi": True})

    assert view._collect_params()["model"] == "mochi-1-preview"


# ── SP-2 Task 3: auto-select the running/starting model (fresh populate) ──
#
# `_FakeStatusService.running_or_starting` is keyed by capability
# ("image"/"video"/"animate" — see `_MODEL_STATUS_CAPABILITY` in
# create_view.py). Video's scoped dropdown lists wan2.2 at index 0 (the
# pre-Task-3 default) and mochi at index 1 (see `_mochi_index`); every test
# below that wants a signal-bearing assertion (one that would actually FAIL
# without the Task 3 implementation) picks a running/starting key that is
# NOT the medium's index-0 default, so a bug that silently ignores the
# service can't accidentally pass.

def test_autoselect_running_model_on_medium_populate(monkeypatch):
    """A fresh populate (medium switch) defaults to the model the service
    reports running for that medium's capability — here Mochi (index 1),
    proving the auto-select actually overrides the pre-Task-3 index-0
    (Wan2.2) default rather than coincidentally matching it."""
    fake_service = _FakeStatusService(running={"video": "mochi"})
    view = _make_view(monkeypatch, status_service=fake_service)

    view._chip_buttons["video"].set_active(True)  # fresh populate for video

    idx = view._model_dropdown.get_selected()
    assert idx == _mochi_index(view)
    _key, canonical, _label = view._model_dropdown_entries[idx]
    assert canonical == "mochi-1-preview"


def test_autoselect_starting_when_none_ready(monkeypatch):
    """`running_or_starting` makes no READY/STARTING distinction visible to
    CreateView (that preference order lives in `ModelStatusService` itself,
    unit-tested separately in test_model_status.py) — from here, a STARTING
    key behaves identically to a READY one: whatever key the service hands
    back is what gets selected. Uses "motif" (image capability, index 3 —
    not the default "flux" at index 0) to keep the assertion signal-bearing
    for a second medium."""
    fake_service = _FakeStatusService(running={"image": "motif"})
    view = _make_view(monkeypatch, status_service=fake_service)
    # "image" is already the default-active medium (see
    # test_first_medium_is_active_by_default) -- but the dropdown is
    # populated by _swap_panel during __init__, before which the fake
    # service already had its `running` map, so construction itself is the
    # fresh populate under test here.

    idx = view._model_dropdown.get_selected()
    _key, canonical, _label = view._model_dropdown_entries[idx]
    assert canonical == "motif-image-6b-preview"


def test_autoselect_falls_back_to_default_when_nothing_running(monkeypatch):
    """`running_or_starting` returning `None` (nothing running/starting for
    this capability) must fall back to the existing medium default (index
    0), not crash or leave the dropdown unselected. Index 0 is now
    AnimateDiff (SP-3c-2 reordering: it's the local, no-server default) —
    "nothing running -> fall back to AnimateDiff" is exactly the intended
    new-default outcome."""
    fake_service = _FakeStatusService(running={})
    view = _make_view(monkeypatch, status_service=fake_service)

    view._chip_buttons["video"].set_active(True)

    assert view._model_dropdown.get_selected() == 0
    assert view._collect_params()["model"] == "animatediff-blackhole"


def test_manual_pick_preserved_across_refresh_with_status_service(monkeypatch):
    """v0.28.1 regression guard, extended to the status_service path: a
    manual pick must survive a same-medium refresh even when the service now
    reports a DIFFERENT model running — auto-select must never fire outside
    the "prev_key not found" (fresh populate) branch."""
    fake_service = _FakeStatusService(running={"video": "wan2.2"})
    view = _make_view(monkeypatch, status_service=fake_service)
    view._chip_buttons["video"].set_active(True)

    view._model_dropdown.set_selected(_mochi_index(view))
    assert view._collect_params()["model"] == "mochi-1-preview"

    # Same-medium refresh (status snapshot push) lands; the service would
    # auto-select wan2.2 on a FRESH populate, but this is a refresh, not a
    # fresh populate, so the manual Mochi pick must hold.
    fake_service.push({"wan2.2": Status.READY, "mochi": Status.OFF})

    assert view._collect_params()["model"] == "mochi-1-preview"


def test_autoselect_noop_when_status_service_is_none(monkeypatch):
    """No service injected -> byte-identical to pre-Task-3 behavior: fresh
    populate always lands on index 0, exactly like
    `test_medium_swap_resets_scoped_model_selection_to_default`. Index 0 is
    now AnimateDiff (SP-3c-2 reordering) — the local, no-server default."""
    view = _make_view(monkeypatch)  # status_service=None
    view._chip_buttons["video"].set_active(True)

    assert view._model_dropdown.get_selected() == 0
    assert view._collect_params()["model"] == "animatediff-blackhole"


# ── Model door visibility (grid shown only in "model" mode) ──────────────
#
# The model door row (which now holds the Task 7 grouped grid, not the
# retired Task 6 placeholder) is visible only while the "model" door toggle
# is active — same show/hide-on-door-toggle contract the idea row has.

def test_model_door_row_visible_only_in_model_mode(monkeypatch):
    view = _make_view(monkeypatch)
    assert view._model_door_row.get_visible() is False

    view._doors["model"].set_active(True)
    assert view._model_door_row.get_visible() is True

    view._doors["idea"].set_active(True)
    assert view._model_door_row.get_visible() is False


# ── Model door: grouped, wrapping model grid (Task 7) ────────────────────
#
# Task 6 left an honest "not built yet" placeholder in the model door after
# retiring the non-wrapping live-model strip. Task 7 replaces that
# placeholder with a grouped, wrapping Gtk.FlowBox grid — every
# server_manager key classified into Image/Video/Animate/Text — so the whole
# (~15 and growing) model collection is browsable without ever overflowing
# the window. `_model_door_groups()` and `_activate_model_card()` are the
# pure/GTK test seams (task-7-brief.md, Step 1, verbatim below).

def test_model_door_groups_by_type(monkeypatch):
    view = _make_view(monkeypatch)
    groups = view._model_door_groups()
    assert set(groups) <= {"Image", "Video", "Animate", "Text"}
    assert "flux" in groups["Image"]
    assert all(v for v in groups.values())


def test_model_door_card_click_routes_to_medium(monkeypatch):
    view = _make_view(monkeypatch)
    view._activate_model_card("flux")
    assert view._active_medium.id == "image"
    assert view._entry_mode == "idea"


def test_model_door_groups_video_and_animate_and_text(monkeypatch):
    """Beyond Image (the brief's one asserted group), the other three groups
    must also classify sensibly against the real server_manager.SERVERS
    table combined with _fake_mediums()'s image/video/animate/verse list."""
    view = _make_view(monkeypatch)
    groups = view._model_door_groups()

    assert "wan2.2" in groups["Video"]
    assert "mochi" in groups["Video"]
    assert "skyreels" in groups["Video"]
    assert "flux" not in groups["Video"]

    assert groups["Animate"] == ["animate"]

    # Every artgen chat-LLM server, plus prompt-server (capability "prompt"),
    # lands in Text.
    assert "prompt-server" in groups["Text"]
    assert "artgen-qwen3-8b" in groups["Text"]
    assert "artgen-llama-3.3-70b" in groups["Text"]


# _fake_mediums() has exactly ONE artgen medium (verse, text-kind), which
# masks a critical mis-grouping/-routing bug: the six chat-LLM backends have
# the generic ("artgen",) capability, and the OLD (3c11874) code classified
# them by "the FIRST artgen medium in mediums_fn()". In the real app that
# first artgen medium is `animatediff` (kind "gif") -> every "Qwen3-8B" card
# would land in **Animate** and clicking it would switch the panel to
# AnimateDiff. This fixture mirrors default_mediums()'s real shape: natives
# first, then artgen mediums in alphabetical order so `animatediff` (gif)
# precedes `verse` (text) — the exact ordering that triggered the bug.

def _fake_mediums_multi_artgen():
    return [
        Medium(id="image", label="Image", icon="\U0001f5bc️", kind="image",
               source="native", generator=None),
        Medium(id="video", label="Video", icon="\U0001f3a5", kind="video",
               source="native", generator=None),
        Medium(id="animate", label="Animate", icon="\U0001f483", kind="gif",
               source="native", generator=None),
        Medium(id="animatediff", label="AnimateDiff", icon="\U0001f57a",
               kind="gif", source="artgen", generator="animatediff"),
        Medium(id="verse", label="Verse", icon="✍", kind="text",
               source="artgen", generator="verse"),
    ]


def test_llm_servers_group_as_text_with_multiple_artgen_mediums(monkeypatch):
    """CRITICAL regression guard (fails against 3c11874, passes after the
    capability-based-classification fix): with MORE THAN ONE artgen medium
    present — `animatediff` (gif) alphabetically before `verse` (text),
    mirroring default_mediums() — every artgen-* chat/LLM key AND
    prompt-server must land in groups["Text"], never in groups["Animate"]."""
    view = _make_view(monkeypatch, mediums_fn=_fake_mediums_multi_artgen)
    groups = view._model_door_groups()

    llm_keys = [
        "artgen-qwen3-8b",
        "artgen-llama-3.1-8b",
        "artgen-qwen2.5-7b",
        "artgen-llama-3.3-70b",
        "artgen-qwen3-32b",
        "artgen-deepseek-r1-70b",
        "prompt-server",
    ]
    for key in llm_keys:
        assert key in groups["Text"], f"{key} must be in Text, not elsewhere"
        assert key not in groups.get("Animate", []), (
            f"{key} must NOT be in Animate (the first-artgen-medium bug)"
        )


def test_activate_llm_card_does_not_jump_to_animate_medium(monkeypatch):
    """CRITICAL regression guard (fails against 3c11874, passes after the
    fix): clicking a chat-LLM card must NOT switch the active medium to the
    animate/gif medium (the first-artgen-medium `_server_key_to_medium_id`
    would resolve). The active medium is left unchanged — a Text card click
    never lands the user on AnimateDiff."""
    view = _make_view(monkeypatch, mediums_fn=_fake_mediums_multi_artgen)
    before = view._active_medium.id
    assert before == "image"

    view._activate_model_card("artgen-qwen3-8b")

    assert view._active_medium.id == before          # unchanged
    assert view._active_medium.id not in ("animate", "animatediff")
    assert view._active_medium.kind != "gif"
    # It still moves the user into the create/Idea flow.
    assert view._entry_mode == "idea"


def test_model_door_omits_empty_groups(monkeypatch):
    """Classification is now purely by `server_manager.SERVERS` capabilities
    (independent of mediums_fn), so an empty group only arises when SERVERS
    itself has no server for that capability. Reduce SERVERS to image+text
    servers and assert the Video/Animate sections are omitted entirely — the
    "no empty groups" requirement."""
    import server_manager

    subset = {
        k: server_manager.SERVERS[k]
        for k in ("flux", "sdxl", "artgen-qwen3-8b", "prompt-server")
    }
    monkeypatch.setattr(server_manager, "SERVERS", subset)

    view = _make_view(monkeypatch)
    groups = view._model_door_groups()

    assert "Animate" not in groups
    assert "Video" not in groups
    assert set(groups) <= {"Image", "Text"}
    assert all(v for v in groups.values())


def test_build_model_door_renders_one_flowbox_section_per_nonempty_group(monkeypatch):
    view = _make_view(monkeypatch)
    groups = view._model_door_groups()

    door = view._build_model_door()

    assert isinstance(door, Gtk.Widget)
    sections = []
    child = door.get_first_child()
    while child is not None:
        sections.append(child)
        child = child.get_next_sibling()
    assert len(sections) == len(groups)

    # Every section's model list is a wrapping Gtk.FlowBox, never an
    # unbounded horizontal Gtk.Box — the width-clamp requirement.
    for section in sections:
        flow = section.get_first_child().get_next_sibling()
        assert isinstance(flow, Gtk.FlowBox)


def test_activate_model_card_switches_active_medium(monkeypatch):
    view = _make_view(monkeypatch)
    assert view._active_medium.id == "image"

    view._activate_model_card("wan2.2")

    assert view._active_medium.id == "video"
    assert view._entry_mode == "idea"


def test_activate_model_card_preselects_scoped_dropdown_when_practical(monkeypatch):
    view = _make_view(monkeypatch)

    view._activate_model_card("wan2.2")

    idx = view._model_dropdown.get_selected()
    key, _canonical, _label = view._model_dropdown_entries[idx]
    assert key == "wan2.2"


def test_activate_model_card_switches_from_model_door_back_to_idea(monkeypatch):
    view = _make_view(monkeypatch)
    view._doors["model"].set_active(True)
    assert view._entry_mode == "model"
    assert view._model_door_row.get_visible() is True

    view._activate_model_card("z-image-turbo")

    assert view._active_medium.id == "image"
    assert view._entry_mode == "idea"
    assert view._model_door_row.get_visible() is False
    assert view._prompt_entry.get_visible() is True


def test_activate_model_card_unknown_key_is_a_noop(monkeypatch):
    view = _make_view(monkeypatch)
    before = view._active_medium.id

    view._activate_model_card("not-a-real-server-key")  # must not raise

    assert view._active_medium.id == before


def test_activate_model_card_capability_without_medium_is_a_noop(monkeypatch):
    """"prompt-server" (capability "prompt") maps to no medium at all
    (_server_key_to_medium_id returns None) -> clicking it must not change
    the active medium or raise."""
    view = _make_view(monkeypatch)
    before = view._active_medium.id

    view._activate_model_card("prompt-server")

    assert view._active_medium.id == before


def test_model_door_rebuilds_dots_on_health_refresh(monkeypatch):
    """Task 6's single-source-of-truth discipline (CLAUDE.md: the artgen
    panel's health dot reuses the same source as generation routing) extends
    to the model door's cards — a health refresh must rebuild the door so its
    dots reflect the fresh status map, not the stale one from construction."""
    view = _make_view(monkeypatch)
    assert view._model_health.get("wan2.2") is True

    view._apply_model_health({"wan2.2": False, "flux": True})

    assert view._model_health == {"wan2.2": False, "flux": True}
    # The rebuilt door must exist and still be a well-formed widget tree
    # (no stale children left over from the pre-refresh build).
    assert view._model_door_row.get_first_child() is not None


# ── _server_key_to_medium_id (Task 7) ────────────────────────────────────
#
# Pure capability -> medium-id mapping, exercised against REAL
# server_manager.SERVERS entries (their `capabilities` tuples are already
# sensible for this) combined with the fake `_fake_mediums()` list.

def test_server_key_to_medium_id_maps_native_capabilities(monkeypatch):
    view = _make_view(monkeypatch)
    assert view._server_key_to_medium_id("wan2.2") == "video"    # capabilities=("video",)
    assert view._server_key_to_medium_id("flux") == "image"      # capabilities=("image",)
    assert view._server_key_to_medium_id("animate") == "animate"  # capabilities=("animate",)


def test_server_key_to_medium_id_maps_artgen_capability_to_first_artgen_medium(monkeypatch):
    """server_manager.SERVERS["artgen-qwen3-8b"] declares capabilities=("artgen",)
    -> since there is no single "artgen" medium id (each generator is its own
    medium), this maps to the first artgen-sourced medium in the current
    medium list — "verse" in _fake_mediums()."""
    view = _make_view(monkeypatch)
    assert view._server_key_to_medium_id("artgen-qwen3-8b") == "verse"


def test_server_key_to_medium_id_returns_none_for_capability_with_no_medium(monkeypatch):
    """server_manager.SERVERS["prompt-server"] declares capabilities=("prompt",)
    -> no medium maps to "prompt" -> None, not a crash or a wrong guess."""
    view = _make_view(monkeypatch)
    assert view._server_key_to_medium_id("prompt-server") is None


def test_server_key_to_medium_id_returns_none_for_unknown_key(monkeypatch):
    view = _make_view(monkeypatch)
    assert view._server_key_to_medium_id("not-a-real-server-key") is None


# ── Idea door: prompt entry (Task 7) ─────────────────────────────────────

def test_idea_door_shows_a_prompt_entry_by_default(monkeypatch):
    view = _make_view(monkeypatch)
    assert isinstance(view._prompt_entry, Gtk.Entry)
    assert view._prompt_entry.get_visible() is True


def test_prompt_entry_hidden_outside_idea_door(monkeypatch):
    view = _make_view(monkeypatch)

    view._doors["model"].set_active(True)
    assert view._prompt_entry.get_visible() is False

    view._doors["idea"].set_active(True)
    assert view._prompt_entry.get_visible() is True


def test_cta_payload_includes_typed_prompt(monkeypatch):
    calls = []
    view = _make_view(monkeypatch, on_create=lambda medium, params: calls.append((medium, params)))

    view._prompt_entry.set_text("a lighthouse in a storm")
    view._cta_btn.emit("clicked")

    assert calls[0][1]["prompt"] == "a lighthouse in a storm"
    # The active panel's own params are still present alongside the prompt.
    for key, value in _IMAGE_DEFAULTS.items():
        assert calls[0][1][key] == value


def test_cta_payload_omits_prompt_key_when_entry_is_empty(monkeypatch):
    """Backward-compat guard: every Tasks 3-6 CTA test asserts an exact params
    dict with no "prompt" key (the prompt entry is untouched/empty in those
    tests) — an empty entry must not silently inject a "prompt": "" key and
    break them."""
    calls = []
    view = _make_view(monkeypatch, on_create=lambda medium, params: calls.append((medium, params)))

    view._cta_btn.emit("clicked")

    assert "prompt" not in calls[0][1]
    assert calls[0][1] == _IMAGE_DEFAULTS


def test_cta_payload_strips_whitespace_only_prompt(monkeypatch):
    calls = []
    view = _make_view(monkeypatch, on_create=lambda medium, params: calls.append((medium, params)))

    view._prompt_entry.set_text("   ")
    view._cta_btn.emit("clicked")

    assert "prompt" not in calls[0][1]


# ── Modifier text folded into the prompt (Task 6) ────────────────────────

def test_collect_params_appends_modifier_text(monkeypatch):
    """Step-1 brief test, verbatim (task-6-brief.md)."""
    view = _make_view(monkeypatch)
    view._prompt_entry.set_text("a castle")
    view._active_panel.append_modifier_for_test("golden hour lighting")

    params = view._collect_params()

    assert params["prompt"] == "a castle golden hour lighting"


def test_collect_params_dict_keys_unchanged_for_image(monkeypatch):
    """Step-1 brief test, verbatim (task-6-brief.md)."""
    view = _make_view(monkeypatch)
    view._prompt_entry.set_text("x")

    p = view._collect_params()

    assert set(p) >= {
        "prompt", "negative_prompt", "num_inference_steps", "seed",
        "guidance_scale", "model",
    }


def test_collect_params_modifier_text_alone_needs_no_leading_space(monkeypatch):
    """An untouched idea-door entry (empty prompt) plus an applied modifier
    must not leave a stray leading space in the final prompt."""
    view = _make_view(monkeypatch)
    view._active_panel.append_modifier_for_test("golden hour lighting")

    params = view._collect_params()

    assert params["prompt"] == "golden hour lighting"


def test_collect_params_no_modifier_and_no_prompt_omits_prompt_key(monkeypatch):
    view = _make_view(monkeypatch)
    params = view._collect_params()
    assert "prompt" not in params


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
    # dedicated assertion on its exact contents. "verse" and every other
    # artgen medium are ported to ArtgenParamPanel in Task 6 — see the
    # ArtgenParamPanel section below for its dedicated CTA-routing assertion.
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
    # Six rows: seed image, steps, seed, guidance scale, model, negative prompt.
    rows = []
    child = widget.get_first_child()
    while child is not None:
        rows.append(child)
        child = child.get_next_sibling()
    assert len(rows) == 6
    # The widgets collect() reads must actually exist after build().
    assert panel._steps_adj is not None
    assert panel._seed_adj is not None
    assert panel._guidance_adj is not None
    assert panel._model_dropdown is not None
    assert panel._neg_entry is not None
    assert panel._seed_well is not None


def test_image_param_panel_collect_returns_exact_worker_kwargs_with_defaults():
    panel = ImageParamPanel()
    panel.build()

    assert panel.collect() == _IMAGE_DEFAULTS
    # Exactly the keys ImageGenerationWorker takes (minus `prompt`) — no more,
    # no less.
    assert set(panel.collect().keys()) == {
        "negative_prompt", "num_inference_steps", "seed", "guidance_scale", "model",
        "seed_image_path",
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
        "seed_image_path": "",
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


# ── VideoParamPanel (standalone, no CreateView needed) ───────────────────

def test_video_param_panel_is_a_create_param_panel():
    assert isinstance(VideoParamPanel(), CreateParamPanel)


def test_video_param_panel_build_returns_a_widget_with_controls():
    panel = VideoParamPanel()
    widget = panel.build()

    assert isinstance(widget, Gtk.Widget)
    # Seven rows: seed image, steps, seed, model, num_frames, negative
    # prompt, AnimateDiff options (SP-3c-2 — hidden by default, but still one
    # row of the panel's own root box).
    rows = []
    child = widget.get_first_child()
    while child is not None:
        rows.append(child)
        child = child.get_next_sibling()
    assert len(rows) == 7
    assert panel._steps_adj is not None
    assert panel._seed_adj is not None
    assert panel._model_dropdown is not None
    assert panel._frames_adj is not None
    assert panel._neg_entry is not None
    assert panel._seed_well is not None
    assert panel._ad_options_row is not None


def test_video_param_panel_collect_returns_exact_worker_kwargs_with_defaults():
    panel = VideoParamPanel()
    panel.build()

    assert panel.collect() == _VIDEO_DEFAULTS
    assert set(panel.collect().keys()) == {
        "negative_prompt", "num_inference_steps", "seed", "model", "num_frames",
        "seed_image_path", "animatediff_args",
    }


def test_video_param_panel_collect_reflects_changed_widget_values():
    panel = VideoParamPanel()
    panel.build()

    panel._neg_entry.set_text("blurry, low quality")
    panel._steps_adj.set_value(45)
    panel._seed_adj.set_value(9)
    panel._model_dropdown.set_selected(1)  # mochi
    panel._frames_adj.set_value(49)

    assert panel.collect() == {
        "negative_prompt": "blurry, low quality",
        "num_inference_steps": 45,
        "seed": 9,
        "model": "mochi-1-preview",
        "num_frames": 49,
        "seed_image_path": "",
        "animatediff_args": dict(_ANIMATEDIFF_DEFAULTS),
    }


def test_video_param_panel_num_frames_zero_collects_as_none():
    """The 0 sentinel means 'runner default' — mirrors the seed field's -1
    sentinel for 'random'. GenerationWorker's own default is `num_frames=None`."""
    panel = VideoParamPanel()
    panel.build()

    panel._frames_adj.set_value(0)
    assert panel.collect()["num_frames"] is None


def test_video_param_panel_model_dropdown_covers_all_four_choices():
    """SP-3c-1 re-enabled SkyReels-I2V (VideoParamPanel owns a `SeedImageWell`
    that can supply its required conditioning image); SP-3c-2 adds native
    AnimateDiff — all four video models (wan2/mochi/skyreels/animatediff) are
    selectable."""
    panel = VideoParamPanel()
    panel.build()

    expected = {
        0: "wan2.2-t2v",
        1: "mochi-1-preview",
        2: "skyreels-v2-i2v-14b-540p",
        3: "animatediff-blackhole",
    }
    for idx, model_id in expected.items():
        panel._model_dropdown.set_selected(idx)
        assert panel.collect()["model"] == model_id
    assert panel._model_dropdown.get_model().get_n_items() == 4
    assert "skyreels-v2-i2v-14b-540p" in _VIDEO_MODEL_IDS.values()
    assert "animatediff-blackhole" in _VIDEO_MODEL_IDS.values()


def test_video_param_panel_seed_well_supplies_skyreels_conditioning_image(tmp_path):
    """The re-enabled SkyReels-I2V choice is only useful once a seed image is
    actually set — proves the well's path reaches collect() regardless of
    which model is selected (VideoParamPanel doesn't gate seed_image_path on
    the model choice; `_create_generate_native`/the worker decide what to do
    with an empty one)."""
    panel = VideoParamPanel()
    panel.build()
    panel._model_dropdown.set_selected(2)  # skyreels

    img_path = tmp_path / "character.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal PNG header, enough to be a real file
    panel._seed_well.set_path(str(img_path))

    collected = panel.collect()
    assert collected["model"] == "skyreels-v2-i2v-14b-540p"
    assert collected["seed_image_path"] == str(img_path)


def test_video_param_panel_collect_before_build_degrades_to_defaults():
    panel = VideoParamPanel()
    assert panel.collect() == _VIDEO_DEFAULTS


# ── AnimateParamPanel (standalone, no CreateView needed) ─────────────────

def test_animate_param_panel_is_a_create_param_panel():
    assert isinstance(AnimateParamPanel(), CreateParamPanel)


def test_animate_param_panel_build_returns_a_widget_with_controls():
    panel = AnimateParamPanel()
    widget = panel.build()

    assert isinstance(widget, Gtk.Widget)
    # Five rows: motion video, character image, mode, steps, seed.
    rows = []
    child = widget.get_first_child()
    while child is not None:
        rows.append(child)
        child = child.get_next_sibling()
    assert len(rows) == 5
    assert panel._ref_video_entry is not None
    assert panel._ref_image_entry is not None
    assert panel._mode_anim_btn is not None
    assert panel._mode_repl_btn is not None
    assert panel._steps_adj is not None
    assert panel._seed_adj is not None


def test_animate_param_panel_collect_returns_exact_worker_kwargs_with_defaults():
    panel = AnimateParamPanel()
    panel.build()

    assert panel.collect() == _ANIMATE_DEFAULTS
    assert set(panel.collect().keys()) == {
        "reference_video_path", "reference_image_path",
        "num_inference_steps", "seed", "animate_mode", "model",
    }


def test_animate_param_panel_collect_reflects_changed_widget_values():
    panel = AnimateParamPanel()
    panel.build()

    panel._ref_video_entry.set_text("/home/user/motion.mp4")
    panel._ref_image_entry.set_text("/home/user/char.jpg")
    panel._steps_adj.set_value(33)
    panel._seed_adj.set_value(555)
    panel._mode_repl_btn.set_active(True)

    assert panel.collect() == {
        "reference_video_path": "/home/user/motion.mp4",
        "reference_image_path": "/home/user/char.jpg",
        "num_inference_steps": 33,
        "seed": 555,
        "animate_mode": "replacement",
        "model": "wan2.2-animate-14b",
    }


def test_animate_param_panel_mode_toggle_is_mutually_exclusive(monkeypatch):
    panel = AnimateParamPanel()
    panel.build()

    panel._mode_repl_btn.set_active(True)
    assert panel.collect()["animate_mode"] == "replacement"
    assert panel._mode_anim_btn.get_active() is False

    panel._mode_anim_btn.set_active(True)
    assert panel.collect()["animate_mode"] == "animation"
    assert panel._mode_repl_btn.get_active() is False


def test_animate_param_panel_empty_ref_paths_are_allowed():
    """Empty paths are valid — validation is the worker/CTA's concern, not
    the panel (see module docstring)."""
    panel = AnimateParamPanel()
    panel.build()

    assert panel.collect()["reference_video_path"] == ""
    assert panel.collect()["reference_image_path"] == ""


def test_animate_param_panel_file_pick_callbacks_set_entry_text_from_gfile():
    """Simulates a completed Gtk.FileDialog.open() round-trip without opening
    a real dialog: calls the panel's `_finish` handlers directly with a fake
    dlg/gfile pair, matching the `open_finish()` try/except pattern documented
    in CLAUDE.md (GTK4 FileDialog is async)."""
    panel = AnimateParamPanel()
    panel.build()

    class _FakeGFile:
        def get_path(self):
            return "/tmp/picked_motion.mp4"

    class _FakeDlg:
        def open_finish(self, _result):
            return _FakeGFile()

    panel._on_ref_video_picked(_FakeDlg(), None)
    assert panel._ref_video_entry.get_text() == "/tmp/picked_motion.mp4"


def test_animate_param_panel_file_pick_cancel_does_not_raise_or_clear():
    """`open_finish()` raises when the user cancels the dialog — the panel
    must swallow that (per CLAUDE.md's FileDialog try/except pattern) and
    leave the existing entry text untouched."""
    panel = AnimateParamPanel()
    panel.build()
    panel._ref_image_entry.set_text("/keep/me.png")

    class _FakeDlg:
        def open_finish(self, _result):
            raise Exception("cancelled")

    panel._on_ref_image_picked(_FakeDlg(), None)  # must not raise
    assert panel._ref_image_entry.get_text() == "/keep/me.png"


def test_animate_param_panel_collect_before_build_degrades_to_defaults():
    panel = AnimateParamPanel()
    assert panel.collect() == _ANIMATE_DEFAULTS


# ── ArtgenParamPanel (standalone, no CreateView needed) ──────────────────
#
# ArtgenParamPanel is parameterized by generator NAME, not one class per
# generator (task-6-brief.md's CRITICAL STRATEGY): it introspects the named
# generator's own `add_args(parser)` via a throwaway argparse.ArgumentParser
# and builds one control per resolved argparse dest. "verse" and "ansi" are
# used below specifically because their `add_args` produce different shapes
# (verse: choice/str/int; ansi: str/int-with-None-default/choice/choice/
# str/str) — proving the panel is generator-driven, not a hardcoded per-
# generator branch.

def test_artgen_param_panel_is_a_create_param_panel():
    assert isinstance(ArtgenParamPanel("verse"), CreateParamPanel)


def test_artgen_param_panel_verse_builds_controls_from_add_args():
    panel = ArtgenParamPanel("verse")
    widget = panel.build()

    assert isinstance(widget, Gtk.Widget)
    # verse.add_args declares exactly three args: --form, --theme, --count.
    assert {c.dest for c in panel._controls} == {"form", "theme", "count"}


def test_artgen_param_panel_verse_collect_returns_generator_defaults():
    panel = ArtgenParamPanel("verse")
    panel.build()

    assert panel.collect() == {
        "form": "haiku",
        "theme": "the passage of time",
        "count": 3,
    }


def test_artgen_param_panel_verse_collect_reflects_edited_widgets():
    panel = ArtgenParamPanel("verse")
    panel.build()

    controls = {c.dest: c for c in panel._controls}
    controls["theme"].widget.set_text("a machine dreaming")
    controls["count"].widget.get_adjustment().set_value(7)
    controls["form"].widget.set_selected(3)  # haiku/lore/epitaph/couplet -> couplet

    assert panel.collect() == {
        "form": "couplet",
        "theme": "a machine dreaming",
        "count": 7,
    }


def test_artgen_param_panel_ansi_builds_its_own_args_not_hardcoded():
    """A second generator with a totally different add_args shape must
    produce its own controls — proves the panel introspects per-generator,
    it does not hardcode verse's three fields."""
    panel = ArtgenParamPanel("ansi")
    panel.build()

    dests = {c.dest for c in panel._controls}
    # ansi.add_args declares: subject, width, colors, ansi_style, board_name, tagline.
    assert dests == {"subject", "width", "colors", "ansi_style", "board_name", "tagline"}
    # Different from verse's control set — the whole point of introspection.
    assert dests != {"form", "theme", "count"}


def test_artgen_param_panel_ansi_collect_returns_generator_defaults():
    panel = ArtgenParamPanel("ansi")
    panel.build()

    assert panel.collect() == {
        "subject": "a mountain at sunset",
        # --width is `type=int default=None`; its spin starts at 0 and an
        # untouched 0 collects as None (unset) so the generator's own
        # auto-default applies — NOT a literal 0 that would build a 0-column
        # canvas (whole-branch review F2).
        "width": None,
        "colors": "256",
        "ansi_style": "scene",
        "board_name": "",
        "tagline": "",
    }


def test_artgen_param_panel_boolean_flag_pairs_collapse_to_one_control():
    """landscape.add_args declares --mountains/--no-mountains (and similar
    pairs) sharing one dest each via argparse's store_true/store_false
    convention — the panel must build exactly ONE control per dest, not two,
    and must resolve the correct starting default (mountains defaults to
    True, clouds/stars/glitch default to False)."""
    panel = ArtgenParamPanel("landscape")
    panel.build()

    dests = [c.dest for c in panel._controls]
    assert dests.count("mountains") == 1
    assert dests.count("clouds") == 1
    assert dests.count("stars") == 1

    defaults = panel.collect()
    assert defaults["mountains"] is True
    assert defaults["clouds"] is False
    assert defaults["stars"] is False
    assert defaults["glitch"] is False


def test_artgen_param_panel_collect_before_build_returns_empty_dict():
    """collect() must never raise, even if called before build()."""
    panel = ArtgenParamPanel("verse")
    assert panel.collect() == {}


def test_artgen_param_panel_boolean_optional_action_renders_as_bool():
    """codeart.add_args uses the modern single-action
    `argparse.BooleanOptionalAction` spelling (`--should-compile`/
    `--no-should-compile` sharing one action, unlike landscape's classic
    store_true/store_false PAIR of actions) — this must also render as a
    switch (default True), not fall through to the str/entry branch."""
    panel = ArtgenParamPanel("codeart")
    panel.build()

    controls = {c.dest: c for c in panel._controls}
    assert controls["should_compile"].kind == "bool"
    assert panel.collect()["should_compile"] is True


def test_artgen_param_panel_unknown_generator_degrades_to_empty_panel():
    """A generator name that isn't registered (e.g. a stale medium after a
    plugin was removed) must not crash build()/collect() — it degrades to an
    empty, honestly-labeled panel."""
    panel = ArtgenParamPanel("not-a-real-generator")
    widget = panel.build()

    assert isinstance(widget, Gtk.Widget)
    assert panel.collect() == {}


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


def test_generation_worker_accepts_expected_kwargs():
    """The worker contract VideoParamPanel.collect() targets must stay stable."""
    import inspect

    import worker

    params = inspect.signature(worker.GenerationWorker.__init__).parameters
    for name in (
        "prompt",
        "negative_prompt",
        "num_inference_steps",
        "seed",
        "model",
        "num_frames",
    ):
        assert name in params, (
            f"worker.GenerationWorker.__init__ no longer accepts {name!r} — "
            "VideoParamPanel.collect() targets exactly these kwargs; the "
            "generation contract must stay intact."
        )


def test_animate_generation_worker_accepts_expected_kwargs():
    """The worker contract AnimateParamPanel.collect() targets must stay stable."""
    import inspect

    import worker

    params = inspect.signature(worker.AnimateGenerationWorker.__init__).parameters
    for name in (
        "reference_video_path",
        "reference_image_path",
        "prompt",
        "num_inference_steps",
        "seed",
        "animate_mode",
        "model",
    ):
        assert name in params, (
            f"worker.AnimateGenerationWorker.__init__ no longer accepts {name!r} — "
            "AnimateParamPanel.collect() targets exactly these kwargs; the "
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



# ── Two-pane responsive layout (Task 2, "in-place Create results":
# .superpowers/sdd/task-2-brief.md) ──────────────────────────────────────
#
# CreateView's existing form column (doors/idea row/model door/chips/scoped
# dropdown/panel host/CTA — everything built above) becomes ONE of two
# children in a responsive `Gtk.FlowBox`, alongside a fresh
# `CreateResultPanel` (Task 1, standalone until now). `min_children_per_line
# =1` / `max_children_per_line=2` makes the FlowBox lay the two panes side
# by side on a wide window and stack them (form first) on a narrow one, with
# no manual resize handling — the same wrapping mechanism already used for
# the chip row / model door sections, just with two children instead of N.
# The whole thing stays inside the existing `wrap_centered` clamp, so
# `_is_width_clamped()` keeps returning True unchanged.

def test_create_view_has_result_panel(make_create_view):
    """Step-1 brief test, verbatim (task-2-brief.md)."""
    cv = make_create_view()
    import create_view as m
    assert isinstance(cv._result_panel, m.CreateResultPanel)


def test_panes_in_wrapping_container_not_hbox(make_create_view):
    """Step-1 brief test, verbatim (task-2-brief.md): the form+result live
    in a FlowBox (wraps) — never a fixed horizontal Box."""
    cv = make_create_view()
    assert cv._panes_wrap()


def test_surface_still_width_clamped(make_create_view):
    """Step-1 brief test, verbatim (task-2-brief.md): unchanged from prior
    work — some ancestor in the built tree is still a MaxWidthBin."""
    assert make_create_view()._is_width_clamped()


def test_result_panel_starts_in_empty_state(make_create_view):
    """The panel is present but not yet wired to generation (Tasks 3-4) —
    it must show its own default "empty" state, not a pending/error state
    that would imply a generation already happened."""
    cv = make_create_view()
    assert cv._result_panel.state == "empty"


# ── Pending-queue display forwarding (SP-3c-4, task-4-brief.md) ─────────────
#
# `CreateView.refresh_queue` is MainWindow's seam for pushing the generation
# queue into the result pane's pending list — a thin forward to
# `CreateResultPanel.set_queue` so MainWindow never reaches into
# `self._create_view._result_panel` directly.

def test_refresh_queue_forwards_to_result_panel(make_create_view):
    cv = make_create_view()

    class _Item:
        def __init__(self, prompt):
            self.prompt = prompt

    items = [_Item("a castle"), _Item("a lighthouse")]
    on_cancel = lambda i: None  # noqa: E731

    cv.refresh_queue(items, on_cancel)

    assert cv._result_panel.queue_count() == 2
    assert cv._result_panel._on_queue_cancel is on_cancel


def test_panes_container_is_a_flowbox_with_two_children(make_create_view):
    """The two-pane container itself (not just `_panes_wrap()`'s boolean)
    is a real `Gtk.FlowBox` holding exactly the form column and the result
    panel — proves the reflow settings apply to the actual pair, not some
    unrelated FlowBox elsewhere in the tree."""
    cv = make_create_view()
    assert isinstance(cv._panes, Gtk.FlowBox)
    assert cv._panes.get_min_children_per_line() == 1
    assert cv._panes.get_max_children_per_line() == 2
    assert cv._panes.get_homogeneous() is False
    assert cv._panes.get_selection_mode() == Gtk.SelectionMode.NONE

    children = []
    child = cv._panes.get_first_child()
    while child is not None:
        children.append(child)
        child = child.get_next_sibling()
    assert len(children) == 2
    # One FlowBoxChild wraps the result panel; the other wraps the form
    # column (a plain Gtk.Box — not itself the result panel).
    inner = [c.get_child() for c in children]
    assert cv._result_panel in inner


def test_existing_form_widgets_still_reachable_in_two_pane_layout(make_create_view):
    """Wrapping the form column as one FlowBox child must not rebuild or
    detach its widgets — the exact same `_cta_btn`/`_chip_buttons` instances
    from before this task must still be part of the live tree, so every
    existing CTA/chip/dropdown test keeps working unmodified."""
    cv = make_create_view()
    assert isinstance(cv._cta_btn, Gtk.Button)
    assert cv._cta_btn.get_parent() is not None
    for btn in cv._chip_buttons.values():
        assert btn.get_parent() is not None


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
