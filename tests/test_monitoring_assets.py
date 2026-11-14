import json
import yaml
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_grafana_dashboard_is_valid_and_uses_emitted_metrics():
    dashboard = json.loads(
        (ROOT / "monitoring/grafana/global-news-intelligence-dashboard.json").read_text()
    )
    assert dashboard["uid"] == "gni-runtime"
    assert len(dashboard["panels"]) >= 5
    queries = [
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    ]
    metrics_source = (ROOT / "src/gni/observability/telemetry.py").read_text()
    for metric in (
        "gni_workers_running",
        "gni_jobs_processed_total",
        "gni_job_processing_duration_seconds",
        "gni_worker_errors_total",
        "gni_worker_polls_total",
        "gni_audit_write_failures_total",
        "gni_metrics_write_failures_total",
        "gni_workflow_runs_total",
        "gni_workflow_steps_observed_total",
        "gni_outbox_events_total",
        "gni_queue_operations_total",
        "gni_dead_letter_events_total",
        "gni_outbox_publish_duration_seconds",
    ):
        assert metric in metrics_source
        assert any(metric in query for query in queries)


def test_prometheus_alert_rules_are_valid_and_use_emitted_metrics():
    rules = yaml.safe_load((ROOT / "monitoring/prometheus/alerts.yml").read_text())
    alerts = [
        rule
        for group in rules["groups"]
        for rule in group["rules"]
        if "alert" in rule
    ]
    assert len(alerts) >= 5
    expressions = " ".join(rule["expr"] for rule in alerts)
    metrics_source = (ROOT / "src/gni/observability/telemetry.py").read_text()
    for metric in (
        "gni_worker_errors_total",
        "gni_audit_write_failures_total",
        "gni_metrics_write_failures_total",
        "gni_job_processing_duration_seconds",
        "gni_jobs_processed_total",
        "gni_workflow_runs_total",
        "gni_outbox_events_total",
        "gni_dead_letter_events_total",
    ):
        assert metric in metrics_source
        assert metric in expressions
