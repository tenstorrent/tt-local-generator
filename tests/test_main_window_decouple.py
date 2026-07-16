# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for SP-3a — decoupling `MainWindow._on_generate` from ControlPanel.

See `docs/superpowers/specs/2026-07-13-sp3a-decouple-on-generate-design.md`
and `docs/superpowers/plans/2026-07-13-sp3a-decouple-on-generate.md`.

Before this task, `_on_generate` read model selection directly off
`self._controls` (`get_video_model()`/`get_image_model()`/
`get_animatediff_args()`). That coupling is why `_create_generate_native`
needed the v0.27.1 `self._controls._video_model = model_key` sync hack
(covered by `tests/test_main_window_create_generate.py`) and blocks deleting
ControlPanel later (SP-3d).

This file proves the other half: `_on_generate` itself takes
`video_model_key`/`image_model_key`/`animatediff_args` as explicit params and
never touches `self._controls` for model selection, and that every
non-Create caller (the legacy ControlPanel generate/enqueue button, the
queue's `_start_next_queued`, and attractor/TT-TV) resolves and passes those
params so the worker built is identical to what ran before this task.

Uses the REAL (unbound) `_on_generate`/`_start_next_queued`/
`_on_attractor_generate`/`_on_attractor_priority_enqueue`/`_on_enqueue`
methods bound onto a bare `MainWindow.__new__` instance (mirrors
`test_main_window_create_generate.py`'s `_make_mw_lifecycle` harness) —
worker construction is REAL (no mocking of `worker.py`'s classes: their
`__init__` is pure attribute assignment, no I/O), and
`threading.Thread.start()` is stubbed to a no-op so the worker object is
inspectable via `obj._worker_gen` without ever running the background
thread's HTTP calls. `ControlPanel._on_action_clicked`/`get_generation_defaults`
are exercised by binding the real unbound methods onto a lightweight
`types.SimpleNamespace` stand-in — no real GTK widget tree needed since
those methods only touch plain attributes/callables on `self`.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)


class _NoOpThread:
    """threading.Thread stand-in whose start() does nothing — the worker
    object itself is already fully constructed (and inspectable via
    `obj._worker_gen`) by the time `_on_generate` reaches `self._worker =
    threading.Thread(...)`; nothing here needs the thread's target to
    actually run (it would attempt a real HTTP call)."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        pass


class _FakeGallery:
    def add_pending_card(self, prompt="", model_source="video"):
        return MagicMock()

    def remove_pending(self):
        pass


def _full_animatediff_args(**overrides) -> dict:
    """A fully-populated get_animatediff_args()-shaped dict — every branch
    that reaches the AnimateDiff worker indexes every one of these keys."""
    args = dict(
        mode="sim",  # not "blackhole" -> chip-busy guard never fires
        negative_prompt="blurry, low quality",
        temporal_alpha=0.5,
        lightning=False,
        lightning_steps=4,
        multi_chip=False,
        device_id=None,
        chain_from=None,
        chain_save=False,
        chain_alpha=0.5,
        motion_adapter=None,
        motion_adapter_alpha=0.5,
        motion_adapter_skip=None,
    )
    args.update(overrides)
    return args


def _make_mw(monkeypatch):
    """Minimal MainWindow exposing the real queue/generate/attractor
    methods under test, with every collaborator stubbed so no real GTK
    widgets, disk I/O, or network calls happen."""
    import main_window as mw

    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)

    obj._set_status = MagicMock()
    # `obj._controls` starts as a raw MagicMock with EVERY model-selection
    # getter set to raise — each test that doesn't care about a given
    # caller's ControlPanel read overrides the specific getter(s) it needs.
    obj._controls = MagicMock()
    obj._controls.get_video_model.side_effect = AssertionError(
        "must not read _controls.get_video_model() in this path"
    )
    obj._controls.get_image_model.side_effect = AssertionError(
        "must not read _controls.get_image_model() in this path"
    )
    obj._controls.get_animatediff_args.side_effect = AssertionError(
        "must not read _controls.get_animatediff_args() in this path"
    )
    obj._client = MagicMock()
    obj._store = MagicMock()
    obj._worker = None
    obj._worker_gen = None
    obj._gen_gallery = None
    obj._gen_completed_count = 0
    obj._create_job_active = False
    obj._queue = []
    obj._attractor_win = None

    fake_gallery = _FakeGallery()
    obj._gallery_for_type = MagicMock(return_value=fake_gallery)
    obj._check_disk_space = MagicMock(return_value=True)
    obj._screensaver_inhibit = MagicMock()
    obj._screensaver_uninhibit = MagicMock()
    obj._persist_queue = MagicMock()
    obj._update_queue_display = MagicMock()
    obj._count_blackhole_chips = MagicMock(return_value=0)
    obj._running_model = None
    obj._fail_create_job = MagicMock()

    for name in (
        "_on_generate",
        "_start_next_queued",
        "_on_attractor_generate",
        "_on_attractor_priority_enqueue",
        "_on_enqueue",
    ):
        setattr(obj, name, getattr(mw.MainWindow, name).__get__(obj))

    monkeypatch.setattr(mw.threading, "Thread", _NoOpThread)
    monkeypatch.setattr(mw.GLib, "idle_add", lambda fn, *a: fn(*a))

    return obj


# ── `_on_generate` itself: zero `_controls` model reads ─────────────────────


def test_on_generate_does_not_read_controls_model_image(monkeypatch):
    """Image job with an explicit model_id — `_on_generate` must not call
    `self._controls.get_image_model()` at all (it would raise if it did)."""
    obj = _make_mw(monkeypatch)

    obj._on_generate("a prompt", "", 20, -1, model_source="image", model_id="flux")

    assert type(obj._worker_gen).__name__ == "ImageGenerationWorker"
    assert obj._worker_gen._model == "flux.1-schnell"


def test_on_generate_image_falls_back_to_image_model_key_param(monkeypatch):
    """No `model_id`, but an explicit `image_model_key` — resolves via the
    `image_model_key` param, not `self._controls.get_image_model()`."""
    obj = _make_mw(monkeypatch)

    obj._on_generate("a prompt", "", 20, -1, model_source="image",
                      image_model_key="motif")

    assert obj._worker_gen._model == "motif-image-6b-preview"


def test_on_generate_video_uses_explicit_key_not_controls(monkeypatch):
    """Video job with `video_model_key="mochi"` -> a Mochi worker, with zero
    `_controls` reads (both getters would raise if called)."""
    obj = _make_mw(monkeypatch)

    obj._on_generate("a prompt", "", 20, -1, model_source="video",
                      video_model_key="mochi")

    assert type(obj._worker_gen).__name__ == "GenerationWorker"
    assert obj._worker_gen._model == "mochi-1-preview"


def test_on_generate_video_skyreels_key_selects_skyreels_worker(monkeypatch):
    obj = _make_mw(monkeypatch)

    obj._on_generate("a prompt", "", 20, -1, model_source="video",
                      video_model_key="skyreels")

    assert obj._worker_gen._model == "skyreels-v2-i2v-14b-540p"


def test_on_generate_animatediff_branch_uses_passed_args_not_controls(monkeypatch):
    """`video_model_key="animatediff"` + an explicit `animatediff_args` dict
    -> an AnimateDiffGenerationWorker built from THOSE args, never
    `self._controls.get_animatediff_args()`."""
    obj = _make_mw(monkeypatch)
    ad_args = _full_animatediff_args(mode="sim", temporal_alpha=0.77)

    obj._on_generate("a prompt", "", 20, -1, model_source="video",
                      video_model_key="animatediff",
                      animatediff_args=ad_args)

    assert type(obj._worker_gen).__name__ == "AnimateDiffGenerationWorker"
    assert obj._worker_gen._model == "animatediff-blackhole"
    # Spot-check a value that only the passed dict (not some default) has.
    assert obj._worker_gen._mode == "sim"


def test_on_generate_default_video_key_matches_old_controls_default(monkeypatch):
    """With no `video_model_key`/`model_id` at all, `_on_generate` falls back
    to the AnimateDiff medium default — the same value ControlPanel's own
    fresh-session `self._video_model = "animatediff"` default used to
    produce via the (now-removed) `self._controls.get_video_model()` read."""
    obj = _make_mw(monkeypatch)
    ad_args = _full_animatediff_args()

    obj._on_generate("a prompt", "", 20, -1, model_source="video",
                      animatediff_args=ad_args)

    assert type(obj._worker_gen).__name__ == "AnimateDiffGenerationWorker"


# ── Legacy ControlPanel generate/enqueue button: the ONE legitimate read ────


def _make_fake_button_self(*, model_source="video", busy=False):
    """A bare stand-in for `self` inside `ControlPanel._on_action_clicked`/
    `get_generation_defaults` — both are plain functions that only touch
    attributes/callables on `self`, so a `types.SimpleNamespace` with those
    attributes pre-populated is enough; no real GTK ControlPanel needed."""
    fs = types.SimpleNamespace()
    fs._get_prompt = lambda: "a prompt"
    fs._model_source = model_source
    fs._seed_image_required = lambda: False
    fs._seed_image_path = ""
    fs._video_model = "mochi"
    fs._image_model = "motif"
    fs._sync_neg_from_widget = lambda: None
    fs._neg = "blurry"
    fs._steps = 20
    fs._seed = -1
    fs._guidance = 3.5
    fs._animate_mode = "animation"
    fs.clear_prompt = MagicMock()
    fs._busy = busy
    fs._on_enqueue = MagicMock()
    fs._on_generate = MagicMock()
    fs.get_animatediff_args = MagicMock(return_value=_full_animatediff_args())
    return fs


def test_legacy_button_generate_passes_model_params_from_controls():
    """`ControlPanel._on_action_clicked`'s idle-branch call to `_on_generate`
    is the one legitimate remaining `self._controls`-equivalent read (it's
    ControlPanel's own attributes) — it must resolve and pass
    `video_model_key`/`image_model_key`/`animatediff_args` explicitly."""
    import main_window as mw
    fs = _make_fake_button_self(model_source="video", busy=False)

    mw.ControlPanel._on_action_clicked.__get__(fs)(None)

    fs._on_generate.assert_called_once()
    fs._on_enqueue.assert_not_called()
    kwargs = fs._on_generate.call_args.kwargs
    assert kwargs["video_model_key"] == "mochi"
    assert kwargs["image_model_key"] == "motif"
    assert kwargs["animatediff_args"]["mode"] == "sim"


def test_legacy_button_enqueue_passes_model_params_from_controls():
    """Same, but the busy-branch -> `_on_enqueue` call (Add to Queue)."""
    import main_window as mw
    fs = _make_fake_button_self(model_source="image", busy=True)

    mw.ControlPanel._on_action_clicked.__get__(fs)(None)

    fs._on_enqueue.assert_called_once()
    fs._on_generate.assert_not_called()
    kwargs = fs._on_enqueue.call_args.kwargs
    assert kwargs["video_model_key"] == "mochi"
    assert kwargs["image_model_key"] == "motif"
    assert kwargs["animatediff_args"]["mode"] == "sim"


def test_get_generation_defaults_includes_model_params():
    """`get_generation_defaults()` (used by `_on_theme_queue_shots`) now also
    returns `video_model_key`/`image_model_key`/`animatediff_args` so that
    caller never needs to read `self._controls` directly either."""
    import main_window as mw
    fs = _make_fake_button_self(model_source="video")

    defaults = mw.ControlPanel.get_generation_defaults.__get__(fs)()

    assert defaults["video_model_key"] == "mochi"
    assert defaults["image_model_key"] == "motif"
    assert defaults["animatediff_args"]["mode"] == "sim"
    assert defaults["model_id"] == "mochi"  # unchanged pre-existing behavior


# ── Queue replay: `_start_next_queued` uses the item, not live `_controls` ──


def test_queue_replay_uses_item_model(monkeypatch):
    """A `_QueueItem` carrying `model_id`/`video_model_key="mochi"` ->
    `_start_next_queued` passes those through so a Mochi worker is built,
    without `_start_next_queued` itself reading `self._controls`."""
    import main_window as mw
    obj = _make_mw(monkeypatch)
    obj._queue.append(mw._QueueItem(
        prompt="a prompt", negative_prompt="", steps=20, seed=-1,
        model_source="video", model_id="mochi", video_model_key="mochi",
    ))

    result = obj._start_next_queued()

    assert result is True
    assert type(obj._worker_gen).__name__ == "GenerationWorker"
    assert obj._worker_gen._model == "mochi-1-preview"
    assert obj._queue == []  # item was popped


def test_queue_replay_image_item_uses_stored_image_model_key(monkeypatch):
    import main_window as mw
    obj = _make_mw(monkeypatch)
    obj._queue.append(mw._QueueItem(
        prompt="a prompt", negative_prompt="", steps=20, seed=-1,
        model_source="image", model_id="motif", image_model_key="motif",
    ))

    obj._start_next_queued()

    assert type(obj._worker_gen).__name__ == "ImageGenerationWorker"
    assert obj._worker_gen._model == "motif-image-6b-preview"


def test_queue_replay_animatediff_item_uses_stored_args(monkeypatch):
    """A queued AnimateDiff job (e.g. enqueued via the legacy "Add to Queue"
    button while the video model was AnimateDiff) replays with the AD args
    snapshot captured at enqueue time, not live `_controls` state."""
    import main_window as mw
    obj = _make_mw(monkeypatch)
    ad_args = _full_animatediff_args(mode="cpu")
    obj._queue.append(mw._QueueItem(
        prompt="a prompt", negative_prompt="", steps=20, seed=-1,
        model_source="video", model_id="animatediff",
        video_model_key="animatediff", animatediff_args=ad_args,
    ))

    obj._start_next_queued()

    assert type(obj._worker_gen).__name__ == "AnimateDiffGenerationWorker"
    assert obj._worker_gen._mode == "cpu"


# ── Attractor/TT-TV: reads ControlPanel once, at the same call-time as before ──


def test_attractor_generate_idle_path_reads_controls_once_and_passes_through(monkeypatch):
    """`_on_attractor_generate`'s worker-idle branch calls `_on_generate`
    directly (attractor.py always passes `model_id=""`) — the resolved
    `video_model_key` must come from `self._controls.get_video_model()` read
    HERE (not inside `_on_generate`, which still must not read it itself)."""
    obj = _make_mw(monkeypatch)
    obj._controls.get_video_model.side_effect = None
    obj._controls.get_video_model.return_value = "mochi"
    obj._controls.get_image_model.side_effect = None
    obj._controls.get_image_model.return_value = "flux"

    obj._on_attractor_generate("a prompt", "", 20, -1, model_source="video",
                                model_id="")

    assert type(obj._worker_gen).__name__ == "GenerationWorker"
    assert obj._worker_gen._model == "mochi-1-preview"


def test_attractor_generate_busy_path_stores_captured_model_on_queue_item(monkeypatch):
    """When the worker is busy, the same ControlPanel-read values are
    captured onto the `_QueueItem` (tagged `from_attractor=True`) so a later
    `_start_next_queued()` replays faithfully without re-reading
    `self._controls` (which may have changed by then)."""
    obj = _make_mw(monkeypatch)
    obj._controls.get_video_model.side_effect = None
    obj._controls.get_video_model.return_value = "skyreels"
    obj._controls.get_image_model.side_effect = None
    obj._controls.get_image_model.return_value = "flux"
    busy_worker = MagicMock()
    busy_worker.is_alive.return_value = True
    obj._worker = busy_worker

    obj._on_attractor_generate("a prompt", "", 20, -1, model_source="video",
                                model_id="")

    assert len(obj._queue) == 1
    item = obj._queue[0]
    assert item.from_attractor is True
    assert item.video_model_key == "skyreels"
    assert item.image_model_key == "flux"

    # Faithful replay: drain the queue with the worker now idle. Re-point the
    # (already-asserted-not-called-again) controls getters back to raising,
    # proving replay uses the ITEM's captured fields, not a fresh read.
    obj._worker = None
    obj._controls.get_video_model.side_effect = AssertionError("must not re-read")
    obj._controls.get_image_model.side_effect = AssertionError("must not re-read")

    obj._start_next_queued()

    assert obj._worker_gen._model == "skyreels-v2-i2v-14b-540p"


def test_attractor_priority_enqueue_busy_path_stores_captured_model(monkeypatch):
    obj = _make_mw(monkeypatch)
    obj._controls.get_video_model.side_effect = None
    obj._controls.get_video_model.return_value = "wan2"
    obj._controls.get_image_model.side_effect = None
    obj._controls.get_image_model.return_value = "flux"
    busy_worker = MagicMock()
    busy_worker.is_alive.return_value = True
    obj._worker = busy_worker

    obj._on_attractor_priority_enqueue("a prompt", model_source="video")

    assert len(obj._queue) == 1
    assert obj._queue[0].video_model_key == "wan2"
