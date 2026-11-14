from __future__ import annotations

import threading

from gni.observability import MetricsRegistry, RuntimeTelemetry
from gni.runtime.health_monitor import RuntimeHealthMonitor


class FakeCollector:
    def __init__(self, *, failures: int = 0) -> None:
        self.telemetry = RuntimeTelemetry(metrics=MetricsRegistry())
        self.failures = failures
        self.calls = 0
        self.reported_errors = []

    def collect(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("database unavailable")
        return object()

    def record_collection_error(self, error):
        self.reported_errors.append(type(error).__name__)
        self.telemetry.record_health_collection_error(error_type=type(error).__name__)


def test_health_monitor_retries_after_collection_failure_and_continues():
    collector = FakeCollector(failures=1)
    errors = []
    monitor = RuntimeHealthMonitor(
        collector, interval_seconds=0.001, on_error=errors.append
    )

    stats = monitor.run(threading.Event(), max_iterations=3)

    assert stats.successful_collections == 2
    assert stats.failed_collections == 1
    assert collector.calls == 3
    assert collector.reported_errors == ["RuntimeError"]
    assert len(errors) == 1
    assert "gni_health_collection_errors_total{error_type=\"RuntimeError\"} 1" in (
        collector.telemetry.metrics.render()
    )


def test_health_monitor_rejects_non_positive_interval():
    collector = FakeCollector()
    try:
        RuntimeHealthMonitor(collector, interval_seconds=0)
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("Expected invalid interval to be rejected.")
