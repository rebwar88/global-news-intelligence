"""PostgreSQL-backed audit sink with bounded read access."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from gni.observability.audit import AuditEvent


class PostgresAuditSink:
    """Persists sanitized audit events using an injected psycopg pool."""

    def __init__(self, pool: Any) -> None:
        if pool is None or not callable(getattr(pool, "connection", None)):
            raise TypeError("pool must provide connection().")
        self._pool = pool

    def record(self, event: AuditEvent) -> None:
        try:
            event_id = UUID(event.event_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("event_id must be a UUID.") from exc
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO audit_events
                (event_id,occurred_at,actor_type,actor_id,action,resource_type,
                 resource_id,outcome,trace_id,metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (event_id) DO NOTHING""",
                (event_id, event.occurred_at, event.actor_type, event.actor_id,
                 event.action, event.resource_type, event.resource_id, event.outcome,
                 event.trace_id, Jsonb(event.metadata)),
            )

    def list_recent(
        self, *, limit: int = 100, resource_type: str | None = None,
        resource_id: str | None = None, trace_id: str | None = None,
    ) -> tuple[AuditEvent, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000.")
        clauses: list[str] = []
        values: list[Any] = []
        if resource_type is not None:
            clauses.append("resource_type=%s")
            values.append(resource_type)
        if resource_id is not None:
            clauses.append("resource_id=%s")
            values.append(resource_id)
        if trace_id is not None:
            clauses.append("trace_id=%s")
            values.append(trace_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT * FROM audit_events {where} ORDER BY occurred_at DESC LIMIT %s",
                values,
            )
            rows = cur.fetchall()
        return tuple(
            AuditEvent(
                event_id=str(row["event_id"]), occurred_at=row["occurred_at"],
                actor_type=row["actor_type"], actor_id=row["actor_id"],
                action=row["action"], resource_type=row["resource_type"],
                resource_id=row["resource_id"], outcome=row["outcome"],
                trace_id=row["trace_id"], metadata=row["metadata"],
            )
            for row in rows
        )
