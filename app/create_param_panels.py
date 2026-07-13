# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
CreateParamPanel protocol + the real per-medium panels: ImageParamPanel
(Create-surface plan, Task 4), VideoParamPanel + AnimateParamPanel (Task 5:
docs/superpowers/plans/2026-07-13-create-surface.md).

CreateView (Task 3, `app/create_view.py`) hosts one panel per medium chip.
Task 3 shipped only a stub label; Task 4 ported the IMAGE medium to a real
panel; this task ports VIDEO and ANIMATE. Only artgen mediums keep the stub
now, until Task 6.

**Why this is a fresh widget, not an extraction from `ControlPanel`**
(see `.superpowers/sdd/task-4-brief.md`'s CRITICAL STRATEGY, which overrides
the brief's own "extract from ControlPanel" wording): `ControlPanel` in
`app/main_window.py` is a monolith — one giant box whose per-medium rows are
toggled by visibility flags, wired directly into `MainWindow._on_generate`
and the live worker/queue machinery. Extracting pieces of it mid-flight risks
breaking real generation, which every project rule treats as sacrosanct.
Task 8 of the Create-surface plan deletes `ControlPanel` outright once every
medium has a CreateView-native panel — so this panel *is* the eventual
replacement, not a second copy that lingers forever. Duplicating the small,
stable bits (default values, the four image-model ids) for one release cycle
is the deliberate trade CLAUDE.md accepts over touching a working generation
path.

`ImageParamPanel.collect()` returns exactly the kwargs
`worker.ImageGenerationWorker` takes, minus `prompt` (CreateView's idea-door
prompt entry owns the prompt text, not this panel — see `create_view.py`):

    {"negative_prompt": str, "num_inference_steps": int, "seed": int,
     "guidance_scale": float, "model": str}

Defaults mirror `ControlPanel.__init__`'s image defaults (`main_window.py`:
`_steps=20`, `_seed=-1`, `_guidance=3.5`, `_image_model="flux"`, `_neg=""`)
so the two surfaces agree on a first-run experience even though no code is
shared.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


class CreateParamPanel(ABC):
    """Contract every per-medium param panel on the Create surface implements.

    CreateView's panel host calls `build()` once per medium selection (the
    returned widget is inserted into the host box) and calls `collect()` on
    every Create-CTA click to read the medium-specific kwargs. A panel never
    knows about `on_create`, workers, or the API client — CreateView owns
    that wiring; a panel only owns its own widgets and their current values.
    """

    @abstractmethod
    def build(self) -> Gtk.Widget:
        """Construct and return this panel's root widget."""

    @abstractmethod
    def collect(self) -> dict:
        """Read current widget values into the medium-specific params dict."""


# Image model dropdown choices: (internal key, human label), display order.
# Mirrors ControlPanel's `_build_image_model_row` entries (main_window.py) —
# duplicated, not imported, per the module docstring's CRITICAL STRATEGY note.
_IMAGE_MODEL_CHOICES: "list[tuple[str, str]]" = [
    ("flux", "FLUX.1-schnell — 1024×1024"),
    ("sdxl", "SDXL — cpp_server"),
    ("z-image-turbo", "Z-Image-Turbo — P150X4 (functional)"),
    ("motif", "Motif-6B-Preview — P300X2"),
]

# Internal key -> server-side model id string, passed as `model=` to
# ImageGenerationWorker. Mirrors ControlPanel's `_IMAGE_MODEL_IDS`.
_IMAGE_MODEL_IDS: "dict[str, str]" = {
    "flux": "flux.1-schnell",
    "sdxl": "stable-diffusion-xl-base-1.0",
    "z-image-turbo": "z-image-turbo",
    "motif": "motif-image-6b-preview",
}

_DEFAULT_MODEL_KEY = "flux"


