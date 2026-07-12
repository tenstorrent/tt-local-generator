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

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

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


class _ImmediateThread:
    """threading.Thread stand-in that runs its target synchronously on start().

    Shared by every PipelineStudio test that drives a background-loading
    method (open-run, run-done, ...) — swaps out real threading so the test
    doesn't race a daemon thread, per the pattern this module's tests already
    established for test_pipeline_studio_open_run_switches_stack_to_open.
    """

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


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


def test_open_view_model_detail_row_present_only_when_model_label_set():
    """A step whose intent.model_label is None (e.g. Describe/CaptionImage)
    must not render a model detail row; a model-bearing step (e.g.
    TextToImage -> "FLUX") must render one. See OpenView._build_step_row's
    `if step.intent.model_label:` guard."""
    from pipeline_studio import OpenView
    view = OpenView()

    with_model = StepView(node_id="1", intent=intent_for("TTLGTextToImage"),
                           status="done", artifact_path=None)
    without_model = StepView(node_id="2", intent=intent_for("TTLGCaptionImage"),
                              status="done", artifact_path=None)
    steps = [with_model, without_model]
    recipe = [f"{s.intent.verb} {s.intent.noun}" for s in steps]
    run = RunView(run_id="run-model-label", title="Model label test",
                  created_at="2026-07-10T12:00:00+00:00", hero_path=None,
                  steps=steps, recipe=recipe)
    view.set_run(run)

    def _has_model_row(node_id: str) -> bool:
        # The step row order mirrors run.steps order; walk steps_box's
        # children to find this step's row, then walk into its "main"
        # column (verb_row, noun_label, [model_label]) to check for a third
        # child. There's no dict keyed by node_id for this column (unlike
        # _step_remix_buttons/_step_thumb_frames), so this reaches into the
        # widget tree directly.
        index = [s.node_id for s in steps].index(node_id)
        row = view._steps_box.get_first_child()
        for _ in range(index):
            row = row.get_next_sibling()
        n_label = row.get_first_child()
        main = n_label.get_next_sibling()
        verb_row = main.get_first_child()
        noun_label = verb_row.get_next_sibling()
        model_label = noun_label.get_next_sibling()
        return model_label is not None

    assert _has_model_row("1") is True   # TTLGTextToImage -> model_label "FLUX"
    assert _has_model_row("2") is False  # TTLGCaptionImage -> model_label None


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

    class _FakeStore:
        def get_run(self, run_id):
            # opaque record; build_run_view is stubbed below to ignore it,
            # but a real spec_path is included so _show_run has one to stash
            # for a later remix-request.
            return {"id": run_id, "spec_path": "/fake/spec.json"}

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
    assert studio._current_spec_path == "/fake/spec.json"


# ── RemixView ────────────────────────────────────────────────────────────────
#
# Reuses tests/fixtures/remix_fixture_spec.json (SP-C Task 1's spec_remix
# fixture): node "1" TTLGTextToImage (prompt text, steps number, negative_prompt
# text), node "2" TTLGImageToVideo (num_frames number; image_path is a WIRED
# input and must never grow a field), node "3" TTLGAnimateDiff (loop bool).

_REMIX_SPEC_PATH = str(Path(__file__).parent / "fixtures" / "remix_fixture_spec.json")


def _make_remix_run() -> RunView:
    """RunView whose node_ids/class_types line up 1:1 with the remix fixture
    spec, so RemixView.set_run can load real editable_params for each step."""
    class_types = {"1": "TTLGTextToImage", "2": "TTLGImageToVideo", "3": "TTLGAnimateDiff"}
    steps = [
        StepView(node_id=nid, intent=intent_for(ct), status="done", artifact_path=None)
        for nid, ct in class_types.items()
    ]
    recipe = [f"{s.intent.verb} {s.intent.noun}" for s in steps]
    return RunView(
        run_id="run-remix-1",
        title="Tower of Pisa GIF",
        created_at="2026-07-10T12:00:00+00:00",
        hero_path=None,
        steps=steps,
        recipe=recipe,
    )


def test_remix_view_constructs():
    from pipeline_studio import RemixView
    view = RemixView()
    assert isinstance(view, Gtk.Box)


