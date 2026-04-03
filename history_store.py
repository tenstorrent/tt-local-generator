#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Persistent generation history.

Stores metadata and file paths for every completed generation in:
    ~/.local/share/tt-video-gen/
        history.json     — list of GenerationRecord dicts, newest-last
        videos/          — downloaded MP4 files
        images/          — generated JPEG images (FLUX)
        thumbnails/      — first-frame JPEG thumbnails / image thumbnails
"""
import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional


# Root storage directory
STORAGE_DIR = Path.home() / ".local" / "share" / "tt-video-gen"
VIDEOS_DIR = STORAGE_DIR / "videos"
IMAGES_DIR = STORAGE_DIR / "images"
THUMBNAILS_DIR = STORAGE_DIR / "thumbnails"
HISTORY_FILE = STORAGE_DIR / "history.json"


@dataclass
class GenerationRecord:
    """Metadata for a single completed generation (video or image)."""

    id: str                             # Unique local ID (matches server job ID for video)
    prompt: str                         # Generation prompt
    negative_prompt: str                # Negative prompt (empty string if none)
    num_inference_steps: int            # Steps used
    seed: int                           # Seed used (-1 = random/unknown)
    video_path: str                     # Absolute path to the MP4 file (empty for images)
    thumbnail_path: str                 # Absolute path to the thumbnail JPEG
    created_at: str                     # ISO 8601 timestamp
    duration_s: float = 0.0            # Wall-clock generation time in seconds
    seed_image_path: str = ""           # Optional reference/seed image (empty = none)
    media_type: str = "video"           # "video" (Wan2.2) or "image" (FLUX)
    image_path: str = ""               # Absolute path to the image file (empty for videos)
    guidance_scale: float = 0.0        # Guidance scale used (image gen only)
    model: str = ""                    # Model identifier, e.g. "wan2.2-t2v", "mochi-1-preview", "flux.1-dev"
    extra_meta: dict = field(default_factory=dict)  # Free-form server response metadata

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
        model: str = "",
    ) -> "GenerationRecord":
        """Create a new video record with pre-computed storage paths."""
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
            media_type="video",
            model=model,
        )

    @classmethod
    def new_image(
        cls,
        job_id: str,
        prompt: str,
        negative_prompt: str,
        num_inference_steps: int,
        seed: int,
        duration_s: float = 0.0,
        guidance_scale: float = 3.5,
        model: str = "",
    ) -> "GenerationRecord":
        """Create a new image record with pre-computed storage paths (FLUX)."""
        ts = datetime.now()
        ts_str = ts.strftime("%Y%m%d_%H%M%S")

        image_path = str(IMAGES_DIR / f"{ts_str}_{job_id[:8]}.jpg")
        thumbnail_path = str(THUMBNAILS_DIR / f"{ts_str}_{job_id[:8]}.jpg")

        return cls(
            id=job_id,
            prompt=prompt,
            negative_prompt=negative_prompt,
            num_inference_steps=num_inference_steps,
            seed=seed,
            video_path="",
            thumbnail_path=thumbnail_path,
            created_at=ts.isoformat(),
            duration_s=duration_s,
            media_type="image",
            image_path=image_path,
            guidance_scale=guidance_scale,
            model=model,
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
        return bool(self.video_path) and Path(self.video_path).exists()

    @property
    def image_exists(self) -> bool:
        return bool(self.image_path) and Path(self.image_path).exists()

    @property
    def media_file_path(self) -> str:
        """Primary media file path — image_path for image records, video_path for video."""
        return self.image_path if self.media_type == "image" else self.video_path

    @property
    def media_exists(self) -> bool:
        """True if the primary media file exists on disk."""
        return bool(self.media_file_path) and Path(self.media_file_path).exists()

    @property
    def thumbnail_exists(self) -> bool:
        return bool(self.thumbnail_path) and Path(self.thumbnail_path).exists()


class HistoryStore:
    """
    Loads and persists the list of GenerationRecord objects.

    Thread-safety: not designed for concurrent writes; all writes happen
    from the Qt main thread after the worker emits finished().
    """

    def __init__(self):
        # Ensure storage directories exist
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

        self._records: List[GenerationRecord] = []
        self._load()

    def _load(self) -> None:
        """Load history from disk. Silently ignores missing or corrupt files."""
        if not HISTORY_FILE.exists():
            return
        try:
            raw = json.loads(HISTORY_FILE.read_text())
            # Tolerate older records missing newer fields (seed_image_path, media_type, etc.)
            self._records = [
                GenerationRecord(**{
                    **r,
                    "seed_image_path": r.get("seed_image_path", ""),
                    "media_type": r.get("media_type", "video"),
                    "image_path": r.get("image_path", ""),
                    "guidance_scale": r.get("guidance_scale", 0.0),
                    "model": r.get("model", ""),
                    "extra_meta": r.get("extra_meta", {}),
                })
                for r in raw
            ]
        except Exception:
            # Corrupt history — back it up then start fresh
            bak = HISTORY_FILE.with_suffix(".json.bak")
            try:
                shutil.copy2(HISTORY_FILE, bak)
            except OSError:
                pass
            self._records = []

    def _save(self) -> None:
        """Persist history to disk atomically (write tmp, then rename)."""
        tmp = HISTORY_FILE.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps([asdict(r) for r in self._records], indent=2)
        )
        os.replace(tmp, HISTORY_FILE)

    def append(self, record: GenerationRecord) -> None:
        """Add a new record and persist immediately."""
        self._records.append(record)
        self._save()

    def all_records(self) -> List[GenerationRecord]:
        """Return all records, newest first."""
        return list(reversed(self._records))

    def delete(self, record_id: str) -> Optional[GenerationRecord]:
        """
        Remove the record with the given ID, persist the change, and return
        the removed record so the caller can also delete its files on disk.
        Returns None if no matching record was found.
        """
        removed = next((r for r in self._records if r.id == record_id), None)
        if removed is None:
            return None
        self._records = [r for r in self._records if r.id != record_id]
        self._save()
        return removed

    def __len__(self) -> int:
        return len(self._records)

    # ── Queue persistence ──────────────────────────────────────────────────────

    _QUEUE_FILE = STORAGE_DIR / "queue.json"

    def save_queue(self, items: list) -> None:
        """Persist the pending queue to disk atomically.

        Each item is a dict with the same keys as _QueueItem (prompt,
        negative_prompt, steps, seed, seed_image_path, model_source,
        guidance_scale, ref_video_path, ref_char_path, animate_mode, model_id).
        Pass an empty list to clear the saved queue.
        """
        tmp = self._QUEUE_FILE.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(items, indent=2))
            os.replace(tmp, self._QUEUE_FILE)
        except OSError:
            pass  # non-fatal; queue loss on crash is better than a crash-on-crash

    def load_queue(self) -> list:
        """Return the persisted queue items, or [] if none / corrupt."""
        if not self._QUEUE_FILE.exists():
            return []
        try:
            return json.loads(self._QUEUE_FILE.read_text())
        except Exception:
            return []
