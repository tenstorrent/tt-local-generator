# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for SP-3c-5 — giving attractor/TT-TV auto-gen a non-ControlPanel model
source, so it survives ControlPanel's deletion (SP-3d).

See `.superpowers/sdd/task-5-brief.md` and spec section 3c-5 in
`docs/superpowers/specs/2026-07-13-sp3c-migrate-into-create-design.md`.

Before this task, `_on_attractor_generate`/`_on_attractor_priority_enqueue`
read `self._controls.get_video_model()`/`get_image_model()`/
`get_animatediff_args()` (SP-3a moved that read out of `_on_generate` itself
but left it on the two attractor call sites — see
`tests/test_main_window_decouple.py`'s prior version). This file proves the
replacement: `MainWindow._resolve_attractor_model(model_source)` resolves the
model via `ModelStatusService.running_or_starting(capability)` — the SAME
"is a model on" authority CreateView's auto-select and the health dot already
use (CLAUDE.md: "Single source of truth for 'is a model on'") — and maps the
returned `server_manager` key to a video/image model key via
`_SERVER_KEY_TO_SOURCE_MODEL` (the identical map `MainWindow.__init__` uses
for last-successful-deployment pre-selection), falling back to each medium's
documented default key when nothing is running/starting.

Harness mirrors `test_main_window_decouple.py`'s `_make_mw`: construct a bare
`MainWindow.__new__`, bind only the real (unbound) methods under test, and
stub every collaborator. `self._controls` is a raw MagicMock whose
`get_video_model`/`get_image_model`/`get_animatediff_args` are wired to raise
`AssertionError` — any test that (bug!) still reads them fails loudly instead
of silently passing with a mocked return value.
"""
from __future__ import annotations

import sys
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
    """threading.Thread stand-in whose start() does nothing (mirrors
    test_main_window_decouple.py's _NoOpThread — worker objects built by
    `_on_generate` are already fully constructed by the time this would run;
    nothing here needs the real background thread to fire)."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        pass


class _FakeGallery:
    def add_pending_card(self, prompt="", model_source="video"):
        return MagicMock()

    def remove_pending(self):
        pass


def _make_mw(monkeypatch):
    """Minimal MainWindow exposing the real attractor/generate/queue methods
    under test, with `self._controls`'s model getters wired to raise and a
    fake `self._status_service` standing in for `ModelStatusService`."""
    import main_window as mw

    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)

    obj._set_status = MagicMock()
    obj._controls = MagicMock()
    obj._controls.get_video_model.side_effect = AssertionError(
        "must not read _controls.get_video_model() — SP-3c-5 uses the status service"
    )
    obj._controls.get_image_model.side_effect = AssertionError(
        "must not read _controls.get_image_model() — SP-3c-5 uses the status service"
    )
    obj._controls.get_animatediff_args.side_effect = AssertionError(
        "must not read _controls.get_animatediff_args() — SP-3c-5 uses _ANIMATEDIFF_DEFAULTS"
    )
    obj._status_service = MagicMock()
    obj._status_service.running_or_starting.return_value = None

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
        "_resolve_attractor_model",
    ):
        setattr(obj, name, getattr(mw.MainWindow, name).__get__(obj))

    monkeypatch.setattr(mw.threading, "Thread", _NoOpThread)
    monkeypatch.setattr(mw.GLib, "idle_add", lambda fn, *a: fn(*a))

    return obj


# ── `_resolve_attractor_model` — the resolver itself ────────────────────────


def test_video_resolves_to_running_server(monkeypatch):
    """A running "wan2.2" server for the "video" capability resolves to the
    "wan2" video model key (via `_SERVER_KEY_TO_SOURCE_MODEL`)."""
    import main_window as mw
    obj = _make_mw(monkeypatch)
    obj._status_service.running_or_starting.return_value = "wan2.2"

    video_key, image_key, ad_args = obj._resolve_attractor_model("video")

    obj._status_service.running_or_starting.assert_called_once_with("video")
    assert video_key == "wan2"
    assert image_key == mw._DEFAULT_IMAGE_KEY  # unaffected — not the image branch
    assert ad_args is None


def test_video_resolves_to_skyreels(monkeypatch):
    import main_window as mw
    obj = _make_mw(monkeypatch)
    obj._status_service.running_or_starting.return_value = "skyreels"

    video_key, _image_key, ad_args = obj._resolve_attractor_model("video")

    assert video_key == "skyreels"
    assert ad_args is None


def test_image_resolves_to_running_server(monkeypatch):
    """model_source="image" never touches the video branch's server lookup —
    `video_model_key` stays at its module default ("animatediff"), which is
    harmless because `_on_generate`'s image branch never reads it. Since that
    default IS "animatediff", `animatediff_args` is still populated (mirrors
    the pre-SP-3c-5 behavior: the old code read ControlPanel's LIVE
    `_video_model` unconditionally too, regardless of `model_source`)."""
    import main_window as mw
    obj = _make_mw(monkeypatch)
    obj._status_service.running_or_starting.return_value = "motif"

    video_key, image_key, ad_args = obj._resolve_attractor_model("image")

    obj._status_service.running_or_starting.assert_called_once_with("image")
    assert image_key == "motif"
    assert video_key == mw._DEFAULT_VIDEO_KEY  # unaffected — not the video branch
    assert ad_args == mw._ANIMATEDIFF_DEFAULTS  # populated but irrelevant/unread for image jobs


