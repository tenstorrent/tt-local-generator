#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Background workers for media generation.

Four worker classes, all with identical run_with_callbacks() interfaces:

  GenerationWorker — Wan2.2 / Mochi / SkyReels video (async/job-based):
    1. Submit job to server (or re-attach via _job_id_override)
    2. Poll status every 3 seconds until complete or failed
    3. Download the MP4 to local storage
    4. Extract a thumbnail (first frame) via ffmpeg
    5. Write prompt sidecar .txt
    6. Persist to history

  ImageGenerationWorker — FLUX.1-dev image (synchronous):
    1. POST /v1/images/generations — blocks until the server responds (~15–90 s)
    2. Decode base64 response and write JPEG to local storage
    3. Create a scaled thumbnail via ffmpeg (falls back to copy)
    4. Write prompt sidecar .txt
    5. Persist to history

  AnimateGenerationWorker — Wan2.2-Animate character animation (async/job-based):
    Same poll-download cycle as GenerationWorker; accepts reference_video_path
    and reference_image_path for motion+character conditioning.

  AnimateDiffGenerationWorker — TTNN AnimateDiff GIF (local Blackhole, no server):
    1. check_hardware() — fail fast if no Blackhole device
    2. run_subprocess() — generate_blackhole_v2.py via tt-metal Python env
    3. make_gif_thumbnail() — extract first frame as JPEG
    4. Persist to history as media_type="animatediff"

