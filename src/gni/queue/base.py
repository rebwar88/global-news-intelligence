"""Durable queue contract.

Production adapters must implement visibility timeouts, durable storage,
acknowledgement, delayed retry and idempotent enqueue semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from gni.contracts.jobs import Job


@dataclass(frozen=True, slots=True)
class QueueReservation:
    reservation_id: str
    job: Job


class JobQueue(Protocol):
    def enqueue(self, job: Job, *, idempotency_key: str) -> bool: ...
    def reserve(self, *, worker_id: str, visibility_timeout_seconds: int) -> QueueReservation | None: ...
    def acknowledge(self, reservation: QueueReservation) -> None: ...
    def release(self, reservation: QueueReservation, *, delay_seconds: int = 0) -> None: ...
    def dead_letter(self, reservation: QueueReservation, *, error_code: str) -> None: ...
