"""Safe runtime instrumentation shared by workers, workflows, queues and outbox."""
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
        except Exception:  # noqa: BLE001
            self._metric_failure()
        self._record_audit(AuditEvent(
            action="job.processed", resource_type="job", resource_id=job_id,
            actor_type="worker", actor_id=worker_id, trace_id=trace_id,
            outcome=status or "unknown",
            metadata={"status": status or "unknown", "action": action, "attempt": attempt},
        ))

    def record_poll(self, *, idle: bool) -> None:
        self._safe_increment(
            "gni_worker_polls_total", labels={"result": "idle" if idle else "processed"},
            help_text="Worker polling iterations.",
        )

    def worker_started(self) -> None:
        self._safe_increment("gni_worker_starts_total", help_text="Worker supervisor starts.")
        self._safe_gauge("gni_workers_running", 1, "Worker supervisor processes currently running.")

    def worker_stopped(self) -> None:
        self._safe_gauge("gni_workers_running", -1, "Worker supervisor processes currently running.")

    def record_worker_error(self, *, worker_id: str, error: Exception) -> None:
        error_type = type(error).__name__[:64]
        self._safe_increment(
            "gni_worker_errors_total", labels={"error_type": error_type},
            help_text="Unhandled worker-loop errors, by exception class.",
        )
        self._record_audit(AuditEvent(
            action="worker.supervisor_error", resource_type="worker",
            resource_id=worker_id, actor_type="worker", actor_id=worker_id,
            outcome="error", metadata={"error_type": error_type},
        ))

    def record_workflow(self, *, action: str, status: str, duration_seconds: float) -> None:
        """Record workflow lifecycle without workflow IDs or other high-cardinality labels."""
        labels = {"action": action[:64], "status": status[:32]}
        self._safe_increment(
            "gni_workflow_runs_total", labels=labels,
            help_text="Workflow scheduler operations by action and resulting status.",
        )
        self._safe_observe(
            "gni_workflow_operation_duration_seconds", duration_seconds,
            labels={"action": action[:64]},
            help_text="Workflow scheduler operation duration in seconds.",
        )

    def record_workflow_steps(self, *, counts: dict[str, int]) -> None:
        """Record a bounded snapshot of step states after workflow advancement."""
        for status, count in counts.items():
            if count > 0:
                self._safe_increment(
                    "gni_workflow_steps_observed_total", count,
                    labels={"status": status[:32]},
                    help_text="Workflow step states observed after scheduler advancement.",
                )

    def record_workflow_recovery(self, *, scanned: int, advanced: int, completed: int, failed: int) -> None:
        self._safe_increment(
            "gni_workflow_recovery_runs_total", labels={"result": "completed"},
            help_text="Workflow recovery scanner executions.",
        )
        for name, value in (
            ("scanned", scanned), ("advanced", advanced),
            ("completed", completed), ("failed", failed),
        ):
            self._safe_increment(
                "gni_workflow_recovery_items_total", value,
                labels={"result": name},
                help_text="Workflow recovery items processed by outcome.",
            )

    def record_outbox(self, *, action: str, duration_seconds: float) -> None:
        """Record an outbox publication outcome using a fixed action vocabulary."""
        self._safe_increment(
            "gni_outbox_events_total", labels={"action": action[:48]},
            help_text="Transactional outbox events handled by publisher outcome.",
        )
        self._safe_observe(
            "gni_outbox_publish_duration_seconds", duration_seconds,
            labels={"action": action[:48]},
            help_text="Outbox event publication duration in seconds.",
        )

    def record_queue_action(self, *, action: str, result: str) -> None:
        self._safe_increment(
            "gni_queue_operations_total",
            labels={"action": action[:32], "result": result[:32]},
            help_text="Durable queue operations by operation and result.",
        )

    def record_dead_letter(self, *, action: str, reason: str = "unspecified") -> None:
        self._safe_increment(
            "gni_dead_letter_events_total",
            labels={"action": action[:32], "reason": reason[:48]},
            help_text="Dead-letter creation and replay operations.",
        )

    def _safe_increment(self, name: str, value: float = 1, *, labels=None, help_text: str = "") -> None:
        try:
            self.metrics.increment(name, value, labels=labels, help_text=help_text)
        except Exception:  # noqa: BLE001
            self._metric_failure()

    def _safe_observe(self, name: str, value: float, *, labels=None, help_text: str = "") -> None:
        try:
            self.metrics.observe(name, value, labels=labels, help_text=help_text)
        except Exception:  # noqa: BLE001
            self._metric_failure()

    def _safe_gauge(self, name: str, delta: float, help_text: str) -> None:
        try:
            self.metrics.adjust_gauge(name, delta, help_text=help_text)
        except Exception:  # noqa: BLE001
            self._metric_failure()

    def _metric_failure(self) -> None:
        try:
            self.metrics.increment(
                "gni_metrics_write_failures_total",
                help_text="Metric recording failures, usually caused by cardinality limits.",
            )
        except Exception:  # noqa: BLE001, S110 - metrics failure must not break runtime.
            return

    def _record_audit(self, event: AuditEvent) -> None:
        if self._audit is None:
            return
        try:
            self._audit.record(event)
        except Exception:  # noqa: BLE001
            self._safe_increment(
                "gni_audit_write_failures_total",
                help_text="Audit sink write failures; inspect database connectivity.",
            )
