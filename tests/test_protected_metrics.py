from __future__ import annotations

import pytest

from gni.observability.metrics import MetricsRegistry
from gni.observability.protected_metrics import ProtectedMetricsApp


def _request(app, *, path="/metrics", authorization=""):
    responses = []

    def start_response(status, headers):
        responses.append((status, dict(headers)))

    body = b"".join(app({
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "HTTP_AUTHORIZATION": authorization,
    }, start_response))
    return responses[0], body


def test_protected_metrics_endpoint_requires_valid_bearer_token():
    metrics = MetricsRegistry()
    metrics.increment("gni_test_events_total")
    app = ProtectedMetricsApp(metrics, bearer_token="long-random-secret")

    response, body = _request(app)
    assert response[0] == "401 Unauthorized"
    assert response[1]["WWW-Authenticate"].startswith("Bearer")
    assert body == b"Unauthorized\n"

    response, body = _request(app, authorization="Bearer wrong-token")
    assert response[0] == "401 Unauthorized"

    response, body = _request(app, authorization="Bearer long-random-secret")
    assert response[0] == "200 OK"
    assert b"gni_test_events_total 1" in body


def test_protected_metrics_endpoint_rejects_empty_token():
    with pytest.raises(ValueError, match="non-empty"):
        ProtectedMetricsApp(MetricsRegistry(), bearer_token="")
