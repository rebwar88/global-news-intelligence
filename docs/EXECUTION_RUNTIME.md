# Agent execution and queue contracts

## Implemented

- Versioned job contract with explicit state transitions, attempt limits, trace IDs and JSON-safe payload validation.
- Agent protocol and structured success/retryable/failure results.
- Dispatcher requires a manifest already present in the registry, explicit activation, a trusted implementation injected by runtime code, and explicit grants for every declared permission.
- Plugin entrypoint strings are metadata only. The dispatcher never imports modules named by a manifest.
- Queue protocol defines enqueue with idempotency key, reservation/visibility timeout, acknowledgement and release.
- In-memory queue exists only for tests/local development and is explicitly non-durable.

## Not production-ready yet

The in-memory queue is not suitable for GitHub Actions across separate runs. It intentionally does not implement real visibility-timeout expiry or delayed delivery. A production adapter must be backed by a durable external service and implement atomic idempotent enqueue, visibility-timeout recovery, delayed retries, dead-letter handling, and safe acknowledgement.

The dispatcher is synchronous and bounded. It does not spawn processes, execute untrusted plugin code, or provide isolation. Production execution requires a worker boundary, time/resource limits, cancellation, structured logs, metrics and audit events.

The next architecture decision is the durable queue provider and database hosting. Do not persist critical state in a GitHub runner filesystem or workflow artifacts.
