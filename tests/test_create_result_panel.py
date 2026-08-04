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

import json
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
    gi.require_version("WebKit", "6.0")
    from gi.repository import Gtk, WebKit
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)

import create_view as cv
from history_store import GenerationRecord
from media_store import MediaRecord


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
    elif kind == "gif":
        p = tmp_path / "a.gif"
        _write_real_gif(p)
        thumb = tmp_path / "a_thumb.png"
        thumb.write_bytes(b"\x89PNG\r\n\x1a\n")  # not a real PNG — proves it's unused
        return GenerationRecord(
            id=job_id,
            prompt="a dancing character",
            negative_prompt="",
            num_inference_steps=0,
            seed=-1,
            video_path=str(p),
            thumbnail_path=str(thumb),
            created_at=ts,
        )
    elif kind == "gif_missing":
        # Points at a .gif path that was never actually written to disk.
        p = tmp_path / "missing.gif"
        return GenerationRecord(
            id=job_id,
            prompt="a dancing character",
            negative_prompt="",
            num_inference_steps=0,
            seed=-1,
            video_path=str(p),
            thumbnail_path="",
            created_at=ts,
        )
    elif kind == "video":
        p = tmp_path / "a.mp4"
        p.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        thumb = tmp_path / "a_thumb.jpg"
        thumb.write_bytes(b"\xff\xd8\xff")
        return GenerationRecord(
            id=job_id,
            prompt="a flying car",
            negative_prompt="",
            num_inference_steps=20,
            seed=7,
            video_path=str(p),
            thumbnail_path=str(thumb),
            created_at=ts,
        )
    else:
        raise ValueError(f"unsupported test kind: {kind}")


def _write_real_gif(path: Path) -> None:
    """Write a tiny real 2-frame animated GIF using PIL (skips the calling
    test if PIL is unavailable — matches the rest of this module's
    real-artifact-over-hand-rolled-fake philosophy)."""
    PIL = pytest.importorskip("PIL", reason="gif rendering tests need PIL")
    from PIL import Image
    frame1 = Image.new("RGB", (32, 24), color=(255, 0, 0))
    frame2 = Image.new("RGB", (32, 24), color=(0, 255, 0))
    frame1.save(path, format="GIF", save_all=True, append_images=[frame2], duration=100, loop=0)


def _artgen_rec(
    tmp_path,
    *,
    generator_type: str,
    ext: str,
    content: str,
    thumb_path: str = "",
    prompt: str = "an artifact",
    params: "dict | None" = None,
) -> MediaRecord:
    """Build a `media_store.MediaRecord` standing in for a finished artgen
    job, pointing at a real tmp file written with `content` at `ext`.

    `media_file_path` is set as a plain instance attribute AFTER
    construction, mirroring exactly what production code does for a real
    artgen record (`main_window.py`'s `rec.media_file_path = str(out_path)`,
    see CLAUDE.md's "In-place results" section) — it is NOT a `MediaRecord`
    dataclass field, so `CreateResultPanel` reads it via `getattr(...)` and
    a test that skipped this step would silently exercise the "no path at
    all" placeholder branch instead of the kind it meant to test.
    """
    p = tmp_path / f"artifact{ext}"
    p.write_text(content, encoding="utf-8")
    rec = MediaRecord(
        id=str(uuid.uuid4()),
        media_type="artgen",
        created_at=datetime.now(timezone.utc).isoformat(),
        file_path=str(p),
        thumbnail_path=thumb_path,
        prompt=prompt,
        model_id="",
        generator_type=generator_type,
        params=json.dumps(params or {}),
        starred=0,
    )
    rec.media_file_path = str(p)
    return rec


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


def test_pending_prompt_label_width_is_bounded():
    """A long prompt must not balloon the result pane's natural width (which
    would kick it below the form in the two-pane FlowBox). The prompt label
    must wrap AND cap its max-width-chars so its natural width stays bounded."""
    long_prompt = (
        "A desert depot, three hundred identically dressed chorus members, a "
        "man walks slowly between them, smoke rising and still, cinematic"
    )
    p = cv.CreateResultPanel()
    p.show_pending(long_prompt, None)
    # Find the prompt label among the current-result children.
    prompt_lbl = None
    child = p._current_box.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label) and child.get_label() == long_prompt:
            prompt_lbl = child
        child = child.get_next_sibling()
    assert prompt_lbl is not None
    assert prompt_lbl.get_wrap() is True
    mwc = prompt_lbl.get_max_width_chars()
    assert 0 < mwc <= 80  # bounded, not the -1 "unlimited natural width" default


