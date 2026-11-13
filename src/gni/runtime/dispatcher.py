"""Safe dispatcher using explicitly injected trusted agent implementations."""
from __future__ import annotations

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
        granted_permissions: dict[str, frozenset[str]] | None = None,
    ) -> None:
        self._registry = registry
        self._agents = dict(agents)
        self._grants = granted_permissions or {}

    def execute(self, job: Job) -> tuple[Job, dict[str, object]]:
        manifest = self._registry.get(job.agent_id)
        agent = self._agents.get(job.agent_id)
        if agent is None:
            raise DispatchError(f"No trusted runtime implementation for {job.agent_id}.")
        if agent.agent_id != manifest.id:
            raise DispatchError("Runtime agent ID does not match its registered manifest.")
        granted = self._grants.get(job.agent_id, frozenset())
        missing = set(manifest.permissions) - set(granted)
        if missing:
            raise DispatchError(f"Required permissions not granted: {', '.join(sorted(missing))}")

        running = job.start()
        context = AgentContext(
            job_id=running.id,
            trace_id=running.trace_id,
            permissions=frozenset(granted),
        )
        try:
            result = agent.run(context, running.payload)
        except Exception as exc:
            failed = running.retry()
            return failed, {"error_code": "agent_unhandled_exception", "message": str(exc)}

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
