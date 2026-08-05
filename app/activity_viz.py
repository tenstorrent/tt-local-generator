# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""activity_viz.py — an OPTIONAL "watch the hardware" widget for the Create
surface.

Embeds the self-contained `tensix-viz` Canvas animation (bundled under
`app/assets/tensix-viz/`, zero external deps) in a `WebKit.WebView` and drives
it from generation state:

  * `set_mode(medium)` → animate the tensix-viz mode that matches the medium
    being generated (diffusion / video / thinking / …); idle when None.
  * `set_running(bool)` → start/stop a 1 s live-telemetry tap.

**Honest chip count.** The widget draws ONE tensix-viz per REAL chip detected
under `/sys/class/tenstorrent/` (capped at `_CHIP_CAP` so a big system stays a
legible corner instrument), and feeds EACH chip its OWN AICLK from that chip's
`tt_aiclk` into `viz.setMemoryStats({dram_bw, l1_fill})` — so on a 4-chip QB2 you
see four chips, each pulsing with its own real clock. The header shows the peak
clock (MHz) and, when the display is capped, "N/total".

Fully optional and fail-soft: hidden by default; a build without WebKit, or a
box with no Tenstorrent chips, degrades to an inert stub / one idle chip and
never breaks the Create surface. Rendering needs no hardware; the telemetry tap
just no-ops when sysfs has nothing to read.
"""
from __future__ import annotations

import json
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

try:
    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit  # noqa: E402
    _WEBKIT_OK = True
except Exception:  # pragma: no cover - environment-dependent
    _WEBKIT_OK = False

_ASSETS = Path(__file__).resolve().parent / "assets" / "tensix-viz"
_SYSFS = Path("/sys/class/tenstorrent")

# Nominal Blackhole AICLK ceiling (MHz) used to normalise the telemetry tap to
# 0..1. Not exact — it only scales the visual "heat", so a rough peak is fine.
_AICLK_CEILING_MHZ = 1400.0

# Most chips the corner instrument will draw. Above this we show _CHIP_CAP and
# label the header "N/total" so it stays honest without becoming unreadable.
_CHIP_CAP = 4

# Which tensix-viz animation MODE best evokes each Create medium.
# (tensix-viz modes: idle/inference/prefill/thinking/agents/diffusion/video/
#  batch/explore/kernel_dispatch.)
_MODE_BY_MEDIUM_ID = {
    "image": "diffusion",
    "video": "video",
    "animate": "video",
    "animatediff": "diffusion",
}

# Human-readable mode captions for the header (keys are tensix-viz modes).
_MODE_CAPTION = {
    "idle": "idle",
    "inference": "inference",
    "diffusion": "diffusion",
    "video": "video",
    "thinking": "thinking",
}


def mode_for_medium(medium) -> str:
    """Pick the tensix-viz mode for *medium* (a create_mediums.Medium or None).
    Pure — unit-testable without GTK."""
    if medium is None:
        return "inference"
    mid = getattr(medium, "id", None)
    if mid in _MODE_BY_MEDIUM_ID:
        return _MODE_BY_MEDIUM_ID[mid]
    # LLM-backed artgen (verse/ansi/landscape/…) reads as "thinking"; any other
    # artgen kind falls back to the generic inference pulse.
    if getattr(medium, "source", "") == "artgen":
        return "thinking"
    return "inference"


# ── sysfs AICLK telemetry (pure, no GTK) ─────────────────────────────────────

def _chip_dirs() -> "list[Path]":
    """Every Tenstorrent chip's sysfs dir, sorted (stable device order)."""
    try:
        return sorted(_SYSFS.glob("tenstorrent!*"))
    except OSError:
        return []


def chip_count() -> int:
    """Number of Tenstorrent chips present. 0 when none / no permission."""
    return len(_chip_dirs())


def read_chip_clocks() -> "list[int | None]":
    """Per-chip AICLK (MHz), position-aligned with `_chip_dirs()` — None for a
    chip whose clock can't be read (so index i always maps to chip i). Instant,
    no subprocess."""
    out: "list[int | None]" = []
    for chip_dir in _chip_dirs():
        try:
            out.append(int((chip_dir / "tt_aiclk").read_text().strip()))
        except (OSError, ValueError):
            out.append(None)
    return out


def read_aiclk_peak_mhz() -> "int | None":
    """Peak AICLK across all chips (MHz), or None when none can be read — the
    number shown in the viz header readout."""
    present = [c for c in read_chip_clocks() if c is not None]
    return max(present) if present else None


def _intensity_for(mhz: int) -> "tuple[float, float]":
    """One chip's clock -> (dram_bw, l1_fill) in 0..1 for setMemoryStats."""
    intensity = max(0.0, min(1.0, mhz / _AICLK_CEILING_MHZ))
    return (intensity, intensity * 0.8)


def read_aiclk_intensity() -> "tuple[float, float] | None":
    """Fleet-wide (dram_bw, l1_fill) from the peak clock, or None when nothing
    can be read. Kept for callers that want a single aggregate value."""
    peak = read_aiclk_peak_mhz()
    return _intensity_for(peak) if peak is not None else None


