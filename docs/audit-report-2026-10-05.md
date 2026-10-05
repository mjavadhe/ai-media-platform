# Architecture Consistency Audit

Date: 2026-10-05 · Scope: documentation-only · Repository: /home/user01/ai-media-platform

## 1. Executive Summary

Overall status: **PASS WITH WARNINGS**

The documentation set is now internally consistent after six contradictions were found and resolved. All warnings are procedural (Proposed ADRs awaiting owner acceptance, provisional queue choice, unverified real-GPU items) or environmental (unknown target server facts). No architectural contradictions remain.

## 2. Repository State

- Files inspected: 34 (AGENTS.md, spec.md, hardware.md, verification-checklist.md, 8 ADRs, 21 design docs, ADR template, .gitignore, 1 historical snapshot)
- Files modified: 6 (see section 16)
- Source files created: none
- Packages installed: none
- Services started: none
- Git commits: none
- Git pushes: none

## 3. Canonical Architecture

- **Backend:** FastAPI, Python 3.12 (uv-pinned), PostgreSQL (ADR-0001, ADR-0005, ADR-0008)
- **Frontend:** React + Vite + TypeScript (ADR-0004)
- **Realtime:** SSE for server-push; REST for commands; WebSockets explicitly deferred (ADR-0003)
- **Queue (Phase 1-3):** PostgreSQL-backed, `SELECT ... FOR UPDATE SKIP LOCKED`, behind a `JobQueue` abstraction; Redis/ARQ is the documented scale path only (ADR-0002)
- **GPU backend:** pluggable `auto|nvml|mock`; NVML real, in-process mock for dev/CI (ADR-0007)
- **ML runtime:** PyTorch 2.7.x+cu126, fp16 only, Python 3.12; cu118 driver fallback (ADR-0006)
- **GPU allocation:** scheduler is the sole owner; workers execute pre-reserved jobs only
- **Scheduler leadership:** singleton via Postgres advisory lock
- **Worker protocol:** pull/claim (POST), 60 s claim lease, heartbeat renewal, lease expiry → job requeued + GPU released
- **Job lifecycle:** CREATED → QUEUED → RESERVED → STARTING → RUNNING → COMPLETED (with PAUSED/RETRYING/FAILED/CANCELLED branches); attempts are distinct rows; retries bounded (max 3)
- **Storage:** pluggable local/NFS/SMB/S3/MinIO; metadata in DB, bytes in object storage

## 4. Critical Contradictions Found

| ID | Area | Documents involved | Old/conflicting behavior | Canonical behavior | Resolution |
|----|------|--------------------|--------------------------|--------------------|------------|
| C-001 | PyTorch/CUDA | hardware.md §D-Q4, verification-checklist V-017 vs ADR-0006 | Referenced "cu124 pin" and "2.6.x+cu124" after ADR-0006 was rewritten to 2.7.x+cu126 | ADR-0006 recommends PyTorch 2.7.x+cu126 (cu118 fallback) | Updated both files to 2.7.x+cu126 with driver minimums |
| C-002 | Python/PyTorch | ADR-0005 vs ADR-0006 | ADR-0005 said Python 3.12 "supported by PyTorch 2.4-2.6" | Viable range is 2.7-2.14 (ComfyUI requires >= 2.7) | Updated ADR-0005 to "2.7-2.14" |
| C-003 | GPU backend | ADR-0007 vs docs 06/08 | ADR-0007 `GpuBackend` port listed `allocate/free`, implying the backend (and by extension worker path) handles allocation | GPU backend exposes no allocation API; scheduler solely owns allocation | Removed `allocate/free` from the port definition in ADR-0007 |
| C-004 | Doc references | doc 02 vs doc 20 | Doc 02 linked "`18-deployment` (later phase)" — nonexistent filename | Deployment doc is `20-deployment-architecture.md` | Fixed link in doc 02 |
| C-005 | API auth | doc 19 vs doc 18 | Doc 19 said all endpoints except /health, /ready, /auth/login require auth — but `POST /api/v1/workers/register` must be unauthenticated (one-time token) | Register endpoint authenticates via one-time registration token | Added register endpoint to the auth exception list in doc 19 |
| C-006 | Redis scope | doc 13 | "multi-node deployments add a Redis pub/sub backplane" — unscoped, could imply Redis is a current dependency | Redis is a future multi-node option only; queue remains Postgres-backed | Tightened wording in doc 13 |

