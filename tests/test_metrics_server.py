from __future__ import annotations

import sys
import threading
from types import SimpleNamespace

import pytest

from gni.observability.metrics import MetricsRegistry
from gni.observability.metrics_server import MetricsHttpServer


class FakeWaitressServer:
    def __init__(self, app):
        self.app = app
        self.stopped = threading.Event()
        self.closed = False

    def run(self):
        self.stopped.wait()

    def close(self):
        self.closed = True
        self.stopped.set()


def test_metrics_http_server_uses_private_bind_and_graceful_lifecycle(monkeypatch):
    captured = {}

    def create_server(app, **kwargs):
        captured["app"] = app
        captured["kwargs"] = kwargs
        server = FakeWaitressServer(app)
        captured["server"] = server
        return server

    monkeypatch.setitem(sys.modules, "waitress", SimpleNamespace(create_server=create_server))
    server = MetricsHttpServer(
        MetricsRegistry(), bearer_token="random-token", host="127.0.0.1",
        port=9108, threads=2,
    )
    assert captured["kwargs"] == {"host": "127.0.0.1", "port": 9108, "threads": 2}
    server.start()
    assert server.is_running
    server.close()
    assert captured["server"].closed is True
    assert not server.is_running


@pytest.mark.parametrize("host", ["", "0.0.0.0", "::", "[::]"])
def test_metrics_http_server_rejects_wildcard_bind(host, monkeypatch):
    monkeypatch.setitem(
        sys.modules, "waitress", SimpleNamespace(create_server=lambda *a, **k: None)
    )
    with pytest.raises(ValueError, match="private interface"):
        MetricsHttpServer(MetricsRegistry(), bearer_token="random-token", host=host)


def test_metrics_http_server_rejects_invalid_port_and_thread_count(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "waitress", SimpleNamespace(create_server=lambda *a, **k: None)
    )
    with pytest.raises(ValueError, match="port"):
        MetricsHttpServer(MetricsRegistry(), bearer_token="random-token", port=70000)
    with pytest.raises(ValueError, match="threads"):
        MetricsHttpServer(MetricsRegistry(), bearer_token="random-token", threads=0)
