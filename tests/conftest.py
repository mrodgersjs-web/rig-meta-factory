"""Shared pytest fixtures: isolated DB, CLI runner, git identity."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "registry.db"
    monkeypatch.setenv("RMF_DB_PATH", str(db))
    monkeypatch.delenv("APP_STORE", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "RIG Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@rig.local")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "RIG Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@rig.local")
    return db


class Rmf:
    """In-process CLI runner returning (exit_code, parsed_json)."""

    def __init__(self, capsys: pytest.CaptureFixture[str]) -> None:
        self.capsys = capsys

    def __call__(self, *args: str) -> tuple[int, dict[str, Any]]:
        from rig_meta_factory.cli import main

        self.capsys.readouterr()
        code = main([*args, "--json"])
        out = self.capsys.readouterr().out
        return code, json.loads(out)


@pytest.fixture
def rmf(capsys: pytest.CaptureFixture[str]) -> Rmf:
    return Rmf(capsys)


def run_subprocess(*args: str, env: dict[str, str] | None = None, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, "-m", "rig_meta_factory.cli", *args],
        capture_output=True,
        text=True,
        env=e,
        input=input_text,
        check=False,
    )
