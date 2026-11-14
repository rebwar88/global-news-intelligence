from dataclasses import dataclass

import pytest

from gni.contracts import JobStatus
from gni.orchestration import (
    JobSubmissionError,
    JobSubmissionService,
    Orchestrator,
    SubmissionPendingError,
    WorkflowPlan,
    WorkflowStep,
    WorkflowValidationError,
)
from gni.persistence import InMemoryJobRepository, InMemoryOutboxRepository
from gni.queue import InMemoryJobQueue
from gni.runtime import OutboxPublisher
from gni.registry import PluginManifest, PluginRegistry


def make_registry(*agent_ids: str) -> PluginRegistry:
    registry = PluginRegistry()
    for agent_id in agent_ids:
        registry.register(PluginManifest.from_mapping({
            "id": agent_id, "name": agent_id, "version": "1.0.0",
            "enabled": False, "entrypoint": "agents:run", "permissions": [],
        }))
    return registry


def test_orchestrator_orders_dependencies_stably():
    registry = make_registry("news-collection", "story-intelligence")
    orchestrator = Orchestrator(
        registry, active_plugins={"news-collection", "story-intelligence"}
    )
    workflow = WorkflowPlan("wf-1", (
        WorkflowStep("collect", "news-collection"),
        WorkflowStep("analyze", "story-intelligence", depends_on=("collect",)),
        WorkflowStep("also-collect", "news-collection"),
    ))
    result = orchestrator.plan(workflow)
    assert [step.step_id for step in result.ordered_steps] == [
        "collect", "also-collect", "analyze"
    ]


def test_orchestrator_rejects_cycles_and_unknown_dependencies():
    registry = make_registry("news-collection")
    orchestrator = Orchestrator(registry, active_plugins={"news-collection"})
    with pytest.raises(WorkflowValidationError, match="cycle"):
        orchestrator.plan(WorkflowPlan("wf", (
            WorkflowStep("a", "news-collection", depends_on=("b",)),
            WorkflowStep("b", "news-collection", depends_on=("a",)),
        )))
    with pytest.raises(WorkflowValidationError, match="unknown dependency"):
        orchestrator.plan(WorkflowPlan("wf", (
            WorkflowStep("a", "news-collection", depends_on=("missing",)),
        )))


def test_orchestrator_rejects_inactive_and_unknown_agents():
    registry = make_registry("news-collection")
    orchestrator = Orchestrator(registry, active_plugins=set())
    with pytest.raises(WorkflowValidationError, match="inactive"):
        orchestrator.plan(WorkflowPlan("wf", (WorkflowStep("a", "news-collection"),)))
    with pytest.raises(WorkflowValidationError, match="unknown agent"):
        orchestrator.plan(WorkflowPlan("wf", (WorkflowStep("a", "unknown-agent"),)))


@dataclass
class FailingQueue:
    def enqueue(self, job, *, idempotency_key):
        raise OSError("temporary queue outage")


def test_submission_is_idempotent_and_does_not_duplicate_job():
    registry = make_registry("news-collection")
    repository = InMemoryJobRepository()
    queue = InMemoryJobQueue()
    service = JobSubmissionService(
        registry=registry, active_plugins={"news-collection"},
        repository=repository, queue=queue,
    )
    first = service.submit(
        agent_id="news-collection", payload={"source": "feed"},
        idempotency_key="feed:event:42",
    )
    second = service.submit(
        agent_id="news-collection", payload={"source": "feed"},
        idempotency_key="feed:event:42",
    )
    assert first.created is True
    assert second.created is False
    assert first.job.id == second.job.id
    assert first.queued is True and second.queued is True
    assert repository.get(first.job.id).job.status == JobStatus.QUEUED


def test_submission_rejects_idempotency_key_reuse_for_other_agent():
    registry = make_registry("news-collection", "media-collection")
    repository = InMemoryJobRepository()
    queue = InMemoryJobQueue()
    service = JobSubmissionService(
        registry=registry, active_plugins={"news-collection", "media-collection"},
        repository=repository, queue=queue,
    )
    service.submit(
        agent_id="news-collection", payload={}, idempotency_key="shared-key"
    )
    with pytest.raises(JobSubmissionError, match="different agent"):
        service.submit(
            agent_id="media-collection", payload={}, idempotency_key="shared-key"
        )


def test_queue_publish_failure_leaves_job_recoverable():
    registry = make_registry("news-collection")
    repository = InMemoryJobRepository()
    service = JobSubmissionService(
        registry=registry, active_plugins={"news-collection"},
        repository=repository, queue=FailingQueue(),
    )
    with pytest.raises(SubmissionPendingError) as error:
        service.submit(
            agent_id="news-collection", payload={},
            idempotency_key="feed:event:pending",
        )
    assert repository.get(error.value.job_id) is not None
    assert repository.get_by_idempotency_key("feed:event:pending") is not None



def test_submission_uses_atomic_outbox_and_publisher_delivers_later():
    registry = make_registry("news-collection")
    repository = InMemoryOutboxRepository()
    queue = InMemoryJobQueue()
    service = JobSubmissionService(
        registry=registry, active_plugins={"news-collection"},
        repository=repository, outbox_repository=repository, queue=queue,
    )
    result = service.submit(
        agent_id="news-collection", payload={"source": "feed"},
        idempotency_key="feed:event:atomic",
    )
    assert result.queued is True
    assert len(repository.list_events()) == 1
    assert queue.reserve(worker_id="before-publish", visibility_timeout_seconds=30) is None

    publisher = OutboxPublisher(
        outbox=repository, jobs=repository, queue=queue
    )
    assert publisher.publish_batch()[0].action == "published"
    reservation = queue.reserve(worker_id="after-publish", visibility_timeout_seconds=30)
    assert reservation.job.id == result.job.id
