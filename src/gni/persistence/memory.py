"""Non-durable in-memory repository for tests and local development only."""
from __future__ import annotations

from threading import RLock

from gni.contracts.jobs import JobStatus
from gni.persistence.base import JobRecord


class InMemoryJobRepository:
    """Thread-safe test adapter; never use as cross-run production storage."""

    def __init__(self) -> None:
        self._records: dict[str, JobRecord] = {}
        self._idempotency: dict[str, str] = {}
        self._lock = RLock()

    def create(self, record: JobRecord, *, idempotency_key: str) -> bool:
        if not idempotency_key:
            raise ValueError("idempotency_key must not be empty.")
        with self._lock:
            if idempotency_key in self._idempotency:
                return False
            if record.job.id in self._records:
                raise ValueError("Job ID already exists.")
            self._records[record.job.id] = record
            self._idempotency[idempotency_key] = record.job.id
            return True

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._records.get(job_id)

    def save(self, record: JobRecord, *, expected_status: JobStatus) -> bool:
        with self._lock:
            current = self._records.get(record.job.id)
            if current is None or current.job.status != expected_status:
                return False
            self._records[record.job.id] = record
            return True
