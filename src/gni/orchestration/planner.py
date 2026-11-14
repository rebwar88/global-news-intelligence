"""Provider-neutral workflow DAG validation and deterministic planning."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gni.registry.registry import PluginNotFoundError, PluginRegistry


class WorkflowValidationError(ValueError):
    """Raised when a workflow plan is invalid or references unavailable agents."""


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    step_id: str
    agent_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.step_id or len(self.step_id) > 128:
            raise ValueError("step_id must be a non-empty string up to 128 characters.")
        if not self.agent_id:
            raise ValueError("agent_id must not be empty.")
        if not isinstance(self.payload, dict):
            raise TypeError("Workflow step payload must be a mapping.")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("A workflow step cannot repeat a dependency.")


@dataclass(frozen=True, slots=True)
class WorkflowPlan:
    workflow_id: str
    steps: tuple[WorkflowStep, ...]
    trace_id: str | None = None

    def __post_init__(self) -> None:
        if not self.workflow_id or len(self.workflow_id) > 128:
            raise ValueError("workflow_id must be a non-empty string up to 128 characters.")
        if not self.steps:
            raise ValueError("A workflow must contain at least one step.")


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    workflow_id: str
    ordered_steps: tuple[WorkflowStep, ...]
    trace_id: str | None = None


class Orchestrator:
    """Validates workflow DAGs without hardcoding the platform's agent catalogue."""

    def __init__(
        self,
        registry: PluginRegistry,
        *,
        active_plugins: frozenset[str] | set[str],
    ) -> None:
        self._registry = registry
        self._active_plugins = frozenset(active_plugins)

    def plan(self, workflow: WorkflowPlan) -> ExecutionPlan:
        by_id: dict[str, WorkflowStep] = {}
        order = {step.step_id: index for index, step in enumerate(workflow.steps)}

        for step in workflow.steps:
            if step.step_id in by_id:
                raise WorkflowValidationError(f"Duplicate workflow step: {step.step_id}")
            by_id[step.step_id] = step
            try:
                self._registry.get(step.agent_id)
            except PluginNotFoundError as exc:
                raise WorkflowValidationError(
                    f"Workflow references an unknown agent: {step.agent_id}"
                ) from exc
            if step.agent_id not in self._active_plugins:
                raise WorkflowValidationError(
                    f"Workflow references an inactive agent: {step.agent_id}"
                )

        for step in workflow.steps:
            for dependency in step.depends_on:
                if dependency not in by_id:
                    raise WorkflowValidationError(
                        f"Step {step.step_id} has unknown dependency: {dependency}"
                    )
                if dependency == step.step_id:
                    raise WorkflowValidationError(
                        f"Step {step.step_id} cannot depend on itself."
                    )

        remaining = {step.step_id: set(step.depends_on) for step in workflow.steps}
        ordered: list[WorkflowStep] = []
        while remaining:
            ready = sorted(
                (step_id for step_id, deps in remaining.items() if not deps),
                key=order.__getitem__,
            )
            if not ready:
                raise WorkflowValidationError("Workflow dependencies contain a cycle.")
            for step_id in ready:
                ordered.append(by_id[step_id])
                del remaining[step_id]
            completed = set(ready)
            for dependencies in remaining.values():
                dependencies.difference_update(completed)

        return ExecutionPlan(
            workflow_id=workflow.workflow_id,
            ordered_steps=tuple(ordered),
            trace_id=workflow.trace_id,
        )
