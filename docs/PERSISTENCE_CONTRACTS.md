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


## Workflow-run persistence

- WorkflowRepository stores the execution plan, workflow status, per-step status/job reference, timestamps and a monotonically increasing version.
- save() must atomically compare the expected version and persist exactly one version increment. This protects against stale scheduler updates.
- Workflow state is separate from job state: the job repository remains authoritative for agent execution outcomes; workflow state records dependency progress and orchestration status.
- The in-memory workflow adapter is process-local and non-durable. A production adapter must persist workflow runs across separate GitHub Actions executions and support concurrent scheduler invocations safely.


## Transactional outbox

- `SubmissionRepository.create_job_with_outbox()` is the atomic submission contract: a production adapter must insert the job and its `job.queued` outbox event in the same database transaction.
- Outbox payload is intentionally minimal: it stores the job/aggregate ID and idempotency key, not a second copy of the potentially sensitive job payload.
- `OutboxPublisher` claims batches with expiring leases and fencing tokens, enqueues the authoritative job using the original idempotency key, and marks the event published only after enqueue succeeds.
- Failures receive capped exponential retry scheduling; exhausted events and events with missing job records are dead-lettered with sanitized error codes.
- If a publisher dies while holding a lease, a later tick reclaims the event after `locked_until`. If it dies after queue enqueue but before marking the event published, the queue idempotency key makes the next enqueue safe.
- `InMemoryOutboxRepository` provides a lock-protected atomic test implementation only. It is not durable across process exits and is not a production database adapter.
- The outbox provides reliable at-least-once publication, not exactly-once execution or exactly-once external side effects.
