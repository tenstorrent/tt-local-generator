#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Attractor Mode — self-sustaining kiosk that cycles generated media and
continuously queues new generations.

Classes:
    AttractorPool  — pure pool/shuffle logic (no GTK, testable)
    AttractorWindow — GTK4 kiosk window (added in subsequent tasks)
"""
from __future__ import annotations

import random
import statistics


class AttractorPool:
    """
    Manages the shuffled playback order for a growing list of media records.

    Records are played in a shuffled order. When the cycle is exhausted the
    pool reshuffles, but the first item of the new cycle is never the same as
    the last item of the previous cycle.  New records added mid-cycle are
    inserted at a random position *after* the current playback position so
    they appear later in the current cycle rather than immediately next.
    """

    def __init__(self, records: list) -> None:
        self._records: list = list(records)
        self._order: list[int] = []
        self._pos: int = 0
        self._last_idx: int | None = None
        self._shuffle_fresh()
        self._recalc_duration()

    # ── public ────────────────────────────────────────────────────────────

    def advance(self) -> int:
        """
        Move to the next item and return its index into self._records.
        Reshuffles automatically at end of cycle.
        """
        if self._pos >= len(self._order):
            self._shuffle_fresh()
        idx = self._order[self._pos]
        self._last_idx = idx
        self._pos += 1
        return idx

    def current_record(self):
        """Return the record most recently returned by advance()."""
        if self._last_idx is None:
            raise RuntimeError("current_record() called before advance()")
        return self._records[self._last_idx]

    def add_record(self, record) -> None:
        """
        Append a new record and insert its index at a random position after
        the current playback position in _order.
        """
        new_idx = len(self._records)
        self._records.append(record)
        # Insert at any position strictly after the current pos so the new record
        # doesn't play immediately next.  If _pos is already at or past the end of
        # _order (cycle about to reshuffle), the only valid slot is the end.
        lower = min(self._pos + 1, len(self._order))
        insert_at = random.randint(lower, len(self._order))
        self._order.insert(insert_at, new_idx)
        self._recalc_duration()

    @property
    def avg_video_duration(self) -> float:
        """Mean duration of video records, or 8.0 s if none exist."""
        return self._avg_dur

    @property
    def size(self) -> int:
        return len(self._records)

    # ── private ───────────────────────────────────────────────────────────

    def _shuffle_fresh(self) -> None:
        order = list(range(len(self._records)))
        random.shuffle(order)
        # Avoid placing last-played item first in new cycle
        if self._last_idx is not None and order and order[0] == self._last_idx:
            if len(order) > 1:
                order[0], order[1] = order[1], order[0]
        self._order = order
        self._pos = 0

    def _recalc_duration(self) -> None:
        durations = [
            r.duration_s for r in self._records
            if r.media_type == "video" and r.duration_s > 0
        ]
        self._avg_dur = statistics.mean(durations) if durations else 8.0
