from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from gni.contracts.jobs import Job
from gni.persistence import InMemoryOutboxRepository, JobRecord
from gni.persistence.outbox import OutboxStatus
from gni.queue import InMemoryJobQueue
from gni.runtime.outbox_publisher import OutboxPublisher


@dataclass
class FailQueue:
    def enqueue(self, job, *, idempotency_key):
        raise OSError("private transport detail")


def create_submission(repository, key="feed:1"):
    job = Job(agent_id="collector", payload={"feed": "public"})
    record, created = repository.create_job_with_outbox(
        JobRecord(job), idempotency_key=key
    )
    return record, created


def test_job_and_outbox_event_are_created_atomically_and_idempotently():
    repository = InMemoryOutboxRepository()
    first, created = create_submission(repository)
    second, duplicate = create_submission(repository)
    assert created is True and duplicate is False
    assert first.job.id == second.job.id
    events = repository.list_events()
    assert len(events) == 1
    assert events[0].aggregate_id == first.job.id
    assert events[0].status == OutboxStatus.PENDING


def test_publisher_enqueues_and_marks_event_published():
    repository = InMemoryOutboxRepository()
    job, _ = create_submission(repository)
    queue = InMemoryJobQueue()
    publisher = OutboxPublisher(outbox=repository, jobs=repository, queue=queue)
    result = publisher.publish_batch()
    assert result[0].action == "published"
    assert repository.list_events()[0].status == OutboxStatus.PUBLISHED
    reservation = queue.reserve(worker_id="test", visibility_timeout_seconds=30)
    assert reservation.job.id == job.job.id


def test_publisher_schedules_retry_without_leaking_exception_details():
    repository = InMemoryOutboxRepository()
    create_submission(repository)
    now = datetime.now(UTC)
    publisher = OutboxPublisher(
        outbox=repository, jobs=repository, queue=FailQueue(),
        base_retry_seconds=10,
    )
    result = publisher.publish_batch(now=now)
    event = repository.list_events()[0]
    assert result[0].action == "scheduled_retry"
    assert event.status == OutboxStatus.PENDING
    assert event.last_error_code == "queue_publish_failed"
    assert event.available_at == now + timedelta(seconds=10)


def test_expired_processing_lease_is_reclaimed_with_fencing_token():
    repository = InMemoryOutboxRepository()
    create_submission(repository)
    now = datetime.now(UTC)
    first = repository.claim_batch(now=now, limit=1, lease_seconds=5)[0]
    second = repository.claim_batch(
        now=now + timedelta(seconds=6), limit=1, lease_seconds=5
    )[0]
    assert first.lease_token != second.lease_token
    assert repository.mark_published(
        first.event.id, lease_token=first.lease_token
    ) is False
    assert repository.mark_published(
        second.event.id, lease_token=second.lease_token
    ) is True


def test_publisher_dead_letters_after_max_attempts():
    repository = InMemoryOutboxRepository()
    create_submission(repository)
    now = datetime.now(UTC)
    publisher = OutboxPublisher(
        outbox=repository, jobs=repository, queue=FailQueue(),
        max_attempts=1,
    )
    result = publisher.publish_batch(now=now)
    assert result[0].action == "dead_lettered"
    assert repository.list_events()[0].status == OutboxStatus.DEAD


def test_missing_job_is_dead_lettered_without_queue_publication():
    repository = InMemoryOutboxRepository()
    record, _ = create_submission(repository)
    repository._records.pop(record.job.id)
    queue = InMemoryJobQueue()
    publisher = OutboxPublisher(outbox=repository, jobs=repository, queue=queue)
    result = publisher.publish_batch()
    assert result[0].action == "dead_lettered_missing_job"
    assert repository.list_events()[0].last_error_code == "job_record_missing"
