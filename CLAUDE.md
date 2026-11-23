# rig-meta-factory

Local BAMA-compliant meta-factory CLI and stdio MCP server (`rmf`).

## Rules
- Follow the build order in `docs/BLUEPRINT.md` one step at a time.
- Keep zero third-party runtime dependencies; Python standard library only under `src/rig_meta_factory/`.
- Every CLI command supports `--json`, emits exactly one JSON object on stdout, and obeys BAMA exit codes (`0` ok, `1` runtime, `2` usage, `3` gate/policy, `4` not found).
- Never bypass Gate-D (`--yes` + matching `payload_sha256` approval) on destructive commands.
- Never allow `verifier_id == builder_id` in `tac_bridge.py`.
- Run `.venv/bin/pytest -q` before every checkpoint commit.
