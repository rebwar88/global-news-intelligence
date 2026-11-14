"""Supervised periodic polling for durable PostgreSQL runtime health."""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from gni.observability.health_postgres import PostgresRuntimeHealthCollector


@dataclass(frozen=True, slots=True)
class HealthMonitorStats:
    successful_collections: int
    failed_collections: int
    stopped: bool


class RuntimeHealthMonitor:
    """Polls health snapshots until stopped; errors never terminate the monitor."""

    def __init__(
        self,
        collector: PostgresRuntimeHealthCollector,
        *,
        interval_seconds: float = 30.0,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive.")
        self._collector = collector
        self._interval = interval_seconds
        self._on_error = on_error

    def run(
        self, stop_event: threading.Event, *, max_iterations: int | None = None
    ) -> HealthMonitorStats:
        succeeded = failed = iterations = 0
        while not stop_event.is_set():
            if max_iterations is not None and iterations >= max_iterations:
                break
            iterations += 1
            try:
                self._collector.collect()
                succeeded += 1
            except Exception as exc:  # noqa: BLE001 - monitor boundary
                failed += 1
                self._collector._telemetry.record_health_collection_error(
                    error_type=type(exc).__name__
                )
                if self._on_error is not None:
                    self._on_error(exc)
            if max_iterations is None or iterations < max_iterations:
                stop_event.wait(self._interval)
        return HealthMonitorStats(succeeded, failed, stop_event.is_set())
