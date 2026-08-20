# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for "unify gallery interaction pattern" Task 2 — makes the right-hand
detail pane a DUAL-RENDERER container: a `Gtk.Stack` holding both
`DetailPanel` (native video/image/animate records) and `ArtgenDetail`
(artgen records), so a later task (Task 3) can drive it from artgen clicks.

This task ONLY builds+wires the container — nothing yet switches it away
from "native", so it must be a no-op for existing native behavior. Task 1
(commit 5c8cc0c) already removed the `self._detail.get_parent() is
_detail_wrap` assumption for exactly this reason — see
test_detail_pane_toggle.py's docstring.

Mirrors test_main_window_create_view_mount.py's convention: constructing the
full `MainWindow` is heavy (network/disk/health-worker side effects), so
these are source-level guards, plus one behavioral test (mirroring
test_main_window_shell_layout.py's `_build_loop_nav` probe) that a real
`Gtk.Stack` with both children actually behaves as expected.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

_SRC = (Path(__file__).parent.parent / "app" / "main_window.py").read_text()


# ── Source-level: import + construction ────────────────────────────────────

def test_artgen_detail_is_imported():
    assert "from artgen_detail import ArtgenDetail" in _SRC


def test_artgen_detail_is_constructed():
    assert "self._artgen_detail = ArtgenDetail(" in _SRC


def test_right_stack_is_constructed():
    assert "self._right_stack = Gtk.Stack(" in _SRC


def test_right_stack_holds_both_renderers_native_default():
    assert 'self._right_stack.add_named(self._detail, "native")' in _SRC
    assert 'self._right_stack.add_named(self._artgen_detail, "artgen")' in _SRC
    assert 'self._right_stack.set_visible_child_name("native")' in _SRC


# ── Source-level: detail_wrap now appends the stack, not self._detail ──────

def test_detail_wrap_appends_right_stack_not_detail_directly():
    assert "self._detail_wrap.append(self._right_stack)" in _SRC
    # The old direct-append call site is gone -- self._detail is now nested
    # inside the stack, not a direct child of _detail_wrap.
    assert "self._detail_wrap.append(self._detail)" not in _SRC


def test_close_bar_and_queue_section_still_bracket_the_stack():
    """The ✕ dismiss bar stays above the stack and the queue section stays
    below it -- only the self._detail append became the stack append."""
    start = _SRC.index("self._detail_wrap.append(_detail_close_bar)")
    end = _SRC.index("inner_paned.set_end_child(self._detail_wrap)", start)
    body = _SRC[start:end]
    assert "self._detail_wrap.append(self._right_stack)" in body
    assert "self._detail_wrap.append(self._queue_section_lbl)" in body
    assert "self._detail_wrap.append(self._queue_box)" in body
    # Order: stack before queue section/box.
    assert body.index("self._detail_wrap.append(self._right_stack)") < body.index(
        "self._detail_wrap.append(self._queue_section_lbl)"
    )


# ── Source-level: partial callback wiring (Task 3 finishes the rest) ───────

def test_artgen_detail_remix_callbacks_wired():
    # Task 8 (remix-pipeline-unification): the former parallel `on_remix`
    # (popover) wiring is gone -- `on_remix_as_pipeline` is the single
    # surviving seam.
    assert "self._artgen_detail.on_remix = self._on_remix_card" not in _SRC
    assert (
        "self._artgen_detail.on_remix_as_pipeline = self._remix_as_pipeline" in _SRC
    )


def test_artgen_detail_gallery_dependent_callbacks_now_wired():
    """Unify-gallery-interaction-pattern Task 3 finishes the wiring Task 2
    deferred -- `on_back`/`on_deleted`/`on_starred` now route into the
    shared right pane / ArtgenGallery sync handlers (see
    test_artgen_detail_shared_pane_wiring.py for the behavioral coverage)."""
    assert "self._artgen_detail.on_back = " in _SRC
    assert "self._artgen_detail.on_deleted = self._on_artgen_detail_deleted" in _SRC
    assert "self._artgen_detail.on_starred = self._on_artgen_detail_starred" in _SRC


# ── Behavioral: a real Gtk.Stack really holds both real widgets ────────────

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()  # probe: raises without a usable display
    _HAVE_GTK_DISPLAY = True
except Exception:  # pragma: no cover - environment-dependent
    _HAVE_GTK_DISPLAY = False

import pytest


@pytest.mark.skipif(not _HAVE_GTK_DISPLAY, reason="no GTK display available")
def test_stack_with_detail_panel_and_artgen_detail_switches_without_raising():
    """Builds the exact same shape the real `__init__` code builds (a
    Gtk.Stack with a DetailPanel named "native" and an ArtgenDetail named
    "artgen", defaulting to "native") and proves switching the visible
    child works both ways without raising -- the container mechanics Task 3
    will drive."""
    from main_window import DetailPanel
    from artgen_detail import ArtgenDetail

    detail = DetailPanel(
        download_cb=lambda *a: None,
        on_localized_cb=lambda *a: None,
        star_cb=lambda *a: None,
    )
    artgen_detail = ArtgenDetail()

    stack = Gtk.Stack()
    stack.add_named(detail, "native")
    stack.add_named(artgen_detail, "artgen")
    stack.set_visible_child_name("native")

    assert stack.get_child_by_name("native") is detail
    assert stack.get_child_by_name("artgen") is artgen_detail
    assert stack.get_visible_child_name() == "native"

    stack.set_visible_child_name("artgen")
    assert stack.get_visible_child_name() == "artgen"

    stack.set_visible_child_name("native")
    assert stack.get_visible_child_name() == "native"
