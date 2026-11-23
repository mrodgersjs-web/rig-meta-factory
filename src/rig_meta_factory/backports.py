"""Feature backports: capture diffs from a running factory, apply to the template, propagate under Gate-D."""

from __future__ import annotations

import difflib
import hashlib
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from rig_meta_factory import gate_d
from rig_meta_factory.cli import CommandSpec, FlagSpec
from rig_meta_factory.db import connect, init_db, new_id, utc_now_iso
from rig_meta_factory.errors import NotFoundRmfError, RuntimeRmfError, UsageRmfError
from rig_meta_factory.factories import get_factory_row, run_git
from rig_meta_factory.owners import add_changelog_in_conn
from rig_meta_factory.templates import (
    IGNORED_DIRS,
    bump_in_conn,
    compute_dir_manifest,
    get_revision,
    get_template_row,
)

# Files that are factory-local and never backported.
_EXCLUDE_TOP = {".claude"}


def _conn() -> sqlite3.Connection:
    init_db()
    return connect()


def _safe_rel(root: Path, rel: str) -> str:
    """Normalise a relative path and assert it stays inside ``root``."""
    if not rel or Path(rel).is_absolute():
        raise UsageRmfError(f"Invalid relative path: {rel!r}", code="PATH_TRAVERSAL")
    resolved = (root / rel).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise UsageRmfError(f"Path escapes root: {rel!r}", code="PATH_TRAVERSAL")
    return resolved.relative_to(root.resolve()).as_posix()


def _read_lines(path: Path) -> list[str] | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        raise UsageRmfError(f"Binary file cannot be backported: {path.name}")


def _factory_files(repo: Path) -> set[str]:
    out = set()
    for p in repo.rglob("*"):
        rel = p.relative_to(repo)
        if p.is_file() and not any(part in IGNORED_DIRS for part in rel.parts) and p.name != ".DS_Store":
            out.add(rel.as_posix())
    return out


def build_diff(template_root: Path, factory_root: Path, paths: list[str] | None) -> str:
    if paths:
        rels = sorted({_safe_rel(factory_root, p) for p in paths})
    else:
        tracked = set(compute_dir_manifest(template_root))
        rels = sorted(tracked | {f for f in _factory_files(factory_root) if f.split("/")[0] not in _EXCLUDE_TOP})
    chunks: list[str] = []
    for rel in rels:
        old = _read_lines(template_root / rel)
        new = _read_lines(factory_root / rel)
        if old == new:
            continue
        chunks.append(
            "".join(
                difflib.unified_diff(
                    old or [], new or [],
                    fromfile=f"a/{rel}" if old is not None else "/dev/null",
                    tofile=f"b/{rel}" if new is not None else "/dev/null",
                )
            )
        )
    return "".join(chunks)


