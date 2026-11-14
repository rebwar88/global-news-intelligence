"""Recovery scanner for durable workflow runs after restarts."""
from __future__ import annotations

from dataclasses import dataclass

from gni.orchestration.scheduler import WorkflowScheduler
from gni.persistence.workflows import WorkflowRepository, WorkflowStatus


@dataclass(frozen=True, slots=True)
class WorkflowRecoveryOutcome:
    scanned: int
    advanced: int
    completed: int
    failed: int


class WorkflowRecoveryService:
    def __init__(self, repository: WorkflowRepository, scheduler: WorkflowScheduler) -> None:
        self._repository = repository
        self._scheduler = scheduler

    def recover(self, *, limit: int = 100) -> WorkflowRecoveryOutcome:
        if limit < 1:
            raise ValueError("limit must be positive.")
        list_active = getattr(self._repository, "list_active", None)
        if not callable(list_active):
            raise TypeError("Workflow repository must implement list_active(limit=...).")
        runs = list_active(limit=limit)
        advanced = completed = failed = 0
        for run in runs:
            updated = self._scheduler.advance(run.workflow_id)
            advanced += 1
            completed += updated.status == WorkflowStatus.SUCCEEDED
            failed += updated.status == WorkflowStatus.FAILED
        return WorkflowRecoveryOutcome(len(runs), advanced, completed, failed)
