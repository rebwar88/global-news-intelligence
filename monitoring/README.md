# Monitoring assets

These files are deployment-ready templates; they do not install or run Prometheus or Grafana.

## Prometheus scrape

The runtime's WSGI metrics endpoint is unauthenticated. Expose it only on a private network or protect it with an authenticated reverse proxy/firewall. Example scrape configuration:

```yaml
scrape_configs:
  - job_name: gni-runtime
    scrape_interval: 15s
    metrics_path: /metrics
    static_configs:
      - targets: ["gni-runtime.internal:9108"]
```

Use the actual private host and port selected by your deployment. Do not expose the endpoint publicly.

## Alert rules

Load `prometheus/alerts.yml` through Prometheus `rule_files`. The latency threshold (p95 > 30 seconds) is provisional and should be tuned from observed workload. The worker target alert assumes the scrape job is named `gni-runtime`.

The current telemetry is process-local. Scrape every worker process or deploy a supported multiprocess/centralized metrics collector before relying on aggregated counters and gauges across multiple workers. In particular, a missing target and a process restart can affect interpretation of counters and worker gauges.

## Grafana dashboard

Import `grafana/global-news-intelligence-dashboard.json` and select the Prometheus datasource when prompted. The dashboard uses only metrics currently emitted by `RuntimeTelemetry`.

## Current limits

- No Prometheus server, Grafana server, alert receiver, or dashboard has been deployed by these files.
- No dead-letter metric is emitted yet; dead-letter monitoring should be added when the dead-letter management service is instrumented.
- Metrics are not a substitute for durable job/workflow records or audit logs.
- The metrics endpoint has no built-in authentication.