class ImageParamPanel(CreateParamPanel):
    """Image medium's param controls: steps, seed, guidance scale, model,
    negative prompt.

    Every widget here is a FRESH construction — this class shares no widget
    instances with `ControlPanel`. The prompt itself is intentionally absent:
    CreateView's idea-door prompt entry is the single source of the prompt
    text for every medium, so this panel only ever collects the params that
    are specific to generating an *image*.
    """

    def __init__(self) -> None:
        self._widget: Optional[Gtk.Widget] = None
        self._steps_adj: Optional[Gtk.Adjustment] = None
        self._seed_adj: Optional[Gtk.Adjustment] = None
        self._guidance_adj: Optional[Gtk.Adjustment] = None
        self._model_dropdown: Optional[Gtk.DropDown] = None
        self._neg_entry: Optional[Gtk.Entry] = None

    # ── CreateParamPanel protocol ────────────────────────────────────────────

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("image-param-panel")

        box.append(self._build_steps_row())
        box.append(self._build_seed_row())
        box.append(self._build_guidance_row())
        box.append(self._build_model_row())
        box.append(self._build_negative_row())

        self._widget = box
        return box

    def collect(self) -> dict:
        """Read every widget's current value into the exact
        `ImageGenerationWorker` kwarg dict (minus `prompt`).

        Falls back to the documented defaults for any widget that (for
        whatever reason) was never built — `collect()` should never raise
        even if called before `build()`.
        """
        return {
            "negative_prompt": self._neg_entry.get_text() if self._neg_entry is not None else "",
            "num_inference_steps": (
                int(self._steps_adj.get_value()) if self._steps_adj is not None else 20
            ),
            "seed": int(self._seed_adj.get_value()) if self._seed_adj is not None else -1,
            "guidance_scale": (
                float(self._guidance_adj.get_value()) if self._guidance_adj is not None else 3.5
            ),
            "model": self._selected_model_id(),
        }

    # ── Internals ─────────────────────────────────────────────────────────────

    def _selected_model_id(self) -> str:
        if self._model_dropdown is None:
            return _IMAGE_MODEL_IDS[_DEFAULT_MODEL_KEY]
        idx = self._model_dropdown.get_selected()
        if idx < 0 or idx >= len(_IMAGE_MODEL_CHOICES):
            return _IMAGE_MODEL_IDS[_DEFAULT_MODEL_KEY]
        key, _label = _IMAGE_MODEL_CHOICES[idx]
        return _IMAGE_MODEL_IDS.get(key, _IMAGE_MODEL_IDS[_DEFAULT_MODEL_KEY])

    def _row(self, label_text: str) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("image-param-row")
        label = Gtk.Label(label=label_text)
        label.add_css_class("image-param-label")
        label.set_xalign(0.0)
        label.set_size_request(120, -1)
        row.append(label)
        return row

    def _build_steps_row(self) -> Gtk.Box:
        row = self._row("Steps")
        # Range mirrors the server-side clamp in api_client.generate_image
        # (max(4, min(50, steps))) so the dial can never silently disagree
        # with what the server will actually do.
        self._steps_adj = Gtk.Adjustment(
            value=20, lower=4, upper=50, step_increment=1, page_increment=5
        )
        spin = Gtk.SpinButton(adjustment=self._steps_adj, climb_rate=1, digits=0)
        spin.set_numeric(True)
        spin.add_css_class("image-param-input")
        row.append(spin)
        return row

    def _build_seed_row(self) -> Gtk.Box:
        row = self._row("Seed")
        self._seed_adj = Gtk.Adjustment(
            value=-1, lower=-1, upper=2 ** 31 - 1, step_increment=1, page_increment=1000
        )
        spin = Gtk.SpinButton(adjustment=self._seed_adj, climb_rate=1, digits=0)
        spin.set_numeric(True)
        spin.add_css_class("image-param-input")
        spin.set_tooltip_text("-1 = random seed")
        row.append(spin)
        return row

    def _build_guidance_row(self) -> Gtk.Box:
        row = self._row("Guidance scale")
        self._guidance_adj = Gtk.Adjustment(
            value=3.5, lower=1.0, upper=20.0, step_increment=0.5, page_increment=1.0
        )
        spin = Gtk.SpinButton(adjustment=self._guidance_adj, climb_rate=0.5, digits=1)
        spin.set_numeric(True)
        spin.add_css_class("image-param-input")
        row.append(spin)
        return row

    def _build_model_row(self) -> Gtk.Box:
        row = self._row("Model")
        labels = [label for _key, label in _IMAGE_MODEL_CHOICES]
        model_list = Gtk.StringList.new(labels)
        dropdown = Gtk.DropDown(model=model_list)
        dropdown.add_css_class("image-param-input")
        default_idx = next(
            (i for i, (key, _l) in enumerate(_IMAGE_MODEL_CHOICES) if key == _DEFAULT_MODEL_KEY),
            0,
        )
        dropdown.set_selected(default_idx)
        self._model_dropdown = dropdown
        row.append(dropdown)
        return row

    def _build_negative_row(self) -> Gtk.Box:
        row = self._row("Negative prompt")
        entry = Gtk.Entry()
        entry.set_placeholder_text("blurry, low quality, deformed…")
        entry.set_hexpand(True)
        entry.add_css_class("image-param-input")
        self._neg_entry = entry
        row.append(entry)
        return row


