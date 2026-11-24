"""Step 9: lead agent status rollup and cron registry."""
from __future__ import annotations

from conftest import make_factories


def test_status_rollup_and_cron_registry(rmf, tmp_path):
    make_factories(rmf, tmp_path, "f1", "f2")
    rmf("mailbox", "send", "--from", "f1", "--to", "f2", "--subject", "s", "--body", "b")
    assert rmf("lead", "cron-set", "--factory", "f1", "--name", "n", "--schedule", "0 3 * * *", "--command", "x")[0] == 0
    assert rmf("lead", "cron-set", "--factory", "f1", "--name", "n", "--schedule", "0 4 * * *", "--command", "y")[1]["cron"]["schedule_expr"] == "0 4 * * *"
    assert rmf("lead", "cron-set", "--factory", "f1", "--name", "b", "--schedule", "bad", "--command", "x")[0] == 2
    st = rmf("lead", "status")[1]
    assert st["factories_by_state"]["empty_stamped"] == 2 and st["unread_mail"] == 1 and st["enabled_crons"] == 1
    assert len(rmf("lead", "crons")[1]["crons"]) == 1
    assert rmf("lead", "cron-remove", "--factory", "f1", "--name", "n")[0] == 0
    assert rmf("lead", "cron-remove", "--factory", "f1", "--name", "n")[0] == 4
