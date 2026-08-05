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
    import artgen_gallery as ag
    g = ag.ArtgenGallery.__new__(ag.ArtgenGallery)
    # on_remix (popover) attribute is gone; on_remix_as_pipeline remains the seam.
    assert not hasattr(ag.ArtgenGallery, "on_remix") or "on_remix_as_pipeline" in inspect.getsource(ag)
