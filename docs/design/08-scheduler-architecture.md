# 08 — Scheduler Architecture

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md)

## Responsibilities (spec sections 11-12)

Select the best (node, GPU) for each queued job, allocate it atomically, and release it on completion. The scheduler is a domain service invoked by the Orchestrator; it owns no I/O — it consumes `GpuBackend` data and the queue, and emits allocation decisions.

## Inputs (spec section 12)

Required VRAM, model requirements, workflow requirements, GPU capabilities, node availability, current GPU load, model cache availability, user-selected GPU/node, job priority, scheduling policy.

## Allocation ownership (single owner: the scheduler)

**The scheduler is the sole owner of GPU allocation.** The queue orders and claims jobs; the scheduler decides and reserves GPUs; the worker only executes jobs already reserved for its GPUs (see `18-worker-protocol.md`). No other component may transition a GPU's lifecycle state.

## GPU lifecycle states (spec sections 7, 51)

The full canonical GPU state set (spec section 7 lifecycle + section 51 health):

```text
AVAILABLE → RESERVED → BUSY → AVAILABLE
AVAILABLE → MAINTENANCE → AVAILABLE
BUSY/RESERVED → DRAINING → AVAILABLE        (graceful: wait for running job)
BUSY/RESERVED → UNHEALTHY → AVAILABLE        (after admin clears error)
Any → OFFLINE                               (heartbeat lost / admin)
```

- `ERROR` (spec section 51 health enumeration) is the health status reported while a GPU is in `UNHEALTHY` lifecycle state — the lifecycle state is `UNHEALTHY`; `ERROR` is its health flag. The two spec enumerations are reconciled this way.
- `DRAINING`: no new jobs are placed; the GPU finishes its current job, then returns to `AVAILABLE` (or `MAINTENANCE` if the admin chose maintenance).
- `MAINTENANCE`: admin-set; no jobs placed; returns to `AVAILABLE` when cleared.
- Only the scheduler (or an admin action processed by the scheduler) transitions GPU device states. Workers report facts; they never transition GPU state.

## GPU ownership boundary (two state domains)

| Domain | Meaning | Who may write it |
|--------|---------|------------------|
| `gpu_devices.status` | **Physical** GPU lifecycle (`AVAILABLE, RESERVED, BUSY, DRAINING, UNHEALTHY, OFFLINE, MAINTENANCE`) | **Scheduler only.** Nobody else, ever. |
| `gpu_reservations.state` | **Logical** allocation/release state tied to a job (`RESERVED, BUSY, RELEASE_PENDING, RELEASED`) | Orchestrator may *request* release (`RESERVED/BUSY → RELEASE_PENDING`). Scheduler *completes* it (`RELEASE_PENDING → RELEASED`) together with the physical release. |

The Orchestrator never writes `gpu_devices.status`. Because it owns the job's terminal semantics, it records only the release *request* in the same transaction as the job transition; the scheduler executes the physical mutation. This is what keeps the writer count for `gpu_devices.status` at exactly one while still allowing single-transaction scheduler recovery paths (see `implementation-contract-2026-10-05.md` sections 5.1 and 14).

## Allocation algorithm

1. **Filter** — candidate GPUs must: be `AVAILABLE` (spec section 7 lifecycle), on a `HEALTHY` node, satisfy the job's capability requirements, have sufficient free VRAM (model requirement + safety margin), and match any user pinning (GPU/node/GPU set).
2. **Score** — apply the active policy (below) to rank survivors.
3. **Reserve** — atomically, in one DB transaction (Phase 1-3): GPU `AVAILABLE → RESERVED` and job `QUEUED → RESERVED`, recording the allocation (node, GPU, policy, scores). Double allocation is impossible by construction.
4. **Dispatch** — the job becomes claimable by the worker on the reserved node; the claim carries the reserved GPU id.
5. **Activate** — the worker's claim request is the trigger (there is no separate `/start` endpoint). Scheduler-executed logic does it in one transaction: create claim + attempt + lease, set `GPUReservation.worker_id`, job `RESERVED → STARTING`, GPU `RESERVED → BUSY`, `GPUReservation RESERVED → BUSY`. The worker is the trigger, not the owner.
6. **Release** — two steps, because job terminal semantics and physical GPU state have different owners:
   - Tx 1 (**Orchestrator**, for completion/failure/cancellation): job → terminal state, attempt close, `GPUReservation → RELEASE_PENDING`.
   - Tx 2 (**Scheduler**): GPU `BUSY → AVAILABLE` (or `UNHEALTHY` if the GPU reported errors) or `RESERVED → AVAILABLE` if the job never started, plus `GPUReservation RELEASE_PENDING → RELEASED`.
   - If the job never starts because the worker was lost or the lease expired, no Orchestrator transaction is involved: the scheduler does job recovery **and** the physical GPU release in one transaction, because it owns both sides.
   - Tx 2 is driven either by a synchronous notification from the Orchestrator or by the scheduler's reconciliation sweep of `RELEASE_PENDING` reservations, which is the crash-safety backstop. Exactly-once is guaranteed by the conditional `RELEASE_PENDING → RELEASED` update.

## Policies (spec section 12)

| Policy | Behavior |
|--------|----------|
| AUTO | Best overall score (capability fit + load + cache) |
| FASTEST | Prefer the GPU with the highest expected throughput for the model (from probe/benchmark data) |
| LOWEST_LOAD | Prefer the lowest current utilization |
| LOWEST_RESOURCE_COST | Prefer the cheapest resource (configurable cost model) |
| CAPABILITY_MATCH | Strict capability equality; fail if unavailable |
| MODEL_CACHE_AWARE | Prefer the node that already has the model cached (spec section 39), unless a higher-priority rule says otherwise |
| ROUND_ROBIN | Cycle across compatible GPUs |
| MANUAL | User-pinned GPU/node/GPU set only |

Policies are pluggable: a policy is a scoring function over the candidate set. New policies register without modifying the scheduler core (spec section 12 extensibility).

## Priority (spec section 54)

`LOW < NORMAL < HIGH < URGENT`. The queue claims highest-priority-oldest first; preemption is **not** performed (a running job is never evicted) — priority affects ordering only.

## Multi-GPU jobs (spec section 8)

Only when the model version + engine adapter declare multi-GPU support: the scheduler allocates the requested GPU set atomically (all-or-nothing) and validates the set against capabilities.

## Model-cache awareness (spec section 39)

`model_installations` records which node holds which model version. The scheduler prefers cache-hitting nodes when the policy allows, reducing load time and network transfer.

## Failure-driven re-scheduling (spec section 14)

On retryable failure, the failed GPU is excluded (or down-weighted) and the scheduler re-runs selection — e.g. OOM on a 1080 Ti → a 4090 if one exists in the cluster.

## Observability (spec section 90)

Every allocation decision is logged with its inputs and scores ("why is this job running there?"), stored as job metadata and surfaced in the UI.
