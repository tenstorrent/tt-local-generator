#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Background worker for video generation.

Runs the full pipeline in a plain thread (no Qt dependency):
  1. Submit job to the server (or re-attach via _job_id_override)
  2. Poll status every 3 seconds until complete or failed
  3. Download the MP4 to local storage
  4. Extract a thumbnail (first frame) via ffmpeg
  5. Write prompt sidecar .txt
  6. Persist to history

Communication back to the UI is via plain callbacks passed to run_with_callbacks().
The caller (GTK main window) wraps each callback in GLib.idle_add() so that UI
updates always happen on the main thread. Never touch GTK widgets from here.
"""
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from api_client import APIClient
from history_store import THUMBNAILS_DIR, GenerationRecord, HistoryStore


class GenerationWorker:
    """
    Runs a single video generation job end-to-end in a background thread.

    Usage (GTK):
        gen = GenerationWorker(client, store, prompt, ...)
        thread = threading.Thread(target=lambda: gen.run_with_callbacks(
            on_progress=lambda msg: GLib.idle_add(update_status, msg),
            on_finished=lambda rec: GLib.idle_add(handle_done, rec),
            on_error=lambda msg: GLib.idle_add(handle_error, msg),
        ), daemon=True)
        thread.start()
    """

    POLL_INTERVAL = 3.0

    def __init__(
        self,
        client: APIClient,
        store: HistoryStore,
        prompt: str,
        negative_prompt: str,
        num_inference_steps: int,
        seed: int,
        seed_image_path: str = "",
    ):
        self._client = client
        self._store = store
        self._prompt = prompt
        self._negative_prompt = negative_prompt
        self._steps = num_inference_steps
        self._seed = seed
        self._seed_image_path = seed_image_path
        self._cancelled = False
        self._job_id_override: Optional[str] = None  # set to skip submit (recovery)
        self._lock = threading.Lock()

    def cancel(self) -> None:
        """Request early termination. Thread-safe."""
        with self._lock:
            self._cancelled = True

    def _is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def _running(self) -> bool:
        """False once cancel() has been called — used by MainWindow to detect activity."""
        return not self._is_cancelled()

    def run_with_callbacks(
        self,
        on_progress: Callable[[str], None],
        on_finished: Callable[[GenerationRecord], None],
        on_error: Callable[[str], None],
    ) -> None:
        """
        Execute the full pipeline. Call this from a background thread.

        The callbacks will be invoked FROM THIS THREAD — callers must wrap them
        in GLib.idle_add() (or equivalent) to safely update GTK widgets.
        """
        start_time = time.monotonic()

        # ── 1. Submit or re-attach ────────────────────────────────────────────
        if self._job_id_override:
            job_id = self._job_id_override
            on_progress(f"Re-attached to job {job_id[:8]}…")
        else:
            try:
                on_progress("Submitting job…")
                seed_arg = self._seed if self._seed >= 0 else None
                job_id = self._client.submit(
                    prompt=self._prompt,
                    negative_prompt=self._negative_prompt or None,
                    num_inference_steps=self._steps,
                    seed=seed_arg,
                )
            except Exception as e:
                on_error(f"Submit failed: {e}")
                return
            on_progress(f"Job queued ({job_id[:8]}…)")

        # ── 2. Poll until complete ────────────────────────────────────────────
        while not self._is_cancelled():
            try:
                status, err = self._client.poll_status(job_id)
            except Exception as e:
                on_error(f"Poll error: {e}")
                return

            if status == "completed":
                break
            if status in ("failed", "cancelled"):
                on_error(f"Job {status}: {err or 'no details'}")
                return

            elapsed = int(time.monotonic() - start_time)
            on_progress(f"Generating… {elapsed}s ({status})")
            time.sleep(self.POLL_INTERVAL)

        if self._is_cancelled():
            on_error("Cancelled by user")
            return

        # ── 3. Build record ───────────────────────────────────────────────────
        duration = time.monotonic() - start_time

        persisted_seed_image = ""
        if self._seed_image_path and Path(self._seed_image_path).is_file():
            src = Path(self._seed_image_path)
            dest = THUMBNAILS_DIR / f"seed_{job_id[:8]}{src.suffix}"
            try:
                shutil.copy2(src, dest)
                persisted_seed_image = str(dest)
            except Exception:
                persisted_seed_image = self._seed_image_path

        record = GenerationRecord.new(
            job_id=job_id,
            prompt=self._prompt,
            negative_prompt=self._negative_prompt,
            num_inference_steps=self._steps,
            seed=self._seed,
            duration_s=round(duration, 1),
            seed_image_path=persisted_seed_image,
        )

        # ── 4. Download ───────────────────────────────────────────────────────
        try:
            on_progress(f"Downloading video… ({duration:.0f}s total)")
            self._client.download(job_id, Path(record.video_path))
        except Exception as e:
            on_error(f"Download failed: {e}")
            return

        # ── 5. Thumbnail ──────────────────────────────────────────────────────
        self._extract_thumbnail(record.video_path, record.thumbnail_path)

        # ── 6. Sidecar ────────────────────────────────────────────────────────
        self._write_prompt_sidecar(record)

        # ── 7. Persist and notify ─────────────────────────────────────────────
        self._store.append(record)
        on_finished(record)

    def _write_prompt_sidecar(self, record: GenerationRecord) -> None:
        """Write a .txt metadata file next to the MP4. Silently skips on I/O error."""
        txt_path = Path(record.video_path).with_suffix(".txt")
        lines = [f"prompt: {record.prompt}"]
        if record.negative_prompt:
            lines.append(f"negative_prompt: {record.negative_prompt}")
        lines += [
            f"steps: {record.num_inference_steps}",
            f"seed: {record.seed}",
            f"generated: {record.created_at}",
            f"duration_s: {record.duration_s}",
        ]
        if record.seed_image_path:
            lines.append(f"seed_image: {record.seed_image_path}")
        try:
            txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            pass

    def _extract_thumbnail(self, video_path: str, thumbnail_path: str) -> None:
        """
        Extract the first frame as a JPEG thumbnail via ffmpeg.
        Silently skips if ffmpeg is unavailable or fails.
        stdin=DEVNULL prevents ffmpeg from blocking on terminal input.
        """
        Path(thumbnail_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", video_path,
                    "-vframes", "1",
                    "-q:v", "2",
                    "-update", "1",   # write single image, not a sequence
                    thumbnail_path,
                ],
                stdin=subprocess.DEVNULL,   # don't block waiting for [q] keypress
                capture_output=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
