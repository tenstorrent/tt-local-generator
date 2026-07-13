# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
CreateParamPanel protocol + the first real per-medium panel: ImageParamPanel
(Create-surface plan, Task 4:
docs/superpowers/plans/2026-07-13-create-surface.md).

CreateView (Task 3, `app/create_view.py`) hosts one panel per medium chip.
Task 3 shipped only a stub label; this task ports the IMAGE medium to a real
panel while every other medium keeps the stub until its own task lands
(video/animate: Task 5, artgen: Task 6).

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