def test_pending_status_and_elapsed_are_bounded_and_centered():
    """The status + elapsed labels change text as a job runs; they must be
    bounded/centered so a longer progress message or a ticking elapsed count
    re-wraps in place instead of resizing the whole pending card."""
    p = cv.CreateResultPanel()
    p.show_pending("a castle", None)
    status = p._pending_status_lbl
    elapsed = p._pending_elapsed_lbl
    assert status is not None and elapsed is not None
    # status: bounded natural width + centered
    assert status.get_wrap() is True
    assert 0 < status.get_max_width_chars() <= 60
    assert status.get_halign() == Gtk.Align.CENTER
    # elapsed: centered so per-second updates don't shift layout
    assert elapsed.get_halign() == Gtk.Align.CENTER
    # a long progress message must not lift the cap
    p.show_progress("chip2: Generating 2 frame(s)… decoding latents at step 37/50")
    assert 0 < status.get_max_width_chars() <= 60


def _find_return_button(box):
    ch = box.get_first_child()
    while ch is not None:
        if isinstance(ch, Gtk.Button) and "Generating" in (ch.get_label() or ""):
            return ch
        ch = ch.get_next_sibling()
    return None


def test_clicking_recent_while_pending_offers_return_and_restores(tmp_path):
    """Peeking at a recent MID-generation must not be a dead end: a 'back to
    Generating' control appears, the job stays active, and returning restores
    the live pending view (not stuck on the recent)."""
    p = cv.CreateResultPanel()
    p.show_pending("a castle", None)
    assert p.state == "pending" and p._pending_active is True

    p._on_recent_clicked(_rec(tmp_path))     # peek at a recent
    assert p.state == "finished"             # now showing the recent
    assert p._pending_active is True         # ...but the job is still in flight
    assert _find_return_button(p._current_box) is not None

    p._return_to_pending()                   # click "back to Generating…"
    assert p.state == "pending"
    assert p._pending_status_lbl is not None


def test_progress_while_viewing_recent_is_shown_on_return(tmp_path):
    """A progress message arriving while the user peeks at a recent is stashed
    and shown when they return — not lost, not stale."""
    p = cv.CreateResultPanel()
    p.show_pending("a castle", None)
    p._on_recent_clicked(_rec(tmp_path))
    p.show_progress("chip2: 12/50")          # arrives while viewing the recent
    assert p.state == "finished"             # progress did NOT yank us back
    p._return_to_pending()
    assert p.state == "pending"
    assert p._pending_status_lbl.get_label() == "chip2: 12/50"


def test_finish_clears_pending_active_so_return_is_inert(tmp_path):
    p = cv.CreateResultPanel()
    p.show_pending("a castle", None)
    p.show_finished(_rec(tmp_path))
    assert p._pending_active is False
    p._return_to_pending()                   # nothing to return to
    assert p.state == "finished"


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


# ── GIF rendering (animated-gif-as-text bug fix) ────────────────────────────
#
# Regression coverage for the bug where a gif record's artifact widget was a
# static `Gtk.Picture` pointed at `thumbnail_path` — which, pre-fix, was
# itself a PIL text-render of the gif's raw binary bytes (see
# artgen_thumb.make_thumbnail and CLAUDE.md's root-cause note). The fix:
# render the ORIGINAL .gif file, animated, via `artgen_gallery`'s
# self-timer-managing `_AnimatedGifWidget` — never the thumbnail.

def test_gif_record_with_real_file_renders_animated_widget_not_thumbnail(tmp_path):
    from artgen_gallery import _AnimatedGifWidget
    p = cv.CreateResultPanel()
    rec = _rec(tmp_path, kind="gif")
    widget = p._build_artifact_widget(rec)

    assert isinstance(widget, _AnimatedGifWidget)
    # Never the old broken behavior: a plain Gtk.Picture pointed at the
    # (possibly-garbage) thumbnail path, nor a TextView showing raw bytes.
    assert not isinstance(widget, Gtk.TextView)


