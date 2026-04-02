"""Tests for prompt_client — no real server or LLM needed."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import requests

import prompt_client


# ── check_health ──────────────────────────────────────────────────────────────

def test_check_health_true_when_model_ready():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok", "model_ready": True}
    with patch("prompt_client.requests.get", return_value=mock_resp):
        assert prompt_client.check_health() is True


def test_check_health_false_when_model_not_ready():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok", "model_ready": False}
    with patch("prompt_client.requests.get", return_value=mock_resp):
        assert prompt_client.check_health() is False


def test_check_health_false_on_network_error():
    with patch("prompt_client.requests.get", side_effect=requests.ConnectionError()):
        assert prompt_client.check_health() is False


def test_check_health_false_on_non_200():
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    with patch("prompt_client.requests.get", return_value=mock_resp):
        assert prompt_client.check_health() is False


def test_check_health_false_on_json_decode_error():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.side_effect = ValueError("No JSON object could be decoded")
    with patch("prompt_client.requests.get", return_value=mock_resp):
        assert prompt_client.check_health() is False


# ── generate_prompt — no seed (attractor / empty inspire box) ─────────────────

def test_generate_prompt_uses_three_tier_when_no_seed():
    """With no seed, delegates to generate_prompt.generate() and returns the prompt."""
    expected = "A red fox trots through deep snow at midnight"
    with patch("generate_prompt.generate", return_value={"prompt": expected, "source": "algo"}):
        result = prompt_client.generate_prompt("video", "")
    assert result == expected


def test_generate_prompt_uses_llm_polish_when_available():
    """With no seed and LLM up, generate() internally uses LLM polish (handled by gp.generate)."""
    polished = "A silver fox pads through a silent birch forest, moonlight on snow"
    with patch("generate_prompt.generate", return_value={"prompt": polished, "source": "llm"}):
        result = prompt_client.generate_prompt("video")
    assert result == polished


def test_generate_prompt_falls_back_to_algo_when_llm_down():
    """generate_prompt.generate() returns algo result; prompt_client returns it unchanged."""
    algo_result = "frost-covered field, winter dawn, static wide, melancholy"
    with patch("generate_prompt.generate", return_value={"prompt": algo_result, "source": "algo"}):
        result = prompt_client.generate_prompt("image", "")
    assert result == algo_result


def test_generate_prompt_accepts_all_source_types():
    """All three source types pass through correctly."""
    for src in ("video", "image", "animate"):
        with patch("generate_prompt.generate", return_value={"prompt": "ok", "source": "algo"}) as m:
            prompt_client.generate_prompt(src)
            m.assert_called_once_with(prompt_type=src, mode="markov", enhance=True)


# ── generate_prompt — with seed (inspire mode with existing text) ─────────────

def test_generate_prompt_with_seed_polishes_via_llm_when_available():
    """When seed provided and LLM available, returns the polished version."""
    seed = "fox in snow"
    polished = "A red fox trots through deep snow at midnight, breath visible in moonlight"
    with patch("generate_prompt._llm_available", return_value=True), \
         patch("generate_prompt._llm_polish", return_value=polished):
        result = prompt_client.generate_prompt("video", seed)
    assert result == polished


def test_generate_prompt_with_seed_falls_through_to_algo_when_llm_down():
    """When seed provided but LLM offline, falls through to fresh algo generation.

    Returning the seed unchanged is useless — the user already has it.
    """
    seed = "fox in snow"
    algo_result = "a red fox trots through deep snow at midnight"
    with patch("generate_prompt._llm_available", return_value=False), \
         patch("generate_prompt.generate", return_value={"prompt": algo_result, "source": "algo"}) as m:
        result = prompt_client.generate_prompt("video", seed)
    assert result == algo_result
    m.assert_called_once_with(prompt_type="video", mode="markov", enhance=True)


def test_generate_prompt_with_seed_falls_through_to_algo_when_polish_fails():
    """When LLM available but polish returns None, falls through to fresh generation."""
    seed = "fox in snow"
    algo_result = "a red fox trots through deep snow at midnight"
    with patch("generate_prompt._llm_available", return_value=True), \
         patch("generate_prompt._llm_polish", return_value=None), \
         patch("generate_prompt.generate", return_value={"prompt": algo_result, "source": "algo"}) as m:
        result = prompt_client.generate_prompt("video", seed)
    assert result == algo_result
    m.assert_called_once_with(prompt_type="video", mode="markov", enhance=True)


def test_generate_prompt_with_seed_strips_whitespace_for_llm():
    """Seed text is stripped before being passed to the LLM polish function."""
    seed = "  fox in snow  "
    polished = "A red fox trots through deep snow at midnight"
    with patch("generate_prompt._llm_available", return_value=True), \
         patch("generate_prompt._llm_polish", return_value=polished) as m:
        result = prompt_client.generate_prompt("video", seed)
    assert result == polished
    m.assert_called_once_with("fox in snow", "video")


def test_generate_prompt_ignores_system_prompt_arg():
    """system_prompt param is accepted but ignored — generate_prompt.py uses its own."""
    with patch("generate_prompt.generate", return_value={"prompt": "ok", "source": "algo"}):
        result = prompt_client.generate_prompt("video", "", "old system prompt")
    assert result == "ok"
