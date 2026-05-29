"""Tests for the pure-Python helper functions in remix_popover.py.

These helpers are GTK-free and can run without a display. They were added
after review bugs were found in _source_type_from_record (returning 'artgen'
for palette/verse records instead of the generator type) and _neg_from_record
(crashing on MediaRecord.params_dict property).
"""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

# Import only the pure helpers — not the GTK classes
from remix_popover import (
    _source_type_from_record,
    _prompt_from_record,
    _neg_from_record,
    _thumbnail_path,
    _media_path,
    _build_hint,
)


# ── _source_type_from_record ───────────────────────────────────────────────────

def test_source_type_prefers_generator_type_over_media_type():
    """Artgen records: generator_type ('palette') beats media_type ('artgen')."""
    rec = SimpleNamespace(media_type="artgen", generator_type="palette")
    assert _source_type_from_record(rec) == "palette"


def test_source_type_uses_generator_type_for_verse():
    rec = SimpleNamespace(media_type="artgen", generator_type="verse")
    assert _source_type_from_record(rec) == "verse"


def test_source_type_falls_back_to_media_type_for_video():
    rec = SimpleNamespace(media_type="video", generator_type=None)
    assert _source_type_from_record(rec) == "video"


def test_source_type_skips_artgen_media_type_without_generator_type():
    """If generator_type is absent and media_type is 'artgen', fall through to 'artgen'."""
    rec = SimpleNamespace(media_type="artgen", generator_type=None)
    assert _source_type_from_record(rec) == "artgen"


def test_source_type_handles_image_record():
    rec = SimpleNamespace(media_type="image", generator_type=None)
    assert _source_type_from_record(rec) == "image"


def test_source_type_handles_missing_attributes():
    """Record with no media_type or generator_type defaults to 'video'."""
    rec = SimpleNamespace()
    assert _source_type_from_record(rec) == "video"


# ── _neg_from_record ───────────────────────────────────────────────────────────

def test_neg_from_record_direct_field():
    """GenerationRecord: reads negative_prompt directly."""
    rec = SimpleNamespace(negative_prompt="blurry, low quality")
    assert _neg_from_record(rec) == "blurry, low quality"


def test_neg_from_record_empty_direct_field():
    rec = SimpleNamespace(negative_prompt="")
    assert _neg_from_record(rec) == ""


def test_neg_from_record_params_dict_property():
    """MediaRecord: reads from params_dict dict property."""
    rec = SimpleNamespace(
        params_dict={"negative_prompt": "overexposed"},
    )
    assert _neg_from_record(rec) == "overexposed"


def test_neg_from_record_params_dict_callable():
    """MediaRecord: reads from params_dict() callable."""
    rec = SimpleNamespace(
        params_dict=lambda: {"negative_prompt": "blurry"},
    )
    assert _neg_from_record(rec) == "blurry"


def test_neg_from_record_no_neg_in_params_dict():
    rec = SimpleNamespace(params_dict={"other_key": "val"})
    assert _neg_from_record(rec) == ""


def test_neg_from_record_no_attributes():
    rec = SimpleNamespace()
    assert _neg_from_record(rec) == ""


def test_neg_from_record_does_not_crash_on_property_dict():
    """Regression: MediaRecord.params_dict is a property returning dict, not callable."""
    class _MediaLike:
        @property
        def params_dict(self):
            return {"negative_prompt": "test"}
    assert _neg_from_record(_MediaLike()) == "test"


# ── _prompt_from_record / _thumbnail_path / _media_path ───────────────────────

def test_prompt_from_record():
    rec = SimpleNamespace(prompt="a candle flame")
    assert _prompt_from_record(rec) == "a candle flame"


def test_prompt_from_record_missing():
    assert _prompt_from_record(SimpleNamespace()) == ""


def test_thumbnail_path_video_record():
    rec = SimpleNamespace(thumbnail_path="/tmp/thumb.jpg")
    assert _thumbnail_path(rec) == "/tmp/thumb.jpg"


def test_thumbnail_path_missing():
    assert _thumbnail_path(SimpleNamespace()) == ""


def test_media_path_prefers_video_path():
    rec = SimpleNamespace(video_path="/tmp/vid.mp4", file_path="/tmp/art.svg")
    assert _media_path(rec) == "/tmp/vid.mp4"


def test_media_path_falls_back_to_file_path():
    rec = SimpleNamespace(video_path="", file_path="/tmp/art.svg")
    assert _media_path(rec) == "/tmp/art.svg"


def test_media_path_missing():
    assert _media_path(SimpleNamespace()) == ""


# ── _build_hint ────────────────────────────────────────────────────────────────

def test_build_hint_prompt_only():
    rec = SimpleNamespace(prompt="misty mountains", video_path="", file_path="")
    result = _build_hint(rec, "video", "video", {"prompt"})
    assert result == "misty mountains"


def test_build_hint_text_key():
    rec = SimpleNamespace(prompt="cathedral dust", video_path="", file_path="")
    result = _build_hint(rec, "verse", "video", {"text"})
    assert result == "cathedral dust"


def test_build_hint_vibe_key():
    rec = SimpleNamespace(prompt="volcanic winter", video_path="", file_path="")
    result = _build_hint(rec, "landscape", "video", {"vibe"})
    assert result == "volcanic winter"


def test_build_hint_colors_from_palette_file(tmp_path):
    palette_data = {
        "name": "Drowned Ironwork",
        "colors": [
            {"hex": "#2c3e50", "role": "background"},
            {"hex": "#8e9eab", "role": "midtone"},
            {"hex": "#c0392b", "role": "accent"},
        ],
        "lore": "Wet stone in a flooded foundry.",
    }
    palette_file = tmp_path / "palette.json"
    palette_file.write_text(json.dumps(palette_data))

    rec = SimpleNamespace(prompt="drowned empire", file_path=str(palette_file), video_path="")
    result = _build_hint(rec, "palette", "video", {"colors"})
    assert "#2c3e50" in result
    assert "#8e9eab" in result


def test_build_hint_lore_from_palette_file(tmp_path):
    palette_data = {"lore": "The hush of a cathedral.", "colors": []}
    palette_file = tmp_path / "palette.json"
    palette_file.write_text(json.dumps(palette_data))

    rec = SimpleNamespace(prompt="", file_path=str(palette_file), video_path="")
    result = _build_hint(rec, "palette", "video", {"lore"})
    assert "cathedral" in result


def test_build_hint_empty_active_keys():
    rec = SimpleNamespace(prompt="test", video_path="", file_path="")
    result = _build_hint(rec, "video", "video", set())
    assert result == ""


def test_build_hint_missing_file_silently_ignored():
    """If palette file doesn't exist, colors/lore silently produce nothing."""
    rec = SimpleNamespace(prompt="test", file_path="/nonexistent/file.json", video_path="")
    result = _build_hint(rec, "palette", "video", {"colors", "lore"})
    # Should not raise; colors/lore silently return nothing; prompt if included
    assert isinstance(result, str)


def test_build_hint_combined_prompt_and_colors(tmp_path):
    palette_data = {"colors": [{"hex": "#ff0000", "role": "accent"}], "lore": ""}
    palette_file = tmp_path / "p.json"
    palette_file.write_text(json.dumps(palette_data))

    rec = SimpleNamespace(prompt="volcanic winter", file_path=str(palette_file), video_path="")
    result = _build_hint(rec, "palette", "video", {"prompt", "colors"})
    assert "volcanic winter" in result
    assert "#ff0000" in result