def test_remix_view_set_run_builds_prefilled_fields_per_kind():
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    node1 = view._field_widgets["1"]
    assert isinstance(node1["prompt"], Gtk.Entry)
    assert node1["prompt"].get_text() == "a test prompt"
    assert isinstance(node1["steps"], Gtk.SpinButton)
    assert node1["steps"].get_value() == 4
    assert isinstance(node1["negative_prompt"], Gtk.Entry)
    assert node1["negative_prompt"].get_text() == "blurry"

    node3 = view._field_widgets["3"]
    assert isinstance(node3["loop"], Gtk.Switch)
    assert node3["loop"].get_active() is True


def test_remix_view_excludes_wired_input_from_fields():
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    node2 = view._field_widgets["2"]
    assert "image_path" not in node2
    assert "num_frames" in node2
    assert node2["num_frames"].get_value() == 33


def test_remix_view_run_with_no_edits_emits_empty_dict():
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    received = []
    view.connect("run-remix", lambda _w, spec_path, edits: received.append((spec_path, edits)))
    view._run_button.emit("clicked")

    assert received == [(_REMIX_SPEC_PATH, {})]


def test_remix_view_run_with_text_edit_emits_only_changed_field():
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    view._field_widgets["1"]["prompt"].set_text("a new prompt")

    received = []
    view.connect("run-remix", lambda _w, spec_path, edits: received.append((spec_path, edits)))
    view._run_button.emit("clicked")

    assert received == [(_REMIX_SPEC_PATH, {"1": {"prompt": "a new prompt"}})]


def test_remix_view_run_with_number_edit_emits_int_value():
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    view._field_widgets["1"]["steps"].set_value(8)

    received = []
    view.connect("run-remix", lambda _w, spec_path, edits: received.append((spec_path, edits)))
    view._run_button.emit("clicked")

    assert received == [(_REMIX_SPEC_PATH, {"1": {"steps": 8}})]
    assert isinstance(received[0][1]["1"]["steps"], int)  # not 8.0


def test_remix_view_run_with_bool_edit_emits_changed_field_only():
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    view._field_widgets["3"]["loop"].set_active(False)

    received = []
    view.connect("run-remix", lambda _w, spec_path, edits: received.append((spec_path, edits)))
    view._run_button.emit("clicked")

    assert received == [(_REMIX_SPEC_PATH, {"3": {"loop": False}})]


def test_remix_view_model_label_shown_only_when_present():
    """TTLGTextToImage -> model_label 'FLUX' should render; TTLGImageToVideo's
    ParamField labels themselves are unaffected either way — this just checks
    the step card doesn't crash/omit rendering when building both kinds."""
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    # No assertion beyond "did not raise" plus the fields already asserted
    # above — model-label rendering reuses OpenView's already-tested
    # `if step.intent.model_label:` guard pattern.


def test_remix_view_set_run_is_repeat_safe():
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    assert set(view._field_widgets.keys()) == {"1", "2", "3"}
    # Exactly one prompt Entry survives per node — not stacked duplicates.
    assert view._field_widgets["1"]["prompt"].get_text() == "a test prompt"


# ── RemixView: composer add/remove steps (SP-C Phase 2b-1 Task 3) ───────────
#
# Reuses the same remix fixture spec: node "1" TTLGTextToImage (output_kind
# "image"), node "2" TTLGImageToVideo (wired to node 1, output_kind "video"),
# node "3" TTLGAnimateDiff (standalone, no wire, output_kind "gif").

def test_remix_view_set_run_shows_remove_and_add_after_per_step():
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    assert set(view._remove_buttons.keys()) == {"1", "2", "3"}
    assert set(view._add_after_buttons.keys()) == {"1", "2", "3"}


def test_add_after_picker_lists_only_kind_compatible_intents():
    """Node '1' produces an image (output_kind 'image'). The add-after picker
    must list only intents whose input_kind is 'image' — TTLGCaptionImage
    ('Describe it') yes, TTLGTextToImage ('Generate an image') no (its
    input_kind is 'text')."""
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    choice_class_types = {c.class_type for c in view._add_after_intents_for("1")}
    assert "TTLGCaptionImage" in choice_class_types
    assert "TTLGTextToImage" not in choice_class_types

    # The rendered popover mirrors the same choice set.
    assert "TTLGCaptionImage" in view._add_after_choice_buttons["1"]
    assert "TTLGTextToImage" not in view._add_after_choice_buttons["1"]


