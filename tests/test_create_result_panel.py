# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
GTK widget tests for CreateResultPanel — in-place Create results, Task 1
(docs/superpowers/sdd/task-1-brief.md).

CreateResultPanel is a standalone widget this task: it renders a single
current Create result (pending / finished / error) plus a capped "recents"
strip. It is built ALONGSIDE CreateView/main_window.py — wiring it into the
real generation flow is a later task (see the brief). These tests only
exercise the widget's own public seams (`show_pending`/`show_progress`/
`show_finished`/`show_error`/`clear`/`state`/`recents_count`) with a minimal,
real `history_store.GenerationRecord` (never a hand-rolled fake) so a
constructor drift in that dataclass would fail here too.

Creating GTK widgets needs a display; the full suite runs under xvfb. When no
display is available this module skips itself (matches test_create_view.py's
own headless fallback).
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

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

import create_view as cv
from history_store import GenerationRecord


def _rec(tmp_path, kind: str = "image") -> GenerationRecord:
    """Build a minimal, valid GenerationRecord pointing at a real tmp file.

    `kind` picks which artifact-kind CreateResultPanel should classify the
    record as by extension: "image" -> a.png (image_path, media_type=image),
    "text" -> a.txt (video_path — the generic non-image path field —
    media_type left at its "video" default since CreateResultPanel classifies
    by extension, not by media_type).
    """
    ts = datetime.now(timezone.utc).isoformat()
    job_id = str(uuid.uuid4())
    if kind == "image":
        p = tmp_path / "a.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        return GenerationRecord(
            id=job_id,
            prompt="a castle",
            negative_prompt="",
            num_inference_steps=20,
            seed=42,
            video_path="",
            thumbnail_path="",
            created_at=ts,
            media_type="image",
            image_path=str(p),
        )
    elif kind == "text":
        p = tmp_path / "a.txt"
        p.write_bytes(b"hi")
        return GenerationRecord(
            id=job_id,
            prompt="a poem",
            negative_prompt="",
            num_inference_steps=0,
            seed=-1,
            video_path=str(p),
            thumbnail_path="",
            created_at=ts,
        )
    else:
        raise ValueError(f"unsupported test kind: {kind}")


def test_starts_empty():
    assert cv.CreateResultPanel().state == "empty"


def test_pending_then_finished(tmp_path):
    p = cv.CreateResultPanel()
    p.show_pending("a castle", None)
    assert p.state == "pending"
    p.show_finished(_rec(tmp_path))
    assert p.state == "finished"
    assert p.recents_count() == 1


def test_pending_progress_updates_status(tmp_path):
    p = cv.CreateResultPanel()
    p.show_pending("a castle", None)
    p.show_progress("still cooking...")
    assert p.state == "pending"
    assert p._pending_status_lbl.get_label() == "still cooking..."


def test_recents_caps_at_max(tmp_path):
    p = cv.CreateResultPanel()
    for _ in range(cv._RECENTS_MAX + 3):
        p.show_finished(_rec(tmp_path))
    assert p.recents_count() == cv._RECENTS_MAX


def test_recents_drops_oldest_newest_first(tmp_path):
    p = cv.CreateResultPanel()
    recs = [_rec(tmp_path) for _ in range(cv._RECENTS_MAX + 1)]
    for r in recs:
        p.show_finished(r)
    # Oldest (recs[0]) dropped; newest (recs[-1]) is first in the strip.
    assert p._recents[0] is recs[-1]
    assert recs[0] not in p._recents


def test_error_state(tmp_path):
    p = cv.CreateResultPanel()
    p.show_error("boom")
    assert p.state == "error"


def test_clear_resets_to_empty(tmp_path):
    p = cv.CreateResultPanel()
    p.show_finished(_rec(tmp_path))
    p.clear()
    assert p.state == "empty"
    # clear() only resets the current-result view, not the recents strip.
    assert p.recents_count() == 1


