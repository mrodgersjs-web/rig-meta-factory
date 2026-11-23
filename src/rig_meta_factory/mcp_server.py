"""stdio JSON-RPC 2.0 MCP server exposing every rmf command as a tool."""

from __future__ import annotations

import json
import sys
from typing import Any

from rig_meta_factory import __version__
from rig_meta_factory.cli import get_command_specs, run_command
from rig_meta_factory.errors import RmfError

PROTOCOL = "2024-11-05"


def tools() -> list[dict[str, Any]]:
    return [
        {
            "name": s.mcp_name,
            "description": s.description,
            "inputSchema": {"type": "object", "properties": s.properties, "required": s.required},
            "annotations": {"readOnlyHint": s.read_only, "destructiveHint": s.destructive},
        }
        for s in get_command_specs()
    ]


def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    spec = next((s for s in get_command_specs() if s.mcp_name == name), None)
    if spec is None:
        return {"isError": True, "content": [{"type": "text", "text": json.dumps({"error": {"code": "NOT_FOUND", "message": f"Unknown tool {name}", "exit_code": 4}})}]}
    try:
        out, err = run_command(spec, dict(args or {})), False
    except RmfError as e:
        out, err = e.to_dict(), True
    except Exception as e:  # pragma: no cover
        out, err = {"error": {"code": "RUNTIME_ERROR", "message": str(e), "exit_code": 1}}, True
    return {"isError": err, "content": [{"type": "text", "text": json.dumps(out, sort_keys=True)}], "structuredContent": out}


def handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None  # notification
    if method == "initialize":
        result: Any = {"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                       "capabilities": {"tools": {"listChanged": False}},
                       "serverInfo": {"name": "rmf", "version": __version__}}
    elif method == "tools/list":
        result = {"tools": tools()}
    elif method == "tools/call":
        result = call(params.get("name", ""), params.get("arguments") or {})
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve_mcp() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp: dict[str, Any] | None = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    return 0
