# Hardware & Environment

## A. Development machine (no discrete GPU)
- OS: Ubuntu 26.04.1 LTS (resolute), kernel 7.0.0-38-generic
- CPU: Intel Core i3-12100 (4 cores / 8 threads), x86_64
- RAM: 14 GiB total (≈5.5 GiB available under normal desktop load), swap 14 GiB
- Disk: /dev/sda5 212G, 138G free (single disk, ext4/other)
- Docker: NOT installed yet
- NVIDIA GPU: NONE (nvidia-smi not available) -> cannot run CUDA workloads
- Implication: GPU worker must be developed/tested against a MOCK GPU backend.
- Resource budget: keep the full dev stack (Postgres, queue, backend, frontend, mock workers) within ~4 GiB RAM.

## B. Target server (real GPUs)
- Everything UNKNOWN until we get access (OS, driver, CUDA, Docker, nvidia-container-toolkit, ComfyUI, disk, network).
- Expected GPUs: 5 × GTX 1080 Ti (11 GB each) — NOT verified yet.
- Access to the target server: UNKNOWN / not available during Phases 1-3.
- When access is available, collect: `nvidia-smi`, `nvidia-smi -L`,
  `nvidia-smi --query-gpu=name,uuid,memory.total,driver_version,compute_cap --format=csv`,
  `lsb_release -a`, `uname -r`, `lscpu`, `free -h`, `df -h`, `docker --version`, `docker info | grep -i runtime`.

## C. Constraints (ASSUMPTIONS — owner to confirm/edit)
- Users expected: small team, roughly 1-5 concurrent users.
- UI languages: English first; UI must be RTL-ready and localization-ready (Persian planned, per spec section 15).
- Internet / mirrors: do NOT assume unrestricted access to Hugging Face, Docker Hub, PyPI, npm or GitHub.
  Design model download, dependency install and container image pulls to work with configurable mirrors
  and an offline/pre-seeded path (spec sections 74-75).
- Deployment: single server first (spec section 69), multi-node later.

## D. Open questions for the owner
- (Agent: list your unanswered questions here during the design phase.)
