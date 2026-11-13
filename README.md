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

This repository currently contains the initial architecture scaffold. Implementation decisions and integrations will be documented before production activation.