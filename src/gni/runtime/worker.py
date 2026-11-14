"""Single-job worker orchestration with persist-before-ack semantics."""
from __future__ import annotations

from dataclasses import dataclass

from gni.contracts.jobs import JobStatus
from gni.persistence.base import JobRecord, JobRepository
from gni.queue.base import JobQueue
from gni.runtime.dispatcher import AgentDispatcher, DispatchError


@dataclass(frozen=True, slots=True)
class WorkerOutcome:
    job_id: str
    status: JobStatus | None
    action: str


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

    def process_one(self) -> WorkerOutcome | None:
        reservation = self._queue.reserve(
            worker_id=self._worker_id,
            visibility_timeout_seconds=self._visibility_timeout,
        )
        if reservation is None:
            return None

        job_id = reservation.job.id
        record = self._repository.get(job_id)
        if record is None:
            # Never execute a queue item whose authoritative record is missing.
            self._queue.acknowledge(reservation)
            return WorkerOutcome(job_id, None, "discarded_missing_record")

        job = record.job
        if job.status == JobStatus.FAILED:
            self._queue.dead_letter(
                reservation,
                error_code=(record.error.get("error_code") or "job_failed"),
            )
            return WorkerOutcome(job_id, job.status, "dead_lettered_terminal")
        if job.status in {JobStatus.SUCCEEDED, JobStatus.CANCELLED}:
            self._queue.acknowledge(reservation)
            return WorkerOutcome(job_id, job.status, "acknowledged_terminal")

        if job.status == JobStatus.RUNNING:
            # A previous worker likely died after persisting RUNNING. Requeue
            # through an explicit compare-and-set before starting another attempt.
            recovered = job.recover()
            if not self._repository.save(
                JobRecord(recovered, error={"error_code": "worker_interrupted"}),
                expected_status=JobStatus.RUNNING,
            ):
                self._queue.release(reservation)
                return WorkerOutcome(job_id, job.status, "state_conflict")
            job = recovered
            if job.status == JobStatus.FAILED:
                self._queue.dead_letter(reservation, error_code="attempts_exhausted")
                return WorkerOutcome(job_id, job.status, "failed_after_interrupted_attempt")

        running = job.start()
        if not self._repository.save(
            JobRecord(running), expected_status=job.status
        ):
            self._queue.release(reservation)
            return WorkerOutcome(job_id, job.status, "state_conflict")

        try:
            final_job, outcome = self._dispatcher.execute_running(running)
            if final_job.status == JobStatus.SUCCEEDED:
                final_record = JobRecord(final_job, output=outcome)
            elif final_job.status == JobStatus.RETRYABLE:
                final_record = JobRecord(final_job, error=outcome)
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
            # Do not acknowledge: a durable queue may redeliver after visibility expiry.
            self._queue.release(reservation)
            return WorkerOutcome(job_id, final_job.status, "result_persistence_conflict")

        if final_job.status == JobStatus.RETRYABLE:
            self._queue.release(reservation, delay_seconds=self._retry_delay)
            return WorkerOutcome(job_id, final_job.status, "released_for_retry")

        # Result is durable before acknowledgement. If ack fails, redelivery sees
        # the terminal record and will acknowledge without running the agent again.
        if final_job.status == JobStatus.FAILED:
            error_code = final_record.error.get("error_code") or "job_failed"
            self._queue.dead_letter(reservation, error_code=error_code)
            return WorkerOutcome(job_id, final_job.status, "dead_lettered")
        self._queue.acknowledge(reservation)
        return WorkerOutcome(job_id, final_job.status, "completed")