def test_finished_text_record_renders_without_crashing(tmp_path):
    p = cv.CreateResultPanel()
    p.show_finished(_rec(tmp_path, kind="text"))
    assert p.state == "finished"


def test_missing_file_shows_placeholder_not_broken_image(tmp_path):
    rec = _rec(tmp_path, kind="image")
    Path(rec.image_path).unlink()  # file no longer exists on disk
    p = cv.CreateResultPanel()
    p.show_finished(rec)
    assert p.state == "finished"  # never raises / never crashes


def test_clicking_a_recent_rerenders_it(tmp_path):
    p = cv.CreateResultPanel()
    rec1 = _rec(tmp_path, kind="image")
    p.show_finished(rec1)
    p.show_error("boom")
    assert p.state == "error"
    # Re-render the recent directly via the same path clicking would use.
    p._on_recent_clicked(rec1)
    assert p.state == "finished"


def test_pending_timer_cancelled_on_state_change(tmp_path):
    p = cv.CreateResultPanel()
    p.show_pending("a castle", None)
    assert p._timer_id is not None
    p.show_finished(_rec(tmp_path))
    assert p._timer_id is None


# ── Pending-queue display (SP-3c-4, task-4-brief.md) ────────────────────────
#
# Surfaces the this-session generation queue (`MainWindow._queue`) in the
# result pane, near the recents strip. `set_queue` takes a plain list of
# duck-typed items (only `.prompt` is read — a real `main_window._QueueItem`
# or any stand-in works) plus an `on_cancel(index)` callback CreateView never
# constructs itself; MainWindow wires it to `_on_queue_remove`.

class _FakeQueueItem:
    """Duck-typed stand-in for `main_window._QueueItem` — `set_queue` only
    ever reads `.prompt`."""

    def __init__(self, prompt: str) -> None:
        self.prompt = prompt


def test_queue_starts_empty_and_hidden():
    p = cv.CreateResultPanel()
    assert p.queue_count() == 0
    assert p._queue_box.get_visible() is False


def test_set_queue_renders_items_and_becomes_visible():
    p = cv.CreateResultPanel()
    p.set_queue([_FakeQueueItem("first prompt"), _FakeQueueItem("second prompt")],
                on_cancel=lambda i: None)
    assert p.queue_count() == 2
    assert p._queue_box.get_visible() is True


def test_set_queue_empty_list_hides_box():
    p = cv.CreateResultPanel()
    p.set_queue([_FakeQueueItem("only one")], on_cancel=lambda i: None)
    assert p._queue_box.get_visible() is True
    p.set_queue([], on_cancel=lambda i: None)
    assert p.queue_count() == 0
    assert p._queue_box.get_visible() is False


def test_set_queue_cancel_button_calls_on_cancel_with_index():
    p = cv.CreateResultPanel()
    cancelled: list = []
    p.set_queue(
        [_FakeQueueItem("a"), _FakeQueueItem("b"), _FakeQueueItem("c")],
        on_cancel=lambda i: cancelled.append(i),
    )
    # Walk the rendered rows and click each row's cancel (last child, a
    # Gtk.Button) — proves the index closure captured per-row is correct
    # (a naive `lambda _b: self._on_cancel_clicked(i)` without a default arg
    # would let every button close over the SAME final `i`).
    row = p._queue_box.get_first_child()
    while row is not None:
        cancel_btn = row.get_last_child()
        cancel_btn.emit("clicked")
        row = row.get_next_sibling()
    assert cancelled == [0, 1, 2]


def test_set_queue_rerender_replaces_previous_rows():
    p = cv.CreateResultPanel()
    p.set_queue([_FakeQueueItem("a"), _FakeQueueItem("b")], on_cancel=lambda i: None)
    assert p.queue_count() == 2
    p.set_queue([_FakeQueueItem("only-one-now")], on_cancel=lambda i: None)
    assert p.queue_count() == 1
    row = p._queue_box.get_first_child()
    assert row.get_next_sibling() is None  # exactly one row left
