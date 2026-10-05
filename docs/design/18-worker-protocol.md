# 18 — Worker Protocol

Status: Proposed · Date: 2026-10-05 · Parent: [spec §106 item 08](../spec.md), [spec §10](../spec.md), [spec §71](../spec.md), [spec §72](../spec.md)

## Scope

How workers register, authenticate, heartbeat, receive jobs, report progress, upload results, and how lost heartbeats are handled. The protocol is transport-agnostic; the Phase 1-3 implementation uses REST + SSE between the worker and the control plane (single server), and the same protocol works across nodes in Phase 9.

## Worker lifecycle (spec sections 10, 71)

```text
Install → Connect → Authenticate → Discover Hardware → Discover GPUs
        → Determine Capabilities → Register → Heartbeat → READY
```

## 1. Connection, authentication, and credential lifecycle

- The worker reads control-plane URL + credentials from config/env (no hard-coded addresses or secrets, spec section 96).

### First worker token issuance (bootstrap)

1. An **ADMIN** authenticates to the control plane and creates a worker via `POST /api/v1/workers` (name, node name, optional node pinning).
2. The control plane generates a **one-time registration token** and returns it to the admin (shown once; stored hashed).
3. The admin installs the worker with that token (env/config).
4. The worker calls `POST /api/v1/workers/register` with the one-time token → the control plane consumes it (single use) and issues the worker's standing credentials.

This keeps worker creation an audited admin action (spec section 61) while the one-time token survives insecure delivery channels.

### Credential renewal

- Registration returns: a **long-lived worker token** (stored hashed, revocable by an admin) and **short-lived access credentials** (default 24 h, or mTLS client cert where supported).
- All API calls authenticate with the access credentials.
- Before expiry, the worker re-authenticates with the long-lived token via `POST /api/v1/workers/{id}/credentials` → new short-lived access credentials. Renewal is transparent to running jobs.
- Admins can revoke a worker token or rotate credentials at any time; revoked workers fail auth on their next call and must be re-registered.

## 2. Registration payload

Worker ID, node name, hostname, version, platform, and the full discovery snapshot (see `17-gpu-discovery-protocol.md`): GPUs with UUID/name/vendor/VRAM/compute capability/driver/PCI, plus derived capabilities.

## 3. Heartbeat (spec section 72)

- Worker sends `POST /api/workers/{id}/heartbeat` at a configurable interval (default 10 s).
- Payload: node status, per-GPU metrics snapshot, current job per GPU, model cache state, health flags.
- Control plane updates `gpu_nodes.last_heartbeat_at` and GPU metrics; fans out changes over SSE.

### Lost heartbeat (spec sections 52, 72)

```text
heartbeat missed × N (configurable, default 3)
  → node OFFLINE
  → running jobs on that node evaluated
  → retryable jobs requeued (new attempt, new GPU selection)
  → non-retryable jobs FAILED with "worker lost"
  → GPUs marked OFFLINE
```

## 4. Job delivery — pull model with leases

**Decision: workers pull jobs; the control plane never pushes execution commands.**