def _bp_row(conn: sqlite3.Connection, bp_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM backports WHERE id = ?", (bp_id,)).fetchone()
    if row is None:
        raise NotFoundRmfError(f"Backport not found: {bp_id}")
    return row


def _public(row: sqlite3.Row, with_diff: bool = False) -> dict[str, Any]:
    d = dict(row)
    if not with_diff:
        d.pop("diff_text", None)
    return d


def capture_backport(factory_slug: str, title: str, paths: list[str] | None = None) -> dict[str, Any]:
    if not title.strip():
        raise UsageRmfError("title must be non-empty")
    conn = _conn()
    try:
        fac = get_factory_row(conn, factory_slug)
        tpl = conn.execute("SELECT * FROM templates WHERE id = ?", (fac["template_id"],)).fetchone()
        diff = build_diff(Path(tpl["root_path"]), Path(fac["repo_path"]), paths)
        if not diff.strip():
            raise UsageRmfError("No differences between factory and template", code="EMPTY_DIFF")
        bid, now = new_id("bp"), utc_now_iso()
        with conn:
            conn.execute(
                "INSERT INTO backports (id, source_factory_id, template_id, title, diff_text, diff_sha256, status,"
                " applied_revision_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'captured', NULL, ?, ?)",
                (bid, fac["id"], tpl["id"], title, diff, hashlib.sha256(diff.encode()).hexdigest(), now, now),
            )
        return {"backport": _public(_bp_row(conn, bid), with_diff=True)}
    finally:
        conn.close()


def list_backports(status: str | None = None) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        sql = "SELECT b.*, f.slug AS source_factory FROM backports b JOIN factories f ON f.id = b.source_factory_id"
        args: list[Any] = []
        if status:
            sql += " WHERE b.status = ?"
            args.append(status)
        sql += " ORDER BY b.created_at, b.id"
        return [_public(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def show_backport(bp_id: str) -> dict[str, Any]:
    conn = _conn()
    try:
        return {"backport": _public(_bp_row(conn, bp_id), with_diff=True)}
    finally:
        conn.close()


def _changed_paths(diff: str) -> list[str]:
    paths = []
    for line in diff.splitlines():
        if line.startswith("+++ "):
            name = line[4:]
            if name == "/dev/null":
                continue
            paths.append(name[2:])
        elif line.startswith("--- ") and line[4:] != "/dev/null":
            paths.append(line[6:])
    return sorted(set(paths))


def _apply_diff_to_lines(old: list[str], diff_lines: list[str]) -> list[str]:
    """Apply one file's unified diff (from difflib) to ``old``. Raises on context mismatch."""
    out: list[str] = []
    pos = 0
    i = 0
    while i < len(diff_lines):
        line = diff_lines[i]
        if line.startswith("@@"):
            # @@ -a,b +c,d @@
            try:
                old_start = int(line.split()[1].lstrip("-").split(",")[0])
            except (IndexError, ValueError) as exc:
                raise RuntimeRmfError(f"Malformed hunk header: {line!r}", code="PATCH_FAILED") from exc
            start = max(old_start - 1, 0) if not line.split()[1].endswith(",0") else old_start
            if start < pos:
                raise RuntimeRmfError("Overlapping hunks", code="PATCH_FAILED")
            out.extend(old[pos:start])
            pos = start
            i += 1
            while i < len(diff_lines) and not diff_lines[i].startswith("@@"):
                h = diff_lines[i]
                body = h[1:]
                if h.startswith(" "):
                    if pos >= len(old) or old[pos] != body:
                        raise RuntimeRmfError("Patch context mismatch (diverged file)", code="PATCH_FAILED")
                    out.append(old[pos]); pos += 1
                elif h.startswith("-"):
                    if pos >= len(old) or old[pos] != body:
                        raise RuntimeRmfError("Patch removal mismatch (diverged file)", code="PATCH_FAILED")
                    pos += 1
                elif h.startswith("+"):
                    out.append(body)
                elif h.startswith("\\"):
                    pass
                i += 1
        else:
            i += 1
    out.extend(old[pos:])
    return out


def split_diff(diff: str) -> dict[str, dict[str, Any]]:
    """Split a concatenated unified diff into {relpath: {"new": bool, "deleted": bool, "lines": [...]}}."""
    files: dict[str, dict[str, Any]] = {}
    cur: dict[str, Any] | None = None
    lines = diff.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            old_name, new_name = ln[4:].rstrip("\n"), lines[i + 1][4:].rstrip("\n")
            rel = (new_name if new_name != "/dev/null" else old_name)[2:]
            cur = {"new": old_name == "/dev/null", "deleted": new_name == "/dev/null", "lines": []}
            files[rel] = cur
            i += 2
            continue
        if cur is not None:
            cur["lines"].append(ln)
        i += 1
    return files


def apply_diff_to_root(diff: str, root: Path, check_only: bool = False) -> list[str]:
    """Apply diff to files under ``root``. All files are validated before any is written."""
    files = split_diff(diff)
    planned: dict[str, list[str] | None] = {}
    for rel, info in files.items():
        _safe_rel(root, rel)
        target = root / rel
        old = _read_lines(target) or []
        if info["new"] and target.exists():
            existing = _read_lines(target) or []
            planned[rel] = existing if existing == _new_content(info["lines"]) else _fail(rel)
            continue
        if info["deleted"]:
            planned[rel] = None
            continue
        planned[rel] = _apply_diff_to_lines(old, [l.rstrip("\n") if False else l.rstrip("\n") for l in info["lines"]]) if False else _apply_hunks(old, info["lines"])
    if not check_only:
        for rel, content in planned.items():
            target = root / rel
            if content is None:
                if target.exists():
                    target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("".join(content), encoding="utf-8")
    return sorted(planned)


def _fail(rel: str) -> Any:
    raise RuntimeRmfError(f"Patch conflicts with existing file: {rel}", code="PATCH_FAILED")


def _new_content(hunk_lines: list[str]) -> list[str]:
    return [l[1:] for l in hunk_lines if l.startswith("+")]


def _apply_hunks(old: list[str], raw_lines: list[str]) -> list[str]:
    # Keep trailing newlines on content lines; compare using the original line endings.
    out: list[str] = []
    pos = 0
    i = 0
    while i < len(raw_lines):
        h = raw_lines[i]
        if h.startswith("@@"):
            spec = h.split()[1]  # -a,b
            a = int(spec[1:].split(",")[0])
            count = int(spec[1:].split(",")[1]) if "," in spec else 1
            start = a if count == 0 else a - 1
            if start < pos:
                raise RuntimeRmfError("Overlapping hunks", code="PATCH_FAILED")
            out.extend(old[pos:start]); pos = start
            i += 1
            while i < len(raw_lines) and not raw_lines[i].startswith("@@"):
                ln = raw_lines[i]
                body = ln[1:]
                if ln.startswith(" "):
                    if pos >= len(old) or old[pos] != body:
                        raise RuntimeRmfError("Patch context mismatch (target diverged)", code="PATCH_FAILED")
                    out.append(old[pos]); pos += 1
                elif ln.startswith("-"):
                    if pos >= len(old) or old[pos] != body:
                        raise RuntimeRmfError("Patch removal mismatch (target diverged)", code="PATCH_FAILED")
                    pos += 1
                elif ln.startswith("+"):
                    out.append(body)
                i += 1
        else:
            i += 1
    out.extend(old[pos:])
    return out


def apply_backport(bp_id: str) -> dict[str, Any]:
    conn = _conn()
    try:
        bp = _bp_row(conn, bp_id)
        if bp["status"] != "captured":
            raise UsageRmfError(f"Backport {bp_id} is {bp['status']}, expected captured", code="INVALID_STATE")
        tpl = conn.execute("SELECT * FROM templates WHERE id = ?", (bp["template_id"],)).fetchone()
        root = Path(tpl["root_path"])
        touched = apply_diff_to_root(bp["diff_text"], root)
        with conn:
            rev = bump_in_conn(conn, tpl, f"backport {bp_id}: {bp['title']}", source_backport_id=bp_id)
            conn.execute(
                "UPDATE backports SET status='applied', applied_revision_id=?, updated_at=? WHERE id=?",
                (rev["id"], utc_now_iso(), bp_id),
            )
        return {"backport": _public(_bp_row(conn, bp_id)), "revision": rev, "files": touched}
    finally:
        conn.close()


def propagate_payload(bp: sqlite3.Row, target_slugs: list[str]) -> dict[str, Any]:
    return {
        "action": "backport_propagate",
        "backport_id": bp["id"],
        "diff_sha256": bp["diff_sha256"],
        "revision_id": bp["applied_revision_id"],
        "targets": sorted(target_slugs),
    }


def propagate_backport(
    bp_id: str, targets: str, yes: bool = False, approval_id: str | None = None
) -> dict[str, Any]:
    slugs = sorted({t.strip() for t in (targets or "").split(",") if t.strip()})
    if not slugs:
        raise UsageRmfError("targets must list at least one factory slug")
    conn = _conn()
    try:
        bp = _bp_row(conn, bp_id)
        if bp["status"] not in ("applied", "propagated") or not bp["applied_revision_id"]:
            raise UsageRmfError(f"Backport {bp_id} must be applied before propagation", code="INVALID_STATE")
        facs = [get_factory_row(conn, s) for s in slugs]
        for f in facs:
            if f["template_id"] != bp["template_id"]:
                raise UsageRmfError(f"Factory {f['slug']} was not stamped from this backport's template")
        payload = propagate_payload(bp, slugs)
        sha = gate_d.canonical_payload_sha256(payload)
        files = _changed_paths(bp["diff_text"])
        if not yes:
            return {
                "dry_run": True,
                "action_type": "backport_propagate",
                "target_ref": bp_id,
                "payload": payload,
                "payload_sha256": sha,
                "approval_required": True,
                "would_affect": {"factories": slugs, "files": files},
            }
        # Gate check first (consumes only after patch applicability is proven below).
        row = conn.execute("SELECT status FROM gate_approvals WHERE id = ?", (approval_id or "",)).fetchone()
        # Validate approval without consuming, then check applicability, then consume + write atomically.
        probe = conn.execute("SAVEPOINT probe")
        try:
            gate_d.require_and_consume_approval(conn, approval_id, "backport_propagate", bp_id, sha)
        finally:
            conn.execute("ROLLBACK TO probe")
            conn.execute("RELEASE probe")
        for f in facs:
            apply_diff_to_root(bp["diff_text"], Path(f["repo_path"]), check_only=True)
        tpl_rev = get_revision(conn, bp["applied_revision_id"])
        with conn:
            gate_d.require_and_consume_approval(conn, approval_id, "backport_propagate", bp_id, sha)
            for f in facs:
                repo = Path(f["repo_path"])
                apply_diff_to_root(bp["diff_text"], repo)
                conn.execute(
                    "UPDATE factories SET template_revision_id=?, updated_at=? WHERE id=?",
                    (bp["applied_revision_id"], utc_now_iso(), f["id"]),
                )
                git_sha = ""
                if (repo / ".git").exists():
                    run_git(["add", "-A"], repo)
                    run_git(
                        ["-c", "user.email=rmf@rig.local", "-c", "user.name=rmf", "commit", "-m",
                         f"backport {bp_id} (template v{tpl_rev['version']})", "--no-verify"], repo)
                    git_sha = run_git(["rev-parse", "HEAD"], repo).stdout.strip()
                add_changelog_in_conn(
                    conn, f["id"], "rmf",
                    f"Propagated backport {bp_id} ({bp['title']}) -> template v{tpl_rev['version']}", git_sha, "",
                )
            conn.execute("UPDATE backports SET status='propagated', updated_at=? WHERE id=?", (utc_now_iso(), bp_id))
        return {
            "dry_run": False,
            "propagated": slugs,
            "files": files,
            "revision_id": bp["applied_revision_id"],
            "payload_sha256": sha,
        }
    finally:
        conn.close()


def command_specs() -> list[CommandSpec]:
    bid = FlagSpec("id", required=True, description="Backport id")
    return [
        CommandSpec(
            "backport capture",
            "Diff a running factory against its template and store the diff as a backport.",
            "rmf backport capture --factory f1 --title 'Add CI' --paths README.md --json",
            False,
            lambda p: capture_backport(
                p["factory"], p["title"], [x for x in (p.get("paths") or "").split(",") if x.strip()] or None
            ),
            (
                FlagSpec("factory", required=True, description="Source factory slug"),
                FlagSpec("title", required=True, description="Backport title"),
                FlagSpec("paths", description="Comma-separated relative paths (default: all)"),
            ),
        ),
        CommandSpec(
            "backport list", "List backports.", "rmf backport list --status captured --json", True,
            lambda p: {"backports": list_backports(p.get("status"))},
            (FlagSpec("status", description="Filter by status", choices=("captured", "applied", "propagated", "rejected")),),
        ),
        CommandSpec("backport show", "Show the full diff and SHA-256 of a backport.", "rmf backport show --id bp_abc --json", True,
                    lambda p: show_backport(p["id"]), (bid,)),
        CommandSpec("backport apply", "Apply a backport diff to the template and bump its revision.",
                    "rmf backport apply --id bp_abc --json", False, lambda p: apply_backport(p["id"]), (bid,)),
        CommandSpec(
            "backport propagate",
            "Propagate an applied backport to target factories (Gate-D: dry-run without --yes).",
            "rmf backport propagate --id bp_abc --targets f2,f3 --yes --approval-id gate_abc --json",
            False,
            lambda p: propagate_backport(p["id"], p["targets"], bool(p.get("yes")), p.get("approval_id")),
            (
                bid,
                FlagSpec("targets", required=True, description="Comma-separated target factory slugs"),
                FlagSpec("yes", type="boolean", description="Execute (requires approved Gate-D item)"),
                FlagSpec("approval_id", description="Approved Gate-D approval id"),
            ),
            destructive=True,
        ),
    ]
