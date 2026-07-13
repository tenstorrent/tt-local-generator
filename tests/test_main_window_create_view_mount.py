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
    assert "self._create_view = CreateView()" in _SRC
    assert 'self._gallery_stack.add_named(self._create_view, "create")' in _SRC


def test_loop_nav_create_does_not_yet_route_to_create_view():
    """Migration-safety guard: the Create movement must still resolve through
    `_on_source_change` (the existing generation UI), not switch
    `_gallery_stack` directly to "create" — that re-wiring is a later task
    (see docs/superpowers/plans/2026-07-13-create-surface.md, Task 8)."""
    assert (
        'def _on_loop_nav_create(self) -> None:' in _SRC
    )
    # The handler body must not reference the new "create" stack child.
    start = _SRC.index("def _on_loop_nav_create(self) -> None:")
    end = _SRC.index("def _on_loop_nav_discover", start)
    body = _SRC[start:end]
    assert '"create"' not in body
    assert "_gallery_stack.set_visible_child_name" not in body
