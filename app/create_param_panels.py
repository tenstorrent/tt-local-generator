# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
CreateParamPanel protocol + the real per-medium panels: ImageParamPanel
(Create-surface plan, Task 4), VideoParamPanel + AnimateParamPanel (Task 5),
ArtgenParamPanel (Task 6: docs/superpowers/plans/2026-07-13-create-surface.md).

CreateView (Task 3, `app/create_view.py`) hosts one panel per medium chip.
Task 3 shipped only a stub label; Task 4 ported the IMAGE medium to a real
panel; Task 5 ported VIDEO and ANIMATE; this task (6) ports every artgen
generator medium (verse/ansi/landscape/…) to `ArtgenParamPanel` — see that
class's own section below for the introspection strategy. Every medium the
Create surface offers now has a real panel; only a future, not-yet-existing
medium would fall back to the Task 3 stub.

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

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

import field_roles


@dataclass
class FieldSpec:
    """Purely-additive metadata describing ONE field a param panel exposes.

    `field_specs()` (below) emits a list of these per panel so a later task's
    shared RoleZonePanel can group fields into brief/direction/control zones
    without having to know each panel's widget internals. This dataclass
    carries no widget references and does not participate in `collect()` —
    it is a read-only description, built fresh on every call.

    Fields:
      key      — matches a `collect()` dict key (or an artgen `_ArgSpec.dest`)
                 EXACTLY. This is the join key between "what this field is"
                 (FieldSpec) and "what value it currently holds" (collect()).
      label    — human-readable label (mirrors the panel's own row label,
                 or `_humanize_dest()` for artgen).
      kind     — widget-shape hint: "int" | "float" | "bool" | "choice" |
                 "str" | "path" | "model". "model" is a deliberate special
                 case (see module docstring's per-panel notes below) — model
                 selection is not one of the brief/direction/control zones,
                 it is handled as its own concern by the caller.
      default  — the value this field starts at (mirrors the widget's built
                 default, NOT necessarily `collect()`'s current value).
      role     — a `field_roles.FieldRole` — which zone (brief/direction/
                 control) and how the value is used (words/interpreted/exact).
      choices  — for "choice"/"model" kinds, the list of legal values (for
                 "model" specs this is the panel's own `(key, label)` tuple
                 list, unchanged, so a caller can render the same dropdown).
      tooltip  — optional help text (artgen forwards the argparse `help=`
                 string here; native panels default to "").
    """

    key: str
    label: str
    kind: str
    default: object
    role: "field_roles.FieldRole"
    choices: "Optional[list]" = None
    tooltip: str = ""


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

    @abstractmethod
    def field_specs(self) -> "list[FieldSpec]":
        """Describe every field this panel exposes as a list of `FieldSpec`.

        Purely additive metadata for a later task's RoleZonePanel grouping —
        does NOT affect `collect()` in any way, and does not require `build()`
        to have been called first (mirrors `collect()`'s own "never raises
        before build()" contract where practical). Every `key` here MUST
        match a real `collect()` key (or, for `ArtgenParamPanel`, a real
        introspected argparse dest) — see each panel's own implementation for
        the exact key list and rationale for any role that isn't a bare
        `classify_native`/`classify_artgen` call.
        """

    def _row_for(self, key: str) -> "Optional[Gtk.Widget]":
        """Return the built row widget for a field `key`, if this panel's
        `build()` populated a `self._rows` dict (every concrete panel below
        does). Returns `None` before `build()` has run, or for a key that
        deliberately has no zone-placeable row (e.g. the `"model"` kind
        special case — see `FieldSpec.kind`'s docstring).

        Purely additive: `RoleZonePanel` (Task 5) uses this to re-parent an
        ALREADY-BUILT widget into a zone container. It never constructs a new
        widget and never touches `collect()`'s data path.
        """
        return getattr(self, "_rows", {}).get(key)


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
        # key -> built row widget, populated by build(). Lets RoleZonePanel
        # (Task 5) re-parent an already-built row into a zone by field key
        # via the base class's `_row_for` — see that method's docstring.
        self._rows: "dict[str, Gtk.Widget]" = {}

    # ── CreateParamPanel protocol ────────────────────────────────────────────

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("image-param-panel")

        self._rows = {}
        steps_row = self._build_steps_row()
        box.append(steps_row)
        self._rows["num_inference_steps"] = steps_row

        seed_row = self._build_seed_row()
        box.append(seed_row)
        self._rows["seed"] = seed_row

        guidance_row = self._build_guidance_row()
        box.append(guidance_row)
        self._rows["guidance_scale"] = guidance_row

        model_row = self._build_model_row()
        box.append(model_row)
        self._rows["model"] = model_row

        neg_row = self._build_negative_row()
        box.append(neg_row)
        self._rows["negative_prompt"] = neg_row

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

    def field_specs(self) -> "list[FieldSpec]":
        """One spec per `collect()` key (see that method's exact dict shape).

        `model` is deliberately `kind="model"` rather than being classified by
        `field_roles.classify_native` — model selection is not a brief/
        direction/control zone field, it's handled as its own concern by a
        later task's caller (see `FieldSpec.kind`'s docstring note). Every
        other key uses `classify_native(key)` unmodified so the roles agree
        with the shared vocabulary `field_roles.py` defines for native panels.
        """
        return [
            FieldSpec(
                key="num_inference_steps", label="Steps", kind="int", default=20,
                role=field_roles.classify_native("num_inference_steps"),
            ),
            FieldSpec(
                key="seed", label="Seed", kind="int", default=-1,
                role=field_roles.classify_native("seed"),
                tooltip="-1 = random seed",
            ),
            FieldSpec(
                key="guidance_scale", label="Guidance scale", kind="float", default=3.5,
                role=field_roles.classify_native("guidance_scale"),
            ),
            FieldSpec(
                key="negative_prompt", label="Negative prompt", kind="str", default="",
                role=field_roles.classify_native("negative_prompt"),
                tooltip="blurry, low quality, deformed…",
            ),
            FieldSpec(
                key="model", label="Model", kind="model", default=_DEFAULT_MODEL_KEY,
                role=field_roles.FieldRole(field_roles.ROLE_CONTROL, field_roles.MARK_EXACT),
                choices=_IMAGE_MODEL_CHOICES,
            ),
        ]

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
# Only the text-to-video models `worker.GenerationWorker` drives through
# `api_client.APIClient.submit()` are offered here — "animatediff" is
# deliberately excluded: it runs through a completely different code path
# in ControlPanel (a local, serverless GIF pipeline with its own `frames=`
# kwarg and its own v0.9 config box, see main_window.py's
# `_build_animatediff_box`/`get_animatediff_args`), not `GenerationWorker`,
# so it doesn't belong in a panel whose whole contract is
# `GenerationWorker`'s kwargs. Labels mirror ControlPanel's
# `_ALL_VIDEO_MODEL_ENTRIES` (main_window.py) for the three shared entries —
# duplicated, not imported, per the module docstring's CRITICAL STRATEGY note.
#
# SkyReels is deliberately OMITTED: `skyreels-v2-i2v-14b-540p` is an
# image-to-video model that requires a conditioning (character/seed) image,
# but the Video door is text-to-video oriented and collects NO image input —
# `_create_generate_native`'s video branch would hand the I2V model
# `seed_image_path=""`, so it can only fail. Offering it here is a trap. Re-add
# it once the Create surface grows an image-seeded video path that can supply
# the conditioning image this model needs.
_VIDEO_MODEL_CHOICES: "list[tuple[str, str]]" = [
    ("wan2", "Wan2.2 — 720p video"),
    ("mochi", "Mochi-1 — 480×848 video"),
]

