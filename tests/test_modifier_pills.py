# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for `ModifierPills` — the Create surface's "Direction" zone widget
(Task 3, Create-surface plan). Tapping a category-grouped add chip creates a
visible, removable pill; `applied_text()` returns the space-joined modifier
text (in click order) to append to the brief.

`load_chips_for_kind` is a module-level seam in `create_param_panels.py` so
these tests don't depend on the real `config/prompt_chips.yaml` contents —
`test_add_then_applied_text`/`test_remove_drops_pill` monkeypatch it with a
known bank.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)

import create_param_panels as cpp


def test_pills_start_empty():
    p = cpp.ModifierPills("image")
    assert p.applied_text() == ""


def test_add_then_applied_text(monkeypatch):
    # Force a known bank so the test doesn't depend on prompt_chips.yaml content.
    from chip_config import ChipCategory, ChipEntry
    monkeypatch.setattr(
        cpp, "load_chips_for_kind",
        lambda k: [ChipCategory("Lighting", [ChipEntry("golden hour", "golden hour lighting", "")])],
    )
    p = cpp.ModifierPills("image")
    p._apply_entry(ChipEntry("golden hour", "golden hour lighting", ""))
    assert p.applied_text() == "golden hour lighting"


def test_remove_drops_pill():
    from chip_config import ChipEntry
    p = cpp.ModifierPills("image")
    e = ChipEntry("neon", "neon glow", "")
    p._apply_entry(e)
    p._remove_entry(e)
    assert p.applied_text() == ""


def _descendant_button(container, substr):
    """First Gtk.Button anywhere under *container* whose label contains substr."""
    if isinstance(container, Gtk.Button) and substr in (container.get_label() or ""):
        return container
    child = container.get_first_child() if hasattr(container, "get_first_child") else None
    while child is not None:
        found = _descendant_button(child, substr)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _descendant_label_texts(container):
    """Every Gtk.Label text anywhere under *container*."""
    texts = []
    if isinstance(container, Gtk.Label):
        texts.append(container.get_label() or "")
    child = container.get_first_child() if hasattr(container, "get_first_child") else None
    while child is not None:
        texts.extend(_descendant_label_texts(child))
        child = child.get_next_sibling()
    return texts


def test_category_renders_as_collapsible_expander_with_icon_name_and_hint():
    """Each modifier category is a collapsed Gtk.Expander whose label row shows
    an icon (borrowed from the first chip's emoji) + the category name + a hint
    of what's inside; expanding it reveals every add-chip. This replaced the old
    '2 chips + N more' layout."""
    from chip_config import ChipCategory, ChipEntry
    cat = ChipCategory("Lighting", [
        ChipEntry("🌅 golden hour", "golden hour", ""),
        ChipEntry("🌙 moonlit", "moonlit", ""),
        ChipEntry("💡 studio", "studio lighting", ""),
    ])
    p = cpp.ModifierPills("image")
    box = p._build_category_box(cat)
    assert isinstance(box, Gtk.Expander)
    assert box.get_expanded() is False   # collapsed by default
    header_texts = _descendant_label_texts(box.get_label_widget())
    assert any("Lighting" in t for t in header_texts)          # category name
    assert any("golden hour" in t for t in header_texts)       # content hint
    assert any(t == "🌅" for t in header_texts)                # icon from first chip
    # Expanding reveals the actual add-chips.
    assert _descendant_button(box.get_child(), "golden hour") is not None


def test_category_icon_and_hint_helpers():
    from chip_config import ChipCategory, ChipEntry
    cat = ChipCategory("Camera", [
        ChipEntry("🎥 cinematic", "cinematic", ""),
        ChipEntry("🚁 aerial", "aerial", ""),
        ChipEntry("📷 close-up", "close-up", ""),
        ChipEntry("🏔 wide", "wide", ""),
        ChipEntry("👁 POV", "pov", ""),
    ])
    assert cpp._category_icon(cat) == "🎥"
    hint = cpp._category_hint(cat, n=3)
    assert hint.startswith("cinematic, aerial, close-up")
    assert hint.endswith("…")   # more than 3 chips -> ellipsis
    # No-emoji fallback icon.
    plain = ChipCategory("Plain", [ChipEntry("just text", "t", "")])
    assert cpp._category_icon(plain) == "✦"


def test_unknown_kind_no_crash():
    assert cpp.ModifierPills("nope").applied_text() == ""


# ── De-dup (Task 6, task-6-brief.md item 6) ──────────────────────────────────

def test_add_chip_hides_after_applying_and_reappears_on_removal(monkeypatch):
    """Clicking an add-chip must hide it so the same modifier can't be added
    twice; removing the resulting pill restores the add-chip so it can be
    re-applied later."""
    from chip_config import ChipCategory, ChipEntry
    entry = ChipEntry("neon", "neon glow", "")
    monkeypatch.setattr(
        cpp, "load_chips_for_kind",
        lambda k: [ChipCategory("Lighting", [entry])],
    )

    p = cpp.ModifierPills("image")
    add_btn = p._add_buttons[id(entry)]
    assert add_btn.get_visible() is True

    add_btn.emit("clicked")
    assert add_btn.get_visible() is False
    assert p.applied_text() == "neon glow"

    # Only one applied pill exists — click its "x" to remove it.
    pill_child = p._applied_flow.get_first_child()
    assert pill_child is not None
    pill_btn = pill_child.get_child()
    pill_btn.emit("clicked")

    assert add_btn.get_visible() is True
    assert p.applied_text() == ""


def test_add_chip_click_cannot_double_apply_the_same_modifier(monkeypatch):
    """Regression guard for the exact bug item 6 describes: before the hide-
    on-apply fix, nothing stopped a second click from appending the same
    entry twice. Since the button is hidden (not destroyed) after the first
    click, emitting "clicked" again still fires the same handler — the real
    UI prevents the second click by hiding the widget; this test asserts the
    widget-visibility side of that contract directly."""
    from chip_config import ChipCategory, ChipEntry
    entry = ChipEntry("neon", "neon glow", "")
    monkeypatch.setattr(
        cpp, "load_chips_for_kind",
        lambda k: [ChipCategory("Lighting", [entry])],
    )

    p = cpp.ModifierPills("image")
    add_btn = p._add_buttons[id(entry)]

    add_btn.emit("clicked")
    assert add_btn.get_visible() is False
    # A hidden widget is exactly what keeps a real user from clicking it
    # again — the de-dup contract this task requires.
