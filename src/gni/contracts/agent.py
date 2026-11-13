"""Agent execution contracts; implementations are injected by trusted runtime code."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class AgentStatus(StrEnum):
    SUCCEEDED = "succeeded"
    RETRYABLE = "retryable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AgentContext:
    job_id: str
    trace_id: str
    permissions: frozenset[str] = frozenset()
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentResult:
    status: AgentStatus
    output: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    message: str | None = None


class Agent(Protocol):
    """A specialist must implement this bounded synchronous execution contract."""

    agent_id: str

    def run(self, context: AgentContext, payload: dict[str, Any]) -> AgentResult:
        """Execute one bounded job and return a structured result."""
        ...
