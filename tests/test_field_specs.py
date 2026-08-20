# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for `FieldSpec` + `field_specs()` on every Create param panel (Task 4 of
the Create surface redesign — see `.superpowers/sdd/task-4-brief.md`).

`field_specs()` is purely additive metadata: it describes each panel's fields
(key/label/kind/default/role/choices/tooltip) so a later task's shared
RoleZonePanel can group them into brief/direction/control zones. It must NEVER
change what `collect()` returns — that dict is a hard migration invariant
feeding real generation. Every test here that touches `collect()` asserts it
is byte-for-byte the pre-existing shape.

The central correctness property checked throughout: every key `field_specs()`
emits for a native panel (Image/Video/Animate) must be one of that panel's
`collect()` keys, and vice versa (with the deliberate exception noted per
panel below), so the metadata can never silently drift from what actually
gets sent to a worker.
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


# ── ImageParamPanel ──────────────────────────────────────────────────────────


def test_image_panel_field_specs_roles():
    specs = {s.key: s for s in cpp.ImageParamPanel().field_specs()}
    assert specs["num_inference_steps"].role == fr.FieldRole(fr.ROLE_CONTROL, fr.MARK_EXACT)
    assert specs["negative_prompt"].role == fr.FieldRole(fr.ROLE_BRIEF, fr.MARK_WORDS)
    assert specs["model"].kind == "model"   # model handled specially, not a zone field


def test_image_panel_field_specs_keys_match_collect():
    """field_specs() must describe exactly the keys collect() returns — no
    more, no less — so the metadata can never drift from the real params."""
    panel = cpp.ImageParamPanel()
    collect_keys = set(panel.collect())
    spec_keys = {s.key for s in panel.field_specs()}
    assert spec_keys == collect_keys


def test_image_panel_model_spec_choices():
    specs = {s.key: s for s in cpp.ImageParamPanel().field_specs()}
    assert specs["model"].choices == cpp._IMAGE_MODEL_CHOICES


def test_collect_unchanged_image():
    # collect() must still produce the legacy dict shape, plus SP-3c-1's
    # migration-safe `seed_image_path` addition (defaults to "").
    d = cpp.ImageParamPanel().collect()
    assert set(d) == {
        "negative_prompt", "num_inference_steps", "seed", "guidance_scale", "model",
        "seed_image_path",
    }


# ── VideoParamPanel ───────────────────────────────────────────────────────────


def test_video_panel_field_specs_keys_match_collect():
    panel = cpp.VideoParamPanel()
    collect_keys = set(panel.collect())
    spec_keys = {s.key for s in panel.field_specs()}
    assert spec_keys == collect_keys


def test_video_panel_field_specs_roles():
    specs = {s.key: s for s in cpp.VideoParamPanel().field_specs()}
    assert specs["num_inference_steps"].role == fr.FieldRole(fr.ROLE_CONTROL, fr.MARK_EXACT)
    assert specs["num_frames"].role == fr.FieldRole(fr.ROLE_CONTROL, fr.MARK_EXACT)
    assert specs["negative_prompt"].role == fr.FieldRole(fr.ROLE_BRIEF, fr.MARK_WORDS)
    assert specs["model"].kind == "model"
    assert specs["model"].choices == cpp._VIDEO_MODEL_CHOICES


# ── AnimateParamPanel ─────────────────────────────────────────────────────────


def test_animate_panel_field_specs_keys_match_collect():
    panel = cpp.AnimateParamPanel()
    collect_keys = set(panel.collect())
    spec_keys = {s.key for s in panel.field_specs()}
    assert spec_keys == collect_keys


def test_animate_panel_field_specs_roles():
    specs = {s.key: s for s in cpp.AnimateParamPanel().field_specs()}
    assert specs["num_inference_steps"].role == fr.FieldRole(fr.ROLE_CONTROL, fr.MARK_EXACT)
    assert specs["animate_mode"].role == fr.FieldRole(fr.ROLE_DIRECTION, fr.MARK_EXACT)
    # Path fields get kind="path" and a brief/words role (see task-4 brief:
    # "path fields -> give them kind='path' and a sensible role (brief/words
    # is fine for reference inputs)").
    assert specs["reference_video_path"].kind == "path"
    assert specs["reference_image_path"].kind == "path"
    assert specs["reference_video_path"].role == fr.FieldRole(fr.ROLE_BRIEF, fr.MARK_WORDS)
    assert specs["reference_image_path"].role == fr.FieldRole(fr.ROLE_BRIEF, fr.MARK_WORDS)
    # model has no dedicated dropdown on Animate (single fixed model id) but
    # still gets a "model" kind spec so callers can treat it uniformly.
    assert specs["model"].kind == "model"


# ── ArtgenParamPanel ──────────────────────────────────────────────────────────


def test_artgen_panel_field_specs_use_classifier():
    p = cpp.ArtgenParamPanel("landscape")
    roles = {s.key: s.role for s in p.field_specs()}
    # mountains is a bool -> direction/exact
    assert roles["mountains"] == fr.FieldRole(fr.ROLE_DIRECTION, fr.MARK_EXACT)
    # palette default "random" -> direction/interpreted
    assert roles["palette"].marker == fr.MARK_INTERPRETED


def test_artgen_panel_field_specs_keys_match_introspection():
    """field_specs() keys must match exactly the dests _introspect_generator_args
    resolves for the same generator — the same set collect() would populate
    once the panel is built."""
    p = cpp.ArtgenParamPanel("landscape")
    spec_keys = {s.key for s in p.field_specs()}
    introspected_dests = {s.dest for s in cpp._introspect_generator_args("landscape")}
    assert spec_keys == introspected_dests


def test_artgen_panel_field_specs_carry_tooltip_and_label():
    p = cpp.ArtgenParamPanel("verse")
    specs = {s.key: s for s in p.field_specs()}
    assert specs["count"].label == cpp._humanize_dest("count")
    # tooltip mirrors the argparse help string (may be empty for some args,
    # but the attribute must exist and be a string).
    assert isinstance(specs["count"].tooltip, str)


def test_artgen_panel_field_specs_empty_for_unknown_generator():
    """Fail-soft: an unknown/broken generator yields [] rather than raising —
    mirrors _introspect_generator_args's own fail-soft contract."""
    p = cpp.ArtgenParamPanel("no-such-generator-xyz")
    assert p.field_specs() == []


# ── FieldSpec shape ───────────────────────────────────────────────────────────


def test_field_spec_is_a_dataclass_with_expected_fields():
    spec = cpp.FieldSpec(
        key="k", label="K", kind="int", default=1,
        role=fr.FieldRole(fr.ROLE_CONTROL, fr.MARK_EXACT),
    )
    assert spec.key == "k"
    assert spec.choices is None
    assert spec.tooltip == ""
