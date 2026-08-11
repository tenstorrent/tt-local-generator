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
import subprocess
import threading
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

# PRIMARY signal: per-chip POWER draw (W). Power tracks real utilisation
# continuously (idle ~15-20 W → 150 W+ under diffusion), unlike AICLK which is
# effectively binary on Blackhole (~800 idle / 1350 boosted) and often sits
# pinned at 1350 even at rest — so AICLK barely moves during a job. Floor/ceiling
# only scale the visual "heat". A modest ceiling (real diffusion load saturates
# it) + a perceptual curve keep the data-flow lively rather than washed-out.
_POWER_FLOOR_W = 15.0
_POWER_CEILING_W = 110.0
_POWER_CURVE = 0.6          # <1 boosts low/mid loads so flow reads clearly

# Fallback signal (no tt-smi): AICLK, normalised IDLE-relative (800→1350) so the
# fallback still swings 0..1 instead of sitting at a constant mid-value.
_AICLK_IDLE_MHZ = 800.0
_AICLK_BOOST_MHZ = 1350.0
_AICLK_CEILING_MHZ = 1400.0  # (kept for read_aiclk_intensity back-compat)

# How often the background telemetry thread samples (seconds). A tt-smi snapshot
# is ~0.3 s, so this is a light passive read — same cadence class as tt-toplike.
_TELEMETRY_INTERVAL_S = 1.5

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
    "prefill": "prefill",
    "thinking": "thinking",
    "agents": "agents",
    "diffusion": "diffusion",
    "video": "video",
    "batch": "batch",
    "explore": "explore",
    "kernel_dispatch": "kernel dispatch",
}

# Order the header cycles through when you click the title (all tensix-viz
# modes) — a fun manual override; the auto-driver reasserts on the next job.
_CYCLE_MODES = [
    "idle", "inference", "prefill", "thinking", "agents",
    "diffusion", "video", "batch", "explore", "kernel_dispatch",
]


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


# ── Power telemetry (the responsive signal; via tt-smi snapshot) ─────────────

def parse_powers(snapshot: dict) -> "list[float | None]":
    """Extract per-chip power (W) from a `tt-smi -s` snapshot dict, device
    order preserved (None for a chip with no numeric power). Pure —
    unit-testable without hardware."""
    out: "list[float | None]" = []
    for dev in snapshot.get("device_info", []) or []:
        tel = dev.get("telemetry", {}) if isinstance(dev, dict) else {}
        try:
            out.append(float(tel.get("power")))
        except (TypeError, ValueError):
            out.append(None)
    return out


def read_chip_power_watts() -> "list[float | None]":
    """Per-chip power (W) from a `tt-smi` snapshot. Runs a subprocess (~0.3 s)
    — MUST be called off the GTK main thread. Returns [] on any failure (no
    tt-smi, timeout, bad JSON) so the caller can fall back to AICLK."""
    try:
        proc = subprocess.run(
            ["tt-smi", "-s", "--snapshot_no_tty"],
            capture_output=True, text=True, timeout=8,
        )
        return parse_powers(json.loads(proc.stdout))
    except Exception:
        return []


def power_activity(watts: float) -> float:
    """One chip's power draw -> an 'activity' scalar in 0..1 (floor..ceiling,
    perceptually curved so mid loads read as clearly busy). Pure."""
    span = _POWER_CEILING_W - _POWER_FLOOR_W
    frac = (watts - _POWER_FLOOR_W) / span if span > 0 else 0.0
    return max(0.0, min(1.0, frac)) ** _POWER_CURVE


def _clock_activity(mhz: int) -> float:
    """AICLK -> activity 0..1, idle-relative (800→1350) so the fallback swings."""
    span = _AICLK_BOOST_MHZ - _AICLK_IDLE_MHZ
    frac = (mhz - _AICLK_IDLE_MHZ) / span if span > 0 else 0.0
    return max(0.0, min(1.0, frac))


def shape_flow(activity: float, active: bool) -> "tuple[float, float, float]":
    """Map an activity scalar (0..1) to tensix-viz memory params
    `(dram_bw, l1_fill, writeback)` — the read-particle density, L1 fill, and
    return-particle density. When *active* (a job's mode is showing) a floor
    guarantees clearly visible BIDIRECTIONAL flow that then intensifies with
    real load; idle tracks activity but stays quiet. Pure — unit-testable."""
    if active:
        dram, l1, wb = 0.35 + 0.65 * activity, 0.30 + 0.60 * activity, 0.15 + 0.35 * activity
    else:
        dram, l1, wb = 0.05 + 0.45 * activity, 0.10 + 0.30 * activity, 0.10 * activity
    clamp = lambda x: max(0.0, min(1.0, x))  # noqa: E731
    return (round(clamp(dram), 3), round(clamp(l1), 3), round(clamp(wb), 3))


def _readout_text(head: "str | None", display: int, actual: int) -> str:
    """Header readout string: the headline value plus 'shown/total' when the
    display is capped below the real chip count. Em dash when there's nothing."""
    if head is None:
        return "—"  # em dash
    return "%s · %d/%d" % (head, display, actual) if display < actual else head


