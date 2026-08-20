# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for "unify gallery interaction pattern" Task 3 — `ArtgenDetail.
pause_animation()`.

Why this exists: `ArtgenDetail` now lives as one of two children inside
MainWindow's shared `_right_stack` (a `Gtk.Stack`). Unlike the removed
artgen-gallery `Gtk.Overlay` (which this task deletes), a `Gtk.Stack` keeps
its hidden child realized -- so a GIF-driving `GLib` timer started while the
artgen pane was visible would otherwise keep firing forever after the stack
switches to the "native" `DetailPanel` child. `pause_animation()` exposes
`_render`'s own timer-cancellation guard as a public no-arg method so
`MainWindow._on_card_selected` can call it when switching the pane away from
"artgen" (see main_window.py).

Run under xvfb (GTK4 widgets need a real display):
    xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_artgen_detail_pause_animation.py -v
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
def test_pause_animation_cancels_a_running_gif_timer():
    from artgen_detail import ArtgenDetail
    from gi.repository import GLib

    detail = ArtgenDetail()
    # Simulate the state _animate_gif leaves behind while a GIF is playing,
    # without needing a real animated GIF file on disk.
    timer_id = GLib.timeout_add(60_000, lambda: True)
    detail._gif_timer_id = timer_id

    detail.pause_animation()

    assert detail._gif_timer_id is None
    # Proof the underlying GLib source was actually removed (not just the
    # attribute cleared) -- looking it up in the default main context returns
    # None once the source is gone.
    assert GLib.MainContext.default().find_source_by_id(timer_id) is None


@gtk_required
def test_pause_animation_is_a_noop_when_nothing_is_running():
    from artgen_detail import ArtgenDetail

    detail = ArtgenDetail()
    assert detail._gif_timer_id is None

    detail.pause_animation()  # must not raise

    assert detail._gif_timer_id is None
