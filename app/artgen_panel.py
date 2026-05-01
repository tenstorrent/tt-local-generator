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
from datetime import datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import GLib, Gtk, Pango

import artgen
from media_store import media_store as _media_store, MediaRecord, make_artgen_path, make_thumbnail
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
    "Qwen3-8B":             "artgen-qwen3-8b",
    "Llama-3.1-8B-Instruct": "artgen-llama-3.1-8b",
    "Qwen2.5-7B-Instruct":  "artgen-qwen2.5-7b",
}
_ARTGEN_MODELS = list(_MODEL_TO_KEY)


class ArtgenPanel(Gtk.Box):
    """
    Two-column artgen UI: scrollable controls (left) + SVG/text preview (right).
    Drop into a Gtk.Stack as the named child "artgen".
    """

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._generating: bool = False
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

        self._create_tab_btn = Gtk.ToggleButton(label="✦ Create")
        self._create_tab_btn.set_active(True)
        self._create_tab_btn.add_css_class("artgen-subnav-btn")
        self._create_tab_btn.connect("toggled", self._on_tab_toggled, "create")

        self._gallery_tab_btn = Gtk.ToggleButton(label="▦ Gallery")
        self._gallery_tab_btn.add_css_class("artgen-subnav-btn")
        self._gallery_tab_btn.connect("toggled", self._on_tab_toggled, "gallery")
        self._gallery_tab_btn.set_group(self._create_tab_btn)

        self._watch_tab_btn = Gtk.ToggleButton(label="▶ Watch")
        self._watch_tab_btn.add_css_class("artgen-subnav-btn")
        self._watch_tab_btn.connect("toggled", self._on_tab_toggled, "watch")
        self._watch_tab_btn.set_group(self._create_tab_btn)

        nav.append(self._create_tab_btn)
        nav.append(self._gallery_tab_btn)
        nav.append(self._watch_tab_btn)
        self.append(nav)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Sub-tab stack ─────────────────────────────────────────────────────
        self._sub_stack = Gtk.Stack()
        self._sub_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._sub_stack.set_transition_duration(120)
        self._sub_stack.set_hexpand(True)
        self._sub_stack.set_vexpand(True)

        create_pane = self._build_create_pane()
        self._sub_stack.add_named(create_pane, "create")

        self._gallery = ArtgenGallery()
        self._gallery.on_card_activated = self._on_gallery_card_activated
        self._gallery.on_watch_requested = self._on_watch_requested
        self._sub_stack.add_named(self._gallery, "gallery")

        # Detail view navigated to from gallery (not a top-level tab button)
        self._detail = ArtgenDetail()
        self._detail.on_back = self._on_detail_back
        self._detail.on_deleted = self._on_detail_deleted
        self._sub_stack.add_named(self._detail, "detail")

        self._watch = ArtgenWatch()
        self._watch.on_exit = self._on_watch_exit
        self._sub_stack.add_named(self._watch, "watch")

        self._sub_stack.set_visible_child_name("create")
        self.append(self._sub_stack)

    def _build_create_pane(self) -> Gtk.Box:
        """Two-column Create tab: controls (left 240px) + latest-generations mini-grid (right)."""
        pane = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        pane.set_hexpand(True)
        pane.set_vexpand(True)

        # ── Left: controls ────────────────────────────────────────────────────
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        left_box.set_size_request(240, -1)
        left_box.add_css_class("artgen-ctrl-pane")

        type_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        type_bar.set_margin_start(12); type_bar.set_margin_end(12)
        type_bar.set_margin_top(10); type_bar.set_margin_bottom(6)
        type_lbl = _section_lbl("type")
        type_lbl.set_size_request(44, -1)
        type_bar.append(type_lbl)
        gen_names = artgen.all_names()
        self._type_dd = _dd(gen_names, "landscape")
        self._type_dd.set_hexpand(True)
        self._type_dd.connect("notify::selected", self._on_type_changed)
        type_bar.append(self._type_dd)
        left_box.append(type_bar)
        left_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self._controls_stack = Gtk.Stack()
        self._controls_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._controls_stack.set_transition_duration(80)
        self._controls_stack.set_margin_start(12); self._controls_stack.set_margin_end(12)
        self._controls_stack.set_margin_top(10); self._controls_stack.set_margin_bottom(6)
        for name in gen_names:
            self._controls_stack.add_named(self._build_controls_page(name), name)
        self._controls_stack.set_visible_child_name("landscape")
        ctrl_scroll = Gtk.ScrolledWindow()
        ctrl_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        ctrl_scroll.set_vexpand(True)
        ctrl_scroll.set_child(self._controls_stack)
        left_box.append(ctrl_scroll)
        left_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        srv_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        srv_box.set_margin_start(12); srv_box.set_margin_end(12)
        srv_box.set_margin_top(8); srv_box.set_margin_bottom(4)
        srv_box.append(_section_lbl("server"))
        self._srv_model_dd = _dd(_ARTGEN_MODELS, "Qwen3-8B")
        srv_box.append(_row("Model", self._srv_model_dd, label_width=46))
        srv_btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._srv_start_btn = Gtk.Button(label="Start")
        self._srv_start_btn.add_css_class("artgen-srv-start-btn")
        self._srv_start_btn.connect("clicked", self._on_srv_start)
        srv_btn_row.append(self._srv_start_btn)
        self._srv_stop_btn = Gtk.Button(label="Stop")
        self._srv_stop_btn.add_css_class("artgen-srv-stop-btn")
        self._srv_stop_btn.connect("clicked", self._on_srv_stop)
        srv_btn_row.append(self._srv_stop_btn)
        self._health_dot = Gtk.Label(label="●")
        self._health_dot.set_margin_start(4)
        self._health_dot.add_css_class("artgen-health-unknown")
        srv_btn_row.append(self._health_dot)
        self._srv_status_lbl = Gtk.Label(label="unknown")
        self._srv_status_lbl.set_xalign(0)
        self._srv_status_lbl.add_css_class("artgen-status")
        self._srv_status_lbl.set_hexpand(True)
        srv_btn_row.append(self._srv_status_lbl)
        srv_box.append(srv_btn_row)
        left_box.append(srv_box)

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        footer.set_margin_start(12); footer.set_margin_end(12)
        footer.set_margin_top(10); footer.set_margin_bottom(12)
        self._gen_btn = Gtk.Button(label="✦ Generate")
        self._gen_btn.add_css_class("artgen-generate-btn")
        self._gen_btn.connect("clicked", self._on_generate_clicked)
        footer.append(self._gen_btn)
        self._status_lbl = Gtk.Label(label="Ready — choose a type and click Generate")
        self._status_lbl.set_xalign(0)
        self._status_lbl.add_css_class("artgen-status")
        self._status_lbl.set_wrap(True)
        self._status_lbl.set_max_width_chars(32)
        footer.append(self._status_lbl)
        left_box.append(footer)

        # Auto-generate collapsible section
        self._build_auto_section(left_box)

        pane.append(left_box)
        pane.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # ── Right: latest-generations mini-grid ───────────────────────────────
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right_box.set_hexpand(True)
        right_box.set_vexpand(True)
        right_box.set_margin_start(12); right_box.set_margin_end(12)
        right_box.set_margin_top(10); right_box.set_margin_bottom(10)

        mini_hdr = Gtk.Label(label="LATEST GENERATIONS — click any to go to Gallery")
        mini_hdr.set_xalign(0)
        mini_hdr.add_css_class("section-label")
        right_box.append(mini_hdr)

        self._mini_flow = Gtk.FlowBox()
        self._mini_flow.set_max_children_per_line(4)
        self._mini_flow.set_min_children_per_line(2)
        self._mini_flow.set_homogeneous(True)
        self._mini_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._mini_flow.set_row_spacing(6)
        self._mini_flow.set_column_spacing(6)
        self._mini_flow.set_vexpand(True)
        self._mini_flow.connect("child-activated", self._on_mini_card_activated)
        right_box.append(self._mini_flow)

        self._view_all_btn = Gtk.Button(label="→ View all in Gallery")
        self._view_all_btn.add_css_class("flat")
        self._view_all_btn.connect("clicked", lambda _: self._switch_tab("gallery"))
        right_box.append(self._view_all_btn)

        pane.append(right_box)
        self._refresh_mini_grid()
        return pane

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

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_type_changed(self, dd: Gtk.DropDown, _pspec) -> None:
        item = dd.get_selected_item()
        if item is None:
            return
        name = item.get_string()
        self._controls_stack.set_visible_child_name(name)

    def _on_generate_clicked(self, _btn) -> None:
        if self._generating:
            return
        item = self._type_dd.get_selected_item()
        if item is None:
            return
        gen_name = item.get_string()
        # Gather all widget state HERE on the main thread — GTK widgets must
        # never be accessed from a background thread (silent deadlock in GTK4).
        args = self._build_args(gen_name)
        self._generating = True
        self._gen_btn.set_sensitive(False)
        self._gen_btn.set_label("Generating…")
        self._set_status("Detecting model on port 8002…")
        threading.Thread(
            target=self._run_generation,
            args=(gen_name, args),
            daemon=True,
        ).start()

    def _on_inspire(self, gen_name: str, entry: Gtk.Entry) -> None:
        """Call prompt server in background; update entry on main thread."""
        seed = entry.get_text().strip()
        entry.set_sensitive(False)

        def _bg():
            try:
                import prompt_client
                result = prompt_client.generate_prompt(source="artgen", seed_text=seed)
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
            ok = is_healthy("artgen-qwen3-8b")
        except Exception:
            ok = False
        GLib.idle_add(self._set_health, ok)

    def _set_health(self, ok: bool) -> None:
        self._health_dot.remove_css_class("artgen-health-ok")
        self._health_dot.remove_css_class("artgen-health-bad")
        self._health_dot.remove_css_class("artgen-health-unknown")
        self._health_dot.add_css_class("artgen-health-ok" if ok else "artgen-health-bad")
        if self._srv_status_lbl.get_label() in ("unknown", "running", "offline"):
            self._srv_status_lbl.set_label("running" if ok else "offline")

    def _set_srv_status(self, text: str) -> None:
        self._srv_status_lbl.set_label(text)

    # ── Background generation ─────────────────────────────────────────────────

    def _run_generation(self, gen_name: str, args) -> None:
        """Background thread: detect model → build prompt → call LLM → parse → save.

        *args* must be pre-built on the main thread via _build_args() — GTK
        widgets cannot be accessed safely from a background thread.
        """
        try:
            gen = artgen.get(gen_name)
            base_url = server_config.base_url("artgen")

            model_id = artgen.detect_model(base_url + "/v1")
            if model_id is None:
                GLib.idle_add(self._finish_error,
                    f"No chat model detected at {base_url}/v1/models\n\n"
                    "artgen needs a chat/text LLM on port 8002.\n"
                    "Start one:\n"
                    "  python3 app/prompt_server.py --port 8002\n"
                    "  vllm serve <model> --port 8002\n\n"
                    "Or override the port in Server Settings."
                )
                return

            GLib.idle_add(self._set_status, f"[{model_id}] building prompt…")

            try:
                prompt = gen.build_prompt(args)
            except ValueError as e:
                GLib.idle_add(self._finish_error, f"Prompt error: {e}")
                return
            t0 = time.monotonic()
            GLib.idle_add(self._begin_llm_timer, t0)

            # Verse generator stashes a system prompt on args._verse_system
            system_msg = getattr(args, "_verse_system", None)
            try:
                raw = artgen.call_llm(
                    prompt, model_id, base_url + "/v1",
                    system=system_msg,
                )
            except Exception as e:
                GLib.idle_add(self._finish_error, f"LLM error: {e}")
                return

            GLib.idle_add(self._set_status, "Parsing output…")

            try:
                artifact = gen.parse_output(raw, args)
            except ValueError as e:
                GLib.idle_add(self._finish_error,
                    f"Parse error: {e}\n\nRaw output preview:\n{raw[:600]}"
                )
                return

            artifact = gen.post_process(artifact, args)

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
            params["generation_seconds"] = int(time.monotonic() - t0)

            rec = MediaRecord(
                id=str(uuid.uuid4()),
                media_type="artgen",
                created_at=datetime.now().isoformat(),
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

            GLib.idle_add(self._finish_success, artifact, str(out_path), rec.id)

        except Exception as e:
            GLib.idle_add(self._finish_error, f"Unexpected error: {e}")

    def _build_args(self, gen_name: str) -> types.SimpleNamespace:
        """Build an argparse-Namespace-compatible object from the current UI state."""
        args = types.SimpleNamespace()
        args.output = None
        args.max_tokens = 4096
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

    def _finish_success(self, artifact: str, out_path_str: str, rec_id: str = "") -> None:
        elapsed = self._cancel_llm_timer()
        self._generating = False
        self._gen_btn.set_sensitive(True)
        self._gen_btn.set_label("✦ Generate")
        self._last_out_path = Path(out_path_str)
        suffix = f"  ({elapsed}s)" if elapsed is not None else ""
        self._set_status(f"Saved → {out_path_str}{suffix}")
        self._refresh_mini_grid()
        if self._auto_gen:
            self._auto_gen_error_streak = 0
            self._auto_maybe_schedule()

    def _finish_error(self, msg: str) -> None:
        self._cancel_llm_timer()
        self._generating = False
        self._gen_btn.set_sensitive(True)
        self._gen_btn.set_label("✦ Generate")
        self._set_status(f"Error: {msg[:80]}")
        if self._auto_gen:
            self._auto_gen_error_streak += 1
            if self._auto_gen_error_streak >= 3:
                self._auto_stop("3 errors in a row — auto-generate paused")
                try:
                    dlg = Gtk.AlertDialog.new(
                        "Auto-generate stopped after 3 consecutive failures.\n"
                        "Check that the artgen server is running on port 8002."
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
        self._sub_stack.set_visible_child_name(tab)

    def _switch_tab(self, tab: str) -> None:
        if tab == "gallery":
            self._create_tab_btn.set_active(False)
            self._gallery_tab_btn.set_active(True)
            self._gallery.refresh()
        self._sub_stack.set_visible_child_name(tab)

    def _on_gallery_card_activated(self, media_id: str) -> None:
        records = _media_store.query(
            media_type="artgen",
            generator_type=self._gallery._active_filter,
        )
        self._detail.show_record(media_id, records)
        self._sub_stack.set_visible_child_name("detail")

    def _on_detail_back(self) -> None:
        self._sub_stack.set_visible_child_name("gallery")

    def _on_detail_deleted(self, media_id: str) -> None:
        self._gallery.refresh()
        self._refresh_mini_grid()

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

    def _on_mini_card_activated(self, _flow, _child) -> None:
        self._switch_tab("gallery")

    def _refresh_mini_grid(self) -> None:
        while child := self._mini_flow.get_first_child():
            self._mini_flow.remove(child)
        recent = _media_store.query(media_type="artgen", limit=4)
        if not recent:
            placeholder = Gtk.Label(label="✦\nYour generations\nwill appear here")
            placeholder.set_xalign(0.5)
            placeholder.add_css_class("artgen-empty-hint")
            self._mini_flow.append(placeholder)
            self._view_all_btn.set_label("→ View all in Gallery")
            return
        total = len(_media_store.query(media_type="artgen"))
        self._view_all_btn.set_label(f"→ View all {total} in Gallery")
        for i, rec in enumerate(recent):
            card = self._make_mini_card(rec, highlight=(i == 0))
            self._mini_flow.append(card)

    def _make_mini_card(self, rec: "MediaRecord", highlight: bool = False) -> Gtk.Box:
        from artgen_gallery import ArtgenGallery
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_size_request(100, 80)
        box.add_css_class("artgen-card")
        if highlight:
            box.add_css_class("artgen-card-new")
        if rec.thumbnail_path and Path(rec.thumbnail_path).exists():
            img = Gtk.Picture.new_for_filename(rec.thumbnail_path)
            img.set_content_fit(Gtk.ContentFit.COVER)
        elif rec.file_path.endswith(".svg") and Path(rec.file_path).exists():
            img = Gtk.Picture.new_for_filename(rec.file_path)
            img.set_content_fit(Gtk.ContentFit.COVER)
        else:
            img = Gtk.Label(label=ArtgenGallery._type_emoji(rec.generator_type))
            img.add_css_class("artgen-card-placeholder")
        img.set_hexpand(True)
        img.set_vexpand(True)
        box.append(img)
        lbl = Gtk.Label(label=f"{rec.generator_type or '?'} · {rec.created_at[5:10]}")
        lbl.add_css_class("artgen-card-bottom")
        box.append(lbl)
        return box

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
        self._auto_type_checks: dict[str, Gtk.CheckButton] = {}
        for gname in artgen.all_names():
            cb = Gtk.CheckButton.new_with_label(gname)
            cb.set_active(True)
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

        # Switch the type dropdown to the chosen type so the user can see what's next
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
            from prompt_client import generate_prompt
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

        # Types that accept free-form text from Inspire
        _TEXT_TYPES = {"verse", "palette", "ansi", "freeform"}
        if gen_name == "verse":
            self._verse_theme.set_text(theme)
        elif gen_name == "palette":
            self._pal_mood.set_text(theme)
        elif gen_name == "ansi":
            self._ansi_subject.set_text(theme)
        elif gen_name == "freeform":
            self._free_tv.get_buffer().set_text(theme)
        else:
            # Visual types: show the inspiration in the status bar
            self._set_status(f"Inspired: {theme[:60]}")

        # Collect widget state before handing off to the background thread
        args = self._build_args(gen_name)
        self._generating = True
        self._gen_btn.set_sensitive(False)
        self._gen_btn.set_label("Generating…")
        if gen_name not in _TEXT_TYPES:
            self._set_status("Detecting model on port 8002…")
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

        # freeform: text written entirely by _auto_fire_with_theme after Inspire
