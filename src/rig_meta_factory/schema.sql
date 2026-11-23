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

CREATE UNIQUE INDEX IF NOT EXISTS idx_template_revisions_template_ver
    ON template_revisions(template_id, version);

CREATE INDEX IF NOT EXISTS idx_factories_state
    ON factories(state);

CREATE INDEX IF NOT EXISTS idx_owner_changelogs_factory_created
    ON owner_changelogs(factory_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_backports_template_status
    ON backports(template_id, status);

CREATE INDEX IF NOT EXISTS idx_mailbox_to_status
    ON mailbox_messages(to_factory_id, status, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_factory_crons_enabled
    ON factory_crons(enabled, factory_id);

CREATE INDEX IF NOT EXISTS idx_gate_approvals_lookup
    ON gate_approvals(action_type, target_ref, payload_sha256, status);

CREATE INDEX IF NOT EXISTS idx_tac_missions_factory_status
    ON tac_missions(factory_id, status);
