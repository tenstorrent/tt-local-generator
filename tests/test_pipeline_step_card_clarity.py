import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
try:
    import gi; gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()
except Exception:
    pytest.skip("no GTK display", allow_module_level=True)

import pipeline_studio as ps


def _labels(widget, acc):
    if isinstance(widget, Gtk.Label):
        acc.append(widget.get_label())
    child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
    while child is not None:
        _labels(child, acc)
        child = child.get_next_sibling()
    return acc


def test_remix_step_card_shows_flow_line():
    rv = ps.RemixView()
    card = rv._build_step_card(2, "2", "TTLGAnimateDiff", [])
    texts = _labels(card, [])
    assert any("→ makes a looping GIF" in t for t in texts)
