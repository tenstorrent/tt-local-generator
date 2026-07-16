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
    """Stand-in proving `_create_generate_native`'s video branch no longer
    touches ControlPanel's model state at all (SP-3a decouple).

    This class used to reproduce ControlPanel's REAL `_set_model`
    source-gated clobber bug (FIX 1: routing a video-model key through
    `_set_model` while `_model_source == "image"` would corrupt the legacy
    Image tab's `_image_model`) so the fix — setting `_video_model` directly
    — could be proven. SP-3a deleted that whole sync (`_on_generate` now
    takes `video_model_key` as an explicit param instead of reading
    `self._controls.get_video_model()`), so `_set_model`/`get_video_model`
    now raise: re-introducing either the old sync-hack write or a read-back
    would fail these tests loudly. `get_image_model` still works — read by
    the harness to prove an unrelated video Create job never touches it.
    """

    def __init__(self, model_source: str) -> None:
        self._model_source = model_source
        self._image_model = "flux"        # a valid image key (the default)
        self._video_model = "animatediff"  # untouched sentinel — no branch may reassign this

    def _set_model(self, model: str) -> None:
        raise AssertionError(
            "_create_generate_native must not sync ControlPanel's model "
            "state anymore — SP-3a removed the v0.27.1 hack"
        )

    def get_video_model(self) -> str:
        raise AssertionError(
            "_create_generate_native must not read _controls.get_video_model() "
            "— SP-3a: video_model_key is passed to _on_generate explicitly"
        )

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
    # SP-3a: `_create_generate_native` no longer touches `self._controls` at
    # all (the model key is passed to `_on_generate` explicitly) — a
    # MagicMock is fine here for every branch. Tests that want to PROVE
    # `_controls` is untouched swap in `_FakeControls` (below), whose
    # `_set_model`/`get_video_model` raise.
    obj._controls = MagicMock()
    # `_on_create_generate`'s re-entrancy guard (review fix, task-3-report.md)
    # reads this unconditionally at the very top of the method, before the
    # try/except that used to be the only place Create-job state was
    # touched — so every harness building a real `_on_create_generate` call
    # must initialize it now, mirroring `MainWindow.__init__`'s own default.
    obj._create_job_active = False

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


def test_image_medium_forwards_seed_image_path(monkeypatch):
    """SP-3c-1: ImageParamPanel.collect()'s `seed_image_path` (from its
    SeedImageWell) must reach `_on_generate` so i2i generation actually gets
    the conditioning image."""
    obj = _make_mw(monkeypatch)
    params = {"model": "flux.1-schnell", "seed_image_path": "/tmp/a-seed.png"}

    obj._on_create_generate(_IMAGE_MEDIUM, params)

    _args, kwargs = obj._on_generate.call_args
    assert kwargs["seed_image_path"] == "/tmp/a-seed.png"


def test_image_medium_defaults_seed_image_path_to_empty_string(monkeypatch):
    """MIGRATION-SAFE: a panel/caller that never supplies `seed_image_path`
    (e.g. before SeedImageWell existed) must still produce "" — the exact
    default that preserves today's text-to-image behavior — not raise or
    forward `None`."""
    obj = _make_mw(monkeypatch)
    params = {"model": "flux.1-schnell"}  # no seed_image_path key at all

    obj._on_create_generate(_IMAGE_MEDIUM, params)

    _args, kwargs = obj._on_generate.call_args
    assert kwargs["seed_image_path"] == ""


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


def test_video_medium_forwards_seed_image_path(monkeypatch):
    """SP-3c-1: VideoParamPanel.collect()'s `seed_image_path` must reach
    `_on_generate` — this is what makes the re-enabled SkyReels-I2V model
    actually able to receive its required conditioning image (the base64-
    encode block in `_on_generate`'s video branch reads this parameter for
    `video_model_key == "skyreels"`)."""
    obj = _make_mw(monkeypatch)
    params = {"model": "skyreels-v2-i2v-14b-540p", "seed_image_path": "/tmp/character.png"}

    obj._on_create_generate(_VIDEO_MEDIUM, params)

    _args, kwargs = obj._on_generate.call_args
    assert kwargs["video_model_key"] == "skyreels"
    assert kwargs["seed_image_path"] == "/tmp/character.png"


def test_video_medium_defaults_seed_image_path_to_empty_string(monkeypatch):
    """MIGRATION-SAFE: no `seed_image_path` in params -> "" reaches
    `_on_generate`, preserving today's exact text-to-video behavior for
    wan2/mochi."""
    obj = _make_mw(monkeypatch)
    params = {"model": "wan2.2-t2v"}  # no seed_image_path key at all

    obj._on_create_generate(_VIDEO_MEDIUM, params)

    _args, kwargs = obj._on_generate.call_args
    assert kwargs["seed_image_path"] == ""


def test_video_medium_no_longer_syncs_or_reads_controls_video_model(monkeypatch):
    """SP-3a (decouple `_on_generate` from ControlPanel): the v0.27.1 "FIX 1"
    sync hack — `_create_generate_native` setting `self._controls._video_model`
    before calling `_on_generate` so its (now-deleted) internal
    `self._controls.get_video_model()` read would pick up the right key — is
    gone. `_on_generate` now takes `video_model_key` as an explicit param, so
    `_create_generate_native` neither writes nor reads ControlPanel's model
    state at all. `_FakeControls` (`_set_model`/`get_video_model` raise)
    proves that; the resolved short key still reaches `_on_generate` via the
    new kwarg."""
    obj = _make_mw(monkeypatch)
    obj._controls = _FakeControls("video")

    obj._on_create_generate(_VIDEO_MEDIUM, {"model": "wan2.2-t2v"})

    # canonical "wan2.2-t2v" -> short key "wan2", passed explicitly.
    obj._on_generate.assert_called_once()
    assert obj._on_generate.call_args.kwargs["video_model_key"] == "wan2"
    assert obj._on_generate.call_args.kwargs["model_id"] == "wan2"
    # The sentinel proves _video_model was never reassigned (no sync hack).
    assert obj._controls._video_model == "animatediff"


