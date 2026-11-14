"""Polling supervisor with cooperative stop and bounded idle polling."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from collections.abc import Callable

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
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive.")
        self._worker = worker
        self._poll_interval = poll_interval_seconds
        self._on_error = on_error

    def run(self, stop_event: threading.Event, *, max_polls: int | None = None) -> SupervisorStats:
        processed = idle = errors = polls = 0
        while not stop_event.is_set():
            if max_polls is not None and polls >= max_polls:
                break
            polls += 1
            try:
                outcome: WorkerOutcome | None = self._worker.process_one()
                if outcome is None:
                    idle += 1
                    stop_event.wait(self._poll_interval)
                else:
                    processed += 1
            except Exception as exc:  # noqa: BLE001 - supervisor is the process boundary.
                errors += 1
                if self._on_error is not None:
                    self._on_error(exc)
                stop_event.wait(self._poll_interval)
        return SupervisorStats(processed, idle, errors, stop_event.is_set())
