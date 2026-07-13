# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
import sys; from pathlib import Path
sys.path.insert(0, "/usr/lib/python3/dist-packages")
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pytest
try:
    import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk
    Gtk.Box()
except Exception:
    pytest.skip("no GTK display", allow_module_level=True)
import gtk_layout

def test_wrap_centered_returns_widget_containing_content():
    content = Gtk.Label(label="x")
    w = gtk_layout.wrap_centered(content, 700)
    assert isinstance(w, Gtk.Widget)

def test_pipeline_studio_still_exposes_aliases():
    import pipeline_studio as ps
    assert ps._MaxWidthBin is gtk_layout.MaxWidthBin
    assert ps._wrap_centered is gtk_layout.wrap_centered
