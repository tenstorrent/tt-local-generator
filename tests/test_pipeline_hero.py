# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Task 7 (final-result hero): pure unit tests for `pipeline_view_model.
final_index_for`.

`build_run_view` already computes `RunView.hero_path` (the last/topologically-
final heroable image/video artifact — the run's final deliverable) but
nothing ever read it — `final_index_for` promotes
it into "which STEP produced the hero", which OpenView uses to render a
"Here's what you made" hero instead of just another row. Pure/GTK-free, per
this module's existing discipline (see its module docstring).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pipeline_view_model as vm
from intent_vocab import intent_for


def _step(node, ct, path=None):
    return vm.StepView(node_id=node, intent=intent_for(ct), status="done",
                       artifact_path=path, artifact_paths=((path,) if path else ()))


def test_final_index_points_at_hero_artifact():
    steps = [_step("1", "TTLGPaletteToPrompt", None),
             _step("2", "TTLGAnimateDiff", "/out/node2_artifact.gif")]
    rv = vm.RunView(run_id="r", title="t", created_at="", recipe=[],
                    hero_path="/out/node2_artifact.gif", steps=steps)
    assert vm.final_index_for(rv) == 1   # 0-based index of the deliverable step


def test_final_index_none_when_no_hero():
    steps = [_step("1", "TTLGGenerateText", None)]
    rv = vm.RunView(run_id="r", title="t", created_at="", recipe=[],
                    hero_path=None, steps=steps)
    assert vm.final_index_for(rv) is None


def test_final_index_none_when_hero_path_matches_no_step():
    """A hero_path that doesn't belong to any current step (e.g. a stale
    value, or a step that was pruned) must not crash or false-match — None,
    not an accidental index."""
    steps = [_step("1", "TTLGTextToImage", "/out/node1_image.png")]
    rv = vm.RunView(run_id="r", title="t", created_at="", recipe=[],
                    hero_path="/out/node9_image.png", steps=steps)
    assert vm.final_index_for(rv) is None


def test_final_index_none_on_empty_steps():
    rv = vm.RunView(run_id="r", title="t", created_at="", recipe=[],
                    hero_path=None, steps=[])
    assert vm.final_index_for(rv) is None


# ── OpenView: hero rendering (GTK) ──────────────────────────────────────────
#
# Same headless-skip convention as test_pipeline_studio.py: creating GTK
# widgets needs a display; the full suite runs under xvfb.

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)


_FIXTURE_PNG = str(Path(__file__).parent / "fixtures" / "sp_c_run" / "node1_image.png")


def _run_with_image_hero() -> vm.RunView:
    """A finished run whose only artifact is heroable."""
    done = vm.StepView(node_id="1", intent=intent_for("TTLGTextToImage"), status="done",
                        artifact_path=_FIXTURE_PNG, artifact_paths=(_FIXTURE_PNG,))
    other = vm.StepView(node_id="2", intent=intent_for("TTLGCaptionImage"), status="done",
                         artifact_path=None, text_content="a caption")
    steps = [done, other]
    return vm.RunView(run_id="r-hero", title="Hero test", created_at="",
                       hero_path=_FIXTURE_PNG, steps=steps,
                       recipe=[f"{s.intent.verb} {s.intent.noun}" for s in steps])


def _run_text_only_finished() -> vm.RunView:
    """A finished, text-only run: no artifact anywhere, last step is text."""
    step = vm.StepView(node_id="1", intent=intent_for("TTLGGenerateText"), status="done",
                        artifact_path=None, text_content="A poem.")
    return vm.RunView(run_id="r-text", title="Text-only test", created_at="",
                       hero_path=None, steps=[step], recipe=["Write text"])


def test_open_view_promotes_image_hero_and_folds_rest_under_expander():
    from pipeline_studio import OpenView
    view = OpenView()
    view.set_run(_run_with_image_hero())

    assert view._hero_title_label is not None
    assert view._hero_title_label.get_label() == "Here's what you made"
    assert view._how_made_expander is not None
    assert view._how_made_expander.get_label() == "How it was made"
    assert view._how_made_expander.get_expanded() is False  # collapsed by default

    # Both steps still registered even though "1" no longer gets an ordinary
    # row — hero owns its dict entries instead.
    assert set(view._step_remix_buttons.keys()) == {"1", "2"}
    assert isinstance(view._step_thumb_frames["1"].get_first_child(), Gtk.Picture)
    assert view._step_text_blocks["2"].get_label() == "a caption"
    # The non-hero step is tagged inside the "How it was made" breakdown.
    assert view._step_kind_tags["2"].get_label() == "text"


def test_open_view_text_only_finished_run_uses_text_as_hero():
    from pipeline_studio import OpenView
    view = OpenView()
    view.set_run(_run_text_only_finished())

    assert view._hero_title_label is not None
    assert view._step_text_blocks["1"].get_label() == "A poem."
    # Whole-branch review Finding 5: the hero is the run's ONLY step, so
    # there is nothing left to fold under "How it was made" -- no expander
    # should be built at all (an empty-bodied one was dead, misleading UI).
    assert view._how_made_expander is None


def test_open_view_hero_fullscreen_button_reuses_open_fullscreen(monkeypatch):
    from pipeline_studio import OpenView
    view = OpenView()
    view.set_run(_run_with_image_hero())

    calls = []
    monkeypatch.setattr(view, "_open_fullscreen", lambda node_id: calls.append(node_id))
    view._hero_fullscreen_btn.emit("clicked")
    assert calls == ["1"]


def test_open_view_hero_remix_button_reuses_on_remix_clicked():
    from pipeline_studio import OpenView
    view = OpenView()
    view.set_run(_run_with_image_hero())

    received = []
    view.connect("remix-request", lambda _w, node_id: received.append(node_id))
    view._hero_remix_btn.emit("clicked")
    assert received == ["1"]


def test_open_view_fullscreen_opens_a_window_for_the_hero_artifact():
    from pipeline_studio import OpenView
    view = OpenView()
    view.set_run(_run_with_image_hero())

    view._open_fullscreen("1")
    assert view._fullscreen_window is not None
    assert isinstance(view._fullscreen_window, Gtk.Window)


def test_open_view_no_hero_renders_flat_list_as_before():
    """Regression guard: a run with no promoted hero (hero_path=None, not a
    finished text-only run either) must render exactly as before this task —
    flat rows, no expander, no hero widgets."""
    from pipeline_studio import OpenView
    step = vm.StepView(node_id="1", intent=intent_for("TTLGImageToVideo"),
                        status="pending", artifact_path=None)
    run = vm.RunView(run_id="r-flat", title="Flat test", created_at="",
                      hero_path=None, steps=[step], recipe=[])
    view = OpenView()
    view.set_run(run)

    assert view._hero_title_label is None
    assert view._how_made_expander is None
    assert list(view._step_remix_buttons.keys()) == ["1"]
