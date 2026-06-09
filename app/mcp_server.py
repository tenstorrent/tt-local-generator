"""
MCP server — exposes all loaded plugins as MCP tools over HTTP (JSON-RPC 2.0).

Protocol: MCP over HTTP (JSON-RPC 2.0) — standard MCP client compatible.
Port: 8003 (configurable via TTLG_MCP_PORT env var).

Start:
    python3 app/mcp_server.py
    python3 app/mcp_server.py --port 8003 --host 0.0.0.0

Claude Code integration:
    tt-ctl mcp-config   # outputs JSON; merge into ~/.claude/mcp.json (don't use >>)

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
    """Load plugins at server startup.

    Using a lifespan handler (rather than module-level load_plugins()) means
    that importlib.reload(mcp_server) in tests does NOT trigger a real plugin
    scan — the scan only runs when the ASGI server actually starts up.
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
        if not pdef.runnable:
            continue  # skip MCP-server stubs not yet delegating
        for tool in pdef.tools:
            tools.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "inputSchema": tool.get(
                    "inputSchema", {"type": "object", "properties": {}}
                ),
            })
    return tools


def _make_call_fn(base_url: str | None = None):
    """Return a call_fn that routes to the best available LLM endpoint.

    Resolution order:
      1. base_url if provided (from TTLG_LLM_URL environment variable)
      2. artgen server on port 8002
      3. prompt-gen server on port 8001
      4. RuntimeError with clear instructions

    This makes MCP tool invocations for art generators fully functional when
    a server is running, without requiring any extra configuration.
    """
    import urllib.request
    import json as _json

    candidates = []
    if base_url:
        candidates.append(base_url)
    candidates += ["http://localhost:8002/v1/chat/completions",
                   "http://localhost:8001/v1/chat/completions"]

    def _call_fn(prompt: str, system: str | None = None,
                 max_tokens: int | None = None) -> str:
        payload = _json.dumps({
            "model": "default",
            "messages": [
                *([] if not system else [{"role": "system", "content": system}]),
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens or 2048,
        }).encode()
        for url in candidates:
            try:
                req = urllib.request.Request(
                    url, data=payload,
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = _json.loads(r.read())
                return data["choices"][0]["message"]["content"]
            except Exception:
                continue
        raise RuntimeError(
            "No LLM server reachable. Start one first: "
            "tt-ctl start artgen-qwen3-8b  or  tt-ctl start prompt-server"
        )

    return _call_fn


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

        # Resolve the plugin by scanning all tool lists, not just primary names.
        # Multi-tool plugins (e.g. midi with generate_midi + stream_midi) are
        # keyed by their primary tool name, so a direct get(tool_name) would
        # miss non-primary tool names like stream_midi.
        pdef = None
        for candidate in plugin_loader.all_plugins():
            if candidate.runnable and any(t["name"] == tool_name for t in candidate.tools):
                pdef = candidate
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

        # Find the matching tool definition so we can validate arguments against
        # its inputSchema before constructing the Namespace.
        tool_def = next(
            (t for t in pdef.tools if t["name"] == tool_name), {}
        )

        # Invoke the generator.  generate_artifact() receives an argparse
        # Namespace built from the MCP arguments dict, plus a call_fn that
        # routes to the best available LLM server (artgen on 8002 → prompt-gen
        # on 8001, or the URL from TTLG_LLM_URL env var).
        # Errors are surfaced as isError=True content (not JSON-RPC errors) so
        # MCP clients receive a structured response rather than a hard failure.
        try:
            import argparse as _ap

            # Validate argument keys against the tool's inputSchema.  Reject
            # unknown keys outright (they would silently land in the Namespace
            # and could shadow plugin attributes or trigger unexpected behaviour).
            # Only validate when the schema actually declares properties; tools
            # with an empty/absent schema accept any arguments (legacy compat).
            allowed_keys = set(
                tool_def.get("inputSchema", {}).get("properties", {}).keys()
            )
            if allowed_keys:
                bad_keys = set(arguments.keys()) - allowed_keys
                if bad_keys:
                    return JSONResponse({
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "result": {
                            "content": [{"type": "text",
                                         "text": f"Unknown argument(s): {sorted(bad_keys)}"}],
                            "isError": True,
                        },
                    })

            # Strip any keys that are not valid Python identifiers as a second
            # safety layer — argparse.Namespace(**kw) raises if a key is not a
            # valid attribute name (e.g. "my-arg" with a hyphen).
            safe_args = {k: v for k, v in arguments.items() if k.isidentifier()}
            args = _ap.Namespace(**safe_args)
            llm_url = os.environ.get("TTLG_LLM_URL")
            call_fn = _make_call_fn(llm_url)
            result = pdef.generator.generate_artifact(args, call_fn)
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
        "--host", default="127.0.0.1",
        help="Bind address (default: %(default)s). Use 0.0.0.0 to expose on LAN.",
    )
    cli_args = parser.parse_args()
    uvicorn.run(app, host=cli_args.host, port=cli_args.port)
