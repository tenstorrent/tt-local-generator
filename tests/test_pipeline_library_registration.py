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


@pytest.mark.parametrize("ext", [".svg", ".ans", ".json", ".py", ".md"])
def test_other_artgen_kinds_also_register_media_record(tmp_path, ext):
    obj = _make_mw()
    run_view = _run_with_final(tmp_path, ext, run_id=f"run-{ext}")

    fake_media_store = MagicMock()
    with patch("media_store.media_store", fake_media_store):
        obj._register_pipeline_final(run_view)

    fake_media_store.add.assert_called_once()


# ── raster/mp4 final -> history_store.GenerationRecord + gallery ────────────

def test_png_final_registers_generation_record_via_gallery(tmp_path):
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


def test_mp4_final_registers_video_generation_record(tmp_path):
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


def test_registration_error_never_raises(tmp_path):
    """Any exception while building/writing the record is swallowed — a
    failed registration must never break the run-done view."""
    obj = _make_mw()
    obj._gallery_for_type = MagicMock(side_effect=RuntimeError("boom"))
    run_view = _run_with_final(tmp_path, ".png")

    obj._register_pipeline_final(run_view)  # must not raise


# ── register-once-per-run ────────────────────────────────────────────────────

def test_registers_only_once_per_run_id(tmp_path):
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