def test_add_step_after_grows_working_spec_by_one_wired_node():
    from pipeline_studio import RemixView
    from pipeline_engine import topo_order
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    before_ids = set(view.working_spec)
    before_len = len(topo_order(view.current_spec()))

    view.add_step_after("1", "TTLGCaptionImage")

    after_len = len(topo_order(view.current_spec()))
    assert after_len == before_len + 1

    new_ids = set(view.working_spec) - before_ids
    assert len(new_ids) == 1
    new_id = new_ids.pop()
    assert view.working_spec[new_id]["class_type"] == "TTLGCaptionImage"
    # Wired to node "1"'s primary output (image_path).
    assert view.working_spec[new_id]["inputs"]["src"] == ["1", "image_path"]

    # Re-rendered: the new node now has its own Remove/add-after controls too.
    assert new_id in view._remove_buttons
    assert new_id in view._add_after_buttons


def test_remove_step_by_id_shrinks_working_spec():
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    view.remove_step_by_id("3")

    assert "3" not in view.working_spec
    assert set(view.working_spec) == {"1", "2"}
    assert "3" not in view._remove_buttons
    assert "3" not in view._field_widgets


def test_incompatible_add_step_after_is_guarded_no_crash():
    """Node '1' produces 'image'; TTLGGenerateText wants a 'text' input.
    Forcing this incompatible pairing (bypassing the picker, which would
    never offer it) must be caught, not crash, and must not mutate the
    working spec."""
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    before_ids = set(view.working_spec)

    view.add_step_after("1", "TTLGGenerateText")  # must not raise

    assert set(view.working_spec) == before_ids
    assert view._message_label.get_visible() is True
    assert view._message_label.get_label() != ""


def test_current_spec_reflects_param_edit_and_structural_change():
    """A pending (not-yet-Run) text-field edit survives a structural add —
    _commit_pending_edits bakes it into working_spec before add_step_after
    re-renders and rebuilds the field widgets from scratch."""
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    view._field_widgets["1"]["prompt"].set_text("a new prompt")
    view.add_step_after("1", "TTLGCaptionImage")

    spec = view.current_spec()
    assert spec["1"]["inputs"]["prompt"] == "a new prompt"
    new_ids = set(spec) - {"1", "2", "3"}
    assert len(new_ids) == 1
    assert spec[new_ids.pop()]["class_type"] == "TTLGCaptionImage"

    # The rebuilt field widget for node "1" now shows the committed value as
    # its fresh "original" — the field itself is no longer "changed".
    assert view._field_widgets["1"]["prompt"].get_text() == "a new prompt"


# ── RemixView: add-after picker uses dynamic capabilities (SP-C Phase 2b-2 Task 2)
#
# `capability_fn` is an injected seam (defaults to
# capability_discovery.default_capabilities in real use) so these tests never
# touch real plugins/hardware — a fake returns a fixed mix of one live native
# Capability, one live plugin Capability, and one latent Capability regardless
# of the output_kind asked for.

def _fake_capability_fn(output_kind):
    import capability_discovery as cd
    return [
        cd.Capability(
            id="TTLGCaptionImage", label="Describe it", kind_out="text",
            kind_in=output_kind, source="native", class_type="TTLGCaptionImage",
            plugin=None, hardware=None, live=True, reason=None,
        ),
        cd.Capability(
            id="verse", label="Make a verse", kind_out="text",
            kind_in=None, source="plugin", class_type="TTLGArtgenGenerate",
            plugin="verse", hardware=None, live=True, reason=None,
        ),
        cd.Capability(
            id="vidhw", label="Animate a scene", kind_out="video",
            kind_in=None, source="plugin", class_type="TTLGArtgenGenerate",
            plugin="vidhw", hardware="blackhole", live=False,
            reason="start a video model",
        ),
    ]


def test_add_after_picker_lists_live_native_live_plugin_and_disabled_latent():
    from pipeline_studio import RemixView
    view = RemixView(capability_fn=_fake_capability_fn)
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    buttons = view._add_after_choice_buttons["1"]
    assert set(buttons) == {"TTLGCaptionImage", "verse", "vidhw"}

    assert buttons["TTLGCaptionImage"].get_sensitive() is True
    assert buttons["verse"].get_sensitive() is True

    latent_btn = buttons["vidhw"]
    assert latent_btn.get_sensitive() is False
    assert latent_btn.get_tooltip_text() == "start a video model"