# ── Layout: how to arrange N chip canvases in the corner instrument ──────────

_GAP = 4  # px between chip canvases


def grid_layout(display_count: int) -> "tuple[int, int, int]":
    """(cols, canvas_w, canvas_h) for `display_count` chips. One chip gets a
    single larger canvas; two-or-more tile in a 2-wide grid of smaller ones.
    Pure — unit-testable."""
    if display_count <= 1:
        return (1, 224, 150)
    return (2, 150, 108)


class ActivityVizWidget(Gtk.Box):
    """Corner instrument: a header (mode + live peak-AICLK + ✕ dismiss) above a
    grid of one tensix-viz chip per REAL detected chip.

    Two independent controls: `set_mode(medium)` (animation) and
    `set_running(bool)` (telemetry tap). Corner-pinned by construction (fixed
    size + NO expand) so a Gtk.Overlay host honours halign/valign. A WebKit-less
    build gets an inert stub. `on_close` (a plain callable, default None) fires
    from the ✕ button."""

    def __init__(self, arch: str = "blackhole") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("activity-viz")
        # Corner-pin discipline: a fixed footprint that does NOT expand, so a
        # Gtk.Overlay host honours halign/valign (an expanding child gets
        # stretched to fill the pane instead of pinned to the corner — the MVP
        # bug). Clip to the rounded border so the header's own background can't
        # square off the top corners.
        self.set_hexpand(False)
        self.set_vexpand(False)
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.END)
        self.set_overflow(Gtk.Overflow.HIDDEN)

        self._arch = arch
        self._webview = None
        self._pending_js: "list[str]" = []
        self._telemetry_timer: "int | None" = None
        self._mode = "idle"
        self.on_close = None  # set by host -> untoggle Watch

        # Honest chip count: draw one chip per real device (>=1 so the widget
        # never renders empty), capped so a big system stays legible.
        self._chip_actual = chip_count()
        self._chip_display = min(max(self._chip_actual, 1), _CHIP_CAP)
        self._cols, self._cw, self._ch = grid_layout(self._chip_display)
        width = self._cols * self._cw + (self._cols - 1) * _GAP
        self.set_size_request(width, -1)

        # ── Header: mode caption (left) + live MHz readout + ✕ (right) ────────
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("activity-viz-header")
        self._mode_lbl = Gtk.Label(label="◉ idle")  # ◉ + mode
        self._mode_lbl.add_css_class("activity-viz-title")
        self._mode_lbl.set_xalign(0.0)
        self._mode_lbl.set_hexpand(True)
        header.append(self._mode_lbl)
        self._readout_lbl = Gtk.Label(label="")
        self._readout_lbl.add_css_class("activity-viz-readout")
        header.append(self._readout_lbl)
        close_btn = Gtk.Button(label="✕")  # ✕
        close_btn.add_css_class("activity-viz-close")
        close_btn.set_tooltip_text("Hide the activity view")
        close_btn.connect("clicked", self._on_close_clicked)
        header.append(close_btn)
        self.append(header)

        if not _WEBKIT_OK:
            self._webview = None  # inert stub — header still ticks the readout
            return

        self._webview = WebKit.WebView()
        try:
            self._webview.get_settings().set_enable_javascript(True)
        except Exception:
            pass
        # NO expand (see corner-pin note): a fixed canvas grid keeps the
        # footprint bounded so the overlay can pin us to the corner.
        self._webview.set_hexpand(False)
        self._webview.set_vexpand(False)
        rows = (self._chip_display + self._cols - 1) // self._cols
        canvas_h = rows * self._ch + (rows - 1) * _GAP
        self._webview.set_size_request(width, canvas_h)
        # Same realize-deferral as artgen_detail: load_html()/evaluate_javascript
        # before the WebView is realized is a silent no-op.
        self._webview.connect("realize", self._on_realize)
        self._webview.connect("unrealize", lambda *_a: self._stop_telemetry())
        try:
            self._webview.load_html(self._page_html(), "about:blank")
        except Exception:
            pass
        self.append(self._webview)

    def _on_close_clicked(self, _btn) -> None:
        if callable(self.on_close):
            try:
                self.on_close()
            except Exception:
                pass

    # ── HTML page (tensix-viz inlined; N chips + a per-chip facade) ──────────
    def _page_html(self) -> str:
        try:
            js = (_ASSETS / "tensix-viz.js").read_text()
            css = (_ASSETS / "tensix-viz.css").read_text()
        except OSError:
            js = css = ""
        # A small facade on window.__viz fans activate()/setMemoryStats() out to
        # every chip and adds setChipStats(i, s) for per-chip telemetry. Built
        # here (not via CardViz/SystemViz) because those hide their inner
        # TensixViz instances — we need per-chip setMemoryStats access.
        init = (
            "(function(){var host=document.getElementById('chips');"
            "window.__vizChips=[];"
            "for(var i=0;i<" + str(self._chip_display) + ";i++){"
            "var c=document.createElement('canvas');"
            "c.width=" + str(self._cw) + ";c.height=" + str(self._ch) + ";"
            "c.className='tv-chip-canvas';host.appendChild(c);"
            "try{window.__vizChips.push(new window.TensixViz(c,{arch:"
            + json.dumps(self._arch) + ",showMemory:true}));}catch(e){}}"
            "window.__viz={"
            "activate:function(m){window.__vizChips.forEach(function(v,i){"
            "setTimeout(function(){try{v.activate(m);}catch(e){}},i*100);});},"
            "setChipStats:function(i,s){var v=window.__vizChips[i];"
            "if(v){try{v.setMemoryStats(s);}catch(e){}}},"
            "setMemoryStats:function(s){window.__vizChips.forEach(function(v){"
            "try{v.setMemoryStats(s);}catch(e){}});}};"
            "try{window.__viz.activate('idle');}catch(e){}})();"
        )
        grid_css = (
            "#chips{display:grid;grid-template-columns:repeat("
            + str(self._cols) + "," + str(self._cw) + "px);gap:" + str(_GAP)
            + "px;justify-content:center;}"
        )
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<style>html,body{margin:0;padding:0;background:#0F2A35;overflow:hidden}"
            "canvas{display:block}" + grid_css + css + "</style></head><body>"
            "<div id='chips'></div>"
            "<script>" + js + "</script>"
            "<script>" + init + "</script>"
            "</body></html>"
        )

    def _on_realize(self, _w) -> None:
        for js in self._pending_js:
            self._eval_now(js)
        self._pending_js = []

    def _eval_now(self, js: str) -> None:
        try:
            # WebKit 6.0: evaluate_javascript(script, length, world, source_uri,
            # cancellable, callback, user_data) — fire-and-forget with None cb.
            self._webview.evaluate_javascript(js, -1, None, None, None, None, None)
        except Exception:
            pass  # fail-soft: the viz just doesn't update this tick

    def _eval(self, js: str) -> None:
        if self._webview is None:
            return
        if self._webview.get_realized():
            self._eval_now(js)
        else:
            # Queue until realized (load_html/evaluate before realize is a
            # no-op). Bound the backlog: a viz that's running while still
            # unrealized would otherwise accumulate telemetry calls forever.
            # 32 keeps the mode-activate call (queued first) plus recent
            # telemetry, dropping only stale telemetry.
            self._pending_js.append(js)
            if len(self._pending_js) > 32:
                del self._pending_js[1:-16]

    # ── Public API ───────────────────────────────────────────────────────────
    def set_mode(self, medium=None) -> None:
        """Animate the tensix-viz mode matching *medium* (idle when None) and
        update the header caption. Independent of the telemetry tap."""
        mode = mode_for_medium(medium) if medium is not None else "idle"
        self._mode = mode
        self._eval("window.__viz&&window.__viz.activate(" + json.dumps(mode) + ")")
        caption = _MODE_CAPTION.get(mode, mode)
        self._mode_lbl.set_label("◉ " + caption)  # ◉ + mode

    def set_running(self, running: bool) -> None:
        """Start/stop the 1 s live-telemetry tap. Kept separate from `set_mode`
        so the header's live clock ticks the whole time Watch is shown, even
        between jobs (idle animation but real, moving AICLK)."""
        if running:
            self._start_telemetry()
        else:
            self._stop_telemetry()

    # Back-compat aliases (older callers / tests): active == mode + running.
    def set_active(self, medium=None) -> None:
        self.set_mode(medium)
        self.set_running(True)

    def set_idle(self) -> None:
        self.set_mode(None)
        self.set_running(False)

    # ── Live telemetry tap ───────────────────────────────────────────────────
    def _start_telemetry(self) -> None:
        if self._telemetry_timer is not None:
            return
        self._tick_telemetry()  # immediate first sample
        self._telemetry_timer = GLib.timeout_add(1000, self._tick_telemetry)

    def _stop_telemetry(self) -> None:
        if self._telemetry_timer is not None:
            GLib.source_remove(self._telemetry_timer)
            self._telemetry_timer = None
        # Blank the readout so a stale MHz number doesn't linger after stop.
        if getattr(self, "_readout_lbl", None) is not None:
            self._readout_lbl.set_label("")

    def _tick_telemetry(self) -> bool:
        clocks = read_chip_clocks()
        present = [c for c in clocks if c is not None]
        peak = max(present) if present else None

        # Header readout: real peak clock, plus "shown/total" when capped.
        if self._readout_lbl is not None:
            if peak is None:
                self._readout_lbl.set_label("—")  # em dash
            elif self._chip_display < self._chip_actual:
                self._readout_lbl.set_label(
                    "%d MHz · %d/%d" % (peak, self._chip_display, self._chip_actual)
                )
            else:
                self._readout_lbl.set_label("%d MHz" % peak)

        # Per-chip: each drawn chip pulses with ITS OWN clock (honest).
        for i in range(self._chip_display):
            mhz = clocks[i] if i < len(clocks) else None
            if mhz is not None:
                dram, l1 = _intensity_for(mhz)
                self._eval(
                    "window.__viz&&window.__viz.setChipStats(%d,{dram_bw:%.3f,l1_fill:%.3f})"
                    % (i, dram, l1)
                )
        return True  # keep polling until _stop_telemetry
