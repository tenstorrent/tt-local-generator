# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for `MainWindow._on_create_generate` — CreateView's `on_create` seam
wired to REAL generation (Create-surface plan, Task 8 switchover subset; see
`.superpowers/sdd/task-8-report.md`, which overrides the plan document's
"remove the old tabs" Task 8 wording — this task only wires generation +
flips the Create loop-nav verb, deferring old-tab removal to a later task).

Mirrors the existing `__new__` + unbound-method-binding harness style already
established in tests/test_main_window_loop_nav.py / test_main_window_pipelines.py:
construct a minimal MainWindow via `__new__` (skipping the heavy
`Gtk.ApplicationWindow.__init__`/GTK widget tree), bind only the real
(unbound) methods under test, and hand-populate the handful of collaborators
they touch (`_on_generate` is a MagicMock spy for the native-medium tests —
this task's job is dispatch/translation, NOT re-testing `_on_generate`
itself, which already has its own coverage elsewhere).

Artgen-route tests monkeypatch `pipeline_engine._run_tt_ctl` (the same
subprocess helper `pipeline_engine._h_artgen_generate` already uses for
pipeline nodes) and `media_store.media_store` so no real `tt-ctl` subprocess
or sqlite write happens; `threading.Thread`/`GLib.idle_add` are patched to
run synchronously so the background-thread artgen path is fully exercised
inline, per the same pattern `tests/test_create_view.py`'s `_ImmediateThread`
already uses for CreateView's own off-thread health refresh.
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


