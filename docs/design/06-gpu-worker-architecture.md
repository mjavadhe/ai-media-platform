# 06 — GPU / Worker Architecture

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md) · ADRs: [ADR-0007](../decisions/ADR-0007-gpu-backend-abstraction.md), [ADR-0006](../decisions/ADR-0006-pytorch-cuda-pascal.md)

## Pluggable GPU backend (spec sections 5-8, AGENTS.md)

```text
worker/
  gpu/
    base.py            # GpuBackend interface
    nvml_backend.py    # real: NVML / nvidia-smi
    mock_backend.py    # simulated N GPUs, configurable VRAM/util/failures/OOM
    capabilities.py    # capability rules + probes (data-driven)
```

### `GpuBackend` interface

| Method | Purpose |
|--------|---------|
| `list_devices() -> [GpuDevice]` | discovery: uuid, name, vendor, vram_total, compute_capability, driver, pci_bus_id |
| `get_metrics(device_id) -> GpuMetrics` | vram_used, utilization, memory_utilization, temperature, power, power_limit, processes |
| `get_capabilities(device_id) -> [Capability]` | derived from hardware data + registry rules + probes |
| `run_probe(device_id, probe) -> ProbeResult` | optional benchmark/validation jobs (spec section 6) |
| `execute(device_id, job) -> JobResult` | run inference via the engine adapter |

**Allocation ownership:** the GPU backend exposes no allocation API. GPU allocation and reservation are owned exclusively by the **scheduler** (see `08-scheduler-architecture.md`); the worker only executes jobs the scheduler has already reserved for one of its GPUs.

### Backend selection

`GPU_BACKEND=auto|nvml|mock` (env/config). `auto` → NVML if a GPU is present, else mock. **No GPU-name if-statements anywhere** (AGENTS.md): all capability logic consumes backend data.

### Mock backend

- Simulates N GPUs (configurable count, VRAM, compute capability, driver version).
- Configurable utilization/temperature/power baselines and drift.
- Injectable failures: `oom`, `device_lost`, `probe_failure`, `hang` — used by scheduler/queue/worker tests (spec section 93) and CI.
- Deterministic seed support for reproducible tests.

## Capability system (spec section 6)

Capabilities (e.g. `image_generation`, `inpainting`, `video_generation`, `upscaling`, `model:<id>`) are computed from:

1. **Hardware data** — compute capability, VRAM, driver (from `GpuBackend`).
2. **Registry rules** — declarative rules (e.g. "VRAM >= 10 GB and cc >= 6.0 → image_generation"), stored as data, not code branches on GPU names.
3. **Probes** — optional real jobs that validate a capability on the actual hardware (recorded with result + timestamp).

The scheduler queries capabilities; it never inspects GPU names.

## Worker internals

- **Execution loop:** claim a job already reserved for one of its GPUs (see `18-worker-protocol.md`) → re-validate capabilities/VRAM against the reserved GPU → run engine adapter → stream progress → upload results via storage port → report status. The worker never allocates, reserves, or releases GPUs — that is the scheduler's exclusive job.
- **Engine adapters** (spec section 42): `ComfyUIAdapter` (Phase 5), `DiffusersAdapter`, future engines — one interface: `validate(job) → run(job, progress_callback) → cancel()`.
- **Model cache:** local LRU cache per node; cache hits reported to the scheduler for cache-aware placement (spec section 39).
- **Isolation:** one job per GPU at a time, enforced by the scheduler's allocation lock (spec section 53); the worker's re-check is a safety net, not the mechanism.

## Multi-GPU distinction (spec section 8)

- **Parallel jobs:** default — scheduler places each job on a different GPU.
- **Multi-GPU inference:** only if the model version + engine adapter explicitly declare multi-GPU support; the scheduler then allocates a GPU set atomically. Never assumed.

## Failure handling

- OOM / CUDA errors are classified (retryable vs fatal) and drive the retry policy (spec section 14).
- Worker crash → heartbeat loss → node OFFLINE → retryable jobs requeued (spec section 52; see `18-worker-protocol.md`).