def test_image_resolves_to_sdxl(monkeypatch):
    obj = _make_mw(monkeypatch)
    obj._status_service.running_or_starting.return_value = "sdxl"

    _video_key, image_key, _ad_args = obj._resolve_attractor_model("image")

    assert image_key == "sdxl"


def test_animate_queries_animate_capability(monkeypatch):
    """model_source="animate" queries the "animate" capability; the animate
    branch of `_on_generate` reads neither video_model_key, image_model_key,
    nor animatediff_args, so all three staying at their (harmless, unread)
    defaults is correct — see `test_image_resolves_to_running_server` for
    why `ad_args` is non-None here."""
    import main_window as mw
    obj = _make_mw(monkeypatch)
    obj._status_service.running_or_starting.return_value = "animate"

    video_key, image_key, ad_args = obj._resolve_attractor_model("animate")

    obj._status_service.running_or_starting.assert_called_once_with("animate")
    assert video_key == mw._DEFAULT_VIDEO_KEY
    assert image_key == mw._DEFAULT_IMAGE_KEY
    assert ad_args == mw._ANIMATEDIFF_DEFAULTS


def test_nothing_running_falls_back_to_medium_defaults(monkeypatch):
    """`running_or_starting` returning None (nothing running/starting for the
    capability) falls back to the medium's documented default key — the same
    fallback `_on_generate` itself uses when given no model at all."""
    import main_window as mw
    obj = _make_mw(monkeypatch)
    obj._status_service.running_or_starting.return_value = None

    video_key, image_key, ad_args = obj._resolve_attractor_model("video")

    assert video_key == mw._DEFAULT_VIDEO_KEY  # "animatediff"
    assert image_key == mw._DEFAULT_IMAGE_KEY
    # Falling back to the animatediff default must still produce a COMPLETE
    # animatediff_args dict (never None when video_model_key=="animatediff").
    assert ad_args is not None
    assert ad_args["mode"] == mw._ANIMATEDIFF_DEFAULTS["mode"]


def test_unrecognized_server_key_falls_back_to_defaults(monkeypatch):
    """A key `_SERVER_KEY_TO_SOURCE_MODEL` doesn't recognize (defensive —
    shouldn't happen since the map mirrors `server_manager.SERVERS`) must not
    raise; falls back exactly like "nothing running"."""
    import main_window as mw
    obj = _make_mw(monkeypatch)
    obj._status_service.running_or_starting.return_value = "some-unknown-key"

    video_key, image_key, _ad_args = obj._resolve_attractor_model("video")

    assert video_key == mw._DEFAULT_VIDEO_KEY
    assert image_key == mw._DEFAULT_IMAGE_KEY


def test_animatediff_default_produces_complete_args_dict(monkeypatch):
    """Whenever the resolved video_model_key is "animatediff" (default OR an
    explicit resolution), the returned `animatediff_args` must be a COMPLETE
    dict — every key `_on_generate`'s `ad["..."]` indexing touches — sourced
    from `_ANIMATEDIFF_DEFAULTS`, never `self._controls.get_animatediff_args()`
    (which raises in this harness if called)."""
    import main_window as mw
    obj = _make_mw(monkeypatch)
    obj._status_service.running_or_starting.return_value = None  # -> default "animatediff"

    _video_key, _image_key, ad_args = obj._resolve_attractor_model("video")

    assert ad_args == mw._ANIMATEDIFF_DEFAULTS
    # It's a fresh copy — mutating it must never corrupt the module-level default.
    ad_args["mode"] = "mutated"
    assert mw._ANIMATEDIFF_DEFAULTS["mode"] != "mutated"


# ── Zero `self._controls.get_*` reads anywhere in the attractor path ───────


def test_on_attractor_generate_never_reads_controls(monkeypatch):
    """End-to-end: `_on_attractor_generate`'s idle path resolves entirely via
    the status service; `self._controls.get_video_model`/`get_image_model`/
    `get_animatediff_args` (each wired to raise in `_make_mw`) are never
    invoked."""
    obj = _make_mw(monkeypatch)
    obj._status_service.running_or_starting.return_value = "flux"

    obj._on_attractor_generate("a prompt", "", 20, -1, model_source="image",
                                model_id="")

    assert type(obj._worker_gen).__name__ == "ImageGenerationWorker"
    assert obj._worker_gen._model == "flux.1-schnell"
    obj._controls.get_video_model.assert_not_called()
    obj._controls.get_image_model.assert_not_called()
    obj._controls.get_animatediff_args.assert_not_called()


