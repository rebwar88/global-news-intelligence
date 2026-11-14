# Worker lifecycle

## Implemented

- `JobWorker.process_one()` reserves at most one queue item; an external supervisor owns polling, process lifetime and graceful shutdown.
- The repository is authoritative. A queue item without a matching record is discarded without executing code.
- The worker persists RUNNING with an incremented attempt before calling the dispatcher.
- A recovered RUNNING record is treated as an interrupted attempt and moved to RETRYABLE or FAILED using a compare-and-set update.
- The final result is persisted before queue acknowledgement. Redelivery of an already terminal job is acknowledged without executing the agent again.
- Retryable results are released back to the queue with the configured delay. The in-memory queue supports immediate release only.
- Dispatch policy failures become terminal, sanitized job errors rather than endless retries.

## Reliability boundaries

This is an at-least-once processing design, not exactly-once execution. External side effects performed by an agent can repeat if a worker crashes after the side effect but before the final result is committed. Agents must use the job ID as an idempotency key for external writes, and integrations should support deduplication.

Production queue adapters must implement durable reservations, visibility expiry, delayed delivery, dead-letter handling and fencing/lease semantics so an expired worker cannot overwrite a newer attempt. Visibility timeout must exceed the enforced maximum agent runtime, or the worker must renew its lease. The current synchronous dispatcher does not enforce CPU, memory or wall-clock limits; do not run untrusted plugin code in this process.

The repository's compare-and-set update must be atomic across workers. The worker intentionally does not acknowledge when state persistence conflicts. Monitoring should alert on repeated conflicts and exhausted attempts.

## Not implemented

No background polling daemon, distributed lease renewal, cancellation signal handling, process sandbox, durable queue/database adapter, dead-letter store, metrics exporter or audit-event sink is included yet. The in-memory queue and repository are for tests/local development only.
