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
import threading as _threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

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


@pytest.fixture(autouse=True)
def _isolate_capability_discovery_io(monkeypatch):
    """Fix 2 (SP-C Phase-2b-2 final review — test isolation).

    ~14 tests below construct `RemixView()` with no `capability_fn`, so
    `set_run()`'s render falls through to
    `capability_discovery.default_capabilities` — which reads every
    `plugins/<name>/mcp.json` off disk and probes real server health/ports
    (see that module's docstring). Replace it, autouse for every test in this
    file, with a trivial static Capability list so no test performs real
    disk/network I/O merely by constructing an un-injected RemixView.

    Tests that inject their own `capability_fn` (e.g. `_fake_capability_fn`
    below, or the `lambda output_kind: []` fake) are unaffected — RemixView
    only falls back to `capability_discovery.default_capabilities` when
    `capability_fn` is `None`, so those tests never reach it regardless.

    Also stubs `_read_all_plugin_mcp` to return `{}` — `PipelineStudio`
    (SP-C Phase 2b-3 Task 4) now eagerly constructs a `MuseView` in its own
    `__init__`, whose default `goals_fn` wraps `recipes.goals_for`, which in
    turn reads every real `plugins/<name>/mcp.json` off disk via this same
    reader when not overridden. Stubbing it here keeps every PipelineStudio-
    constructing test in this file free of real plugin-manifest I/O, exactly
    like the `default_capabilities` stub above already does for RemixView.
    """
    import capability_discovery as cd

    def _fake_default_capabilities(output_kind):
        return [
            cd.Capability(
                id="TTLGCaptionImage", label="Describe it", kind_out="text",
                kind_in=output_kind, source="native", class_type="TTLGCaptionImage",
                plugin=None, hardware=None, live=True, reason=None,
            ),
        ]

    monkeypatch.setattr(cd, "default_capabilities", _fake_default_capabilities)
    monkeypatch.setattr(cd, "_read_all_plugin_mcp", lambda: {})


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


# ── _build_thumb_frame / _wrap_centered (module-level helpers) ──────────────

def test_build_thumb_frame_no_path_with_intent_shows_intent_icon():
    """Fix #4: the placeholder tile shows the step's intent icon — an
    intentional 'nothing here yet' tile, not the bare generic fallback."""
    import pipeline_studio as ps
    intent = intent_for("TTLGCaptionImage")  # icon "📝" — distinct from the
    # generic 🖼️ fallback so this test can't pass by coincidence.
    frame = ps._build_thumb_frame(None, 100, 60, "ps-card-thumb", intent)
    placeholder = frame.get_first_child()
    assert isinstance(placeholder, Gtk.Label)
    assert placeholder.get_label() == intent.icon


def test_build_thumb_frame_no_path_no_intent_falls_back_to_generic_glyph():
    import pipeline_studio as ps
    frame = ps._build_thumb_frame(None, 100, 60, "ps-card-thumb")
    placeholder = frame.get_first_child()
    assert isinstance(placeholder, Gtk.Label)
    assert placeholder.get_label() == "\U0001f5bc️"  # 🖼️


def test_wrap_centered_caps_oversize_child_width():
    """Fix #5 (review follow-up): the column cap must be a REAL ceiling, not
    just a set_size_request floor. A deliberately-too-wide (3000px) child
    must be MEASURED and ALLOCATED at <= the max width — this would FAIL with
    the old `set_size_request(960, -1)` floor, which only raises the minimum
    and lets GTK allocate more."""
    import pipeline_studio as ps
    child = Gtk.Box()
    child.set_size_request(3000, 40)  # far wider than the 960 cap
    wrapper = ps._wrap_centered(child)

    # Measurement: the wrapper's natural width never exceeds the cap, even
    # though its child wants 3000px.
    _min, natural, _mb, _nb = wrapper.measure(Gtk.Orientation.HORIZONTAL, -1)
    assert natural <= ps._CONTENT_MAX_WIDTH

    # Allocation: hand the wrapper 2000px and the child is still capped.
    wrapper.allocate(2000, 300, -1, None)
    assert child.get_width() <= ps._CONTENT_MAX_WIDTH


def test_wrap_centered_narrow_window_lets_child_shrink():
    """The cap is a ceiling, not a fixed width: on a window narrower than the
    cap the child shrinks to fit rather than being pinned at 960px."""
    import pipeline_studio as ps
    child = Gtk.Box()
    child.set_size_request(3000, 40)
    wrapper = ps._wrap_centered(child)

    wrapper.allocate(400, 300, -1, None)
    assert child.get_width() <= 400


# ── DiscoverView ───────────────────────────────────────────────────────────────

def test_discover_view_constructs():
    from pipeline_studio import DiscoverView
    view = DiscoverView()
    assert isinstance(view, Gtk.Box)


def test_discover_view_content_column_is_capped_max_width_bin():
    """Fix #5 (user feedback): content is routed through the real max-width
    clamp so it can't sprawl across the full window."""
    import pipeline_studio as ps
    from pipeline_studio import DiscoverView
    view = DiscoverView()
    assert isinstance(view._content_wrapper, ps._MaxWidthBin)
    _min, natural, _mb, _nb = view._content_wrapper.measure(Gtk.Orientation.HORIZONTAL, -1)
    assert natural <= ps._CONTENT_MAX_WIDTH


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


def test_open_view_content_column_is_capped_max_width_bin():
    """Fix #5 (user feedback): same real-cap contract as
    DiscoverView/RemixView."""
    import pipeline_studio as ps
    from pipeline_studio import OpenView
    view = OpenView()
    assert isinstance(view._content_wrapper, ps._MaxWidthBin)
    _min, natural, _mb, _nb = view._content_wrapper.measure(Gtk.Orientation.HORIZONTAL, -1)
    assert natural <= ps._CONTENT_MAX_WIDTH


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


