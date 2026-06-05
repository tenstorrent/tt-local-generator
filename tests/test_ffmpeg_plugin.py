"""Unit tests for the ffmpeg utility plugin.

All subprocess calls are mocked — no real ffmpeg invocation.
"""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "ffmpeg"))


def test_extract_frame_calls_ffmpeg(tmp_path):
    """extract_frame runs ffmpeg with -ss and -frames:v 1."""
    from plugin import extract_frame
    out = str(tmp_path / "frame.jpg")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        extract_frame("/fake/video.mp4", out, timestamp=2.5)
    args = mock_run.call_args[0][0]
    assert "ffmpeg" in args[0]
    assert "-ss" in args
    assert "2.5" in args
    assert out in args


def test_extract_frame_default_timestamp(tmp_path):
    """extract_frame defaults to timestamp=0."""
    from plugin import extract_frame
    out = str(tmp_path / "frame.jpg")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        extract_frame("/fake/video.mp4", out)
    args = mock_run.call_args[0][0]
    assert "0" in args  # timestamp 0


def test_extract_frame_propagates_error(tmp_path):
    """extract_frame lets CalledProcessError propagate."""
    from plugin import extract_frame
    out = str(tmp_path / "frame.jpg")
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "ffmpeg")):
        try:
            extract_frame("/fake/video.mp4", out)
            assert False, "should have raised"
        except subprocess.CalledProcessError:
            pass


def test_get_metadata_parses_ffprobe():
    """get_metadata returns dict with duration, width, height, fps."""
    from plugin import get_metadata
    fake_output = json.dumps({
        "streams": [{"width": 1280, "height": 720,
                     "r_frame_rate": "24/1", "codec_name": "h264",
                     "codec_type": "video"}],
        "format": {"duration": "5.5", "size": "1234567"},
    })
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_output.encode())
        meta = get_metadata("/fake/video.mp4")
    assert meta["width"] == 1280
    assert meta["height"] == 720
    assert abs(meta["duration"] - 5.5) < 0.01
    assert meta["fps"] == 24.0
    assert meta["codec"] == "h264"


def test_convert_to_gif_two_passes(tmp_path):
    """convert_to_gif calls ffmpeg twice (palette generation + dithering)."""
    from plugin import convert_to_gif
    out = str(tmp_path / "out.gif")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        convert_to_gif("/fake/video.mp4", out, fps=12, width=480)
    assert mock_run.call_count == 2
    first_args = mock_run.call_args_list[0][0][0]
    assert "palettegen" in " ".join(first_args)
    second_args = mock_run.call_args_list[1][0][0]
    assert "paletteuse" in " ".join(second_args)


def test_convert_to_mp4_calls_ffmpeg(tmp_path):
    """convert_to_mp4 encodes with libx264 and yuv420p."""
    from plugin import convert_to_mp4
    out = str(tmp_path / "out.mp4")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        convert_to_mp4("/fake/video.gif", out)
    args = mock_run.call_args[0][0]
    assert "libx264" in args
    assert "yuv420p" in args


def test_resize_calls_ffmpeg(tmp_path):
    """resize passes scale filter with correct width."""
    from plugin import resize
    out = str(tmp_path / "out.mp4")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        resize("/fake/video.mp4", out, width=512)
    args = " ".join(mock_run.call_args[0][0])
    assert "512" in args
    assert "scale" in args
