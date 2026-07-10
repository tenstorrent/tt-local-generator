"""Concurrency for the artgen panel: cap-3 active jobs, queue refill, aggregate
button/ticker, auto-gen guard. Drives main-thread methods with threads + GLib
mocked; the panel is built via __new__ so no GTK display is needed."""
import sys
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import artgen_panel


def _panel(active=0, queue_items=()):
    """A panel stub with only the attributes the concurrency methods touch."""
    p = artgen_panel.ArtgenPanel.__new__(artgen_panel.ArtgenPanel)
    p._active_count = active
    p._gen_queue = deque(queue_items)
    p._gen_btn = MagicMock()
    p._status_lbl = MagicMock()
    p._llm_timer_id = None
    p._llm_t0 = 0.0
    p._auto_gen = False
    p._auto_gen_error_streak = 0
    # gallery/view attrs touched by _finish_success (not used by error/drain tests)
    p._gallery = MagicMock()
    p._watch = MagicMock(); p._watch._records = []
    p._sub_stack = MagicMock()
    p._gallery_tab_btn = MagicMock()
    p._last_out_path = None
    return p


def test_button_label_variants():
    f = artgen_panel.ArtgenPanel._gen_button_label
    assert f(0, 0) == "✦ Generate"
    assert f(3, 0) == "Generating… (3 running)"
    assert f(3, 2) == "Generating… (3 running, +2)"
    assert f(1, 0) == "Generating… (1 running)"


def test_drain_launches_up_to_cap():
    p = _panel(active=0, queue_items=[("verse", object()) for _ in range(5)])
    with patch.object(artgen_panel, "threading") as th:
        p._drain_queue()
        assert th.Thread.call_count == 3          # cap
    assert p._active_count == 3
    assert len(p._gen_queue) == 2                 # remainder queued


def test_drain_respects_existing_active():
    p = _panel(active=2, queue_items=[("verse", object()) for _ in range(5)])
    with patch.object(artgen_panel, "threading") as th:
        p._drain_queue()
        assert th.Thread.call_count == 1          # only 1 free slot
    assert p._active_count == 3
    assert len(p._gen_queue) == 4


def test_finish_error_decrements_and_refills():
    p = _panel(active=3, queue_items=[("verse", object())])
    with patch.object(artgen_panel, "threading") as th, \
         patch.object(artgen_panel, "GLib") as glib:
        p._finish_error("boom")
        # one slot freed by the finish, immediately refilled from the queue
        assert th.Thread.call_count == 1
    assert p._active_count == 3
    assert len(p._gen_queue) == 0


def test_finish_error_never_negative():
    p = _panel(active=0)
    with patch.object(artgen_panel, "GLib"):
        p._finish_error("x")
        p._finish_error("x")
    assert p._active_count == 0


def test_ensure_ticker_idempotent():
    p = _panel(active=1)
    with patch.object(artgen_panel, "GLib") as glib:
        glib.timeout_add.return_value = 42
        p._ensure_ticker()
        p._ensure_ticker()
        assert glib.timeout_add.call_count == 1   # second call is a no-op
    assert p._llm_timer_id == 42


def test_auto_fire_waits_when_at_cap():
    p = _panel(active=3)
    p._auto_gen = True
    p._auto_status_lbl = MagicMock()
    with patch.object(artgen_panel, "threading") as th, \
         patch.object(artgen_panel, "GLib") as glib:
        p._auto_fire()
        th.Thread.assert_not_called()             # no inspire thread while full
    p._auto_status_lbl.set_label.assert_called_with("Waiting for generation…")


class TestAutoScheduleGuard:
    def test_no_double_timer_when_countdown_pending(self):
        p = _panel(active=0)
        p._auto_gen = True
        p._auto_type_checks = {"verse": MagicMock(get_active=lambda: True)}
        p._auto_delay_spin = MagicMock(get_value=lambda: 5.0)
        p._auto_delay_row = MagicMock(); p._auto_countdown_row = MagicMock()
        p._auto_status_lbl = MagicMock(); p._auto_progress = MagicMock()
        p._auto_gen_timer_id = 99   # a countdown is already pending
        with patch.object(artgen_panel, "GLib") as glib:
            p._auto_maybe_schedule()
            glib.timeout_add.assert_not_called()   # guard: no second timer
        assert p._auto_gen_timer_id == 99          # unchanged

    def test_schedules_when_no_timer_pending(self):
        p = _panel(active=0)
        p._auto_gen = True
        p._auto_type_checks = {"verse": MagicMock(get_active=lambda: True)}
        p._auto_delay_spin = MagicMock(get_value=lambda: 5.0)
        p._auto_delay_row = MagicMock(); p._auto_countdown_row = MagicMock()
        p._auto_status_lbl = MagicMock(); p._auto_progress = MagicMock()
        p._auto_gen_timer_id = None
        with patch.object(artgen_panel, "GLib") as glib:
            glib.timeout_add.return_value = 7
            p._auto_maybe_schedule()
            glib.timeout_add.assert_called_once()
        assert p._auto_gen_timer_id == 7
