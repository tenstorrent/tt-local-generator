# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
`ChipProgressRows` — the per-chip progress breakdown shown while a multi-chip
AnimateDiff run streams `chipN: ...` prefixed log lines.

Extracted verbatim (behavior-identical) from `create_view.py`'s
`CreateResultPanel` (`_upsert_chip_row`, `_pending_chip_box`, `_chip_status`,
`_chip_row_labels`) so it can be reused by the pipeline Stage's run view
without copy-pasting the same regex/row logic a second time. This module is a
small, self-contained Gtk widget — no dependency on `create_view.py` or any
other app module.

Contract:
    feed(message)    -> bool   # True iff `message` was a "chipN: ..." line
                                 (upserts that chip's row + reveals the box)
    reset()          -> None   # drop all rows/state (fresh job)
    snapshot()       -> dict[int, str]   # current chip index -> latest text
    restore(state)   -> None   # rebuild rows from a previously-snapshotted dict

`_chip_status` (index -> latest text) and `_chip_row_labels` (index -> the
live `Gtk.Label`) are intentionally public-ish (single leading underscore,
not name-mangled) because both `CreateResultPanel`'s and, later, the Stage's
tests read them directly to assert on-screen content — mirroring the
pre-extraction test contract in `tests/test_create_result_panel.py`.
"""
from __future__ import annotations

import re

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

# Matches a per-chip progress line, e.g. "chip0: Denoising step 5/25".
# Multi-chip AnimateDiff (remix mode) prefixes each chip's log lines this way
# (see `_run_multi_chip` in `artgen/generators/animatediff.py`).
CHIP_LINE_RE = re.compile(r"chip(\d+):\s*(.*)", re.IGNORECASE)


class ChipProgressRows(Gtk.Box):
    """A vertical box that renders one status row per chip, upserted as
    "chipN: ..." lines arrive. Hidden (and empty) until the first chip line
    is fed; `reset()` returns it to that hidden/empty state for a fresh job."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.add_css_class("create-result-chip-box")
        self.set_halign(Gtk.Align.CENTER)
        self.set_visible(False)

        # `_chip_status` is the persistent job state (chip index -> latest
        # line), read back by `snapshot()` for stashing across a
        # navigate-away/return-to-pending cycle. `_chip_row_labels` maps the
        # same index to its live `Gtk.Label` child.
        self._chip_status: dict = {}
        self._chip_row_labels: dict = {}

    def _upsert(self, idx: int, text: str) -> None:
        """Create or update the status row for chip `idx` and reveal the box.
        Lifted verbatim from `CreateResultPanel._upsert_chip_row`."""
        self._chip_status[idx] = text
        lbl = self._chip_row_labels.get(idx)
        if lbl is None:
            lbl = Gtk.Label()
            lbl.add_css_class("create-result-chip-row")
            lbl.set_xalign(0.0)
            lbl.set_max_width_chars(40)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)  # long lines never widen the pane
            self._chip_row_labels[idx] = lbl
            self.append(lbl)
            self.set_visible(True)
        lbl.set_label(f"chip {idx}: {text}")

    def feed(self, message: str) -> bool:
        """If `message` is a "chipN: ..." line, upsert that chip's row (and
        reveal the box) and return True. Otherwise return False untouched —
        the caller is expected to route non-chip lines to its own single
        status label instead."""
        m = CHIP_LINE_RE.match(message)
        if not m:
            return False
        idx, text = int(m.group(1)), m.group(2)
        self._upsert(idx, text)
        return True

    def reset(self) -> None:
        """Drop every row and hide the box — a fresh job starts with no
        stale chips from a previous run."""
        child = self.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.remove(child)
            child = nxt
        self._chip_status = {}
        self._chip_row_labels = {}
        self.set_visible(False)

    def snapshot(self) -> dict:
        """Return a copy of the current chip index -> latest-text state, for
        stashing while the box is detached/not shown (e.g. the user is
        viewing a different recent while this job keeps progressing)."""
        return dict(self._chip_status)

    def restore(self, state: dict) -> None:
        """Rebuild every row from a previously-`snapshot()`ed state, in
        index order (mirrors the original `_render_pending`'s
        `for idx in sorted(self._chip_status)` rebuild loop)."""
        self.reset()
        for idx in sorted(state):
            self._upsert(idx, state[idx])
