"""Provider-neutral persistence contracts and development adapters."""

from gni.persistence.base import JobRecord, JobRepository
from gni.persistence.memory import InMemoryJobRepository

__all__ = ["InMemoryJobRepository", "JobRecord", "JobRepository"]
