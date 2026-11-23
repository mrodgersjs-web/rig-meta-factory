"""Tests for Step 2: SQLite registry and WAL migration runner."""

from __future__ import annotations

import sqlite3
import pytest
from pathlib import Path

from rig_meta_factory.db import connect, init_db, new_id, utc_now_iso


def test_init_db_creates_parent_wal_and_tables(tmp_path: Path) -> None:
    db_file = tmp_path / "nested" / "dir" / "registry.db"
    assert not db_file.exists()

    version = init_db(db_file)
    assert version == 1
    assert db_file.exists()

    conn = connect(db_file)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
        assert mode == "wal"

        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {
            "schema_migrations",
            "templates",
            "template_revisions",
            "factories",
            "owner_agents",
            "owner_changelogs",
            "backports",
            "mailbox_messages",
            "factory_crons",
            "gate_approvals",
            "tac_missions",
        }
        assert expected.issubset(tables)
    finally:
        conn.close()


def test_idempotent_double_init_preserves_rows(tmp_path: Path) -> None:
    db_file = tmp_path / "registry.db"
    assert init_db(db_file) == 1

    conn = connect(db_file)
    try:
        now = utc_now_iso()
        with conn:
            conn.execute(
                """
                INSERT INTO templates (id, slug, name, root_path, current_revision_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("tpl_1", "starter", "Starter", "/tmp/starter", None, now, now),
            )
    finally:
        conn.close()

    assert init_db(db_file) == 1

    conn2 = connect(db_file)
    try:
        row = conn2.execute("SELECT slug, name FROM templates WHERE id = 'tpl_1'").fetchone()
        assert row is not None
        assert row["slug"] == "starter"
        assert row["name"] == "Starter"
    finally:
        conn2.close()


def test_foreign_key_and_state_check_constraints(tmp_path: Path) -> None:
    db_file = tmp_path / "registry.db"
    init_db(db_file)

    conn = connect(db_file)
    try:
        now = utc_now_iso()
        # Violate FK on template_revisions
        with pytest.raises(sqlite3.IntegrityError):
            with conn:
                conn.execute(
                    """
                    INSERT INTO template_revisions
                    (id, template_id, version, manifest_sha256, manifest_json, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (new_id("rev"), "nonexistent_tpl", 1, "abc", "{}", "", now),
                )

        # Insert valid template + revision, then violate factories.state CHECK constraint
        with conn:
            conn.execute(
                """
                INSERT INTO templates (id, slug, name, root_path, current_revision_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("tpl_1", "starter", "Starter", "/tmp/starter", "rev_1", now, now),
            )
            conn.execute(
                """
                INSERT INTO template_revisions
                (id, template_id, version, manifest_sha256, manifest_json, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("rev_1", "tpl_1", 1, "abc", "{}", "", now),
            )

        with pytest.raises(sqlite3.IntegrityError):
            with conn:
                conn.execute(
                    """
                    INSERT INTO factories
                    (id, slug, name, repo_path, template_id, template_revision_id, state, sssf_installed, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("fac_1", "f1", "F1", "/tmp/f1", "tpl_1", "rev_1", "invalid_state", 0, now, now),
                )

        count = conn.execute("SELECT COUNT(*) FROM factories").fetchone()[0]
        assert count == 0
    finally:
        conn.close()
