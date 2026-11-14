# Monitoring assets

These files are deployment-ready templates; they do not install or run Prometheus or Grafana.

## Prometheus scrape

Use `MetricsHttpServer` from the optional `monitoring` extra inside the
worker process, passing the exact same `MetricsRegistry` instance used by
`RuntimeTelemetry`. It starts a threaded Waitress WSGI listener in that
process, protected by a Bearer token validated with a constant-time comparison.
The default bind is loopback; if Prometheus is on another host, bind only to a
private interface and firewall the port to the Prometheus host.

Install with `pip install -e ".[postgres,monitoring]"`. Set
`GNI_METRICS_BEARER_TOKEN` from a secret manager and pass it to the server;
never store the token in Git. Call `close()` during graceful shutdown before
the process exits.

The scrape fragment is in `prometheus/scrape-config.yml`. Configure the token
file on the Prometheus host at the path shown there, restrict its permissions
to the Prometheus service account, and replace the example target with the
worker's private host and port. Never expose the listener publicly.

## Alert rules

Load `prometheus/alerts.yml` through Prometheus `rule_files`. The latency threshold (p95 > 30 seconds) is provisional and should be tuned from observed workload. The worker target alert assumes the scrape job is named `gni-runtime`.

The current telemetry is process-local. Scrape every worker process or deploy a supported multiprocess/centralized metrics collector before relying on aggregated counters and gauges across multiple workers. In particular, a missing target and a process restart can affect interpretation of counters and worker gauges.

## Grafana dashboard

Import `grafana/global-news-intelligence-dashboard.json` and select the Prometheus datasource when prompted. The dashboard uses metrics emitted by `RuntimeTelemetry`: worker/job processing, workflow scheduler and recovery, outbox publication, PostgreSQL queue operations and dead-letter events. These additional subsystems must receive the same `RuntimeTelemetry` instance during application composition; telemetry injection is optional.

## Current limits

- No Prometheus server, Grafana server, alert receiver, or dashboard has been deployed by these files.
- Queue, workflow and outbox metrics are opt-in: inject a shared `RuntimeTelemetry` into `WorkflowScheduler`, `WorkflowRecoveryService`, `OutboxPublisher` and `PostgresJobQueue`.
- Queue metrics are emitted by the PostgreSQL adapter; the in-memory adapter remains a test/local-development adapter and does not emit queue operation metrics.
- Metrics are not a substitute for durable job/workflow records or audit logs.
- The protected WSGI metrics app requires a Bearer token; the plain low-level `PrometheusMetricsApp` remains unauthenticated and should not be mounted directly on a network-facing listener.


## Durable backlog and lease health

Compose `PostgresRuntimeHealthCollector(pool, telemetry)` and invoke
`collect()` periodically (recommended starting interval: 30–60 seconds).
It publishes durable database snapshots for pending queue/outbox work, active
reservations, expired leases, oldest pending age, running workflows and open
dead letters. Give the collector a read-only database role for these tables.
Do not interpret missing gauge series as zero: verify the collector is running
and being scraped. Thresholds in the alert rules are starting defaults and
must be tuned after observing real traffic.
