import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import intent_vocab as iv


def test_palette_to_prompt_intent_shape():
    i = iv.intent_for("TTLGPaletteToPrompt")
    assert i.output_kind == "text"
    assert i.outputs == ("prompt",)
    assert i.input_key is None and i.input_kind is None  # source-style node


def test_adapter_for_palette_text():
    assert iv.adapter_for("palette", "text") == "TTLGPaletteToPrompt"


def test_adapter_for_unknown_is_none():
    assert iv.adapter_for("palette", "image") is None
    assert iv.adapter_for("image", "text") is None   # not shipped yet (YAGNI)
    assert iv.adapter_for(None, "text") is None
