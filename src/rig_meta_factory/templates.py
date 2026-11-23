"""Empty factory template store with SHA-256 revision manifests."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from rig_meta_factory.cli import CommandSpec, FlagSpec
from rig_meta_factory.db import connect, init_db, new_id, utc_now_iso
from rig_meta_factory.errors import NotFoundRmfError, UsageRmfError

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
IGNORED_DIRS = {".git"}
IGNORED_NAMES = {".DS_Store"}


def validate_slug(slug: str, what: str = "slug") -> str:
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise UsageRmfError(f"Invalid {what} '{slug}': must match ^[a-z0-9][a-z0-9-]{{1,48}}$")
    return slug


def compute_dir_manifest(root_path: Path | str) -> dict[str, str]:
    """Deterministic map of sorted relative path -> sha256, ignoring .git/."""
    root = Path(root_path)
    manifest: dict[str, str] = {}
    if not root.is_dir():
        return manifest
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in IGNORED_DIRS for part in rel.parts) or path.name in IGNORED_NAMES:
            continue
        if path.is_file() and not path.is_symlink():
            manifest[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(manifest.items()))


def manifest_sha256(manifest: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _conn() -> sqlite3.Connection:
    init_db()
    return connect()


def _rev_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    d["manifest"] = json.loads(d.pop("manifest_json"))
    return d


def get_template_row(conn: sqlite3.Connection, slug: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM templates WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise NotFoundRmfError(f"Template not found: {slug}")
    return row


def get_revision(conn: sqlite3.Connection, revision_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM template_revisions WHERE id = ?", (revision_id,)).fetchone()
    rev = _rev_row(row)
    if rev is None:
        raise NotFoundRmfError(f"Template revision not found: {revision_id}")
    return rev


_STARTER_FILES = {
    "README.md": "# Empty Factory\n\nStarter empty software factory template managed by rig-meta-factory.\n",
    "CLAUDE.md": "# Factory\n\nOwner agent: see `rmf owner show`. Follow the factory mission and changelog.\n",
}


def init_template(slug: str, name: str, root: str) -> dict[str, Any]:
    validate_slug(slug, "template slug")
    if not name.strip():
        raise UsageRmfError("name must be non-empty")
    root_path = Path(root).expanduser().resolve()
    conn = _conn()
    try:
        if conn.execute("SELECT 1 FROM templates WHERE slug = ?", (slug,)).fetchone():
            raise UsageRmfError(f"Template slug already exists: {slug}", code="DUPLICATE_SLUG")
        root_path.mkdir(parents=True, exist_ok=True)
        for rel, content in _STARTER_FILES.items():
            target = root_path / rel
            if not target.exists():
                target.write_text(content, encoding="utf-8")
        meta = root_path / ".rmf-template.json"
        if not meta.exists():
            meta.write_text(json.dumps({"slug": slug, "name": name}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = compute_dir_manifest(root_path)
        now = utc_now_iso()
        tpl_id, rev_id = new_id("tpl"), new_id("rev")
        with conn:
            conn.execute(
                "INSERT INTO templates (id, slug, name, root_path, current_revision_id, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, NULL, ?, ?)",
                (tpl_id, slug, name, str(root_path), now, now),
            )
            conn.execute(
                "INSERT INTO template_revisions (id, template_id, version, manifest_sha256, manifest_json, notes,"
                " source_backport_id, created_at) VALUES (?, ?, 1, ?, ?, 'initial', NULL, ?)",
                (rev_id, tpl_id, manifest_sha256(manifest), json.dumps(manifest, sort_keys=True), now),
            )
            conn.execute("UPDATE templates SET current_revision_id = ? WHERE id = ?", (rev_id, tpl_id))
        return {
            "template": dict(conn.execute("SELECT * FROM templates WHERE id = ?", (tpl_id,)).fetchone()),
            "revision": _rev_row(conn.execute("SELECT * FROM template_revisions WHERE id = ?", (rev_id,)).fetchone()),
        }
    finally:
        conn.close()


def list_templates() -> list[dict[str, Any]]:
    conn = _conn()
    try:
        out = []
        for row in conn.execute("SELECT * FROM templates ORDER BY slug").fetchall():
            d = dict(row)
            rev = conn.execute(
                "SELECT version, manifest_sha256 FROM template_revisions WHERE id = ?", (row["current_revision_id"],)
            ).fetchone()
            d["current_version"] = rev["version"] if rev else None
            d["current_manifest_sha256"] = rev["manifest_sha256"] if rev else None
            out.append(d)
        return out
    finally:
        conn.close()


def show_template(slug: str) -> dict[str, Any]:
    conn = _conn()
    try:
        tpl = get_template_row(conn, slug)
        revs = [
            _rev_row(r)
            for r in conn.execute(
                "SELECT * FROM template_revisions WHERE template_id = ? ORDER BY version", (tpl["id"],)
            ).fetchall()
        ]
        current = next((r for r in revs if r and r["id"] == tpl["current_revision_id"]), None)
        return {"template": dict(tpl), "current_revision": current, "revisions": revs}
    finally:
        conn.close()


def bump_in_conn(
    conn: sqlite3.Connection, tpl: sqlite3.Row, notes: str, source_backport_id: str | None = None
) -> dict[str, Any]:
    """Create revision vN+1 from the template root on disk (caller owns transaction)."""
    manifest = compute_dir_manifest(tpl["root_path"])
    sha = manifest_sha256(manifest)
    current = conn.execute(
        "SELECT * FROM template_revisions WHERE id = ?", (tpl["current_revision_id"],)
    ).fetchone()
    if current is not None and current["manifest_sha256"] == sha:
        raise UsageRmfError("Template files unchanged since current revision", code="NO_CHANGES")
    version = (current["version"] if current else 0) + 1
    rev_id, now = new_id("rev"), utc_now_iso()
    conn.execute(
        "INSERT INTO template_revisions (id, template_id, version, manifest_sha256, manifest_json, notes,"
        " source_backport_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (rev_id, tpl["id"], version, sha, json.dumps(manifest, sort_keys=True), notes, source_backport_id, now),
    )
    conn.execute(
        "UPDATE templates SET current_revision_id = ?, updated_at = ? WHERE id = ?", (rev_id, now, tpl["id"])
    )
    return _rev_row(conn.execute("SELECT * FROM template_revisions WHERE id = ?", (rev_id,)).fetchone())  # type: ignore[return-value]


def bump_template(slug: str, notes: str = "") -> dict[str, Any]:
    conn = _conn()
    try:
        tpl = get_template_row(conn, slug)
        with conn:
            rev = bump_in_conn(conn, tpl, notes)
        return {"revision": rev}
    finally:
        conn.close()


def _h_init(p: dict[str, Any]) -> dict[str, Any]:
    return init_template(p["slug"], p["name"], p["root"])


def _h_list(_p: dict[str, Any]) -> dict[str, Any]:
    return {"templates": list_templates()}


def _h_show(p: dict[str, Any]) -> dict[str, Any]:
    return show_template(p["slug"])


def _h_bump(p: dict[str, Any]) -> dict[str, Any]:
    return bump_template(p["slug"], p.get("notes") or "")


def command_specs() -> list[CommandSpec]:
    return [
        CommandSpec(
            "template init",
            "Initialize an empty factory template directory and revision 1.",
            "rmf template init --slug base --name 'Base Factory' --root ./tpl --json",
            False,
            _h_init,
            (
                FlagSpec("slug", required=True, description="Template slug"),
                FlagSpec("name", required=True, description="Template display name"),
                FlagSpec("root", required=True, description="Template root directory"),
            ),
        ),
        CommandSpec("template list", "List all templates and current revisions.", "rmf template list --json", True, _h_list),
        CommandSpec(
            "template show",
            "Show template metadata and file manifest.",
            "rmf template show --slug base --json",
            True,
            _h_show,
            (FlagSpec("slug", required=True, description="Template slug"),),
        ),
        CommandSpec(
            "template bump",
            "Snapshot updated template files as a new revision.",
            "rmf template bump --slug base --notes 'add file' --json",
            False,
            _h_bump,
            (
                FlagSpec("slug", required=True, description="Template slug"),
                FlagSpec("notes", description="Revision notes"),
            ),
        ),
    ]
