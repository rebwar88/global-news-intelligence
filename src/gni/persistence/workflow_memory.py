"""In-memory workflow persistence for tests and local development only."""
from __future__ import annotations

from threading import RLock

from gni.persistence.workflows import WorkflowRepository, WorkflowRun


class InMemoryWorkflowRepository(WorkflowRepository):
    """Thread-safe process-local adapter; it is not durable across runner executions."""

    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRun] = {}
        self._lock = RLock()

    def create(self, run: WorkflowRun) -> bool:
        with self._lock:
            if run.workflow_id in self._runs:
                return False
            self._runs[run.workflow_id] = run
            return True

    def get(self, workflow_id: str) -> WorkflowRun | None:
        with self._lock:
            return self._runs.get(workflow_id)

    def list_active(self, *, limit: int = 100) -> tuple[WorkflowRun, ...]:
        if limit < 1:
            raise ValueError("limit must be positive.")
        with self._lock:
            return tuple(run for run in self._runs.values()
                         if run.status.value == "running")[:limit]

    def save(self, run: WorkflowRun, *, expected_version: int) -> bool:
        with self._lock:
            current = self._runs.get(run.workflow_id)
            if current is None or current.version != expected_version:
                return False
            if run.version != expected_version + 1:
                raise ValueError("Workflow version must increment exactly once.")
            self._runs[run.workflow_id] = run
            return True
