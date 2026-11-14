# Monitoring and operational visibility

## Current execution model: GitHub Actions only

The project currently runs its automation and CI through GitHub Actions. No VPS, always-on metrics server, Prometheus server, Grafana server, or Alertmanager is required or deployed in this phase.

The GitHub-native monitoring workflow is:

1. The CI workflow runs tests and linting.
2. Pytest writes a JUnit XML report; CI uploads it as a 14-day artifact.
3. When CI completes on `main`, the GNI Monitoring Report workflow runs.
4. The report records the workflow result, run duration, branch/commit metadata, and test totals.
5. A concise report is added to the GitHub Actions job summary, while JSON and Markdown reports are retained as 30-day artifacts.

View the latest runs in the repository's **Actions** tab. Open a **GNI Monitoring Report** run to see the summary and download its report artifact. The report deliberately excludes raw test failure output, source content, job payloads, and secrets.

## Local/runtime instrumentation

The application still has a dependency-light, process-local `MetricsRegistry` and `RuntimeTelemetry`. These support tests and future runtime composition. They do not create persistent time-series history by themselves, and GitHub Actions jobs are ephemeral.

The Prometheus exposition app, protected metrics listener, scrape fragment, alert rules, and Grafana dashboard are retained as future integration assets. They are not part of the current GitHub Actions execution path. Do not configure a public listener or provision a server in this phase.

## Future phase (only after funding and explicit approval)

When a VPS or another persistent runtime is approved, we can:
- run persistent workers and a PostgreSQL-backed queue;
- expose authenticated metrics on a private network;
- deploy Prometheus, Grafana and Alertmanager;
- connect the existing dashboard and alert rules;
- configure an explicitly approved notification destination.

Until then, GitHub Actions workflow summaries and retained artifacts are the operational record. Artifact retention is finite; it is not a replacement for a long-term metrics database.