def test_gif_record_finished_state_is_animated_not_static_picture(tmp_path):
    """End-to-end through show_finished (not just _build_artifact_widget
    directly) — proves the real wiring path also animates."""
    from artgen_gallery import _AnimatedGifWidget
    p = cv.CreateResultPanel()
    rec = _rec(tmp_path, kind="gif")
    p.show_finished(rec)
    assert p.state == "finished"
    child = p._current_box.get_first_child()
    assert isinstance(child, _AnimatedGifWidget)


def test_gif_record_missing_file_degrades_to_placeholder_no_crash(tmp_path):
    from artgen_gallery import _AnimatedGifWidget
    p = cv.CreateResultPanel()
    rec = _rec(tmp_path, kind="gif_missing")
    widget = p._build_artifact_widget(rec)  # must not raise
    assert not isinstance(widget, _AnimatedGifWidget)
    assert not isinstance(widget, Gtk.TextView)
    assert isinstance(widget, Gtk.Label)


def test_video_record_still_uses_static_poster(tmp_path):
    """Video (.mp4) is explicitly OUT of scope for this fix — it keeps the
    existing static-poster/placeholder behavior."""
    from artgen_gallery import _AnimatedGifWidget
    p = cv.CreateResultPanel()
    rec = _rec(tmp_path, kind="video")
    widget = p._build_artifact_widget(rec)
    assert not isinstance(widget, _AnimatedGifWidget)
    # Static poster: a Gtk.Picture pointed at the thumbnail (unchanged).
    assert isinstance(widget, Gtk.Picture)


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


# ── Task 2: every artgen kind showcased richly (SDD task-2-brief.md,
# docs/superpowers/specs/2026-07-17-media-showcase-everywhere-design.md) ────
#
# Before this fix, `_artifact_kind` only recognised image/.mp4/.gif/.txt/.ans
# — a successful `.svg` (5 generators), `.json` (palette), or `.py` (codeart)
# generation claimed "Result file not found" despite the file existing right
# there on disk, `.ans` rendered as raw escape-code gibberish in a
# `Gtk.TextView`, and `.md`/verse rendered as unformatted plain text. These
# tests build a record per kind pointing at a REAL tmp artifact file (never
# a hand-rolled fake) and assert the rich widget, never the placeholder.

def test_svg_record_renders_vector_picture_not_placeholder(tmp_path):
    p = cv.CreateResultPanel()
    rec = _artgen_rec(
        tmp_path, generator_type="landscape", ext=".svg",
        content='<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
                '<rect width="10" height="10" fill="red"/></svg>',
    )
    widget = p._build_artifact_widget(rec)
    assert isinstance(widget, Gtk.Picture)
    assert not isinstance(widget, Gtk.Label)


def test_palette_json_record_renders_reading_view_not_placeholder(tmp_path):
    p = cv.CreateResultPanel()
    rec = _artgen_rec(
        tmp_path, generator_type="palette", ext=".json",
        content=json.dumps({
            "name": "Sunset", "lore": "warm dusk",
            "colors": [{"hex": "#ff4400", "role": "primary"}],
        }),
    )
    widget = p._build_artifact_widget(rec)
    assert isinstance(widget, WebKit.WebView)
    assert not isinstance(widget, Gtk.Label)


def test_ansi_record_renders_reading_view_not_raw_textview(tmp_path):
    p = cv.CreateResultPanel()
    rec = _artgen_rec(
        tmp_path, generator_type="ansi", ext=".ans",
        content="\x1b[38;5;196m█\x1b[38;5;46m█\n",
    )
    widget = p._build_artifact_widget(rec)
    assert isinstance(widget, WebKit.WebView)
    # The old bug: raw escape codes dumped into a plain Gtk.TextView.
    assert not isinstance(widget, Gtk.TextView)


