"""Step 10: rig-tac-os bridge, driven by a fake that prints what the real CLI prints.

The fake mirrors ``src/rig_tac/cli.py`` in the TAC system: ``plan`` prints ``plan: <file>``,
``run`` prints ``run_record: <file>``, ``verify`` prints ``passed: <True|False>`` and
``verify_record: <file>``, and ``receipt --out <dir>`` writes ``<dir>/<run_id>.receipt.json``
then prints ``receipt_json: <file>``. Behaviour switches are FAKE_* environment variables.
"""
from __future__ import annotations

import hashlib
import stat
import time
from pathlib import Path

from conftest import make_factories

_FAKE = r"""#!/bin/sh
echo "$@" >> "$FAKE_HOME/tac.log"
repo=""; plan=""; out=""; prev=""
for a in "$@"; do
  case "$prev" in --repo) repo="$a";; --plan) plan="$a";; --out) out="$a";; esac
  prev="$a"
done
case "$1" in
  plan)
    mkdir -p "$FAKE_HOME/plans"
    p="$FAKE_HOME/plans/plan_test.plan.json"
    echo '{"plan_id":"plan_test","done_test":{"command":"true","expected_exit":0}}' > "$p"
    echo "plan_id: plan_test"; echo "plan: $p"
    echo "prompt: $FAKE_HOME/plans/plan_test.prompt.md"; echo "sha256: 0000"
    [ -z "$FAKE_PLAN_RC" ] || exit "$FAKE_PLAN_RC" ;;
  ship-stop)
    if [ -n "$FAKE_SHIP_STOP_RC" ]; then echo "SHIP-STOP: refused (fake)" >&2; exit "$FAKE_SHIP_STOP_RC"; fi
    echo "OK TAC-278 (fake): gated" ;;
  run)
    [ -f "$2" ] || { echo "run: plan file missing: $2" >&2; exit 3; }
    mkdir -p "$FAKE_HOME/runs"
    r="$FAKE_HOME/runs/run_test.run.json"
    echo '{"run_id":"run_test","builder":{"terminal_id":"t-builder"}}' > "$r"
    echo "run_id: run_test"
    echo "builder_terminal: t-builder (TAC BUILDER plan_test)"
    echo "model_route: default mimo-plan/mimo-v2.6-pro"
    [ -n "$FAKE_RUN_SILENT" ] || echo "run_record: $r"
    echo "builder output that must not be parsed: run_record: NOT_A_FILE" ;;
  verify)
    [ -f "$2" ] || { echo "verify: run file missing: $2" >&2; exit 3; }
    [ -f "$plan" ] || { echo "verify: plan file missing: $plan" >&2; exit 3; }
    v="$FAKE_HOME/runs/verify_test.verify.json"
    echo '{"verify_id":"verify_test"}' > "$v"
    echo "verify_id: verify_test"
    echo "done_test_exit: ${FAKE_DONE_TEST_EXIT:-0}"
    echo "verifier_modified_source: False"
    echo "passed: ${FAKE_PASSED:-True}"
    echo "verify_record: $v"
    [ -z "$FAKE_TOUCH_REPO" ] || touch "$repo/$FAKE_TOUCH_REPO"
    [ "${FAKE_PASSED:-True}" = "True" ] ;;
  receipt)
    [ -f "$2" ] && [ -f "$3" ] || { echo "receipt: missing run/verify record" >&2; exit 3; }
    [ -d "$out" ] || { echo "receipt: --out must be a directory" >&2; exit 3; }
    j="$out/run_test.receipt.json"
    echo '{"schema":"rig-tac.receipt.v1","run_id":"run_test"}' > "$j"
    echo "receipt: $out/run_test.receipt.md"; echo "receipt_json: $j" ;;
esac
"""


