"""Publishes durable outbox events to the queue and recovers expired leases."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter

from gni.persistence.base import JobRepository
from gni.persistence.outbox import OutboxRepository
from gni.observability.telemetry import RuntimeTelemetry
from gni.queue.base import JobQueue


@dataclass(frozen=True, slots=True)
class OutboxPublishOutcome:
    event_id: str
    action: str


class OutboxPublisher:
    """A bounded recovery worker; invoke periodically from a durable scheduler."""

    def __init__(
        self, *, outbox: OutboxRepository, jobs: JobRepository, queue: JobQueue,
        max_attempts: int = 8, lease_seconds: int = 60,
        base_retry_seconds: int = 5, max_retry_seconds: int = 900,
        telemetry: RuntimeTelemetry | None = None,
    ) -> None:
        if min(max_attempts, lease_seconds, base_retry_seconds, max_retry_seconds) < 1:
            raise ValueError("Publisher timing and retry settings must be positive.")
        self._outbox = outbox
        self._jobs = jobs
        self._queue = queue
        self._max_attempts = max_attempts
        self._lease_seconds = lease_seconds
        self._base_retry = base_retry_seconds
        self._max_retry = max_retry_seconds
        self._telemetry = telemetry

    def publish_batch(
        self, *, limit: int = 50, now: datetime | None = None
    ) -> tuple[OutboxPublishOutcome, ...]:
        current_time = now or datetime.now(UTC)
        leases = self._outbox.claim_batch(
            now=current_time, limit=limit, lease_seconds=self._lease_seconds
        )
        outcomes: list[OutboxPublishOutcome] = []
        for lease in leases:
            event_started = perf_counter()
            event = lease.event
            record = self._jobs.get(event.aggregate_id)
            if record is None:
                self._outbox.dead_letter(
                    event.id, lease_token=lease.lease_token,
                    error_code="job_record_missing",
                )
                outcomes.append(OutboxPublishOutcome(event.id, "dead_lettered_missing_job"))
                self._record("dead_lettered_missing_job", event_started)
                continue
            try:
                self._queue.enqueue(
                    record.job, idempotency_key=event.idempotency_key
                )
            except Exception:  # noqa: BLE001 - transport adapters expose vendor-specific errors.
                if event.attempts >= self._max_attempts:
                    self._outbox.dead_letter(
                        event.id, lease_token=lease.lease_token,
                        error_code="queue_publish_exhausted",
                    )
                    action = "dead_lettered"
                else:
                    delay = min(
                        self._base_retry * (2 ** (event.attempts - 1)),
                        self._max_retry,
                    )
                    self._outbox.retry(
                        event.id, lease_token=lease.lease_token,
                        available_at=current_time + timedelta(seconds=delay),
                        error_code="queue_publish_failed",
                    )
                    action = "scheduled_retry"
                outcomes.append(OutboxPublishOutcome(event.id, action))
                self._record(action, event_started)
                continue

            if self._outbox.mark_published(
                event.id, lease_token=lease.lease_token
            ):
                outcomes.append(OutboxPublishOutcome(event.id, "published"))
                self._record("published", event_started)
            else:
                # Queue enqueue is idempotent; a later lease can safely retry.
                outcomes.append(OutboxPublishOutcome(event.id, "lease_lost_after_enqueue"))
                self._record("lease_lost_after_enqueue", event_started)
        return tuple(outcomes)

    def _record(self, action: str, started: float) -> None:
        if self._telemetry is not None:
            self._telemetry.record_outbox(
                action=action, duration_seconds=max(0.0, perf_counter() - started)
            )
            if action.startswith("dead_lettered"):
                self._telemetry.record_dead_letter(action="created", reason=action)
