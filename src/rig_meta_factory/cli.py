"""BAMA-compliant CLI entry point for rig-meta-factory (`rmf`).

Commands are declared as :class:`CommandSpec` entries (name, flags, handler). The same
specs drive argparse parsing, ``rmf schema``/``rmf help --json`` and the MCP tools, so the
CLI and MCP server always expose exactly the same surface.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from rig_meta_factory import __version__
from rig_meta_factory.errors import (
    EXIT_OK,
    RmfError,
    RuntimeRmfError,
    UsageRmfError,
    emit_json_error,
)


def _default_db_path() -> Path:
    env_db = os.environ.get("RMF_DB_PATH", "").strip()
    if env_db:
        return Path(env_db).expanduser()
    app_store = os.environ.get("APP_STORE", "").strip()
    if app_store:
        return Path(f"{app_store}.db").expanduser()
    return Path("~/.rig/meta-factory/registry.db").expanduser()


class RmfArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that raises UsageRmfError instead of printing to stderr and exiting."""

    def error(self, message: str) -> None:  # pragma: no cover - exercised via CLI
        raise UsageRmfError(message, code="USAGE_ERROR")


@dataclass(frozen=True)
class FlagSpec:
    """One CLI flag. ``name`` is the snake_case parameter name (flag is ``--name-with-dashes``)."""

    name: str
    type: str = "string"  # string | integer | boolean
    required: bool = False
    description: str = ""
    default: Any = None
    choices: tuple[str, ...] | None = None
    cli_flag: str | None = None  # override, e.g. "--from" for parameter from_factory

    @property
    def flag(self) -> str:
        return self.cli_flag or "--" + self.name.replace("_", "-")

    def schema(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "description": self.description or self.name}
        if self.choices:
            out["enum"] = list(self.choices)
        return out


@dataclass(frozen=True)
class CommandSpec:
    name: str
    summary: str
    example: str
    read_only: bool
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    flags: tuple[FlagSpec, ...] = field(default_factory=tuple)
    destructive: bool = False

    @property
    def mcp_name(self) -> str:
        return "rmf_" + self.name.replace(" ", "_").replace("-", "_")

    @property
    def required(self) -> list[str]:
        return [f.name for f in self.flags if f.required]

    @property
    def properties(self) -> dict[str, dict[str, Any]]:
        return {f.name: f.schema() for f in self.flags}

    @property
    def description(self) -> str:
        return f"{self.summary} Example: {self.example}"

    def to_schema_entry(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mcp_name": self.mcp_name,
            "description": self.description,
            "example": self.example,
            "read_only": self.read_only,
            "readOnly": self.read_only,
            "destructive": self.destructive,
            "required": self.required,
            "inputSchema": {
                "type": "object",
                "properties": self.properties,
                "required": self.required,
            },
        }


def _handle_status(_params: dict[str, Any]) -> dict[str, Any]:
    db_path = _default_db_path()
    schema_version = 0
    counts: dict[str, int] = {"templates": 0, "factories": 0, "pending_approvals": 0}
    wal = False
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                wal = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
                cur = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
                )
                if cur.fetchone() is not None:
                    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
                    schema_version = int(row[0]) if row else 0
                for key, table, where in (
                    ("templates", "templates", ""),
                    ("factories", "factories", ""),
                    ("pending_approvals", "gate_approvals", " WHERE status = 'pending'"),
                ):
                    tcur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
                    if tcur.fetchone() is not None:
                        crow = conn.execute(f"SELECT COUNT(*) FROM {table}{where}").fetchone()
                        counts[key] = int(crow[0]) if crow else 0
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise RuntimeRmfError(f"Failed reading SQLite status: {exc}") from exc
    return {
        "ok": True,
        "version": __version__,
        "db_path": str(db_path),
        "schema_version": schema_version,
        "wal": wal,
        "counts": counts,
    }


def _core_specs() -> list[CommandSpec]:
    return [
        CommandSpec(
            name="status",
            summary="Show rig-meta-factory health, DB path, schema version, and counts.",
            example="rmf status --json",
            read_only=True,
            handler=_handle_status,
        ),
        CommandSpec(
            name="schema",
            summary="Emit the machine-readable BAMA command manifest.",
            example="rmf schema --json",
            read_only=True,
            handler=lambda _params: build_schema_manifest(),
        ),
    ]


