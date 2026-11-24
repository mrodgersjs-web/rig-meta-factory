"""Step 11: BAMA stdio MCP server (line-delimited JSON-RPC 2.0)."""
from __future__ import annotations

import json

from conftest import run_subprocess


def test_mcp_stdio_contract(tmp_path):
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "rmf_lead_status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "rmf_owner_show", "arguments": {"factory": "nope"}}},
    ]
    cp = run_subprocess("mcp", input_text="\n".join(json.dumps(r) for r in reqs) + "\n")
    lines = [json.loads(x) for x in cp.stdout.splitlines()]
    assert [x["id"] for x in lines] == [1, 2, 3, 4]  # the notification gets no reply
    assert "tools" in lines[0]["result"]["capabilities"]
    schema = json.loads(run_subprocess("schema", "--json").stdout)
    names = {t["name"] for t in lines[1]["result"]["tools"]}
    assert names == {c["mcp_name"] for c in schema["commands"]}
    assert all("readOnlyHint" in t["annotations"] for t in lines[1]["result"]["tools"])
    assert lines[2]["result"]["isError"] is False
    assert lines[3]["result"]["isError"] is True
