# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for `ArtgenParamPanel`'s ✨ Inspire wiring (regression fix 2/2,
"tt-local-generator inspire2" — restoring the per-field Inspire button the
OLD (deleted, SP-3d-5) `ArtgenPanel` gave every generator's theme/subject/
prompt-shaped entry).

Coverage:
  - a genuine creative-text field (`_artgen_field_wants_inspire` ==
    kind=="str" AND `field_roles.classify_artgen(...).role == ROLE_BRIEF`)
    gets a ✨ button when the panel is given an `inspire_fn`
  - int/bool/choice fields never get one, regardless of `inspire_fn`
  - structured/enum-like OR path-like "str"-kind fields (circuit's
    inputs/gates/circuit_style, landscape's palette, animatediff's
    negative_prompt/chain_from/chain_save/motion_adapter) do NOT get one —
    only genuinely free-form prose does
  - `inspire_fn=None` (the default) means no ✨ buttons at all, anywhere
  - clicking a field's ✨ button forwards THAT field's current text as the
    seed and fills the result back into the SAME entry (two-mode contract,
    reusing `create_param_panels.attach_inspire_button`)
  - `collect()` (bare panel AND `RoleZonePanel`-wrapped) is byte-for-byte
    identical whether or not `inspire_fn` was supplied — the ✨ button is
    decoration inside the field's row, never part of the value-bearing
    widget `collect()` reads.
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
from create_mediums import Medium


@pytest.fixture(autouse=True)
def _synchronous_idle_add(monkeypatch):
    """Same pattern as tests/test_inspire_button.py: attach_inspire_button's
    result/error callbacks post through GLib.idle_add — run it inline since
    there's no live main loop draining that queue in these tests."""
    monkeypatch.setattr(cpp.GLib, "idle_add", lambda fn, *a: fn(*a))


class _FakeInspire:
    def __init__(self):
        self.calls = []

    def __call__(self, prompt_type, seed_text, on_result, on_error):
        self.calls.append((prompt_type, seed_text, on_result, on_error))


def _row_inspire_button(row: Gtk.Widget) -> "Gtk.Button | None":
    """Walk *row*'s direct children for a `.create-inspire-btn` Gtk.Button,
    or None if there isn't one."""
    child = row.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button) and child.has_css_class("create-inspire-btn"):
            return child
        child = child.get_next_sibling()
    return None


def _medium(generator: str) -> Medium:
    return Medium(id=generator, label=generator.title(), icon="✍", kind="text",
                  source="artgen", generator=generator)


# ── Eligibility: creative text fields get a button ──────────────────────────


def test_verse_theme_gets_inspire_button_when_fn_given():
    panel = cpp.ArtgenParamPanel("verse", inspire_fn=_FakeInspire(), prompt_type_getter=lambda: "video")
    panel.build()
    assert _row_inspire_button(panel._rows["theme"]) is not None


def test_ansi_subject_board_name_tagline_get_inspire_button():
    panel = cpp.ArtgenParamPanel("ansi", inspire_fn=_FakeInspire(), prompt_type_getter=lambda: "image")
    panel.build()
    assert _row_inspire_button(panel._rows["subject"]) is not None
    assert _row_inspire_button(panel._rows["board_name"]) is not None
    assert _row_inspire_button(panel._rows["tagline"]) is not None


def test_freeform_field_gets_inspire_button():
    """freeform's whole-prompt --freeform field (classify_artgen extended for
    this regression fix — see field_roles.py) qualifies."""
    panel = cpp.ArtgenParamPanel("freeform", inspire_fn=_FakeInspire(), prompt_type_getter=lambda: "video")
    panel.build()
    assert _row_inspire_button(panel._rows["freeform"]) is not None


def test_palette_mood_field_gets_inspire_button():
    """palette's --mood is a mood/theme seed (classify_artgen extended) — the
    OLD ArtgenPanel gave its `_pal_mood` entry an Inspire button too."""
    panel = cpp.ArtgenParamPanel("palette", inspire_fn=_FakeInspire(), prompt_type_getter=lambda: "image")
    panel.build()
    assert _row_inspire_button(panel._rows["mood"]) is not None


