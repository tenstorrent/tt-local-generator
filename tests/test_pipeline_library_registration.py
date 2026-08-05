"""Tests for Task 8: registering a completed pipeline run's final deliverable
into the Library (`MainWindow._register_pipeline_final`), and the
`PipelineStudio(on_run_complete=...)` wiring that invokes it on run-done.

Two seams are exercised:

1. `MainWindow._register_pipeline_final(run_view)` — pure classify/build/
   persist logic, tested the same way `tests/test_main_window_pipelines.py`
   tests other MainWindow seams: a bare `MainWindow.__new__` instance with
   only the collaborators the method actually touches (`_store`,
   `_gallery_for_type`, `_artgen_gallery`) hand-populated as mocks. This
   avoids constructing the full heavy `MainWindow` (network/disk-dependent).

2. `PipelineStudio._on_run_done` calling the `on_run_complete` callback with
   the freshly-built `RunView` — exercised the same way
   `tests/test_pipeline_studio.py::test_pipeline_studio_run_done_returns_to_open_with_fresh_run`
   does (an `_ImmediateThread` stand-in + a synchronous `GLib.idle_add` fake so
   the background-thread/idle_add plumbing runs inline for the test).

Reused store seams (never invented): `history_store.GenerationRecord` +
`GalleryWidget.replace_pending_with` (mirrors `MainWindow._on_finished`) for
raster/mp4 finals; `media_store.MediaRecord` + `media_store.media_store.add`/
`ensure_auto_playlists` (mirrors `_create_generate_artgen` and the
`ansi-image` transform branch in `_run_transform`, see
`tests/test_forge_transforms.py::test_run_transform_ansi_image_creates_artgen_record`
for the same `patch("media_store.media_store", ...)` convention followed here)
for artgen-kind finals.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)

import main_window as mw
import pipeline_view_model as vm
from intent_vocab import intent_for


def _run_with_final(tmp_path, ext, class_type="TTLGArtgenGenerate", run_id="run-1"):
    """Build a minimal RunView whose single step's artifact is the hero/final."""
    art = tmp_path / f"final{ext}"
    art.write_bytes(b"x")
    step = vm.StepView(
        node_id="2", intent=intent_for(class_type), status="done",
        artifact_path=str(art), artifact_paths=(str(art),),
    )
    return vm.RunView(
        run_id=run_id, title="A test run", created_at="2026-08-01T00:00:00+00:00",
        hero_path=str(art), steps=[step], recipe=["Make", "Generative art"],
    )


def _make_mw():
    """Bare MainWindow with only the collaborators _register_pipeline_final touches."""
    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)
    obj._store = MagicMock()
    obj._gallery_for_type = MagicMock(return_value=MagicMock())
    obj._artgen_gallery = MagicMock()
    return obj


