# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for SP-3d-3 — rehoming every SURVIVING `self._controls.*` hook off
ControlPanel, the prerequisite for deleting it outright in 3d-5.

See `.superpowers/sdd/sp3d-audit.md` §1 for the per-member classification this
task follows, and `.superpowers/sdd/task-3-brief.md` for the task itself.

ControlPanel is NOT deleted by this task — it's still constructed, still the
generation control surface for the legacy tabs. What changes is that every
SURVIVING caller (i.e. NOT one of the audit's LEGACY-ONLY rows, which retire
alongside `_health_loop`/`_artgen_health_loop`/ControlPanel itself in later
sub-stages) now asks `CreateView._active_medium` — via new MainWindow helpers
`_current_medium_source`/`_active_medium_is_animatediff`/
`_current_medium_model_key`/`_running_generation_server`/
`_display_label_for_server_key` — instead of `self._controls.get_model_source()`/
`get_video_model()`/`get_image_model()`/`_server_ready`/`_running_model`.

Harness mirrors the established `__new__` + unbound-method-binding style
(tests/test_main_window_loop_nav.py, tests/test_main_window_decouple.py,
tests/test_main_window_attractor_model_source.py) — MainWindow is heavy to
construct for real (network/disk/GTK-tree side effects), so these tests build
a bare `MainWindow.__new__` instance, bind only the real (unbound) methods
under test, and stub every collaborator they touch.
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

from create_mediums import Medium

_SRC = (Path(__file__).parent.parent / "app" / "main_window.py").read_text()


class _NoOpThread:
    """threading.Thread stand-in whose start() does nothing — the resolution
    logic under test (script/model-key lookup, note_starting/note_stopping)
    all runs before the thread is created in both `_on_start_server` and
    `_on_stop_server`, so the background subprocess body never needs to run
    for these tests."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        pass


class _FakeCreateView:
    """Stand-in exposing only what these helpers read: `_active_medium` and
    `_selected_model_key()` (CreateView's scoped-dropdown accessor)."""

    def __init__(self, active_medium=None, selected_model_key=None,
                 raise_on_select=False):
        self._active_medium = active_medium
        self._selected_model_key_value = selected_model_key
        self._raise_on_select = raise_on_select

    def _selected_model_key(self):
        if self._raise_on_select:
            raise RuntimeError("boom")
        return self._selected_model_key_value


def _make_mw(monkeypatch, create_view=None):
    """Minimal MainWindow exposing the real medium/source-resolution helpers
    plus `_on_start_server`/`_on_stop_server`/`_active_gallery`."""
    import main_window as mw

    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)

    obj._create_view = create_view
    obj._status_service = MagicMock()
    obj._status_service.ready_keys.return_value = []
    obj._servers_control = MagicMock()
    obj._set_status = MagicMock()
    obj._hw_statusbar = MagicMock()
    obj.lookup_action = MagicMock(return_value=None)
    obj._log_tail_stop = None
    obj._controls = MagicMock()
    obj._controls.get_model_source.side_effect = AssertionError(
        "must not read _controls.get_model_source() — SP-3d-3 uses CreateView"
    )
    obj._controls.get_video_model.side_effect = AssertionError(
        "must not read _controls.get_video_model() — SP-3d-3 uses CreateView"
    )
    obj._controls.get_image_model.side_effect = AssertionError(
        "must not read _controls.get_image_model() — SP-3d-3 uses CreateView"
    )
    obj._video_gallery = MagicMock(name="video_gallery")
    obj._image_gallery = MagicMock(name="image_gallery")
    obj._animate_gallery = MagicMock(name="animate_gallery")

    for name in (
        "_current_medium_source",
        "_active_medium_is_animatediff",
        "_current_medium_model_key",
        "_running_generation_server",
        "_display_label_for_server_key",
        "_active_gallery",
        "_gallery_for_type",
        "_on_start_server",
        "_on_stop_server",
    ):
        setattr(obj, name, getattr(mw.MainWindow, name).__get__(obj))

    monkeypatch.setattr(mw.threading, "Thread", _NoOpThread)

    return obj


# ── `_current_medium_source` — the medium -> legacy-vocabulary translation ──


