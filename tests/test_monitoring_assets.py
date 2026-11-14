import json
from pathlib import Path

import yaml

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


def test_dashboard_and_alerts_cover_durable_backlog_health_metrics():
    dashboard = json.loads(
        (ROOT / "monitoring/grafana/global-news-intelligence-dashboard.json").read_text()
    )
    queries = " ".join(
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    )
    rules = yaml.safe_load((ROOT / "monitoring/prometheus/alerts.yml").read_text())
    expressions = " ".join(
        rule["expr"] for group in rules["groups"] for rule in group["rules"]
        if "alert" in rule
    )
    for metric in (
        "gni_queue_pending_messages", "gni_queue_processing_messages",
        "gni_queue_expired_leases", "gni_queue_oldest_pending_seconds",
        "gni_outbox_pending_events", "gni_outbox_processing_events",
        "gni_outbox_expired_leases", "gni_outbox_oldest_pending_seconds",
        "gni_workflows_running", "gni_dead_letters_open",
        "gni_health_collection_errors_total",
    ):
        assert metric in queries
    assert "gni_health_collection_errors_total" in expressions


def test_prometheus_scrape_config_uses_authenticated_private_target():
    config = yaml.safe_load(
        (ROOT / "monitoring/prometheus/scrape-config.yml").read_text()
    )
    scrape = config["scrape_configs"][0]
    assert scrape["job_name"] == "gni-runtime"
    assert scrape["metrics_path"] == "/metrics"
    assert scrape["bearer_token_file"]
    assert scrape["static_configs"][0]["targets"] == ["gni-runtime.internal:9108"]

def test_monitoring_compose_stack_is_private_and_secret_backed():
    compose = yaml.safe_load((ROOT / "monitoring/compose.yml").read_text())
    services = compose["services"]
    assert set(services) == {"prometheus", "grafana", "alertmanager"}
    for service in services.values():
        assert service["restart"] == "unless-stopped"
        assert "no-new-privileges:true" in service["security_opt"]
    assert services["prometheus"]["ports"] == ["127.0.0.1:9090:9090"]
    assert services["grafana"]["ports"] == ["127.0.0.1:3000:3000"]
    assert services["alertmanager"]["ports"] == ["127.0.0.1:9093:9093"]
    assert any("gni-metrics-token" in mount for mount in services["prometheus"]["volumes"])
    assert "GRAFANA_ADMIN_PASSWORD" in services["grafana"]["environment"]


def test_prometheus_stack_loads_runtime_rules_and_alertmanager():
    config = yaml.safe_load((ROOT / "monitoring/prometheus/prometheus.yml").read_text())
    assert "/etc/prometheus/alerts.yml" in config["rule_files"]
    assert config["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"] == [
        "alertmanager:9093"
    ]
    runtime = next(job for job in config["scrape_configs"] if job["job_name"] == "gni-runtime")
    assert runtime["bearer_token_file"] == "/etc/prometheus/secrets/gni-metrics-token"
    assert runtime["static_configs"][0]["targets"] == ["host.docker.internal:9108"]


def test_grafana_provisioning_matches_dashboard_datasource_uid():
    datasource = yaml.safe_load(
        (ROOT / "monitoring/grafana/provisioning/datasources/prometheus.yml").read_text()
    )
    assert datasource["datasources"][0]["uid"] == "DS_PROMETHEUS"
    dashboard = json.loads(
        (ROOT / "monitoring/grafana/global-news-intelligence-dashboard.json").read_text()
    )
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            assert target["datasource"]["uid"] == "DS_PROMETHEUS"


def test_alertmanager_does_not_invent_an_external_notification_destination():
    config = yaml.safe_load((ROOT / "monitoring/alertmanager/alertmanager.yml").read_text())
    receiver_name = config["route"]["receiver"]
    assert receiver_name in {receiver["name"] for receiver in config["receivers"]}
    assert config["receivers"][0].get("webhook_configs") is None
    assert config["receivers"][0].get("email_configs") is None