def test_verse_txt_record_renders_formatted_reading_view(tmp_path):
    p = cv.CreateResultPanel()
    rec = _artgen_rec(
        tmp_path, generator_type="verse", ext=".txt",
        content="Roses are red\nViolets are blue",
        params={"form": "couplet", "theme": "love"},
    )
    widget = p._build_artifact_widget(rec)
    assert isinstance(widget, WebKit.WebView)
    assert not isinstance(widget, Gtk.TextView)


def test_codeart_py_record_renders_monospace_reading_view(tmp_path):
    p = cv.CreateResultPanel()
    rec = _artgen_rec(
        tmp_path, generator_type="codeart", ext=".py",
        content="def recurse(n):\n    return recurse(n - 1)\n",
    )
    widget = p._build_artifact_widget(rec)
    assert isinstance(widget, WebKit.WebView)
    assert not isinstance(widget, Gtk.TextView)


@pytest.mark.parametrize("generator_type,ext,content", [
    ("landscape", ".svg", '<svg xmlns="http://www.w3.org/2000/svg"></svg>'),
    ("palette", ".json", '{"name":"x","colors":[]}'),
    ("ansi", ".ans", "\x1b[38;5;196m█\n"),
    ("verse", ".txt", "a poem"),
    ("codeart", ".py", "x = 1\n"),
])
def test_every_existing_kind_never_shows_not_found_placeholder(
    tmp_path, generator_type, ext, content
):
    """CRITICAL: for ANY kind whose file EXISTS, `_build_artifact_widget`
    must never return the "Result file not found" placeholder label — the
    core bug this task fixes."""
    p = cv.CreateResultPanel()
    rec = _artgen_rec(tmp_path, generator_type=generator_type, ext=ext, content=content)
    widget = p._build_artifact_widget(rec)  # must not raise
    if isinstance(widget, Gtk.Label):
        assert widget.get_label() != "Result file not found."


def test_missing_artgen_file_still_shows_honest_placeholder(tmp_path):
    """The ONE case that legitimately still shows the placeholder: a
    genuinely-absent file — never a false negative for a file that exists,
    but also never silently swallowed for one that truly doesn't."""
    p = cv.CreateResultPanel()
    rec = _artgen_rec(tmp_path, generator_type="ansi", ext=".ans", content="x")
    Path(rec.media_file_path).unlink()
    widget = p._build_artifact_widget(rec)  # must not raise
    assert isinstance(widget, Gtk.Label)
    assert widget.get_label() == "Result file not found."


# ── Recents strip: real thumbnails for image/gif/svg, labeled chips (never
# a bare "?") for ansi/palette/text/code ────────────────────────────────────

def test_recent_card_svg_shows_thumbnail_widget(tmp_path):
    p = cv.CreateResultPanel()
    rec = _artgen_rec(
        tmp_path, generator_type="landscape", ext=".svg",
        content='<svg xmlns="http://www.w3.org/2000/svg"></svg>',
    )
    card = p._build_recent_card(rec)
    assert isinstance(card.get_child(), Gtk.Picture)


def test_recent_card_verse_shows_verse_labeled_chip_not_bare_question_mark(tmp_path):
    p = cv.CreateResultPanel()
    rec = _artgen_rec(tmp_path, generator_type="verse", ext=".txt", content="a poem")
    card = p._build_recent_card(rec)
    assert card.get_label() == "Verse"


def test_recent_card_ansi_shows_ansi_labeled_chip_not_bare_question_mark(tmp_path):
    p = cv.CreateResultPanel()
    rec = _artgen_rec(tmp_path, generator_type="ansi", ext=".ans", content="x")
    card = p._build_recent_card(rec)
    assert card.get_label() == "ANSI"


def test_recent_card_palette_shows_palette_labeled_chip(tmp_path):
    p = cv.CreateResultPanel()
    rec = _artgen_rec(
        tmp_path, generator_type="palette", ext=".json",
        content='{"name":"x","colors":[]}',
    )
    card = p._build_recent_card(rec)
    assert card.get_label() == "Palette"


def test_recent_card_codeart_shows_code_labeled_chip(tmp_path):
    p = cv.CreateResultPanel()
    rec = _artgen_rec(tmp_path, generator_type="codeart", ext=".py", content="x = 1\n")
    card = p._build_recent_card(rec)
    assert card.get_label() == "Code"


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
