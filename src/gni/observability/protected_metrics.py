"""Bearer-token protection for the private Prometheus scrape endpoint."""
from __future__ import annotations

import hmac
from collections.abc import Callable, Iterable
from typing import Any

from gni.observability.prometheus import PrometheusMetricsApp
from gni.observability.metrics import MetricsRegistry

StartResponse = Callable[[str, list[tuple[str, str]]], Any]


class ProtectedMetricsApp:
    """Protect a metrics WSGI app with a constant-time Bearer token check.

    Bind the WSGI server to a private interface as well; authentication is
    defense in depth, not a reason to expose operational metrics publicly.
    """

    def __init__(
        self,
        registry: MetricsRegistry,
        *,
        bearer_token: str,
        path: str = "/metrics",
    ) -> None:
        if (
            not bearer_token
            or not bearer_token.isascii()
            or any(character.isspace() for character in bearer_token)
        ):
            raise ValueError("A non-empty metrics bearer token without whitespace is required.")
        self._token = bearer_token
        self._path = path
        self._app = PrometheusMetricsApp(registry, path=path)

    def __call__(self, environ: dict[str, Any], start_response: StartResponse) -> Iterable[bytes]:
        if environ.get("PATH_INFO", "") == self._path:
            authorization = environ.get("HTTP_AUTHORIZATION", "")
            scheme, separator, supplied = authorization.partition(" ")
            if (
                not separator
                or scheme.lower() != "bearer"
                or not hmac.compare_digest(supplied, self._token)
            ):
                body = b"Unauthorized\n"
                start_response("401 Unauthorized", [
                    ("Content-Type", "text/plain"),
                    ("WWW-Authenticate", 'Bearer realm="gni-metrics"'),
                    ("Content-Length", str(len(body))),
                ])
                return [body]
        return self._app(environ, start_response)
