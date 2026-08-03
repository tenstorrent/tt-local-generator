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
     "guidance_scale": float, "model": str, "seed_image_path": str}

Defaults mirror `ControlPanel.__init__`'s image defaults (`main_window.py`:
`_steps=20`, `_seed=-1`, `_guidance=3.5`, `_image_model="flux"`, `_neg=""`)
so the two surfaces agree on a first-run experience even though no code is
shared. `seed_image_path` (SP-3c-1, `.superpowers/sdd/task-1-brief.md`)
defaults to "" — a migration-safe addition; an empty path preserves today's
exact text-to-image behavior, and only image-to-image generation needs it
non-empty. `VideoParamPanel.collect()` gained the identical
`seed_image_path` key the same task, for the same reason plus re-enabling
SkyReels-I2V (an image-to-video model that needs a conditioning image).
"""
from __future__ import annotations

import argparse
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, Gio, GdkPixbuf, GLib, Gtk  # noqa: E402

import field_roles
from app_settings import settings as _settings


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
                 "str" | "path" | "model" | "dict". "model" is a deliberate
                 special case (see module docstring's per-panel notes below)
                 — model selection is not one of the brief/direction/control
                 zones, it is handled as its own concern by the caller.
                 "dict" (SP-3c-2) covers `VideoParamPanel`'s single
                 "animatediff_args" field — a cohesive group of sub-controls
                 collected together rather than each getting its own spec.
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


def _load_pixbuf(path: str, width: int, height: int) -> "Optional[GdkPixbuf.Pixbuf]":
    """Load *path* scaled to fit within width×height, preserving aspect ratio.

    A small local counterpart to `main_window._load_pixbuf` — duplicated
    rather than imported (this module must never import from `main_window`;
    that direction already runs the other way) so `SeedImageWell` below has
    no ControlPanel dependency at all. Returns `None` on any failure (missing
    file, unreadable image format, …) so a caller can fall back to a
    placeholder instead of raising.
    """
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, width, height, True)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SeedImageWell (SP-3c-1: docs/superpowers/specs/2026-07-13-sp3c-migrate-into-
# create-design.md, section 3c-1) — a small, reusable seed-image / i2i
# conditioning-image well shared by `ImageParamPanel` and `VideoParamPanel`.
# ─────────────────────────────────────────────────────────────────────────────
#
# Adapted from ControlPanel's `_seed_thumb_box` (main_window.py, ~line 4250):
# same visual contract (a small square thumbnail; left-click to browse;
# right-click to clear) but a DELIBERATELY LEANER implementation.
# ControlPanel's well opens a full `PickerPopover` (Gallery + Disk tabs,
# needs a live `_store`/history-record reference it reads at click time) —
# this widget has NO app-state dependencies at all, so its click handler
# opens a plain `Gtk.FileDialog` instead: async `open()` + `open_finish()`
# wrapped in try/except, per CLAUDE.md's FileDialog gotcha, exactly mirroring
# the existing `AnimateParamPanel._on_pick_ref_video`/`_on_pick_ref_image`
# precedent already in this module. A `Gtk.DropTarget` additionally accepts
# one dropped file (e.g. dragged in from a file manager).
class SeedImageWell(Gtk.Box):
    """Click-to-browse / drop-to-set / right-click-to-clear seed-image well.

    Public API: `path() -> str`, `set_path(path)`, `clear()`. `set_path("")`
    and `clear()` are equivalent. A path that isn't a regular file (a
    directory, or one that has since vanished) is silently rejected back to
    "" — mirrors ControlPanel's `_set_seed_image` guard: a directory passes
    `Path.exists()` but not `Path.is_file()`, and handing one to a worker's
    `read_bytes()` call raises at generation time instead of failing here,
    where the mistake is cheap and obvious.
    """

    def __init__(self, size: int = 40) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._path: str = ""
        self._size = size
        self.set_size_request(size, size)
        # Fixed compact square — never stretch to fill a tall row/frame (that
        # turned the empty well into a huge vertical bar and made the form wonky).
        self.set_vexpand(False)
        self.set_hexpand(False)
        self.set_valign(Gtk.Align.START)
        self.set_halign(Gtk.Align.START)
        self.add_css_class("seed-image-well")
        self._set_empty_tooltip()

        self.append(self._build_placeholder())

        # Left-click: open a plain Gtk.FileDialog (async, per CLAUDE.md).
        click = Gtk.GestureClick()
        click.set_button(1)  # primary mouse button
        click.connect("released", lambda _g, _n, _x, _y: self._open_file_dialog())
        self.add_controller(click)

        # Right-click: clear the current seed image.
        rclick = Gtk.GestureClick()
        rclick.set_button(3)  # secondary mouse button
        rclick.connect("released", lambda _g, _n, _x, _y: self.clear())
        self.add_controller(rclick)

        # Drop target: accepts one dropped file (e.g. from a file manager).
        # Gio.File is the type GTK4 resolves a single-file drop to on every
        # desktop this app targets (Nautilus, Files, GNOME/KDE file pickers).
        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

    # ── public API (consumed by ImageParamPanel/VideoParamPanel.collect()) ──

    def path(self) -> str:
        """Current seed image path, or "" when none is set."""
        return self._path

    def set_path(self, path: str) -> None:
        """Set the seed image path and refresh the thumbnail in place.

        See the class docstring for the directory/missing-file guard.
        """
        if path and not Path(path).is_file():
            path = ""
        self._path = path
        self._refresh_thumbnail()

    def clear(self) -> None:
        """Clear the current seed image. Equivalent to `set_path("")`."""
        self.set_path("")

    # ── internals ─────────────────────────────────────────────────────────────

    def _build_placeholder(self) -> Gtk.Label:
        lbl = Gtk.Label(label="\U0001f5bc")  # picture-frame glyph
        lbl.set_vexpand(True)
        lbl.set_valign(Gtk.Align.CENTER)
        return lbl

    def _refresh_thumbnail(self) -> None:
        """Replace every child with either a scaled thumbnail Picture or the
        placeholder icon, mirroring `ControlPanel._set_seed_image`'s well
        update (main_window.py)."""
        child = self.get_first_child()
        while child is not None:
            self.remove(child)
            child = self.get_first_child()

        if self._path:
            interior = max(self._size - 4, 8)
            pb = _load_pixbuf(self._path, interior, interior)
            if pb is not None:
                img = Gtk.Picture.new_for_pixbuf(pb)
                img.set_size_request(interior, interior)
                img.set_can_shrink(False)
                img.set_vexpand(True)
                self.append(img)
            else:
                # Pixbuf load failed (unreadable/corrupt file) — a
                # question-mark placeholder, same fallback ControlPanel uses.
                lbl = Gtk.Label(label="?")
                lbl.set_vexpand(True)
                lbl.set_valign(Gtk.Align.CENTER)
                self.append(lbl)
            self.add_css_class("has-seed")
            self.set_tooltip_text("Seed image set — right-click to clear")
        else:
            self.append(self._build_placeholder())
            self.remove_css_class("has-seed")
            self._set_empty_tooltip()

    def _set_empty_tooltip(self) -> None:
        self.set_tooltip_text(
            "Seed image — click to browse, right-click to clear\n"
            "Drop an image file here to use as seed image"
        )

    def _open_file_dialog(self) -> None:
        dlg = Gtk.FileDialog()
        dlg.set_title("Select seed image")
        # Derive the transient parent from the live widget tree at click
        # time (not a cached reference) — matches AnimateParamPanel's
        # `_on_pick_ref_video`/`_on_pick_ref_image` rationale: once a
        # RoleZonePanel re-parents this well elsewhere, `get_root()` still
        # resolves to the real window as long as the well itself is mounted.
        dlg.open(self.get_root(), None, self._on_file_picked)

    def _on_file_picked(self, dlg: "Gtk.FileDialog", result: "Gio.AsyncResult") -> None:
        try:
            gfile = dlg.open_finish(result)
        except Exception:
            return  # user cancelled — leave the existing seed untouched
        if gfile is not None:
            path = gfile.get_path()
            if path:
                self.set_path(path)

    def _on_drop(self, _target: "Gtk.DropTarget", value: "Gio.File", _x: float, _y: float) -> bool:
        """`Gtk.DropTarget` "drop" signal handler. *value* is a `Gio.File`
        (the single type this target was constructed with)."""
        try:
            path = value.get_path() if value is not None else None
        except Exception:
            path = None
        if path:
            self.set_path(path)
            return True
        return False


# ─────────────────────────────────────────────────────────────────────────────
# SeedModeControl (SP-3d-2: docs/superpowers/specs/2026-07-17-sp3d-delete-
# vestiges-design.md, section 3d-2; `.superpowers/sdd/task-2-brief.md`) --
# random / repeat-last / keep-this seed mode, shared by every native Create
# param panel that exposes a seed field (Image/Video/Animate).
#
# Migrates ControlPanel's three-way seed-mode toggle (main_window.py's
# `_seed_random_btn`/`_seed_repeat_btn`/`_seed_keep_btn` radio group,
# `_on_seed_mode`, `_apply_seed_mode_from_settings`, ~line 5024-5140) into
# Create so the feature survives ControlPanel's eventual deletion (SP-3d --
# CLAUDE.md/the audit call this out explicitly as a "never drop" migration,
# not a silent feature loss).
#
# Persists to the EXACT SAME `seed_mode` settings key ControlPanel reads and
# writes (`app_settings.DEFAULTS["seed_mode"]`) -- not a forked "create seed
# mode" key -- so the two surfaces always agree on which mode is active:
# picking "Repeat last" here and later opening the legacy ControlPanel (or
# vice versa) shows the same mode selected.
# ─────────────────────────────────────────────────────────────────────────────

# (internal key, dropdown label) -- persisted value uses ControlPanel's own
# vocabulary ("random"/"repeat"/"keep") so `_settings.get("seed_mode")` means
# the same thing on both surfaces.
_SEED_MODE_CHOICES: "list[tuple[str, str]]" = [
    ("random", "\U0001f3b2 Random"),
    ("repeat", "\U0001f501 Repeat last"),
    ("keep", "\U0001f4cc Keep this"),
]
_SEED_MODE_KEYS: "list[str]" = [key for key, _label in _SEED_MODE_CHOICES]
_DEFAULT_SEED_MODE = "random"


def _last_used_seed() -> int:
    """Return the seed of the most recently generated record, or -1 (the
    "random" sentinel) when there is no history yet.

    Mirrors ControlPanel's `_on_seed_mode`/`_apply_seed_mode_from_settings`
    "repeat" branch (main_window.py ~5066-5140) EXACTLY: same source (a fresh
    `HistoryStore()` -- `HistoryStore.all_records()` proxies straight through
    to the singleton `media_store.media_store`, so a fresh instance here sees
    the IDENTICAL records ControlPanel's own `self._store` does, no forked
    persistence), same derivation (sort ascending by `created_at`, take the
    last one). Fails soft to -1 on any error (store not yet initialised,
    corrupt record, etc.) -- same fallback ControlPanel uses when "repeat" is
    requested with empty history.
    """
    try:
        from history_store import HistoryStore
        recs = HistoryStore().all_records()
    except Exception:
        return -1
    if not recs:
        return -1
    try:
        last = sorted(recs, key=lambda r: getattr(r, "created_at", ""))[-1]
        seed = getattr(last, "seed", -1)
        return int(seed) if seed is not None else -1
    except Exception:
        return -1


class SeedModeControl(Gtk.Box):
    """Compact random/repeat-last/keep selector -- a single `Gtk.DropDown`,
    not ControlPanel's three separate toggle buttons, to fit the Controls
    zone's one-row-per-field layout.

    `on_change`, if given, is called with the new mode string every time the
    user picks a different entry -- panels use it to write-through "random"'s
    -1 sentinel into their own seed Adjustment immediately (see
    `ImageParamPanel._on_seed_mode_changed` and its Video/Animate mirrors).
    "repeat" is deliberately NOT resolved here or via write-through: the
    "last used seed" can change between mode-selection and the next Create
    click (e.g. another medium finishes generating in the background), so it
    is re-resolved fresh at `collect()` time instead (see `_collect_seed`).

    MIGRATION-SAFE: a panel that never builds this control has `_seed_mode is
    None`, and `_collect_seed(seed_adj, None)` falls back to reading the seed
    Adjustment's raw value unchanged -- the exact pre-existing behaviour.
    """

    def __init__(
        self,
        on_change: "Optional[callable]" = None,
        css_class: str = "",
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.add_css_class("seed-mode-control")
        self._on_change = on_change

        labels = [label for _key, label in _SEED_MODE_CHOICES]
        self._dropdown = Gtk.DropDown(model=Gtk.StringList.new(labels))
        if css_class:
            self._dropdown.add_css_class(css_class)
        self._dropdown.set_tooltip_text(
            "Random: a new seed every generation\n"
            "Repeat last: reuse the most recently generated seed\n"
            "Keep this: use the seed value typed in the field"
        )
        self.append(self._dropdown)

        # Initialise from the SAME settings key ControlPanel persists to (see
        # module comment above) -- an unknown/corrupt saved value falls back
        # to "random", matching `_DEFAULT_SEED_MODE`/`app_settings.DEFAULTS`.
        saved_mode = str(_settings.get("seed_mode") or _DEFAULT_SEED_MODE)
        if saved_mode not in _SEED_MODE_KEYS:
            saved_mode = _DEFAULT_SEED_MODE
        self._mode = saved_mode
        self._dropdown.set_selected(_SEED_MODE_KEYS.index(saved_mode))

        # Connected AFTER set_selected() above so construction-time init
        # doesn't immediately re-fire on_change/re-persist the value it just
        # read from settings.
        self._dropdown.connect("notify::selected", self._on_dropdown_changed)

    @property
    def mode(self) -> str:
        """Current mode string -- one of "random"/"repeat"/"keep"."""
        return self._mode

    def _on_dropdown_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        idx = dropdown.get_selected()
        if idx < 0 or idx >= len(_SEED_MODE_KEYS):
            return
        mode = _SEED_MODE_KEYS[idx]
        self._mode = mode
        # Persist to the SAME key ControlPanel's own `_on_seed_mode`
        # (main_window.py) reads/writes -- reusing the existing key/logic,
        # not forking a parallel "create seed mode" setting.
        _settings.set("seed_mode", mode)
        if self._on_change is not None:
            try:
                self._on_change(mode)
            except Exception:
                pass  # fail-soft: a broken write-through must not crash the UI


def _collect_seed(
    seed_adj: "Optional[Gtk.Adjustment]",
    seed_mode: "Optional[SeedModeControl]",
) -> int:
    """Resolve the seed value `collect()` should forward, given a panel's own
    seed Adjustment and (optional) `SeedModeControl`.

    Every case but "repeat" simply reads the Adjustment's raw value -- this
    is the exact PRE-EXISTING behaviour (no mode concept at all), which is
    why it's also what a panel with no `SeedModeControl` built yet falls back
    to. "random" doesn't need a special case here: selecting it write-throughs
    the Adjustment to -1 immediately (see `SeedModeControl`'s docstring), so
    reading the Adjustment already returns -1 for it. "keep" IS "read the
    Adjustment" by definition -- the seed field itself is the fixed value.
    Only "repeat" needs live resolution here, since the most-recently-used
    seed can change between mode-selection and this call.
    """
    fixed = int(seed_adj.get_value()) if seed_adj is not None else -1
    if seed_mode is not None and seed_mode.mode == "repeat":
        return _last_used_seed()
    return fixed


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
        # SP-3c-1: seed / i2i conditioning image well (empty path = today's
        # unchanged text-to-image behavior — see `SeedImageWell`'s own
        # docstring above and this class's `collect()`).
        self._seed_well: Optional[SeedImageWell] = None
        # SP-3d-2: random/repeat-last/keep seed mode, migrated from
        # ControlPanel — see `SeedModeControl`'s docstring above and
        # `_resolved_seed()`/`collect()` below.
        self._seed_mode: Optional[SeedModeControl] = None
        # key -> built row widget, populated by build(). Lets RoleZonePanel
        # (Task 5) re-parent an already-built row into a zone by field key
        # via the base class's `_row_for` — see that method's docstring.
        self._rows: "dict[str, Gtk.Widget]" = {}

    # ── CreateParamPanel protocol ────────────────────────────────────────────

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("image-param-panel")

        self._rows = {}
        seed_image_row = self._build_seed_image_row()
        box.append(seed_image_row)
        self._rows["seed_image_path"] = seed_image_row

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
        even if called before `build()`. `seed_image_path` defaults to ""
        (no well built / no image chosen) — this is a MIGRATION-SAFE
        addition (SP-3c-1): an empty seed path preserves today's exact
        text-to-image behavior, only image-conditioned (i2i) generation
        needs it non-empty. `seed` (SP-3d-2) is resolved through
        `_collect_seed` per the active `SeedModeControl` mode — see that
        function's docstring; a panel built before this task (or with the
        control never built) falls back to the Adjustment's raw value
        unchanged, so the default random/-1 case is byte-for-byte identical
        to before.
        """
        return {
            "negative_prompt": self._neg_entry.get_text() if self._neg_entry is not None else "",
            "num_inference_steps": (
                int(self._steps_adj.get_value()) if self._steps_adj is not None else 20
            ),
            "seed": _collect_seed(self._seed_adj, self._seed_mode),
            "guidance_scale": (
                float(self._guidance_adj.get_value()) if self._guidance_adj is not None else 3.5
            ),
            "model": self._selected_model_id(),
            "seed_image_path": self._seed_well.path() if self._seed_well is not None else "",
        }

    def field_specs(self) -> "list[FieldSpec]":
        """One spec per `collect()` key (see that method's exact dict shape).

        `model` is deliberately `kind="model"` rather than being classified by
        `field_roles.classify_native` — model selection is not a brief/
        direction/control zone field, it's handled as its own concern by a
        later task's caller (see `FieldSpec.kind`'s docstring note). Every
        other key uses `classify_native(key)` unmodified so the roles agree
        with the shared vocabulary `field_roles.py` defines for native panels.

        `seed_image_path` (SP-3c-1) is classified the same way
        `AnimateParamPanel` classifies its `reference_video_path`/
        `reference_image_path` fields: `kind="path"` (a path-picker widget,
        not plain text) with an explicit `FieldRole(ROLE_BRIEF, MARK_WORDS)`
        — a seed image is user-supplied creative input the model conditions
        on (i2i), the same "raw material" role a text brief plays, not a
        control dial.
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
            FieldSpec(
                key="seed_image_path", label="Seed image", kind="path", default="",
                role=field_roles.FieldRole(field_roles.ROLE_BRIEF, field_roles.MARK_WORDS),
                tooltip="optional — starting image for image-to-image",
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

    def _build_seed_image_row(self) -> Gtk.Box:
        row = self._row("Seed image")
        self._seed_well = SeedImageWell()
        row.append(self._seed_well)
        hint = Gtk.Label(label="optional — image-to-image starting point")
        hint.add_css_class("image-param-hint")
        hint.set_xalign(0.0)
        row.append(hint)
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

        # SP-3d-2: random/repeat-last/keep mode selector, migrated from
        # ControlPanel — see `SeedModeControl`'s docstring.
        self._seed_mode = SeedModeControl(
            on_change=self._on_seed_mode_changed, css_class="image-param-input"
        )
        row.append(self._seed_mode)
        return row

    def _on_seed_mode_changed(self, mode: str) -> None:
        """Write-through for "random": immediately reset the spin to -1 so a
        stale "keep" value can never leak into a "random" generation.
        "repeat" and "keep" leave the spin untouched — "repeat" is resolved
        dynamically at `collect()` time (see `_collect_seed`), and "keep" IS
        the spin's own value by definition."""
        if mode == "random" and self._seed_adj is not None:
            self._seed_adj.set_value(-1)

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
# SkyReels-V2-I2V-14B-540P — RE-ENABLED (SP-3c-1). It was omitted from
# v0.27.1 through v0.35.x because it's an image-to-video model requiring a
# conditioning (character/seed) image, and the Video door collected NO image
# input at all — `_create_generate_native`'s video branch would have handed
# the I2V model `seed_image_path=""`, which can only fail server-side.
# `VideoParamPanel` now owns a `SeedImageWell` (same as `ImageParamPanel`;
# see that widget's docstring above), so a conditioning image IS available
# here — offering the choice is no longer a trap.
#
# SP-3c-2 adds "animatediff" — the NATIVE AnimateDiff v0.9 path (distinct
# from the artgen `animatediff` plugin medium, which stays a separate,
# artgen-sourced "gif" medium generating via `tt-ctl artgen animatediff`).
# Unlike the other three, this one runs through a completely serverless
# code path (`worker.AnimateDiffGenerationWorker`, no HTTP call at all) —
# see `_ANIMATEDIFF_DEFAULTS`/`_collect_animatediff_args` below for the full
# args config this option reveals.
_VIDEO_MODEL_CHOICES: "list[tuple[str, str]]" = [
    ("wan2", "Wan2.2 — 720p video"),
    ("mochi", "Mochi-1 — 480×848 video"),
    ("skyreels", "SkyReels-V2-I2V — 960×544 (needs a seed image)"),
    ("animatediff", "AnimateDiff — local, no server needed"),
]

# Internal key -> server-side model id string, passed as `model=` to
# GenerationWorker. Mirrors ControlPanel's `_VIDEO_MODEL_IDS` exactly,
# including the "animatediff" entry (main_window.py's own `_VIDEO_MODEL_IDS`
# maps it to the same "animatediff-blackhole" id) — SP-3c-2 needs the round
# trip create_view.py's `_VIDEO_MODEL_ID_TO_KEY` (in main_window.py) inverts
# to work for AnimateDiff too.
_VIDEO_MODEL_IDS: "dict[str, str]" = {
    "wan2": "wan2.2-t2v",
    "mochi": "mochi-1-preview",
    "skyreels": "skyreels-v2-i2v-14b-540p",
    "animatediff": "animatediff-blackhole",
}

# Dropdown choice lists for the AnimateDiff-specific options box — mirror
# `ControlPanel._build_animatediff_box()`'s own `_dd(...)` calls exactly
# (main_window.py ~5155/~5185/~5255).
_ANIMATEDIFF_MODE_CHOICES: "list[str]" = ["blackhole", "cpu", "sim"]
_ANIMATEDIFF_LIGHTNING_STEPS_CHOICES: "list[str]" = ["2", "4", "8"]
_ANIMATEDIFF_MOTION_SKIP_CHOICES: "list[str]" = [
    "None (full quality)", "Fast (skip up1 up2)", "Balanced (skip up2)",
]
# Skip-preset label -> the frame-name list AnimateDiffGenerationWorker
# expects, mirrors `ControlPanel.get_animatediff_args`'s `_dd_val`/if-elif
# translation (main_window.py ~5293-5299) exactly.
_ANIMATEDIFF_MOTION_SKIP_VALUES: "dict[str, Optional[list]]" = {
    "None (full quality)": None,
    "Fast (skip up1 up2)": ["up1", "up2"],
    "Balanced (skip up2)": ["up2"],
}

# Duplicated from `main_window._ANIMATEDIFF_DEFAULTS` per this module's
# "duplicate small, stable bits" strategy (see module docstring) — read
# `ControlPanel.get_animatediff_args()` (main_window.py ~5283) if the two
# ever need to be reconciled; this dict's keys/values must match it exactly
# so `_create_generate_native`'s `{**_ANIMATEDIFF_DEFAULTS, **animatediff_args}`
# merge (main_window.py) never disagrees with what this panel already
# collects as "complete" on its own.
_ANIMATEDIFF_DEFAULTS: dict = dict(
    mode="blackhole",
    negative_prompt="blurry, low quality",
    temporal_alpha=0.35,
    lightning=False,
    lightning_steps=4,
    multi_chip=True,
    device_id=None,
    chain_from=None,
    chain_save=False,
    chain_alpha=0.6,
    motion_adapter=None,
    motion_adapter_alpha=1.0,
    motion_adapter_skip=None,
)

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
        # SP-3c-1: seed image well — required for SkyReels-I2V, optional
        # (i2v-style conditioning) for the other video models. Empty path
        # preserves today's exact text-to-video behavior.
        self._seed_well: Optional[SeedImageWell] = None
        # SP-3d-2: random/repeat-last/keep seed mode, migrated from
        # ControlPanel — see `SeedModeControl`'s docstring.
        self._seed_mode: Optional[SeedModeControl] = None
        # key -> built row widget, populated by build() — see ImageParamPanel's
        # `_rows` for the rationale (RoleZonePanel re-parenting, Task 5).
        self._rows: "dict[str, Gtk.Widget]" = {}

        # SP-3c-2: native AnimateDiff option's own config widgets — mirror
        # `ControlPanel._build_animatediff_box()`/`get_animatediff_args()`
        # field-for-field (main_window.py ~5103/~5283). All are `None` until
        # `build()` runs; `_collect_animatediff_args()` falls back to
        # `_ANIMATEDIFF_DEFAULTS` in that case, same "never raise before
        # build()" contract every other collect() path in this file follows.
        self._ad_options_row: Optional[Gtk.Widget] = None
        self._ad_mode: Optional[Gtk.DropDown] = None
        self._ad_neg_prompt: Optional[Gtk.Entry] = None
        self._ad_temporal_alpha: Optional[Gtk.SpinButton] = None
        self._ad_lightning: Optional[Gtk.CheckButton] = None
        self._ad_lightning_steps: Optional[Gtk.DropDown] = None
        self._ad_lightning_steps_row: Optional[Gtk.Widget] = None
        self._ad_multi_chip: Optional[Gtk.CheckButton] = None
        self._ad_device_id: Optional[Gtk.SpinButton] = None
        self._ad_chain_from: Optional[Gtk.Entry] = None
        self._ad_chain_save: Optional[Gtk.CheckButton] = None
        self._ad_chain_alpha: Optional[Gtk.SpinButton] = None
        self._ad_motion_adapter: Optional[Gtk.CheckButton] = None
        self._ad_motion_skip: Optional[Gtk.DropDown] = None
        self._ad_injection_alpha: Optional[Gtk.SpinButton] = None

    # ── CreateParamPanel protocol ────────────────────────────────────────────

    def build(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("video-param-panel")

        self._rows = {}
        seed_image_row = self._build_seed_image_row()
        box.append(seed_image_row)
        self._rows["seed_image_path"] = seed_image_row

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

        ad_row = self._build_animatediff_options_row()
        box.append(ad_row)
        self._rows["animatediff_args"] = ad_row

        # Reflect the model dropdown's BUILT-IN default (see _build_model_row)
        # immediately, rather than waiting for a first "notify::selected" —
        # AnimateDiff isn't the default choice, so this is normally a no-op,
        # but it keeps build() honest if the default ever changes.
        self._update_animatediff_visibility()

        self._widget = box
        return box

    def collect(self) -> dict:
        """Read every widget's current value into the exact
        `GenerationWorker` kwarg dict (minus `prompt`).

        Falls back to the documented defaults for any widget that was never
        built — `collect()` should never raise even if called before
        `build()`. `seed_image_path` defaults to "" (MIGRATION-SAFE, SP-3c-1):
        an empty path preserves today's exact text-to-video behavior for
        wan2/mochi; only SkyReels-I2V requires it non-empty.

        `animatediff_args` (SP-3c-2) is ALWAYS a complete dict — every
        `_ANIMATEDIFF_DEFAULTS` key present — regardless of whether
        AnimateDiff is the currently-selected model, so a caller never has to
        special-case "panel wasn't showing AnimateDiff" before reading it;
        `_create_generate_native` (main_window.py) only actually forwards it
        to `_on_generate` when `model_key == "animatediff"`.

        `seed` (SP-3d-2) is resolved through `_collect_seed` per the active
        `SeedModeControl` mode — see `ImageParamPanel.collect()`'s docstring
        for the same migration-safety note.
        """
        return {
            "negative_prompt": self._neg_entry.get_text() if self._neg_entry is not None else "",
            "num_inference_steps": (
                int(self._steps_adj.get_value()) if self._steps_adj is not None else 20
            ),
            "seed": _collect_seed(self._seed_adj, self._seed_mode),
            "model": self._selected_model_id(),
            "num_frames": self._selected_num_frames(),
            "seed_image_path": self._seed_well.path() if self._seed_well is not None else "",
            "animatediff_args": self._collect_animatediff_args(),
        }

    def field_specs(self) -> "list[FieldSpec]":
        """One spec per `collect()` key (see that method's exact dict shape).

        `model` is `kind="model"` for the same reason as `ImageParamPanel`
        (model selection is handled as its own concern, not a zone field).
        `num_frames`'s 0="runner default" sentinel (see `_selected_num_frames`)
        is metadata-invisible here — the spec's `default` is the widget's
        starting value (0), matching the other native panels' convention of
        describing the built default, not the collect()-time semantics.
        `seed_image_path` mirrors `ImageParamPanel`'s own spec for the same
        field (see that class's `field_specs()` docstring for the rationale).
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
            FieldSpec(
                key="seed_image_path", label="Seed image", kind="path", default="",
                role=field_roles.FieldRole(field_roles.ROLE_BRIEF, field_roles.MARK_WORDS),
                tooltip="required for SkyReels-I2V; optional otherwise",
            ),
            FieldSpec(
                key="animatediff_args", label="AnimateDiff Options", kind="dict",
                default=dict(_ANIMATEDIFF_DEFAULTS),
                role=field_roles.FieldRole(field_roles.ROLE_CONTROL, field_roles.MARK_EXACT),
                tooltip=(
                    "AnimateDiff-specific settings (mode, temporal α, lightning, "
                    "chain continuity, MotionAdapter) — shown only when AnimateDiff "
                    "is the selected video model"
                ),
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

    def _selected_video_key(self) -> str:
        """The plain internal key ("wan2"/"mochi"/"skyreels"/"animatediff")
        behind the current model-dropdown selection — unlike
        `_selected_model_id()`, this is never translated to the canonical
        server id, so it's what `_update_animatediff_visibility` and
        `set_selected_model_key` compare against."""
        if self._model_dropdown is None:
            return _DEFAULT_VIDEO_MODEL_KEY
        idx = self._model_dropdown.get_selected()
        if idx < 0 or idx >= len(_VIDEO_MODEL_CHOICES):
            return _DEFAULT_VIDEO_MODEL_KEY
        return _VIDEO_MODEL_CHOICES[idx][0]

    def set_selected_model_key(self, key: str) -> None:
        """Programmatically select *key* in this panel's OWN model dropdown.

        SP-3c-2: `RoleZonePanel` deliberately never renders a wrapped panel's
        `kind == "model"` row (see that class's module comment) — the
        user-visible model picker is CreateView's SCOPED `_model_dropdown`
        instead. Without this hook this panel's internal model state would
        sit frozen at its own built-in default (`_DEFAULT_VIDEO_MODEL_KEY`)
        forever, so the AnimateDiff options box (whose visibility keys off
        THIS panel's own dropdown, not the scoped one) could never actually
        appear. CreateView calls this whenever the scoped dropdown's
        selection changes (see `create_view.py`'s `_sync_panel_model_selection`)
        so the two stay in lockstep. No-op if the dropdown isn't built yet or
        *key* isn't one of `_VIDEO_MODEL_CHOICES`.
        """
        if self._model_dropdown is None:
            return
        for idx, (choice_key, _label) in enumerate(_VIDEO_MODEL_CHOICES):
            if choice_key == key:
                self._model_dropdown.set_selected(idx)
                break
        self._update_animatediff_visibility()

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

    def _build_seed_image_row(self) -> Gtk.Box:
        row = self._row("Seed image")
        self._seed_well = SeedImageWell()
        row.append(self._seed_well)
        hint = Gtk.Label(label="required for SkyReels-I2V; optional otherwise")
        hint.add_css_class("video-param-hint")
        hint.set_xalign(0.0)
        row.append(hint)
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

        # SP-3d-2: random/repeat-last/keep mode selector, migrated from
        # ControlPanel — see `SeedModeControl`'s docstring.
        self._seed_mode = SeedModeControl(
            on_change=self._on_seed_mode_changed, css_class="video-param-input"
        )
        row.append(self._seed_mode)
        return row

    def _on_seed_mode_changed(self, mode: str) -> None:
        """Write-through for "random" — see `ImageParamPanel`'s identical
        method for the full rationale."""
        if mode == "random" and self._seed_adj is not None:
            self._seed_adj.set_value(-1)

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
        # SP-3c-2: toggle the AnimateDiff options box's visibility whenever
        # THIS panel's own (normally-hidden, see set_selected_model_key's
        # docstring) model selection changes — covers both a direct test
        # manipulating this widget and `set_selected_model_key`'s own
        # explicit call to the same toggle (GObject's `notify::selected` is
        # not guaranteed to fire when the index doesn't actually change).
        dropdown.connect("notify::selected", lambda *_a: self._update_animatediff_visibility())
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

    # ── AnimateDiff options (SP-3c-2) ────────────────────────────────────────
    #
    # Mirrors `ControlPanel._build_animatediff_box()` / `get_animatediff_args()`
    # (main_window.py ~5103-5317) field-for-field. Shown/hidden as one unit —
    # see `_update_animatediff_visibility` — rather than each individual
    # sub-control getting its own `FieldSpec`/zone placement, since these are
    # all one cohesive "AnimateDiff Options" concern (a single `FieldSpec`,
    # key="animatediff_args", carries the whole group into the Controls zone —
    # see `field_specs()`).

    def _update_animatediff_visibility(self) -> None:
        """Show the AnimateDiff options row only when "animatediff" is this
        panel's own currently-selected model key; hide it for every other
        video model. Also refreshes the lightning-steps sub-row's visibility
        (it depends on BOTH the lightning checkbox and the mode dropdown —
        see `_build_animatediff_options_row`), since a model-key change can
        indirectly matter there too (defensive; the two dropdowns are
        independent widgets, so this call is cheap and always safe)."""
        if self._ad_options_row is not None:
            self._ad_options_row.set_visible(self._selected_video_key() == "animatediff")
        self._sync_ad_lightning_steps_visibility()

    def _sync_ad_lightning_steps_visibility(self) -> None:
        """Mirrors `ControlPanel`'s `_on_ad_lightning_toggled` exactly
        (main_window.py ~5190-5195): the "Distill steps" row is visible only
        when Lightning mode is checked AND the AnimateDiff mode dropdown is
        set to "cpu" (Lightning has no effect on blackhole/sim)."""
        if self._ad_lightning is None or self._ad_lightning_steps_row is None:
            return
        on = self._ad_lightning.get_active()
        cpu = self._ad_mode is not None and self._ad_dd_val(self._ad_mode) == "cpu"
        self._ad_lightning_steps_row.set_visible(on and cpu)

    def _ad_dd_val(self, dd: Gtk.DropDown) -> str:
        """Read a `Gtk.DropDown`'s currently-selected string — mirrors
        `ControlPanel.get_animatediff_args()`'s local `_dd_val` helper
        (main_window.py ~5286-5291) exactly."""
        idx = dd.get_selected()
        m = dd.get_model()
        if m and idx < m.get_n_items():
            return m.get_string(idx)
        return ""

    def _ad_dd(self, items: "list[str]", default: str) -> Gtk.DropDown:
        """Build a `Gtk.DropDown` from a plain string list, pre-selecting
        *default* — mirrors `ControlPanel._build_animatediff_box()`'s local
        `_dd` helper (main_window.py ~5129-5138) exactly."""
        sl = Gtk.StringList()
        for item in items:
            sl.append(item)
        dd = Gtk.DropDown(model=sl)
        try:
            dd.set_selected(items.index(default))
        except ValueError:
            pass
        return dd

    def _ad_spin(self, lo: float, hi: float, step: float, val: float) -> Gtk.SpinButton:
        """Mirrors `ControlPanel._build_animatediff_box()`'s local `_spin`
        helper (main_window.py ~5147-5152) exactly."""
        adj = Gtk.Adjustment(value=val, lower=lo, upper=hi, step_increment=step)
        sb = Gtk.SpinButton(adjustment=adj)
        sb.set_digits(2 if step < 1 else 0)
        sb.set_size_request(70, -1)
        return sb

    def _build_animatediff_options_row(self) -> Gtk.Box:
        """Build the whole AnimateDiff options group as ONE row (a vertical
        box whose first child is a plain header Label, so `RoleZonePanel.
        _relabel_row` can still rewrite it with the marker glyph — see that
        method's "no-op if the row's first child isn't a Gtk.Label" fallback,
        which this deliberately satisfies).

        Hidden by default (`_update_animatediff_visibility` called at the end
        of `build()`) since "wan2" is `_DEFAULT_VIDEO_MODEL_KEY`, not
        "animatediff".
        """
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.add_css_class("video-param-row")
        outer.add_css_class("video-animatediff-options")

        header = Gtk.Label(label="AnimateDiff Options")
        header.set_xalign(0.0)
        header.add_css_class("video-param-label")
        outer.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content.set_margin_start(8)
        content.set_margin_top(4)

        def _sub_row(lbl_text: str, widget: Gtk.Widget) -> Gtk.Box:
            r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            lbl = Gtk.Label(label=lbl_text)
            lbl.set_xalign(0)
            lbl.set_hexpand(True)
            lbl.add_css_class("video-param-hint")
            r.append(lbl)
            r.append(widget)
            return r

        # Mode
        self._ad_mode = self._ad_dd(_ANIMATEDIFF_MODE_CHOICES, "blackhole")
        content.append(_sub_row("Mode", self._ad_mode))

        # Negative prompt (AnimateDiff's OWN — distinct from the shared
        # "negative_prompt" row above, which other video models use).
        self._ad_neg_prompt = Gtk.Entry()
        self._ad_neg_prompt.set_placeholder_text("blurry, low quality")
        self._ad_neg_prompt.set_hexpand(True)
        content.append(_sub_row("Negative prompt", self._ad_neg_prompt))

        # Temporal alpha
        self._ad_temporal_alpha = self._ad_spin(0.0, 1.0, 0.05, 0.35)
        content.append(_sub_row("Temporal α", self._ad_temporal_alpha))

        # Performance
        self._ad_lightning = Gtk.CheckButton(label="Lightning mode (Euler scheduler)")
        self._ad_lightning.add_css_class("video-param-hint")
        content.append(self._ad_lightning)

        self._ad_lightning_steps = self._ad_dd(_ANIMATEDIFF_LIGHTNING_STEPS_CHOICES, "4")
        self._ad_lightning_steps_row = _sub_row("Distill steps", self._ad_lightning_steps)
        self._ad_lightning_steps_row.set_visible(False)
        content.append(self._ad_lightning_steps_row)

        self._ad_lightning.connect(
            "toggled", lambda *_a: self._sync_ad_lightning_steps_visibility()
        )
        self._ad_mode.connect(
            "notify::selected", lambda *_a: self._sync_ad_lightning_steps_visibility()
        )

        self._ad_multi_chip = Gtk.CheckButton(label="Use all chips in parallel")
        self._ad_multi_chip.set_active(True)
        self._ad_multi_chip.add_css_class("video-param-hint")
        content.append(self._ad_multi_chip)

        self._ad_device_id = self._ad_spin(-1, 7, 1, -1)
        self._ad_device_id.set_tooltip_text("-1 = auto (all chips)")
        content.append(_sub_row("Device ID", self._ad_device_id))

        # Chain continuity
        self._ad_chain_from = Gtk.Entry()
        self._ad_chain_from.set_placeholder_text("path/to/latents.chain.pt")
        self._ad_chain_from.set_hexpand(True)
        chain_pick_btn = Gtk.Button(label="…")
        chain_pick_btn.add_css_class("video-param-hint")
        chain_pick_btn.connect("clicked", self._on_ad_chain_from_pick)
        chain_from_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        chain_from_row.append(self._ad_chain_from)
        chain_from_row.append(chain_pick_btn)
        content.append(_sub_row("Chain from (.pt)", chain_from_row))

        self._ad_chain_save = Gtk.CheckButton(label="Save latents for chaining")
        self._ad_chain_save.add_css_class("video-param-hint")
        content.append(self._ad_chain_save)

        self._ad_chain_alpha = self._ad_spin(0.0, 1.0, 0.05, 0.6)
        content.append(_sub_row("Chain α", self._ad_chain_alpha))

        # MotionAdapter
        self._ad_motion_adapter = Gtk.CheckButton(label="Enable MotionAdapter")
        self._ad_motion_adapter.add_css_class("video-param-hint")
        content.append(self._ad_motion_adapter)

        self._ad_motion_skip = self._ad_dd(
            _ANIMATEDIFF_MOTION_SKIP_CHOICES, "None (full quality)"
        )
        content.append(_sub_row("Skip preset", self._ad_motion_skip))

        self._ad_injection_alpha = self._ad_spin(0.0, 1.0, 0.05, 1.0)
        content.append(_sub_row("Injection α", self._ad_injection_alpha))

        outer.append(content)
        self._ad_options_row = outer
        outer.set_visible(False)  # AnimateDiff isn't the default model
        return outer

    def _on_ad_chain_from_pick(self, _btn: Gtk.Button) -> None:
        """Open a file-chooser to pick a .chain.pt latents file — same
        async `Gtk.FileDialog` pattern as `AnimateParamPanel`'s ref-video/
        ref-image pickers below (per CLAUDE.md's FileDialog gotcha)."""
        dlg = Gtk.FileDialog()
        dlg.set_title("Select chain latents (.pt)")
        parent = _btn.get_root() if _btn is not None else None
        dlg.open(parent, None, self._on_ad_chain_from_picked)

    def _on_ad_chain_from_picked(self, dlg, result) -> None:
        try:
            gfile = dlg.open_finish(result)
        except Exception:
            return  # user cancelled — leave the existing entry text untouched
        if gfile is not None and self._ad_chain_from is not None:
            path = gfile.get_path()
            if path:
                self._ad_chain_from.set_text(path)

    def _collect_animatediff_args(self) -> dict:
        """Read every AnimateDiff-specific widget into the exact
        `get_animatediff_args()` shape (main_window.py ~5283) — ALWAYS a
        complete dict (every key present), whether or not `build()` has run
        yet (falls back to `_ANIMATEDIFF_DEFAULTS` verbatim) or AnimateDiff
        is the currently-selected model (the widgets still hold real values
        either way; only their ENCLOSING row's visibility is gated)."""
        if self._ad_mode is None:
            return dict(_ANIMATEDIFF_DEFAULTS)

        skip_preset = self._ad_dd_val(self._ad_motion_skip)
        motion_skip = _ANIMATEDIFF_MOTION_SKIP_VALUES.get(skip_preset)

        raw_device_id = int(self._ad_device_id.get_value())

        return dict(
            mode=self._ad_dd_val(self._ad_mode) or "blackhole",
            negative_prompt=self._ad_neg_prompt.get_text() or "blurry, low quality",
            temporal_alpha=round(self._ad_temporal_alpha.get_value(), 2),
            lightning=self._ad_lightning.get_active(),
            lightning_steps=int(self._ad_dd_val(self._ad_lightning_steps) or "4"),
            multi_chip=self._ad_multi_chip.get_active(),
            device_id=raw_device_id if raw_device_id >= 0 else None,
            chain_from=self._ad_chain_from.get_text().strip() or None,
            chain_save=self._ad_chain_save.get_active(),
            chain_alpha=round(self._ad_chain_alpha.get_value(), 2),
            motion_adapter="" if self._ad_motion_adapter.get_active() else None,
            motion_adapter_alpha=round(self._ad_injection_alpha.get_value(), 2),
            motion_adapter_skip=motion_skip,
        )


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
        # SP-3d-2: random/repeat-last/keep seed mode, migrated from
        # ControlPanel — see `SeedModeControl`'s docstring.
        self._seed_mode: Optional[SeedModeControl] = None
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
        `build()`. `seed` (SP-3d-2) is resolved through `_collect_seed` per
        the active `SeedModeControl` mode — see `ImageParamPanel.collect()`'s
        docstring for the same migration-safety note.
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
            "seed": _collect_seed(self._seed_adj, self._seed_mode),
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

        # SP-3d-2: random/repeat-last/keep mode selector, migrated from
        # ControlPanel — see `SeedModeControl`'s docstring.
        self._seed_mode = SeedModeControl(
            on_change=self._on_seed_mode_changed, css_class="animate-param-input"
        )
        row.append(self._seed_mode)
        return row

    def _on_seed_mode_changed(self, mode: str) -> None:
        """Write-through for "random" — see `ImageParamPanel`'s identical
        method for the full rationale."""
        if mode == "random" and self._seed_adj is not None:
            self._seed_adj.set_value(-1)

    # ── File pickers (GTK4 async Gtk.FileDialog, per CLAUDE.md) ──────────────

    def _on_pick_ref_video(self, _btn: Gtk.Button) -> None:
        dlg = Gtk.FileDialog()
        dlg.set_title("Select motion video")
        # Derive the transient parent from the clicked button, not from
        # `self._widget`: once a RoleZonePanel (Task 5) re-parents this panel's
        # rows out of its own `build()` box, `self._widget` becomes an orphaned,
        # un-mounted box whose `get_root()` is None — but the button is still
        # in the live widget tree, so its `get_root()` is the real window.
        parent = _btn.get_root() if _btn is not None else None
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
        # See `_on_pick_ref_video`: derive the parent from the live button, not
        # from the possibly-orphaned `self._widget`, so the dialog stays modal
        # to the real window after RoleZonePanel re-parenting.
        parent = _btn.get_root() if _btn is not None else None
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


def _artgen_field_wants_inspire(spec: _ArgSpec) -> bool:
    """True for an `_ArgSpec` the ✨ Inspire button belongs on (regression
    fix 2/2).

    Reuses `field_roles.classify_artgen` — the SAME single-source-of-truth
    classifier `field_specs()` already calls to place this field in
    RoleZonePanel's Brief/Direction/Controls zones — as the "is this
    creative text" test, rather than a second, parallel allowlist that could
    quietly drift from it. Restricting to `kind == "str"` on top of
    `ROLE_BRIEF` is belt-and-suspenders (today every `ROLE_BRIEF` dest IS
    "str"-kind — `classify_artgen` never assigns ROLE_BRIEF to an int/float/
    bool/choice field — but this makes that assumption explicit rather than
    implicit, and this function only ever gets called from the "str" branch
    of `_build_control_row` anyway).

    Concretely, as of today's registered generators (`artgen.get(name)` —
    note "animatediff" resolves to the reduced-arg MCP plugin at
    `plugins/animatediff/plugin.py`, not the fuller CLI arg set
    `app/artgen/generators/animatediff.py` itself declares but never gets
    instantiated with, per that plugin's own comment): ansi's `subject`/
    `board_name`/`tagline`, verse's `theme`, freeform's `freeform`,
    palette's `mood`, and animatediff's `prompt` all qualify. circuit's
    `inputs`/`gates`/`circuit_style`, landscape's `palette`, and
    animatediff's `negative_prompt` do NOT — they're structured config
    strings or a negation, not prose an LLM prompt-generator should rewrite.
    Any FUTURE path-like `str` field (e.g. a `--chain-from PATH`) would also
    be excluded the same way: a `default=None`/"auto" str field that isn't
    in `classify_artgen`'s recognized creative-dest set resolves to
    ROLE_DIRECTION, not ROLE_BRIEF.
    """
    return spec.kind == "str" and field_roles.classify_artgen(spec).role == field_roles.ROLE_BRIEF


class ArtgenParamPanel(CreateParamPanel):
    """One param panel class for every artgen generator (verse/ansi/
    landscape/…) — parameterized by generator NAME at construction, not
    subclassed per generator (see module comment for the CRITICAL STRATEGY
    this implements).

    `collect()` returns exactly `{dest: value}` for every argparse dest the
    named generator's `add_args` declares — this IS the params dict a later
    task (the artgen run-path wiring) feeds straight to `argparse.Namespace`
    construction / `generate_artifact`, no key translation needed.

    **✨ Inspire (regression fix 2/2):** the OLD (deleted, SP-3d-5)
    `ArtgenPanel` gave every generator's theme/subject/prompt-shaped entry a
    ✦ Inspire button. `inspire_fn`/`prompt_type_getter` (both optional,
    default `None`) restore that per-field wiring via the shared
    `attach_inspire_button` helper -- `inspire_fn is None` (the default, and
    what every pre-existing caller/test still passes) means "no ✨ buttons at
    all", so this is purely additive. A creative-text field is one whose
    `_ArgSpec.kind == "str"` AND whose `field_roles.classify_artgen`
    classification is `ROLE_BRIEF` (see `_artgen_field_wants_inspire` below) --
    this is deliberately narrower than "any str field", since several
    generators have str-kind fields that are structured/enum-like config
    (circuit's `--inputs`/`--gates`/`--circuit-style`, landscape's
    `--palette`) rather than prose an LLM prompt-generator should ever
    rewrite -- see `_artgen_field_wants_inspire`'s own docstring for the
    exact reasoning and current qualifying/non-qualifying dests.
    """

    def __init__(
        self,
        generator_name: str,
        *,
        inspire_fn: "Optional[Callable[[str, str, Callable[[str], None], Callable[[str], None]], None]]" = None,
        prompt_type_getter: "Optional[Callable[[], str]]" = None,
    ) -> None:
        self._generator_name = generator_name
        self._widget: Optional[Gtk.Widget] = None
        self._controls: "list[_ArgControl]" = []
        # ✨ Inspire seam (see class docstring). `prompt_type_getter` defaults
        # to a constant "video" getter when an `inspire_fn` is given but no
        # getter was -- matches `generate_prompt.py`'s own CLI default
        # (`_INSPIRE_PROMPT_TYPE_DEFAULT` in create_view.py) -- so a caller
        # that only cares about wiring the seam through doesn't also have to
        # supply a trivial getter.
        self._inspire_fn = inspire_fn
        self._prompt_type_getter = prompt_type_getter or (lambda: "video")
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

            # ✨ Inspire (regression fix 2/2): only for a genuine creative-text
            # field (see `_artgen_field_wants_inspire`) and only when this
            # panel was actually given an `inspire_fn` — appended INSIDE
            # `row` (not as a separate top-level widget) so it travels with
            # the row if a caller ever re-parents it (mirrors RoleZonePanel's
            # re-parenting contract for native panels; ArtgenParamPanel rows
            # are re-parented whole exactly the same way — see
            # `CreateParamPanel._row_for`'s docstring).
            if self._inspire_fn is not None and _artgen_field_wants_inspire(spec):
                inspire_btn = attach_inspire_button(
                    entry, self._prompt_type_getter, self._inspire_fn,
                )
                row.append(inspire_btn)

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


def attach_inspire_button(
    entry: Gtk.Entry,
    prompt_type_getter: "Callable[[], str]",
    inspire_fn: "Callable[[str, str, Callable[[str], None], Callable[[str], None]], None]",
    *,
    label: str = "✨ Inspire",
    tooltip: "Optional[str]" = None,
) -> Gtk.Button:
    """Build a ready-to-append "✨ Inspire" `Gtk.Button` wired to *entry*.

    Shared by Create's idea-door entry and (Task 2) pipeline-editor field
    entries, so a "✨" button means the same thing everywhere: ONE
    implementation of the two-mode click contract instead of forking it per
    surface (regression fix 1/2's step (b) -- see CLAUDE.md's "Create
    surface" section).

    **Two-mode contract** (mirrors `prompt_client.generate_prompt`'s own
    docstring): at click time, *entry*'s CURRENT text is read as the seed --
        - empty   -> `seed_text=""`      -> the backend generates a fresh
          prompt from scratch (algo -> markov -> LLM polish).
        - non-empty -> `seed_text=<text>` -> the backend polishes/remixes
          those exact words instead of discarding them.
    This restores the behavior the deleted `ControlPanel`/`ArtgenPanel`
    Inspire buttons had (SP-3d-5 lost only the CALLER wiring, not the
    backend -- `prompt_client.generate_prompt(source, seed_text, ...)` was
    two-mode the whole time).

    *inspire_fn* is called as `inspire_fn(prompt_type, seed_text, on_result,
    on_error)`. It is expected to run off the GTK main thread and post its
    outcome via `GLib.idle_add` (the GTK threading rule, CLAUDE.md) -- but
    the callbacks built here wrap the actual widget mutation in ANOTHER
    `GLib.idle_add` regardless, so a same-thread (test) fake is just as safe
    as a real background thread, and *inspire_fn* raising SYNCHRONOUSLY
    (e.g. a thread failing to spawn) is caught here too, so a misbehaving
    seam can never crash the caller.

    Returns the `Gtk.Button` — callers append it wherever the entry lives and
    may keep a reference (e.g. `self._inspire_btn`) for tests that assert on
    its `get_sensitive()` loading state.
    """
    btn = Gtk.Button(label=label)
    btn.add_css_class("create-inspire-btn")
    btn.set_tooltip_text(
        tooltip
        or "Inspire a fresh prompt, or reimagine what you've typed."
    )

    # Mutable closure state (plain dict, not an attribute on the fresh
    # button) tracking whether a generation is currently in flight -- guards
    # against a re-entrant click firing a second overlapping call.
    state = {"generating": False}

    def _set_generating(generating: bool) -> None:
        state["generating"] = generating
        if generating:
            btn.set_label("⏳ Generating…")
            btn.set_sensitive(False)
        else:
            btn.set_label(label)
            btn.set_sensitive(True)

    def _apply_result(text: str) -> bool:
        entry.set_text(text)
        _set_generating(False)
        return False  # GLib.idle_add: fire once

    def _on_result(text: str) -> None:
        # May be invoked from any thread -- post the widget mutation to the
        # GTK main thread (GTK threading rule, CLAUDE.md).
        GLib.idle_add(_apply_result, text)

    def _apply_error(msg: str) -> bool:
        print(f"[tt-gen] Inspire error: {msg}", file=sys.stderr)
        _set_generating(False)
        return False

    def _on_error(msg: str) -> None:
        GLib.idle_add(_apply_error, msg)

    def _on_clicked(_btn) -> None:
        if state["generating"]:
            return
        seed_text = entry.get_text().strip()
        _set_generating(True)
        try:
            inspire_fn(prompt_type_getter(), seed_text, _on_result, _on_error)
        except Exception as e:  # noqa: BLE001 - fail-soft, see docstring
            _on_error(str(e))

    btn.connect("clicked", _on_clicked)
    return btn


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

    **De-dup (Task 6, task-6-brief.md item 6)**: once an entry is applied its
    "add" chip is hidden (`set_visible(False)`) so the SAME modifier can't be
    tapped twice — before this, `_applied` could accumulate the identical
    `ChipEntry` any number of times, and `applied_text()` would repeat its
    text once per click. Removing the pill restores the add-chip
    (`set_visible(True)`), so a modifier can always be re-applied after being
    taken off. `self._add_buttons` maps `id(entry)` (ChipEntry is a plain,
    unhashable dataclass) to the exact `Gtk.Button` built for it in
    `_build_category_box`, so `_apply_entry`/`_remove_entry` can look it up
    without a widget tree walk. A synthetic entry never built by this widget
    (e.g. `append_modifier_for_test`'s ad-hoc `ChipEntry`) simply has no
    matching button — `.get(id(entry))` returns `None` and the toggle is
    skipped, matching this class's existing fail-soft conventions.
    """

    def __init__(self, kind: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class("modifier-pills")

        self._kind = kind
        self._applied: "list" = []  # ordered list[ChipEntry], click order
        self._add_buttons: "dict[int, Gtk.Button]" = {}  # id(entry) -> its add-chip button

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

    # How many "add" chips each category shows inline before the rest tuck
    # behind a "+N more…" reveal — keeps the Direction zone compact so the
    # Create surface fits without scrolling.
    _VISIBLE_PER_CATEGORY = 2

    def _build_category_box(self, category) -> Gtk.Widget:
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        group.add_css_class("modifier-pills-category")

        header = Gtk.Label(label=category.name)
        header.add_css_class("modifier-pills-category-label")
        header.set_xalign(0.0)
        group.append(header)

        # First N chips show inline; any beyond go into a collapsed Revealer
        # toggled by "+N more…". Every chip is still built and registered in
        # `_add_buttons`, so de-dup + `applied_text()` are unaffected — the
        # reveal only controls whether the overflow GROUP is on screen.
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.add_css_class("modifier-pills-add-row")

        overflow_flow = Gtk.FlowBox()
        overflow_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        overflow_flow.add_css_class("modifier-pills-add-row")
        revealer = Gtk.Revealer()
        revealer.set_child(overflow_flow)
        revealer.set_reveal_child(False)

        overflow = 0
        for i, entry in enumerate(category.chips):
            btn = Gtk.Button(label=f"+ {entry.label}")
            btn.add_css_class("create-addchip")
            if entry.tip:
                btn.set_tooltip_text(entry.tip)
            btn.connect("clicked", lambda _b, e=entry: self._apply_entry(e))
            self._add_buttons[id(entry)] = btn
            if i < self._VISIBLE_PER_CATEGORY:
                flow.append(btn)
            else:
                overflow_flow.append(btn)
                overflow += 1
        group.append(flow)

        if overflow > 0:
            more = Gtk.Button(label=f"+{overflow} more…")
            more.add_css_class("create-addchip")
            more.add_css_class("modifier-pills-more")

            def _toggle_more(_b, rev=revealer, mbtn=more, n=overflow):
                now = not rev.get_reveal_child()
                rev.set_reveal_child(now)
                mbtn.set_label("− less" if now else f"+{n} more…")

            more.connect("clicked", _toggle_more)
            group.append(more)
            group.append(revealer)

        return group

    def _apply_entry(self, entry) -> None:
        self._applied.append(entry)
        btn = self._add_buttons.get(id(entry))
        if btn is not None:
            btn.set_visible(False)  # de-dup: can't add the same modifier twice
        self._render_applied()

    def _remove_entry(self, entry) -> None:
        if entry in self._applied:
            self._applied.remove(entry)
        btn = self._add_buttons.get(id(entry))
        if btn is not None:
            btn.set_visible(True)  # restore the add-chip now the pill is gone
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
