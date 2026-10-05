# Implementation Contract — Phase 1 (Frozen)

Date: 2026-10-05 · Status: **FROZEN** · Supersedes: nothing (this is the contract all Phase 1 code must implement)
Source-of-truth: AGENTS.md > spec.md > ADRs (all Proposed) > hardware.md > design docs. Where this contract restates a design doc, the design doc remains the detail reference; where they disagree, this contract wins and the design doc is corrected.

Classification key used throughout: **DOCUMENTATION-CONSISTENT** (docs agree) · **ARCHITECTURALLY-DEFINED** (decided, not yet built) · **IMPLEMENTATION-READY** (unambiguous, codable) · **HARDWARE-VALIDATED** (proven on real hardware — currently NONE).

---

## 1. Executive Summary

This document freezes the Phase 1 implementation contract. Every state machine, ownership rule, transaction boundary, API endpoint, and scope decision needed to write Phase 1 code without guessing is defined here.

Phase 1 proves the canonical happy path end-to-end against the **mock GPU backend**: project → job → queue → scheduler → GPU reservation → worker claim → lease → mock execution → progress → completion → GPU release → generation record.

All ADRs remain **Proposed**; nothing in this contract silently accepts them. The PyTorch/CUDA/Pascal stack is **CANDIDATE / DOCUMENTATION-ALIGNED / PENDING REAL PASCAL VALIDATION** — no hardware validation has occurred.

---

## 2. Frozen Architecture

- Modular monolith control plane (FastAPI + Orchestrator + Scheduler) + separate Worker Agent process (ADR-0001).
- PostgreSQL is the only required infrastructure service: business data + Phase 1-3 job queue (ADR-0002, ADR-0008).
- SSE for realtime push; REST for all commands (ADR-0003).
- Pluggable GPU backend: `auto | nvml | mock` (ADR-0007).
- Scheduler is the **sole** GPU allocation authority; singleton via Postgres advisory lock (doc 04).
- Workers pull pre-reserved jobs; they never allocate GPUs (docs 06, 08, 18).
- Storage behind a port; media never in the DB (doc 11).
- Python 3.12 pinned via uv + committed `uv.lock` (ADR-0005).

---

## 3. Job State Machine (FROZEN)

Canonical states (spec section 13): `CREATED, QUEUED, RESERVED, STARTING, RUNNING, PAUSED, RETRYING, COMPLETED, FAILED, CANCELLED`.

### 3.1 State-transition table

| State | Meaning | Incoming from | Outgoing to | Actor (owner of transition) | DB operation | GPU allocated? | Worker claim exists? | Terminal? |
|-------|---------|---------------|-------------|------------------------------|--------------|----------------|-----------------------|-----------|
| CREATED | Request validated, persisted | — (initial) | QUEUED | API/domain | INSERT job (status=CREATED), then UPDATE → QUEUED in same tx as enqueue | No | No | No |
| QUEUED | In queue, waiting for scheduler | CREATED, PAUSED (resume), RETRYING, lease-expiry recovery | RESERVED, CANCELLED | Scheduler (→RESERVED); User/Orchestrator (→CANCELLED) | `SELECT ... FOR UPDATE SKIP LOCKED` claim; UPDATE status | No | No | No |
| RESERVED | Scheduler selected node+GPU; reservation held | QUEUED | STARTING, CANCELLED, QUEUED (recovery) | Scheduler (→STARTING on worker report); Scheduler (→QUEUED on lease expiry/worker loss); User/Orchestrator (→CANCELLED) | Atomic tx: GPU AVAILABLE→RESERVED **and** job QUEUED→RESERVED | **Yes** | No (claim comes later) | No |
| STARTING | Worker claimed the job; model loading | RESERVED | RUNNING, FAILED, CANCELLED | **Into STARTING:** Scheduler (triggered by the worker's claim request, section 10). **Out of STARTING:** `→RUNNING` Orchestrator (triggered by the worker's first progress report); `→FAILED`/`→CANCELLED` Orchestrator | Into STARTING: atomic tx: GPU RESERVED→BUSY **and** job RESERVED→STARTING (+ claim/attempt/lease rows). Out of STARTING: single-row job UPDATE; no `gpu_devices` write, reservation stays BUSY | Yes | Yes | No |
| RUNNING | Inference executing | STARTING | COMPLETED, PAUSED, RETRYING, FAILED, CANCELLED | Worker *reports*; **Orchestrator** owns the transitions (COMPLETED, FAILED, RETRYING, PAUSED, CANCELLED); **Scheduler** owns them only on recovery paths it detects itself (lease expiry, worker loss, GPU fault) | Tx 1: UPDATE status + progress (Orchestrator) or, on recovery, job + GPU + reservation in one Scheduler tx | Yes | Yes | No |
| PAUSED | Temporarily suspended (engine-dependent) | RUNNING | QUEUED (resume), CANCELLED | User/Orchestrator | UPDATE status | Yes (held) | Yes | No |
| RETRYING | Failure classified retryable; backoff pending | RUNNING | QUEUED, FAILED (retries exhausted) | Orchestrator | UPDATE status + retry_count; schedule `visible_at` | Released before retry | Released | No |
| COMPLETED | Output assets registered | RUNNING | — | Orchestrator (on worker complete report) | Tx 1: UPDATE status, end time, duration; register GeneratedMedia; reservation→RELEASE_PENDING. Tx 2 (Scheduler): GPU BUSY→AVAILABLE + reservation→RELEASED | Released by Scheduler in Tx 2 | Released | **Yes** |
| FAILED | Terminal failure (fatal or retries exhausted) | RESERVED, STARTING, RUNNING, RETRYING | — | Orchestrator (worker-reported failure); Scheduler (recovery paths: lease expiry, worker loss, GPU failure) | Tx 1: UPDATE status, error, end time; attempt close; reservation→RELEASE_PENDING. Tx 2 (Scheduler): GPU release. Recovery paths are a single Scheduler tx | Released (Scheduler, Tx 2) | Released (if any) | **Yes** |
| CANCELLED | Terminal cancellation | QUEUED, RESERVED, STARTING, RUNNING | — | Orchestrator (executes the job transition) | Tx 1: UPDATE status; attempt close; reservation→RELEASE_PENDING. Tx 2 (Scheduler): GPU RESERVED/BUSY→AVAILABLE + reservation→RELEASED. If no GPU held (QUEUED), Tx 1 only | Released (Scheduler, Tx 2) | Released | **Yes** |

### 3.2 Who may change Job.state — unambiguous rule

