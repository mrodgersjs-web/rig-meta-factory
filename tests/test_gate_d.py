"""Tests for Step 3: Gate-D approval queue."""

from __future__ import annotations

import json
import sqlite3

from rig_meta_factory import gate_d
from rig_meta_factory.db import connect, init_db
from rig_meta_factory.errors import PolicyRmfError

import pytest


def _queue(rmf, payload=None):
    payload = payload or {"action": "factory_delete", "slug": "f1"}
    code, out = rmf(
        "gate", "queue", "--action-type", "factory_delete", "--target", "f1",
        "--payload-json", json.dumps(payload), "--requested-by", "lead",
    )
    assert code == 0
    return out["approval"]


def test_empty_list(rmf):
    code, out = rmf("gate", "list")
    assert code == 0 and out["approvals"] == []


def test_queue_persists_pending_with_deterministic_sha(rmf):
    a = _queue(rmf, {"b": 1, "a": 2})
    assert a["status"] == "pending"
    assert a["payload_sha256"] == gate_d.canonical_payload_sha256({"a": 2, "b": 1})
    code, out = rmf("gate", "show", "--id", a["id"])
    assert code == 0 and out["approval"]["payload"] == {"a": 2, "b": 1}
    code, out = rmf("gate", "list", "--status", "pending")
    assert len(out["approvals"]) == 1


def test_approve_requires_matching_sha(rmf):
    a = _queue(rmf)
    code, out = rmf("gate", "approve", "--id", a["id"], "--approver", "mike", "--sha256", "0" * 64)
    assert code == 3 and out["error"]["code"] == "GATE_D_SHA_MISMATCH"
    assert rmf("gate", "show", "--id", a["id"])[1]["approval"]["status"] == "pending"
    code, out = rmf("gate", "approve", "--id", a["id"], "--approver", "mike", "--sha256", a["payload_sha256"])
    assert code == 0 and out["approval"]["status"] == "approved" and out["approval"]["approver"] == "mike"


def test_reject(rmf):
    a = _queue(rmf)
    code, out = rmf("gate", "reject", "--id", a["id"], "--approver", "mike", "--note", "nope")
    assert code == 0 and out["approval"]["status"] == "rejected"
    assert out["approval"]["decision_note"] == "nope"
    code, out = rmf("gate", "approve", "--id", a["id"], "--approver", "mike", "--sha256", a["payload_sha256"])
    assert code == 3


def test_not_found_and_usage(rmf):
    assert rmf("gate", "show", "--id", "gate_nope")[0] == 4
    code, out = rmf("gate", "queue", "--action-type", "x", "--target", "t", "--payload-json", "{bad", "--requested-by", "a")
    assert code == 2 and out["error"]["code"] == "USAGE_ERROR"
    assert rmf("gate", "queue", "--action-type", "x")[0] == 2


def test_require_and_consume_and_replay(rmf):
    a = _queue(rmf)
    rmf("gate", "approve", "--id", a["id"], "--approver", "mike", "--sha256", a["payload_sha256"])
    init_db()
    conn = connect()
    try:
        with conn:
            gate_d.require_and_consume_approval(conn, a["id"], "factory_delete", "f1", a["payload_sha256"])
        assert rmf("gate", "show", "--id", a["id"])[1]["approval"]["status"] == "consumed"
        with pytest.raises(PolicyRmfError) as ei:
            with conn:
                gate_d.require_and_consume_approval(conn, a["id"], "factory_delete", "f1", a["payload_sha256"])
        assert ei.value.exit_code == 3 and ei.value.code == "GATE_D_REQUIRED"
    finally:
        conn.close()


def test_require_rejects_mismatch_unapproved_missing(rmf):
    a = _queue(rmf)
    conn = connect()
    try:
        for aid, act, tgt, sha in [
            (None, "factory_delete", "f1", a["payload_sha256"]),
            ("gate_missing", "factory_delete", "f1", a["payload_sha256"]),
            (a["id"], "factory_delete", "f1", a["payload_sha256"]),  # pending
            (a["id"], "other", "f1", a["payload_sha256"]),
            (a["id"], "factory_delete", "f2", a["payload_sha256"]),
            (a["id"], "factory_delete", "f1", "1" * 64),
        ]:
            with pytest.raises(PolicyRmfError):
                gate_d.require_and_consume_approval(conn, aid, act, tgt, sha)
    finally:
        conn.close()
