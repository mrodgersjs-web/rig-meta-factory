"""BAMA error hierarchy and JSON error formatting for rig-meta-factory."""

from __future__ import annotations

import json
import sys
from typing import TextIO

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_POLICY = 3
EXIT_NOT_FOUND = 4


class RmfError(Exception):
    """Base structured CLI/MCP exception mapped to a BAMA exit code."""

    default_code = "RUNTIME_ERROR"
    default_exit_code = EXIT_RUNTIME

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.exit_code = self.default_exit_code if exit_code is None else exit_code

    def to_dict(self) -> dict[str, object]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "exit_code": self.exit_code,
            }
        }


class RuntimeRmfError(RmfError):
    """Exit 1: internal, filesystem, SQLite, or subprocess runtime failure."""

    default_code = "RUNTIME_ERROR"
    default_exit_code = EXIT_RUNTIME


class UsageRmfError(RmfError):
    """Exit 2: invalid command, missing flag, or validation failure."""

    default_code = "USAGE_ERROR"
    default_exit_code = EXIT_USAGE


class PolicyRmfError(RmfError):
    """Exit 3: Gate-D approval required, SHA-256 mismatch, or self-verify block."""

    default_code = "GATE_D_REQUIRED"
    default_exit_code = EXIT_POLICY


class NotFoundRmfError(RmfError):
    """Exit 4: unknown template, factory, owner, backport, message, cron, or mission."""

    default_code = "NOT_FOUND"
    default_exit_code = EXIT_NOT_FOUND


def emit_json_error(err: RmfError, stream: TextIO | None = None) -> int:
    """Write a single JSON error envelope to stdout (or provided stream) and return exit code."""
    target = sys.stdout if stream is None else stream
    target.write(json.dumps(err.to_dict(), sort_keys=True) + "\n")
    target.flush()
    return err.exit_code
