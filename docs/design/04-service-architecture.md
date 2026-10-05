# 04 — Service Architecture

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md)

## Logical services (spec section 67)

The platform starts as a **modular monolith**: one deployable with strict internal boundaries. Services become independent deployables only where operationally justified (first candidate: the Worker Agent in multi-node Phase 9, which is already a separate process by design).

```text
            ┌─────────────────────────────────────────────┐
            │                  API (FastAPI)               │
            │  REST (spec §56) · OpenAPI (§57) · SSE (§18) │
            └──────────────────────┬──────────────────────┘
                                   │
            ┌──────────────────────┴──────────────────────┐
            │                Domain Layer                  │
            │  Orchestrator · Scheduler · Model Mgr ·      │
            │  Workflow Mgr · Project/Asset/Shot domain    │
            └───────┬──────────────┬──────────────┬───────┘
                    │              │              │
        ┌───────────┴───┐  ┌───────┴──────┐  ┌────┴──────────┐
        │ Persistence   │  │ Job Queue    │  │ Storage       │
        │ PostgreSQL    │  │ (abstract)   │  │ Abstraction   │
        │ (ADR-0008)    │  │ (ADR-0002)   │  │ (spec §43-45) │
        └───────────────┘  └──────────────┘  └───────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │      Worker Agent (spec §10) │
                    │  GpuBackend: NVML | Mock      │
                    │  Engine adapters (spec §42)   │
                    └──────────────────────────────┘
```

## Ports and adapters

| Port (interface) | Adapters |
|------------------|----------|
| `JobQueue` | `PostgresQueue` (Phase 1-3, provisional), `ArqQueue` (scale path) |
| `StorageBackend` | `LocalStorage`, `S3Storage` (MinIO/NFS/SMB later) |
| `GpuBackend` | `NvmlGpuBackend`, `MockGpuBackend` (ADR-0007) |
| `EngineAdapter` | `ComfyUIAdapter`, `DiffusersAdapter`, future engines (spec section 42) |
| `ModelSource` | `HuggingFaceSource`, `GitSource`, `HttpSource`, `InternalMirror` (spec section 74) |
| `LlmProvider` | abstract; future prompt assistant (spec section 29) |

## Request/response flows

- **User action (e.g. generate image):** API → domain → `JobQueue.enqueue` → **scheduling claim** by the Scheduler → GPU reservation → **worker claim** by the worker on that node → Worker executes → progress via SSE → result via Storage → job completed.
- **Worker event (e.g. heartbeat):** Worker Agent → API/Orchestrator → domain → persistence → SSE fan-out to dashboards.
- **Failure:** Worker/Orchestrator → retry policy (spec section 14) → re-enqueue or mark failed → SSE notification.

## Process model (Phase 1-3, single server)

- `backend` process: API + Orchestrator + Scheduler (async, one uvicorn process).
- `worker` process: Worker Agent (can be co-located; talks to the same DB and, in multi-node, to the control plane over the worker protocol).
- `frontend`: static build served by the backend or a lightweight web server.
- Postgres: local. Object storage: local disk or MinIO.

## Splitting criteria (spec section 67)

A module may become its own deployable only when: (a) it has a different scaling profile, (b) it has different availability requirements, or (c) it needs independent release cadence. The Worker Agent already meets (a) in multi-node deployments.

## Scheduler singleton guarantee

The scheduler loop (**scheduling claim → allocate → reserve → dispatch → start → release**) must run in **exactly one process** at a time, even if multiple backend processes start (multiple uvicorn workers, a restarted process overlapping an old one, or a future horizontal scale-out of the API tier). Two schedulers would double-allocate GPUs. Note this loop is distinct from the **worker claim** (`POST /api/v1/workers/{id}/jobs/claim`), which is a worker's request for an already-reserved job.

**Mechanism: Postgres advisory lock.**

- At startup, each backend process attempts `pg_try_advisory_lock(<constant key>)` (session-level, key namespaced per environment to avoid collisions across deployments sharing a database).
- The process that acquires the lock runs the scheduler loop; others run API-only and skip scheduling.
- The lock is held for the process lifetime; if the leader crashes, Postgres releases it automatically and another process acquires it within seconds — no split brain, no external coordination service.
- The leader re-checks lock ownership before each allocation transaction as a defense-in-depth measure (the DB transaction in `08-scheduler-architecture.md` remains the actual double-allocation guard).

**Alternative (rejected for now):** a separate leader-election service (e.g. etcd/Consul) — adds operational complexity without benefit while Postgres is already the system of record. Revisit if the API tier ever scales beyond one host.

**Consequence:** API-only processes are safe to scale horizontally; scheduling capacity is intentionally single-node until a future ADR revisits this.
