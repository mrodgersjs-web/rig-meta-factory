"""Step 7: backports capture/apply/propagate with Gate-D."""
from __future__ import annotations


def _setup(rmf, tmp_path):
    tpl = tmp_path / "tpl"; tpl.mkdir()
    (tpl / "README.md").write_text("hello\n")
    assert rmf("template", "init", "--slug", "base", "--name", "B", "--root", str(tpl))[0] == 0
    for s in ("f1", "f2"):
        assert rmf("factory", "create", "--slug", s, "--name", s, "--template", "base", "--dest", str(tmp_path / s))[0] == 0
    return tpl


def test_backport_full_cycle(rmf, tmp_path):
    tpl = _setup(rmf, tmp_path)
    (tmp_path / "f1" / "README.md").write_text("hello\nworld\n")
    code, out = rmf("backport", "capture", "--factory", "f1", "--title", "add world")
    assert code == 0, out
    bid = out["backport"]["id"]
    assert rmf("backport", "list", "--status", "captured")[1]["backports"][0]["id"] == bid
    code, out = rmf("backport", "apply", "--id", bid)
    assert code == 0, out
    assert (tpl / "README.md").read_text() == "hello\nworld\n"
    code, dry = rmf("backport", "propagate", "--id", bid, "--targets", "f2")
    assert code == 0 and dry["dry_run"] is True
    assert (tmp_path / "f2" / "README.md").read_text() == "hello\n"
    code, out = rmf("backport", "propagate", "--id", bid, "--targets", "f2", "--yes")
    assert code == 3
    import json
    code, q = rmf("gate", "queue", "--action-type", "backport_propagate", "--target", bid,
                  "--payload-json", json.dumps(dry["payload"]), "--requested-by", "t")
    assert code == 0, q
    aid = q["approval"]["id"] if "approval" in q else q["id"]
    sha = dry["payload_sha256"]
    assert rmf("gate", "approve", "--id", aid, "--approver", "mike", "--sha256", sha)[0] == 0
    code, out = rmf("backport", "propagate", "--id", bid, "--targets", "f2", "--yes", "--approval-id", aid)
    assert code == 0, out
    assert (tmp_path / "f2" / "README.md").read_text() == "hello\nworld\n"
    assert rmf("backport", "propagate", "--id", bid, "--targets", "f2", "--yes", "--approval-id", aid)[0] == 3


def test_backport_errors(rmf, tmp_path):
    _setup(rmf, tmp_path)
    assert rmf("backport", "capture", "--factory", "f1", "--title", "x")[0] == 2
    assert rmf("backport", "show", "--id", "bp_nope")[0] == 4
