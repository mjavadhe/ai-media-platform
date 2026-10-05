# 09 — Model Registry Architecture

Status: Proposed · Date: 2026-10-05 · Parent: [spec §103](../spec.md) · ADR: [ADR-0006](../decisions/ADR-0006-pytorch-cuda-pascal.md)

## Registry data model (spec sections 36-37)

Each model has structured metadata: `Model ID, Name, Version, Type, Provider, Repository, License, File Size, VRAM Requirement, Recommended VRAM, Precision, Quantization, Supported Tasks, Supported Engines, Supported GPUs, Multi-GPU Support, Dependencies, Workflow, Checksum, Installation Method`. Types include image/video generation, upscaling, editing, segmentation, control, embedding, LLM, vision, audio, other (spec section 37). All fields are data — nothing about specific models is hard-coded (spec section 100).

## Model versions and installations

- `models` → `model_versions` (immutable versioned metadata) → `model_installations` (per-node install state: path, checksum, status).
- A model version declares its engine compatibility, precision, quantization, and VRAM requirements; the capability system (spec section 6) uses these to decide where it can run.

## Installation pipeline (spec sections 38, 73-75)

```text
resolve source (HF / git / URL / internal mirror — spec §74)
  → check disk space, VRAM requirement, runtime deps, CUDA + engine compatibility
  → download (retries, resumable, checksum validation — spec §75)
  → verify checksum + probe load
  → register installation (per node)
```

Sources are pluggable (`ModelSource` port, spec section 74). Mirrors and offline pre-seeded paths are configuration (hardware.md section C).

## Pascal 11 GB feasibility assessment

**All VRAM and speed numbers below are ESTIMATES derived from public community reports and general model sizes. They are NOT measurements and MUST be confirmed by a spike on the real GPUs before being relied upon (see verification checklist V-007, V-011, V-012).**

| Model class | Precision | Est. VRAM | Est. feasible on 11 GB Pascal? | Notes |
|-------------|-----------|-----------|-------------------------------|-------|
| SD 1.5 (512×512) | fp16 | ~3-5 GB | **Likely yes** | Baseline target for MVP image generation |
| SDXL (1024×1024) | fp16 | ~9-12 GB | **Borderline** | May require ComfyUI `--lowvram`/offloading; unverified |
| SDXL Turbo / Lightning | fp16 | ~8-10 GB | **Probably yes** | Fewer steps, but same weights |
| Flux (fp16) | fp16 | ~20+ GB | **No** (without quantization) | GGUF Q4 est. ~6-9 GB — unverified on Pascal |
| AnimateDiff (SD1.5-based I2V) | fp16 | ~5-8 GB | **Likely yes** at low res/frames | Estimated; frame count and resolution limited |
| ModelScope T2V / LTX-Video | fp16 | ~16+ GB | **No** (estimate) | Exceeds 11 GB even with offloading, likely |
| Real-ESRGAN upscaler | fp16 | ~2-4 GB | **Likely yes** | Estimated |
| ComfyUI base overhead | — | ~1-2 GB | — | Estimated; reduces budget for the model itself |

**Consequences for the MVP:** the registry must record per-model VRAM requirements and the scheduler must enforce them against discovered GPU VRAM (spec sections 6, 12). Models estimated infeasible on Pascal are still registrable (the platform is hardware-agnostic) but will be marked as such for the current cluster until a spike proves otherwise. Video generation (spec section 33) is scheduled after the MVP foundation is stable, consistent with spec section 99.

## Compatibility detection (spec section 38)

Installation validates: disk space, VRAM requirement vs. discovered GPU VRAM, runtime dependencies, CUDA compatibility (ADR-0006), engine compatibility (spec section 42), and checksum where available. Failures are reported with actionable messages.

## Model cache (spec section 39)

Per-node LRU cache; the scheduler is cache-aware (see `08-scheduler-architecture.md`). Cache state is reported by workers and stored in `model_installations`.