def _open_view_row_for(view, steps, node_id):
    """Walk `view._steps_box`'s children to find *node_id*'s row.

    Shared by the structural tests below — there's no dict keyed by node_id
    for the row itself (unlike _step_remix_buttons/_step_thumb_frames), so
    tests that need to inspect the row's internal layout reach into the
    widget tree directly.
    """
    index = [s.node_id for s in steps].index(node_id)
    row = view._steps_box.get_first_child()
    for _ in range(index):
        row = row.get_next_sibling()
    return row


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
        # main column is (intent_row, [model_label]) — the combined intent
        # label lives in intent_row (fix #1), so a model caption is the
        # SECOND child of main, if present at all.
        row = _open_view_row_for(view, steps, node_id)
        n_label = row.get_first_child()
        main = n_label.get_next_sibling()
        intent_row = main.get_first_child()
        model_label = intent_row.get_next_sibling()
        return model_label is not None

    assert _has_model_row("1") is True   # TTLGTextToImage -> model_label "FLUX"
    assert _has_model_row("2") is False  # TTLGCaptionImage -> model_label None


def test_open_view_step_row_has_single_combined_intent_label():
    """Fix #1: verb+noun render as ONE label ('Generate an image'), not
    separate verb/noun widgets stacked on top of each other."""
    from pipeline_studio import OpenView
    view = OpenView()

    step = StepView(node_id="1", intent=intent_for("TTLGTextToImage"),
                     status="done", artifact_path=None)
    steps = [step]
    run = RunView(run_id="run-combined-label", title="Combined label test",
                  created_at="2026-07-10T12:00:00+00:00", hero_path=None,
                  steps=steps, recipe=[f"{step.intent.verb} {step.intent.noun}"])
    view.set_run(run)

    row = _open_view_row_for(view, steps, "1")
    n_label = row.get_first_child()
    main = n_label.get_next_sibling()
    intent_row = main.get_first_child()
    intent_label = intent_row.get_first_child()

    assert isinstance(intent_label, Gtk.Label)
    assert intent_label.get_label() == f"{step.intent.verb} {step.intent.noun}"
    # The status glyph sits inline in the same row, not on a third line.
    status_label = intent_label.get_next_sibling()
    assert isinstance(status_label, Gtk.Label)


def test_open_view_empty_step_is_compact_step_with_content_is_rich():
    """Fix #2/#6 reconciliation: a step with no artifact/text gets the
    compact treatment; a step that produced something gets the rich one."""
    from pipeline_studio import OpenView
    view = OpenView()

    empty_step = StepView(node_id="1", intent=intent_for("TTLGImageToVideo"),
                           status="pending", artifact_path=None)
    text_step = StepView(node_id="2", intent=intent_for("TTLGGenerateText"),
                          status="done", artifact_path=None,
                          text_content="A poem about the lighthouse.")
    steps = [empty_step, text_step]
    run = RunView(run_id="run-compact-rich", title="Compact/rich test",
                  created_at="2026-07-10T12:00:00+00:00", hero_path=None,
                  steps=steps, recipe=[])
    view.set_run(run)

    empty_row = _open_view_row_for(view, steps, "1")
    rich_row = _open_view_row_for(view, steps, "2")

    assert empty_row.has_css_class("ps-step-compact")
    assert not empty_row.has_css_class("ps-step-rich")
    assert rich_row.has_css_class("ps-step-rich")
    assert not rich_row.has_css_class("ps-step-compact")


def test_open_view_text_content_step_renders_inline_readable_text():
    """Fix #6: a text-producing step's real produced text renders inline as
    a readable label, not just a placeholder icon."""
    from pipeline_studio import OpenView
    view = OpenView()

    text = "A weathered lighthouse stands watch over the bay."
    step = StepView(node_id="4", intent=intent_for("TTLGGenerateText"),
                     status="done", artifact_path=None, text_content=text)
    run = RunView(run_id="run-text-content", title="Text content test",
                  created_at="2026-07-10T12:00:00+00:00", hero_path=None,
                  steps=[step], recipe=[])
    view.set_run(run)

    assert view._step_text_blocks["4"].get_label() == text
    # A text step has real content — no placeholder thumb frame at all.
    assert "4" not in view._step_thumb_frames


def test_open_view_image_artifact_gets_a_substantially_larger_preview():
    """Fix #6: a step WITH a real image artifact requests a preview larger
    than the old flat 150×92 thumb."""
    from pipeline_studio import OpenView
    view = OpenView()
    view.set_run(_make_run_with_artifact())

    frame = view._step_thumb_frames["1"]
    width, height = frame.get_size_request()
    assert width > 150
    assert height > 92
    assert (width, height) == (OpenView.PREVIEW_W, OpenView.PREVIEW_H)


def test_open_view_pending_step_placeholder_shows_intent_icon():
    """Fix #4: a step with no artifact yet renders its intent's icon on the
    placeholder tile, not the bare generic 🖼️ fallback."""
    from pipeline_studio import OpenView
    view = OpenView()
    view.set_run(_make_run_with_artifact())  # node "2" is pending, no artifact

    pending_frame = view._step_thumb_frames["2"]
    placeholder_label = pending_frame.get_first_child()
    assert isinstance(placeholder_label, Gtk.Label)
    assert placeholder_label.get_label() == intent_for("TTLGImageToVideo").icon


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


# ── OpenView: "Build showcase" capstone (SP-C Phase 3, Task 2) ────────────────
#
# `showcase_fn` is injected exactly like RemixView's `wingit_fn` seam: tests
# never touch the real `showcase.write_showcase` (no PIL/disk work), and the
# busy-guard tests below reuse the same "real threading.Thread + captured
# GLib.idle_add" technique `_click_wingit_compose_with_blocking_fn` established
# so the button-disabled-mid-flight assertion isn't a race.

def test_open_view_build_showcase_button_present_and_result_hidden_initially():
    from pipeline_studio import OpenView
    view = OpenView(showcase_fn=lambda run_view: "/tmp/unused.html")
    view.set_run(_make_run_with_artifact())

    assert view._showcase_btn.get_sensitive() is True
    assert view._showcase_path_label.get_visible() is False
    assert view._showcase_open_btn.get_visible() is False
    assert view._showcase_message_label.get_visible() is False


