"""Dependency-aware workflow scheduler over provider-neutral job contracts."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from gni.contracts.jobs import JobStatus
from gni.orchestration.planner import Orchestrator, WorkflowPlan
from gni.orchestration.submission import (
    JobSubmissionError,
    JobSubmissionService,
    SubmissionPendingError,
)
from gni.persistence.base import JobRepository
from gni.persistence.workflows import (
    WorkflowRepository,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStepState,
    WorkflowStepStatus,
)


class WorkflowConcurrencyError(RuntimeError):
    """Raised when a workflow run changes too often to advance safely."""


class WorkflowScheduler:
    """Starts runs and releases steps only after all dependencies succeed."""

    def __init__(
        self,
        *,
        orchestrator: Orchestrator,
        submission: JobSubmissionService,
        workflow_repository: WorkflowRepository,
        job_repository: JobRepository,
    ) -> None:
        self._orchestrator = orchestrator
        self._submission = submission
        self._workflow_repository = workflow_repository
        self._job_repository = job_repository

    def start(self, workflow: WorkflowPlan) -> WorkflowRun:
        plan = self._orchestrator.plan(workflow)
        now = datetime.now(UTC)
        run = WorkflowRun(
            workflow_id=plan.workflow_id,
            plan=plan,
            steps=tuple(
                WorkflowStepState(step_id=step.step_id)
                for step in plan.ordered_steps
            ),
            created_at=now,
            updated_at=now,
        )
        if not self._workflow_repository.create(run):
            existing = self._workflow_repository.get(plan.workflow_id)
            if existing is None or existing.plan != plan:
                raise ValueError("workflow_id already exists with a different plan.")
        return self.advance(plan.workflow_id)

    def advance(self, workflow_id: str) -> WorkflowRun:
        for _ in range(5):
            run = self._workflow_repository.get(workflow_id)
            if run is None:
                raise KeyError(f"Unknown workflow: {workflow_id}")
            if run.status != WorkflowStatus.RUNNING:
                return run

            states = {state.step_id: state for state in run.steps}
            changed = False

            for step in run.plan.ordered_steps:
                state = states[step.step_id]

                if state.status == WorkflowStepStatus.SUBMISSION_PENDING:
                    new_state = self._retry_pending_submission(run, step, state)
                    if new_state != state:
                        states[step.step_id] = new_state
                        changed = True
                    continue

                if state.job_id is not None:
                    new_state = self._refresh_job_state(state)
                    if new_state != state:
                        states[step.step_id] = new_state
                        changed = True
                    continue

                dependency_states = [
                    states[dependency] for dependency in step.depends_on
                ]
                if any(
                    dependency.status in {
                        WorkflowStepStatus.FAILED,
                        WorkflowStepStatus.BLOCKED,
                    }
                    for dependency in dependency_states
                ):
                    states[step.step_id] = replace(
                        state, status=WorkflowStepStatus.BLOCKED,
                        error_code="dependency_failed",
                    )
                    changed = True
                    continue

                if not all(
                    dependency.status == WorkflowStepStatus.SUCCEEDED
                    for dependency in dependency_states
                ):
                    continue

                new_state = self._submit_step(run, step, state)
                if new_state != state:
                    states[step.step_id] = new_state
                    changed = True

            step_states = tuple(states[state.step_id] for state in run.steps)
            next_status = self._workflow_status(step_states)
            if next_status != run.status:
                changed = True
            if not changed:
                return run

            updated = replace(
                run,
                steps=step_states,
                status=next_status,
                version=run.version + 1,
                updated_at=datetime.now(UTC),
            )
            if self._workflow_repository.save(updated, expected_version=run.version):
                return updated

        raise WorkflowConcurrencyError(
            f"Workflow {workflow_id} changed repeatedly while the scheduler advanced it."
        )

    def _submit_step(self, run: WorkflowRun, step, state: WorkflowStepState):
        key = f"workflow:{run.workflow_id}:step:{step.step_id}"
        payload = {
            "workflow_id": run.workflow_id,
            "workflow_step_id": step.step_id,
            "input": step.payload,
        }
        try:
            result = self._submission.submit(
                agent_id=step.agent_id,
                payload=payload,
                idempotency_key=key,
                trace_id=run.plan.trace_id or run.workflow_id,
            )
        except SubmissionPendingError as exc:
            return replace(
                state,
                status=WorkflowStepStatus.SUBMISSION_PENDING,
                job_id=exc.job_id,
                error_code="queue_publish_pending",
            )
        except JobSubmissionError:
            return replace(
                state,
                status=WorkflowStepStatus.FAILED,
                error_code="submission_rejected",
            )
        return self._state_from_job(state, result.job.id, result.job.status)

    def _retry_pending_submission(self, run: WorkflowRun, step, state: WorkflowStepState):
        key = f"workflow:{run.workflow_id}:step:{step.step_id}"
        payload = {
            "workflow_id": run.workflow_id,
            "workflow_step_id": step.step_id,
            "input": step.payload,
        }
        try:
            result = self._submission.submit(
                agent_id=step.agent_id,
                payload=payload,
                idempotency_key=key,
                trace_id=run.plan.trace_id or run.workflow_id,
            )
        except SubmissionPendingError as exc:
            return replace(state, job_id=exc.job_id)
        except JobSubmissionError:
            return replace(
                state,
                status=WorkflowStepStatus.FAILED,
                error_code="submission_rejected",
            )
        return self._state_from_job(state, result.job.id, result.job.status)

    def _refresh_job_state(self, state: WorkflowStepState) -> WorkflowStepState:
        record = self._job_repository.get(state.job_id)
        if record is None:
            return replace(
                state, status=WorkflowStepStatus.FAILED,
                error_code="job_record_missing",
            )
        return self._state_from_job(state, state.job_id, record.job.status)

    @staticmethod
    def _state_from_job(
        state: WorkflowStepState, job_id: str, status: JobStatus
    ) -> WorkflowStepState:
        mapping = {
            JobStatus.QUEUED: WorkflowStepStatus.QUEUED,
            JobStatus.RETRYABLE: WorkflowStepStatus.QUEUED,
            JobStatus.RUNNING: WorkflowStepStatus.RUNNING,
            JobStatus.SUCCEEDED: WorkflowStepStatus.SUCCEEDED,
            JobStatus.FAILED: WorkflowStepStatus.FAILED,
            JobStatus.CANCELLED: WorkflowStepStatus.FAILED,
        }
        return replace(
            state,
            job_id=job_id,
            status=mapping[status],
            error_code=(
                "job_cancelled" if status == JobStatus.CANCELLED else None
            ),
        )

    @staticmethod
    def _workflow_status(states: tuple[WorkflowStepState, ...]) -> WorkflowStatus:
        terminal = {
            WorkflowStepStatus.SUCCEEDED,
            WorkflowStepStatus.FAILED,
            WorkflowStepStatus.BLOCKED,
        }
        if not all(state.status in terminal for state in states):
            return WorkflowStatus.RUNNING
        if all(state.status == WorkflowStepStatus.SUCCEEDED for state in states):
            return WorkflowStatus.SUCCEEDED
        return WorkflowStatus.FAILED
