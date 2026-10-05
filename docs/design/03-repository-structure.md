# 03 — Repository Structure

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md)

```text
ai-media-platform/
├── .python-version                 # pinned Python (ADR-0005)
├── pyproject.toml                  # workspace root: backend + worker packages
├── uv.lock                         # committed lockfile
├── README.md
├── AGENTS.md
├── docs/
│   ├── spec.md                     # requirements (source of truth)
│   ├── hardware.md                 # environment reality (source of truth)
│   ├── design/                     # this package (spec §103 items)
│   ├── decisions/                  # ADRs
│   └── verification-checklist.md   # real-GPU verification items
├── deploy/
│   ├── docker/                     # Dockerfiles (Phase 2+)
│   │   ├── Dockerfile.backend
│   │   ├── Dockerfile.worker       # NVIDIA CUDA base image
│   │   └── Dockerfile.frontend
│   └── docker-compose.yml          # single-server stack (spec section 69)
├── backend/
│   └── src/app/
│       ├── api/                    # FastAPI routers (thin adapters)
│       ├── domain/                 # pure business logic, no framework imports
│       │   ├── jobs/  projects/  assets/  models/  workflows/
│       │   ├── scheduler/  orchestrator/  gpu/
│       ├── persistence/            # repositories, Alembic migrations
│       ├── infrastructure/         # config, secrets, storage, queue adapters
│       └── main.py                 # app composition root
├── worker/
│   └── src/worker/
│       ├── agent.py                # registration, heartbeat, execution loop
│       ├── gpu/
│       │   ├── base.py             # GpuBackend interface
│       │   ├── nvml_backend.py     # real backend
│       │   └── mock_backend.py     # simulated N GPUs, failures, OOM
│       ├── engines/
│       │   ├── base.py             # EngineAdapter interface
│       │   ├── comfyui_adapter.py  # Phase 5
│       │   └── diffusers_adapter.py
│       └── capabilities/           # capability rules + probes
├── frontend/
│   └── src/
│       ├── api/                    # generated typed client (OpenAPI)
│       ├── components/             # shared UI
│       ├── features/               # dashboard, projects, jobs, gpus, ...
│       └── i18n/                   # en, fa (RTL-ready)
└── tests/
    ├── backend/  worker/  frontend/  e2e/
```

## Rules

- `domain/` never imports FastAPI, SQLAlchemy, or torch — framework independence keeps the core testable and engine-agnostic (spec sections 42, 66).
- Migrations live with the code they migrate (`backend/src/app/persistence/migrations`).
- No secrets, paths, or hardware names in any file (spec section 100) — all via env/config.