def test_no_create_view_falls_back_to_video(monkeypatch):
    obj = _make_mw(monkeypatch, create_view=None)
    assert obj._current_medium_source() == "video"


def test_no_active_medium_falls_back_to_video(monkeypatch):
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(active_medium=None))
    assert obj._current_medium_source() == "video"


@pytest.mark.parametrize("native_id", ["video", "image", "animate"])
def test_native_medium_maps_to_its_own_id(monkeypatch, native_id):
    medium = Medium(id=native_id, label=native_id.title(), icon="x",
                     kind=native_id, source="native")
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(active_medium=medium))
    assert obj._current_medium_source() == native_id


@pytest.mark.parametrize("generator", ["verse", "ansi", "landscape"])
def test_artgen_medium_folds_to_artgen_regardless_of_generator_name(monkeypatch, generator):
    medium = Medium(id=generator, label=generator.title(), icon="x", kind="image",
                     source="artgen", generator=generator)
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(active_medium=medium))
    assert obj._current_medium_source() == "artgen"


def test_animatediff_generator_also_folds_to_artgen_via_generic_helper(monkeypatch):
    """The generic helper deliberately does NOT special-case AnimateDiff —
    that distinction belongs to `_active_medium_is_animatediff()` alone (see
    `_on_open_attractor`'s override, which layers that check ON TOP of this
    generic fold)."""
    medium = Medium(id="animatediff", label="AnimateDiff", icon="🕺", kind="gif",
                     source="artgen", generator="animatediff")
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(active_medium=medium))
    assert obj._current_medium_source() == "artgen"


# ── `_active_medium_is_animatediff` ──────────────────────────────────────────


def test_animatediff_medium_is_detected(monkeypatch):
    medium = Medium(id="animatediff", label="AnimateDiff", icon="🕺", kind="gif",
                     source="artgen", generator="animatediff")
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(active_medium=medium))
    assert obj._active_medium_is_animatediff() is True


def test_other_artgen_medium_is_not_animatediff(monkeypatch):
    medium = Medium(id="verse", label="Verse", icon="x", kind="text", source="artgen",
                     generator="verse")
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(active_medium=medium))
    assert obj._active_medium_is_animatediff() is False


def test_native_video_medium_is_not_animatediff(monkeypatch):
    medium = Medium(id="video", label="Video", icon="x", kind="video", source="native")
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(active_medium=medium))
    assert obj._active_medium_is_animatediff() is False


def test_no_medium_is_not_animatediff(monkeypatch):
    obj = _make_mw(monkeypatch, create_view=None)
    assert obj._active_medium_is_animatediff() is False


# ── `_current_medium_model_key` ──────────────────────────────────────────────


def test_video_server_key_translated_via_alias(monkeypatch):
    """server_manager's "wan2.2" key becomes the short "wan2" key
    `_SERVER_SCRIPTS` is keyed by, via `_SERVER_KEY_TO_SOURCE_MODEL` — the
    same map `_resolve_attractor_model`/startup pre-select already use."""
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(selected_model_key="wan2.2"))
    assert obj._current_medium_model_key("video") == "wan2"


def test_video_server_key_skyreels_passes_through(monkeypatch):
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(selected_model_key="skyreels"))
    assert obj._current_medium_model_key("video") == "skyreels"


def test_image_server_key_passes_through(monkeypatch):
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(selected_model_key="motif"))
    assert obj._current_medium_model_key("image") == "motif"


def test_no_selection_falls_back_to_medium_default(monkeypatch):
    import main_window as mw
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(selected_model_key=None))
    assert obj._current_medium_model_key("video") == mw._DEFAULT_VIDEO_KEY
    assert obj._current_medium_model_key("image") == mw._DEFAULT_IMAGE_KEY


def test_no_create_view_falls_back_to_medium_default(monkeypatch):
    import main_window as mw
    obj = _make_mw(monkeypatch, create_view=None)
    assert obj._current_medium_model_key("video") == mw._DEFAULT_VIDEO_KEY
    assert obj._current_medium_model_key("image") == mw._DEFAULT_IMAGE_KEY


