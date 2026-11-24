"""Tests for Step 1: CLI shell and BAMA contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run_rmf(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["RMF_DB_PATH"] = str(tmp_path / "test-registry.db")
    return subprocess.run(
        [sys.executable, "-m", "rig_meta_factory.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_status_json_contract(tmp_path: Path) -> None:
    res = _run_rmf(tmp_path, "status", "--json")
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert data["ok"] is True
    assert data["version"] == "0.1.0"
    assert data["db_path"] == str(tmp_path / "test-registry.db")
    assert isinstance(data["schema_version"], int)


def test_schema_and_help_json_contract(tmp_path: Path) -> None:
    res = _run_rmf(tmp_path, "schema", "--json")
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert isinstance(data["commands"], list)
    assert len(data["commands"]) >= 2
    for cmd in data["commands"]:
        assert "name" in cmd
        assert isinstance(cmd["read_only"], bool)
        assert isinstance(cmd["readOnly"], bool)
        assert isinstance(cmd["required"], list)
        assert "Example:" in cmd["description"]
        assert cmd["inputSchema"]["type"] == "object"

    help_res = _run_rmf(tmp_path, "help", "--json")
    assert help_res.returncode == 0
    assert json.loads(help_res.stdout)["commands"] == data["commands"]

    human_help = _run_rmf(tmp_path, "help")
    assert human_help.returncode == 0
    assert "rmf 0.1.0" in human_help.stdout


def test_unknown_command_exits_2_with_json_error(tmp_path: Path) -> None:
    res = _run_rmf(tmp_path, "nonexistent", "--json")
    assert res.returncode == 2
    data = json.loads(res.stdout)
    assert data["error"]["code"] == "USAGE_ERROR"
    assert data["error"]["exit_code"] == 2
    assert len(data["error"]["message"]) > 0

    plain = _run_rmf(tmp_path, "nonexistent")
    assert plain.returncode == 2


def test_per_command_help_and_noun_help(tmp_path: Path) -> None:
    """`rmf <noun> <verb> --help` and `rmf help <noun> [verb]` answer; unknown words exit 2."""
    text = _run_rmf(tmp_path, "factory", "create", "--help")
    assert text.returncode == 0
    assert text.stdout.startswith("rmf factory create")
    assert "--dest" in text.stdout and "--template" in text.stdout

    as_json = _run_rmf(tmp_path, "factory", "create", "--help", "--json")
    assert as_json.returncode == 0
    data = json.loads(as_json.stdout)
    assert data["help"] == "command"
    assert data["command"]["name"] == "factory create"
    flags = {f["name"]: f for f in data["flags"]}
    assert flags["dest"]["flag"] == "--dest"
    assert flags["slug"]["required"] is True
    assert all("_" not in f["flag"] for f in flags.values())

    noun = _run_rmf(tmp_path, "help", "factory")
    assert noun.returncode == 0
    for verb in ("create", "list", "show", "update", "delete"):
        assert f"factory {verb}" in noun.stdout

    noun_json = _run_rmf(tmp_path, "help", "factory", "--json")
    assert noun_json.returncode == 0
    data = json.loads(noun_json.stdout)
    assert data["help"] == "noun" and data["noun"] == "factory"
    assert {c["name"] for c in data["commands"]} == {
        "factory create", "factory delete", "factory list", "factory show", "factory update"
    }

    one = _run_rmf(tmp_path, "help", "tac", "plan", "--json")
    assert json.loads(one.stdout)["command"]["name"] == "tac plan"
    mcp = _run_rmf(tmp_path, "help", "mcp")
    assert mcp.returncode == 0 and "stdio JSON-RPC 2.0 MCP server" in mcp.stdout

    unknown = _run_rmf(tmp_path, "help", "nonsense", "--json")
    assert unknown.returncode == 2
    assert json.loads(unknown.stdout)["error"]["code"] == "USAGE_ERROR"
    trailing = _run_rmf(tmp_path, "help", "factory", "create", "extra", "--json")
    assert trailing.returncode == 2


def test_no_machine_specific_paths_in_shipped_code():
    """The repo is public: no absolute path to one machine may ship in code or examples."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    targets = [*(root / "src" / "rig_meta_factory").glob("*.py"), root / ".env.example"]
    offenders = sorted(str(p.relative_to(root)) for p in targets if "/Users/" in p.read_text())
    assert offenders == []
