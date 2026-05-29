"""Tests for RemixContext extension and ingredient model."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def test_remix_context_extended_fields():
    """Extended RemixContext accepts the new fields."""
    from artgen import RemixContext
    ctx = RemixContext(
        source_record={"id": "x", "prompt": "test"},
        source_type="video",
        target_type="animate",
        hint="test prompt",
        seed_image_path="/tmp/thumb.jpg",
        ref_video_path="/tmp/src.mp4",
        target_label="Animate",
        negative_hint="",
    )
    assert ctx.seed_image_path == "/tmp/thumb.jpg"
    assert ctx.ref_video_path == "/tmp/src.mp4"
    assert ctx.target_label == "Animate"
    assert ctx.negative_hint == ""


def test_remix_context_defaults():
    """New fields default to empty strings so old callsites still work."""
    from artgen import RemixContext
    ctx = RemixContext(
        source_record={},
        source_type="verse",
        target_type="video",
        hint="a fox",
    )
    assert ctx.seed_image_path == ""
    assert ctx.ref_video_path == ""
    assert ctx.target_label == ""
    assert ctx.negative_hint == ""


def test_ingredient_spec_fields():
    """IngredientSpec stores all fields correctly."""
    from artgen import IngredientSpec
    spec = IngredientSpec(key="prompt", label="Prompt text",
                          value="a red car", default_on=True)
    assert spec.key == "prompt"
    assert spec.label == "Prompt text"
    assert spec.value == "a red car"
    assert spec.default_on is True


def test_ingredient_spec_default_off():
    """IngredientSpec can be constructed with default_on=False."""
    from artgen import IngredientSpec
    spec = IngredientSpec(key="ref_video", label="Full video", value="/tmp/vid.mp4", default_on=False)
    assert spec.default_on is False


def test_ingredients_for_palette_all():
    """palette source exposes three ingredients."""
    from artgen import ingredients_for
    specs = ingredients_for("palette", "video")
    keys = [s.key for s in specs]
    assert "colors" in keys
    assert "lore" in keys
    assert "prompt" in keys


def test_ingredients_for_video_animate():
    """video -> animate: thumbnail (seed image) + prompt; no ref_video.

    Animate tab uses a seed image, not a motion-reference video — the
    motion-reference picker was removed from the UI. Using the thumbnail
    as the seed image is the correct ingredient for this target.
    """
    from artgen import ingredients_for
    specs = ingredients_for("video", "animate")
    keys = [s.key for s in specs]
    assert "thumbnail" in keys
    assert "prompt" in keys
    assert "ref_video" not in keys


def test_ingredients_for_video_i2v():
    """video -> video (I2V): thumbnail + prompt; no full video."""
    from artgen import ingredients_for
    specs = ingredients_for("video", "video")
    keys = [s.key for s in specs]
    assert "thumbnail" in keys
    assert "prompt" in keys
    assert "ref_video" not in keys


def test_ingredients_for_verse_video():
    """verse -> video: full text + theme prompt."""
    from artgen import ingredients_for
    specs = ingredients_for("verse", "video")
    keys = [s.key for s in specs]
    assert "text" in keys
    assert "prompt" in keys


def test_ingredients_for_image_video():
    """image -> video: image file + prompt."""
    from artgen import ingredients_for
    specs = ingredients_for("image", "video")
    keys = [s.key for s in specs]
    assert "image" in keys
    assert "prompt" in keys


def test_ingredients_for_unknown_pair():
    """ingredients_for returns [] for unknown (source, target) pairs."""
    from artgen import ingredients_for
    specs = ingredients_for("unknown_type", "unknown_target")
    assert specs == []
