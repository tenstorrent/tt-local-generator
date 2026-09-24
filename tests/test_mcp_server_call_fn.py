"""Tests that mcp_server._make_call_fn stamps a model_tier onto its closure."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed — skipping MCP server tests")


def test_make_call_fn_stamps_tier_from_detected_model(monkeypatch):
    import mcp_server
    import artgen as artgen_module

    monkeypatch.setattr(
        artgen_module, "detect_artgen_endpoint",
        lambda preferred_url=None: ("http://localhost:20000", "Qwen/Qwen3.8-27B"),
    )
    fn = mcp_server._make_call_fn()
    assert fn.model_tier == "small"


def test_make_call_fn_defaults_to_large_when_no_endpoint_detected(monkeypatch):
    import mcp_server
    import artgen as artgen_module

    monkeypatch.setattr(
        artgen_module, "detect_artgen_endpoint",
        lambda preferred_url=None: (None, None),
    )
    fn = mcp_server._make_call_fn()
    assert fn.model_tier == "large"


def test_make_call_fn_still_raises_inside_call_fn_when_no_endpoint(monkeypatch):
    # Regression: the upfront detection added for tier-stamping must not
    # swallow the existing "no server reachable" error the real call still
    # raises when actually invoked.
    import mcp_server
    import artgen as artgen_module

    monkeypatch.setattr(
        artgen_module, "detect_artgen_endpoint",
        lambda preferred_url=None: (None, None),
    )
    fn = mcp_server._make_call_fn()
    with pytest.raises(RuntimeError, match="No LLM server reachable"):
        fn("a prompt")
