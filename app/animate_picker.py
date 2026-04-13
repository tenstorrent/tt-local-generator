#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
animate_picker.py — Animate input widgets and popover picker for tt-local-generator.

Components:
    extract_thumbnail   — thin ffmpeg wrapper; returns True on success
    BundledClipScanner  — scans app/assets/motion_clips/ subdirectory tree
    InputWidget         — Gtk.Button subclass for motion/character inputs (see Task 4)
    PickerPopover       — Gtk.Popover with Bundled / Gallery / Disk tabs (see Task 5)
"""
import subprocess
from pathlib import Path
from typing import Optional

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Gtk, Pango
    _GTK_AVAILABLE = True
    _GtkButtonBase = Gtk.Button
except (ImportError, ValueError):
    # GTK not available (e.g. headless test environment).
    # Define a stub so the class body can be parsed without error;
    # instantiation will raise RuntimeError at runtime.
    _GTK_AVAILABLE = False
    Gtk = None  # type: ignore[assignment]
    Pango = None  # type: ignore[assignment]

    class _GtkButtonBase:  # type: ignore[no-redef]
        """Placeholder base used when GTK4 is not importable."""

        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "GTK4 is not available in this environment. "
                "InputWidget cannot be instantiated without a GTK4 display."
            )


def extract_thumbnail(src_path: str, dest_path: str) -> bool:
    """
    Extract the first frame of *src_path* as a JPEG saved to *dest_path*.

    Returns True when ffmpeg succeeds and the output file exists.
    Returns False silently on any error (ffmpeg absent, timeout, corrupt input).
    """
    try:
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", src_path,
                "-vframes", "1",
                "-q:v", "2",
                "-update", "1",
                dest_path,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0 and Path(dest_path).exists()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


class BundledClipScanner:
    """
    Scans a motion_clips directory tree for bundled MP4 clips.

    Directory layout expected:
        <clips_dir>/
            <category>/
                <clip_name>.mp4
                <clip_name>.jpg   ← thumbnail, extracted on first scan if absent

    Usage:
        scanner = BundledClipScanner("app/assets/motion_clips")
        data = scanner.scan()
        # {"walk": [{"name": "walk_forward", "mp4": "...", "thumb": "..."}, ...], ...}
    """

    def __init__(self, clips_dir: str) -> None:
        self._clips_dir = Path(clips_dir)

    def scan(self) -> dict:
        """
        Return a dict mapping category name → list of clip dicts.

        Each clip dict: {"name": str, "mp4": str, "thumb": str}
        Categories are sorted alphabetically; clips within each category are
        sorted alphabetically by filename stem.
        """
        result: dict = {}
        if not self._clips_dir.is_dir():
            return result

        for cat_dir in sorted(self._clips_dir.iterdir()):
            if not cat_dir.is_dir():
                continue
            clips = []
            for mp4_path in sorted(cat_dir.glob("*.mp4")):
                thumb_path = mp4_path.with_suffix(".jpg")
                if not thumb_path.exists():
                    extract_thumbnail(str(mp4_path), str(thumb_path))
                clips.append({
                    "name": mp4_path.stem,
                    "mp4":  str(mp4_path),
                    "thumb": str(thumb_path),
                })
            if clips:
                result[cat_dir.name] = clips

        return result


# ── InputWidget ───────────────────────────────────────────────────────────────

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm", ".mkv"}


class InputWidget(_GtkButtonBase):
    """
    Clickable thumbnail widget for selecting a motion video or character image.

    Layout (vertical, inside the button):
      ┌────────────────────────┐
      │ MOTION VIDEO (8 px)    │  ← type label
      │ ┌──────────────────┐   │
      │ │ thumbnail / +    │   │  ← 40 px tall thumb area
      │ └──────────────────┘   │
      │ filename.mp4       ▾   │  ← name row
      └────────────────────────┘

    CSS classes applied to the button:
      .input-widget          — always present
      .input-widget-filled-motion — when widget_type=="motion" and path is set
      .input-widget-filled-char   — when widget_type=="char" and path is set

    Call set_value(path) to update programmatically (gallery card actions do this).
    Clicking the widget opens the PickerPopover (wired by ControlPanel in Task 6).
    """

    def __init__(self, widget_type: str, label: str) -> None:
        """
        Args:
            widget_type: "motion" or "char"
            label:       Type label text, e.g. "MOTION VIDEO" or "CHARACTER"
        """
        super().__init__()
        self._widget_type: str = widget_type
        self._path: str = ""
        self.add_css_class("input-widget")
        self.set_hexpand(True)

        # Vertical content box inside the button
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(5)
        box.set_margin_end(5)
        self.set_child(box)

        # Type label — muted uppercase
        type_lbl = Gtk.Label(label=label)
        type_lbl.add_css_class("input-widget-type")
        type_lbl.set_xalign(0)
        box.append(type_lbl)

        # Thumbnail area — 40 px tall
        self._thumb_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._thumb_box.add_css_class("input-widget-thumb")
        self._thumb_box.set_size_request(-1, 40)
        self._thumb_box.set_hexpand(True)
        box.append(self._thumb_box)
        self._show_placeholder()

        # Name row — filename truncated + ▾ caret
        name_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._name_lbl = Gtk.Label(label="none")
        self._name_lbl.add_css_class("input-widget-name")
        self._name_lbl.add_css_class("muted")
        self._name_lbl.set_hexpand(True)
        self._name_lbl.set_ellipsize(Pango.EllipsizeMode.START)
        self._name_lbl.set_xalign(0)
        caret_lbl = Gtk.Label(label="▾")
        caret_lbl.add_css_class("input-widget-caret")
        name_row.append(self._name_lbl)
        name_row.append(caret_lbl)
        box.append(name_row)

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_value(self, path: str) -> None:
        """
        Update the widget to show a thumbnail and filename for *path*.
        Pass an empty string to clear back to the placeholder state.
        """
        self._path = path
        filled_class = f"input-widget-filled-{self._widget_type}"

        # Clear existing thumb children
        self._clear_thumb()

        if path and Path(path).exists():
            thumb_path = self._resolve_thumb_path(path)
            if thumb_path and Path(thumb_path).exists():
                pic = Gtk.Picture.new_for_filename(thumb_path)
                pic.set_can_shrink(True)
                pic.set_hexpand(True)
                self._thumb_box.append(pic)
            else:
                # ffmpeg failed or unavailable — show emoji placeholder
                suffix = Path(path).suffix.lower()
                emoji = "🎬" if suffix in _VIDEO_EXTENSIONS else "🖼"
                self._show_placeholder(emoji)
            self._name_lbl.set_label(Path(path).name)
            self._name_lbl.remove_css_class("muted")
            self.add_css_class(filled_class)
        else:
            self._show_placeholder()
            self._name_lbl.set_label("none")
            self._name_lbl.add_css_class("muted")
            self.remove_css_class(filled_class)

    def get_path(self) -> str:
        """Return the currently selected path, or "" if empty."""
        return self._path

    # ── Private helpers ────────────────────────────────────────────────────────

    def _clear_thumb(self) -> None:
        """Remove all children from the thumbnail area box."""
        child = self._thumb_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._thumb_box.remove(child)
            child = nxt

    def _show_placeholder(self, emoji: str = "＋") -> None:
        """Fill the thumb area with an emoji placeholder label."""
        self._clear_thumb()
        lbl = Gtk.Label(label=emoji)
        lbl.add_css_class("input-widget-placeholder")
        lbl.set_hexpand(True)
        lbl.set_vexpand(True)
        lbl.set_valign(Gtk.Align.CENTER)
        self._thumb_box.append(lbl)

    def _resolve_thumb_path(self, src_path: str) -> Optional[str]:
        """
        Return a path to a JPEG thumbnail for *src_path*.

        For image files: the file itself is the thumbnail.
        For video files: <same_dir>/<stem>.jpg, extracted via ffmpeg on first call.
        Returns None if thumbnail cannot be obtained.
        """
        p = Path(src_path)
        if p.suffix.lower() in _IMAGE_EXTENSIONS:
            return src_path
        # Video: cache thumbnail as <stem>.jpg next to the file
        thumb = p.with_suffix(".jpg")
        if not thumb.exists():
            ok = extract_thumbnail(src_path, str(thumb))
            if not ok:
                return None
        return str(thumb)
