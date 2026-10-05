# 14 — MVP Roadmap

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md), [spec §99](../spec.md)

## MVP scope (spec section 99)

GPU Discovery · Node Discovery · GPU Dashboard · Projects · Characters · Locations · Assets · Shots · Job Queue · Scheduler · Worker · Model Registry · Workflow Registry · ComfyUI Integration · Image Generation · Logs · Storage · Authentication.

Video generation follows after the MVP foundation is stable (spec section 99).

## Acceptance criteria per MVP item

Each criterion is testable in CI against the **mock GPU backend** (ADR-0007) unless marked otherwise. Items marked "real-GPU" are verified per `docs/verification-checklist.md`.

### 1. GPU Discovery
- AC1: On startup, the worker discovers all GPUs via the configured `GpuBackend` and reports UUID, name, vendor, VRAM total/available, driver, compute capability, PCI bus ID (spec section 5).
- AC2: With `GPU_BACKEND=mock` and N configured GPUs, exactly N GPUs appear with the configured properties.
- AC3: No GPU name/count is hard-coded (code review + config-driven test).
- AC4 (real-GPU): discovery matches `nvidia-smi` on the target server (V-004).

### 2. Node Discovery
- AC1: A worker registers itself on connect (spec section 71) and appears as a node with status `HEALTHY`.
- AC2: A second worker registers as a second node; both are listed independently.
- AC3: Node list and status are available via API and UI.

### 3. GPU Dashboard
- AC1: Dashboard shows per-GPU: name, UUID, node, VRAM total/used/free, utilization, memory utilization, temperature, power, current job, current model, health, status (spec section 19).
- AC2: Values update in real time without page refresh (SSE).
- AC3: With the mock backend, all fields render from simulated metrics.

### 4. Projects
- AC1: CRUD projects via API and UI; projects are independent (spec section 20).
- AC2: Project list/detail/generation history visible.

### 5. Characters
- AC1: CRUD characters with the spec section 21 fields; reusable across projects (global vs project-specific, spec section 24).
- AC2: Reference images attach via the storage abstraction (no bytes in DB).

### 6. Locations
- AC1: CRUD locations with spec section 22 fields; reusable across projects.

### 7. Assets
- AC1: Arbitrary asset types (spec section 23) with versioning, tagging, metadata, project association.
- AC2: Asset versions list and compare (spec section 86 preview).

### 8. Shots
- AC1: CRUD shots with spec section 30 fields, linked to project/sequence.
- AC2: Batch shot creation (spec section 31) creates N shots.

### 9. Job Queue
- AC1: Jobs can be enqueued with priority; claimed in priority order; status transitions are persisted (spec section 13).
- AC2: Delayed jobs become claimable only after `visible_at`; cancellation is cooperative and stops the worker at the next checkpoint.
- AC3: Queue tests run in CI against the Phase 1-3 queue implementation (ADR-0002) — no external services.

### 10. Scheduler
- AC1: Given a job with VRAM/capability requirements and a set of GPUs (mock), the scheduler selects a compatible GPU and never double-allocates (concurrent claim test).
- AC2: All spec section 12 policies are selectable and produce the expected ordering in tests.
- AC3: Priority ordering: URGENT jobs claim before LOW.
- AC4: Allocation decisions are logged with reasons (spec section 90).

### 11. Worker
- AC1: Worker executes a job end-to-end against the mock backend: claim a job already reserved for one of its GPUs → re-validate → execute → progress → result. The worker never allocates GPUs (scheduler-owned, see `08-scheduler-architecture.md`).
- AC2: Worker crash mid-job → heartbeat loss → node OFFLINE → retryable job requeued (spec section 52).
- AC3: OOM injection (mock) produces a classified OOM failure and bounded retries (spec section 14).
- AC4: Claim lease expiry (mock: stop heartbeats) returns the job to QUEUED and releases the GPU reservation — in one scheduler-owned transaction, since the scheduler owns both the job and the physical GPU side of that recovery.

### 12. Model Registry
- AC1: Register a model with full spec section 36 metadata; versions are immutable.
- AC2: Installation validation rejects a model whose VRAM requirement exceeds every discovered GPU.
- AC3: Install/enable/disable/remove/rollback flows work via API.

### 13. Workflow Registry
- AC1: Register a workflow with an input schema; the generator form exposes only schema-defined parameters (spec section 26).
- AC2: Workflow versions are immutable and jobs pin a version.

### 14. ComfyUI Integration
- AC1: A ComfyUI adapter executes a registered workflow on a GPU and returns outputs through the storage port.
- AC2 (real-GPU): ComfyUI runs a basic image workflow on one 1080 Ti (V-006).
- AC3: The platform functions without ComfyUI for non-generation operations (engine abstraction, spec section 42).

### 15. Image Generation
- AC1: Text-to-image job: prompt + model + workflow + seed + resolution → job → output image stored via storage port, metadata preserved (spec sections 46-47).
- AC2: Batch generation (1/5/20/100 shots) creates the right number of jobs (spec section 32).
- AC3: Generation history per shot with versions and accept/reject states (spec sections 87-88).
- AC4 (real-GPU): at least one image model fits and runs within 11 GB (V-007).

### 16. Logs
- AC1: Structured logs with all spec section 48 fields are queryable via API with filters (level/service/node/GPU/job/project/time).
- AC2: Real-time log stream via SSE.

### 17. Storage
- AC1: Media upload/download/delete through the storage port with local backend; DB stores metadata only.
- AC2: Path traversal and arbitrary path inputs are rejected (security test).

### 18. Authentication
- AC1: Users can log in (session) and use API tokens; unauthorized requests are rejected.
- AC2: RBAC: the four spec section 59 roles enforce their permissions (negative tests for each role).

## Roadmap order (within MVP)

1. Foundation: repo, config, DB, auth, API skeleton (Phase 1)
2. GPU infrastructure: worker, discovery, mock backend, heartbeat (Phase 2)
3. Job system: queue, scheduler, execution, retry, logs (Phase 3)
4. Model system: registry, install, cache (Phase 4)
5. Workflow system + ComfyUI adapter (Phase 5)
6. Asset system: projects/characters/locations/assets/shots (Phase 6)
7. Image generation end-to-end (Phase 7)

Each phase ends with tests + docs + review (AGENTS.md workflow rules).
