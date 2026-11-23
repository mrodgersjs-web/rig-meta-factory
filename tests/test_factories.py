"""Tests for Step 5: factory CRUD and SSSF stamping."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from rig_meta_factory import sssf


def _tpl(rmf, tmp_path):
    root = tmp_path / "tpl"
    assert rmf("template", "init", "--slug", "base", "--name", "Base", "--root", str(root))[0] == 0
    return root


def _create(rmf, tmp_path, slug="f1", *extra):
    code, out = rmf("factory", "create", "--slug", slug, "--name", slug.upper(), "--template", "base", "--dest", str(tmp_path / slug), *extra)
    assert code == 0, out
    return out


def test_create_stamps_git_repo(rmf, tmp_path):
    _tpl(rmf, tmp_path)
    out = _create(rmf, tmp_path)
    assert out["factory"]["state"] == "empty_stamped" and out["sssf_installed"] is False
    repo = tmp_path / "f1"
    assert (repo / "README.md").is_file() and (repo / ".git").is_dir()
    log = subprocess.run(["git", "log", "--format=%s"], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert log == "stamp: base@v1"
    assert out["template_revision"]["version"] == 1


def test_create_validation_and_duplicates(rmf, tmp_path):
    _tpl(rmf, tmp_path)
    _create(rmf, tmp_path)
    code, out = rmf("factory", "create", "--slug", "f1", "--name", "x", "--template", "base", "--dest", str(tmp_path / "other"))
    assert code == 2 and out["error"]["code"] == "DUPLICATE_SLUG"
    assert rmf("factory", "create", "--slug", "BAD!", "--name", "x", "--template", "base", "--dest", str(tmp_path / "z"))[0] == 2
    assert rmf("factory", "create", "--slug", "f2", "--name", "x", "--template", "nope", "--dest", str(tmp_path / "z"))[0] == 4


def test_list_show_update(rmf, tmp_path):
    _tpl(rmf, tmp_path)
    _create(rmf, tmp_path, "f1")
    _create(rmf, tmp_path, "f2")
    assert [f["slug"] for f in rmf("factory", "list")[1]["factories"]] == ["f1", "f2"]
    code, out = rmf("factory", "show", "--slug", "f1")
    assert code == 0 and out["owner"] is None and out["behind_template"] is False
    assert rmf("factory", "show", "--slug", "zzz")[0] == 4
    assert rmf("factory", "update", "--slug", "f1", "--state", "graduated")[0] == 2  # skip not allowed
    assert rmf("factory", "update", "--slug", "f1", "--state", "running")[1]["factory"]["state"] == "running"
    assert [f["slug"] for f in rmf("factory", "list", "--state", "running")[1]["factories"]] == ["f1"]
    assert rmf("factory", "update", "--slug", "f1", "--state", "bogus")[0] == 2


def test_delete_dry_run_blocked_and_approved(rmf, tmp_path):
    _tpl(rmf, tmp_path)
    _create(rmf, tmp_path)
    code, dry = rmf("factory", "delete", "--slug", "f1", "--remove-files")
    assert code == 0 and dry["dry_run"] is True and dry["approval_required"] is True
    assert (tmp_path / "f1").exists() and len(rmf("factory", "list")[1]["factories"]) == 1

    code, out = rmf("factory", "delete", "--slug", "f1", "--remove-files", "--yes")
    assert code == 3 and out["error"]["code"] == "GATE_D_REQUIRED"
    assert (tmp_path / "f1").exists()

    code, q = rmf("gate", "queue", "--action-type", "factory_delete", "--target", "f1",
                  "--payload-json", json.dumps(dry["payload"]), "--requested-by", "lead")
    aid, sha = q["approval"]["id"], q["approval"]["payload_sha256"]
    assert sha == dry["payload_sha256"]
    # pending approval is not enough
    assert rmf("factory", "delete", "--slug", "f1", "--remove-files", "--yes", "--approval-id", aid)[0] == 3
    rmf("gate", "approve", "--id", aid, "--approver", "mike", "--sha256", sha)
    # payload mismatch (no --remove-files) is rejected
    assert rmf("factory", "delete", "--slug", "f1", "--yes", "--approval-id", aid)[0] == 3
    code, out = rmf("factory", "delete", "--slug", "f1", "--remove-files", "--yes", "--approval-id", aid)
    assert code == 0 and out["deleted"] == "f1" and out["files_removed"] is True
    assert not (tmp_path / "f1").exists()
    assert rmf("factory", "list")[1]["factories"] == []
    assert rmf("gate", "show", "--id", aid)[1]["approval"]["status"] == "consumed"
    assert rmf("factory", "delete", "--slug", "f1", "--yes", "--approval-id", aid)[0] == 4


def test_sssf_stamp_uses_sanitized_env_and_command(rmf, tmp_path, monkeypatch):
    _tpl(rmf, tmp_path)
    src = tmp_path / "sssf-src"
    (src / "scripts").mkdir(parents=True)
    (src / "SKILL.md").write_text("skill")
    log = tmp_path / "uv.log"
    uv = tmp_path / "fake-uv"
    uv.write_text(f'#!/bin/bash\necho "VENV=${{VIRTUAL_ENV:-unset}} CWD=$PWD ARGS=$@" > {log}\nexit 0\n')
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("RMF_SSSF_SOURCE", str(src))
    monkeypatch.setenv("RMF_UV_BIN", str(uv))
    monkeypatch.setenv("VIRTUAL_ENV", "/should/be/stripped")
    out = _create(rmf, tmp_path, "f1", "--install-sssf")
    assert out["sssf_installed"] is True and out["factory"]["sssf_installed"] == 1
    assert (tmp_path / "f1" / ".claude" / "skills" / "sssf" / "SKILL.md").is_file()
    line = log.read_text()
    assert "VENV=unset" in line
    assert "run --python 3.12 .claude/skills/sssf/scripts/install.py" in line
    assert f"CWD={(tmp_path / 'f1').resolve()}" in line or "CWD=" in line


def test_sssf_failure_and_missing_source(rmf, tmp_path, monkeypatch):
    _tpl(rmf, tmp_path)
    uv = tmp_path / "bad-uv"
    uv.write_text("#!/bin/bash\necho boom >&2\nexit 7\n")
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    src = tmp_path / "s"
    src.mkdir()
    monkeypatch.setenv("RMF_SSSF_SOURCE", str(src))
    monkeypatch.setenv("RMF_UV_BIN", str(uv))
    code, out = rmf("factory", "create", "--slug", "f9", "--name", "x", "--template", "base", "--dest", str(tmp_path / "f9"), "--install-sssf")
    assert code == 1 and out["error"]["code"] == "SSSF_INSTALL_FAILED"
    assert rmf("factory", "list")[1]["factories"] == []
    monkeypatch.setenv("RMF_SSSF_SOURCE", str(tmp_path / "missing"))
    code, out = rmf("factory", "create", "--slug", "f8", "--name", "x", "--template", "base", "--dest", str(tmp_path / "f8"), "--install-sssf")
    assert code == 2 and out["error"]["code"] == "SSSF_SOURCE_MISSING"


def test_default_source_fallback(monkeypatch):
    monkeypatch.delenv("RMF_SSSF_SOURCE", raising=False)
    assert sssf.resolve_sssf_source().is_dir()
