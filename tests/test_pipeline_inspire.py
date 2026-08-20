# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for `RemixView`'s ✨ Inspire wiring (regression fix 2/2,
"tt-local-generator inspire2" — pipeline-editor adoption of the same
`create_param_panels.attach_inspire_button` two-mode seam Create's idea-door
and `ArtgenParamPanel` fields already use).

Reuses `tests/fixtures/remix_fixture_spec.json` (node "1" TTLGTextToImage:
`prompt` text field + `negative_prompt` text field + `steps` number field),
the same fixture `tests/test_pipeline_studio.py`'s own RemixView tests build
against — see that file's "RemixView" section comment for the fixture's
node/class_type layout.

Coverage:
  - a BRIEF-role "text" field (`prompt`) gets a ✨ button when `inspire_fn`
    is injected
  - `negative_prompt` (also BRIEF/"text", but a negation — `_NEGATIVE_FIELD_
    KEYS`) never gets one, mirroring ModifierPills' own exclusion
  - a "number" field (`steps`) never gets one
  - `inspire_fn=None` (every pre-existing RemixView test's construction) means
    no ✨ buttons anywhere — migration-safe
  - clicking forwards the field's current text as the seed, resolves the
    prompt-gen "source" from the owning node's `Intent.output_kind`
    (`_prompt_type_for_output`), and fills the result back into the SAME
    entry `_collect_edits` reads — so an Inspire result is a genuine, diffable
    edit like any hand-typed one
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

from intent_vocab import intent_for
from pipeline_view_model import RunView, StepView

import pipeline_studio as ps
from pipeline_studio import RemixView

_REMIX_SPEC_PATH = str(Path(__file__).parent / "fixtures" / "remix_fixture_spec.json")


def _make_remix_run() -> RunView:
    class_types = {"1": "TTLGTextToImage", "2": "TTLGImageToVideo", "3": "TTLGAnimateDiff"}
    steps = [
        StepView(node_id=nid, intent=intent_for(ct), status="done", artifact_path=None)
        for nid, ct in class_types.items()
    ]
    recipe = [f"{s.intent.verb} {s.intent.noun}" for s in steps]
    return RunView(
        run_id="run-remix-inspire-1",
        title="Tower of Pisa GIF",
        created_at="2026-07-10T12:00:00+00:00",
        hero_path=None,
        steps=steps,
        recipe=recipe,
    )


class _FakeInspire:
    def __init__(self):
        self.calls = []

    def __call__(self, prompt_type, seed_text, on_result, on_error):
        self.calls.append((prompt_type, seed_text, on_result, on_error))


def _make_view(inspire_fn=None):
    # capability_fn=lambda: [] keeps the "add a step after" popover from
    # touching real plugin-manifest disk I/O (same convention
    # tests/test_pipeline_studio.py uses).
    view = RemixView(capability_fn=lambda output_kind: [], inspire_fn=inspire_fn)
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    return view


def _row_of(entry: Gtk.Widget) -> Gtk.Widget:
    return entry.get_parent()


def _inspire_button_next_to(entry: Gtk.Widget) -> "Gtk.Button | None":
    row = _row_of(entry)
    child = row.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button) and child.has_css_class("create-inspire-btn"):
            return child
        child = child.get_next_sibling()
    return None


@pytest.fixture(autouse=True)
def _synchronous_idle_add(monkeypatch):
    monkeypatch.setattr(ps.GLib, "idle_add", lambda fn, *a: fn(*a))


# ── Eligibility ──────────────────────────────────────────────────────────────


def test_prompt_field_gets_inspire_button_when_fn_given():
    view = _make_view(inspire_fn=_FakeInspire())
    entry = view._field_widgets["1"]["prompt"]
    assert _inspire_button_next_to(entry) is not None


