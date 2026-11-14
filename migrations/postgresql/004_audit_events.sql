-- Durable audit trail for security and operational events.
BEGIN;
CREATE TABLE IF NOT EXISTS audit_events (
    event_id UUID PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL,
    actor_type VARCHAR(128) NOT NULL,
    actor_id VARCHAR(255),
    action VARCHAR(128) NOT NULL,
    resource_type VARCHAR(128) NOT NULL,
    resource_id VARCHAR(255),
    outcome VARCHAR(128) NOT NULL,
    trace_id VARCHAR(255),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(metadata) = 'object')
);
CREATE INDEX IF NOT EXISTS audit_events_occurred_idx
    ON audit_events (occurred_at DESC);
CREATE INDEX IF NOT EXISTS audit_events_resource_idx
    ON audit_events (resource_type, resource_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS audit_events_trace_idx
    ON audit_events (trace_id)
    WHERE trace_id IS NOT NULL;
INSERT INTO schema_migrations (version)
VALUES ('004_audit_events')
ON CONFLICT (version) DO NOTHING;
COMMIT;