# Internal key -> server-side model id string, passed as `model=` to
# GenerationWorker. Mirrors ControlPanel's `_VIDEO_MODEL_IDS` (minus the
# animatediff entry, excluded above, and skyreels — see the choices note).
_VIDEO_MODEL_IDS: "dict[str, str]" = {
    "wan2": "wan2.2-t2v",
    "mochi": "mochi-1-preview",
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
        # key -> built row widget, populated by build() — see ImageParamPanel's
        # `_rows` for the rationale (RoleZonePanel re-parenting, Task 5).
        self._rows: "dict[str, Gtk.Widget]" = {}

    # ── CreateParamPanel protocol ────────────────────────────────────────────

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("video-param-panel")

        self._rows = {}
        steps_row = self._build_steps_row()
        box.append(steps_row)
        self._rows["num_inference_steps"] = steps_row

        seed_row = self._build_seed_row()
        box.append(seed_row)
        self._rows["seed"] = seed_row

        model_row = self._build_model_row()
        box.append(model_row)
        self._rows["model"] = model_row

        frames_row = self._build_frames_row()
        box.append(frames_row)
        self._rows["num_frames"] = frames_row

        neg_row = self._build_negative_row()
        box.append(neg_row)
        self._rows["negative_prompt"] = neg_row

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

    def field_specs(self) -> "list[FieldSpec]":
        """One spec per `collect()` key (see that method's exact dict shape).

        `model` is `kind="model"` for the same reason as `ImageParamPanel`
        (model selection is handled as its own concern, not a zone field).
        `num_frames`'s 0="runner default" sentinel (see `_selected_num_frames`)
        is metadata-invisible here — the spec's `default` is the widget's
        starting value (0), matching the other native panels' convention of
        describing the built default, not the collect()-time semantics.
        """
        return [
            FieldSpec(
                key="num_inference_steps", label="Steps", kind="int", default=20,
                role=field_roles.classify_native("num_inference_steps"),
            ),
            FieldSpec(
                key="seed", label="Seed", kind="int", default=-1,
                role=field_roles.classify_native("seed"),
                tooltip="-1 = random seed",
            ),
            FieldSpec(
                key="num_frames", label="Frame count", kind="int", default=0,
                role=field_roles.classify_native("num_frames"),
                tooltip="0 = runner default",
            ),
            FieldSpec(
                key="negative_prompt", label="Negative prompt", kind="str", default="",
                role=field_roles.classify_native("negative_prompt"),
                tooltip="blurry, low quality, deformed…",
            ),
            FieldSpec(
                key="model", label="Model", kind="model", default=_DEFAULT_VIDEO_MODEL_KEY,
                role=field_roles.FieldRole(field_roles.ROLE_CONTROL, field_roles.MARK_EXACT),
                choices=_VIDEO_MODEL_CHOICES,
            ),
        ]

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
        # key -> built row widget, populated by build() — see ImageParamPanel's
        # `_rows` for the rationale (RoleZonePanel re-parenting, Task 5).
        # Note: no "model" entry — this panel builds no model row (single
        # fixed model id, no dropdown); RoleZonePanel skips kind="model"
        # specs entirely regardless, so the absence is harmless.
        self._rows: "dict[str, Gtk.Widget]" = {}

    # ── CreateParamPanel protocol ────────────────────────────────────────────

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("animate-param-panel")

        self._rows = {}
        ref_video_row = self._build_ref_video_row()
        box.append(ref_video_row)
        self._rows["reference_video_path"] = ref_video_row

        ref_image_row = self._build_ref_image_row()
        box.append(ref_image_row)
        self._rows["reference_image_path"] = ref_image_row

        mode_row = self._build_mode_row()
        box.append(mode_row)
        self._rows["animate_mode"] = mode_row

        steps_row = self._build_steps_row()
        box.append(steps_row)
        self._rows["num_inference_steps"] = steps_row

        seed_row = self._build_seed_row()
        box.append(seed_row)
        self._rows["seed"] = seed_row

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

    def field_specs(self) -> "list[FieldSpec]":
        """One spec per `collect()` key (see that method's exact dict shape).

        Three keys deliberately do NOT use `field_roles.classify_native`
        (which would fall through to its "unknown key -> control/exact"
        default for all three, misclassifying them):

          - `animate_mode` is a direction field, not a numeric control — it's
            a toggle between "animation"/"replacement" interpretive framing,
            so it gets an explicit `FieldRole(ROLE_DIRECTION, MARK_EXACT)`
            per the task brief.
          - `reference_video_path` / `reference_image_path` are file paths,
            not native brief/direction/control text — `kind="path"` flags
            that a path-picker widget (not a plain entry) should render them.
            Per the task brief ("path fields -> give them kind='path' and a
            sensible role (brief/words is fine for reference inputs, or
            direction - pick brief/words and note it)") these are classified
            brief/words: the reference video/image are user-supplied CREATIVE
            INPUT the model conditions on (motion pattern / character
            likeness), the same "raw material the generator reads" role a
            text brief plays for image/video prompts — not an exact numeric
            dial the model never interprets.

        `model` is `kind="model"` with no dropdown choices (this medium has
        exactly one native model today — see `_ANIMATE_MODEL_ID`) — still
        emitted so a caller can treat "model" uniformly across all three
        native panels without a hasattr/None check.
        """
        brief_words = field_roles.FieldRole(field_roles.ROLE_BRIEF, field_roles.MARK_WORDS)
        return [
            FieldSpec(
                key="num_inference_steps", label="Steps", kind="int", default=20,
                role=field_roles.classify_native("num_inference_steps"),
            ),
            FieldSpec(
                key="seed", label="Seed", kind="int", default=-1,
                role=field_roles.classify_native("seed"),
                tooltip="-1 = random seed",
            ),
            FieldSpec(
                key="animate_mode", label="Mode", kind="choice",
                default=_ANIMATE_MODE_ANIMATION,
                role=field_roles.FieldRole(field_roles.ROLE_DIRECTION, field_roles.MARK_EXACT),
                choices=[_ANIMATE_MODE_ANIMATION, _ANIMATE_MODE_REPLACEMENT],
            ),
            FieldSpec(
                key="reference_video_path", label="Motion video", kind="path", default="",
                role=brief_words,
                tooltip="no motion video selected",
            ),
            FieldSpec(
                key="reference_image_path", label="Character image", kind="path", default="",
                role=brief_words,
                tooltip="no character image selected",
            ),
            FieldSpec(
                key="model", label="Model", kind="model", default=_ANIMATE_MODEL_ID,
                role=field_roles.FieldRole(field_roles.ROLE_CONTROL, field_roles.MARK_EXACT),
                choices=None,
            ),
        ]

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


# ─────────────────────────────────────────────────────────────────────────────
# ArtgenParamPanel (Task 6 — docs/superpowers/plans/2026-07-13-create-surface.md)
# ─────────────────────────────────────────────────────────────────────────────
#
# Unlike Image/Video/Animate, artgen has ~11 generators (verse/ansi/landscape/
# codeart/freeform/…) each with its own bespoke `add_args(parser)`. The
# CRITICAL STRATEGY for this task (task-6-brief.md, overriding the plan
# document's "reuse ArtgenPanel's controls" wording) is: do NOT extract from
# or touch `ArtgenPanel._build_controls_page` in artgen_panel.py — that method
# is a per-generator monolith of hardcoded if/elif branches wired directly
# into the live artgen generation UI, and touching it risks the one thing
# every project rule treats as sacrosanct (breaking real generation).
#
# Instead, ONE class — `ArtgenParamPanel` — is parameterized by generator name
# and INTROSPECTS that generator's own `add_args` via a throwaway
# `argparse.ArgumentParser`, deriving a tidy control per resolved argparse
# dest. A new artgen plugin with a novel `add_args` gets a working param panel
# for free, with zero new code here.
#
# ── Introspection contract ───────────────────────────────────────────────────
#
# `_introspect_generator_args(name)` builds `parser = argparse.ArgumentParser()`,
# calls `artgen.get(name).add_args(parser)`, then walks `parser._actions`:
#
#   - the implicit `-h/--help` action (dest "help") is skipped.
#   - `action.choices` truthy           -> "choice" (dropdown).
#   - `type(action).__name__` is one of argparse's private
#     `_StoreTrueAction`/`_StoreFalseAction` -> "bool" (switch).
#   - `action.type is int`              -> "int" (spin button).
#   - `action.type is float`            -> "float" (spin button, 2 decimals).
#   - else                              -> "str" (entry).
#
# **Boolean flag-pair dedup**: several generators (e.g. landscape's
# `--mountains`/`--no-mountains`) register TWO argparse actions that share one
# `dest` — the standard argparse idiom for a flag with an explicit "off"
# spelling. Building a widget per ACTION would show two controls for one
# value. Dedup is by `dest`, keeping only the first action's metadata
# (label/help) — the *default value*, however, is resolved by calling
# `parser.parse_args([])` and reading `vars(namespace)[dest]`, not
# `action.default` directly: argparse only applies the earliest-registered
# action's default for a shared dest when no flag is passed (later actions in
# the pair have no default of their own), so parsing an empty arg list is the
# only reliable way to get the resolved value — same trick a real CLI
# invocation with no flags would produce.
#
# **The `None`-default sentinel**: some int/float args default to `None`
# (e.g. ansi's `--width`, landscape's `--glitch-seed`) to mean "let the
# generator decide". Mirrors `VideoParamPanel`'s `num_frames` 0="runner
# default" convention: the spin button's starting value is 0 when the
# resolved default is `None`; `collect()` returns whatever integer/float the
# spin currently shows (0 included) — the panel never re-encodes 0 back to
# `None`, unlike `VideoParamPanel._selected_num_frames`, because there is no
# single shared "0 means auto" contract across ~11 generators' args the way
# there is for the one native video path. Downstream (the artgen run seam,
# a later task) is expected to treat this the same way the CLI already does:
# an explicit 0 is a valid value for some args and "unset" for others, and
# only the generator itself knows which.


@dataclass
class _ArgSpec:
    """One argparse dest's resolved shape, ready to become a control.

    `default` is the value `parser.parse_args([])` actually resolves for this
    dest (see module comment above) — NOT necessarily `action.default`, which
    can lie for a shared-dest boolean pair.
    """

    dest: str
    kind: str  # "choice" | "bool" | "int" | "float" | "str"
    default: object
    help: str = ""
    choices: "Optional[list]" = None
    # Bool dests carry their explicit CLI spellings so the artgen run seam can
    # emit the flag that MATCHES the switch state — the positive flag when ON
    # (e.g. "--mountains"), the negative flag when OFF if the generator defines
    # one (e.g. "--no-mountains"). `neg_flag` is None for a bool with no "off"
    # spelling (e.g. a bare "--glitch"), in which case OFF emits nothing.
    pos_flag: "Optional[str]" = None
    neg_flag: "Optional[str]" = None
    # True for an int/float dest whose RESOLVED default is None (e.g. ansi's
    # `--width`, landscape's `--glitch-seed`, animatediff's `--device-id`),
    # meaning "let the generator decide". The spin starts at 0 for these; a
    # current value of 0 collects as None so the generator's own auto-default
    # applies instead of a literal 0 overriding it. See `_ArgControl.read`.
    none_default: bool = False


@dataclass
class _ArgControl:
    """A built widget plus enough metadata for `collect()` to read it back."""

    dest: str
    kind: str
    widget: Gtk.Widget
    choices: "list" = field(default_factory=list)
    # Mirrors `_ArgSpec.none_default`: when set, a numeric value of 0 reads
    # back as None (the "unset — use the generator's auto-default" sentinel)
    # rather than the literal 0 that would override that default downstream.
    none_default: bool = False

    def read(self) -> object:
        if self.kind == "choice":
            idx = self.widget.get_selected()
            if self.choices and 0 <= idx < len(self.choices):
                return self.choices[idx]
            return self.choices[0] if self.choices else None
        if self.kind == "bool":
            return bool(self.widget.get_active())
        if self.kind == "int":
            value = int(self.widget.get_value())
            # Honor the tooltip contract the int control shows the user for a
            # None-default arg ("0 = generator default"): 0 means "unset", so
            # return None and let the seam omit the flag entirely.
            if self.none_default and value == 0:
                return None
            return value
        if self.kind == "float":
            value = float(self.widget.get_value())
            if self.none_default and value == 0:
                return None
            return value
        return self.widget.get_text()  # "str"


def _humanize_dest(dest: str) -> str:
    """"ansi_style" -> "Ansi Style"; "count" -> "Count". Falls back to the raw
    dest string if it's empty/whitespace-only (never returns "")."""
    text = dest.replace("_", " ").replace("-", " ").strip()
    return text.title() if text else dest


def _classify_action(action: "argparse.Action") -> "tuple[str, Optional[list]]":
    """Return (kind, choices) for one argparse.Action — see module comment.

    Three distinct argparse spellings all mean "this is a boolean flag":
    the classic `_StoreTrueAction`/`_StoreFalseAction` pair (one action per
    flag, sharing a dest — see the boolean flag-pair dedup note above) and
    the modern single-action `argparse.BooleanOptionalAction` (Python 3.9+,
    registers `--flag`/`--no-flag` together, e.g. codeart's
    `--should-compile`/`--no-should-compile`). All three must render as a
    switch, not fall through to the "str" default — the plain class-name
    check below (not an isinstance of the public `BooleanOptionalAction`)
    keeps this uniform with the private-class checks it sits beside.
    """
    cls_name = type(action).__name__
    if cls_name in ("_StoreTrueAction", "_StoreFalseAction", "BooleanOptionalAction"):
        return "bool", None
    if action.choices:
        return "choice", list(action.choices)
    if action.type is int:
        return "int", None
    if action.type is float:
        return "float", None
    return "str", None


def _bool_flag_pair(actions: "list[argparse.Action]") -> "tuple[Optional[str], Optional[str]]":
    """Given EVERY argparse action that shares one boolean dest, return the
    ``(positive, negative)`` CLI option-string spellings the generator accepts.

    Three spellings all mean "boolean flag" (see `_classify_action`):

      - the classic `_StoreTrueAction`/`_StoreFalseAction` PAIR — two actions
        sharing a dest, e.g. landscape's `--mountains` (store_true) and
        `--no-mountains` (store_false); the positive is the store_true
        action's option string, the negative is the store_false's.
      - a single `argparse.BooleanOptionalAction` — one action whose
        `option_strings` holds both `--flag` and `--no-flag`; the negative is
        the one starting `--no-`, the positive is the other.

    Either half may be absent (e.g. a bare `--glitch` store_true with no
    `--no-glitch`), in which case that side of the tuple is None.
    """
    pos: "Optional[str]" = None
    neg: "Optional[str]" = None
    for action in actions:
        cls_name = type(action).__name__
        opts = list(action.option_strings)
        if cls_name == "BooleanOptionalAction":
            for opt in opts:
                if opt.startswith("--no-"):
                    neg = neg or opt
                else:
                    pos = pos or opt
        elif cls_name == "_StoreFalseAction":
            if opts:
                neg = neg or opts[0]
        elif cls_name == "_StoreTrueAction":
            if opts:
                pos = pos or opts[0]
    return pos, neg


def _introspect_generator_args(generator_name: str) -> "list[_ArgSpec]":
    """Build the arg specs for *generator_name* by calling its own `add_args`
    against a throwaway parser. Returns `[]` for an unregistered generator
    name or any exception raised while introspecting — a bad/removed plugin
    must degrade to an empty panel, never crash the Create surface.

    Imports `artgen` lazily (inside this function), matching the convention
    already established by `create_mediums.default_mediums`: this module
    stays importable — and every OTHER panel here stays usable — even if the
    artgen package or its plugin-loading machinery is broken.
    """
    try:
        import artgen as _artgen
        gen = _artgen.get(generator_name)
    except Exception:
        return []

    parser = argparse.ArgumentParser()
    try:
        gen.add_args(parser)
    except Exception:
        return []

    # Resolve the shared-dest boolean pairs' real defaults (see module
    # comment) by parsing an empty arg list — exactly what a bare
    # `tt-ctl artgen <name>` invocation with no flags would resolve to.
    try:
        defaults_ns = vars(parser.parse_args([]))
    except SystemExit:
        defaults_ns = {}

    # Group every action by dest FIRST so a shared-dest boolean pair (e.g.
    # landscape's --mountains/--no-mountains) can contribute BOTH its positive
    # and negative CLI spellings to the one spec that dest becomes — see
    # `_bool_flag_pair`. (The old code dedup'd by dest keeping only the first
    # action, which discarded the negative spelling entirely.)
    actions_by_dest: "dict[str, list]" = {}
    dest_order: "list[str]" = []
    for action in getattr(parser, "_actions", []):
        dest = action.dest
        if dest == "help":
            continue
        if dest not in actions_by_dest:
            actions_by_dest[dest] = []
            dest_order.append(dest)
        actions_by_dest[dest].append(action)

    specs: "list[_ArgSpec]" = []
    for dest in dest_order:
        actions = actions_by_dest[dest]
        first = actions[0]  # keep first action's metadata (label/help), as before
        kind, choices = _classify_action(first)
        default = defaults_ns.get(dest, first.default)
        pos_flag = neg_flag = None
        none_default = False
        if kind == "bool":
            pos_flag, neg_flag = _bool_flag_pair(actions)
        elif kind in ("int", "float"):
            none_default = default is None
        specs.append(_ArgSpec(
            dest=dest, kind=kind, default=default,
            help=first.help or "", choices=choices,
            pos_flag=pos_flag, neg_flag=neg_flag, none_default=none_default,
        ))
    return specs


def artgen_bool_flags(
    generator_name: str,
) -> "dict[str, tuple[Optional[str], Optional[str]]]":
    """Map each boolean dest of *generator_name* to its ``(positive, negative)``
    CLI flag spellings, so the artgen run seam (`MainWindow._create_generate_
    artgen`) can emit the EXACT flag matching a switch state: the positive flag
    when the switch is ON, the negative flag (when the generator defines one)
    when OFF.

    Returns ``{}`` for an unknown/broken generator — same fail-soft contract as
    `_introspect_generator_args`. Kept here (not in `pipeline_engine`) so the
    shared `_append_flag_value` stays generator-agnostic; the bool-spelling
    knowledge lives with the introspection that produced it.
    """
    flags: "dict[str, tuple[Optional[str], Optional[str]]]" = {}
    for spec in _introspect_generator_args(generator_name):
        if spec.kind == "bool":
            flags[spec.dest] = (spec.pos_flag, spec.neg_flag)
    return flags


class ArtgenParamPanel(CreateParamPanel):
    """One param panel class for every artgen generator (verse/ansi/
    landscape/…) — parameterized by generator NAME at construction, not
    subclassed per generator (see module comment for the CRITICAL STRATEGY
    this implements).

    `collect()` returns exactly `{dest: value}` for every argparse dest the
    named generator's `add_args` declares — this IS the params dict a later
    task (the artgen run-path wiring) feeds straight to `argparse.Namespace`
    construction / `generate_artifact`, no key translation needed.
    """

    def __init__(self, generator_name: str) -> None:
        self._generator_name = generator_name
        self._widget: Optional[Gtk.Widget] = None
        self._controls: "list[_ArgControl]" = []
        # dest -> built row widget, populated by build() — see
        # ImageParamPanel's `_rows` for the rationale (RoleZonePanel
        # re-parenting, Task 5). Absent entirely when introspection finds no
        # args (the "no configurable parameters" label has no dest to key on).
        self._rows: "dict[str, Gtk.Widget]" = {}

    # ── CreateParamPanel protocol ────────────────────────────────────────────

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("artgen-param-panel")
        self._controls = []
        self._rows = {}

        try:
            specs = _introspect_generator_args(self._generator_name)
        except Exception:
            specs = []

        if not specs:
            label = Gtk.Label(
                label=f"{self._generator_name} — no configurable parameters"
            )
            label.add_css_class("artgen-param-empty-label")
            label.set_xalign(0.0)
            box.append(label)
        else:
            for spec in specs:
                row, control = self._build_control_row(spec)
                box.append(row)
                self._controls.append(control)
                self._rows[spec.dest] = row

        self._widget = box
        return box

    def collect(self) -> dict:
        """Read every mounted control's current value into `{dest: value}`.

        Empty before `build()` (or if introspection found no args) —
        deliberately `{}`, not a defaulted dict, since there is no fixed
        kwarg contract to fall back to the way Image/Video/Animate have
        (those wrap ONE fixed worker signature; this wraps N different
        generators' N different argparse shapes)."""
        result: dict = {}
        for control in self._controls:
            try:
                result[control.dest] = control.read()
            except Exception:
                result[control.dest] = None
        return result

    def field_specs(self) -> "list[FieldSpec]":
        """One `FieldSpec` per introspected argparse dest for this generator.

        Re-runs `_introspect_generator_args` fresh (does NOT require `build()`
        to have run first, and does not depend on `self._controls`) — same
        fail-soft contract as that function: an unknown/broken generator name
        yields `[]`, never raises. `role` comes from `field_roles.classify_artgen`,
        which is exactly the classifier the task brief specifies for artgen
        dests (unlike the native panels, no key needs a hand-rolled override —
        `classify_artgen` already handles bool/int/float/choice/str uniformly).
        """
        try:
            arg_specs = _introspect_generator_args(self._generator_name)
        except Exception:
            arg_specs = []
        return [
            FieldSpec(
                key=spec.dest,
                label=_humanize_dest(spec.dest),
                kind=spec.kind,
                default=spec.default,
                role=field_roles.classify_artgen(spec),
                choices=spec.choices,
                tooltip=spec.help,
            )
            for spec in arg_specs
        ]

    # ── Internals ─────────────────────────────────────────────────────────────

    def _row(self, label_text: str) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("artgen-param-row")
        label = Gtk.Label(label=label_text)
        label.add_css_class("artgen-param-label")
        label.set_xalign(0.0)
        label.set_size_request(120, -1)
        row.append(label)
        return row

    def _build_control_row(self, spec: _ArgSpec) -> "tuple[Gtk.Box, _ArgControl]":
        row = self._row(_humanize_dest(spec.dest))
        tooltip = spec.help or None

        if spec.kind == "choice":
            choices = spec.choices or []
            labels = [str(c) for c in choices]
            dropdown = Gtk.DropDown(model=Gtk.StringList.new(labels))
            dropdown.add_css_class("artgen-param-input")
            default_idx = choices.index(spec.default) if spec.default in choices else 0
            dropdown.set_selected(default_idx)
            if tooltip:
                dropdown.set_tooltip_text(tooltip)
            row.append(dropdown)
            widget = dropdown

        elif spec.kind == "bool":
            switch = Gtk.Switch()
            switch.set_valign(Gtk.Align.CENTER)
            switch.set_active(bool(spec.default))
            switch.add_css_class("artgen-param-input")
            if tooltip:
                switch.set_tooltip_text(tooltip)
            row.append(switch)
            widget = switch

        elif spec.kind == "int":
            default_val = int(spec.default) if isinstance(spec.default, int) else 0
            adj = Gtk.Adjustment(
                value=default_val, lower=-1_000_000, upper=1_000_000,
                step_increment=1, page_increment=10,
            )
            spin = Gtk.SpinButton(adjustment=adj, climb_rate=1, digits=0)
            spin.set_numeric(True)
            spin.add_css_class("artgen-param-input")
            if spec.default is None:
                spin.set_tooltip_text((tooltip + " " if tooltip else "") + "(0 = generator default)")
            elif tooltip:
                spin.set_tooltip_text(tooltip)
            row.append(spin)
            widget = spin

        elif spec.kind == "float":
            default_val = float(spec.default) if isinstance(spec.default, (int, float)) else 0.0
            adj = Gtk.Adjustment(
                value=default_val, lower=-1_000_000.0, upper=1_000_000.0,
                step_increment=0.1, page_increment=1.0,
            )
            spin = Gtk.SpinButton(adjustment=adj, climb_rate=0.1, digits=2)
            spin.set_numeric(True)
            spin.add_css_class("artgen-param-input")
            if tooltip:
                spin.set_tooltip_text(tooltip)
            row.append(spin)
            widget = spin

        else:  # "str"
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            entry.add_css_class("artgen-param-input")
            if isinstance(spec.default, str):
                entry.set_text(spec.default)
            if tooltip:
                entry.set_placeholder_text(tooltip)
                entry.set_tooltip_text(tooltip)
            row.append(entry)
            widget = entry

        control = _ArgControl(
            dest=spec.dest, kind=spec.kind, widget=widget,
            choices=spec.choices or [], none_default=spec.none_default,
        )
        return row, control


# ─────────────────────────────────────────────────────────────────────────────
# ModifierPills (Task 3 — docs/superpowers/plans/2026-07-13-create-surface.md)
# ─────────────────────────────────────────────────────────────────────────────
#
# Reusable widget for the Create surface's "Direction" zone (built in a later
# task) and, eventually, pipeline text fields: tapping a category-grouped "add"
# chip creates a visible, REMOVABLE pill; `applied_text()` returns the
# space-joined modifier text (in click order) to append to the brief.
#
# `load_chips_for_kind` is a thin seam over `chip_config.load_chips` — tests
# monkeypatch it directly so they never depend on `config/prompt_chips.yaml`'s
# actual contents, and a broken/missing config file degrades to "no chips"
# rather than raising (same fail-soft convention as `_introspect_generator_args`
# above).


def load_chips_for_kind(kind: str) -> "list":
    """Seam over `chip_config.load_chips(kind)` — returns `[]` for an unknown
    kind or any load error (missing/malformed YAML), never raises. Tests
    monkeypatch this attribute directly to force a known bank."""
    try:
        from chip_config import load_chips
        return load_chips(kind)
    except Exception:
        return []


class ModifierPills(Gtk.Box):
    """Category-grouped "add" chips that turn into removable pills.

    Every chip row (both the "applied" row and each category's "add" row) is
    a `Gtk.FlowBox` — NEVER a plain horizontal `Gtk.Box` — so a long bank of
    chips wraps onto additional lines instead of overflowing the panel width.

    `self._applied` is an ordered `list[ChipEntry]` recording click order;
    `applied_text()` space-joins each entry's `.text` in that order. Clicking
    an "add" chip calls `_apply_entry`; clicking a pill's "✕" calls
    `_remove_entry`. Both re-render just the applied-pills row (the add-chip
    rows never change after construction).
    """

    def __init__(self, kind: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class("modifier-pills")

        self._kind = kind
        self._applied: "list" = []  # ordered list[ChipEntry], click order

        try:
            self._categories = load_chips_for_kind(kind) or []
        except Exception:
            self._categories = []

        # Applied-pills row: rebuilt on every apply/remove.
        self._applied_flow = Gtk.FlowBox()
        self._applied_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._applied_flow.add_css_class("modifier-pills-applied")
        self.append(self._applied_flow)

        # One "add" FlowBox per category — built once, never rebuilt.
        for category in self._categories:
            self.append(self._build_category_box(category))

        self._render_applied()

    # ── Public API ───────────────────────────────────────────────────────────

    def applied_text(self) -> str:
        """Space-joined `.text` of every applied pill, in click order."""
        return " ".join(entry.text for entry in self._applied)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _build_category_box(self, category) -> Gtk.Widget:
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        group.add_css_class("modifier-pills-category")

        header = Gtk.Label(label=category.name)
        header.add_css_class("modifier-pills-category-label")
        header.set_xalign(0.0)
        group.append(header)

        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.add_css_class("modifier-pills-add-row")
        for entry in category.chips:
            btn = Gtk.Button(label=f"+ {entry.label}")
            btn.add_css_class("create-addchip")
            if entry.tip:
                btn.set_tooltip_text(entry.tip)
            btn.connect("clicked", lambda _b, e=entry: self._apply_entry(e))
            flow.append(btn)
        group.append(flow)

        return group

    def _apply_entry(self, entry) -> None:
        self._applied.append(entry)
        self._render_applied()

    def _remove_entry(self, entry) -> None:
        if entry in self._applied:
            self._applied.remove(entry)
        self._render_applied()

    def _render_applied(self) -> None:
        """Rebuild the applied-pills FlowBox from `self._applied`."""
        child = self._applied_flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._applied_flow.remove(child)
            child = nxt

        for entry in self._applied:
            pill = Gtk.Button(label=f"{entry.label} ✕")
            pill.add_css_class("create-pill")
            if entry.tip:
                pill.set_tooltip_text(entry.tip)
            pill.connect("clicked", lambda _b, e=entry: self._remove_entry(e))
            self._applied_flow.append(pill)


# ─────────────────────────────────────────────────────────────────────────────
# RoleZonePanel (Task 5 — docs/superpowers/plans/2026-07-13-create-surface.md,
# `.superpowers/sdd/task-5-brief.md`)
# ─────────────────────────────────────────────────────────────────────────────
#
# THE HARD MIGRATION INVARIANT: `RoleZonePanel.collect()` MUST return the
# wrapped panel's `collect()` output byte-for-byte. That dict feeds real
# generation workers (ImageGenerationWorker / GenerationWorker /
# AnimateGenerationWorker / the artgen run seam) — any drift here is a
# regression in what actually gets generated, not a cosmetic bug.
#
# The way this is guaranteed: RoleZonePanel calls `panel.build()` exactly
# ONCE, then RE-PARENTS the panel's already-built per-field row widgets (via
# the base class's `_row_for(key)`, populated by every concrete panel's own
# `build()` — see each panel's `_rows` dict above) into zone containers. It
# NEVER constructs a new widget bound to a field's value and NEVER touches
# the panel's adjustments/entries/dropdowns directly. Because the panel keeps
# ownership of those exact widget instances, `panel.collect()` reads them
# unchanged no matter which container currently parents them — GTK4 widgets
# don't care who their parent is for the purpose of `get_value()`/`get_text()`
# etc. `RoleZonePanel.collect()` itself is a one-line passthrough.
#
# ── The "model" special case ─────────────────────────────────────────────────
#
# `FieldSpec.kind == "model"` is explicitly NOT one of the brief/direction/
# control zones (see that dataclass's own docstring) — model selection is a
# separate concern a later task's caller (CreateView) renders itself.
# RoleZonePanel therefore skips any spec with `kind == "model"` entirely: it
# never looks up a row for it and never re-parents anything. (AnimateParamPanel
# doesn't even build a row for its `model` spec — there is no dropdown, just a
# fixed id — so skipping it is also the only way to avoid `_row_for` returning
# `None` and being treated as an error.)
#
# ── Marker-glyph labeling ─────────────────────────────────────────────────────
#
# Every OTHER relocated field gets its row's label text rewritten to
# `f"{MARKER_GLYPH[marker]} {spec.label}"` and a tooltip from `MARKER_TIP`,
# regardless of which zone it lands in — brief, direction, or control. This
# is a label/tooltip-only mutation (never touches the value-bearing widget
# next to the label), so it cannot affect `collect()`. The ONE override:
# `spec.kind == "path"` fields (e.g. Animate's reference video/character
# image, classified brief/words) get a neutral tooltip instead of
# `MARKER_TIP[MARK_WORDS]` ("Your words — the model turns this into art."),
# which reads strangely floating next to a file-path picker row (Task 4's
# noted oddity, carried forward and fixed here).


class RoleZonePanel(Gtk.Box):
    """Shared three-zone renderer wrapping ANY `CreateParamPanel`.

    Lays the wrapped panel's fields into three visual zones per
    `field_specs()`'s roles:

      - **Your brief** — the panel's `ROLE_BRIEF` fields (e.g. Image/Video's
        `negative_prompt`, Animate's reference video/image paths). The
        MAIN prompt itself is NOT owned here — CreateView's idea-door prompt
        entry persists across medium swaps and sits directly above wherever
        this panel is mounted, so the prompt and this zone read as one region.
      - **Direction** — a `ModifierPills(medium.kind)` followed by the
        panel's `ROLE_DIRECTION` fields.
      - **Controls** — a collapsed `Gtk.Expander` holding the panel's
        `ROLE_CONTROL` fields in a wrapping `Gtk.FlowBox` grid.

    See the module comment above for the hard `collect()` invariant this
    class exists to preserve.
    """

    def __init__(self, panel: CreateParamPanel, medium) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("role-zone-panel")

        self._panel = panel

        # Build the wrapped panel's widgets EXACTLY ONCE. Everything below
        # re-parents these widgets into zone containers — it never rebuilds
        # them — so `panel.collect()` keeps reading its own, unchanged
        # widget instances (the hard invariant, see module comment).
        panel.build()

        try:
            specs = panel.field_specs()
        except Exception:
            specs = []

        # ── "Your brief" zone ────────────────────────────────────────────
        self._brief_zone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._brief_zone.add_css_class("role-zone-brief-body")
        brief_frame = Gtk.Frame(label="Your brief")
        brief_frame.add_css_class("role-zone-brief")
        brief_frame.set_child(self._brief_zone)

        # ── "Direction" zone ─────────────────────────────────────────────
        self._direction_zone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._direction_zone.add_css_class("role-zone-direction-body")
        self._modifier_pills = ModifierPills(medium.kind)
        self._direction_zone.append(self._modifier_pills)
        direction_frame = Gtk.Frame(label="Direction")
        direction_frame.add_css_class("role-zone-direction")
        direction_frame.set_child(self._direction_zone)

        # ── "Controls" zone (collapsed expander, wrapping grid) ─────────
        self._controls_grid = Gtk.FlowBox()
        self._controls_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self._controls_grid.add_css_class("role-zone-controls-grid")
        self._controls_expander = Gtk.Expander(label="Controls")
        self._controls_expander.add_css_class("role-zone-controls")
        self._controls_expander.set_expanded(False)  # collapsed by default
        self._controls_expander.set_child(self._controls_grid)

        # ── Re-parent every field's row into its zone ───────────────────
        for spec in specs:
            if spec.kind == "model":
                # Deliberately not a zone field — see module comment.
                continue
            row = panel._row_for(spec.key)
            if row is None:
                # No built widget to relocate for this key (should not
                # normally happen for a non-model spec, but never crash the
                # Create surface over a panel/spec mismatch).
                continue
            row.unparent()
            self._relabel_row(row, spec)
            self._zone_for_role(spec.role.role).append(row)

        self.append(brief_frame)
        self.append(direction_frame)
        self.append(self._controls_expander)

    # ── Public API ───────────────────────────────────────────────────────────

    def collect(self) -> dict:
        """Return the wrapped panel's `collect()` output verbatim — see the
        module comment's hard migration invariant."""
        return self._panel.collect()

    def applied_modifier_text(self) -> str:
        """Space-joined applied-modifier text from the Direction zone's
        `ModifierPills` — CreateView reads this to build the final prompt
        (the modifier text is NOT injected into `collect()`'s dict here)."""
        return self._modifier_pills.applied_text()

    def append_modifier_for_test(self, text: str) -> None:
        """Test hook: apply a synthetic modifier entry so a later task can
        assert prompt assembly without depending on the real chip bank's
        contents (`config/prompt_chips.yaml`)."""
        from chip_config import ChipEntry
        self._modifier_pills._apply_entry(ChipEntry(label=text, text=text, tip=""))

    # ── Internals ─────────────────────────────────────────────────────────────

    def _zone_for_role(self, role: str) -> Gtk.Widget:
        if role == field_roles.ROLE_BRIEF:
            return self._brief_zone
        if role == field_roles.ROLE_DIRECTION:
            return self._direction_zone
        return self._controls_grid  # ROLE_CONTROL, and any unknown role

    def _relabel_row(self, row: Gtk.Widget, spec: FieldSpec) -> None:
        """Rewrite *row*'s label text to `"{glyph} {label}"` and set a
        marker tooltip — see module comment for the `kind == "path"`
        override. A no-op if the row's first child isn't a `Gtk.Label`
        (defensive; every panel's `_row()` helper puts the label first)."""
        label_widget = row.get_first_child()
        if not isinstance(label_widget, Gtk.Label):
            return
        glyph = field_roles.MARKER_GLYPH.get(spec.role.marker, "")
        label_widget.set_label(f"{glyph} {spec.label}".strip())
        if spec.kind == "path":
            tip = spec.tooltip or "Reference input the model reads"
        else:
            tip = field_roles.MARKER_TIP.get(spec.role.marker, "")
        if tip:
            label_widget.set_tooltip_text(tip)

    def _direction_label_texts(self) -> "list[str]":
        """Test helper: the rendered label strings of every row currently in
        the Direction zone (the `ModifierPills` widget itself is skipped —
        it has no row-shaped label)."""
        texts: "list[str]" = []
        child = self._direction_zone.get_first_child()
        while child is not None:
            if child is not self._modifier_pills:
                label_widget = child.get_first_child()
                if isinstance(label_widget, Gtk.Label):
                    texts.append(label_widget.get_label())
            child = child.get_next_sibling()
        return texts
