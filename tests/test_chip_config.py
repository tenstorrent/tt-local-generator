"""Tests for chip_config.py — zero GTK dependencies."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import pytest
from pathlib import Path
from chip_config import load_chips, load_chips_for_artgen, surprise_pool, ChipEntry, ChipCategory

def _yaml(content: str, tmp_path: Path) -> Path:
    p = tmp_path / "chips.yaml"
    p.write_text(content)
    return p


# ── load_chips() basic filtering ─────────────────────────────────────────────

def test_load_chips_returns_categories_for_tab(tmp_path):
    p = _yaml("""
- name: Camera
  for: [video, animate]
  chips:
    - label: "🎥 cinematic"
      text: "cinematic shot"
      tip: "Wide-format filmic look"
""", tmp_path)
    cats = load_chips("video", p)
    assert len(cats) == 1
    assert cats[0].name == "Camera"
    assert cats[0].chips[0].label == "🎥 cinematic"
    assert cats[0].chips[0].text == "cinematic shot"
    assert cats[0].chips[0].tip == "Wide-format filmic look"


def test_category_excluded_when_tab_not_in_for(tmp_path):
    p = _yaml("""
- name: Camera
  for: [video]
  chips:
    - label: "🎥 cinematic"
      text: "cinematic shot"
""", tmp_path)
    cats = load_chips("image", p)
    assert cats == []


def test_category_for_defaults_to_all_tabs(tmp_path):
    p = _yaml("""
- name: Quality
  chips:
    - label: "✨ 4K"
      text: "4K, ultra-detailed"
""", tmp_path)
    for tab in ("video", "image", "animate"):
        cats = load_chips(tab, p)
        assert len(cats) == 1, f"Expected category for tab={tab}"


# ── chip-level for: override ──────────────────────────────────────────────────

def test_chip_level_for_overrides_category_for(tmp_path):
    p = _yaml("""
- name: Lighting
  for: [video, animate]
  chips:
    - label: "💡 studio"
      text: "studio lighting"
      for: [image]
""", tmp_path)
    # Category says video+animate, chip says image — chip wins
    image_cats = load_chips("image", p)
    assert len(image_cats) == 1
    assert image_cats[0].chips[0].label == "💡 studio"

    video_cats = load_chips("video", p)
    assert video_cats == []   # chip-level for=[image] excludes video


def test_chip_level_for_does_not_merge_with_category(tmp_path):
    """chip-level for: replaces (not merges with) category for:."""
    p = _yaml("""
- name: Style
  for: [video]
  chips:
    - label: "🌈 vibrant"
      text: "vibrant colors"
      for: [image]
    - label: "🎞 film grain"
      text: "35mm film grain"
""", tmp_path)
    video_cats = load_chips("video", p)
    # Only the chip without an override should appear
    assert len(video_cats) == 1
    assert len(video_cats[0].chips) == 1
    assert video_cats[0].chips[0].label == "🎞 film grain"


def test_categories_with_no_matching_chips_excluded(tmp_path):
    p = _yaml("""
- name: VideoOnly
  for: [video]
  chips:
    - label: "🚁 aerial"
      text: "aerial drone shot"
- name: ImageOnly
  for: [image]
  chips:
    - label: "💡 studio"
      text: "studio lighting"
""", tmp_path)
    cats = load_chips("animate", p)
    assert cats == []


# ── tip default ───────────────────────────────────────────────────────────────

def test_tip_defaults_to_empty_string(tmp_path):
    p = _yaml("""
- name: Style
  chips:
    - label: "🎞 grain"
      text: "film grain"
""", tmp_path)
    cats = load_chips("video", p)
    assert cats[0].chips[0].tip == ""


# ── error cases ───────────────────────────────────────────────────────────────

def test_missing_yaml_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_chips("video", tmp_path / "nonexistent.yaml")


def test_malformed_yaml_missing_label_raises_value_error(tmp_path):
    p = _yaml("""
- name: Style
  chips:
    - text: "film grain"
""", tmp_path)
    with pytest.raises(ValueError, match="label"):
        load_chips("video", p)


def test_malformed_yaml_missing_text_raises_value_error(tmp_path):
    p = _yaml("""
- name: Style
  chips:
    - label: "🎞 grain"
""", tmp_path)
    with pytest.raises(ValueError, match="text"):
        load_chips("video", p)


def test_empty_yaml_returns_empty_list(tmp_path):
    p = _yaml("", tmp_path)
    assert load_chips("video", p) == []


# ── surprise chips ────────────────────────────────────────────────────────────

def test_surprise_chip_needs_no_text(tmp_path):
    cfg = _yaml(
        "- name: Mood\n"
        "  for: [palette]\n"
        "  chips:\n"
        "    - {label: '🎲 Surprise', surprise: true}\n"
        "    - {label: 'moody', text: 'moody'}\n",
        tmp_path)
    cats = load_chips("palette", cfg)
    chips = cats[0].chips
    assert chips[0].surprise is True and chips[0].text == ""
    assert chips[1].surprise is False


def test_non_surprise_chip_still_requires_text(tmp_path):
    cfg = _yaml("- name: X\n  for: [palette]\n  chips:\n    - {label: 'a'}\n", tmp_path)
    with pytest.raises(ValueError):
        load_chips("palette", cfg)


# ── load_chips_for_artgen ─────────────────────────────────────────────────────

def test_load_chips_for_artgen_merges_type_and_shared(tmp_path):
    cfg = _yaml(
        "- name: Palette Mood\n  for: [palette]\n  chips:\n    - {label: 'moody', text: 'moody'}\n"
        "- name: Feeling\n  for: [artgen]\n  chips:\n    - {label: 'serene', text: 'serene'}\n"
        "- name: Camera\n  for: [video, image]\n  chips:\n    - {label: 'cine', text: 'cinematic'}\n",
        tmp_path)
    cats = load_chips_for_artgen("palette", cfg)
    names = [c.name for c in cats]
    assert "Palette Mood" in names and "Feeling" in names   # type + shared
    assert "Camera" not in names                             # photo bank excluded


def test_load_chips_for_artgen_type_with_no_banks_gets_shared_only(tmp_path):
    cfg = _yaml("- name: Feeling\n  for: [artgen]\n  chips:\n    - {label: 'serene', text: 'serene'}\n", tmp_path)
    cats = load_chips_for_artgen("verse", cfg)
    assert [c.name for c in cats] == ["Feeling"]


# ── surprise_pool ─────────────────────────────────────────────────────────────

def test_surprise_pool_excludes_surprise_chip(tmp_path):
    cfg = _yaml(
        "- name: Mood\n  for: [palette]\n  chips:\n"
        "    - {label: '🎲 Surprise', surprise: true}\n"
        "    - {label: 'moody', text: 'moody'}\n    - {label: 'lush', text: 'lush'}\n",
        tmp_path)
    cat = load_chips("palette", cfg)[0]
    assert surprise_pool(cat) == ["moody", "lush"]
