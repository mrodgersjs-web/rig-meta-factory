"""Inter-factory mailbox: send, inbox, ack."""

from __future__ import annotations

import sqlite3
from typing import Any

from rig_meta_factory.cli import CommandSpec, FlagSpec
from rig_meta_factory.db import connect, init_db, new_id, utc_now_iso
from rig_meta_factory.errors import NotFoundRmfError, UsageRmfError
from rig_meta_factory.factories import get_factory_row


def _conn() -> sqlite3.Connection:
    init_db()
    return connect()


def _pub(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for k in ("from", "to"):
        r = conn.execute("SELECT slug FROM factories WHERE id = ?", (d[f"{k}_factory_id"],)).fetchone()
        d[f"{k}_factory"] = r["slug"] if r else None
    return d


def send(from_slug: str, to_slug: str, subject: str, body: str) -> dict[str, Any]:
    if from_slug == to_slug:
        raise UsageRmfError("A factory cannot mail itself", code="SELF_SEND")
    if not subject.strip() or not body.strip():
        raise UsageRmfError("subject and body must be non-empty")
    conn = _conn()
    try:
        f, t = get_factory_row(conn, from_slug), get_factory_row(conn, to_slug)
        mid = new_id("msg")
        with conn:
            conn.execute(
                "INSERT INTO mailbox_messages (id, from_factory_id, to_factory_id, subject, body, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, 'unread', ?)",
                (mid, f["id"], t["id"], subject, body, utc_now_iso()),
            )
        return {"message": _pub(conn, conn.execute("SELECT * FROM mailbox_messages WHERE id = ?", (mid,)).fetchone())}
    finally:
        conn.close()


def inbox(slug: str, status: str = "unread") -> dict[str, Any]:
    conn = _conn()
    try:
        fac = get_factory_row(conn, slug)
        sql = "SELECT * FROM mailbox_messages WHERE to_factory_id = ?"
        args: list[Any] = [fac["id"]]
        if status != "all":
            sql += " AND status = ?"
            args.append(status)
        rows = conn.execute(sql + " ORDER BY created_at ASC, rowid ASC", args).fetchall()
        return {"factory": slug, "messages": [_pub(conn, r) for r in rows]}
    finally:
        conn.close()


def ack(mid: str) -> dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM mailbox_messages WHERE id = ?", (mid,)).fetchone()
        if row is None:
            raise NotFoundRmfError(f"Unknown message: {mid}")
        if row["status"] != "acked":
            with conn:
                conn.execute("UPDATE mailbox_messages SET status='acked', acked_at=? WHERE id=?", (utc_now_iso(), mid))
        return {"message": _pub(conn, conn.execute("SELECT * FROM mailbox_messages WHERE id = ?", (mid,)).fetchone())}
    finally:
        conn.close()


def command_specs() -> list[CommandSpec]:
    return [
        CommandSpec(
            "mailbox send", "Send a message from one factory to another.",
            "rmf mailbox send --from f1 --to f2 --subject 'Need schema' --body 'Please share v2' --json", False,
            lambda p: send(p["from_factory"], p["to_factory"], p["subject"], p["body"]),
            (
                FlagSpec("from_factory", required=True, description="Sender factory slug", cli_flag="--from"),
                FlagSpec("to_factory", required=True, description="Recipient factory slug", cli_flag="--to"),
                FlagSpec("subject", required=True, description="Subject"),
                FlagSpec("body", required=True, description="Body"),
            ),
        ),
        CommandSpec(
            "mailbox inbox", "List a factory's inbox, oldest first.", "rmf mailbox inbox --factory f2 --status unread --json", True,
            lambda p: inbox(p["factory"], p.get("status") or "unread"),
            (
                FlagSpec("factory", required=True, description="Factory slug"),
                FlagSpec("status", description="unread|all", default="unread", choices=("unread", "all")),
            ),
        ),
        CommandSpec("mailbox ack", "Acknowledge a message.", "rmf mailbox ack --id msg_abc --json", False,
                    lambda p: ack(p["id"]), (FlagSpec("id", required=True, description="Message id"),)),
    ]
