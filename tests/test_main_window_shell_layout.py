# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for SP-3d Task 4 (.superpowers/sdd/task-4-brief.md; see
.superpowers/sdd/sp3d-audit.md §2): window-layout restructure that stops
mounting ControlPanel's `toolbar_box`/`footer_box` and collapses the 3-pane
`outer_paned` (controls | gallery | detail) down to a 2-pane `inner_paned`
(gallery | detail) split.

ControlPanel was NOT deleted by this task (that was SP-3d-5) -- at the time
this file was written it was still constructed, and `_ctrl_wrapper` (its
scrollable body's wrapper) was still built so the existing
`_ctrl_wrapper.set_visible(...)` calls scattered through
`_on_source_change`/`_show_pipelines`/`_on_loop_nav_create` kept working.
SP-3d-5 has since deleted ControlPanel, `_ctrl_wrapper`, and
`_on_source_change` entirely (superseded by `_sync_gallery_to_source`) --
the handful of tests below that asserted those still-existed are updated to
assert the opposite. What changes in THIS task is that `_ctrl_wrapper` (and,
transitively, `self._controls.footer_box`, which used to be appended into
it) is never attached anywhere in the window's widget tree, and
ControlPanel's `toolbar_box` (logo/title + the now-superseded medium-tab
toggle) is never read at all -- MainWindow's own buttons (▶ Play / Pipelines
/ Servers ▾), which used to be appended onto `toolbar_box`, fold directly
into the loop-nav row instead.

Constructing the full `MainWindow` is heavy (see test_main_window_pipelines.py's
docstring) -- these are source-level guards in the same style as
test_main_window_create_view_mount.py / test_main_window_servers_control.py,
plus one behavioral test that the loop-nav row (a real Gtk.Box, built by the
real unbound `_build_loop_nav`) is a widget the three buttons can actually be
appended onto.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

_SRC = (Path(__file__).parent.parent / "app" / "main_window.py").read_text()


# ── Source-level: toolbar_box / footer_box are no longer mounted ───────────

def test_controls_toolbar_box_never_read():
    """`self._controls.toolbar_box` (the property) must not be referenced
    anywhere in MainWindow -- it was the sole call site before this task."""
    assert "self._controls.toolbar_box" not in _SRC


def test_controls_footer_box_never_read():
    """`self._controls.footer_box` must not be referenced anywhere in
    MainWindow -- it used to be appended into `_ctrl_wrapper`."""
    assert "self._controls.footer_box" not in _SRC


def test_main_toolbar_composite_box_is_gone():
    """The old composite box (`main_toolbar = self._controls.toolbar_box`,
    with MainWindow's three buttons appended onto it, then
    `root_box.append(main_toolbar)`) no longer exists in any form."""
    assert "main_toolbar" not in _SRC


# ── Source-level: the three buttons fold into the loop-nav row ────────────

def test_loop_nav_row_captured_and_appended_to_root_box():
    assert "loop_nav_row = self._build_loop_nav()" in _SRC
    assert "root_box.append(loop_nav_row)" in _SRC


def test_attractor_btn_appended_to_loop_nav_row():
    """RN-1 (two-place nav): the ▶ Play button (`_attractor_btn`) is
    constructed and appended inside `_build_loop_nav` itself (onto its own
    local `row`, beside Library), not in `_build_ui`'s `loop_nav_row` -- same
    end result (the button lands in the row `_build_ui` mounts), different
    call site."""
    assert "row.append(self._attractor_btn)" in _SRC


def test_pipelines_btn_appended_only_when_flag_on():
    # the append is now guarded by the flag, not unconditional
    assert "if app_settings.PIPELINE_MODE_ENABLED" in _SRC
    assert "loop_nav_row.append(self._pipelines_btn)" in _SRC   # still present, now inside the guard


def test_servers_button_appended_to_loop_nav_row():
    assert "loop_nav_row.append(self._servers_control.servers_button)" in _SRC


# ── Source-level: 3-pane collapses to 2-pane ───────────────────────────────

def test_outer_paned_is_gone():
    """`outer_paned` (the controls | gallery-detail split) no longer exists
    in any form -- `inner_paned` (gallery | detail) is the window's only
    paned, appended directly to `root_box`."""
    assert "outer_paned" not in _SRC


def test_inner_paned_appended_directly_to_root_box():
    assert "root_box.append(inner_paned)" in _SRC


def test_ctrl_wrapper_is_gone():
    """SP-3d-5 update: `_ctrl_wrapper` (ControlPanel's scrollable-body
    wrapper -- unmounted since this task, but still constructed until
    SP-3d-5) is now deleted entirely, along with every `.set_visible()` call
    on it. Supersedes this file's earlier `test_ctrl_wrapper_still_built_but_
    never_mounted`, which asserted the opposite (that it WAS still built)."""
    assert "self._ctrl_wrapper = Gtk.Box(" not in _SRC
    assert "self._ctrl_wrapper.set_visible(" not in _SRC
    assert ".append(self._ctrl_wrapper)" not in _SRC
    assert ".set_start_child(self._ctrl_wrapper)" not in _SRC
    assert ".set_end_child(self._ctrl_wrapper)" not in _SRC


def test_control_panel_is_gone():
    """SP-3d-5 update: ControlPanel is deleted entirely (this task only
    stopped mounting its toolbar/footer; the class itself is gone now).
    Supersedes this file's earlier `test_control_panel_still_constructed`,
    which asserted the opposite (that it WAS still built)."""
    assert "self._controls = ControlPanel(" not in _SRC
    assert "class ControlPanel(Gtk.Box):" not in _SRC


# ── Behavioral: the loop-nav row really is an appendable Gtk widget ────────

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
def test_build_loop_nav_row_accepts_extra_appended_buttons():
    """The real (unbound) `_build_loop_nav` returns a plain `Gtk.Box`-derived
    row; MainWindow appends the Watch-TT-TV/Pipelines/Servers buttons onto it
    directly (see the source-level tests above). This proves that row is a
    genuine container new children can be appended to -- not, e.g., a
    `Gtk.ToggleButton` or something else that would silently no-op/raise."""
    from unittest.mock import patch
    import main_window as mw

    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)

    row = mw.MainWindow._build_loop_nav(obj)
    assert isinstance(row, Gtk.Box)

    extra = Gtk.Button(label="probe")
    row.append(extra)
    assert extra.get_parent() is row
