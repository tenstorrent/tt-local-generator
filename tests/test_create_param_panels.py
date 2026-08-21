# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for `ArtgenParamPanel`'s boolean/None-default parameter handling
(whole-branch review, Create-surface slice).

Two defects these tests pin:

  F1 — default-True boolean switches must be able to emit their explicit "off"
       spelling. landscape registers `--mountains` (store_true default=True)
       AND `--no-mountains` (store_false, same dest). Turning the switch OFF
       must resolve to `--no-mountains`, not silently omit both flags (which
       let the generator fall back to its default, ignoring the user's choice).

  F2 — a None-default numeric arg must not forward a literal 0. ansi's
       `--width` is `type=int default=None` ("80 for bbs, 40 otherwise"); the
       spin starts at 0 and `collect()` must return None for it (so the seam
       omits the flag), NOT 0 (which would build a 0-column canvas). A concrete
       non-None default (verse's `--count`, default 3) must still forward a 0.

The bool→argv-flag emission itself lives in the run seam
(`MainWindow._create_generate_artgen`) and is covered end-to-end in
tests/test_main_window_create_generate.py; here we test the panel/introspection
seam that feeds it.
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

from create_param_panels import (
    AnimateParamPanel,
    ArtgenParamPanel,
    ImageParamPanel,
    VideoParamPanel,
    _ANIMATEDIFF_DEFAULTS,
    _SEED_MODE_KEYS,
    _VIDEO_MODEL_CHOICES,
    _collect_seed,
    artgen_bool_flags,
)


def _controls_by_dest(panel: ArtgenParamPanel) -> dict:
    """Map dest -> built `_ArgControl` for a freshly built panel."""
    return {c.dest: c for c in panel._controls}


# ── F1: boolean flag-pair spellings ─────────────────────────────────────────


def test_artgen_bool_flags_expose_positive_and_negative_spellings():
    """`artgen_bool_flags` reports BOTH spellings for a classic store_true/
    store_false pair, and None as the negative for a bare store_true."""
    flags = artgen_bool_flags("landscape")

    assert flags["mountains"] == ("--mountains", "--no-mountains")
    assert flags["clouds"] == ("--clouds", "--no-clouds")
    assert flags["stars"] == ("--stars", "--no-stars")
    # `--glitch` is a bare store_true with no negation → no "off" spelling.
    assert flags["glitch"] == ("--glitch", None)


def test_artgen_bool_flags_empty_for_unknown_generator():
    """Fail-soft: an unknown/broken generator yields {} rather than raising."""
    assert artgen_bool_flags("no-such-generator-xyz") == {}


def test_landscape_panel_mountains_switch_off_collects_false():
    """The single rendered switch for the --mountains/--no-mountains PAIR
    defaults ON (store_true default=True); turning it OFF collects False, and
    a default-off flag (--clouds) turned ON collects True."""
    panel = ArtgenParamPanel("landscape")
    panel.build()
    controls = _controls_by_dest(panel)

    # mountains defaults ON (the resolved default of the shared-dest pair)
    assert controls["mountains"].widget.get_active() is True
    assert panel.collect()["mountains"] is True

    # user turns mountains OFF → collect() reports False (the seam then emits
    # the explicit --no-mountains for it)
    controls["mountains"].widget.set_active(False)
    assert panel.collect()["mountains"] is False

    # clouds defaults OFF; turning it ON collects True → seam emits --clouds
    assert controls["clouds"].widget.get_active() is False
    controls["clouds"].widget.set_active(True)
    assert panel.collect()["clouds"] is True


# ── F2: None-default numeric args ────────────────────────────────────────────


def test_ansi_width_none_default_zero_collects_as_none():
    """ansi's --width is `type=int default=None` — its spin starts at 0 and an
    untouched 0 must collect as None (unset), so the seam omits the flag and
    the generator's own auto-default (80 for bbs, 40 otherwise) applies."""
    panel = ArtgenParamPanel("ansi")
    panel.build()
    controls = _controls_by_dest(panel)

    width = controls["width"]
    assert width.none_default is True
    assert width.widget.get_value() == 0  # spin starts at 0 for a None default
    assert panel.collect()["width"] is None


def test_ansi_width_nonzero_forwards_the_value():
    """A real, user-set width is forwarded unchanged (not swallowed as None)."""
    panel = ArtgenParamPanel("ansi")
    panel.build()
    controls = _controls_by_dest(panel)

    controls["width"].widget.set_value(50)
    assert panel.collect()["width"] == 50


def test_concrete_default_int_zero_still_forwards():
    """verse's --count is `type=int default=3` (a CONCRETE default, not None):
    it is NOT a None-default arg, so a value of 0 forwards as the literal 0 —
    only None-default args treat 0 as 'unset'."""
    panel = ArtgenParamPanel("verse")
    panel.build()
    controls = _controls_by_dest(panel)

    count = controls["count"]
    assert count.none_default is False
    count.widget.set_value(0)
    assert panel.collect()["count"] == 0


# ── VideoParamPanel: native AnimateDiff option (SP-3c-2) ─────────────────────
#
# Migrates AnimateDiff v0.9 (the "native" hardware path — DISTINCT from the
# artgen `animatediff` plugin medium, which stays a separate generator invoked
# via `tt-ctl artgen animatediff`) into Create's Video medium. Mirrors
# `ControlPanel._build_animatediff_box()`/`get_animatediff_args()`
# (main_window.py ~5103/~5283) field-for-field — see
# `.superpowers/sdd/task-2-brief.md`.


def _video_model_index(key: str) -> int:
    for idx, (choice_key, _label) in enumerate(_VIDEO_MODEL_CHOICES):
        if choice_key == key:
            return idx
    raise AssertionError(f"{key!r} not in _VIDEO_MODEL_CHOICES")


def test_video_model_choices_include_animatediff():
    keys = [key for key, _label in _VIDEO_MODEL_CHOICES]
    assert "animatediff" in keys


def test_video_panel_collect_always_includes_complete_animatediff_args():
    """Regardless of which model is selected, collect()'s "animatediff_args"
    is a COMPLETE dict — every `_ANIMATEDIFF_DEFAULTS` key present — so a
    caller (main_window._create_generate_native) never needs to special-case
    a missing key before forwarding it to `_on_generate`."""
    panel = VideoParamPanel()
    panel.build()

    args = panel.collect()["animatediff_args"]
    assert set(args) == set(_ANIMATEDIFF_DEFAULTS)


def test_video_panel_collect_before_build_still_has_complete_animatediff_args():
    """collect() must never raise even if called before build() — matches
    every other field's fallback-to-default contract in this panel."""
    panel = VideoParamPanel()
    assert panel.collect()["animatediff_args"] == _ANIMATEDIFF_DEFAULTS


def test_video_panel_animatediff_options_hidden_by_default():
    """"wan2" is the panel's built-in default model — the AnimateDiff options
    row must not be visible until AnimateDiff is actually selected."""
    panel = VideoParamPanel()
    panel.build()

    assert panel._ad_options_row.get_visible() is False


def test_video_panel_animatediff_options_visible_when_selected():
    panel = VideoParamPanel()
    panel.build()

    panel._model_dropdown.set_selected(_video_model_index("animatediff"))

    assert panel._ad_options_row.get_visible() is True


def test_video_panel_animatediff_options_hidden_again_after_switching_away():
    panel = VideoParamPanel()
    panel.build()
    panel._model_dropdown.set_selected(_video_model_index("animatediff"))
    assert panel._ad_options_row.get_visible() is True

    panel._model_dropdown.set_selected(_video_model_index("wan2"))

    assert panel._ad_options_row.get_visible() is False


def test_video_panel_set_selected_model_key_reveals_animatediff_options():
    """The programmatic hook CreateView calls to keep this panel's own
    (otherwise-invisible, see RoleZonePanel's "model" kind skip) model state
    in sync with the scoped dropdown the user actually sees."""
    panel = VideoParamPanel()
    panel.build()

    panel.set_selected_model_key("animatediff")

    assert panel._ad_options_row.get_visible() is True
    assert panel._selected_video_key() == "animatediff"


def test_video_panel_set_selected_model_key_unknown_key_is_a_noop():
    panel = VideoParamPanel()
    panel.build()

    panel.set_selected_model_key("not-a-real-model")

    # Falls back to whatever was already selected (the built-in default).
    assert panel._selected_video_key() == "wan2"


def test_video_panel_animatediff_args_reflect_widget_values():
    """A round trip: set a handful of AnimateDiff-specific widgets, then
    confirm collect()'s "animatediff_args" reflects exactly those values
    (and leaves the rest at their documented defaults)."""
    panel = VideoParamPanel()
    panel.build()

    panel._ad_temporal_alpha.set_value(0.7)
    panel._ad_neg_prompt.set_text("oversaturated")
    panel._ad_chain_save.set_active(True)
    panel._ad_multichip_mode.set_selected(2)  # "Off — single chip"

    args = panel.collect()["animatediff_args"]
    assert args["temporal_alpha"] == 0.7
    assert args["negative_prompt"] == "oversaturated"
    assert args["chain_save"] is True
    # "Off" -> single chip: the derived boolean is False and the mode is "off".
    assert args["multi_chip"] is False
    assert args["multichip_mode"] == "off"
    # Untouched fields keep their defaults.
    assert args["mode"] == "blackhole"
    assert args["lightning"] is False


def _force_multichip_default(monkeypatch, mode):
    """Pin the `animatediff_multichip_default` setting read to `mode` (leaving
    every other settings read intact), so these tests don't depend on the
    machine's real settings.json."""
    import create_param_panels as cpp
    orig = cpp._settings.get
    monkeypatch.setattr(cpp._settings, "get",
                        lambda k: mode if k == "animatediff_multichip_default" else orig(k))


def test_video_panel_multichip_selector_maps_mode_and_bool(monkeypatch):
    """The 3-way Multi-chip selector drives BOTH the engine `multichip_mode`
    string and the legacy `multi_chip` bool (True unless "Off")."""
    _force_multichip_default(monkeypatch, "remix")
    panel = VideoParamPanel()
    panel.build()

    # Default follows the setting (pinned to Remix above — the proven-reliable
    # multi-chip path; Coherent is opt-in, see v0.91.1).
    args = panel.collect()["animatediff_args"]
    assert (args["multichip_mode"], args["multi_chip"]) == ("remix", True)

    panel._ad_multichip_mode.set_selected(1)  # "Coherent — one longer video"
    args = panel.collect()["animatediff_args"]
    assert (args["multichip_mode"], args["multi_chip"]) == ("coherent", True)

    panel._ad_multichip_mode.set_selected(2)  # "Off — single chip"
    args = panel.collect()["animatediff_args"]
    assert (args["multichip_mode"], args["multi_chip"]) == ("off", False)


def test_video_panel_multichip_default_follows_setting(monkeypatch):
    """The panel preselects the persisted `animatediff_multichip_default` — so a
    user can pin Coherent (or Off) without a code edit."""
    _force_multichip_default(monkeypatch, "coherent")
    panel = VideoParamPanel()
    panel.build()
    assert panel.collect()["animatediff_args"]["multichip_mode"] == "coherent"


def test_video_panel_lightning_steps_row_hidden_until_lightning_and_cpu():
    """Mirrors ControlPanel's own `_on_ad_lightning_toggled`: the Distill
    steps row only appears when Lightning is ON *and* mode is "cpu"."""
    panel = VideoParamPanel()
    panel.build()
    assert panel._ad_lightning_steps_row.get_visible() is False

    panel._ad_lightning.set_active(True)
    assert panel._ad_lightning_steps_row.get_visible() is False  # mode still blackhole

    panel._ad_mode.set_selected(_video_model_index_in(["blackhole", "cpu", "sim"], "cpu"))
    assert panel._ad_lightning_steps_row.get_visible() is True

    panel._ad_lightning.set_active(False)
    assert panel._ad_lightning_steps_row.get_visible() is False


def _video_model_index_in(choices, value):
    return choices.index(value)


def test_video_panel_animatediff_args_role_is_control_exact():
    import field_roles as fr

    specs = {s.key: s for s in VideoParamPanel().field_specs()}
    assert specs["animatediff_args"].role == fr.FieldRole(fr.ROLE_CONTROL, fr.MARK_EXACT)
    assert specs["animatediff_args"].kind == "dict"


# ── SeedModeControl: random / repeat-last / keep (SP-3d-2) ──────────────────
#
# Migrates ControlPanel's three-way seed-mode toggle (main_window.py's
# `_seed_random_btn`/`_seed_repeat_btn`/`_seed_keep_btn`, `_on_seed_mode`,
# `_apply_seed_mode_from_settings`) into Create's Controls zone — see
# `.superpowers/sdd/task-2-brief.md` and `create_param_panels.SeedModeControl`'s
# own docstring for the full design rationale. `tests/conftest.py`'s
# `_isolate_app_settings` fixture (added alongside this task) guarantees
# `_settings.get("seed_mode")` starts at the DEFAULTS value ("random") for
# every test here, regardless of what's persisted in the real app's settings
# file on whatever machine runs the suite.

def _select_seed_mode(panel, mode: str) -> None:
    """Drive *panel*'s built `SeedModeControl` dropdown to *mode* — mirrors a
    real user picking an entry (fires the same "notify::selected" handler
    that persists to settings and write-throughs "random")."""
    idx = _SEED_MODE_KEYS.index(mode)
    panel._seed_mode._dropdown.set_selected(idx)


def test_collect_seed_helper_defaults_to_negative_one_with_nothing_built():
    """`_collect_seed(None, None)` — the state before `build()` runs, or for
    a hypothetical panel that never builds a seed field at all — must fall
    back to -1, exactly the pre-existing "no seed widget" contract every
    other `collect()` fallback in this module follows."""
    assert _collect_seed(None, None) == -1


def test_seed_mode_selector_is_built_inside_the_seed_row():
    """The mode selector must live INSIDE the same row `_rows["seed"]` maps
    to, not as a separate field — this is what makes it ride along with the
    "seed" field's existing ROLE_CONTROL classification into RoleZonePanel's
    collapsed Controls zone for free, with no new FieldSpec needed."""
    panel = ImageParamPanel()
    panel.build()

    assert panel._seed_mode is not None
    row = panel._row_for("seed")
    assert row is not None

    # Walk the row's children looking for the SeedModeControl instance.
    found = False
    child = row.get_first_child()
    while child is not None:
        if child is panel._seed_mode:
            found = True
        child = child.get_next_sibling()
    assert found


def test_seed_mode_defaults_to_random_and_collect_is_unchanged():
    """MIGRATION-SAFE default case: a freshly built panel, mode untouched,
    collects seed=-1 — byte-for-byte the same value/type as before this
    control existed."""
    panel = ImageParamPanel()
    panel.build()

    assert panel._seed_mode.mode == "random"
    assert panel.collect()["seed"] == -1
    assert isinstance(panel.collect()["seed"], int)


def test_seed_mode_random_write_throughs_spin_to_negative_one():
    """Selecting "Random" after the spin holds some other value immediately
    resets it to -1 (write-through) — so "random" truly means "a new seed
    every generation" even if the spin previously held a "keep" value."""
    panel = ImageParamPanel()
    panel.build()

    _select_seed_mode(panel, "keep")
    panel._seed_adj.set_value(99)
    assert panel.collect()["seed"] == 99

    _select_seed_mode(panel, "random")

    assert panel._seed_adj.get_value() == -1
    assert panel.collect()["seed"] == -1


def test_seed_mode_keep_uses_the_seed_field_value():
    """"keep"/fixed: the seed spin IS the source of truth — collect() must
    forward exactly what's typed in, unmodified."""
    panel = ImageParamPanel()
    panel.build()

    _select_seed_mode(panel, "keep")
    panel._seed_adj.set_value(555)

    assert panel.collect()["seed"] == 555


def test_seed_mode_repeat_reproduces_the_last_generated_seed():
    """"repeat-last": collect() resolves to the SAME history-store-derived
    value ControlPanel's own "repeat" mode uses — a fresh `HistoryStore()`
    reading the most recent record, not a separate/forked persistence path.
    Overrides whatever the spin itself currently displays."""
    from history_store import GenerationRecord, HistoryStore

    HistoryStore().append(
        GenerationRecord.new(
            job_id="job-1", prompt="a fox", negative_prompt="",
            num_inference_steps=20, seed=42424,
        )
    )

    panel = ImageParamPanel()
    panel.build()
    panel._seed_adj.set_value(1)  # deliberately different — must be overridden

    _select_seed_mode(panel, "repeat")

    assert panel.collect()["seed"] == 42424


def test_seed_mode_repeat_falls_back_to_random_with_no_history():
    """No history yet — "repeat" resolves to -1, the same fallback
    ControlPanel's own `_on_seed_mode`/`_apply_seed_mode_from_settings`
    "repeat" branch uses when there is nothing to repeat."""
    panel = ImageParamPanel()
    panel.build()

    _select_seed_mode(panel, "repeat")

    assert panel.collect()["seed"] == -1


def test_seed_mode_persists_to_the_shared_settings_key():
    """Picking a mode writes to the SAME `seed_mode` settings key
    ControlPanel's own `_on_seed_mode` uses — not a forked "create seed
    mode" key — so the two surfaces always agree on the active mode."""
    from app_settings import settings as _settings

    panel = ImageParamPanel()
    panel.build()

    for mode in ("keep", "repeat", "random"):
        _select_seed_mode(panel, mode)
        assert _settings.get("seed_mode") == mode


def test_seed_mode_control_initialises_from_previously_saved_settings():
    """A panel built AFTER some other surface (or an earlier Create session)
    already saved a mode must start showing that mode, not always "random" —
    this is what lets Create and the legacy ControlPanel agree on the active
    mode across sessions/surfaces."""
    from app_settings import settings as _settings

    _settings.set("seed_mode", "keep")

    panel = ImageParamPanel()
    panel.build()

    assert panel._seed_mode.mode == "keep"


# ── The same control, reused by Video/Animate (not a separate implementation) ─

def test_video_panel_seed_mode_repeat_reproduces_the_last_generated_seed():
    from history_store import GenerationRecord, HistoryStore

    HistoryStore().append(
        GenerationRecord.new(
            job_id="job-2", prompt="a river", negative_prompt="",
            num_inference_steps=20, seed=98765,
        )
    )

    panel = VideoParamPanel()
    panel.build()
    _select_seed_mode(panel, "repeat")

    assert panel.collect()["seed"] == 98765


def test_animate_panel_seed_mode_random_write_throughs_spin_to_negative_one():
    panel = AnimateParamPanel()
    panel.build()

    _select_seed_mode(panel, "keep")
    panel._seed_adj.set_value(7)
    _select_seed_mode(panel, "random")

    assert panel._seed_adj.get_value() == -1
    assert panel.collect()["seed"] == -1
