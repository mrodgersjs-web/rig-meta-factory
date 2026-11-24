"""Bridge to rig-tac-os: plan -> ship-stop -> run -> verify (separate verifier) -> receipt.

rig-tac-os prints ``key: value`` lines and writes its own record files. The registry stores the
paths of THOSE files (plan, run record, verify record, receipt JSON), never captured stdout, so
every later stage and the Jev gate read the records rig-tac-os itself wrote. The raw stdout and
stderr of each call are kept under ``<registry dir>/tac/<mission id>/``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from rig_meta_factory.cli import CommandSpec, FlagSpec
from rig_meta_factory.db import connect, init_db, new_id, resolve_db_path, utc_now_iso
from rig_meta_factory.errors import NotFoundRmfError, PolicyRmfError, RuntimeRmfError, UsageRmfError
from rig_meta_factory.factories import get_factory_row
from rig_meta_factory.owners import add_changelog_in_conn

MISSION_STATUSES = ("planned", "ship_stopped", "ran", "verified", "receipted", "failed")
_KEY_LINE = re.compile(r"^([a-z][a-z0-9_]*): (.*)$")


def default_tac_bin() -> str:
    """First ``rig-tac-os`` on PATH, else ``~/.rig/bin/rig-tac-os``."""
    return shutil.which("rig-tac-os") or str(Path.home() / ".rig" / "bin" / "rig-tac-os")


def tac_bin() -> str:
    """RMF_TAC_BIN, else the default lookup."""
    return os.environ.get("RMF_TAC_BIN", "").strip() or default_tac_bin()


def require_tac_bin() -> str:
    binary = tac_bin()
    if not Path(binary).exists():
        raise RuntimeRmfError(f"rig-tac-os not found at {binary} (set RMF_TAC_BIN)", code="TAC_MISSING")
    return binary


def orca_bin() -> str:
    """``RMF_ORCA_BIN`` (an empty string disables the Orca preflight), else the first ``orca`` on PATH."""
    if "RMF_ORCA_BIN" in os.environ:
        return os.environ["RMF_ORCA_BIN"].strip()
    return shutil.which("orca") or ""


def _orca_json(binary: str, args: list[str]) -> dict[str, Any]:
    proc = subprocess.run([binary, *args, "--json"], capture_output=True, text=True, check=False, timeout=60)
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _orca_preflight(repo: Path) -> dict[str, Any]:
    """rig-tac-os opens its builder and verifier terminals with ``orca terminal create --worktree path:<repo>``.

    That needs a reachable Orca runtime and a repo Orca already knows. Both are checked here and named
    in the error, instead of surfacing a traceback from inside rig-tac-os.
    """
    binary = orca_bin()
    if not binary:
        return {"checked": False, "reason": "no orca binary found (set RMF_ORCA_BIN)"}
    if not _orca_json(binary, ["status"]).get("ok"):
        raise RuntimeRmfError(
            "Orca runtime is not reachable; rig-tac-os starts its builder and verifier in Orca terminals. "
            "Start Orca (`orca open`) and retry.",
            code="ORCA_UNAVAILABLE",
        )
    if not _orca_json(binary, ["worktree", "show", "--worktree", f"path:{repo}"]).get("ok"):
        raise RuntimeRmfError(
            f"Factory repo {repo} is not registered with Orca, so no builder terminal can open there. "
            f"Run `orca repo add --path {repo}` and retry.",
            code="ORCA_WORKTREE_UNREGISTERED",
        )
    return {"checked": True}


def _conn() -> sqlite3.Connection:
    init_db()
    return connect()


def _mission(conn: sqlite3.Connection, mid: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tac_missions WHERE id = ?", (mid,)).fetchone()
    if row is None:
        raise NotFoundRmfError(f"Unknown mission: {mid}")
    return row


def _factory(conn: sqlite3.Connection, mission: sqlite3.Row) -> sqlite3.Row:
    return conn.execute("SELECT * FROM factories WHERE id=?", (mission["factory_id"],)).fetchone()


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


def _work(mid: str) -> Path:
    """Per-mission folder beside the registry, so rig-tac-os output never lands in the factory repo."""
    d = resolve_db_path().expanduser().parent / "tac" / mid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tac(args: list[str], cwd: Path, mid: str, stage: str) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run([require_tac_bin(), *args], cwd=str(cwd), capture_output=True, text=True, check=False)
    work = _work(mid)
    (work / f"{stage}.stdout.txt").write_text(cp.stdout)
    (work / f"{stage}.stderr.txt").write_text(cp.stderr)
    return cp


def _fields(stdout: str) -> dict[str, str]:
    """rig-tac-os ``key: value`` lines; the first value of a key wins (builder output may repeat a key)."""
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        match = _KEY_LINE.match(line.strip())
        if match:
            out.setdefault(match.group(1), match.group(2).strip())
    return out


def _git_status(repo: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        # A directory git cannot read would otherwise look "unchanged" and pass silently.
        raise RuntimeRmfError(
            f"Factory repo {repo} is not a readable git repository, so verifier writes cannot be detected: "
            f"{_last_line(proc.stderr) or f'git exited {proc.returncode}'}",
            code="FACTORY_NOT_GIT_REPO",
        )
    return sorted(line for line in proc.stdout.splitlines() if line.strip())


_BUILDER_EXIT = re.compile(r"^__BUILDER_EXIT__=(\d+)\s*$")


def _builder_exit(lines: list[str]) -> int | None:
    """Exit code of the builder command, taken from its own output line.

    The terminal also echoes the command that was typed, which carries the marker text mid-line,
    so only a line that starts with the marker counts.
    """
    for line in reversed(lines):
        match = _BUILDER_EXIT.match(line.strip())
        if match:
            return int(match.group(1))
    return None


_ENDED_STATUSES = frozenset({"exited", "closed", "killed", "dead"})


def _wait_for_builder(
    binary: str,
    terminal_id: str,
    wait_seconds: float,
    poll_seconds: float,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Poll the builder terminal until the builder command prints its exit line."""
    deadline = clock() + wait_seconds
    seen: dict[str, Any] = {"terminal": terminal_id, "exit_code": None, "status": "unknown"}
    while True:
        payload = _orca_json(binary, ["terminal", "read", "--terminal", terminal_id, "--limit", "200"])
        if payload.get("ok") is False:
            # Orca answered and no longer knows the terminal (closed, or Orca restarted).
            error = payload.get("error") or {}
            code = error.get("code") if isinstance(error, dict) else None
            return {**seen, "gone": True, "orca_error": str(code or "unknown")}
        terminal = (payload.get("result") or {}).get("terminal") or {}
        tail = terminal.get("tail") or []
        lines = [str(line) for line in tail] if isinstance(tail, list) else str(tail).splitlines()
        seen["status"] = str(terminal.get("status") or seen["status"])
        code = _builder_exit(lines)
        if code is not None:
            return {**seen, "exit_code": code}
        if seen["status"].lower() in _ENDED_STATUSES:
            # Orca still knows the terminal but it has ended without the exit line (closed or killed):
            # nothing more will be printed, so waiting on is pointless.
            return {**seen, "gone": True, "orca_error": f"terminal_{seen['status'].lower()}_without_exit_line"}
        if clock() >= deadline:
            return {**seen, "timed_out": True}
        sleeper(poll_seconds)


