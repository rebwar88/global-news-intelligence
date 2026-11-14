from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg_pool import ConnectionPool

from gni.contracts.jobs import Job, JobStatus
from gni.observability.audit import AuditEvent
from gni.orchestration.planner import ExecutionPlan, WorkflowStep
from gni.persistence.audit_postgres import PostgresAuditSink
from gni.persistence.base import JobRecord
from gni.persistence.postgres import PostgresPersistence, PostgresWorkflowRepository
from gni.persistence.workflows import (
    WorkflowRun,
    WorkflowStepState,
    WorkflowStepStatus,
)
from gni.queue.postgres import PostgresJobQueue


@pytest.fixture(scope="module")
def persistence():
    dsn = os.environ.get("GNI_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("GNI_TEST_DATABASE_URL is not configured.")
    pool = ConnectionPool(conninfo=dsn, min_size=1, max_size=5, open=True)
    for migration_path in (
        "migrations/postgresql/001_initial_persistence.sql",
        "migrations/postgresql/002_job_queue.sql",
        "migrations/postgresql/003_dead_letter_messages.sql",
        "migrations/postgresql/004_audit_events.sql",
    ):
        migration = Path(migration_path).read_text()
        with pool.connection() as conn:
            conn.execute(migration, prepare=False)
    with pool.connection() as conn:
        conn.execute("TRUNCATE queue_messages, outbox_events, workflow_runs, jobs CASCADE")
    yield PostgresPersistence(pool)
    pool.close()


def test_postgres_job_and_transactional_outbox(persistence):
    job = Job(agent_id="collector", payload={"source": "feed"})
    record = JobRecord(job, output={"accepted": True})
    stored, created = persistence.create_job_with_outbox(
        record, idempotency_key=f"test:{uuid4()}"
    )
    assert created is True
    assert stored == record
    assert persistence.get(job.id) == record

    lease = persistence.claim_batch(
        now=datetime.now(UTC), limit=10, lease_seconds=30
    )[0]
    assert lease.event.aggregate_id == job.id
    assert lease.event.attempts == 1
    assert persistence.mark_published(
        lease.event.id, lease_token=lease.lease_token
    ) is True
    assert persistence.mark_published(
        lease.event.id, lease_token=lease.lease_token
    ) is False


def test_postgres_job_compare_and_set_and_idempotency(persistence):
    key = f"cas:{uuid4()}"
    job = Job(agent_id="collector", payload={"n": 1})
    record = JobRecord(job)
    assert persistence.create(record, idempotency_key=key) is True
    assert persistence.create(record, idempotency_key=key) is False
    running = JobRecord(job.start())
    assert persistence.save(running, expected_status=JobStatus.QUEUED) is True
    assert persistence.save(record, expected_status=JobStatus.QUEUED) is False
    assert persistence.get_by_idempotency_key(key) == running


def test_postgres_workflow_repository_round_trip_and_cas(persistence):
    now = datetime.now(UTC)
    plan = ExecutionPlan(
        workflow_id=f"wf-{uuid4()}",
        trace_id="trace-1",
        ordered_steps=(
            WorkflowStep("collect", "collector", {"url": "https://example.test"}),
            WorkflowStep("analyze", "analyst", {}, ("collect",)),
        ),
    )
    run = WorkflowRun(
        workflow_id=plan.workflow_id,
        plan=plan,
        steps=(
            WorkflowStepState("collect"),
            WorkflowStepState("analyze"),
        ),
        created_at=now,
        updated_at=now,
    )
    repository = PostgresWorkflowRepository(persistence)
    assert repository.create(run) is True
    assert repository.create(run) is False
    assert repository.get(run.workflow_id) == run
    assert repository.list_active(limit=10) == (run,)

    updated = replace(
        run,
        steps=(WorkflowStepState("collect", WorkflowStepStatus.QUEUED, "job-1"),
               WorkflowStepState("analyze")),
        version=2,
        updated_at=datetime.now(UTC),
    )
    assert repository.save(updated, expected_version=1) is True
    assert repository.save(updated, expected_version=1) is False
    assert repository.get(run.workflow_id) == updated



def test_postgres_queue_reserve_ack_and_idempotent_enqueue(persistence):
    job = Job(agent_id="collector", payload={"feed": "wire"})
    assert persistence.create(JobRecord(job), idempotency_key=f"job:{job.id}")
    queue = PostgresJobQueue(persistence._pool)
    key = f"queue:{job.id}"
    assert queue.enqueue(job, idempotency_key=key) is True
    assert queue.enqueue(job, idempotency_key=key) is False

    reservation = queue.reserve(worker_id="worker-a", visibility_timeout_seconds=30)
    assert reservation is not None
    assert reservation.job.id == job.id
    queue.acknowledge(reservation)
    assert queue.reserve(worker_id="worker-b", visibility_timeout_seconds=30) is None


def test_postgres_queue_release_and_stale_reservation_fencing(persistence):
    job = Job(agent_id="collector", payload={"feed": "wire"})
    assert persistence.create(JobRecord(job), idempotency_key=f"job:{job.id}")
    queue = PostgresJobQueue(persistence._pool)
    assert queue.enqueue(job, idempotency_key=f"queue:{job.id}")

    first = queue.reserve(worker_id="worker-a", visibility_timeout_seconds=30)
    assert first is not None
    queue.release(first)

    second = queue.reserve(worker_id="worker-b", visibility_timeout_seconds=30)
    assert second is not None
    assert second.reservation_id != first.reservation_id
    with pytest.raises(ValueError, match="stale"):
        queue.acknowledge(first)
    queue.acknowledge(second)


def test_postgres_queue_recovers_expired_visibility_lease(persistence):
    job = Job(agent_id="collector", payload={"feed": "wire"})
    assert persistence.create(JobRecord(job), idempotency_key=f"job:{job.id}")
    queue = PostgresJobQueue(persistence._pool)
    assert queue.enqueue(job, idempotency_key=f"queue:{job.id}")

    first = queue.reserve(worker_id="worker-a", visibility_timeout_seconds=30)
    assert first is not None
    with persistence._pool.connection() as conn:
        conn.execute(
            "UPDATE queue_messages SET locked_until=NOW()-INTERVAL '1 second' "
            "WHERE job_id=%s", (job.id,)
        )
    second = queue.reserve(worker_id="worker-b", visibility_timeout_seconds=30)
    assert second is not None
    assert second.reservation_id != first.reservation_id
    with pytest.raises(ValueError, match="stale"):
        queue.release(first)
    queue.acknowledge(second)



def test_postgres_queue_moves_failed_job_to_dead_letter(persistence):
    job = Job(agent_id="collector", payload={"feed": "bad"})
    assert persistence.create(JobRecord(job), idempotency_key=f"job:{job.id}")
    queue = PostgresJobQueue(persistence._pool)
    assert queue.enqueue(job, idempotency_key=f"queue:{job.id}")
    reservation = queue.reserve(worker_id="worker-a", visibility_timeout_seconds=30)
    assert reservation is not None
    queue.dead_letter(reservation, error_code="permanent_failure")
    with persistence._pool.connection() as conn:
        row = conn.execute(
            "SELECT error_code FROM dead_letter_messages WHERE job_id=%s",
            (job.id,),
        ).fetchone()
    assert row[0] == "permanent_failure"



def test_postgres_dead_letter_can_be_inspected_and_replayed(persistence):
    job = Job(
        agent_id="collector", payload={"feed": "manual-replay"},
        status=JobStatus.FAILED, attempt=2,
    )
    assert persistence.create(JobRecord(job, error={"error_code": "failed"}), idempotency_key=f"job:{job.id}")
    queue = PostgresJobQueue(persistence._pool)
    assert queue.enqueue(job, idempotency_key=f"queue:{job.id}")
    reservation = queue.reserve(worker_id="worker-a", visibility_timeout_seconds=30)
    assert reservation is not None
    queue.dead_letter(reservation, error_code="manual_review")

    dead = [item for item in queue.list_dead_letters(limit=100) if item["job_id"] == job.id]
    assert len(dead) == 1
    assert dead[0]["attempt_count"] == 2

    assert queue.requeue_dead_letter(job.id, reason="editor approved replay") is True
    assert queue.requeue_dead_letter(job.id, reason="duplicate request") is False
    refreshed = persistence.get(job.id)
    assert refreshed.job.status == JobStatus.RETRYABLE
    assert refreshed.job.attempt == 0
    assert all(item["job_id"] != job.id for item in queue.list_dead_letters(limit=100))

    replay = queue.reserve(worker_id="worker-b", visibility_timeout_seconds=30)
    assert replay is not None
    assert replay.job.status == JobStatus.RETRYABLE
    queue.acknowledge(replay)



def test_postgres_audit_sink_persists_and_filters_traceable_events(persistence):
    sink = PostgresAuditSink(persistence._pool)
    event = AuditEvent(
        action="job.processed", resource_type="job", resource_id=f"audit-{uuid4()}",
        actor_type="worker", actor_id="worker-test", trace_id=f"trace-{uuid4()}",
        outcome="succeeded",
        metadata={"attempt": 1, "api_token": "must-not-be-stored"},
    )
    sink.record(event)
    sink.record(event)
    found = sink.list_recent(
        limit=20, resource_type="job", resource_id=event.resource_id,
        trace_id=event.trace_id,
    )
    assert len(found) == 1
    assert found[0].event_id == event.event_id
    assert found[0].metadata["api_token"] == "[redacted]"


def test_postgres_runtime_health_collector_reports_durable_backlogs(persistence):
    from gni.observability import MetricsRegistry, RuntimeTelemetry
    from gni.observability.health_postgres import PostgresRuntimeHealthCollector

    metrics = MetricsRegistry()
    telemetry = RuntimeTelemetry(metrics=metrics)
    collector = PostgresRuntimeHealthCollector(persistence._pool, telemetry)

    before = collector.collect()
    queued_job = Job(agent_id="collector", payload={"health": "queue"})
    _, created = persistence.create_job_with_outbox(
        JobRecord(queued_job), idempotency_key=f"health:{queued_job.id}"
    )
    assert created is True
    queue = PostgresJobQueue(persistence._pool)
    assert queue.enqueue(queued_job, idempotency_key=f"health-queue:{queued_job.id}")

    snapshot = collector.collect()
    assert snapshot.queue_pending == before.queue_pending + 1
    assert snapshot.outbox_pending == before.outbox_pending + 1
    assert snapshot.workflows_running >= before.workflows_running
    assert snapshot.dead_letters_open >= before.dead_letters_open
    rendered = metrics.render()
    assert f"gni_queue_pending_messages {snapshot.queue_pending}" in rendered
    assert f"gni_outbox_pending_events {snapshot.outbox_pending}" in rendered
