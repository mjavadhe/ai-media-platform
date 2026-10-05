# ADR-0002: Job Queue

- Status: Proposed
- Date: 2026-10-05

## Context

Spec section 64 requires a production-grade job/message queue supporting: priorities, retries, delayed jobs, cancellation, worker assignment, job status, and failure handling. The queue layer must be abstract enough to allow future replacement. The dev stack must fit in ~4 GiB RAM (AGENTS.md), and the first deployment is a single server (spec section 69). This ADR compares three families and defines explicit decision criteria. Per project policy, no option is pre-selected; the decision is criteria-driven and the final choice is confirmed by the owner.

**Ownership boundary:** the PostgreSQL-backed queue owns **job ordering, visibility (`visible_at`), and atomic scheduling claims** (`SELECT ... FOR UPDATE SKIP LOCKED`) — queue-related persistence mechanics only. It does **not** own `Job.state`: lifecycle ownership stays with the API/domain (`CREATED → QUEUED`), the **Scheduler** (`QUEUED → RESERVED`, `RESERVED → STARTING`, and scheduler-detected recovery transitions) and the **Orchestrator** (completion, failure classification, retry decisions, cancellation execution, media registration), per the frozen Job State Machine in `docs/implementation-contract-2026-10-05.md` section 3. The Worker never writes `Job.state` — it reports events. **GPU placement, reservation, lifecycle and release are owned exclusively by the Scheduler** (see `docs/design/08-scheduler-architecture.md`). Note the two distinct claim operations: the **scheduling claim** (queue mechanism, over `QUEUED` jobs) and the **worker claim** (`POST /api/v1/workers/{id}/jobs/claim`, over `RESERVED` jobs) — neither performs GPU placement, which is Scheduler-owned.

## Options considered

### Option A: Postgres-backed queue (SKIP LOCKED)

Jobs are rows in a `job` table; the scheduler's scheduling claim uses `SELECT ... FOR UPDATE SKIP LOCKED`.

- Pros:
  - Zero additional infrastructure — the database already exists (spec section 62).
  - Lowest RAM footprint of the three (no extra service).
  - Transactional consistency: job state, scheduler decisions, and business data commit atomically. "Same transaction" always refers to the **job + attempt + reservation records**; the physical GPU device row (`gpu_devices.status`) is written only by the scheduler, so completion/failure/cancellation release the GPU in a second transaction (`implementation-contract-2026-10-05.md` sections 5.1 and 14).
  - Priorities: `ORDER BY priority, created_at`. Delayed jobs: `visible_at` timestamp. Cancellation: cooperative status check by the worker. Failure handling: the job status update commits atomically with its attempt/reservation records (never with `gpu_devices.status` — see the ownership boundary above).
  - Trivially testable in CI against a test database — no external services.
- Cons:
  - Polling-based claim loop adds scheduling latency (mitigated by `LISTEN/NOTIFY` or short poll intervals).
  - Sustained high throughput loads the database (acceptable at MVP scale: 1-5 users, tens of jobs).
  - Horizontal scaling beyond one scheduler is harder later.
  - Delayed jobs and cancellation are cooperative, not push-based.

### Option B: Redis-based queue (ARQ / RQ)

- Pros:
  - Low-latency push model; ARQ supports delayed jobs, priorities (multiple queues), retries, and job abort natively.
  - Proven scale path for later phases.
- Cons:
  - Adds Redis to the stack (~50-150 MB RAM — fits the budget but consumes headroom).
  - Two sources of truth (Redis + Postgres) require reconciliation on failure.
  - Persistence must be configured (AOF/RDB) or jobs are lost on restart.
  - ARQ is asyncio-only (worker must be async); RQ's cancellation (`send_stop_job_command`) is fragile across restarts.
  - More moving parts to test in CI (Redis service or fakeredis).

### Option C: Celery

- Pros:
  - Mature, feature-complete: retries, ETA/delayed jobs, priorities (multiple queues), revocation, result backend, huge ecosystem.
- Cons:
  - Heaviest option: Celery worker processes + broker (Redis/RabbitMQ) + optional result backend; largest RAM and ops footprint.
  - Configuration and deployment complexity is the highest of the three.
  - Still requires a broker service, so it does not avoid Option B's infra cost while adding more on top.
  - Overkill for a single-node MVP with 1-5 users.

## Decision criteria (explicit)

| # | Criterion | Weight | A: Postgres SKIP LOCKED | B: Redis (ARQ/RQ) | C: Celery |
|---|-----------|--------|------------------------|-------------------|-----------|
| C1 | Meets all spec section 64 features (priorities, retries, delayed, cancellation, assignment, status, failure) | High | Yes (delayed/cancellation cooperative) | Yes (native) | Yes (native) |
| C2 | Fits 4 GiB dev RAM budget with headroom | High | Best (no extra service) | Good (~50-150 MB) | Worst (multi-process + broker) |
| C3 | Minimal operational complexity for single-node Phase 1-3 | High | Best | Moderate | Worst |
| C4 | Job state durability / consistency | High | Best (transactional) | Moderate (needs AOF/RDB) | Moderate (depends on config) |
| C5 | Testable in CI without external services | Medium | Best | Moderate (fakeredis/Redis svc) | Moderate |
| C6 | Replaceable behind an abstraction (spec section 64) | High | Yes (interface-based) | Yes | Yes |
| C7 | Scale path to multi-node (Phase 9) | Medium | Weakest | Strong | Strong |

## Decision

**Adopt a queue abstraction interface now** (port + adapters, see `docs/design/07-job-lifecycle.md` and `docs/design/04-service-architecture.md`). The implementation is selected by the criteria above at the start of Phase 3:

- **Phase 1-3 (single node, MVP):** criteria C2/C3/C4/C5 favor **Option A (Postgres-backed, SKIP LOCKED)**. It is the provisional choice, pending owner confirmation.
- **Scale path (Phase 9, multi-node):** criteria C7 favors **Option B (Redis/ARQ)**; the abstraction makes this a swap of the adapter, not a rewrite.
- **Option C (Celery)** is not selected: it does not beat Option B on features while being worse on C2/C3.

The owner confirms the Phase 1-3 implementation against these criteria before Phase 3 begins.

## Consequences

- Easier: one less service in dev/CI; transactional job state; no broker ops.
- Harder: delayed jobs and cancellation are cooperative; a future migration to Redis/ARQ must be executed behind the abstraction (planned, not urgent).
- Must verify later: job throughput under the Postgres-backed queue once real job volumes are known (Phase 3+).
