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

from create_param_panels import ArtgenParamPanel, artgen_bool_flags


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
