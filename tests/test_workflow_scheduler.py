from dataclasses import dataclass

from gni.contracts.jobs import JobStatus
from gni.orchestration import (
    JobSubmissionService,
    Orchestrator,
    WorkflowPlan,
    WorkflowStep,
)
from gni.orchestration.scheduler import WorkflowScheduler
from gni.persistence import InMemoryJobRepository, InMemoryWorkflowRepository, JobRecord
from gni.persistence.workflows import WorkflowStatus, WorkflowStepStatus
from gni.queue import InMemoryJobQueue
from gni.registry import PluginManifest, PluginRegistry


def make_registry(*agent_ids: str) -> PluginRegistry:
    registry = PluginRegistry()
    for agent_id in agent_ids:
        registry.register(PluginManifest.from_mapping({
            "id": agent_id, "name": agent_id, "version": "1.0.0",
            "enabled": False, "entrypoint": "agents:run", "permissions": [],
        }))
    return registry


def make_scheduler(queue=None):
    registry = make_registry("collector", "analyst", "translator")
    jobs = InMemoryJobRepository()
    queue = queue or InMemoryJobQueue()
    active = {"collector", "analyst", "translator"}
    submission = JobSubmissionService(
        registry=registry, active_plugins=active, repository=jobs, queue=queue
    )
    scheduler = WorkflowScheduler(
        orchestrator=Orchestrator(registry, active_plugins=active),
        submission=submission,
        workflow_repository=InMemoryWorkflowRepository(),
        job_repository=jobs,
    )
    return scheduler, jobs, queue


def complete_job(jobs, job_id, *, success=True):
    record = jobs.get(job_id)
    running = record.job.start()
    jobs.save(JobRecord(running), expected_status=JobStatus.QUEUED)
    final = running.succeed() if success else running.fail()
    jobs.save(JobRecord(final), expected_status=JobStatus.RUNNING)


def test_scheduler_only_submits_dependent_step_after_success():
    scheduler, jobs, queue = make_scheduler()
    run = scheduler.start(WorkflowPlan("wf-chain", (
        WorkflowStep("collect", "collector", {"feed": "x"}),
        WorkflowStep("analyze", "analyst", {"mode": "cluster"}, ("collect",)),
        WorkflowStep("translate", "translator", {}, ("analyze",)),
    )))
    assert run.state_for("collect").status == WorkflowStepStatus.QUEUED
    assert run.state_for("analyze").status == WorkflowStepStatus.PENDING
    assert len(queue._pending) == 1

    complete_job(jobs, run.state_for("collect").job_id)
    run = scheduler.advance("wf-chain")
    assert run.state_for("collect").status == WorkflowStepStatus.SUCCEEDED
    assert run.state_for("analyze").status == WorkflowStepStatus.QUEUED
    assert run.state_for("translate").status == WorkflowStepStatus.PENDING

    complete_job(jobs, run.state_for("analyze").job_id)
    run = scheduler.advance("wf-chain")
    assert run.state_for("translate").status == WorkflowStepStatus.QUEUED


def test_failed_step_blocks_descendants_but_not_independent_branches():
    scheduler, jobs, queue = make_scheduler()
    run = scheduler.start(WorkflowPlan("wf-branches", (
        WorkflowStep("collect", "collector"),
        WorkflowStep("independent", "translator"),
        WorkflowStep("analyze", "analyst", depends_on=("collect",)),
    )))
    complete_job(jobs, run.state_for("collect").job_id, success=False)
    run = scheduler.advance("wf-branches")
    assert run.state_for("analyze").status == WorkflowStepStatus.BLOCKED
    assert run.state_for("independent").status == WorkflowStepStatus.QUEUED
    assert run.status == WorkflowStatus.RUNNING


@dataclass
class FailOnceQueue:
    inner: InMemoryJobQueue
    failed: bool = False

    def enqueue(self, job, *, idempotency_key):
        if not self.failed:
            self.failed = True
            raise OSError("temporary queue failure")
        return self.inner.enqueue(job, idempotency_key=idempotency_key)

    def reserve(self, **kwargs):
        return self.inner.reserve(**kwargs)

    def acknowledge(self, reservation):
        self.inner.acknowledge(reservation)

    def release(self, reservation, *, delay_seconds=0):
        self.inner.release(reservation, delay_seconds=delay_seconds)


def test_scheduler_recovers_pending_queue_publication():
    queue = FailOnceQueue(InMemoryJobQueue())
    scheduler, jobs, _ = make_scheduler(queue)
    run = scheduler.start(WorkflowPlan(
        "wf-pending", (WorkflowStep("collect", "collector"),)
    ))
    assert run.state_for("collect").status == WorkflowStepStatus.SUBMISSION_PENDING
    run = scheduler.advance("wf-pending")
    assert run.state_for("collect").status == WorkflowStepStatus.QUEUED
    assert len(queue.inner._pending) == 1


def test_scheduler_marks_workflow_succeeded_after_all_steps_complete():
    scheduler, jobs, _ = make_scheduler()
    run = scheduler.start(WorkflowPlan("wf-done", (
        WorkflowStep("collect", "collector"),
    )))
    complete_job(jobs, run.state_for("collect").job_id)
    run = scheduler.advance("wf-done")
    assert run.status == WorkflowStatus.SUCCEEDED