def test_video_medium_does_not_touch_image_model_in_image_source(monkeypatch):
    """Regression guard for the FIX-1-era bug this replaces: even with the
    control in `_model_source == "image"` (zero-click-reachable at startup
    when `last_successful_deployment` was an image model), a video Create job
    must not touch `_image_model`. Trivially true post-SP-3a since
    `_create_generate_native`'s video branch never calls into `_controls` at
    all — kept as an explicit regression guard against that coupling coming
    back."""
    obj = _make_mw(monkeypatch)
    obj._controls = _FakeControls("image")  # zero-click-reachable startup state

    obj._on_create_generate(_VIDEO_MEDIUM, {"model": "wan2.2-t2v"})

    # (a) the video worker selection is correct, passed explicitly...
    obj._on_generate.assert_called_once()
    assert obj._on_generate.call_args.kwargs["video_model_key"] == "wan2"
    assert obj._on_generate.call_args.kwargs["model_source"] == "video"
    # (b) ...and the legacy image-model selection is UNTOUCHED (still "flux").
    assert obj._controls.get_image_model() == "flux"


# ── Native AnimateDiff routing (SP-3c-2) ─────────────────────────────────────
#
# Distinct from the artgen `animatediff` plugin medium (source="artgen",
# generator="animatediff" — see `_ANIMATEDIFF_MEDIUM`/
# `test_artgen_animatediff_skips_empty_append_flags` further down, which
# shells out via `tt-ctl artgen animatediff`). THIS is the native, serverless
# AnimateDiff v0.9 path selectable from Create's Video medium
# (`create_param_panels.VideoParamPanel`'s "animatediff" model choice).


def test_video_medium_animatediff_selected_routes_with_complete_args(monkeypatch):
    """Selecting AnimateDiff (canonical id "animatediff-blackhole", the value
    `create_view._collect_params` writes into "model" once the scoped
    dropdown's AnimateDiff entry is chosen — see test_create_view.py's
    `test_collect_params_model_animatediff`) must resolve to
    `video_model_key="animatediff"` and a COMPLETE `animatediff_args` dict —
    every `_ANIMATEDIFF_DEFAULTS` key present — with the panel's own value
    (here just `temporal_alpha`, simulating a partial dict) applied over the
    defaults."""
    import main_window as mw

    obj = _make_mw(monkeypatch)
    params = {
        "prompt": "a glitchy dance loop",
        "model": "animatediff-blackhole",
        "num_inference_steps": 20,
        "seed": -1,
        "animatediff_args": {"temporal_alpha": 0.9},
    }

    obj._on_create_generate(_VIDEO_MEDIUM, params)

    obj._on_generate.assert_called_once()
    kwargs = obj._on_generate.call_args.kwargs
    assert kwargs["video_model_key"] == "animatediff"
    assert kwargs["model_id"] == "animatediff"
    ad = kwargs["animatediff_args"]
    assert set(ad) == set(mw._ANIMATEDIFF_DEFAULTS)
    # The panel-supplied value wins...
    assert ad["temporal_alpha"] == 0.9
    # ...every other key falls back to the documented default.
    for key, default_value in mw._ANIMATEDIFF_DEFAULTS.items():
        if key == "temporal_alpha":
            continue
        assert ad[key] == default_value


def test_video_medium_animatediff_with_full_panel_args_passes_through_unchanged(monkeypatch):
    """A COMPLETE `animatediff_args` (what `VideoParamPanel.collect()` always
    actually produces — see that method's docstring) merges over
    `_ANIMATEDIFF_DEFAULTS` as a pure no-op: every value is the caller's,
    none silently reset to a default."""
    import main_window as mw

    obj = _make_mw(monkeypatch)
    full_args = {
        "mode": "cpu", "negative_prompt": "oversaturated", "temporal_alpha": 0.5,
        "lightning": True, "lightning_steps": 8, "multi_chip": False,
        "device_id": 2, "chain_from": "/tmp/latents.chain.pt", "chain_save": True,
        "chain_alpha": 0.4, "motion_adapter": "", "motion_adapter_alpha": 0.8,
        "motion_adapter_skip": ["up2"],
    }
    assert set(full_args) == set(mw._ANIMATEDIFF_DEFAULTS)  # test itself stays honest
    params = {"model": "animatediff-blackhole", "animatediff_args": full_args}

    obj._on_create_generate(_VIDEO_MEDIUM, params)

    kwargs = obj._on_generate.call_args.kwargs
    assert kwargs["animatediff_args"] == full_args


def test_video_medium_animatediff_with_no_args_key_still_gets_complete_defaults(monkeypatch):
    """A caller that selects AnimateDiff but supplies no "animatediff_args"
    key at all (defensive — shouldn't happen via VideoParamPanel, which
    always includes it, but must never KeyError downstream in
    `_on_generate`'s `ad["..."]` indexing) still gets the full default dict."""
    import main_window as mw

    obj = _make_mw(monkeypatch)
    params = {"model": "animatediff-blackhole"}  # no "animatediff_args" key

    obj._on_create_generate(_VIDEO_MEDIUM, params)

    kwargs = obj._on_generate.call_args.kwargs
    assert kwargs["video_model_key"] == "animatediff"
    assert kwargs["animatediff_args"] == mw._ANIMATEDIFF_DEFAULTS


def test_non_animatediff_video_model_passes_animatediff_args_as_none(monkeypatch):
    """Parity guard: a non-AnimateDiff video model must route EXACTLY as
    before this task — `animatediff_args` stays `None` (the pre-existing
    `_on_generate` default), never a dict, even if `params` happens to carry
    a stray "animatediff_args" key (e.g. a stale queue replay)."""
    obj = _make_mw(monkeypatch)
    params = {
        "model": "wan2.2-t2v",
        "animatediff_args": {"temporal_alpha": 0.1},  # must be ignored
    }

    obj._on_create_generate(_VIDEO_MEDIUM, params)

    kwargs = obj._on_generate.call_args.kwargs
    assert kwargs["video_model_key"] == "wan2"
    assert kwargs["animatediff_args"] is None


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


# ── In-place Create results: native generation lifecycle → result panel ─────
#
# (task-3-brief.md) A Create-originated native (image/video/animate) job must
# drive `self._create_view._result_panel` (a `create_view.CreateResultPanel`)
# through pending -> progress* -> finished|error, and skip the gallery's own
# PendingCard (the panel owns that UI for Create jobs instead) — while the
# finished record must still reach the gallery/store exactly as it does for
# a non-Create job (attractor/TT-TV/queue), which must remain UNAFFECTED.
#
# This harness binds the REAL `_on_generate`/`_on_progress`/`_on_finished`/
# `_on_error` (unlike `_make_mw` above, which mocks `_on_generate` out because
# that file's job is dispatch/translation, not the generation lifecycle
# itself). `threading.Thread` is stubbed to NEVER run its target — these
# tests only care about the synchronous state established before the worker
# thread would start (gallery/panel wiring), not about a real worker actually
# executing (which would need a live server).

