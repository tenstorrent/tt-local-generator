# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
ffmpeg utility plugin — frame extraction, format conversion, metadata.

All functions call ffmpeg/ffprobe as subprocesses.  stdin=DEVNULL prevents
blocking on interactive prompts.  check=True propagates non-zero exit as
CalledProcessError; callers in the remix engine catch this and apply fallback.

In-process import usage (remix engine):
    from plugins.ffmpeg.plugin import extract_frame, get_metadata

Note: this plugin is marked utility:true in mcp.json, so plugin_loader skips
it for the generator registry and the MCP server tool list. It is used
in-process only by the remix engine (app/remix_popover.py).
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


def extract_frame(
    input_path: str,
    output_path: str,
    timestamp: float = 0.0,
    format: str = "jpg",  # noqa: A002
) -> str:
    """Extract one frame from *input_path* at *timestamp* seconds.

    Returns *output_path* on success.
    Raises subprocess.CalledProcessError on ffmpeg failure.
    """
    # Format timestamp without trailing ".0" for whole-second values so that
    # callers and tests can match the string "0" or "2" rather than "0.0"/"2.0".
    ts_str = str(int(timestamp)) if timestamp == int(timestamp) else str(timestamp)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", ts_str,
            "-i", input_path,
            "-frames:v", "1",
            "-update", "1",
            output_path,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
    )
    return output_path


def get_metadata(input_path: str) -> dict:
    """Return a dict with duration, width, height, fps, codec, size_bytes.

    Raises subprocess.CalledProcessError if ffprobe fails.
    Raises ValueError if the JSON output cannot be parsed.
    """
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            input_path,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next(
        (s for s in streams if s.get("codec_type") == "video"),
        streams[0] if streams else {},
    )
    fmt = data.get("format", {})

    fps_raw = video.get("r_frame_rate", "0/1")
    try:
        num, den = fps_raw.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except Exception:
        fps = 0.0

    return {
        "width":      int(video.get("width", 0)),
        "height":     int(video.get("height", 0)),
        "fps":        fps,
        "codec":      video.get("codec_name", ""),
        "duration":   float(fmt.get("duration", 0)),
        "size_bytes": int(fmt.get("size", 0)),
    }


def convert_to_gif(
    input_path: str,
    output_path: str,
    fps: int = 12,
    width: int = 480,
) -> str:
    """Convert *input_path* to an optimised animated GIF via two-pass palette.

    Pass 1 generates a palette PNG tuned to the video's color distribution.
    Pass 2 applies dithering using that palette for the final GIF.

    Returns *output_path* on success.
    Raises subprocess.CalledProcessError on any ffmpeg failure.
    """
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        palette_path = tmp.name

    scale = f"fps={fps},scale={width}:-1:flags=lanczos"
    try:
        # Pass 1: generate optimised palette
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", input_path,
                "-vf", f"{scale},palettegen",
                palette_path,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
        )

        # Pass 2: encode GIF using the palette with dithering
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", input_path, "-i", palette_path,
                "-vf", f"{scale},paletteuse",
                output_path,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
        )
    finally:
        Path(palette_path).unlink(missing_ok=True)
    return output_path


def convert_to_mp4(input_path: str, output_path: str) -> str:
    """Re-encode *input_path* as H.264 MP4 suitable for web playback.

    Uses yuv420p pixel format for maximum browser/player compatibility.
    +faststart moves the moov atom to the front for progressive streaming.
    The scale filter rounds width/height to even numbers (required by libx264).

    Returns *output_path* on success.
    Raises subprocess.CalledProcessError on ffmpeg failure.
    """
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            output_path,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
    )
    return output_path


def resize(
    input_path: str,
    output_path: str,
    width: int,
    height: "int | None" = None,
) -> str:
    """Resize *input_path* to *width* px (height scales proportionally unless given).

    When *height* is omitted, ffmpeg uses -1 to compute the correct height
    while preserving the original aspect ratio.

    Returns *output_path* on success.
    Raises subprocess.CalledProcessError on ffmpeg failure.
    """
    h_expr = str(height) if height is not None else "-1"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", f"scale={width}:{h_expr}",
            output_path,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
    )
    return output_path
