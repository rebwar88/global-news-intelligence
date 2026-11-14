# Observability: metrics and audit trail

## Scope

The observability layer is provider-neutral and does not change agent contracts.
It provides process-local metrics in Prometheus text format and a durable PostgreSQL
audit sink. Metrics are operational measurements, not a source of truth; the audit
table is the durable event record.

## Metrics

`gni.observability.MetricsRegistry` is thread-safe and dependency-free. It supports
counters, gauges and histograms, with a configurable per-metric series limit (500 by
default). Label names and values are validated/bounded. Do not use job IDs, user IDs,
URLs, source names, raw exception messages or other unbounded values as labels.

Current runtime metrics:

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `gni_jobs_processed_total` | Counter | status, action, agent | Reserved jobs handled by workers |
| `gni_job_processing_duration_seconds` | Histogram | action, agent | Time from worker reservation through durable result/ack handling |
| `gni_worker_polls_total` | Counter | result | Processed or idle polling iterations |
| `gni_worker_starts_total` | Counter | none | Supervisor starts |
| `gni_workers_running` | Gauge | none | Active supervisor loops in this process |
| `gni_worker_errors_total` | Counter | exception class | Unhandled worker-loop exceptions |
| `gni_audit_write_failures_total` | Counter | none | Failed durable audit writes |
| `gni_metrics_write_failures_total` | Counter | none | Metric write failures, such as series limit exhaustion |

The registry exports Prometheus 0.0.4 text. A WSGI-compatible scrape app is available:

```python
from wsgiref.simple_server import make_server

from gni.observability import MetricsRegistry, PrometheusMetricsApp

metrics = MetricsRegistry()
metrics.increment("gni_example_events_total")
app = PrometheusMetricsApp(metrics, path="/metrics")
server = make_server("127.0.0.1", 9108, app)
server.serve_forever()
```

This is an example only; mount the app in the chosen service runtime instead of
starting a second server where possible. The WSGI app has no authentication built
in. Bind it to a private interface/network and restrict access with firewall,
reverse-proxy authentication or service-mesh policy. Never expose scrape metrics
publicly by default.

Metrics are process-local. In a multi-process deployment, configure Prometheus to
scrape each worker process or use a supported multiprocess collector before relying
on aggregate values. The current registry is not a distributed metrics store.

## Audit events

`AuditEvent` records actor, action, resource, outcome, timestamp, trace ID and
small structured metadata. The event constructor recursively redacts common secret
fields (passwords, tokens, API keys, credentials, cookies and authorization values),
limits nesting/field lengths, and rejects non-JSON-safe metadata.

The worker records a traceable `job.processed` event after it has persisted the
outcome and completed queue handling. The supervisor records worker-loop errors
using the exception class only; raw exception messages and payload contents are
not written to audit metadata.

For durable storage, use:

```python
from gni.persistence.audit_postgres import PostgresAuditSink
from gni.observability import RuntimeTelemetry

audit_sink = PostgresAuditSink(pool)
telemetry = RuntimeTelemetry(metrics=metrics, audit_sink=audit_sink)
```

Pass the same telemetry object to `JobWorker` and `WorkerSupervisor`. The
in-memory audit sink is for tests/local development only. Audit write failures do
not stop job execution; they increment `gni_audit_write_failures_total` so they
can be alerted on. For privileged operations, the calling service must also emit
an explicit audit event with the responsible actor and reason.

The audit table is append-only through the application interface, but database
owners can still alter it. Use a dedicated database role with INSERT and SELECT
permissions only for the runtime/audit reader, restrict migration-owner credentials,
and export backups to protected storage. Define retention and deletion policy before
production; do not retain personal or source payload content in audit metadata.

## Alerting recommendations

Start with alerts for:
- Any sustained increase in `gni_worker_errors_total`.
- `gni_audit_write_failures_total > 0`.
- Jobs repeatedly reaching `failed` or dead-letter actions.
- A worker count of zero while a worker service is expected to run.
- High p95/p99 job processing duration relative to each agent's configured timeout.

The repository does not yet include a Prometheus server, Grafana dashboards, alert
rules, external log aggregation or model-provider token/cost instrumentation. Add
provider usage metrics only when model adapters expose trustworthy token and cost
usage; never estimate billing from prompt length and present it as actual spend.