def _module_specs() -> list[CommandSpec]:
    """Collect specs from domain modules (imported lazily to avoid import cycles)."""
    specs: list[CommandSpec] = []
    from rig_meta_factory import command_defs

    specs.extend(command_defs.all_specs())
    return specs


def get_command_specs() -> list[CommandSpec]:
    return _core_specs() + _module_specs()


def build_schema_manifest() -> dict[str, Any]:
    specs = get_command_specs()
    return {
        "name": "rmf",
        "version": __version__,
        "commands": [spec.to_schema_entry() for spec in specs],
    }


def _format_human_help() -> str:
    lines = [
        f"rmf {__version__} - BAMA-compliant meta-factory CLI and stdio MCP server",
        "",
        "Usage:",
        "  rmf <noun> <verb> [flags] [--json]",
        "  rmf status|schema|help|mcp [--json]",
        "",
        "Commands:",
    ]
    for spec in get_command_specs():
        ro = "read-only" if spec.read_only else ("destructive/Gate-D" if spec.destructive else "write")
        lines.append(f"  {spec.name:<24} [{ro}] {spec.summary}")
        lines.append(f"    Example: {spec.example}")
    lines.append(f"  {'mcp':<24} [server] Run the stdio JSON-RPC 2.0 MCP server. Example: rmf mcp")
    return "\n".join(lines)


def _emit_output(payload: dict[str, Any], as_json: bool) -> int:
    if as_json:
        sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    else:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    sys.stdout.flush()
    return EXIT_OK


def _find_spec(tokens: list[str]) -> tuple[CommandSpec, list[str]] | None:
    specs = {s.name: s for s in get_command_specs()}
    if len(tokens) >= 2 and f"{tokens[0]} {tokens[1]}" in specs:
        return specs[f"{tokens[0]} {tokens[1]}"], tokens[2:]
    if tokens and tokens[0] in specs:
        return specs[tokens[0]], tokens[1:]
    return None


def _mode(spec: CommandSpec) -> str:
    if spec.read_only:
        return "read-only"
    return "destructive: dry-run until a Gate-D item is approved" if spec.destructive else "write"


def _command_help(spec: CommandSpec) -> tuple[str, dict[str, Any]]:
    """Human text and JSON payload for ``rmf <command> --help``."""
    lines = [
        f"rmf {spec.name}",
        f"  {spec.summary}",
        "",
        "Usage:",
        f"  {spec.example}",
        "",
        f"Flags ({_mode(spec)}; add --json for one JSON object):",
    ]
    for f in spec.flags:
        bits = [f.type, "required" if f.required else "optional"]
        if f.choices:
            bits.append("one of " + ", ".join(f.choices))
        if f.default not in (None, False, ""):
            bits.append(f"default {f.default}")
        lines.append(f"  {f.flag:<26} {'; '.join(bits)}: {f.description or f.name}")
    if not spec.flags:
        lines.append("  (no flags)")
    payload = {
        "help": "command",
        "command": spec.to_schema_entry(),
        "flags": [
            {
                "flag": f.flag,
                "name": f.name,
                "type": f.type,
                "required": f.required,
                "description": f.description or f.name,
                "default": f.default,
                "choices": list(f.choices or ()),
            }
            for f in spec.flags
        ],
    }
    return "\n".join(lines), payload


