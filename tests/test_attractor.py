"""Unit tests for AttractorPool — no GTK required."""
import sys
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from attractor import AttractorPool

def _rec(media_type="video", duration_s=5.0):
    r = MagicMock()
    r.media_type = media_type
    r.duration_s = duration_s
    return r

def test_pool_order_covers_all_records():
    recs = [_rec() for _ in range(5)]
    pool = AttractorPool(recs)
    visited = set()
    for _ in range(5):
        idx = pool.advance()
        visited.add(idx)
    assert visited == {0, 1, 2, 3, 4}

def test_pool_reshuffles_after_full_cycle():
    recs = [_rec() for _ in range(3)]
    pool = AttractorPool(recs)
    first_cycle = [pool.advance() for _ in range(3)]
    second_cycle = [pool.advance() for _ in range(3)]
    assert sorted(first_cycle) == [0, 1, 2]
    assert sorted(second_cycle) == [0, 1, 2]

def test_pool_no_immediate_repeat_across_cycle():
    recs = [_rec() for _ in range(4)]
    pool = AttractorPool(recs)
    last_of_first = None
    for _ in range(4):
        last_of_first = pool.advance()
    first_of_second = pool.advance()
    assert first_of_second != last_of_first

def test_pool_add_record_appears_later_in_cycle():
    recs = [_rec() for _ in range(4)]
    pool = AttractorPool(recs)
    # advance once so _pos = 1
    pool.advance()
    new_rec = _rec()
    pool.add_record(new_rec)
    # The new record's index (4) must NOT be immediately next
    next_idx = pool.advance()
    assert next_idx != 4, "New record should not be immediately next after add_record()"
    # But it must appear somewhere in the remainder of this cycle
    remaining = [pool.advance() for _ in range(3)]
    assert 4 in remaining, "New record must appear later in current cycle"

def test_scheduling_constants_are_positive():
    # IMAGE_DWELL_MS and VIDEO_FALLBACK_MS must be positive integers.
    # duration_s is inference time, not playback time — not used for scheduling.
    assert AttractorPool.IMAGE_DWELL_MS > 0
    assert AttractorPool.VIDEO_FALLBACK_MS > 0

def test_video_fallback_longer_than_image_dwell():
    # Videos need more display time than stills.
    assert AttractorPool.VIDEO_FALLBACK_MS > AttractorPool.IMAGE_DWELL_MS

def test_current_record_returns_correct_record():
    recs = [_rec() for _ in range(3)]
    pool = AttractorPool(recs)
    idx = pool.advance()
    assert pool.current_record() is recs[idx]

def test_pool_size_property():
    recs = [_rec(), _rec(), _rec()]
    pool = AttractorPool(recs)
    assert pool.size == 3
    pool.add_record(_rec())
    assert pool.size == 4

def test_add_record_soon_lands_within_window():
    """soon=True should insert within the next SOON_WINDOW positions."""
    window = AttractorPool.SOON_WINDOW
    # Large pool so a random insert would have many possible positions far away.
    recs = [_rec() for _ in range(50)]
    pool = AttractorPool(recs)
    # Advance a few times so _pos > 0.
    for _ in range(5):
        pool.advance()

    new_rec = _rec()
    new_rec.id = "soon-test"
    pool.add_record(new_rec, soon=True)
    new_idx = 50  # index appended to _records

    # The new record must appear within the next SOON_WINDOW advances.
    found_at = None
    for step in range(window + 2):
        idx = pool.advance()
        if idx == new_idx:
            found_at = step
            break
    assert found_at is not None, "soon record not found"
    # Record can appear at step 1..window (step 0 is always a pre-existing record
    # because lower = _pos+1 — the insert is never at the very-next slot).
    assert found_at <= window, f"soon record appeared at step {found_at}, expected <= {window}"

def test_add_record_soon_default_false_can_land_far():
    """Default (soon=False) can place new records far from current position."""
    # With a large pool, it would be astronomically unlikely for the default
    # insert to always land in the first SOON_WINDOW slots across many trials.
    window = AttractorPool.SOON_WINDOW
    far_count = 0
    trials = 30
    for _ in range(trials):
        recs = [_rec() for _ in range(30)]
        pool = AttractorPool(recs)
        pool.advance()  # _pos = 1
        pool.add_record(_rec())
        new_idx = 30
        landed_far = True
        for step in range(window):
            if pool.advance() == new_idx:
                landed_far = False
                break
        if landed_far:
            far_count += 1
    # At least half the trials should land outside the window.
    assert far_count > trials // 3, "Default add_record seems to always insert near front"


