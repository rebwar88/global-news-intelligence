"""Provider-neutral, sanitized audit event contracts."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

_SENSITIVE = (
    "password", "secret", "token", "authorization", "cookie", "credential",
    "apikey", "privatekey", "accesskey", "refresh",
)


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:512]
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in list(value.items())[:50]:
            name = str(key)[:128]
            normalized = name.casefold().replace("_", "").replace("-", "")
            if any(part in normalized for part in _SENSITIVE):
                safe[name] = "[redacted]"
            else:
                safe[name] = _safe_value(item, depth=depth + 1)
        return safe
    return f"[unsupported:{type(value).__name__}]"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    action: str
    resource_type: str
    resource_id: str | None = None
    outcome: str = "success"
    actor_type: str = "system"
    actor_id: str | None = None
    trace_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        for field_name in ("action", "resource_type", "outcome", "actor_type"):
            value = getattr(self, field_name)
            if not value or len(value) > 128:
                raise ValueError(f"{field_name} must contain 1-128 characters.")
        for field_name in ("resource_id", "actor_id", "trace_id"):
            value = getattr(self, field_name)
            if value is not None and len(value) > 255:
                raise ValueError(f"{field_name} must not exceed 255 characters.")
        try:
            UUID(self.event_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("event_id must be a UUID.") from exc
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware.")
        if not isinstance(self.metadata, dict):
            raise TypeError("Audit metadata must be a mapping.")
        safe = _safe_value(self.metadata)
        try:
            json.dumps(safe, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Audit metadata must be JSON-safe.") from exc
        object.__setattr__(self, "metadata", safe)


class AuditSink(Protocol):
    def record(self, event: AuditEvent) -> None: ...


class InMemoryAuditSink:
    """Thread-safe test/development sink; not durable across process restarts."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = threading.Lock()

    def record(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def list_recent(self, *, limit: int = 100) -> tuple[AuditEvent, ...]:
        if limit < 1:
            raise ValueError("limit must be positive.")
        with self._lock:
            return tuple(reversed(self._events[-limit:]))
