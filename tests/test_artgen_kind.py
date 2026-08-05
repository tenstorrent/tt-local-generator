"""Tests for artgen_seed_kind — classify an artgen artifact's pipeline seed kind.

Pure, extension-driven classifier: maps a file path (as produced by any
media_type="artgen" generator) to a pipeline seed KIND string, or None when
the artifact isn't seedable as a pipeline (e.g. .json, unknown, or missing).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from artgen_kind import artgen_seed_kind  # noqa: E402


def test_txt_is_text():
    assert artgen_seed_kind("/tmp/lore.txt") == "text"


def test_md_is_text():
    assert artgen_seed_kind("/tmp/lore.md") == "text"


def test_py_is_text():
    assert artgen_seed_kind("/tmp/generated.py") == "text"


def test_png_is_image():
    assert artgen_seed_kind("/tmp/art.png") == "image"


def test_jpg_is_image():
    assert artgen_seed_kind("/tmp/art.jpg") == "image"


def test_jpeg_is_image():
    assert artgen_seed_kind("/tmp/art.jpeg") == "image"


def test_svg_is_image():
    assert artgen_seed_kind("/tmp/art.svg") == "image"


def test_ans_is_image():
    assert artgen_seed_kind("/tmp/art.ans") == "image"


def test_webp_is_image():
    assert artgen_seed_kind("/tmp/art.webp") == "image"


def test_gif_is_gif():
    assert artgen_seed_kind("/tmp/art.gif") == "gif"


def test_json_is_none():
    assert artgen_seed_kind("/tmp/data.json") is None


def test_none_path_is_none():
    assert artgen_seed_kind(None) is None


def test_no_extension_is_none():
    assert artgen_seed_kind("/tmp/noext") is None


def test_unknown_extension_is_none():
    assert artgen_seed_kind("/tmp/art.xyz") is None


def test_missing_file_path_still_classified_by_extension():
    """Classification is extension-driven only — the file need not exist."""
    assert artgen_seed_kind("/no/such/path/lore.txt") == "text"


def test_case_insensitive_extension():
    assert artgen_seed_kind("/tmp/ART.PNG") == "image"
    assert artgen_seed_kind("/tmp/LORE.TXT") == "text"
    assert artgen_seed_kind("/tmp/ANIM.GIF") == "gif"


def test_generator_type_accepted_but_extension_wins():
    """generator_type is accepted for future use, but extension is authoritative."""
    assert artgen_seed_kind("/tmp/art.png", generator_type="ansi") == "image"
    assert artgen_seed_kind("/tmp/lore.txt", generator_type="ansi") == "text"


def test_empty_string_path_is_none():
    assert artgen_seed_kind("") is None


def test_palette_json_is_palette_kind():
    assert artgen_seed_kind("/x/pal.json", "palette") == "palette"


def test_other_json_still_none():
    assert artgen_seed_kind("/x/data.json", "somethingelse") is None
    assert artgen_seed_kind("/x/data.json") is None


def test_existing_kinds_unchanged():
    assert artgen_seed_kind("/x/a.png") == "image"
    assert artgen_seed_kind("/x/a.gif") == "gif"
    assert artgen_seed_kind("/x/a.md") == "text"
