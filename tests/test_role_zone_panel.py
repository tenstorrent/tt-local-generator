# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for `RoleZonePanel` — the shared three-zone renderer (Create surface
redesign, Task 5: `.superpowers/sdd/task-5-brief.md`).

`RoleZonePanel` wraps ANY existing `CreateParamPanel` and lays its already-
built field widgets into three zones ("Your brief" / "Direction" / collapsed
"Controls") without ever rebuilding them. The hard migration invariant this
task carries: `RoleZonePanel.collect()` MUST return the wrapped panel's
`collect()` output byte-for-byte, because that dict feeds real generation
workers. These tests pin that invariant for both a native panel (Image) and
an introspected artgen panel (landscape), plus the zone-structure and
marker-glyph-labeling behavior described in the task brief.
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
import field_roles as fr


def _medium(kind="image"):
    from create_mediums import Medium
    return Medium(id="image", label="Image", icon="🖼", kind=kind, source="native")


def test_zones_present_and_controls_collapsed():
    rp = cpp.RoleZonePanel(cpp.ImageParamPanel(), _medium())
    # brief, direction, controls expander all built
    assert rp._brief_zone is not None and rp._direction_zone is not None
    assert rp._controls_expander.get_expanded() is False


def test_collect_matches_legacy_image():
    legacy = cpp.ImageParamPanel(); legacy.build()
    rp = cpp.RoleZonePanel(cpp.ImageParamPanel(), _medium())
    # default (untouched) collect() must equal the legacy panel's default dict
    assert rp.collect() == legacy.collect()


def test_collect_matches_legacy_artgen():
    legacy = cpp.ArtgenParamPanel("landscape"); legacy.build()
    rp = cpp.RoleZonePanel(cpp.ArtgenParamPanel("landscape"), _medium("image"))
    assert rp.collect() == legacy.collect()


def test_interpreted_field_has_spark_glyph():
    rp = cpp.RoleZonePanel(cpp.ArtgenParamPanel("landscape"), _medium())
    labels = rp._direction_label_texts()   # test helper returning the rendered zone labels
    assert any("✨" in t for t in labels)


# ── Additional coverage beyond the brief's Step 1 tests ─────────────────────


def test_applied_modifier_text_delegates_to_pills():
    rp = cpp.RoleZonePanel(cpp.ImageParamPanel(), _medium())
    assert rp.applied_modifier_text() == ""
    rp.append_modifier_for_test("gritty texture")
    assert rp.applied_modifier_text() == "gritty texture"


def test_brief_zone_holds_negative_prompt_field():
    """ImageParamPanel's `negative_prompt` field (one of its two ROLE_BRIEF
    fields, alongside SP-3c-1's `seed_image_path`) must land in the brief
    zone, not direction or controls."""
    rp = cpp.RoleZonePanel(cpp.ImageParamPanel(), _medium())
    # Walk the brief zone's children looking for the negative-prompt entry.
    found = False
    child = rp._brief_zone.get_first_child()
    while child is not None:
        if child is rp._panel._neg_entry or _contains_widget(child, rp._panel._neg_entry):
            found = True
        child = child.get_next_sibling()
    assert found


def _contains_widget(container, target) -> bool:
    if container is target:
        return True
    child = container.get_first_child() if hasattr(container, "get_first_child") else None
    while child is not None:
        if _contains_widget(child, target):
            return True
        child = child.get_next_sibling()
    return False


def test_model_field_is_not_placed_in_any_zone():
    """kind="model" is a deliberate special case (FieldSpec docstring) — not
    one of the brief/direction/control zones, so RoleZonePanel must not raise
    trying to relocate it and must not place it under any zone."""
    rp = cpp.RoleZonePanel(cpp.ImageParamPanel(), _medium())
    assert not _contains_widget(rp._brief_zone, rp._panel._model_dropdown)
    assert not _contains_widget(rp._direction_zone, rp._panel._model_dropdown)


def test_controls_expander_holds_control_fields():
    """ImageParamPanel's ROLE_CONTROL fields (steps/seed/guidance) must be
    reachable inside the collapsed Controls expander."""
    rp = cpp.RoleZonePanel(cpp.ImageParamPanel(), _medium())
    controls_child = rp._controls_expander.get_child()
    steps_row = rp._panel._row_for("num_inference_steps")
    assert _contains_widget(controls_child, steps_row)


