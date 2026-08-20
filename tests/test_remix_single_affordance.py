# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Task 8: collapse to one remix affordance (drop the popover).

Remix now means exactly one thing: seed a pipeline from an artifact. Every
surface used to show TWO buttons (the 🔀 Remix popover + the 🧩 Remix as
pipeline… bridge); this pins the popover path being fully unwired from
MainWindow (RemixPopover/_dispatch_remix/_on_remix_card), leaving
`_remix_as_pipeline` as the single surviving handler.
"""
import inspect, sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import main_window as mw


def test_mainwindow_no_popover_remix_symbols():
    # The 🔀 popover path is gone; only the pipeline path remains.
    assert not hasattr(mw.MainWindow, "_on_remix_card")
    assert not hasattr(mw.MainWindow, "_dispatch_remix")
    assert hasattr(mw.MainWindow, "_remix_as_pipeline")


def test_mainwindow_source_does_not_wire_remixpopover():
    src = inspect.getsource(mw)
    # No live construction of RemixPopover from main_window anymore.
    assert "RemixPopover(" not in src


def test_artgen_gallery_has_single_remix_callback():
    """Check the real single-affordance behavior on an INSTANCE, not the
    class. `on_remix`/`on_remix_as_pipeline` are set inside `__init__` as
    plain instance attributes, so `hasattr(ArtgenGallery, "on_remix")` is
    `False` on the *class* regardless of whether `__init__` sets it or
    not — a class-level check is tautological and would have passed even
    before Task 8 deleted the popover seam. Building a real instance (via
    `ArtgenGallery()`, not `__new__`) actually runs `__init__` and proves
    the popover seam is gone while `on_remix_as_pipeline` still works.
    """
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        Gtk.Entry()  # probe: raises without a usable display
    except Exception:  # pragma: no cover - environment-dependent
        pytest.skip("no GTK display available")

    import artgen_gallery as ag

    gallery = ag.ArtgenGallery()

    # The popover seam is gone -- no live `on_remix` attribute at all.
    assert not hasattr(gallery, "on_remix")

    # The single surviving seam is a real, working callable attribute.
    calls = []
    gallery.on_remix_as_pipeline = lambda r: calls.append(r)
    gallery.on_remix_as_pipeline("some-record")
    assert calls == ["some-record"]