class _NoOpThread:
    """threading.Thread stand-in whose start() does nothing. Unlike
    `_ImmediateThread` above (which runs its target synchronously — used by
    the artgen tests, whose target is fully mocked-safe), `_on_generate`'s
    thread target calls a REAL `GenerationWorker.run_with_callbacks`, which
    would attempt a real HTTP request. These lifecycle tests only assert on
    state set up before the thread is started, so the target is never run."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        pass  # deliberately never runs self._target


class _FakeResultPanel:
    """Records every call CreateResultPanel would receive, in order, as
    `(method_name, *args)` tuples — mirrors the brief's
    `mw._create_view._result_panel.calls` seam."""

    def __init__(self) -> None:
        self.calls: list = []

    def show_pending(self, prompt, medium=None):
        self.calls.append(("show_pending", prompt, medium))

    def show_progress(self, message):
        self.calls.append(("show_progress", message))

    def show_finished(self, record):
        self.calls.append(("show_finished", record))

    def show_error(self, message):
        self.calls.append(("show_error", message))


class _FakeCreateView:
    def __init__(self) -> None:
        self._result_panel = _FakeResultPanel()
        # SP-3c-4: `_refresh_create_queue_display` calls
        # `self._create_view.refresh_queue(pending, on_cancel)` — a MagicMock
        # here lets tests assert on the pending-queue payload MainWindow
        # pushed into CreateView without needing a real `CreateResultPanel`
        # GTK widget tree.
        self.refresh_queue = MagicMock()


class _FakeGallery:
    """Stand-in for `GalleryWidget` recording only the calls these tests
    care about: whether a pending card was added, and whether a finished
    record reached the gallery (the persistence invariant)."""

    def __init__(self) -> None:
        self.add_pending_calls: list = []
        self.replace_calls: list = []
        self.remove_pending_calls = 0

    def add_pending_card(self, prompt="", model_source="video"):
        self.add_pending_calls.append((prompt, model_source))
        return MagicMock()

    def replace_pending_with(self, record):
        # Real `GalleryWidget.replace_pending_with` degrades gracefully when
        # there's no pending card to replace — it inserts the record as a
        # normal card instead (verified by reading main_window.py directly).
        # This fake mirrors that: it always records the record regardless of
        # whether a pending card exists, matching the real persistence path.
        self.replace_calls.append(record)

    def remove_pending(self):
        self.remove_pending_calls += 1


class _FakeRecord:
    """Duck-typed stand-in for `history_store.GenerationRecord` — only the
    attributes `_on_finished` actually reads."""

    def __init__(self) -> None:
        self.id = "rec-1"
        self.media_type = "image"
        self.media_file_path = "/tmp/fake_record.png"
        self.duration_s = 1.5


def _fake_record() -> _FakeRecord:
    return _FakeRecord()


def _make_mw_lifecycle(monkeypatch):
    """Minimal MainWindow exposing the real generation lifecycle methods
    (`_on_create_generate` through `_on_generate`/`_on_progress`/
    `_on_finished`/`_on_error`), with every collaborator they touch stubbed
    to a lightweight fake/mock so no real GTK widgets, disk I/O, or network
    calls happen."""
    import main_window as mw

    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)

    obj._set_status = MagicMock()
    obj._controls = MagicMock()
    obj._client = MagicMock()
    obj._store = MagicMock()
    obj._worker = None
    obj._worker_gen = None
    obj._gen_gallery = None
    obj._gen_completed_count = 0
    obj._last_error_log_path = None
    obj._attractor_win = None
    obj._create_job_active = False
    obj._artgen_panel = None

    fake_gallery = _FakeGallery()
    obj._gallery_for_type = MagicMock(return_value=fake_gallery)
    obj._active_gallery = MagicMock(return_value=fake_gallery)
    obj._check_disk_space = MagicMock(return_value=True)
    obj._screensaver_inhibit = MagicMock()
    obj._screensaver_uninhibit = MagicMock()
    obj._start_next_queued = MagicMock()
    obj._update_attractor_btn = MagicMock()
    obj._rebuild_playlists_menu = MagicMock()
    obj._count_blackhole_chips = MagicMock(return_value=0)

    # SP-3c-4: the generation queue itself, plus enough of a real widget
    # tree (`_queue_box`/`_queue_section_lbl` are real GTK widgets — this
    # module already requires a display, see the probe at the top of the
    # file — `_hw_statusbar` is a MagicMock since HardwareStatusBar isn't
    # under test here) that the REAL `_update_queue_display` can run without
    # touching anything not set up by this harness.
    obj._queue = []
    obj._queue_box = Gtk.Box()
    obj._queue_section_lbl = Gtk.Label()
    obj._hw_statusbar = MagicMock()

    fake_create_view = _FakeCreateView()
    obj._create_view = fake_create_view

    for name in (
        "_on_create_generate",
        "_create_generate_native",
        "_create_generate_artgen",
        "_native_generate_args",
        "_create_enqueue_native",
        "_on_create_artgen_done",
        "_on_create_artgen_error",
        "_begin_create_job",
        "_fail_create_job",
        "_on_generate",
        "_on_progress",
        "_on_finished",
        "_on_error",
        "_on_enqueue",
        "_on_queue_remove",
        "_persist_queue",
        "_update_queue_display",
        "_refresh_create_queue_display",
    ):
        setattr(obj, name, getattr(mw.MainWindow, name).__get__(obj))
    # `_start_next_queued` stays the no-op MagicMock set above — enqueue-path
    # tests must not accidentally auto-drain the queue. The one
    # faithful-replay test that needs the REAL `_start_next_queued` rebinds
    # it itself, right before calling it.

    monkeypatch.setattr(mw.threading, "Thread", _NoOpThread)
    monkeypatch.setattr(mw.GLib, "idle_add", lambda fn, *a: fn(*a))

    return obj, fake_gallery, fake_create_view


# ── SkyReels-I2V seed-image guard (SP-3c-1 review fix) ──────────────────────
#
# SkyReels-V2-I2V-14B-540P (re-enabled this task) is an image-to-video model
# that REQUIRES a conditioning image — the exact reason it was pulled from
# the Video door in v0.27.1. Re-enabling the model choice without also
# gating generation would let a user click Create with no seed image and
# have the request silently fail server-side (or worse, wrongly proceed).
# `_create_generate_native`'s video branch must block BEFORE ever calling
# `_on_generate` when model == skyreels and `seed_image_path` is empty,
# surfacing a clear message via the inline result panel — mirrors
# ControlPanel's own `_seed_image_required()`/`_on_action_clicked` guard
# (main_window.py ~6126/~6579) so both surfaces enforce the same rule.


def test_skyreels_without_seed_image_blocks_generation_and_shows_error(monkeypatch):
    obj, _fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._on_generate = MagicMock()  # spy: must never be called when blocked

    params = {
        "prompt": "a dancer moving to the beat",
        "model": "skyreels-v2-i2v-14b-540p",
        "num_inference_steps": 20,
        "seed": -1,
        # no "seed_image_path" key at all — the exact state a fresh
        # VideoParamPanel with an untouched SeedImageWell collects.
    }

    obj._on_create_generate(_VIDEO_MEDIUM, params)

    obj._on_generate.assert_not_called()
    assert fake_create_view._result_panel.calls[-1][0] == "show_error"
    assert "SkyReels" in fake_create_view._result_panel.calls[-1][1]
    # `_fail_create_job` must clear the flag, exactly like every other
    # early-return guard (worker-busy, disk space, artgen-no-generator) —
    # otherwise the panel would be stuck "pending" forever.
    assert obj._create_job_active is False


def test_skyreels_with_empty_string_seed_image_path_also_blocks(monkeypatch):
    """An explicit `""` (not just a missing key) must be treated identically
    — both are "no seed image", the exact default `SeedImageWell.path()`
    returns before a file is chosen."""
    obj, _fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._on_generate = MagicMock()

    params = {"model": "skyreels-v2-i2v-14b-540p", "seed_image_path": ""}

    obj._on_create_generate(_VIDEO_MEDIUM, params)

    obj._on_generate.assert_not_called()
    assert fake_create_view._result_panel.calls[-1][0] == "show_error"


def test_skyreels_with_seed_image_proceeds_to_on_generate(monkeypatch):
    """With a real seed image path present, SkyReels-I2V generation proceeds
    normally — the guard must never block the model when it CAN actually
    run, only when it can't."""
    obj, _fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._on_generate = MagicMock()

    params = {
        "prompt": "a dancer moving to the beat",
        "model": "skyreels-v2-i2v-14b-540p",
        "seed_image_path": "/tmp/character.png",
    }

    obj._on_create_generate(_VIDEO_MEDIUM, params)

    obj._on_generate.assert_called_once()
    kwargs = obj._on_generate.call_args.kwargs
    assert kwargs["video_model_key"] == "skyreels"
    assert kwargs["seed_image_path"] == "/tmp/character.png"
    assert not any(c[0] == "show_error" for c in fake_create_view._result_panel.calls)


