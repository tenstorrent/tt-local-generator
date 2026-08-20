# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for making `ArtgenDetail`'s WebKit reading-view build LAZILY (deep-review
consistency finding, pr23-review-fixes/f8).

Why this exists: `ArtgenDetail.__init__` used to EAGERLY construct a real
`WebKit.WebView()` whenever `_WEBKIT_OK` was True, even though the reading view
(verse/markdown/palette) is only ever shown for a subset of artgen record
kinds. This mirrors `activity_viz`'s lazy-build pattern (a web-process-backed
widget should not be paid for on every `ArtgenDetail()` construction, nor on
every test that builds one but never opens a reading view). Behavior once a
reading view IS actually shown must stay identical -- these tests pin the two
degrade paths (no WebKit at all; WebKit available but not yet built) plus the
one new lazy-build seam, `_ensure_webview()`.

Run under xvfb (GTK4 widgets need a real display):
    xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_artgen_detail_lazy_webkit.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _gtk_available() -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk  # noqa: F401
        return True
    except Exception:
        return False


gtk_required = pytest.mark.skipif(
    not _gtk_available(), reason="GTK4 display not available"
)


@gtk_required
def test_construction_builds_no_webview():
    """The core lazy-build assertion: `__init__` must never construct a
    `WebKit.WebView`, regardless of `_WEBKIT_OK`. Before this change, `_webview`
    was a real `WebKit.WebView()` the instant `_WEBKIT_OK` was True in this
    environment -- this test would have FAILED against that code."""
    from artgen_detail import ArtgenDetail

    d = ArtgenDetail()

    assert d._webview is None


@gtk_required
def test_reading_fallback_always_built_regardless_of_webkit_availability():
    """`_reading_fallback` (the plain-text degrade label) must exist and be
    wired into the stack as "reading" unconditionally now -- it's no longer
    inside an `if _WEBKIT_OK: ... else: ...` split."""
    from artgen_detail import ArtgenDetail

    d = ArtgenDetail()

    assert d._reading_fallback is not None
    assert d._art_stack.get_child_by_name("reading") is not None


@gtk_required
def test_reading_render_without_webkit_uses_fallback(monkeypatch):
    """No WebKit available at all: `_load_html` must degrade to the
    tag-stripped fallback label and select the "reading" stack child, exactly
    as it did before this refactor."""
    import artgen_detail

    monkeypatch.setattr(artgen_detail, "_WEBKIT_OK", False)
    d = artgen_detail.ArtgenDetail()

    d._load_html("<p>hello &amp; bye</p>")

    assert d._webview is None
    assert d._reading_fallback.get_label() == "hello &amp; bye"
    assert d._art_stack.get_visible_child_name() == "reading"


@gtk_required
def test_ensure_webview_builds_reading_web_child_when_available(monkeypatch):
    """When WebKit IS available, `_ensure_webview()` builds the WebView lazily
    (first call only), wires it into the stack as a NEW "reading-web" child
    (distinct from the always-present "reading" fallback child), and returns
    True. A fake `WebKit.WebView` (a real `Gtk.Box` subclass) is used instead
    of the real WebKit widget to stay CI-portable -- constructing a real
    `WebKit.WebView` is safe in this dev environment (see module docstring in
    artgen_detail.py) but a fake keeps this test honest about ONLY exercising
    `_ensure_webview`'s wiring, not WebKit itself."""
    import artgen_detail
    from gi.repository import Gtk

    class _FakeSettings:
        def set_enable_javascript(self, _v):
            pass

    class _FakeWebView(Gtk.Box):
        def get_settings(self):
            return _FakeSettings()

    monkeypatch.setattr(artgen_detail, "_WEBKIT_OK", True)
    monkeypatch.setattr(
        artgen_detail, "WebKit",
        type("FakeWebKitModule", (), {"WebView": _FakeWebView}),
    )

    d = artgen_detail.ArtgenDetail()
    assert d._webview is None  # still lazy before the first call

    result = d._ensure_webview()

    assert result is True
    assert d._webview is not None
    assert isinstance(d._webview, _FakeWebView)
    assert d._art_stack.get_child_by_name("reading-web") is not None

    # Idempotent: a second call must not rebuild it.
    same = d._webview
    assert d._ensure_webview() is True
    assert d._webview is same


@gtk_required
def test_ensure_webview_returns_false_when_webkit_unavailable(monkeypatch):
    import artgen_detail

    monkeypatch.setattr(artgen_detail, "_WEBKIT_OK", False)
    d = artgen_detail.ArtgenDetail()

    assert d._ensure_webview() is False
    assert d._webview is None
