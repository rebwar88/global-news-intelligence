from gni.contracts.jobs import Job, JobStatus
from gni.persistence import InMemoryJobRepository, JobRecord


def test_repository_create_is_idempotent_and_retrievable() -> None:
    repository = InMemoryJobRepository()
    record = JobRecord(Job(agent_id="sample-agent", payload={"item": 1}))

    assert repository.create(record, idempotency_key="source:event:1") is True
    assert repository.create(record, idempotency_key="source:event:1") is False
    assert repository.get(record.job.id) == record


def test_repository_compare_and_set_prevents_stale_overwrite() -> None:
    repository = InMemoryJobRepository()
    job = Job(agent_id="sample-agent", payload={})
    record = JobRecord(job)
    assert repository.create(record, idempotency_key="job:1") is True

    running = JobRecord(job.start())
    assert repository.save(running, expected_status=JobStatus.QUEUED) is True
    assert repository.save(record, expected_status=JobStatus.QUEUED) is False
    assert repository.get(job.id) == running


def test_repository_rejects_empty_idempotency_key() -> None:
    repository = InMemoryJobRepository()
    record = JobRecord(Job(agent_id="sample-agent", payload={}))
    try:
        repository.create(record, idempotency_key="")
    except ValueError:
        pass
    else:
        raise AssertionError("Empty idempotency keys must be rejected.")
