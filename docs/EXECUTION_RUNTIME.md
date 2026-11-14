# Agent execution and queue contracts

## Implemented

- Versioned job contract with explicit state transitions, attempt limits, trace IDs and JSON-safe payload validation.
- Agent protocol and structured success/retryable/failure results.
- Dispatcher requires a registered manifest, explicit activation, a trusted implementation injected by runtime code, and explicit grants for every declared permission.
- Plugin entrypoint strings are metadata only. The dispatcher never imports modules named by a manifest.
- Queue protocol defines enqueue with idempotency key, reservation/visibility timeout, acknowledgement and release.
- Provider-neutral job repository contract with idempotent creation and compare-and-set state updates.
- `JobWorker.process_one()` persists RUNNING before execution and persists the final result before acknowledging the queue reservation.
- Interrupted RUNNING attempts can be recovered after queue redelivery; retry limits are enforced.
- Terminal redelivery is acknowledged without rerunning the agent. Missing authoritative job records are discarded without executing code.
- In-memory queue and repository exist only for tests/local development and are explicitly non-durable.

## Reliability model

The worker provides at-least-once processing, not exactly-once side effects. Agents must make external writes idempotent using the job ID. Production queue adapters must provide durable reservations, visibility expiry, delayed delivery, dead-letter handling and fencing/lease semantics. Repository compare-and-set updates must be atomic across workers. See [WORKER_LIFECYCLE.md](WORKER_LIFECYCLE.md) for recovery details and limitations.

## Not production-ready yet

The in-memory queue is not suitable for GitHub Actions across separate runs. It does not implement real visibility-timeout expiry or delayed delivery. No durable database/queue adapter, polling supervisor, lease renewal, process sandbox, CPU/memory/wall-clock enforcement, cancellation signal handling, metrics exporter or audit-event sink exists yet.

The dispatcher is synchronous and does not execute untrusted plugin code in an isolated process. Do not expose it to arbitrary third-party plugins. The next architecture decision is the durable queue provider and database hosting. Do not persist critical state in a GitHub runner filesystem or workflow artifacts.
