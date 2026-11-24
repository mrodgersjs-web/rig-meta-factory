"""Step 8: factory-to-factory mailbox."""
from __future__ import annotations

from conftest import make_factories


def test_message_round_trip(rmf, tmp_path):
    make_factories(rmf, tmp_path, "f1", "f2")
    code, out = rmf("mailbox", "send", "--from", "f1", "--to", "f2", "--subject", "hi", "--body", "b")
    assert code == 0 and out["message"]["from_factory"] == "f1"
    mid = out["message"]["id"]
    assert [m["id"] for m in rmf("mailbox", "inbox", "--factory", "f2")[1]["messages"]] == [mid]
    assert rmf("mailbox", "ack", "--id", mid)[1]["message"]["acked_at"]
    assert rmf("mailbox", "inbox", "--factory", "f2")[1]["messages"] == []
    assert len(rmf("mailbox", "inbox", "--factory", "f2", "--status", "all")[1]["messages"]) == 1


def test_send_and_ack_validation(rmf, tmp_path):
    make_factories(rmf, tmp_path, "f1")
    assert rmf("mailbox", "send", "--from", "f1", "--to", "f1", "--subject", "s", "--body", "b")[0] == 2
    assert rmf("mailbox", "send", "--from", "f1", "--to", "zz", "--subject", "s", "--body", "b")[0] == 4
    assert rmf("mailbox", "ack", "--id", "msg_nope")[0] == 4