def test_animate_path_field_gets_neutral_tooltip_not_marker_tip():
    """Task-4's oddity: a `kind="path"` field shouldn't get the words-marker
    tooltip ("Your words — the model turns this into art.") since that reads
    strangely next to a file-path row; it should keep a neutral tooltip."""
    rp = cpp.RoleZonePanel(cpp.AnimateParamPanel(), _medium("gif"))
    row = rp._panel._row_for("reference_video_path")
    assert row is not None
    label = row.get_first_child()
    assert isinstance(label, Gtk.Label)
    tip = label.get_tooltip_text()
    assert tip != fr.MARKER_TIP[fr.MARK_WORDS]


def _find_browse_button(row) -> "Gtk.Button | None":
    """Return the 'Browse…' Gtk.Button inside an Animate ref-video/ref-image
    row (label, entry, then the browse button)."""
    child = row.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            return child
        child = child.get_next_sibling()
    return None


def test_animate_pickers_derive_live_root_after_reparenting(monkeypatch):
    """Regression guard: after RoleZonePanel re-parents AnimateParamPanel's
    rows, the panel's own `build()` box (`self._widget`) is orphaned and its
    `get_root()` is None — but the Browse buttons are in the live RoleZonePanel
    widget tree, so THEY resolve the real window. The file-picker handlers must
    therefore derive their FileDialog transient parent from the clicked button
    (`_btn.get_root()`), not from `self._widget`, so the dialog stays parented
    to and modal against the main window once Task 6 mounts this.

    We capture the parent the handler actually passes to `Gtk.FileDialog.open`.
    Against 4ef08e7 (handlers used `self._widget.get_root()`) that parent would
    be None because `self._widget` is orphaned; with the fix it is the live
    window. Invoking the real handler (not just checking widget roots) is what
    makes this test genuinely fail against the pre-fix code.
    """
    panel = cpp.AnimateParamPanel()
    rp = cpp.RoleZonePanel(panel, _medium("gif"))

    win = Gtk.Window()
    win.set_child(rp)
    win.present()  # realize the widget tree so get_root() resolves the window

    # The panel's own build() box was re-parented away and discarded — orphaned.
    assert panel._widget.get_root() is None

    captured: "list" = []

    def _fake_open(self, parent, cancellable, callback):
        captured.append(parent)  # no dialog is actually shown

    monkeypatch.setattr(Gtk.FileDialog, "open", _fake_open)

    for key, handler in (
        ("reference_video_path", panel._on_pick_ref_video),
        ("reference_image_path", panel._on_pick_ref_image),
    ):
        row = panel._row_for(key)
        assert row is not None
        browse = _find_browse_button(row)
        assert browse is not None
        # The button (live in the tree) resolves the real window; the handler
        # must forward exactly that as the dialog's transient parent.
        assert browse.get_root() is win
        captured.clear()
        handler(browse)
        assert captured == [win]

    win.destroy()


def test_seed_image_well_derives_live_root_after_reparenting(monkeypatch):
    """SP-3c-1 review fix (Minor 1): the same regression guard as
    `test_animate_pickers_derive_live_root_after_reparenting`, for
    `ImageParamPanel`'s `SeedImageWell`.

    After `RoleZonePanel` re-parents `ImageParamPanel`'s rows (including the
    seed-image row) into its own brief/direction/controls zones, the panel's
    own `build()` box (`self._widget`) is orphaned — `get_root()` on it
    returns None. Unlike `AnimateParamPanel`'s Browse buttons (which derive
    their FileDialog parent from the CLICKED BUTTON), `SeedImageWell` IS the
    widget that gets re-parented directly — `_open_file_dialog` calls
    `self.get_root()` on the well itself, so as long as the well stays live
    in the RoleZonePanel tree (it does — `_row_for`/re-parenting moves rows,
    never destroys their contents), that resolves to the real window.
    """
    panel = cpp.ImageParamPanel()
    rp = cpp.RoleZonePanel(panel, _medium("image"))

    win = Gtk.Window()
    win.set_child(rp)
    win.present()  # realize the widget tree so get_root() resolves the window

    # The panel's own build() box was re-parented away and discarded — orphaned.
    assert panel._widget.get_root() is None
    # But the seed well itself, live in the RoleZonePanel tree, resolves the
    # real window.
    assert panel._seed_well.get_root() is win

    captured: "list" = []

    def _fake_open(self, parent, cancellable, callback):
        captured.append(parent)  # no dialog is actually shown

    monkeypatch.setattr(Gtk.FileDialog, "open", _fake_open)

    panel._seed_well._open_file_dialog()

    assert captured == [win]

    win.destroy()