def test_wrong_source_key_falls_back_to_default(monkeypatch):
    """The selected key resolves to "image" (e.g. "flux"), but the caller
    asks for "video" — must not hand back the image model's key."""
    import main_window as mw
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(selected_model_key="flux"))
    assert obj._current_medium_model_key("video") == mw._DEFAULT_VIDEO_KEY


def test_selected_model_key_raising_falls_back_to_default(monkeypatch):
    """Defensive: a CreateView whose `_selected_model_key()` raises must not
    crash `_on_start_server`/`_on_stop_server` — falls back like "no selection"."""
    import main_window as mw
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(raise_on_select=True))
    assert obj._current_medium_model_key("video") == mw._DEFAULT_VIDEO_KEY


def test_animate_source_returns_empty_string(monkeypatch):
    """Mirrors the original `_on_start_server`/`_on_stop_server` else-branch:
    "animate" (and anything else) has no video/image model key at all."""
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(selected_model_key="wan2.2"))
    assert obj._current_medium_model_key("animate") == ""


# ── `_running_generation_server` / `_display_label_for_server_key` ─────────


def test_running_generation_server_prefers_video_capability(monkeypatch):
    obj = _make_mw(monkeypatch)
    obj._status_service.ready_keys = MagicMock(
        side_effect=lambda cap: ["wan2.2"] if cap == "video" else ["motif"]
    )
    assert obj._running_generation_server() == (True, "wan2.2")


def test_running_generation_server_falls_back_to_image_capability(monkeypatch):
    obj = _make_mw(monkeypatch)
    obj._status_service.ready_keys = MagicMock(
        side_effect=lambda cap: [] if cap == "video" else ["flux"]
    )
    assert obj._running_generation_server() == (True, "flux")


def test_running_generation_server_nothing_ready(monkeypatch):
    obj = _make_mw(monkeypatch)
    obj._status_service.ready_keys.return_value = []
    assert obj._running_generation_server() == (False, None)


def test_display_label_for_known_server_key(monkeypatch):
    import server_manager
    obj = _make_mw(monkeypatch)
    assert obj._display_label_for_server_key("wan2.2") == server_manager.SERVERS["wan2.2"].label


def test_display_label_for_unknown_server_key_returns_key_itself(monkeypatch):
    obj = _make_mw(monkeypatch)
    assert obj._display_label_for_server_key("some-unknown-key") == "some-unknown-key"


def test_display_label_for_falsy_key_returns_none(monkeypatch):
    obj = _make_mw(monkeypatch)
    assert obj._display_label_for_server_key(None) is None
    assert obj._display_label_for_server_key("") is None


# ── `_active_gallery` — no longer reads `_controls.get_model_source()` ─────


def test_active_gallery_reads_create_view_not_controls(monkeypatch):
    medium = Medium(id="image", label="Image", icon="x", kind="image", source="native")
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(active_medium=medium))
    assert obj._active_gallery() is obj._image_gallery
    obj._controls.get_model_source.assert_not_called()


def test_active_gallery_defaults_to_video_gallery_with_no_create_view(monkeypatch):
    obj = _make_mw(monkeypatch, create_view=None)
    assert obj._active_gallery() is obj._video_gallery
    obj._controls.get_model_source.assert_not_called()


# ── `_on_start_server`/`_on_stop_server` — resolve via CreateView, never `_controls` ──


def test_on_start_server_resolves_video_model_via_create_view(monkeypatch):
    """A video medium with server key "wan2.2" selected must launch the
    Wan2.2 script and note_starting("wan2.2") -- entirely via CreateView,
    with `self._controls.get_model_source`/`get_video_model` never touched
    (both wired to raise in `_make_mw`)."""
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(selected_model_key="wan2.2"))

    obj._on_start_server("video")

    obj._status_service.note_starting.assert_called_once_with("wan2.2")
    obj._servers_control.append_server_log.assert_called_once()
    log_msg = obj._servers_control.append_server_log.call_args[0][0]
    assert "start_wan_qb2.sh" in log_msg


def test_on_start_server_resolves_image_model_via_create_view(monkeypatch):
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(selected_model_key="motif"))

    obj._on_start_server("image")

    obj._status_service.note_starting.assert_called_once_with("motif")
    log_msg = obj._servers_control.append_server_log.call_args[0][0]
    assert "start_motif.sh" in log_msg


