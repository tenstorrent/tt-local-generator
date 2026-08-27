"""GTK-level tests for `_StatusBar`'s per-function segments.

Covers the widget half of the by-function status bar: four segments always
present, each painting its own glyph/CSS, and only a launching segment carrying
an elapsed counter. The pure state resolution lives in
`tests/test_status_segments.py`.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

if not Gtk.init_check():
    pytest.skip("no display available", allow_module_level=True)

import main_window as mw  # noqa: E402
import status_segments as ss  # noqa: E402
from model_status import Status  # noqa: E402


@pytest.fixture()
def bar(monkeypatch):
    """A `_StatusBar` with its tt-smi/disk polling thread stubbed out.

    `_poll_loop` shells out to tt-smi (up to ~13 s on a cold start) and is
    irrelevant to the segments — stub it so these tests stay instant and never
    touch hardware.
    """
    monkeypatch.setattr(mw._StatusBar, "_poll_loop", lambda self: None)
    b = mw._StatusBar(start_cb=lambda: None, stop_cb=lambda: None)
    yield b
    b._stop_timer()


# ── Structure ────────────────────────────────────────────────────────────────

def test_all_four_segments_are_built(bar):
    assert list(bar._seg_dots) == list(ss.SEGMENT_KEYS)
    assert [bar._seg_labels[k] for k in ss.SEGMENT_KEYS] == [
        "Prompt", "Image", "Video", "Art LLM"
    ]


def test_segments_start_off(bar):
    assert all(s == Status.OFF for s in bar._seg_state.values())
    assert all(d.get_label() == "○" for d in bar._seg_dots.values())


def test_there_is_no_aggregate_dot_left(bar):
    """The single dot this replaces is gone, not merely hidden — a leftover
    would be a second, contradictory source of truth on the same bar."""
    assert not hasattr(bar, "_srv_dot")
    assert not hasattr(bar, "_srv_lbl")
    assert not hasattr(bar, "update_server")
    assert not hasattr(bar, "update_starting")


# ── Painting ─────────────────────────────────────────────────────────────────

def test_update_segments_paints_glyph_and_css(bar):
    bar.update_segments({"video": Status.READY, "image": Status.ERROR})

    assert bar._seg_dots["video"].get_label() == "●"
    assert bar._seg_dots["video"].has_css_class("tt-statusbar-dot-ready")
    assert bar._seg_names["video"].has_css_class("tt-statusbar-segname-ready")

    assert bar._seg_dots["image"].get_label() == "✕"
    assert bar._seg_dots["image"].has_css_class("tt-statusbar-dot-error")


def test_repaint_clears_the_previous_state_class(bar):
    bar.update_segments({"video": Status.READY})
    bar.update_segments({"video": Status.OFF})
    assert not bar._seg_dots["video"].has_css_class("tt-statusbar-dot-ready")
    assert bar._seg_dots["video"].has_css_class("tt-statusbar-dot-offline")
    assert not bar._seg_names["video"].has_css_class("tt-statusbar-segname-ready")


def test_partial_map_leaves_other_segments_alone(bar):
    """An optimistic `mark_starting()` must not be blanked by a partial map."""
    bar.mark_starting("video")
    bar.update_segments({"prompt": Status.READY})
    assert bar._seg_state["video"] == Status.STARTING


def test_unknown_segment_key_is_ignored(bar):
    bar.update_segments({"not-a-segment": Status.READY})  # must not raise
    assert "not-a-segment" not in bar._seg_state


# ── Elapsed counter ──────────────────────────────────────────────────────────

def test_only_a_starting_segment_shows_an_elapsed_counter(bar):
    bar.update_segments({"video": Status.STARTING, "prompt": Status.READY})
    assert bar._seg_names["video"].get_label().startswith("Video ")
    assert bar._seg_names["prompt"].get_label() == "Prompt"


def test_elapsed_clock_is_not_reset_by_repeated_starting_pushes(bar):
    """The exact failure of the retired `update_starting()`: it reset the timer
    to 0:00 on every call, so calling it from each poll tick froze the counter.
    """
    bar.update_segments({"video": Status.STARTING})
    first_ts = bar._seg_start_ts["video"]
    time.sleep(0.05)
    bar.update_segments({"video": Status.STARTING})
    assert bar._seg_start_ts["video"] == first_ts


def test_re_entering_starting_does_reset_the_clock(bar):
    bar.update_segments({"video": Status.STARTING})
    first_ts = bar._seg_start_ts["video"]
    bar.update_segments({"video": Status.READY})
    time.sleep(0.05)
    bar.update_segments({"video": Status.STARTING})
    assert bar._seg_start_ts["video"] > first_ts


def test_timer_runs_only_while_something_is_starting(bar):
    assert bar._timer_id is None
    bar.update_segments({"video": Status.STARTING})
    assert bar._timer_id is not None
    bar.update_segments({"video": Status.READY})
    assert bar._timer_id is None


def test_timer_survives_until_the_last_starting_segment_settles(bar):
    bar.update_segments({"video": Status.STARTING, "image": Status.STARTING})
    bar.update_segments({"video": Status.READY, "image": Status.STARTING})
    assert bar._timer_id is not None
    bar.update_segments({"video": Status.READY, "image": Status.READY})
    assert bar._timer_id is None


def test_tick_drops_its_source_when_nothing_is_starting(bar):
    """Defensive: a stray tick must return False so GLib retires the source."""
    assert bar._tick() is False
    assert bar._timer_id is None


# ── Tooltips (where the detail lives) ────────────────────────────────────────

def test_tooltip_reports_state_per_segment(bar):
    bar.update_segments({"video": Status.READY, "image": Status.OFF})
    assert bar._seg_dots["video"].get_tooltip_text() == "Video: ready"
    assert bar._seg_names["image"].get_tooltip_text() == "Image: off"


def test_set_phase_lands_in_the_tooltip_not_the_label(bar):
    """A chatty startup phase must never widen the bar."""
    bar.update_segments({"video": Status.STARTING})
    bar.set_phase("loading weights")
    assert "loading weights" in bar._seg_dots["video"].get_tooltip_text()
    assert "loading weights" not in bar._seg_names["video"].get_label()


def test_set_phase_is_ignored_when_nothing_is_starting(bar):
    bar.update_segments({"video": Status.READY})
    bar.set_phase("loading weights")  # stale tail callback — must not raise
    assert bar._seg_dots["video"].get_tooltip_text() == "Video: ready"


def test_mark_error_message_shows_in_the_tooltip(bar):
    bar.mark_error("video", "start failed — click for log")
    assert bar._seg_state["video"] == Status.ERROR
    assert bar._seg_dots["video"].get_tooltip_text() == (
        "Video: start failed — click for log"
    )


def test_error_message_is_cleared_when_the_segment_recovers(bar):
    bar.mark_error("video", "start failed — click for log")
    bar.update_segments({"video": Status.READY})
    assert bar._seg_dots["video"].get_tooltip_text() == "Video: ready"
    assert bar._seg_error["video"] == ""


# ── The popover is untouched ─────────────────────────────────────────────────

def test_capability_popover_rows_still_work(bar):
    """The bar is the glance; the popover is the detail. `update_capability`
    keeps naming the actual model behind each capability."""
    bar.update_capability("video", True, "Wan2.2-T2V-A14B")
    lbl = bar._cap_rows["video"]
    assert lbl.get_label() == "● Wan2.2-T2V-A14B"
    assert lbl.has_css_class("cap-row-ready")


def test_stop_retires_the_tick_source(bar):
    """Closing the window must not leave a 1 s timeout firing against a dead
    widget — `stop()` retires the tick source as well as the poll thread."""
    bar.update_segments({"video": Status.STARTING})
    assert bar._timer_id is not None
    bar.stop()
    assert bar._timer_id is None


# ── A painted failure must survive the next poll ─────────────────────────────
#
# `update_segments()` applies the snapshot unconditionally, so a locally-painted
# ERROR was erased by the very next tick — the failure tooltip vanished before
# anyone could read it. (Copilot PR#26 review.)

def test_mark_error_survives_the_next_snapshot(bar):
    bar.mark_error("video", "start failed — click for log")
    bar.update_segments({"video": Status.OFF})
    assert bar._seg_state["video"] == Status.ERROR
    assert bar._seg_dots["video"].get_tooltip_text() == (
        "Video: start failed — click for log"
    )


def test_mark_error_survives_a_lingering_starting_report(bar):
    """The concrete case: the start script died but ModelStatusService may still
    report STARTING until its bookkeeping clears. The bar must not flip back to
    a ticking clock on a server we know has failed."""
    bar.mark_error("video", "start failed — click for log")
    bar.update_segments({"video": Status.STARTING})
    assert bar._seg_state["video"] == Status.ERROR
    assert bar._timer_id is None, "a failed segment must not run an elapsed timer"


def test_a_failed_segment_clears_once_it_actually_comes_up(bar):
    """READY is real evidence the failure is over — the error must not be
    sticky forever."""
    bar.mark_error("video", "start failed — click for log")
    bar.update_segments({"video": Status.READY})
    assert bar._seg_state["video"] == Status.READY
    assert bar._seg_dots["video"].get_tooltip_text() == "Video: ready"
    # ...and it does not come back on the following poll.
    bar.update_segments({"video": Status.OFF})
    assert bar._seg_state["video"] == Status.OFF


def test_starting_a_new_launch_clears_a_previous_failure(bar):
    """The user retrying is an explicit action that supersedes the old error."""
    bar.mark_error("video", "start failed — click for log")
    bar.mark_starting("video")
    assert bar._seg_state["video"] == Status.STARTING
    bar.update_segments({"video": Status.STARTING})
    assert bar._seg_state["video"] == Status.STARTING


def test_sticky_error_is_per_segment(bar):
    bar.mark_error("video", "boom")
    bar.update_segments({"video": Status.OFF, "image": Status.READY})
    assert bar._seg_state["video"] == Status.ERROR
    assert bar._seg_state["image"] == Status.READY


def test_a_snapshot_error_is_not_sticky(bar):
    """Only a LOCALLY painted failure is sticky. An ERROR that came from the
    service is just the current snapshot, and must follow it."""
    bar.update_segments({"video": Status.ERROR})
    bar.update_segments({"video": Status.OFF})
    assert bar._seg_state["video"] == Status.OFF


# ── Local work with no server behind it (AnimateDiff) ───────────────────────
#
# AnimateDiff runs as a local subprocess, not a managed server, so
# ModelStatusService has no SERVERS entry for it and the Video segment sits at
# ○ off for the whole generation. Technically true, practically misleading:
# video IS being made. `set_segment_busy` lights it — but with its own glyph,
# because ● means "a server is ready to accept work" and no server exists here.

def test_busy_lights_the_segment_with_its_own_glyph(bar):
    bar.set_segment_busy("video", True, "AnimateDiff")
    assert bar._seg_dots["video"].get_label() == mw._BUSY_GLYPH
    assert bar._seg_dots["video"].get_label() != ss.GLYPHS[Status.READY], (
        "busy must not be indistinguishable from a ready server"
    )


def test_busy_says_what_is_running_in_the_tooltip(bar):
    bar.set_segment_busy("video", True, "AnimateDiff")
    tip = bar._seg_dots["video"].get_tooltip_text()
    assert "AnimateDiff" in tip and "no server" in tip


def test_busy_survives_the_next_snapshot(bar):
    """The service will keep reporting OFF for the whole run — that must not
    switch the light back off mid-generation."""
    bar.set_segment_busy("video", True, "AnimateDiff")
    bar.update_segments({"video": Status.OFF})
    assert bar._seg_dots["video"].get_label() == mw._BUSY_GLYPH


def test_clearing_busy_returns_to_the_snapshot_state(bar):
    bar.update_segments({"video": Status.OFF})
    bar.set_segment_busy("video", True, "AnimateDiff")
    bar.set_segment_busy("video", False)
    assert bar._seg_dots["video"].get_label() == ss.GLYPHS[Status.OFF]


def test_busy_does_not_mask_a_real_server_coming_up(bar):
    """If a real Video server goes READY mid-run, that is better information
    than 'local work in progress' — the snapshot wins."""
    bar.set_segment_busy("video", True, "AnimateDiff")
    bar.update_segments({"video": Status.READY})
    assert bar._seg_dots["video"].get_label() == ss.GLYPHS[Status.READY]


def test_busy_is_per_segment(bar):
    bar.set_segment_busy("video", True, "AnimateDiff")
    assert bar._seg_dots["image"].get_label() == ss.GLYPHS[Status.OFF]


def test_busy_on_an_unknown_segment_is_ignored(bar):
    bar.set_segment_busy("not-a-segment", True, "x")  # must not raise


def test_busy_does_not_start_the_elapsed_timer(bar):
    """Busy is not 'starting' — it must not grow a launch clock."""
    bar.set_segment_busy("video", True, "AnimateDiff")
    assert bar._timer_id is None
    assert bar._seg_names["video"].get_label() == "Video"
