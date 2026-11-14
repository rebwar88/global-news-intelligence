"""Durable PostgreSQL queue with visibility timeout and fenced reservations."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from gni.contracts.jobs import Job, JobStatus
from gni.queue.base import QueueReservation


class PostgresJobQueue:
    """PostgreSQL JobQueue adapter; inject the shared psycopg connection pool."""

    def __init__(self, pool: Any) -> None:
        if pool is None or not callable(getattr(pool, "connection", None)):
            raise TypeError("pool must provide connection().")
        self._pool = pool

    def enqueue(self, job: Job, *, idempotency_key: str) -> bool:
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key is required.")
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO queue_messages
                (id,job_id,idempotency_key,status,available_at,created_at,updated_at)
                VALUES (gen_random_uuid(),%s,%s,'pending',NOW(),NOW(),NOW())
                ON CONFLICT (idempotency_key) DO NOTHING""",
                (job.id, idempotency_key),
            )
            if cur.rowcount == 1:
                return True
            cur.execute(
                "SELECT job_id FROM queue_messages WHERE idempotency_key=%s",
                (idempotency_key,),
            )
            existing = cur.fetchone()
            if existing is None or existing[0] != job.id:
                raise ValueError("Queue idempotency key is already bound to another job.")
            return False

    def reserve(
        self, *, worker_id: str, visibility_timeout_seconds: int
    ) -> QueueReservation | None:
        if not worker_id or len(worker_id) > 128:
            raise ValueError("worker_id must be a non-empty string up to 128 characters.")
        if visibility_timeout_seconds < 1:
            raise ValueError("visibility_timeout_seconds must be positive.")
        now = datetime.now(UTC)
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """WITH candidate AS (
                    SELECT id FROM queue_messages
                    WHERE (status='pending' AND available_at<=%s)
                       OR (status='processing' AND locked_until<=%s)
                    ORDER BY available_at,created_at
                    FOR UPDATE SKIP LOCKED LIMIT 1
                ), claimed AS (
                    UPDATE queue_messages q
                    SET status='processing',worker_id=%s,
                        reservation_token=gen_random_uuid(),
                        locked_until=%s + (%s * INTERVAL '1 second'),
                        updated_at=%s
                    FROM candidate c
                    WHERE q.id=c.id
                    RETURNING q.job_id,q.reservation_token
                )
                SELECT j.*,claimed.reservation_token
                FROM claimed JOIN jobs j ON j.id=claimed.job_id""",
                (now, now, worker_id, now, visibility_timeout_seconds, now),
            )
            row = cur.fetchone()
            if row is None:
                return None
            job = Job(
                id=row["id"], agent_id=row["agent_id"],
                status=JobStatus(row["status"]), payload=row["payload"],
                attempt=row["attempt"], max_attempts=row["max_attempts"],
                trace_id=row["trace_id"], contract_version=row["contract_version"],
                created_at=row["created_at"],
            )
            return QueueReservation(
                reservation_id=str(row["reservation_token"]), job=job
            )

    def acknowledge(self, reservation: QueueReservation) -> None:
        token = self._token(reservation.reservation_id)
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE queue_messages SET status='acknowledged',worker_id=NULL,
                reservation_token=NULL,locked_until=NULL,updated_at=NOW()
                WHERE job_id=%s AND status='processing' AND reservation_token=%s""",
                (reservation.job.id, token),
            )
            if cur.rowcount != 1:
                raise ValueError("Unknown or stale queue reservation.")

    def release(
        self, reservation: QueueReservation, *, delay_seconds: int = 0
    ) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative.")
        token = self._token(reservation.reservation_id)
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE queue_messages SET status='pending',worker_id=NULL,
                reservation_token=NULL,locked_until=NULL,
                available_at=NOW() + (%s * INTERVAL '1 second'),updated_at=NOW()
                WHERE job_id=%s AND status='processing' AND reservation_token=%s""",
                (delay_seconds, reservation.job.id, token),
            )
            if cur.rowcount != 1:
                raise ValueError("Unknown or stale queue reservation.")

    def dead_letter(self, reservation: QueueReservation, *, error_code: str) -> None:
        if not error_code or len(error_code) > 128:
            raise ValueError("error_code must contain 1-128 characters.")
        token = self._token(reservation.reservation_id)
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """WITH moved AS (
                    UPDATE queue_messages SET status='acknowledged',worker_id=NULL,
                    reservation_token=NULL,locked_until=NULL,updated_at=NOW()
                    WHERE job_id=%s AND status='processing' AND reservation_token=%s
                    RETURNING id,job_id,idempotency_key,created_at
                )
                INSERT INTO dead_letter_messages
                (job_id,queue_message_id,idempotency_key,error_code,attempt_count,original_created_at)
                SELECT moved.job_id,moved.id,moved.idempotency_key,%s,j.attempt,moved.created_at
                FROM moved JOIN jobs j ON j.id=moved.job_id
                ON CONFLICT (job_id) WHERE resolved_at IS NULL DO NOTHING""",
                (reservation.job.id, token, error_code),
            )
            if cur.rowcount != 1:
                raise ValueError("Unknown, stale, or already dead-lettered reservation.")


    def list_dead_letters(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000.")
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT id,job_id,error_code,attempt_count,failed_at,
                queue_message_id,idempotency_key FROM dead_letter_messages
                WHERE resolved_at IS NULL ORDER BY failed_at ASC LIMIT %s""",
                (limit,),
            )
            return tuple(cur.fetchall())

    def requeue_dead_letter(self, job_id: str, *, reason: str) -> bool:
        if not reason or not reason.strip() or len(reason) > 500:
            raise ValueError("A replay reason of 1-500 characters is required.")
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT id FROM dead_letter_messages
                WHERE job_id=%s AND resolved_at IS NULL FOR UPDATE""",
                (job_id,),
            )
            dead_letter = cur.fetchone()
            if dead_letter is None:
                return False
            cur.execute(
                """UPDATE jobs SET status='retryable',attempt=0,error='{}'::jsonb,
                updated_at=NOW() WHERE id=%s AND status='failed'""",
                (job_id,),
            )
            if cur.rowcount != 1:
                raise ValueError("Only a failed job can be manually replayed.")
            cur.execute(
                """UPDATE queue_messages SET status='pending',worker_id=NULL,
                reservation_token=NULL,locked_until=NULL,available_at=NOW(),updated_at=NOW()
                WHERE job_id=%s AND status='acknowledged'""",
                (job_id,),
            )
            if cur.rowcount != 1:
                raise RuntimeError("The original queue message is unavailable for replay.")
            cur.execute(
                """UPDATE dead_letter_messages SET resolved_at=NOW(),resolution=%s
                WHERE id=%s AND resolved_at IS NULL""",
                (reason.strip(), dead_letter[0]),
            )
            if cur.rowcount != 1:
                raise RuntimeError("Dead-letter record changed during replay.")
            return True

    @staticmethod
    def _token(value: str) -> UUID:
        try:
            return UUID(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid queue reservation token.") from exc