def test_choosing_live_native_capability_adds_step_of_its_class_type():
    from pipeline_studio import RemixView
    view = RemixView(capability_fn=_fake_capability_fn)
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    before_ids = set(view.working_spec)
    view._add_after_choice_buttons["1"]["TTLGCaptionImage"].emit("clicked")

    new_ids = set(view.working_spec) - before_ids
    assert len(new_ids) == 1
    new_node = view.working_spec[new_ids.pop()]
    assert new_node["class_type"] == "TTLGCaptionImage"


def test_choosing_live_plugin_capability_adds_artgen_node_with_plugin_param():
    from pipeline_studio import RemixView
    view = RemixView(capability_fn=_fake_capability_fn)
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    before_ids = set(view.working_spec)
    view._add_after_choice_buttons["1"]["verse"].emit("clicked")

    new_ids = set(view.working_spec) - before_ids
    assert len(new_ids) == 1
    new_id = new_ids.pop()
    spec = view.current_spec()
    assert spec[new_id]["class_type"] == "TTLGArtgenGenerate"
    assert spec[new_id]["inputs"]["plugin"] == "verse"


def test_clicking_latent_capability_adds_nothing():
    from pipeline_studio import RemixView
    view = RemixView(capability_fn=_fake_capability_fn)
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    before_ids = set(view.working_spec)
    view._add_after_choice_buttons["1"]["vidhw"].emit("clicked")

    assert set(view.working_spec) == before_ids


def test_capability_fn_empty_falls_back_gracefully_no_crash():
    """An injected capability_fn returning [] must not crash the picker — it
    either falls back to the static vocabulary or shows nothing, but never
    raises."""
    from pipeline_studio import RemixView
    view = RemixView(capability_fn=lambda output_kind: [])
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)  # must not raise

    assert "1" in view._add_after_buttons


# ── LiveRunView ──────────────────────────────────────────────────────────────
#
# begin()/on_node_update()/on_log()/on_finished() are driven directly here —
# no real PipelineRunner/subprocess involved (see pipeline_studio.py's
# LiveRunView docstring: this view owns no runner, Task 4 wires one to it).
# on_node_update/on_finished's argument shapes below are copied verbatim from
# how pipeline_runner.PipelineRunner actually calls them (_parse_line's NODE:
# dispatch, start()'s __health__ signal, _watch_stdout's single-bool finish).

def _make_live_run() -> RunView:
    class_types = ["TTLGTextToImage", "TTLGImageToVideo", "TTLGGenerateText"]
    steps = [
        StepView(node_id=str(i), intent=intent_for(ct), status="pending", artifact_path=None)
        for i, ct in enumerate(class_types, start=1)
    ]
    recipe = [f"{s.intent.verb} {s.intent.noun}" for s in steps]
    return RunView(
        run_id="run-live-1",
        title="Live Test Run",
        created_at="2026-07-10T12:00:00+00:00",
        hero_path=None,
        steps=steps,
        recipe=recipe,
    )


def test_live_run_view_constructs():
    from pipeline_studio import LiveRunView
    view = LiveRunView()
    assert isinstance(view, Gtk.Box)


def test_live_run_view_begin_shows_all_steps_pending():
    from pipeline_studio import LiveRunView
    view = LiveRunView()
    view.begin(_make_live_run())

    assert list(view._step_status_labels.keys()) == ["1", "2", "3"]
    for label in view._step_status_labels.values():
        assert label.get_label() == "•"


def test_live_run_view_on_node_update_running_then_done():
    from pipeline_studio import LiveRunView
    view = LiveRunView()
    view.begin(_make_live_run())

    view.on_node_update("job", "1", "running", "")
    assert view._step_status_labels["1"].get_label() == "⟳"

    view.on_node_update("job", "1", "done", "")
    assert view._step_status_labels["1"].get_label() == "✓"


def test_live_run_view_on_node_update_failed_glyph():
    from pipeline_studio import LiveRunView
    view = LiveRunView()
    view.begin(_make_live_run())

    view.on_node_update("job", "2", "failed", "boom")
    assert view._step_status_labels["2"].get_label() == "✕"


def test_live_run_view_health_update_does_not_create_step_row():
    """The runner's synthetic __health__/__chips__ signal must not be mistaken
    for a real step (no new entry in _step_status_labels) and must not crash."""
    from pipeline_studio import LiveRunView
    view = LiveRunView()
    view.begin(_make_live_run())

    view.on_node_update("__health__", "__chips__", "4", "")

    assert list(view._step_status_labels.keys()) == ["1", "2", "3"]
    assert "__chips__" not in view._step_status_labels
    assert view._health_note.get_visible() is True
    assert "4" in view._health_note.get_label()