def sample_telemetry(display: int, actual: int) -> "tuple[str, list]":
    """Sample the hardware and return `(readout_text, per_chip_activity)` where
    each entry is an activity scalar 0..1 (or None for an unreadable chip).
    Prefers per-chip POWER (graded, tracks real load); falls back to AICLK when
    tt-smi isn't available. Pure w.r.t. GTK (does subprocess/sysfs I/O only) —
    call from the background thread. The caller maps activity → flow params via
    `shape_flow` (it needs the current animation mode, which lives on the UI)."""
    powers = read_chip_power_watts()
    if powers:
        acts = [
            power_activity(powers[i]) if i < len(powers) and powers[i] is not None
            else None
            for i in range(display)
        ]
        present = [w for w in powers if w is not None]
        head = ("%d W" % round(max(present))) if present else None
        return _readout_text(head, display, actual), acts

    # Fallback: sysfs AICLK (instant, but a coarse/near-binary signal).
    clocks = read_chip_clocks()
    acts = [
        _clock_activity(clocks[i]) if i < len(clocks) and clocks[i] is not None
        else None
        for i in range(display)
    ]
    present = [c for c in clocks if c is not None]
    head = ("%d MHz" % max(present)) if present else None
    return _readout_text(head, display, actual), acts


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
        self._tel_thread: "threading.Thread | None" = None
        self._tel_stop: "threading.Event | None" = None
        self._tel_running = False
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
        self._mode_lbl.set_tooltip_text("Click to change animation mode")
        # Click the title to cycle animation modes — a fun manual override. On
        # the label (which fills most of the header) so it never conflicts with
        # the ✕ button's own click.
        _mode_click = Gtk.GestureClick()
        _mode_click.connect("pressed", lambda *_a: self.cycle_mode())
        self._mode_lbl.add_controller(_mode_click)
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

        # Stop the telemetry daemon thread when THIS widget is torn down —
        # connected unconditionally (on the Box itself, not just the WebView)
        # so the no-WebKit stub path below is covered too. Without this, a
        # no-WebKit build that ever called set_running(True) would leak the
        # 1.5s tt-smi-spawning daemon thread forever after teardown (review I4).
        # _stop_telemetry is idempotent, so the WebView's own unrealize hook
        # (WebKit path) firing as well is harmless.
        self.connect("unrealize", lambda *_a: self._stop_telemetry())

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
    def _apply_mode(self, mode: str) -> None:
        """Activate a tensix-viz mode string + sync the header caption."""
        self._mode = mode
        self._eval("window.__viz&&window.__viz.activate(" + json.dumps(mode) + ")")
        self._mode_lbl.set_label("◉ " + _MODE_CAPTION.get(mode, mode))  # ◉ + mode

    def set_mode(self, medium=None) -> None:
        """Animate the tensix-viz mode matching *medium* (idle when None) and
        update the header caption. Independent of the telemetry tap."""
        self._apply_mode(mode_for_medium(medium) if medium is not None else "idle")

    def cycle_mode(self) -> None:
        """Advance to the next tensix-viz mode (title-click manual override).
        The auto-driver reasserts the medium's mode on the next job."""
        try:
            i = _CYCLE_MODES.index(self._mode)
        except ValueError:
            i = -1
        self._apply_mode(_CYCLE_MODES[(i + 1) % len(_CYCLE_MODES)])

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

    # ── Live telemetry tap (background thread; power via tt-smi) ─────────────
    def _start_telemetry(self) -> None:
        if self._tel_thread is not None:
            return
        self._tel_running = True
        self._tel_stop = threading.Event()
        self._tel_thread = threading.Thread(
            target=self._telemetry_loop, args=(self._tel_stop,), daemon=True
        )
        self._tel_thread.start()

    def _stop_telemetry(self) -> None:
        self._tel_running = False
        if self._tel_stop is not None:
            self._tel_stop.set()
        self._tel_thread = None
        # Blank the readout so a stale value doesn't linger after stop.
        if getattr(self, "_readout_lbl", None) is not None:
            self._readout_lbl.set_label("")

    def _telemetry_loop(self, stop: "threading.Event") -> None:
        """Background poll: read per-chip power (tt-smi, ~0.3 s) or fall back to
        sysfs AICLK, then hand the result to the main thread. Does NO GTK work
        here — the subprocess would otherwise block the UI (GTK threading rule).
        Exits promptly when `stop` is set (via `stop.wait`, not sleep)."""
        while not stop.is_set():
            readout, pairs = sample_telemetry(self._chip_display, self._chip_actual)
            GLib.idle_add(self._apply_sample, readout, pairs)
            stop.wait(_TELEMETRY_INTERVAL_S)

    def _apply_sample(self, readout: str, activities) -> bool:
        """Main-thread: update the header readout + feed each chip its flow. The
        activity scalar is shaped into dram_bw/l1_fill/writeback here (not in the
        sampler) because `shape_flow` needs the current mode — a job's mode gets
        a floor so the flow is clearly visible, then intensifies with real load.
        A late sample arriving after stop is ignored (`_tel_running`)."""
        if not self._tel_running:
            return False
        if self._readout_lbl is not None:
            self._readout_lbl.set_label(readout)
        active = self._mode != "idle"
        for i, act in enumerate(activities):
            if act is not None:
                dram, l1, wb = shape_flow(act, active)
                self._eval(
                    "window.__viz&&window.__viz.setChipStats(%d,"
                    "{dram_bw:%.3f,l1_fill:%.3f,writeback:%.3f})" % (i, dram, l1, wb)
                )
        return False  # one-shot idle callback
