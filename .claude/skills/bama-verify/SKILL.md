---
name: bama-verify
description: Run BAMA CLI and MCP contract verification against rig-meta-factory.
---

## When to use
After any change to `src/rig_meta_factory/cli.py`, `errors.py`, or `mcp_server.py`.

## Steps
1. Run `.venv/bin/pytest -q tests/test_cli_contract.py tests/test_mcp_server.py`.
2. Run `RMF_DB_PATH="/tmp/rmf-bama-check.db" /Users/rig128gb/Developer/bama/skills/code/agent-cli/scripts/check-agent-cli.sh .venv/bin/rmf`.
3. Clean up `/tmp/rmf-bama-check.db*`.

## Verify
Both commands exit `0` with zero failed assertions.

## Do not
Do not run `check-agent-cli.sh` against the live `~/.rig/meta-factory/registry.db`.
