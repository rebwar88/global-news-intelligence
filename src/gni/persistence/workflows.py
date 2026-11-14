"""Provider-neutral workflow-run persistence contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from gni.orchestration.planner import ExecutionPlan


class WorkflowStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStepStatus(StrEnum):
    PENDING = "pending"
    SUBMISSION_PENDING = "submission_pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


_STEP_TERMINAL = {
    WorkflowStepStatus.SUCCEEDED,
    WorkflowStepStatus.FAILED,
    WorkflowStepStatus.BLOCKED,
}


@dataclass(frozen=True, slots=True)
class WorkflowStepState:
    step_id: str
    status: WorkflowStepStatus = WorkflowStepStatus.PENDING
    job_id: str | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    workflow_id: str
    plan: ExecutionPlan
    steps: tuple[WorkflowStepState, ...]
    status: WorkflowStatus = WorkflowStatus.RUNNING
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        expected = {step.step_id for step in self.plan.ordered_steps}
        actual = {step.step_id for step in self.steps}
        if expected != actual or len(actual) != len(self.steps):
            raise ValueError("Workflow run states must match plan steps exactly.")
        if self.version < 1:
            raise ValueError("Workflow run version must be positive.")

    def state_for(self, step_id: str) -> WorkflowStepState:
        for state in self.steps:
            if state.step_id == step_id:
                return state
        raise KeyError(step_id)


class WorkflowRepository(Protocol):
    """Persistence contract; save must atomically compare the expected version."""

    def create(self, run: WorkflowRun) -> bool: ...

    def get(self, workflow_id: str) -> WorkflowRun | None: ...

    def save(self, run: WorkflowRun, *, expected_version: int) -> bool: ...

    def list_active(self, *, limit: int = 100) -> tuple[WorkflowRun, ...]: ...
