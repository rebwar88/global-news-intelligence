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


## Orchestration and submission

- The Orchestrator validates declarative workflow DAGs, checks that every referenced agent is registered and explicitly active, rejects missing dependencies and cycles, and returns a deterministic topological order.
- The planner is data-driven: it does not hardcode the current 12-agent catalogue, so new registered agents can participate without editing Orchestrator code.
- Job submission validates agent activation, persists the job before queue publication, and uses an idempotency key to avoid creating duplicate jobs. Repeated submissions can re-attempt queue publication for a still-queued job.
- If queue publication fails, the service raises `SubmissionPendingError` with the persisted job ID. The record is retained so a later retry can publish it; callers must not treat this as queued.
- This first Orchestrator increment plans workflows but does not yet persist workflow-run state or automatically release dependent steps after predecessor completion. Those require a durable workflow-state/scheduler adapter.
- The current persist-then-enqueue sequence is not an atomic transaction. Production persistence/queue integration should use a transactional outbox (or an equivalent recoverable publisher) to close the crash window between database commit and queue publication. The in-memory adapters remain development/test only.


## Workflow state and scheduling

- WorkflowRepository stores a versioned workflow run and its per-step state. Updates use compare-and-set on the run version so competing schedulers cannot silently overwrite newer state.
- WorkflowScheduler.start() validates and persists the plan, then submits steps with no dependencies. advance() reconciles each submitted step against the authoritative job repository and submits a dependent step only after every dependency succeeds.
- Independent branches can continue when another branch fails. Descendants of a failed or blocked step become BLOCKED; the workflow becomes terminal only after all steps reach a terminal state.
- Queue-publication failures are retained as SUBMISSION_PENDING; a later scheduler tick retries publication with the same deterministic idempotency key.
- Agent jobs receive a stable envelope containing workflow_id, workflow_step_id, and the original step input under input.
- This is a scheduler core, not a continuously running service. A durable scheduler trigger/supervisor must call advance() periodically and recover stale runs after process restarts.
- Workflow-run and job repositories must be durable and share consistent atomic/CAS guarantees in production. In-memory adapters are tests/local development only.