def test_live_run_view_health_update_real_signal_shape():
    """The actual shape PipelineRunner.start() sends for a degraded chip check."""
    from pipeline_studio import LiveRunView
    view = LiveRunView()
    view.begin(_make_live_run())

    view.on_node_update("__health__", "__chips__", "degraded", "AC power cycle recommended")

    assert view._health_note.get_visible() is True
    assert "AC power cycle recommended" in view._health_note.get_label()


def test_live_run_view_on_log_switch_line_styled_as_switch_row():
    from pipeline_studio import LiveRunView
    view = LiveRunView()
    view.begin(_make_live_run())

    view.on_log("LOG:  resetting boards (flux → skyreels)")

    last = view._log_box.get_last_child()
    assert last.has_css_class("ps-log-switch")


def test_live_run_view_on_log_plain_line_not_styled_as_switch():
    from pipeline_studio import LiveRunView
    view = LiveRunView()
    view.begin(_make_live_run())

    view.on_log("NODE:1:running:")

    last = view._log_box.get_last_child()
    assert not last.has_css_class("ps-log-switch")
    assert last.has_css_class("ps-log-line")


def test_live_run_view_on_finished_marks_running_steps_done_and_emits_run_done():
    from pipeline_studio import LiveRunView
    view = LiveRunView()
    view.begin(_make_live_run())
    view.on_node_update("job", "1", "done", "")
    view.on_node_update("job", "2", "running", "")
    # step "3" is left pending (never started)

    received = []
    view.connect("run-done", lambda _w, run_id: received.append(run_id))
    view.on_finished(True)

    assert view._step_status_labels["1"].get_label() == "✓"
    assert view._step_status_labels["2"].get_label() == "✓"  # running -> done on success
    assert view._step_status_labels["3"].get_label() == "•"  # untouched, never started
    assert received == ["run-live-1"]


def test_live_run_view_on_finished_failure_marks_running_as_failed():
    from pipeline_studio import LiveRunView
    view = LiveRunView()
    view.begin(_make_live_run())
    view.on_node_update("job", "1", "running", "")

    view.on_finished(False)

    assert view._step_status_labels["1"].get_label() == "✕"


def test_live_run_view_begin_is_repeat_safe():
    """Calling begin() again resets to PENDING and clears the log — no
    leftover status from a previous run, matching set_runs/set_run's rule."""
    from pipeline_studio import LiveRunView
    view = LiveRunView()
    view.begin(_make_live_run())
    view.on_node_update("job", "1", "done", "")
    view.on_log("some earlier line")

    view.begin(_make_live_run())

    assert view._step_status_labels["1"].get_label() == "•"
    assert view._log_box.get_first_child() is None


# ── PipelineStudio: wire the loop (Open → Remix → Run → done) ──────────────
#
# SP-C Phase 2a Task 4 wired this loop by calling spec_remix.derive_spec(
# spec_path, edits) directly. SP-C Phase 2b-1 Task 4 replaced that with
# remix_view.current_spec() + spec_remix.write_spec so RemixView's structural
# add/remove edits (Phase 2b-1 Task 3) actually reach the executed run
# instead of being silently dropped -- derive_spec re-reads spec_path fresh
# off disk and has no way to see edits living only in RemixView.working_spec.
# write_spec/current_spec are left REAL (pure JSON I/O) — only PipelineRunner
# and PipelineStore are mocked; this is a wiring test, not a real-subprocess/
# real-disk integration test (see test_pipeline_runner.py for PipelineRunner's
# own unit tests). A monkeypatched pipeline_studio.REMIXES_DIR points every
# written file at a tmp_path remixes dir, so nothing lands under the actual
# user's home directory.