def _await_builder(builder_terminal: str) -> dict[str, Any]:
    """Wait for the builder command to exit.

    ``rig-tac-os run --wait`` returns early: it looks for the marker text anywhere in the terminal,
    and the terminal echoes the typed command, which contains it.
    """
    terminal_id = builder_terminal.split(" ", 1)[0]
    builder: dict[str, Any] = {"terminal": terminal_id, "waited": False, "exit_code": None}
    binary = orca_bin()
    if not binary or not terminal_id:
        return builder
    wait_seconds = float(os.environ.get("RMF_TAC_WAIT_SECONDS", "900"))
    poll_seconds = float(os.environ.get("RMF_TAC_POLL_SECONDS", "5"))
    started = time.monotonic()
    builder.update(_wait_for_builder(binary, terminal_id, wait_seconds, poll_seconds))
    builder.update(waited=True, wait_limit_seconds=wait_seconds,
                     waited_seconds=round(time.monotonic() - started, 1))
    return builder


_BOX_EDGE = re.compile(r"^[\s│╭╮┃┏┓|]+")
_BOX_TAIL = re.compile(r"[\s│╭╮╰╯┃┏┓┗┛|]+$")


def _meaningful_lines(text: str) -> list[str]:
    """Non-blank output lines without box-drawing borders, keeping the lines that say something."""
    kept: list[str] = []
    for line in text.splitlines():
        inner = _BOX_TAIL.sub("", _BOX_EDGE.sub("", line)).strip()
        if len(re.findall(r"[A-Za-z0-9]", inner)) >= 4:
            kept.append(inner)
    return kept


