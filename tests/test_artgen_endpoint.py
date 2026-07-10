"""Tests for artgen LLM endpoint discovery (detect_artgen_endpoint).

The app hardcodes ports for servers *it* starts (artgen=8002, prompt-server=8001),
but a model started outside the app can land on any port. Discovery must find
whatever OpenAI-compatible chat server is actually up and prefer a real model
over the tiny prompt-gen fallback (Qwen3-0.6B on 8001).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import artgen  # noqa: E402


@pytest.fixture
def fake_servers(monkeypatch):
    """Patch detect_model so a fixed {base_url: model_id} map defines what's 'up'.

    Returns the mutable dict so each test can declare its own topology.
    """
    servers: dict[str, str] = {}

    def _fake_detect_model(base_url, *args, **kwargs):
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        return servers.get(base)

    monkeypatch.setattr(artgen, "detect_model", _fake_detect_model)
    return servers


def test_external_llama_beats_tiny_prompt_server(fake_servers):
    """The bug: a 70B Llama on a non-standard port (8003) must win over the
    0.6B prompt-gen fallback on 8001 — not be silently ignored."""
    fake_servers["http://localhost:8001"] = "Qwen/Qwen3-0.6B"
    fake_servers["http://localhost:8003"] = "meta-llama/Llama-3.3-70B-Instruct"
    # 8002 (dedicated artgen) is NOT running.

    base_url, model_id = artgen.detect_artgen_endpoint()

    assert base_url == "http://localhost:8003"
    assert model_id == "meta-llama/Llama-3.3-70B-Instruct"


def test_dedicated_artgen_port_still_preferred(fake_servers):
    """When the app's own artgen server (8002) is up, it wins over a scan hit."""
    fake_servers["http://localhost:8002"] = "Qwen3-8B"
    fake_servers["http://localhost:8003"] = "meta-llama/Llama-3.3-70B-Instruct"

    base_url, model_id = artgen.detect_artgen_endpoint()

    assert base_url == "http://localhost:8002"
    assert model_id == "Qwen3-8B"


def test_prompt_server_is_last_resort(fake_servers):
    """With only the tiny prompt server up, it's still returned (day-one value)."""
    fake_servers["http://localhost:8001"] = "Qwen/Qwen3-0.6B"

    base_url, model_id = artgen.detect_artgen_endpoint()

    assert base_url == "http://localhost:8001"
    assert model_id == "Qwen/Qwen3-0.6B"


def test_preferred_url_wins(fake_servers):
    """An explicit override is probed first."""
    fake_servers["http://localhost:8002"] = "Qwen3-8B"
    fake_servers["http://myremote:9000"] = "custom-model"

    base_url, model_id = artgen.detect_artgen_endpoint(
        preferred_url="http://myremote:9000"
    )

    assert base_url == "http://myremote:9000"
    assert model_id == "custom-model"


def test_nothing_running_returns_none(fake_servers):
    base_url, model_id = artgen.detect_artgen_endpoint()
    assert base_url is None
    assert model_id is None


def test_diffusion_port_not_used_for_chat(fake_servers):
    """Port 8000 hosts the diffusion media server (video/image), never a chat
    model. Even if something answers there it must not be picked up as an artgen
    chat endpoint over a real chat server."""
    fake_servers["http://localhost:8000"] = "wan2.2-should-not-be-chat"
    fake_servers["http://localhost:8003"] = "meta-llama/Llama-3.3-70B-Instruct"

    base_url, model_id = artgen.detect_artgen_endpoint()

    assert base_url == "http://localhost:8003"
