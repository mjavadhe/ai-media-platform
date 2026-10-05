# ADR-0007: Pluggable GPU Backend (Real NVML + Mock)

- Status: Proposed
- Date: 2026-10-05

## Context

AGENTS.md requires the worker to support a pluggable GPU-discovery/execution backend: a real one (NVML / nvidia-smi) and a MOCK one that simulates N GPUs with configurable VRAM, utilization, failures, and OOM. All scheduler/queue/worker tests must run against the mock in CI. The dev machine has no GPU (hardware.md section A). Spec sections 5-8 require automatic GPU discovery, a dynamic capability system, and GPU lifecycle states — all without hard-coding GPU names or counts.

## Options considered

1. **Interface + two adapters (NVML real, in-process mock)**
   - A `GpuBackend` port defines: `list_devices()`, `get_metrics(device_id)`, `get_capabilities(device_id)`, `run_probe(device_id, probe)`, `execute(job)`. The port exposes **no allocation API** — GPU allocation/reservation is owned exclusively by the scheduler (see `docs/design/08-scheduler-architecture.md`); the backend reports facts and executes.
   - `NvmlGpuBackend` wraps NVML (via `pynvml` or `nvidia-smi` parsing) on real workers.
   - `MockGpuBackend` simulates N GPUs with configurable VRAM, utilization, temperature, power, injected failures, and OOM; selected by config/env var in dev and CI.
   - Pros: zero GPU needed for the entire test suite; failure/OOM paths are testable deterministically (spec section 93); capability decisions stay data-driven (AGENTS.md); swapping backends is config, not code.
   - Cons: the mock must be kept honest (modeled on real NVML semantics); two code paths to maintain.

2. **Real NVML only, tests against recorded fixtures**
   - Pros: one code path.
   - Cons: cannot test OOM/failure injection deterministically; CI still needs a GPU or heavy faking; violates the AGENTS.md mock requirement. Rejected.

3. **External GPU simulator sidecar**
   - Pros: none over option 1.
   - Cons: extra process, extra ops. Rejected.

## Decision

**Interface + two adapters** (option 1). The backend is selected by configuration (`GPU_BACKEND=auto|nvml|mock`), never by GPU-name if-statements (AGENTS.md). `auto` uses NVML when available, else mock. The mock is a first-class component with its own tests and is the mandatory backend for CI scheduler/queue/worker tests. Capability determination (spec section 6) consumes backend-reported data (compute capability, VRAM, driver, probe results) through a registry of capability rules — no hardware names in business logic.

## Consequences

- Easier: full test coverage without GPUs; deterministic failure injection; dev and CI run identical code paths.
- Harder: the mock must mirror NVML semantics closely enough that behavior differences surface only in real-GPU verification (V-004, V-005).
- Must verify later: V-004 (real NVML backend matches nvidia-smi), V-005 (metrics parity).
