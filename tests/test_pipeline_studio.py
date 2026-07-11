"""GTK widget tests for Pipeline Studio's Discover view (SP-C Phase 1, Task 3).

Creating GTK widgets needs a display; the full suite runs under xvfb. When no
display is available this module skips itself (matches the repo's headless
fallback for GTK-widget tests, see test_artgen_panel_codeart.py).

DiscoverView is unit-tested directly with hand-built RunView fixtures — it
never talks to PipelineStore itself (see pipeline_studio.py docstring), so no
filesystem/store setup is needed for those tests. The one PipelineStudio shell
test below monkeypatches pipeline_store's paths so the background run-loading
thread it starts on construction never touches the real user's history.
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

from intent_vocab import intent_for
from pipeline_view_model import RunView, StepView


def _make_run(run_id: str = "run-1", title: str = "1964 World's Fair") -> RunView:
    """Build a RunView fixture directly from the dataclasses (no store/spec needed)."""
    class_types = ["TTLGTextToImage", "TTLGCaptionImage", "TTLGImageToVideo"]
    steps = [
        StepView(node_id=str(i), intent=intent_for(ct), status="done", artifact_path=None)
        for i, ct in enumerate(class_types, start=1)
    ]
    recipe = [f"{s.intent.verb} {s.intent.noun}" for s in steps]
    return RunView(
        run_id=run_id,
        title=title,
        created_at="2026-07-10T12:00:00+00:00",
        hero_path=None,  # no on-disk thumbnail needed for these tests
        steps=steps,
        recipe=recipe,
    )


# ── DiscoverView ───────────────────────────────────────────────────────────────

def test_discover_view_constructs():
    from pipeline_studio import DiscoverView
    view = DiscoverView()
    assert isinstance(view, Gtk.Box)


def test_set_runs_empty_list_no_crash():
    """Empty list -> friendly empty state, no exception."""
    from pipeline_studio import DiscoverView
    view = DiscoverView()
    view.set_runs([])


def test_set_runs_populates_hero_title_and_recipe():
    from pipeline_studio import DiscoverView
    view = DiscoverView()
    run = _make_run(run_id="run-hero", title="1964 World's Fair")
    view.set_runs([run])

    assert view._hero_title.get_label() == run.title
    chip_texts = [lbl.get_label() for lbl in view._hero_recipe_labels]
    assert chip_texts == run.recipe


def test_open_run_signal_emitted_from_hero_open_button():
    from pipeline_studio import DiscoverView
    view = DiscoverView()
    run = _make_run(run_id="run-42")
    view.set_runs([run])

    received = []
    view.connect("open-run", lambda _w, rid: received.append(rid))
    view._hero_open_btn.emit("clicked")

    assert received == ["run-42"]


def test_set_runs_builds_grid_cards_for_remaining_runs():
    """First run becomes the hero; the rest get grid cards with Open buttons."""
    from pipeline_studio import DiscoverView
    view = DiscoverView()
    runs = [_make_run("run-1", "First"), _make_run("run-2", "Second"), _make_run("run-3", "Third")]
    view.set_runs(runs)

    assert set(view._card_open_buttons.keys()) == {"run-2", "run-3"}


def test_open_run_signal_emitted_from_grid_card():
    from pipeline_studio import DiscoverView
    view = DiscoverView()
    runs = [_make_run("run-1", "First"), _make_run("run-2", "Second")]
    view.set_runs(runs)

    received = []
    view.connect("open-run", lambda _w, rid: received.append(rid))
    view._card_open_buttons["run-2"].emit("clicked")

    assert received == ["run-2"]


def test_set_runs_can_be_called_again_to_refresh():
    """Calling set_runs a second time rebuilds cleanly (no leftover widgets/handlers)."""
    from pipeline_studio import DiscoverView
    view = DiscoverView()
    view.set_runs([_make_run("run-1", "First")])
    view.set_runs([_make_run("run-2", "Second"), _make_run("run-3", "Third")])

    assert view._hero_title.get_label() == "Second"
    assert set(view._card_open_buttons.keys()) == {"run-3"}


# ── PipelineStudio shell ────────────────────────────────────────────────────────

def test_pipeline_studio_shell_stack_children(tmp_path, monkeypatch):
    """Stack has discover + open children, discover shown by default.

    Monkeypatches pipeline_store's index/runs paths into tmp_path so the
    background loading thread PipelineStudio kicks off on construction reads
    an empty, throwaway store instead of the real user's pipeline history.
    """
    import pipeline_store
    monkeypatch.setattr(pipeline_store, "_INDEX_PATH", tmp_path / "pipeline-index.json")
    monkeypatch.setattr(pipeline_store, "_RUNS_DIR", tmp_path / "runs")

    from pipeline_studio import PipelineStudio
    studio = PipelineStudio()

    assert studio.stack.get_child_by_name("discover") is not None
    assert studio.stack.get_child_by_name("open") is not None
    assert studio.stack.get_visible_child_name() == "discover"


# ── OpenView ─────────────────────────────────────────────────────────────────

_FIXTURE_PNG = str(Path(__file__).parent / "fixtures" / "sp_c_run" / "node1_image.png")


def _make_run_with_artifact() -> RunView:
    """RunView with one 'done' step carrying a real artifact + one 'pending' step."""
    done = StepView(node_id="1", intent=intent_for("TTLGTextToImage"), status="done",
                    artifact_path=_FIXTURE_PNG)
    pending = StepView(node_id="2", intent=intent_for("TTLGImageToVideo"), status="pending",
                        artifact_path=None)
    steps = [done, pending]
    recipe = [f"{s.intent.verb} {s.intent.noun}" for s in steps]
    return RunView(
        run_id="run-open-1",
        title="1964 World's Fair",
        created_at="2026-07-10T12:00:00+00:00",
        hero_path=_FIXTURE_PNG,
        steps=steps,
        recipe=recipe,
    )


def test_open_view_constructs():
    from pipeline_studio import OpenView
    view = OpenView()
    assert isinstance(view, Gtk.Box)


def test_open_view_set_run_renders_one_row_per_step_in_order():
    from pipeline_studio import OpenView
    view = OpenView()
    run = _make_run_with_artifact()
    view.set_run(run)

    assert list(view._step_remix_buttons.keys()) == ["1", "2"]
    assert view._title_label.get_label() == run.title


def test_open_view_done_step_has_thumbnail_pending_shows_placeholder():
    from pipeline_studio import OpenView
    view = OpenView()
    view.set_run(_make_run_with_artifact())

    done_frame = view._step_thumb_frames["1"]
    assert isinstance(done_frame.get_first_child(), Gtk.Picture)

    pending_frame = view._step_thumb_frames["2"]
    assert isinstance(pending_frame.get_first_child(), Gtk.Label)


def test_open_view_set_run_can_be_called_again_to_refresh():
    from pipeline_studio import OpenView
    view = OpenView()
    view.set_run(_make_run_with_artifact())
    view.set_run(_make_run_with_artifact())

    # Repeat-safe: no leftover widgets/handlers from the first call.
    assert list(view._step_remix_buttons.keys()) == ["1", "2"]


def test_open_view_set_run_zero_steps_no_crash():
    from pipeline_studio import OpenView
    view = OpenView()
    run = RunView(run_id="empty", title="Empty run", created_at="2026-07-10T12:00:00+00:00",
                  hero_path=None, steps=[], recipe=[])
    view.set_run(run)
    assert view._step_remix_buttons == {}


def test_open_view_remix_from_here_emits_node_id():
    from pipeline_studio import OpenView
    view = OpenView()
    view.set_run(_make_run_with_artifact())

    received = []
    view.connect("remix-request", lambda _w, node_id: received.append(node_id))
    view._step_remix_buttons["2"].emit("clicked")

    assert received == ["2"]


def test_open_view_remix_whole_pipeline_emits_empty_string():
    from pipeline_studio import OpenView
    view = OpenView()
    view.set_run(_make_run_with_artifact())

    received = []
    view.connect("remix-request", lambda _w, node_id: received.append(node_id))
    view._remix_all_btn.emit("clicked")

    assert received == [""]


# ── PipelineStudio: open-run wiring ──────────────────────────────────────────

def test_pipeline_studio_open_run_switches_stack_to_open(monkeypatch):
    """Driving DiscoverView's open-run handler synchronously loads + shows the run.

    Monkeypatches threading.Thread to run its target immediately (no real
    background thread) and GLib.idle_add to call its callback immediately, so
    the test doesn't race a daemon thread — per the brief's suggestion to
    drive the load synchronously instead of depending on thread timing.
    """
    import pipeline_studio

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    class _FakeStore:
        def get_run(self, run_id):
            return {"id": run_id}  # opaque; build_run_view is stubbed below

    monkeypatch.setattr(pipeline_studio.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(pipeline_studio.GLib, "idle_add", lambda fn, *a: fn(*a))
    monkeypatch.setattr(pipeline_studio, "PipelineStore", _FakeStore)
    monkeypatch.setattr(pipeline_studio, "build_run_view",
                         lambda _record: _make_run_with_artifact())

    from pipeline_studio import PipelineStudio
    studio = PipelineStudio()

    studio._on_open_run(studio.discover, "run-open-1")

    assert studio.stack.get_visible_child_name() == "open"
    assert studio.open_view._title_label.get_label() == "1964 World's Fair"