def _last_line(text: str) -> str:
    """The last line that says something. A last line of fewer than four words is prefixed with the
    line before it, so a boxed ``Got unexpected extra argument(s)`` / ``(<argument>)`` pair stays readable."""
    lines = _meaningful_lines(text)
    if not lines:
        return ""
    if len(lines[-1].split()) < 4 and len(lines) >= 2:
        return f"{lines[-2]} {lines[-1]}"
    return lines[-1]


def _fail(conn: sqlite3.Connection, mid: str, cp: subprocess.CompletedProcess[str], stage: str) -> None:
    _update(conn, mid, status="failed")
    raise RuntimeRmfError(
        f"rig-tac-os {stage} exited {cp.returncode}: "
        f"{_last_line(cp.stderr) or _last_line(cp.stdout) or 'no output'} "
        f"(full output: {_work(mid) / (stage + '.stderr.txt')})",
        code="TAC_FAILED",
    )


def _record_path(fields: dict[str, str], key: str, stage: str,
                 cp: subprocess.CompletedProcess[str]) -> Path:
    """A record file rig-tac-os says it wrote; anything else is a failed stage."""
    raw = fields.get(key, "")
    path = Path(raw) if raw else None
    if path is None or not path.is_file():
        raise RuntimeRmfError(
            f"rig-tac-os {stage} reported no existing {key} file ({raw or 'nothing printed'}); "
            f"stdout tail: {cp.stdout.strip()[-300:]}",
            code="TAC_FAILED",
        )
    return path


def _verify_error(mid: str, cp: subprocess.CompletedProcess[str], fields: dict[str, str],
                  touched: list[str]) -> RuntimeRmfError:
    return RuntimeRmfError(
        f"Verification failed for mission {mid}: done_test_exit={fields.get('done_test_exit', 'n/a')}, "
        f"verifier_modified_source={fields.get('verifier_modified_source', 'n/a')}, "
        f"factory_repo_touched={touched or 'none'}; stderr: {(cp.stderr or '').strip()[:300]}",
        code="TAC_VERIFY_FAILED",
    )


def _timeout_error(mid: str, builder: dict[str, Any]) -> RuntimeRmfError:
    terminal = builder.get("terminal")
    return RuntimeRmfError(
        f"Builder terminal {terminal} printed no exit line within the "
        f"{builder.get('wait_limit_seconds'):g}s wait limit (RMF_TAC_WAIT_SECONDS) and may still be "
        f"running in Orca. If it finishes later it can still change the factory repo. Read it with "
        f"`orca terminal read --terminal {terminal}`, then wait for it with "
        f"`rmf tac await --id {mid} --terminal {terminal}`; start no other builder in this factory "
        f"until it has exited.",
        code="TAC_BUILDER_TIMEOUT",
    )


def _check_builder(conn: sqlite3.Connection, mid: str, builder: dict[str, Any]) -> None:
    """Fail the mission when its builder neither exited nor can still be read."""
    if builder.get("gone"):
        _update(conn, mid, status="failed")
        raise RuntimeRmfError(
            f"Builder terminal {builder.get('terminal')} is no longer running and printed no exit line "
            f"(orca error {builder.get('orca_error')}); mission {mid} is failed. The builder cannot change "
            f"the factory repo any more: check what it left (`git -C <factory repo> status`), then plan "
            f"a new mission.",
            code="TAC_BUILDER_TERMINAL_GONE",
        )
    if builder.get("timed_out"):
        _update(conn, mid, status="failed")
        raise _timeout_error(mid, builder)


