#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Persistent generation history.

Stores metadata and file paths for every completed generation in:
    ~/.local/share/tt-video-gen/
        history.json     — list of GenerationRecord dicts, newest-last
        videos/          — downloaded MP4 files
        thumbnails/      — first-frame JPEG thumbnails
"""
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional


# Root storage directory
STORAGE_DIR = Path.home() / ".local" / "share" / "tt-video-gen"
VIDEOS_DIR = STORAGE_DIR / "videos"
THUMBNAILS_DIR = STORAGE_DIR / "thumbnails"
HISTORY_FILE = STORAGE_DIR / "history.json"


@dataclass
class GenerationRecord:
    """Metadata for a single completed video generation."""

    id: str                             # Unique local ID (matches server job ID)
    prompt: str                         # Generation prompt
    negative_prompt: str                # Negative prompt (empty string if none)
    num_inference_steps: int            # Steps used
    seed: int                           # Seed used (-1 = random/unknown)
    video_path: str                     # Absolute path to the MP4 file
    thumbnail_path: str                 # Absolute path to the thumbnail JPEG
    created_at: str                     # ISO 8601 timestamp
    duration_s: float = 0.0            # Wall-clock generation time in seconds
    seed_image_path: str = ""           # Optional reference/seed image (empty = none)

    @classmethod
    def new(
        cls,
        job_id: str,
        prompt: str,
        negative_prompt: str,
        num_inference_steps: int,
        seed: int,
        duration_s: float = 0.0,
        seed_image_path: str = "",
    ) -> "GenerationRecord":
        """Create a new record with pre-computed storage paths."""
        ts = datetime.now()
        ts_str = ts.strftime("%Y%m%d_%H%M%S")

        video_path = str(VIDEOS_DIR / f"{ts_str}_{job_id[:8]}.mp4")
        thumbnail_path = str(THUMBNAILS_DIR / f"{ts_str}_{job_id[:8]}.jpg")

        return cls(
            id=job_id,
            prompt=prompt,
            negative_prompt=negative_prompt,
            num_inference_steps=num_inference_steps,
            seed=seed,
            video_path=video_path,
            thumbnail_path=thumbnail_path,
            created_at=ts.isoformat(),
            duration_s=duration_s,
            seed_image_path=seed_image_path,
        )

    @property
    def display_time(self) -> str:
        """Human-readable creation time, e.g. '14:32'."""
        try:
            dt = datetime.fromisoformat(self.created_at)
            return dt.strftime("%H:%M")
        except (ValueError, TypeError):
            return ""

    @property
    def video_exists(self) -> bool:
        return Path(self.video_path).exists()

    @property
    def thumbnail_exists(self) -> bool:
        return Path(self.thumbnail_path).exists()


class HistoryStore:
    """
    Loads and persists the list of GenerationRecord objects.

    Thread-safety: not designed for concurrent writes; all writes happen
    from the Qt main thread after the worker emits finished().
    """

    def __init__(self):
        # Ensure storage directories exist
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

        self._records: List[GenerationRecord] = []
        self._load()

    def _load(self) -> None:
        """Load history from disk. Silently ignores missing or corrupt files."""
        if not HISTORY_FILE.exists():
            return
        try:
            raw = json.loads(HISTORY_FILE.read_text())
            # Tolerate older records that predate the seed_image_path field
            self._records = [
                GenerationRecord(**{**r, "seed_image_path": r.get("seed_image_path", "")})
                for r in raw
            ]
        except Exception:
            # Corrupt history — start fresh rather than crash
            self._records = []

    def _save(self) -> None:
        """Persist history to disk."""
        HISTORY_FILE.write_text(
            json.dumps([asdict(r) for r in self._records], indent=2)
        )

    def append(self, record: GenerationRecord) -> None:
        """Add a new record and persist immediately."""
        self._records.append(record)
        self._save()

    def all_records(self) -> List[GenerationRecord]:
        """Return all records, newest first."""
        return list(reversed(self._records))

    def __len__(self) -> int:
        return len(self._records)