## 5. Stale References Removed

| File | Old reference | New reference |
|------|---------------|---------------|
| docs/hardware.md (§D-Q4) | "recommended PyTorch cu124 pin ... fall back to cu118" | "PyTorch 2.7.x+cu126, requires Linux driver >= 560.28.03; cu118 fallback (driver >= 520.61.05)" |
| docs/verification-checklist.md (V-017) | "compatible with ADR-0006 (2.6.x+cu124)" | "runs on the ADR-0006 pin (PyTorch 2.7.x+cu126); ComfyUI requires torch >= 2.7" |
| docs/decisions/ADR-0005 | "supported by PyTorch 2.4-2.6" | "supported by every PyTorch version in the ADR-0006 viable range, 2.7-2.14" |
| docs/decisions/ADR-0007 | port method `allocate/free` | removed (scheduler owns allocation) |
| docs/design/02 | `18-deployment` | `20-deployment-architecture.md` |
| docs/design/13 | unscoped Redis backplane | scoped as future multi-node option |
| docs/design/19 | auth exception list | added worker /register exception |

**Historical artifact (left intact):** `ai-media-platform-report.txt` (untracked, repo root) is a point-in-time environment/file snapshot from 2026-10-05 15:02. Its quoted copies of hardware.md and verification-checklist.md contain the pre-fix cu124/2.6.x wording. It is a historical record, not architecture documentation, and was deliberately not modified; its quoted content is superseded by the live docs.

## 6. Job Lifecycle Audit

Canonical state machine (consistent across docs 07, 08, 18 after audit):

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

- Reservation is atomic with scheduling: one transaction performs GPU `AVAILABLE→RESERVED` and job `QUEUED→RESERVED` (doc 08).
- Worker STARTING report triggers GPU `RESERVED→BUSY` (scheduler-owned transition).
- Lease expiry / worker loss: job → `QUEUED` (new attempt), GPU `RESERVED/BUSY → AVAILABLE` (or `UNHEALTHY`). No document implies a dead worker permanently owns a GPU.
- Attempts are distinct `job_attempts` rows; previous attempts are never overwritten.
- Terminal states are never re-entered.

## 7. Scheduler / Queue / Worker Ownership

- **Queue:** job ordering, visibility, claim eligibility, atomic claiming, duplicate-claim prevention. Does NOT own GPU placement.
- **Scheduler:** sole authority for which job runs on which GPU; atomic reservation; singleton via `pg_try_advisory_lock`; API tier may scale horizontally while scheduling leadership stays singleton.
- **Worker:** pull/claim only; executes jobs the scheduler already reserved for its GPUs; re-validates capabilities/VRAM as a safety net; never allocates, reserves, or releases GPUs.

## 8. GPU Architecture

- `GpuBackend` port: `list_devices`, `get_metrics`, `get_capabilities`, `run_probe`, `execute` — no allocation API.
- Backends: `NvmlGpuBackend` (real), `MockGpuBackend` (N simulated GPUs, configurable VRAM/utilization/failures/OOM); selection via `GPU_BACKEND=auto|nvml|mock`.
- Capabilities derived from hardware facts + registry rules + probes; never GPU-name if-statements.
- Parallel jobs (job A → GPU 1, job B → GPU 2) are the default; true multi-GPU inference only when model version + engine adapter declare it — never assumed.
- GPU-less development and CI are fully supported via the mock backend.

## 9. Worker Protocol

- **Registration:** admin creates worker (`POST /api/v1/workers`) → one-time registration token → worker calls `POST /api/v1/workers/register` → standing credentials (long-lived worker token + short-lived access credentials, renewable via `POST /api/v1/workers/{id}/credentials`).
- **Heartbeat:** `POST /api/v1/workers/{id}/heartbeat` every ~10 s; renews active claim leases; missed × N → node OFFLINE → jobs evaluated/requeued.
- **Claim:** `POST /api/v1/workers/{id}/jobs/claim` (POST — side effects); response carries the reserved GPU id.
- **Lease:** 60 s default visibility timeout; renewed via heartbeat or `POST /api/v1/workers/{id}/claims/{claim_id}/renew`; expiry → job QUEUED, new attempt, GPU released, rescheduling.
- **Progress/complete/fail:** `POST /api/v1/jobs/{id}/progress`, `POST /api/v1/jobs/{id}/complete`, `POST /api/v1/jobs/{id}/fail` with classified errors.

