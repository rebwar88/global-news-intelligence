"""Lifecycle-managed private HTTP listener for the in-process metrics registry."""
from __future__ import annotations

import threading
from typing import Any

from gni.observability.metrics import MetricsRegistry
from gni.observability.protected_metrics import ProtectedMetricsApp


class MetricsHttpServer:
    """Serve the worker's own metrics registry on a private interface.

    Waitress is loaded lazily from the optional monitoring extra. The
    default loopback bind prevents accidental exposure on public interfaces.
    """

    def __init__(
        self,
        registry: MetricsRegistry,
        *,
        bearer_token: str,
        host: str = "127.0.0.1",
        port: int = 9108,
        threads: int = 2,
    ) -> None:
        if not host or host in {"0.0.0.0", "::", "[::]"}:
            raise ValueError("Metrics server must bind to a specific private interface.")
        if not 1 <= port <= 65535:
            raise ValueError("Metrics server port must be between 1 and 65535.")
        if threads < 1:
            raise ValueError("Metrics server threads must be positive.")
        try:
            from waitress import create_server
        except ImportError as exc:
            raise RuntimeError(
                "Install the optional monitoring dependencies: pip install "
                "global-news-intelligence[monitoring]"
            ) from exc

        app = ProtectedMetricsApp(registry, bearer_token=bearer_token)
        self._server: Any = create_server(
            app, host=host, port=port, threads=threads
        )
        self._thread: threading.Thread | None = None
        self._closed = False

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("Metrics HTTP server is closed and cannot be restarted.")
        if self.is_running:
            raise RuntimeError("Metrics HTTP server is already running.")
        self._thread = threading.Thread(
            target=self._server.run,
            name="gni-prometheus-metrics",
            daemon=False,
        )
        self._thread.start()

    def close(self, *, timeout_seconds: float = 10.0) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds cannot be negative.")
        if not self._closed:
            self._server.close()
            self._closed = True
        if self._thread is not None:
            self._thread.join(timeout_seconds)
            if self._thread.is_alive():
                raise TimeoutError("Metrics HTTP server did not stop within the timeout.")
