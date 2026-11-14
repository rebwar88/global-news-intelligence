# Worker lifecycle

## Implemented

- `JobWorker.process_one()` reserves at most one queue item. `WorkerSupervisor` owns polling, idle backoff and cooperative graceful shutdown between jobs.
- The repository is authoritative. A queue item without a matching record is discarded without executing code.
- The worker persists RUNNING with an incremented attempt before calling the dispatcher.
- A recovered RUNNING record is treated as an interrupted attempt and moved to RETRYABLE or FAILED using a compare-and-set update.
- The final result is persisted before queue acknowledgement. Redelivery of an already terminal job is acknowledged without executing the agent again.
- Retryable results are released back to the queue with the configured delay. The in-memory queue supports immediate release only.
- Dispatch policy failures become terminal, sanitized job errors rather than endless retries.
- Permanently failed jobs are moved to a dead-letter store; terminal redelivery retries this move instead of silently discarding the failure.
- `WorkflowRecoveryService` scans active durable workflow runs and re-invokes the idempotent scheduler after a restart.
- `ProcessIsolatedAgentRunner` can execute an agent call in a killable child process with wall-clock, address-space, CPU and file-descriptor limits on POSIX.

## Reliability boundaries

This is an at-least-once processing design, not exactly-once execution. External side effects performed by an agent can repeat if a worker crashes after the side effect but before the final result is committed. Agents must use the job ID as an idempotency key for external writes, and integrations should support deduplication.

Production queue adapters must implement durable reservations, visibility expiry, delayed delivery, dead-letter handling and fencing/lease semantics. The PostgreSQL queue implements these contracts, including a durable dead-letter table. Configure visibility timeout above the maximum agent runtime; lease renewal is not yet implemented. The default dispatcher remains in-process. The optional process runner limits resource consumption, but it is not a hardened security sandbox: it does not isolate filesystem/network access or credentials. Do not execute untrusted plugin code; use a container/OS sandbox with restricted identity and network policy for that.

The repository's compare-and-set update must be atomic across workers. The worker intentionally does not acknowledge when state persistence conflicts. Monitoring should alert on repeated conflicts and exhausted attempts.

## Remaining production work

Distributed lease renewal, in-flight cancellation and a hardened OS sandbox are not
implemented. The Prometheus metrics registry, WSGI scrape app and PostgreSQL audit
sink are implemented; Prometheus/Grafana servers, dashboards, alert rules and external
log aggregation have not been deployed. A process supervisor such as systemd or
container orchestration should manage the long-running supervisor process. The
in-memory queue, repository and audit sink are for tests/local development only.


## Workflow scheduling boundary

The scheduler is invoked by an external supervisor or scheduled workflow. It is safe to call advance() repeatedly: job submission uses deterministic workflow-step idempotency keys, and workflow-run writes use version compare-and-set. The current in-memory repositories do not provide cross-process guarantees. Production must use durable workflow/job repositories and a durable idempotent queue. A stale workflow in RUNNING state should be scanned and advanced by the supervisor; no background daemon is included in this phase.


## Runtime composition example

The following is a composition pattern for a Linux worker process. The application
must create and migrate the database before starting workers, and must register only
approved agent implementations:

```python
import os
import threading
from psycopg_pool import ConnectionPool

from gni.persistence.postgres import PostgresPersistence
from gni.persistence.audit_postgres import PostgresAuditSink
from gni.observability import MetricsHttpServer, MetricsRegistry, RuntimeTelemetry
from gni.queue.postgres import PostgresJobQueue
from gni.runtime import (
    AgentDispatcher, IsolationLimits, JobWorker,
    ProcessIsolatedAgentRunner, WorkerSupervisor,
)

pool = ConnectionPool(conninfo=DATABASE_URL, min_size=2, max_size=10)
persistence = PostgresPersistence(pool)
queue = PostgresJobQueue(pool)
metrics = MetricsRegistry()
telemetry = RuntimeTelemetry(metrics=metrics, audit_sink=PostgresAuditSink(pool))
dispatcher = AgentDispatcher(
    registry=registry,
    agents=trusted_agent_implementations,
    active_plugins=approved_agent_ids,
    granted_permissions=approved_permission_grants,
    execution_runner=ProcessIsolatedAgentRunner(
        IsolationLimits(timeout_seconds=240, memory_bytes=2 * 1024**3, cpu_seconds=230)
    ),
)
worker = JobWorker(
    worker_id="worker-01",
    queue=queue,
    repository=persistence,
    dispatcher=dispatcher,
    visibility_timeout_seconds=300,
    retry_delay_seconds=30,
    telemetry=telemetry,
)
metrics_server = MetricsHttpServer(
    metrics,
    bearer_token=os.environ["GNI_METRICS_BEARER_TOKEN"],
    host="127.0.0.1",
    port=9108,
)
metrics_server.start()

stop = threading.Event()
try:
    stats = WorkerSupervisor(
        worker, poll_interval_seconds=1, telemetry=telemetry
    ).run(stop)
finally:
    metrics_server.close()
```

A service manager/container orchestrator should own the process and set the stop
event during shutdown. Run a separate recovery loop that calls
`WorkflowRecoveryService(workflow_repository, scheduler).recover()` and run the
outbox publisher loop as separate supervised services. Do not start these loops
inside a GitHub Actions step expecting them to remain alive after the runner exits.

The process-isolated runner currently requires POSIX (Linux) and uses `fork`.
It is a resource/crash boundary only, not a security sandbox: filesystem, network
and inherited credentials are still reachable. For hostile or third-party code,
use a hardened container/VM sandbox with a non-root identity, read-only filesystem,
restricted network egress, no host secrets and explicit syscall/resource policy.


## Runtime health monitor

The PostgreSQL health collector is intentionally an optional dependency-backed
component. Install the `postgres` extra, create the same shared
`RuntimeTelemetry` instance used by workers, and run the monitor alongside the
worker under the process supervisor:

```python
import threading

from gni.observability.health_postgres import PostgresRuntimeHealthCollector
from gni.runtime.health_monitor import RuntimeHealthMonitor

health_collector = PostgresRuntimeHealthCollector(pool, telemetry)
health_monitor = RuntimeHealthMonitor(
    health_collector, interval_seconds=30,
    on_error=lambda exc: logger.exception("Runtime health collection failed", exc_info=exc),
)
health_stop = threading.Event()
health_thread = threading.Thread(
    target=health_monitor.run, args=(health_stop,),
    name="gni-health-monitor", daemon=False,
)
health_thread.start()
```

During graceful shutdown, set `health_stop` and join the thread before closing
the PostgreSQL pool. The monitor records collection failures as a bounded
Prometheus counter and continues polling; a failed database query never
publishes a false zero-valued snapshot. Keep the scrape endpoint private.
