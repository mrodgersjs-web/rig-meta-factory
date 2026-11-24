# Blueprint: rig-meta-factory

- **Project slug:** `rig-meta-factory`
- **Target repo path (planned):** `~/Developer/rig-meta-factory`
- **Emission mode:** Single-file mode (11 steps, ≤11 threshold; all workspace and verify-critical files emitted as labelled fenced blocks in §19).
- **Date:** 2026-11-23
- **Status:** Design blueprint — no application code written yet.

---

## 1. Project Overview & Non-Goals

### Vision

`rig-meta-factory` is a local-first meta-factory control plane that turns software factories into repeatable, versioned, governed units ("liquid software"). It maintains a canonical empty factory template, stamps specialized factories from that template (reusing Super Simple Software Factory / SSSF installation), tracks each factory's owner agent mission and changelog, backports validated improvements from running factories back into the template and propagates them out, routes asynchronous messages across factories via a mailbox, rolls up lead-agent status and cron schedules, enforces a human-near-the-loop Gate-D approval queue, and executes every factory change as a governed TAC mission via `~/.rig/bin/rig-tac-os` with a separate verifier and hashed receipt.

### Users

- **Primary operator (Mike Rodgers):** Reviews and approves Gate-D queued actions (destructive factory deletes, template backport propagations, outward dispatches), inspects lead-agent status/cron rollups, and audits TAC receipts.
- **Lead / Owner Agents (Aside, Jake, Owen, Orca builders, MCP clients):** Drive `rmf` via its BAMA-compliant JSON CLI (`rmf <noun> <verb> --json`) or stdio JSON-RPC 2.0 MCP server (`rmf mcp`) to stamp factories, log owner changelogs, exchange mailbox messages, capture backports, and run TAC missions.

### Goals — v1 scope

1. Provide a BAMA-compliant CLI (`rmf <noun> <verb> --json`, exit codes `0`–`4`, structured JSON error envelope on stdout, destructive dry-run without `--yes`) and stdio MCP server (`rmf mcp`).
2. Store all meta-factory state in a single local SQLite database in WAL mode (`RMF_DB_PATH`) with idempotent schema migrations.
3. Manage a canonical versioned empty factory template (`templates` + `template_revisions`) with SHA-256 file manifests.
4. Support full CRUD for factories stamped from a pinned template revision, reusing the local SSSF stamping flow (`~/.aside/u/0/repos/disler/super-simple-software-factory/.claude/skills/sssf`).
5. Track a per-factory owner agent record with mission statement and append-only changelog linked to git commits and TAC receipt hashes.
6. Capture unified diffs from a running factory as backport proposals, apply them to create a new template revision, and propagate approved revisions to target factories.
7. Provide a SQLite-backed factory-to-factory asynchronous mailbox (`send`, `inbox`, `ack`).
8. Provide a lead-agent status rollup and per-factory cron schedule registry (`status`, `crons`, `cron-set`, `cron-remove`).
9. Enforce a Gate-D approval queue requiring exact SHA-256 payload approval plus `--yes` before destructive deletes or multi-factory template propagations execute.
10. Integrate with `~/.rig/bin/rig-tac-os` (`plan`, `ship-stop`, `run`, `verify`, `receipt`) so every factory change runs as a TAC mission with a separate verifier identity and persisted receipt hash.

### Non-Goals — explicitly out of scope for v1

- **No web UI or desktop GUI:** v1 is strictly a headless CLI and stdio MCP server.
- **No remote multi-tenant server or cloud hosting:** v1 runs locally on macOS against a local SQLite file and local git repositories.
- **No autonomous outward network publishing:** `rmf` never pushes to remote git hosts, posts to social channels, or sends emails; outward actions remain behind Gate-D outside v1.
- **No built-in cron daemon:** `rmf` registers and inspects cron definitions in SQLite; OS scheduling (`launchd` / Aside routines) reads the registry rather than `rmf` running a background scheduler loop.
- **No embedded LLM inference:** `rmf` is deterministic Python; LLM planning/building/verification is delegated to `rig-tac-os` and external agents.

### Success metrics

- `check-agent-cli.sh` (from `~/Developer/bama/skills/code/agent-cli/scripts/check-agent-cli.sh`) passes 100% of assertions against `rmf`.
- `bama audit-cli --cli rmf --json` reports `passed: true` with 0 failures.
- 100% of unit and integration tests in `tests/` pass via `pytest` with zero skipped security or Gate-D tests.
- Every destructive command (`factory delete`, `backport propagate`) performs a dry-run (exit `0`, `"dry_run": true`, zero mutations) when `--yes` is omitted, and exits `3` (policy/gate blocked) when `--yes` is passed without a matching Gate-D approval.
- Every TAC mission recorded via `rmf tac receipt` enforces `builder_id != verifier_id` and writes a verifiable SHA-256 receipt link into `owner_changelogs`.

---

## 2. Tech Stack

- **Language & Runtime:** Python (standard library `sqlite3`, `argparse`, `json`, `hashlib`, `subprocess`, `pathlib`, `shutil`, `difflib`, `datetime`, `uuid`). Exact version pinned in §11.
- **Packaging & Environment Manager:** `uv` with `pyproject.toml` (`setuptools` build backend) and `src/` layout. Exact versions pinned in §11.
- **Database:** SQLite with `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000`. Exact version pinned in §11.
- **Test Runner:** `pytest`. Exact version pinned in §11.
- **VCS & Diff Engine:** `git` CLI invoked via `subprocess.run(..., check=False)` for factory repo init, diff capture, and patch apply. Exact version pinned in §11.
- **External Local Integrations:**
  - SSSF skill source at `~/.aside/u/0/repos/disler/super-simple-software-factory/.claude/skills/sssf`.
  - TAC OS CLI at `~/.rig/bin/rig-tac-os`.
  - BAMA verifier script at `~/Developer/bama/skills/code/agent-cli/scripts/check-agent-cli.sh` and `~/.local/bin/bama`.

### Compatibility check

- Python standard-library `sqlite3` on this macOS host links against the local SQLite library verified in §11 and supports WAL mode, `STRICT` tables, `CHECK` constraints, and foreign keys natively.
- Zero third-party runtime dependencies eliminates ABI/wheel conflicts and keeps CLI cold-start under 50ms for MCP tool calls.
- SSSF's `install.py` requires `--python 3.12` via `uv run` on macOS (plus `unset VIRTUAL_ENV` and `xattr -dr com.apple.quarantine` when needed); `rmf` invokes SSSF in a sanitized subprocess environment so `rmf`'s own Python runtime and SSSF's `uv run --python 3.12` never collide.

---

## 3. Directory Structure

```
~/Developer/rig-meta-factory/
├── .claude/
│   ├── rules/
│   │   └── cli-contract.md
│   ├── settings.json
│   └── skills/
│       └── bama-verify/
│           └── SKILL.md
├── .env.example
├── .gitignore
├── AGENTS.md
├── CLAUDE.md
├── pyproject.toml
├── src/
│   └── rig_meta_factory/
│       ├── __init__.py
│       ├── backports.py
│       ├── cli.py
│       ├── db.py
│       ├── errors.py
│       ├── factories.py
│       ├── gate_d.py
│       ├── lead.py
│       ├── mailbox.py
│       ├── mcp_server.py
│       ├── owners.py
│       ├── schema.sql
│       ├── sssf.py
│       ├── tac_bridge.py
│       └── templates.py
└── tests/
    ├── test_backports.py
    ├── test_cli_contract.py
    ├── test_db.py
    ├── test_factories.py
    ├── test_gate_d.py
    ├── test_lead.py
    ├── test_mailbox.py
    ├── test_mcp_server.py
    ├── test_owners.py
    ├── test_tac_bridge.py
    └── test_templates.py
```

---

## 4. Data Model

### Entities

1. `templates` — Named empty factory template roots (`id`, `slug`, `name`, `root_path`, `current_revision_id`, `created_at`, `updated_at`).
2. `template_revisions` — Immutable snapshots of a template (`id`, `template_id`, `version`, `manifest_sha256`, `manifest_json`, `notes`, `source_backport_id`, `created_at`).
3. `factories` — Individual factories stamped from a template revision (`id`, `slug`, `name`, `repo_path`, `template_id`, `template_revision_id`, `state`, `sssf_installed`, `created_at`, `updated_at`). Valid `state` values: `empty_stamped`, `running`, `graduated`, `archived`.
4. `owner_agents` — One owner agent record per factory (`id`, `factory_id`, `agent_name`, `mission`, `status`, `updated_at`).
5. `owner_changelogs` — Append-only change log for each factory (`id`, `factory_id`, `author_agent`, `summary`, `git_sha`, `tac_receipt_sha256`, `created_at`).
6. `backports` — Feature diffs captured from a running factory for backporting into its template (`id`, `source_factory_id`, `template_id`, `title`, `diff_text`, `diff_sha256`, `status`, `applied_revision_id`, `created_at`, `updated_at`). Valid `status`: `captured`, `applied`, `propagated`, `rejected`.
7. `mailbox_messages` — Asynchronous messages exchanged between factories (`id`, `from_factory_id`, `to_factory_id`, `subject`, `body`, `status`, `created_at`, `acked_at`). Valid `status`: `unread`, `acked`.
8. `factory_crons` — Scheduled job definitions owned by factories and surfaced to the lead agent (`id`, `factory_id`, `name`, `schedule_expr`, `command`, `enabled`, `last_run_at`, `last_status`, `updated_at`).
9. `gate_approvals` — Gate-D human-near-the-loop approval queue (`id`, `action_type`, `target_ref`, `payload_json`, `payload_sha256`, `status`, `requested_by`, `approver`, `decision_note`, `created_at`, `decided_at`). Valid `status`: `pending`, `approved`, `rejected`, `consumed`.
10. `tac_missions` — Governed TAC missions run through `rig-tac-os` (`id`, `factory_id`, `step_name`, `task_text`, `done_test`, `builder_id`, `verifier_id`, `plan_path`, `run_path`, `verify_path`, `receipt_path`, `receipt_sha256`, `status`, `created_at`, `updated_at`). Valid `status`: `planned`, `ship_stopped`, `ran`, `verified`, `receipted`, `failed`.

### Relationships

- `templates` 1:N `template_revisions` (`template_revisions.template_id -> templates.id`).
- `templates` 1:N `factories` and `template_revisions` 1:N `factories`.
- `factories` 1:1 `owner_agents` (`UNIQUE(factory_id)`).
- `factories` 1:N `owner_changelogs`.
- `factories` 1:N `backports` (`source_factory_id -> factories.id`), `templates` 1:N `backports`.
- `factories` 1:N `mailbox_messages` (`from_factory_id` and `to_factory_id` both reference `factories.id`).
- `factories` 1:N `factory_crons` (`UNIQUE(factory_id, name)`).
- `factories` 1:N `tac_missions`.

