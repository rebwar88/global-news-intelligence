"""Minimal WSGI endpoint for Prometheus-compatible metric scraping."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from gni.observability.metrics import MetricsRegistry

StartResponse = Callable[[str, list[tuple[str, str]]], Any]


class PrometheusMetricsApp:
    """Mount at /metrics in a WSGI server; it does not start a web server itself."""

    def __init__(self, registry: MetricsRegistry, *, path: str = "/metrics") -> None:
        if not path.startswith("/") or path == "/":
            raise ValueError("Metrics path must be a non-root absolute path.")
        self._registry = registry
        self._path = path

    def __call__(self, environ: dict[str, Any], start_response: StartResponse) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "")
        if path != self._path:
            body = b"Not Found\n"
            start_response("404 Not Found", [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))])
            return [body]
        if method != "GET":
            body = b"Method Not Allowed\n"
            start_response("405 Method Not Allowed", [
                ("Content-Type", "text/plain"), ("Allow", "GET"),
                ("Content-Length", str(len(body))),
            ])
            return [body]
        body = self._registry.render().encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "text/plain; version=0.0.4; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ])
        return [body]
