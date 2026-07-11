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
