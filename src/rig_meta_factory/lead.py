"""Factory lead: fleet status and per-factory cron registry."""

from __future__ import annotations

import sqlite3
from typing import Any

from rig_meta_factory.cli import CommandSpec, FlagSpec
from rig_meta_factory.db import connect, init_db, new_id, utc_now_iso
from rig_meta_factory.errors import NotFoundRmfError, UsageRmfError
from rig_meta_factory.factories import STATES, get_factory_row


def _conn() -> sqlite3.Connection:
    init_db()
    return connect()


def status() -> dict[str, Any]:
    conn = _conn()
    try:
        by_state = {s: 0 for s in STATES}
        for r in conn.execute("SELECT state, COUNT(*) n FROM factories GROUP BY state"):
            by_state[r["state"]] = r["n"]
        one = lambda q: int(conn.execute(q).fetchone()[0])  # noqa: E731
        rows = []
        for f in conn.execute("SELECT * FROM factories ORDER BY slug"):
            fid = f["id"]
            own = conn.execute("SELECT agent_name FROM owner_agents WHERE factory_id=?", (fid,)).fetchone()
            rows.append({
                "slug": f["slug"], "state": f["state"],
                "owner": own["agent_name"] if own else None,
                "unread_mail": int(conn.execute("SELECT COUNT(*) FROM mailbox_messages WHERE to_factory_id=? AND status='unread'", (fid,)).fetchone()[0]),
                "enabled_crons": int(conn.execute("SELECT COUNT(*) FROM factory_crons WHERE factory_id=? AND enabled=1", (fid,)).fetchone()[0]),
                "open_missions": int(conn.execute("SELECT COUNT(*) FROM tac_missions WHERE factory_id=? AND status NOT IN ('receipted','failed')", (fid,)).fetchone()[0]),
            })
        return {
            "factories_by_state": by_state,
            "unread_mail": one("SELECT COUNT(*) FROM mailbox_messages WHERE status='unread'"),
            "pending_gate": one("SELECT COUNT(*) FROM gate_approvals WHERE status='pending'"),
            "enabled_crons": one("SELECT COUNT(*) FROM factory_crons WHERE enabled=1"),
            "factories": rows,
        }
    finally:
        conn.close()


def crons(slug: str | None) -> dict[str, Any]:
    conn = _conn()
    try:
        sql, args = "SELECT c.*, f.slug factory FROM factory_crons c JOIN factories f ON f.id=c.factory_id", []
        if slug:
            sql += " WHERE c.factory_id = ?"
            args.append(get_factory_row(conn, slug)["id"])
        return {"crons": [dict(r) for r in conn.execute(sql + " ORDER BY f.slug, c.name", args)]}
    finally:
        conn.close()


def cron_set(slug: str, name: str, schedule: str, command: str, enabled: bool = True) -> dict[str, Any]:
    if not name.strip() or not schedule.strip() or not command.strip():
        raise UsageRmfError("name, schedule and command must be non-empty")
    if len(schedule.split()) != 5:
        raise UsageRmfError("schedule must be a 5-field cron expression", code="INVALID_SCHEDULE")
    conn = _conn()
    try:
        fac = get_factory_row(conn, slug)
        with conn:
            conn.execute(
                "INSERT INTO factory_crons (id, factory_id, name, schedule_expr, command, enabled, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(factory_id, name) DO UPDATE SET"
                " schedule_expr=excluded.schedule_expr, command=excluded.command, enabled=excluded.enabled,"
                " updated_at=excluded.updated_at",
                (new_id("cron"), fac["id"], name, schedule, command, 1 if enabled else 0, utc_now_iso()),
            )
        row = conn.execute("SELECT * FROM factory_crons WHERE factory_id=? AND name=?", (fac["id"], name)).fetchone()
        return {"cron": dict(row), "factory": slug}
    finally:
        conn.close()


def cron_remove(slug: str, name: str) -> dict[str, Any]:
    conn = _conn()
    try:
        fac = get_factory_row(conn, slug)
        with conn:
            cur = conn.execute("DELETE FROM factory_crons WHERE factory_id=? AND name=?", (fac["id"], name))
        if cur.rowcount == 0:
            raise NotFoundRmfError(f"No cron '{name}' on factory {slug}")
        return {"removed": name, "factory": slug}
    finally:
        conn.close()


def command_specs() -> list[CommandSpec]:
    fac = FlagSpec("factory", required=True, description="Factory slug")
    nm = FlagSpec("name", required=True, description="Cron name")
    return [
        CommandSpec("lead status", "Fleet view: factories by state, unread mail, pending Gate-D, crons.",
                    "rmf lead status --json", True, lambda p: status()),
        CommandSpec("lead crons", "List registered factory crons.", "rmf lead crons --factory f1 --json", True,
                    lambda p: crons(p.get("factory")), (FlagSpec("factory", description="Filter by factory slug"),)),
        CommandSpec(
            "lead cron-set", "Create or update a factory cron (registry only; nothing is scheduled on the host).",
            "rmf lead cron-set --factory f1 --name nightly --schedule '0 3 * * *' --command 'rmf lead status' --json", False,
            lambda p: cron_set(p["factory"], p["name"], p["schedule"], p["command"], not p.get("disabled")),
            (fac, nm, FlagSpec("schedule", required=True, description="5-field cron expression"),
             FlagSpec("command", required=True, description="Command to run"),
             FlagSpec("disabled", type="boolean", description="Register disabled")),
        ),
        CommandSpec("lead cron-remove", "Remove a factory cron.", "rmf lead cron-remove --factory f1 --name nightly --json",
                    False, lambda p: cron_remove(p["factory"], p["name"]), (fac, nm)),
    ]
