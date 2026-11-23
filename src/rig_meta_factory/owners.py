"""Per-factory owner agent mission and append-only changelog."""

from __future__ import annotations

import sqlite3
from typing import Any

from rig_meta_factory.cli import CommandSpec, FlagSpec
from rig_meta_factory.db import connect, init_db, new_id, utc_now_iso
from rig_meta_factory.errors import NotFoundRmfError, UsageRmfError
from rig_meta_factory.factories import get_factory_row

OWNER_STATUSES = ("active", "paused", "retired")


def _conn() -> sqlite3.Connection:
    init_db()
    return connect()


def set_owner(factory_slug: str, agent_name: str, mission: str, status: str = "active") -> dict[str, Any]:
    if not agent_name.strip() or not mission.strip():
        raise UsageRmfError("agent and mission must be non-empty")
    if status not in OWNER_STATUSES:
        raise UsageRmfError(f"Invalid status '{status}': one of {', '.join(OWNER_STATUSES)}")
    conn = _conn()
    try:
        fac = get_factory_row(conn, factory_slug)
        now = utc_now_iso()
        with conn:
            conn.execute(
                "INSERT INTO owner_agents (id, factory_id, agent_name, mission, status, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(factory_id) DO UPDATE SET agent_name=excluded.agent_name,"
                " mission=excluded.mission, status=excluded.status, updated_at=excluded.updated_at",
                (new_id("own"), fac["id"], agent_name, mission, status, now),
            )
        return {"owner": dict(conn.execute("SELECT * FROM owner_agents WHERE factory_id = ?", (fac["id"],)).fetchone()), "factory": factory_slug}
    finally:
        conn.close()


def show_owner(factory_slug: str) -> dict[str, Any]:
    conn = _conn()
    try:
        fac = get_factory_row(conn, factory_slug)
        row = conn.execute("SELECT * FROM owner_agents WHERE factory_id = ?", (fac["id"],)).fetchone()
        if row is None:
            raise NotFoundRmfError(f"No owner agent set for factory: {factory_slug}")
        return {"owner": dict(row), "factory": factory_slug}
    finally:
        conn.close()


def add_changelog_in_conn(
    conn: sqlite3.Connection, factory_id: str, author_agent: str, summary: str,
    git_sha: str = "", tac_receipt_sha256: str = "",
) -> dict[str, Any]:
    cid = new_id("chg")
    conn.execute(
        "INSERT INTO owner_changelogs (id, factory_id, author_agent, summary, git_sha, tac_receipt_sha256, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cid, factory_id, author_agent, summary, git_sha or "", tac_receipt_sha256 or "", utc_now_iso()),
    )
    return dict(conn.execute("SELECT * FROM owner_changelogs WHERE id = ?", (cid,)).fetchone())


def add_changelog(
    factory_slug: str, author_agent: str, summary: str, git_sha: str = "", tac_receipt_sha256: str = ""
) -> dict[str, Any]:
    if not author_agent.strip() or not summary.strip():
        raise UsageRmfError("author and summary must be non-empty")
    conn = _conn()
    try:
        fac = get_factory_row(conn, factory_slug)
        with conn:
            entry = add_changelog_in_conn(conn, fac["id"], author_agent, summary, git_sha, tac_receipt_sha256)
        return {"entry": entry}
    finally:
        conn.close()


def list_changelogs(factory_slug: str, limit: int = 50) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        fac = get_factory_row(conn, factory_slug)
        rows = conn.execute(
            "SELECT * FROM owner_changelogs WHERE factory_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (fac["id"], max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def command_specs() -> list[CommandSpec]:
    fac = FlagSpec("factory", required=True, description="Factory slug")
    return [
        CommandSpec(
            "owner set",
            "Create or update the owner agent and mission for a factory.",
            "rmf owner set --factory f1 --agent owner-1 --mission 'Ship reliable builds' --json",
            False,
            lambda p: set_owner(p["factory"], p["agent"], p["mission"], p.get("status") or "active"),
            (
                fac,
                FlagSpec("agent", required=True, description="Owner agent name"),
                FlagSpec("mission", required=True, description="Mission statement"),
                FlagSpec("status", description="Owner status", choices=OWNER_STATUSES),
            ),
        ),
        CommandSpec("owner show", "Show the owner agent for a factory.", "rmf owner show --factory f1 --json", True,
                    lambda p: show_owner(p["factory"]), (fac,)),
        CommandSpec(
            "owner changelog-add",
            "Append a changelog entry (optional git and TAC receipt SHA).",
            "rmf owner changelog-add --factory f1 --author owner-1 --summary 'Added x' --git-sha abc123 --json",
            False,
            lambda p: add_changelog(p["factory"], p["author"], p["summary"], p.get("git_sha") or "", p.get("tac_receipt_sha256") or ""),
            (
                fac,
                FlagSpec("author", required=True, description="Author agent"),
                FlagSpec("summary", required=True, description="Change summary"),
                FlagSpec("git_sha", description="Related git commit SHA"),
                FlagSpec("tac_receipt_sha256", description="Related TAC receipt SHA-256"),
            ),
        ),
        CommandSpec(
            "owner changelog-list",
            "List changelog entries for a factory, newest first.",
            "rmf owner changelog-list --factory f1 --json",
            True,
            lambda p: {"entries": list_changelogs(p["factory"], p.get("limit") or 50)},
            (fac, FlagSpec("limit", type="integer", description="Max entries", default=50)),
        ),
    ]
