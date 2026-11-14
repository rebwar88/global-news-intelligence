"""Optional POSIX process isolation for trusted, importable agent implementations.

This limits resource consumption and contains crashes/timeouts. It is not an OS
security sandbox; use containers or a dedicated sandbox runtime for untrusted code.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import time
from dataclasses import dataclass
from typing import Any

from gni.contracts.agent import AgentContext, AgentResult, AgentStatus


@dataclass(frozen=True, slots=True)
class IsolationLimits:
    timeout_seconds: float = 60.0
    memory_bytes: int = 2 * 1024 * 1024 * 1024
    cpu_seconds: int = 55

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0 or self.memory_bytes < 64 * 1024 * 1024 or self.cpu_seconds < 1:
            raise ValueError("Invalid process isolation limits.")


def _child_run(agent: Any, context: AgentContext, payload: dict, limits: IsolationLimits, output) -> None:
    try:
        if os.name == "posix":
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
            resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
            if hasattr(resource, "RLIMIT_NOFILE"):
                resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        result = agent.run(context, payload)
        if not isinstance(result, AgentResult) or not isinstance(result.status, AgentStatus):
            raise TypeError("Agent returned an invalid result.")
        encoded = {"status": result.status.value, "output": result.output,
                   "error_code": result.error_code, "message": result.message}
        json.dumps(encoded, allow_nan=False)
        output.send(("ok", encoded))
    except BaseException:  # noqa: BLE001
        output.send(("error", "agent_process_failed"))


class ProcessIsolatedAgentRunner:
    """Executes one agent call in a killable child process with resource limits."""

    def __init__(self, limits: IsolationLimits | None = None) -> None:
        if os.name != "posix":
            raise RuntimeError("Resource-limited process execution currently requires POSIX.")
        self._limits = limits or IsolationLimits()

    def run(self, agent: Any, context: AgentContext, payload: dict) -> AgentResult:
        ctx = multiprocessing.get_context("fork")
        parent, child = ctx.Pipe(duplex=False)
        process = ctx.Process(
            target=_child_run,
            args=(agent, context, payload, self._limits, child),
        )
        deadline = time.monotonic() + self._limits.timeout_seconds
        process.start()
        child.close()
        try:
            if not parent.poll(self._limits.timeout_seconds):
                self._terminate(process)
                raise TimeoutError("Agent execution exceeded its wall-clock limit.")
            try:
                kind, data = parent.recv()
            except EOFError as exc:
                raise RuntimeError("Agent process exited without a result.") from exc
            process.join(max(0.0, deadline - time.monotonic()))
            if process.is_alive():
                self._terminate(process)
                raise TimeoutError("Agent process did not exit after returning a result.")
            if kind != "ok":
                raise RuntimeError("Agent process failed.")
            return AgentResult(
                status=AgentStatus(data["status"]), output=data["output"],
                error_code=data["error_code"], message=data["message"],
            )
        finally:
            parent.close()
            if process.is_alive():
                self._terminate(process)

    @staticmethod
    def _terminate(process: multiprocessing.Process) -> None:
        process.terminate()
        process.join(2)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join()
