import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pipeline_engine as pe


def test_palette_to_prompt_handler_passes_prompt_through():
    assert "TTLGPaletteToPrompt" in pe.HANDLERS
    out = pe.HANDLERS["TTLGPaletteToPrompt"]("1", {"prompt": "a moody dusk"}, None)
    assert out == {"prompt": "a moody dusk"}


def test_palette_to_prompt_handler_empty_default():
    assert pe.HANDLERS["TTLGPaletteToPrompt"]("1", {}, None) == {"prompt": ""}
