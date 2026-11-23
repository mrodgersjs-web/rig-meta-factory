---
paths:
  - "src/rig_meta_factory/**/*.py"
---

# CLI & MCP Contract Rules

1. When `--json` is set, write ONLY a single JSON object to stdout; never print progress text to stdout.
2. Exit codes: `0` success/dry-run, `1` runtime error, `2` usage/validation error, `3` policy/Gate-D/self-verify block, `4` not found.
3. All `subprocess.run` calls must pass an explicit list of args with `shell=False`.
4. All SQLite writes must execute inside a transaction (`with conn:`) with `PRAGMA foreign_keys = ON`.
