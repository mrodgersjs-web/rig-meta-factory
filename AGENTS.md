# AGENTS.md — rig-meta-factory

- Read `CLAUDE.md` and `.claude/rules/cli-contract.md` before editing code.
- Use `.venv/bin/pytest` and `.venv/bin/rmf` for all verification commands.
- Never run tests against `~/.rig/meta-factory/registry.db`; always set `RMF_DB_PATH` to a temp path.
- Never use `shell=True` in `subprocess.run`.
