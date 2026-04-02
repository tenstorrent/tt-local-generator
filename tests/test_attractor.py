"""Unit tests for AttractorPool — no GTK required."""
import sys
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent))

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

def test_avg_video_duration_uses_video_durations():
    recs = [_rec("video", 6.0), _rec("video", 10.0), _rec("image", 0.0)]
    pool = AttractorPool(recs)
    assert pool.avg_video_duration == 8.0

def test_avg_video_duration_defaults_when_no_videos():
    recs = [_rec("image", 0.0), _rec("image", 0.0)]
    pool = AttractorPool(recs)
    assert pool.avg_video_duration == 8.0

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
