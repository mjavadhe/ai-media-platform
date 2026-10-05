# 02 — Technology Stack

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md)

Every choice below is justified in an ADR under `docs/decisions/`.

| Layer | Choice | ADR |
|-------|--------|-----|
| Language (backend/worker) | Python 3.12 (pinned via uv) | [ADR-0005](../decisions/ADR-0005-python-dependency-management.md) |
| Dependency management | uv + `pyproject.toml` + committed `uv.lock` | [ADR-0005](../decisions/ADR-0005-python-dependency-management.md) |
| Backend framework | FastAPI (async, OpenAPI-native) | [ADR-0001](../decisions/ADR-0001-backend-framework.md) |
| Database | PostgreSQL (Alembic migrations) | [ADR-0008](../decisions/ADR-0008-database.md) |
| Job queue | Queue abstraction; Phase 1-3: Postgres-backed (SKIP LOCKED) — provisional, owner confirms; scale path: Redis/ARQ | [ADR-0002](../decisions/ADR-0002-job-queue.md) |
| Realtime transport | Server-Sent Events (REST for commands) | [ADR-0003](../decisions/ADR-0003-realtime-transport.md) |
| Frontend | React + Vite + TypeScript | [ADR-0004](../decisions/ADR-0004-frontend-stack.md) |
| GPU backend | Pluggable: NVML (real) + Mock; `auto\|nvml\|mock` config | [ADR-0007](../decisions/ADR-0007-gpu-backend-abstraction.md) |
| ML runtime (worker) | PyTorch 2.7.x+cu126, fp16 only (Pascal sm_61); cu118 fallback | [ADR-0006](../decisions/ADR-0006-pytorch-cuda-pascal.md) |
| Inference engines | ComfyUI adapter (Phase 5), diffusers/custom behind same interface (spec section 42) | — |
| Storage | Pluggable: local / NFS / SMB / S3 / MinIO behind one interface | `11-storage-architecture.md` |
| Packaging | Docker (Phase 2+); GPU workers via NVIDIA Container Runtime (spec section 68) | `20-deployment-architecture.md` |

## Constraints honored

- **4 GiB dev RAM budget** (AGENTS.md): no mandatory all-services-at-once; Postgres + backend + frontend + mock worker fit comfortably; Redis only if the queue is later migrated to ARQ (ADR-0002).
- **No unrestricted internet assumed** (hardware.md section C): uv/pip/npm mirrors and model mirrors are configuration; offline pre-seeded paths supported.
- **No GPU on dev machine** (hardware.md section A): mock GPU backend is the default dev/CI backend (ADR-0007).
