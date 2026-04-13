#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
app_settings.py — Persistent application settings for tt-local-generator.

Settings are stored in ~/.local/share/tt-video-gen/settings.json alongside
the history and queue files.

Usage:
    from app_settings import settings
    steps = settings.get("quality_steps")   # returns int/float/bool/str
    settings.set("quality_steps", 50)       # writes through to disk immediately

All keys and their defaults are defined in DEFAULTS below.  Unknown keys in the
JSON file are preserved on load (forward-compatibility) but get() only serves
known keys.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# ── Storage location (same directory as history.json) ─────────────────────────

STORAGE_DIR = Path.home() / ".local" / "share" / "tt-video-gen"
SETTINGS_FILE = STORAGE_DIR / "settings.json"

# ── Defaults for every known key ──────────────────────────────────────────────

DEFAULTS: dict = {
    # Generation quality
    "quality_steps": 20,            # default inference steps loaded into the steps spin
    # Sleep / power
    "sleep_after_n_gens": 0,        # 0 = never; N = call systemctl suspend after N completions
    # Screensaver
    "inhibit_screensaver": False,   # True = inhibit screensaver via D-Bus while generating
    # Disk management
    "max_disk_gb": 0,               # 0 = use hardcoded 18 GB floor; N = stop when less than N GB free
    # TT-TV timing
    "tttv_image_dwell_s": 10,       # seconds to display each image in TT-TV
    "tttv_video_fallback_s": 90,    # fallback timer (s) if GStreamer never fires the 'ended' signal
    # Prompt director style
    "director_style_prob": 0.33,    # probability a video prompt draws a named director aesthetic
    "director_pin": "",             # "" = random pick; else exact string from CINEMATIC_DIRECTORS
    # SkyReels video length
    # Valid counts: (N-1) % 4 == 0  →  9 (~0.4s), 33 (~1.4s), 65 (~2.7s), 97 (~4s)
    "skyreels_num_frames": 33,
    # Create zone — named control state
    "clip_length_slot":      "standard",  # "short"|"standard"|"long"|"extended"
    "preferred_video_model": "",          # "wan2"|"mochi"|"skyreels"|"" (auto)
    "seed_mode":             "random",    # "random"|"repeat"|"keep"
    "pinned_seed":           -1,          # used when seed_mode == "keep"
    # Recovery
    "dismissed_job_ids": [],        # server job IDs permanently hidden from the Recover Jobs dialog
    # Animate picker — user-chosen disk folder
    "motion_clips_dir": "",         # empty = Disk tab shows only Browse tile
}


class AppSettings:
    """
    Thread-safe key/value settings store backed by a JSON file.

    Reads the file once at construction time.  Every call to set() writes the
    entire file immediately so the state on disk is always current.  Reads are
    in-memory only (no file I/O per get()).
    """

    def __init__(self) -> None:
        self._data: dict = dict(DEFAULTS)
        self._load()

    # ── Public API ─────────────────────────────────────────────────────────────

    def get(self, key: str):
        """Return the current value for key, falling back to DEFAULTS."""
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        """Update key in memory and persist immediately to disk."""
        self._data[key] = value
        self._save()

    def all(self) -> dict:
        """Return a shallow copy of all current settings (known + unknown)."""
        return dict(self._data)

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load settings from disk.  Silently falls back to defaults on any error."""
        try:
            if SETTINGS_FILE.exists():
                raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    # Overlay saved values onto defaults so new keys in DEFAULTS
                    # always have sensible values even on older settings files.
                    self._data.update(raw)
        except Exception as exc:
            log.warning("app_settings: could not load %s: %s", SETTINGS_FILE, exc)

    def _save(self) -> None:
        """Write current settings to disk.  Silently ignores write errors."""
        try:
            STORAGE_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("app_settings: could not save %s: %s", SETTINGS_FILE, exc)


# ── Module-level singleton ─────────────────────────────────────────────────────
# Import this everywhere:  from app_settings import settings

settings = AppSettings()
