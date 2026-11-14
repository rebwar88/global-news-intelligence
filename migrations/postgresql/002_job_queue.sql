-- Durable PostgreSQL-backed job queue.
BEGIN;

CREATE TABLE IF NOT EXISTS queue_messages (
    id UUID PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE RESTRICT,
    idempotency_key TEXT NOT NULL UNIQUE,
    status VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'processing', 'acknowledged')
    ),
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    worker_id VARCHAR(128),
    reservation_token UUID,
    locked_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (status = 'processing' AND worker_id IS NOT NULL
         AND reservation_token IS NOT NULL AND locked_until IS NOT NULL)
        OR
        (status <> 'processing' AND worker_id IS NULL
         AND reservation_token IS NULL AND locked_until IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS queue_messages_ready_idx
    ON queue_messages (available_at, created_at)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS queue_messages_expired_idx
    ON queue_messages (locked_until)
    WHERE status = 'processing';

INSERT INTO schema_migrations (version)
VALUES ('002_postgresql_job_queue')
ON CONFLICT (version) DO NOTHING;

COMMIT;
