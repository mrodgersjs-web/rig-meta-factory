"""Tests for Step 4: template store and revision manifests."""

from __future__ import annotations

from pathlib import Path

from rig_meta_factory.templates import compute_dir_manifest, manifest_sha256


def _init(rmf, tmp_path: Path, slug="base"):
    root = tmp_path / f"tpl-{slug}"
    code, out = rmf("template", "init", "--slug", slug, "--name", "Base Factory", "--root", str(root))
    assert code == 0, out
    return root, out


def test_init_creates_files_and_revision_1(rmf, tmp_path):
    root, out = _init(rmf, tmp_path)
    for f in ("README.md", "CLAUDE.md", ".rmf-template.json"):
        assert (root / f).is_file()
    assert out["revision"]["version"] == 1
    assert out["revision"]["manifest_sha256"] == manifest_sha256(compute_dir_manifest(root))
    assert out["template"]["current_revision_id"] == out["revision"]["id"]


def test_list_and_show(rmf, tmp_path):
    _init(rmf, tmp_path)
    code, out = rmf("template", "list")
    assert code == 0 and out["templates"][0]["slug"] == "base" and out["templates"][0]["current_version"] == 1
    code, out = rmf("template", "show", "--slug", "base")
    assert code == 0 and "README.md" in out["current_revision"]["manifest"]
    assert rmf("template", "show", "--slug", "nope")[0] == 4


def test_bump_after_edit_creates_v2(rmf, tmp_path):
    root, first = _init(rmf, tmp_path)
    (root / "NEW.md").write_text("hello\n")
    code, out = rmf("template", "bump", "--slug", "base", "--notes", "add NEW")
    assert code == 0 and out["revision"]["version"] == 2
    assert out["revision"]["manifest_sha256"] != first["revision"]["manifest_sha256"]
    _, shown = rmf("template", "show", "--slug", "base")
    assert shown["template"]["current_revision_id"] == out["revision"]["id"]
    assert len(shown["revisions"]) == 2


def test_bump_no_changes_and_duplicate_slug(rmf, tmp_path):
    root, _ = _init(rmf, tmp_path)
    code, out = rmf("template", "bump", "--slug", "base")
    assert code == 2 and out["error"]["code"] == "NO_CHANGES"
    assert len(rmf("template", "show", "--slug", "base")[1]["revisions"]) == 1
    code, out = rmf("template", "init", "--slug", "base", "--name", "x", "--root", str(tmp_path / "other"))
    assert code == 2 and out["error"]["code"] == "DUPLICATE_SLUG"
    code, out = rmf("template", "init", "--slug", "Bad Slug", "--name", "x", "--root", str(tmp_path / "o2"))
    assert code == 2


def test_manifest_ignores_git(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "x").write_text("1")
    (tmp_path / "a.txt").write_text("a")
    assert list(compute_dir_manifest(tmp_path)) == ["a.txt"]
