# 01 — System Architecture

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md)

## Overview

The platform is a **modular monolith control plane** with **distributed worker agents** (spec sections 9, 67). It is not a collection of premature microservices: API, Orchestrator, Scheduler, Worker, Model Manager, Workflow Manager, Storage, and Monitoring are logical modules with strict boundaries (spec section 67), deployed as one unit on a single server first (spec section 69) and splittable later where operationally justified.

```text
┌────────────────────────────────────────────────────────────┐
│                     Control Plane (single server)            │
│                                                              │
│  ┌──────────┐  ┌──────────────┐  ┌────────────┐             │
│  │ API      │  │ Orchestrator │  │ Scheduler  │             │
│  │ (FastAPI)│  │              │  │            │             │
│  └────┬─────┘  └──────┬───────┘  └─────┬──────┘             │
│       │               │                │                     │
│  ┌────┴───────────────┴────────────────┴────┐               │
│  │            Domain Layer (pure logic)       │               │
│  └────┬───────────────┬────────────────┬────┘               │
│  ┌────┴─────┐   ┌─────┴──────┐   ┌─────┴──────┐             │
│  │ Storage  │   │ Model Mgr  │   │ Workflow   │             │
│  │ Abstr.   │   │            │   │ Mgr        │             │
│  └────┬─────┘   └────────────┘   └────────────┘             │
│       │                                                      │
│  ┌────┴─────┐   ┌────────────┐   ┌────────────┐             │
│  │PostgreSQL│   │ Job Queue  │   │ Object     │             │
│  │          │   │ (abstract) │   │ Storage    │             │
│  └──────────┘   └────────────┘   └────────────┘             │
│                                                              │
│  ┌──────────────────────────────────────────┐               │
│  │ Worker Agent (co-located in Phase 1)      │               │
│  │  GpuBackend (NVML | Mock) · Engine Adapters│              │
│  └──────────────────────────────────────────┘               │
└────────────────────────────────────────────────────────────┘
              │  (control channel: REST + SSE, mTLS/JWT)
              ▼
┌─────────────────────────────┐
│ Worker Node 02..N (Phase 9) │
│  Worker Agent + GPUs        │
└─────────────────────────────┘
```

## Principles

- **API-first** (spec section 56): every UI operation has an API equivalent.
- **No hard-coding** (spec section 100): no GPU names, model names, workflow names, project names, storage paths, or secrets in code. Everything is configuration or registry data.
- **Data-driven capabilities** (spec section 6, AGENTS.md): GPU capabilities come from discovery data + probes, never name-based if-statements.
- **Storage abstraction** (spec sections 43-45): the relational DB stores metadata only; media lives behind a storage port.
- **Extensibility without rewrite** (spec section 3): engines, models, workflows, storage, and queue are all behind interfaces.

## Module responsibilities

| Module | Spec | Responsibility |
|--------|------|----------------|
| API | 56-57 | REST endpoints, OpenAPI, auth, request validation |
| Orchestrator | 11 | Job lifecycle, retries, failure recovery, worker management |
| Scheduler | 12, 53 | GPU allocation, policies, priority, locking |
| Worker Agent | 10, 71 | Registration, discovery, heartbeat, execution, progress |
| Model Manager | 36-39 | Registry, install/validate/cache, compatibility |
| Workflow Manager | 40-42 | Registry, versions, engine adapters |
| Storage | 43-45 | Media read/write/delete, provider abstraction |
| Monitoring | 48-51 | Logs, metrics, health |
| Queue | 64 | Job enqueue / ordering / visibility / scheduling claim (mechanism). **Job.state ownership stays with API/domain, Scheduler and Orchestrator** per `implementation-contract-2026-10-05.md` §3 (ADR-0002) |

## Deployment shapes

- **Phase 1-3 (single server, spec section 69):** one process group runs API + Orchestrator + Scheduler + Worker Agent; Postgres + object storage on the same host.
- **Phase 9+ (multi-node, spec section 70):** Worker Agents run on worker nodes and connect to the control plane; no core rewrite — the worker is already a separate agent with its own protocol (see `18-worker-protocol.md`).

## Technology references

- Backend: [ADR-0001](../decisions/ADR-0001-backend-framework.md) (FastAPI)
- Queue: [ADR-0002](../decisions/ADR-0002-job-queue.md)
- Realtime: [ADR-0003](../decisions/ADR-0003-realtime-transport.md) (SSE)
- Frontend: [ADR-0004](../decisions/ADR-0004-frontend-stack.md) (React+Vite+TS)
- Python: [ADR-0005](../decisions/ADR-0005-python-dependency-management.md) (uv)
- PyTorch/CUDA: [ADR-0006](../decisions/ADR-0006-pytorch-cuda-pascal.md)
- GPU backend: [ADR-0007](../decisions/ADR-0007-gpu-backend-abstraction.md)
- Database: [ADR-0008](../decisions/ADR-0008-database.md) (PostgreSQL)
