"""Queue interfaces and development implementations.

Import the optional PostgreSQL adapter from gni.queue.postgres explicitly so
installing the base package does not require PostgreSQL extras.
"""
from gni.queue.base import JobQueue, QueueReservation
from gni.queue.memory import InMemoryJobQueue

__all__ = ["InMemoryJobQueue", "JobQueue", "QueueReservation"]