## 10. Retry / Attempt Model

- Retryable: OOM, transient CUDA, worker crash, network/transient infrastructure. Fatal: invalid workflow/model/request, validation failure.
- Bounded: max 3 attempts (default), exponential backoff with jitter, per-type caps. No infinite loops.
- Each attempt records: failure reason, worker, GPU, timestamps, model, workflow, parameters, seed, duration, attempt number.

## 11. API Consistency

- One inconsistency found and resolved (C-005: worker /register auth exception).
- All worker endpoints agree between doc 18 and doc 19: register, credentials, heartbeat, jobs/claim, claims/renew, jobs/progress, jobs/complete, jobs/fail.
- Error format, pagination, filtering, and versioning conventions are uniform.

## 12. Database / Transaction Consistency

- GPU reservation + job reservation: single transaction (doc 08) — no read-decide-write race.
- Job claim: `SKIP LOCKED` — no duplicate claims.
- Lease expiry + requeue + GPU release: transactional state transitions.
- Scheduler leadership: advisory lock — no split-brain double allocation.
- No race-prone sequences remain in any document.

## 13. PyTorch / CUDA / Pascal

- Documented target: PyTorch 2.7.x+cu126 wheel, Python 3.12, fp16 only (Pascal sm_61, no tensor cores, no bf16).
- Driver requirement: Linux >= 560.28.03 (cu126); fallback cu118 build (driver >= 520.61.05).
- ComfyUI constraint: requires torch >= 2.7 (official system requirements) — the earlier 2.6 pin was rejected.
- Verification status: **NOT VERIFIED ON REAL PASCAL HARDWARE** — V-001..V-018 all TODO; no claim of successful CUDA/PyTorch execution is made anywhere.
- Stale references removed: all cu124 / PyTorch 2.6 / "2.4-2.6" references in live docs (see section 5).

## 14. Remaining Risks

1. ADR-0002 Phase 1-3 queue choice (Postgres SKIP LOCKED) is provisional pending owner confirmation.
2. All ADRs remain **Proposed** — per the ADR process, none are silently accepted; real Pascal hardware verification is separate from documentation consistency.
3. Target server facts unknown (hardware.md §B): driver, CUDA, OS, disk, network, ComfyUI presence.
4. `ai-media-platform-report.txt` (untracked historical snapshot) contains pre-fix quoted content; left intact deliberately.
5. Model VRAM/speed estimates in doc 09 remain unconfirmed estimates pending real-GPU spikes.

## 15. Recommended Next Phase

1. Owner reviews and accepts/rejects the eight Proposed ADRs (especially ADR-0002's provisional queue choice and ADR-0006's version pin).
2. Owner answers the open questions in hardware.md §D (target server access, driver, mirrors, disk).
3. Begin Phase 1 implementation (doc 16) once ADRs are accepted — no implementation has started in this audit.

## 16. Final File Change List

- docs/hardware.md — §D-Q4 updated to 2.7.x+cu126 + driver minimums
- docs/verification-checklist.md — V-017 updated to 2.7.x+cu126
- docs/decisions/ADR-0005-python-dependency-management.md — PyTorch range corrected to 2.7-2.14
- docs/decisions/ADR-0007-gpu-backend-abstraction.md — removed `allocate/free` from port
- docs/design/02-technology-stack.md — fixed `18-deployment` → `20-deployment-architecture.md`
- docs/design/13-observability-architecture.md — Redis backplane scoped as future-only
- docs/design/19-api-specification.md — worker /register added to auth exceptions
- docs/audit-report-2026-10-05.md — this report (new)

## 17. Final Verification

- NO APPLICATION CODE WRITTEN
- NO PACKAGES INSTALLED
- NO SERVICES STARTED
- NO GPU REQUIRED
- NO GIT COMMIT CREATED
- NO GIT PUSH PERFORMED

AUDIT COMPLETE — NO CODE CHANGED