def test_close_request_hides_and_stops_never_destroys():
    """Persistent-window model: closing the kiosk HIDES it (keeps its GL
    context) and soft-stops playback — it must NOT destroy. Destroying the
    window stranded GStreamer's cached GL context, breaking the next reopen's
    video into an unclosable surface. Returns True so GTK's default close (which
    would destroy) doesn't run. `on_hide` fires for MainWindow bookkeeping."""
    import attractor
    win = attractor.AttractorWindow.__new__(attractor.AttractorWindow)
    win.stop = MagicMock()
    win.set_visible = MagicMock()
    win.destroy = MagicMock()
    hidden = []
    win._on_hide = lambda: hidden.append(True)

    ret = attractor.AttractorWindow._on_close_requested(win, win)

    assert ret is True
    win.stop.assert_called_once()
    win.set_visible.assert_called_once_with(False)
    win.destroy.assert_not_called()   # NEVER destroy on a normal close
    assert hidden == [True]


def test_stop_soft_stops_and_is_idempotent():
    """stop() marks the kiosk not-running and tears down playback, keeping the
    window alive. A second stop() (already stopped) is a no-op."""
    import attractor
    win = attractor.AttractorWindow.__new__(attractor.AttractorWindow)
    win._started = True
    calls = []
    win._teardown_playback = lambda: calls.append(True)

    attractor.AttractorWindow.stop(win)
    assert win._started is False and calls == [True]

    attractor.AttractorWindow.stop(win)          # already stopped
    assert calls == [True]                        # no second teardown


def _teardown_fake():
    from unittest.mock import MagicMock
    import attractor
    win = attractor.AttractorWindow.__new__(attractor.AttractorWindow)
    win._alive = True
    win._started = True
    win._gen_stop = MagicMock()
    win._att_poll_stop = MagicMock()
    win._dbus_sub_ids = []
    win._pending_advance_source = None
    win._graveyard_timer = 0
    win._video_graveyard = []
    win._pending_flash_source = 0
    win._watched_stream = None
    win._stream_handler_id = None
    win._slot_a = object()
    win._slot_b = object()
    return win


def test_teardown_releases_both_slot_pipelines(monkeypatch):
    """Shared teardown must release the GStreamer pipeline for BOTH video slots
    on EVERY platform (used by stop() and _on_destroy). Leaked pipelines are the
    thread/fd flood behind the unclosable-kiosk bug."""
    import attractor
    unloaded = []
    monkeypatch.setattr(attractor, "_unload_slot_video", lambda slot: unloaded.append(slot))
    win = _teardown_fake()

    attractor.AttractorWindow._teardown_playback(win)

    assert win._slot_a in unloaded and win._slot_b in unloaded
    win._gen_stop.set.assert_called_once()
    win._att_poll_stop.set.assert_called_once()


def test_on_destroy_marks_dead_and_tears_down(monkeypatch):
    """Real destruction (app shutdown) marks the window dead first, then runs
    the shared teardown (which releases both slot pipelines)."""
    import attractor
    unloaded = []
    monkeypatch.setattr(attractor, "_unload_slot_video", lambda slot: unloaded.append(slot))
    win = _teardown_fake()

    attractor.AttractorWindow._on_destroy(win, win)

    assert win._alive is False and win._started is False
    assert win._slot_a in unloaded and win._slot_b in unloaded


def test_generation_loop_exits_when_run_id_superseded(monkeypatch):
    """A generation loop from a SUPERSEDED start() (run_id mismatch) must exit
    immediately, not run forever — the zombie-loop race on a fast close->reopen
    (stop() sets the Event, start() clears it; a thread mid-body never sees the
    set, so a per-run token is the real guard).

    "Didn't hang" is a weak signal on its own (a regressed gate could still
    return after doing an iteration of real work). Prove it actually bailed
    at the `while ... and self._run_id == run_id` gate — before the loop body
    runs at all — by spying on the per-iteration work the body performs
    (`_get_queue_depth`/`_get_is_generating`/`_get_server_status`, each read
    at the top of every pass, and `prompt_client.generate_prompt`, the actual
    generation call further down) and asserting every one of them was called
    ZERO times.
    """
    import threading
    import attractor
    win = attractor.AttractorWindow.__new__(attractor.AttractorWindow)
    win._gen_stop = threading.Event()      # NOT set -> would loop forever without the run_id gate
    win._auto_generate = True
    win._model_source = "video"
    win._run_id = 5                        # the current run

    # Spies on every per-iteration work call the loop body makes. If the
    # run_id gate regressed (e.g. dropped from the `while` condition), the
    # loop would enter its body and call these before this test's assertions
    # ever run — a direct, deterministic proof, unlike relying on a hang.
    win._get_queue_depth = MagicMock(name="_get_queue_depth")
    win._get_is_generating = MagicMock(name="_get_is_generating")
    win._get_server_status = MagicMock(name="_get_server_status")
    generate_prompt = MagicMock(name="generate_prompt")
    monkeypatch.setattr(attractor.prompt_client, "generate_prompt", generate_prompt)

    # A loop launched for an OLD run (1) must bail immediately (returns == exits).
    result = attractor.AttractorWindow._generation_loop(win, run_id=1)

    assert result is None
    win._get_queue_depth.assert_not_called()
    win._get_is_generating.assert_not_called()
    win._get_server_status.assert_not_called()
    generate_prompt.assert_not_called()
