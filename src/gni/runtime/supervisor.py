"""Polling supervisor with cooperative stop and runtime telemetry."""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from gni.observability.telemetry import RuntimeTelemetry
from gni.runtime.worker import JobWorker, WorkerOutcome


@dataclass(frozen=True, slots=True)
class SupervisorStats:
    processed: int
    idle_polls: int
    errors: int
    stopped: bool


class WorkerSupervisor:
    """Runs one worker serially; stop is graceful between jobs."""

    def __init__(
        self, worker: JobWorker, *, poll_interval_seconds: float = 1.0,
        on_error: Callable[[Exception], None] | None = None,
        telemetry: RuntimeTelemetry | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive.")
        self._worker = worker
        self._poll_interval = poll_interval_seconds
        self._on_error = on_error
        self._telemetry = telemetry or RuntimeTelemetry()

    def run(self, stop_event: threading.Event, *, max_polls: int | None = None) -> SupervisorStats:
        processed = idle = errors = polls = 0
        self._telemetry.worker_started()
        try:
            while not stop_event.is_set():
                if max_polls is not None and polls >= max_polls:
                    break
                polls += 1
                try:
                    outcome: WorkerOutcome | None = self._worker.process_one()
                    is_idle = outcome is None
                    self._telemetry.record_poll(idle=is_idle)
                    if is_idle:
                        idle += 1
                        stop_event.wait(self._poll_interval)
                    else:
                        processed += 1
                except Exception as exc:  # noqa: BLE001 - supervisor is the process boundary.
                    errors += 1
                    self._telemetry.record_worker_error(
                        worker_id=self._worker.worker_id, error=exc
                    )
                    if self._on_error is not None:
                        self._on_error(exc)
                    stop_event.wait(self._poll_interval)
        finally:
            self._telemetry.worker_stopped()
        return SupervisorStats(processed, idle, errors, stop_event.is_set())