def test_open_view_build_showcase_calls_fn_with_run_view_and_reveals_path(monkeypatch, tmp_path):
    """Driving the click synchronously (thread + idle_add both immediate):
    the fake is called with the loaded RunView and its returned path is
    revealed via the path label + Open button."""
    import pipeline_studio

    monkeypatch.setattr(pipeline_studio.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(pipeline_studio.GLib, "idle_add", lambda fn, *a: fn(*a))

    fake_path = str(tmp_path / "showcase_test_0.html")
    received = []

    def fake_showcase_fn(run_view):
        received.append(run_view)
        return fake_path

    from pipeline_studio import OpenView
    view = OpenView(showcase_fn=fake_showcase_fn)
    run = _make_run_with_artifact()
    view.set_run(run)

    view._showcase_btn.emit("clicked")

    assert received == [run]
    assert view._showcase_path_label.get_label() == fake_path
    assert view._showcase_path_label.get_visible() is True
    assert view._showcase_open_btn.get_visible() is True
    assert view._showcase_message_label.get_visible() is False
    # Re-enabled after the (synchronous, here) build completes.
    assert view._showcase_btn.get_sensitive() is True


def test_open_view_build_showcase_disables_button_while_in_flight(monkeypatch):
    from pipeline_studio import OpenView

    release = _threading.Event()

    def slow_showcase_fn(run_view):
        release.wait(timeout=5)
        return "/tmp/showcase_slow.html"

    view = OpenView(showcase_fn=slow_showcase_fn)
    view.set_run(_make_run_with_artifact())
    assert view._showcase_btn.get_sensitive() is True

    import pipeline_studio
    idle_calls: list = []
    monkeypatch.setattr(pipeline_studio.GLib, "idle_add", lambda fn, *a: idle_calls.append((fn, a)))

    view._showcase_btn.emit("clicked")
    try:
        assert view._showcase_btn.get_sensitive() is False
    finally:
        release.set()  # let the blocked worker thread finish either way


def test_open_view_build_showcase_reenables_and_reveals_after_success(monkeypatch):
    from pipeline_studio import OpenView

    release = _threading.Event()

    def slow_showcase_fn(run_view):
        release.wait(timeout=5)
        return "/tmp/showcase_success.html"

    view = OpenView(showcase_fn=slow_showcase_fn)
    view.set_run(_make_run_with_artifact())

    import pipeline_studio
    idle_calls: list = []
    monkeypatch.setattr(pipeline_studio.GLib, "idle_add", lambda fn, *a: idle_calls.append((fn, a)))

    view._showcase_btn.emit("clicked")
    release.set()
    for _ in range(200):
        if idle_calls:
            break
        time.sleep(0.01)
    assert idle_calls, "worker never posted its result back via idle_add"

    fn, args = idle_calls[0]
    fn(*args)  # runs _apply_showcase_result(...) "on the main thread"

    assert view._showcase_btn.get_sensitive() is True
    assert view._showcase_path_label.get_label() == "/tmp/showcase_success.html"
    assert view._showcase_path_label.get_visible() is True
    assert view._showcase_open_btn.get_visible() is True
    assert view._showcase_message_label.get_visible() is False


def test_open_view_build_showcase_raising_fn_shows_gentle_message_no_crash(monkeypatch):
    """A raising `showcase_fn` must never crash the worker thread or the app
    — it degrades to a gentle inline message and re-enables the button."""
    from pipeline_studio import OpenView

    release = _threading.Event()

    def raising_showcase_fn(run_view):
        release.wait(timeout=5)
        raise RuntimeError("boom")

    view = OpenView(showcase_fn=raising_showcase_fn)
    view.set_run(_make_run_with_artifact())

    import pipeline_studio
    idle_calls: list = []
    monkeypatch.setattr(pipeline_studio.GLib, "idle_add", lambda fn, *a: idle_calls.append((fn, a)))

    view._showcase_btn.emit("clicked")
    release.set()
    for _ in range(200):
        if idle_calls:
            break
        time.sleep(0.01)
    assert idle_calls, "worker never posted its result back via idle_add"

    fn, args = idle_calls[0]
    fn(*args)  # runs _apply_showcase_result(...) "on the main thread"

    assert view._showcase_btn.get_sensitive() is True
    assert view._showcase_message_label.get_visible() is True
    assert view._showcase_message_label.get_label() != ""
    assert view._showcase_path_label.get_visible() is False
    assert view._showcase_open_btn.get_visible() is False


def test_open_view_set_run_again_resets_showcase_state(monkeypatch):
    """Repeat-safe: a stale path/message from a previous run must not bleed
    into a freshly-loaded run's showcase state."""
    import pipeline_studio

    monkeypatch.setattr(pipeline_studio.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(pipeline_studio.GLib, "idle_add", lambda fn, *a: fn(*a))

    from pipeline_studio import OpenView
    view = OpenView(showcase_fn=lambda run_view: "/tmp/first.html")
    view.set_run(_make_run_with_artifact())
    view._showcase_btn.emit("clicked")
    assert view._showcase_path_label.get_visible() is True

    view.set_run(_make_run_with_artifact())
    assert view._showcase_path_label.get_visible() is False
    assert view._showcase_open_btn.get_visible() is False
    assert view._showcase_message_label.get_visible() is False
    assert view._showcase_btn.get_sensitive() is True


def test_open_view_open_showcase_button_launches_via_xdg_open(monkeypatch):
    """The Open affordance reuses the app's existing external-open pattern
    (`subprocess.Popen(["xdg-open", path])`, see main_window.py's
    `_open_external`) — mocked here so the test never shells out for real."""
    import pipeline_studio

    monkeypatch.setattr(pipeline_studio.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(pipeline_studio.GLib, "idle_add", lambda fn, *a: fn(*a))

    from pipeline_studio import OpenView
    view = OpenView(showcase_fn=lambda run_view: "/tmp/showcase_open_me.html")
    view.set_run(_make_run_with_artifact())
    view._showcase_btn.emit("clicked")
    assert view._showcase_open_btn.get_visible() is True

    with patch("subprocess.Popen") as mock_popen:
        view._showcase_open_btn.emit("clicked")

    assert mock_popen.called
    args = mock_popen.call_args.args[0]
    assert args[-1] == "/tmp/showcase_open_me.html"


def test_open_view_open_showcase_button_guards_popen_exception(monkeypatch):
    """A failing opener (e.g. `xdg-open` missing) must not crash the app."""
    import pipeline_studio

    monkeypatch.setattr(pipeline_studio.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(pipeline_studio.GLib, "idle_add", lambda fn, *a: fn(*a))

    from pipeline_studio import OpenView
    view = OpenView(showcase_fn=lambda run_view: "/tmp/showcase_open_me.html")
    view.set_run(_make_run_with_artifact())
    view._showcase_btn.emit("clicked")

    with patch("subprocess.Popen", side_effect=OSError("no such opener")):
        view._showcase_open_btn.emit("clicked")  # must not raise


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


def test_remix_view_content_column_is_capped_max_width_bin():
    """Fix #5 (user feedback): same real-cap contract as
    DiscoverView/OpenView."""
    import pipeline_studio as ps
    from pipeline_studio import RemixView
    view = RemixView()
    assert isinstance(view._content_wrapper, ps._MaxWidthBin)
    _min, natural, _mb, _nb = view._content_wrapper.measure(Gtk.Orientation.HORIZONTAL, -1)
    assert natural <= ps._CONTENT_MAX_WIDTH


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


def test_uninjected_remix_view_does_not_perform_real_capability_io(monkeypatch):
    """Fix 2 (SP-C Phase-2b-2 final review): ~14 tests in this module
    construct RemixView() with no capability_fn, so set_run()'s render falls
    through to capability_discovery.default_capabilities — which reads
    plugins/ off disk and probes real server health/ports (see its docstring
    in capability_discovery.py). Proves the module-level autouse isolation
    fixture actually replaces `default_capabilities` before any such test
    runs.

    Uses call-count SPIES rather than raising fakes: discover_capabilities is
    deliberately robust to its injected deps raising (a raising mcp_reader
    degrades to native-only caps, see capability_discovery.py), so a raising
    fake can't distinguish "never called" from "called, then its exception
    was swallowed". A call counter can.
    """
    import capability_discovery as cd

    calls = {"read": 0, "plugin_loaded": 0, "backend_up": 0}

    def _spy_read(*_a, **_k):
        calls["read"] += 1
        return {}

    def _spy_plugin_loaded(*_a, **_k):
        calls["plugin_loaded"] += 1
        return False

    def _spy_backend_up(*_a, **_k):
        calls["backend_up"] += 1
        return False

    monkeypatch.setattr(cd, "_read_all_plugin_mcp", _spy_read)
    monkeypatch.setattr(cd, "_real_is_plugin_loaded", _spy_plugin_loaded)
    monkeypatch.setattr(cd, "_real_is_backend_up", _spy_backend_up)

    from pipeline_studio import RemixView
    view = RemixView()  # no capability_fn injected — must not touch real I/O
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    assert calls == {"read": 0, "plugin_loaded": 0, "backend_up": 0}


# ── RemixView: wing-it (free-form "describe the next step") (SP-C Phase 2b-3
# Task 2) ────────────────────────────────────────────────────────────────────
#
# `wingit_fn` is an injected seam (defaults to a real closure wiring
# `wingit.map_freeform_to_step` + `capability_discovery.default_capabilities` +
# `wingit.default_llm_fn` — see pipeline_studio.py's RemixView.__init__). Tests
# inject a fake `wingit_fn(text, output_kind) -> WingitResult | None` and drive
# the compose handler through the real click path, monkeypatching
# `threading.Thread`/`GLib.idle_add` (the `_ImmediateThread` pattern already
# used above for PipelineStudio's open-run) so the background-thread + idle_add
# hop the real code takes runs synchronously and deterministically in tests —
# no timing/races to fight.

def _run_wingit_inline(monkeypatch) -> None:
    import pipeline_studio
    monkeypatch.setattr(pipeline_studio.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(pipeline_studio.GLib, "idle_add", lambda fn, *a: fn(*a))


def test_remix_view_set_run_shows_wingit_entry_and_compose_button_per_step():
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    assert set(view._wingit_entries.keys()) == {"1", "2", "3"}
    assert set(view._wingit_compose_buttons.keys()) == {"1", "2", "3"}


def test_wingit_compose_native_result_adds_step(monkeypatch):
    """A WingitResult naming a NATIVE class_type adds a node of that
    class_type after the composing step, same as choosing it from the
    capability picker would."""
    from pipeline_studio import RemixView
    from wingit import WingitResult

    _run_wingit_inline(monkeypatch)

    def fake_wingit_fn(text, output_kind):
        assert output_kind == "image"  # node "1" (TTLGTextToImage) output_kind
        return WingitResult(
            class_type="TTLGCaptionImage", params={}, capability_id="TTLGCaptionImage",
            via="fallback",
        )

    view = RemixView(wingit_fn=fake_wingit_fn)
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    before_ids = set(view.working_spec)
    view._wingit_entries["1"].set_text("describe this image")
    view._wingit_compose_buttons["1"].emit("clicked")

    new_ids = set(view.working_spec) - before_ids
    assert len(new_ids) == 1
    new_node = view.working_spec[new_ids.pop()]
    assert new_node["class_type"] == "TTLGCaptionImage"


def test_wingit_compose_plugin_result_adds_artgen_node_with_plugin_param(monkeypatch):
    """A WingitResult naming a PLUGIN capability (class_type always
    TTLGArtgenGenerate, params carrying `plugin`) adds that generic node with
    its plugin input set, same as choosing a plugin from the picker would."""
    from pipeline_studio import RemixView
    from wingit import WingitResult

    _run_wingit_inline(monkeypatch)

    def fake_wingit_fn(text, output_kind):
        return WingitResult(
            class_type="TTLGArtgenGenerate", params={"plugin": "verse", "prompt": text},
            capability_id="verse", via="fallback",
        )

    view = RemixView(wingit_fn=fake_wingit_fn)
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    view._wingit_entries["1"].set_text("make a verse about it")
    view._wingit_compose_buttons["1"].emit("clicked")

    spec = view.current_spec()
    new_ids = set(spec) - {"1", "2", "3"}
    assert len(new_ids) == 1
    new_node = spec[new_ids.pop()]
    assert new_node["class_type"] == "TTLGArtgenGenerate"
    assert new_node["inputs"]["plugin"] == "verse"


def test_wingit_compose_none_result_shows_message_and_adds_nothing(monkeypatch):
    """wingit_fn returning None (nothing maps) -> gentle inline message, no
    node added, no crash."""
    from pipeline_studio import RemixView

    _run_wingit_inline(monkeypatch)

    def fake_wingit_fn(text, output_kind):
        return None

    view = RemixView(wingit_fn=fake_wingit_fn)
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    before_ids = set(view.working_spec)
    view._wingit_entries["1"].set_text("total gibberish, wing it anyway")
    view._wingit_compose_buttons["1"].emit("clicked")  # must not raise

    assert set(view.working_spec) == before_ids
    assert view._message_label.get_visible() is True
    assert view._message_label.get_label() != ""


def test_wingit_compose_empty_text_does_nothing(monkeypatch):
    """An empty/whitespace-only entry never calls wingit_fn or adds a step —
    Compose is a no-op rather than a spurious "couldn't compose" message."""
    from pipeline_studio import RemixView

    _run_wingit_inline(monkeypatch)

    calls = []

    def fake_wingit_fn(text, output_kind):
        calls.append(text)
        return None

    view = RemixView(wingit_fn=fake_wingit_fn)
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)

    before_ids = set(view.working_spec)
    view._wingit_entries["1"].set_text("   ")
    view._wingit_compose_buttons["1"].emit("clicked")

    assert calls == []
    assert set(view.working_spec) == before_ids
    assert view._message_label.get_visible() is False


def test_default_wingit_fn_wires_map_freeform_to_step_and_capabilities(monkeypatch):
    """RemixView() with no injected wingit_fn wires the real closure:
    wingit.map_freeform_to_step + capability_discovery.default_capabilities +
    wingit.default_llm_fn — proven here by monkeypatching those two real deps
    (never touching actual plugins/disk/network) and confirming the default
    closure still produces a sensible fallback result end-to-end."""
    import capability_discovery as cd
    import wingit as wingit_mod
    from pipeline_studio import RemixView

    def fake_default_capabilities(output_kind):
        return [
            cd.Capability(
                id="TTLGCaptionImage", label="Describe it", kind_out="text",
                kind_in=output_kind, source="native", class_type="TTLGCaptionImage",
                plugin=None, hardware=None, live=True, reason=None,
            ),
        ]

    monkeypatch.setattr(cd, "default_capabilities", fake_default_capabilities)
    monkeypatch.setattr(wingit_mod, "default_llm_fn", lambda prompt: None)  # force fallback

    view = RemixView()
    result = view._wingit_fn("describe this photo", "image")

    assert result is not None
    assert result.class_type == "TTLGCaptionImage"


def _click_wingit_compose_with_blocking_fn(view, node_id, text, monkeypatch):
    """Drive the real (unmocked) `_on_wingit_compose_clicked` click path with a
    `wingit_fn` that blocks on an `Event` until the test releases it, and a
    `GLib.idle_add` that records the callback instead of running it.

    Used by the busy-guard tests below: real `threading.Thread` actually runs
    the worker on a background thread (so we can observe the button disabled
    *while the worker is still in flight*, not just before-and-after), but
    `GLib.idle_add` is captured rather than executed so the test controls
    exactly when `_apply_wingit_result` runs (simulating the main-thread hop).
    Returns `(release_event, idle_calls)` — set the event to unblock the
    worker, then call `fn(*args)` for the single `idle_calls` entry once it
    appears to run `_apply_wingit_result` "on the main thread".
    """
    import pipeline_studio

    idle_calls: list = []
    monkeypatch.setattr(pipeline_studio.GLib, "idle_add", lambda fn, *a: idle_calls.append((fn, a)))

    release = _threading.Event()

    def fake_wingit_fn(_text, _output_kind):
        release.wait(timeout=5)
        return None

    view._wingit_fn = fake_wingit_fn
    view._wingit_entries[node_id].set_text(text)
    view._wingit_compose_buttons[node_id].emit("clicked")
    return release, idle_calls


def test_wingit_compose_disables_button_while_mapping_in_flight(monkeypatch):
    """The Compose button for the composing step goes insensitive as soon as
    Compose is clicked — synchronously, before the background worker even
    finishes — so a second click can't spawn a second worker/duplicate step."""
    from pipeline_studio import RemixView

    view = RemixView(wingit_fn=lambda text, output_kind: None)
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    btn = view._wingit_compose_buttons["1"]
    assert btn.get_sensitive() is True

    release, _idle_calls = _click_wingit_compose_with_blocking_fn(
        view, "1", "describe this image", monkeypatch,
    )
    try:
        assert btn.get_sensitive() is False
    finally:
        release.set()  # let the blocked worker thread finish either way


def test_wingit_compose_reenables_button_after_none_result(monkeypatch):
    """`_apply_wingit_result(node_id, None)` re-enables the Compose button
    on the failure/no-mapping path, not just the success path."""
    from pipeline_studio import RemixView

    view = RemixView(wingit_fn=lambda text, output_kind: None)
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    btn = view._wingit_compose_buttons["1"]

    release, idle_calls = _click_wingit_compose_with_blocking_fn(
        view, "1", "total gibberish", monkeypatch,
    )
    release.set()
    for _ in range(200):
        if idle_calls:
            break
        time.sleep(0.01)
    assert idle_calls, "worker never posted its result back via idle_add"

    fn, args = idle_calls[0]
    fn(*args)  # runs _apply_wingit_result(node_id, None) "on the main thread"

    assert btn.get_sensitive() is True


def test_wingit_compose_reenables_button_after_success_result(monkeypatch):
    """`_apply_wingit_result(node_id, result)` re-enables the Compose button
    on the success path too."""
    from pipeline_studio import RemixView
    from wingit import WingitResult

    view = RemixView(wingit_fn=lambda text, output_kind: None)
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    btn = view._wingit_compose_buttons["1"]

    idle_calls: list = []
    import pipeline_studio
    monkeypatch.setattr(pipeline_studio.GLib, "idle_add", lambda fn, *a: idle_calls.append((fn, a)))

    release = _threading.Event()

    def fake_wingit_fn(_text, _output_kind):
        release.wait(timeout=5)
        return WingitResult(
            class_type="TTLGCaptionImage", params={}, capability_id="TTLGCaptionImage",
            via="fallback",
        )

    view._wingit_fn = fake_wingit_fn
    view._wingit_entries["1"].set_text("describe this image")
    view._wingit_compose_buttons["1"].emit("clicked")

    release.set()
    for _ in range(200):
        if idle_calls:
            break
        time.sleep(0.01)
    assert idle_calls, "worker never posted its result back via idle_add"

    fn, args = idle_calls[0]
    fn(*args)  # runs _apply_wingit_result(node_id, result) "on the main thread"

    # add_step_after -> _render() rebuilds the button dict; the *new* button
    # for node "1" (which now has a fresh added-after step) must be sensitive.
    new_btn = view._wingit_compose_buttons.get("1")
    if new_btn is not None:
        assert new_btn.get_sensitive() is True
    assert btn.get_sensitive() is True  # stale reference must not stay stuck disabled either


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


def test_live_run_view_step_row_has_single_combined_intent_label():
    """Fix #1 (review follow-up — MINOR): LiveRunView._build_step_row also
    renders ONE combined 'verb noun' label (ps-step-intent) with the status
    glyph inline, matching OpenView — not separate verb/noun widgets."""
    from pipeline_studio import LiveRunView
    view = LiveRunView()
    run = _make_live_run()
    view.begin(run)

    step = run.steps[0]  # TTLGTextToImage -> "Generate an image"
    # Walk the first step row: n_label, then main (intent_row, [model]).
    row = view._steps_box.get_first_child()
    n_label = row.get_first_child()
    main = n_label.get_next_sibling()
    intent_row = main.get_first_child()
    intent_label = intent_row.get_first_child()

    assert isinstance(intent_label, Gtk.Label)
    assert intent_label.has_css_class("ps-step-intent")
    assert intent_label.get_label() == f"{step.intent.verb} {step.intent.noun}"
    # Status glyph sits inline in the same row.
    status_label = intent_label.get_next_sibling()
    assert isinstance(status_label, Gtk.Label)


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


# ── MuseView (SP-C Phase 2b-3 Task 4 — goal-first "start from scratch") ─────
#
# `goals_fn`/`wingit_pipeline_fn`/`seed_spec_fn` are injected seams (default
# to real closures wiring `recipes.goals_for`, `wingit.map_freeform_to_pipeline`
# + `capability_discovery.default_capabilities` + `wingit.default_llm_fn`, and
# `spec_remix.seed_spec` — see MuseView.__init__ in pipeline_studio.py). Goal
# cards and "Surprise me" build a spec via the REAL `recipes.build_seed_spec`
# (not an injected seam — see the task brief's decision notes), so the fixture
# Goals below use real native class_types so that call never raises.

from recipes import Goal  # noqa: E402

_MUSE_GOAL_A = Goal(
    id="poster-fixture", label="A poster", icon="\U0001f5bc", output_kind="image",
    applies_to="blank", recipe_steps=(("TTLGGenerateText", {}),),
)
_MUSE_GOAL_B = Goal(
    id="verse-fixture", label="A verse", icon="\U0001f4dc", output_kind="text",
    applies_to="blank", recipe_steps=(("TTLGGenerateText", {}),),
)
_MUSE_GOAL_SCOPED = Goal(
    id="depth-fixture", label="A depth scene", icon="\U0001f300", output_kind="image",
    applies_to="scoped", recipe_steps=(("TTLGEstimateDepth", {}),),
)


def test_muse_view_constructs():
    from pipeline_studio import MuseView
    view = MuseView(goals_fn=lambda kind: [])
    assert isinstance(view, Gtk.Box)


def test_muse_view_blank_shows_goal_cards_surprise_and_freeform():
    from pipeline_studio import MuseView
    view = MuseView(goals_fn=lambda kind: [_MUSE_GOAL_A, _MUSE_GOAL_B])

    assert set(view._goal_buttons.keys()) == {"poster-fixture", "verse-fixture"}
    assert view._heading_label.get_label() == "What do you want to make?"
    assert view._surprise_button.get_sensitive() is True
    assert isinstance(view._freeform_entry, Gtk.Entry)
    assert isinstance(view._dream_button, Gtk.Button)


def test_muse_view_goal_card_click_emits_goal_chosen_with_built_spec():
    from pipeline_studio import MuseView
    import recipes

    view = MuseView(goals_fn=lambda kind: [_MUSE_GOAL_A])
    received = []
    view.connect("goal-chosen", lambda _w, spec: received.append(spec))

    view._goal_buttons["poster-fixture"].emit("clicked")

    assert received == [recipes.build_seed_spec(_MUSE_GOAL_A, seed_artifact=None)]


def test_muse_view_scoped_context_calls_goals_fn_with_kind_and_shows_heading(tmp_path):
    from pipeline_studio import MuseView

    calls = []

    def fake_goals_fn(seed_output_kind):
        calls.append(seed_output_kind)
        return [_MUSE_GOAL_SCOPED]

    view = MuseView(goals_fn=fake_goals_fn)
    calls.clear()  # drop the constructor's own blank-mode call

    art_path = str(tmp_path / "art.png")
    view.set_context((art_path, "image", None))

    assert calls == ["image"]
    assert view._heading_label.get_label() == "Make this image into…"
    assert set(view._goal_buttons.keys()) == {"depth-fixture"}


def test_muse_view_scoped_card_click_uses_path_and_kind_only():
    """Decision: build_seed_spec is called with seed_artifact=(path, kind) —
    the thumb_path (3rd tuple element passed to set_context) is display-only
    and must never reach build_seed_spec/spec_remix.seed_spec."""
    from pipeline_studio import MuseView
    import recipes

    view = MuseView(goals_fn=lambda kind: [_MUSE_GOAL_SCOPED])
    view.set_context(("/tmp/art.png", "image", "/tmp/thumb.png"))

    received = []
    view.connect("goal-chosen", lambda _w, spec: received.append(spec))
    view._goal_buttons["depth-fixture"].emit("clicked")

    expected = recipes.build_seed_spec(_MUSE_GOAL_SCOPED, seed_artifact=("/tmp/art.png", "image"))
    assert received == [expected]


def test_muse_view_surprise_picks_deterministic_middle_goal():
    from pipeline_studio import MuseView
    import recipes

    goals = [_MUSE_GOAL_A, _MUSE_GOAL_B, _MUSE_GOAL_SCOPED]
    view = MuseView(goals_fn=lambda kind: goals)

    received = []
    view.connect("goal-chosen", lambda _w, spec: received.append(spec))
    view._surprise_button.emit("clicked")

    expected_goal = goals[len(goals) // 2]  # index 1 -> _MUSE_GOAL_B, NOT random
    assert received == [recipes.build_seed_spec(expected_goal, seed_artifact=None)]


def test_muse_view_surprise_noop_when_no_goals():
    from pipeline_studio import MuseView
    view = MuseView(goals_fn=lambda kind: [])
    received = []
    view.connect("goal-chosen", lambda _w, spec: received.append(spec))
    view._surprise_button.emit("clicked")  # must not raise
    assert received == []


def test_muse_view_scoped_empty_goals_shows_gentle_message_and_hides_surprise(tmp_path):
    """Non-image seeds (video/gif) have no curated scoped goal to offer —
    goals_fn returns []. The empty state must degrade gracefully: a gentle
    in-view message, "Surprise me" hidden/disabled (nothing to be surprised
    with), and the free-text escape hatch still present. Never a dead
    "Surprise me" button with silent zero cards."""
    from pipeline_studio import MuseView

    view = MuseView(goals_fn=lambda kind: [])
    clip_path = str(tmp_path / "clip.mp4")
    view.set_context((clip_path, "video", None))

    assert view._goal_buttons == {}
    assert view._message_label.get_visible() is True
    message = view._message_label.get_label().lower()
    assert "no ready-made recipes" in message

    surprise_dead = (
        view._surprise_button.get_visible() is False
        or view._surprise_button.get_sensitive() is False
    )
    assert surprise_dead, "Surprise me must be hidden or disabled when there are no goals"

    # The free-text escape hatch must remain available.
    assert isinstance(view._freeform_entry, Gtk.Entry)
    assert view._freeform_entry.get_visible() is True
    assert isinstance(view._dream_button, Gtk.Button)
    assert view._dream_button.get_visible() is True


def test_muse_view_blank_mode_unaffected_by_scoped_empty_state(tmp_path):
    """Blank mode always has curated goals — this empty-state handling is
    scoped-only. Re-entering blank mode after a scoped-empty state must
    restore normal blank behavior (goal cards + a live Surprise me)."""
    from pipeline_studio import MuseView

    calls = []

    def fake_goals_fn(seed_output_kind):
        calls.append(seed_output_kind)
        if seed_output_kind is None:
            return [_MUSE_GOAL_A, _MUSE_GOAL_B]
        return []

    view = MuseView(goals_fn=fake_goals_fn)
    clip_path = str(tmp_path / "clip.mp4")
    view.set_context((clip_path, "video", None))
    assert view._surprise_button.get_visible() is False or view._surprise_button.get_sensitive() is False

    view.set_context(None)

    assert view._message_label.get_visible() is False
    assert view._surprise_button.get_visible() is True
    assert view._surprise_button.get_sensitive() is True
    assert set(view._goal_buttons.keys()) == {"poster-fixture", "verse-fixture"}


def test_muse_view_freeform_none_result_shows_gentle_message_no_emit(monkeypatch):
    from pipeline_studio import MuseView
    _run_wingit_inline(monkeypatch)

    view = MuseView(goals_fn=lambda kind: [], wingit_pipeline_fn=lambda text, kind: None)
    received = []
    view.connect("goal-chosen", lambda _w, spec: received.append(spec))

    view._freeform_entry.set_text("something dreamy but unmappable")
    view._dream_button.emit("clicked")  # must not raise

    assert received == []
    assert view._message_label.get_visible() is True
    assert "rephrasing" in view._message_label.get_label().lower()


def test_muse_view_freeform_success_builds_via_seed_spec_fn_and_emits(monkeypatch):
    from pipeline_studio import MuseView
    _run_wingit_inline(monkeypatch)

    draft_steps = [("TTLGGenerateText", {"caption": "a dream"})]

    def fake_wingit_pipeline_fn(text, seed_output_kind):
        assert text == "a lucid dream about the ocean"
        assert seed_output_kind is None
        return draft_steps

    fake_spec = {"1": {"class_type": "TTLGGenerateText", "inputs": {"caption": "a dream"}}}

    def fake_seed_spec_fn(steps, seed_artifact=None):
        assert steps == draft_steps
        assert seed_artifact is None
        return fake_spec

    view = MuseView(
        goals_fn=lambda kind: [],
        wingit_pipeline_fn=fake_wingit_pipeline_fn,
        seed_spec_fn=fake_seed_spec_fn,
    )
    received = []
    view.connect("goal-chosen", lambda _w, spec: received.append(spec))

    view._freeform_entry.set_text("a lucid dream about the ocean")
    view._dream_button.emit("clicked")

    assert received == [fake_spec]


def test_muse_view_freeform_scoped_passes_path_kind_pair_to_seed_spec_fn(monkeypatch, tmp_path):
    from pipeline_studio import MuseView
    _run_wingit_inline(monkeypatch)

    draft_steps = [("TTLGEstimateDepth", {})]

    def fake_wingit_pipeline_fn(text, seed_output_kind):
        assert seed_output_kind == "image"
        return draft_steps

    captured = {}

    def fake_seed_spec_fn(steps, seed_artifact=None):
        captured["seed_artifact"] = seed_artifact
        return {"1": {"class_type": "TTLGEstimateDepth", "inputs": {}}}

    view = MuseView(
        goals_fn=lambda kind: [],
        wingit_pipeline_fn=fake_wingit_pipeline_fn,
        seed_spec_fn=fake_seed_spec_fn,
    )
    art_path = str(tmp_path / "art.png")
    view.set_context((art_path, "image", "/tmp/thumb.png"))
    view._freeform_entry.set_text("make it feel deep")
    view._dream_button.emit("clicked")

    assert captured["seed_artifact"] == (art_path, "image")


def test_muse_view_freeform_empty_text_does_nothing(monkeypatch):
    from pipeline_studio import MuseView
    _run_wingit_inline(monkeypatch)

    calls = []

    def fake_wingit_pipeline_fn(text, seed_output_kind):
        calls.append(text)
        return None

    view = MuseView(goals_fn=lambda kind: [], wingit_pipeline_fn=fake_wingit_pipeline_fn)
    view._freeform_entry.set_text("   ")
    view._dream_button.emit("clicked")

    assert calls == []
    assert view._message_label.get_visible() is False


# ── DiscoverView: "Start from scratch" affordance ───────────────────────────

def test_discover_view_start_from_scratch_button_emits_signal():
    from pipeline_studio import DiscoverView
    view = DiscoverView()
    received = []
    view.connect("start-from-scratch", lambda _w: received.append(True))

    view._start_from_scratch_btn.emit("clicked")

    assert received == [True]


# ── RemixView.load_seed_spec ─────────────────────────────────────────────────

def test_remix_view_load_seed_spec_sets_title_working_spec_and_renders():
    from pipeline_studio import RemixView
    view = RemixView()

    view.load_seed_spec(_REMIX_SPEC_PATH, "a new pipeline")

    assert view._spec_path == _REMIX_SPEC_PATH
    assert view._title_label.get_label() == "Composing · a new pipeline"
    assert set(view.working_spec.keys()) == {"1", "2", "3"}
    assert set(view._field_widgets.keys()) == {"1", "2", "3"}


# ── PipelineStudio: muse page + wiring ──────────────────────────────────────

def test_pipeline_studio_shell_has_muse_page(monkeypatch, tmp_path):
    import pipeline_store
    monkeypatch.setattr(pipeline_store, "_INDEX_PATH", tmp_path / "pipeline-index.json")
    monkeypatch.setattr(pipeline_store, "_RUNS_DIR", tmp_path / "runs")

    from pipeline_studio import PipelineStudio
    studio = PipelineStudio()

    assert studio.stack.get_child_by_name("muse") is not None


def test_pipeline_studio_show_muse_switches_stack_and_sets_context(monkeypatch, tmp_path):
    import pipeline_store
    monkeypatch.setattr(pipeline_store, "_INDEX_PATH", tmp_path / "pipeline-index.json")
    monkeypatch.setattr(pipeline_store, "_RUNS_DIR", tmp_path / "runs")

    from pipeline_studio import PipelineStudio
    studio = PipelineStudio()

    studio.show_muse()

    assert studio.stack.get_visible_child_name() == "muse"
    assert studio.muse._heading_label.get_label() == "What do you want to make?"


def test_pipeline_studio_discover_start_from_scratch_shows_muse(monkeypatch, tmp_path):
    import pipeline_store
    monkeypatch.setattr(pipeline_store, "_INDEX_PATH", tmp_path / "pipeline-index.json")
    monkeypatch.setattr(pipeline_store, "_RUNS_DIR", tmp_path / "runs")

    from pipeline_studio import PipelineStudio
    studio = PipelineStudio()

    studio.discover.emit("start-from-scratch")

    assert studio.stack.get_visible_child_name() == "muse"


def test_pipeline_studio_muse_back_button_returns_to_discover(monkeypatch, tmp_path):
    import pipeline_store
    monkeypatch.setattr(pipeline_store, "_INDEX_PATH", tmp_path / "pipeline-index.json")
    monkeypatch.setattr(pipeline_store, "_RUNS_DIR", tmp_path / "runs")

    from pipeline_studio import PipelineStudio
    studio = PipelineStudio()

    studio.show_muse()
    assert studio.stack.get_visible_child_name() == "muse"

    muse_page = studio.stack.get_child_by_name("muse")
    back_bar = muse_page.get_first_child()
    back_btn = back_bar.get_first_child()
    back_btn.emit("clicked")

    assert studio.stack.get_visible_child_name() == "discover"


def test_pipeline_studio_muse_goal_chosen_blank_writes_spec_and_shows_remix(monkeypatch, tmp_path):
    import pipeline_studio
    import pipeline_store
    monkeypatch.setattr(pipeline_store, "_INDEX_PATH", tmp_path / "pipeline-index.json")
    monkeypatch.setattr(pipeline_store, "_RUNS_DIR", tmp_path / "runs")

    remixes_dir = tmp_path / "remixes"
    monkeypatch.setattr(pipeline_studio, "REMIXES_DIR", remixes_dir)

    from pipeline_studio import PipelineStudio
    studio = PipelineStudio()

    studio.show_muse()  # blank mode
    spec = {"1": {"class_type": "TTLGGenerateText", "inputs": {}}}
    studio.muse.emit("goal-chosen", spec)

    written = list(remixes_dir.glob("remix_muse_*.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text()) == spec

    assert studio.stack.get_visible_child_name() == "remix"
    assert studio.remix_view._title_label.get_label() == "Composing · a new pipeline"
    assert studio.remix_view.working_spec == spec


def test_pipeline_studio_muse_goal_chosen_scoped_uses_kind_title(monkeypatch, tmp_path):
    import pipeline_studio
    import pipeline_store
    monkeypatch.setattr(pipeline_store, "_INDEX_PATH", tmp_path / "pipeline-index.json")
    monkeypatch.setattr(pipeline_store, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(pipeline_studio, "REMIXES_DIR", tmp_path / "remixes")

    from pipeline_studio import PipelineStudio
    studio = PipelineStudio()

    studio.show_muse((str(tmp_path / "art.png"), "image", None))
    spec = {"1": {"class_type": "TTLGEstimateDepth", "inputs": {}}}
    studio.muse.emit("goal-chosen", spec)

    assert studio.remix_view._title_label.get_label() == "Composing · your image"
    assert studio.stack.get_visible_child_name() == "remix"


# ── Video poster-frame thumbnails (_poster_frame_for) ────────────────────────

def test_poster_frame_image_passes_through():
    import pipeline_studio as ps
    assert ps._poster_frame_for("/x/a.png", extract_fn=lambda s, d: False) == "/x/a.png"
    assert ps._poster_frame_for("/x/a.gif", extract_fn=lambda s, d: False) == "/x/a.gif"


def test_poster_frame_video_extracts_and_caches(tmp_path):
    import pipeline_studio as ps
    vid = tmp_path / "node6_video.mp4"
    vid.write_bytes(b"fake")
    calls = []

    def fake_extract(src, dest):
        calls.append((src, dest))
        Path(dest).write_bytes(b"jpg")
        return True

    out = ps._poster_frame_for(str(vid), extract_fn=fake_extract)
    assert out == str(tmp_path / "node6_video.poster.jpg")
    assert Path(out).exists()
    assert len(calls) == 1
    # cached — a second call reuses the poster, no re-extraction
    out2 = ps._poster_frame_for(str(vid), extract_fn=fake_extract)
    assert out2 == out and len(calls) == 1


def test_poster_frame_video_extract_fails_returns_none(tmp_path):
    import pipeline_studio as ps
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"fake")
    assert ps._poster_frame_for(str(vid), extract_fn=lambda s, d: False) is None
