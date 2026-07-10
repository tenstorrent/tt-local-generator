"""GUI wiring for the codeart generator in the artgen panel.

Creating GTK widgets needs a display; the full suite runs under xvfb. When no
display is available this module skips itself (matches the repo's headless
fallback for GTK-widget tests).
"""
import sys
from pathlib import Path

import pytest

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

# Skip the whole module if a GTK display/widget cannot be created (headless).
try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)

import artgen_panel


def _panel():
    return artgen_panel.ArtgenPanel.__new__(artgen_panel.ArtgenPanel)


def test_controls_page_builds_codeart_widgets():
    p = _panel()
    box = p._build_controls_page("codeart")
    assert box is not None
    # widgets were created and stashed for _build_args to read
    assert p._code_language.get_text() == "python"
    assert p._code_inspiration.get_text() == "the nature of recursion"
    assert p._code_should_compile.get_active() is True


def test_build_args_reads_codeart_widgets():
    p = _panel()
    p._build_controls_page("codeart")           # create the widgets
    p._code_language.set_text("rust")
    p._code_inspiration.set_text("the tide")
    p._set_dd(p._code_style, "quine")
    p._code_should_compile.set_active(False)

    args = p._build_args("codeart")
    assert args.language == "rust"
    assert args.inspiration == "the tide"
    assert args.style == "quine"
    assert args.should_compile is False


def test_build_args_defaults_when_blank():
    p = _panel()
    p._build_controls_page("codeart")
    p._code_language.set_text("")
    p._code_inspiration.set_text("")
    args = p._build_args("codeart")
    assert args.language == "python"
    assert args.inspiration == "the nature of recursion"