class _ImmediateThread:
    """threading.Thread stand-in that runs its target synchronously on start()
    — same pattern `tests/test_main_window_create_generate.py` uses. Needed
    because whole-branch review Finding 2 moved
    `_register_pipeline_final_native`'s ffmpeg/record-build work onto a real
    background thread; these tests want that work to have already happened
    by the time they assert."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _patch_native_threading(monkeypatch):
    """Make `_register_pipeline_final_native`'s background thread + its
    `GLib.idle_add` tail-callback run inline, so tests can assert synchronously."""
    monkeypatch.setattr(mw.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(mw.GLib, "idle_add", lambda fn, *a: fn(*a))


# ── artgen-kind final (.gif) -> media_store.MediaRecord ──────────────────────

def test_gif_final_registers_media_record_with_pipeline_provenance(tmp_path):
    obj = _make_mw()
    run_view = _run_with_final(tmp_path, ".gif", class_type="TTLGAnimateDiff")

    fake_media_store = MagicMock()
    with patch("media_store.media_store", fake_media_store):
        obj._register_pipeline_final(run_view)

    fake_media_store.add.assert_called_once()
    rec = fake_media_store.add.call_args.args[0]
    assert rec.media_type == "artgen"
    assert rec.generator_type == "pipeline"
    assert rec.file_path == run_view.steps[0].artifact_path
    params = rec.params_dict
    assert params["_pipeline_run_id"] == "run-1"
    assert params["recipe"] == run_view.recipe
    fake_media_store.ensure_auto_playlists.assert_called_once()
    obj._artgen_gallery.refresh.assert_called_once()
    # Must NEVER touch the native store/gallery for an artgen-kind final.
    obj._store.append.assert_not_called()
    obj._gallery_for_type.assert_not_called()


@pytest.mark.parametrize("ext", [".svg", ".ans", ".json", ".py", ".md", ".webp"])
def test_other_artgen_kinds_also_register_media_record(tmp_path, ext):
    obj = _make_mw()
    run_view = _run_with_final(tmp_path, ext, run_id=f"run-{ext}")

    fake_media_store = MagicMock()
    with patch("media_store.media_store", fake_media_store):
        obj._register_pipeline_final(run_view)

    fake_media_store.add.assert_called_once()


# ── raster/mp4 final -> history_store.GenerationRecord + gallery ────────────

def test_png_final_registers_generation_record_via_gallery(tmp_path, monkeypatch):
    _patch_native_threading(monkeypatch)
    obj = _make_mw()
    run_view = _run_with_final(tmp_path, ".png", class_type="TTLGTextToImage")

    with patch("main_window.subprocess.run"):
        obj._register_pipeline_final(run_view)

    obj._store.append.assert_called_once()
    rec = obj._store.append.call_args.args[0]
    assert rec.media_type == "image"
    assert rec.image_path == run_view.steps[0].artifact_path

    obj._gallery_for_type.assert_called_once_with("image")
    gallery = obj._gallery_for_type.return_value
    gallery.replace_pending_with.assert_called_once_with(rec)

    # Must NEVER touch the artgen media store for a native raster/mp4 final.
    obj._artgen_gallery.refresh.assert_not_called()


def test_mp4_final_registers_video_generation_record(tmp_path, monkeypatch):
    _patch_native_threading(monkeypatch)
    obj = _make_mw()
    run_view = _run_with_final(tmp_path, ".mp4", class_type="TTLGImageToVideo")

    with patch("main_window.subprocess.run"):
        obj._register_pipeline_final(run_view)

    obj._store.append.assert_called_once()
    rec = obj._store.append.call_args.args[0]
    assert rec.media_type == "video"
    assert rec.video_path == run_view.steps[0].artifact_path

    obj._gallery_for_type.assert_called_once_with("video")
    gallery = obj._gallery_for_type.return_value
    gallery.replace_pending_with.assert_called_once_with(rec)


# ── fail-soft: no final / missing file / any registration error ─────────────

def test_no_final_step_registers_nothing(tmp_path):
    """hero_path is None (no heroable artifact) -> final_index_for returns
    None -> nothing is registered, no crash."""
    obj = _make_mw()
    run_view = vm.RunView(
        run_id="run-none", title="t", created_at="2026-08-01T00:00:00+00:00",
        hero_path=None, steps=[], recipe=[],
    )

    obj._register_pipeline_final(run_view)  # must not raise

    obj._store.append.assert_not_called()
    obj._gallery_for_type.assert_not_called()
    obj._artgen_gallery.refresh.assert_not_called()


def test_missing_final_artifact_file_registers_nothing(tmp_path):
    """artifact_path points at a file that no longer exists on disk."""
    obj = _make_mw()
    run_view = _run_with_final(tmp_path, ".png")
    # Delete the file after building the RunView so artifact_path is stale.
    Path(run_view.steps[0].artifact_path).unlink()

    obj._register_pipeline_final(run_view)  # must not raise

    obj._store.append.assert_not_called()
    obj._gallery_for_type.assert_not_called()


def test_failed_run_registers_nothing(tmp_path):
    """Whole-branch review Finding 4: `LiveRunView.on_finished` emits
    "run-done" on failure too, and registration must not treat the final
    step's leftover/stale artifact as a genuine deliverable when that step's
    own status is "failed" (not "done")."""
    obj = _make_mw()
    run_view = _run_with_final(tmp_path, ".png")
    run_view.steps[0].status = "failed"

    with patch("main_window.subprocess.run"):
        obj._register_pipeline_final(run_view)  # must not raise

    obj._store.append.assert_not_called()
    obj._gallery_for_type.assert_not_called()
    obj._artgen_gallery.refresh.assert_not_called()


def test_registration_error_never_raises(tmp_path, monkeypatch):
    """Any exception while building/writing the record is swallowed — a
    failed registration must never break the run-done view."""
    _patch_native_threading(monkeypatch)
    obj = _make_mw()
    obj._gallery_for_type = MagicMock(side_effect=RuntimeError("boom"))
    run_view = _run_with_final(tmp_path, ".png")

    obj._register_pipeline_final(run_view)  # must not raise


# ── register-once-per-run ────────────────────────────────────────────────────

def test_registers_only_once_per_run_id(tmp_path, monkeypatch):
    _patch_native_threading(monkeypatch)
    obj = _make_mw()
    run_view = _run_with_final(tmp_path, ".png")

    with patch("main_window.subprocess.run"):
        obj._register_pipeline_final(run_view)
        obj._register_pipeline_final(run_view)

    obj._store.append.assert_called_once()
    obj._gallery_for_type.assert_called_once()


# ── PipelineStudio(on_run_complete=...) wiring ───────────────────────────────

def test_pipeline_studio_invokes_on_run_complete_on_run_done(monkeypatch):
    """run-done fires the on_run_complete callback with the fresh RunView,
    mirroring test_pipeline_studio.py's own run-done wiring test."""
    import pipeline_studio
    from test_pipeline_studio import _ImmediateThread, _make_remix_run, _REMIX_SPEC_PATH

    monkeypatch.setattr(pipeline_studio.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(pipeline_studio.GLib, "idle_add", lambda fn, *a: fn(*a))

    finished_record = {
        "id": "run-finished-1",
        "spec_path": _REMIX_SPEC_PATH,
        "spec_name": "remix_fixture_spec",
        "output_dir": "",
        "job_states": {},
        "started_at": "2026-07-11T00:00:00+00:00",
    }
    mock_store_instance = MagicMock()
    mock_store_instance.get_run.return_value = finished_record
    monkeypatch.setattr(pipeline_studio, "PipelineStore", MagicMock(return_value=mock_store_instance))

    from pipeline_studio import PipelineStudio
    calls = []
    studio = PipelineStudio(on_run_complete=lambda rv: calls.append(rv))
    studio.stack.set_visible_child_name("run")
    studio.live_run.begin(_make_remix_run())

    studio._on_run_done(studio.live_run, "run-finished-1")

    assert len(calls) == 1
    assert calls[0].run_id == "run-finished-1"


def test_pipeline_studio_run_done_tolerates_no_on_run_complete(monkeypatch):
    """The callback is optional (default None) — run-done must not crash
    without one, matching every other optional-callback seam in this module."""
    import pipeline_studio
    from test_pipeline_studio import _ImmediateThread, _make_remix_run, _REMIX_SPEC_PATH

    monkeypatch.setattr(pipeline_studio.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(pipeline_studio.GLib, "idle_add", lambda fn, *a: fn(*a))

    finished_record = {
        "id": "run-finished-2",
        "spec_path": _REMIX_SPEC_PATH,
        "spec_name": "remix_fixture_spec",
        "output_dir": "",
        "job_states": {},
        "started_at": "2026-07-11T00:00:00+00:00",
    }
    mock_store_instance = MagicMock()
    mock_store_instance.get_run.return_value = finished_record
    monkeypatch.setattr(pipeline_studio, "PipelineStore", MagicMock(return_value=mock_store_instance))

    from pipeline_studio import PipelineStudio
    studio = PipelineStudio()
    studio.stack.set_visible_child_name("run")
    studio.live_run.begin(_make_remix_run())

    studio._on_run_done(studio.live_run, "run-finished-2")  # must not raise
    assert studio.stack.get_visible_child_name() == "open"


# ── Review fix: real build_run_view integration (closes the hand-built-RunView
#    bypass gap the coordinator's review found) ──────────────────────────────
#
# The 14 tests above all construct RunView/StepView by hand with hero_path
# pre-set — proving _register_pipeline_final's classify/build logic works,
# but never exercising pipeline_view_model.build_run_view's own hero_path
# SELECTION. That selection had a real bug: _HERO_KINDS only covered
# "image"/"video", so an AnimateDiff gif final (kind "gif") or a visual
# artgen final (svg/ansi/palette — kind "any" at the intent level, since
# every artgen generator shares TTLGArtgenGenerate's generic output) never
# became hero_path, so final_index_for always returned None and
# _register_pipeline_final registered nothing for either case — the marquee
# palette->AnimateDiff->GIF journey got no hero AND no Library entry. These
# tests go through the REAL build_run_view (not a hand-built RunView) to
# prove that gap is closed.

def _build_real_run_view(tmp_path, run_id: str, class_type: str, node_inputs: dict,
                          artifact_filename: str) -> vm.RunView:
    """Build a RunView via the REAL pipeline_view_model.build_run_view: writes
    a real one-node spec.json + output_dir artifact and loads them through
    load_spec/topo_order/build_run_view exactly like a genuine finished run,
    instead of hand-constructing StepView/RunView (which bypasses the very
    hero_path-selection logic this fix targets)."""
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({
        "1": {"class_type": class_type, "inputs": node_inputs},
    }))
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / artifact_filename).write_bytes(b"x")

    record = {
        "id": run_id,
        "spec_path": str(spec_path),
        "spec_name": "Integration run",
        "output_dir": str(output_dir),
        "job_states": {"job1": {"1": {"status": "done"}}},
    }
    return vm.build_run_view(record)