- **Scheduler owns:** QUEUED→RESERVED, RESERVED→STARTING (triggered by the worker's claim request), RESERVED→QUEUED (recovery), QUEUED/RUNNING→FAILED and QUEUED/RUNNING→RETRYING *only on recovery paths it detects itself* (lease expiry, worker loss, GPU failure) — in those single transactions the Scheduler also performs the physical GPU release, because it owns both sides (section 5.1).
- **Scheduler exclusively owns** all mutations of `gpu_devices.status` (physical GPU lifecycle), on every path.
- **Orchestrator owns:** STARTING→RUNNING (triggered by the worker's first progress report — there is no separate `/running` endpoint; the first progress report *is* the signal that the engine is executing), RUNNING→COMPLETED, RUNNING→FAILED, RUNNING→RETRYING, RUNNING→CANCELLED, STARTING→FAILED, STARTING→CANCELLED, RETRYING→QUEUED, RETRYING→FAILED, RUNNING→PAUSED, PAUSED→QUEUED, and the Tx 1 side of RESERVED→CANCELLED. STARTING→RUNNING touches **only** the job row: `gpu_devices.status` stays `BUSY` and the reservation stays `BUSY`, so no Scheduler involvement is required.
- **API/domain owns:** CREATED→QUEUED (enqueue).
- **Who requests *vs* who executes:** the *user* requests cancellation, but the **Orchestrator** executes the job transition transactionally. A request is never an ownership grant.
- **Orchestrator's only permitted GPU write** is the logical reservation marker `gpu_reservations.state → RELEASE_PENDING` (section 5.1). It never writes `gpu_devices.status`.
- **Worker never directly sets Job.state.** The worker *reports events* (`/progress`, `/complete`, `/fail`); the orchestrator validates and performs the transition. A worker report is a trigger, not a state ownership grant.

---

## 4. Job Attempt Contract (FROZEN)

> **A retry MUST create a new Job Attempt and MUST NOT overwrite historical attempt data.** (Confirmed.)

- **Creation:** one `job_attempts` row per execution, created when a worker claims a job (claim → attempt association in one transaction).
- **Numbering:** `attempt_no` = MAX(attempt_no) for the job + 1, assigned transactionally.
- **Status:** mirrors the job states of its execution window: `RUNNING` → `COMPLETED | FAILED | INTERRUPTED`.
- **Retry linkage:** `job_id` + `attempt_no`; the failed attempt's `error` and `failure_class` drive the retry decision.
- **Timestamps:** `created_at`, `started_at`, `finished_at`, `duration`.
- **Failure reason:** `error` (message) + `failure_class` (`oom`, `cuda_error`, `engine_error`, `validation_error`, `storage_error`, `worker_lost`, `worker_crash`, `cancelled`).
- **Worker/node:** `worker_id`, `node_id`.
- **GPU:** `gpu_device_id` (the reserved GPU).
- **Model/workflow:** denormalized snapshot on the attempt (`model_version_id`, `workflow_version_id`, `seed`, `params`) so history survives registry changes.
- **Logs:** attempt-scoped log correlation via `job_id` + `attempt_no`.
- **Reuse:** attempts are immutable. Never updated in place except for status/timestamps of the live attempt; historical attempts are never rewritten.

---

## 5. GPU State Machine (FROZEN)

Canonical lifecycle states (spec §7 + §51, reconciled): `AVAILABLE, RESERVED, BUSY, DRAINING, UNHEALTHY, OFFLINE, MAINTENANCE`. Health flag `ERROR` (spec §51) is reported while lifecycle state is `UNHEALTHY`.

| Transition | Owner | Trigger | Transaction | Job relationship |
|------------|-------|---------|-------------|------------------|
| AVAILABLE→RESERVED | Scheduler | Job reservation | Same tx as job QUEUED→RESERVED | Job id recorded on GPU allocation |
| RESERVED→BUSY | Scheduler | Worker claim request (= start trigger, section 10) | Same tx as job RESERVED→STARTING | Job id |
| BUSY→AVAILABLE | Scheduler | Job COMPLETED/CANCELLED/FAILED | **Separate tx (Tx 2)** — Orchestrator marks reservation RELEASE_PENDING in Tx 1 (with job terminal transition); Scheduler executes GPU release in Tx 2. Crash-safe via reconciliation (section 14) | Job id cleared |
| RESERVED→AVAILABLE | Scheduler | **Worker loss / lease expiry:** single tx (scheduler owns both job recovery and GPU release). **Cancellation before start:** two-tx — Orchestrator marks reservation RELEASE_PENDING in Tx 1 (with job→CANCELLED), Scheduler releases GPU in Tx 2 | Single tx for worker loss; two txs for cancellation (section 14) | Job id cleared |
| BUSY→UNHEALTHY | Scheduler | GPU error reported (worker metrics or execution failure) | GPU update; job →FAILED/RETRYING | Job id |
| RESERVED→UNHEALTHY | Scheduler | GPU error before start | GPU update; job →QUEUED (re-select) | Job id cleared |
| UNHEALTHY→AVAILABLE | Scheduler (admin action) | Admin clears error after maintenance | GPU update; audit entry | None |
| Any→DRAINING | Scheduler (admin action) | Admin requests drain | GPU update; no new reservations | Current job finishes first |
| DRAINING→AVAILABLE | Scheduler | Current job completes/cancels | GPU update | Job id cleared |
| Any→MAINTENANCE | Scheduler (admin action) | Admin sets maintenance | GPU update; no new reservations | Current job finishes first |
| MAINTENANCE→AVAILABLE | Scheduler (admin action) | Admin clears maintenance | GPU update | None |
| Any→OFFLINE | Scheduler | Heartbeat lost × N, or admin | GPU update; node OFFLINE; jobs evaluated | Running jobs requeued |

**Invariant:** only the scheduler (or an admin action executed through the scheduler) transitions GPU state. Workers report facts (metrics, errors); they never transition GPU state. The `GpuBackend` port exposes **no** allocation API.

**Allocation state machine (GPUReservation):** `RESERVED → BUSY → RELEASE_PENDING → RELEASED`. The Orchestrator records `RESERVED/BUSY → RELEASE_PENDING` (in the same tx as the job terminal transition); the Scheduler executes the physical GPU release (`BUSY→AVAILABLE` or `RESERVED→AVAILABLE`) and `RELEASE_PENDING→RELEASED`. The Orchestrator never mutates `gpu_devices.status`.

### 5.1 GPU Ownership Boundary (FROZEN)

There are two distinct state domains and they have different owners:

1. **`gpu_devices.status`** — physical GPU lifecycle state (`AVAILABLE, RESERVED, BUSY, DRAINING, UNHEALTHY, OFFLINE, MAINTENANCE`).
   **ONLY the Scheduler may mutate it.**
2. **`gpu_reservations.state`** — logical allocation/release state associated with a job (`RESERVED, BUSY, RELEASE_PENDING, RELEASED`).
   The **Orchestrator** may *request* release by transitioning `RESERVED/BUSY → RELEASE_PENDING`.
   The **Scheduler** exclusively *completes* `RELEASE_PENDING → RELEASED` and performs the corresponding physical GPU release.

```text
Orchestrator
    owns job lifecycle
    may mark GPUReservation RELEASE_PENDING
    NEVER mutates gpu_devices.status

Scheduler
    owns physical GPU lifecycle
    performs reservation/release
    performs GPU state transitions
```

Consequence: the release of a GPU is always a *two-step* logical operation (request → execute). Only the second step touches `gpu_devices.status`, so the number of writers to `gpu_devices.status` is exactly one regardless of how many Orchestrator-driven terminal transitions occur.

---

## 6. Scheduler Contract (FROZEN)

- **Sole authority** for: which job runs on which GPU; GPU reservation/release; all mutations of `gpu_devices.status` (physical GPU lifecycle — see section 5.1).
- **Inputs** (spec §12): required VRAM, model/workflow requirements, GPU capabilities, node availability, GPU load, model cache, user pinning, priority, policy.
- **Algorithm:** filter → score → reserve (atomic) → dispatch → activate → release (doc 08).
- **Policies:** AUTO, FASTEST, LOWEST_LOAD, LOWEST_RESOURCE_COST, CAPABILITY_MATCH, MODEL_CACHE_AWARE, ROUND_ROBIN, MANUAL — pluggable scoring functions.
- **Priority:** LOW < NORMAL < HIGH < URGENT; ordering only, no preemption.
- **Singleton:** Postgres advisory lock (`pg_try_advisory_lock`, session-level, env-namespaced key). Exactly one leader runs the allocation loop; API-only processes skip scheduling. Leader crash → lock auto-released → another process acquires within seconds. No Redis, no external coordination service.
- **Multi-GPU jobs:** only when model version + engine adapter declare multi-GPU support; all-or-nothing atomic GPU-set allocation. Parallel jobs on separate GPUs are the default and are NOT multi-GPU inference.
- **Observability:** every allocation decision logged with inputs + scores (spec §90).

---

## 7. Queue Contract (FROZEN) — Phase 1-3

PostgreSQL-backed queue (ADR-0002 provisional choice, owner to confirm). The `jobs` table is the queue.

- **Enqueue:** INSERT/UPDATE job → `QUEUED` with `priority`, `created_at`, `visible_at` (for delayed jobs).
- **Ordering:** `ORDER BY priority DESC, created_at ASC` (FIFO within priority).
- **Scheduling claim (leader only):** `SELECT ... FOR UPDATE SKIP LOCKED` — concurrent claims never return the same row. This is the *scheduling* claim of a `QUEUED` job and is distinct from the *worker* claim of a `RESERVED` job (section 10).
- **Concurrent schedulers:** safe — only the advisory-lock leader runs the loop; SKIP LOCKED is the second guard.
- **Stale jobs:** a QUEUED job whose `visible_at` is in the future is not claimable; a job stuck in RESERVED past its lease is recovered (section 10).
- **Cancelled jobs:** status flag checked by worker between checkpoints; scheduler never dispatches CANCELLED jobs.
- **Retries:** Orchestrator sets `RETRYING` → `visible_at = now() + backoff(attempt)` → `QUEUED`; new attempt on next claim.
- **Delayed retries:** `visible_at` timestamp; claim query filters `visible_at <= now()`.

**Separated responsibilities (never merged):** Queue = ordering/visibility/atomic claim. Scheduler = GPU placement. Worker = execution of claimed jobs.

---

## 8. Worker Contract (FROZEN)

- **Discovers** GPUs via `GpuBackend`; **reports** capabilities/metrics/health; **executes** assigned work; **reports** progress/completion/failure.
- **MUST NOT:** choose/reserve/release GPUs, bypass scheduler placement, allocate GPUs independently, or act as a scheduler.
- **Pull model:** `POST /api/v1/workers/{id}/jobs/claim`; the claim response carries the reserved GPU id. The worker re-validates capabilities/VRAM against the reserved GPU (safety net only).
- **Execution:** engine adapter per workflow's engine; outputs via storage port; progress via `POST /api/v1/jobs/{id}/progress`.
- **Failure reporting:** `POST /api/v1/jobs/{id}/fail` with classified error; orchestrator owns the retry decision.

---

## 9. Authentication Contract (FROZEN)

### 9.1 Worker registration lifecycle

1. **Admin** (authenticated, ADMIN role) calls `POST /api/v1/workers` → control plane generates a **one-time registration token** (stored hashed, shown once).
2. Admin installs the worker with the token (env/config).
3. Worker calls `POST /api/v1/workers/register` with the one-time token → token consumed (single use) → control plane issues **standing credentials**: a long-lived worker token (stored hashed, revocable) + short-lived access credentials (default 24 h; mTLS client cert where supported).
4. Worker uses access credentials for all API calls.
5. **Renewal:** `POST /api/v1/workers/{id}/credentials` with the long-lived token → new short-lived access credentials. Transparent to running jobs.
6. **Revocation:** admin revokes the worker token → worker fails auth on next call → must re-register. Admin can disable a worker (node → MAINTENANCE/OFFLINE).

### 9.2 Endpoint authentication matrix

| Endpoint | Authentication |
|----------|----------------|
| `POST /api/v1/auth/login` | none (rate-limited) |
| `GET /health`, `GET /ready` | none |
| `POST /api/v1/workers/register` | one-time registration token (unauthenticated by design — the token IS the credential) |
| `POST /api/v1/workers` | ADMIN role |
| `POST /api/v1/workers/{id}/heartbeat` | worker access credentials |
| `POST /api/v1/workers/{id}/jobs/claim` | worker access credentials |
| `POST /api/v1/workers/{id}/claims/{claim_id}/renew` | worker access credentials |
| `POST /api/v1/workers/{id}/credentials` | long-lived worker token |
| `POST /api/v1/jobs/{id}/progress|complete|fail` | worker access credentials |
| All other `/api/v1/*` | session cookie (UI) or `Authorization: Bearer <token>` (API tokens) |

The register endpoint is the single deliberate exception to "everything requires auth"; it is documented in doc 19 and is not an accidental contradiction.

---

## 10. Claim / Lease Contract (FROZEN)

- **Claim:** `POST /api/v1/workers/{id}/jobs/claim` (POST — side effects). Creates a `JobClaim` row: `claim_id`, `job_id`, `worker_id`, `attempt_id`, `expires_at = now() + 60 s` (default, configurable). The job must be RESERVED for this worker's node; the claim carries the reserved GPU id.
- **Attempt association:** claim creation and attempt creation are one transaction (section 14).
- **The claim request is the start trigger.** There is no separate `/start` endpoint. The authenticated claim request is validated by Scheduler-executed logic that, in **one single transaction**, performs all of: create `JobClaim` + `JobAttempt` + lease; set `GPUReservation.worker_id`; `Job RESERVED→STARTING`; `GPU RESERVED→BUSY`; `GPUReservation RESERVED→BUSY`. This is the `Start` row of the matrix in 14.5 — one executing component (Scheduler), one transaction, covering both the job side and the physical GPU side, because the Scheduler owns both (section 5.1). A worker never writes `jobs.status` or `gpu_devices.status`.
- **Who executes it:** the HTTP handler authenticates the worker, then delegates the transactional work to the Scheduler-owned claim/start service. The worker is the *trigger*, not the owner.
- **Lease renewal:** heartbeat includes active `claim_id`s → leases extended. Dedicated `POST /api/v1/workers/{id}/claims/{claim_id}/renew` for between-heartbeat renewal. No maximum renewal count — renewal requires a live worker (heartbeat proves liveness).
- **Lease expiry (deterministic recovery):**
  1. Claim released; job → `QUEUED` (visible immediately, or after short delay if it had started).
  2. Attempt marked `INTERRUPTED` with `failure_class=worker_lost` (retryable).
  3. GPU released by the **Scheduler** in the same transaction (`RESERVED→AVAILABLE`, or `BUSY→AVAILABLE`/`UNHEALTHY`) — no Orchestrator transaction is involved because this is a scheduler-owned recovery path.
  4. Scheduler re-selects a GPU per retry policy; new attempt on next claim.
- **Same job claimed twice:** impossible while a lease is valid (visibility timeout); after expiry the old attempt is closed before the new claim is accepted.
- **Worker continues after lease expiry:** no — the worker checks lease validity between checkpoints and self-terminates; the server-side recovery above is the source of truth.
- **Idempotency:** claim is idempotent per (worker, job) while a valid lease exists — returns the existing claim instead of creating a duplicate.

### 10.1 Worker assignment contract

The `GPUReservation` record identifies the execution target:

```text
GPUReservation
  reservation_id (PK)
  job_id (FK -> jobs)
  node_id (FK -> gpu_nodes)          # the node where the GPU lives
  gpu_device_id (FK -> gpu_devices)  # the specific GPU
  worker_id (FK -> workers, nullable) # set at claim time, not at reservation time
  policy (text)                       # which scheduling policy selected this
  scores (jsonb)                      # policy input scores for observability
  state: RESERVED | BUSY | RELEASE_PENDING | RELEASED
  created_at, updated_at
```

**Key invariant:** the reservation is **node-scoped at creation** (scheduler picks node+GPU), then **worker-scoped at claim** (the worker on that node claims it). The reservation does NOT contain a worker_id at creation time — the scheduler does not know which worker will claim it, only which node/GPU.

**Claim authorization invariant:**

```text
worker.node_id == GPUReservation.node_id
AND
GPUReservation.gpu_device_id belongs to worker.node_id
```

A worker can only claim a reservation whose `node_id` matches the worker's registered node. Cross-node claims are rejected.

### 10.2 Claim security invariant

A claim MUST fail with `403 CLAIM_REJECTED` if any of the following is true:

| Condition | Failure response |
|-----------|-----------------|
| Worker is not registered | `403 WORKER_NOT_FOUND` |
| Worker is disabled | `403 WORKER_DISABLED` |
| Worker's node is OFFLINE | `403 NODE_OFFLINE` |
| Job is not in RESERVED state | `409 JOB_NOT_RESERVABLE` |
| Job reservation belongs to another node | `403 RESERVATION_NODE_MISMATCH` |
| Reserved GPU does not belong to worker's node | `403 GPU_NODE_MISMATCH` |
| Job is already claimed by another active worker | `409 JOB_ALREADY_CLAIMED` |

The claim endpoint validates all conditions in a single transaction before creating the claim.

---

## 11. Heartbeat Contract (FROZEN)

- **Endpoint:** `POST /api/v1/workers/{id}/heartbeat`, authenticated with worker access credentials.
- **Interval:** ~10 s (configurable).
- **Payload:** node status, per-GPU metrics snapshot, current job per GPU, active `claim_id`s (→ lease renewal), model cache state, health flags.
- **Semantics:** (a) worker liveness update (`last_heartbeat_at`); (b) GPU metrics persistence + SSE fan-out; (c) lease renewal for listed claims.
- **Timeout:** missed × N (default 3) → node `OFFLINE` → running jobs evaluated → retryable requeued, non-retryable FAILED with `worker_lost` → GPUs `OFFLINE`.
- **Distinctions:** heartbeat = liveness + metrics + lease renewal. Lease renewal = claim visibility extension. Progress reporting = `POST /api/v1/jobs/{id}/progress` (step/total, percent, message). They share the heartbeat endpoint but have explicit separate semantics.

---

## 12. Retry Contract (FROZEN)

- **Max retries:** 3 (default, configurable per job type). Bounded — no infinite loops.
- **Retryable:** worker crash, transient network failure, transient CUDA failure, GPU OOM (if policy allows), lease expiration.
- **Fatal:** invalid workflow, invalid model, invalid request, validation error, unsupported capability. Never retried.
- **Counter:** `retry_count` on the job; `attempt_no` on attempts.
- **Backoff:** exponential with jitter; `visible_at = now() + backoff(attempt_no)`.
- **Attempt creation:** every retry creates a NEW attempt (section 4).
- **GPU release:** before requeue, the failed GPU is released by the **Scheduler** (Tx 2 of the failure transaction, section 14) and excluded/down-weighted in re-selection (spec §14: OOM on one GPU → prefer a higher-VRAM GPU if one exists).
- **Worker behavior:** none — the worker reports failure; the orchestrator owns the retry decision.
- **Final FAILED:** retries exhausted → job FAILED with full attempt history.

---

## 13. Cancellation Contract (FROZEN)

- **Who requests:** user (ARTIST/OPERATOR/ADMIN) or system (admin worker disable).
- **Who performs:** Orchestrator owns the job transition; Scheduler owns the GPU release. Two transactions (see section 14).
- **Transitions:** QUEUED→CANCELLED, RESERVED→CANCELLED, STARTING→CANCELLED, RUNNING→CANCELLED. Terminal states never re-entered.
- **Cancellation while RESERVED (before start):**
  - Tx 1 (Orchestrator): Job RESERVED→CANCELLED + GPUReservation RESERVED→RELEASE_PENDING.
  - Tx 2 (Scheduler): GPU device RESERVED→AVAILABLE + GPUReservation RELEASE_PENDING→RELEASED.
- **Cancellation while BUSY/RUNNING:**
  - Tx 1 (Orchestrator): Job RUNNING→CANCELLED + GPUReservation BUSY→RELEASE_PENDING.
  - Tx 2 (Scheduler): GPU BUSY→AVAILABLE + GPUReservation RELEASE_PENDING→RELEASED.
- **Worker notification:** cooperative — the worker checks the cancellation flag between steps/batches and aborts at the next checkpoint; the claim is closed with `failure_class=cancelled`.
- **Cancellation timeout:** if the worker does not abort within a configurable grace period, the server force-closes the attempt as `INTERRUPTED` (retryable only if the user explicitly retries — a cancelled job is NOT auto-retried).
- **Attempt state:** closed as CANCELLED (or INTERRUPTED on force-close).

---

## 14. Transaction Boundaries (FROZEN)

### 14.1 The completion-ownership contradiction and its resolution

**The problem:** Job completion is Orchestrator-owned (`RUNNING→COMPLETED`) but GPU release is Scheduler-owned (`BUSY→AVAILABLE`). A single transaction containing both would make the Orchestrator execute a GPU state mutation — a second GPU owner. This is forbidden.

**The resolution — two transactions, scheduler-executed GPU release:**

The `GPUReservation` record has its own logical state machine: `RESERVED → BUSY → RELEASE_PENDING → RELEASED`. Per section 5.1, the Orchestrator may record `RESERVED/BUSY → RELEASE_PENDING` (a release *request*); only the Scheduler completes `RELEASE_PENDING → RELEASED` and performs the physical GPU release. The Orchestrator never touches `gpu_devices.status`.

**Completion (success):**
- **Tx 1 (Orchestrator):** validate completion → job `RUNNING→COMPLETED` → end time, duration → register GeneratedMedia → `GPUReservation → RELEASE_PENDING`.
- **Tx 2 (Scheduler):** `GPU BUSY→AVAILABLE` + `GPUReservation RELEASE_PENDING→RELEASED`.

The Scheduler executes Tx 2 either (a) synchronously when the Orchestrator notifies it of completion (in-process call, separate transaction), or (b) via periodic reconciliation of `RELEASE_PENDING` allocations. Path (b) is the crash-safety backstop.

**Crash between Tx 1 and Tx 2:** job is COMPLETED but GPU is stuck at BUSY with a `RELEASE_PENDING` allocation. The scheduler's reconciliation loop detects this and completes the release. Deterministic, safe, no double-release (single scheduler leader + allocation state machine prevents it).

**The same two-transaction pattern applies to failure and cancellation** (sections 14.2, 14.3). Operations where the Scheduler owns both the job transition and the GPU transition (reservation, start, worker loss, GPU failure) remain single-transaction.

### 14.2 Transaction table

| Operation | Tx 1 (Orchestrator) | Tx 2 (Scheduler) | Mechanism |
|-----------|---------------------|-------------------|-----------|
| Job creation + enqueue | INSERT job + status QUEUED | — | single tx |
| Scheduler reservation | — | GPU AVAILABLE→RESERVED **and** job QUEUED→RESERVED | single tx; leader holds advisory lock |
| Worker claim (= start trigger, section 10) | — | single tx: claim row + attempt row + lease + `GPUReservation.worker_id` + job RESERVED→STARTING + GPU RESERVED→BUSY + GPUReservation RESERVED→BUSY | single tx; scheduler owns both sides; job must be RESERVED for this node |
| Lease renewal | — | UPDATE claim `expires_at` | single row update; heartbeat auth proves liveness |
| Lease expiry recovery | — | close attempt (INTERRUPTED) + job →QUEUED + GPU release | single tx; scheduler owns both transitions |
| Completion | job →COMPLETED + end time + GeneratedMedia + GPUReservation→RELEASE_PENDING | GPU BUSY→AVAILABLE + GPUReservation→RELEASED | two txs; scheduler executes GPU mutation |
| Failure | job →FAILED/RETRYING + attempt close + GPUReservation→RELEASE_PENDING | GPU BUSY→AVAILABLE + GPUReservation→RELEASED | two txs; scheduler executes GPU mutation |
| Retry scheduling | job →RETRYING→QUEUED + `visible_at` + retry_count++ | — (GPU already released by failure tx) | single tx; new attempt only on next claim |
| Cancellation before start (job RESERVED) | job RESERVED→CANCELLED + attempt close + GPUReservation RESERVED→RELEASE_PENDING | GPU device RESERVED→AVAILABLE + GPUReservation RELEASE_PENDING→RELEASED | two txs; scheduler executes GPU mutation |
| Cancellation while running (job RUNNING) | job RUNNING→CANCELLED + attempt close + GPUReservation BUSY→RELEASE_PENDING | GPU BUSY→AVAILABLE + GPUReservation RELEASE_PENDING→RELEASED | two txs; scheduler executes GPU mutation |
| Worker loss (lease expiry) | — | close attempt + job →QUEUED + GPU release | single tx; scheduler owns both |
| GPU failure | — | GPU →UNHEALTHY + job →FAILED/RETRYING | single tx; scheduler owns both |
| Scheduler leadership | — | `pg_try_advisory_lock` (session-level) | advisory lock; auto-released on process death |

**Isolation:** READ COMMITTED is sufficient given the above atomic units; `SKIP LOCKED` prevents claim contention; the advisory lock prevents dual leaders. No read-then-write races remain: every decision that affects allocation is made and committed in the same transaction that records it. The two-transaction completion/failure/cancellation path is made crash-safe by the scheduler's reconciliation of `RELEASE_PENDING` allocations.

### 14.3 Consistency model for two-transaction operations

For completion, failure, and cancellation:

1. **Normal path:** Tx 1 commits → scheduler notified → Tx 2 commits. Total latency: milliseconds.
2. **Crash after Tx 1, before Tx 2:** job is terminal, GPU is BUSY with RELEASE_PENDING allocation. Scheduler reconciliation (runs every N seconds on the leader) detects RELEASE_PENDING allocations older than a threshold and completes the release. The job is already correctly terminal — only the GPU needs release.
3. **Crash before Tx 1:** job is still RUNNING, GPU is BUSY. Lease expiry or worker-loss detection recovers it (single-tx path, scheduler-owned).
4. **Double Tx 2:** impossible — only the scheduler leader runs reconciliation, and the allocation state machine (RELEASE_PENDING→RELEASED) is a conditional UPDATE that succeeds exactly once.

**Ownership invariant:** The Orchestrator owns job terminal semantics for completion, failure, retry, and cancellation. For terminal transitions that require GPU release, the Orchestrator records the logical `GPUReservation` transition to `RELEASE_PENDING` in the same transaction as the job transition. The Scheduler exclusively executes the physical GPU lifecycle mutation and completes `RELEASE_PENDING → RELEASED`. The Orchestrator never mutates `gpu_devices.status`.

Scheduler-owned recovery transitions (reservation, start, lease expiry, worker loss, GPU failure) may mutate both job state and physical GPU state in one transaction because the Scheduler owns both sides of those recovery operations.

### 14.4 Ownership matrix (FROZEN)

| Operation | Job Owner | GPU Owner | Transaction Owner |
|-----------|-----------|-----------|-------------------|
| Queue (enqueue) | API/domain | — | API/domain |
| Reserve | Scheduler | Scheduler | Scheduler (single tx) |
| Start (claim + STARTING) | Scheduler (triggered by the worker's claim request, section 10) | Scheduler | Scheduler (single tx) |
| Start → Running (first progress report) | Orchestrator (job row only; GPU stays BUSY) | — (no physical GPU write) | — (none required) |
| Complete | Orchestrator | Scheduler | Tx 1: Orchestrator. Tx 2: Scheduler |
| Fail | Orchestrator | Scheduler | Tx 1: Orchestrator. Tx 2: Scheduler |
| Retry | Orchestrator | Scheduler (already released) | Orchestrator (single tx; GPU released by prior failure) |
| Cancel | Orchestrator | Scheduler | Tx 1: Orchestrator. Tx 2: Scheduler |
| Lease expiry | Scheduler | Scheduler | Scheduler (single tx) |
| Worker loss | Scheduler | Scheduler | Scheduler (single tx) |
| GPU failure | Scheduler | Scheduler | Scheduler (single tx) |

**No ambiguous cells.** The Scheduler is the sole owner of `gpu_devices.status` in every row. The Orchestrator owns job terminal transitions and may write only the logical reservation marker (`→ RELEASE_PENDING`); it never writes `gpu_devices.status`. Where both a job transition and a GPU release are needed (complete/fail/cancel), it is two transactions with the Scheduler executing the GPU mutation in Tx 2.

### 14.5 Final consistency matrix (FROZEN)

**No row below contains an ambiguous transaction owner.** Every row names exactly one executing component per transaction.

| Operation | Tx 1 | Tx 2 | Physical GPU mutation |
|-----------|------|------|-----------------------|
| Reservation | **Scheduler:** Job QUEUED→RESERVED + reservation RESERVED | — | Scheduler |
| Start | **Scheduler:** Job RESERVED→STARTING + GPU RESERVED→BUSY | — | Scheduler |
| Start → Running | **Orchestrator:** Job STARTING→RUNNING (trigger: worker's first progress report). Job row only — GPU stays BUSY | — | none (no physical GPU write) |
| Completion | **Orchestrator:** Job→COMPLETED + GeneratedMedia + reservation→RELEASE_PENDING | **Scheduler:** GPU BUSY→AVAILABLE + reservation→RELEASED | Scheduler |
| Failure | **Orchestrator:** Job→FAILED/RETRYING + attempt close + reservation→RELEASE_PENDING | **Scheduler:** GPU release + reservation→RELEASED | Scheduler |
| Cancellation before start | **Orchestrator:** Job→CANCELLED + reservation→RELEASE_PENDING | **Scheduler:** GPU RESERVED→AVAILABLE + reservation→RELEASED | Scheduler |
| Cancellation while running | **Orchestrator:** Job→CANCELLED + reservation→RELEASE_PENDING | **Scheduler:** GPU BUSY→AVAILABLE + reservation→RELEASED | Scheduler |
| Lease expiry | **Scheduler:** recovery + GPU release | — | Scheduler |
| Worker loss | **Scheduler:** recovery + GPU release + node state | — | Scheduler |
| GPU failure | **Scheduler:** GPU→UNHEALTHY + job recovery | — | Scheduler |

**Reading rule:** rows with an Orchestrator Tx 1 and a Scheduler Tx 2 are the two-transaction terminal-semantics path. Rows with a single Scheduler transaction are recovery paths where the Scheduler owns both the job side and the physical GPU side, so a single atomic transaction is correct and no Orchestrator involvement is required.

**Crash-window summary for two-transaction rows:** if the process dies between Tx 1 and Tx 2, the job is already in its correct terminal state and the reservation is `RELEASE_PENDING`; the Scheduler's reconciliation loop completes Tx 2. The only intermediate observable is a terminal job paired with a still-`BUSY`/`RESERVED` GPU, which is bounded by the reconciliation interval and is not an invalid state (section 5.1: the reservation carries the release request).

### 14.6 Idempotency contract (FROZEN)

| Endpoint | Idempotency mechanism | Duplicate behavior (same outcome) | Conflicting outcome | GPU-release behavior |
|----------|----------------------|--------------------------------|---------------------|---------------------|
| `POST /api/v1/jobs/{id}/complete` | Natural: job status check. Only RUNNING jobs can complete. | If already COMPLETED: returns current state `200 OK`, `idempotent: true`, no re-processing. | If already FAILED/CANCELLED: `409 JOB_NOT_RUNNING`. No state change. | Released exactly once: Tx 2 is a conditional UPDATE on reservation state (RELEASE_PENDING→RELEASED). Second execution affects 0 rows. |
| `POST /api/v1/jobs/{id}/fail` | Natural: job status check. Only STARTING/RUNNING jobs can fail. | If already FAILED: returns current state `200 OK`, `idempotent: true`. | If already COMPLETED/CANCELLED: `409 JOB_NOT_RUNNING`. | Same as completion — conditional UPDATE prevents double-release. |
| `POST /api/v1/jobs/{id}/cancel` | Natural: job status check. Only non-terminal jobs can cancel. | If already CANCELLED: returns current state `200 OK`, `idempotent: true`. | If already COMPLETED/FAILED: `409 JOB_NOT_CANCELLABLE`. | Released exactly once via conditional UPDATE (same as completion). |
| `POST /api/v1/workers/{id}/jobs/claim` | Natural: partial unique index on (job_id) while a claim is active; `(worker_id, job_id)` unique per attempt. | Returns the existing valid claim + same `claim_id`, no duplicate claim/attempt rows. | If job already claimed by a *different* active worker: `409 JOB_ALREADY_CLAIMED`. If job not RESERVED: `409 JOB_NOT_RESERVABLE`. | N/A (claim does not release GPU). |
| `POST /api/v1/workers/{id}/claims/{claim_id}/renew` | Natural: lease validity check. | Returns current `expires_at`, no duplicate row. | If claim expired/released: `409 CLAIM_EXPIRED`. | N/A. |
| Scheduler release / reconciliation | Conditional state transition on `gpu_reservations.state`. | Already RELEASED → 0 rows affected, treated as success (no-op). | Reservation not in RELEASE_PENDING (e.g. still RESERVED/BUSY, or absent) → skipped; never transitions backwards. | This is the **only** writer of `gpu_devices.status` back to AVAILABLE. Exactly-once by construction. |

**Why duplicate completion cannot double-release a GPU:** the release is two conditional updates that must both match:

```sql
UPDATE gpu_devices
   SET status = 'AVAILABLE', reservation_id = NULL
 WHERE id = :gpu AND status IN ('BUSY','RESERVED') AND reservation_id = :res_id;

UPDATE gpu_reservations
   SET state = 'RELEASED'
 WHERE id = :res_id AND state = 'RELEASE_PENDING';
```

The second statement matches exactly once (state leaves `RELEASE_PENDING` and never returns). The first is additionally guarded by `reservation_id`, so a reservation already released cannot free a GPU that has since been reserved for another job.

**Attempt closure:** `UPDATE job_attempts SET state='...' WHERE id=:attempt_id AND state='RUNNING'` — matches at most once, so a duplicate fail/cancel cannot produce a duplicate closure row or rewind a closed attempt.

**GeneratedMedia:** rows are keyed `(attempt_id, asset_role)` with a unique constraint, so a duplicate complete cannot register the same asset twice.

**Allocation counts:** GPU availability is derived from `gpu_devices.status`, never from a counter. A double release therefore cannot decrement a counter below zero, because no counter exists.

---

## 15. API Contract Table (FROZEN) — Phase 1

Conventions: JSON; errors per doc 19 error format; pagination `?page&page_size`; all state transitions per section 3.

### Worker endpoints

| Method | Path | Auth | Request | Response | Idempotent | Actor | State transition | DB side effects |
|--------|------|------|---------|----------|------------|-------|------------------|-----------------|
| POST | `/api/v1/workers` | ADMIN | `{name, node_name?}` | `{worker_id, one_time_token}` | No | Admin | — | worker row (token hashed) |
| POST | `/api/v1/workers/register` | one-time token | `{token, version, platform, discovery_snapshot}` | `{worker_id, worker_token, access_credentials, expires_at}` | No | Worker | node registered | worker + node + gpu_devices + capabilities |
| POST | `/api/v1/workers/{id}/heartbeat` | worker creds | `{gpus[], jobs[], claim_ids[], cache_state}` | `{ok, renewed_claim_ids[]}` | Yes | Worker | — (lease renewal) | last_heartbeat_at, metrics, claim expires_at |
| POST | `/api/v1/workers/{id}/jobs/claim` | worker creds | `{?max}` | `{job, claim_id, attempt_id, gpu_id, lease_expires_at}` | Yes (per worker+job) | Worker triggers; Scheduler owns the transaction | RESERVED→STARTING + GPU RESERVED→BUSY (single tx, section 10) | claim + attempt rows + `GPUReservation.worker_id` + job STARTING + GPU BUSY |
| POST | `/api/v1/workers/{id}/claims/{claim_id}/renew` | worker creds | — | `{expires_at}` | Yes | Worker | — | claim expires_at |
| POST | `/api/v1/workers/{id}/credentials` | worker token | — | `{access_credentials, expires_at}` | Yes | Worker | — | credential rotation |
| POST | `/api/v1/jobs/{id}/progress` | worker creds | `{step, total, percent, message}` | `{ok}` | Yes | Worker | — (progress only) | job progress |
| POST | `/api/v1/jobs/{id}/complete` | worker creds | `{outputs[], metadata}` | `{ok}` | Yes | Worker reports; Orchestrator owns job transition; Scheduler owns GPU release | RUNNING→COMPLETED (Tx 1) + GPU BUSY→AVAILABLE (Tx 2) | Tx 1: job terminal + GeneratedMedia + reservation RELEASE_PENDING. Tx 2: GPU release |
| POST | `/api/v1/jobs/{id}/fail` | worker creds | `{error, failure_class}` | `{ok}` | Yes | Worker reports; Orchestrator owns job transition; Scheduler owns GPU release | RUNNING→FAILED/RETRYING (Tx 1) + GPU release (Tx 2) | Tx 1: job/attempt update + reservation RELEASE_PENDING. Tx 2: GPU release |

### Job/project/generation endpoints (Phase 1 required subset)

| Method | Path | Auth | Purpose | State transition |
|--------|------|------|---------|------------------|
| GET/POST | `/api/v1/projects` | session/token | project CRUD | — |
| GET/POST | `/api/v1/projects/{id}/sequences` | session/token | sequence CRUD | — |
| GET/POST | `/api/v1/sequences/{id}/shots` | session/token | shot CRUD | — |
| POST | `/api/v1/sequences/{id}/shots/batch` | session/token | batch shot creation (spec §31) | — |
| GET/POST | `/api/v1/assets` | session/token | asset CRUD (typed views: characters, locations) | — |
| GET/POST | `/api/v1/jobs` | session/token | job list / create | CREATED→QUEUED |
| GET | `/api/v1/jobs/{id}` | session/token | job detail + attempts | — |
| POST | `/api/v1/jobs/{id}/cancel` | session/token | cancel | →CANCELLED (Orchestrator Tx 1; Scheduler Tx 2 releases GPU). From QUEUED with no GPU held: Tx 1 only |
| POST | `/api/v1/jobs/{id}/retry` | session/token | manual retry | FAILED→QUEUED (new attempt) |
| PATCH | `/api/v1/jobs/{id}/priority` | session/token | change priority | — |
| GET | `/api/v1/nodes`, `/api/v1/gpus` | session/token | cluster view | — |
| GET | `/api/v1/logs` | session/token | log center | — |
| GET | `/api/v1/stream/{gpus,jobs,logs,nodes}` | session/token | SSE streams | — |
| POST | `/api/v1/auth/login`, `POST /api/v1/tokens` | — | auth | — |

Model/workflow registry endpoints exist per doc 19 but are **Phase 4-5** (see section 22-23).

---

## 16. Database Contract (FROZEN) — Phase 1 minimum

Full schema in doc 05; entity behavior in doc 21. Phase 1 required vs deferred:

| Entity | Phase 1 | Notes |
|--------|---------|-------|
| User, Role, Permission | **Required** | auth + RBAC skeleton |
| Project, Sequence, Shot | **Required** | core hierarchy |
| Asset, AssetVersion, ReferenceMedia | **Required** | polymorphic, global/project scope |
| GeneratedMedia, GeneratedMediaVersion | **Required** | versions + approval status (spec §46, 88) |
| Job, JobAttempt | **Required** | queue + attempts |
| JobClaim | **Required** | lease/visibility |
| GPUReservation (allocation record) | **Required** | node+GPU+policy+scores per allocation |
| Worker (registration/credentials) | **Required** | worker identity + tokens |
| GPUNode, GPUDevice, GPUCapability | **Required** | discovery + capabilities |
| Model, ModelVersion, ModelInstallation | **Phase 4** | registry; Phase 1 jobs may reference a placeholder model ref |
| Workflow, WorkflowVersion | **Phase 5** | registry; Phase 1 execution is engine-agnostic mock |
| StorageObject | **Required** | media metadata |
| LogEntry, AuditEntry, Metric | **Required** | observability |
| PromptTemplate/StyleTemplate/CameraTemplate/LightingTemplate | **Phase 6+** | prompt system (spec §27-28) |
| Tag (join tables) | **Phase 6+** | tagging (spec §79) — or earlier if assets need it |

**Constraints:** UUID PKs; `created_at`/`updated_at` everywhere; JSONB for open-ended params/metadata with GIN indexes; no media bytes in DB; attempts immutable; claims unique per (job, active).

---

## 17. GPU Backend Contract (FROZEN)

```text
GpuBackend (interface)
  list_devices() -> [GpuDevice]        # uuid, name, vendor, vram_total, compute_capability, driver, pci_bus_id
  get_metrics(device_id) -> GpuMetrics # vram_used, utilization, temperature, power, power_limit, processes
  get_capabilities(device_id) -> [Capability]
  run_probe(device_id, probe) -> ProbeResult
  execute(device_id, job) -> JobResult
```

- **No allocation API.** The backend reports facts and executes; it never allocates.
- Backends: `NvmlGpuBackend` (real, NVML/nvidia-smi), `MockGpuBackend` (simulated). Selection: `GPU_BACKEND=auto|nvml|mock`; `auto` → NVML if present else mock.
- Capability rules are data (registry + probes); **no GPU-name if-statements anywhere** (AGENTS.md).
- Extensible to future backends (AMD, etc.) without core changes.

---

## 18. Mock GPU Contract (FROZEN)

`MockGpuBackend` implements the same interface as NVML and simulates:

- **Discovery:** N GPUs (configurable count, VRAM, compute capability, driver version, UUIDs).
- **Identity:** stable UUIDs per simulated GPU.
- **Metrics:** configurable baselines + drift for utilization, VRAM used, temperature, power.
- **Health:** healthy/degraded/error flags.
- **Capabilities:** derived from simulated hardware facts via the same rule engine as real hardware.
- **Execution timing:** configurable simulated duration per job type.
- **Outcomes:** success, failure (configurable class), OOM-like failure, device-lost, hang — injectable per test.
- **Determinism:** seed support for reproducible CI tests.

The application contains zero hard-coded GPU-specific business logic (`if GTX_1080_Ti:` is forbidden). All scheduler/queue/worker tests run against the mock in CI.

---

## 19. Model Registry Contract (FROZEN)

- **Model** (identity, type, provider, license) → **ModelVersion** (immutable: repo, vram_requirement, recommended_vram, precision, quantization, tasks, engines, multi_gpu_support, dependencies, checksum) → **ModelInstallation** (per-node: path, checksum, status).
- **Registry ≠ cache ≠ GPU inventory.** The registry is the authoritative metadata layer; the cache is a per-node LRU; the scheduler is cache-aware but the registry is the source of truth.
- **Compatibility question the scheduler must answer:** "Can GPU X execute Model Version Y?" — answered from capabilities (compute capability, VRAM, driver) + model version requirements + probes. Never from GPU names.
- Phase 1: registry schema lands in Phase 4; Phase 1 jobs carry a placeholder model reference so the queue/scheduler/worker path is exercised without real models.

---

## 20. Workflow Registry Contract (FROZEN)

- **Workflow** → **WorkflowVersion** (immutable: engine, input schema, optional model reference).
- **EngineAdapter** interface: `validate(job)`, `run(job, progress_callback)`, `cancel()`. Adapters: `ComfyUIAdapter` (Phase 5), `DiffusersAdapter`, future custom engines.
- The core Job/Scheduler/Worker architecture never imports an engine; only adapters do. ComfyUI is an adapter, not the platform.
- Input schema drives the UI (only schema-exposed parameters, spec §26) and adapter validation.
- Phase 1: workflow registry lands in Phase 5; Phase 1 execution is a mock workload through the same engine-adapter seam.

---

## 21. Storage Contract (FROZEN)

```text
StorageBackend (interface)
  put(key, file, metadata) -> StorageObject
  get(key) -> stream
  delete(key)
  exists(key) -> bool
  url(key, expiry) -> str
```

- Backends: `LocalStorage` (Phase 1 default), `S3Storage` (MinIO/S3), `NfsSmbStorage` (later). Layout is a configurable prefix template (doc 11).
- DB stores metadata only (`storage_objects`: backend, bucket, key, size, mime_type, checksum, owner). No media bytes in the DB.
- The domain layer never touches filesystem paths directly; all access via the port. Uploads resumable where supported; downloads via time-limited URLs for previews.

---

## 22. Phase 1 Scope (FROZEN)

### MUST IMPLEMENT
1. Repo bootstrap: pyproject/uv.lock, structure per doc 03, CI skeleton (lint, typecheck, unit tests, mock backend only).
2. Configuration system (env vars, `config.example`, `GPU_BACKEND` selection).
3. PostgreSQL + Alembic; Phase 1 entities (section 16).
4. Auth: users, roles, sessions, API tokens, RBAC skeleton.
5. FastAPI skeleton: health/ready, error format, pagination, OpenAPI.
6. Frontend skeleton: Vite+React+TS, login, dashboard shell, generated API client, i18n/RTL scaffolding.
7. Worker agent: registration/auth, discovery (mock), heartbeat, claim, lease, progress, complete, fail.
8. GPU backend: mock (full contract, section 18) + NVML stub behind the same interface.
9. Job system: queue (Postgres SKIP LOCKED), scheduler (advisory lock, policies, reservation), attempts, retry, cancellation.
10. Projects/Sequences/Shots/Assets CRUD (API + UI) with storage-backed references.
11. Observability: structured logs, metrics, health, SSE streams.
12. **Canonical Phase 1 happy path (must demonstrably work):** Create Project → Create Job → Queue Job → Scheduler sees Job → Mock GPU discovered → GPU reserved → Worker registered → Worker claims Job → Lease established → Worker executes Mock workload → Progress reported → Job completed → GPU released → Generation/Media result recorded.

### SHOULD IMPLEMENT
- Model/workflow registry schemas (land early to avoid rework, but not exercised end-to-end).
- Batch shot creation, asset versioning UI.
- Log center UI, GPU dashboard with realtime metrics.

### DEFERRED (explicitly NOT Phase 1)
- Real NVML backend verification, PyTorch/CUDA stack, ComfyUI integration, real image/video generation.
- Model installation pipeline, model cache, cache-aware scheduling at scale.
- Multi-node deployment, multi-GPU inference, GPU sets.
- Prompt/style template system, tagging, search, LLM prompt assistant.
- Video generation, upscaling, export, backup/restore, alerting, OpenTelemetry.
- Redis/ARQ queue migration (scale path only).

---

## 23. Failure/Recovery Matrix (FROZEN)

| ID | Scenario | Detection | Owner | Recovery | Final state |
|----|----------|-----------|-------|----------|-------------|
| F1 | Worker dies before claim | heartbeat loss × N | Scheduler | Job is RESERVED (GPU already allocated). Scheduler detects node loss → job RESERVED→QUEUED + GPU RESERVED→AVAILABLE (single tx) → re-dispatches to another worker/GPU | Job completes elsewhere |
| F2 | Worker dies after claim (STARTING) | heartbeat loss / lease expiry | Scheduler | Attempt INTERRUPTED (worker_lost); GPU released; job QUEUED; new attempt | Job completes elsewhere |
| F3 | Worker dies during RUNNING | heartbeat loss / lease expiry | Scheduler | Same as F2; progress lost is acceptable (checkpointed progress only) | Job completes elsewhere |
| F4 | GPU fails while RESERVED | worker metrics / probe | Scheduler | GPU UNHEALTHY; job QUEUED; re-select GPU | Job completes elsewhere |
| F5 | GPU fault while RUNNING | worker failure/metrics report **or** scheduler-side probe | Tx 1 **Orchestrator** when worker-reported (job →FAILED/RETRYING, attempt close, reservation →RELEASE_PENDING); single tx **Scheduler** when the scheduler detects the fault itself (GPU →UNHEALTHY + job recovery) | Tx 2 **Scheduler**: GPU →UNHEALTHY + reservation →RELEASED | Job completes elsewhere or FAILED after retries |
| F6 | Scheduler crashes | advisory lock release | Postgres | Another backend process acquires lock within seconds; in-flight reservations recovered (RESERVED jobs past lease → QUEUED) | No split brain; jobs recover |
| F7 | API restarts | — | — | Stateless API; scheduler leader re-elected via lock; workers re-heartbeat | Transparent |
| F8 | Duplicate claim request | claim uniqueness per (worker, job) | Scheduler (executed tx) | Idempotent: returns existing valid claim; no duplicate claim/attempt/lease rows; no duplicate start transition | Single attempt |
| F9 | Duplicate completion request | job already terminal | Orchestrator (Tx 1) | Idempotent: returns current state; the Tx 2 release is a conditional `RELEASE_PENDING→RELEASED` update, so a repeat affects 0 rows | Single terminal transition |
| F10 | Lease expires | `expires_at` passed without renewal | Scheduler | Deterministic path (section 10): attempt INTERRUPTED, GPU released, job QUEUED, new attempt — all in one scheduler-owned transaction | Job completes elsewhere |
| F11 | Retry limit reached | `retry_count >= max` | Orchestrator | Job FAILED with full attempt history; no further retries | FAILED (terminal) |
| F12 | Job cancelled while RUNNING | user request | Tx 1 **Orchestrator**; Tx 2 **Scheduler** | Worker aborts at checkpoint (cooperative); attempt CANCELLED; reservation →RELEASE_PENDING; GPU released by Scheduler in Tx 2; NOT auto-retried | CANCELLED (terminal) |
| F13 | Job cancelled while RESERVED (before start) | user request | Tx 1 **Orchestrator**; Tx 2 **Scheduler** | No worker involved (no claim exists). Tx 1: job RESERVED→CANCELLED + reservation RESERVED→RELEASE_PENDING. Tx 2: GPU RESERVED→AVAILABLE + reservation →RELEASED | CANCELLED (terminal) |
| F14 | Duplicate cancel request | job already CANCELLED | Orchestrator (Tx 1) | Idempotent: returns current state, no second `RELEASE_PENDING` write, no second release attempt | Single terminal transition |
| F15 | Duplicate release / reconciliation sweep runs twice | reservation already RELEASED | Scheduler | Conditional `RELEASE_PENDING→RELEASED` matches 0 rows; treated as success. No double GPU release | Released exactly once |

---

## 24. Open Risks

1. **ADR-0002 provisional:** the Postgres-backed queue choice is criteria-selected but awaits owner confirmation.
2. **All ADRs Proposed:** none are accepted; this contract freezes the *documentation-aligned* architecture, not an accepted one.
3. **PyTorch/CUDA/Pascal:** CANDIDATE / DOCUMENTATION-ALIGNED / **PENDING REAL PASCAL VALIDATION**. ADR-0006 remains Proposed. No hardware validation has occurred; no document may claim otherwise.
4. **Target server unknowns** (hardware.md §B): driver, CUDA, OS, disk, network, ComfyUI presence — all open questions in hardware.md §D.
5. **Model feasibility estimates** (doc 09) remain unconfirmed estimates pending real-GPU spikes.
6. **Historical snapshot** `ai-media-platform-report.txt` contains pre-audit quoted content; left intact as a historical record.

---

## 25. Documentation Changes Made During This Review

**Round 1 — ownership/transaction consistency review:**

- `docs/design/08-scheduler-architecture.md` — added the full GPU lifecycle state set (DRAINING, MAINTENANCE) and the ERROR/UNHEALTHY reconciliation required by spec §7/§51.
- `docs/design/13-observability-architecture.md` — aligned GPU state enumeration with spec §7/§51.
- `docs/implementation-contract-2026-10-05.md` — this document (new).

**Round 2 — cancellation and GPU ownership boundary micro-fix:**

- `docs/implementation-contract-2026-10-05.md`
  - §5 `RESERVED→AVAILABLE` row corrected: cancellation before start is now explicitly two-transaction (was "same tx as job →CANCELLED", which contradicted the matrix).
  - §5.1 **GPU Ownership Boundary** added (new): the two state domains (`gpu_devices.status` = Scheduler-only; `gpu_reservations.state` = Orchestrator may request, Scheduler completes).
  - §3.1 rows COMPLETED / FAILED / CANCELLED now state the Tx 1 / Tx 2 split and name the actor per transition; the CANCELLED row notes QUEUED cancellation has no GPU and needs Tx 1 only.
  - §3.2 rewritten: removed the false implication that the Scheduler owns all job transitions; Scheduler's job-side writes are now scoped to recovery paths it detects itself.
  - §10 the claim request is now explicitly the **start trigger** (no `/start` endpoint), executed by Scheduler logic in one transaction; the worker is the trigger, not the owner.
  - §13 cancellation split into the two variants (while RESERVED / while RUNNING) with explicit Tx 1 and Tx 2 contents.
  - §14.1 the "Only the Scheduler transitions it" claim about `GPUReservation` corrected to the request/execute split.
  - §14.2 cancellation split into "before start" and "while running" rows; claim row merged with start.
  - §14.3 the incorrect sentence *"The Scheduler never executes a job terminal transition without also marking the allocation RELEASE_PENDING in the same transaction"* was **removed** and replaced with the ownership invariant plus the explicit scheduler-recovery carve-out.
  - §14.5 rewritten as the final consistency matrix using the required Tx 1 / Tx 2 / physical-GPU-mutation columns, with no ambiguous owner in any row.
  - §14.6 rewritten: duplicate same-outcome vs conflicting-outcome behaviour separated, `/claims/` renew path corrected to the worker-scoped canonical route, scheduler release/reconciliation added as a row, and the exactly-once release expressed as the two conditional UPDATEs that must both match. Attempt-closure and `GeneratedMedia` uniqueness, and the "no allocation counter" argument, added.
  - §7 the scheduling claim is now distinguished from the worker claim.
  - §15 claim and cancel API rows state their Tx 1 / Tx 2 side effects and owners.
- `docs/design/05-database-design.md` — added the two-state-domain ownership table and the exactly-once release note; added a partial-unique index note preventing two live reservations per GPU.
- `docs/design/07-job-lifecycle.md` — completion sequence diagram now shows Tx 1 (Orchestrator) and Tx 2 (Scheduler); failure paths split into scheduler-owned single-tx recovery vs worker-reported two-tx failure.
- `docs/design/08-scheduler-architecture.md` — added the GPU ownership boundary table; activate/release steps rewritten to name the trigger and both transactions.
- `docs/design/18-worker-protocol.md` — claim documented as the start trigger; result upload, failure and cancellation sections now state the Orchestrator-requests / Scheduler-releases split; lease-expiry recovery marked as one scheduler-owned transaction.
- `docs/decisions/ADR-0002-job-queue.md` — clarified that "same transaction" refers to job/attempt/reservation records, not the physical GPU row.

**Round 3 — cross-document consistency gate:**

- `docs/decisions/ADR-0002-job-queue.md` — **primary blocker fixed.** The line "the queue owns job ordering, claiming, and status" contradicted the frozen Job State Machine. Replaced with the accurate boundary: the Postgres queue owns ordering, visibility (`visible_at`) and atomic *scheduling* claims as persistence mechanics only, and does **not** own `Job.state` (API/domain owns `CREATED→QUEUED`; Scheduler owns `QUEUED→RESERVED`, `RESERVED→STARTING` and scheduler-detected recovery; Orchestrator owns completion, failure classification, retry, cancellation execution and media registration; the Worker never writes `Job.state`). The two claim operations are now named explicitly. Two smaller edits for consistency: Option A's description no longer says "workers claim jobs with SKIP LOCKED" (it is the scheduling claim), and the "same transaction as the status update" bullet is scoped to job/attempt/reservation records.
- `docs/decisions/ADR-0006-pytorch-cuda-pascal.md` — added an explicit classification line to the Decision: candidate / documentation-aligned / **pending real Pascal validation**, with nothing HARDWARE-VALIDATED. Status remains `Proposed`.
- `docs/implementation-contract-2026-10-05.md`
  - **New gap closed:** `STARTING → RUNNING` had no documented owner anywhere. Now defined as Orchestrator-owned (trigger: the worker's first progress report; job row only, GPU stays `BUSY`, no Scheduler involvement) in §3.1, §3.2, §14.4 and §14.5.
  - §8 and §11: unversioned `/api/jobs/{id}/progress|fail` normalized to the canonical `/api/v1/...`.
  - §25/§26 updated with this round's changes.
- `docs/design/01-system-architecture.md` — spec-coverage row for the Queue no longer reads as a `Job.state` ownership claim.
- `docs/design/04-service-architecture.md` — "Scheduler claims" and "scheduler loop (claim → …)" renamed to **scheduling claim**, and the distinction from the **worker claim** stated.
- `docs/design/07-job-lifecycle.md` — the state table's ambiguous "Entered by" column split into **Trigger** and **Owner (writes `Job.state`)**, so the worker is no longer shown as the owner of STARTING/RUNNING/COMPLETED.
- `docs/design/18-worker-protocol.md` — unversioned `/api/workers/{id}/heartbeat` and `/api/jobs/{id}/progress|complete|fail` normalized to `/api/v1/...`.
- `docs/audit-report-2026-10-05.md` — unversioned progress/complete/fail paths normalized to `/api/v1/...`.

Verified consistent and needing no change: ADR-0007 (GpuBackend exposes no allocation API), ADR-0003 (SSE selected, WebSockets deferred), ADR-0001, ADR-0004, ADR-0005, ADR-0008, docs 02/06/10/11/12/13/14/15/16/17/19/20/21, and `docs/spec.md` (source of truth, untouched).

No other documentation required changes; the audit report (`docs/audit-report-2026-10-05.md`) already resolved the six prior contradictions.

---

## 26. Final Readiness Verdict

- Job state machine: **deterministic** (section 3)
- Job Attempt model: **deterministic** (section 4)
- GPU state machine: **deterministic** (section 5)
- Scheduler ownership: **deterministic** (section 6)
- Queue semantics: **deterministic** (section 7)
- Worker protocol: **deterministic** (section 8)
- Authentication: **deterministic** (section 9)
- Claim/lease semantics: **deterministic** (section 10)
- Heartbeat semantics: **deterministic** (section 11)
- Retry semantics: **deterministic** (section 12)
- Cancellation semantics: **deterministic** (section 13)
- Transaction boundaries: **deterministic** (section 14)
- Phase 1 API contract: **deterministic** (section 15)
- Phase 1 database model: **deterministic** (section 16)
- Mock GPU contract: **deterministic** (section 18)
- No unresolved architecture contradiction exists.
- PyTorch/CUDA/Pascal correctly classified as **PENDING REAL PASCAL VALIDATION**; ADR-0006 remains **Proposed**.
- No critical blocker remains. Open risks (section 24) are procedural/environmental, not architectural ambiguities.

**READY FOR PHASE 1 IMPLEMENTATION**