def test_animatediff_prompt_gets_inspire_button():
    panel = cpp.ArtgenParamPanel("animatediff", inspire_fn=_FakeInspire(), prompt_type_getter=lambda: "video")
    panel.build()
    assert _row_inspire_button(panel._rows["prompt"]) is not None


# ── Eligibility: non-text / structured / path fields never get one ─────────


def test_ansi_int_and_choice_fields_never_get_inspire_button():
    panel = cpp.ArtgenParamPanel("ansi", inspire_fn=_FakeInspire(), prompt_type_getter=lambda: "image")
    panel.build()
    assert _row_inspire_button(panel._rows["width"]) is None       # int, None-default
    assert _row_inspire_button(panel._rows["colors"]) is None      # choice
    assert _row_inspire_button(panel._rows["ansi_style"]) is None  # choice


def test_landscape_bool_fields_never_get_inspire_button():
    panel = cpp.ArtgenParamPanel("landscape", inspire_fn=_FakeInspire(), prompt_type_getter=lambda: "image")
    panel.build()
    assert _row_inspire_button(panel._rows["mountains"]) is None
    assert _row_inspire_button(panel._rows["clouds"]) is None


def test_landscape_palette_str_field_is_not_creative_text_skips_inspire():
    """landscape's --palette (default "random") is a "str"-kind field but
    hands the choice to the generator (field_roles ROLE_DIRECTION/
    MARK_INTERPRETED) rather than being raw creative prose — must NOT get a
    button even though it's technically kind=='str'."""
    panel = cpp.ArtgenParamPanel("landscape", inspire_fn=_FakeInspire(), prompt_type_getter=lambda: "image")
    panel.build()
    assert _row_inspire_button(panel._rows["palette"]) is None


def test_circuit_structured_str_fields_skip_inspire():
    """circuit's --inputs/--gates/--circuit-style are comma-lists / style
    keys, not free-form prose — an Inspire click would overwrite a
    machine-parsed field with a nonsense sentence."""
    panel = cpp.ArtgenParamPanel("circuit", inspire_fn=_FakeInspire(), prompt_type_getter=lambda: "image")
    panel.build()
    assert _row_inspire_button(panel._rows["inputs"]) is None
    assert _row_inspire_button(panel._rows["gates"]) is None
    assert _row_inspire_button(panel._rows["circuit_style"]) is None


def test_animatediff_negative_prompt_field_skips_inspire():
    """animatediff's actual registered generator is the MCP plugin
    (`plugins/animatediff/plugin.py`, a deliberately reduced arg set — see
    that module's own comment on why it, not
    `app/artgen/generators/animatediff.py`'s `add_args`, is what
    `artgen.get("animatediff")` instantiates): prompt/negative_prompt/
    frames/steps/seed/temporal_alpha. `negative_prompt` is excluded the same
    way it is everywhere else — a negation, not a creative prompt."""
    panel = cpp.ArtgenParamPanel("animatediff", inspire_fn=_FakeInspire(), prompt_type_getter=lambda: "video")
    panel.build()
    assert _row_inspire_button(panel._rows["negative_prompt"]) is None


# ── inspire_fn=None: migration-safe, no buttons anywhere ────────────────────


def test_no_inspire_fn_means_no_buttons_at_all():
    panel = cpp.ArtgenParamPanel("verse")  # defaults: inspire_fn=None
    panel.build()
    assert _row_inspire_button(panel._rows["theme"]) is None


def test_no_inspire_fn_is_the_default_for_every_generator_construction():
    """Bare `ArtgenParamPanel(name)` — the pre-existing call shape every
    other test in this suite already uses — must still build with zero ✨
    buttons anywhere, matching CreateView's own migration-safe contract."""
    for name in ("verse", "ansi", "freeform", "palette", "animatediff", "landscape", "circuit"):
        panel = cpp.ArtgenParamPanel(name)
        panel.build()
        for dest, row in panel._rows.items():
            assert _row_inspire_button(row) is None, f"{name}.{dest} grew a button with no inspire_fn"


