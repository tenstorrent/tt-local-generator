"""Tests for the MCP server endpoint served by app/mcp_server.py."""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

# FastAPI is available in the venv but not always under /usr/bin/python3.
# Skip the whole module gracefully when it's absent.
fastapi = pytest.importorskip("fastapi", reason="fastapi not installed — skipping MCP server tests")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """FastAPI test client with two fake plugins loaded."""
    from fastapi.testclient import TestClient
    import plugin_loader

    from artgen import ArtGenerator

    class _FakeGen(ArtGenerator):
        name = "fake"
        description = "Fake generator for tests"
        output_ext = ".txt"
        def build_prompt(self, args): return "prompt"

    from plugin_loader import PluginDef
    plugin_loader._PLUGINS.clear()
    plugin_loader._PLUGINS["fake"] = PluginDef(
        path=tmp_path / "fake",
        manifest={
            "x-ttlg": {"output_ext": ".txt", "media_type": "text",
                        "accepts_remix_from": [], "can_remix_to": [],
                        "tab": "generative-art", "hardware": None},
            "tools": [{
                "name": "fake",
                "description": "Fake generator for tests",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
                "examples": [],
                "x-ttlg": {"streaming": None, "artifact_tool": True},
            }],
        },
        name="fake",
        tools=[{
            "name": "fake",
            "description": "Fake generator for tests",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
            "examples": [],
            "x-ttlg": {"streaming": None, "artifact_tool": True},
        }],
        generator=_FakeGen(),
        accepts_remix_from=(),
        can_remix_to=(),
    )

    import importlib
    import mcp_server
    importlib.reload(mcp_server)
    return TestClient(mcp_server.app)


def test_tools_list_returns_all_plugins(client):
    """POST /mcp with tools/list returns all loaded plugins as tools."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    tools = data["result"]["tools"]
    names = [t["name"] for t in tools]
    assert "fake" in names


def test_initialize_returns_server_info(client):
    """POST /mcp with initialize returns server name and protocol version."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2024-11-05", "capabilities": {}}}
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert "serverInfo" in result
    assert result["serverInfo"]["name"] == "tt-local-gen"


def test_tools_call_routes_to_generator(client, monkeypatch):
    """tools/call dispatches to the correct generator's generate_artifact."""
    import plugin_loader
    gen_mock = MagicMock(return_value="generated output")
    plugin_loader._PLUGINS["fake"].generator.generate_artifact = gen_mock

    payload = {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "fake", "arguments": {}}
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    gen_mock.assert_called_once()


def test_tools_call_unknown_tool_returns_error(client):
    """tools/call for unknown tool returns JSON-RPC error."""
    payload = {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "nonexistent", "arguments": {}}
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data


def test_get_mcp_returns_manifest(client):
    """GET /mcp returns the server manifest JSON."""
    resp = client.get("/mcp")
    assert resp.status_code == 200
    data = resp.json()
    assert "tools" in data
