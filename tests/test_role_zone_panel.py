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
    """ImageParamPanel's one ROLE_BRIEF field (negative_prompt) must land in
    the brief zone, not direction or controls."""
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
