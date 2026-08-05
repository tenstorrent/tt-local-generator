import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import prompt_client


def test_polish_returns_none_when_llm_unavailable(monkeypatch):
    import generate_prompt as gp
    monkeypatch.setattr(gp, "_llm_available", lambda: False)
    assert prompt_client.llm_polish_or_none("video", "palette: #fff, calm") is None


def test_polish_returns_text_when_available(monkeypatch):
    import generate_prompt as gp
    monkeypatch.setattr(gp, "_llm_available", lambda: True)
    monkeypatch.setattr(gp, "_llm_polish", lambda seed, source: "a calm white dawn, drifting")
    assert prompt_client.llm_polish_or_none("video", "palette: #fff, calm") == \
        "a calm white dawn, drifting"


def test_polish_none_when_polish_empty(monkeypatch):
    import generate_prompt as gp
    monkeypatch.setattr(gp, "_llm_available", lambda: True)
    monkeypatch.setattr(gp, "_llm_polish", lambda seed, source: "")
    assert prompt_client.llm_polish_or_none("video", "x") is None