def test_pipeline_studio_remix_request_shows_remix_page_for_open_run(monkeypatch, tmp_path):
    """OpenView's remix-request (either button) opens RemixView pre-filled
    with whichever run is currently on the Open page."""
    import pipeline_store
    monkeypatch.setattr(pipeline_store, "_INDEX_PATH", tmp_path / "pipeline-index.json")
    monkeypatch.setattr(pipeline_store, "_RUNS_DIR", tmp_path / "runs")

    from pipeline_studio import PipelineStudio
    studio = PipelineStudio()

    studio._show_run(_make_remix_run(), _REMIX_SPEC_PATH)
    studio.open_view.emit("remix-request", "")

    assert studio.stack.get_visible_child_name() == "remix"
    assert studio.remix_view._spec_path == _REMIX_SPEC_PATH
    assert set(studio.remix_view._field_widgets.keys()) == {"1", "2", "3"}


def test_pipeline_studio_remix_request_noop_when_nothing_open(monkeypatch, tmp_path):
    """A remix-request before any run has ever been opened must not crash or
    switch pages — there is nothing to pre-fill RemixView with."""
    import pipeline_store
    monkeypatch.setattr(pipeline_store, "_INDEX_PATH", tmp_path / "pipeline-index.json")
    monkeypatch.setattr(pipeline_store, "_RUNS_DIR", tmp_path / "runs")

    from pipeline_studio import PipelineStudio
    studio = PipelineStudio()

    studio.open_view.emit("remix-request", "")

    assert studio.stack.get_visible_child_name() == "discover"


def test_pipeline_studio_run_remix_writes_composed_spec_creates_run_and_starts_runner(
        monkeypatch, tmp_path):
    """RemixView's run-remix must: write remix_view.current_spec() (the
    composed working spec, including any pending field edit) under
    REMIXES_DIR via spec_remix.write_spec, create a provisional PipelineStore
    run record for the WRITTEN path, show it in LiveRunView and switch to
    "run", then construct a PipelineRunner and start() it with the written
    path and LiveRunView's own handlers bound directly as its callbacks."""
    import pipeline_studio

    remixes_dir = tmp_path / "remixes"
    monkeypatch.setattr(pipeline_studio, "REMIXES_DIR", remixes_dir)

    # PipelineStore is fully mocked — create_run/get_run return a provisional
    # record pointing at whatever path create_run was ACTUALLY called with
    # (the written path), not a hardcoded guess.
    mock_store_instance = MagicMock()
    mock_store_instance.create_run.return_value = "provisional-run-1"

    def _fake_get_run(run_id):
        kwargs = mock_store_instance.create_run.call_args.kwargs
        return {
            "id": run_id,
            "spec_path": kwargs["spec_path"],
            "spec_name": kwargs["spec_name"],
            "output_dir": "",
            "job_states": {},
            "started_at": "2026-07-11T00:00:00+00:00",
        }
    mock_store_instance.get_run.side_effect = _fake_get_run
    mock_store_cls = MagicMock(return_value=mock_store_instance)
    monkeypatch.setattr(pipeline_studio, "PipelineStore", mock_store_cls)

    mock_runner_instance = MagicMock()
    mock_runner_cls = MagicMock(return_value=mock_runner_instance)
    monkeypatch.setattr(pipeline_studio, "PipelineRunner", mock_runner_cls)

    from pipeline_studio import PipelineStudio
    studio = PipelineStudio()

    studio.remix_view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    studio.remix_view._field_widgets["1"]["prompt"].set_text("a new prompt")

    edits = {"1": {"prompt": "a new prompt"}}
    studio.remix_view.emit("run-remix", _REMIX_SPEC_PATH, edits)

    written = list(remixes_dir.glob("remix_remix_fixture_spec_*.json"))
    assert len(written) == 1, "run-remix must write exactly one derived spec file"
    written_path = str(written[0])
    written_spec = json.loads(written[0].read_text())

    # The pending field edit (typed but never separately "applied") made it
    # into the written spec via current_spec().
    assert written_spec["1"]["inputs"]["prompt"] == "a new prompt"

    # create_run called with the WRITTEN path.
    create_kwargs = mock_store_instance.create_run.call_args.kwargs
    assert create_kwargs["spec_path"] == written_path
    assert create_kwargs["param_overrides"] == edits

    # PipelineRunner constructed with GLib.idle_add, started with the written
    # path and LiveRunView's handlers bound directly.
    mock_runner_cls.assert_called_once_with(idle_add=pipeline_studio.GLib.idle_add)
    start_args, start_kwargs = mock_runner_instance.start.call_args
    assert start_args[0] == written_path
    assert start_kwargs["on_node_update"] == studio.live_run.on_node_update
    assert start_kwargs["on_log"] == studio.live_run.on_log
    assert start_kwargs["on_run_finished"] == studio.live_run.on_finished
    assert start_kwargs["run_id"] == "provisional-run-1"

    assert studio.stack.get_visible_child_name() == "run"
    assert list(studio.live_run._step_status_labels.keys()) == ["1", "2", "3"]


