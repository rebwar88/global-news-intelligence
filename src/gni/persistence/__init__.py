"""Provider-neutral persistence contracts and development adapters."""

from gni.persistence.base import JobRecord, JobRepository
from gni.persistence.memory import InMemoryJobRepository
from gni.persistence.workflow_memory import InMemoryWorkflowRepository
from gni.persistence.workflows import (
    WorkflowRepository,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStepState,
    WorkflowStepStatus,
)

__all__ = [
    "InMemoryJobRepository",
    "InMemoryWorkflowRepository",
    "JobRecord",
    "JobRepository",
    "WorkflowRepository",
    "WorkflowRun",
    "WorkflowStatus",
    "WorkflowStepState",
    "WorkflowStepStatus",
]
