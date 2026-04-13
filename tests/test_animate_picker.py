"""
Unit tests for animate_picker — BundledClipScanner and extract_thumbnail.
No GTK display required for these tests.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from animate_picker import BundledClipScanner, extract_thumbnail


def test_bundled_scanner_empty_dir_returns_empty(tmp_path):
    """Scanning a non-existent directory returns {}."""
    scanner = BundledClipScanner(str(tmp_path / "nonexistent"))
    assert scanner.scan() == {}


def test_bundled_scanner_empty_clips_dir_returns_empty(tmp_path):
    """A clips dir with no subdirectories returns {}."""
    clips_dir = tmp_path / "motion_clips"
    clips_dir.mkdir()
    scanner = BundledClipScanner(str(clips_dir))
    assert scanner.scan() == {}


def test_bundled_scanner_discovers_categories_and_clips(tmp_path):
    """Scanner finds category subdirs and MP4 files within them."""
    clips_dir = tmp_path / "motion_clips"
    walk_dir = clips_dir / "walk"
    walk_dir.mkdir(parents=True)
    (walk_dir / "walk_forward.mp4").write_bytes(b"fake-mp4")
    (walk_dir / "walk_backward.mp4").write_bytes(b"fake-mp4")
    (clips_dir / "readme.txt").write_text("not a dir")  # should be ignored

    # Patch extract_thumbnail so no ffmpeg call is made
    with patch("animate_picker.extract_thumbnail", return_value=False):
        scanner = BundledClipScanner(str(clips_dir))
        result = scanner.scan()

    assert "walk" in result
    names = {clip["name"] for clip in result["walk"]}
    assert names == {"walk_forward", "walk_backward"}
    assert len(result) == 1  # only the 'walk' category


def test_bundled_scanner_skips_dirs_with_no_mp4(tmp_path):
    """A subdirectory with no MP4 files is omitted from results."""
    clips_dir = tmp_path / "motion_clips"
    empty_cat = clips_dir / "gestures"
    empty_cat.mkdir(parents=True)
    (empty_cat / "readme.txt").write_text("no clips yet")

    with patch("animate_picker.extract_thumbnail", return_value=False):
        result = BundledClipScanner(str(clips_dir)).scan()

    assert result == {}


def test_bundled_scanner_clip_dict_has_required_keys(tmp_path):
    """Each clip dict has 'name', 'mp4', and 'thumb' keys."""
    clips_dir = tmp_path / "motion_clips"
    (clips_dir / "run").mkdir(parents=True)
    mp4 = clips_dir / "run" / "run_forward.mp4"
    mp4.write_bytes(b"fake")

    with patch("animate_picker.extract_thumbnail", return_value=False):
        result = BundledClipScanner(str(clips_dir)).scan()

    clip = result["run"][0]
    assert clip["name"] == "run_forward"
    assert clip["mp4"] == str(mp4)
    assert "thumb" in clip


def test_extract_thumbnail_returns_false_on_missing_ffmpeg(tmp_path):
    """extract_thumbnail returns False when ffmpeg is not found."""
    src = str(tmp_path / "video.mp4")
    dest = str(tmp_path / "thumb.jpg")
    Path(src).write_bytes(b"fake")

    with patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg not found")):
        result = extract_thumbnail(src, dest)

    assert result is False
