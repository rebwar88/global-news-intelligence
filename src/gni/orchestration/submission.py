"""Idempotent job submission boundary for orchestrated agent work."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4
from typing import Any

from gni.contracts.jobs import Job, JobStatus
from gni.persistence.base import JobRecord, JobRepository
from gni.queue.base import JobQueue
from gni.registry.registry import PluginNotFoundError, PluginRegistry


class JobSubmissionError(RuntimeError):
    """Base class for job submission failures."""


class SubmissionPendingError(JobSubmissionError):
    """The job is persisted, but queue publication did not complete."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"Job {job_id} is persisted but is not confirmed queued.")


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    job: Job
    created: bool
    queued: bool


class JobSubmissionService:
    """Persists before enqueueing and safely retries publication by idempotency key."""

    def __init__(
        self,
        *,
        registry: PluginRegistry,
        active_plugins: frozenset[str] | set[str],
        repository: JobRepository,
        queue: JobQueue,
    ) -> None:
        self._registry = registry
        self._active_plugins = frozenset(active_plugins)
        self._repository = repository
        self._queue = queue

    def submit(
        self,
        *,
        agent_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
        trace_id: str | None = None,
        max_attempts: int = 3,
    ) -> SubmissionResult:
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("A non-empty idempotency_key is required.")
        try:
            self._registry.get(agent_id)
        except PluginNotFoundError as exc:
            raise JobSubmissionError(f"Unknown agent: {agent_id}") from exc
        if agent_id not in self._active_plugins:
            raise JobSubmissionError(f"Agent is not active: {agent_id}")

        existing = self._repository.get_by_idempotency_key(idempotency_key)
        created = existing is None
        if existing is None:
            job = Job(
                agent_id=agent_id,
                payload=payload,
                trace_id=trace_id or str(uuid4()),
                max_attempts=max_attempts,
            )
            record = JobRecord(job)
            created = self._repository.create(record, idempotency_key=idempotency_key)
            if not created:
                existing = self._repository.get_by_idempotency_key(idempotency_key)
                if existing is None:
                    raise JobSubmissionError(
                        "Idempotency conflict occurred but the existing job was not found."
                    )
            else:
                existing = record

        assert existing is not None
        job = existing.job
        if job.agent_id != agent_id:
            raise JobSubmissionError(
                "Idempotency key is already associated with a different agent."
            )
        if job.status not in {JobStatus.QUEUED, JobStatus.RETRYABLE}:
            return SubmissionResult(job=job, created=False, queued=False)

        try:
            self._queue.enqueue(job, idempotency_key=idempotency_key)
        except Exception as exc:
            raise SubmissionPendingError(job.id) from exc
        # A False queue response means this idempotency key was already accepted.
        return SubmissionResult(job=job, created=created, queued=True)
