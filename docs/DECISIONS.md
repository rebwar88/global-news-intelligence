# Architecture decisions

## Accepted

- Exactly 12 agents: Main Orchestrator plus 11 specialist agents.
- Plugin-based extension model with versioned manifests and explicit activation.
- GitHub Actions is the Phase 1 scheduled/batch runtime; later VPS migration must not require business-logic rewrites.
- PostgreSQL is the planned persistent database; final queue technology remains to be selected.
- Cloudflare R2 is the initial media storage provider behind a provider-neutral adapter.
- Media metadata and references are stored in the database; binary media is stored in object storage.
- PayamNewsAggregator remains the newsroom/publishing destination.

## Pending

- Durable queue implementation and provider.
- Database hosting/provider and migration strategy.
- Initial connector/platform access inventory.
- Sorani editorial style and source approval policy.
- R2 bucket layout, lifecycle, retention and access policy.
