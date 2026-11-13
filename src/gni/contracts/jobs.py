"""Versioned job contract and safe state transitions."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRYABLE = "retryable"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


@dataclass(frozen=True, slots=True)
class Job:
    agent_id: str
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid4()))
    status: JobStatus = JobStatus.QUEUED
    attempt: int = 0
    max_attempts: int = 3
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    contract_version: str = "1.0"

    def __post_init__(self) -> None:
        if not self.agent_id or len(self.agent_id) > 63:
            raise ValueError("agent_id must be a non-empty identifier.")
        if not isinstance(self.status, JobStatus):
            raise TypeError("status must be a JobStatus value.")
        if not isinstance(self.payload, dict):
            raise TypeError("Job payload must be a mapping.")
        try:
            json.dumps(self.payload, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Job payload must contain JSON-safe values.") from exc
        if self.attempt < 0 or self.max_attempts < 1 or self.attempt > self.max_attempts:
            raise ValueError("Invalid job attempt limits.")

    def start(self) -> Job:
        if self.status not in {JobStatus.QUEUED, JobStatus.RETRYABLE}:
            raise ValueError(f"Cannot start job from {self.status}.")
        if self.attempt >= self.max_attempts:
            raise ValueError("Job has exhausted its retry limit.")
        return replace(self, status=JobStatus.RUNNING, attempt=self.attempt + 1)

    def succeed(self) -> Job:
        self._require_running()
        return replace(self, status=JobStatus.SUCCEEDED)

    def retry(self) -> "Job":
        self._require_running()
        next_status = JobStatus.RETRYABLE if self.attempt < self.max_attempts else JobStatus.FAILED
        return replace(self, status=next_status)

    def fail(self) -> "Job":
        self._require_running()
        return replace(self, status=JobStatus.FAILED)

    def cancel(self) -> "Job":
        if self.status in _TERMINAL:
            raise ValueError(f"Cannot cancel terminal job in {self.status}.")
        return replace(self, status=JobStatus.CANCELLED)

    def _require_running(self) -> None:
        if self.status != JobStatus.RUNNING:
            raise ValueError(f"Expected running job, got {self.status}.")
