"""Per-step model picker in RemixView (pipeline UX overhaul, Task 5).

`RemixView._build_step_card` grows a `model_picker.ModelPickerRow` in the
"Runs on" slot for any node whose `class_type` maps to a capability via
`intent_vocab.capability_for_intent` (image/video/animatediff/artgen) — the
same picker widget/vocabulary Create's scoped model dropdown uses (Task 3),
just embedded per-step instead of per-medium.

Reuses `tests/test_pipeline_studio.py`'s own `remix_fixture_spec.json`
fixture (node "1" TTLGTextToImage, "2" TTLGImageToVideo, "3" TTLGAnimateDiff
— none of which carry an explicit "model" input) so this file exercises the
REAL `_build_step_card`/`_collect_edits` path via `set_run()`, not a
hand-rolled spec.

The critical invariant this file guards: a node with a capability but NO
explicit "model" input (the common case in every existing fixture) must
still round-trip through `_collect_edits()` as a no-op when the picker isn't
touched — the picker's post-construction `selected_key()` (whatever it
resolved to, e.g. the first entry) is recorded as the "original" value, not
the raw (missing -> None) field value. Otherwise every remix of a fixture
like this would spuriously emit a `model` edit despite zero user action —
exactly the class of bug `test_remix_no_edits_still_emits_empty_dict`
(pre-existing, in test_pipeline_studio.py) already guards for the
non-model-picker fields; this file extends the same guarantee to the new
picker.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)

import pipeline_studio as ps
from intent_vocab import intent_for
from model_picker import ModelPickerRow
from pipeline_view_model import RunView, StepView
from spec_remix import ParamField

_REMIX_SPEC_PATH = str(Path(__file__).parent / "fixtures" / "remix_fixture_spec.json")


def _make_remix_run() -> RunView:
    """Same fixture `test_pipeline_studio.py` uses: node "1" TTLGTextToImage
    (image), "2" TTLGImageToVideo (video), "3" TTLGAnimateDiff (animatediff)
    — none carry a "model" input."""
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


def test_image_video_animatediff_steps_each_get_a_model_picker():
    view = ps.RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    for node_id in ("1", "2", "3"):
        picker = view._model_pickers.get(node_id)
        assert isinstance(picker, ModelPickerRow)
        # A picker is only useful if it recorded SOME resolved original
        # selection to diff future changes against.
        assert node_id in view._model_orig


def test_non_model_step_has_no_picker():
    view = ps.RemixView()
    view._build_step_card(1, "1", "TTLGCaptionImage", [])
    assert view._model_pickers.get("1") is None
    assert "1" not in view._model_orig


def test_model_field_is_not_also_rendered_as_a_free_text_field():
    """A `model` ParamField on a capability node is OWNED by the picker —
    it must not also show up as a plain text Gtk.Entry field (that would be
    a duplicate, disconnected control for the same value)."""
    view = ps.RemixView()
    field = ParamField(node_id="1", key="model", label="Model", kind="text", value="flux")
    view._build_step_card(1, "1", "TTLGTextToImage", [field])
    assert "model" not in view._field_widgets.get("1", {})
    assert view._model_pickers.get("1") is not None


def test_collect_edits_with_no_interaction_is_still_empty_dict():
    """The hard invariant: adding the picker must not turn an untouched
    remix into a non-empty edits dict. None of this fixture's 3 nodes carry
    an explicit "model" input, so each picker's original is whatever it
    resolved to on construction (e.g. its first entry) — the diff must
    compare against THAT, not a missing/None raw field value."""
    view = ps.RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    received = []
    view.connect("run-remix", lambda _w, sp, edits: received.append(edits))
    view._run_button.emit("clicked")

    assert received == [{}]


def test_collect_edits_emits_model_edit_only_when_picker_selection_changes():
    view = ps.RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    picker = view._model_pickers["1"]
    orig_key = view._model_orig["1"]
    # "image" has multiple ServerDefs (flux/sdxl/z-image-turbo/motif) in
    # server_manager.SERVERS, so there is always a second entry to switch to.
    assert len(picker._entries) >= 2
    other_index = next(
        i for i, (key, *_r) in enumerate(picker._entries) if key != orig_key
    )
    picker._dropdown.set_selected(other_index)
    new_key = picker.selected_key()
    assert new_key != orig_key

    received = []
    view.connect("run-remix", lambda _w, sp, edits: received.append(edits))
    view._run_button.emit("clicked")

    assert received == [{"1": {"model": new_key}}]


def test_model_field_prefills_picker_selection():
    """When the spec DOES carry an explicit "model" value that matches a
    real capability entry, the picker should pre-select it (not silently
    fall back to the default first entry)."""
    view = ps.RemixView()
    field = ParamField(node_id="1", key="model", label="Model", kind="text", value="sdxl")
    view._build_step_card(1, "1", "TTLGTextToImage", [field])
    assert view._model_orig["1"] == "sdxl"
    assert view._model_pickers["1"].selected_key() == "sdxl"
