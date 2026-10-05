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

Design-phase questions that block or de-risk later phases. Please answer what you can; anything unanswered stays a "needs verification" item.

### Target server access
1. When will we get access to the target server (hardware.md section B)? All real-GPU work (V-001..V-010) depends on it.
2. Do we have sudo/root on the target server? (Driver upgrades, Docker, nvidia-container-toolkit installation.)
3. Is the 5 × GTX 1080 Ti configuration confirmed, or could it differ (spec section 4 lists other possibilities)?

### Driver / CUDA / runtime
4. What NVIDIA driver version is installed (or will be)? ADR-0006 recommends PyTorch 2.7.x+cu126, which requires Linux driver >= 560.28.03; if the driver is older we fall back to the cu118 build (driver >= 520.61.05) or upgrade the driver.
5. Is Docker + nvidia-container-toolkit acceptable for deployment, or must everything run bare-metal?
6. Is ComfyUI already installed on the target server? If yes, which version and which PyTorch does it pin? (Must be compatible with ADR-0006 — V-006.)

### Network / mirrors
7. Which package/model/container mirrors are reachable from the target server (PyPI, npm, Docker Hub, Hugging Face, GitHub)? hardware.md section C says do NOT assume unrestricted access — we need the actual list to configure mirrors and offline pre-seeding.
8. Is there a proxy, and what is its config?

### Capacity / operations
9. What is the target server's disk capacity and free space? (Model files are large — a single SDXL is ~6-7 GB; Flux is 20+ GB. Affects which models are installable, spec section 38 disk-space validation.)
10. Is the 1-5 concurrent user assumption (hardware.md section C) still correct?
11. What backup storage is available for DB + important assets (spec section 77)?

### People / process
12. Who administers the server day-to-day (for Phase 10 operations runbooks)?
13. Confirm the UI language plan: English first, Persian (RTL) later — any other languages needed for MVP?
