from dataclasses import dataclass

from gni.contracts import AgentResult, AgentStatus, Job, JobStatus
from gni.persistence import InMemoryJobRepository, JobRecord
from gni.queue import InMemoryJobQueue
from gni.registry import PluginManifest, PluginRegistry
from gni.runtime import AgentDispatcher, JobWorker


@dataclass
class SuccessAgent:
    agent_id: str = "sample-agent"

    def run(self, context, payload):
        return AgentResult(AgentStatus.SUCCEEDED, {"value": payload["value"]})


@dataclass
class RetryOnceAgent:
    calls: int = 0
    agent_id: str = "sample-agent"

    def run(self, context, payload):
        self.calls += 1
        if self.calls == 1:
            return AgentResult(AgentStatus.RETRYABLE, error_code="temporary")
        return AgentResult(AgentStatus.SUCCEEDED, {"ok": True})


def make_worker(agent, *, max_attempts=3, retry_delay=0):
    registry = PluginRegistry()
    registry.register(PluginManifest.from_mapping({
        "id": "sample-agent", "name": "Sample", "version": "1.0.0",
        "enabled": False, "entrypoint": "sample:run", "permissions": [],
    }))
    dispatcher = AgentDispatcher(
        registry=registry, agents={"sample-agent": agent},
        active_plugins=frozenset({"sample-agent"}),
    )
    queue = InMemoryJobQueue()
    repository = InMemoryJobRepository()
    job = Job(agent_id="sample-agent", payload={"value": "ok"}, max_attempts=max_attempts)
    assert repository.create(JobRecord(job), idempotency_key=job.id)
    assert queue.enqueue(job, idempotency_key=job.id)
    worker = JobWorker(
        worker_id="worker-test", queue=queue, repository=repository,
        dispatcher=dispatcher, retry_delay_seconds=retry_delay,
    )
    return worker, queue, repository, job


def test_worker_persists_success_before_acknowledgement():
    worker, queue, repository, job = make_worker(SuccessAgent())
    outcome = worker.process_one()
    record = repository.get(job.id)
    assert outcome.action == "completed"
    assert record.job.status == JobStatus.SUCCEEDED
    assert record.output == {"value": "ok"}
    assert queue.reserve(worker_id="next", visibility_timeout_seconds=30) is None


def test_worker_releases_retryable_job_and_completes_next_attempt():
    agent = RetryOnceAgent()
    worker, _, repository, job = make_worker(agent)
    first = worker.process_one()
    assert first.status == JobStatus.RETRYABLE
    assert repository.get(job.id).job.attempt == 1
    second = worker.process_one()
    assert second.status == JobStatus.SUCCEEDED
    assert repository.get(job.id).job.attempt == 2
    assert agent.calls == 2


def test_worker_acknowledges_terminal_redelivery_without_reexecution():
    agent = SuccessAgent()
    worker, queue, repository, job = make_worker(agent)
    assert worker.process_one().status == JobStatus.SUCCEEDED
    assert repository.get(job.id).job.status == JobStatus.SUCCEEDED
    assert queue.reserve(worker_id="another", visibility_timeout_seconds=30) is None


def test_worker_discards_queue_item_without_authoritative_record():
    registry = PluginRegistry()
    dispatcher = AgentDispatcher(registry=registry, agents={}, active_plugins=frozenset())
    queue = InMemoryJobQueue()
    repository = InMemoryJobRepository()
    job = Job(agent_id="missing-agent", payload={})
    queue.enqueue(job, idempotency_key=job.id)
    worker = JobWorker(
        worker_id="worker-test", queue=queue, repository=repository,
        dispatcher=dispatcher,
    )
    assert worker.process_one().action == "discarded_missing_record"


def test_job_recovery_moves_interrupted_run_to_retryable_or_failed():
    running = Job(agent_id="sample-agent", payload={}, max_attempts=2).start()
    assert running.recover().status == JobStatus.RETRYABLE
    exhausted = running.recover().start().recover()
    assert exhausted.status == JobStatus.FAILED


def test_worker_marks_exhausted_interrupted_attempt_failed():
    worker, queue, repository, job = make_worker(SuccessAgent(), max_attempts=1)
    running = job.start()
    assert repository.save(JobRecord(running), expected_status=JobStatus.QUEUED)
    outcome = worker.process_one()
    assert outcome.status == JobStatus.FAILED
    assert outcome.action == "failed_after_interrupted_attempt"
    assert repository.get(job.id).job.status == JobStatus.FAILED
    assert queue.reserve(worker_id="next", visibility_timeout_seconds=30) is None
