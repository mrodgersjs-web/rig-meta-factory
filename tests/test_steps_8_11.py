"""Steps 8-11: mailbox, lead, tac bridge (fake rig-tac-os), MCP server."""
from __future__ import annotations

import json
import stat

from conftest import run_subprocess


def _facs(rmf, tmp_path, *slugs):
    rmf("template", "init", "--slug", "base", "--name", "B", "--root", str(tmp_path / "tpl"))
    for s in slugs:
        assert rmf("factory", "create", "--slug", s, "--name", s, "--template", "base", "--dest", str(tmp_path / s))[0] == 0


def test_mailbox(rmf, tmp_path):
    _facs(rmf, tmp_path, "f1", "f2")
    code, out = rmf("mailbox", "send", "--from", "f1", "--to", "f2", "--subject", "hi", "--body", "b")
    assert code == 0 and out["message"]["from_factory"] == "f1"
    mid = out["message"]["id"]
    assert [m["id"] for m in rmf("mailbox", "inbox", "--factory", "f2")[1]["messages"]] == [mid]
    assert rmf("mailbox", "ack", "--id", mid)[1]["message"]["acked_at"]
    assert rmf("mailbox", "inbox", "--factory", "f2")[1]["messages"] == []
    assert len(rmf("mailbox", "inbox", "--factory", "f2", "--status", "all")[1]["messages"]) == 1
    assert rmf("mailbox", "send", "--from", "f1", "--to", "f1", "--subject", "s", "--body", "b")[0] == 2
    assert rmf("mailbox", "send", "--from", "f1", "--to", "zz", "--subject", "s", "--body", "b")[0] == 4
    assert rmf("mailbox", "ack", "--id", "msg_nope")[0] == 4


def test_lead(rmf, tmp_path):
    _facs(rmf, tmp_path, "f1", "f2")
    rmf("mailbox", "send", "--from", "f1", "--to", "f2", "--subject", "s", "--body", "b")
    assert rmf("lead", "cron-set", "--factory", "f1", "--name", "n", "--schedule", "0 3 * * *", "--command", "x")[0] == 0
    assert rmf("lead", "cron-set", "--factory", "f1", "--name", "n", "--schedule", "0 4 * * *", "--command", "y")[1]["cron"]["schedule_expr"] == "0 4 * * *"
    assert rmf("lead", "cron-set", "--factory", "f1", "--name", "b", "--schedule", "bad", "--command", "x")[0] == 2
    st = rmf("lead", "status")[1]
    assert st["factories_by_state"]["empty_stamped"] == 2 and st["unread_mail"] == 1 and st["enabled_crons"] == 1
    assert len(rmf("lead", "crons")[1]["crons"]) == 1
    assert rmf("lead", "cron-remove", "--factory", "f1", "--name", "n")[0] == 0
    assert rmf("lead", "cron-remove", "--factory", "f1", "--name", "n")[0] == 4


def _fake_tac(tmp_path, fail_verify=False):
    b = tmp_path / "fake-tac"
    b.write_text(f"""#!/bin/sh
echo "$@" >> "{tmp_path}/tac.log"
case "$1" in
  plan) while [ $# -gt 0 ]; do [ "$1" = --out ] && echo '{{"plan":1}}' > "$2"; shift; done ;;
  verify) {'exit 1' if fail_verify else 'echo "{\\"verdict\\":\\"PASS\\"}"'} ;;
  receipt) while [ $# -gt 0 ]; do [ "$1" = --out ] && echo '{{"receipt":1}}' > "$2"; shift; done ;;
  *) echo '{{"ok":true}}' ;;
esac
""")
    b.chmod(b.stat().st_mode | stat.S_IEXEC)
    return b


def test_tac_flow(rmf, tmp_path, monkeypatch):
    _facs(rmf, tmp_path, "f1")
    monkeypatch.setenv("RMF_TAC_BIN", str(_fake_tac(tmp_path)))
    code, out = rmf("tac", "plan", "--factory", "f1", "--step", "s1", "--task", "t", "--done-test", "true", "--builder", "b1")
    assert code == 0, out
    mid = out["mission"]["id"]
    assert rmf("tac", "receipt", "--id", mid)[1]["error"]["code"] == "NOT_VERIFIED"
    assert rmf("tac", "ship-stop", "--id", mid)[0] == 0
    assert rmf("tac", "run", "--id", mid)[0] == 0
    log_before = (tmp_path / "tac.log").read_text()
    code, out = rmf("tac", "verify", "--id", mid, "--verifier", "b1")
    assert code == 3 and out["error"]["code"] == "SELF_VERIFY_FORBIDDEN"
    assert (tmp_path / "tac.log").read_text() == log_before  # bin not called
    assert rmf("tac", "verify", "--id", mid, "--verifier", "g1")[0] == 0
    code, out = rmf("tac", "receipt", "--id", mid)
    assert code == 0 and len(out["mission"]["receipt_sha256"]) == 64
    assert out["changelog"]["tac_receipt_sha256"] == out["mission"]["receipt_sha256"]
    assert rmf("tac", "list", "--status", "receipted")[1]["missions"][0]["id"] == mid
    assert rmf("tac", "run", "--id", "tac_nope")[0] == 4


def test_tac_verify_failure(rmf, tmp_path, monkeypatch):
    _facs(rmf, tmp_path, "f1")
    monkeypatch.setenv("RMF_TAC_BIN", str(_fake_tac(tmp_path, fail_verify=True)))
    mid = rmf("tac", "plan", "--factory", "f1", "--step", "s", "--task", "t", "--done-test", "true", "--builder", "b")[1]["mission"]["id"]
    rmf("tac", "ship-stop", "--id", mid); rmf("tac", "run", "--id", mid)
    assert rmf("tac", "verify", "--id", mid, "--verifier", "g")[0] == 1
    assert rmf("tac", "list")[1]["missions"][0]["status"] == "failed"


def test_mcp(tmp_path):
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "rmf_lead_status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "rmf_owner_show", "arguments": {"factory": "nope"}}},
    ]
    cp = run_subprocess("mcp", input_text="\n".join(json.dumps(r) for r in reqs) + "\n")
    lines = [json.loads(x) for x in cp.stdout.splitlines()]
    assert [x["id"] for x in lines] == [1, 2, 3, 4]
    assert "tools" in lines[0]["result"]["capabilities"]
    schema = json.loads(run_subprocess("schema", "--json").stdout)
    names = {t["name"] for t in lines[1]["result"]["tools"]}
    assert names == {c["mcp_name"] for c in schema["commands"]}
    assert all("readOnlyHint" in t["annotations"] for t in lines[1]["result"]["tools"])
    assert lines[2]["result"]["isError"] is False
    assert lines[3]["result"]["isError"] is True
