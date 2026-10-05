# 15 — Risks and Technical Challenges

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md)

## Hardware / environment risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Target server unknowns (driver, CUDA, OS, disk, network) — hardware.md section B | PyTorch/CUDA pin may not work; model disk footprint may not fit | ADR-0006 pins with fallbacks (2.7.x+cu126 primary, cu118 driver fallback); V-001 collects facts first; model install validates disk space (spec section 38) |
| GTX 1080 Ti 11 GB limits model choice | SDXL borderline; most video models infeasible | Feasibility table in `09-model-registry-architecture.md` (all estimates, spike-required); registry enforces VRAM requirements; video deferred past MVP (spec section 99) |
| No unrestricted internet (hardware.md section C) | Dependency installs, model downloads, container pulls may fail | Configurable mirrors + offline pre-seeded paths (spec sections 74-75); lockfile-based installs (ADR-0005) |
| 4 GiB dev RAM budget | Full stack may not fit if services are heavyweight | Lightweight stack by design (FastAPI, Postgres, local storage, mock worker); Redis only if queue migrates (ADR-0002) |

## Technical risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Pascal has no tensor cores / bf16 | Slow inference; no AMP-bf16 | fp16-only policy (ADR-0006); expectations set in UI; FASTEST policy uses probe data |
| Mock vs real NVML behavior divergence | Tests pass, production fails | Mock models NVML semantics; V-004/V-005 parity checks on target |
| Postgres-backed queue throughput | Latency under load | SKIP LOCKED + short poll; scale path to Redis/ARQ behind the abstraction (ADR-0002) |
| ComfyUI's own PyTorch pin conflicts with ADR-0006 | Engine fails to start | V-006 spike before Phase 5; adapter isolates the engine |
| Job state consistency across worker crash | Lost or stuck jobs | Heartbeat + requeue flow (spec section 52); transactional state transitions |
| Scope creep across 106 spec sections | MVP delay | Strict phase gating (AGENTS.md); MVP acceptance criteria in `14-mvp-roadmap.md` |

## Process risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Design docs drift from code | Misleading documentation | Docs updated per phase (AGENTS.md definition of done) |
| ADRs approved without owner confirmation | Wrong major decisions | All ADRs Status: Proposed until owner accepts |