def test_non_skyreels_video_model_never_requires_a_seed_image(monkeypatch):
    """Regression guard: the new check must be scoped to `model_key ==
    "skyreels"` only — wan2/mochi must keep working with no seed image at
    all, exactly as before this task."""
    obj, _fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._on_generate = MagicMock()

    params = {"model": "wan2.2-t2v"}  # no seed_image_path

    obj._on_create_generate(_VIDEO_MEDIUM, params)

    obj._on_generate.assert_called_once()
    assert not any(c[0] == "show_error" for c in fake_create_view._result_panel.calls)


def test_create_native_job_shows_pending_in_panel(monkeypatch):
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)

    obj._on_create_generate(_IMAGE_MEDIUM, {"prompt": "a lighthouse at dawn",
                                             "model": "flux.1-schnell"})

    assert fake_create_view._result_panel.calls[0] == (
        "show_pending", "a lighthouse at dawn", _IMAGE_MEDIUM
    )
    assert obj._create_job_active is True


def test_create_job_skips_gallery_pending_card(monkeypatch):
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)

    obj._on_create_generate(_IMAGE_MEDIUM, {"prompt": "a lighthouse at dawn",
                                             "model": "flux.1-schnell"})

    # The panel owns pending UI for Create jobs — the gallery must NOT get
    # its own redundant PendingCard.
    assert fake_gallery.add_pending_calls == []
    # ...but `_gen_gallery` is still set, so the finished record can still
    # land in the right gallery/store on completion.
    assert obj._gen_gallery is fake_gallery


def test_non_create_job_still_adds_gallery_pending_card(monkeypatch):
    """Migration-safety: a non-Create job (attractor/TT-TV/queue) must be
    completely unaffected — it still gets the gallery's own pending card and
    never touches the panel."""
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    assert obj._create_job_active is False

    obj._on_generate("a train through the mountains", "", 20, -1,
                     model_source="image", model_id="flux")

    assert len(fake_gallery.add_pending_calls) == 1
    assert fake_create_view._result_panel.calls == []


def test_progress_forwards_to_panel_when_create_job_active(monkeypatch):
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._create_job_active = True

    obj._on_progress("Generating image with flux.1-schnell…", None)

    assert fake_create_view._result_panel.calls == [
        ("show_progress", "Generating image with flux.1-schnell…")
    ]


def test_progress_does_not_touch_panel_for_non_create_job(monkeypatch):
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    assert obj._create_job_active is False

    obj._on_progress("Generating…", MagicMock())

    assert fake_create_view._result_panel.calls == []


def test_finished_forwards_to_panel_and_still_hits_store(monkeypatch):
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._create_job_active = True
    obj._gen_gallery = fake_gallery
    record = _fake_record()

    obj._on_finished(record)

    assert tuple(c[0] for c in fake_create_view._result_panel.calls[-1:]) == ("show_finished",)
    assert fake_create_view._result_panel.calls[-1][1] is record
    # Flag cleared once the job completes.
    assert obj._create_job_active is False
    # Persistence path still ran — the record reached the gallery exactly as
    # it does today, even though no pending card existed to "replace".
    assert fake_gallery.replace_calls == [record]


def test_non_create_job_does_not_touch_panel(monkeypatch):
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._create_job_active = False
    obj._gen_gallery = fake_gallery
    record = _fake_record()

    obj._on_finished(record)

    assert fake_create_view._result_panel.calls == []
    # Persistence is identical regardless of Create involvement.
    assert fake_gallery.replace_calls == [record]


def test_error_forwards_to_panel_and_clears_flag(monkeypatch):
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._create_job_active = True
    obj._gen_gallery = fake_gallery

    obj._on_error("Worker crashed: boom")

    assert fake_create_view._result_panel.calls[-1][0] == "show_error"
    assert obj._create_job_active is False


