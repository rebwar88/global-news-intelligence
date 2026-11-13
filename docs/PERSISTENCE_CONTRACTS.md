# Persistence contracts

## Current implementation

- `JobRepository` is a provider-neutral contract for idempotent job creation, lookup, and compare-and-set state updates.
- `JobRecord` keeps the job state and structured output/error together, allowing a durable adapter to persist the result before acknowledging a queue reservation.
- `InMemoryJobRepository` is a thread-safe development/test adapter only. It loses all data when the process exits and is not shared across GitHub Actions runs.

## Required production guarantees

A production repository adapter must provide durable storage, unique idempotency constraints, atomic compare-and-set updates, and safe concurrent access. The worker must persist the final job state and outcome before queue acknowledgement. If acknowledgement fails after persistence, redelivery must detect the already completed job and acknowledge without executing the agent again. If a worker crashes before persistence, queue visibility expiry must allow another worker to retry.

A database transaction plus a transactional outbox may be appropriate when job creation and queue publication must be coordinated. The exact design depends on the selected queue and database; no vendor has been selected yet.

## Deliberately not selected

PostgreSQL is a candidate, not a confirmed decision. Redis, managed queues, and other durable queue services remain options. Do not treat this in-memory adapter as production persistence or rely on GitHub runner files/artifacts for durable state.