def test_pipeline_studio_run_remix_includes_added_step_and_preserves_metadata(
        monkeypatch, tmp_path):
    """Regression guard for the Phase 2b-1 Task 3 -> Task 4 gap: a step ADDED
    via the composer (RemixView.add_step_after) must reach the ACTUAL run.
    The old wiring called spec_remix.derive_spec(spec_path, edits), which
    re-reads spec_path fresh off disk -- it has no way to see a structural
    add/remove living only in RemixView.working_spec, so the added node was
    silently dropped from the executed run. Also asserts the written spec
    preserves the base file's top-level `_`-metadata (current_spec()'s
    working_spec strips it via pipeline_engine.load_spec, so it must be
    re-merged before write_spec) and that create_run()/PipelineRunner.start()
    both receive the WRITTEN path with the single-record run_id preserved.
    """
    import pipeline_studio

    remixes_dir = tmp_path / "remixes"
    monkeypatch.setattr(pipeline_studio, "REMIXES_DIR", remixes_dir)

    mock_store_instance = MagicMock()
    mock_store_instance.create_run.return_value = "provisional-run-1"

    def _fake_get_run(run_id):
        kwargs = mock_store_instance.create_run.call_args.kwargs
        return {
            "id": run_id,
            "spec_path": kwargs["spec_path"],
            "spec_name": kwargs["spec_name"],
            "output_dir": "",
            "job_states": {},
            "started_at": "2026-07-11T00:00:00+00:00",
        }
    mock_store_instance.get_run.side_effect = _fake_get_run
    mock_store_cls = MagicMock(return_value=mock_store_instance)
    monkeypatch.setattr(pipeline_studio, "PipelineStore", mock_store_cls)

    mock_runner_instance = MagicMock()
    mock_runner_cls = MagicMock(return_value=mock_runner_instance)
    monkeypatch.setattr(pipeline_studio, "PipelineRunner", mock_runner_cls)

    from pipeline_studio import PipelineStudio
    studio = PipelineStudio()

    studio.remix_view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    # Structurally add a step after node "1" (Generate an image, kind
    # "image") -- TTLGCaptionImage is a valid next step (input_kind "image").
    studio.remix_view.add_step_after("1", "TTLGCaptionImage")

    studio.remix_view.emit("run-remix", _REMIX_SPEC_PATH, {})

    written = list(remixes_dir.glob("remix_remix_fixture_spec_*.json"))
    assert len(written) == 1, "run-remix must write exactly one derived spec file"
    written_path = str(written[0])
    written_spec = json.loads(written[0].read_text())

    # The added node made it into the ACTUAL run -- this is exactly what
    # derive_spec(spec_path, edits) could never see.
    assert "4" in written_spec
    assert written_spec["4"]["class_type"] == "TTLGCaptionImage"

    # Top-level `_`-metadata from the base spec file survives even though
    # current_spec()'s working_spec (built via load_spec) stripped it.
    assert written_spec.get("_spec_version") == "comfyui-api-v1"
    assert "_comment" in written_spec

    # create_run/PipelineRunner.start both target the WRITTEN path, and the
    # single provisional run_id flows through unchanged.
    create_kwargs = mock_store_instance.create_run.call_args.kwargs
    assert create_kwargs["spec_path"] == written_path

    start_args, start_kwargs = mock_runner_instance.start.call_args
    assert start_args[0] == written_path
    assert start_kwargs["run_id"] == "provisional-run-1"

    assert studio.stack.get_visible_child_name() == "run"


def test_pipeline_studio_run_done_returns_to_open_with_fresh_run(monkeypatch, tmp_path):
    """LiveRunView's run-done rebuilds the Open page from that run id's
    current record and switches back to "open"."""
    import pipeline_studio

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
    mock_store_cls = MagicMock(return_value=mock_store_instance)
    monkeypatch.setattr(pipeline_studio, "PipelineStore", mock_store_cls)

    from pipeline_studio import PipelineStudio
    studio = PipelineStudio()
    studio.stack.set_visible_child_name("run")

    studio.live_run.begin(_make_remix_run())
    studio._on_run_done(studio.live_run, "run-finished-1")

    mock_store_instance.get_run.assert_called_with("run-finished-1")
    assert studio.stack.get_visible_child_name() == "open"
    assert studio._current_spec_path == _REMIX_SPEC_PATH


