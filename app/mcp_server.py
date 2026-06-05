"""
MCP server — exposes all loaded plugins as MCP tools over HTTP (JSON-RPC 2.0).

Protocol: MCP over HTTP (JSON-RPC 2.0) — standard MCP client compatible.
Port: 8003 (configurable via TTLG_MCP_PORT env var).

Start standalone:
    python3 app/mcp_server.py

Claude Code integration:
    tt-ctl mcp-config >> ~/.claude/mcp.json

Endpoints:
    GET  /mcp   — server manifest listing all available tools
    POST /mcp   — JSON-RPC 2.0 dispatch (initialize, tools/list, tools/call)

JSON-RPC methods supported:
    initialize    — returns server name, protocol version, capabilities
    tools/list    — returns all loaded plugins as MCP tool descriptors
    tools/call    — dispatches to the matching plugin's generate_artifact()
"""
from __future__ import annotations

import argparse
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

import plugin_loader


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Load plugins at server startup, unload on shutdown.

    Using a lifespan handler (rather than module-level load_plugins()) means
    that importlib.reload(mcp_server) in tests does NOT trigger a real plugin
    scan — the scan only runs when the ASGI server actually starts up.  Tests
    can safely inject fake plugins into plugin_loader._PLUGINS before creating
    a TestClient without having them overwritten.
    """
    plugin_loader.load_plugins()
    yield


app = FastAPI(title="tt-local-gen MCP server", version="1.0.0", lifespan=_lifespan)

# MCP protocol version this server speaks.
_PROTOCOL_VERSION = "2024-11-05"

# Static server identification block returned in initialize and GET /mcp.
_SERVER_INFO = {
    "name": "tt-local-gen",
    "version": "1.0.0",
}


# ── Internal helpers ──────────────────────────────────────────────────────────


def _all_tools() -> list[dict]:
    """
    Build the MCP tools/list payload from all loaded plugins.

    Each PluginDef carries a list of MCP tool dicts (sourced directly from
    mcp.json).  We flatten them, retaining only the three fields that the MCP
    spec requires clients to understand: name, description, inputSchema.
    """
    tools: list[dict] = []
    for pdef in plugin_loader.all_plugins():
        for tool in pdef.tools:
            tools.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "inputSchema": tool.get(
                    "inputSchema", {"type": "object", "properties": {}}
                ),
            })
    return tools


def _noop_call_fn(prompt: str, system: str | None = None,
                  max_tokens: int | None = None) -> str:
    """
    Placeholder call_fn passed to generate_artifact() via the MCP route.

    Real invocations that need an LLM should wire in the artgen endpoint
    (prompt-server on port 8001, or a remote model server on port 8000).
    Until that wiring is in place, any plugin that tries to call the LLM
    will receive a descriptive RuntimeError rather than a silent hang.
    """
    raise RuntimeError(
        "This plugin requires a live LLM server. "
        "Start the artgen server first (bin/start_artgen.sh) or the prompt server "
        "(bin/start_prompt_gen.sh), then retry the tools/call request."
    )


# ── HTTP routes ───────────────────────────────────────────────────────────────


@app.get("/mcp")
async def get_manifest() -> JSONResponse:
    """
    Return the MCP server manifest.

    The manifest lists all available tools together with server identification.
    MCP clients that perform a GET before connecting via POST can use this to
    discover capabilities without establishing a full JSON-RPC session.
    """
    return JSONResponse({
        "tools": _all_tools(),
        "serverInfo": _SERVER_INFO,
    })


@app.post("/mcp")
async def handle_rpc(body: dict) -> JSONResponse:
    """
    Handle a single JSON-RPC 2.0 MCP message.

    Supported methods:
        initialize  — capability negotiation
        tools/list  — enumerate all loaded plugins as tools
        tools/call  — invoke a plugin's generate_artifact()

    Unknown methods return error code -32601 (Method not found).
    """
    rpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    # ── initialize ────────────────────────────────────────────────────────────
    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": _SERVER_INFO,
            },
        })

    # ── tools/list ────────────────────────────────────────────────────────────
    if method == "tools/list":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {"tools": _all_tools()},
        })

    # ── tools/call ────────────────────────────────────────────────────────────
    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        # Resolve the plugin by searching all tools in all plugins.
        # plugin_loader.get() only looks up by PluginDef.name (the primary
        # artifact tool), so multi-tool plugins would be advertised via
        # tools/list but unreachable here.  Instead we walk every plugin and
        # every tool entry to find the matching name.
        pdef = None
        for _pdef in plugin_loader.all_plugins():
            for _tool in _pdef.tools:
                if _tool.get("name") == tool_name:
                    pdef = _pdef
                    break
            if pdef:
                break
        if pdef is None:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {
                    "code": -32601,
                    "message": f"Tool not found: {tool_name}",
                },
            })

        # Invoke the generator.  generate_artifact() receives an argparse
        # Namespace built from the MCP arguments dict, plus our no-op call_fn.
        # Errors are surfaced as isError=True content (not JSON-RPC errors) so
        # MCP clients receive a structured response rather than a hard failure.
        try:
            import argparse as _ap
            args = _ap.Namespace(**arguments)
            result = pdef.generator.generate_artifact(args, _noop_call_fn)
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "content": [{"type": "text", "text": str(result)}],
                    "isError": False,
                },
            })
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            })

    # ── unknown method ────────────────────────────────────────────────────────
    return JSONResponse({
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {
            "code": -32601,
            "message": f"Method not found: {method}",
        },
    })


# ── Standalone entry point ────────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn

    default_port = int(os.environ.get("TTLG_MCP_PORT", "8003"))
    parser = argparse.ArgumentParser(description="tt-local-gen MCP server")
    parser.add_argument(
        "--port", type=int, default=default_port,
        help="Port to listen on (default: %(default)s, env: TTLG_MCP_PORT)",
    )
    parser.add_argument(
        "--host", default="0.0.0.0",
        help="Bind address (default: %(default)s)",
    )
    cli_args = parser.parse_args()
    uvicorn.run(app, host=cli_args.host, port=cli_args.port)