_FAKE_ORCA = """#!/bin/sh
# Stand-in for the Orca CLI: reachable + registered unless a FAKE_ORCA_* variable says otherwise.
echo "$*" >> "$FAKE_HOME/orca.log"
case "$1 $2" in
  "status "*)
    if [ -n "${FAKE_ORCA_DOWN:-}" ]; then
      echo '{"ok":false,"error":{"code":"runtime_unavailable"}}'; exit 1
    fi
    echo '{"ok":true}' ;;
  "worktree show")
    if [ -n "${FAKE_ORCA_UNREGISTERED:-}" ]; then
      echo '{"ok":false,"error":{"code":"selector_not_found"}}'
    else
      echo '{"ok":true,"result":{"worktree":{"id":"repo::path"}}}'
    fi ;;
  "terminal read")
    c=0; [ -f "$FAKE_HOME/reads" ] && c="$(cat "$FAKE_HOME/reads")"; c=$((c + 1)); echo "$c" > "$FAKE_HOME/reads"
    if [ -n "${FAKE_ORCA_EXITED:-}" ]; then
      echo '{"ok":true,"result":{"terminal":{"handle":"t-builder","status":"exited","tail":[],"truncated":false,"limited":false,"returnedLineCount":0,"source":"stream"}}}'
    elif [ -n "${FAKE_ORCA_GONE:-}" ]; then
      echo '{"ok":false,"error":{"code":"terminal_not_found"}}'
    elif [ "$c" -gt "${FAKE_ORCA_POLLS:-0}" ]; then
      echo '{"ok":true,"result":{"terminal":{"status":"running","tail":["prompt.md); echo __BUILDER_EXIT__=$?","Working...","__BUILDER_EXIT__='"${FAKE_BUILDER_EXIT:-0}"'"]}}}'
    else
      echo '{"ok":true,"result":{"terminal":{"status":"running","tail":["prompt.md); echo __BUILDER_EXIT__=$?","Working..."]}}}'
    fi ;;
  *) echo '{"ok":false,"error":{"code":"unknown_command"}}'; exit 1 ;;
esac
"""


