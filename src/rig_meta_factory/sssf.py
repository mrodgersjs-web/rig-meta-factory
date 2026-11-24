"""SSSF (Super Simple Software Factory) stamping: copy skill source and run its installer."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from rig_meta_factory.errors import RuntimeRmfError, UsageRmfError

def bundled_sssf_source() -> Path:
    """SSSF skill shipped inside this repo (editable install or source checkout)."""
    return Path(__file__).resolve().parents[2] / ".claude" / "skills" / "sssf"


def resolve_sssf_source() -> Path:
    """RMF_SSSF_SOURCE, else the SSSF skill bundled with this repo."""
    env = os.environ.get("RMF_SSSF_SOURCE", "").strip()
    if env:
        return Path(env).expanduser()
    return bundled_sssf_source()


def resolve_uv_bin() -> str:
    """RMF_UV_BIN, else first ``uv`` on PATH, else ``~/.local/bin/uv``."""
    env = os.environ.get("RMF_UV_BIN", "").strip()
    if env:
        return env
    return shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")


def sanitized_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    return env


def build_install_command(uv_bin: str | None = None) -> list[str]:
    return [uv_bin or resolve_uv_bin(), "run", "--python", "3.12", ".claude/skills/sssf/scripts/install.py"]


def stamp_sssf(repo_path: Path | str, sssf_source: Path | str | None = None, dry_run: bool = False) -> dict[str, Any]:
    repo = Path(repo_path)
    source = Path(sssf_source) if sssf_source is not None else resolve_sssf_source()
    if not source.is_dir():
        raise UsageRmfError(f"SSSF source not found: {source}", code="SSSF_SOURCE_MISSING")
    dest = repo / ".claude" / "skills" / "sssf"
    cmd = build_install_command()
    if dry_run:
        return {"source": str(source), "dest": str(dest), "command": cmd, "ran": False}
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns("__pycache__", ".venv"))
    if sys.platform == "darwin":
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(dest)], capture_output=True, check=False)
    try:
        proc = subprocess.run(cmd, cwd=str(repo), env=sanitized_env(), capture_output=True, text=True, check=False)
    except OSError as exc:
        raise RuntimeRmfError(f"SSSF installer could not start: {exc}", code="SSSF_INSTALL_FAILED") from exc
    if proc.returncode != 0:
        raise RuntimeRmfError(
            f"SSSF installer exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:500]}",
            code="SSSF_INSTALL_FAILED",
        )
    return {"source": str(source), "dest": str(dest), "command": cmd, "ran": True, "returncode": proc.returncode}
