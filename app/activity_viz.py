# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""activity_viz.py — an OPTIONAL "watch the hardware" widget for the Create
surface.

Embeds the self-contained `tensix-viz` Canvas animation (bundled under
`app/assets/tensix-viz/`, zero external deps) in a `WebKit.WebView` and drives
it from generation state:

  * `set_active(medium)` → `viz.activate(<mode>)` — animate the mode that
    matches the medium being generated (diffusion / video / thinking / …) AND
    start a light live-telemetry tap.
  * `set_idle()`         → `viz.activate('idle')` — calm it back down and stop
    the tap.

The telemetry tap reads AICLK straight from sysfs
(`/sys/class/tenstorrent/*/tt_aiclk`, the same source the TT-TV chip HUD uses)
and feeds a normalised intensity into `viz.setMemoryStats({dram_bw, l1_fill})`,
so the memory layer pulses with the chips' REAL clock activity — enough of the
"is there promise here?" experiment to judge going further.

Fully optional and fail-soft: hidden by default; a build without WebKit, or a
box with no Tenstorrent chips, degrades to an inert stub / preset animation and
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

# Which tensix-viz animation MODE best evokes each Create medium.
# (tensix-viz modes: idle/inference/prefill/thinking/agents/diffusion/video/
#  batch/explore/kernel_dispatch.)
_MODE_BY_MEDIUM_ID = {
    "image": "diffusion",
    "video": "video",
    "animate": "video",
    "animatediff": "diffusion",
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


def read_aiclk_intensity() -> "tuple[float, float] | None":
    """Read AICLK from sysfs and normalise to `(dram_bw, l1_fill)` in 0..1 for
    `viz.setMemoryStats`. Returns None when no chips/telemetry are present (so
    the caller leaves the mode preset's own values alone). Pure/instant — no
    subprocess."""
    try:
        clocks = []
        for chip_dir in sorted(_SYSFS.glob("tenstorrent!*")):
            try:
                clocks.append(int((chip_dir / "tt_aiclk").read_text().strip()))
            except (OSError, ValueError):
                pass
    except OSError:
        return None
    if not clocks:
        return None
    intensity = max(0.0, min(1.0, max(clocks) / _AICLK_CEILING_MHZ))
    return (intensity, intensity * 0.8)


class ActivityVizWidget(Gtk.Box):
    """Small tensix-viz chip animation you can 'watch' while generating.

    `set_active(medium)` starts the matching mode + a 1 s telemetry poll;
    `set_idle()` calms it and stops polling. A WebKit-less build gets an inert
    stub (the app is never blocked on this experiment)."""

    def __init__(self, arch: str = "blackhole") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("activity-viz")
        self._arch = arch
        self._webview = None
        self._pending_js: "list[str]" = []
        self._telemetry_timer: "int | None" = None

        if not _WEBKIT_OK:
            return  # inert stub — no WebKit on this build

        self._webview = WebKit.WebView()
        try:
            self._webview.get_settings().set_enable_javascript(True)
        except Exception:
            pass
        self._webview.set_hexpand(True)
        self._webview.set_vexpand(True)
        self._webview.set_size_request(220, 160)
        # Same realize-deferral as artgen_detail: load_html()/evaluate_javascript
        # before the WebView is realized is a silent no-op.
        self._webview.connect("realize", self._on_realize)
        self._webview.connect("unrealize", lambda *_a: self._stop_telemetry())
        try:
            self._webview.load_html(self._page_html(), "about:blank")
        except Exception:
            pass
        self.append(self._webview)

    # ── HTML page (tensix-viz inlined, self-contained) ───────────────────────
    def _page_html(self) -> str:
        try:
            js = (_ASSETS / "tensix-viz.js").read_text()
            css = (_ASSETS / "tensix-viz.css").read_text()
        except OSError:
            js = css = ""
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<style>html,body{margin:0;padding:0;background:#0F2A35;overflow:hidden}"
            "canvas{display:block}" + css + "</style></head><body>"
            "<canvas id='viz' width='220' height='160'></canvas>"
            "<script>" + js + "</script>"
            "<script>try{window.__viz=new window.TensixViz("
            "document.getElementById('viz'),{arch:" + json.dumps(self._arch) +
            ",showMemory:true});window.__viz.activate('idle');}catch(e){}</script>"
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
            # no-op). Bound the backlog: a viz that's set_active while still
            # unrealized would otherwise accumulate one setMemoryStats per
            # second forever. 32 keeps the mode-activate call (queued first)
            # plus recent telemetry, and drops only stale telemetry.
            self._pending_js.append(js)
            if len(self._pending_js) > 32:
                del self._pending_js[1:-16]

    # ── Public API ───────────────────────────────────────────────────────────
    def set_active(self, medium=None) -> None:
        """Begin 'watching': animate the mode matching *medium* and start the
        live AICLK telemetry tap."""
        mode = mode_for_medium(medium)
        self._eval("window.__viz&&window.__viz.activate(" + json.dumps(mode) + ")")
        self._start_telemetry()

    def set_idle(self) -> None:
        """Calm the animation back to idle and stop the telemetry tap."""
        self._eval("window.__viz&&window.__viz.activate('idle')")
        self._stop_telemetry()

    # ── Live telemetry tap ───────────────────────────────────────────────────
    def _start_telemetry(self) -> None:
        if self._telemetry_timer is not None or self._webview is None:
            return
        self._tick_telemetry()  # immediate first sample
        self._telemetry_timer = GLib.timeout_add(1000, self._tick_telemetry)

    def _stop_telemetry(self) -> None:
        if self._telemetry_timer is not None:
            GLib.source_remove(self._telemetry_timer)
            self._telemetry_timer = None

    def _tick_telemetry(self) -> bool:
        vals = read_aiclk_intensity()
        if vals is not None:
            dram, l1 = vals
            self._eval(
                "window.__viz&&window.__viz.setMemoryStats({dram_bw:%.3f,l1_fill:%.3f})"
                % (dram, l1)
            )
        return True  # keep polling until _stop_telemetry
