"""Gate-D human-near-the-loop approval queue with SHA-256 payload binding."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from rig_meta_factory.cli import CommandSpec, FlagSpec
from rig_meta_factory.db import connect, init_db, new_id, utc_now_iso
from rig_meta_factory.errors import NotFoundRmfError, PolicyRmfError, UsageRmfError


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_payload_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    try:
        d["payload"] = json.loads(d["payload_json"])
    except (TypeError, ValueError):
        d["payload"] = None
    return d


def _conn() -> sqlite3.Connection:
    init_db()
    return connect()


def queue_approval(action_type: str, target_ref: str, payload: Any, requested_by: str) -> dict[str, Any]:
    if not action_type.strip() or not target_ref.strip() or not requested_by.strip():
        raise UsageRmfError("action_type, target and requested_by must be non-empty")
    payload_json = canonical_json(payload)
    sha = canonical_payload_sha256(payload)
    conn = _conn()
    try:
        approval_id = new_id("gate")
        with conn:
            conn.execute(
                "INSERT INTO gate_approvals (id, action_type, target_ref, payload_json, payload_sha256, status,"
                " requested_by, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                (approval_id, action_type, target_ref, payload_json, sha, requested_by, utc_now_iso()),
            )
        return _row(conn.execute("SELECT * FROM gate_approvals WHERE id = ?", (approval_id,)).fetchone())
    finally:
        conn.close()


def list_approvals(status: str | None = None) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM gate_approvals WHERE status = ? ORDER BY created_at, id", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM gate_approvals ORDER BY created_at, id").fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def show_approval(approval_id: str) -> dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM gate_approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise NotFoundRmfError(f"Approval not found: {approval_id}")
        return _row(row)
    finally:
        conn.close()


def approve_item(approval_id: str, approver: str, sha256: str) -> dict[str, Any]:
    if not approver.strip():
        raise UsageRmfError("approver must be non-empty")
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM gate_approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise NotFoundRmfError(f"Approval not found: {approval_id}")
        if row["status"] != "pending":
            raise PolicyRmfError(
                f"Approval {approval_id} is {row['status']}, not pending", code="GATE_D_NOT_PENDING"
            )
        if sha256.strip().lower() != row["payload_sha256"]:
            raise PolicyRmfError(
                "SHA-256 does not match the stored payload_sha256", code="GATE_D_SHA_MISMATCH"
            )
        with conn:
            conn.execute(
                "UPDATE gate_approvals SET status='approved', approver=?, decided_at=? WHERE id=? AND status='pending'",
                (approver, utc_now_iso(), approval_id),
            )
        return _row(conn.execute("SELECT * FROM gate_approvals WHERE id = ?", (approval_id,)).fetchone())
    finally:
        conn.close()


def reject_item(approval_id: str, approver: str, note: str = "") -> dict[str, Any]:
    if not approver.strip():
        raise UsageRmfError("approver must be non-empty")
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM gate_approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise NotFoundRmfError(f"Approval not found: {approval_id}")
        if row["status"] != "pending":
            raise PolicyRmfError(
                f"Approval {approval_id} is {row['status']}, not pending", code="GATE_D_NOT_PENDING"
            )
        with conn:
            conn.execute(
                "UPDATE gate_approvals SET status='rejected', approver=?, decision_note=?, decided_at=? WHERE id=?",
                (approver, note, utc_now_iso(), approval_id),
            )
        return _row(conn.execute("SELECT * FROM gate_approvals WHERE id = ?", (approval_id,)).fetchone())
    finally:
        conn.close()


def require_and_consume_approval(
    conn: sqlite3.Connection,
    approval_id: str | None,
    action_type: str,
    target_ref: str,
    payload_sha256: str,
) -> dict[str, Any]:
    """Atomically consume a matching approved item on ``conn`` (caller owns the transaction).

    Raises PolicyRmfError (exit 3, GATE_D_REQUIRED) when missing/unapproved/mismatched/consumed.
    """
    if not approval_id:
        raise PolicyRmfError(
            "Gate-D approval required: pass --approval-id of an approved item", code="GATE_D_REQUIRED"
        )
    row = conn.execute("SELECT * FROM gate_approvals WHERE id = ?", (approval_id,)).fetchone()
    if row is None:
        raise PolicyRmfError(f"Gate-D approval not found: {approval_id}", code="GATE_D_REQUIRED")
    if (
        row["action_type"] != action_type
        or row["target_ref"] != target_ref
        or row["payload_sha256"] != payload_sha256
    ):
        raise PolicyRmfError(
            "Gate-D approval does not match action, target and payload SHA-256", code="GATE_D_REQUIRED"
        )
    if row["status"] != "approved":
        raise PolicyRmfError(
            f"Gate-D approval {approval_id} is {row['status']}, not approved", code="GATE_D_REQUIRED"
        )
    cur = conn.execute(
        "UPDATE gate_approvals SET status='consumed', decided_at=COALESCE(decided_at, ?) "
        "WHERE id=? AND status='approved'",
        (utc_now_iso(), approval_id),
    )
    if cur.rowcount != 1:
        raise PolicyRmfError(f"Gate-D approval {approval_id} already consumed", code="GATE_D_REQUIRED")
    return dict(row)


def _h_queue(p: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(p["payload_json"])
    except ValueError as exc:
        raise UsageRmfError(f"payload-json is not valid JSON: {exc}") from exc
    item = queue_approval(p["action_type"], p["target"], payload, p["requested_by"])
    return {"approval": item}


def _h_list(p: dict[str, Any]) -> dict[str, Any]:
    return {"approvals": list_approvals(p.get("status"))}


def _h_show(p: dict[str, Any]) -> dict[str, Any]:
    return {"approval": show_approval(p["id"])}


def _h_approve(p: dict[str, Any]) -> dict[str, Any]:
    return {"approval": approve_item(p["id"], p["approver"], p["sha256"])}


def _h_reject(p: dict[str, Any]) -> dict[str, Any]:
    return {"approval": reject_item(p["id"], p["approver"], p.get("note") or "")}


def command_specs() -> list[CommandSpec]:
    return [
        CommandSpec(
            "gate queue",
            "Enqueue an action for Gate-D approval.",
            "rmf gate queue --action-type factory_delete --target f1 --payload-json '{\"slug\":\"f1\"}' --requested-by lead --json",
            False,
            _h_queue,
            (
                FlagSpec("action_type", required=True, description="Action type, e.g. factory_delete"),
                FlagSpec("target", required=True, description="Target reference (slug or id)"),
                FlagSpec("payload_json", required=True, description="Exact JSON payload to approve"),
                FlagSpec("requested_by", required=True, description="Requesting agent name"),
            ),
        ),
        CommandSpec(
            "gate list",
            "List Gate-D queue items.",
            "rmf gate list --status pending --json",
            True,
            _h_list,
            (FlagSpec("status", description="Filter by status", choices=("pending", "approved", "rejected", "consumed")),),
        ),
        CommandSpec(
            "gate show",
            "Show exact payload and SHA-256 of a Gate-D item.",
            "rmf gate show --id gate_abc --json",
            True,
            _h_show,
            (FlagSpec("id", required=True, description="Approval id"),),
        ),
        CommandSpec(
            "gate approve",
            "Approve a pending Gate-D item; the SHA-256 must match the stored payload hash.",
            "rmf gate approve --id gate_abc --approver mike --sha256 <sha256> --json",
            False,
            _h_approve,
            (
                FlagSpec("id", required=True, description="Approval id"),
                FlagSpec("approver", required=True, description="Human approver name"),
                FlagSpec("sha256", required=True, description="Exact payload_sha256 being approved"),
            ),
        ),
        CommandSpec(
            "gate reject",
            "Reject a pending Gate-D item.",
            "rmf gate reject --id gate_abc --approver mike --note no --json",
            False,
            _h_reject,
            (
                FlagSpec("id", required=True, description="Approval id"),
                FlagSpec("approver", required=True, description="Human approver name"),
                FlagSpec("note", description="Decision note"),
            ),
        ),
    ]
