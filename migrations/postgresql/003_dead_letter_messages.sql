-- Dead-letter retention for jobs that exhaust retries or fail permanently.
BEGIN;
CREATE TABLE IF NOT EXISTS dead_letter_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE RESTRICT,
    queue_message_id UUID NOT NULL,
    idempotency_key TEXT NOT NULL,
    error_code VARCHAR(128) NOT NULL,
    failed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    original_created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS dead_letter_failed_at_idx
    ON dead_letter_messages (failed_at DESC);
INSERT INTO schema_migrations (version)
VALUES ('003_dead_letter_messages')
ON CONFLICT (version) DO NOTHING;
COMMIT;