# ── Click behavior: two-mode seed/fill on the SAME entry ────────────────────


def test_click_forwards_current_text_as_seed_and_fills_same_entry():
    fake = _FakeInspire()
    panel = cpp.ArtgenParamPanel("verse", inspire_fn=fake, prompt_type_getter=lambda: "video")
    panel.build()
    controls = {c.dest: c for c in panel._controls}
    entry = controls["theme"].widget
    entry.set_text("an old theme")

    btn = _row_inspire_button(panel._rows["theme"])
    btn.emit("clicked")

    assert len(fake.calls) == 1
    prompt_type, seed_text, on_result, _on_error = fake.calls[0]
    assert prompt_type == "video"
    assert seed_text == "an old theme"

    on_result("a brand new theme")
    assert entry.get_text() == "a brand new theme"


def test_click_with_empty_entry_passes_empty_seed():
    fake = _FakeInspire()
    panel = cpp.ArtgenParamPanel("freeform", inspire_fn=fake, prompt_type_getter=lambda: "video")
    panel.build()
    # freeform's default is None -> entry starts empty.
    btn = _row_inspire_button(panel._rows["freeform"])
    btn.emit("clicked")

    assert fake.calls[0][1] == ""


def test_prompt_type_getter_is_consulted_at_click_time():
    fake = _FakeInspire()
    state = {"kind": "image"}
    panel = cpp.ArtgenParamPanel("ansi", inspire_fn=fake, prompt_type_getter=lambda: state["kind"])
    panel.build()

    btn = _row_inspire_button(panel._rows["subject"])
    btn.emit("clicked")
    assert fake.calls[0][0] == "image"


def test_default_prompt_type_getter_is_video_when_none_supplied():
    """A caller that supplies `inspire_fn` but no `prompt_type_getter` still
    gets a working button — falls back to a constant "video" getter (matches
    generate_prompt.py's own CLI default)."""
    fake = _FakeInspire()
    panel = cpp.ArtgenParamPanel("verse", inspire_fn=fake)  # no prompt_type_getter
    panel.build()

    btn = _row_inspire_button(panel._rows["theme"])
    btn.emit("clicked")
    assert fake.calls[0][0] == "video"


# ── collect() invariant: byte-for-byte identical with/without inspire_fn ───


def test_collect_identical_with_and_without_inspire_fn_bare_panel():
    plain = cpp.ArtgenParamPanel("landscape")
    plain.build()

    wired = cpp.ArtgenParamPanel(
        "landscape", inspire_fn=_FakeInspire(), prompt_type_getter=lambda: "image",
    )
    wired.build()

    assert plain.collect() == wired.collect()


def test_collect_identical_with_and_without_inspire_fn_role_zone_panel():
    """The hard `RoleZonePanel.collect()` invariant (module docstring in
    create_param_panels.py) must survive the ✨ button's presence — it's a
    decoration appended to the field's row, never the value-bearing widget
    `collect()` reads, and RoleZonePanel re-parents whole rows unchanged."""
    plain = cpp.ArtgenParamPanel("landscape")
    zoned_plain = cpp.RoleZonePanel(plain, _medium("landscape"))

    wired = cpp.ArtgenParamPanel(
        "landscape", inspire_fn=_FakeInspire(), prompt_type_getter=lambda: "image",
    )
    zoned_wired = cpp.RoleZonePanel(wired, _medium("landscape"))

    assert zoned_plain.collect() == zoned_wired.collect()


def test_collect_still_reads_edited_value_when_inspire_button_present():
    """Not just "equal by coincidence" — collect() must still track live user
    edits to the SAME entry the ✨ button is attached to."""
    panel = cpp.ArtgenParamPanel("verse", inspire_fn=_FakeInspire(), prompt_type_getter=lambda: "video")
    panel.build()
    controls = {c.dest: c for c in panel._controls}
    controls["theme"].widget.set_text("edited by hand")

    assert panel.collect()["theme"] == "edited by hand"
