import importlib.util, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import intent_vocab as iv


def test_covers_all_native_class_types():
    from workflow_compat import COMPATIBILITY_MAP
    native = [k for k, v in COMPATIBILITY_MAP.items() if v.get("ttlg") == k]
    for ct in native:
        assert ct in iv.INTENTS, f"missing intent for {ct}"


def test_intent_shape_and_language():
    i = iv.INTENTS["TTLGTextToImage"]
    assert i.verb and i.noun and i.icon
    assert "image" in iv.label("TTLGTextToImage").lower()
    assert "TTLG" not in iv.label("TTLGTextToImage")   # no tool names in the label
    assert "image_path" in i.outputs                    # matches the engine output-key contract


def test_unknown_is_generic_not_crash():
    assert iv.intent_for("TTLGNope").class_type == "TTLGNope"


def test_unknown_label_never_leaks_raw_class_type():
    """The generic fallback label must stay tool-agnostic — a passthrough/
    non-native node's class_type (e.g. an imported ComfyUI "CLIPTextEncode")
    must never surface in the user-facing label() string, even though it's
    still available on Intent.class_type for lookups."""
    lbl = iv.label("CLIPTextEncode")
    assert "CLIPTextEncode" not in lbl
    assert "this step" in lbl
    # class_type is still preserved on the Intent itself for lookups.
    assert iv.intent_for("CLIPTextEncode").class_type == "CLIPTextEncode"


def test_image_to_video_label_has_motion_word_no_model_name():
    lbl = iv.label("TTLGImageToVideo").lower()
    assert ("film" in lbl) or ("animate" in lbl)
    # no model names leaking into the label
    for banned in ("skyreels", "flux", "llama", "wan"):
        assert banned not in lbl


# ── I/O wiring metadata (SP-C Phase 2b-1 Task 1) ──────────────────────────────


def test_representative_wiring_metadata():
    i = iv.INTENTS["TTLGCaptionImage"]
    assert (i.input_key, i.input_kind, i.output_kind) == ("src", "image", "text")

    i = iv.INTENTS["TTLGImageToVideo"]
    assert (i.input_key, i.input_kind, i.output_kind) == ("image", "image", "video")

    i = iv.INTENTS["TTLGTextToImage"]
    assert (i.input_key, i.input_kind, i.output_kind) == ("prompt", "text", "image")

    i = iv.INTENTS["TTLGRemoveBackground"]
    assert (i.input_key, i.input_kind, i.output_kind) == ("src", "image", "image")

    i = iv.INTENTS["TTLGGenerateText"]
    assert (i.input_key, i.input_kind, i.output_kind) == ("caption", "text", "text")

    i = iv.INTENTS["TTLGEstimateDepth"]
    assert (i.input_key, i.input_kind, i.output_kind) == ("src", "image", "image")

    i = iv.INTENTS["TTLGPromptCompose"]
    assert (i.input_key, i.input_kind, i.output_kind) == ("caption", "text", "text")

    i = iv.INTENTS["TTLGSVGRender"]
    assert (i.input_key, i.input_kind, i.output_kind) == ("src", "text", "image")

    i = iv.INTENTS["TTLGComposite"]
    assert (i.input_key, i.input_kind, i.output_kind) == (
        "background_path",
        "image",
        "image",
    )

    i = iv.INTENTS["TTLGAddToPlaylist"]
    assert (i.input_key, i.input_kind, i.output_kind) == (None, None, "playlist")

    i = iv.INTENTS["TTLGArtgenGenerate"]
    assert (i.input_key, i.input_kind, i.output_kind) == (None, None, "text")

    i = iv.INTENTS["TTLGAnimateDiff"]
    assert (i.input_key, i.input_kind, i.output_kind) == ("prompt", "text", "gif")


def test_every_intent_has_wiring_fields_present():
    """All three fields must exist on every one of the 12 native Intents
    (None is a valid value — e.g. collector/plugin-driven nodes with no
    single upstream artifact input — but the attribute must be present)."""
    for class_type, i in iv.INTENTS.items():
        assert hasattr(i, "input_key"), class_type
        assert hasattr(i, "input_kind"), class_type
        assert hasattr(i, "output_kind"), class_type


def test_generic_fallback_has_none_wiring_fields():
    i = iv.intent_for("TTLGNope")
    assert i.input_key is None
    assert i.input_kind is None
    assert i.output_kind is None


def test_compatible_intents_image_output():
    compat = {i.class_type for i in iv.compatible_intents("image")}
    assert {
        "TTLGCaptionImage",
        "TTLGRemoveBackground",
        "TTLGEstimateDepth",
        "TTLGImageToVideo",
    } <= compat
    # text-input intents must not be able to consume an image artifact
    assert "TTLGTextToImage" not in compat
    assert "TTLGGenerateText" not in compat


def test_compatible_intents_text_output():
    compat = {i.class_type for i in iv.compatible_intents("text")}
    assert {
        "TTLGTextToImage",
        "TTLGGenerateText",
        "TTLGPromptCompose",
        "TTLGAnimateDiff",
    } <= compat
    # image-input intents must not be able to consume a text artifact
    assert "TTLGCaptionImage" not in compat
    assert "TTLGRemoveBackground" not in compat
    assert "TTLGEstimateDepth" not in compat


def test_compatible_intents_deterministic_order():
    order1 = [i.class_type for i in iv.compatible_intents("image")]
    order2 = [i.class_type for i in iv.compatible_intents("image")]
    assert order1 == order2
