"""SQLite registry connection, WAL configuration, and idempotent schema migration runner."""

from __future__ import annotations

import importlib.resources
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp with microsecond precision for deterministic ordering."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_id(prefix: str) -> str:
    """Generate a compact unique identifier with a domain prefix."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def resolve_db_path(db_path: Path | str | None = None) -> Path:
    """Resolve the SQLite database path from explicit argument, RMF_DB_PATH, APP_STORE, or default."""
    if db_path is not None:
        return Path(db_path).expanduser()
    env_db = os.environ.get("RMF_DB_PATH", "").strip()
    if env_db:
        return Path(env_db).expanduser()
    app_store = os.environ.get("APP_STORE", "").strip()
    if app_store:
        return Path(f"{app_store}.db").expanduser()
    return Path("~/.rig/meta-factory/registry.db").expanduser()


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection configured with WAL, foreign keys, busy timeout, and Row factory."""
    resolved = resolve_db_path(db_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(resolved), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _load_schema_sql() -> str:
    schema_ref = importlib.resources.files("rig_meta_factory").joinpath("schema.sql")
    return schema_ref.read_text(encoding="utf-8")


def _split_sql_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    for chunk in sql_text.split(";"):
        lines = [
            line
            for line in chunk.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        stmt = "\n".join(lines).strip()
        if stmt:
            statements.append(stmt)
    return statements


def init_db(db_path: Path | str | None = None) -> int:
    """Idempotently initialize the SQLite registry schema and return the active schema version."""
    conn = connect(db_path)
    try:
        sql_text = _load_schema_sql()
        statements = _split_sql_statements(sql_text)
        with conn:
            for stmt in statements:
                conn.execute(stmt)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_now_iso()),
            )
        row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
        return int(row[0]) if row else SCHEMA_VERSION
    finally:
        conn.close()