def test_on_start_server_no_selection_falls_back_to_animatediff_default_video(monkeypatch):
    """No model selected yet (fresh CreateView) -> falls back to
    `_DEFAULT_VIDEO_KEY` ("animatediff"), which has no `_SERVER_SCRIPTS` entry
    for ("video", "animatediff") -> the dict's own fallback script/label."""
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(selected_model_key=None))

    obj._on_start_server("video")

    # No _SERVER_SCRIPTS[("video", "animatediff")] entry -> falls back to the
    # dict's documented default ("start_wan.sh", "Wan2.2 video") — never raises.
    log_msg = obj._servers_control.append_server_log.call_args[0][0]
    assert "start_wan.sh" in log_msg


def test_on_stop_server_resolves_via_create_view(monkeypatch):
    medium = Medium(id="video", label="Video", icon="x", kind="video", source="native")
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(
        active_medium=medium, selected_model_key="skyreels",
    ))

    obj._on_stop_server()

    obj._status_service.note_stopping.assert_called_once_with("skyreels")


def test_on_stop_server_image_medium_resolves_via_create_view(monkeypatch):
    medium = Medium(id="image", label="Image", icon="x", kind="image", source="native")
    obj = _make_mw(monkeypatch, create_view=_FakeCreateView(
        active_medium=medium, selected_model_key="sdxl",
    ))

    obj._on_stop_server()

    obj._status_service.note_stopping.assert_called_once_with("sdxl")


# ── Source-level regression guards ──────────────────────────────────────────
#
# `_on_open_attractor`/`_update_attractor_btn` are expensive to exercise
# end-to-end (real `attractor.AttractorWindow` construction) — mirrors the
# established house style (tests/test_main_window_status_service.py) of
# asserting directly against the method's source body for these two.


def test_hw_statusbar_start_cb_uses_current_medium_source():
    assert 'start_cb=lambda: self._on_start_server(self._current_medium_source())' in _SRC
    assert 'start_cb=lambda: self._on_start_server(self._controls.get_model_source())' not in _SRC


def test_on_open_attractor_never_reads_controls_for_source_or_status():
    start = _SRC.index("    def _on_open_attractor(\n")
    end = _SRC.index("\n    def _on_attractor_closed")
    body = _SRC[start:end]
    assert "self._controls.get_model_source()" not in body
    assert "self._controls.get_video_model()" not in body
    assert "self._controls._server_ready" not in body
    assert "self._controls._running_model" not in body
    assert "self._current_medium_source()" in body
    assert "self._active_medium_is_animatediff()" in body
    assert "self._running_generation_server()" in body


def test_update_attractor_btn_never_reads_controls():
    start = _SRC.index("    def _update_attractor_btn(self)")
    end = _SRC.index("\n    def ", start + 1)
    body = _SRC[start:end]
    assert "self._controls.get_model_source()" not in body
    assert "self._controls.get_video_model()" not in body
    assert "self._active_medium_is_animatediff()" in body


def test_on_generate_animatediff_guard_never_reads_controls_server_ready():
    start = _SRC.index("    def _on_generate(self, prompt")
    end = _SRC.index("\n    def ", start + 1)
    body = _SRC[start:end]
    assert "self._controls._server_ready" not in body
    assert "self._running_model" not in body  # the pre-existing undefined-attribute bug
    assert "self._running_generation_server()" in body


def test_set_busy_calls_are_gone():
    """Audit-confirmed dead weight (§1): `ControlPanel.set_busy` only ever
    drove ControlPanel's own Generate/Cancel buttons — nothing surviving
    reads `_controls._busy`. Only the class's own definition may remain."""
    calls = _SRC.count("self._controls.set_busy(")
    assert calls == 0


def test_on_loop_nav_discover_and_hide_pipelines_use_current_medium_source():
    for method_name in ("_on_loop_nav_discover", "_hide_pipelines"):
        start = _SRC.index(f"    def {method_name}(self)")
        end = _SRC.index("\n    def ", start + 1)
        body = _SRC[start:end]
        assert "self._controls.get_model_source()" not in body, method_name
        assert "self._current_medium_source()" in body, method_name