def test_on_attractor_priority_enqueue_never_reads_controls(monkeypatch):
    """Same, for `_on_attractor_priority_enqueue`'s busy (queued) path."""
    obj = _make_mw(monkeypatch)
    obj._status_service.running_or_starting.return_value = "mochi"
    busy_worker = MagicMock()
    busy_worker.is_alive.return_value = True
    obj._worker = busy_worker

    obj._on_attractor_priority_enqueue("a prompt", model_source="video")

    assert len(obj._queue) == 1
    assert obj._queue[0].video_model_key == "mochi"
    obj._controls.get_video_model.assert_not_called()
    obj._controls.get_image_model.assert_not_called()
    obj._controls.get_animatediff_args.assert_not_called()


def test_attractor_generate_animatediff_worker_built_from_module_defaults(monkeypatch):
    """A full end-to-end AnimateDiff run: nothing running -> default
    "animatediff" video key -> `_on_generate` builds an
    AnimateDiffGenerationWorker whose fields match `_ANIMATEDIFF_DEFAULTS`
    (mode="blackhole" would trip the chip-busy guard if `_server_ready`/chip
    count were wired that way — `_controls._server_ready` is a MagicMock
    truthy value and `_count_blackhole_chips` returns 0 in this harness, so
    the guard's `== 1` check never fires)."""
    import main_window as mw
    obj = _make_mw(monkeypatch)
    obj._status_service.running_or_starting.return_value = None

    obj._on_attractor_generate("a prompt", "", 20, -1, model_source="video",
                                model_id="")

    assert type(obj._worker_gen).__name__ == "AnimateDiffGenerationWorker"
    assert obj._worker_gen._mode == mw._ANIMATEDIFF_DEFAULTS["mode"]
    assert obj._worker_gen._temporal_alpha == mw._ANIMATEDIFF_DEFAULTS["temporal_alpha"]


# ── Launch affordance: `_attractor_btn` is a MainWindow toolbar button ─────


def test_attractor_btn_is_a_main_toolbar_button_not_controlpanel():
    """SP-3c-5 asks to VERIFY (not migrate — it's already true) that the
    TT-TV launch button lives on MainWindow's own toolbar and is wired
    directly to `_on_open_attractor`, independent of ControlPanel's
    `_endless_btn` (an animatediff-only secondary launch that dies with
    ControlPanel in SP-3d — out of scope here).

    SP-3d-4: "MainWindow's own toolbar" is now the loop-nav row itself --
    ControlPanel's `toolbar_box` (the composite this button used to be
    appended onto) is no longer mounted at all.

    SP-1 (four-verb loop nav) relabelled the button "📺 Watch" (was "📺 Watch
    TT-TV") and moved its construction+append into `_build_loop_nav` itself.
    RN-1 (Unified-Stage two-place nav) relabels it again to "▶ Play" (now a
    companion beside 🗂 Library rather than interleaved between Discover and
    Remix, which are gone) -- same end result (the button lands in the row
    `_build_ui` mounts as the toolbar), different call site/label."""
    src = (Path(__file__).parent.parent / "app" / "main_window.py").read_text()
    assert 'self._attractor_btn = Gtk.Button(label="▶ Play")' in src
    assert 'self._attractor_btn.connect("clicked", self._on_open_attractor)' in src
    assert "row.append(self._attractor_btn)" in src
    assert "main_toolbar.append(self._attractor_btn)" not in src


def test_open_attractor_passes_application_at_construction(monkeypatch):
    """The attractor window must receive the Gtk.Application at CONSTRUCTION
    (so its Wayland app_id / KDE icon is fixed before realize and every launch
    is identical), not set post-construction where a warm/second launch could
    realize first and end up with the fallback icon + an unclosable window."""
    import main_window as mw
    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)

    obj._attractor_win = None
    _g = MagicMock()
    obj._video_gallery = _g
    obj._animate_gallery = _g
    obj._image_gallery = _g
    obj._store = MagicMock()
    obj._store.all_records.return_value = []
    obj._store.artgen_records.return_value = []
    obj._current_medium_source = MagicMock(return_value="video")
    obj._active_medium_is_animatediff = MagicMock(return_value=False)
    obj._prompt_gen_system_prompt = "sys"
    obj._attractor_server_status = MagicMock(return_value=(True, None))
    obj._get_animate_inputs = MagicMock()
    obj._queue = []
    obj._worker = None
    obj._worker_gen = None
    obj._set_crumbs = MagicMock()
    obj._nav_open_context = MagicMock()
    fake_app = MagicMock()
    obj.get_application = MagicMock(return_value=fake_app)

    captured = {}

    def _fake_attractor_window(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(mw.attractor, "AttractorWindow", _fake_attractor_window)
    monkeypatch.setattr(mw.GLib, "idle_add", lambda fn, *a: None)

    mw.MainWindow._on_open_attractor.__get__(obj)()

    assert captured.get("application") is fake_app
