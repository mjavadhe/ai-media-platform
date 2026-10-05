# 07 — Job Lifecycle

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md) · ADR: [ADR-0002](../decisions/ADR-0002-job-queue.md)

## Job states (spec section 13)

```text
CREATED → QUEUED → RESERVED → STARTING → RUNNING → COMPLETED
                    │          │          │
                    │          │          ├→ PAUSED → QUEUED (resume)
                    │          │          ├→ RETRYING → QUEUED
                    │          │          ├→ FAILED
                    │          │          └→ CANCELLED
                    │          └→ FAILED (worker never started)
                    └→ CANCELLED (dequeued before start)
```

## State semantics

"Trigger" is the event that causes the transition. "Owner" is the component that actually writes `Job.state` — **the worker is never the owner**; it only reports events (see `implementation-contract-2026-10-05.md` §3.2). Recovery rows are transitions the Scheduler detects and performs itself.

| State | Meaning | Trigger | Owner (writes `Job.state`) |
|-------|---------|---------|----------------------------|
| CREATED | Request validated, persisted | API request | API/domain |
| QUEUED | In queue, waiting for a GPU | `JobQueue.enqueue` | API/domain |
| QUEUED | Re-queued after recovery | lease expiry / worker loss | **Scheduler** (recovery) |
| RESERVED | Scheduler selected node+GPU, allocation lock held | scheduling claim + GPU placement | **Scheduler** |
| STARTING | Worker claimed the job, loading model | worker's claim request | **Scheduler** (same tx as GPU `RESERVED→BUSY`) |
| RUNNING | Inference executing | worker's first progress report | **Orchestrator** (job row only) |
| PAUSED | Temporarily suspended (where the engine supports it) | user action | Orchestrator |
| RETRYING | Failure classified retryable, waiting for retry delay | failure classification | Orchestrator |
| QUEUED | Re-queued after retry backoff | retry timer / `visible_at` | Orchestrator |
| COMPLETED | Output assets registered | worker's complete report | **Orchestrator** |
| FAILED | Terminal (or retries exhausted) | worker's fail report **or** scheduler-detected recovery | **Orchestrator** for worker-reported failures; **Scheduler** for recovery paths it detects itself |
| CANCELLED | Terminal, by user or system | user/system request | **Orchestrator** executes the job transition; **Scheduler** releases the GPU in Tx 2 |

## Job record (spec section 13)

`Job ID, Project ID, Sequence ID, Shot ID, Job Type, Priority, User, Model, Model Version, Workflow, Workflow Version, Input Assets, GPU, Node, Parameters, Prompt, Status, Progress, Start Time, End Time, Duration, Error, Output Assets` — mapped to the `jobs` table (see `05-database-design.md`). Every state transition is persisted with a timestamp and an optional actor (user/worker/system).

## Job attempts

Each execution is a `job_attempts` row: attempt number, worker id, GPU, status, error, timings. Retries create new attempts, not new jobs — preserving the generation history (spec section 87).

## Allocation sequence (single owner: the scheduler)

The scheduler is the sole owner of GPU allocation and lifecycle transitions; the worker only executes pre-reserved jobs (see `08-scheduler-architecture.md`, `18-worker-protocol.md`).

```text
User/API          Queue           Scheduler          Worker           GPU state
   │ enqueue(job)    │                 │                │
   │───────────────>│                 │                │
   │                 │ job QUEUED      │                │
   │                 │<── claim loop ──│                │
   │                 │                 │ filter+score   │
   │                 │                 │ (capabilities, │
   │                 │                 │  VRAM, policy) │
   │                 │                 │                │
   │                 │  RESERVE (1 transaction):        │ AVAILABLE
   │                 │  job QUEUED→RESERVED,            │   │
   │                 │  GPU AVAILABLE→RESERVED ────────>│ RESERVED
   │                 │                 │                │
   │                 │        dispatch: job claimable    │
   │                 │<── POST /jobs/claim ──────────────│
   │                 │                 │  job RESERVED   │
   │                 │                 │  →STARTING ────>│ RESERVED
   │                 │                 │  GPU RESERVED   │   │
   │                 │                 │  →BUSY ────────>│ BUSY
   │                 │                 │                │
   │                 │            run + progress (SSE)   │
   │                 │                 │<── complete ────│
   │                 │                 │  Tx1 ORCHESTRATOR:      │ BUSY
   │                 │                 │  job RUNNING→COMPLETED, │   │
   │                 │                 │  GeneratedMedia,         │
   │                 │                 │  reservation→RELEASE_PENDING
   │                 │                 │  Tx2 SCHEDULER:          │
   │                 │                 │  GPU BUSY→AVAILABLE ───>│ AVAILABLE
   │                 │                 │  reservation→RELEASED    │
```

Note the two steps at the end: the Orchestrator owns the job's terminal transition and only records the release *request*; the Scheduler executes the physical GPU release in a separate transaction. The worker is the trigger for completion and never touches GPU state. See `08-scheduler-architecture.md` (ownership boundary) and `implementation-contract-2026-10-05.md` section 14.

Failure paths: worker crash/lease expiry → scheduler recovery in **one** transaction (job back to `QUEUED`, new attempt, GPU `RESERVED/BUSY → AVAILABLE` or `UNHEALTHY`); retry policy re-selects the GPU (spec section 14). Worker-reported failure → Orchestrator Tx 1 (job → `FAILED`/`RETRYING`, attempt close, reservation → `RELEASE_PENDING`), Scheduler Tx 2 (GPU release).

## Retry policy (spec section 14)

- **Classification:** failures are classified as `retryable` (OOM, transient CUDA, worker crash, network) or `fatal` (invalid workflow, invalid model, validation error).
- **Policy:** max attempts (default 3), exponential backoff with jitter, attempt cap per job type — **no infinite loops** (spec section 14).
- **Resource re-selection:** on retry, the scheduler re-evaluates compatible GPUs (e.g. OOM on an 11 GB card → prefer a higher-VRAM GPU if available — spec section 14 example).
- **Escalation:** exhausted retries → FAILED with full attempt history.

## Cancellation

- Cooperative: the worker checks a cancellation flag between steps/batches; the queue marks the job `CANCELLED` and the worker stops at the next checkpoint.
- Terminal states are never re-entered.

## Queue abstraction (spec section 64, ADR-0002)

The `JobQueue` port exposes: `enqueue`, `claim` (worker assignment), `update_status`, `cancel`, `retry`, `requeue`, `move`, `list`. Phase 1-3 implementation: Postgres-backed with `SKIP LOCKED` claim, priority ordering, `visible_at` for delayed jobs. The abstraction allows swapping to Redis/ARQ later without touching the Orchestrator or Worker.

## Progress reporting

Workers stream progress (step x of y, tokens, frames) to the Orchestrator, which persists it and fans it out over SSE (ADR-0003). Progress is also recoverable via REST after reconnects.
