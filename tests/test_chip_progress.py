# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
GTK widget tests for the shared `ChipProgressRows` widget (Task 3,
docs/superpowers/sdd/2026-08-11-pipeline-stage-making-of/task-3-brief.md).

Extracted from `CreateResultPanel`'s inline per-chip progress box so it can be
reused by the Stage (pipeline run view) in a later task. Behavior-identical to
the logic it replaces in `create_view.py`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import pytest

try:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("GTK4 not available", allow_module_level=True)

import chip_progress as cp


def test_feed_matches_chip_line_and_upserts():
    w = cp.ChipProgressRows()
    assert w.feed("Starting on 4 chips") is False   # not a chip line
    assert w.feed("chip0: Step 2/25") is True
    assert w.feed("chip1: Step 3/25") is True
    assert w.feed("chip1: Step 9/25") is True        # update in place
    assert set(w._chip_row_labels) == {0, 1}
    assert w._chip_row_labels[1].get_label() == "chip 1: Step 9/25"
    assert w.get_visible() is True


def test_reset_and_restore():
    w = cp.ChipProgressRows()
    w.feed("chip0: a")
    snap = w.snapshot()
    w.reset()
    assert w._chip_row_labels == {}
    w.restore(snap)
    assert w._chip_row_labels[0].get_label() == "chip 0: a"