def test_error_does_not_touch_panel_for_non_create_job(monkeypatch):
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._create_job_active = False
    obj._gen_gallery = fake_gallery

    obj._on_error("Worker crashed: boom")

    assert fake_create_view._result_panel.calls == []


def test_end_to_end_create_native_job_lifecycle_persists_without_pending_card(monkeypatch):
    """Full lifecycle in one test: dispatch through `_on_create_generate`
    (which internally calls the REAL `_on_generate`), then simulate the
    worker callbacks that would normally fire from the background thread.
    Confirms the whole chain — pending shown in panel, no gallery pending
    card, record still reaches the gallery/store, flag cleared at the end."""
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)

    obj._on_create_generate(_IMAGE_MEDIUM, {"prompt": "a lighthouse at dawn",
                                             "model": "flux.1-schnell"})
    assert obj._create_job_active is True
    assert fake_gallery.add_pending_calls == []

    record = _fake_record()
    obj._on_finished(record)

    assert fake_gallery.replace_calls == [record]
    assert obj._create_job_active is False
    method_names = [c[0] for c in fake_create_view._result_panel.calls]
    assert method_names == ["show_pending", "show_finished"]


def test_panel_error_never_blocks_generation(monkeypatch):
    """A raising CreateResultPanel must never prevent generation from
    starting — `_begin_create_job` wraps the panel call in try/except."""
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)

    def _boom(*_a, **_k):
        raise RuntimeError("panel exploded")

    fake_create_view._result_panel.show_pending = _boom

    obj._on_create_generate(_IMAGE_MEDIUM, {"prompt": "a lighthouse at dawn",
                                             "model": "flux.1-schnell"})

    assert obj._create_job_active is True
    # Generation itself proceeded past the panel call (gen_gallery got set,
    # meaning _on_generate ran) and no "Couldn't start generation" status was
    # ever set — the exception never propagated past _begin_create_job.
    assert obj._gen_gallery is fake_gallery
    for call in obj._set_status.call_args_list:
        assert "Couldn't start generation" not in call.args[0]


# ── Review fix: _on_generate early returns must clear Create-job state ──────
#
# Bug found in review of commit 18b4486: `_begin_create_job` sets
# `_create_job_active = True` and shows "pending" in the panel BEFORE
# `_on_generate` runs — but `_on_generate` has several early returns (worker
# already running, disk space critically low, AnimateDiff chip-busy) that
# fire before any Create-aware logic. Left as-is, a Create job that hits one
# of these would leave the panel stuck on "Generating…" forever AND leave
# `_create_job_active` stuck True — the window-global flag then causes the
# NEXT unrelated (non-Create) job to wrongly skip its own gallery pending
# card and have its progress/finished/error misrouted into the stale Create
# panel. `_fail_create_job(reason)` fixes this: called at every early
# return, it clears the flag and shows the reason as an error in the panel
# (a no-op when no Create job is active, so non-Create early returns are
# unaffected).

def test_disk_space_early_return_clears_create_job_and_shows_error(monkeypatch):
    """A Create job that dies on the disk-space guard must not leave the
    panel stuck "pending" or the flag stuck True."""
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._check_disk_space = MagicMock(return_value=False)

    obj._on_create_generate(_IMAGE_MEDIUM, {"prompt": "a lighthouse at dawn",
                                             "model": "flux.1-schnell"})

    # Not stuck: flag cleared, panel moved off "pending" into "error".
    assert obj._create_job_active is False
    assert fake_create_view._result_panel.calls[-1][0] == "show_error"
    # No worker/gallery pending card was ever created — the guard fired
    # before any of that setup.
    assert fake_gallery.add_pending_calls == []
    assert obj._gen_gallery is None


def test_worker_already_running_early_return_clears_create_job_and_shows_error(monkeypatch):
    """Same invariant for the worker-already-alive guard."""
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    busy_worker = MagicMock()
    busy_worker.is_alive.return_value = True
    obj._worker = busy_worker

    obj._on_create_generate(_IMAGE_MEDIUM, {"prompt": "a lighthouse at dawn",
                                             "model": "flux.1-schnell"})

    assert obj._create_job_active is False
    assert fake_create_view._result_panel.calls[-1][0] == "show_error"
    assert fake_gallery.add_pending_calls == []
    assert obj._gen_gallery is None


def test_animatediff_chip_busy_early_return_clears_create_job_and_shows_error(monkeypatch):
    """Same invariant for the AnimateDiff chip-busy guard deep in the video
    branch — currently unreachable via Create's scoped video-model dropdown,
    but fixed for consistency per the review note.

    SP-3a: `_on_generate` no longer reads `self._controls.get_video_model()`/
    `get_animatediff_args()` to decide this branch — the AD key and args are
    now passed as explicit `video_model_key`/`animatediff_args` kwargs,
    exactly as `_start_next_queued`/the legacy ControlPanel button do."""
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._controls.get_video_model.side_effect = AssertionError("must not read")
    obj._controls.get_animatediff_args.side_effect = AssertionError("must not read")
    animatediff_args = {
        "mode": "blackhole", "negative_prompt": "", "temporal_alpha": 0.0,
        "lightning": False, "lightning_steps": 0, "multi_chip": False,
        "device_id": 0, "chain_from": None, "chain_save": False,
        "chain_alpha": 0.0, "motion_adapter": None, "motion_adapter_alpha": 0.0,
        "motion_adapter_skip": 0,
    }
    obj._controls._server_ready = True
    obj._count_blackhole_chips = MagicMock(return_value=1)
    obj._running_model = "Wan2.2"
    # Simulate having already dispatched through _begin_create_job.
    obj._create_job_active = True
    fake_create_view._result_panel.show_pending("prompt", _VIDEO_MEDIUM)

    obj._on_generate("prompt", "", 20, -1, model_source="video",
                      video_model_key="animatediff",
                      animatediff_args=animatediff_args)

    assert obj._create_job_active is False
    assert fake_create_view._result_panel.calls[-1][0] == "show_error"


