# 17 — GPU Discovery Protocol

Status: Proposed · Date: 2026-10-05 · Parent: [spec §106 item 07](../spec.md), [spec §5](../spec.md) · ADR: [ADR-0007](../decisions/ADR-0007-gpu-backend-abstraction.md)

## Scope

How a worker discovers its GPUs, what is reported, how capabilities are derived, and how the data reaches the control plane. Discovery is **automatic** — users never register GPUs manually (spec section 5).

## Discovery flow

```text
Worker start
  → load GpuBackend (auto|nvml|mock — config, ADR-0007)
  → list_devices()          # uuid, name, vendor, vram_total, compute_capability, driver, pci_bus_id
  → get_metrics(device)     # vram_used, utilization, temperature, power, processes
  → derive_capabilities()   # registry rules + optional probes (spec §6)
  → register node (see 18-worker-protocol.md)
  → periodic re-discovery + metrics push (heartbeat payload)
```

## Reported data (spec section 5 — minimum set)

`Node ID, GPU ID, GPU UUID, GPU Name, GPU Vendor, VRAM Total, VRAM Available, Driver Version, CUDA Version, Compute Capability, PCI Bus ID, Temperature, Power Usage, Power Limit, GPU Utilization, Memory Utilization, Running Processes, Health Status, Current Job, Capabilities`

## Capability derivation (spec section 6)

1. **Hardware facts** come from the backend (compute capability, VRAM, driver).
2. **Registry rules** (data, not code) map facts → capabilities (e.g. `vram_total >= 10 GB AND cc_major >= 6 → image_generation`).
3. **Probes** (optional) run small validation jobs per capability; results are stored with timestamps and can override/augment rules.
4. Capabilities are re-derived on every discovery cycle and on hardware/driver changes.

**No GPU-name-based decisions** (AGENTS.md): rules consume reported facts only.

## Transport and timing

- Discovery results ride the **worker heartbeat** (see `18-worker-protocol.md`): full snapshot on registration, deltas + metrics on each heartbeat.
- The control plane persists `gpu_devices`, `gpu_capabilities`, and updates `gpu_nodes.status`.
- Metrics needed more frequently than the heartbeat (dashboard refresh) are pushed via the same control channel at a configurable cadence (default: heartbeat interval; metrics stream at a faster interval over SSE).

## Health classification (spec section 51)

The backend reports raw facts; the control plane maps them to `HEALTHY / DEGRADED / UNHEALTHY / OFFLINE / MAINTENANCE` using configurable thresholds (e.g. temperature limit, ECC errors, utilization stuck at 100%).

## Failure behavior

- NVML/backend failure → discovery retried with backoff; repeated failure → node `DEGRADED`, GPUs `ERROR`.
- Mock backend: discovery always succeeds unless failure injection is configured (tests, spec section 93).

## Real-GPU verification

- V-004: real NVML backend reports all GPUs with UUID, VRAM, compute cap — matches `nvidia-smi`.
- V-005: utilization/VRAM/temperature/power values match `nvidia-smi` under idle and load.
