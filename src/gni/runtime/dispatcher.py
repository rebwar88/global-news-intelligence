"""Safe dispatcher using explicitly injected trusted agent implementations."""
from __future__ import annotations

import json

from gni.contracts.agent import Agent, AgentContext, AgentStatus
from gni.contracts.jobs import Job, JobStatus
from gni.registry.registry import PluginRegistry


class DispatchError(RuntimeError):
    """Raised when a job cannot be safely dispatched."""


class AgentDispatcher:
    def __init__(
        self,
        *,
        registry: PluginRegistry,
        agents: dict[str, Agent],
        active_plugins: frozenset[str],
        granted_permissions: dict[str, frozenset[str]] | None = None,
    ) -> None:
        self._registry = registry
        self._agents = dict(agents)
        self._active_plugins = active_plugins
        self._grants = granted_permissions or {}

    def execute(self, job: Job) -> tuple[Job, dict[str, object]]:
        """Convenience entry point for direct, non-worker execution."""
        return self.execute_running(job.start())

    def execute_running(self, running: Job) -> tuple[Job, dict[str, object]]:
        """Execute a job whose RUNNING transition has already been persisted."""
        if running.status != JobStatus.RUNNING:
            raise DispatchError("Dispatcher requires a persisted RUNNING job.")
        manifest = self._registry.get(running.agent_id)
        if running.agent_id not in self._active_plugins:
            raise DispatchError(f"Plugin is not explicitly activated: {running.agent_id}.")
        agent = self._agents.get(running.agent_id)
        if agent is None:
            raise DispatchError(f"No trusted runtime implementation for {running.agent_id}.")
        if agent.agent_id != manifest.id:
            raise DispatchError("Runtime agent ID does not match its registered manifest.")
        granted = self._grants.get(running.agent_id, frozenset())
        missing = set(manifest.permissions) - set(granted)
        if missing:
            raise DispatchError(
                f"Required permissions not granted: {', '.join(sorted(missing))}"
            )

        context = AgentContext(
            job_id=running.id,
            trace_id=running.trace_id,
            permissions=frozenset(granted),
        )
        try:
            result = agent.run(context, running.payload)
            if not isinstance(result.output, dict):
                raise TypeError("Agent output must be a mapping.")
            json.dumps(result.output, allow_nan=False)
            if not isinstance(result.status, AgentStatus):
                raise TypeError("Agent returned an invalid status.")
        except Exception:  # noqa: BLE001 - isolate arbitrary plugin failures at the runtime boundary.
            return running.retry(), {
                "error_code": "agent_execution_error",
                "message": "Agent execution failed; inspect the restricted runtime logs.",
            }

        if result.status == AgentStatus.SUCCEEDED:
            return running.succeed(), result.output
        if result.status == AgentStatus.RETRYABLE:
            return running.retry(), {
                "error_code": result.error_code,
                "message": result.message,
            }
        return running.fail(), {
            "error_code": result.error_code,
            "message": result.message,
        }
