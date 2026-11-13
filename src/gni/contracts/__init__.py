"""Stable contracts shared by the platform core and plugins."""
from gni.contracts.agent import Agent, AgentContext, AgentResult, AgentStatus
from gni.contracts.jobs import Job, JobStatus

__all__ = ["Agent", "AgentContext", "AgentResult", "AgentStatus", "Job", "JobStatus"]
