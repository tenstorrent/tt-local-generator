"""Tests for the Servers popover dismiss-on-focus-loss behavior.

The Servers popover uses set_autohide(False) so it stays open (non-modal) while
a service boots and its startup log streams. The trade-off is that a
non-autohide popover surface is not tied to window focus and would linger on top
of other applications' windows when you switch away. ControlPanel wires the
toplevel's notify::is-active to _servers_popover_on_active() to close it on a
genuine app switch while leaving it open during in-app clicks.

Real WM focus changes can't be driven headlessly, so we test the decision method
directly with mocked window/popover objects.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

# System PyGObject lives outside the venv; make it importable like the other
# main_window tests do.
_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _decide(is_active: bool, visible: bool):
    """Invoke _servers_popover_on_active with mocked win/popover; return the
    popover mock so the caller can assert on popdown()."""
    import main_window as mw

    obj = mw.ControlPanel.__new__(mw.ControlPanel)  # skip GTK __init__
    win = MagicMock()
    win.get_property.return_value = is_active
    popover = MagicMock()
    popover.get_visible.return_value = visible

    mw.ControlPanel._servers_popover_on_active(obj, win, popover)
    return popover


def test_dismissed_when_window_becomes_inactive():
    """App switch: window inactive + popover visible → popdown()."""
    popover = _decide(is_active=False, visible=True)
    popover.popdown.assert_called_once_with()


def test_kept_open_while_window_active():
    """Clicking the popover's own buttons keeps the toplevel active → stay open."""
    popover = _decide(is_active=True, visible=True)
    popover.popdown.assert_not_called()


def test_no_popdown_when_already_hidden():
    """A stray inactive notification after the popover closed must not re-trigger."""
    popover = _decide(is_active=False, visible=False)
    popover.popdown.assert_not_called()
