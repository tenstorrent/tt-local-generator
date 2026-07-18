# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for "unify gallery interaction pattern" Task 1 — a no-behavior-change
prep refactor that removes the `self._detail.get_parent() is _detail_wrap`
assumption from the detail-pane show/hide path, and adds a defensive guard
to `_gallery_for_type` for "artgen".

**Why this matters:** a later task inserts a `Gtk.Stack` between
`_detail_wrap` and `self._detail` to host a second renderer. Once that
lands, `self._detail.get_parent()` returns the new `Gtk.Stack`, not
`_detail_wrap` — so `_on_toggle_detail`'s old
`self._detail.get_parent().set_visible(...)` would silently start hiding/
showing the wrong widget (the stack, which stays a fixed size inside the
still-visible `_detail_wrap`) instead of collapsing the whole right-hand
pane. This task collapses BOTH existing call sites (the ✕ dismiss-bar
button and the `win.toggle-detail` menu action) onto one new method,
`_set_detail_pane_visible(visible)`, that targets `self._detail_wrap`
directly — so the later Gtk.Stack insertion cannot regress this behavior
again.

Also: `_gallery_for_type` used to silently return `_video_gallery` for
media_type "artgen" (no explicit branch) even though `ArtgenGallery` does
not implement `GalleryWidget`'s API (`all_cards()`/`delete_card()`/
`replace_pending_with()`/...). That's a landmine for any future caller that
passes an artgen record's media_type through this helper. This task makes
the misroute loud (`raise ValueError`) instead of silent.