def _help_for(words: list[str]) -> tuple[str, dict[str, Any]]:
    """Resolve ``rmf help <words>`` to one command's help or one noun's command list."""
    found = _find_spec(words)
    if found is not None:
        spec, leftover = found
        if leftover:
            raise UsageRmfError(f"Unexpected arguments after help: {' '.join(words)}", code="USAGE_ERROR")
        return _command_help(spec)
    noun = words[0]
    if noun == "mcp":
        text = (
            "rmf mcp\n"
            "  Run the stdio JSON-RPC 2.0 MCP server (initialize, tools/list, tools/call).\n\n"
            "Usage:\n"
            "  rmf mcp\n"
        )
        payload: dict[str, Any] = {
            "help": "command",
            "command": {
                "name": "mcp",
                "description": (
                    "Run the stdio JSON-RPC 2.0 MCP server (initialize, tools/list, tools/call); "
                    "every command is one tool named rmf_<noun>_<verb>."
                ),
                "example": "rmf mcp",
                "read_only": False,
                "readOnly": False,
                "destructive": False,
                "required": [],
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
            "flags": [],
        }
        return text, payload
    group = [s for s in get_command_specs() if s.name.split(" ", 1)[0] == noun]
    if not group:
        raise UsageRmfError(f"Unknown command: {' '.join(words)}", code="USAGE_ERROR")
    lines = [f"rmf {noun} <verb> [flags] [--json]:", ""]
    for spec in group:
        lines.append(f"  {spec.name:<26} [{_mode(spec)}] {spec.summary}")
        lines.append(f"    Example: {spec.example}")
    return "\n".join(lines), {
        "help": "noun",
        "noun": noun,
        "commands": [spec.to_schema_entry() for spec in group],
    }


def _emit_help(helped: tuple[str, dict[str, Any]], as_json: bool) -> int:
    if as_json:
        return _emit_output(helped[1], as_json=True)
    sys.stdout.write(helped[0] + "\n")
    sys.stdout.flush()
    return EXIT_OK


def parse_params(spec: CommandSpec, rest: list[str]) -> dict[str, Any]:
    parser = RmfArgumentParser(prog=f"rmf {spec.name}", add_help=False)
    for f in spec.flags:
        kwargs: dict[str, Any] = {"dest": f.name}
        if f.type == "boolean":
            kwargs["action"] = "store_true"
            kwargs["default"] = bool(f.default)
        else:
            kwargs["default"] = f.default
            if f.type == "integer":
                kwargs["type"] = int
            if f.choices:
                kwargs["choices"] = list(f.choices)
        parser.add_argument(f.flag, **kwargs)
    ns = parser.parse_args(rest)
    params = vars(ns)
    missing = [f.flag for f in spec.flags if f.required and params.get(f.name) in (None, "")]
    if missing:
        raise UsageRmfError(f"Missing required flag(s): {', '.join(missing)}")
    return params


def run_command(spec: CommandSpec, params: dict[str, Any]) -> dict[str, Any]:
    """Validate required params and invoke the handler (shared by CLI and MCP)."""
    full = {f.name: f.default for f in spec.flags}
    full.update({k: v for k, v in params.items() if v is not None})
    missing = [f.name for f in spec.flags if f.required and full.get(f.name) in (None, "")]
    if missing:
        raise UsageRmfError(f"Missing required parameter(s): {', '.join(missing)}")
    return spec.handler(full)


def _dispatch(tokens: list[str], as_json: bool) -> int:
    if not tokens or tokens[0] in ("help", "-h", "--help"):
        if len(tokens) > 1:
            return _emit_help(_help_for(tokens[1:]), as_json)
        if as_json:
            return _emit_output(build_schema_manifest(), as_json=True)
        sys.stdout.write(_format_human_help() + "\n")
        sys.stdout.flush()
        return EXIT_OK

    if tokens[0] == "mcp":
        from rig_meta_factory.mcp_server import serve_mcp

        return serve_mcp()

    found = _find_spec(tokens)
    if found is None:
        raise UsageRmfError(f"Unknown command: {' '.join(tokens)}", code="USAGE_ERROR")
    spec, rest = found
    if any(arg in ("-h", "--help") for arg in rest):
        return _emit_help(_command_help(spec), as_json)
    params = parse_params(spec, rest)
    return _emit_output(run_command(spec, params), as_json=as_json)


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in raw_args
    filtered = [arg for arg in raw_args if arg != "--json"]

    try:
        return _dispatch(filtered, as_json=as_json)
    except RmfError as err:
        if as_json:
            return emit_json_error(err, stream=sys.stdout)
        sys.stderr.write(f"error [{err.code}]: {err.message}\n")
        sys.stderr.flush()
        return err.exit_code
    except Exception as exc:  # pragma: no cover - defensive runtime wrapper
        wrapped = RuntimeRmfError(str(exc))
        if as_json:
            return emit_json_error(wrapped, stream=sys.stdout)
        sys.stderr.write(f"error [{wrapped.code}]: {wrapped.message}\n")
        sys.stderr.flush()
        return wrapped.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
