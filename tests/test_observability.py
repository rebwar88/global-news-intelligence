from __future__ import annotations

import pytest

from gni.observability import (
    AuditEvent,
    InMemoryAuditSink,
    MetricsRegistry,
    PrometheusMetricsApp,
    RuntimeTelemetry,
)


def test_metrics_registry_renders_counters_gauges_and_histograms():
    metrics = MetricsRegistry()
    metrics.increment("gni_jobs_processed_total", labels={"status": "succeeded"})
    metrics.increment("gni_jobs_processed_total", labels={"status": "succeeded"})
    metrics.set_gauge("gni_workers_running", 1)
    metrics.observe("gni_job_processing_duration_seconds", 0.2, labels={"action": "completed"})

    rendered = metrics.render()
    assert '# TYPE gni_jobs_processed_total counter' in rendered
    assert 'gni_jobs_processed_total{status="succeeded"} 2' in rendered
    assert 'gni_workers_running 1' in rendered
    assert 'gni_job_processing_duration_seconds_count{action="completed"} 1' in rendered
    assert 'gni_job_processing_duration_seconds_bucket{action="completed",le="+Inf"} 1' in rendered


def test_metrics_registry_limits_label_cardinality_and_rejects_negative_counter():
    metrics = MetricsRegistry(max_series_per_metric=1)
    metrics.increment("gni_events_total", labels={"action": "created"})
    with pytest.raises(ValueError, match="series limit"):
        metrics.increment("gni_events_total", labels={"action": "updated"})
    with pytest.raises(ValueError, match="negative"):
        metrics.increment("gni_events_total", -1)


def test_audit_event_redacts_sensitive_metadata_recursively():
    event = AuditEvent(
        action="job.processed",
        resource_type="job",
        resource_id="job-1",
        trace_id="trace-1",
        metadata={
            "status": "failed",
            "api_key": "do-not-store",
            "nested": {"Authorization": "Bearer do-not-store"},
            "attempt": 2,
        },
    )
    assert event.metadata["api_key"] == "[redacted]"
    assert event.metadata["nested"]["Authorization"] == "[redacted]"
    assert event.metadata["status"] == "failed"


def test_in_memory_audit_sink_and_runtime_telemetry():
    sink = InMemoryAuditSink()
    telemetry = RuntimeTelemetry(audit_sink=sink)
    telemetry.record_job(
        worker_id="worker-1", job_id="job-1", trace_id="trace-1",
        status="succeeded", action="completed", duration_seconds=0.25, attempt=1,
    )
    events = sink.list_recent()
    assert len(events) == 1
    assert events[0].resource_id == "job-1"
    assert events[0].trace_id == "trace-1"
    assert telemetry.metrics.render().find("gni_jobs_processed_total") >= 0


def test_prometheus_wsgi_endpoint_serves_metrics_and_rejects_other_paths():
    metrics = MetricsRegistry()
    metrics.increment("gni_test_events_total")
    app = PrometheusMetricsApp(metrics)
    responses = []

    def start_response(status, headers):
        responses.append((status, dict(headers)))

    body = b"".join(app({"REQUEST_METHOD": "GET", "PATH_INFO": "/metrics"}, start_response))
    assert responses[-1][0] == "200 OK"
    assert "gni_test_events_total 1" in body.decode()

    body = b"".join(app({"REQUEST_METHOD": "GET", "PATH_INFO": "/health"}, start_response))
    assert responses[-1][0] == "404 Not Found"
    assert body == b"Not Found\n"


def test_audit_sink_failure_does_not_break_telemetry_or_job_processing():
    class BrokenSink:
        def record(self, event):
            raise RuntimeError("database unavailable")

    telemetry = RuntimeTelemetry(audit_sink=BrokenSink())
    telemetry.record_job(
        worker_id="worker-1", job_id="job-2", trace_id="trace-2",
        status="succeeded", action="completed", duration_seconds=0.1, attempt=1,
    )
    assert "gni_audit_write_failures_total 1" in telemetry.metrics.render()


def test_runtime_telemetry_records_workflow_queue_outbox_and_dead_letters():
    telemetry = RuntimeTelemetry()
    telemetry.record_workflow(action="advance_updated", status="running", duration_seconds=0.2)
    telemetry.record_workflow_steps(counts={"queued": 2, "pending": 1})
    telemetry.record_workflow_recovery(scanned=3, advanced=3, completed=1, failed=0)
    telemetry.record_outbox(action="published", duration_seconds=0.05)
    telemetry.record_outbox(action="scheduled_retry", duration_seconds=0.1)
    telemetry.record_queue_action(action="reserve", result="reserved")
    telemetry.record_dead_letter(action="created", reason="permanent_failure")

    rendered = telemetry.metrics.render()
    for metric in (
        "gni_workflow_runs_total",
        "gni_workflow_steps_observed_total",
        "gni_workflow_recovery_runs_total",
        "gni_workflow_recovery_items_total",
        "gni_outbox_events_total",
        "gni_outbox_publish_duration_seconds",
        "gni_queue_operations_total",
        "gni_dead_letter_events_total",
    ):
        assert metric in rendered
    assert 'gni_outbox_events_total{action="published"} 1' in rendered
    assert 'gni_queue_operations_total{action="reserve",result="reserved"} 1' in rendered
