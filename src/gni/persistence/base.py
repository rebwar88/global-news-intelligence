"""Persistence contracts independent of database vendors."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from gni.contracts.jobs import Job, JobStatus


@dataclass(frozen=True, slots=True)
class JobRecord:
    """Persisted job state and its structured outcome."""

    job: Job
    output: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.output, dict) or not isinstance(self.error, dict):
            raise TypeError("Job output and error must be mappings.")


class JobRepository(Protocol):
    """Atomic persistence API; adapters must enforce compare-and-set transitions."""

    def create(self, record: JobRecord, *, idempotency_key: str) -> bool: ...
    def get(self, job_id: str) -> JobRecord | None: ...
    def save(self, record: JobRecord, *, expected_status: JobStatus) -> bool: ...
