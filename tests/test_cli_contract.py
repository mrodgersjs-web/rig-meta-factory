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