def plan(slug: str, step: str, task: str, done_test: str, builder: str, model: str | None = None) -> dict[str, Any]:
    for value, name in ((step, "step"), (task, "task"), (done_test, "done_test"), (builder, "builder")):
        if not value.strip():
            raise UsageRmfError(f"{name} must be non-empty")
    require_tac_bin()
    conn = _conn()
    try:
        fac = get_factory_row(conn, slug)
        mid, now = new_id("tac"), utc_now_iso()
        with conn:
            conn.execute(
                "INSERT INTO tac_missions (id, factory_id, step_name, task_text, done_test, builder_id, status,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'planned', ?, ?)",
                (mid, fac["id"], step, task, done_test, builder, now, now),
            )
        # No --out: the plan stays where rig-tac-os keeps plans, because `receipt` only hashes a
        # plan it finds in its own plans folder.
        argv = ["plan", task, "--step", step, "--done-test", done_test]
        if model:
            argv += ["--model", model]  # rig-tac-os takes the route itself and refuses Flash-class routes
        cp = _tac(argv, Path(fac["repo_path"]), mid, "plan")
        if cp.returncode != 0:
            _fail(conn, mid, cp, "plan")
        fields = _fields(cp.stdout)
        try:
            plan_path = _record_path(fields, "plan", "plan", cp)
        except RuntimeRmfError:
            _update(conn, mid, status="failed")
            raise
        return {"mission": _update(conn, mid, plan_path=str(plan_path)), "tac": fields}
    finally:
        conn.close()


def ship_stop(mid: str) -> dict[str, Any]:
    conn = _conn()
    try:
        m = _mission(conn, mid)
        _need(m, "planned")
        fac = _factory(conn, m)
        cp = _tac(["ship-stop", "--step", m["step_name"], "--plan", m["plan_path"]], Path(fac["repo_path"]), mid,
                  "ship-stop")
        if cp.returncode != 0:
            _update(conn, mid, status="failed")
            raise PolicyRmfError(
                f"SHIP-STOP refused mission {mid}: {(cp.stderr or cp.stdout).strip()[:400]}", code="SHIP_STOP_DENIED"
            )
        return {"mission": _update(conn, mid, status="ship_stopped"), "ship_stop": cp.stdout.strip()[-1500:]}
    finally:
        conn.close()


def run(mid: str) -> dict[str, Any]:
    conn = _conn()
    try:
        m = _mission(conn, mid)
        if m["run_path"]:
            # The run record is stored before the builder wait, so this mission's builder was already
            # started (a timeout or an interrupted `tac run` leaves exactly this state). Never start a
            # second one, and say so before the plain state check does.
            raise UsageRmfError(
                f"Mission {mid} already has a run record ({m['run_path']}), so its builder was started and may "
                f"still be running. Wait for it with `rmf tac await --id {mid} --terminal <terminal>`, or plan "
                f"a new mission; a second builder is never started for one mission.",
                code="INVALID_STATE",
            )
        _need(m, "ship_stopped")
        fac = _factory(conn, m)
        orca = _orca_preflight(Path(fac["repo_path"]))
        cp = _tac(["run", m["plan_path"], "--repo", fac["repo_path"], "--wait"], Path(fac["repo_path"]), mid, "run")
        if cp.returncode != 0:
            _fail(conn, mid, cp, "run")
        fields = _fields(cp.stdout)
        try:
            run_path = _record_path(fields, "run_record", "run", cp)
        except RuntimeRmfError:
            _update(conn, mid, status="failed")
            raise
        # The run record exists from here on: keep its path even if the builder never exits.
        _update(conn, mid, run_path=str(run_path))
        builder = _await_builder(fields.get("builder_terminal", ""))
        _check_builder(conn, mid, builder)
        return {"mission": _update(conn, mid, status="ran"), "tac": fields,
                "orca": orca, "builder": builder}
    finally:
        conn.close()


def await_run(mid: str, terminal: str) -> dict[str, Any]:
    """Wait again for a builder that outlived the ``tac run`` wait limit (timeout recovery).

    After TAC_BUILDER_TIMEOUT the mission is ``failed`` while its builder may still be running in Orca.
    This re-attaches to that terminal: once the builder exits (any exit code, as in ``run``: verify
    decides) the mission moves to ``ran`` on the run record it already has. The same applies to a mission
    left ``ship_stopped`` with a run record because ``tac run`` itself was interrupted.
    """
    handle = terminal.strip().split(" ", 1)[0]
    if not handle:
        raise UsageRmfError("terminal must be non-empty")
    conn = _conn()
    try:
        m = _mission(conn, mid)
        if m["status"] not in ("failed", "ship_stopped") or not m["run_path"]:
            raise UsageRmfError(
                f"Mission {mid} is {m['status']}; await is for a mission whose builder outlived the run wait "
                f"(status failed with a run record, or ship_stopped after an interrupted run)",
                code="INVALID_STATE",
            )
        if not orca_bin():
            raise RuntimeRmfError(
                "No orca binary found (set RMF_ORCA_BIN); the builder terminal cannot be read",
                code="ORCA_UNAVAILABLE",
            )
        builder = _await_builder(handle)
        _check_builder(conn, mid, builder)
        return {"mission": _update(conn, mid, status="ran"), "builder": builder}
    finally:
        conn.close()