Communication back to the UI is via plain callbacks. The caller (GTK main window)
wraps each callback in GLib.idle_add() so UI updates always happen on the main
thread. Never import or touch GTK widgets from here.
"""
import logging
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from api_client import APIClient
from history_store import THUMBNAILS_DIR, VIDEOS_DIR, GenerationRecord, HistoryStore


# ── Metadata helpers ───────────────────────────────────────────────────────────

# Keys to exclude when storing server response as extra_meta:
# large base64 payloads and fields already captured in GenerationRecord fields.
_META_SKIP = frozenset({
    "images",
    "video_b64",
    "reference_video_b64",
    "reference_image_b64",
    "status",
    "error",
})


def _safe_meta(data: dict) -> dict:
    """
    Return a copy of a server response dict safe to store as extra_meta.
    Strips large binary/base64 fields and fields already captured elsewhere.
    """
    return {
        k: v for k, v in data.items()
        if k not in _META_SKIP and not isinstance(v, (bytes, bytearray))
    }


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
        model: str = "wan2.2-t2v",
        num_frames: Optional[int] = None,
        image: Optional[str] = None,
    ):
        self._client = client
        self._store = store
        self._prompt = prompt
        self._negative_prompt = negative_prompt
        self._steps = num_inference_steps
        self._seed = seed
        self._seed_image_path = seed_image_path
        self._model = model
        self._num_frames = num_frames
        self._image = image  # base64-encoded conditioning image for I2V models
        self._cancelled = False
        self._job_id_override: Optional[str] = None  # set to skip submit (recovery)
        self._current_job_id: Optional[str] = None   # set after submission / re-attach so callers can exclude it from recovery scans
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
                    num_frames=self._num_frames,
                    image=self._image,
                )
            except Exception as e:
                on_error(f"Submit failed: {e}")
                return
            on_progress(f"Job queued ({job_id[:8]}…)")

        # Expose the live job ID so MainWindow can exclude it from recovery scans.
        self._current_job_id = job_id

        # ── 2. Poll until complete ────────────────────────────────────────────
        server_meta: dict = {}
        while not self._is_cancelled():
            try:
                status, err, data = self._client.poll_status(job_id)
            except Exception as e:
                on_error(f"Poll error: {e}")
                return

            if status == "completed":
                server_meta = _safe_meta(data)
                # Prefer the server-reported model over the locally-set default.
                # This corrects attribution for recovered jobs (which are created
                # with a generic default) and for any server that reports its
                # own model identifier.
                if data.get("model"):
                    self._model = data["model"]
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
            model=self._model,
        )
        record.extra_meta = server_meta

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
        sec_per_step = (
            f"{record.duration_s / record.num_inference_steps:.2f}"
            if record.duration_s and record.num_inference_steps
            else "—"
        )
        lines += [
            f"steps: {record.num_inference_steps}",
            f"seed: {record.seed}",
            f"generated: {record.created_at}",
            f"duration_s: {record.duration_s}",
            f"sec_per_step: {sec_per_step}",
        ]
        if record.seed_image_path:
            lines.append(f"seed_image: {record.seed_image_path}")
        try:
            txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as _e:
            logging.warning("sidecar write failed for %s: %s", txt_path, _e)

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


# ── Wan2.2-Animate-14B generation worker ──────────────────────────────────────

class AnimateGenerationWorker:
    """
    Runs a single Wan2.2-Animate-14B character animation job in a background thread.

    Like GenerationWorker, the server API is async/job-based — submit then poll.
    The key difference: a reference motion video and a character image are required
    inputs, base64-encoded and sent inline with the job submission.

    Two animation modes:
      "animation"   — character image mimics the motion in the reference video
      "replacement" — character image replaces the person in the reference video

    Usage (GTK):
        gen = AnimateGenerationWorker(client, store, reference_video_path,
                                      reference_image_path, ...)
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
        reference_video_path: str,
        reference_image_path: str,
        prompt: str = "",
        num_inference_steps: int = 20,
        seed: int = -1,
        animate_mode: str = "animation",
        model: str = "wan2.2-animate-14b",
    ):
        self._client = client
        self._store = store
        self._ref_video = reference_video_path
        self._ref_image = reference_image_path
        self._prompt = prompt
        self._steps = num_inference_steps
        self._seed = seed
        self._animate_mode = animate_mode
        self._model = model
        self._cancelled = False
        self._lock = threading.Lock()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    def _is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def run_with_callbacks(
        self,
        on_progress: Callable[[str], None],
        on_finished: Callable[[GenerationRecord], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Execute the full pipeline. Call from a background thread."""
        start_time = time.monotonic()

        # ── 1. Submit ──────────────────────────────────────────────────────────
        try:
            on_progress("Submitting animate job…")
            seed_arg = self._seed if self._seed >= 0 else None
            job_id = self._client.submit_animate(
                reference_video_path=self._ref_video,
                reference_image_path=self._ref_image,
                prompt=self._prompt,
                num_inference_steps=self._steps,
                seed=seed_arg,
                animate_mode=self._animate_mode,
            )
        except Exception as e:
            on_error(f"Submit failed: {e}")
            return
        on_progress(f"Animate job queued ({job_id[:8]}…)")

        # ── 2. Poll until complete ─────────────────────────────────────────────
        while not self._is_cancelled():
            try:
                status, err, _ = self._client.poll_status(job_id)
            except Exception as e:
                on_error(f"Poll error: {e}")
                return

            if status == "completed":
                break
            if status in ("failed", "cancelled"):
                on_error(f"Job {status}: {err or 'no details'}")
                return

            elapsed = int(time.monotonic() - start_time)
            on_progress(f"Animating… {elapsed}s ({status})")
            time.sleep(self.POLL_INTERVAL)

        if self._is_cancelled():
            on_error("Cancelled by user")
            return

        # ── 3. Build record ────────────────────────────────────────────────────
        duration = time.monotonic() - start_time

        # Persist the character image alongside the video as seed_image_path
        # so the detail panel can show it as context.
        persisted_ref_image = ""
        ref_img = Path(self._ref_image)
        if ref_img.is_file():
            dest = THUMBNAILS_DIR / f"animate_char_{job_id[:8]}{ref_img.suffix}"
            try:
                shutil.copy2(ref_img, dest)
                persisted_ref_image = str(dest)
            except Exception:
                persisted_ref_image = self._ref_image

        record = GenerationRecord.new_animate(
            job_id=job_id,
            prompt=self._prompt or f"[animate:{self._animate_mode}]",
            negative_prompt="",
            num_inference_steps=self._steps,
            seed=self._seed,
            duration_s=round(duration, 1),
            seed_image_path=persisted_ref_image,
            model=self._model,
        )

        # ── 4. Download ────────────────────────────────────────────────────────
        try:
            on_progress(f"Downloading video… ({duration:.0f}s total)")
            self._client.download(job_id, Path(record.video_path))
        except Exception as e:
            on_error(f"Download failed: {e}")
            return

        # ── 5. Thumbnail ───────────────────────────────────────────────────────
        self._extract_thumbnail(record.video_path, record.thumbnail_path)

        # ── 5b. Last frame ─────────────────────────────────────────────────────
        # Extract the final frame so the TT-TV auto-generation loop can use it
        # as a character reference for the next clip in the continuity chain.
        last_frame_path = str(
            Path(record.thumbnail_path).with_stem(
                Path(record.thumbnail_path).stem + "_last"
            )
        )
        if self._extract_last_frame(record.video_path, last_frame_path):
            record.extra_meta["last_frame_path"] = last_frame_path

        # ── 6. Sidecar ─────────────────────────────────────────────────────────
        self._write_prompt_sidecar(record)

        # ── 7. Persist and notify ──────────────────────────────────────────────
        self._store.append(record)
        on_finished(record)

    def _write_prompt_sidecar(self, record: GenerationRecord) -> None:
        txt_path = Path(record.video_path).with_suffix(".txt")
        sec_per_step = (
            f"{record.duration_s / record.num_inference_steps:.2f}"
            if record.duration_s and record.num_inference_steps
            else "—"
        )
        lines = [
            f"mode: animate:{self._animate_mode}",
            f"prompt: {record.prompt}",
            f"reference_video: {self._ref_video}",
            f"reference_image: {self._ref_image}",
            f"last_frame: {record.extra_meta.get('last_frame_path', '')}",
            f"steps: {record.num_inference_steps}",
            f"seed: {record.seed}",
            f"generated: {record.created_at}",
            f"duration_s: {record.duration_s}",
            f"sec_per_step: {sec_per_step}",
        ]
        try:
            txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as _e:
            logging.warning("sidecar write failed for %s: %s", txt_path, _e)

    def _extract_thumbnail(self, video_path: str, thumbnail_path: str) -> None:
        Path(thumbnail_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", video_path,
                    "-vframes", "1",
                    "-q:v", "2",
                    "-update", "1",
                    thumbnail_path,
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    def _extract_last_frame(self, video_path: str, last_frame_path: str) -> bool:
        """Extract the last frame of the video as a JPEG via ffmpeg.

        Uses -sseof -0.1 to seek 0.1 s from the end of the video, then grabs
        a single frame.  This gives the TT-TV auto-generation loop a character
        reference image for continuity chaining into the next clip.

        Returns True on success, False on any ffmpeg error (FileNotFoundError
        if ffmpeg is not installed, or TimeoutExpired).  The caller decides
        whether to record the path in extra_meta.
        """
        Path(last_frame_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-sseof", "-0.1",
                    "-i", video_path,
                    "-vframes", "1",
                    "-q:v", "2",
                    "-update", "1",
                    last_frame_path,
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=30,
            )
            return result.returncode == 0 and Path(last_frame_path).is_file()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False


# ── FLUX image generation worker ───────────────────────────────────────────────

class ImageGenerationWorker:
    """
    Runs a single image generation request end-to-end in a background thread.

    Supports FLUX.1-schnell (Python/uvicorn, ~3s) and SDXL via the C++ cpp_server
    (~2s). The image API is synchronous: the POST blocks until the image is ready.
    No polling is required. The response contains the image as base64-encoded JPEG.

    Usage (GTK):
        gen = ImageGenerationWorker(client, store, prompt, ...)
        thread = threading.Thread(target=lambda: gen.run_with_callbacks(
            on_progress=lambda msg: GLib.idle_add(update_status, msg),
            on_finished=lambda rec: GLib.idle_add(handle_done, rec),
            on_error=lambda msg: GLib.idle_add(handle_error, msg),
        ), daemon=True)
        thread.start()
    """

    def __init__(
        self,
        client: APIClient,
        store: HistoryStore,
        prompt: str,
        negative_prompt: str,
        num_inference_steps: int,
        seed: int,
        guidance_scale: float = 3.5,
        model: str = "flux.1-schnell",
    ):
        self._client = client
        self._store = store
        self._prompt = prompt
        self._negative_prompt = negative_prompt
        self._steps = num_inference_steps
        self._seed = seed
        self._guidance_scale = guidance_scale
        self._model = model
        self._cancelled = False
        self._lock = threading.Lock()

    def cancel(self) -> None:
        """Request early termination. Thread-safe."""
        with self._lock:
            self._cancelled = True

    def _is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def _running(self) -> bool:
        """False once cancel() has been called."""
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
        job_id = str(uuid.uuid4())   # local ID; image API has no server-side job ID

        # ── 1. Generate ───────────────────────────────────────────────────────
        try:
            on_progress("Generating image with FLUX.1-dev…")
            seed_arg = self._seed if self._seed >= 0 else None
            image_bytes, server_meta = self._client.generate_image(
                prompt=self._prompt,
                negative_prompt=self._negative_prompt or None,
                num_inference_steps=self._steps,
                seed=seed_arg,
                guidance_scale=self._guidance_scale,
            )
            server_meta = _safe_meta(server_meta)
        except Exception as e:
            on_error(f"Image generation failed: {e}")
            return

        if self._is_cancelled():
            on_error("Cancelled by user")
            return

        duration = time.monotonic() - start_time

        # ── 2. Build record ───────────────────────────────────────────────────
        record = GenerationRecord.new_image(
            job_id=job_id,
            prompt=self._prompt,
            negative_prompt=self._negative_prompt,
            num_inference_steps=self._steps,
            seed=self._seed,
            duration_s=round(duration, 1),
            guidance_scale=self._guidance_scale,
            model=self._model,
        )
        record.extra_meta = server_meta

        # ── 3. Save image ─────────────────────────────────────────────────────
        try:
            on_progress(f"Saving image… ({duration:.0f}s)")
            Path(record.image_path).parent.mkdir(parents=True, exist_ok=True)
            Path(record.image_path).write_bytes(image_bytes)
        except Exception as e:
            on_error(f"Failed to save image: {e}")
            return

        # ── 4. Thumbnail ──────────────────────────────────────────────────────
        self._make_thumbnail(record.image_path, record.thumbnail_path)

        # ── 5. Sidecar ────────────────────────────────────────────────────────
        self._write_prompt_sidecar(record)

        # ── 6. Persist and notify ─────────────────────────────────────────────
        self._store.append(record)
        on_finished(record)

    def _make_thumbnail(self, image_path: str, thumbnail_path: str) -> None:
        """
        Create a _THUMB_W × _THUMB_H thumbnail of the generated image via ffmpeg.
        Falls back to a straight copy if ffmpeg is unavailable or fails.
        """
        Path(thumbnail_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", image_path,
                    "-vf", "scale=200:112:force_original_aspect_ratio=decrease,"
                           "pad=200:112:(ow-iw)/2:(oh-ih)/2",
                    "-q:v", "3",
                    thumbnail_path,
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # No ffmpeg — just copy the original as the "thumbnail"
            try:
                shutil.copy2(image_path, thumbnail_path)
            except Exception:
                pass

    def _write_prompt_sidecar(self, record: GenerationRecord) -> None:
        """Write a .txt metadata file next to the JPEG. Silently skips on I/O error."""
        txt_path = Path(record.image_path).with_suffix(".txt")
        lines = [f"prompt: {record.prompt}"]
        if record.negative_prompt:
            lines.append(f"negative_prompt: {record.negative_prompt}")
        sec_per_step = (
            f"{record.duration_s / record.num_inference_steps:.2f}"
            if record.duration_s and record.num_inference_steps
            else "—"
        )
        lines += [
            f"steps: {record.num_inference_steps}",
            f"guidance_scale: {record.guidance_scale}",
            f"seed: {record.seed}",
            f"generated: {record.created_at}",
            f"duration_s: {record.duration_s}",
            f"sec_per_step: {sec_per_step}",
        ]
        try:
            txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as _e:
            logging.warning("sidecar write failed for %s: %s", txt_path, _e)


# ── AnimateDiff (local Blackhole) generation worker ────────────────────────────

class AnimateDiffGenerationWorker:
    """
    Runs a single AnimateDiff generation job locally on Blackhole hardware.

    Unlike the server-based workers, this runs generate_blackhole_v2.py as a
    subprocess via artgen.generators.animatediff.run_subprocess(). No network
    required — the TTNN UNet runs directly on the local Blackhole device.

    Produces an animated GIF in VIDEOS_DIR, then creates a thumbnail from the
    first frame. The result is stored as a GenerationRecord with media_type
    "animatediff" and surfaces in the video gallery alongside MP4s.

    Usage (GTK):
        gen = AnimateDiffGenerationWorker(store, prompt, ...)
        thread = threading.Thread(target=lambda: gen.run_with_callbacks(
            on_progress=lambda msg: GLib.idle_add(update_status, msg),
            on_finished=lambda rec: GLib.idle_add(handle_done, rec),
            on_error=lambda msg: GLib.idle_add(handle_error, msg),
        ), daemon=True)
        thread.start()
    """

    def __init__(
        self,
        store: HistoryStore,
        prompt: str,
        negative_prompt: str = "blurry, low quality",
        steps: int = 25,
        seed: int = 42,
        frames: int = 8,
        temporal_alpha: float = 0.35,
        model: str = "animatediff-blackhole",
        # v0.9 params
        mode: str = "blackhole",
        lightning: bool = False,
        lightning_steps: int = 4,
        multi_chip: bool = True,
        device_id: "int | None" = None,
        chain_from: "str | None" = None,
        chain_save: "str | None" = None,
        chain_alpha: float = 0.6,
        motion_adapter: "str | None" = None,
        motion_adapter_alpha: float = 1.0,
        motion_adapter_skip: "list | None" = None,
    ):
        self._store = store
        self._prompt = prompt
        self._negative_prompt = negative_prompt
        self._steps = steps
        self._seed = seed
        self._frames = frames
        self._temporal_alpha = temporal_alpha
        self._model = model
        self._mode = mode
        self._lightning = lightning
        self._lightning_steps = lightning_steps
        self._multi_chip = multi_chip
        self._device_id = device_id
        self._chain_from = chain_from
        self._chain_save = chain_save
        self._chain_alpha = chain_alpha
        self._motion_adapter = motion_adapter
        self._motion_adapter_alpha = motion_adapter_alpha
        self._motion_adapter_skip = motion_adapter_skip
        self._cancelled = False
        self._lock = threading.Lock()

    def cancel(self) -> None:
        """Request early termination. Thread-safe."""
        with self._lock:
            self._cancelled = True

    def _is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def _running(self) -> bool:
        """False once cancel() has been called."""
        return not self._is_cancelled()

    def run_with_callbacks(
        self,
        on_progress: Callable[[str], None],
        on_finished: Callable[[GenerationRecord], None],
        on_error: Callable[[str], None],
    ) -> None:
        """
        Execute the full pipeline. Call this from a background thread.

        Callbacks are invoked FROM THIS THREAD — callers must wrap them in
        GLib.idle_add() to safely update GTK widgets.
        """
        from artgen.generators.animatediff import (
            check_hardware,
            run_subprocess,
            make_gif_thumbnail,
        )

        start_time = time.monotonic()
        job_id = str(uuid.uuid4())

        # ── 1. Hardware check ─────────────────────────────────────────────────
        # Only required for blackhole mode; cpu/sim run without TT hardware.
        num_chips = 1
        if self._mode == "blackhole":
            on_progress("Checking hardware…")
            ok, hw_msg, num_chips = check_hardware()
            if not ok:
                on_error(f"AnimateDiff requires Blackhole hardware: {hw_msg}")
                return
        else:
            hw_msg = self._mode

        if self._is_cancelled():
            on_error("Cancelled by user")
            return

        # ── 2. Build output paths ─────────────────────────────────────────────
        from datetime import datetime, timezone
        ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = VIDEOS_DIR / f"{ts_str}_{job_id[:8]}.gif"
        thumb_path = THUMBNAILS_DIR / f"{ts_str}_{job_id[:8]}.jpg"
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

        # ── 3. Run subprocess ─────────────────────────────────────────────────
        effective_chips = num_chips if (self._multi_chip and self._device_id is None) else 1
        chip_info = (
            f"{effective_chips} chip{'s' if effective_chips != 1 else ''}"
            if self._mode == "blackhole" else hw_msg
        )
        on_progress(f"Loading AnimateDiff model ({chip_info})…")

        def _progress_fwd(msg: str) -> None:
            if not self._is_cancelled():
                elapsed = int(time.monotonic() - start_time)
                on_progress(f"{msg}  ({elapsed}s)")

        # The main-window "Use all chips in parallel" checkbox only sets
        # self._multi_chip (a bool) — it has no concept of remix vs coherent.
        # run_subprocess() defaults multichip_mode to "off", so without this
        # mapping the checkbox was a silent no-op: effective_chips could be
        # >1 but multichip_mode stayed "off" and every run fell through to
        # the single-chip path. Map the boolean to "remix" (seed-spread
        # glitch across chips using default ramp/stitch settings), which is
        # the intended "use all chips" behaviour for this simple checkbox.
        multichip_mode = "remix" if (self._multi_chip and effective_chips > 1) else "off"

        ok, err = run_subprocess(
            prompt=self._prompt,
            out_path=out_path,
            mode=self._mode,
            frames=self._frames,
            steps=self._steps,
            seed=self._seed,
            negative_prompt=self._negative_prompt,
            temporal_alpha=self._temporal_alpha,
            lightning=self._lightning,
            lightning_steps=self._lightning_steps,
            device_id=self._device_id,
            chain_from=self._chain_from,
            chain_save=self._chain_save,
            chain_alpha=self._chain_alpha,
            motion_adapter=self._motion_adapter,
            motion_adapter_alpha=self._motion_adapter_alpha,
            motion_adapter_skip=self._motion_adapter_skip,
            on_progress=_progress_fwd,
            num_chips=effective_chips,
            multichip_mode=multichip_mode,
        )

        if not ok:
            on_error(f"AnimateDiff failed: {err}")
            return

        if self._is_cancelled():
            on_error("Cancelled by user")
            return

        # ── 4. Thumbnail ──────────────────────────────────────────────────────
        make_gif_thumbnail(out_path, thumb_path)

        # ── 5. Build record ───────────────────────────────────────────────────
        duration = time.monotonic() - start_time
        record = GenerationRecord.new_animatediff(
            job_id=job_id,
            prompt=self._prompt,
            negative_prompt=self._negative_prompt,
            num_inference_steps=self._steps,
            seed=self._seed,
            video_path=str(out_path),
            thumbnail_path=str(thumb_path),
            duration_s=round(duration, 1),
            model=self._model,
        )

        # ── 6. Sidecar ────────────────────────────────────────────────────────
        self._write_prompt_sidecar(record)

        # ── 7. Persist and notify ─────────────────────────────────────────────
        self._store.append(record)
        on_finished(record)

    def _write_prompt_sidecar(self, record: GenerationRecord) -> None:
        """Write a .txt metadata file next to the GIF. Silently skips on I/O error."""
        txt_path = Path(record.video_path).with_suffix(".txt")
        sec_per_step = (
            f"{record.duration_s / record.num_inference_steps:.2f}"
            if record.duration_s and record.num_inference_steps
            else "—"
        )
        lines = [
            f"mode: animatediff",
            f"prompt: {record.prompt}",
            f"negative_prompt: {record.negative_prompt}",
            f"frames: {self._frames}",
            f"temporal_alpha: {self._temporal_alpha}",
            f"steps: {record.num_inference_steps}",
            f"seed: {record.seed}",
            f"generated: {record.created_at}",
            f"duration_s: {record.duration_s}",
            f"sec_per_step: {sec_per_step}",
        ]
        try:
            txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as _e:
            logging.warning("sidecar write failed for %s: %s", txt_path, _e)
