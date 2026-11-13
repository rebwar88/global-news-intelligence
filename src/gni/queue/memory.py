"""Non-durable queue for unit tests and local development only."""
from __future__ import annotations

from collections import deque
from uuid import uuid4

from gni.contracts.jobs import Job
from gni.queue.base import QueueReservation


class InMemoryJobQueue:
    """This queue loses all state on process exit; never use it in production."""

    def __init__(self) -> None:
        self._pending: deque[Job] = deque()
        self._reserved: dict[str, Job] = {}
        self._idempotency: set[str] = set()

    def enqueue(self, job: Job, *, idempotency_key: str) -> bool:
        if not idempotency_key:
            raise ValueError("idempotency_key is required.")
        if idempotency_key in self._idempotency:
            return False
        self._idempotency.add(idempotency_key)
        self._pending.append(job)
        return True

    def reserve(
        self, *, worker_id: str, visibility_timeout_seconds: int
    ) -> QueueReservation | None:
        if not worker_id:
            raise ValueError("worker_id is required.")
        if visibility_timeout_seconds < 1:
            raise ValueError("visibility_timeout_seconds must be positive.")
        if not self._pending:
            return None
        job = self._pending.popleft()
        reservation_id = str(uuid4())
        self._reserved[reservation_id] = job
        return QueueReservation(reservation_id=reservation_id, job=job)

    def acknowledge(self, reservation: QueueReservation) -> None:
        self._take_reserved(reservation)

    def release(self, reservation: QueueReservation, *, delay_seconds: int = 0) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative.")
        if delay_seconds:
            raise NotImplementedError("Delayed delivery is not supported by the test queue.")
        job = self._take_reserved(reservation)
        self._pending.append(job)

    def _take_reserved(self, reservation: QueueReservation) -> Job:
        job = self._reserved.get(reservation.reservation_id)
        if job is None or job.id != reservation.job.id:
            raise ValueError("Unknown or stale queue reservation.")
        del self._reserved[reservation.reservation_id]
        return job
