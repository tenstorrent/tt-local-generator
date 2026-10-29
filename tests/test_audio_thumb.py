"""audio_thumb.make_waveform_thumbnail: a real PNG from a real decode, None on anything unreadable."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import audio_thumb
from PIL import Image

BG, FG = (0x0F, 0x2A, 0x35), (0x4F, 0xD1, 0xC5)


def _tone(path, fmt="flac"):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    str(path)], check=True, stdin=subprocess.DEVNULL, timeout=30)
    return path


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg to make the tone")
def test_waveform_thumbnail_is_nonblank_for_a_tone(tmp_path):
    src = _tone(tmp_path / "t.flac")
    out = audio_thumb.make_waveform_thumbnail(str(src), str(tmp_path / "t.png"))
    assert out == str(tmp_path / "t.png")
    img = Image.open(out).convert("RGB")
    assert img.size == (320, 180)
    colours = {c for _, c in img.getcolors(maxcolors=100000)}
    assert BG in colours and FG in colours


def test_waveform_returns_none_for_garbage_file(tmp_path):
    bad = tmp_path / "x.flac"
    bad.write_bytes(b"not audio at all" * 10)
    out = tmp_path / "x.png"
    assert audio_thumb.make_waveform_thumbnail(str(bad), str(out)) is None
    assert not out.exists()


def test_waveform_returns_none_for_missing_file_and_never_raises(tmp_path):
    assert audio_thumb.make_waveform_thumbnail(str(tmp_path / "nope.flac"), str(tmp_path / "o.png")) is None


def test_waveform_returns_none_when_ffmpeg_is_absent(tmp_path, monkeypatch):
    src = tmp_path / "t.flac"
    src.write_bytes(b"x" * 64)          # the decoder is faked to raise, so the content does not matter
    monkeypatch.setattr(audio_thumb.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("ffmpeg")))
    assert audio_thumb.make_waveform_thumbnail(str(src), str(tmp_path / "o.png")) is None


def test_ffmpeg_runs_with_devnull_stdin_and_a_timeout(tmp_path, monkeypatch):
    seen = {}

    def fake(cmd, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(audio_thumb.subprocess, "run", fake)
    assert audio_thumb.make_waveform_thumbnail("a.flac", str(tmp_path / "o.png")) is None
    assert seen["stdin"] == subprocess.DEVNULL and seen["timeout"] > 0
