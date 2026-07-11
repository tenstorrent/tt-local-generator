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


def test_image_to_video_label_has_motion_word_no_model_name():
    lbl = iv.label("TTLGImageToVideo").lower()
    assert ("film" in lbl) or ("animate" in lbl)
    # no model names leaking into the label
    for banned in ("skyreels", "flux", "llama", "wan"):
        assert banned not in lbl
