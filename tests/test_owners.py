"""Tests for Step 6: owner agents and changelogs."""

from __future__ import annotations


def _factory(rmf, tmp_path, slug="f1"):
    if rmf("template", "list")[1]["templates"] == []:
        rmf("template", "init", "--slug", "base", "--name", "B", "--root", str(tmp_path / "tpl"))
    assert rmf("factory", "create", "--slug", slug, "--name", slug, "--template", "base", "--dest", str(tmp_path / slug))[0] == 0


def test_owner_upsert(rmf, tmp_path):
    _factory(rmf, tmp_path)
    code, out = rmf("owner", "set", "--factory", "f1", "--agent", "owner-1", "--mission", "Ship it")
    assert code == 0 and out["owner"]["agent_name"] == "owner-1" and out["owner"]["status"] == "active"
    first_id = out["owner"]["id"]
    code, out = rmf("owner", "set", "--factory", "f1", "--agent", "owner-2", "--mission", "New", "--status", "paused")
    assert code == 0 and out["owner"]["agent_name"] == "owner-2" and out["owner"]["status"] == "paused"
    assert out["owner"]["id"] == first_id
    assert rmf("owner", "show", "--factory", "f1")[1]["owner"]["mission"] == "New"
    assert rmf("owner", "set", "--factory", "f1", "--agent", "a", "--mission", "m", "--status", "bad")[0] == 2


def test_changelog_ordering_newest_first(rmf, tmp_path):
    _factory(rmf, tmp_path)
    rmf("owner", "changelog-add", "--factory", "f1", "--author", "a", "--summary", "first")
    code, out = rmf("owner", "changelog-add", "--factory", "f1", "--author", "a", "--summary", "second",
                    "--git-sha", "abc123", "--tac-receipt-sha256", "f" * 64)
    assert code == 0 and out["entry"]["git_sha"] == "abc123"
    code, out = rmf("owner", "changelog-list", "--factory", "f1")
    assert [e["summary"] for e in out["entries"]] == ["second", "first"]
    assert out["entries"][0]["tac_receipt_sha256"] == "f" * 64
    assert len(rmf("owner", "changelog-list", "--factory", "f1", "--limit", "1")[1]["entries"]) == 1


def test_unknown_factory_and_missing_owner(rmf, tmp_path):
    code, out = rmf("owner", "show", "--factory", "unknown")
    assert code == 4 and out["error"]["code"] == "NOT_FOUND"
    assert rmf("owner", "changelog-add", "--factory", "unknown", "--author", "a", "--summary", "s")[0] == 4
    assert rmf("owner", "changelog-list", "--factory", "unknown")[0] == 4
    _factory(rmf, tmp_path)
    assert rmf("owner", "show", "--factory", "f1")[0] == 4  # no owner yet