def test_negative_prompt_field_never_gets_inspire_button():
    """negative_prompt is BRIEF/"text" too, but excluded via
    _NEGATIVE_FIELD_KEYS — same exclusion ModifierPills already uses."""
    view = _make_view(inspire_fn=_FakeInspire())
    entry = view._field_widgets["1"]["negative_prompt"]
    assert _inspire_button_next_to(entry) is None


def test_number_field_never_gets_inspire_button():
    view = _make_view(inspire_fn=_FakeInspire())
    steps_spin = view._field_widgets["1"]["steps"]
    row = _row_of(steps_spin)
    child = row.get_first_child()
    found = False
    while child is not None:
        if isinstance(child, Gtk.Button) and child.has_css_class("create-inspire-btn"):
            found = True
        child = child.get_next_sibling()
    assert found is False


def test_bool_field_never_gets_inspire_button():
    view = _make_view(inspire_fn=_FakeInspire())
    loop_switch = view._field_widgets["3"]["loop"]
    assert _inspire_button_next_to(loop_switch) is None


def test_no_inspire_fn_means_no_buttons_anywhere():
    """Every pre-existing RemixView() construction (test_pipeline_studio.py)
    never passes inspire_fn — must render byte-identical to before, with no
    ✨ button anywhere on the card."""
    view = _make_view(inspire_fn=None)
    entry = view._field_widgets["1"]["prompt"]
    assert _inspire_button_next_to(entry) is None


# ── Click behavior ───────────────────────────────────────────────────────────


def test_click_forwards_current_prompt_text_as_seed_and_fills_same_entry():
    fake = _FakeInspire()
    view = _make_view(inspire_fn=fake)
    entry = view._field_widgets["1"]["prompt"]
    assert entry.get_text() == "a test prompt"  # pre-filled from the fixture

    btn = _inspire_button_next_to(entry)
    btn.emit("clicked")

    assert len(fake.calls) == 1
    prompt_type, seed_text, on_result, _on_error = fake.calls[0]
    assert seed_text == "a test prompt"
    assert prompt_type == "image"  # TTLGTextToImage's Intent.output_kind

    on_result("a reimagined prompt")
    assert entry.get_text() == "a reimagined prompt"


def test_inspire_result_is_a_genuine_diffable_edit():
    """An Inspire-filled field counts as a real edit at Run time, same as a
    hand-typed one — _collect_edits just reads the entry's current text."""
    fake = _FakeInspire()
    view = _make_view(inspire_fn=fake)
    entry = view._field_widgets["1"]["prompt"]

    btn = _inspire_button_next_to(entry)
    btn.emit("clicked")
    fake.calls[0][2]("an inspired prompt")  # on_result

    received = []
    view.connect("run-remix", lambda _w, spec_path, edits: received.append((spec_path, edits)))
    view._run_button.emit("clicked")

    assert received == [(_REMIX_SPEC_PATH, {"1": {"prompt": "an inspired prompt"}})]


def test_prompt_type_resolves_from_output_kind_per_node():
    """`_prompt_type_for_output` maps image/video/gif -> the matching source
    string, defaulting to "video" for anything else (text/playlist/None) —
    exercised here via the module-level helper directly since the fixture
    spec has no editable text field on a video/gif/text node to click."""
    view = _make_view(inspire_fn=_FakeInspire())
    assert view._prompt_type_for_output("image") == "image"
    assert view._prompt_type_for_output("video") == "video"
    assert view._prompt_type_for_output("gif") == "animate"
    assert view._prompt_type_for_output("text") == "video"
    assert view._prompt_type_for_output(None) == "video"


def test_inspire_fn_raising_synchronously_is_fail_soft():
    def _boom(prompt_type, seed_text, on_result, on_error):
        raise RuntimeError("boom")

    view = _make_view(inspire_fn=_boom)
    entry = view._field_widgets["1"]["prompt"]
    btn = _inspire_button_next_to(entry)
    btn.emit("clicked")  # must not raise
    assert btn.get_sensitive() is True
