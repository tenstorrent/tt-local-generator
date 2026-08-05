"""Tests for the optional 'watch the hardware' activity viz (activity_viz.py)
and its wiring into CreateResultPanel.

The pure helpers (`mode_for_medium`, `read_aiclk_intensity`) are testable with
no GTK/display at all. The widget-construction + drive-wiring tests need a
display and are skipped headless (mirrors the rest of the GTK suite)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import activity_viz  # noqa: E402


# ── Pure: medium -> tensix-viz mode ──────────────────────────────────────────

def test_mode_for_medium_none_is_generic_inference():
    assert activity_viz.mode_for_medium(None) == "inference"


def test_mode_for_medium_image_is_diffusion():
    assert activity_viz.mode_for_medium(SimpleNamespace(id="image", source="native")) == "diffusion"


def test_mode_for_medium_video_is_video():
    assert activity_viz.mode_for_medium(SimpleNamespace(id="video", source="native")) == "video"


def test_mode_for_medium_animatediff_is_diffusion():
    # AnimateDiff is a self-contained diffusion GIF generator.
    assert activity_viz.mode_for_medium(SimpleNamespace(id="animatediff", source="artgen")) == "diffusion"


def test_mode_for_medium_llm_artgen_is_thinking():
    # An unrecognised artgen medium (verse/ansi/landscape/…) reads as "thinking".
    assert activity_viz.mode_for_medium(SimpleNamespace(id="verse", source="artgen")) == "thinking"


def test_mode_for_medium_unknown_native_falls_back_to_inference():
    assert activity_viz.mode_for_medium(SimpleNamespace(id="mystery", source="native")) == "inference"


# ── Pure: sysfs AICLK -> normalised (dram_bw, l1_fill) ───────────────────────

def test_read_aiclk_intensity_none_when_no_chip_dirs(monkeypatch, tmp_path):
    # Point at an empty dir: no tenstorrent!* entries -> None (leave preset).
    monkeypatch.setattr(activity_viz, "_SYSFS", tmp_path)
    assert activity_viz.read_aiclk_intensity() is None


def test_read_aiclk_intensity_normalises_peak(monkeypatch, tmp_path):
    # Two fake chips; peak 700 MHz against the 1400 ceiling -> 0.5 dram, 0.4 l1.
    for mhz in (350, 700):
        d = tmp_path / f"tenstorrent!{mhz}"
        d.mkdir()
        (d / "tt_aiclk").write_text(str(mhz))
    monkeypatch.setattr(activity_viz, "_SYSFS", tmp_path)
    dram, l1 = activity_viz.read_aiclk_intensity()
    assert dram == pytest.approx(0.5, abs=1e-3)
    assert l1 == pytest.approx(0.4, abs=1e-3)


def test_read_aiclk_intensity_clamps_to_one(monkeypatch, tmp_path):
    d = tmp_path / "tenstorrent!x"
    d.mkdir()
    (d / "tt_aiclk").write_text("999999")  # absurdly high -> clamped to 1.0
    monkeypatch.setattr(activity_viz, "_SYSFS", tmp_path)
    dram, l1 = activity_viz.read_aiclk_intensity()
    assert dram == 1.0 and l1 == pytest.approx(0.8, abs=1e-3)


def test_read_aiclk_intensity_skips_unreadable_chip(monkeypatch, tmp_path):
    # One chip has a garbage clock file; it's skipped, the good one wins.
    bad = tmp_path / "tenstorrent!bad"
    bad.mkdir()
    (bad / "tt_aiclk").write_text("not-a-number")
    good = tmp_path / "tenstorrent!good"
    good.mkdir()
    (good / "tt_aiclk").write_text("700")
    monkeypatch.setattr(activity_viz, "_SYSFS", tmp_path)
    dram, _ = activity_viz.read_aiclk_intensity()
    assert dram == pytest.approx(0.5, abs=1e-3)


# ── Pure: honest chip count + per-chip clocks + layout ───────────────────────

def _make_chips(tmp_path, clocks):
    """Create fake chip dirs with the given per-chip clock strings (None -> a
    dir with a missing/garbage clock file), sorted-name aligned to index."""
    for i, mhz in enumerate(clocks):
        d = tmp_path / f"tenstorrent!{i}"
        d.mkdir()
        if mhz is not None:
            (d / "tt_aiclk").write_text(str(mhz))
    return tmp_path


def test_chip_count_reflects_sysfs(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_viz, "_SYSFS", _make_chips(tmp_path, [800, 810, 790, 805]))
    assert activity_viz.chip_count() == 4


def test_chip_count_zero_when_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_viz, "_SYSFS", tmp_path)
    assert activity_viz.chip_count() == 0


def test_read_chip_clocks_is_position_aligned(monkeypatch, tmp_path):
    # Middle chip unreadable -> None at its index, others intact.
    monkeypatch.setattr(activity_viz, "_SYSFS", _make_chips(tmp_path, [800, None, 790]))
    assert activity_viz.read_chip_clocks() == [800, None, 790]


def test_peak_ignores_unreadable(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_viz, "_SYSFS", _make_chips(tmp_path, [800, None, 900]))
    assert activity_viz.read_aiclk_peak_mhz() == 900


def test_grid_layout_one_vs_many():
    assert activity_viz.grid_layout(1)[0] == 1      # single column
    assert activity_viz.grid_layout(2)[0] == 2      # 2-wide
    assert activity_viz.grid_layout(4)[0] == 2      # 2x2


# ── Pure: power telemetry (the responsive signal) ────────────────────────────

def test_parse_powers_from_snapshot():
    snap = {"device_info": [
        {"telemetry": {"power": "61.0"}},
        {"telemetry": {"power": " 53.0"}},
        {"telemetry": {"power": None}},   # non-numeric -> None
        {"telemetry": {}},                 # missing -> None
    ]}
    assert activity_viz.parse_powers(snap) == [61.0, 53.0, None, None]


def test_parse_powers_empty_when_no_devices():
    assert activity_viz.parse_powers({}) == []


def test_power_activity_floor_ceiling_and_curve():
    assert activity_viz.power_activity(activity_viz._POWER_FLOOR_W) == 0.0
    assert activity_viz.power_activity(activity_viz._POWER_CEILING_W) == 1.0
    assert activity_viz.power_activity(5.0) == 0.0        # below floor clamps
    # The perceptual curve (exp<1) lifts a mid load ABOVE its linear fraction.
    mid = (activity_viz._POWER_FLOOR_W + activity_viz._POWER_CEILING_W) / 2
    assert activity_viz.power_activity(mid) > 0.5
    # Monotonic.
    assert activity_viz.power_activity(40.0) < activity_viz.power_activity(80.0)


def test_shape_flow_active_has_floor_and_writeback():
    # A running job always shows clearly visible bidirectional flow...
    dram, l1, wb = activity_viz.shape_flow(0.0, active=True)
    assert dram >= 0.35 and wb >= 0.15
    # ...that intensifies with load (and stays clamped at full).
    dram2, l1_2, wb2 = activity_viz.shape_flow(1.0, active=True)
    assert dram2 == 1.0 and dram2 > dram and wb2 > wb


def test_shape_flow_idle_is_quiet():
    dram, l1, wb = activity_viz.shape_flow(0.0, active=False)
    assert dram < 0.1 and wb == 0.0


def test_sample_telemetry_prefers_power(monkeypatch):
    monkeypatch.setattr(activity_viz, "read_chip_power_watts", lambda: [85.0, 85.0])
    readout, acts = activity_viz.sample_telemetry(2, 2)
    assert readout == "85 W"                      # watts, not MHz
    assert acts[0] == pytest.approx(activity_viz.power_activity(85.0), abs=1e-6)
    assert 0.0 < acts[0] <= 1.0


def test_sample_telemetry_falls_back_to_aiclk(monkeypatch, tmp_path):
    # No power (tt-smi absent) -> AICLK path, reported in MHz, idle-relative.
    monkeypatch.setattr(activity_viz, "read_chip_power_watts", lambda: [])
    monkeypatch.setattr(activity_viz, "_SYSFS", _make_chips(tmp_path, [1350, 1350]))
    readout, acts = activity_viz.sample_telemetry(2, 2)
    assert readout == "1350 MHz"
    assert acts[0] == pytest.approx(1.0, abs=1e-6)     # boosted -> full activity


def test_sample_telemetry_readout_shows_shown_over_total_when_capped(monkeypatch):
    monkeypatch.setattr(activity_viz, "read_chip_power_watts", lambda: [40.0] * 8)
    readout, acts = activity_viz.sample_telemetry(4, 8)  # cap 4 of 8
    assert readout.endswith("· 4/8")
    assert len(acts) == 4


# ── Widget + CreateResultPanel drive wiring (needs a display) ────────────────

def _gtk_or_skip():
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        if not Gtk.init_check():
            pytest.skip("no display")
        return Gtk
    except Exception:
        pytest.skip("GTK unavailable")


class _FakeViz:
    """Records set_mode/set_running/visibility without touching WebKit — a
    stand-in for ActivityVizWidget so the drive-wiring tests don't need a
    browser."""
    def __init__(self):
        self.calls = []
        self.visible = None
        self.running = None

    def set_visible(self, v):
        self.visible = v

    def set_running(self, r):
        self.running = r
        self.calls.append(("running", r))

    def set_mode(self, medium=None):
        self.calls.append(("mode", getattr(medium, "id", None)))


def test_result_panel_construction_is_webkit_free():
    # Regression guard: CreateResultPanel must NOT build the (heavy) WebKit viz
    # in __init__ — it's built lazily by CreateView only when the user toggles
    # Watch on. `_activity_viz` starts None, and the drive methods must be
    # harmless no-ops until a viz is injected.
    _gtk_or_skip()
    from create_view import CreateResultPanel
    panel = CreateResultPanel()
    assert panel._activity_viz is None
    panel.set_activity_visible(True)   # no viz yet -> just sets the flag
    panel.set_activity_visible(False)
    assert panel._activity_viz is None


def test_show_pending_animates_only_when_revealed():
    _gtk_or_skip()
    from create_view import CreateResultPanel
    panel = CreateResultPanel()
    fake = _FakeViz()
    panel._activity_viz = fake

    medium = SimpleNamespace(id="image", source="native", icon="", label="Image")

    # Hidden: pending must NOT drive the viz.
    panel.show_pending("a cat", medium)
    assert fake.calls == []

    # Reveal while pending -> telemetry starts AND it animates the active medium.
    panel.set_activity_visible(True)
    assert ("running", True) in fake.calls
    assert ("mode", "image") in fake.calls


def test_reveal_then_pending_animates():
    _gtk_or_skip()
    from create_view import CreateResultPanel
    panel = CreateResultPanel()
    fake = _FakeViz()
    panel._activity_viz = fake
    panel.set_activity_visible(True)      # revealed, idle so far (mode None)
    assert ("running", True) in fake.calls
    fake.calls.clear()

    medium = SimpleNamespace(id="video", source="native", icon="", label="Video")
    panel.show_pending("a dog", medium)
    assert fake.calls == [("mode", "video")]  # mode only — telemetry already on


def test_finish_calms_animation_but_keeps_telemetry():
    _gtk_or_skip()
    from create_view import CreateResultPanel
    panel = CreateResultPanel()
    fake = _FakeViz()
    panel._activity_viz = fake
    panel.set_activity_visible(True)
    fake.calls.clear()

    # A record the panel can render without files: minimal duck type.
    rec = SimpleNamespace(prompt="p", thumbnail_path=None, media_file_path=None,
                          media_type="image")
    panel.show_finished(rec)
    # Animation calms to idle, but telemetry is NOT stopped (clock keeps ticking).
    assert ("mode", None) in fake.calls
    assert ("running", False) not in fake.calls


def test_cycle_mode_advances_and_wraps(monkeypatch):
    _gtk_or_skip()
    import activity_viz
    # Force the WebKit-less stub so the widget builds its header (mode label)
    # without spinning up a WebView — cycle_mode only touches the label + eval
    # (which no-ops without a webview). Keeps the test off WebKit entirely.
    monkeypatch.setattr(activity_viz, "_WEBKIT_OK", False)
    from activity_viz import ActivityVizWidget, _CYCLE_MODES
    w = ActivityVizWidget()
    w._mode = _CYCLE_MODES[0]
    w.cycle_mode()
    assert w._mode == _CYCLE_MODES[1]
    # Jump to the last and confirm it wraps back to the first.
    w._mode = _CYCLE_MODES[-1]
    w.cycle_mode()
    assert w._mode == _CYCLE_MODES[0]
    # Header caption reflects the current mode.
    assert w._mode_lbl.get_label().startswith("◉")


def test_hide_stops_telemetry_and_calms():
    _gtk_or_skip()
    from create_view import CreateResultPanel
    panel = CreateResultPanel()
    fake = _FakeViz()
    panel._activity_viz = fake
    panel.set_activity_visible(True)
    fake.calls.clear()
    panel.set_activity_visible(False)
    assert ("running", False) in fake.calls
    assert ("mode", None) in fake.calls
    assert fake.visible is False
