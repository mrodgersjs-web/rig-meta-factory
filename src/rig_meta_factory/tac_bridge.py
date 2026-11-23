"""Bridge to rig-tac-os: plan -> ship-stop -> run -> verify (separate verifier) -> receipt."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from rig_meta_factory.cli import CommandSpec, FlagSpec
from rig_meta_factory.db import connect, init_db, new_id, utc_now_iso
from rig_meta_factory.errors import NotFoundRmfError, PolicyRmfError, RuntimeRmfError, UsageRmfError
from rig_meta_factory.factories import get_factory_row
from rig_meta_factory.owners import add_changelog_in_conn

DEFAULT_TAC_BIN = "/Users/rig128gb/.rig/bin/rig-tac-os"
MISSION_STATUSES = ("planned", "ship_stopped", "ran", "verified", "receipted", "failed")


def _conn() -> sqlite3.Connection:
    init_db()
    return connect()


def tac_bin() -> str:
    return os.environ.get("RMF_TAC_BIN", "").strip() or DEFAULT_TAC_BIN


def _tac(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    b = tac_bin()
    if not Path(b).exists():
        raise RuntimeRmfError(f"rig-tac-os not found at {b} (set RMF_TAC_BIN)", code="TAC_MISSING")
    return subprocess.run([b, *args], cwd=str(cwd), capture_output=True, text=True, check=False)


def _mission(conn: sqlite3.Connection, mid: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tac_missions WHERE id = ?", (mid,)).fetchone()
    if row is None:
        raise NotFoundRmfError(f"Unknown mission: {mid}")
    return row


def _update(conn: sqlite3.Connection, mid: str, **cols: Any) -> dict[str, Any]:
    cols["updated_at"] = utc_now_iso()
    with conn:
        conn.execute(
            f"UPDATE tac_missions SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", (*cols.values(), mid)
        )
    return dict(_mission(conn, mid))


def _need(row: sqlite3.Row, status: str) -> None:
    if row["status"] != status:
        raise UsageRmfError(f"Mission {row['id']} is {row['status']}, expected {status}", code="INVALID_STATE")


def _work(fac: sqlite3.Row, mid: str) -> Path:
    d = Path(fac["repo_path"]) / ".rmf" / "tac" / mid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fail(conn: sqlite3.Connection, mid: str, cp: subprocess.CompletedProcess[str], stage: str) -> None:
    _update(conn, mid, status="failed")
    raise RuntimeRmfError(f"rig-tac-os {stage} exited {cp.returncode}: {(cp.stderr or cp.stdout).strip()[:400]}",
                          code="TAC_FAILED")


def plan(slug: str, step: str, task: str, done_test: str, builder: str) -> dict[str, Any]:
    for v, n in ((step, "step"), (task, "task"), (done_test, "done_test"), (builder, "builder")):
        if not v.strip():
            raise UsageRmfError(f"{n} must be non-empty")
    conn = _conn()
    try:
        fac = get_factory_row(conn, slug)
        mid, now = new_id("tac"), utc_now_iso()
        out = _work(fac, mid) / "plan.json"
        with conn:
            conn.execute(
                "INSERT INTO tac_missions (id, factory_id, step_name, task_text, done_test, builder_id, status,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'planned', ?, ?)",
                (mid, fac["id"], step, task, done_test, builder, now, now),
            )
        cp = _tac(["plan", task, "--step", step, "--done-test", done_test, "--out", str(out)], Path(fac["repo_path"]))
        if cp.returncode != 0:
            _fail(conn, mid, cp, "plan")
        return {"mission": _update(conn, mid, plan_path=str(out))}
    finally:
        conn.close()


def ship_stop(mid: str) -> dict[str, Any]:
    conn = _conn()
    try:
        m = _mission(conn, mid)
        _need(m, "planned")
        fac = conn.execute("SELECT * FROM factories WHERE id=?", (m["factory_id"],)).fetchone()
        cp = _tac(["ship-stop", "--plan", m["plan_path"]], Path(fac["repo_path"]))
        if cp.returncode != 0:
            _fail(conn, mid, cp, "ship-stop")
        return {"mission": _update(conn, mid, status="ship_stopped"), "ship_stop": cp.stdout.strip()}
    finally:
        conn.close()


def run(mid: str) -> dict[str, Any]:
    conn = _conn()
    try:
        m = _mission(conn, mid)
        _need(m, "ship_stopped")
        fac = conn.execute("SELECT * FROM factories WHERE id=?", (m["factory_id"],)).fetchone()
        out = _work(fac, mid) / "run.json"
        cp = _tac(["run", m["plan_path"], "--repo", fac["repo_path"], "--wait"], Path(fac["repo_path"]))
        if cp.returncode != 0:
            _fail(conn, mid, cp, "run")
        out.write_text(cp.stdout)
        return {"mission": _update(conn, mid, status="ran", run_path=str(out))}
    finally:
        conn.close()


def verify(mid: str, verifier: str) -> dict[str, Any]:
    conn = _conn()
    try:
        m = _mission(conn, mid)
        if not (verifier or "").strip() or verifier.strip() == m["builder_id"]:
            raise PolicyRmfError("Verifier must be set and differ from the builder", code="SELF_VERIFY_FORBIDDEN")
        _need(m, "ran")
        fac = conn.execute("SELECT * FROM factories WHERE id=?", (m["factory_id"],)).fetchone()
        out = _work(fac, mid) / "verify.json"
        cp = _tac(["verify", m["run_path"], "--repo", fac["repo_path"], "--plan", m["plan_path"]], Path(fac["repo_path"]))
        out.write_text(cp.stdout)
        if cp.returncode != 0:
            _update(conn, mid, verifier_id=verifier, verify_path=str(out))
            _fail(conn, mid, cp, "verify")
        return {"mission": _update(conn, mid, status="verified", verifier_id=verifier, verify_path=str(out))}
    finally:
        conn.close()


def receipt(mid: str) -> dict[str, Any]:
    conn = _conn()
    try:
        m = _mission(conn, mid)
        if m["status"] != "verified":
            raise PolicyRmfError(f"Mission {mid} is {m['status']}; receipt needs verified", code="NOT_VERIFIED")
        fac = conn.execute("SELECT * FROM factories WHERE id=?", (m["factory_id"],)).fetchone()
        out = _work(fac, mid) / "receipt.json"
        cp = _tac(["receipt", m["run_path"], m["verify_path"], "--out", str(out)], Path(fac["repo_path"]))
        if cp.returncode != 0 or not out.exists():
            _fail(conn, mid, cp, "receipt")
        sha = hashlib.sha256(out.read_bytes()).hexdigest()
        with conn:
            entry = add_changelog_in_conn(conn, fac["id"], m["builder_id"],
                                          f"TAC receipt for {m['step_name']} ({mid}), verified by {m['verifier_id']}", "", sha)
        return {"mission": _update(conn, mid, status="receipted", receipt_path=str(out), receipt_sha256=sha),
                "changelog": entry}
    finally:
        conn.close()


def list_missions(slug: str | None, status: str | None) -> dict[str, Any]:
    conn = _conn()
    try:
        sql, args = "SELECT * FROM tac_missions WHERE 1=1", []
        if slug:
            sql += " AND factory_id=?"
            args.append(get_factory_row(conn, slug)["id"])
        if status:
            sql += " AND status=?"
            args.append(status)
        return {"missions": [dict(r) for r in conn.execute(sql + " ORDER BY created_at DESC, rowid DESC", args)]}
    finally:
        conn.close()


def command_specs() -> list[CommandSpec]:
    mid = FlagSpec("id", required=True, description="Mission id")
    return [
        CommandSpec(
            "tac plan", "Create a TAC mission and its rig-tac-os plan for a factory step.",
            "rmf tac plan --factory f1 --step build --task 'Add CI' --done-test 'pytest -q' --builder owner-1 --json", False,
            lambda p: plan(p["factory"], p["step"], p["task"], p["done_test"], p["builder"]),
            (FlagSpec("factory", required=True, description="Factory slug"),
             FlagSpec("step", required=True, description="Step name"),
             FlagSpec("task", required=True, description="Task text"),
             FlagSpec("done_test", required=True, description="Runnable done-test command"),
             FlagSpec("builder", required=True, description="Builder agent id")),
        ),
        CommandSpec("tac ship-stop", "Run the SHIP-STOP review on a planned mission.", "rmf tac ship-stop --id tac_abc --json",
                    False, lambda p: ship_stop(p["id"]), (mid,)),
        CommandSpec("tac run", "Run the builder for a ship-stopped mission.", "rmf tac run --id tac_abc --json",
                    False, lambda p: run(p["id"]), (mid,)),
        CommandSpec("tac verify", "Verify a run with a separate verifier (self-verify is refused, exit 3).",
                    "rmf tac verify --id tac_abc --verifier goldfish-1 --json", False,
                    lambda p: verify(p["id"], p.get("verifier") or ""),
                    (mid, FlagSpec("verifier", required=True, description="Verifier agent id (must differ from builder)"))),
        CommandSpec("tac receipt", "Hash the receipt of a verified mission and link it to the owner changelog.",
                    "rmf tac receipt --id tac_abc --json", False, lambda p: receipt(p["id"]), (mid,)),
        CommandSpec("tac list", "List TAC missions.", "rmf tac list --factory f1 --status verified --json", True,
                    lambda p: list_missions(p.get("factory"), p.get("status")),
                    (FlagSpec("factory", description="Filter by factory slug"),
                     FlagSpec("status", description="Filter by status", choices=MISSION_STATUSES))),
    ]
