import os
import threading
import time
from dataclasses import dataclass

import pytest

from gni.contracts import AgentResult, AgentStatus, Job, JobStatus
from gni.contracts.agent import AgentContext
from gni.persistence import InMemoryJobRepository, JobRecord
from gni.queue import InMemoryJobQueue
from gni.registry import PluginManifest, PluginRegistry
from gni.runtime import AgentDispatcher, JobWorker, WorkerSupervisor
from gni.runtime.isolation import IsolationLimits, ProcessIsolatedAgentRunner
from gni.observability import InMemoryAuditSink, MetricsRegistry, RuntimeTelemetry


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



@dataclass
class PermanentFailureAgent:
    agent_id: str = "sample-agent"

    def run(self, context, payload):
        return AgentResult(AgentStatus.FAILED, error_code="permanent_failure")


def test_worker_sends_permanent_failure_to_dead_letter():
    worker, queue, _repository, job = make_worker(PermanentFailureAgent())
    outcome = worker.process_one()
    assert outcome.status == JobStatus.FAILED
    assert outcome.action == "dead_lettered"
    assert len(queue.dead_letters) == 1
    assert queue.dead_letters[0][0].id == job.id
    assert queue.dead_letters[0][1] == "permanent_failure"


def test_worker_supervisor_polls_and_stops_cooperatively():
    worker, _, _, _ = make_worker(SuccessAgent())
    stop = threading.Event()
    supervisor = WorkerSupervisor(worker, poll_interval_seconds=0.001)
    stats = supervisor.run(stop, max_polls=3)
    assert stats.processed == 1
    assert stats.idle_polls == 2
    assert stats.errors == 0


def test_process_isolated_runner_executes_agent_with_resource_limits():
    if os.name != "posix":
        return
    result = ProcessIsolatedAgentRunner(
        IsolationLimits(timeout_seconds=5, memory_bytes=2 * 1024 * 1024 * 1024, cpu_seconds=4)
    ).run(SuccessAgent(), AgentContext("job-1", "trace-1"), {"value": "isolated"})
    assert result.status == AgentStatus.SUCCEEDED
    assert result.output == {"value": "isolated"}



def test_process_isolated_runner_terminates_timed_out_agent():
    if os.name != "posix":
        pytest.skip("POSIX process resource limits are required.")
    @dataclass
    class SlowAgent:
        agent_id: str = "sample-agent"

        def run(self, context, payload):
            time.sleep(2)
            return AgentResult(AgentStatus.SUCCEEDED, {"late": True})

    runner = ProcessIsolatedAgentRunner(
        IsolationLimits(timeout_seconds=0.1, memory_bytes=2 * 1024 * 1024 * 1024, cpu_seconds=2)
    )
    with pytest.raises(TimeoutError):
        runner.run(SlowAgent(), AgentContext("job-timeout", "trace-timeout"), {})



def test_worker_emits_metrics_and_traceable_audit_event():
    sink = InMemoryAuditSink()
    metrics = MetricsRegistry()
    telemetry = RuntimeTelemetry(metrics=metrics, audit_sink=sink)
    registry = PluginRegistry()
    registry.register(PluginManifest.from_mapping({
        "id": "sample-agent", "name": "Sample", "version": "1.0.0",
        "enabled": False, "entrypoint": "sample:run", "permissions": [],
    }))
    dispatcher = AgentDispatcher(
        registry=registry, agents={"sample-agent": SuccessAgent()},
        active_plugins=frozenset({"sample-agent"}),
    )
    queue = InMemoryJobQueue()
    repository = InMemoryJobRepository()
    job = Job(agent_id="sample-agent", payload={"value": "ok"})
    assert repository.create(JobRecord(job), idempotency_key=job.id)
    assert queue.enqueue(job, idempotency_key=job.id)
    worker = JobWorker(
        worker_id="worker-observed", queue=queue, repository=repository,
        dispatcher=dispatcher, telemetry=telemetry,
    )

    outcome = worker.process_one()
    assert outcome.action == "completed"
    assert outcome.trace_id == job.trace_id
    event = sink.list_recent()[0]
    assert event.trace_id == job.trace_id
    assert event.resource_id == job.id
    assert event.actor_id == "worker-observed"
    assert 'gni_jobs_processed_total{action="completed",agent="sample-agent",status="succeeded"} 1' in metrics.render()



def test_supervisor_tracks_running_workers_and_poll_results():
    metrics = MetricsRegistry()
    telemetry = RuntimeTelemetry(metrics=metrics)
    worker, _, _, _ = make_worker(SuccessAgent())
    stop = threading.Event()
    supervisor = WorkerSupervisor(
        worker, poll_interval_seconds=0.001, telemetry=telemetry,
    )
    stats = supervisor.run(stop, max_polls=2)
    assert stats.processed == 1
    rendered = metrics.render()
    assert "gni_worker_starts_total 1" in rendered
    assert "gni_workers_running 0" in rendered
    assert 'gni_worker_polls_total{result="processed"} 1' in rendered
    assert 'gni_worker_polls_total{result="idle"} 1' in rendered
