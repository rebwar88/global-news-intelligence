"""Transactional outbox contracts and a test-only in-memory implementation."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from gni.persistence.base import JobRecord
from gni.persistence.memory import InMemoryJobRepository


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PUBLISHED = "published"
    DEAD = "dead"


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    id: str
    event_type: str
    aggregate_id: str
    idempotency_key: str
    created_at: datetime
    available_at: datetime
    status: OutboxStatus = OutboxStatus.PENDING
    attempts: int = 0
    lease_token: str | None = None
    locked_until: datetime | None = None
    last_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class OutboxLease:
    event: OutboxEvent
    lease_token: str


class SubmissionRepository(Protocol):
    """Must atomically persist a job and its outbox event in one DB transaction."""

    def create_job_with_outbox(
        self, record: JobRecord, *, idempotency_key: str
    ) -> tuple[JobRecord, bool]: ...


class OutboxRepository(Protocol):
    def claim_batch(
        self, *, now: datetime, limit: int, lease_seconds: int
    ) -> tuple[OutboxLease, ...]: ...
    def mark_published(self, event_id: str, *, lease_token: str) -> bool: ...
    def retry(
        self, event_id: str, *, lease_token: str, available_at: datetime,
        error_code: str,
    ) -> bool: ...
    def dead_letter(
        self, event_id: str, *, lease_token: str, error_code: str
    ) -> bool: ...


class InMemoryOutboxRepository(InMemoryJobRepository):
    """Atomic in-process adapter for tests only; not durable across restarts."""

    def __init__(self) -> None:
        super().__init__()
        self._events: dict[str, OutboxEvent] = {}
        self._event_by_key: dict[str, str] = {}

    def create_job_with_outbox(
        self, record: JobRecord, *, idempotency_key: str
    ) -> tuple[JobRecord, bool]:
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key must not be empty.")
        with self._lock:
            existing_id = self._idempotency.get(idempotency_key)
            if existing_id is not None:
                return self._records[existing_id], False
            if record.job.id in self._records:
                raise ValueError("Job ID already exists.")
            now = datetime.now(UTC)
            event = OutboxEvent(
                id=str(uuid4()),
                event_type="job.queued",
                aggregate_id=record.job.id,
                idempotency_key=idempotency_key,
                created_at=now,
                available_at=now,
            )
            self._records[record.job.id] = record
            self._idempotency[idempotency_key] = record.job.id
            self._events[event.id] = event
            self._event_by_key[idempotency_key] = event.id
            return record, True

    def claim_batch(
        self, *, now: datetime, limit: int, lease_seconds: int
    ) -> tuple[OutboxLease, ...]:
        if limit < 1 or lease_seconds < 1:
            raise ValueError("limit and lease_seconds must be positive.")
        leases: list[OutboxLease] = []
        with self._lock:
            eligible = sorted(
                self._events.values(), key=lambda event: (event.available_at, event.created_at)
            )
            for event in eligible:
                expired = (
                    event.status == OutboxStatus.PROCESSING
                    and event.locked_until is not None
                    and event.locked_until <= now
                )
                pending = (
                    event.status == OutboxStatus.PENDING and event.available_at <= now
                )
                if not (pending or expired):
                    continue
                token = str(uuid4())
                claimed = replace(
                    event,
                    status=OutboxStatus.PROCESSING,
                    attempts=event.attempts + 1,
                    lease_token=token,
                    locked_until=now + timedelta(seconds=lease_seconds),
                )
                self._events[event.id] = claimed
                leases.append(OutboxLease(claimed, token))
                if len(leases) >= limit:
                    break
        return tuple(leases)

    def mark_published(self, event_id: str, *, lease_token: str) -> bool:
        return self._finish(
            event_id, lease_token,
            status=OutboxStatus.PUBLISHED, error_code=None,
        )

    def retry(
        self, event_id: str, *, lease_token: str, available_at: datetime,
        error_code: str,
    ) -> bool:
        with self._lock:
            current = self._events.get(event_id)
            if current is None or current.status != OutboxStatus.PROCESSING:
                return False
            if current.lease_token != lease_token:
                return False
            self._events[event_id] = replace(
                current, status=OutboxStatus.PENDING, available_at=available_at,
                lease_token=None, locked_until=None, last_error_code=error_code,
            )
            return True

    def dead_letter(
        self, event_id: str, *, lease_token: str, error_code: str
    ) -> bool:
        return self._finish(
            event_id, lease_token, status=OutboxStatus.DEAD, error_code=error_code
        )

    def get_event(self, event_id: str) -> OutboxEvent | None:
        with self._lock:
            return self._events.get(event_id)

    def list_events(self) -> tuple[OutboxEvent, ...]:
        with self._lock:
            return tuple(self._events.values())

    def _finish(
        self, event_id: str, lease_token: str, *,
        status: OutboxStatus, error_code: str | None,
    ) -> bool:
        with self._lock:
            current = self._events.get(event_id)
            if current is None or current.status != OutboxStatus.PROCESSING:
                return False
            if current.lease_token != lease_token:
                return False
            self._events[event_id] = replace(
                current, status=status, lease_token=None, locked_until=None,
                last_error_code=error_code,
            )
            return True