def test_early_return_does_not_bleed_state_into_next_non_create_job(monkeypatch):
    """Regression: after a Create job dies on an early return (and gets
    cleaned up by `_fail_create_job`), the NEXT unrelated non-Create job must
    behave completely normally — its own gallery pending card added, and the
    (stale) Create panel left untouched. Proves no window-global state-bleed
    survives the failed Create job."""
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._check_disk_space = MagicMock(return_value=False)

    # First: a Create job that dies on the disk-space guard.
    obj._on_create_generate(_IMAGE_MEDIUM, {"prompt": "a lighthouse at dawn",
                                             "model": "flux.1-schnell"})
    assert obj._create_job_active is False
    calls_after_failure = list(fake_create_view._result_panel.calls)

    # Disk space recovers; an unrelated (non-Create) job runs next — e.g. the
    # attractor/TT-TV loop or the manual queue, neither of which ever touches
    # `_create_job_active`.
    obj._check_disk_space = MagicMock(return_value=True)
    obj._on_generate("unrelated attractor prompt", "", 20, -1,
                     model_source="image", model_id="flux")

    # The non-Create job got its own gallery pending card, exactly as today...
    assert len(fake_gallery.add_pending_calls) == 1
    # ...and the panel was NOT touched again by it — no state bled forward.
    assert fake_create_view._result_panel.calls == calls_after_failure


# ── Whole-slice review fix: three more `_create_job_active` lifecycle gaps ──
#
# FINDING A (Important) — a second Create click while a job is already
# running (the Create CTA is never disabled mid-generation) would re-enter
# `_on_create_generate` -> `_begin_create_job`, overwriting the FIRST job's
# pending display, and if the second call's dispatch then hit
# `_on_generate`'s worker-busy guard, `_fail_create_job` would clear
# `_create_job_active` out from under the still-running first job — so the
# first job's own `_on_finished` would see the flag already False and never
# forward to the panel. Fixed by a re-entrancy guard at the very top of
# `_on_create_generate`.
#
# FINDING B (Important) — `_on_create_generate`'s `except Exception` ran
# AFTER `_begin_create_job` already set the flag + shown "pending", but only
# set a status message — leaving the flag stuck True (and the panel stuck
# "Generating…") on any synchronous dispatch exception. Fixed by calling
# `_fail_create_job` in that except.
#
# FINDING C (Minor) — `_create_generate_artgen`'s `if not generator: return`
# also ran after `_begin_create_job`, without clearing the flag. Unreachable
# today (discover_mediums always sets a generator) but fixed for the same
# "every terminal path clears the flag" consistency.

def test_reentrant_create_call_while_job_active_enqueues_native_medium(monkeypatch):
    """SP-3c-4: a second Create click for a NATIVE medium while
    `_create_job_active` is already True must ENQUEUE — not no-op. No second
    `_begin_create_job`/pending display (the running job's own panel state
    is untouched), the flag is left alone (still True — it belongs to the
    FIRST, still-running job), and no `_create_generate_native`/`_on_generate`
    dispatch happens — the job goes onto `self._queue` instead."""
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._create_job_active = True  # simulates a first job already in flight
    obj._create_generate_native = MagicMock()  # spy: the "generate now" path must never run

    obj._on_create_generate(_IMAGE_MEDIUM, {"prompt": "second click",
                                             "model": "flux.1-schnell"})

    obj._create_generate_native.assert_not_called()
    # No second show_pending (or any other panel call) — the first job's
    # pending display (whatever it was) is untouched by this call.
    assert fake_create_view._result_panel.calls == []
    # The flag is exactly as it was — still True, i.e. NOT cleared out from
    # under the (simulated) still-running first job.
    assert obj._create_job_active is True
    # The job landed on the queue instead of being dropped.
    assert len(obj._queue) == 1
    assert obj._queue[0].prompt == "second click"
    obj._set_status.assert_called_with("Added to queue (1 item queued)")


# ── SP-3c-4: generation queue in Create (task-4-brief.md) ───────────────────
#
# When `_create_job_active`, a native medium's Create click now enqueues via
# the SAME `_QueueItem`/`_on_enqueue`/`_persist_queue` machinery the legacy
# ControlPanel's own Generate-when-busy button already uses (see
# `_on_action_clicked`'s `self._on_enqueue(*args, **model_kwargs)` branch),
# instead of the SP-3-era no-op. `_create_enqueue_native` builds the exact
# same args/kwargs `_create_generate_native` would have passed to
# `_on_generate` (via the shared `_native_generate_args` helper) so
# `_start_next_queued` later replays the job faithfully.

def test_create_while_busy_enqueues_image_job_with_faithful_params(monkeypatch):
    obj, _fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._create_job_active = True

    obj._on_create_generate(_IMAGE_MEDIUM, {
        "prompt": "a lighthouse at dawn",
        "negative_prompt": "blurry",
        "num_inference_steps": 30,
        "seed": 42,
        "guidance_scale": 4.2,
        "model": "motif-image-6b-preview",
        "seed_image_path": "/tmp/a-seed.png",
    })

    assert len(obj._queue) == 1
    item = obj._queue[0]
    assert item.prompt == "a lighthouse at dawn"
    assert item.negative_prompt == "blurry"
    assert item.steps == 30
    assert item.seed == 42
    assert item.model_source == "image"
    assert item.guidance_scale == 4.2
    assert item.seed_image_path == "/tmp/a-seed.png"
    # canonical "motif-image-6b-preview" -> short key "motif", same inverse
    # map `_create_generate_native` uses.
    assert item.model_id == "motif"
    assert item.image_model_key == "motif"
    # Create's own pending-queue display (in the result pane) was refreshed
    # with the post-enqueue queue.
    fake_create_view.refresh_queue.assert_called_once()
    (pushed_items, on_cancel), _kwargs = fake_create_view.refresh_queue.call_args
    assert pushed_items == obj._queue
    assert on_cancel is obj._on_queue_remove


def test_create_while_busy_enqueues_video_animatediff_job_with_complete_args(monkeypatch):
    """Faithful replay must cover the SP-3c-2 native AnimateDiff case too —
    the queued item's `animatediff_args` must be the SAME complete dict
    `_create_generate_native` would have forwarded to `_on_generate`."""
    import main_window as mw

    obj, _fake_gallery, _fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._create_job_active = True

    obj._on_create_generate(_VIDEO_MEDIUM, {
        "prompt": "a glitchy dance loop",
        "model": "animatediff-blackhole",
        "num_inference_steps": 20,
        "seed": -1,
        "animatediff_args": {"temporal_alpha": 0.9},
    })

    assert len(obj._queue) == 1
    item = obj._queue[0]
    assert item.model_source == "video"
    assert item.video_model_key == "animatediff"
    assert item.model_id == "animatediff"
    assert set(item.animatediff_args) == set(mw._ANIMATEDIFF_DEFAULTS)
    assert item.animatediff_args["temporal_alpha"] == 0.9


