# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
"Unify gallery interaction" Task 7 — `ArtgenDetail` gets a "⛶ Fullscreen"
button (parity with native `DetailPanel`'s ⛶ buttons, which open
`VideoPlayerWindow`/`ImageViewerWindow`) that opens `ArtgenViewerWindow` for
the currently-shown record, and the vestigial visible "← Gallery" back
button is removed.

Why the back button is vestigial: in the two-pane right-stack layout (Task
2/3), the gallery grid is always visible on the left -- there is no "go back
to the grid" navigation left to do, and native `DetailPanel` has no back
button at all. `on_back` itself (the plain callable attribute) stays: Task 3
wired `main_window.py`'s `self._artgen_detail.on_back =
lambda: self._set_detail_pane_visible(False)` so `_delete_confirmed` can
still collapse the pane when a delete empties the record list. Only the
visible widget + its label setter go away.

Creating GTK widgets needs a display; the full suite runs under xvfb. When no
display is available this module skips itself (matches the repo's headless
fallback for GTK-widget tests).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)


def _media_record(tmp_path, filename="lore.txt", content="Once upon a time...",
                   generator_type="lore", thumbnail_path="", media_id="mr-1"):
    """Build a real MediaRecord backed by a real file on disk (mirrors the
    fixture in test_artgen_pipeline_bridge.py)."""
    from media_store import MediaRecord

    p = tmp_path / filename
    if content is not None:
        p.write_text(content, encoding="utf-8")
    return MediaRecord(
        id=media_id,
        media_type="artgen",
        created_at="2026-07-01T00:00:00Z",
        file_path=str(p),
        thumbnail_path=thumbnail_path,
        prompt="a lore prompt",
        model_id="artgen-qwen3-8b",
        generator_type=generator_type,
        params="{}",
        starred=0,
    )


# ── Visible back button is gone ──────────────────────────────────────────────

def test_no_visible_back_button():
    from artgen_detail import ArtgenDetail

    detail = ArtgenDetail()

    assert not hasattr(detail, "_back_btn")


def test_set_back_label_method_is_gone():
    from artgen_detail import ArtgenDetail

    detail = ArtgenDetail()

    assert not hasattr(detail, "set_back_label")


def test_no_gallery_label_widget_anywhere_in_the_built_tree():
    """Walk the whole built widget tree and confirm no button carries the old
    "← Gallery" label -- proves the button was removed, not just renamed off
    of `_back_btn`."""
    from artgen_detail import ArtgenDetail

    detail = ArtgenDetail()

    def _walk(widget):
        yield widget
        child = widget.get_first_child()
        while child is not None:
            yield from _walk(child)
            child = child.get_next_sibling()

    labels = [
        w.get_label() for w in _walk(detail)
        if isinstance(w, Gtk.Button) and w.get_label() is not None
    ]
    assert "← Gallery" not in labels


# ── on_back callback still works (delete-empties-list path, Task 3) ─────────

def test_on_back_still_invoked_when_delete_empties_the_list(tmp_path):
    from artgen_detail import ArtgenDetail

    detail = ArtgenDetail()
    calls = []
    detail.on_back = lambda: calls.append(True)
    rec = _media_record(tmp_path)
    detail.show_record(rec.id, [rec])

    class _FakeDialog:
        def choose_finish(self, result):
            return 1  # "Delete"

    detail._delete_confirmed(_FakeDialog(), None, rec.id)

    assert calls == [True]


def test_on_back_not_invoked_when_records_remain(tmp_path):
    from artgen_detail import ArtgenDetail

    detail = ArtgenDetail()
    calls = []
    detail.on_back = lambda: calls.append(True)
    rec1 = _media_record(tmp_path, filename="a.txt", media_id="a")
    rec2 = _media_record(tmp_path, filename="b.txt", media_id="b")
    detail.show_record(rec1.id, [rec1, rec2])

    class _FakeDialog:
        def choose_finish(self, result):
            return 1  # "Delete"

    detail._delete_confirmed(_FakeDialog(), None, rec1.id)

    assert calls == []


# ── ⛶ Fullscreen button ──────────────────────────────────────────────────────

def test_fullscreen_button_opens_artgen_viewer_window_for_current_record(tmp_path, monkeypatch):
    import artgen_detail as ad_mod

    captured = []

    class _FakeViewer:
        def __init__(self, record, parent_window):
            captured.append((record, parent_window))

        def present(self):
            captured.append("presented")

    monkeypatch.setattr(ad_mod, "ArtgenViewerWindow", _FakeViewer)

    detail = ad_mod.ArtgenDetail()
    rec1 = _media_record(tmp_path, filename="a.txt", media_id="a")
    rec2 = _media_record(tmp_path, filename="b.txt", media_id="b")
    detail.show_record(rec2.id, [rec1, rec2])

    detail._full_btn.emit("clicked")

    assert len(captured) == 2
    record, parent_window = captured[0]
    assert record is rec2  # the CURRENTLY shown record, not the first in the list
    assert parent_window is detail.get_root()
    assert captured[1] == "presented"


def test_fullscreen_button_insensitive_and_noop_with_no_records(tmp_path, monkeypatch):
    import artgen_detail as ad_mod

    captured = []

    class _FakeViewer:
        def __init__(self, record, parent_window):
            captured.append((record, parent_window))

        def present(self):
            captured.append("presented")

    monkeypatch.setattr(ad_mod, "ArtgenViewerWindow", _FakeViewer)

    detail = ad_mod.ArtgenDetail()

    assert detail._full_btn.get_sensitive() is False

    detail._full_btn.emit("clicked")  # must not raise, must not construct

    assert captured == []


def test_fullscreen_button_sensitive_after_show_record(tmp_path):
    from artgen_detail import ArtgenDetail

    detail = ArtgenDetail()
    rec = _media_record(tmp_path)
    detail.show_record(rec.id, [rec])

    assert detail._full_btn.get_sensitive() is True
