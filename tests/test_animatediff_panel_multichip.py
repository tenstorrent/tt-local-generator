"""
Tests for the "🎛 Multi-chip" panel (Option A) in artgen_panel.py.

Verifies the Off/Remix/Coherent mode dropdown, per-chip prompt entries,
seed-spread spinner, ramp/stitch dropdowns all get built, and that
_build_args("animatediff") reads them into the args namespace that
_run_animatediff() forwards to run_subprocess().

GUI test — requires a real (or Xvfb) display, per repo convention (see
tests/test_artgen_panel_codeart.py).
"""
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
    Gtk.Entry()
except Exception:  # pragma: no cover
    pytest.skip("no GTK display", allow_module_level=True)

import artgen_panel


def _panel():
    return artgen_panel.ArtgenPanel.__new__(artgen_panel.ArtgenPanel)


def test_multichip_widgets_built_and_args_read():
    p = _panel()
    p._build_controls_page("animatediff")
    # default mode is Off
    assert artgen_panel._dd_val(p._ad_mc_mode) == "Off"
    # Remix reveal box starts hidden (mode defaults to Off)
    assert p._ad_mc_remix_box.get_visible() is False
    # set Remix + per-chip prompt 0 + seed spread + stitch order
    p._set_dd(p._ad_mc_mode, "Remix")
    assert p._ad_mc_remix_box.get_visible() is True
    p._ad_mc_prompt_entries[0].set_text("koi at dawn")
    p._ad_mc_seed_spread.set_value(2)
    p._set_dd(p._ad_mc_stitch, "concatenate")
    args = p._build_args("animatediff")
    assert args.multichip_mode == "remix"
    assert args.per_chip_prompts[0] == "koi at dawn"
    assert args.seed_spread == 2
    assert args.ramp == "none"
    assert args.stitch_order == "concatenate"


def test_multichip_prompt_entries_capped_at_four(monkeypatch):
    """UI caps per-chip prompt rows at 4 even if more chips are detected."""
    import artgen.generators.animatediff as ad

    monkeypatch.setattr(ad, "check_hardware", lambda: (True, "8 chips", 8))
    p = _panel()
    p._build_controls_page("animatediff")
    assert len(p._ad_mc_prompt_entries) == 4
