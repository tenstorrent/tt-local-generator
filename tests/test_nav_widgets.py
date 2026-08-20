import sys
from pathlib import Path
sys.path.insert(0, "/usr/lib/python3/dist-packages")
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pytest
try:
    import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk
    Gtk.Entry()
except Exception:
    pytest.skip("no GTK display available", allow_module_level=True)

from nav_state import NavState, Crumb, Context
from nav_widgets import Breadcrumb, ContextTray

def _labels(box):
    out, ch = [], box.get_first_child()
    while ch is not None:
        # descend one level to catch Button/Label labels
        if isinstance(ch, Gtk.Button): out.append(("btn", ch.get_label()))
        elif isinstance(ch, Gtk.Label): out.append(("lbl", ch.get_label()))
        else: out.append(("box", None))
        ch = ch.get_next_sibling()
    return out

def test_breadcrumb_renders_links_and_leaf(monkeypatch):
    # render synchronously: make GLib.idle_add call immediately
    import nav_widgets
    monkeypatch.setattr(nav_widgets.GLib, "idle_add", lambda fn, *a: (fn(*a), False)[1])
    ns = NavState()
    nav = []
    bc = Breadcrumb(ns, on_navigate=lambda t: nav.append(t))
    ns.set_crumbs([Crumb("Library", "library"), Crumb("Lighthouse")])
    kinds = _labels(bc)
    assert ("btn", "Library") in kinds       # linked crumb -> button
    assert ("lbl", "Lighthouse") in kinds     # leaf crumb -> plain label
    # clicking the link routes on_navigate with the target
    ch = bc.get_first_child()
    while ch is not None and not (isinstance(ch, Gtk.Button) and ch.get_label() == "Library"):
        ch = ch.get_next_sibling()
    ch.emit("clicked")
    assert nav == ["library"]

def test_context_tray_chip_resume_and_dismiss(monkeypatch):
    import nav_widgets
    monkeypatch.setattr(nav_widgets.GLib, "idle_add", lambda fn, *a: (fn(*a), False)[1])
    ns = NavState()
    resumed, dismissed = [], []
    tray = ContextTray(ns, on_resume=lambda i: resumed.append(i), on_dismiss=lambda i: dismissed.append(i))
    assert tray.get_visible() is False           # empty -> hidden
    ns.open_context(Context("pipeline", "Pipeline 2/5", kind="pipeline", running=True))
    assert tray.get_visible() is True
    # find the resume + close buttons in the single chip
    chip = tray.get_first_child()
    btns = []
    c = chip.get_first_child()
    while c is not None:
        if isinstance(c, Gtk.Button): btns.append(c)
        c = c.get_next_sibling()
    # first button = open/resume (label carries the context label), last = ✕
    btns[0].emit("clicked"); assert resumed == ["pipeline"]
    btns[-1].emit("clicked"); assert dismissed == ["pipeline"]