def test_animatediff_gif_final_gets_hero_and_registers_via_real_build_run_view(tmp_path):
    """AnimateDiff gif deliverable: build_run_view sets hero_path to the gif,
    final_index_for finds it, and _register_pipeline_final creates a
    media_store.MediaRecord(generator_type="pipeline") with the run id in
    params — going through every real seam, no hand-built RunView shortcut."""
    run_view = _build_real_run_view(
        tmp_path, "run-gif-int", "TTLGAnimateDiff", {"prompt": "a candle flame"},
        "node1.gif",
    )
    gif_path = str(tmp_path / "out" / "node1.gif")
    assert run_view.hero_path == gif_path
    assert vm.final_index_for(run_view) == 0

    obj = _make_mw()
    fake_media_store = MagicMock()
    with patch("media_store.media_store", fake_media_store):
        obj._register_pipeline_final(run_view)

    fake_media_store.add.assert_called_once()
    rec = fake_media_store.add.call_args.args[0]
    assert rec.generator_type == "pipeline"
    assert rec.media_type == "artgen"
    assert rec.file_path == gif_path
    assert rec.params_dict["_pipeline_run_id"] == "run-gif-int"
    fake_media_store.ensure_auto_playlists.assert_called_once()
    obj._artgen_gallery.refresh.assert_called_once()


