# PostgreSQL persistence schema

## Purpose

The initial PostgreSQL schema provides durable tables for the existing provider-neutral
contracts: jobs, workflow runs and transactional outbox events. It does not select a
database hosting vendor or a queue provider. PostgreSQL is the planned relational
database; hosting, connection management and deployment remain separate decisions.

 Migrations: `migrations/postgresql/001_initial_persistence.sql` and
`migrations/postgresql/002_job_queue.sql`.

## Tables

- `jobs`: authoritative agent job state, JSON payload/result, attempts, trace ID and
  unique submission idempotency key.
- `workflow_runs`: serialized execution plan and step states, protected by a monotonic
  version for compare-and-set updates.
- `outbox_events`: minimal `job.queued` publication intent, linked to its job by a
  foreign key. It stores no duplicate job payload.
- `queue_messages`: durable queue records linked to authoritative jobs, with delayed
  availability, visibility leases, worker ownership and idempotency keys.
- `schema_migrations`: applied migration versions.

## Required transaction boundaries

1. Job submission inserts the `jobs` row and corresponding `outbox_events` row in one
   database transaction. The unique idempotency constraints decide concurrent duplicates.
2. Outbox claiming must atomically select eligible pending events and expired leases,
   increment attempts, and assign a fresh lease token/expiry. PostgreSQL adapters should
   use row locking (for example `FOR UPDATE SKIP LOCKED`) inside a transaction.
3. Mark-published, retry and dead-letter updates must match both event ID and lease token.
   A stale publisher must not mutate a lease acquired by another worker.
4. Job state updates must compare the expected current status in the UPDATE predicate
   and verify exactly one affected row.
5. Workflow updates must compare the expected version and increment it atomically.

## Operational requirements

- Run migrations as a controlled deployment step using a migration runner; do not run
  schema DDL on every worker startup.
- Use TLS for remote database connections, least-privilege credentials, connection
  pooling with bounded limits, backups and tested restore procedures.
- Keep credentials in GitHub Secrets during the GitHub Actions phase; never commit them.
- Do not store media binaries in PostgreSQL. Store media metadata/references here and
  binaries through the provider-neutral object-storage adapter (initially Cloudflare R2).
- Do not use GitHub runner files or workflow artifacts as durable application state.

## Not implemented yet

This migration is a schema foundation, not a production adapter. Python repository
implementations, transaction management, connection pooling, migration automation,
backup/restore operations and a production deployment have not been implemented.
The in-memory repositories and queue remain test-only. A PostgreSQL-backed durable
queue adapter now implements the queue contract; PostgreSQL is the initial queue
backend candidate so the first deployment can avoid operating a separate broker.
A different queue can be added later behind the same contract if throughput or
operational measurements justify it.


## Python adapter

The optional PostgreSQL adapter is in `gni.persistence.postgres`. Install it with:

```bash
python -m pip install -e ".[postgres]"
```

Create a psycopg connection pool in the application composition root and inject it.
The adapter does not connect during import:

```python
from psycopg_pool import ConnectionPool
from gni.persistence.postgres import PostgresPersistence, PostgresWorkflowRepository

pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10)
persistence = PostgresPersistence(pool)
workflow_repository = PostgresWorkflowRepository(persistence)
```

Use `persistence` for the job, submission and outbox contracts, and the
workflow wrapper for the workflow repository contract. The wrapper is necessary
because the job and workflow protocols both define methods named `create`,
`get` and `save` with different record types.

Job + outbox creation is one database transaction. Outbox claims use
`FOR UPDATE SKIP LOCKED` and UUID lease tokens; state changes compare the
current lease token. Job and workflow writes use compare-and-set conditions.

The adapter is an implementation foundation, not a deployed production service.
Configure pool sizing, connection TLS, credentials/secrets, backup/restore,
migration execution and operational monitoring before production use.


## Durable PostgreSQL queue

The optional adapter is `gni.queue.postgres.PostgresJobQueue`. It uses the same
psycopg connection pool as `PostgresPersistence`:

```python
from gni.queue.postgres import PostgresJobQueue

queue = PostgresJobQueue(pool)
```

Queue insertion is idempotent and references the authoritative `jobs` row.
Reservations use `FOR UPDATE SKIP LOCKED`, worker IDs, UUID fencing tokens and
visibility expirations. A worker may acknowledge or release only its current
reservation. Expired reservations become available to another worker; delayed
release updates `available_at`. Acknowledged rows remain as idempotency tombstones.

This queue is durable but shares PostgreSQL's capacity with job/workflow/outbox
traffic. Before high-volume production, benchmark concurrent reservation latency,
database connection pressure, table/index growth and cleanup/retention strategy.