class _ImmediateThread:
    """threading.Thread stand-in that runs its target synchronously on start()."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


class _FakeControls:
    """Minimal stand-in reproducing the legacy `ControlPanel`'s REAL
    `_set_model` source-gated branching (main_window.py) — the exact behavior
    a MagicMock hides and that FIX 1's re-review side-effect hinged on.

    The key faithful detail: `_set_model` is NOT a no-op when
    `_model_source != "video"` — with `_model_source == "image"` it clobbers
    `_image_model` with whatever key it's handed. So a video-model key routed
    through `_set_model` while the (permanently-mounted, still-reachable)
    control is in image mode would corrupt the legacy Image tab's model
    selection. These tests use this stand-in so re-introducing that
    `_set_model` call would fail loudly.
    """

    def __init__(self, model_source: str) -> None:
        self._model_source = model_source
        self._image_model = "flux"        # a valid image key (the default)
        self._video_model = "animatediff"  # the fresh-session default that started the bug

    def _set_model(self, model: str) -> None:
        if self._model_source == "video":
            self._video_model = model
        elif self._model_source == "image":
            self._image_model = model  # <-- the clobber FIX 1's re-review caught

    def get_video_model(self) -> str:
        return self._video_model

    def get_image_model(self) -> str:
        return self._image_model


def _make_mw(monkeypatch):
    """Minimal MainWindow exposing only what `_on_create_generate` touches."""
    import main_window as mw

    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)

    obj._on_generate = MagicMock()
    obj._set_status = MagicMock()
    obj._artgen_panel = None  # absent by default; artgen-done test overrides
    # `_create_generate_native`'s video branch syncs the OLD ControlPanel's
    # model selection before calling `_on_generate` (FIX 1). A MagicMock is
    # enough for the image/animate branches (which never touch it) and lets
    # the video tests assert the sync happened.
    obj._controls = MagicMock()

    for name in (
        "_on_create_generate",
        "_create_generate_native",
        "_create_generate_artgen",
        "_on_create_artgen_done",
        "_on_create_artgen_error",
    ):
        setattr(obj, name, getattr(mw.MainWindow, name).__get__(obj))

    monkeypatch.setattr(mw.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(mw.GLib, "idle_add", lambda fn, *a: fn(*a))

    return obj


# ── Native mediums: image / video / animate ─────────────────────────────────

_IMAGE_MEDIUM = Medium(id="image", label="Image", icon="\U0001f5bc", kind="image",
                        source="native", generator=None)
_VIDEO_MEDIUM = Medium(id="video", label="Video", icon="\U0001f3a5", kind="video",
                       source="native", generator=None)
_ANIMATE_MEDIUM = Medium(id="animate", label="Animate", icon="\U0001f483", kind="gif",
                         source="native", generator=None)


def test_image_medium_routes_to_on_generate_with_model_source_image(monkeypatch):
    obj = _make_mw(monkeypatch)
    params = {
        "prompt": "a lighthouse at dawn",
        "negative_prompt": "blurry",
        "num_inference_steps": 30,
        "seed": 42,
        "guidance_scale": 4.2,
        "model": "motif-image-6b-preview",
    }

    obj._on_create_generate(_IMAGE_MEDIUM, params)

    obj._on_generate.assert_called_once()
    args, kwargs = obj._on_generate.call_args
    assert args[0] == "a lighthouse at dawn"
    assert args[1] == "blurry"
    assert args[2] == 30
    assert args[3] == 42
    assert kwargs["model_source"] == "image"
    assert kwargs["guidance_scale"] == 4.2
    # canonical "motif-image-6b-preview" -> short key "motif" (main_window's
    # inverse of _IMAGE_MODEL_IDS) — _on_generate looks model_id up as a
    # SHORT key, not the canonical id.
    assert kwargs["model_id"] == "motif"


def test_image_medium_unknown_model_falls_back_to_flux_key(monkeypatch):
    obj = _make_mw(monkeypatch)
    params = {"model": "some-future-model-id"}

    obj._on_create_generate(_IMAGE_MEDIUM, params)

    _args, kwargs = obj._on_generate.call_args
    assert kwargs["model_id"] == "flux"


def test_video_medium_routes_to_on_generate_with_model_source_video(monkeypatch):
    obj = _make_mw(monkeypatch)
    params = {
        "prompt": "a train through the mountains",
        "negative_prompt": "static",
        "num_inference_steps": 25,
        "seed": 7,
        "model": "mochi-1-preview",
        "num_frames": 65,
    }

    obj._on_create_generate(_VIDEO_MEDIUM, params)

    obj._on_generate.assert_called_once()
    args, kwargs = obj._on_generate.call_args
    assert args[0] == "a train through the mountains"
    assert args[1] == "static"
    assert args[2] == 25
    assert args[3] == 7
    assert kwargs["model_source"] == "video"
    assert kwargs["model_id"] == "mochi"


def test_video_medium_syncs_video_model_before_generate(monkeypatch):
    """FIX 1 (silently-wrong-worker bug): `_on_generate`'s video branch picks
    the worker from `self._controls.get_video_model()`, NOT `model_id`. That
    field defaults to "animatediff" on a fresh session — so CreateView MUST
    set the control's video model to the chosen model BEFORE `_on_generate`,
    or Wan2.2/Mochi silently run AnimateDiff. Uses the REAL-branching stand-in
    (source already "video") and asserts the value was in place before the
    generation call fired."""
    obj = _make_mw(monkeypatch)
    obj._controls = _FakeControls("video")

    seen_before_generate = {}
    obj._on_generate.side_effect = (
        lambda *a, **k: seen_before_generate.update(video_model=obj._controls.get_video_model())
    )

    obj._on_create_generate(_VIDEO_MEDIUM, {"model": "wan2.2-t2v"})

    # canonical "wan2.2-t2v" -> short key "wan2", in place before _on_generate.
    assert seen_before_generate == {"video_model": "wan2"}
    assert obj._controls.get_video_model() == "wan2"
    obj._on_generate.assert_called_once()


def test_video_medium_does_not_clobber_image_model_in_image_source(monkeypatch):
    """FIX 1 re-review (side-effect bug): syncing the video model must NOT
    corrupt the legacy Image tab. With the control in `_model_source ==
    "image"` (reachable with zero clicks when `last_successful_deployment` was
    an image model), routing the video key through `_set_model` would take its
    image branch and clobber `_image_model` with "wan2" — so a later click on
    the still-mounted Image tab would silently fall back to FLUX / launch the
    Wan2.2 script. The fix sets `_video_model` DIRECTLY (never `_set_model`),
    so `get_video_model()` becomes "wan2" while `_image_model` is untouched.

    This FAILS against the pre-fix 37eaaa9 code (which called `_set_model`)
    and passes after."""
    obj = _make_mw(monkeypatch)
    obj._controls = _FakeControls("image")  # zero-click-reachable startup state

    obj._on_create_generate(_VIDEO_MEDIUM, {"model": "wan2.2-t2v"})

    # (a) the video worker selection is correct...
    assert obj._controls.get_video_model() == "wan2"
    # (b) ...and the legacy image-model selection is UNTOUCHED (not "wan2").
    assert obj._controls.get_image_model() == "flux"
    obj._on_generate.assert_called_once()
    assert obj._on_generate.call_args.kwargs["model_source"] == "video"


def test_animate_medium_routes_to_on_generate_with_ref_paths_and_mode(monkeypatch):
    obj = _make_mw(monkeypatch)
    params = {
        "prompt": "dance like the reference",
        "reference_video_path": "/tmp/motion.mp4",
        "reference_image_path": "/tmp/character.png",
        "num_inference_steps": 18,
        "seed": -1,
        "animate_mode": "replacement",
        "model": "wan2.2-animate-14b",
    }

    obj._on_create_generate(_ANIMATE_MEDIUM, params)

    obj._on_generate.assert_called_once()
    args, kwargs = obj._on_generate.call_args
    assert args[0] == "dance like the reference"
    assert kwargs["model_source"] == "animate"
    assert kwargs["ref_video_path"] == "/tmp/motion.mp4"
    assert kwargs["ref_char_path"] == "/tmp/character.png"
    assert kwargs["animate_mode"] == "replacement"


# ── Artgen mediums ───────────────────────────────────────────────────────────

_VERSE_MEDIUM = Medium(id="verse", label="Verse", icon="✍", kind="text",
                       source="artgen", generator="verse")


def _patch_artgen_deps(monkeypatch, *, run_tt_ctl=None, tmp_path=None):
    """Patch every real dependency `_create_generate_artgen`'s background
    closure imports, so no real subprocess/disk/sqlite I/O happens."""
    import artgen
    import artgen_thumb
    import media_store
    import pipeline_engine

    fake_gen = MagicMock()
    fake_gen.output_ext = ".txt"
    monkeypatch.setattr(artgen, "get", lambda name: fake_gen)

    out_path = (tmp_path or Path("/tmp")) / "verse_artifact.txt"
    monkeypatch.setattr(artgen_thumb, "make_artgen_path", lambda *a, **k: out_path)
    monkeypatch.setattr(artgen_thumb, "make_thumbnail", lambda src, dst: Path(""))

    fake_ms = MagicMock()
    monkeypatch.setattr(media_store, "media_store", fake_ms)

    run_tt_ctl_spy = run_tt_ctl or MagicMock()
    monkeypatch.setattr(pipeline_engine, "_run_tt_ctl", run_tt_ctl_spy)

    return fake_ms, run_tt_ctl_spy, out_path


def test_artgen_medium_shells_out_to_tt_ctl_artgen_with_generator_and_flags(
    monkeypatch, tmp_path
):
    obj = _make_mw(monkeypatch)
    fake_ms, run_tt_ctl_spy, out_path = _patch_artgen_deps(monkeypatch, tmp_path=tmp_path)

    params = {"prompt": "winter forges", "form": "haiku", "count": 5}
    obj._on_create_generate(_VERSE_MEDIUM, params)

    run_tt_ctl_spy.assert_called_once()
    (argv,), _kwargs = run_tt_ctl_spy.call_args
    assert argv[0] == "artgen"
    assert argv[1] == "verse"
    assert "--output" in argv
    assert str(out_path) in argv
    # generator-specific flags map via _flag_from_key (underscore -> hyphen)
    assert "--form" in argv
    assert "haiku" in argv
    assert "--count" in argv
    assert "5" in argv
    # the idea-door prompt must NOT be forwarded — no artgen generator has a
    # common --prompt flag (see _create_generate_artgen's docstring).
    assert "--prompt" not in argv


_ANIMATEDIFF_MEDIUM = Medium(id="animatediff", label="AnimateDiff", icon="🕺",
                             kind="gif", source="artgen", generator="animatediff")


def test_artgen_animatediff_skips_empty_append_flags(monkeypatch, tmp_path):
    """FIX 2 (artgen "animatediff" medium failed 100% via Create): its
    `--per-chip-prompt`/`--prompt-schedule` are argparse `action="append"`
    flags rendered as blank entries that collect "" (not None). The forwarding
    loop must skip empty/whitespace strings and empty lists, or it emits
    `--prompt-schedule ""` → `tt-ctl artgen animatediff` raises. A meaningful
    scalar flag alongside must still be forwarded."""
    obj = _make_mw(monkeypatch)
    fake_ms, run_tt_ctl_spy, _out = _patch_artgen_deps(monkeypatch, tmp_path=tmp_path)
    monkeypatch.setattr(
        __import__("artgen"), "get",
        lambda name: type("G", (), {"output_ext": ".gif"})(),
    )

    params = {
        "prompt": "a spinning galaxy",       # idea-door prompt — never forwarded
        "prompt_schedule": "",               # empty append flag — must be skipped
        "per_chip_prompt": [],               # empty list append flag — must be skipped
        "ad_neg_prompt": "   ",              # whitespace-only string — must be skipped
        "steps": 8,                          # real scalar — must be forwarded
    }
    obj._on_create_generate(_ANIMATEDIFF_MEDIUM, params)

    run_tt_ctl_spy.assert_called_once()
    (argv,), _kwargs = run_tt_ctl_spy.call_args
    assert argv[0] == "artgen"
    assert argv[1] == "animatediff"
    # No empty-valued flag anywhere in argv.
    assert "--prompt-schedule" not in argv
    assert "--per-chip-prompt" not in argv
    assert "--ad-neg-prompt" not in argv
    assert "--prompt" not in argv
    # The one meaningful scalar flag survives.
    assert "--steps" in argv
    assert "8" in argv
    # No bare "" ever leaked into argv as a value.
    assert "" not in argv
    assert "   " not in argv


_LANDSCAPE_MEDIUM = Medium(id="landscape", label="Landscape", icon="🏔",
                           kind="image", source="artgen", generator="landscape")


def _patch_artgen_deps_real_generator(monkeypatch, *, tmp_path):
    """Like `_patch_artgen_deps` but LEAVES `artgen.get` real, so
    `artgen_bool_flags`/introspection see the generator's actual argparse — the
    bool-spelling decision (FIX 3) depends on it. Only the disk/sqlite/subprocess
    side effects are stubbed."""
    import artgen_thumb
    import media_store
    import pipeline_engine

    out_path = tmp_path / "landscape_artifact.svg"
    monkeypatch.setattr(artgen_thumb, "make_artgen_path", lambda *a, **k: out_path)
    monkeypatch.setattr(artgen_thumb, "make_thumbnail", lambda src, dst: Path(""))

    fake_ms = MagicMock()
    monkeypatch.setattr(media_store, "media_store", fake_ms)

    run_tt_ctl_spy = MagicMock()
    monkeypatch.setattr(pipeline_engine, "_run_tt_ctl", run_tt_ctl_spy)

    return fake_ms, run_tt_ctl_spy, out_path


def test_artgen_default_true_bool_off_emits_negative_flag(monkeypatch, tmp_path):
    """FIX 3 (whole-branch review): landscape's --mountains is store_true
    default=True, paired with --no-mountains (store_false, same dest). Turning
    the switch OFF collects `mountains=False`; the seam must emit the EXPLICIT
    --no-mountains (never omit both flags, which would let the generator fall
    back to its default and ignore the user's choice). A default-off flag
    turned ON (clouds=True) emits its bare positive --clouds.

    This FAILS against the pre-fix code (which routed False through
    `_append_flag_value` and dropped it entirely)."""
    obj = _make_mw(monkeypatch)
    fake_ms, run_tt_ctl_spy, _out = _patch_artgen_deps_real_generator(
        monkeypatch, tmp_path=tmp_path
    )

    params = {
        "mountains": False,   # default-True switch turned OFF → --no-mountains
        "clouds": True,       # default-False switch turned ON  → --clouds
        "stars": False,       # default-False, still OFF        → --no-stars (or omit)
        "glitch": False,      # bare store_true, OFF, no negation → omitted entirely
    }
    obj._on_create_generate(_LANDSCAPE_MEDIUM, params)

    run_tt_ctl_spy.assert_called_once()
    (argv,), _kwargs = run_tt_ctl_spy.call_args

    # OFF default-True bool → its explicit negative spelling, NOT omitted.
    assert "--no-mountains" in argv
    assert "--mountains" not in argv
    # ON default-False bool → bare positive spelling.
    assert "--clouds" in argv
    assert "--no-clouds" not in argv
    # A bare store_true with no "--no-x" left OFF emits nothing.
    assert "--glitch" not in argv


def test_artgen_medium_records_artifact_in_media_store(monkeypatch, tmp_path):
    obj = _make_mw(monkeypatch)
    fake_ms, _spy, out_path = _patch_artgen_deps(monkeypatch, tmp_path=tmp_path)

    obj._on_create_generate(_VERSE_MEDIUM, {"prompt": "winter forges", "form": "haiku"})

    fake_ms.add.assert_called_once()
    (rec,), _kwargs = fake_ms.add.call_args
    assert rec.media_type == "artgen"
    assert rec.generator_type == "verse"
    assert rec.file_path == str(out_path)
    fake_ms.ensure_auto_playlists.assert_called_once()
    obj._set_status.assert_called_with("Verse ready.")


def test_artgen_medium_fails_soft_when_tt_ctl_raises(monkeypatch, tmp_path):
    """A failed subprocess must surface as a status message, never crash —
    and must never record a (missing/invalid) artifact into the media store."""
    obj = _make_mw(monkeypatch)
    failing_spy = MagicMock(side_effect=RuntimeError("tt-ctl artgen verse failed (exit 1)"))
    fake_ms, _spy, _out_path = _patch_artgen_deps(
        monkeypatch, run_tt_ctl=failing_spy, tmp_path=tmp_path
    )

    obj._on_create_generate(_VERSE_MEDIUM, {"prompt": "winter forges"})

    fake_ms.add.assert_not_called()
    last_status = obj._set_status.call_args[0][0]
    assert "Couldn't generate Verse" in last_status


def test_artgen_medium_with_no_generator_mapped_fails_soft(monkeypatch):
    """A medium claiming source="artgen" but no generator name must not crash —
    (defensive: discover_mediums never actually produces this shape today)."""
    obj = _make_mw(monkeypatch)
    bad_medium = Medium(id="mystery", label="Mystery", icon="?", kind="text",
                        source="artgen", generator=None)

    obj._on_create_generate(bad_medium, {})

    obj._set_status.assert_called_with("No artgen generator mapped for Mystery.")


def test_unknown_medium_source_fails_soft(monkeypatch):
    obj = _make_mw(monkeypatch)
    weird_medium = Medium(id="future", label="Future Thing", icon="?", kind="text",
                          source="plugin", generator=None)

    obj._on_create_generate(weird_medium, {})

    obj._set_status.assert_called_with("Don't know how to generate a Future Thing yet.")