# ─────────────────────────────────────────────────────────────────────────────
# VideoParamPanel (Task 5: docs/superpowers/plans/2026-07-13-create-surface.md)
# ─────────────────────────────────────────────────────────────────────────────

# Video model dropdown choices: (internal key, human label), display order.
# Only the three models `worker.GenerationWorker` actually drives through
# `api_client.APIClient.submit()` are offered here — "animatediff" is
# deliberately excluded: it runs through a completely different code path
# in ControlPanel (a local, serverless GIF pipeline with its own `frames=`
# kwarg and its own v0.9 config box, see main_window.py's
# `_build_animatediff_box`/`get_animatediff_args`), not `GenerationWorker`,
# so it doesn't belong in a panel whose whole contract is
# `GenerationWorker`'s kwargs. Labels mirror ControlPanel's
# `_ALL_VIDEO_MODEL_ENTRIES` (main_window.py) for the three shared entries —
# duplicated, not imported, per the module docstring's CRITICAL STRATEGY note.
_VIDEO_MODEL_CHOICES: "list[tuple[str, str]]" = [
    ("wan2", "Wan2.2 — 720p video"),
    ("mochi", "Mochi-1 — 480×848 video"),
    ("skyreels", "SkyReels I2V — 960×544 Blackhole"),
]

# Internal key -> server-side model id string, passed as `model=` to
# GenerationWorker. Mirrors ControlPanel's `_VIDEO_MODEL_IDS` (minus the
# animatediff entry, excluded above).
_VIDEO_MODEL_IDS: "dict[str, str]" = {
    "wan2": "wan2.2-t2v",
    "mochi": "mochi-1-preview",
    "skyreels": "skyreels-v2-i2v-14b-540p",
}

_DEFAULT_VIDEO_MODEL_KEY = "wan2"