Harness mirrors `test_main_window_attractor_model_source.py`'s `_make_mw`:
a bare `MainWindow.__new__` with the real (unbound) methods under test
bound on, and every collaborator it touches stubbed with plain fakes/Mocks
— no real widget tree is built, so the GTK-display probe below is really
just import-time insurance (matches the rest of the suite's convention).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

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

_SRC = (Path(__file__).parent.parent / "app" / "main_window.py").read_text()


# ── Source-level assertions (routing + the removed assumption) ─────────────

def test_set_detail_pane_visible_method_exists():
    assert "def _set_detail_pane_visible(self" in _SRC


def test_close_bar_routes_through_set_detail_pane_visible():
    """The ✕ dismiss-bar button's click handler must call the new method
    instead of reaching for `self._detail_wrap`/`self._inner_paned` inline."""
    start = _SRC.index('_detail_close_btn.connect(')
    end = _SRC.index("_detail_close_bar.append(_detail_close_btn)", start)
    handler_src = _SRC[start:end]
    assert "self._set_detail_pane_visible(False)" in handler_src
    # The old inline lambda body must be gone from this call site.
    assert "self._detail_wrap.set_visible(False)" not in handler_src


def test_on_toggle_detail_routes_through_set_detail_pane_visible_and_drops_get_parent():
    start = _SRC.index("def _on_toggle_detail(self")
    end = _SRC.index("\n    def ", start + 1)
    body = _SRC[start:end]
    assert "self._set_detail_pane_visible(not self._detail_visible)" in body
    assert "action.set_state(GLib.Variant(\"b\", self._detail_visible))" in body
    # The assumption this task removes:
    assert "self._detail.get_parent()" not in body


def test_gallery_for_type_has_explicit_artgen_guard():
    start = _SRC.index("def _gallery_for_type(self")
    end = _SRC.index("\n    def ", start + 1)
    body = _SRC[start:end]
    assert '"artgen"' in body
    assert "raise ValueError" in body


# ── Behavioral tests (real unbound methods on a bare stand-in) ──────────────

def _make_bare_mw():
    """Bind the real `_set_detail_pane_visible`/`_on_toggle_detail` methods
    onto a `MainWindow.__new__` instance with fake `_detail_wrap`/
    `_inner_paned` widgets — plain Mocks are enough since both methods only
    call `set_visible`/`get_allocation`/`set_position`, never touch anything
    else on the real widget tree."""
    import main_window as mw

    obj = mw.MainWindow.__new__(mw.MainWindow)
    obj._detail_visible = True
    obj._detail_wrap = MagicMock()
    obj._inner_paned = MagicMock()
    obj._inner_paned.get_allocation.return_value.width = 987

    for name in ("_set_detail_pane_visible", "_on_toggle_detail"):
        setattr(obj, name, getattr(mw.MainWindow, name).__get__(obj))

    return obj


def test_set_detail_pane_visible_false_hides_wrap_and_snaps_paned_to_full_width():
    obj = _make_bare_mw()
    obj._set_detail_pane_visible(False)
    assert obj._detail_visible is False
    obj._detail_wrap.set_visible.assert_called_once_with(False)
    # Same repositioning the old ✕ handler did when hiding: snap the paned's
    # divider to the window's full allocated width.
    obj._inner_paned.set_position.assert_called_once_with(987)


def test_set_detail_pane_visible_true_shows_wrap_without_repositioning_paned():
    """Showing the pane never repositioned `inner_paned` before this task
    (the old ✕ handler only ever set visible=False) — preserve that."""
    obj = _make_bare_mw()
    obj._detail_visible = False
    obj._set_detail_pane_visible(True)
    assert obj._detail_visible is True
    obj._detail_wrap.set_visible.assert_called_once_with(True)
    obj._inner_paned.set_position.assert_not_called()


def test_set_detail_pane_visible_tolerates_missing_inner_paned():
    """Mirrors the old lambda's `hasattr(self, "_inner_paned") else None`
    guard — must not raise if `_inner_paned` isn't set yet."""
    import main_window as mw

    obj = mw.MainWindow.__new__(mw.MainWindow)
    obj._detail_visible = True
    obj._detail_wrap = MagicMock()
    obj._set_detail_pane_visible = mw.MainWindow._set_detail_pane_visible.__get__(obj)

    obj._set_detail_pane_visible(False)  # must not raise
    assert obj._detail_visible is False
    obj._detail_wrap.set_visible.assert_called_once_with(False)


def test_on_toggle_detail_toggles_via_detail_wrap_not_get_parent():
    obj = _make_bare_mw()
    action = MagicMock()
    obj._detail_visible = True

    obj._on_toggle_detail(action, None)

    assert obj._detail_visible is False
    obj._detail_wrap.set_visible.assert_called_once_with(False)
    obj._inner_paned.set_position.assert_called_once_with(987)
    action.set_state.assert_called_once()

    obj._detail_wrap.reset_mock()
    obj._inner_paned.reset_mock()
    obj._on_toggle_detail(action, None)

    assert obj._detail_visible is True
    obj._detail_wrap.set_visible.assert_called_once_with(True)
    obj._inner_paned.set_position.assert_not_called()


# ── `_gallery_for_type` behavior ────────────────────────────────────────────

def _make_gallery_mw():
    import main_window as mw

    obj = mw.MainWindow.__new__(mw.MainWindow)
    obj._image_gallery = "IMAGE_GALLERY"
    obj._animate_gallery = "ANIMATE_GALLERY"
    obj._video_gallery = "VIDEO_GALLERY"
    obj._gallery_for_type = mw.MainWindow._gallery_for_type.__get__(obj)
    return obj


@pytest.mark.parametrize(
    "media_type,expected",
    [
        ("image", "IMAGE_GALLERY"),
        ("animate", "ANIMATE_GALLERY"),
        ("video", "VIDEO_GALLERY"),
        ("some-unknown-type", "VIDEO_GALLERY"),  # unchanged fallback behavior
    ],
)
def test_gallery_for_type_unchanged_for_native_types(media_type, expected):
    obj = _make_gallery_mw()
    assert obj._gallery_for_type(media_type) == expected


def test_gallery_for_type_raises_for_artgen():
    obj = _make_gallery_mw()
    with pytest.raises(ValueError):
        obj._gallery_for_type("artgen")