def _fake_tac(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "fake-home"
    home.mkdir(exist_ok=True)
    script = tmp_path / "fake-rig-tac-os"
    script.write_text(_FAKE)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("RMF_TAC_BIN", str(script))
    monkeypatch.setenv("FAKE_HOME", str(home))
    orca = tmp_path / "fake-orca"
    orca.write_text(_FAKE_ORCA)
    orca.chmod(orca.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("RMF_ORCA_BIN", str(orca))
    return home


def _plan(rmf, slug: str = "f1") -> str:
    code, out = rmf("tac", "plan", "--factory", slug, "--step", "s1", "--task", "t", "--done-test", "true", "--builder", "b1")
    assert code == 0, out
    return out["mission"]["id"]


def _ran(rmf, tmp_path: Path, monkeypatch) -> str:
    make_factories(rmf, tmp_path, "f1")
    _fake_tac(tmp_path, monkeypatch)
    mid = _plan(rmf)
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    assert rmf("tac", "run", "--id", mid)[0] == 0
    return mid


def _tac_log(home: Path) -> list[str]:
    return (home / "tac.log").read_text().splitlines()


def test_chain_keeps_the_record_files_tac_wrote(rmf, tmp_path, monkeypatch):
    repo = make_factories(rmf, tmp_path, "f1")
    home = _fake_tac(tmp_path, monkeypatch)
    mid = _plan(rmf)
    plan_file = Path(rmf("tac", "list")[1]["missions"][0]["plan_path"])
    assert plan_file.is_file() and json_plan_id(plan_file) == "plan_test"
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    ran = rmf("tac", "run", "--id", mid)[1]["mission"]
    run_file = Path(ran["run_path"])
    assert ran["status"] == "ran" and run_file.name == "run_test.run.json"  # first `run_record:` line wins
    work = tmp_path / "tac" / mid  # beside the registry, not inside the factory
    assert "run_record:" in (work / "run.stdout.txt").read_text()
    assert rmf("tac", "verify", "--id", mid, "--verifier", "b1")[1]["error"]["code"] == "SELF_VERIFY_FORBIDDEN"
    code, out = rmf("tac", "verify", "--id", mid, "--verifier", "g1")
    assert code == 0 and out["mission"]["status"] == "verified"
    verify_file = Path(out["mission"]["verify_path"])
    assert verify_file.name == "verify_test.verify.json"
    code, out = rmf("tac", "receipt", "--id", mid)
    assert code == 0, out
    receipt_file = Path(out["mission"]["receipt_path"])
    assert receipt_file.name == "run_test.receipt.json"
    assert out["mission"]["receipt_sha256"] == hashlib.sha256(receipt_file.read_bytes()).hexdigest()
    assert out["changelog"]["tac_receipt_sha256"] == out["mission"]["receipt_sha256"]
    assert rmf("tac", "list", "--factory", "f1", "--status", "receipted")[1]["missions"][0]["id"] == mid
    log = _tac_log(home)
    assert log[0] == "plan t --step s1 --done-test true"  # no --out: the plan stays in TAC's own folder
    assert log[1] == f"ship-stop --step s1 --plan {plan_file}"
    assert log[2] == f"run {plan_file} --repo {repo} --wait"
    assert log[3] == f"verify {run_file} --repo {repo} --plan {plan_file}"
    assert log[4] == f"receipt {run_file} {verify_file} --out {work}"


def json_plan_id(path: Path) -> str:
    import json

    return json.loads(path.read_text())["plan_id"]


def test_failed_done_test_fails_the_mission(rmf, tmp_path, monkeypatch):
    mid = _ran(rmf, tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_PASSED", "False")
    monkeypatch.setenv("FAKE_DONE_TEST_EXIT", "1")
    code, out = rmf("tac", "verify", "--id", mid, "--verifier", "g1")
    assert code == 1 and out["error"]["code"] == "TAC_VERIFY_FAILED"
    mission = rmf("tac", "list")[1]["missions"][0]
    assert mission["status"] == "failed" and mission["verifier_id"] == "g1"
    assert Path(mission["verify_path"]).is_file()  # the failing verify record is kept
    assert rmf("tac", "receipt", "--id", mid)[1]["error"]["code"] == "NOT_VERIFIED"


def test_verifier_writing_to_the_factory_repo_fails_the_mission(rmf, tmp_path, monkeypatch):
    repo = Path(tmp_path / "f1")
    mid = _ran(rmf, tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_TOUCH_REPO", "verifier_touched.txt")
    code, out = rmf("tac", "verify", "--id", mid, "--verifier", "g1")
    assert code == 1 and out["error"]["code"] == "TAC_VERIFY_FAILED"
    assert "factory_repo_touched=['?? verifier_touched.txt/']" in out["error"]["message"] or "verifier_touched.txt" in out["error"]["message"]
    assert (repo / "verifier_touched.txt").exists()  # the fake verifier really wrote it
    assert rmf("tac", "list")[1]["missions"][0]["status"] == "failed"


def test_ship_stop_refusal_is_a_policy_failure(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    _fake_tac(tmp_path, monkeypatch)
    mid = _plan(rmf)
    monkeypatch.setenv("FAKE_SHIP_STOP_RC", "2")
    code, out = rmf("tac", "ship-stop", "--id", mid)
    assert code == 3 and out["error"]["code"] == "SHIP_STOP_DENIED"
    assert rmf("tac", "list")[1]["missions"][0]["status"] == "failed"


def test_run_without_a_run_record_fails(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    _fake_tac(tmp_path, monkeypatch)
    mid = _plan(rmf)
    monkeypatch.setenv("FAKE_RUN_SILENT", "1")
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    code, out = rmf("tac", "run", "--id", mid)
    assert code == 1 and out["error"]["code"] == "TAC_FAILED"
    assert rmf("tac", "list")[1]["missions"][0]["status"] == "failed"


def test_missing_tac_binary_creates_no_mission(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    monkeypatch.setenv("RMF_TAC_BIN", str(tmp_path / "nowhere" / "rig-tac-os"))
    code, out = rmf("tac", "plan", "--factory", "f1", "--step", "s", "--task", "t", "--done-test", "true", "--builder", "b")
    assert code == 1 and out["error"]["code"] == "TAC_MISSING"
    assert rmf("tac", "list")[1]["missions"] == []


def test_lookup_and_state_guards(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    home = _fake_tac(tmp_path, monkeypatch)
    mid = _plan(rmf)
    calls = len(_tac_log(home))
    assert rmf("tac", "run", "--id", "tac_nope")[0] == 4
    assert rmf("tac", "run", "--id", mid)[1]["error"]["code"] == "INVALID_STATE"  # planned, not ship-stopped
    assert rmf("tac", "verify", "--id", mid, "--verifier", "b1")[1]["error"]["code"] == "SELF_VERIFY_FORBIDDEN"
    assert rmf("tac", "receipt", "--id", mid)[1]["error"]["code"] == "NOT_VERIFIED"
    assert len(_tac_log(home)) == calls  # none of the refusals reached rig-tac-os


def _status(mid: str) -> str:
    import os
    import sqlite3

    with sqlite3.connect(os.environ["RMF_DB_PATH"]) as conn:
        return str(conn.execute("SELECT status FROM tac_missions WHERE id = ?", (mid,)).fetchone()[0])


def _stage_calls(home: Path) -> list[str]:
    return [line.split(" ", 1)[0] for line in _tac_log(home)]


def test_tac_run_names_an_unreachable_orca_runtime(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    home = _fake_tac(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_ORCA_DOWN", "1")
    mid = _plan(rmf)
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    code, out = rmf("tac", "run", "--id", mid)
    assert code == 1 and out["error"]["code"] == "ORCA_UNAVAILABLE", out
    assert "orca open" in out["error"]["message"]
    assert _status(mid) == "ship_stopped"
    assert "run" not in _stage_calls(home)


def test_tac_run_names_an_unregistered_factory_repo(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    home = _fake_tac(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_ORCA_UNREGISTERED", "1")
    mid = _plan(rmf)
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    code, out = rmf("tac", "run", "--id", mid)
    assert code == 1 and out["error"]["code"] == "ORCA_WORKTREE_UNREGISTERED", out
    assert "orca repo add --path" in out["error"]["message"]
    assert _status(mid) == "ship_stopped"
    assert "run" not in _stage_calls(home)


def test_tac_verify_runs_the_same_orca_preflight(rmf, tmp_path, monkeypatch):
    mid = _ran(rmf, tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_ORCA_DOWN", "1")
    code, out = rmf("tac", "verify", "--id", mid, "--verifier", "other-builder")
    assert code == 1 and out["error"]["code"] == "ORCA_UNAVAILABLE", out
    assert _status(mid) == "ran"


def test_tac_orca_check_can_be_skipped_and_is_reported(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    home = _fake_tac(tmp_path, monkeypatch)
    monkeypatch.setenv("RMF_ORCA_BIN", "")
    mid = _plan(rmf)
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    code, out = rmf("tac", "run", "--id", mid)
    assert code == 0 and out["orca"] == {"checked": False, "reason": "no orca binary found (set RMF_ORCA_BIN)"}, out
    assert not (home / "orca.log").exists()
    assert out["builder"] == {"terminal": "t-builder", "waited": False, "exit_code": None}, out


def test_tac_failure_message_uses_the_last_output_line():
    from rig_meta_factory.tac_bridge import _last_line

    boxed = "Traceback (most recent call last):\n  box  x.py:51 in _orca_json\nOrcaError: orca terminal create failed rc=1: \n"
    assert _last_line(boxed) == "OrcaError: orca terminal create failed rc=1:"
    assert _last_line("") == ""
    rich = "╭─ Error ─────────────\n│ Got unexpected extra argument(s)                    │\n│ (rig-gateway/mimo-plan/mimo-v2.6-pro-ultraspeed)    │\n╰─────────────────────────────────────────────────────╯\n"
    assert _last_line(rich) == "Got unexpected extra argument(s) (rig-gateway/mimo-plan/mimo-v2.6-pro-ultraspeed)"


def test_tac_run_waits_for_the_builder_exit_line(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    home = _fake_tac(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_ORCA_POLLS", "2")
    monkeypatch.setenv("RMF_TAC_POLL_SECONDS", "0.01")
    mid = _plan(rmf)
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    code, out = rmf("tac", "run", "--id", mid)
    assert code == 0, out
    assert out["builder"]["waited"] is True and out["builder"]["exit_code"] == 0, out
    assert out["builder"]["terminal"] == "t-builder"
    assert (home / "reads").read_text().strip() == "3"
    assert _status(mid) == "ran"


def test_tac_run_reports_a_failed_builder_and_leaves_the_verdict_to_verify(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    _fake_tac(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_BUILDER_EXIT", "3")
    mid = _plan(rmf)
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    code, out = rmf("tac", "run", "--id", mid)
    assert code == 0 and out["builder"]["exit_code"] == 3, out
    assert _status(mid) == "ran"


def test_tac_run_fails_when_the_builder_never_prints_its_exit_line(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    _fake_tac(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_ORCA_POLLS", "999")
    monkeypatch.setenv("RMF_TAC_WAIT_SECONDS", "0.05")
    monkeypatch.setenv("RMF_TAC_POLL_SECONDS", "0.01")
    mid = _plan(rmf)
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    code, out = rmf("tac", "run", "--id", mid)
    assert code == 1 and out["error"]["code"] == "TAC_BUILDER_TIMEOUT", out
    assert "within the 0.05s wait limit (RMF_TAC_WAIT_SECONDS)" in out["error"]["message"], out
    assert "can still change the factory repo" in out["error"]["message"], out
    assert "orca terminal read --terminal t-builder" in out["error"]["message"]
    assert _status(mid) == "failed"


def test_builder_exit_ignores_the_echoed_command_line():
    from rig_meta_factory.tac_bridge import _builder_exit

    echoed = ['-prompt.md)"; echo __BUILDER_EXIT__=$?', "Working..."]
    assert _builder_exit(echoed) is None
    assert _builder_exit([*echoed, "__BUILDER_EXIT__=0"]) == 0
    assert _builder_exit(["__BUILDER_EXIT__=1", "__BUILDER_EXIT__=0"]) == 0


def test_tac_plan_passes_an_explicit_model_route_and_omits_it_by_default(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    home = _fake_tac(tmp_path, monkeypatch)
    base = ["tac", "plan", "--factory", "f1", "--step", "build", "--task", "Add NOTES.md",
            "--done-test", "test -f NOTES.md", "--builder", "orca-builder-1"]
    route = "rig-gateway/mimo-plan/mimo-v2.6-pro-ultraspeed"
    code, out = rmf(*base, "--model", route)
    assert code == 0, out
    code, out = rmf(*base)
    assert code == 0, out
    plans = [line for line in _tac_log(home) if line.startswith("plan ")]
    assert plans[0].endswith("--model " + route), plans[0]
    assert "--model" not in plans[1], plans[1]


def test_tac_verify_refuses_a_factory_that_is_not_a_git_repo(rmf, tmp_path, monkeypatch):
    import shutil
    mid = _ran(rmf, tmp_path, monkeypatch)
    shutil.rmtree(tmp_path / "f1" / ".git")
    code, out = rmf("tac", "verify", "--id", mid, "--verifier", "g1")
    assert code == 1 and out["error"]["code"] == "FACTORY_NOT_GIT_REPO", out
    assert "not a readable git repository" in out["error"]["message"], out
    assert rmf("tac", "list")[1]["missions"][0]["status"] == "ran"  # precondition failed: no state change
    assert not any(line.startswith("verify ") for line in _tac_log(tmp_path / "fake-home"))  # rig-tac-os never called


def test_tac_await_recovers_a_run_that_outlived_the_wait_limit(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    home = _fake_tac(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_ORCA_POLLS", "50")  # the builder needs 51 reads: longer than the short wait
    monkeypatch.setenv("RMF_TAC_WAIT_SECONDS", "0.05")
    monkeypatch.setenv("RMF_TAC_POLL_SECONDS", "0.01")
    mid = _plan(rmf)
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    code, out = rmf("tac", "run", "--id", mid)
    assert code == 1 and out["error"]["code"] == "TAC_BUILDER_TIMEOUT"
    assert f"tac await --id {mid} --terminal t-builder" in out["error"]["message"]
    assert rmf("tac", "verify", "--id", mid, "--verifier", "g1")[1]["error"]["code"] == "INVALID_STATE"
    code, out = rmf("tac", "await", "--id", mid, "--terminal", "t-builder")
    assert code == 1 and out["error"]["code"] == "TAC_BUILDER_TIMEOUT"  # still running: still failed
    assert rmf("tac", "list")[1]["missions"][0]["status"] == "failed"
    (home / "reads").unlink()  # the builder finishes: a fresh wait window now sees its exit line
    monkeypatch.setenv("FAKE_ORCA_POLLS", "0")
    monkeypatch.setenv("RMF_TAC_WAIT_SECONDS", "2")
    code, out = rmf("tac", "await", "--id", mid, "--terminal", "t-builder")
    assert code == 0, out
    assert out["mission"]["status"] == "ran" and out["builder"]["exit_code"] == 0
    assert rmf("tac", "verify", "--id", mid, "--verifier", "g1")[0] == 0


def test_tac_await_keeps_verify_decides_for_a_builder_that_exited_nonzero(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    _fake_tac(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_BUILDER_EXIT", "3")
    mid = _plan(rmf)
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    code, out = rmf("tac", "run", "--id", mid)
    assert code == 0 and out["mission"]["status"] == "ran" and out["builder"]["exit_code"] == 3
    # `ran` is not a timed-out mission: await refuses it instead of re-judging the builder.
    assert rmf("tac", "await", "--id", mid, "--terminal", "t-builder")[1]["error"]["code"] == "INVALID_STATE"


def test_tac_await_names_a_terminal_orca_no_longer_knows(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    _fake_tac(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_ORCA_POLLS", "50")
    monkeypatch.setenv("RMF_TAC_WAIT_SECONDS", "0.05")
    monkeypatch.setenv("RMF_TAC_POLL_SECONDS", "0.01")
    mid = _plan(rmf)
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    assert rmf("tac", "run", "--id", mid)[1]["error"]["code"] == "TAC_BUILDER_TIMEOUT"
    monkeypatch.setenv("FAKE_ORCA_GONE", "1")  # the terminal was closed, or Orca restarted
    code, out = rmf("tac", "await", "--id", mid, "--terminal", "t-builder")
    assert code == 1 and out["error"]["code"] == "TAC_BUILDER_TERMINAL_GONE"
    assert "terminal_not_found" in out["error"]["message"]
    assert rmf("tac", "list")[1]["missions"][0]["status"] == "failed"


def _set_status(mid: str, status: str) -> None:
    import os
    import sqlite3

    with sqlite3.connect(os.environ["RMF_DB_PATH"]) as conn:
        conn.execute("UPDATE tac_missions SET status = ? WHERE id = ?", (status, mid))


def test_tac_run_never_starts_a_second_builder_for_one_mission(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    home = _fake_tac(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_ORCA_POLLS", "50")
    monkeypatch.setenv("RMF_TAC_WAIT_SECONDS", "0.05")
    monkeypatch.setenv("RMF_TAC_POLL_SECONDS", "0.01")
    mid = _plan(rmf)
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    assert rmf("tac", "run", "--id", mid)[1]["error"]["code"] == "TAC_BUILDER_TIMEOUT"
    assert _stage_calls(home).count("run") == 1
    code, out = rmf("tac", "run", "--id", mid)
    assert code == 2 and out["error"]["code"] == "INVALID_STATE"
    assert "already has a run record" in out["error"]["message"]
    assert f"tac await --id {mid} --terminal" in out["error"]["message"]
    assert _stage_calls(home).count("run") == 1  # rig-tac-os run was not called again
    assert _status(mid) == "failed"


def test_tac_await_finishes_a_run_that_was_interrupted(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    home = _fake_tac(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_ORCA_POLLS", "50")
    monkeypatch.setenv("RMF_TAC_WAIT_SECONDS", "0.05")
    monkeypatch.setenv("RMF_TAC_POLL_SECONDS", "0.01")
    mid = _plan(rmf)
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    assert rmf("tac", "run", "--id", mid)[1]["error"]["code"] == "TAC_BUILDER_TIMEOUT"
    # An interrupted `tac run` (Ctrl-C during the builder wait): the run record is stored, the status
    # never left ship_stopped, and the builder is still running in Orca.
    _set_status(mid, "ship_stopped")
    assert rmf("tac", "verify", "--id", mid, "--verifier", "g1")[1]["error"]["code"] == "INVALID_STATE"
    (home / "reads").unlink()
    monkeypatch.setenv("FAKE_ORCA_POLLS", "0")
    monkeypatch.setenv("RMF_TAC_WAIT_SECONDS", "2")
    code, out = rmf("tac", "await", "--id", mid, "--terminal", "t-builder")
    assert code == 0, out
    assert out["mission"]["status"] == "ran" and out["builder"]["exit_code"] == 0
    assert rmf("tac", "verify", "--id", mid, "--verifier", "g1")[0] == 0


def test_tac_await_names_a_gone_terminal_after_an_interrupted_run(rmf, tmp_path, monkeypatch):
    make_factories(rmf, tmp_path, "f1")
    _fake_tac(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_ORCA_POLLS", "50")
    monkeypatch.setenv("RMF_TAC_WAIT_SECONDS", "0.05")
    monkeypatch.setenv("RMF_TAC_POLL_SECONDS", "0.01")
    mid = _plan(rmf)
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    assert rmf("tac", "run", "--id", mid)[1]["error"]["code"] == "TAC_BUILDER_TIMEOUT"
    _set_status(mid, "ship_stopped")
    monkeypatch.setenv("FAKE_ORCA_GONE", "1")
    code, out = rmf("tac", "await", "--id", mid, "--terminal", "t-builder")
    assert code == 1 and out["error"]["code"] == "TAC_BUILDER_TERMINAL_GONE"
    assert _status(mid) == "failed"
    # Without a run record await is still the wrong tool: plan and run first.
    mid2 = _plan(rmf)
    assert rmf("tac", "ship-stop", "--id", mid2)[0] == 0
    assert rmf("tac", "await", "--id", mid2, "--terminal", "t-builder")[1]["error"]["code"] == "INVALID_STATE"


def test_tac_await_names_a_terminal_that_ended_without_an_exit_line(rmf, tmp_path, monkeypatch):
    # What Orca really returns for a terminal that was closed: ok true, status "exited", an empty tail.
    make_factories(rmf, tmp_path, "f1")
    _fake_tac(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_ORCA_POLLS", "50")
    monkeypatch.setenv("RMF_TAC_WAIT_SECONDS", "0.05")
    monkeypatch.setenv("RMF_TAC_POLL_SECONDS", "0.01")
    mid = _plan(rmf)
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    assert rmf("tac", "run", "--id", mid)[1]["error"]["code"] == "TAC_BUILDER_TIMEOUT"
    monkeypatch.setenv("FAKE_ORCA_EXITED", "1")
    monkeypatch.setenv("RMF_TAC_WAIT_SECONDS", "30")  # must not wait for this: the terminal has ended
    started = time.monotonic()
    code, out = rmf("tac", "await", "--id", mid, "--terminal", "t-builder")
    assert code == 1 and out["error"]["code"] == "TAC_BUILDER_TERMINAL_GONE"
    assert "terminal_exited_without_exit_line" in out["error"]["message"]
    assert "cannot change the factory repo any more" in out["error"]["message"]
    assert _status(mid) == "failed"
    assert time.monotonic() - started < 10
