-- PostgreSQL schema for durable jobs, workflow runs and transactional outbox.
-- Apply once through a migration runner; do not execute on every application start.
BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(128) PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY,
    agent_id VARCHAR(63) NOT NULL,
    status VARCHAR(16) NOT NULL CHECK (
        status IN ('queued', 'running', 'succeeded', 'retryable', 'failed', 'cancelled')
    ),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    output JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(output) = 'object'),
    error JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(error) = 'object'),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    trace_id UUID NOT NULL,
    contract_version VARCHAR(32) NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (attempt <= max_attempts)
);

CREATE INDEX IF NOT EXISTS jobs_status_created_idx
    ON jobs (status, created_at);
CREATE INDEX IF NOT EXISTS jobs_agent_status_idx
    ON jobs (agent_id, status, created_at);

CREATE TABLE IF NOT EXISTS workflow_runs (
    workflow_id UUID PRIMARY KEY,
    plan JSONB NOT NULL CHECK (jsonb_typeof(plan) = 'object'),
    steps JSONB NOT NULL CHECK (jsonb_typeof(steps) = 'array'),
    status VARCHAR(16) NOT NULL CHECK (
        status IN ('running', 'succeeded', 'failed', 'cancelled')
    ),
    version BIGINT NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS workflow_runs_status_updated_idx
    ON workflow_runs (status, updated_at);

CREATE TABLE IF NOT EXISTS outbox_events (
    id UUID PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    aggregate_id UUID NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT,
    idempotency_key VARCHAR(255) NOT NULL UNIQUE,
    status VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'processing', 'published', 'dead')
    ),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at TIMESTAMPTZ NOT NULL,
    lease_token UUID,
    locked_until TIMESTAMPTZ,
    last_error_code VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL,
    CHECK (
        (status = 'processing' AND lease_token IS NOT NULL AND locked_until IS NOT NULL)
        OR
        (status <> 'processing' AND lease_token IS NULL AND locked_until IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS outbox_ready_idx
    ON outbox_events (available_at, created_at)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS outbox_expired_lease_idx
    ON outbox_events (locked_until)
    WHERE status = 'processing';
CREATE INDEX IF NOT EXISTS outbox_aggregate_idx
    ON outbox_events (aggregate_id);

INSERT INTO schema_migrations (version)
VALUES ('001_initial_persistence')
ON CONFLICT (version) DO NOTHING;

COMMIT;
