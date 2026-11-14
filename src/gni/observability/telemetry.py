"""Safe runtime instrumentation shared by workers and supervisors."""
from __future__ import annotations

from gni.observability.audit import AuditEvent, AuditSink
from gni.observability.metrics import MetricsRegistry


class RuntimeTelemetry:
    """Combines process-local metrics with an optional durable audit sink."""

    def __init__(
        self, metrics: MetricsRegistry | None = None, audit_sink: AuditSink | None = None
    ) -> None:
        self.metrics = metrics or MetricsRegistry()
        self._audit = audit_sink

    def record_job(
        self, *, worker_id: str, job_id: str, trace_id: str, status: str,
        action: str, duration_seconds: float, attempt: int, agent_id: str = "unknown",
    ) -> None:
        labels = {"status": status or "unknown", "action": action, "agent": agent_id}
        try:
            self.metrics.increment(
                "gni_jobs_processed_total", labels=labels,
                help_text="Jobs handled by workers, including terminal redeliveries.",
            )
            self.metrics.observe(
                "gni_job_processing_duration_seconds", duration_seconds,
                labels={"action": action, "agent": agent_id},
                help_text="Time spent processing a reserved job, in seconds.",
            )
        except Exception:  # noqa: BLE001 - observability must not interrupt job handling.
            self.metrics.increment(
                "gni_metrics_write_failures_total",
                help_text="Metric recording failures, usually caused by cardinality limits.",
            )
        self._record_audit(AuditEvent(
            action="job.processed", resource_type="job", resource_id=job_id,
            actor_type="worker", actor_id=worker_id, trace_id=trace_id,
            outcome=status or "unknown",
            metadata={"status": status or "unknown", "action": action, "attempt": attempt},
        ))

    def record_poll(self, *, idle: bool) -> None:
        self.metrics.increment(
            "gni_worker_polls_total", labels={"result": "idle" if idle else "processed"},
            help_text="Worker polling iterations.",
        )

    def worker_started(self) -> None:
        self.metrics.increment(
            "gni_worker_starts_total", help_text="Worker supervisor starts."
        )
        self.metrics.adjust_gauge(
            "gni_workers_running", 1, help_text="Worker supervisor processes currently running."
        )

    def worker_stopped(self) -> None:
        self.metrics.adjust_gauge(
            "gni_workers_running", -1, help_text="Worker supervisor processes currently running."
        )

    def record_worker_error(self, *, worker_id: str, error: Exception) -> None:
        error_type = type(error).__name__[:64]
        self.metrics.increment(
            "gni_worker_errors_total", labels={"error_type": error_type},
            help_text="Unhandled worker-loop errors, by exception class.",
        )
        self._record_audit(AuditEvent(
            action="worker.supervisor_error", resource_type="worker",
            resource_id=worker_id, actor_type="worker", actor_id=worker_id,
            outcome="error", metadata={"error_type": error_type},
        ))

    def _record_audit(self, event: AuditEvent) -> None:
        if self._audit is None:
            return
        try:
            self._audit.record(event)
        except Exception:  # noqa: BLE001 - telemetry must never stop job processing.
            self.metrics.increment(
                "gni_audit_write_failures_total",
                help_text="Audit sink write failures; inspect database connectivity.",
            )
