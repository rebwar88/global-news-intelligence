"""Single-job worker orchestration with persist-before-ack semantics."""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from gni.contracts.jobs import JobStatus
from gni.observability.telemetry import RuntimeTelemetry
from gni.persistence.base import JobRecord, JobRepository
from gni.queue.base import JobQueue
from gni.runtime.dispatcher import AgentDispatcher, DispatchError


@dataclass(frozen=True, slots=True)
class WorkerOutcome:
    job_id: str
    status: JobStatus | None
    action: str
    trace_id: str | None = None
    agent_id: str | None = None
    attempt: int = 0


class JobWorker:
    """Processes one reservation; a supervisor controls polling and shutdown.

    The queue visibility timeout must exceed the configured maximum agent runtime.
    Durable adapters must reclaim expired reservations and fence stale workers.
    """

    def __init__(
        self,
        *,
        worker_id: str,
        queue: JobQueue,
        repository: JobRepository,
        dispatcher: AgentDispatcher,
        visibility_timeout_seconds: int = 300,
        retry_delay_seconds: int = 0,
        telemetry: RuntimeTelemetry | None = None,
    ) -> None:
        if not worker_id:
            raise ValueError("worker_id is required.")
        if visibility_timeout_seconds < 1 or retry_delay_seconds < 0:
            raise ValueError("Invalid worker timing configuration.")
        self._worker_id = worker_id
        self._queue = queue
        self._repository = repository
        self._dispatcher = dispatcher
        self._visibility_timeout = visibility_timeout_seconds
        self._retry_delay = retry_delay_seconds
        self._telemetry = telemetry or RuntimeTelemetry()

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def process_one(self) -> WorkerOutcome | None:
        started = perf_counter()
        outcome = self._process_one()
        if outcome is not None:
            self._telemetry.record_job(
                worker_id=self._worker_id,
                job_id=outcome.job_id,
                trace_id=outcome.trace_id or "",
                status=outcome.status.value if outcome.status else "unknown",
                action=outcome.action,
                duration_seconds=max(0.0, perf_counter() - started),
                attempt=outcome.attempt,
                agent_id=outcome.agent_id or "unknown",
            )
        return outcome

    def _result(
        self, reservation, status: JobStatus | None, action: str,
        *, attempt: int | None = None,
    ) -> WorkerOutcome:
        return WorkerOutcome(
            reservation.job.id, status, action, reservation.job.trace_id,
            reservation.job.agent_id,
            reservation.job.attempt if attempt is None else attempt,
        )

    def _process_one(self) -> WorkerOutcome | None:
        reservation = self._queue.reserve(
            worker_id=self._worker_id,
            visibility_timeout_seconds=self._visibility_timeout,
        )
        if reservation is None:
            return None

        job_id = reservation.job.id
        record = self._repository.get(job_id)
        if record is None:
            self._queue.acknowledge(reservation)
            return self._result(reservation, None, "discarded_missing_record")

        job = record.job
        if job.status == JobStatus.FAILED:
            self._queue.dead_letter(
                reservation,
                error_code=(record.error.get("error_code") or "job_failed"),
            )
            return self._result(reservation, job.status, "dead_lettered_terminal", attempt=job.attempt)
        if job.status in {JobStatus.SUCCEEDED, JobStatus.CANCELLED}:
            self._queue.acknowledge(reservation)
            return self._result(reservation, job.status, "acknowledged_terminal", attempt=job.attempt)

        if job.status == JobStatus.RUNNING:
            recovered = job.recover()
            if not self._repository.save(
                JobRecord(recovered, error={"error_code": "worker_interrupted"}),
                expected_status=JobStatus.RUNNING,
            ):
                self._queue.release(reservation)
                return self._result(reservation, job.status, "state_conflict", attempt=job.attempt)
            job = recovered
            if job.status == JobStatus.FAILED:
                self._queue.dead_letter(reservation, error_code="attempts_exhausted")
                return self._result(reservation, job.status, "failed_after_interrupted_attempt", attempt=job.attempt)

        running = job.start()
        if not self._repository.save(
            JobRecord(running), expected_status=job.status
        ):
            self._queue.release(reservation)
            return self._result(reservation, job.status, "state_conflict")

        try:
            final_job, outcome = self._dispatcher.execute_running(running)
            if final_job.status == JobStatus.SUCCEEDED:
                final_record = JobRecord(final_job, output=outcome)
            else:
                final_record = JobRecord(final_job, error=outcome)
        except DispatchError:
            final_job = running.fail()
            final_record = JobRecord(
                final_job,
                error={
                    "error_code": "dispatch_rejected",
                    "message": "The job could not be safely dispatched; review runtime configuration.",
                },
            )

        if not self._repository.save(final_record, expected_status=JobStatus.RUNNING):
            self._queue.release(reservation)
            return self._result(reservation, final_job.status, "result_persistence_conflict")

        if final_job.status == JobStatus.RETRYABLE:
            self._queue.release(reservation, delay_seconds=self._retry_delay)
            return self._result(reservation, final_job.status, "released_for_retry", attempt=running.attempt)

        if final_job.status == JobStatus.FAILED:
            error_code = final_record.error.get("error_code") or "job_failed"
            self._queue.dead_letter(reservation, error_code=error_code)
            return self._result(reservation, final_job.status, "dead_lettered", attempt=running.attempt)
        self._queue.acknowledge(reservation)
        return self._result(reservation, final_job.status, "completed", attempt=running.attempt)
