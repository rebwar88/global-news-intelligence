from dataclasses import dataclass

import pytest

from gni.contracts import AgentResult, AgentStatus, Job, JobStatus
from gni.queue import InMemoryJobQueue
from gni.registry import PluginManifest, PluginRegistry
from gni.runtime import AgentDispatcher


def test_job_state_machine_and_retry_limit():
    job = Job(agent_id="sample-agent", payload={"item": 1}, max_attempts=2)
    running = job.start()
    assert running.status == JobStatus.RUNNING
    retryable = running.retry()
    assert retryable.status == JobStatus.RETRYABLE
    final_attempt = retryable.start()
    failed = final_attempt.retry()
    assert failed.status == JobStatus.FAILED
    with pytest.raises(ValueError):
        failed.start()


def test_job_rejects_non_json_payload():
    with pytest.raises(ValueError):
        Job(agent_id="sample-agent", payload={"bad": object()})


def test_memory_queue_is_idempotent_and_reserves():
    queue = InMemoryJobQueue()
    job = Job(agent_id="sample-agent", payload={})
    assert queue.enqueue(job, idempotency_key=job.id) is True
    assert queue.enqueue(job, idempotency_key=job.id) is False
    reservation = queue.reserve(worker_id="test-worker", visibility_timeout_seconds=30)
    assert reservation is not None
    queue.acknowledge(reservation)
    assert queue.reserve(worker_id="test-worker", visibility_timeout_seconds=30) is None


@dataclass
class ExampleAgent:
    agent_id: str = "sample-agent"

    def run(self, context, payload):
        assert context.job_id
        return AgentResult(status=AgentStatus.SUCCEEDED, output={"processed": payload["value"]})


def test_dispatcher_runs_only_injected_agent_with_permission_grants():
    registry = PluginRegistry()
    registry.register(
        PluginManifest.from_mapping({
            "id": "sample-agent",
            "name": "Sample",
            "version": "1.0.0",
            "enabled": False,
            "entrypoint": "sample:run",
            "permissions": ["sample.read"],
        })
    )
    dispatcher = AgentDispatcher(
        registry=registry,
        agents={"sample-agent": ExampleAgent()},
        granted_permissions={"sample-agent": frozenset({"sample.read"})},
    )
    final_job, output = dispatcher.execute(
        Job(agent_id="sample-agent", payload={"value": "ok"})
    )
    assert final_job.status == JobStatus.SUCCEEDED
    assert output == {"processed": "ok"}


def test_dispatcher_rejects_missing_permissions():
    registry = PluginRegistry()
    registry.register(
        PluginManifest.from_mapping({
            "id": "sample-agent",
            "name": "Sample",
            "version": "1.0.0",
            "enabled": False,
            "entrypoint": "sample:run",
            "permissions": ["sample.read"],
        })
    )
    dispatcher = AgentDispatcher(
        registry=registry,
        agents={"sample-agent": ExampleAgent()},
    )
    with pytest.raises(Exception):
        dispatcher.execute(Job(agent_id="sample-agent", payload={"value": "ok"}))