- The scheduler reserves a GPU for the job (see `08-scheduler-architecture.md` — the scheduler is the sole allocation owner) and the job becomes claimable by a worker on the reserved node.
- The worker claims via **`POST /api/v1/workers/{id}/jobs/claim`** (POST, not GET — claiming has side effects: it creates a claim/lease record and starts the lease timer). Long-poll or short interval.
- The claim response carries the job **and the reserved GPU id**. The worker never chooses a GPU; it re-validates capabilities/VRAM against the reserved GPU before starting (safety net, not the allocation mechanism).
- **The claim request is the start trigger.** There is no separate `/start` endpoint: scheduler-executed logic handles the claim in one transaction — create claim + attempt + lease, bind `GPUReservation.worker_id`, job `RESERVED → STARTING`, GPU `RESERVED → BUSY`. The worker triggers it; it never writes job or GPU state itself.
- Pull was chosen over push because: NAT/firewall-friendly (workers may sit behind routers), naturally balanced (workers claim when ready), simpler recovery (a crashed worker's jobs are simply re-allocated after heartbeat loss), and it keeps the control plane stateless w.r.t. connections.

### Claim lease (visibility timeout)

- Every claim creates a **lease**: `claim_id`, `job_id`, `worker_id`, `expires_at` (default 60 s, configurable).
- While a lease is valid, the job is invisible to other workers' claims (visibility timeout pattern).
- **Lease renewal:** the worker's heartbeat (section 3) includes its active `claim_id`s; each heartbeat renews those leases (extending `expires_at`). A dedicated `POST /api/v1/workers/{id}/claims/{claim_id}/renew` exists for renewal between heartbeats.
- **Lease expiry:** if `expires_at` passes without renewal (worker crashed, hung, or partitioned):
  1. The claim is released; the job returns to `QUEUED` (visible immediately, or after a short delay if it had already started).
  2. A new `job_attempts` row records the lost attempt with error `worker_lost` (retryable).
  3. The GPU reservation is released (`RESERVED → AVAILABLE`, or `BUSY → AVAILABLE`/`UNHEALTHY` if the job had started) and the scheduler re-selects a GPU per the retry policy (spec section 14). This whole recovery step is **one scheduler-owned transaction** — job side and physical GPU side together, because the scheduler owns both.
  4. If the "worker" was actually alive but slow (e.g. model load exceeded the lease), the next claim creates a fresh attempt — the old attempt's partial progress is discarded, keeping execution idempotent-at-checkpoint.

## 5. Progress reporting

- Worker streams progress via `POST /api/v1/jobs/{id}/progress` (step/total, percent, message) at a throttled cadence.
- The Orchestrator persists progress and fans it out over SSE to the UI (spec section 18).
- Progress is also fetchable via REST after reconnects (no data loss on UI reconnect).

## 6. Result upload

- Worker writes outputs through the **storage port** (never to the DB): `put()` → storage key.
- Worker calls `POST /api/v1/jobs/{id}/complete` with output asset metadata (storage keys, mime types, sizes, checksums) + reproducibility metadata (spec section 47: model/workflow versions, prompt, seed, parameters, engine version, runtime info, GPU, node, duration).
- The Orchestrator registers `GeneratedMedia` rows, links them to the shot/project, and records the GPU reservation `→ RELEASE_PENDING`. The **Scheduler** then releases the GPU (`BUSY → AVAILABLE`, or `RESERVED → AVAILABLE` if the job never started) plus `RELEASE_PENDING → RELEASED` in a separate transaction. The Orchestrator never writes `gpu_devices.status` (see `08-scheduler-architecture.md` ownership boundary and `implementation-contract-2026-10-05.md` sections 5.1 and 14).

## 7. Failure reporting

- Worker reports `POST /api/v1/jobs/{id}/fail` with a classified error (see `07-job-lifecycle.md`): `oom`, `cuda_error`, `engine_error`, `validation_error`, `storage_error`, `worker_crash`.
- The Orchestrator applies the retry policy (spec section 14): retryable → requeue with backoff and re-selection; fatal → FAILED.

## 8. Cancellation

- The user requests cancellation; the **Orchestrator** executes the job transition (`→ CANCELLED`) transactionally and records the reservation `→ RELEASE_PENDING`; the worker checks the flag between steps/batches and aborts at the next checkpoint. The **Scheduler** performs the GPU release (`BUSY/RESERVED → AVAILABLE`) in Tx 2 — never the worker, never the Orchestrator.

## 9. Log and metric forwarding

- Workers ship structured logs (spec section 48) and node metrics (spec section 50) to the control plane (append endpoints or stdout shipping in single-server mode).

## 10. Multi-node (Phase 9)

- Unchanged protocol: each worker agent runs on its node, connects to the control plane, registers, heartbeats, pulls. Adding a node requires no core changes (spec section 70).

## Real-GPU verification

- V-009: two parallel jobs run on two different GPUs without interference.
- V-010: killing the worker marks the node OFFLINE and requeues retryable jobs.
