import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from chip_config import load_chips, load_chips_for_artgen


def _labels(cats):
    return [c.label for cat in cats for c in cat.chips]


def test_palette_bank_present_and_photo_absent():
    cats = load_chips_for_artgen("palette")   # real app/config/prompt_chips.yaml
    labels = _labels(cats)
    assert any("moody" in l.lower() or "pastel" in l.lower() for l in labels)
    # a photo-only chip must NOT appear for palette
    assert not any("cinematic" in l.lower() or "aerial" in l.lower() for l in labels)


def test_verse_text_type_gets_banks():
    cats = load_chips_for_artgen("verse")
    assert cats, "verse should get at least the shared mood bank"


def test_every_artgen_bank_has_a_surprise_chip():
    for t in ("palette", "verse", "ansi", "landscape"):
        cats = load_chips_for_artgen(t)
        assert any(c.surprise for cat in cats for c in cat.chips), f"{t} lacks a Surprise chip"


def test_shared_mood_bank_reaches_every_type():
    p = {c.name for c in load_chips_for_artgen("palette")}
    v = {c.name for c in load_chips_for_artgen("verse")}
    assert p & v, "a shared (artgen) category should appear for both"


def test_native_image_bank_excludes_artgen_categories():
    names = {c.name for c in load_chips("image")}
    # the shared artgen mood bank + palette bank must not show on the native image tab
    assert "Feeling" not in names