def verify(mid: str, verifier: str) -> dict[str, Any]:
    vid = verifier.strip()
    conn = _conn()
    try:
        m = _mission(conn, mid)
        if not vid or vid == m["builder_id"]:
            raise PolicyRmfError("Verifier must be set and differ from the builder", code="SELF_VERIFY_FORBIDDEN")
        _need(m, "ran")
        fac = _factory(conn, m)
        repo = Path(fac["repo_path"])
        orca = _orca_preflight(repo)
        before = _git_status(repo)
        cp = _tac(["verify", m["run_path"], "--repo", fac["repo_path"], "--plan", m["plan_path"]], repo, mid, "verify")
        fields = _fields(cp.stdout)
        recorded: dict[str, Any] = {"verifier_id": vid}
        if Path(fields.get("verify_record", "") or ".").is_file():
            recorded["verify_path"] = fields["verify_record"]
        if cp.returncode != 0:
            _update(conn, mid, status="failed", **recorded)
            if "passed" in fields:
                raise _verify_error(mid, cp, fields, [])
            _fail(conn, mid, cp, "verify")
        # rig-tac-os checks its own repo for verifier writes; the factory repo is checked here.
        touched = [line for line in _git_status(repo) if line not in before]
        if fields.get("passed") != "True" or touched:
            _update(conn, mid, status="failed", **recorded)
            raise _verify_error(mid, cp, fields, touched)
        try:
            verify_path = _record_path(fields, "verify_record", "verify", cp)
        except RuntimeRmfError:
            _update(conn, mid, status="failed")
            raise
        return {"mission": _update(conn, mid, status="verified", **recorded), "tac": fields, "orca": orca}
    finally:
        conn.close()


def receipt(mid: str) -> dict[str, Any]:
    conn = _conn()
    try:
        m = _mission(conn, mid)
        if m["status"] != "verified":
            raise PolicyRmfError(f"Mission {mid} is {m['status']}; receipt needs verified", code="NOT_VERIFIED")
        fac = _factory(conn, m)
        work = _work(mid)
        # --out is a folder: rig-tac-os writes <run_id>.receipt.json and .md inside it.
        cp = _tac(["receipt", m["run_path"], m["verify_path"], "--out", str(work)], Path(fac["repo_path"]), mid,
                  "receipt")
        if cp.returncode != 0:
            _fail(conn, mid, cp, "receipt")
        try:
            out = _record_path(_fields(cp.stdout), "receipt_json", "receipt", cp)
        except RuntimeRmfError:
            _update(conn, mid, status="failed")
            raise
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
            lambda p: plan(p["factory"], p["step"], p["task"], p["done_test"], p["builder"], p.get("model") or None),
            (FlagSpec("factory", required=True, description="Factory slug"),
             FlagSpec("step", required=True, description="Step name"),
             FlagSpec("task", required=True, description="Task text"),
             FlagSpec("done_test", required=True, description="Runnable done-test command"),
             FlagSpec("builder", required=True, description="Builder agent id"),
             FlagSpec("model", description="Model route for the builder (default: the rig-tac-os route; Flash-class routes are refused)")),
        ),
        CommandSpec("tac ship-stop", "Run the SHIP-STOP review on a planned mission (refusal: exit 3).",
                    "rmf tac ship-stop --id tac_abc --json", False, lambda p: ship_stop(p["id"]), (mid,)),
        CommandSpec("tac run", "Run the builder for a ship-stopped mission.",
                    "rmf tac run --id tac_abc --json", False, lambda p: run(p["id"]), (mid,)),
        CommandSpec("tac await", "Wait again for a builder that outlived the run wait limit or an interrupted run.",
                    "rmf tac await --id tac_abc --terminal term_123 --json", False,
                    lambda p: await_run(p["id"], p.get("terminal") or ""),
                    (mid, FlagSpec("terminal", required=True,
                                   description="Builder terminal handle named in the TAC_BUILDER_TIMEOUT message"))),
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