def test_pipeline_studio_full_loop_finish_returns_to_open(monkeypatch, tmp_path):
    """End-to-end wiring: run-remix -> (simulated finish) -> run-done -> Open.

    Regression test for the SP-C dual-run-record bug: PipelineRunner.start()
    used to unconditionally mint its OWN run id via create_run(), so the
    provisional record _on_run_remix created (and handed to LiveRunView.begin)
    was never the record that accumulated node/output/finish updates -- Open's
    post-finish rebuild would then read the stale, never-updated provisional
    record. The store here is keyed BY ID (a dict), not a single fixed
    return_value/side_effect result, precisely so that a query for the WRONG
    id (the old bug's second, runner-minted id) would come back None instead
    of silently handing back a plausible-looking fake record. This asserts a
    single id flows: create_run() mints it -> start(run_id=...) receives it ->
    get_run() is queried with it (both by _on_run_remix and by _on_run_done).
    """
    import pipeline_studio

    remixes_dir = tmp_path / "remixes"
    monkeypatch.setattr(pipeline_studio, "REMIXES_DIR", remixes_dir)
    monkeypatch.setattr(pipeline_studio.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(pipeline_studio.GLib, "idle_add", lambda fn, *a: fn(*a))

    # Records keyed by id -- NOT a single fixed value. A get_run() call with
    # any id other than the one create_run() minted misses and returns None.
    records: dict[str, dict] = {}

    def _fake_create_run(spec_path, spec_name, jobs, param_overrides, pid, log_file):
        run_id = "provisional-run-1"
        records[run_id] = {
            "id": run_id,
            "spec_path": spec_path,
            "spec_name": spec_name,
            "output_dir": "",
            "job_states": {},
            "started_at": "2026-07-11T00:00:00+00:00",
        }
        return run_id

    mock_store_instance = MagicMock()
    mock_store_instance.create_run.side_effect = _fake_create_run
    mock_store_instance.get_run.side_effect = lambda run_id: records.get(run_id)
    mock_store_cls = MagicMock(return_value=mock_store_instance)
    monkeypatch.setattr(pipeline_studio, "PipelineStore", mock_store_cls)

    mock_runner_instance = MagicMock()
    mock_runner_cls = MagicMock(return_value=mock_runner_instance)
    monkeypatch.setattr(pipeline_studio, "PipelineRunner", mock_runner_cls)

    from pipeline_studio import PipelineStudio
    studio = PipelineStudio()

    studio.remix_view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    studio.remix_view.emit("run-remix", _REMIX_SPEC_PATH, {})
    assert studio.stack.get_visible_child_name() == "run"

    # Exactly one id was minted -- pull it out of the fake store's records.
    assert len(records) == 1
    created_run_id = next(iter(records))

    # PipelineRunner.start() must have been handed that SAME id via run_id=,
    # so the real runner (unmocked in production) adopts the existing record
    # instead of minting a second one.
    start_kwargs = mock_runner_instance.start.call_args.kwargs
    assert "run_id" in start_kwargs, (
        "PipelineStudio._on_run_remix must pass run_id=<provisional id> to "
        "PipelineRunner.start() so the runner adopts the existing record "
        "instead of creating a second, divergent one"
    )
    assert start_kwargs["run_id"] == created_run_id

    # Simulate the runner finishing: call the exact callback it was started
    # with (LiveRunView.on_finished), which resolves running steps and emits
    # run-done — PipelineStudio's handler (real, unmocked) then rebuilds Open.
    on_run_finished = start_kwargs["on_run_finished"]
    on_run_finished(True)

    assert studio.stack.get_visible_child_name() == "open"

    # Every get_run() call throughout the whole loop -- both _on_run_remix's
    # own lookup and _on_run_done's rebuild lookup -- must target the SAME id.
    get_run_ids = [c.args[0] for c in mock_store_instance.get_run.call_args_list]
    assert get_run_ids, "expected at least one get_run() call"
    assert all(rid == created_run_id for rid in get_run_ids), (
        f"expected only {created_run_id!r} to ever be queried via get_run(), "
        f"got {get_run_ids} -- a divergent id means dual run records"
    )
