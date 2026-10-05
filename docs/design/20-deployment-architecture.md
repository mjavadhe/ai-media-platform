# 20 — Deployment Architecture

Status: Proposed · Date: 2026-10-05 · Parent: [spec §106 item 18](../spec.md), [spec §68](../spec.md), [spec §69](../spec.md), [spec §70](../spec.md)

## Target shapes

1. **Single server (Phase 1-3, spec section 69):** frontend, backend, database, queue, orchestrator, scheduler, worker, GPU(s), storage — all on one machine.
2. **Multi-node (Phase 9+, spec section 70):** one controller node + N worker nodes; workers connect via the worker protocol (`18-worker-protocol.md`). No core rewrite — the worker is already a separate agent.

## Container strategy (spec section 68)

```text
deploy/docker/
  Dockerfile.frontend    # nginx serving the React build (+ /api proxy)
  Dockerfile.backend     # FastAPI app (API + Orchestrator + Scheduler)
  Dockerfile.worker      # NVIDIA CUDA base image + ComfyUI + worker agent
  docker-compose.yml     # single-server stack
```

- **Backend:** plain Python image; no GPU access needed.
- **Worker:** built on an NVIDIA CUDA base image matching ADR-0006 (cu126); requires the NVIDIA Container Runtime on the host (`docker run --gpus all` / compose `deploy.resources.reservations.devices`).
- **Frontend:** static build behind nginx; proxies `/api` to the backend.
- **Database:** Postgres container (or host Postgres) with a named volume.
- **Storage:** local volume (Phase 1) or MinIO container (S3-compatible, spec section 44).

## Single-server compose topology

```text
┌────────────────────────── single host ──────────────────────────┐
│  frontend (nginx) ──proxy──> backend (FastAPI) ──> postgres     │
│                                     │                           │
│                                     ├──> worker (GPU, --gpus)   │
│                                     └──> minio (optional)       │
│  GPU(s) visible to the worker via nvidia-container-runtime       │
└─────────────────────────────────────────────────────────────────┘
```

## Resource budget (dev machine, ~4 GiB cap — AGENTS.md)

| Service | Typical RAM |
|---------|-------------|
| backend | ~200-400 MB |
| frontend (nginx) | ~20-50 MB |
| postgres | ~100-200 MB |
| worker (mock backend, no model loaded) | ~200-400 MB |
| minio (optional) | ~100-200 MB |

The full dev stack fits in ~1-1.5 GiB, leaving headroom. The worker with a loaded model needs VRAM (GPU), not host RAM, beyond the base process. Not all services must run at once (AGENTS.md): dev workflows can run backend + worker + Postgres only.

## GPU container requirements (spec section 68)

- Host: NVIDIA driver >= 560.28.03 (cu126, ADR-0006) + nvidia-container-toolkit.
- Worker container: CUDA base image; GPUs exposed via the NVIDIA Container Runtime; `GPU_BACKEND=nvml` inside the container.
- Verification: V-002 (`docker run --gpus all ... nvidia-smi`).

## Configuration and secrets (spec section 96)

- All configuration via env vars / config files; secrets via env or a secret manager — never in images or repos.
- `config.example` documents every variable; compose files reference env, not literals.
- Mirrors (PyPI, npm, Docker Hub, Hugging Face) and offline pre-seeded paths are configuration (hardware.md section C).

## Multi-node (Phase 9)

- Controller node: frontend + backend + Postgres + storage.
- Worker nodes: worker agent only; connects to the controller over the worker protocol (mTLS/JWT, pull model — `18-worker-protocol.md`).
- Adding a node = install agent + register; no core changes (spec section 70).

## CI/CD (spec section 97)

```text
push → lint → typecheck → unit tests → integration tests (mock GPU backend)
     → build images → security scans → (manual) deploy
```

- All tests run against the **mock GPU backend** (ADR-0007); no GPU in CI.
- Images built in CI; deployment via compose on the target server.
- Real-GPU verification is a separate manual gate (`docs/verification-checklist.md`) before production use.

## Backup (spec section 77)

- Postgres: scheduled dumps (volume or object storage).
- Storage: provider-level replication for MinIO/S3; local volume backed up per policy.
- Recovery runbook: Phase 10.
