"""Factory CRUD: stamp from a template revision, git init, optional SSSF, state transitions, Gate-D delete."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from rig_meta_factory import gate_d, sssf
from rig_meta_factory.cli import CommandSpec, FlagSpec
from rig_meta_factory.db import connect, init_db, new_id, utc_now_iso
from rig_meta_factory.errors import NotFoundRmfError, RuntimeRmfError, UsageRmfError
from rig_meta_factory.templates import get_revision, get_template_row, validate_slug

STATES = ("empty_stamped", "running", "graduated", "archived")
TRANSITIONS = {
    "empty_stamped": {"running"},
    "running": {"graduated"},
    "graduated": {"archived"},
    "archived": set(),
}


def _conn() -> sqlite3.Connection:
    init_db()
    return connect()


def get_factory_row(conn: sqlite3.Connection, slug: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM factories WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise NotFoundRmfError(f"Factory not found: {slug}")
    return row


def run_git(args: list[str], cwd: Path | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False)


def _git_or_raise(args: list[str], cwd: Path | str) -> str:
    proc = run_git(args, cwd)
    if proc.returncode != 0:
        raise RuntimeRmfError(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    return proc.stdout.strip()


def stamp_template_files(template_root: Path, manifest: dict[str, str], dest: Path) -> None:
    for rel in manifest:
        src = template_root / rel
        if not src.is_file():
            raise RuntimeRmfError(f"Template file missing on disk: {rel}")
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)


def create_factory(
    slug: str, name: str, template_slug: str, dest: str, install_sssf: bool = False
) -> dict[str, Any]:
    validate_slug(slug, "factory slug")
    if not name.strip():
        raise UsageRmfError("name must be non-empty")
    dest_path = Path(dest).expanduser().resolve()
    conn = _conn()
    try:
        tpl = get_template_row(conn, template_slug)
        if tpl["current_revision_id"] is None:
            raise UsageRmfError(f"Template {template_slug} has no revision")
        rev = get_revision(conn, tpl["current_revision_id"])
        if conn.execute("SELECT 1 FROM factories WHERE slug = ? OR repo_path = ?", (slug, str(dest_path))).fetchone():
            raise UsageRmfError(f"Factory slug or path already registered: {slug}", code="DUPLICATE_SLUG")
        if dest_path.exists() and any(dest_path.iterdir()):
            raise UsageRmfError(f"Destination exists and is not empty: {dest_path}")
        dest_path.mkdir(parents=True, exist_ok=True)
        stamp_template_files(Path(tpl["root_path"]), rev["manifest"], dest_path)
        _git_or_raise(["init", "-b", "main"], dest_path)
        _git_or_raise(["add", "-A"], dest_path)
        _git_or_raise(
            ["-c", "user.email=rmf@rig.local", "-c", "user.name=rmf", "commit", "-m", f"stamp: {template_slug}@v{rev['version']}", "--no-verify"],
            dest_path,
        )
        sssf_info: dict[str, Any] | None = None
        if install_sssf:
            sssf_info = sssf.stamp_sssf(dest_path)
        fid, now = new_id("fac"), utc_now_iso()
        with conn:
            conn.execute(
                "INSERT INTO factories (id, slug, name, repo_path, template_id, template_revision_id, state,"
                " sssf_installed, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'empty_stamped', ?, ?, ?)",
                (fid, slug, name, str(dest_path), tpl["id"], rev["id"], 1 if install_sssf else 0, now, now),
            )
        return {
            "factory": dict(conn.execute("SELECT * FROM factories WHERE id = ?", (fid,)).fetchone()),
            "template_revision": {k: v for k, v in rev.items() if k != "manifest"},
            "sssf_installed": bool(install_sssf),
            "sssf": sssf_info,
        }
    finally:
        conn.close()


def list_factories(state: str | None = None) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        if state:
            rows = conn.execute("SELECT * FROM factories WHERE state = ? ORDER BY slug", (state,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM factories ORDER BY slug").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def show_factory(slug: str) -> dict[str, Any]:
    conn = _conn()
    try:
        fac = get_factory_row(conn, slug)
        rev = conn.execute(
            "SELECT id, template_id, version, manifest_sha256, notes, source_backport_id, created_at "
            "FROM template_revisions WHERE id = ?",
            (fac["template_revision_id"],),
        ).fetchone()
        owner = conn.execute("SELECT * FROM owner_agents WHERE factory_id = ?", (fac["id"],)).fetchone()
        latest = conn.execute(
            "SELECT t.current_revision_id, r.version FROM templates t JOIN template_revisions r "
            "ON r.id = t.current_revision_id WHERE t.id = ?",
            (fac["template_id"],),
        ).fetchone()
        return {
            "factory": dict(fac),
            "template_revision": dict(rev) if rev else None,
            "owner": dict(owner) if owner else None,
            "behind_template": bool(latest and latest["current_revision_id"] != fac["template_revision_id"]),
            "latest_template_version": latest["version"] if latest else None,
        }
    finally:
        conn.close()


def update_factory(slug: str, state: str) -> dict[str, Any]:
    if state not in STATES:
        raise UsageRmfError(f"Invalid state '{state}': one of {', '.join(STATES)}")
    conn = _conn()
    try:
        fac = get_factory_row(conn, slug)
        if state not in TRANSITIONS[fac["state"]]:
            raise UsageRmfError(
                f"Invalid transition {fac['state']} -> {state}", code="INVALID_TRANSITION"
            )
        with conn:
            conn.execute(
                "UPDATE factories SET state = ?, updated_at = ? WHERE id = ?", (state, utc_now_iso(), fac["id"])
            )
        return {"factory": dict(conn.execute("SELECT * FROM factories WHERE id = ?", (fac["id"],)).fetchone())}
    finally:
        conn.close()


def delete_payload(slug: str, remove_files: bool) -> dict[str, Any]:
    return {"action": "factory_delete", "slug": slug, "remove_files": bool(remove_files)}


def delete_factory(
    slug: str, remove_files: bool = False, yes: bool = False, approval_id: str | None = None
) -> dict[str, Any]:
    conn = _conn()
    try:
        fac = get_factory_row(conn, slug)
        payload = delete_payload(slug, remove_files)
        sha = gate_d.canonical_payload_sha256(payload)
        if not yes:
            return {
                "dry_run": True,
                "action_type": "factory_delete",
                "target_ref": slug,
                "payload": payload,
                "payload_sha256": sha,
                "approval_required": True,
                "would_affect": {"factory": slug, "repo_path": fac["repo_path"], "remove_files": bool(remove_files)},
            }
        with conn:  # approval consumption and row delete are one transaction
            gate_d.require_and_consume_approval(conn, approval_id, "factory_delete", slug, sha)
            conn.execute("DELETE FROM factories WHERE id = ?", (fac["id"],))
        removed = False
        if remove_files:
            repo = Path(fac["repo_path"])
            if repo.is_dir():
                shutil.rmtree(repo)
                removed = True
        return {"dry_run": False, "deleted": slug, "files_removed": removed, "payload_sha256": sha}
    finally:
        conn.close()


def _h_create(p: dict[str, Any]) -> dict[str, Any]:
    return create_factory(p["slug"], p["name"], p["template"], p["dest"], bool(p.get("install_sssf")))


def _h_list(p: dict[str, Any]) -> dict[str, Any]:
    return {"factories": list_factories(p.get("state"))}


def _h_show(p: dict[str, Any]) -> dict[str, Any]:
    return show_factory(p["slug"])


def _h_update(p: dict[str, Any]) -> dict[str, Any]:
    return update_factory(p["slug"], p["state"])


def _h_delete(p: dict[str, Any]) -> dict[str, Any]:
    return delete_factory(p["slug"], bool(p.get("remove_files")), bool(p.get("yes")), p.get("approval_id"))


def command_specs() -> list[CommandSpec]:
    slug = FlagSpec("slug", required=True, description="Factory slug")
    return [
        CommandSpec(
            "factory create",
            "Stamp a new factory from a template's current revision (optionally installing SSSF).",
            "rmf factory create --slug f1 --name 'Factory One' --template base --dest ./f1 --json",
            False,
            _h_create,
            (
                slug,
                FlagSpec("name", required=True, description="Factory display name"),
                FlagSpec("template", required=True, description="Template slug"),
                FlagSpec("dest", required=True, description="Destination directory for the factory repo"),
                FlagSpec("install_sssf", type="boolean", description="Copy and install the SSSF skill"),
            ),
        ),
        CommandSpec(
            "factory list",
            "List all factories, optionally filtered by state.",
            "rmf factory list --state running --json",
            True,
            _h_list,
            (FlagSpec("state", description="Filter by state", choices=STATES),),
        ),
        CommandSpec("factory show", "Show a factory, its owner agent, and template revision.", "rmf factory show --slug f1 --json", True, _h_show, (slug,)),
        CommandSpec(
            "factory update",
            "Transition factory state (empty_stamped -> running -> graduated -> archived).",
            "rmf factory update --slug f1 --state running --json",
            False,
            _h_update,
            (slug, FlagSpec("state", required=True, description="New state", choices=STATES)),
        ),
        CommandSpec(
            "factory delete",
            "Delete a factory record (Gate-D: dry-run without --yes; --yes needs an approved --approval-id).",
            "rmf factory delete --slug f1 --remove-files --yes --approval-id gate_abc --json",
            False,
            _h_delete,
            (
                slug,
                FlagSpec("remove_files", type="boolean", description="Also remove the factory directory"),
                FlagSpec("yes", type="boolean", description="Execute (requires approved Gate-D item)"),
                FlagSpec("approval_id", description="Approved Gate-D approval id"),
            ),
            destructive=True,
        ),
    ]