def test_create_while_busy_skyreels_guard_blocks_enqueue_without_touching_active_job(monkeypatch):
    """The SkyReels-I2V "no seed image" guard must still fire on the busy
    (enqueue) path — but unlike the not-busy path, it must NOT call
    `_fail_create_job`: that would wrongly clear the flag/show an error in
    the panel for the FIRST job, which is still running and has nothing to
    do with this rejected enqueue attempt."""
    obj, _fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._create_job_active = True

    obj._on_create_generate(_VIDEO_MEDIUM, {
        "prompt": "a dancer",
        "model": "skyreels-v2-i2v-14b-540p",
        # no seed_image_path
    })

    assert obj._queue == []
    assert obj._create_job_active is True  # untouched — still the first job's flag
    assert fake_create_view._result_panel.calls == []  # no show_error from _fail_create_job
    obj._set_status.assert_called_with(
        "SkyReels I2V requires a starting image — add one to the "
        "seed image well before generating."
    )


def test_create_while_busy_animate_medium_enqueues_too(monkeypatch):
    """The Animate medium's translation (no negative_prompt, ref paths) must
    enqueue faithfully as well — not just image/video."""
    obj, _fake_gallery, _fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._create_job_active = True

    obj._on_create_generate(_ANIMATE_MEDIUM, {
        "prompt": "dance like nobody's watching",
        "num_inference_steps": 18,
        "seed": 5,
        "reference_video_path": "/tmp/motion.mp4",
        "reference_image_path": "/tmp/char.png",
        "animate_mode": "replacement",
    })

    assert len(obj._queue) == 1
    item = obj._queue[0]
    assert item.model_source == "animate"
    assert item.negative_prompt == ""
    assert item.ref_video_path == "/tmp/motion.mp4"
    assert item.ref_char_path == "/tmp/char.png"
    assert item.animate_mode == "replacement"


def test_create_while_busy_artgen_medium_shows_status_not_enqueue(monkeypatch):
    """Artgen mediums have no `_QueueItem` equivalent (their generation path
    shells out to `tt-ctl`, never through `_on_generate`/`self._queue`) — a
    second artgen click while busy must still surface an informative status
    message rather than silently enqueuing nothing or re-entering
    `_begin_create_job` (which would clobber the running job's panel)."""
    obj, _fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._create_job_active = True
    obj._create_generate_artgen = MagicMock()

    obj._on_create_generate(_VERSE_MEDIUM, {"prompt": "winter forges"})

    obj._create_generate_artgen.assert_not_called()
    assert obj._queue == []
    assert fake_create_view._result_panel.calls == []
    assert obj._create_job_active is True
    obj._set_status.assert_called_with(
        "A generation is already running — Verse can't be queued yet; "
        "try again once it finishes."
    )


def test_queue_cancel_removes_item_and_refreshes_create_display(monkeypatch):
    """The cancel callback CreateView's queue rows call
    (`fake_create_view.refresh_queue`'s second arg, `_on_queue_remove`) must
    pop the right item, persist, and push the updated (now-empty) queue back
    into CreateView."""
    obj, _fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._create_job_active = True
    obj._on_create_generate(_IMAGE_MEDIUM, {"prompt": "first", "model": "flux.1-schnell"})
    obj._on_create_generate(_IMAGE_MEDIUM, {"prompt": "second", "model": "flux.1-schnell"})
    assert len(obj._queue) == 2

    # The cancel callback CreateView was handed (identical every call).
    on_cancel = fake_create_view.refresh_queue.call_args.args[1]
    on_cancel(0)  # cancel the FIRST queued item ("first")

    assert len(obj._queue) == 1
    assert obj._queue[0].prompt == "second"
    # Refreshed again after the cancel, now with just the remaining item.
    last_pushed = fake_create_view.refresh_queue.call_args.args[0]
    assert [i.prompt for i in last_pushed] == ["second"]


def test_faithful_replay_drains_enqueued_native_job_into_on_generate(monkeypatch):
    """The whole point of SP-3c-4: an enqueued Create job, once drained by
    `_start_next_queued`, must call `_on_generate` with the SAME args/kwargs
    `_create_generate_native` would have used immediately — model/seed-image/
    animatediff_args all faithfully replayed, not just the prompt."""
    import main_window as mw

    obj, _fake_gallery, _fake_create_view = _make_mw_lifecycle(monkeypatch)
    obj._create_job_active = True  # a first job is "running"

    obj._on_create_generate(_VIDEO_MEDIUM, {
        "prompt": "a train through the mountains",
        "negative_prompt": "static",
        "num_inference_steps": 25,
        "seed": 7,
        "model": "skyreels-v2-i2v-14b-540p",
        "seed_image_path": "/tmp/character.png",
    })
    assert len(obj._queue) == 1

    # The first job "finishes": _on_finished/_on_error would normally clear
    # this and null out the worker — simulate that directly.
    obj._create_job_active = False
    obj._worker = None
    obj._on_generate = MagicMock()  # swap the real bound method for a spy
    obj._start_next_queued = getattr(mw.MainWindow, "_start_next_queued").__get__(obj)

    obj._start_next_queued()

    obj._on_generate.assert_called_once()
    args, kwargs = obj._on_generate.call_args
    # `_start_next_queued` replays every `_QueueItem` field POSITIONALLY
    # (except the three model-selection kwargs) — mirrors `_on_generate`'s
    # own positional signature (prompt, neg, steps, seed, seed_image_path,
    # model_source, guidance_scale, ref_video_path, ref_char_path,
    # animate_mode, model_id).
    assert args[0] == "a train through the mountains"
    assert args[1] == "static"
    assert args[2] == 25
    assert args[3] == 7
    assert args[4] == "/tmp/character.png"   # seed_image_path
    assert args[5] == "video"                # model_source
    assert args[10] == "skyreels"            # model_id
    assert kwargs["video_model_key"] == "skyreels"
    # Queue is empty again after the drain.
    assert obj._queue == []


def test_dispatch_exception_clears_create_job_and_shows_error(monkeypatch):
    """A synchronous exception during dispatch (worker constructor raising,
    a bad int()/float() param parse, etc.) must not leave the flag stuck
    True / the panel stuck "pending" — `_fail_create_job` must run in the
    `except` clause."""
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)

    def _boom(_medium, _params):
        raise RuntimeError("worker constructor exploded")

    obj._create_generate_native = _boom

    obj._on_create_generate(_IMAGE_MEDIUM, {"prompt": "a lighthouse at dawn",
                                             "model": "flux.1-schnell"})

    assert obj._create_job_active is False
    assert fake_create_view._result_panel.calls[-1][0] == "show_error"
    # The existing status-bar message is still set (unchanged behavior).
    obj._set_status.assert_called_with(
        "Couldn't start generation: worker constructor exploded"
    )


