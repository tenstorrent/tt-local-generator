#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
ArtgenPanel — GTK4 widget for the artgen generative art tool.

Self-contained panel with a left column of per-type controls and a right
preview pane (SVG picture or text).  Designed to be inserted as the "artgen"
child of main_window's _gallery_stack.

Threading discipline (same as main_window.py):
    GTK is single-threaded.  The background generation thread must NEVER
    touch widgets directly — all UI updates go via GLib.idle_add().
"""

from __future__ import annotations

import json
import random
import threading
import time
import types
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

_WEBKIT_OK = False
try:
    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit as _WebKit
    _WEBKIT_OK = True
except Exception:
    pass

import artgen
from media_store import media_store as _media_store, MediaRecord, make_artgen_path, make_thumbnail
from app_settings import settings as _settings
from server_config import server_config


# ── Widget helpers ────────────────────────────────────────────────────────────

def _dd(options: list[str], default: str | None = None) -> Gtk.DropDown:
    """Create a DropDown backed by a StringList."""
    model = Gtk.StringList.new(options)
    dd = Gtk.DropDown.new(model, None)
    if default and default in options:
        dd.set_selected(options.index(default))
    return dd


def _dd_val(dd: Gtk.DropDown) -> str:
    item = dd.get_selected_item()
    return item.get_string() if item else ""


def _spin(lo: float, hi: float, step: float = 1.0, value: float = 0.0) -> Gtk.SpinButton:
    adj = Gtk.Adjustment.new(value, lo, hi, step, step * 10, 0)
    btn = Gtk.SpinButton.new(adj, 1.0, 0)
    btn.set_numeric(True)
    return btn


def _check(label: str, active: bool = False) -> Gtk.CheckButton:
    cb = Gtk.CheckButton.new_with_label(label)
    cb.set_active(active)
    return cb


def _row(label: str, widget: Gtk.Widget, label_width: int = 80) -> Gtk.Box:
    """Horizontal box: fixed-width label on the left, expanding widget on the right."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    lbl = Gtk.Label(label=label)
    lbl.set_xalign(0)
    lbl.set_size_request(label_width, -1)
    box.append(lbl)
    widget.set_hexpand(True)
    box.append(widget)
    return box


def _section_lbl(text: str) -> Gtk.Label:
    lbl = Gtk.Label(label=text.upper())
    lbl.set_xalign(0)
    lbl.add_css_class("section-label")
    return lbl


# ── ArtgenPanel ───────────────────────────────────────────────────────────────

_MODEL_TO_KEY: dict[str, str] = {
    "Qwen3-8B":               "artgen-qwen3-8b",
    "Llama-3.1-8B-Instruct":  "artgen-llama-3.1-8b",
    "Qwen2.5-7B-Instruct":    "artgen-qwen2.5-7b",
    "Llama-3.3-70B-Instruct": "artgen-llama-3.3-70b",
}
_ARTGEN_MODELS = list(_MODEL_TO_KEY)


