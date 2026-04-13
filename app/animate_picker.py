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