class VideoParamPanel(CreateParamPanel):
    """Video medium's param controls: steps, seed, model, num_frames,
    negative prompt.

    `collect()` returns exactly the kwargs `worker.GenerationWorker` takes,
    minus `prompt` (owned by CreateView's idea-door entry, same convention as
    `ImageParamPanel`).

    Every widget here is a FRESH construction — no widget instances are
    shared with `ControlPanel`. Defaults:
      - steps=20, seed=-1 (mirrors `ImageParamPanel` / `ControlPanel`'s
        `_settings.get("quality_steps") or 20` and `_seed = -1`)
      - steps range 12-50 (NOT 4-50 like images) — mirrors the server-side
        clamp in `api_client.APIClient.submit`/`submit_animate`
        (`max(12, min(50, num_inference_steps))`), which differs from
        `generate_image`'s 4-50 clamp.
      - model="wan2.2-t2v" (the "wan2" key's server id)
      - num_frames=None via a 0="runner default" spin sentinel, mirroring the
        seed field's -1="random" sentinel — `GenerationWorker.__init__`'s own
        default is `num_frames: Optional[int] = None`, so 0 must round-trip
        to `None`, not to the literal integer 0.
    """

    def __init__(self) -> None:
        self._widget: Optional[Gtk.Widget] = None
        self._steps_adj: Optional[Gtk.Adjustment] = None
        self._seed_adj: Optional[Gtk.Adjustment] = None
        self._model_dropdown: Optional[Gtk.DropDown] = None
        self._frames_adj: Optional[Gtk.Adjustment] = None
        self._neg_entry: Optional[Gtk.Entry] = None

    # ── CreateParamPanel protocol ────────────────────────────────────────────

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("video-param-panel")

        box.append(self._build_steps_row())
        box.append(self._build_seed_row())
        box.append(self._build_model_row())
        box.append(self._build_frames_row())
        box.append(self._build_negative_row())

        self._widget = box
        return box

    def collect(self) -> dict:
        """Read every widget's current value into the exact
        `GenerationWorker` kwarg dict (minus `prompt`).

        Falls back to the documented defaults for any widget that was never
        built — `collect()` should never raise even if called before
        `build()`.
        """
        return {
            "negative_prompt": self._neg_entry.get_text() if self._neg_entry is not None else "",
            "num_inference_steps": (
                int(self._steps_adj.get_value()) if self._steps_adj is not None else 20
            ),
            "seed": int(self._seed_adj.get_value()) if self._seed_adj is not None else -1,
            "model": self._selected_model_id(),
            "num_frames": self._selected_num_frames(),
        }

    # ── Internals ─────────────────────────────────────────────────────────────

    def _selected_model_id(self) -> str:
        if self._model_dropdown is None:
            return _VIDEO_MODEL_IDS[_DEFAULT_VIDEO_MODEL_KEY]
        idx = self._model_dropdown.get_selected()
        if idx < 0 or idx >= len(_VIDEO_MODEL_CHOICES):
            return _VIDEO_MODEL_IDS[_DEFAULT_VIDEO_MODEL_KEY]
        key, _label = _VIDEO_MODEL_CHOICES[idx]
        return _VIDEO_MODEL_IDS.get(key, _VIDEO_MODEL_IDS[_DEFAULT_VIDEO_MODEL_KEY])

    def _selected_num_frames(self) -> "int | None":
        """0 ("auto"/unset) collects as `None` — `GenerationWorker`'s own
        default — everything else round-trips as the exact frame count."""
        if self._frames_adj is None:
            return None
        value = int(self._frames_adj.get_value())
        return value if value > 0 else None

    def _row(self, label_text: str) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("video-param-row")
        label = Gtk.Label(label=label_text)
        label.add_css_class("video-param-label")
        label.set_xalign(0.0)
        label.set_size_request(120, -1)
        row.append(label)
        return row

    def _build_steps_row(self) -> Gtk.Box:
        row = self._row("Steps")
        # Range mirrors the server-side clamp in api_client.APIClient.submit
        # (max(12, min(50, steps))) so the dial can never silently disagree
        # with what the server will actually do.
        self._steps_adj = Gtk.Adjustment(
            value=20, lower=12, upper=50, step_increment=1, page_increment=5
        )
        spin = Gtk.SpinButton(adjustment=self._steps_adj, climb_rate=1, digits=0)
        spin.set_numeric(True)
        spin.add_css_class("video-param-input")
        row.append(spin)
        return row

    def _build_seed_row(self) -> Gtk.Box:
        row = self._row("Seed")
        self._seed_adj = Gtk.Adjustment(
            value=-1, lower=-1, upper=2 ** 31 - 1, step_increment=1, page_increment=1000
        )
        spin = Gtk.SpinButton(adjustment=self._seed_adj, climb_rate=1, digits=0)
        spin.set_numeric(True)
        spin.add_css_class("video-param-input")
        spin.set_tooltip_text("-1 = random seed")
        row.append(spin)
        return row

    def _build_model_row(self) -> Gtk.Box:
        row = self._row("Model")
        labels = [label for _key, label in _VIDEO_MODEL_CHOICES]
        model_list = Gtk.StringList.new(labels)
        dropdown = Gtk.DropDown(model=model_list)
        dropdown.add_css_class("video-param-input")
        default_idx = next(
            (i for i, (key, _l) in enumerate(_VIDEO_MODEL_CHOICES) if key == _DEFAULT_VIDEO_MODEL_KEY),
            0,
        )
        dropdown.set_selected(default_idx)
        self._model_dropdown = dropdown
        row.append(dropdown)
        return row

    def _build_frames_row(self) -> Gtk.Box:
        row = self._row("Frame count")
        # 193 = the "extended" wan2 slot's frame count (generation_config.py's
        # CLIP_LENGTH_FRAMES ceiling) plus headroom; 0 means "let the runner
        # pick" (collects as None — see _selected_num_frames).
        self._frames_adj = Gtk.Adjustment(
            value=0, lower=0, upper=201, step_increment=1, page_increment=8
        )
        spin = Gtk.SpinButton(adjustment=self._frames_adj, climb_rate=1, digits=0)
        spin.set_numeric(True)
        spin.add_css_class("video-param-input")
        spin.set_tooltip_text("0 = runner default")
        row.append(spin)
        return row

    def _build_negative_row(self) -> Gtk.Box:
        row = self._row("Negative prompt")
        entry = Gtk.Entry()
        entry.set_placeholder_text("blurry, low quality, deformed…")
        entry.set_hexpand(True)
        entry.add_css_class("video-param-input")
        self._neg_entry = entry
        row.append(entry)
        return row


