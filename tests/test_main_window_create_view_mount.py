# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Guard test for mounting CreateView in the main window (Create-surface plan,
Task 3: docs/superpowers/specs/2026-07-13-create-surface-design.md).

Constructing the full `MainWindow` (ControlPanel, GalleryWidget, DetailPanel,
history load, health workers, ...) is heavy and network/disk dependent (see
tests/test_main_window_pipelines.py's docstring). This task's mount is
additive-only — a new `_gallery_stack` child that the loop nav does NOT yet
route to — so, mirroring
test_main_window_pipelines.py::test_main_window_wires_artgen_panel_on_remix_as_pipeline_source's
source-text style, this asserts the mount lines exist rather than exercising
`_build_ui()` end-to-end.
"""
from __future__ import annotations

from pathlib import Path

_SRC = (Path(__file__).parent.parent / "app" / "main_window.py").read_text()


def test_create_view_is_imported():
    assert "from create_view import CreateView" in _SRC


def test_create_view_is_constructed_and_mounted_as_gallery_stack_child():
    assert "self._create_view = CreateView(" in _SRC
    # CreateView is mounted DIRECTLY as the "create" stack child (no outer
    # ScrolledWindow): it owns its own internal scroll for the form and pins
    # the ✨ Create CTA below that scroll, so the CTA is always visible.
    assert 'self._gallery_stack.add_named(self._create_view, "create")' in _SRC


def test_inspiration_door_wired_only_when_flag_on():
    """Task 5 (remix-without-pipeline-mode): the inspiration door hands off
    to the existing Muse seam — `_on_loop_nav_remix` already does exactly the
    unseeded `show_muse()` activation dance (see its docstring) — but only
    when pipeline mode is enabled; CreateView omits its Inspiration door
    entirely (`on_inspiration=None`) when the flag is off.
    """
    assert "on_inspiration=self._on_loop_nav_remix if app_settings.PIPELINE_MODE_ENABLED else None" in _SRC


def test_create_view_on_create_wired_to_real_generation():
    """Task 8 (switchover subset): the Create CTA now routes to real
    generation via `_on_create_generate`, not `on_create=None` (Task 3's
    placeholder)."""
    assert "on_create=self._on_create_generate" in _SRC


def test_loop_nav_create_now_routes_to_create_view():
    """Task 8 (switchover subset — see .superpowers/sdd/task-8-report.md,
    which overrides Task 8's plan-document wording of "remove the old
    tabs" for this task): the Create movement now switches `_gallery_stack`
    to "create" directly, instead of resolving through `_on_source_change`.
    The old ControlPanel/medium-tab UI is left fully intact in the source —
    this only asserts the loop-nav routing target changed."""
    assert 'def _on_loop_nav_create(self) -> None:' in _SRC
    start = _SRC.index("def _on_loop_nav_create(self) -> None:")
    end = _SRC.index("def _on_loop_nav_discover", start)
    body = _SRC[start:end]
    assert '"create"' in body
    assert '_gallery_stack.set_visible_child_name("create")' in body


def test_native_galleries_and_artgen_gallery_still_constructed():
    """SP-3d-5 update: ControlPanel itself is deleted (Task 8's switchover
    subset deferred that; this repo's SP-3d-5 is the "later task" it
    referred to), but the three native-medium galleries + the artgen gallery
    Discover browses are unaffected — still built and mounted, just via the
    standalone `ArtgenGallery` instead of `ArtgenPanel` for the "artgen" page."""
    assert 'self._gallery_stack.add_named(self._video_gallery, "video")' in _SRC
    assert 'self._gallery_stack.add_named(self._animate_gallery, "animate")' in _SRC
    assert 'self._gallery_stack.add_named(self._image_gallery, "image")' in _SRC
    assert 'self._gallery_stack.add_named(self._artgen_gallery, "artgen")' in _SRC
    assert "class ControlPanel(Gtk.Box):" not in _SRC
