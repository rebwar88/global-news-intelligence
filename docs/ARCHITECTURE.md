# Architecture

## Runtime

Phase 1 uses GitHub Actions as a scheduled/batch execution environment. Durable state must live outside ephemeral runners. Workers remain stateless between runs where possible.

## Core boundaries

- Orchestrator: schedules and routes jobs; it does not contain specialist business logic.
- Agents: execute bounded specialist tasks through versioned contracts.
- Skills: reusable capabilities shared by agents.
- Connectors: external source/platform adapters.
- Tools: deterministic utilities.
- Workflows: declarative task graphs and routing rules.
- Providers: replaceable AI/model integrations.
- Storage: provider-neutral object storage interface.

## Persistence

PostgreSQL is the planned system of record. Queue implementation must be durable and external to GitHub runner filesystems. Object binaries are stored separately from relational metadata.

## Migration

The runtime must be portable to VPS workers without changing agent contracts, workflow definitions, or business logic.
