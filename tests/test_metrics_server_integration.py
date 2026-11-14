from __future__ import annotations

import socket
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

pytest.importorskip("waitress")

from gni.observability.metrics import MetricsRegistry
from gni.observability.metrics_server import MetricsHttpServer
from gni.observability.telemetry import RuntimeTelemetry


def test_waitress_metrics_server_serves_shared_runtime_metrics_and_requires_auth():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    registry = MetricsRegistry()
    telemetry = RuntimeTelemetry(metrics=registry)
    telemetry.record_poll(idle=True)
    server = MetricsHttpServer(
        registry, bearer_token="integration-test-secret",
        host="127.0.0.1", port=port,
    )
    server.start()
    try:
        request = Request(f"http://127.0.0.1:{port}/metrics")
        with pytest.raises(HTTPError) as unauthorized:
            urlopen(request, timeout=3)
        assert unauthorized.value.code == 401

        authorized = Request(
            f"http://127.0.0.1:{port}/metrics",
            headers={"Authorization": "Bearer integration-test-secret"},
        )
        with urlopen(authorized, timeout=3) as response:
            body = response.read().decode("utf-8")
        assert response.status == 200
        assert 'gni_worker_polls_total{result="idle"} 1' in body
    finally:
        server.close()
