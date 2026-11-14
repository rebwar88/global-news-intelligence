"""PostgreSQL adapter for durable jobs, workflow runs and transactional outbox."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from gni.contracts.jobs import Job, JobStatus
from gni.orchestration.planner import ExecutionPlan, WorkflowStep
from gni.persistence.base import JobRecord
from gni.persistence.outbox import OutboxEvent, OutboxLease, OutboxStatus
from gni.persistence.workflows import (
    WorkflowRun, WorkflowStatus, WorkflowStepState, WorkflowStepStatus,
)


class PostgresPersistence:
    """Combined adapter. Inject a psycopg_pool-compatible pool; no import-time I/O."""

    def __init__(self, pool: Any) -> None:
        if pool is None or not callable(getattr(pool, "connection", None)):
            raise TypeError("pool must provide connection().")
        self._pool = pool

    @staticmethod
    def _job_values(record: JobRecord, key: str) -> tuple[Any, ...]:
        j = record.job
        return (j.id, j.agent_id, j.status.value, Jsonb(j.payload), Jsonb(record.output),
                Jsonb(record.error), j.attempt, j.max_attempts, j.trace_id,
                j.contract_version, key, j.created_at)

    @staticmethod
    def _job(row: dict[str, Any] | None) -> JobRecord | None:
        if row is None:
            return None
        job = Job(
            id=row["id"], agent_id=row["agent_id"], status=JobStatus(row["status"]),
            payload=row["payload"], attempt=row["attempt"],
            max_attempts=row["max_attempts"], trace_id=row["trace_id"],
            contract_version=row["contract_version"], created_at=row["created_at"],
        )
        return JobRecord(job, output=row["output"], error=row["error"])

    @staticmethod
    def _plan_data(run: WorkflowRun) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        plan = {
            "workflow_id": run.plan.workflow_id, "trace_id": run.plan.trace_id,
            "ordered_steps": [
                {"step_id": s.step_id, "agent_id": s.agent_id, "payload": s.payload,
                 "depends_on": list(s.depends_on)}
                for s in run.plan.ordered_steps
            ],
        }
        steps = [
            {"step_id": s.step_id, "status": s.status.value, "job_id": s.job_id,
             "error_code": s.error_code} for s in run.steps
        ]
        return plan, steps

    @staticmethod
    def _run(row: dict[str, Any] | None) -> WorkflowRun | None:
        if row is None:
            return None
        raw = row["plan"]
        plan = ExecutionPlan(
            workflow_id=raw["workflow_id"], trace_id=raw.get("trace_id"),
            ordered_steps=tuple(
                WorkflowStep(
                    step_id=s["step_id"], agent_id=s["agent_id"],
                    payload=s.get("payload", {}), depends_on=tuple(s.get("depends_on", ())),
                ) for s in raw["ordered_steps"]
            ),
        )
        return WorkflowRun(
            workflow_id=row["workflow_id"], plan=plan,
            steps=tuple(
                WorkflowStepState(
                    step_id=s["step_id"], status=WorkflowStepStatus(s["status"]),
                    job_id=s.get("job_id"), error_code=s.get("error_code"),
                ) for s in row["steps"]
            ),
            status=WorkflowStatus(row["status"]), version=row["version"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _event(row: dict[str, Any]) -> OutboxEvent:
        return OutboxEvent(
            id=str(row["id"]), event_type=row["event_type"],
            aggregate_id=row["aggregate_id"], idempotency_key=row["idempotency_key"],
            created_at=row["created_at"], available_at=row["available_at"],
            status=OutboxStatus(row["status"]), attempts=row["attempts"],
            lease_token=str(row["lease_token"]) if row["lease_token"] else None,
            locked_until=row["locked_until"], last_error_code=row["last_error_code"],
        )

    def get(self, job_id: str) -> JobRecord | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM jobs WHERE id=%s", (job_id,))
            return self._job(cur.fetchone())

    def get_by_idempotency_key(self, idempotency_key: str) -> JobRecord | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM jobs WHERE idempotency_key=%s", (idempotency_key,))
            return self._job(cur.fetchone())

    def save(self, record: JobRecord, *, expected_status: JobStatus) -> bool:
        j = record.job
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """UPDATE jobs SET status=%s,payload=%s,output=%s,error=%s,attempt=%s,
                max_attempts=%s,trace_id=%s,contract_version=%s,updated_at=NOW()
                WHERE id=%s AND status=%s""",
                (j.status.value, j.payload, record.output, record.error, j.attempt,
                 j.max_attempts, j.trace_id, j.contract_version, j.id, expected_status.value),
            )
            return cur.rowcount == 1

    def create_job_with_outbox(
        self, record: JobRecord, *, idempotency_key: str
    ) -> tuple[JobRecord, bool]:
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key must not be empty.")
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """INSERT INTO jobs
                (id,agent_id,status,payload,output,error,attempt,max_attempts,trace_id,
                 contract_version,idempotency_key,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (idempotency_key) DO NOTHING RETURNING id""",
                self._job_values(record, idempotency_key),
            )
            if cur.fetchone() is None:
                cur.execute("SELECT * FROM jobs WHERE idempotency_key=%s", (idempotency_key,))
                existing = self._job(cur.fetchone())
                if existing is None:
                    raise RuntimeError("Conflicting idempotent job was not found.")
                return existing, False
            cur.execute(
                """INSERT INTO outbox_events
                (id,event_type,aggregate_id,idempotency_key,status,attempts,available_at,created_at)
                VALUES (%s,'job.queued',%s,%s,'pending',0,NOW(),NOW())""",
                (uuid4(), record.job.id, idempotency_key),
            )
            return record, True

    def create(self, record: JobRecord, *, idempotency_key: str) -> bool:
        return self._create_job(record, idempotency_key)

    def _create_job(self, record: JobRecord, key: str) -> bool:
        if not key or not key.strip():
            raise ValueError("idempotency_key must not be empty.")
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """INSERT INTO jobs
                (id,agent_id,status,payload,output,error,attempt,max_attempts,trace_id,
                 contract_version,idempotency_key,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (idempotency_key) DO NOTHING""", self._job_values(record, key)
            )
            return cur.rowcount == 1

    def create_workflow(self, run: WorkflowRun) -> bool:
        plan, steps = self._plan_data(run)
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """INSERT INTO workflow_runs
                (workflow_id,plan,steps,status,version,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (workflow_id) DO NOTHING""",
                (run.workflow_id, Jsonb(plan), Jsonb(steps), run.status.value, run.version,
                 run.created_at, run.updated_at),
            )
            return cur.rowcount == 1

    def get_workflow(self, workflow_id: str) -> WorkflowRun | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM workflow_runs WHERE workflow_id=%s", (workflow_id,))
            return self._run(cur.fetchone())

    def save_workflow(self, run: WorkflowRun, *, expected_version: int) -> bool:
        if run.version != expected_version + 1:
            raise ValueError("Workflow version must increment exactly once.")
        plan, steps = self._plan_data(run)
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """UPDATE workflow_runs SET plan=%s,steps=%s,status=%s,version=%s,updated_at=%s
                WHERE workflow_id=%s AND version=%s""",
                (Jsonb(plan), Jsonb(steps), run.status.value, run.version, run.updated_at,
                 run.workflow_id, expected_version),
            )
            return cur.rowcount == 1

    def claim_batch(self, *, now: datetime, limit: int, lease_seconds: int) -> tuple[OutboxLease, ...]:
        if limit < 1 or lease_seconds < 1:
            raise ValueError("limit and lease_seconds must be positive.")
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """WITH candidates AS (
                    SELECT id FROM outbox_events
                    WHERE (status='pending' AND available_at<=%s)
                       OR (status='processing' AND locked_until<=%s)
                    ORDER BY available_at,created_at FOR UPDATE SKIP LOCKED LIMIT %s
                )
                UPDATE outbox_events e SET status='processing',attempts=e.attempts+1,
                    lease_token=gen_random_uuid(),
                    locked_until=%s + (%s * INTERVAL '1 second')
                FROM candidates c WHERE e.id=c.id RETURNING e.*""",
                (now, now, limit, now, lease_seconds),
            )
            leases = []
            for row in cur.fetchall():
                event = self._event(row)
                leases.append(OutboxLease(event, event.lease_token or ""))
            return tuple(leases)

    def mark_published(self, event_id: str, *, lease_token: str) -> bool:
        return self._finish(event_id, lease_token, OutboxStatus.PUBLISHED, None)

    def retry(self, event_id: str, *, lease_token: str, available_at: datetime,
              error_code: str) -> bool:
        return self._finish(event_id, lease_token, OutboxStatus.PENDING, error_code, available_at)

    def dead_letter(self, event_id: str, *, lease_token: str, error_code: str) -> bool:
        return self._finish(event_id, lease_token, OutboxStatus.DEAD, error_code)

    def _finish(self, event_id: str, token: str, status: OutboxStatus,
                error: str | None, available_at: datetime | None = None) -> bool:
        try:
            event_uuid, lease_uuid = UUID(event_id), UUID(token)
        except (ValueError, TypeError):
            return False
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """UPDATE outbox_events SET status=%s,last_error_code=%s,
                available_at=COALESCE(%s,available_at),lease_token=NULL,locked_until=NULL
                WHERE id=%s AND status='processing' AND lease_token=%s""",
                (status.value, error, available_at, event_uuid, lease_uuid),
            )
            return cur.rowcount == 1


class PostgresWorkflowRepository:
    """WorkflowRepository view over PostgresPersistence's shared connection pool."""

    def __init__(self, persistence: PostgresPersistence) -> None:
        self._persistence = persistence

    def create(self, run: WorkflowRun) -> bool:
        return self._persistence.create_workflow(run)

    def get(self, workflow_id: str) -> WorkflowRun | None:
        return self._persistence.get_workflow(workflow_id)

    def save(self, run: WorkflowRun, *, expected_version: int) -> bool:
        return self._persistence.save_workflow(run, expected_version=expected_version)
