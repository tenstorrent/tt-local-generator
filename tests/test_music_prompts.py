"""Music prompt helpers and the music chip bank (Task 3). No real LLM, no docker."""
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import pytest

import prompt_client
from chip_config import load_chips, load_chips_for_artgen


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def fake_llm(monkeypatch):
    """Replace urlopen; record every (url, body) and answer with canned lyrics."""
    calls = []

    def _urlopen(req, timeout=None):
        body = json.loads(req.data.decode())
        calls.append((req.full_url, body))
        text = "[Verse]\nline one\n\n[Chorus]\nline two"
        return _Resp(json.dumps({"choices": [{"message": {"content": text}}]}).encode())

    monkeypatch.setattr(prompt_client.urllib.request, "urlopen", _urlopen)
    return calls


def _detect(monkeypatch, result):
    import artgen
    monkeypatch.setattr(artgen, "detect_artgen_endpoint", lambda *a, **k: result)


def _names(cats):
    return [c.name for c in cats]


def test_music_bank_has_expected_categories():
    names = _names(load_chips_for_artgen("music"))
    for expected in ("Genre", "Mood", "Instruments", "Tempo", "Era", "In the style of"):
        assert expected in names
    assert any(c.surprise for cat in load_chips_for_artgen("music") for c in cat.chips)


def test_style_chip_takes_free_text():
    cat = next(c for c in load_chips_for_artgen("music") if c.name == "In the style of")
    chip = cat.chips[0]
    assert chip.freetext and "{}" in chip.text
    assert chip.text.format("Nina Simone") == "in the style of Nina Simone"


def test_music_bank_not_offered_to_native_tabs():
    music = {"Genre", "Instruments", "Tempo", "In the style of"}
    for tab in ("video", "image", "animate"):
        assert not music & set(_names(load_chips(tab)))
    # Mood and Era exist for palette; they must not carry music chips on native tabs
    for tab in ("video", "image", "animate"):
        for cat in load_chips(tab):
            assert not any("bpm" in c.text.lower() for c in cat.chips)


def test_lyrics_polish_returns_none_without_llm(monkeypatch, fake_llm):
    _detect(monkeypatch, (None, None))
    assert prompt_client.llm_polish_or_none("lyrics", "a song about rain") is None
    assert fake_llm == []


def test_lyrics_prompt_asks_for_structure_tags(monkeypatch, fake_llm):
    _detect(monkeypatch, ("http://127.0.0.1:8002", "Qwen/Qwen3-8B"))
    out = prompt_client.llm_polish_or_none("lyrics", "a song about rain")
    assert out and "[Verse]" in out and "[Chorus]" in out
    system = fake_llm[0][1]["messages"][0]["content"]
    assert "[Verse]" in system and "[Chorus]" in system


def test_lyrics_prefers_artgen_endpoint_over_prompt_server(monkeypatch, fake_llm):
    _detect(monkeypatch, ("http://127.0.0.1:20000", "Qwen/Qwen3.8-27B"))
    prompt_client.generate_prompt("lyrics", "rain")
    url, body = fake_llm[0]
    assert url.startswith("http://127.0.0.1:20000") and "8001" not in url
    assert body["model"] == "Qwen/Qwen3.8-27B"


def test_music_llm_available(monkeypatch):
    _detect(monkeypatch, ("http://127.0.0.1:8001", "Qwen/Qwen3-0.6B"))
    assert prompt_client.music_llm_available() is True
    _detect(monkeypatch, (None, None))
    assert prompt_client.music_llm_available() is False


def test_music_prompt_without_llm_is_nonempty(monkeypatch, fake_llm):
    _detect(monkeypatch, (None, None))
    assert prompt_client.generate_prompt("music").strip()
