-- Dead-letter retention and operator-controlled replay for permanently failed jobs.
BEGIN;
CREATE TABLE IF NOT EXISTS dead_letter_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT,
    queue_message_id UUID NOT NULL,
    idempotency_key TEXT NOT NULL,
    error_code VARCHAR(128) NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    failed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    resolution TEXT,
    original_created_at TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS dead_letter_one_open_per_job_idx
    ON dead_letter_messages (job_id) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS dead_letter_failed_at_idx
    ON dead_letter_messages (failed_at DESC);
INSERT INTO schema_migrations (version)
VALUES ('003_dead_letter_messages')
ON CONFLICT (version) DO NOTHING;
COMMIT;
