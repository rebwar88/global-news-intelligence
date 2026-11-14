# Monitoring stack deployment

This stack runs Prometheus, Grafana and Alertmanager with Docker Compose. It is a deployment template; it does not provision a VPS, firewall, TLS reverse proxy, worker process, or notification destination.

## 1. Host prerequisites

- A supported Linux VPS with Docker Engine and the Docker Compose plugin.
- A private network path from the monitoring host to the GNI worker.
- A separate, persistent disk/volume for monitoring data where possible.
- Do not expose ports 3000, 9090, 9093 or 9108 directly to the public internet.

The Compose file binds the Grafana, Prometheus and Alertmanager host ports to loopback only. Access them through an SSH tunnel or a properly authenticated TLS reverse proxy.

## 2. Prepare secrets

From this directory:

```sh
cp .env.example .env
chmod 600 .env
```

Replace the Grafana password with a long, unique random value. Do not commit `.env`.

Create the worker metrics token file. It must contain exactly the same token passed to `ProtectedMetricsApp` / `MetricsHttpServer` in the worker runtime:

```sh
mkdir -p secrets
umask 077
openssl rand -hex 32 > secrets/gni-metrics-token
chmod 600 secrets/gni-metrics-token
```

Transfer that token securely to the worker's secret manager as `GNI_METRICS_BEARER_TOKEN`; do not reuse the Grafana password.

## 3. Connect the worker

Prometheus uses `host.docker.internal:9108` through Docker's host-gateway mapping. On Linux, verify the host-gateway address and bind the worker metrics listener to that specific private bridge/interface address (commonly the Docker bridge gateway, but verify it on the actual host). Do not bind to `0.0.0.0` or `::`.

Configure the worker's firewall to allow TCP/9108 only from the monitoring host. Keep Bearer authentication enabled. The worker must start `MetricsHttpServer` with the same `MetricsRegistry` instance used by `RuntimeTelemetry`.

If the worker runs on a different machine, replace `host.docker.internal:9108` in `prometheus/prometheus.yml` with its private IP and ensure private routing and firewall rules permit the connection.

## 4. Start and validate

```sh
docker compose --env-file .env -f compose.yml config
docker compose --env-file .env -f compose.yml up -d
docker compose --env-file .env -f compose.yml ps
docker compose --env-file .env -f compose.yml logs --tail=100
```

Open services only through a local SSH tunnel, for example:

```sh
ssh -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 -L 9093:127.0.0.1:9093 user@YOUR_VPS
```

Then visit Grafana at `http://127.0.0.1:3000`. The Prometheus datasource and GNI dashboard are provisioned automatically. Check Prometheus `/targets` and confirm `gni-runtime` is UP before relying on alerts.

## 5. Alert delivery is not configured yet

Alertmanager starts with a deliberately non-notifying receiver. This prevents accidental delivery to an invented or unapproved endpoint. Before production use, configure a real approved email/webhook receiver in `alertmanager/alertmanager.yml`, validate it, and send a test alert. Until then, alert rules can evaluate and appear in Prometheus/Alertmanager, but no external notification is delivered.

## 6. Operations and backup

- Monitor disk usage and tune Prometheus retention for the available disk.
- Back up the named volumes `prometheus_data`, `grafana_data` and `alertmanager_data`.
- Pin and update container image versions through reviewed changes.
- Keep the stack on a private network; use TLS and authentication at any reverse proxy.
- Scrape each worker process or use a supported multiprocess/central metrics design. Current GNI runtime metrics are process-local.
- Do not treat missing health gauges as zero; verify the collector and target first.

## 7. Stop / update

```sh
docker compose --env-file .env -f compose.yml pull
docker compose --env-file .env -f compose.yml up -d
docker compose --env-file .env -f compose.yml down
```

Do not use `down -v` unless intentionally deleting monitoring history.