def test_artgen_no_generator_clears_create_job_and_shows_error(monkeypatch):
    """The artgen "no generator mapped" terminal path must also clear the
    flag it set via `_begin_create_job`, even though it's unreachable via
    `discover_mediums` today."""
    obj, fake_gallery, fake_create_view = _make_mw_lifecycle(monkeypatch)
    bad_medium = Medium(id="mystery", label="Mystery", icon="?", kind="text",
                        source="artgen", generator=None)

    obj._on_create_generate(bad_medium, {})

    assert obj._create_job_active is False
    assert fake_create_view._result_panel.calls[-1][0] == "show_error"
    obj._set_status.assert_called_with("No artgen generator mapped for Mystery.")


# ── In-place Create results: artgen generation lifecycle → result panel ─────
#
# (task-4-brief.md) A Create-originated artgen (verse/ansi/landscape/…) job
# must drive `self._create_view._result_panel` through pending -> finished |
# error too, exactly like the native path (task-3) — `_begin_create_job`
# already shows "pending" for BOTH branches, so before this task the artgen
# panel state never resolved and `_create_job_active` stayed stuck True.
# `_create_generate_artgen`'s subprocess + disk + sqlite I/O must still reach
# the media store / Artgen gallery exactly as before (persistence unaffected
# — the panel forwarding is additive).

def _make_mw_artgen_lifecycle(monkeypatch):
    """Like `_make_mw` (the artgen-dispatch tests above) but ALSO wires the
    Create-panel/flag plumbing (`_create_view`, `_create_job_active`,
    `_begin_create_job`, `_fail_create_job`, `_on_create_artgen_finished`) so
    the panel-forwarding half of the artgen lifecycle can be exercised too.

    `threading.Thread` is stubbed with `_ImmediateThread` (runs its target
    synchronously) rather than `_NoOpThread` (used by the native lifecycle
    harness above): unlike `_on_generate`'s worker, which calls a REAL
    `GenerationWorker.run_with_callbacks` that would attempt a real HTTP
    request, the artgen worker closure's entire body is made mock-safe by
    `_patch_artgen_deps` (subprocess/disk/sqlite all patched), so it's safe —
    and necessary, since that's where the record actually gets built — to
    let it run inline.
    """
    import main_window as mw

    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)

    obj._set_status = MagicMock()
    obj._artgen_panel = None
    obj._controls = MagicMock()
    obj._create_job_active = False
    fake_create_view = _FakeCreateView()
    obj._create_view = fake_create_view

    for name in (
        "_on_create_generate",
        "_create_generate_artgen",
        "_on_create_artgen_done",
        "_on_create_artgen_error",
        "_on_create_artgen_finished",
        "_begin_create_job",
        "_fail_create_job",
    ):
        setattr(obj, name, getattr(mw.MainWindow, name).__get__(obj))

    monkeypatch.setattr(mw.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(mw.GLib, "idle_add", lambda fn, *a: fn(*a))

    return obj, fake_create_view


def test_artgen_create_shows_pending_then_finished(monkeypatch, tmp_path):
    """Success path: the panel sees show_pending (from `_begin_create_job`,
    called before dispatch) then show_finished with the SAME record that was
    written to the media store, and the flag is cleared."""
    obj, fake_create_view = _make_mw_artgen_lifecycle(monkeypatch)
    fake_ms, _spy, out_path = _patch_artgen_deps(monkeypatch, tmp_path=tmp_path)

    obj._on_create_generate(_VERSE_MEDIUM, {"prompt": "winter forges", "form": "haiku"})

    method_names = [c[0] for c in fake_create_view._result_panel.calls]
    assert method_names == ["show_pending", "show_finished"]

    fake_ms.add.assert_called_once()
    (written_rec,), _kwargs = fake_ms.add.call_args
    finished_rec = fake_create_view._result_panel.calls[-1][1]
    # The panel got the very same record object the media store wrote.
    assert finished_rec is written_rec
    assert finished_rec.file_path == str(out_path)
    # Duck-typed alias `_build_artifact_widget` (create_view.py) actually
    # reads — without it the panel would always show its "not found"
    # placeholder even though the artifact exists at `file_path`.
    assert finished_rec.media_file_path == str(out_path)

    assert obj._create_job_active is False


def test_artgen_create_failure_shows_error_in_panel(monkeypatch, tmp_path):
    """Failure path: a raising `tt-ctl` subprocess must surface as show_error
    in the panel (via `_fail_create_job`) and clear the flag — never leave it
    stuck True — while still keeping the existing status-bar message and
    never writing a (missing/invalid) record to the media store."""
    obj, fake_create_view = _make_mw_artgen_lifecycle(monkeypatch)
    failing_spy = MagicMock(side_effect=RuntimeError("tt-ctl artgen verse failed (exit 1)"))
    fake_ms, _spy, _out_path = _patch_artgen_deps(
        monkeypatch, run_tt_ctl=failing_spy, tmp_path=tmp_path
    )

    obj._on_create_generate(_VERSE_MEDIUM, {"prompt": "winter forges"})

    method_names = [c[0] for c in fake_create_view._result_panel.calls]
    assert method_names == ["show_pending", "show_error"]
    assert obj._create_job_active is False
    fake_ms.add.assert_not_called()
    # The existing status-bar message is still set (unchanged behavior).
    last_status = obj._set_status.call_args[0][0]
    assert "Couldn't generate Verse" in last_status


def test_artgen_create_panel_error_does_not_leave_flag_stuck(monkeypatch, tmp_path):
    """A raising `show_finished` must not prevent the flag from clearing —
    mirrors the native path's `test_panel_error_never_blocks_generation`."""
    obj, fake_create_view = _make_mw_artgen_lifecycle(monkeypatch)
    _patch_artgen_deps(monkeypatch, tmp_path=tmp_path)

    def _boom(_record):
        raise RuntimeError("panel exploded")
    fake_create_view._result_panel.show_finished = _boom

    obj._on_create_generate(_VERSE_MEDIUM, {"prompt": "winter forges"})

    assert obj._create_job_active is False
