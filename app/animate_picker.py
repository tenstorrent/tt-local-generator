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
import hashlib
import subprocess
from pathlib import Path
from typing import Optional

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Pango", "1.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, Gio, GLib, Gtk, Pango
    _GTK_AVAILABLE = True
    _GtkButtonBase = Gtk.Button
except (ImportError, ValueError):
    # GTK not available (e.g. headless test environment).
    # Define a stub so the class body can be parsed without error;
    # instantiation will raise RuntimeError at runtime.
    _GTK_AVAILABLE = False
    GdkPixbuf = None  # type: ignore[assignment]
    Gio = None  # type: ignore[assignment]
    GLib = None  # type: ignore[assignment]
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

        self._path is only updated when *path* is non-empty and the file exists,
        so get_path() never returns a path that was rejected by the existence check.
        """
        filled_class = f"input-widget-filled-{self._widget_type}"

        # Clear existing thumb children
        self._clear_thumb()

        if path and Path(path).exists():
            # Only store the path once we know the file is accessible.
            self._path = path
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
            # Clear on empty string or nonexistent path so get_path() stays consistent.
            self._path = ""
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


# ── PickerPopover ─────────────────────────────────────────────────────────────


class PickerPopover(Gtk.Popover if _GTK_AVAILABLE else object):
    """
    Three-tab popover picker anchored to an InputWidget.

    Tabs:
      📦 Bundled — clips from app/assets/motion_clips/ (motion picker only)
      🎬 Gallery — records from HistoryStore
      📁 Disk    — files from a user-chosen folder (saved in AppSettings)

    Args:
        widget_type:      "motion" or "char"
        clips_dir:        path to app/assets/motion_clips/ (used for Bundled tab)
        history_records:  list of GenerationRecord from HistoryStore.all_records()
        settings:         AppSettings instance
        on_select:        callable(path: str) — called when user confirms selection

    Usage:
        popover = PickerPopover("motion", clips_dir, records, settings, on_select)
        popover.set_parent(input_widget)
        popover.popup()
    """

    def __init__(
        self,
        widget_type: str,
        clips_dir: str,
        history_records: list,
        settings,
        on_select,
    ) -> None:
        if not _GTK_AVAILABLE:
            raise RuntimeError(
                "GTK4 is not available in this environment. "
                "PickerPopover cannot be instantiated without a GTK4 display."
            )
        super().__init__()
        self._widget_type = widget_type
        self._clips_dir = clips_dir
        self._history_records = history_records
        self._settings = settings
        self._on_select = on_select
        self._selected_path: Optional[str] = None
        # Initialized before tab-building so _set_selection() (called from
        # _activate_tab during __init__) has a safe target to check.
        self._use_btn: "Gtk.Button | None" = None

        self.add_css_class("picker-popover")
        self.set_size_request(300, -1)

        # Outer vertical box — all popover content lives here
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_top(8)
        outer.set_margin_bottom(0)
        outer.set_margin_start(0)
        outer.set_margin_end(0)
        self.set_child(outer)

        # ── Header: title + close button ──────────────────────────────────────
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        header.set_margin_start(10)
        header.set_margin_end(6)
        header.set_margin_bottom(4)
        title_text = "Pick Motion Video" if widget_type == "motion" else "Pick Character Image"
        title_lbl = Gtk.Label(label=title_text)
        title_lbl.add_css_class("picker-title")
        title_lbl.set_hexpand(True)
        title_lbl.set_xalign(0)
        close_btn = Gtk.Button(label="✕")
        close_btn.add_css_class("trash-btn")  # reuse small transparent button style
        close_btn.connect("clicked", lambda _: self.popdown())
        header.append(title_lbl)
        header.append(close_btn)
        outer.append(header)

        # ── Tab strip ─────────────────────────────────────────────────────────
        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        tab_box.set_margin_start(4)
        tab_box.set_margin_end(4)
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        outer.append(tab_box)
        outer.append(sep)

        # ── Stack (one page per tab) ───────────────────────────────────────────
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.NONE)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(160)
        scroll.set_max_content_height(260)
        scroll.set_child(self._stack)
        outer.append(scroll)

        # ── Build tabs ────────────────────────────────────────────────────────
        self._tab_btns: dict = {}

        if widget_type == "motion":
            self._add_tab(tab_box, "bundled", "📦 Bundled", self._build_bundled_page())
        self._add_tab(tab_box, "gallery", "🎬 Gallery", self._build_gallery_page())
        self._add_tab(tab_box, "disk", "📁 Disk", self._build_disk_page())

        # Activate the first available tab
        first_tab = "bundled" if widget_type == "motion" else "gallery"
        self._activate_tab(first_tab)

        # ── Footer: Cancel + Use this ─────────────────────────────────────────
        footer_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        outer.append(footer_sep)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        footer.set_halign(Gtk.Align.END)
        footer.set_margin_top(6)
        footer.set_margin_bottom(6)
        footer.set_margin_end(10)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.add_css_class("picker-cancel-btn")
        cancel_btn.connect("clicked", lambda _: self.popdown())
        self._use_btn = Gtk.Button(label="Use this")
        self._use_btn.add_css_class("picker-use-btn")
        self._use_btn.set_sensitive(False)
        self._use_btn.connect("clicked", self._on_use_clicked)
        footer.append(cancel_btn)
        footer.append(self._use_btn)
        outer.append(footer)

    # ── Tab management ─────────────────────────────────────────────────────────

    def _add_tab(self, tab_box: "Gtk.Box", name: str, label: str, page: "Gtk.Widget") -> None:
        btn = Gtk.ToggleButton(label=label)
        btn.add_css_class("picker-tab-btn")
        btn.connect("clicked", lambda b, n=name: self._activate_tab(n))
        tab_box.append(btn)
        self._tab_btns[name] = btn
        self._stack.add_named(page, name)

    def _activate_tab(self, name: str) -> None:
        self._stack.set_visible_child_name(name)
        for tab_name, btn in self._tab_btns.items():
            if tab_name == name:
                btn.add_css_class("picker-tab-btn-active")
                btn.set_active(True)
            else:
                btn.remove_css_class("picker-tab-btn-active")
                btn.set_active(False)
        # Clear selection when switching tabs
        self._set_selection(None)

    # ── Tab page builders ─────────────────────────────────────────────────────

    def _build_bundled_page(self) -> "Gtk.Widget":
        """📦 Bundled tab — scans app/assets/motion_clips/ at open time."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_bottom(4)

        scanner = BundledClipScanner(self._clips_dir)
        data = scanner.scan()

        if not data:
            lbl = Gtk.Label(label="No bundled clips found.")
            lbl.add_css_class("picker-empty")
            box.append(lbl)
            return box

        # Category filter chips
        cat_flow = Gtk.FlowBox()
        cat_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        cat_flow.set_row_spacing(4)
        cat_flow.set_column_spacing(4)

        # Thumbnail grid
        grid = Gtk.FlowBox()
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_row_spacing(5)
        grid.set_column_spacing(5)
        grid.set_min_children_per_line(3)
        grid.set_max_children_per_line(6)

        all_flat: list = [clip for clips in data.values() for clip in clips]

        def _populate_grid(clips: list) -> None:
            child = grid.get_first_child()
            while child:
                nxt = child.get_next_sibling()
                grid.remove(child)
                child = nxt
            for clip in clips:
                cell = self._make_thumb_cell(clip["name"], clip["thumb"], clip["mp4"])
                grid.append(cell)

        # Build category chip buttons
        active_chip: dict = {"btn": None}

        def _on_cat_clicked(btn, cat_name: str) -> None:
            if active_chip["btn"]:
                active_chip["btn"].remove_css_class("picker-cat-chip-active")
            active_chip["btn"] = btn
            btn.add_css_class("picker-cat-chip-active")
            self._deselect_all_in_widget(grid)   # clear visual selection before repopulating
            _populate_grid(data[cat_name])
            self._set_selection(None)

        for cat_name in sorted(data.keys()):
            chip_btn = Gtk.Button(label=cat_name.capitalize())
            chip_btn.add_css_class("picker-cat-chip")
            chip_btn.connect("clicked", _on_cat_clicked, cat_name)
            cat_flow.append(chip_btn)

        box.append(cat_flow)
        _populate_grid(all_flat)
        box.append(grid)
        return box

    def _build_gallery_page(self) -> "Gtk.Widget":
        """🎬 Gallery tab — reads HistoryStore records."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_bottom(4)

        if self._widget_type == "motion":
            records = [r for r in self._history_records
                       if r.media_type == "video" and r.video_exists]
            hint = "Your generated videos — newest first"
        else:
            # Character: show all records with a thumbnail (first-frame stills work)
            records = [r for r in self._history_records if r.thumbnail_exists]
            hint = "Your generated outputs — newest first"

        if not records:
            lbl = Gtk.Label(label="No generated outputs yet.")
            lbl.add_css_class("picker-empty")
            box.append(lbl)
            return box

        hint_lbl = Gtk.Label(label=hint)
        hint_lbl.add_css_class("picker-empty")
        hint_lbl.set_xalign(0)
        box.append(hint_lbl)

        grid = Gtk.FlowBox()
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_row_spacing(5)
        grid.set_column_spacing(5)
        grid.set_min_children_per_line(3)
        grid.set_max_children_per_line(6)

        for rec in records:
            # Motion picker: path is the video. Character picker: path is the thumbnail.
            media_path = rec.video_path if self._widget_type == "motion" else rec.thumbnail_path
            label = rec.id[:8]
            cell = self._make_thumb_cell(label, rec.thumbnail_path, media_path)
            grid.append(cell)

        box.append(grid)
        return box

    def _build_disk_page(self) -> "Gtk.Widget":
        """📁 Disk tab — user-chosen folder scanned on open."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_bottom(4)

        folder = self._settings.get("motion_clips_dir") or ""

        # Folder path row
        folder_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        folder_row.add_css_class("picker-folder-row")
        folder_icon = Gtk.Label(label="📁")
        folder_path_lbl = Gtk.Label(label=folder if folder else "No folder selected")
        folder_path_lbl.add_css_class("picker-empty")
        folder_path_lbl.set_hexpand(True)
        folder_path_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        folder_path_lbl.set_xalign(0)
        change_btn = Gtk.Button(label="Change…")
        change_btn.add_css_class("picker-cancel-btn")

        def _change_folder(_btn):
            dlg = Gtk.FileDialog()
            dlg.set_title("Select Motion Clips Folder")
            dlg.select_folder(None, None, self._on_folder_chosen)

        change_btn.connect("clicked", _change_folder)
        folder_row.append(folder_icon)
        folder_row.append(folder_path_lbl)
        folder_row.append(change_btn)
        box.append(folder_row)

        # Grid of video/image files in the folder
        grid = Gtk.FlowBox()
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_row_spacing(5)
        grid.set_column_spacing(5)
        grid.set_min_children_per_line(3)
        grid.set_max_children_per_line(6)

        if folder and Path(folder).is_dir():
            self._populate_disk_grid(grid, folder)
        else:
            empty_lbl = Gtk.Label(label="No folder — drag clips here or click Change…")
            empty_lbl.add_css_class("picker-empty")
            empty_lbl.set_wrap(True)
            grid.append(empty_lbl)

        # Dashed "Browse…" tile for one-off file picks
        browse_tile = Gtk.Button(label="＋\nBrowse…")
        browse_tile.add_css_class("picker-browse-tile")
        browse_tile.connect("clicked", self._on_browse_file)
        grid.append(browse_tile)
        box.append(grid)
        return box

    def _populate_disk_grid(self, grid: "Gtk.FlowBox", folder: str) -> None:
        """Add thumbnail cells for all video/image files in *folder*.

        Thumbnails for video files are cached in ~/.cache/tt-video-gen/disk_thumbs/
        rather than written next to the source file (which may be on a read-only
        or user-owned filesystem).
        """
        folder_path = Path(folder)
        cache_dir = Path.home() / ".cache" / "tt-video-gen" / "disk_thumbs"
        cache_dir.mkdir(parents=True, exist_ok=True)

        extensions = set(_VIDEO_EXTENSIONS) | set(_IMAGE_EXTENSIONS)
        try:
            files = sorted(
                f for f in folder_path.iterdir()
                if f.is_file() and f.suffix.lower() in extensions
            )
        except PermissionError:
            err_lbl = Gtk.Label(label="Cannot read folder — check permissions.")
            err_lbl.add_css_class("picker-empty")
            err_lbl.set_wrap(True)
            grid.append(err_lbl)
            return

        if not files:
            lbl = Gtk.Label(label="No files in folder — drag some in or click Browse.")
            lbl.add_css_class("picker-empty")
            lbl.set_wrap(True)
            grid.append(lbl)
            return

        for file_path in files:
            if file_path.suffix.lower() in _IMAGE_EXTENSIONS:
                thumb_path = str(file_path)
            else:
                # Cache video thumbnails using a hash-based name to avoid collisions
                thumb_name = file_path.stem + "_" + hashlib.md5(str(file_path).encode()).hexdigest()[:8] + ".jpg"
                thumb_path_obj = cache_dir / thumb_name
                if not thumb_path_obj.exists():
                    extract_thumbnail(str(file_path), str(thumb_path_obj))
                thumb_path = str(thumb_path_obj) if thumb_path_obj.exists() else ""

            cell = self._make_thumb_cell(file_path.name[:12], thumb_path, str(file_path))
            grid.append(cell)

    # ── Selection helpers ─────────────────────────────────────────────────────

    # Fixed pixel size for picker thumbnail cells.  Chosen to fit ≥3 columns in
    # the 300 px minimum popover width (3 × 80 + 2 × 5 spacing = 250 px < 284 px
    # available after 8 px left/right margins).
    _CELL_W = 80
    _CELL_H = 60

    def _make_thumb_cell(self, label: str, thumb_path: str, media_path: str) -> "Gtk.Widget":
        """Return a fixed-size thumbnail cell widget for the picker grid.

        Uses GdkPixbuf to pre-scale and COVER-crop the thumbnail so the
        Gtk.Image widget reports its natural size as exactly _CELL_W × _CELL_H.
        This prevents Gtk.Picture's large natural image size from confusing the
        FlowBox into allocating one giant cell per row.
        """
        cw, ch = self._CELL_W, self._CELL_H

        overlay = Gtk.Overlay()
        overlay.set_size_request(cw, ch)

        frame = Gtk.Frame()
        frame.add_css_class("picker-thumb-cell")
        frame.set_size_request(cw, ch)
        overlay.set_child(frame)

        if thumb_path and Path(thumb_path).exists():
            img_widget = None
            if GdkPixbuf is not None:
                try:
                    # COVER-style scale: expand uniformly until both dimensions
                    # are ≥ cell size, then subpixbuf-crop to exact cell size.
                    src = GdkPixbuf.Pixbuf.new_from_file(thumb_path)
                    scale = max(cw / src.get_width(), ch / src.get_height())
                    sw = max(cw, int(src.get_width() * scale))
                    sh = max(ch, int(src.get_height() * scale))
                    scaled = src.scale_simple(sw, sh, GdkPixbuf.InterpType.BILINEAR)
                    x_off = (sw - cw) // 2
                    y_off = (sh - ch) // 2
                    cropped = scaled.new_subpixbuf(x_off, y_off, cw, ch)
                    img_widget = Gtk.Image.new_from_pixbuf(cropped)
                    img_widget.set_size_request(cw, ch)
                except Exception:
                    pass  # fall through to Gtk.Picture fallback

            if img_widget is None:
                # GdkPixbuf unavailable or load failed: use Gtk.Picture with
                # can_shrink as a best-effort fallback (may mis-size in the grid).
                img_widget = Gtk.Picture.new_for_filename(thumb_path)
                img_widget.set_can_shrink(True)

            frame.set_child(img_widget)
        else:
            icon = Gtk.Label(label="🎬")
            icon.set_valign(Gtk.Align.CENTER)
            icon.set_halign(Gtk.Align.CENTER)
            frame.set_child(icon)

        # Filename label overlaid at bottom
        name_lbl = Gtk.Label(label=label)
        name_lbl.add_css_class("picker-empty")
        name_lbl.set_valign(Gtk.Align.END)
        name_lbl.set_halign(Gtk.Align.FILL)
        name_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        overlay.add_overlay(name_lbl)

        # Click gesture on the overlay (outermost widget in the cell)
        gesture = Gtk.GestureClick()
        gesture.connect("pressed", lambda *_: self._on_cell_clicked(frame, media_path))
        overlay.add_controller(gesture)

        return overlay

    def _on_cell_clicked(self, frame: "Gtk.Frame", path: str) -> None:
        """Handle a click on a thumbnail cell."""
        page = self._stack.get_visible_child()
        if page:
            self._deselect_all_in_widget(page)
        frame.add_css_class("picker-thumb-cell-selected")
        self._set_selection(path)

    def _deselect_all_in_widget(self, widget: "Gtk.Widget") -> None:
        """Recursively remove picker-thumb-cell-selected from all descendants."""
        if widget.has_css_class("picker-thumb-cell"):
            widget.remove_css_class("picker-thumb-cell-selected")
        child = widget.get_first_child()
        while child:
            self._deselect_all_in_widget(child)
            child = child.get_next_sibling()

    def _set_selection(self, path: Optional[str]) -> None:
        # Accept non-empty string only; None or "" both mean no selection.
        self._selected_path = path if path else None
        # _use_btn may be None during __init__ (tabs are built before the footer)
        if self._use_btn is not None:
            self._use_btn.set_sensitive(bool(self._selected_path))

    # ── Action handlers ───────────────────────────────────────────────────────

    def _on_use_clicked(self, _btn) -> None:
        if self._selected_path:
            self._on_select(self._selected_path)
        self.popdown()

    def _on_folder_chosen(self, dlg, result) -> None:
        try:
            gfile = dlg.select_folder_finish(result)
        except Exception:
            return
        path = gfile.get_path()
        if path:
            self._settings.set("motion_clips_dir", path)
            # Rebuild the disk page with the new folder
            disk_page = self._build_disk_page()
            old = self._stack.get_child_by_name("disk")
            if old:
                self._stack.remove(old)
            self._stack.add_named(disk_page, "disk")
            self._activate_tab("disk")

    def _on_browse_file(self, _btn) -> None:
        """Open a single-file FileDialog for a one-off pick."""
        dlg = Gtk.FileDialog()
        dlg.set_title("Select File")
        f = Gtk.FileFilter()
        f.set_name("Videos and Images")
        for pat in ("*.mp4", "*.mov", "*.avi", "*.webm", "*.mkv",
                    "*.png", "*.jpg", "*.jpeg", "*.webp"):
            f.add_pattern(pat)
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        dlg.set_filters(store)
        dlg.open(None, None, self._on_browse_file_chosen)

    def _on_browse_file_chosen(self, dlg, result) -> None:
        try:
            gfile = dlg.open_finish(result)
        except Exception:
            return
        path = gfile.get_path()
        if path:
            self._on_select(path)
            self.popdown()
