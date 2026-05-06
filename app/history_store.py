#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Persistent generation history — thin wrapper around media_store.

All video/image/animate records are stored in media.db via MediaStore.
The JSON history.json is migrated on first launch by MediaStore.__init__.

Storage layout (managed by media_store.py):
    ~/.local/share/tt-video-gen/
        media.db         — SQLite store (WAL mode)
        videos/          — downloaded MP4 files
        images/          — generated JPEG images (FLUX)
        thumbnails/      — first-frame JPEG thumbnails / image thumbnails
"""
import json
import os
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
    starred: int = 0                     # 0 | 1 — mirrors media_store.MediaRecord.starred

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

    @classmethod
    def new_animate(
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
        """Create a new animation record with media_type='animate'."""
        ts = datetime.now()
        ts_str = ts.strftime("%Y%m%d_%H%M%S")
        return cls(
            id=job_id,
            prompt=prompt,
            negative_prompt=negative_prompt,
            num_inference_steps=num_inference_steps,
            seed=seed,
            video_path=str(VIDEOS_DIR / f"{ts_str}_{job_id[:8]}.mp4"),
            thumbnail_path=str(THUMBNAILS_DIR / f"{ts_str}_{job_id[:8]}.jpg"),
            created_at=ts.isoformat(),
            duration_s=duration_s,
            seed_image_path=seed_image_path,
            media_type="animate",
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
    Thin wrapper around media_store for backward compatibility.

    All video/image/animate records are stored in media.db via MediaStore.
    The JSON history.json is migrated on first launch by MediaStore.__init__.
    """

    def __init__(self) -> None:
        # Ensure storage directories still exist (other code expects them)
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Public API (unchanged signatures) ─────────────────────────────────────

    def append(self, record: GenerationRecord) -> None:
        """Add a new record to media_store. Silently drops duplicates."""
        from media_store import media_store as _ms, MediaRecord
        if _ms.get(record.id) is not None:
            return  # deduplicate
        _ms.add(MediaRecord(
            id=record.id,
            media_type=record.media_type,
            created_at=record.created_at,
            file_path=record.video_path or record.image_path or "",
            thumbnail_path=record.thumbnail_path,
            prompt=record.prompt,
            model_id=record.model,
            generator_type=None,
            params=json.dumps({
                "negative_prompt":     record.negative_prompt,
                "num_inference_steps": record.num_inference_steps,
                "seed":                record.seed,
                "duration_s":          record.duration_s,
                "seed_image_path":     record.seed_image_path,
                "guidance_scale":      record.guidance_scale,
                "extra_meta":          record.extra_meta,
                "video_path":          record.video_path,
                "image_path":          record.image_path,
            }),
            starred=0,
        ))

    def all_records(self) -> list[GenerationRecord]:
        """Return all non-artgen records, newest first (media_store orders by created_at DESC)."""
        from media_store import media_store as _ms
        rows = _ms.query()
        return [self._to_gen(r) for r in rows if r.media_type != "artgen"]

    def star(self, record_id: str, starred: bool) -> None:
        """Toggle the starred flag for a video/image/animate record."""
        from media_store import media_store as _ms
        _ms.star(record_id, starred)

    def delete(self, record_id: str) -> Optional[GenerationRecord]:
        """
        Remove the record with the given ID from media_store and return it.
        Returns None if no matching record was found (or if it is an artgen record).
        """
        from media_store import media_store as _ms
        rec = _ms.get(record_id)
        if rec is None or rec.media_type == "artgen":
            return None
        gen = self._to_gen(rec)
        _ms.delete(record_id)
        return gen

    def __len__(self) -> int:
        from media_store import media_store as _ms
        return sum(1 for r in _ms.query() if r.media_type != "artgen")

    # ── Queue persistence (unchanged — kept in JSON) ───────────────────────────

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

    # ── Internal ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_gen(r) -> GenerationRecord:
        """Convert a MediaRecord back to a GenerationRecord for API compatibility."""
        p = r.params_dict
        return GenerationRecord(
            id=r.id,
            prompt=r.prompt,
            negative_prompt=p.get("negative_prompt", ""),
            num_inference_steps=p.get("num_inference_steps", 0),
            seed=p.get("seed", -1),
            video_path=p.get("video_path", ""),
            thumbnail_path=r.thumbnail_path,
            created_at=r.created_at,
            duration_s=p.get("duration_s", 0.0),
            seed_image_path=p.get("seed_image_path", ""),
            media_type=r.media_type,
            image_path=p.get("image_path", ""),
            guidance_scale=p.get("guidance_scale", 0.0),
            model=r.model_id,
            extra_meta=p.get("extra_meta", {}),
            starred=r.starred,
        )


# Module-level singleton for backward-compatible imports
history_store = HistoryStore()
