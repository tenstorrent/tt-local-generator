# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
RemixPopover — single entry point for all "take artifact A, start a new
generation from it" actions.

Usage:
    pop = RemixPopover(record, on_remix=self._dispatch_remix)
    pop.set_parent(trigger_btn)
    pop.popup()

*record* may be a GenerationRecord (video/image tab) or a MediaRecord (artgen tab).
*on_remix* is called with a fully-resolved RemixContext on the GTK main thread.
"""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from artgen import RemixContext, IngredientSpec, ingredients_for, remix_targets_for

# Thumbnail dimensions in the source identity row
_THUMB_W = 72
_THUMB_H = 40


def _source_type_from_record(record) -> str:
    """Derive a canonical source_type string from whatever record type we have.

    For artgen MediaRecords, media_type is always "artgen" but the actual generator
    (palette, verse, landscape, etc.) lives in generator_type. Remix ingredient
    tables and plugin targets are keyed by generator type, not "artgen", so we
    prefer generator_type when present.
    """
    gt = getattr(record, "generator_type", None)
    if gt:
        return str(gt)
    mt = getattr(record, "media_type", None)
    if mt and mt != "artgen":
        return str(mt)
    return str(mt or "video")


def _prompt_from_record(record) -> str:
    return getattr(record, "prompt", "") or ""


def _neg_from_record(record) -> str:
    """Extract negative prompt from either a GenerationRecord or MediaRecord."""
    # GenerationRecord has negative_prompt as a direct field
    neg = getattr(record, "negative_prompt", None)
    if neg is not None:
        return str(neg)
    # MediaRecord stores it in params JSON via params_dict property
    params_dict = getattr(record, "params_dict", None)
    if isinstance(params_dict, dict):
        return params_dict.get("negative_prompt", "")
    if callable(params_dict):
        try:
            return params_dict().get("negative_prompt", "")
        except Exception:
            pass
    return ""


def _thumbnail_path(record) -> str:
    return getattr(record, "thumbnail_path", "") or ""


def _media_path(record) -> str:
    """Return the primary media file path (video_path or file_path)."""
    return (
        getattr(record, "video_path", "")
        or getattr(record, "file_path", "")
        or ""
    )


def _build_hint(record, source_type: str, target_type: str, active_keys: set) -> str:
    """Build the combined hint string from the checked ingredient keys."""
    parts = []
    if "text" in active_keys or "prompt" in active_keys:
        p = _prompt_from_record(record)
        if p:
            parts.append(p)
    if "colors" in active_keys:
        try:
            import json as _json
            data = _json.loads(Path(_media_path(record)).read_text())
            hexes = " ".join(c["hex"] for c in data.get("colors", [])[:6])
            if hexes:
                parts.append(f"palette: {hexes}")
        except Exception:
            pass
    if "lore" in active_keys:
        try:
            import json as _json
            data = _json.loads(Path(_media_path(record)).read_text())
            lore = data.get("lore", "")
            if lore:
                parts.append(lore)
        except Exception:
            pass
    if "vibe" in active_keys:
        p = _prompt_from_record(record)
        if p:
            parts.append(p)
    return ", ".join(p for p in parts if p)


class RemixPopover(Gtk.Popover):
    """Popover for remixing any artifact into a new generation."""

    def __init__(
        self,
        record,
        on_remix: Callable[[RemixContext], None],
    ):
        super().__init__()
        self._record = record
        self._on_remix = on_remix
        self._source_type = _source_type_from_record(record)
        self._active_keys: set = set()
        self._ingredient_checks: dict = {}
        self._hint_lbl: Optional[Gtk.Label] = None
        self._target_btns: dict = {}

        self.set_has_arrow(True)
        self.set_position(Gtk.PositionType.BOTTOM)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_top(10)
        outer.set_margin_bottom(10)
        outer.set_margin_start(12)
        outer.set_margin_end(12)
        outer.set_size_request(240, -1)

        # ── Source identity ────────────────────────────────────────────────────
        id_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        thumb_path = _thumbnail_path(record)
        if thumb_path and Path(thumb_path).exists():
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    thumb_path, _THUMB_W, _THUMB_H, True
                )
                thumb_pic = Gtk.Picture.new_for_paintable(Gdk.Texture.new_for_pixbuf(pb))
                thumb_pic.set_size_request(_THUMB_W, _THUMB_H)
                thumb_pic.set_content_fit(Gtk.ContentFit.COVER)
                id_row.append(thumb_pic)
            except Exception:
                pass

        id_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        id_text.set_hexpand(True)
        prompt_lbl = Gtk.Label(label=_prompt_from_record(record)[:60] or f"[{self._source_type}]")
        prompt_lbl.set_xalign(0)
        prompt_lbl.set_ellipsize(3)  # Pango.EllipsizeMode.END = 3
        prompt_lbl.add_css_class("body")
        id_text.append(prompt_lbl)
        type_lbl = Gtk.Label(label=self._source_type)
        type_lbl.set_xalign(0)
        type_lbl.add_css_class("muted")
        id_text.append(type_lbl)
        id_row.append(id_text)
        outer.append(id_row)

        outer.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Build target list from plugin registry + "New variation" always last ──
        plugin_targets = remix_targets_for(self._source_type)
        _built_targets: list = []
        for pdef in plugin_targets:
            key = pdef.name
            label = getattr(pdef, "label", None) or key.title()
            _built_targets.append((key, label))
        _built_targets.append(("same", "✨ New variation"))

        # Default target for ingredient display: first non-"same" target
        default_target = next(
            (k for k, _ in _built_targets if k != "same"),
            "video",
        )

        # ── Ingredient section (only if >1 ingredient for default target) ─────
        specs = ingredients_for(self._source_type, default_target)
        if len(specs) > 1:
            carry_lbl = Gtk.Label(label="CARRY INTO REMIX")
            carry_lbl.set_xalign(0)
            carry_lbl.add_css_class("hint")
            outer.append(carry_lbl)

            for spec in specs:
                if spec.default_on:               # only add key if checkbox starts checked
                    self._active_keys.add(spec.key)
                cb = Gtk.CheckButton.new_with_label(spec.label)
                cb.set_active(spec.default_on)
                cb.connect("toggled", self._on_ingredient_toggled, spec.key)
                self._ingredient_checks[spec.key] = cb
                outer.append(cb)

            outer.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            hint_header = Gtk.Label(label="HINT PREVIEW")
            hint_header.set_xalign(0)
            hint_header.add_css_class("hint")
            outer.append(hint_header)
            self._hint_lbl = Gtk.Label(label=self._compute_hint(default_target))
            self._hint_lbl.set_xalign(0)
            self._hint_lbl.set_wrap(True)
            self._hint_lbl.set_max_width_chars(36)
            self._hint_lbl.add_css_class("remix-hint-preview")
            outer.append(self._hint_lbl)
            outer.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        else:
            for spec in specs:
                if spec.default_on:
                    self._active_keys.add(spec.key)

        # ── Target buttons ─────────────────────────────────────────────────────
        target_lbl = Gtk.Label(label="REMIX AS")
        target_lbl.set_xalign(0)
        target_lbl.add_css_class("hint")
        outer.append(target_lbl)

        target_flow = Gtk.FlowBox()
        target_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        target_flow.set_column_spacing(4)
        target_flow.set_row_spacing(4)
        target_flow.set_max_children_per_line(3)

        for key, label in _built_targets:
            btn = Gtk.Button(label=label)
            btn.add_css_class("remix-target-btn")
            btn.connect("clicked", self._on_target_clicked, key, label)
            self._target_btns[key] = btn
            target_flow.append(btn)

        outer.append(target_flow)
        self.set_child(outer)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _compute_hint(self, target_type: str) -> str:
        # "New variation" re-uses the source prompt directly — no ingredient
        # table entry exists for (source, "same"), and we want the existing
        # prompt, not "(no text hint)" placeholder text.
        if target_type == "same":
            return _prompt_from_record(self._record) or "(no prompt)"
        specs = ingredients_for(self._source_type, target_type)
        active = {s.key for s in specs if s.key in self._active_keys}
        return _build_hint(self._record, self._source_type, target_type, active) or "(no text hint)"

    def _on_ingredient_toggled(self, cb: Gtk.CheckButton, key: str) -> None:
        if cb.get_active():
            self._active_keys.add(key)
        else:
            self._active_keys.discard(key)
        if self._hint_lbl:
            first_target = next(
                (k for k in self._target_btns if k != "same"),
                "video",
            )
            self._hint_lbl.set_label(self._compute_hint(first_target))

    def _on_target_clicked(self, _btn: Gtk.Button, target_key: str, target_label: str) -> None:
        """Disable all target buttons, show spinner, resolve ingredients in background thread."""
        for btn in self._target_btns.values():
            btn.set_sensitive(False)
        _btn.set_label("⟳ preparing…")

        hint = self._compute_hint(target_key)
        record = self._record
        source_type = self._source_type
        active_keys = set(self._active_keys)
        on_remix = self._on_remix

        def _resolve():
            seed_image_path = ""
            ref_video_path = ""

            media_path = _media_path(record)
            thumb_path = _thumbnail_path(record)

            # Silent format conversions — spec §3 resolution rules
            if source_type in ("video", "gif", "animatediff"):
                if target_key == "animate":
                    # Animate tab uses a seed image (thumbnail), not a motion
                    # reference video — dispatch ignores ref_video_path since
                    # the motion-reference picker was removed from the UI.
                    if thumb_path and Path(thumb_path).exists():
                        seed_image_path = thumb_path
                    elif media_path and Path(media_path).exists():
                        # Extract first frame as seed image
                        try:
                            import importlib.util as _ilu
                            _spec = _ilu.spec_from_file_location(
                                "ffmpeg_plugin",
                                Path(__file__).parent.parent / "plugins" / "ffmpeg" / "plugin.py",
                            )
                            _ffmpeg = _ilu.module_from_spec(_spec)
                            _spec.loader.exec_module(_ffmpeg)
                            tmp2 = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                            tmp2.close()
                            _ffmpeg.extract_frame(media_path, tmp2.name, timestamp=0.0)
                            seed_image_path = tmp2.name
                        except Exception:
                            pass
                elif target_key in ("video", "image", "same"):
                    if thumb_path and Path(thumb_path).exists():
                        seed_image_path = thumb_path
                    elif media_path and Path(media_path).exists():
                        tmp_path = None
                        try:
                            import importlib.util as _ilu
                            _spec = _ilu.spec_from_file_location(
                                "ffmpeg_plugin",
                                Path(__file__).parent.parent / "plugins" / "ffmpeg" / "plugin.py",
                            )
                            _ffmpeg = _ilu.module_from_spec(_spec)
                            _spec.loader.exec_module(_ffmpeg)
                            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                            tmp.close()
                            tmp_path = tmp.name
                            _ffmpeg.extract_frame(media_path, tmp_path, timestamp=0.0)
                            seed_image_path = tmp_path
                            tmp_path = None  # ownership transferred to seed_image_path
                        except Exception:
                            if tmp_path:
                                try:
                                    Path(tmp_path).unlink(missing_ok=True)
                                except OSError:
                                    pass
            elif source_type in (
                "landscape", "skyline", "geometric", "circuit",
                "constellation", "ansi", "image",
            ):
                if "thumbnail" in active_keys or "image" in active_keys:
                    if thumb_path and Path(thumb_path).exists():
                        seed_image_path = thumb_path
                    elif media_path and Path(media_path).exists():
                        seed_image_path = media_path

            ctx = RemixContext(
                source_record=(
                    record.__dict__ if hasattr(record, "__dict__") else {}
                ),
                source_type=source_type,
                target_type=target_key,
                hint=hint,
                seed_image_path=seed_image_path,
                ref_video_path=ref_video_path,
                target_label=target_label,
                negative_hint=_neg_from_record(record),
            )
            GLib.idle_add(_done, ctx)

        def _done(ctx: RemixContext) -> bool:
            self.popdown()
            on_remix(ctx)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=_resolve, daemon=True).start()
