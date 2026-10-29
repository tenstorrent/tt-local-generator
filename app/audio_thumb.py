"""Waveform thumbnails for audio results (no GTK imports).

`make_waveform_thumbnail` decodes the file with ffmpeg to 8 kHz mono signed 16-bit PCM, reduces it to one peak per bar
and draws the bars with Pillow in the app's main scheme (#4FD1C5 on #0F2A35). Any failure returns None so the caller
keeps its placeholder; nothing here raises.
"""
from __future__ import annotations

import array
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

BG = (0x0F, 0x2A, 0x35)
FG = (0x4F, 0xD1, 0xC5)

# A song is at most a few minutes: 8 kHz mono s16 is 16 KB/s, so even 10 minutes is under 10 MB.
_DECODE_TIMEOUT_S = 60
_MAX_PCM_BYTES = 64 * 1024 * 1024


def _decode_pcm(audio_path: str) -> "array.array":
    """ffmpeg -> s16le mono 8 kHz. stdin is /dev/null (an inherited terminal stdin hangs ffmpeg)."""
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", audio_path, "-f", "s16le", "-ac", "1", "-ar", "8000", "-"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=_DECODE_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg exited {proc.returncode}")
    data = proc.stdout[:_MAX_PCM_BYTES]
    data = data[: len(data) // 2 * 2]
    samples = array.array("h")
    samples.frombytes(data)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def make_waveform_thumbnail(audio_path: str, out_path: str, size: "tuple[int, int]" = (320, 180)) -> "Optional[str]":
    """Draw a bar waveform of `audio_path` to `out_path` (PNG). Returns `out_path`, or None on any failure."""
    try:
        from PIL import Image, ImageDraw

        width, height = size
        samples = _decode_pcm(audio_path)
        if len(samples) == 0:
            return None
        bar_w, gap = 3, 1
        bars = max(1, width // (bar_w + gap))
        per = max(1, len(samples) // bars)
        peaks = []
        for i in range(bars):
            chunk = samples[i * per:(i + 1) * per]
            peaks.append(max((abs(s) for s in chunk), default=0))
        top = max(peaks)
        if top == 0:
            return None  # digital silence: a flat line says nothing, keep the placeholder
        img = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(img)
        mid = height // 2
        for i, pk in enumerate(peaks):
            half = max(1, int(pk / top * (height // 2 - 4)))
            x = i * (bar_w + gap)
            draw.rectangle([x, mid - half, x + bar_w - 1, mid + half], fill=FG)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, format="PNG")
        return out_path
    except Exception:  # noqa: BLE001 - a thumbnail must never break a save
        _log.debug("waveform thumbnail failed for %s", audio_path, exc_info=True)
        try:
            Path(out_path).unlink()
        except OSError:
            pass
        return None