# ─────────────────────────────────────────────────────────────────────────────
# AnimateParamPanel (Task 5)
# ─────────────────────────────────────────────────────────────────────────────

# There is exactly one native animate model today (Wan2.2-Animate-14B), so
# unlike Image/Video there is no model dropdown — the id is a fixed constant.
_ANIMATE_MODEL_ID = "wan2.2-animate-14b"

_ANIMATE_MODE_ANIMATION = "animation"
_ANIMATE_MODE_REPLACEMENT = "replacement"


class AnimateParamPanel(CreateParamPanel):
    """Animate medium's param controls: motion-video picker, character-image
    picker, animate-mode toggle, steps, seed.

    `collect()` returns exactly the kwargs `worker.AnimateGenerationWorker`
    takes, minus `prompt` (owned by CreateView's idea-door entry).

    File pickers use GTK4's async `Gtk.FileDialog` (`open()` + `open_finish()`
    wrapped in try/except, per CLAUDE.md's FileDialog gotcha — `open_finish()`
    raises when the user cancels). Empty paths are valid: this panel never
    validates that a file was actually chosen — that's the worker/CTA's
    concern (see module docstring).
    """

    def __init__(self) -> None:
        self._widget: Optional[Gtk.Widget] = None
        self._ref_video_entry: Optional[Gtk.Entry] = None
        self._ref_image_entry: Optional[Gtk.Entry] = None
        self._mode_anim_btn: Optional[Gtk.ToggleButton] = None
        self._mode_repl_btn: Optional[Gtk.ToggleButton] = None
        self._steps_adj: Optional[Gtk.Adjustment] = None
        self._seed_adj: Optional[Gtk.Adjustment] = None
        # Mirrors CreateView's `_entry_mode` pattern: a plain attribute kept
        # in sync by the toggle group's "toggled" handler, read by collect()
        # rather than re-deriving it from widget state on every read.
        self._animate_mode: str = _ANIMATE_MODE_ANIMATION

    # ── CreateParamPanel protocol ────────────────────────────────────────────

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("animate-param-panel")

        box.append(self._build_ref_video_row())
        box.append(self._build_ref_image_row())
        box.append(self._build_mode_row())
        box.append(self._build_steps_row())
        box.append(self._build_seed_row())

        self._widget = box
        return box

    def collect(self) -> dict:
        """Read every widget's current value into the exact
        `AnimateGenerationWorker` kwarg dict (minus `prompt`).

        Falls back to the documented defaults for any widget that was never
        built — `collect()` should never raise even if called before
        `build()`.
        """
        return {
            "reference_video_path": (
                self._ref_video_entry.get_text() if self._ref_video_entry is not None else ""
            ),
            "reference_image_path": (
                self._ref_image_entry.get_text() if self._ref_image_entry is not None else ""
            ),
            "num_inference_steps": (
                int(self._steps_adj.get_value()) if self._steps_adj is not None else 20
            ),
            "seed": int(self._seed_adj.get_value()) if self._seed_adj is not None else -1,
            "animate_mode": self._animate_mode,
            "model": _ANIMATE_MODEL_ID,
        }

    # ── Internals ─────────────────────────────────────────────────────────────

    def _row(self, label_text: str) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("animate-param-row")
        label = Gtk.Label(label=label_text)
        label.add_css_class("animate-param-label")
        label.set_xalign(0.0)
        label.set_size_request(120, -1)
        row.append(label)
        return row

    def _build_ref_video_row(self) -> Gtk.Box:
        row = self._row("Motion video")
        entry = Gtk.Entry()
        entry.set_placeholder_text("no motion video selected")
        entry.set_hexpand(True)
        entry.add_css_class("animate-param-input")
        self._ref_video_entry = entry
        row.append(entry)

        browse = Gtk.Button(label="Browse…")
        browse.connect("clicked", self._on_pick_ref_video)
        row.append(browse)
        return row

    def _build_ref_image_row(self) -> Gtk.Box:
        row = self._row("Character image")
        entry = Gtk.Entry()
        entry.set_placeholder_text("no character image selected")
        entry.set_hexpand(True)
        entry.add_css_class("animate-param-input")
        self._ref_image_entry = entry
        row.append(entry)

        browse = Gtk.Button(label="Browse…")
        browse.connect("clicked", self._on_pick_ref_image)
        row.append(browse)
        return row

    def _build_mode_row(self) -> Gtk.Box:
        row = self._row("Mode")

        anim_btn = Gtk.ToggleButton(label="Animation")
        anim_btn.add_css_class("animate-param-input")
        anim_btn.set_tooltip_text("Character image mimics the motion video")

        repl_btn = Gtk.ToggleButton(label="Replacement")
        repl_btn.add_css_class("animate-param-input")
        repl_btn.set_tooltip_text("Character image replaces the person in the motion video")
        repl_btn.set_group(anim_btn)

        anim_btn.connect(
            "toggled",
            lambda b: b.get_active() and self._set_animate_mode(_ANIMATE_MODE_ANIMATION),
        )
        repl_btn.connect(
            "toggled",
            lambda b: b.get_active() and self._set_animate_mode(_ANIMATE_MODE_REPLACEMENT),
        )

        self._mode_anim_btn = anim_btn
        self._mode_repl_btn = repl_btn
        anim_btn.set_active(True)  # default "animation"; fires _set_animate_mode

        row.append(anim_btn)
        row.append(repl_btn)
        return row

    def _set_animate_mode(self, mode: str) -> None:
        self._animate_mode = mode

    def _build_steps_row(self) -> Gtk.Box:
        row = self._row("Steps")
        # Range mirrors the server-side clamp in
        # api_client.APIClient.submit_animate (max(12, min(50, steps))).
        self._steps_adj = Gtk.Adjustment(
            value=20, lower=12, upper=50, step_increment=1, page_increment=5
        )
        spin = Gtk.SpinButton(adjustment=self._steps_adj, climb_rate=1, digits=0)
        spin.set_numeric(True)
        spin.add_css_class("animate-param-input")
        row.append(spin)
        return row

    def _build_seed_row(self) -> Gtk.Box:
        row = self._row("Seed")
        self._seed_adj = Gtk.Adjustment(
            value=-1, lower=-1, upper=2 ** 31 - 1, step_increment=1, page_increment=1000
        )
        spin = Gtk.SpinButton(adjustment=self._seed_adj, climb_rate=1, digits=0)
        spin.set_numeric(True)
        spin.add_css_class("animate-param-input")
        spin.set_tooltip_text("-1 = random seed")
        row.append(spin)
        return row

    # ── File pickers (GTK4 async Gtk.FileDialog, per CLAUDE.md) ──────────────

    def _on_pick_ref_video(self, _btn: Gtk.Button) -> None:
        dlg = Gtk.FileDialog()
        dlg.set_title("Select motion video")
        parent = self._widget.get_root() if self._widget is not None else None
        dlg.open(parent, None, self._on_ref_video_picked)

    def _on_ref_video_picked(self, dlg, result) -> None:
        try:
            gfile = dlg.open_finish(result)
        except Exception:
            return  # user cancelled — leave the existing entry text untouched
        if gfile is not None and self._ref_video_entry is not None:
            path = gfile.get_path()
            if path:
                self._ref_video_entry.set_text(path)

    def _on_pick_ref_image(self, _btn: Gtk.Button) -> None:
        dlg = Gtk.FileDialog()
        dlg.set_title("Select character image")
        parent = self._widget.get_root() if self._widget is not None else None
        dlg.open(parent, None, self._on_ref_image_picked)

    def _on_ref_image_picked(self, dlg, result) -> None:
        try:
            gfile = dlg.open_finish(result)
        except Exception:
            return  # user cancelled — leave the existing entry text untouched
        if gfile is not None and self._ref_image_entry is not None:
            path = gfile.get_path()
            if path:
                self._ref_image_entry.set_text(path)