@pytest.mark.parametrize("ext", [".svg", ".ans"])
def test_artgen_visual_final_gets_hero_and_registers_via_real_build_run_view(tmp_path, ext):
    """A generic TTLGArtgenGenerate visual final (svg/ansi — "any" kind at the
    intent level, distinguishable from verse/codeart's plain-text finals only
    by file extension via pipeline_view_model._ARTGEN_VISUAL_EXTS) also gets a
    real hero_path and a real MediaRecord registration."""
    run_view = _build_real_run_view(
        tmp_path, f"run-artgen-int-{ext.strip('.')}", "TTLGArtgenGenerate", {},
        f"node1_artifact{ext}",
    )
    artifact_path = str(tmp_path / "out" / f"node1_artifact{ext}")
    assert run_view.hero_path == artifact_path
    assert vm.final_index_for(run_view) == 0

    obj = _make_mw()
    fake_media_store = MagicMock()
    with patch("media_store.media_store", fake_media_store):
        obj._register_pipeline_final(run_view)

    fake_media_store.add.assert_called_once()
    rec = fake_media_store.add.call_args.args[0]
    assert rec.generator_type == "pipeline"
    assert rec.file_path == artifact_path
    assert rec.params_dict["_pipeline_run_id"] == f"run-artgen-int-{ext.strip('.')}"


def test_artgen_text_final_does_not_get_a_false_image_hero(tmp_path):
    """Guard for the flip side of the fix: a genuinely TEXTUAL artgen final
    (verse/freeform's .txt, kind "any" at the intent level) must NOT become
    the hero — only visual artgen kinds should. build_run_view's separate
    text-only-pipeline fallback doesn't apply here either (that only fires
    when artifact_path is None; verse writes a real file), so this run
    legitimately has no hero_path -- it must not silently become one via the
    "any" kind bypass this fix introduces."""
    run_view = _build_real_run_view(
        tmp_path, "run-text-int", "TTLGArtgenGenerate", {}, "node1_artifact.txt",
    )
    assert run_view.hero_path is None
    assert vm.final_index_for(run_view) is None