class ArtgenPanel(Gtk.Box):
    """
    Two-column artgen UI: scrollable controls (left) + SVG/text preview (right).
    Drop into a Gtk.Stack as the named child "artgen".
    """

    # Generators hidden from the artgen picker.  AnimateDiff is excluded here
    # because it prompts and plays back like a video (not generative art) and
    # now lives in the Video tab as a first-class generation mode.  Historical
    # artgen MediaRecords with generator_type="animatediff" still display in the
    # gallery; only the picker entry is removed.
    _HIDDEN_GENERATORS: frozenset = frozenset({
        "animatediff",   # lives in Video tab, not Art tab
        "generate_midi", # MCP-delegate stub — no implementation yet
    })

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.on_remix: "Optional[Callable[['MediaRecord'], None]]" = None
        self._generating: bool = False
        self._gen_queue: deque = deque()  # (gen_name, args) tuples pending manual generation
        self._last_out_path: Path | None = None
        self._tmp_svg: Path | None = None
        self._llm_timer_id: int | None = None
        self._llm_t0: float = 0.0
        # Auto-generate state
        self._auto_gen: bool = False
        self._auto_gen_timer_id: int | None = None
        self._auto_gen_countdown: float = 0.0
        self._auto_gen_error_streak: int = 0
        self._build()
        # Start background health polling every 5 seconds.
        GLib.timeout_add_seconds(5, self._poll_health)
        # Immediate first check so the dot isn't "unknown" for 5 seconds.
        threading.Thread(target=self._check_health_bg, daemon=True).start()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        from artgen_gallery import ArtgenGallery
        from artgen_detail import ArtgenDetail
        from artgen_watch import ArtgenWatch

        # ── Sub-navigation header ─────────────────────────────────────────────
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        nav.add_css_class("artgen-subnav")

        self._gallery_tab_btn = Gtk.ToggleButton(label="▦ Gallery")
        self._gallery_tab_btn.set_active(True)
        self._gallery_tab_btn.add_css_class("artgen-subnav-btn")
        self._gallery_tab_btn.connect("toggled", self._on_tab_toggled, "gallery")

        self._watch_tab_btn = Gtk.ToggleButton(label="▶ Watch")
        self._watch_tab_btn.add_css_class("artgen-subnav-btn")
        self._watch_tab_btn.connect("toggled", self._on_tab_toggled, "watch")
        self._watch_tab_btn.set_group(self._gallery_tab_btn)

        nav.append(self._gallery_tab_btn)
        nav.append(self._watch_tab_btn)
        self.append(nav)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Body: permanent create sidebar (left) + content stack (right) ─────
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        body.set_hexpand(True)
        body.set_vexpand(True)

        sidebar = self._build_create_sidebar()
        body.append(sidebar)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self._sub_stack = Gtk.Stack()
        self._sub_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._sub_stack.set_transition_duration(120)
        self._sub_stack.set_hexpand(True)
        self._sub_stack.set_vexpand(True)

        self._gallery = ArtgenGallery()
        self._gallery.on_card_activated = self._on_gallery_card_activated
        self._gallery.on_watch_requested = self._on_watch_requested
        self._gallery.on_card_deleted = self._on_gallery_card_deleted
        self._gallery.on_remix = self._on_remix_record
        self._sub_stack.add_named(self._gallery, "gallery")

        self._detail = ArtgenDetail()
        self._detail.on_back = self._on_detail_back
        self._detail.on_deleted = self._on_detail_deleted
        self._detail.on_remix = self._on_remix_record
        self._sub_stack.add_named(self._detail, "detail")

        self._watch = ArtgenWatch()
        self._watch.on_exit = self._on_watch_exit
        self._sub_stack.add_named(self._watch, "watch")

        self._sub_stack.set_visible_child_name("gallery")
        # Defer the initial gallery refresh so it runs after the window paints.
        # _rebuild_grid() with 100+ artgen records takes ~250 ms synchronously,
        # which delays the first frame.
        GLib.idle_add(self._gallery.refresh)
        body.append(self._sub_stack)
        self.append(body)

    def _build_create_sidebar(self) -> Gtk.Box:
        """Permanent create controls sidebar (left column, always visible)."""
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        left_box.set_size_request(240, -1)
        left_box.set_hexpand(False)
        left_box.add_css_class("artgen-ctrl-pane")

        type_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        type_bar.set_margin_start(12); type_bar.set_margin_end(12)
        type_bar.set_margin_top(10); type_bar.set_margin_bottom(6)
        type_lbl = _section_lbl("type")
        type_lbl.set_size_request(44, -1)
        type_bar.append(type_lbl)
        gen_names = [n for n in artgen.all_names() if n not in self._HIDDEN_GENERATORS and n not in (set(_settings.get("hidden_plugins") or []))]
        self._type_dd = _dd(gen_names, "landscape")
        self._type_dd.set_hexpand(True)
        self._type_dd.connect("notify::selected", self._on_type_changed)
        type_bar.append(self._type_dd)
        # Presets button — opens grouped preset popover
        preset_menu_btn = self._build_preset_menu_btn()
        type_bar.append(preset_menu_btn)
        left_box.append(type_bar)
        left_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self._controls_stack = Gtk.Stack()
        self._controls_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._controls_stack.set_transition_duration(80)
        self._controls_stack.set_margin_start(12); self._controls_stack.set_margin_end(12)
        self._controls_stack.set_margin_top(10); self._controls_stack.set_margin_bottom(6)
        for name in gen_names:
            self._controls_stack.add_named(self._build_controls_page(name), name)
        # Build control pages for hidden generators too so their widget references
        # (self._ad_prompt etc.) exist when auto-generate fires them.
        for name in self._HIDDEN_GENERATORS:
            if name in artgen.all_names():
                self._controls_stack.add_named(self._build_controls_page(name), name)
        self._controls_stack.set_visible_child_name("landscape")
        ctrl_scroll = Gtk.ScrolledWindow()
        ctrl_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        ctrl_scroll.set_vexpand(True)
        ctrl_scroll.set_child(self._controls_stack)
        left_box.append(ctrl_scroll)
        left_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Footer: Generate + Server popover ─────────────────────────────────
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        footer.set_margin_start(12); footer.set_margin_end(12)
        footer.set_margin_top(10); footer.set_margin_bottom(12)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._gen_btn = Gtk.Button(label="✦ Generate")
        self._gen_btn.add_css_class("artgen-generate-btn")
        self._gen_btn.set_hexpand(True)
        self._gen_btn.connect("clicked", self._on_generate_clicked)
        btn_row.append(self._gen_btn)

        # Server popover — keeps infrastructure out of the creative surface
        srv_pop_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        srv_pop_content.set_margin_start(12); srv_pop_content.set_margin_end(12)
        srv_pop_content.set_margin_top(10); srv_pop_content.set_margin_bottom(10)
        srv_pop_content.set_size_request(210, -1)
        srv_pop_content.append(_section_lbl("Generative Art server"))
        self._srv_model_dd = _dd(_ARTGEN_MODELS, "Qwen3-8B")
        srv_pop_content.append(_row("Model", self._srv_model_dd, label_width=46))
        srv_btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._srv_start_btn = Gtk.Button(label="▶ Start")
        self._srv_start_btn.add_css_class("artgen-srv-start-btn")
        self._srv_start_btn.connect("clicked", self._on_srv_start)
        srv_btn_row.append(self._srv_start_btn)
        self._srv_stop_btn = Gtk.Button(label="■ Stop")
        self._srv_stop_btn.add_css_class("artgen-srv-stop-btn")
        self._srv_stop_btn.connect("clicked", self._on_srv_stop)
        srv_btn_row.append(self._srv_stop_btn)
        srv_pop_content.append(srv_btn_row)
        pop_health_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._health_dot = Gtk.Label(label="●")
        self._health_dot.add_css_class("artgen-health-unknown")
        pop_health_row.append(self._health_dot)
        self._srv_status_lbl = Gtk.Label(label="unknown")
        self._srv_status_lbl.set_xalign(0)
        self._srv_status_lbl.add_css_class("artgen-status")
        self._srv_status_lbl.set_hexpand(True)
        pop_health_row.append(self._srv_status_lbl)
        srv_pop_content.append(pop_health_row)
        srv_popover = Gtk.Popover()
        srv_popover.set_child(srv_pop_content)
        srv_popover.set_position(Gtk.PositionType.TOP)

        srv_btn_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._srv_btn_dot = Gtk.Label(label="●")
        self._srv_btn_dot.add_css_class("artgen-health-unknown")
        srv_btn_inner.append(self._srv_btn_dot)
        srv_btn_inner.append(Gtk.Label(label="Server"))
        srv_menu_btn = Gtk.MenuButton()
        srv_menu_btn.set_child(srv_btn_inner)
        srv_menu_btn.add_css_class("flat")
        srv_menu_btn.set_popover(srv_popover)
        srv_menu_btn.set_tooltip_text("Generative Art server controls (model, start/stop, health)")
        btn_row.append(srv_menu_btn)

        footer.append(btn_row)
        self._status_lbl = Gtk.Label(label="Ready — choose a type and click Generate")
        self._status_lbl.set_xalign(0)
        self._status_lbl.add_css_class("artgen-status")
        self._status_lbl.set_wrap(True)
        self._status_lbl.set_max_width_chars(32)
        footer.append(self._status_lbl)
        left_box.append(footer)

        # Auto-generate collapsible section
        self._build_auto_section(left_box)

        return left_box

    # ── Per-type controls pages ───────────────────────────────────────────────

    def _build_controls_page(self, name: str) -> Gtk.Box:
        """Build and return the controls box for one generator type."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        if name == "landscape":
            from artgen.generators.landscape import PALETTES
            self._land_palette = _dd(list(PALETTES), "sunset")
            self._land_mountains = _check("Mountains", True)
            self._land_clouds = _check("Clouds", False)
            self._land_stars = _check("Stars", False)
            self._land_glitch = _check("Glitch effect", False)
            box.append(_row("Palette", self._land_palette))
            box.append(self._land_mountains)
            box.append(self._land_clouds)
            box.append(self._land_stars)
            box.append(self._land_glitch)

        elif name == "skyline":
            from artgen.generators.skyline import _ERA_PALETTES, _DENSITY_DESC, _SKY_DESC
            self._sky_era = _dd(list(_ERA_PALETTES), "modern")
            self._sky_density = _dd(list(_DENSITY_DESC), "medium")
            self._sky_sky = _dd(list(_SKY_DESC), "night")
            box.append(_row("Era", self._sky_era))
            box.append(_row("Density", self._sky_density))
            box.append(_row("Sky", self._sky_sky))

        elif name == "constellation":
            from artgen.generators.constellation import _CULTURE_PROMPTS
            self._con_culture = _dd(list(_CULTURE_PROMPTS), "invented")
            self._con_stars = _spin(3, 20, 1, 8)
            self._con_lore = _check("Append mythology lore", False)
            box.append(_row("Culture", self._con_culture))
            box.append(_row("Stars", self._con_stars))
            box.append(self._con_lore)

        elif name == "geometric":
            from artgen.generators.geometric import _STYLE_PROMPTS, _NAMED_PALETTES, _COMPLEXITY_HINTS
            self._geo_style = _dd(list(_STYLE_PROMPTS), "mondrian")
            self._geo_palette = _dd(list(_NAMED_PALETTES), "teal")
            self._geo_complexity = _dd(list(_COMPLEXITY_HINTS), "low")
            box.append(_row("Style", self._geo_style))
            box.append(_row("Palette", self._geo_palette))
            box.append(_row("Complexity", self._geo_complexity))

        elif name == "circuit":
            from artgen.generators.circuit import _DIAGRAM_STYLES, _GATE_SHAPES
            self._cir_inputs = Gtk.Entry()
            self._cir_inputs.set_text("A,B,C")
            self._cir_inputs.set_placeholder_text("e.g. A,B,C")
            self._cir_depth = _spin(1, 3, 1, 2)
            self._cir_style = _dd(list(_DIAGRAM_STYLES), "clean")
            box.append(_row("Inputs", self._cir_inputs))
            box.append(_row("Depth", self._cir_depth))
            box.append(_row("Style", self._cir_style))
            box.append(_section_lbl("gates"))
            gate_flow = Gtk.FlowBox()
            gate_flow.set_max_children_per_line(3)
            gate_flow.set_selection_mode(Gtk.SelectionMode.NONE)
            self._gate_checks: dict[str, Gtk.CheckButton] = {}
            for gate in _GATE_SHAPES:
                cb = _check(gate, gate in ("and", "or"))
                self._gate_checks[gate] = cb
                gate_flow.append(cb)
            box.append(gate_flow)

        elif name == "verse":
            from artgen.generators.verse import _FORMS
            self._verse_form = _dd(list(_FORMS), "haiku")
            self._verse_theme = Gtk.Entry()
            self._verse_theme.set_text("the passage of time")
            self._verse_count = _spin(1, 10, 1, 3)
            box.append(_row("Form", self._verse_form))
            box.append(_row("Theme", self._verse_theme))
            box.append(_row("Count", self._verse_count))

        elif name == "palette":
            self._pal_mood = Gtk.Entry()
            self._pal_mood.set_text("volcanic")
            self._pal_mood.set_placeholder_text("e.g. drowned empire, neon city")
            self._pal_count = _spin(3, 12, 1, 6)
            self._pal_css = _check("Also export CSS variables file", False)
            box.append(_row("Mood", self._pal_mood))
            box.append(_row("Colors", self._pal_count))
            box.append(self._pal_css)

        elif name == "ansi":
            from artgen.generators.ansi import _COLOR_MODES, _STYLE_HINTS
            self._ansi_subject = Gtk.Entry()
            self._ansi_subject.set_text("a mountain at sunset")
            self._ansi_subject.set_placeholder_text("what to draw")
            self._ansi_width = _spin(20, 120, 1, 60)
            self._ansi_colors = _dd(list(_COLOR_MODES), "256")
            self._ansi_style = _dd(list(_STYLE_HINTS), "scene")
            box.append(_row("Subject", self._ansi_subject))
            box.append(_row("Width", self._ansi_width))
            box.append(_row("Colors", self._ansi_colors))
            box.append(_row("Style", self._ansi_style))

        elif name == "freeform":
            hint = Gtk.Label(
                label="Describe what to generate.\n"
                      "Set Output to .svg, .json, .ans, or .txt to hint the format."
            )
            hint.set_xalign(0)
            hint.set_wrap(True)
            hint.add_css_class("hint")
            self._free_tv = Gtk.TextView()
            self._free_tv.set_wrap_mode(Gtk.WrapMode.WORD)
            self._free_tv.get_buffer().set_text(
                "a circuit diagram of a sad robot as SVG"
            )
            self._free_tv.set_monospace(True)
            self._free_tv.set_margin_start(4)
            self._free_tv.set_margin_end(4)
            self._free_tv.set_margin_top(4)
            self._free_tv.set_margin_bottom(4)
            free_scroll = Gtk.ScrolledWindow()
            free_scroll.set_min_content_height(90)
            free_scroll.set_child(self._free_tv)
            free_scroll.add_css_class("freeform-entry")
            box.append(hint)
            box.append(free_scroll)

        elif name == "animatediff":
            # ── Prompt row ────────────────────────────────────────────────────
            prompt_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            self._ad_prompt = Gtk.Entry()
            self._ad_prompt.set_hexpand(True)
            self._ad_prompt.set_placeholder_text("describe the animation…")
            self._ad_prompt.set_text(
                "purple phosphor glow across distant mountains at 2am, retro CRT haze, cyan mist, cinematic"
            )
            inspire_btn = Gtk.Button(label="✦")
            inspire_btn.add_css_class("artgen-inspire-btn")
            inspire_btn.connect("clicked", lambda _: self._on_inspire("animatediff", self._ad_prompt))
            prompt_row.append(self._ad_prompt)
            prompt_row.append(inspire_btn)

            self._ad_neg_prompt = Gtk.Entry()
            self._ad_neg_prompt.set_placeholder_text("negative prompt…")
            self._ad_neg_prompt.set_text("blurry, low quality")

            # ── Core params ───────────────────────────────────────────────────
            self._ad_frames = _dd(["8", "16", "24", "32"], "8")
            self._ad_steps = _spin(4, 50, 1, 25)
            self._ad_seed = _spin(0, 2**31 - 1, 1, 42)
            self._ad_temporal_alpha = _spin(0.0, 1.0, 0.05, 0.35)
            self._ad_temporal_alpha.set_digits(2)

            box.append(_section_lbl("Prompt"))
            box.append(prompt_row)
            box.append(_row("Negative", self._ad_neg_prompt))
            box.append(_row("Frames", self._ad_frames))
            box.append(_row("Steps", self._ad_steps))
            box.append(_row("Seed", self._ad_seed))
            box.append(_row("Temporal α", self._ad_temporal_alpha))

            # ── Performance expander ──────────────────────────────────────────
            perf_exp = Gtk.Expander(label="⚡ Performance & Mode")
            perf_exp.set_margin_top(6)
            perf_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            perf_box.set_margin_start(8)
            perf_box.set_margin_top(4)

            self._ad_mode = _dd(["blackhole", "cpu", "sim"], "blackhole")
            self._ad_lightning = Gtk.CheckButton(label="Lightning (Euler scheduler)")
            self._ad_lightning.set_tooltip_text(
                "Blackhole: uses Euler solver with cosine temporal decay.\n"
                "CPU: loads AnimateDiff-Lightning distilled weights (CFG=1.0)."
            )
            self._ad_lightning_steps = _dd(["2", "4", "8"], "4")
            self._ad_device_id = _spin(-1, 7, 1, -1)
            self._ad_device_id.set_tooltip_text("-1 = all chips (default)")

            # Only show lightning-steps when both cpu mode and lightning are active
            self._ad_lightning_steps_row = _row("Distill steps", self._ad_lightning_steps)
            self._ad_lightning_steps_row.set_visible(False)

            def _update_lightning_steps_vis(*_):
                cpu = _dd_val(self._ad_mode) == "cpu"
                lit = self._ad_lightning.get_active()
                self._ad_lightning_steps_row.set_visible(cpu and lit)

            self._ad_mode.connect("notify::selected", _update_lightning_steps_vis)
            self._ad_lightning.connect("toggled", _update_lightning_steps_vis)

            perf_box.append(_row("Mode", self._ad_mode))
            perf_box.append(self._ad_lightning)
            perf_box.append(self._ad_lightning_steps_row)
            perf_box.append(_row("Device ID", self._ad_device_id))
            perf_exp.set_child(perf_box)
            box.append(perf_exp)

            # ── Chain continuity expander ─────────────────────────────────────
            chain_exp = Gtk.Expander(label="🔗 Chain Continuity")
            chain_exp.set_margin_top(4)
            chain_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            chain_box.set_margin_start(8)
            chain_box.set_margin_top(4)

            chain_hint = Gtk.Label(
                label="Thread visual DNA across separately-prompted generations.\n"
                      "chain-from blends the previous run's latents into seed noise."
            )
            chain_hint.set_xalign(0)
            chain_hint.set_wrap(True)
            chain_hint.add_css_class("hint")
            chain_box.append(chain_hint)

            # chain-from path (file picker)
            self._ad_chain_from = Gtk.Entry()
            self._ad_chain_from.set_placeholder_text("path to .pt latents (optional)…")
            chain_from_btn = Gtk.Button(label="…")
            chain_from_btn.set_tooltip_text("Pick a .pt latents file from a previous --chain-save run")
            chain_from_btn.connect("clicked", self._on_ad_chain_from_pick)
            chain_from_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            self._ad_chain_from.set_hexpand(True)
            chain_from_row.append(self._ad_chain_from)
            chain_from_row.append(chain_from_btn)

            # chain-save: auto path derived from output, enabled by checkbox
            self._ad_chain_save = Gtk.CheckButton(label="Save latents for next chain run")
            self._ad_chain_save.set_tooltip_text(
                "Saves this run's final latents as <output>.chain.pt next to the GIF."
            )

            self._ad_chain_alpha = _spin(0.0, 1.0, 0.05, 0.6)
            self._ad_chain_alpha.set_digits(2)
            self._ad_chain_alpha.set_tooltip_text(
                "0 = ignore previous run, 1 = fully replace seed noise with previous latents.\n"
                "Effective range: 0.2–0.55. Values above 0.6 suppress prompt guidance."
            )

            chain_box.append(_row("From (.pt)", chain_from_row))
            chain_box.append(self._ad_chain_save)
            chain_box.append(_row("Chain α", self._ad_chain_alpha))
            chain_exp.set_child(chain_box)
            box.append(chain_exp)

            # ── Phase 3 MotionAdapter expander ────────────────────────────────
            motion_exp = Gtk.Expander(label="🎞 Phase 3: MotionAdapter")
            motion_exp.set_margin_top(4)
            motion_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            motion_box.set_margin_start(8)
            motion_box.set_margin_top(4)

            motion_hint = Gtk.Label(
                label="Phase 3 runs the full AnimateDiff MotionAdapter on Blackhole.\n"
                      "~52 s/frame full; ~7.7 s/frame with 'Fast (skip up1 up2)' preset."
            )
            motion_hint.set_xalign(0)
            motion_hint.set_wrap(True)
            motion_hint.add_css_class("hint")
            motion_box.append(motion_hint)

            self._ad_motion_adapter = Gtk.CheckButton(label="Enable MotionAdapter (Blackhole only)")
            self._ad_injection_alpha = _spin(0.0, 1.0, 0.05, 1.0)
            self._ad_injection_alpha.set_digits(2)
            self._ad_injection_alpha.set_tooltip_text(
                "1.0 = full injection (default). 0.0 = bypass (debug). Values in 0.5–1.0 are useful."
            )

            # Skip-keys preset: None, Fast (up1+up2), or Custom
            self._ad_motion_skip = _dd(
                ["None (full quality)", "Fast (skip up1 up2)", "Balanced (skip up2)"],
                "None (full quality)"
            )
            self._ad_motion_skip.set_tooltip_text(
                "Injection points to skip.\n"
                "Fast: skips up1+up2 (~7.7 s/frame, minimal quality loss).\n"
                "Balanced: skips only up2 (~25 s/frame)."
            )

            motion_box.append(self._ad_motion_adapter)
            motion_box.append(_row("Skip preset", self._ad_motion_skip))
            motion_box.append(_row("Injection α", self._ad_injection_alpha))
            motion_exp.set_child(motion_box)
            box.append(motion_exp)

            hint = Gtk.Label(
                label="Requires Blackhole hardware (blackhole mode).\n"
                      "~2 min on P300C for 8 frames × 25 steps (standard mode)."
            )
            hint.set_xalign(0)
            hint.set_wrap(True)
            hint.add_css_class("hint")
            hint.set_margin_top(6)
            box.append(hint)
            # No separate Theme Inspiration section — inspire is inline with prompt
            return box

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        box.append(_section_lbl("Theme Inspiration"))
        inspire_row, theme_entry = self._build_inspire_row(name)
        box.append(inspire_row)
        box._theme_entry = theme_entry
        return box

    def _build_inspire_row(self, gen_name: str) -> tuple[Gtk.Box, Gtk.Entry]:
        """Returns (row_widget, theme_entry) for the Theme Inspiration row."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        entry = Gtk.Entry()
        entry.set_hexpand(True)
        entry.set_placeholder_text("seed theme…")
        inspire_btn = Gtk.Button(label="✦")
        inspire_btn.add_css_class("artgen-inspire-btn")
        inspire_btn.connect("clicked", lambda _: self._on_inspire(gen_name, entry))
        row.append(entry)
        row.append(inspire_btn)
        return row, entry

    # ── Preset popover ───────────────────────────────────────────────────────

    def _load_presets(self) -> list[dict]:
        """Load artgen_presets.json from the same directory as this module."""
        try:
            p = Path(__file__).parent / "artgen_presets.json"
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _build_preset_menu_btn(self) -> Gtk.MenuButton:
        """Build the ⚡ MenuButton that opens the grouped preset popover."""
        presets = self._load_presets()

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_size_request(280, 360)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_start(8); outer.set_margin_end(8)
        outer.set_margin_top(8); outer.set_margin_bottom(8)

        # Group presets by generator
        groups: dict[str, list[dict]] = {}
        for p in presets:
            groups.setdefault(p["generator"], []).append(p)

        popover = Gtk.Popover()
        popover.set_position(Gtk.PositionType.BOTTOM)

        for gen_name, group in groups.items():
            hdr = Gtk.Label(label=gen_name.upper())
            hdr.set_xalign(0)
            hdr.add_css_class("artgen-preset-section")
            outer.append(hdr)

            for preset in group:
                btn = Gtk.Button()
                btn.add_css_class("artgen-preset-btn")
                btn.set_hexpand(True)

                btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
                name_lbl = Gtk.Label(label=preset["name"])
                name_lbl.set_xalign(0)
                name_lbl.add_css_class("artgen-preset-name")
                desc_lbl = Gtk.Label(label=preset["description"])
                desc_lbl.set_xalign(0)
                desc_lbl.set_wrap(True)
                desc_lbl.set_max_width_chars(36)
                desc_lbl.add_css_class("artgen-preset-desc")
                btn_box.append(name_lbl)
                btn_box.append(desc_lbl)
                btn.set_child(btn_box)

                # Capture preset and popover by value in the closure
                btn.connect(
                    "clicked",
                    lambda _b, pr=preset, pop=popover: self._on_preset_clicked(pr, pop),
                )
                outer.append(btn)

            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            sep.set_margin_top(4); sep.set_margin_bottom(4)
            outer.append(sep)

        scroll.set_child(outer)
        popover.set_child(scroll)

        mb = Gtk.MenuButton()
        mb.set_label("⚡")
        mb.set_tooltip_text("Presets — one-click parameter recipes")
        mb.add_css_class("flat")
        mb.add_css_class("artgen-preset-menu-btn")
        mb.set_popover(popover)
        return mb

    def _on_preset_clicked(self, preset: dict, popover: Gtk.Popover) -> None:
        """Switch generator type and apply all preset params, then close popover."""
        popover.popdown()
        gen_name = preset["generator"]
        gen_names = artgen.all_names()
        if gen_name in gen_names:
            self._type_dd.set_selected(gen_names.index(gen_name))
            self._controls_stack.set_visible_child_name(gen_name)
        self._apply_preset_params(gen_name, preset.get("params", {}))

    def _apply_preset_params(self, gen_name: str, params: dict) -> None:
        """Write preset param values into the matching GTK widgets."""
        if gen_name == "landscape":
            if "palette" in params:
                self._set_dd(self._land_palette, params["palette"])
            if "mountains" in params:
                self._land_mountains.set_active(bool(params["mountains"]))
            if "clouds" in params:
                self._land_clouds.set_active(bool(params["clouds"]))
            if "stars" in params:
                self._land_stars.set_active(bool(params["stars"]))
            if "glitch" in params:
                self._land_glitch.set_active(bool(params["glitch"]))

        elif gen_name == "skyline":
            if "era" in params:
                self._set_dd(self._sky_era, params["era"])
            if "density" in params:
                self._set_dd(self._sky_density, params["density"])
            if "sky" in params:
                self._set_dd(self._sky_sky, params["sky"])

        elif gen_name == "constellation":
            if "culture" in params:
                self._set_dd(self._con_culture, params["culture"])
            if "stars" in params:
                self._con_stars.set_value(int(params["stars"]))
            if "lore" in params:
                self._con_lore.set_active(bool(params["lore"]))

        elif gen_name == "geometric":
            if "style" in params:
                self._set_dd(self._geo_style, params["style"])
            if "geo_palette" in params:
                self._set_dd(self._geo_palette, params["geo_palette"])
            if "complexity" in params:
                self._set_dd(self._geo_complexity, params["complexity"])

        elif gen_name == "circuit":
            if "inputs" in params:
                self._cir_inputs.set_text(params["inputs"])
            if "depth" in params:
                self._cir_depth.set_value(int(params["depth"]))
            if "circuit_style" in params:
                self._set_dd(self._cir_style, params["circuit_style"])
            if "gates" in params:
                gate_set = set(params["gates"])
                for gate, cb in self._gate_checks.items():
                    cb.set_active(gate in gate_set)

        elif gen_name == "verse":
            if "form" in params:
                self._set_dd(self._verse_form, params["form"])
            if "theme" in params:
                self._verse_theme.set_text(params["theme"])
            if "count" in params:
                self._verse_count.set_value(int(params["count"]))

        elif gen_name == "palette":
            if "mood" in params:
                self._pal_mood.set_text(params["mood"])
            if "count" in params:
                self._pal_count.set_value(int(params["count"]))
            if "export_css" in params:
                self._pal_css.set_active(bool(params["export_css"]))

        elif gen_name == "ansi":
            if "subject" in params:
                self._ansi_subject.set_text(params["subject"])
            if "width" in params:
                self._ansi_width.set_value(int(params["width"]))
            if "colors" in params:
                self._set_dd(self._ansi_colors, params["colors"])
            if "ansi_style" in params:
                self._set_dd(self._ansi_style, params["ansi_style"])

        elif gen_name == "freeform":
            if "freeform" in params:
                self._free_tv.get_buffer().set_text(params["freeform"])

        elif gen_name == "animatediff":
            if "prompt" in params:
                self._ad_prompt.set_text(params["prompt"])
            if "negative_prompt" in params:
                self._ad_neg_prompt.set_text(params["negative_prompt"])
            if "mode" in params:
                self._set_dd(self._ad_mode, params["mode"])
            if "frames" in params:
                self._set_dd(self._ad_frames, str(params["frames"]))
            if "steps" in params:
                self._ad_steps.set_value(int(params["steps"]))
            if "seed" in params:
                self._ad_seed.set_value(int(params["seed"]))
            if "temporal_alpha" in params:
                self._ad_temporal_alpha.set_value(float(params["temporal_alpha"]))
            if "lightning" in params:
                self._ad_lightning.set_active(bool(params["lightning"]))

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_type_changed(self, dd: Gtk.DropDown, _pspec) -> None:
        item = dd.get_selected_item()
        if item is None:
            return
        name = item.get_string()
        self._controls_stack.set_visible_child_name(name)

    def _on_generate_clicked(self, _btn) -> None:
        item = self._type_dd.get_selected_item()
        if item is None:
            return
        gen_name = item.get_string()
        # Snapshot all widget state NOW on the main thread — GTK widgets must
        # never be accessed from a background thread (silent deadlock in GTK4).
        args = self._build_args(gen_name)
        self._gen_queue.append((gen_name, args))
        self._drain_queue()

    def _drain_queue(self) -> None:
        """Start the next queued generation if none is running; update button label."""
        if self._generating:
            n = len(self._gen_queue)
            self._gen_btn.set_label(f"Generating… (+{n})" if n else "Generating…")
            return
        if not self._gen_queue:
            self._gen_btn.set_label("✦ Generate")
            return
        gen_name, args = self._gen_queue.popleft()
        n = len(self._gen_queue)
        self._generating = True
        self._gen_btn.set_label(f"Generating… (+{n})" if n else "Generating…")
        self._set_status("Detecting model…")
        threading.Thread(
            target=self._run_generation,
            args=(gen_name, args),
            daemon=True,
        ).start()

    # Map artgen generator names to prompt_client source types so the prompt
    # engine pulls from the right word banks and LLM polishing style.
    _INSPIRE_SOURCE = {
        "animatediff": "animate",
        "landscape":   "video",
        "skyline":     "video",
        "constellation": "video",
        "verse":       "video",
        "palette":     "video",
        "ansi":        "image",
        "circuit":     "image",
        "geometric":   "image",
        "freeform":    "video",
    }

    def _on_inspire(self, gen_name: str, entry: Gtk.Entry) -> None:
        """Call prompt server in background; update entry on main thread."""
        seed = entry.get_text().strip()
        entry.set_sensitive(False)
        source = self._INSPIRE_SOURCE.get(gen_name, "video")

        def _bg():
            try:
                import prompt_client
                # Use the best available LLM — artgen server (8002) if running,
                # otherwise prompt-gen server (8001, Qwen3-0.6B).
                best_url, _ = artgen.detect_artgen_endpoint()
                if best_url:
                    prompt_client.configure_llm_url(best_url)
                result = prompt_client.generate_prompt(source=source, seed_text=seed)
            except Exception:
                try:
                    from word_banks import THEMES
                    result = random.choice(THEMES)
                except Exception:
                    result = seed  # no change
            GLib.idle_add(_done, result)

        def _done(text: str) -> None:
            entry.set_text(text)
            entry.set_sensitive(True)

        threading.Thread(target=_bg, daemon=True).start()

    def _on_srv_start(self, _btn) -> None:
        model = _dd_val(self._srv_model_dd)
        key = _MODEL_TO_KEY.get(model, "artgen-qwen3-8b")
        self._set_srv_status("starting…")
        threading.Thread(target=self._do_srv_start, args=(key,), daemon=True).start()

    def _on_srv_stop(self, _btn) -> None:
        # All artgen keys stop the same port-8002 container; use whichever is selected.
        model = _dd_val(self._srv_model_dd)
        key = _MODEL_TO_KEY.get(model, "artgen-qwen3-8b")
        self._set_srv_status("stopping…")
        threading.Thread(target=self._do_srv_stop, args=(key,), daemon=True).start()

    # ── Server management background threads ──────────────────────────────────

    def _do_srv_start(self, key: str) -> None:
        from server_manager import start as sm_start
        try:
            results = sm_start(key)
            rc = results[0].returncode if results else -1
            if rc == 0:
                GLib.idle_add(self._set_srv_status, "launched — waiting for health")
                # Schedule one extra health check after a short delay.
                GLib.timeout_add_seconds(8, lambda: (
                    threading.Thread(target=self._check_health_bg, daemon=True).start() or False
                ))
            else:
                stderr = (results[0].stderr or "").strip()
                GLib.idle_add(self._set_srv_status, f"start failed (rc={rc})")
                if stderr:
                    GLib.idle_add(self._set_srv_status, f"start failed: {stderr[:80]}")
        except Exception as e:
            GLib.idle_add(self._set_srv_status, f"start error: {e}")

    def _do_srv_stop(self, key: str) -> None:
        from server_manager import stop as sm_stop
        try:
            sm_stop(key)
            GLib.idle_add(self._set_srv_status, "stopped")
            GLib.idle_add(self._set_health, False)
        except Exception as e:
            GLib.idle_add(self._set_srv_status, f"stop error: {e}")

    # ── Health polling ────────────────────────────────────────────────────────

    def _poll_health(self) -> bool:
        threading.Thread(target=self._check_health_bg, daemon=True).start()
        return True  # GLib.SOURCE_CONTINUE — keep the timeout alive

    def _check_health_bg(self) -> None:
        from server_manager import is_healthy
        try:
            model = _dd_val(self._srv_model_dd)
            key = _MODEL_TO_KEY.get(model, "artgen-qwen3-8b")
            ok = is_healthy(key)
        except Exception:
            ok = False
        GLib.idle_add(self._set_health, ok)

    def _set_health(self, ok: bool) -> None:
        for dot in (self._health_dot, self._srv_btn_dot):
            dot.remove_css_class("artgen-health-ok")
            dot.remove_css_class("artgen-health-bad")
            dot.remove_css_class("artgen-health-unknown")
            dot.add_css_class("artgen-health-ok" if ok else "artgen-health-bad")
        if self._srv_status_lbl.get_label() in ("unknown", "running", "offline"):
            self._srv_status_lbl.set_label("running" if ok else "offline")

    def _set_srv_status(self, text: str) -> None:
        self._srv_status_lbl.set_label(text)

    # ── Background generation ─────────────────────────────────────────────────

    def _run_generation(self, gen_name: str, args) -> None:
        """Background thread: detect model → build prompt → call LLM → parse → save.

        *args* must be pre-built on the main thread via _build_args() — GTK
        widgets cannot be accessed safely from a background thread.
        AnimateDiff bypasses the LLM pipeline entirely and is handled separately.
        """
        if gen_name == "animatediff":
            try:
                self._run_animatediff(args)
            except Exception as e:
                GLib.idle_add(self._finish_error, f"AnimateDiff error: {e}")
            return

        try:
            gen = artgen.get(gen_name)

            # Prefer the dedicated artgen LLM (port 8002); fall back to the
            # prompt-gen server (port 8001, Qwen3-0.6B) so the tool works from
            # day one and automatically upgrades once a bigger model is started.
            base_url, model_id = artgen.detect_artgen_endpoint()
            if model_id is None:
                GLib.idle_add(self._finish_error,
                    "No LLM available for artgen generation.\n\n"
                    "Start an artgen server (for best results):\n"
                    "  tt-ctl start artgen-qwen3-8b\n\n"
                    "Or start the prompt server for basic generation:\n"
                    "  tt-ctl start prompt-server"
                )
                return

            GLib.idle_add(self._set_status, f"[{model_id}] generating…")

            # prompt_summary is stored in the MediaRecord for display purposes.
            # For multi-pass generators build_prompt() returns the first pass.
            try:
                prompt_summary = gen.build_prompt(args)
            except ValueError as e:
                GLib.idle_add(self._finish_error, f"Prompt error: {e}")
                return

            t0 = time.monotonic()
            GLib.idle_add(self._begin_llm_timer, t0)

            # Accumulate token usage across all LLM calls (multi-pass generators
            # call call_fn more than once; single-pass generators call it once).
            total_usage: dict = {"prompt_tokens": 0, "completion_tokens": 0}
            call_index = [0]

            # Verse generators stash a system prompt on args._verse_system.
            system_msg = getattr(args, "_verse_system", None)

            def call_fn(prompt, system=None, max_tokens=None):
                call_index[0] += 1
                raw_resp, usage = artgen.call_llm(
                    prompt, model_id, base_url + "/v1",
                    max_tokens=max_tokens or getattr(args, "max_tokens", 4096),
                    system=system or system_msg,
                )
                for k in total_usage:
                    total_usage[k] += usage.get(k, 0)
                return raw_resp

            try:
                artifact = gen.generate_artifact(args, call_fn)
            except Exception as e:
                GLib.idle_add(self._finish_error, f"LLM error: {e}")
                return

            elapsed = time.monotonic() - t0
            usage = total_usage
            prompt = prompt_summary

            # Save to artgen/ dir and record in MediaStore
            short_id = str(uuid.uuid4())[:8]
            ext = Path(gen.default_output()).suffix
            out_path = make_artgen_path(short_id, ext)
            out_path.write_text(artifact, encoding="utf-8")

            thumb_dir = out_path.parent / "thumbnails"
            thumb_path = thumb_dir / (out_path.stem + ".png")
            try:
                make_thumbnail(out_path, thumb_path)
            except Exception:
                thumb_path = Path("")

            params = vars(args).copy()
            params.pop("output", None)
            params.pop("max_tokens", None)
            params.pop("temperature", None)
            params["generation_seconds"] = int(elapsed)
            completion_tokens = usage.get("completion_tokens") or 0
            prompt_tokens = usage.get("prompt_tokens") or 0
            if completion_tokens:
                params["completion_tokens"] = completion_tokens
                params["prompt_tokens"] = prompt_tokens
                params["tokens_per_sec"] = round(completion_tokens / max(elapsed, 0.1), 1)

            rec = MediaRecord(
                id=str(uuid.uuid4()),
                media_type="artgen",
                created_at=datetime.now(timezone.utc).isoformat(),
                file_path=str(out_path),
                thumbnail_path=str(thumb_path) if thumb_path.exists() else "",
                prompt=prompt[:500],
                model_id=model_id,
                generator_type=gen_name,
                params=json.dumps({k: v for k, v in params.items()
                                   if isinstance(v, (str, int, float, bool, type(None)))}),
                starred=0,
            )
            _media_store.add(rec)
            _media_store.ensure_auto_playlists()

            GLib.idle_add(self._finish_success, artifact, str(out_path), rec)

        except Exception as e:
            GLib.idle_add(self._finish_error, f"Unexpected error: {e}")

    def _on_ad_chain_from_pick(self, _btn) -> None:
        """Open a FileDialog to pick a .pt latents file for chain-from."""
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gio as _Gio
        dlg = Gtk.FileDialog()
        dlg.set_title("Select chain latents (.pt)")
        ffilter = Gtk.FileFilter()
        ffilter.set_name("PyTorch latents (*.pt)")
        ffilter.add_pattern("*.pt")
        filters = _Gio.ListStore.new(Gtk.FileFilter)
        filters.append(ffilter)
        dlg.set_filters(filters)
        # Find the top-level window
        win = self.get_root()
        dlg.open(win, None, self._on_ad_chain_from_finish)

    def _on_ad_chain_from_finish(self, dlg, result) -> None:
        try:
            gfile = dlg.open_finish(result)
        except Exception:
            return
        if gfile:
            self._ad_chain_from.set_text(gfile.get_path())

    def _run_animatediff(self, args) -> None:
        """Background thread: run generate.py subprocess → GIF → MediaRecord."""
        import os
        import uuid as _uuid
        from datetime import datetime, timezone

        from artgen.generators.animatediff import check_hardware, run_subprocess, make_gif_thumbnail

        GLib.idle_add(self._set_status, "Checking Blackhole hardware…")

        ok, hw_msg = check_hardware()
        if not ok:
            GLib.idle_add(self._finish_error,
                f"AnimateDiff requires Blackhole hardware.\n{hw_msg}")
            return

        short_id = str(_uuid.uuid4())[:8]
        out_path = make_artgen_path(short_id, ".gif")

        GLib.idle_add(self._set_status, f"Starting AnimateDiff on Blackhole ({hw_msg})…")

        t0 = time.monotonic()

        def _on_progress(line: str) -> None:
            GLib.idle_add(self._set_status, line[:80])

        # Chain-save: auto-derive path from out_path if checkbox is checked
        chain_save_path = None
        if args.ad_chain_save:
            chain_save_path = str(out_path.with_suffix(".chain.pt"))

        success, err = run_subprocess(
            prompt=args.ad_prompt,
            out_path=out_path,
            mode=args.ad_mode,
            frames=args.ad_frames,
            steps=args.ad_steps,
            seed=args.ad_seed,
            negative_prompt=args.ad_neg_prompt,
            temporal_alpha=args.ad_temporal_alpha,
            lightning=args.ad_lightning,
            lightning_steps=args.ad_lightning_steps,
            device_id=args.ad_device_id,
            chain_from=args.ad_chain_from or None,
            chain_save=chain_save_path,
            chain_alpha=args.ad_chain_alpha,
            motion_adapter=args.ad_motion_adapter,
            motion_adapter_alpha=args.ad_injection_alpha,
            motion_adapter_skip=args.ad_motion_skip_keys,
            on_progress=_on_progress,
        )

        if not success:
            GLib.idle_add(self._finish_error, err)
            return

        elapsed_s = int(time.monotonic() - t0)

        thumb_path = out_path.parent / "thumbnails" / (out_path.stem + ".jpg")
        make_gif_thumbnail(out_path, thumb_path)

        params_d = {
            "prompt": args.ad_prompt,
            "negative_prompt": args.ad_neg_prompt,
            "mode": args.ad_mode,
            "frames": args.ad_frames,
            "steps": args.ad_steps,
            "seed": args.ad_seed,
            "temporal_alpha": args.ad_temporal_alpha,
            "lightning": args.ad_lightning,
            "motion_adapter": bool(args.ad_motion_adapter),
            "chain_from": args.ad_chain_from or None,
            "chain_save": chain_save_path,
            "generation_seconds": elapsed_s,
        }
        rec = MediaRecord(
            id=str(_uuid.uuid4()),
            media_type="artgen",
            created_at=datetime.now(timezone.utc).isoformat(),
            file_path=str(out_path),
            thumbnail_path=str(thumb_path) if thumb_path.exists() else "",
            prompt=args.ad_prompt[:500],
            model_id="animatediff-blackhole",
            generator_type="animatediff",
            params=json.dumps(params_d),
            starred=0,
        )
        _media_store.add(rec)
        _media_store.ensure_auto_playlists()

        GLib.idle_add(self._finish_success, "", str(out_path), rec)

    def _build_args(self, gen_name: str) -> types.SimpleNamespace:
        """Build an argparse-Namespace-compatible object from the current UI state."""
        args = types.SimpleNamespace()
        args.output = None
        # SVG generators produce dense output; give them more headroom.
        _SVG_TYPES = {"landscape", "skyline", "geometric", "circuit", "constellation"}
        args.max_tokens = 12288 if gen_name in _SVG_TYPES else 4096
        args.temperature = 0.7

        if gen_name == "landscape":
            args.palette = _dd_val(self._land_palette)
            args.mountains = self._land_mountains.get_active()
            args.clouds = self._land_clouds.get_active()
            args.stars = self._land_stars.get_active()
            args.glitch = self._land_glitch.get_active()
            args.glitch_seed = None

        elif gen_name == "skyline":
            args.era = _dd_val(self._sky_era)
            args.density = _dd_val(self._sky_density)
            args.sky = _dd_val(self._sky_sky)

        elif gen_name == "constellation":
            args.culture = _dd_val(self._con_culture)
            args.stars = int(self._con_stars.get_value())
            args.lore = self._con_lore.get_active()

        elif gen_name == "geometric":
            args.style = _dd_val(self._geo_style)
            args.geo_palette = _dd_val(self._geo_palette)
            args.complexity = _dd_val(self._geo_complexity)

        elif gen_name == "circuit":
            args.inputs = self._cir_inputs.get_text() or "A,B,C"
            args.gates = ",".join(
                k for k, cb in self._gate_checks.items() if cb.get_active()
            ) or "and,or"
            args.depth = int(self._cir_depth.get_value())
            args.circuit_style = _dd_val(self._cir_style)

        elif gen_name == "verse":
            args.form = _dd_val(self._verse_form)
            args.theme = self._verse_theme.get_text() or "the passage of time"
            args.count = int(self._verse_count.get_value())

        elif gen_name == "palette":
            args.mood = self._pal_mood.get_text() or "volcanic"
            args.count = int(self._pal_count.get_value())
            args.export_css = self._pal_css.get_active()

        elif gen_name == "ansi":
            args.subject = self._ansi_subject.get_text() or "a mountain at sunset"
            args.width = int(self._ansi_width.get_value())
            args.colors = _dd_val(self._ansi_colors)
            args.ansi_style = _dd_val(self._ansi_style)

        elif gen_name == "freeform":
            buf = self._free_tv.get_buffer()
            args.freeform = buf.get_text(
                buf.get_start_iter(), buf.get_end_iter(), False
            )

        elif gen_name == "animatediff":
            args.ad_prompt = self._ad_prompt.get_text() or "purple phosphor glow across distant mountains at 2am"
            args.ad_neg_prompt = self._ad_neg_prompt.get_text() or "blurry, low quality"
            args.ad_mode = _dd_val(self._ad_mode) or "blackhole"
            args.ad_frames = int(_dd_val(self._ad_frames) or "8")
            args.ad_steps = int(self._ad_steps.get_value())
            args.ad_seed = int(self._ad_seed.get_value())
            args.ad_temporal_alpha = round(self._ad_temporal_alpha.get_value(), 2)
            # Performance
            args.ad_lightning = self._ad_lightning.get_active()
            args.ad_lightning_steps = int(_dd_val(self._ad_lightning_steps) or "4")
            raw_device_id = int(self._ad_device_id.get_value())
            args.ad_device_id = raw_device_id if raw_device_id >= 0 else None
            # Chain
            args.ad_chain_from = self._ad_chain_from.get_text().strip()
            args.ad_chain_save = self._ad_chain_save.get_active()
            args.ad_chain_alpha = round(self._ad_chain_alpha.get_value(), 2)
            # MotionAdapter
            motion_on = self._ad_motion_adapter.get_active()
            args.ad_motion_adapter = "" if motion_on else None  # "" = use HF default
            args.ad_injection_alpha = round(self._ad_injection_alpha.get_value(), 2)
            skip_preset = _dd_val(self._ad_motion_skip) or "None (full quality)"
            if skip_preset == "Fast (skip up1 up2)":
                args.ad_motion_skip_keys = ["up1", "up2"]
            elif skip_preset == "Balanced (skip up2)":
                args.ad_motion_skip_keys = ["up2"]
            else:
                args.ad_motion_skip_keys = None

        return args

    # ── LLM elapsed-time ticker (main-thread only) ────────────────────────────

    def _begin_llm_timer(self, t0: float) -> None:
        self._llm_t0 = t0
        self._set_status("Calling LLM… 0s")
        self._llm_timer_id = GLib.timeout_add(500, self._tick_llm_timer)

    def _tick_llm_timer(self) -> bool:
        elapsed = int(time.monotonic() - self._llm_t0)
        self._set_status(f"Calling LLM… {elapsed}s")
        return GLib.SOURCE_CONTINUE

    def _cancel_llm_timer(self) -> int | None:
        """Stop the ticker and return elapsed seconds (or None if never started)."""
        elapsed = None
        if self._llm_timer_id is not None:
            GLib.source_remove(self._llm_timer_id)
            self._llm_timer_id = None
            elapsed = int(time.monotonic() - self._llm_t0)
        return elapsed

    # ── UI update callbacks (must only run on the GTK main thread) ────────────

    def _finish_success(self, artifact: str, out_path_str: str, rec: "MediaRecord | None" = None) -> None:
        elapsed = self._cancel_llm_timer()
        self._generating = False
        self._last_out_path = Path(out_path_str)
        suffix = f"  ({elapsed}s)" if elapsed is not None else ""
        self._set_status(f"Done  ({elapsed}s)" if elapsed is not None else "Done")

        # Push the new record into every live view that holds a record list.
        if rec is not None:
            self._gallery.prepend_record(rec)
            if self._watch._records:
                self._watch._records.insert(0, rec)
        else:
            self._gallery.refresh()
        # Switch to Gallery so the new item is immediately visible at the top.
        self._gallery_tab_btn.set_active(True)
        self._sub_stack.set_visible_child_name("gallery")
        self._gallery.scroll_to_top()

        # Drain any manually queued generations before auto-scheduling the next one.
        self._drain_queue()
        if self._auto_gen:
            self._auto_gen_error_streak = 0
            if not self._generating:  # don't schedule auto if a manual item just started
                self._auto_maybe_schedule()

    def _finish_error(self, msg: str) -> None:
        self._cancel_llm_timer()
        self._generating = False
        self._set_status(f"Error: {msg[:80]}")
        # Drain any manually queued generations first.
        self._drain_queue()
        if self._auto_gen and not self._generating:
            self._auto_gen_error_streak += 1
            if self._auto_gen_error_streak >= 3:
                self._auto_stop("3 errors in a row — auto-generate paused")
                try:
                    dlg = Gtk.AlertDialog.new(
                        "Auto-generate stopped after 3 consecutive failures.\n"
                        "Check that a language model is running (tt-ctl start artgen-qwen3-8b or tt-ctl start prompt-server)."
                    )
                    dlg.show(self.get_root())
                except AttributeError:
                    pass  # GTK < 4.10; status bar message is sufficient
            else:
                self._auto_maybe_schedule()

    def _set_status(self, text: str) -> None:
        self._status_lbl.set_label(text)


    # ── Sub-tab wiring ────────────────────────────────────────────────────────

    def _on_tab_toggled(self, btn: Gtk.ToggleButton, tab: str) -> None:
        if not btn.get_active():
            return
        if tab == "gallery":
            self._gallery.refresh()
        elif tab == "watch":
            # Direct tab click: load all records if the slideshow hasn't been started yet.
            if not self._watch._records:
                records = _media_store.query(media_type="artgen")
                if records:
                    self._watch.start(records)
        self._sub_stack.set_visible_child_name(tab)

    def _switch_tab(self, tab: str) -> None:
        if tab == "gallery":
            self._gallery_tab_btn.set_active(True)
            self._gallery.refresh()
        self._sub_stack.set_visible_child_name(tab)

    def _on_gallery_card_activated(self, media_id: str) -> None:
        records = _media_store.query(
            media_type="artgen",
            generator_type=self._gallery._active_filter,
        )
        self._detail_source = "gallery"
        self._detail.set_back_label("← Gallery")
        self._detail.show_record(media_id, records)
        self._sub_stack.set_visible_child_name("detail")

    def _on_detail_back(self) -> None:
        self._gallery_tab_btn.set_active(True)
        self._sub_stack.set_visible_child_name("gallery")

    def _on_gallery_card_deleted(self, media_id: str) -> None:
        """Called when a card is deleted via the gallery hover button."""
        if self._watch._records:
            self._watch._records = [r for r in self._watch._records if r.id != media_id]

    def _on_detail_deleted(self, media_id: str) -> None:
        self._gallery.refresh()
        # Remove from watch queue so the slideshow doesn't try to display it
        if self._watch._records:
            self._watch._records = [r for r in self._watch._records if r.id != media_id]

    def _on_remix_record(self, rec: "MediaRecord") -> None:
        """Forward the remix request to the MainWindow callback if wired."""
        if self.on_remix:
            self.on_remix(rec)

    def _on_watch_requested(self, generator_type: str | None) -> None:
        records = _media_store.query(media_type="artgen", generator_type=generator_type)
        if not records:
            return
        self._watch.start(records)
        self._watch_tab_btn.set_active(True)
        self._sub_stack.set_visible_child_name("watch")

    def _on_watch_exit(self) -> None:
        self._watch.stop()
        self._gallery_tab_btn.set_active(True)
        self._sub_stack.set_visible_child_name("gallery")


    # ── Auto-generate section UI builder ─────────────────────────────────────

    def _build_auto_section(self, left_box: Gtk.Box) -> None:
        """Append the collapsible Auto-Generate section to the left control pane."""
        left_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Header: label on left, switch on right
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hdr.set_margin_start(12)
        hdr.set_margin_end(12)
        hdr.set_margin_top(8)
        hdr.set_margin_bottom(8)
        hdr_lbl = Gtk.Label(label="Auto-Generate")
        hdr_lbl.set_hexpand(True)
        hdr_lbl.set_xalign(0)
        hdr.append(hdr_lbl)
        self._auto_switch = Gtk.Switch()
        self._auto_switch.set_active(False)
        hdr.append(self._auto_switch)
        left_box.append(hdr)

        left_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Revealer wraps the expanded body (revealed when switch ON)
        self._auto_revealer = Gtk.Revealer()
        self._auto_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._auto_revealer.set_transition_duration(150)
        self._auto_revealer.set_reveal_child(False)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.set_margin_start(12)
        body.set_margin_end(12)
        body.set_margin_top(8)
        body.set_margin_bottom(10)

        # Type checkboxes
        body.append(_section_lbl("types"))
        type_flow = Gtk.FlowBox()
        type_flow.set_max_children_per_line(3)
        type_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        type_flow.set_column_spacing(4)
        type_flow.set_row_spacing(2)
        # animatediff is hidden from the manual type picker but kept in the auto pool —
        # it runs via _run_animatediff() regardless of where the picker sits.
        _AUTO_OFF_BY_DEFAULT: set = set()
        self._auto_type_checks: dict[str, Gtk.CheckButton] = {}
        for gname in artgen.all_names():
            cb = Gtk.CheckButton.new_with_label(gname)
            cb.set_active(gname not in _AUTO_OFF_BY_DEFAULT)
            cb.connect("toggled", self._on_auto_type_toggled)
            self._auto_type_checks[gname] = cb
            type_flow.append(cb)
        body.append(type_flow)

        # Mood seed entry
        self._auto_seed_entry = Gtk.Entry()
        self._auto_seed_entry.set_placeholder_text(
            "e.g. 'industrial decay' — blank = pure chaos"
        )
        body.append(_row("Mood seed", self._auto_seed_entry, label_width=70))

        # Delay row (visible when not actively running)
        delay_val = float(server_config.get("artgen_auto", "delay") or 3)
        self._auto_delay_spin = _spin(0, 30, 1, delay_val)
        self._auto_delay_spin.connect("value-changed", self._on_auto_delay_changed)
        delay_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        delay_lbl = Gtk.Label(label="Delay")
        delay_lbl.set_xalign(0)
        delay_lbl.set_size_request(70, -1)
        delay_box.append(delay_lbl)
        self._auto_delay_spin.set_hexpand(True)
        delay_box.append(self._auto_delay_spin)
        delay_box.append(Gtk.Label(label="s"))
        self._auto_delay_row = delay_box
        body.append(delay_box)

        # Countdown row (visible while counting down / inspiring / waiting)
        countdown_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._auto_progress = Gtk.ProgressBar()
        self._auto_progress.set_hexpand(True)
        countdown_box.append(self._auto_progress)
        self._auto_status_lbl = Gtk.Label(label="")
        self._auto_status_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        countdown_box.append(self._auto_status_lbl)
        stop_btn = Gtk.Button(label="■ Stop")
        stop_btn.connect("clicked", lambda _: self._auto_stop())
        countdown_box.append(stop_btn)
        self._auto_countdown_row = countdown_box
        countdown_box.set_visible(False)
        body.append(countdown_box)

        self._auto_revealer.set_child(body)
        left_box.append(self._auto_revealer)

        # Wire switch signal last (widgets must exist before handler references them)
        self._auto_switch_handler = self._auto_switch.connect(
            "notify::active", self._on_auto_switch_changed
        )

    # ── Auto-generate signal handlers ────────────────────────────────────────

    def _on_auto_switch_changed(self, sw: Gtk.Switch, _pspec) -> None:
        active = sw.get_active()
        self._auto_revealer.set_reveal_child(active)
        if active:
            self._auto_gen = True
            if not self._generating:
                self._auto_maybe_schedule()
        else:
            self._auto_stop()

    def _on_auto_type_toggled(self, _cb: Gtk.CheckButton) -> None:
        if not self._auto_gen:
            return
        checked = [n for n, c in self._auto_type_checks.items() if c.get_active()]
        if not checked:
            self._auto_stop("No types selected — auto-generate off")

    def _on_auto_delay_changed(self, spin: Gtk.SpinButton) -> None:
        server_config.set("artgen_auto", "delay", int(spin.get_value()))

    # ── Auto-generate logic ───────────────────────────────────────────────────

    def _auto_maybe_schedule(self) -> None:
        """Start the countdown for the next auto-fire. Runs on the GTK main thread."""
        if not self._auto_gen:
            return
        checked = [n for n, cb in self._auto_type_checks.items() if cb.get_active()]
        if not checked:
            self._auto_stop("No types selected — auto-generate off")
            return
        delay = float(self._auto_delay_spin.get_value())
        self._auto_gen_countdown = delay
        # Switch to countdown row
        self._auto_delay_row.set_visible(False)
        self._auto_countdown_row.set_visible(True)
        secs = max(1, int(delay) + 1)
        self._auto_status_lbl.set_label(f"Next in {secs}s")
        self._auto_progress.set_fraction(1.0)
        self._auto_gen_timer_id = GLib.timeout_add(100, self._auto_tick)

    def _auto_tick(self) -> bool:
        """100 ms heartbeat — drives the countdown bar. GTK main thread only."""
        if not self._auto_gen:
            self._auto_gen_timer_id = None
            return GLib.SOURCE_REMOVE
        self._auto_gen_countdown -= 0.1
        delay = float(self._auto_delay_spin.get_value())
        frac = max(0.0, self._auto_gen_countdown / delay) if delay > 0 else 0.0
        self._auto_progress.set_fraction(frac)
        secs = max(1, int(self._auto_gen_countdown) + 1)
        self._auto_status_lbl.set_label(f"Next in {secs}s")
        if self._auto_gen_countdown <= 0:
            self._auto_gen_timer_id = None
            self._auto_fire()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _auto_fire(self) -> None:
        """Kick off one auto-generation cycle. Runs on the GTK main thread."""
        if not self._auto_gen:
            return
        if self._generating:
            # Previous generation still running — check again in 1s
            self._auto_gen_countdown = 1.0
            self._auto_status_lbl.set_label("Waiting for generation…")
            self._auto_gen_timer_id = GLib.timeout_add(100, self._auto_tick)
            return
        checked = [n for n, cb in self._auto_type_checks.items() if cb.get_active()]
        if not checked:
            self._auto_stop("No types selected — auto-generate off")
            return
        gen_name = random.choice(checked)

        # Switch the type dropdown + controls stack — but only for picker-visible types.
        # Hidden generators (e.g. animatediff) skip the dropdown update so the picker
        # doesn't jump to an index that doesn't match any visible entry.
        if gen_name not in self._HIDDEN_GENERATORS:
            gen_names = artgen.all_names()
            if gen_name in gen_names:
                self._type_dd.set_selected(gen_names.index(gen_name))
                self._controls_stack.set_visible_child_name(gen_name)

        # Randomise params for this type (writes to widgets on the main thread)
        self._auto_apply_random_params(gen_name)
        mood_seed = self._auto_seed_entry.get_text().strip()

        # Disable auto controls while inspiring/generating (Stop button stays live)
        for cb in self._auto_type_checks.values():
            cb.set_sensitive(False)
        self._auto_seed_entry.set_sensitive(False)
        self._auto_delay_spin.set_sensitive(False)

        self._auto_status_lbl.set_label("Inspiring…")
        self._auto_progress.set_fraction(0.0)

        threading.Thread(
            target=self._auto_do_inspire,
            args=(gen_name, mood_seed),
            daemon=True,
        ).start()

    def _auto_do_inspire(self, gen_name: str, mood_seed: str) -> None:
        """Background thread: call prompt_client; fall back to word bank."""
        theme = ""
        try:
            from prompt_client import generate_prompt, configure_llm_url
            # Use the best available LLM for richer inspiration; artgen server
            # (8002) if running, otherwise prompt-gen server (8001, Qwen3-0.6B).
            best_url, _ = artgen.detect_artgen_endpoint()
            if best_url:
                configure_llm_url(best_url)
            seed = mood_seed if mood_seed else ""
            theme = generate_prompt("artgen", seed_text=seed) or ""
        except Exception:
            pass
        if not theme:
            try:
                import word_banks
                theme = random.choice(word_banks.SUBJECTS)
            except Exception:
                theme = mood_seed or "a mysterious vision"
        GLib.idle_add(self._auto_fire_with_theme, gen_name, theme)

    def _auto_fire_with_theme(self, gen_name: str, theme: str) -> None:
        """Write inspire result into widgets and kick off generation. GTK main thread."""
        self._auto_restore_controls()
        if not self._auto_gen:
            return
        # If the user started a manual generation while we were inspiring, drop this
        # auto cycle — _finish_success will call _auto_maybe_schedule for the next one.
        if self._generating:
            return

        # Types that accept free-form text from Inspire
        _TEXT_TYPES = {"verse", "palette", "ansi", "freeform", "animatediff"}
        if gen_name == "verse":
            self._verse_theme.set_text(theme)
        elif gen_name == "palette":
            self._pal_mood.set_text(theme)
        elif gen_name == "ansi":
            self._ansi_subject.set_text(theme)
        elif gen_name == "freeform":
            self._free_tv.get_buffer().set_text(theme)
        elif gen_name == "animatediff":
            self._ad_prompt.set_text(theme)
        else:
            # Visual types: show the inspiration in the status bar
            self._set_status(f"Inspired: {theme[:60]}")

        # Collect widget state before handing off to the background thread
        args = self._build_args(gen_name)
        self._generating = True
        self._gen_btn.set_label("Generating…")
        if gen_name not in _TEXT_TYPES:
            self._set_status("Detecting model…")
        threading.Thread(
            target=self._run_generation,
            args=(gen_name, args),
            daemon=True,
        ).start()

    def _auto_stop(self, reason: str = "") -> None:
        """Turn off auto-generate. Safe to call from any state."""
        self._auto_gen = False
        # Block the switch signal to avoid reentrant _on_auto_switch_changed
        self._auto_switch.handler_block(self._auto_switch_handler)
        self._auto_switch.set_active(False)
        self._auto_switch.handler_unblock(self._auto_switch_handler)
        self._auto_revealer.set_reveal_child(False)
        if self._auto_gen_timer_id is not None:
            GLib.source_remove(self._auto_gen_timer_id)
            self._auto_gen_timer_id = None
        self._auto_progress.set_fraction(0.0)
        self._auto_status_lbl.set_label("")
        self._auto_delay_row.set_visible(True)
        self._auto_countdown_row.set_visible(False)
        self._auto_restore_controls()
        if reason:
            self._set_status(reason)

    def _auto_restore_controls(self) -> None:
        """Re-enable auto-gen controls after inspire/generation completes."""
        for cb in self._auto_type_checks.values():
            cb.set_sensitive(True)
        self._auto_seed_entry.set_sensitive(True)
        self._auto_delay_spin.set_sensitive(True)

    # ── Auto-generate random parameter application ────────────────────────────

    def _set_dd(self, dd: Gtk.DropDown, value: str) -> None:
        """Set a DropDown to a named string value (no-op if value not in model)."""
        model = dd.get_model()
        for i in range(model.get_n_items()):
            if model.get_string(i) == value:
                dd.set_selected(i)
                return

    def _auto_apply_random_params(self, gen_name: str) -> None:
        """Randomise the GTK widgets for gen_name. Runs on the GTK main thread."""
        if gen_name == "landscape":
            self._set_dd(
                self._land_palette,
                random.choice(["sunset", "blue", "purple", "red", "orange"]),
            )
            self._land_mountains.set_active(random.choice([True, False]))
            self._land_clouds.set_active(random.choice([True, False]))
            self._land_stars.set_active(random.choice([True, False]))
            self._land_glitch.set_active(random.random() < 0.2)

        elif gen_name == "skyline":
            self._set_dd(self._sky_era, random.choice(["modern", "retro", "futuristic"]))
            self._set_dd(self._sky_density, random.choice(["low", "medium", "high"]))
            sky_model = self._sky_sky.get_model()
            sky_opts = [sky_model.get_string(i) for i in range(sky_model.get_n_items())]
            self._set_dd(self._sky_sky, random.choice(sky_opts))

        elif gen_name == "verse":
            self._set_dd(self._verse_form, random.choice(["haiku", "lore", "epitaph", "couplet"]))
            self._verse_count.set_value(random.randint(1, 3))
            # theme written by _auto_fire_with_theme after Inspire

        elif gen_name == "constellation":
            self._set_dd(
                self._con_culture,
                random.choice(["invented", "norse", "greek", "random"]),
            )
            self._con_stars.set_value(random.randint(8, 20))
            self._con_lore.set_active(random.choice([True, False]))

        elif gen_name == "geometric":
            self._set_dd(
                self._geo_palette,
                random.choice(["teal", "mono", "ember", "forest"]),
            )
            self._set_dd(self._geo_complexity, random.choice(["low", "high"]))
            self._set_dd(
                self._geo_style,
                random.choice(["mondrian", "circuit", "recursive", "weave"]),
            )

        elif gen_name == "circuit":
            self._set_dd(self._cir_style, random.choice(["clean", "neon", "paper"]))
            letters = random.sample(list("ABCDEFGH"), random.randint(2, 4))
            self._cir_inputs.set_text(",".join(letters))
            gates_pool = ["and", "or", "not", "xor", "nand", "nor"]
            chosen = set(random.sample(gates_pool, random.randint(2, 3)))
            for gate, cb in self._gate_checks.items():
                cb.set_active(gate in chosen)
            self._cir_depth.set_value(random.choice([1, 2, 3]))

        elif gen_name == "ansi":
            self._ansi_width.set_value(80)
            clr_model = self._ansi_colors.get_model()
            clr_opts = [clr_model.get_string(i) for i in range(clr_model.get_n_items())]
            self._set_dd(self._ansi_colors, random.choice(clr_opts))
            sty_model = self._ansi_style.get_model()
            sty_opts = [sty_model.get_string(i) for i in range(sty_model.get_n_items())]
            self._set_dd(self._ansi_style, random.choice(sty_opts))
            # subject written by _auto_fire_with_theme after Inspire

        elif gen_name == "palette":
            self._pal_count.set_value(random.randint(4, 6))
            # mood written by _auto_fire_with_theme after Inspire

        elif gen_name == "animatediff":
            self._set_dd(self._ad_frames, "8")
            self._ad_steps.set_value(25)
            self._ad_seed.set_value(random.randint(0, 9999))
            # prompt written by _auto_fire_with_theme after Inspire

    # ── Public API for context-aware menu bar ─────────────────────────────────

    def toggle_auto_gen(self) -> bool:
        """Toggle auto-generate on/off. Returns the new state (True = enabled).

        Mirrors _on_auto_switch_changed. Blocks/unblocks the Switch signal
        handler to avoid re-entrancy when syncing the widget state.
        """
        if self._auto_gen:
            self._auto_stop("menu toggle")
        else:
            self._auto_gen = True
            self._auto_maybe_schedule()
        if hasattr(self, "_auto_switch") and hasattr(self, "_auto_switch_handler"):
            self._auto_switch.handler_block(self._auto_switch_handler)
            self._auto_switch.set_active(self._auto_gen)
            self._auto_switch.handler_unblock(self._auto_switch_handler)
        return self._auto_gen

    def get_auto_gen_delay(self) -> int:
        """Return the current auto-generate delay in seconds."""
        val = server_config.get("artgen_auto", "delay")
        return int(val) if val is not None else 3

    def set_auto_gen_delay(self, seconds: int) -> None:
        """Persist a new auto-generate delay. Takes effect on the next countdown cycle."""
        server_config.set("artgen_auto", "delay", seconds)

    # ── Remix support ─────────────────────────────────────────────────────────

    def set_generator(self, name: str) -> None:
        """Switch the generator type dropdown to *name* (if present in the picker).

        Called by MainWindow._dispatch_remix when an artgen target is chosen in
        RemixPopover. Updates both the dropdown selection and the controls stack
        so the correct parameter widgets are visible.
        """
        import artgen
        # Use the same filtered list the dropdown was built from so index
        # matches the actual dropdown row (hidden generators are excluded).
        gen_names = [n for n in artgen.all_names() if n not in self._HIDDEN_GENERATORS and n not in (set(_settings.get("hidden_plugins") or []))]
        if name in gen_names:
            self._type_dd.set_selected(gen_names.index(name))
            self._controls_stack.set_visible_child_name(name)

    def set_theme(self, theme: str) -> None:
        """Pre-fill the theme/subject field for the currently selected generator.

        Walks the active controls page for the first Gtk.Entry widget and sets
        its text to *theme*. Silently does nothing if no Entry is found (e.g.
        animatediff, which has no free-text theme field).

        This matches the behavior of _auto_fire_with_theme but operates on the
        currently-visible generator instead of a chosen one.
        """
        child = self._controls_stack.get_visible_child()
        if child is None:
            return

        def _find_entry(widget):
            """Depth-first search for the first Gtk.Entry in the widget tree."""
            if isinstance(widget, Gtk.Entry):
                return widget
            w = widget.get_first_child()
            while w:
                found = _find_entry(w)
                if found:
                    return found
                w = w.get_next_sibling()
            return None

        entry = _find_entry(child)
        if entry:
            entry.set_text(theme)

        # freeform: text written entirely by _auto_fire_with_theme after Inspire