### Indexes

- `idx_template_revisions_template_ver` on `template_revisions(template_id, version)` (UNIQUE).
- `idx_factories_state` on `factories(state)`.
- `idx_owner_changelogs_factory_created` on `owner_changelogs(factory_id, created_at DESC)`.
- `idx_backports_template_status` on `backports(template_id, status)`.
- `idx_mailbox_to_status` on `mailbox_messages(to_factory_id, status, created_at ASC)`.
- `idx_factory_crons_enabled` on `factory_crons(enabled, factory_id)`.
- `idx_gate_approvals_lookup` on `gate_approvals(action_type, target_ref, payload_sha256, status)`.
- `idx_tac_missions_factory_status` on `tac_missions(factory_id, status)`.

### Schema

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS templates (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL,
    current_revision_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS template_revisions (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL REFERENCES templates(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    source_backport_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(template_id, version)
);

CREATE TABLE IF NOT EXISTS factories (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    repo_path TEXT NOT NULL UNIQUE,
    template_id TEXT NOT NULL REFERENCES templates(id),
    template_revision_id TEXT NOT NULL REFERENCES template_revisions(id),
    state TEXT NOT NULL CHECK (state IN ('empty_stamped', 'running', 'graduated', 'archived')),
    sssf_installed INTEGER NOT NULL DEFAULT 0 CHECK (sssf_installed IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS owner_agents (
    id TEXT PRIMARY KEY,
    factory_id TEXT NOT NULL UNIQUE REFERENCES factories(id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,
    mission TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'retired')),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS owner_changelogs (
    id TEXT PRIMARY KEY,
    factory_id TEXT NOT NULL REFERENCES factories(id) ON DELETE CASCADE,
    author_agent TEXT NOT NULL,
    summary TEXT NOT NULL,
    git_sha TEXT NOT NULL DEFAULT '',
    tac_receipt_sha256 TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backports (
    id TEXT PRIMARY KEY,
    source_factory_id TEXT NOT NULL REFERENCES factories(id) ON DELETE CASCADE,
    template_id TEXT NOT NULL REFERENCES templates(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    diff_text TEXT NOT NULL,
    diff_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('captured', 'applied', 'propagated', 'rejected')),
    applied_revision_id TEXT REFERENCES template_revisions(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mailbox_messages (
    id TEXT PRIMARY KEY,
    from_factory_id TEXT NOT NULL REFERENCES factories(id) ON DELETE CASCADE,
    to_factory_id TEXT NOT NULL REFERENCES factories(id) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unread' CHECK (status IN ('unread', 'acked')),
    created_at TEXT NOT NULL,
    acked_at TEXT
);

CREATE TABLE IF NOT EXISTS factory_crons (
    id TEXT PRIMARY KEY,
    factory_id TEXT NOT NULL REFERENCES factories(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    schedule_expr TEXT NOT NULL,
    command TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    last_run_at TEXT,
    last_status TEXT NOT NULL DEFAULT 'never',
    updated_at TEXT NOT NULL,
    UNIQUE(factory_id, name)
);

CREATE TABLE IF NOT EXISTS gate_approvals (
    id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'consumed')),
    requested_by TEXT NOT NULL,
    approver TEXT,
    decision_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS tac_missions (
    id TEXT PRIMARY KEY,
    factory_id TEXT NOT NULL REFERENCES factories(id) ON DELETE CASCADE,
    step_name TEXT NOT NULL,
    task_text TEXT NOT NULL,
    done_test TEXT NOT NULL,
    builder_id TEXT NOT NULL,
    verifier_id TEXT NOT NULL DEFAULT '',
    plan_path TEXT NOT NULL DEFAULT '',
    run_path TEXT NOT NULL DEFAULT '',
    verify_path TEXT NOT NULL DEFAULT '',
    receipt_path TEXT NOT NULL DEFAULT '',
    receipt_sha256 TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('planned', 'ship_stopped', 'ran', 'verified', 'receipted', 'failed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### Migrations

- `db.init_db(db_path)` opens SQLite with `PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;`, executes `schema.sql` inside a single transaction, and records version `1` in `schema_migrations` (`INSERT OR IGNORE`). Re-running `init_db` is a safe no-op returning the current schema version.

### Seed data

- No pre-seeded factories or fake approvals. `init_db` creates an empty registry. `rmf template init` explicitly creates the first empty factory template on disk and in `templates` + `template_revisions`.

---

## 5. API Design

`rig-meta-factory` exposes two interfaces backed by the exact same Python domain functions: the **BAMA CLI** (`rmf`) and the **stdio MCP server** (`rmf mcp`).

### Conventions

- **Invocation shape:** `rmf <noun> <verb> [flags] [--json]` (plus top-level `rmf status [--json]`, `rmf schema [--json]`, `rmf mcp`).
- **Help:** `rmf help` and `rmf help --json` list every command; `rmf help <noun> [verb]` and `rmf <noun> <verb> --help` describe one noun or one command (text, or one JSON object with `--json`, CLI flag names included); unknown words exit `2`.
- **Exit codes (BAMA contract):**
  - `0` — Success (including dry-run preview when `--yes` is omitted on destructive commands).
  - `1` — Internal/runtime/IO error (DB error, subprocess failure).
  - `2` — Usage / validation error (unknown command, missing required flag, invalid state).
  - `3` — Policy / Gate-D / authorization block (missing approval, SHA-256 mismatch, self-verification attempt `builder_id == verifier_id`).
  - `4` — Not found (unknown template, factory, backport, message, approval, or mission ID).
- **JSON output contract:**
  - When `--json` is passed, stdout emits exactly ONE JSON object and nothing else.
  - On any non-zero exit with `--json`, stdout emits `{"error": {"code": "<UPPER_SNAKE>", "message": "<human text>", "exit_code": <int>}}`.
- **Destructive dry-run contract:**
  - `rmf factory delete`, `rmf backport propagate`, and `rmf gate approve` support `--yes`. Without `--yes`, `factory delete` and `backport propagate` return exit `0` with `{"dry_run": true, "would_affect": ..., "payload_sha256": "<sha256>", "approval_required": true}` and perform zero mutations.
- **MCP contract (`rmf mcp`):**
  - Line-delimited JSON-RPC 2.0 over stdin/stdout supporting `initialize`, `notifications/initialized`, `tools/list`, and `tools/call`.
  - Every command in `rmf schema --json` is exposed as one MCP tool with name matching `[A-Za-z0-9_-]{1,64}` (e.g. `rmf_status`, `rmf_factory_list`, `rmf_factory_create`) and `annotations.readOnlyHint` set accurately (`true` for read-only verbs, `false` for mutating verbs).

### Routes

| Command | MCP Tool Name | Read-Only | Destructive / Gate-D | Purpose |
|---|---|---|---|---|
| `rmf status` | `rmf_status` | yes | no | Health, DB path, schema version, counts |
| `rmf schema` | `rmf_schema` | yes | no | Machine-readable BAMA command manifest |
| `rmf template init` | `rmf_template_init` | no | no | Initialize empty factory template dir + rev 1 |
| `rmf template list` | `rmf_template_list` | yes | no | List all templates and current revisions |
| `rmf template show` | `rmf_template_show` | yes | no | Show template metadata and file manifest |
| `rmf template bump` | `rmf_template_bump` | no | no | Snapshot updated template files as new revision |
| `rmf factory create` | `rmf_factory_create` | no | no | Stamp new factory from template (+ optional SSSF) |
| `rmf factory list` | `rmf_factory_list` | yes | no | List all factories (filterable by `--state`) |
| `rmf factory show` | `rmf_factory_show` | yes | no | Show factory, owner agent, and latest revision |
| `rmf factory update` | `rmf_factory_update` | no | no | Transition state (`empty_stamped` -> `running` -> `graduated` -> `archived`) |
| `rmf factory delete` | `rmf_factory_delete` | no | **yes (Gate-D + `--yes`)** | Remove factory record (and optional dir) |
| `rmf owner set` | `rmf_owner_set` | no | no | Create/update per-factory owner agent + mission |
| `rmf owner show` | `rmf_owner_show` | yes | no | Show owner agent for a factory |
| `rmf owner changelog-add` | `rmf_owner_changelog_add` | no | no | Append changelog entry (with optional git/TAC SHA) |
| `rmf owner changelog-list` | `rmf_owner_changelog_list` | yes | no | List changelog entries for a factory |
| `rmf backport capture` | `rmf_backport_capture` | no | no | Diff running factory against its template revision |
| `rmf backport list` | `rmf_backport_list` | yes | no | List captured/applied/propagated backports |
| `rmf backport show` | `rmf_backport_show` | yes | no | Show full diff and SHA-256 of a backport |
| `rmf backport apply` | `rmf_backport_apply` | no | no | Apply backport diff to template and bump revision |
| `rmf backport propagate` | `rmf_backport_propagate` | no | **yes (Gate-D + `--yes`)** | Propagate template revision to target factories |
| `rmf mailbox send` | `rmf_mailbox_send` | no | no | Send message from one factory to another |
| `rmf mailbox inbox` | `rmf_mailbox_inbox` | yes | no | Read inbox for a factory (`--status unread|all`) |
| `rmf mailbox ack` | `rmf_mailbox_ack` | no | no | Acknowledge a mailbox message |
| `rmf lead status` | `rmf_lead_status` | yes | no | Rollup across factories, owners, mailbox, Gate-D, TAC |
| `rmf lead crons` | `rmf_lead_crons` | yes | no | List all registered factory crons |
| `rmf lead cron-set` | `rmf_lead_cron_set` | no | no | Register or update a factory cron entry |
| `rmf lead cron-remove` | `rmf_lead_cron_remove` | no | no | Remove a factory cron entry |
| `rmf gate queue` | `rmf_gate_queue` | no | no | Enqueue an action for Gate-D approval |
| `rmf gate list` | `rmf_gate_list` | yes | no | List Gate-D queue items (filterable by `--status`) |
| `rmf gate show` | `rmf_gate_show` | yes | no | Show exact payload and SHA-256 of a Gate-D item |
| `rmf gate approve` | `rmf_gate_approve` | no | no | Approve a pending Gate-D item (`--approver`, `--sha256`) |
| `rmf gate reject` | `rmf_gate_reject` | no | no | Reject a pending Gate-D item (`--approver`, `--note`) |
| `rmf tac plan` | `rmf_tac_plan` | no | no | Run `rig-tac-os plan` and record TAC mission |
| `rmf tac ship-stop` | `rmf_tac_ship_stop` | no | no | Run `rig-tac-os ship-stop` gate on a mission |
| `rmf tac run` | `rmf_tac_run` | no | no | Run `rig-tac-os run` for a planned mission |
| `rmf tac await` | `rmf_tac_await` | no | no | Wait again for a builder that outlived the run wait limit, or an interrupted `tac run` |
| `rmf tac verify` | `rmf_tac_verify` | no | no | Run `rig-tac-os verify` with `verifier_id != builder_id` |
| `rmf tac receipt` | `rmf_tac_receipt` | no | no | Run `rig-tac-os receipt`, hash it, link to owner changelog |
| `rmf tac list` | `rmf_tac_list` | yes | no | List TAC missions for a factory or all factories |

### Critical endpoints — full detail

1. **`rmf factory create --slug <slug> --name <name> --template <tpl_slug> --dest <dir> [--install-sssf] --json`**
   - Validates `slug` (`^[a-z0-9][a-z0-9-]{1,48}$`), resolves template's `current_revision_id`, copies template files into `<dest>`, runs `git init -b main && git add -A && git commit -m "stamp: <tpl_slug>@v<ver>"`, optionally copies SSSF skill from `RMF_SSSF_SOURCE` and invokes `uv run --python 3.12 .claude/skills/sssf/scripts/install.py` with `VIRTUAL_ENV` stripped, and inserts `factories` row with `state='empty_stamped'`.
   - Returns `200`-equivalent exit `0` JSON: `{"factory": {...}, "template_revision": {...}, "sssf_installed": true|false}`.
2. **`rmf factory delete --slug <slug> [--remove-files] [--yes] [--approval-id <id>] --json`**
   - Computes canonical payload `{"action": "factory_delete", "slug": slug, "remove_files": bool}` and its SHA-256.
   - Without `--yes`: returns exit `0` with `{"dry_run": true, "action_type": "factory_delete", "target_ref": slug, "payload_sha256": "...", "approval_required": true}`.
   - With `--yes`: verifies `gate_approvals` row matching `approval_id`, `action_type='factory_delete'`, `target_ref=slug`, `payload_sha256`, and `status='approved'`. If missing or mismatched, exits `3` with `{"error": {"code": "GATE_D_REQUIRED", ...}}`. If valid, marks approval `consumed`, deletes DB row (and directory if `--remove-files`), exits `0`.
3. **`rmf backport propagate --id <bp_id> --targets <slug1,slug2> [--yes] [--approval-id <id>] --json`**
   - Without `--yes`: returns exit `0` dry-run showing target factories, `payload_sha256`, and files touched.
   - With `--yes`: requires approved Gate-D record matching `payload_sha256`; applies the backport's new template revision diff to each target factory, updates `factories.template_revision_id`, appends an `owner_changelogs` entry per factory, marks approval `consumed`, and exits `0`.
4. **`rmf tac verify --id <mission_id> --verifier <verifier_id> --json`**
   - Rejects with exit `3` (`SELF_VERIFY_FORBIDDEN`) if `verifier_id.strip() == ""` or `verifier_id == mission.builder_id`, without calling `rig-tac-os`.
   - Invokes `RMF_TAC_BIN verify <run_path> --repo <factory.repo_path> --plan <plan_path>` (`RMF_TAC_BIN` defaults to the first `rig-tac-os` on `PATH`, else `~/.rig/bin/rig-tac-os`), stores the verify record it prints at `verify_path`, and sets the mission to `verified` (exit `0`) only when the done-test passed and the factory repo is unchanged, else `failed` (exit `1`, `TAC_VERIFY_FAILED`).

---

## 6. Frontend Architecture

NOT APPLICABLE — CLI and stdio MCP server with no web or desktop GUI.

---

## 7. Design System

NOT APPLICABLE — CLI and stdio MCP server with no visual UI.

---

## 8. Authentication & Authorization

### Provider and rationale

Local single-operator trust model (macOS filesystem permissions on `RMF_DB_PATH` and target repos) layered with **Gate-D explicit approval records** for high-impact mutations. No OAuth, JWT, or remote identity provider is required because v1 never opens a network socket.

### Flows

- **Gate-D Approval Flow:**
  1. Agent runs a destructive command without `--yes` (dry-run) to obtain `payload_sha256`, or runs `rmf gate queue --action-type <t> --target <ref> --payload-json <json> --requested-by <agent> --json`.
  2. Operator inspects `rmf gate show --id <id> --json` and explicitly runs `rmf gate approve --id <id> --approver mike --sha256 <exact_sha256> --json`.
  3. Agent executes the destructive command with `--yes --approval-id <id>`. `rmf` verifies `status == 'approved'` and `payload_sha256` equality, then atomically flips the approval to `consumed` so approvals cannot be replayed.

### Route protection

- Read-only commands require no approval.
- `factory delete` and `backport propagate` require BOTH `--yes` and an `approved` (unconsumed) `gate_approvals` row matching the exact canonical `payload_sha256`.
- `tac verify` requires `verifier_id != builder_id` to enforce independent verification.

### Roles and permissions

- `operator` (human approver, e.g. `mike`): May approve/reject Gate-D items.
- `lead_agent` / `owner_agent`: May queue Gate-D items, stamp factories, log changelogs, send mailbox messages, capture/apply backports, and run TAC missions; cannot self-verify a TAC mission they built.

### Sessions

NOT APPLICABLE — stateless per-invocation CLI and single-process stdio MCP session.

### Multi-tenancy / row-level isolation

NOT APPLICABLE — single-operator local SQLite registry; factory isolation is enforced by distinct `repo_path` directories and `factory_id` foreign keys.

---

## 9. BUILD ORDER

**Emission mode:** Single-file mode (11 steps, ≤11 threshold).

### Step map

| Step | Title | Files Created / Modified | Depends On |
|---|---|---|---|
| 1 | Scaffold package, CLI shell, and BAMA contract | `src/rig_meta_factory/__init__.py`, `src/rig_meta_factory/errors.py`, `src/rig_meta_factory/cli.py`, `tests/test_cli_contract.py` | §10 Bootstrap, §19.6 configs |
| 2 | Implement SQLite registry and WAL migration runner | `src/rig_meta_factory/db.py`, `src/rig_meta_factory/schema.sql`, `tests/test_db.py` | Step 1 |
| 3 | Implement Gate-D approval queue and SHA-256 enforcement | `src/rig_meta_factory/gate_d.py`, `src/rig_meta_factory/cli.py`, `tests/test_gate_d.py` | Step 2 |
| 4 | Implement empty factory template store and revision manifest | `src/rig_meta_factory/templates.py`, `src/rig_meta_factory/cli.py`, `tests/test_templates.py` | Step 2 |
| 5 | Implement factory CRUD and SSSF stamping engine | `src/rig_meta_factory/sssf.py`, `src/rig_meta_factory/factories.py`, `src/rig_meta_factory/cli.py`, `tests/test_factories.py` | Steps 3, 4 |
| 6 | Implement per-factory owner agent mission and changelog | `src/rig_meta_factory/owners.py`, `src/rig_meta_factory/cli.py`, `tests/test_owners.py` | Step 5 |
| 7 | Implement feature backport capture and template propagation | `src/rig_meta_factory/backports.py`, `src/rig_meta_factory/cli.py`, `tests/test_backports.py` | Steps 3, 4, 5, 6 |
| 8 | Implement factory-to-factory mailbox | `src/rig_meta_factory/mailbox.py`, `src/rig_meta_factory/cli.py`, `tests/test_mailbox.py` | Step 5 |
| 9 | Implement lead agent status rollup and cron registry | `src/rig_meta_factory/lead.py`, `src/rig_meta_factory/cli.py`, `tests/test_lead.py` | Steps 3, 5, 6, 8 |
| 10 | Implement TAC mission integration via rig-tac-os | `src/rig_meta_factory/tac_bridge.py`, `src/rig_meta_factory/cli.py`, `tests/test_tac_bridge.py` | Steps 5, 6 |
| 11 | Implement BAMA stdio MCP server and end-to-end audit | `src/rig_meta_factory/mcp_server.py`, `src/rig_meta_factory/cli.py`, `tests/test_mcp_server.py` | Steps 1–10 |

---

#### Step 1 — Scaffold package, CLI shell, and BAMA contract

**Do**
Create `src/rig_meta_factory/__init__.py` (defining `__version__ = "0.1.0"`), `src/rig_meta_factory/errors.py` (defining `RmfError` hierarchy mapping to BAMA exit codes `1`, `2`, `3`, `4` from §5 and `emit_json_error`), `src/rig_meta_factory/cli.py` (implementing `argparse` dispatch for `rmf status [--json]` and `rmf schema [--json]`, unknown-command exit `2` with JSON error envelope on stdout when `--json` is present), and `tests/test_cli_contract.py` (testing `rmf status --json`, `rmf schema --json`, and unknown command exit `2`). Install the package editable into `.venv` using the `pyproject.toml` from §19.6.

**Done when**
- [ ] WHEN `rmf status --json` is executed THE SYSTEM SHALL exit with code `0` and emit a single valid JSON object on stdout containing keys `ok`, `version`, `db_path`, and `schema_version`.
- [ ] WHEN `rmf schema --json` is executed THE SYSTEM SHALL exit with code `0` and emit a single valid JSON object containing a `commands` array whose entries each declare `name`, `read_only`, and `required`.
- [ ] WHEN an unknown subcommand `rmf nonexistent --json` is executed THE SYSTEM SHALL exit with code `2` and emit a single JSON object on stdout with `error.code == "USAGE_ERROR"` and non-empty `error.message`.

**Verify**
```bash
.venv/bin/pytest -q tests/test_cli_contract.py # expected: exit 0, all tests pass
.venv/bin/rmf status --json | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['ok'] is True" # expected: exit 0
.venv/bin/rmf schema --json | python3 -c "import json,sys; d=json.load(sys.stdin); assert isinstance(d['commands'], list) and len(d['commands']) >= 2" # expected: exit 0
status=0; out=$(.venv/bin/rmf nonexistent --json) || status=$?; test "$status" -eq 2 && printf '%s' "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'error' in d and d['error']['code'] == 'USAGE_ERROR'" # expected: exit 0 (command exited 2 with JSON error)
```

**Checkpoint**
```bash
git add -A && git commit -m "step 1: scaffold-cli-bama-contract"
git tag step-01-scaffold-cli-bama-contract
git rev-parse --verify step-01-scaffold-cli-bama-contract # expected: prints 40-char commit SHA, exit 0
```

---

#### Step 2 — Implement SQLite registry and WAL migration runner

**Do**
Create `src/rig_meta_factory/schema.sql` containing the exact 10 domain tables plus `schema_migrations` and all 8 indexes from §4. Create `src/rig_meta_factory/db.py` implementing `resolve_db_path()` (reading `RMF_DB_PATH` from §10 with default `~/.rig/meta-factory/registry.db`), `connect(db_path)` (setting `WAL`, `foreign_keys=ON`, `busy_timeout=5000`, `row_factory=sqlite3.Row`), and idempotent `init_db(db_path)`. Create `tests/test_db.py` testing fresh DB creation, WAL journal mode, foreign-key enforcement, idempotent double-init, and rejection of invalid `factories.state` values.

**Done when**
- [ ] WHEN `init_db(db_path)` runs against a non-existent path THE SYSTEM SHALL create parent directories, enable SQLite `wal` journal mode, and create all 10 domain tables plus `schema_migrations` at version `1`.
- [ ] WHEN `init_db(db_path)` is called a second time on an initialized database THE SYSTEM SHALL complete without raising an exception, leave all existing rows unchanged, and return schema version `1`.
- [ ] WHEN an insert violates a foreign key or `factories.state` CHECK constraint THE SYSTEM SHALL raise `sqlite3.IntegrityError` and leave the table unchanged.

**Verify**
```bash
.venv/bin/pytest -q tests/test_db.py # expected: exit 0, all tests pass
RMF_DB_PATH="/tmp/rmf-step2-check.db" .venv/bin/python3 -c "from rig_meta_factory.db import init_db, connect; v=init_db(); assert v == 1; c=connect(); assert c.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'" && rm -f /tmp/rmf-step2-check.db /tmp/rmf-step2-check.db-wal /tmp/rmf-step2-check.db-shm # expected: exit 0
```

**Checkpoint**
```bash
git add -A && git commit -m "step 2: sqlite-registry-wal-migrations"
git tag step-02-sqlite-registry-wal-migrations
git rev-parse --verify step-02-sqlite-registry-wal-migrations # expected: prints 40-char commit SHA, exit 0
```

---

#### Step 3 — Implement Gate-D approval queue and SHA-256 enforcement

**Do**
Create `src/rig_meta_factory/gate_d.py` implementing canonical JSON hashing (`canonical_payload_sha256`), `queue_approval`, `list_approvals`, `show_approval`, `approve_item` (requiring `--approver` and exact `--sha256` match), `reject_item`, and `require_and_consume_approval` (raising exit `3` `GATE_D_REQUIRED` when missing/unapproved/mismatched/already consumed). Wire `rmf gate queue|list|show|approve|reject` into `src/rig_meta_factory/cli.py` and register them in `rmf schema`. Create `tests/test_gate_d.py` covering queue, approve, reject, SHA-256 mismatch rejection (exit `3`), and replay prevention after consumption (exit `3`).

**Done when**
- [ ] WHEN `rmf gate queue` is invoked with an action type, target ref, and JSON payload THE SYSTEM SHALL persist a `pending` row in `gate_approvals` with its deterministic `payload_sha256` and return exit `0`.
- [ ] WHEN `rmf gate approve` is invoked with a `--sha256` that does not match the stored `payload_sha256` THE SYSTEM SHALL reject the approval with exit code `3` and leave the row in `pending` status.
- [ ] WHEN `require_and_consume_approval` is called on an `approved` item THE SYSTEM SHALL transition the item to `consumed`, and any subsequent call with the same approval ID SHALL fail with exit code `3`.

**Verify**
```bash
.venv/bin/pytest -q tests/test_gate_d.py # expected: exit 0, all tests pass
RMF_DB_PATH="/tmp/rmf-step3.db" .venv/bin/rmf gate list --json | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['approvals'] == []" && rm -f /tmp/rmf-step3.db* # expected: exit 0
```

**Checkpoint**
```bash
git add -A && git commit -m "step 3: gate-d-approval-queue"
git tag step-03-gate-d-approval-queue
git rev-parse --verify step-03-gate-d-approval-queue # expected: prints 40-char commit SHA, exit 0
```

---

#### Step 4 — Implement empty factory template store and revision manifest

**Do**
Create `src/rig_meta_factory/templates.py` implementing `compute_dir_manifest(root_path)` (deterministic sorted relative-path SHA-256 map ignoring `.git/`), `init_template` (creating a starter empty factory template directory with `README.md`, `CLAUDE.md`, and `.rmf-template.json`, recording `templates` + `template_revisions` v1), `list_templates`, `show_template`, and `bump_template` (creating vN+1 when files changed, or rejecting with exit `2` `NO_CHANGES` if manifest hash is unchanged). Wire `rmf template init|list|show|bump` into `src/rig_meta_factory/cli.py` and `rmf schema`. Create `tests/test_templates.py` testing init, list, show, bump after file edit, and duplicate-slug/no-change errors.

**Done when**
- [ ] WHEN `rmf template init --slug <slug> --name <name> --root <dir> --json` runs THE SYSTEM SHALL create the starter template files on disk, record revision `1` with its SHA-256 manifest in SQLite, and exit `0`.
- [ ] WHEN a file inside the template root is modified and `rmf template bump --slug <slug> --notes <msg> --json` runs THE SYSTEM SHALL create revision `2` with a new `manifest_sha256` and update `templates.current_revision_id`.
- [ ] WHEN `rmf template bump` is run with no file changes since the current revision THE SYSTEM SHALL exit `2` with `error.code == "NO_CHANGES"` and create no new revision row.

**Verify**
```bash
.venv/bin/pytest -q tests/test_templates.py # expected: exit 0, all tests pass
```

**Checkpoint**
```bash
git add -A && git commit -m "step 4: template-store-revisions"
git tag step-04-template-store-revisions
git rev-parse --verify step-04-template-store-revisions # expected: prints 40-char commit SHA, exit 0
```

---

#### Step 5 — Implement factory CRUD and SSSF stamping engine

**Do**
Create `src/rig_meta_factory/sssf.py` implementing `stamp_sssf(repo_path, sssf_source, dry_run=False)` which copies `RMF_SSSF_SOURCE` (default `~/.aside/u/0/repos/disler/super-simple-software-factory/.claude/skills/sssf`) into `<repo_path>/.claude/skills/sssf` and runs `uv run --python 3.12 .claude/skills/sssf/scripts/install.py` in a subprocess with `VIRTUAL_ENV` removed from `env` (and optional `xattr -dr com.apple.quarantine` best-effort guard on macOS). Create `src/rig_meta_factory/factories.py` implementing `create_factory` (stamping files from template revision, `git init -b main` + initial commit, optional SSSF stamp, state `empty_stamped`), `list_factories`, `show_factory`, `update_factory` (validating state transitions), and `delete_factory` (dry-run exit `0` without `--yes`; Gate-D check from Step 3 when `--yes` is passed). Wire `rmf factory create|list|show|update|delete` into `src/rig_meta_factory/cli.py` and `rmf schema`. Create `tests/test_factories.py` testing full CRUD, SSSF command construction/sanitized env via fake stub script, dry-run delete, Gate-D blocked delete (exit `3`), and Gate-D approved delete (exit `0`).

**Done when**
- [ ] WHEN `rmf factory create` runs against an initialized template THE SYSTEM SHALL copy template files into `--dest`, initialize a git repo with an initial commit, record `state == "empty_stamped"` in `factories`, and exit `0`.
- [ ] WHEN `rmf factory delete --slug <slug> --json` runs without `--yes` THE SYSTEM SHALL return exit `0` with `"dry_run": true` and `payload_sha256` and leave the factory row and directory untouched.
- [ ] WHEN `rmf factory delete --slug <slug> --yes --json` runs without a matching approved Gate-D record THE SYSTEM SHALL exit `3` with `error.code == "GATE_D_REQUIRED"` and leave the factory intact.
- [ ] WHEN `rmf factory delete --slug <slug> --yes --approval-id <id> --json` runs with a matching approved Gate-D record THE SYSTEM SHALL consume the approval, delete the factory row, and exit `0`.

**Verify**
```bash
.venv/bin/pytest -q tests/test_factories.py # expected: exit 0, all tests pass
```

**Checkpoint**
```bash
git add -A && git commit -m "step 5: factory-crud-sssf-stamping"
git tag step-05-factory-crud-sssf-stamping
git rev-parse --verify step-05-factory-crud-sssf-stamping # expected: prints 40-char commit SHA, exit 0
```

---

#### Step 6 — Implement per-factory owner agent mission and changelog

**Do**
Create `src/rig_meta_factory/owners.py` implementing `set_owner` (upsert `owner_agents` for a factory with `agent_name`, `mission`, `status`), `show_owner` (exit `4` if factory or owner not found), `add_changelog` (append row to `owner_changelogs` with `author_agent`, `summary`, optional `git_sha` and `tac_receipt_sha256`), and `list_changelogs` (newest-first). Wire `rmf owner set|show|changelog-add|changelog-list` into `src/rig_meta_factory/cli.py` and `rmf schema`. Create `tests/test_owners.py` covering owner upsert, changelog append/list ordering, and unknown-factory exit `4`.

**Done when**
- [ ] WHEN `rmf owner set --factory <slug> --agent <name> --mission <text> --json` runs THE SYSTEM SHALL persist or update the `owner_agents` row for that factory and exit `0`.
- [ ] WHEN `rmf owner changelog-add` runs with `--summary`, `--git-sha`, and `--tac-receipt-sha256` THE SYSTEM SHALL append an immutable entry returned first by `rmf owner changelog-list`.
- [ ] WHEN `rmf owner show --factory unknown --json` runs THE SYSTEM SHALL exit `4` with `error.code == "NOT_FOUND"`.

**Verify**
```bash
.venv/bin/pytest -q tests/test_owners.py # expected: exit 0, all tests pass
```

**Checkpoint**
```bash
git add -A && git commit -m "step 6: owner-agent-mission-changelog"
git tag step-06-owner-agent-mission-changelog
git rev-parse --verify step-06-owner-agent-mission-changelog # expected: prints 40-char commit SHA, exit 0
```

---

#### Step 7 — Implement feature backport capture and template propagation

**Do**
Create `src/rig_meta_factory/backports.py` implementing:
- `capture_backport(factory_slug, title, paths)` — generates a deterministic unified diff (`difflib.unified_diff`) between the template root and the factory repo for specified relative paths (or all template-tracked paths), hashes `diff_text` with SHA-256, and inserts a `captured` row in `backports` (exits `2` if diff is empty).
- `list_backports` and `show_backport`.
- `apply_backport(backport_id)` — copies the changed files / applies the patch into the template root, calls `bump_template` to create a new revision linked to `source_backport_id`, and sets backport status to `applied`.
- `propagate_backport(backport_id, target_slugs, yes, approval_id)` — without `--yes`, returns dry-run JSON (`exit 0`) with canonical `payload_sha256`; with `--yes`, verifies Gate-D approval (Step 3), applies the updated template files to each target factory, updates `factories.template_revision_id`, appends an `owner_changelogs` entry per target factory, marks approval `consumed`, and sets backport status to `propagated`.
Wire `rmf backport capture|list|show|apply|propagate` into `src/rig_meta_factory/cli.py` and `rmf schema`. Create `tests/test_backports.py` testing capture, empty-diff exit `2`, apply creating template v2, dry-run propagate, Gate-D blocked propagate (exit `3`), and Gate-D approved propagate updating a sibling factory and its changelog.

**Done when**
- [ ] WHEN a file in a running factory diverges from its template and `rmf backport capture` runs THE SYSTEM SHALL store a non-empty unified diff and `diff_sha256` with status `captured`.
- [ ] WHEN `rmf backport apply --id <bp_id> --json` runs THE SYSTEM SHALL update the template directory, create a new `template_revisions` row referencing `<bp_id>`, and mark the backport `applied`.
- [ ] WHEN `rmf backport propagate` runs without `--yes` THE SYSTEM SHALL return exit `0` with `"dry_run": true` and `payload_sha256` without modifying any target factory.
- [ ] WHEN `rmf backport propagate --yes --approval-id <id>` runs with a matching approved Gate-D record THE SYSTEM SHALL update every target factory's files and `template_revision_id`, append a changelog entry per target factory, and consume the approval.

**Verify**
```bash
.venv/bin/pytest -q tests/test_backports.py # expected: exit 0, all tests pass
```

**Checkpoint**
```bash
git add -A && git commit -m "step 7: backport-capture-and-propagation"
git tag step-07-backport-capture-and-propagation
git rev-parse --verify step-07-backport-capture-and-propagation # expected: prints 40-char commit SHA, exit 0
```

---

#### Step 8 — Implement factory-to-factory mailbox

**Do**
Create `src/rig_meta_factory/mailbox.py` implementing `send_message(from_slug, to_slug, subject, body)` (validating both factories exist, exit `4` if either is missing, exit `2` if `from_slug == to_slug`), `list_inbox(to_slug, status_filter)`, and `ack_message(message_id)` (setting `status='acked'` and `acked_at`). Wire `rmf mailbox send|inbox|ack` into `src/rig_meta_factory/cli.py` and `rmf schema`. Create `tests/test_mailbox.py` covering send, unread inbox filter, ack transition, self-send exit `2`, and unknown-factory exit `4`.

**Done when**
- [ ] WHEN `rmf mailbox send --from <f1> --to <f2> --subject <s> --body <b> --json` runs THE SYSTEM SHALL insert an `unread` message visible in `rmf mailbox inbox --factory <f2> --status unread`.
- [ ] WHEN `rmf mailbox ack --id <msg_id> --json` runs THE SYSTEM SHALL transition the message to `acked` with a non-null `acked_at` timestamp and exclude it from `--status unread`.
- [ ] WHEN `--from` equals `--to` THE SYSTEM SHALL reject the send with exit code `2` and `error.code == "USAGE_ERROR"`.

**Verify**
```bash
.venv/bin/pytest -q tests/test_mailbox.py # expected: exit 0, all tests pass
```

**Checkpoint**
```bash
git add -A && git commit -m "step 8: factory-to-factory-mailbox"
git tag step-08-factory-to-factory-mailbox
git rev-parse --verify step-08-factory-to-factory-mailbox # expected: prints 40-char commit SHA, exit 0
```

---

#### Step 9 — Implement lead agent status rollup and cron registry

**Do**
Create `src/rig_meta_factory/lead.py` implementing:
- `lead_status()` — returns aggregate counts (`templates`, `factories_by_state`, `unread_mailbox_count`, `pending_gate_d_count`, `enabled_crons_count`, `active_tac_missions_count`) plus a per-factory summary array (`slug`, `state`, `template_version`, `owner_agent`, `unread_messages`, `enabled_crons`).
- `list_crons(factory_slug=None)`, `set_cron(factory_slug, name, schedule_expr, command, enabled=True)`, and `remove_cron(factory_slug, name)` (exit `4` if missing).
Wire `rmf lead status|crons|cron-set|cron-remove` into `src/rig_meta_factory/cli.py` and `rmf schema`. Create `tests/test_lead.py` covering status rollup accuracy across multiple factories, cron upsert/list/remove, and missing-cron exit `4`.

**Done when**
- [ ] WHEN `rmf lead status --json` runs THE SYSTEM SHALL return exact counts for factories by state, unread mailbox messages, pending Gate-D approvals, and enabled crons plus per-factory rows.
- [ ] WHEN `rmf lead cron-set` registers a cron for a factory THE SYSTEM SHALL persist it in `factory_crons` and include it in `rmf lead crons --json`.
- [ ] WHEN `rmf lead cron-remove` targets a non-existent cron name THE SYSTEM SHALL exit `4` with `error.code == "NOT_FOUND"`.

**Verify**
```bash
.venv/bin/pytest -q tests/test_lead.py # expected: exit 0, all tests pass
```

**Checkpoint**
```bash
git add -A && git commit -m "step 9: lead-status-and-cron-registry"
git tag step-09-lead-status-and-cron-registry
git rev-parse --verify step-09-lead-status-and-cron-registry # expected: prints 40-char commit SHA, exit 0
```

---

#### Step 10 — Implement TAC mission integration via rig-tac-os

**Do**
Create `src/rig_meta_factory/tac_bridge.py` wrapping `RMF_TAC_BIN` (default `~/.rig/bin/rig-tac-os`) via `subprocess.run`:
- `tac_plan(factory_slug, step_name, task_text, done_test, builder_id, model_route=None)` — invokes `<tac_bin> plan <task_text> --step <step_name> --done-test <done_test>` (plus `--model <model_route>` when a route is given) with no `--out` (the plan stays in rig-tac-os's own plans folder, the only place `receipt` can hash it), inserts a `planned` row in `tac_missions`, and records the file rig-tac-os prints as `plan: <path>`.
- `tac_ship_stop(mission_id)` — invokes `<tac_bin> ship-stop --step <step_name> --plan <plan_path>` and transitions to `ship_stopped` (or exits `3` if ship-stop denies).
- `tac_run(mission_id)` — invokes `<tac_bin> run <plan_path> --repo <factory.repo_path> --wait` and records as `run_path` the run record rig-tac-os prints as `run_record: <path>` (never captured stdout), status `ran`.
- `tac_await(mission_id, terminal)` — waits again for a builder that outlived the run wait limit (mission `failed` with a run record, or `ship_stopped` with a run record after an interrupted `tac run`); when the builder exits the mission is `ran` on that record; a terminal Orca no longer knows is `TAC_BUILDER_TERMINAL_GONE` (exit `1`).
- `tac_verify(mission_id, verifier_id)` — enforces `verifier_id != mission.builder_id` (exit `3` `SELF_VERIFY_FORBIDDEN` if equal or empty), invokes `<tac_bin> verify <run_path> --repo <factory.repo_path> --plan <plan_path>`, records the file printed as `verify_record: <path>` at `verify_path`; status `verified` only when rig-tac-os exits `0`, prints `passed: True`, and the factory repo's `git status --porcelain` is unchanged across the call, else `failed` (exit `1`, `TAC_VERIFY_FAILED`).
- `tac_receipt(mission_id)` — requires status `verified` (exit `3` otherwise), invokes `<tac_bin> receipt <run_path> <verify_path> --out <mission_dir>` (`--out` is a folder), computes SHA-256 of the `receipt_json:` file it writes there, updates `tac_missions` to `receipted`, and appends a linked `owner_changelogs` entry with `tac_receipt_sha256`.
- `tac_list(factory_slug=None)`.
Wire `rmf tac plan|ship-stop|run|await|verify|receipt|list` into `src/rig_meta_factory/cli.py` and `rmf schema`. Create `tests/test_tac_bridge.py` using a deterministic fake `rig-tac-os` executable in `tmp_path` (pointed to by `RMF_TAC_BIN`) to test the full `plan -> ship-stop -> run -> verify -> receipt` chain, self-verification rejection (exit `3`), unverified-receipt rejection (exit `3`), a failed done-test (exit `1`), a verifier that writes to the factory repo (exit `1`), a SHIP-STOP refusal (exit `3`), and a missing `run_record` (exit `1`), with a fake `orca` (on `RMF_ORCA_BIN`) that also covers an unreachable runtime and an unregistered factory repo (each exit `1`) and the builder wait (exit line found, non-zero builder exit, timeout exit `1`, re-attach with `tac await`, a terminal Orca no longer knows exit `1`), and a factory repo that is not a git repository (exit `1`).

**Done when**
- [ ] WHEN a TAC mission completes `plan -> ship-stop -> run -> verify -> receipt` with `verifier_id != builder_id` THE SYSTEM SHALL persist `receipt_sha256` on `tac_missions` and append a matching entry in `owner_changelogs`.
- [ ] WHEN `rmf tac verify` is called with `verifier_id == builder_id` THE SYSTEM SHALL reject the call with exit code `3` and `error.code == "SELF_VERIFY_FORBIDDEN"` without invoking `rig-tac-os`.
- [ ] WHEN `rig-tac-os verify` reports `passed: False` or exits non-zero THE SYSTEM SHALL mark the mission `failed`, keep the verify record at `verify_path`, and exit `1` with `error.code == "TAC_VERIFY_FAILED"`.
- [ ] WHEN the verifier changes the factory repo's `git status --porcelain` THE SYSTEM SHALL mark the mission `failed` and exit `1` with `error.code == "TAC_VERIFY_FAILED"`.
- [ ] WHEN `rig-tac-os ship-stop` denies a mission THE SYSTEM SHALL mark it `failed` and exit `3` with `error.code == "SHIP_STOP_DENIED"`.
- [ ] WHEN `rmf tac plan` is called with `--model <route>` THE SYSTEM SHALL pass `--model <route>` to `rig-tac-os plan` so the builder runs on that route, and without the flag THE SYSTEM SHALL leave the route to `rig-tac-os` (Flash-class routes stay refused there).
- [ ] WHEN the Orca runtime is unreachable THE SYSTEM SHALL refuse `rmf tac run` and `rmf tac verify` with exit code `1` and `error.code == "ORCA_UNAVAILABLE"` before calling `rig-tac-os`, leaving the mission status unchanged.
- [ ] WHEN the factory repo is not registered with Orca THE SYSTEM SHALL refuse with exit code `1`, `error.code == "ORCA_WORKTREE_UNREGISTERED"` and a message that names the `orca repo add --path <repo>` command to run, leaving the mission status unchanged.
- [ ] WHEN `rmf tac run` returns from `rig-tac-os run --wait` THE SYSTEM SHALL keep reading the builder terminal through the Orca CLI until the builder command prints its own `__BUILDER_EXIT__=<code>` line (every 5 s, up to `RMF_TAC_WAIT_SECONDS`, default 900) and report `builder.exit_code`.
- [ ] WHEN the builder prints no exit line before the wait limit THE SYSTEM SHALL mark the mission `failed`, keep the run record path, and exit `1` with `error.code == "TAC_BUILDER_TIMEOUT"`, naming the terminal to read and the `rmf tac await` command that waits for it.
- [ ] WHEN the builder outlives the wait limit and later prints its exit line THE SYSTEM SHALL let `rmf tac await --id <mission> --terminal <terminal>` wait for it again and move the mission from `failed` to `ran` on the run record it already has (any builder exit code: verify decides).
- [ ] WHEN `rmf tac run` is called on a mission that already has a run record (timed out, or interrupted during the builder wait) THE SYSTEM SHALL exit `2` with `error.code == "INVALID_STATE"`, name the `rmf tac await --id <mission> --terminal <terminal>` command, and never call `rig-tac-os run` again for that mission.
- [ ] WHEN `rmf tac await` is called on a mission left `ship_stopped` with a run record by an interrupted `tac run` THE SYSTEM SHALL wait for that builder exactly as for a timed-out mission (exited: `ran`; terminal gone: exit `1`, `TAC_BUILDER_TERMINAL_GONE`, status `failed`).
- [ ] WHEN Orca still knows the builder terminal but reports that it has ended (`status` is `exited`) without an exit line (it was closed or killed) THE SYSTEM SHALL stop waiting at once and exit `1` with `error.code == "TAC_BUILDER_TERMINAL_GONE"` (`orca error terminal_exited_without_exit_line`), status `failed`, instead of waiting out the whole limit.
- [ ] WHEN Orca no longer knows the builder terminal (closed, or Orca restarted) THE SYSTEM SHALL keep the mission `failed` and exit `1` with `error.code == "TAC_BUILDER_TERMINAL_GONE"`.
- [ ] WHEN the factory repo is not a readable git repository THE SYSTEM SHALL refuse `rmf tac verify` with exit code `1` and `error.code == "FACTORY_NOT_GIT_REPO"`, leaving the mission status unchanged.
- [ ] WHEN `rmf tac receipt` is called on a mission whose status is not `verified` THE SYSTEM SHALL reject the call with exit code `3` and `error.code == "NOT_VERIFIED"`.

**Verify**
```bash
.venv/bin/pytest -q tests/test_tac_bridge.py # expected: exit 0, all tests pass
```

**Checkpoint**
```bash
git add -A && git commit -m "step 10: tac-os-mission-integration"
git tag step-10-tac-os-mission-integration
git rev-parse --verify step-10-tac-os-mission-integration # expected: prints 40-char commit SHA, exit 0
```

---

#### Step 11 — Implement BAMA stdio MCP server and end-to-end audit

**Do**
Create `src/rig_meta_factory/mcp_server.py` implementing a line-delimited stdio JSON-RPC 2.0 server (`serve_mcp()`) that handles `initialize`, `notifications/initialized`, `tools/list` (generating 1 tool per command in `rmf schema` with `[A-Za-z0-9_-]{1,64}` names, JSON Schema `inputSchema`, and `annotations.readOnlyHint`), and `tools/call` (dispatching to the same CLI command handlers and returning structured JSON text content or `isError: true` on non-zero exit). Wire `rmf mcp` into `src/rig_meta_factory/cli.py`. Create `tests/test_mcp_server.py` testing `initialize`, `tools/list` parity with `rmf schema --json`, and `tools/call` for `rmf_status` and an error path.

**Done when**
- [ ] WHEN an MCP client sends `initialize` and `tools/list` over stdio to `rmf mcp` THE SYSTEM SHALL return `serverInfo` and exactly one tool per command listed in `rmf schema --json`, each with valid name regex `[A-Za-z0-9_-]{1,64}` and boolean `annotations.readOnlyHint`.
- [ ] WHEN `~/Developer/bama/skills/code/agent-cli/scripts/check-agent-cli.sh .venv/bin/rmf` runs THE SYSTEM SHALL exit `0` with all BAMA contract checks passing.
- [ ] WHEN `tools/call` invokes an unknown tool name THE SYSTEM SHALL return a JSON-RPC error response without crashing the stdio loop.

**Verify**
```bash
.venv/bin/pytest -q # expected: exit 0, all 11 test modules pass
RMF_DB_PATH="/tmp/rmf-bama-audit.db" ~/Developer/bama/skills/code/agent-cli/scripts/check-agent-cli.sh .venv/bin/rmf && rm -f /tmp/rmf-bama-audit.db* # expected: exit 0, BAMA contract checks pass
```

**Checkpoint**
```bash
git add -A && git commit -m "step 11: bama-stdio-mcp-server-and-audit"
git tag step-11-bama-stdio-mcp-server-and-audit
git rev-parse --verify step-11-bama-stdio-mcp-server-and-audit # expected: prints 40-char commit SHA, exit 0
```

---

### 9.1 Parity and cutover

NOT APPLICABLE — greenfield build, no system is being replaced.

---

## 10. Environment Setup

### Prerequisites

- **Target platform:** macOS (Apple Silicon / arm64, zsh or bash).
- Python, `uv`, SQLite, and `git` installed (exact verified versions in §11).
- Local SSSF source repo at `~/.aside/u/0/repos/disler/super-simple-software-factory/.claude/skills/sssf`.
- Local TAC OS CLI at `~/.rig/bin/rig-tac-os`.
- Local BAMA CLI audit script at `~/Developer/bama/skills/code/agent-cli/scripts/check-agent-cli.sh`.

### Accounts to create first

NOT APPLICABLE — local-first CLI and SQLite registry; no external accounts or API keys required for v1.

### Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `RMF_DB_PATH` | no | `~/.rig/meta-factory/registry.db` | Path to the SQLite registry database |
| `RMF_SSSF_SOURCE` | no | `.claude/skills/sssf` in this repo | Local SSSF skill directory copied during factory stamping |
| `RMF_TAC_BIN` | no | first `rig-tac-os` on `PATH`, else `~/.rig/bin/rig-tac-os` | Path to the `rig-tac-os` executable |
| `RMF_TAC_WAIT_SECONDS` | no | `900` | Seconds `tac run` waits for the builder's exit line; `RMF_TAC_POLL_SECONDS` (default `5`) is the poll interval |
| `RMF_ORCA_BIN` | no | first `orca` on `PATH` | Orca binary checked by `tac run` and `tac verify` before rig-tac-os opens its terminals; an empty string skips the check |
| `RMF_UV_BIN` | no | first `uv` on `PATH`, else `~/.local/bin/uv` | Path to `uv` binary used by SSSF installer |

### Files that must be committed

- `pyproject.toml`, `.gitignore`, `.env.example`, `CLAUDE.md`, `AGENTS.md`
- `.claude/settings.json`, `.claude/skills/bama-verify/SKILL.md`, `.claude/rules/cli-contract.md`
- All files under `src/rig_meta_factory/` and `tests/`

### Bootstrap

```bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

if [ ! -d .git ]; then
  git init -b main
fi

if [ ! -d .venv ]; then
  uv venv .venv --python python3
fi

uv pip install --python .venv/bin/python -e ".[dev]"

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
fi
```

---

## 11. Dependencies

### Runtime

- **Python:** `3.13.12` (verified locally 2026-11-23 via `~/.aside/runtime/bin/python3 --version`; zero third-party PyPI runtime dependencies — standard library only).
- **SQLite (via Python `sqlite3`):** `3.51.0` (verified locally 2026-11-23 via `python3 -c "import sqlite3; print(sqlite3.sqlite_version)"`).
- **git:** `2.50.1` (verified locally 2026-11-23 via `git --version`).
- **uv:** `0.13.1` (verified locally 2026-11-23 via `~/.local/bin/uv --version`).

### Development

- **pytest:** `9.1.1` (verified locally 2026-11-23 via `pytest --version`; installed into `.venv` via `uv pip install -e ".[dev]"` in §10 Bootstrap).

### Deliberately not used

- **Click / Typer / FastMCP:** Excluded to keep zero runtime PyPI dependencies, sub-50ms startup, and deterministic BAMA JSON error envelopes without framework stderr leakage.
- **SQLAlchemy / Alembic:** Excluded because 10 flat SQLite tables with a single `schema.sql` migration runner are simpler, faster, and auditable in one file.
- **Ruff / Mypy:** Not installed globally on this host; omitted from v1 required gates rather than introducing unverified pins.

---

## 12. Deployment Strategy

### Hosting

Local workstation installation on macOS (`~/Developer/rig-meta-factory`), exposed globally on PATH via symlink `~/.local/bin/rmf -> ~/Developer/rig-meta-factory/.venv/bin/rmf`.

### Environments

- **Development / Test:** Isolated temp SQLite databases via `RMF_DB_PATH` (`pytest` `tmp_path` fixtures) and stubbed `RMF_TAC_BIN` / `RMF_SSSF_SOURCE`.
- **Local Production (RIG operator):** `~/.rig/meta-factory/registry.db` with `~/.local/bin/rmf` registered as an MCP server (`rmf mcp`) in Aside / AgentsRoom / Autark.

### CI/CD

Local pre-commit / TAC verification gate: `pytest -q` + `check-agent-cli.sh .venv/bin/rmf` + `bama audit-cli --cli rmf --json` run before any git tag or symlink update.

### Release and rollback

- Releases are tagged in git (`v0.1.0`, etc.) after §20.1 Global Acceptance Gate passes.
- Rollback: `git checkout <prior-tag>` and re-run §10 Bootstrap (`uv pip install --python .venv/bin/python -e ".[dev]"`). SQLite database backup (`cp ~/.rig/meta-factory/registry.db ~/.rig/meta-factory/registry.db.bak-<timestamp>`) is taken before any future schema v2 migration.

### Domain, DNS, TLS

NOT APPLICABLE — local CLI and stdio MCP server with no public HTTP endpoint.

---

## 13. Testing Strategy

### Critical flows to cover E2E

1. **BAMA CLI & MCP contract:** `rmf status --json`, `rmf schema --json`, unknown command exit `2` JSON error, `rmf mcp` `initialize` + `tools/list` + `tools/call`, and `check-agent-cli.sh` pass.
2. **Template -> Factory -> Owner lifecycle:** `template init` -> `factory create` (with git init and optional SSSF stub) -> `owner set` -> `owner changelog-add` -> `lead status`.
3. **Backport & Gate-D propagation:** Modify factory file -> `backport capture` -> `backport apply` (creates template v2) -> `backport propagate` dry-run -> `gate queue` + `gate approve` -> `backport propagate --yes --approval-id` updates sibling factory and appends changelog -> replay rejected with exit `3`.
4. **Mailbox & Cron:** `mailbox send` -> `mailbox inbox --status unread` -> `mailbox ack`; `lead cron-set` -> `lead crons` -> `lead cron-remove`.
5. **TAC mission & independent verifier:** `tac plan` -> `tac ship-stop` -> `tac run` (-> `tac await` when the builder outlives the wait limit) -> `tac verify` (self-verify blocked exit `3`, distinct verifier passes) -> `tac receipt` links SHA-256 into `owner_changelogs`.

### Test data

- Every test uses `pytest` `tmp_path` for `RMF_DB_PATH`, template roots, factory repos, and fake `rig-tac-os` / SSSF stub executables so tests never touch `~/.rig/meta-factory/registry.db` or live user repos.

### What is deliberately not tested

- Live LLM responses inside `rig-tac-os` (tests stub `RMF_TAC_BIN` to verify CLI argument passing, exit-code handling, and receipt hashing deterministically).
- Live network downloads inside SSSF's `uv run` (tests verify sanitized environment `VIRTUAL_ENV` removal, file copy, and subprocess invocation against a stub).

---

## 14. Security & Secrets

- **No secrets stored in SQLite or logs:** `rig-meta-factory` never stores API keys, passwords, or OAuth tokens. All environment variables in §10 are filesystem paths.
- **No shell injection:** Every `subprocess.run` call (`git`, `uv`, `rig-tac-os`, `xattr`) uses argument lists (`shell=False`), never string interpolation into a shell.
- **Path traversal prevention:** Factory slugs and template slugs are validated against `^[a-z0-9][a-z0-9-]{1,48}$`. Backport relative paths are resolved and asserted to stay inside the factory/template root (`Path.is_relative_to`).
- **Gate-D anti-replay & integrity:** Approvals bind to canonical `payload_sha256` and transition to `consumed` inside the same SQLite transaction as execution.
- **Independent TAC verification:** `tac_verify` enforces `verifier_id != builder_id` in code so no builder agent can self-certify a mission.

---

## 15. Accessibility

NOT APPLICABLE — headless CLI and stdio MCP server with no graphical interface; CLI errors and help text are plain UTF-8 text on stdout/stderr.

---

## 16. Observability & Cost

### Instrumentation

- Every mutating command writes a structured audit record to SQLite (`owner_changelogs`, `gate_approvals`, `tac_missions`, `template_revisions`) with ISO-8601 UTC timestamps and SHA-256 hashes.
- `rmf lead status --json` provides a single-call operational snapshot of all factories, unread mailbox backlog, pending Gate-D approvals, enabled crons, and TAC mission states.

### The metrics that matter for this project

- Pending Gate-D approval count and age.
- Unread factory-to-factory mailbox count.
- Factories behind latest template revision (`factory.template_revision_id != template.current_revision_id`).
- TAC mission pass vs fail ratio and receipt coverage on `owner_changelogs`.

### Health check

- `rmf status --json` exits `0` and verifies SQLite connectivity, WAL mode, and schema version `1`.

### Cost model

- `$0.00` direct runtime cost (local Python + SQLite). Any LLM cost occurs solely inside delegated `rig-tac-os` runs governed by TAC's own model flags.

---

## 17. Model Routing

### Routing table

| Task | Executor | Rationale |
|---|---|---|
| Registry CRUD, diff capture, Gate-D hashing, mailbox, cron, MCP framing | Deterministic Python (`rmf`) | Zero-cost, zero-hallucination, sub-50ms execution |
| Factory change planning & execution | `rig-tac-os` (`plan` / `run`) | Delegates to configured TAC builder model with bounded scope |
| Independent done-test verification | `rig-tac-os` (`verify`) + distinct `verifier_id` | Enforces separate verifier from builder |
| Quality / done-claim gating | Hosted Jev (`rig-s1gate`) | Required estate gate before outward or completion claims |

### Prompt and context strategy

- `rmf` keeps prompts out of the registry; each factory's `owner_agents.mission` and `owner_changelogs` supply bounded, structured JSON context to agents via MCP tools (`rmf_owner_show`, `rmf_lead_status`).

### Cost controls

- Dry-run by default on destructive and multi-factory propagation commands prevents accidental fan-out runs.

### Failure handling

- Any `rig-tac-os` non-zero exit is captured with stdout/stderr into the mission's artifact file, marks `tac_missions.status = 'failed'`, and returns exit `1` (or `3` on `ship-stop` policy block) without fabricating a receipt.

### Evaluation

- Measured by §20.1 Global Acceptance Gate (`pytest`, `check-agent-cli.sh`, `bama audit-cli`, and live smoke test).

---

## 18. Skills to Use During Build

| Skill | Path | When to use |
|---|---|---|
| `bama` | `~/.aside/u/0/skills/user/bama/SKILL.md` | Verifying BAMA CLI/MCP compliance (`check-agent-cli.sh` and `bama audit-cli`) |
| `tac-os` | `~/.aside/u/0/skills/user/tac-os/SKILL.md` | Verifying `rig-tac-os` plan/ship-stop/run/verify/receipt integration in Step 10 |
| `software-factory` | `~/.aside/u/0/skill-library/software-factory/SKILL.md` | Verifying SSSF stamping flow and macOS `VIRTUAL_ENV`/`xattr` guards in Step 5 |
| `s1gate` | `~/.aside/u/0/skills/user/s1gate/SKILL.md` | Running hosted Jev gate before reporting build completion |
| `done-claim-verification` | `~/.aside/u/0/skills/user/done-claim-verification/SKILL.md` | Independent read-back verification before closing the build |

---

## 19. Agent Workspace

### 19.1 `CLAUDE.md`

```markdown
# rig-meta-factory

Local BAMA-compliant meta-factory CLI and stdio MCP server (`rmf`).

## Rules
- Follow the build order in `rig-meta-factory-blueprint.md` one step at a time.
- Keep zero third-party runtime dependencies; Python standard library only under `src/rig_meta_factory/`.
- Every CLI command supports `--json`, emits exactly one JSON object on stdout, and obeys BAMA exit codes (`0` ok, `1` runtime, `2` usage, `3` gate/policy, `4` not found).
- Never bypass Gate-D (`--yes` + matching `payload_sha256` approval) on destructive commands.
- Never allow `verifier_id == builder_id` in `tac_bridge.py`.
- Run `.venv/bin/pytest -q` before every checkpoint commit.
```

### 19.2 `AGENTS.md`

```markdown
# AGENTS.md — rig-meta-factory

- Read `CLAUDE.md` and `.claude/rules/cli-contract.md` before editing code.
- Use `.venv/bin/pytest` and `.venv/bin/rmf` for all verification commands.
- Never run tests against `~/.rig/meta-factory/registry.db`; always set `RMF_DB_PATH` to a temp path.
- Never use `shell=True` in `subprocess.run`.
```

### 19.3 `.claude/settings.json`

```json
{
  "permissions": {
    "allow": [
      "Bash(.venv/bin/pytest*)",
      "Bash(.venv/bin/rmf*)",
      "Bash(.venv/bin/python3*)",
      "Bash(python3*)",
      "Bash(git status*)",
      "Bash(git diff*)",
      "Bash(git add*)",
      "Bash(git commit*)",
      "Bash(git tag*)",
      "Bash(git rev-parse*)",
      "Bash(uv *)",
      "Bash(/Users/rig128gb/Developer/bama/skills/code/agent-cli/scripts/check-agent-cli.sh*)",
      "Bash(bama audit-cli*)"
    ],
    "deny": [
      "Bash(rm -rf /*)",
      "Bash(git push --force*)",
      "Bash(git reset --hard*)"
    ]
  }
}
```

### 19.4 Project skills — `.claude/skills/<name>/SKILL.md`

File: `.claude/skills/bama-verify/SKILL.md`

```markdown
---
name: bama-verify
description: Run BAMA CLI and MCP contract verification against rig-meta-factory.
---

## When to use
After any change to `src/rig_meta_factory/cli.py`, `errors.py`, or `mcp_server.py`.

## Steps
1. Run `.venv/bin/pytest -q tests/test_cli_contract.py tests/test_mcp_server.py`.
2. Run `RMF_DB_PATH="/tmp/rmf-bama-check.db" ~/Developer/bama/skills/code/agent-cli/scripts/check-agent-cli.sh .venv/bin/rmf`.
3. Clean up `/tmp/rmf-bama-check.db*`.

## Verify
Both commands exit `0` with zero failed assertions.

## Do not
Do not run `check-agent-cli.sh` against the live `~/.rig/meta-factory/registry.db`.
```

### 19.5 `.claude/rules/*.md`

| File | `paths` globs | Covers |
|---|---|---|
| `.claude/rules/cli-contract.md` | `src/rig_meta_factory/**/*.py` | BAMA JSON output, exit codes 0-4, Gate-D enforcement, subprocess safety |

File: `.claude/rules/cli-contract.md`

```markdown
---
paths:
  - "src/rig_meta_factory/**/*.py"
---

# CLI & MCP Contract Rules

1. When `--json` is set, write ONLY a single JSON object to stdout; never print progress text to stdout.
2. Exit codes: `0` success/dry-run, `1` runtime error, `2` usage/validation error, `3` policy/Gate-D/self-verify block, `4` not found.
3. All `subprocess.run` calls must pass an explicit list of args with `shell=False`.
4. All SQLite writes must execute inside a transaction (`with conn:`) with `PRAGMA foreign_keys = ON`.
```

### 19.6 Verify-critical config and local infrastructure

File: `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "rig-meta-factory"
version = "0.1.0"
description = "BAMA-compliant meta-factory CLI and stdio MCP server"
requires-python = ">=3.13"
dependencies = []

[project.optional-dependencies]
dev = [
  "pytest==9.1.1",
]

[project.scripts]
rmf = "rig_meta_factory.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
rig_meta_factory = ["schema.sql"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

File: `.gitignore`

```gitignore
.venv/
.env
__pycache__/
*.pyc
*.pyo
.pytest_cache/
*.egg-info/
dist/
build/
*.db
*.db-wal
*.db-shm
```

File: `.env.example`

```dotenv
RMF_DB_PATH=~/.rig/meta-factory/registry.db
RMF_SSSF_SOURCE=~/.aside/u/0/repos/disler/super-simple-software-factory/.claude/skills/sssf
RMF_TAC_BIN=~/.rig/bin/rig-tac-os
RMF_UV_BIN=~/.local/bin/uv
```

#### Resolution convention matrix

| Loader / Context | Command | Resolution mechanism | Verified config |
|---|---|---|---|
| Editable package install | `uv pip install --python .venv/bin/python -e ".[dev]"` | PEP 660 editable finder for `src/rig_meta_factory` | `pyproject.toml` (`[tool.setuptools.packages.find] where = ["src"]`) |
| Unit / integration tests | `.venv/bin/pytest -q` | `.venv` site-packages editable link + `testpaths = ["tests"]` | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| CLI console script | `.venv/bin/rmf status --json` | Entry point `rmf = "rig_meta_factory.cli:main"` | `pyproject.toml` (`[project.scripts]`) |
| Package data (`schema.sql`) | `importlib.resources.files("rig_meta_factory").joinpath("schema.sql")` | Setuptools package-data inclusion | `pyproject.toml` (`[tool.setuptools.package-data]`) |

#### Byte-exact artifact reconciliation

No byte-exact golden fixture is diffed in §9; all verification checks assert structured JSON keys, SQLite state, and exact exit codes.

#### Bundle-path exclusion

NOT APPLICABLE — single-file mode; no bundle directory exists inside the target repository.

---

## 20. Acceptance Gate, Risks & Decision Log

### 20.1 Global acceptance gate

Run all commands below from `~/Developer/rig-meta-factory` after Step 11 completes:

```bash
# 1. Re-run §10 Bootstrap and confirm idempotent exit 0
bash -c 'set -euo pipefail; export PATH="$HOME/.local/bin:$PATH"; if [ ! -d .git ]; then git init -b main; fi; if [ ! -d .venv ]; then uv venv .venv --python python3; fi; uv pip install --python .venv/bin/python -e ".[dev]"' # expected: exit 0

# 2. Full automated test suite
.venv/bin/pytest -q # expected: exit 0, all 11 test files pass

# 3. BAMA CLI & MCP contract verification
RMF_DB_PATH="/tmp/rmf-global-bama.db" ~/Developer/bama/skills/code/agent-cli/scripts/check-agent-cli.sh .venv/bin/rmf && rm -f /tmp/rmf-global-bama.db* # expected: exit 0

# 4. End-to-end live binary smoke test (template -> factory -> owner -> mailbox -> lead -> Gate-D delete)
TMP_DIR=$(mktemp -d /tmp/rmf-smoke.XXXXXX)
RMF_DB_PATH="$TMP_DIR/reg.db" .venv/bin/rmf template init --slug base --name "Base Factory" --root "$TMP_DIR/tpl" --json | python3 -c "import json,sys; assert json.load(sys.stdin)['revision']['version'] == 1"
RMF_DB_PATH="$TMP_DIR/reg.db" .venv/bin/rmf factory create --slug f1 --name "Factory One" --template base --dest "$TMP_DIR/f1" --json | python3 -c "import json,sys; assert json.load(sys.stdin)['factory']['state'] == 'empty_stamped'"
RMF_DB_PATH="$TMP_DIR/reg.db" .venv/bin/rmf owner set --factory f1 --agent owner-1 --mission "Ship reliable builds" --json | python3 -c "import json,sys; assert json.load(sys.stdin)['owner']['agent_name'] == 'owner-1'"
RMF_DB_PATH="$TMP_DIR/reg.db" .venv/bin/rmf lead status --json | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['counts']['factories'] == 1"
rm -rf "$TMP_DIR" # expected: exit 0
```

### 20.2 Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| SSSF `install.py` fails under parent `VIRTUAL_ENV` or macOS quarantine | Medium | High | `sssf.py` explicitly strips `VIRTUAL_ENV`, invokes `uv run --python 3.12`, and clears `com.apple.quarantine` when present |
| Template backport diff conflicts on diverged sibling factories | Medium | Medium | `backport propagate` checks clean patch applicability per target before writing and requires dry-run + Gate-D approval |
| `rig-tac-os` CLI flags drift in future versions | Low | Medium | `tac_bridge.py` isolates all subprocess calls in one module and verifies against `RMF_TAC_BIN` |
| SQLite lock contention under concurrent MCP/CLI calls | Low | Low | `WAL` mode + `busy_timeout=5000` + short transactions in `db.py` |

### 20.3 Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-11-23 | Zero third-party Python runtime dependencies | Eliminates wheel/env breakage, guarantees <50ms CLI startup, and keeps BAMA JSON error envelopes clean |
| 2026-11-23 | Place Gate-D (Step 3) before Factory CRUD (Step 5) and Backports (Step 7) | Prevents retroactive test breakage; destructive commands enforce Gate-D from their first commit |
| 2026-11-23 | Single-file blueprint mode (11 steps) | Meets ≤11-step threshold cleanly with 3–4 files per step and zero bundle-path config duplication |
| 2026-11-23 | Enforce `verifier_id != builder_id` in `tac_bridge.py` | Guarantees independent verification per RIG doctrine before any TAC receipt can be generated |
| 2026-11-23 | Tests for Steps 8–11 live in the four files §9 names (`test_mailbox.py`, `test_lead.py`, `test_tac_bridge.py`, `test_mcp_server.py`), not one merged file | Each step's Verify command must exist and pass on its own |
| 2026-11-23 | The TAC bridge stores the record files rig-tac-os prints (`plan:`, `run_record:`, `verify_record:`, `receipt_json:`), does not redirect plans with `--out`, and checks the factory repo for verifier writes | The real `rig-tac-os verify` and `receipt` parse those JSON records; `receipt` only hashes a plan in its own plans folder; rig-tac-os checks its own repo for verifier writes, not the factory's |
| 2026-11-23 | Portable defaults: SSSF from `.claude/skills/sssf` in this repo, `rig-tac-os` and `uv` from `PATH` then `~`; no absolute machine path in `src/` or `.env.example` | The repo is public and must run on any machine; `test_no_machine_specific_paths_in_shipped_code` fails if one returns |
| 2026-11-23 | Blueprint lives at `blueprints/rig-meta-factory-blueprint.md`; Step 11 runs `check-agent-cli.sh` by its `zsh` shebang | the-architect blueprint home; `bash <script>` fails on the script's zsh-only syntax |
| 2026-11-23 | §5 `tac verify` flags are `--id` and `--verifier` | Names shipped by Step 10; behaviour unchanged |
| 2026-11-23 | `tac run` and `tac verify` check that the Orca runtime is reachable and that the factory repo is registered with Orca before calling rig-tac-os (`RMF_ORCA_BIN` selects the binary; an empty string skips the check) | rig-tac-os opens its builder and verifier with `orca terminal create --worktree path:<repo>`, which fails with `selector_not_found` for a folder Orca does not know; the first live run failed that way and the error only surfaced as a traceback |
| 2026-11-23 | `tac run` waits for the builder command's own `__BUILDER_EXIT__=<code>` line by reading the Orca terminal (`RMF_TAC_WAIT_SECONDS`, default 900); no exit line in time is `failed` + `TAC_BUILDER_TIMEOUT` | `rig-tac-os run --wait` returned after 7 s on the first live run: `wait_for_marker` matches the marker text anywhere, and the terminal echoes the typed command, which contains it; the builder was still writing |
| 2026-11-23 | Add `--model <route>` to `tac plan` | The pinned `rig-tac-os` route (`mimo-plan/mimo-v2.6-pro`, provider endpoint 127.0.0.1:8655) had no listener and no loaded launchd job on the laptop; a dead route leaves the builder retrying in Orca. The route stays selectable per mission; rmf never edits OMP or fleet config. |
| 2026-11-23 | `rig-tac-os plan` takes `--model <route>` | Its help prints `[default: mimo-plan/mimo-v2.6-pro]`; `default` there is the default VALUE, not a keyword. A live plan with `--model default <route>` failed with exit 2 (`Got unexpected extra argument(s)`), which also showed that failure messages need box borders stripped. |
| 2026-11-23 | `tac await --id --terminal` re-attaches to a builder that outlived the run wait limit (`failed` -> `ran` on the existing run record); a terminal Orca no longer knows is `TAC_BUILDER_TERMINAL_GONE`; `tac run` stores `run_path` as soon as the run record exists | Live: a local-model builder wrote the file after about 8.5 minutes and was still stepping at 15 minutes, so the 900 s limit dead-ended a mission whose builder would have finished |
| 2026-11-23 | `tac verify` refuses a factory repo that is not a readable git repository (`FACTORY_NOT_GIT_REPO`) | Without it a non-git folder looks "unchanged" and a verifier write passes silently |
| 2026-11-23 | `rmf help <noun> [verb]` and `rmf <noun> <verb> --help` describe one noun or one command | Both were usage errors (exit 2): only the full command list existed, which hid the hyphen-only flag names |
| 2026-11-23 | `tac run` refuses a mission that already has a run record (exit `2`); `tac await` also serves a mission left `ship_stopped` by an interrupted run | Live on 2026-11-23: a stopped route loop deleted the file a still-running builder had written and started a second builder in the same factory, and an interrupted `tac run` left a mission in `ship_stopped` with a run record and no way to re-attach. One builder per mission; re-attach with `tac await`. |
| 2026-11-23 | A builder terminal that Orca reports as `exited` with no exit line ends the wait at once (`TAC_BUILDER_TERMINAL_GONE`) | Live on 2026-11-23: `orca terminal read` on a closed terminal answers `ok: true` with `status: "exited"` and an empty tail, not an error, so the first version of the wait would have polled the full 900 s limit. |

### 20.4 What to build next

1. After Mike confirms this blueprint, scaffold `~/Developer/rig-meta-factory` and execute Steps 1–11 sequentially using `git tag` checkpoints.
2. Symlink `~/.local/bin/rmf -> ~/Developer/rig-meta-factory/.venv/bin/rmf` and run `bama audit-cli --cli rmf --json`.
3. Register `rmf mcp` in Aside/Autark MCP config so lead and owner agents can operate the meta-factory directly.
4. Post-v1: add `launchd` cron runner adapter and Work Canvas board sync for visual factory telemetry.
