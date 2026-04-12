# tests/test_generation_config.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from generation_config import (
    clip_frames, quality_steps, slot_for_steps, clip_label,
    MODELS_WITH_FIXED_FRAMES, CLIP_SLOTS, QUALITY_PRESETS,
)


def test_clip_frames_wan2_standard():
    assert clip_frames("wan2", "standard") == 81


def test_clip_frames_wan2_short():
    assert clip_frames("wan2", "short") == 49


def test_clip_frames_wan2_long():
    assert clip_frames("wan2", "long") == 121


def test_clip_frames_wan2_extended():
    assert clip_frames("wan2", "extended") == 193


def test_clip_frames_skyreels_standard():
    assert clip_frames("skyreels", "standard") == 33


def test_clip_frames_skyreels_short():
    assert clip_frames("skyreels", "short") == 9


def test_clip_frames_skyreels_long():
    assert clip_frames("skyreels", "long") == 65


def test_clip_frames_skyreels_extended():
    assert clip_frames("skyreels", "extended") == 97


def test_clip_frames_unknown_slot_snaps_to_standard():
    assert clip_frames("wan2", "bogus") == 81


def test_clip_frames_fixed_frame_model_returns_none():
    """Mochi is in MODELS_WITH_FIXED_FRAMES; clip_frames returns None for it."""
    assert clip_frames("mochi", "standard") is None


def test_clip_frames_truly_unknown_model_returns_none():
    """A model key unknown to both tables returns None (same as fixed-frame behaviour)."""
    assert clip_frames("unknown_model_xyz", "standard") is None


def test_mochi_in_fixed_frames():
    assert "mochi" in MODELS_WITH_FIXED_FRAMES
    assert MODELS_WITH_FIXED_FRAMES["mochi"] == 168


def test_quality_steps_fast():
    assert quality_steps("fast") == 10


def test_quality_steps_standard():
    assert quality_steps("standard") == 30


def test_quality_steps_cinematic():
    assert quality_steps("cinematic") == 40


def test_quality_steps_unknown_returns_standard():
    assert quality_steps("bogus") == 30


def test_slot_for_steps_known():
    assert slot_for_steps(10) == "fast"
    assert slot_for_steps(30) == "standard"
    assert slot_for_steps(40) == "cinematic"


def test_slot_for_steps_unknown_returns_none():
    assert slot_for_steps(25) is None


def test_clip_label_wan2_standard():
    label = clip_label("wan2", "standard")
    assert "81" in label
    assert "3.4" in label


def test_clip_label_skyreels_short():
    label = clip_label("skyreels", "short")
    assert "9 f" in label    # frame count
    assert "0.4" in label    # seconds (9/24 = 0.375 → rounds to 0.4)


def test_clip_label_mochi_fixed():
    label = clip_label("mochi", "standard")
    assert "168" in label
    assert "fixed" in label


def test_clip_slots_order():
    assert CLIP_SLOTS == ["short", "standard", "long", "extended"]


def test_quality_presets_structure():
    assert len(QUALITY_PRESETS) == 3
    names = [p[0] for p in QUALITY_PRESETS]
    assert names == ["fast", "standard", "cinematic"]
