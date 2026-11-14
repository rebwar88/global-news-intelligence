# Global News Intelligence Platform

A modular, plugin-based platform for multilingual news discovery, story intelligence, social trends, media verification, fact-checking, and delivery to PayamNewsAggregator.

## Project status

Architecture and foundation phase. No production agents are enabled yet.

## Architecture principles

- 12 agents: one Main Orchestrator and eleven specialist agents.
- Plugin-based Agent, Skill, Connector, Tool, Workflow, and Model registries.
- GitHub Actions scheduled workers for Phase 1; portable runtime for future VPS migration.
- PostgreSQL as the planned persistent database; queue adapter must use durable external storage.
- Cloudflare R2 as the initial media object-storage provider behind a provider-neutral adapter.
- Media metadata and storage references live in the database; binary objects live in object storage.
- PayamNewsAggregator remains the publishing and newsroom system.
- Secrets are supplied through GitHub Secrets or a dedicated secret manager; never commit credentials.

## Planned agents

1. Main Orchestrator
2. News Collection Agent
3. Source Discovery Agent
4. Story Intelligence Agent
5. Editorial & Translation Agent
6. Kurdish Sorani QA Agent
7. Social Trends Intelligence Agent
8. Social Media Discovery Agent
9. Media Collection Agent
10. Media Verification Agent
11. Fact-Checking Agent
12. Delivery & Sync Agent

## Development status

The repository now includes plugin registries, agent/job contracts, workflow planning and recovery, transactional outbox, PostgreSQL persistence and durable queue adapters, worker supervision, process resource limits, dead-letter handling, Prometheus metrics and PostgreSQL audit logging. PostgreSQL 16 integration tests and lint run in GitHub Actions.

This is still a foundation, not a production deployment. No production database, Prometheus server, Grafana dashboards, alert manager, hardened untrusted-code sandbox or model-provider cost collector has been deployed. Production activation requires infrastructure selection, migrations, secrets, access controls, backups, retention policy, load testing and operational runbooks.

## Operational documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Execution runtime](docs/EXECUTION_RUNTIME.md)
- [Worker lifecycle and isolation](docs/WORKER_LIFECYCLE.md)
- [PostgreSQL schema and operations](docs/POSTGRESQL_SCHEMA.md)
- [Observability, metrics and audit](docs/OBSERVABILITY.md)
- [Security baseline](docs/SECURITY.md)