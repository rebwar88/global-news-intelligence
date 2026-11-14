"""Point-in-time health gauges collected from durable PostgreSQL runtime tables."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg.rows import dict_row

from gni.observability.telemetry import RuntimeTelemetry


@dataclass(frozen=True, slots=True)
class RuntimeHealthSnapshot:
    queue_pending: int
    queue_processing: int
    queue_expired_leases: int
    queue_oldest_pending_seconds: float
    outbox_pending: int
    outbox_processing: int
    outbox_expired_leases: int
    outbox_oldest_pending_seconds: float
    workflows_running: int
    dead_letters_open: int


class PostgresRuntimeHealthCollector:
    """Collect aggregate counts/ages only; never exports job or source identifiers."""

    def __init__(self, pool: Any, telemetry: RuntimeTelemetry) -> None:
        if pool is None or not callable(getattr(pool, "connection", None)):
            raise TypeError("pool must provide connection().")
        self._pool = pool
        self._telemetry = telemetry

    def record_collection_error(self, error: Exception) -> None:
        self._telemetry.record_health_collection_error(error_type=type(error).__name__)

    def collect(self) -> RuntimeHealthSnapshot:
        query = """
        SELECT
          (SELECT count(*) FROM queue_messages
             WHERE status='pending' AND available_at <= NOW()) AS queue_pending,
          (SELECT count(*) FROM queue_messages WHERE status='processing') AS queue_processing,
          (SELECT count(*) FROM queue_messages
             WHERE status='processing' AND locked_until <= NOW()) AS queue_expired_leases,
          (SELECT COALESCE(max(EXTRACT(EPOCH FROM NOW()-created_at)),0)
             FROM queue_messages WHERE status='pending' AND available_at <= NOW())
             AS queue_oldest_pending_seconds,
          (SELECT count(*) FROM outbox_events
             WHERE status='pending' AND available_at <= NOW()) AS outbox_pending,
          (SELECT count(*) FROM outbox_events WHERE status='processing') AS outbox_processing,
          (SELECT count(*) FROM outbox_events
             WHERE status='processing' AND locked_until <= NOW()) AS outbox_expired_leases,
          (SELECT COALESCE(max(EXTRACT(EPOCH FROM NOW()-created_at)),0)
             FROM outbox_events WHERE status='pending' AND available_at <= NOW())
             AS outbox_oldest_pending_seconds,
          (SELECT count(*) FROM workflow_runs WHERE status='running') AS workflows_running,
          (SELECT count(*) FROM dead_letter_messages WHERE resolved_at IS NULL)
             AS dead_letters_open
        """
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query)
            row = cur.fetchone()
        if row is None:
            raise RuntimeError("PostgreSQL health query returned no row.")
        snapshot = RuntimeHealthSnapshot(
            queue_pending=int(row["queue_pending"]),
            queue_processing=int(row["queue_processing"]),
            queue_expired_leases=int(row["queue_expired_leases"]),
            queue_oldest_pending_seconds=float(row["queue_oldest_pending_seconds"]),
            outbox_pending=int(row["outbox_pending"]),
            outbox_processing=int(row["outbox_processing"]),
            outbox_expired_leases=int(row["outbox_expired_leases"]),
            outbox_oldest_pending_seconds=float(row["outbox_oldest_pending_seconds"]),
            workflows_running=int(row["workflows_running"]),
            dead_letters_open=int(row["dead_letters_open"]),
        )
        self._telemetry.record_runtime_health(**{
            field: getattr(snapshot, field) for field in snapshot.__dataclass_fields__
        })
        return snapshot
