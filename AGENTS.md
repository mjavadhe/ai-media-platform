# AI Media Platform — Agent Rules

Full requirements: docs/spec.md (read the relevant sections before working; do not load it all at once).
Real hardware/environment: docs/hardware.md (source of truth over spec examples).

## Workflow rules
- Work phase by phase (spec section 98). Never start a phase without my explicit approval.
- Before any major architectural decision: write an ADR in docs/decisions/ (options, trade-offs, recommendation) and wait for approval.
- Each task must end with: tests written and passing, docs updated, short summary of what changed and what is NOT done.
- Do not generate large amounts of code in one step. Prefer small, reviewable commits.
- If a requirement is ambiguous or conflicts with hardware reality, STOP and ask. Do not guess.

## Hard constraints (spec section 100)
- No hard-coded GPUs, models, workflows, projects, storage paths, or secrets.
- No assumption of 1 GPU / 1 node / 1 project / ComfyUI as the only engine / multi-GPU support.
- No large media inside the relational DB. Storage goes through an abstraction layer.
- Secrets only via env vars or secret manager.

## Hardware reality
- Initial GPUs are GTX 1080 Ti (Pascal, compute capability 6.1, no tensor cores, no bf16, 11 GB).
- Capability checks must be data-driven (registry + probes), never if-statements on GPU names.
- Verify PyTorch/CUDA version compatibility with Pascal before choosing versions; record it in an ADR.

## Definition of done
A feature is done only if: unit + integration tests pass, failure paths are tested, it is documented in docs/, and it was verified on the real worker where relevant.

## Dev environment has no GPU
- The dev machine has no NVIDIA GPU. The worker must support a pluggable GPU-discovery/execution backend:
  a real one (NVML / nvidia-smi) and a MOCK one that simulates N GPUs with configurable VRAM,
  utilization, failures and OOM. All scheduler/queue/worker tests must run against the mock in CI.
- Anything that needs a real GPU must be marked "NEEDS REAL-GPU VERIFICATION" and listed
  in docs/verification-checklist.md, to be run later on the target server.
- Dev machine has ~14 GiB RAM total: keep the full dev stack within ~4 GiB. Do not require all services to run at once.
- Use a pinned Python version via uv or a virtualenv, not the system Python (Ubuntu 26.04 is very new).
