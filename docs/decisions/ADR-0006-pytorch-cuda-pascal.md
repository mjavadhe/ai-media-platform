# ADR-0006: PyTorch / CUDA Versions for Pascal (GTX 1080 Ti, sm_61)

- Status: Proposed
- Date: 2026-10-05 (rewritten with current release data; supersedes the 2026-10-05 initial draft's version claims)

## Context

The initial GPUs are GTX 1080 Ti (Pascal, compute capability 6.1, 11 GB, no tensor cores, no bf16 — AGENTS.md). The worker runs PyTorch-based inference (ComfyUI/diffusers engines, spec sections 41-42). AGENTS.md requires verifying PyTorch/CUDA compatibility with Pascal before choosing versions and recording it in this ADR. Every claim below is cited to an official source; anything unconfirmable is marked **needs verification** and tracked in `docs/verification-checklist.md`.

## Established facts (cited)

1. **Currently supported PyTorch versions: 2.7 through 2.14.** The official release compatibility matrix lists stable CUDA builds for 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, and 2.14. The latest stable release is **2.14.0 (2026-09-02)**; 2.15 is scheduled for 2026-10-28. [Sources: PyTorch RELEASE.md support matrix](https://github.com/pytorch/pytorch/blob/main/RELEASE.md), [PyTorch 2.14 release blog](https://pytorch.org/blog/pytorch-2-14-release-blog/), [Wikipedia stable-release table](https://en.wikipedia.org/wiki/PyTorch)]
2. **CUDA builds that still include Pascal (sm_60/sm_61): cu118 and cu126 only.**
   - cu118: stable builds for PyTorch 2.7 and earlier (2.7 stable CUDA: 11.8, 12.6). [Source: RELEASE.md matrix]
   - cu126: the RELEASE.md architecture table states for releases 2.12-2.14, "CUDA 12.6.3: Maxwell(5.0), **Pascal(6.0)**, Volta(7.0), Turing(7.5), Ampere(8.0, 8.6), Hopper(9.0)". The PyTorch dev-discuss policy announcement confirms cu126 is the retained line for Pascal: "If you need support for these architectures, please utilize CUDA 12.6 instead." [Sources: RELEASE.md](https://github.com/pytorch/pytorch/blob/main/RELEASE.md), [PyTorch dev-discuss](https://dev-discuss.pytorch.org/t/cuda-toolkit-version-and-architecture-support-update-maxwell-and-pascal-architecture-support-removed-in-cuda-12-8-and-12-9-builds/3128)
   - **cu128/cu129: Pascal dropped starting with PyTorch 2.8.** Official release notes: "support for sm50 - sm60 architectures with CUDA 12.8 and 12.9 has been dropped for the 2.8.0 release." [Sources: PyTorch GitHub releases](https://github.com/pytorch/pytorch/releases), [dev-discuss](https://dev-discuss.pytorch.org/t/cuda-toolkit-version-and-architecture-support-update-maxwell-and-pascal-architecture-support-removed-in-cuda-12-8-and-12-9-builds/3128)
   - **cu130/cu132: no Pascal.** These are stable builds for 2.11-2.14; the 2.13 release notes confirm the CUDA 12.8/12.9 build variants were removed entirely for 2.13+, leaving 12.6 as the only Pascal-capable line. [Sources: RELEASE.md](https://github.com/pytorch/pytorch/blob/main/RELEASE.md), [PyTorch 2.13 release blog](https://pytorch.org/blog/pytorch-2-13-release-blog/)
3. **sm_60 cubins run on sm_61 hardware.** NVIDIA's Pascal Compatibility Guide: a cubin targeting compute capability X.Y is supported on all compute capability X.Z devices (same major version). PyTorch's own capability check implements the same rule ("NVIDIA GPU compute architectures are backward compatible within major version"). [Sources: NVIDIA Pascal Compatibility Guide (CUDA 12.6 archive)](https://docs.nvidia.com/cuda/archive/12.6.1/pascal-compatibility-guide/index.html), [PyTorch torch/cuda source](https://github.com/pytorch/pytorch/blob/main/torch/cuda/__init__.py)
4. **Current ComfyUI requires PyTorch >= 2.7.** Official system requirements: "PyTorch 2.7 and above is supported, but using a newer version is strongly recommended. A cu130 or above version of PyTorch is required on Nvidia 20 series and above." ComfyUI's `requirements.txt` leaves `torch` unpinned. [Sources: ComfyUI system requirements](https://docs.comfy.org/installation/system_requirements), [ComfyUI requirements.txt](https://github.com/Comfy-Org/ComfyUI/blob/main/requirements.txt)]
5. **bf16 is not available on Pascal.** The PyTorch CUDA backend supports bfloat16 on Ampere or newer GPUs; on unsupported devices `torch.cuda.is_bf16_supported()` returns False and autocast raises "Current CUDA Device does not support bfloat16. Please switch dtype to float16." [Sources: PyTorch AMP documentation](https://docs.pytorch.org/docs/stable/amp.html), [PyTorch forum confirmation quoting the docs](https://discuss.pytorch.org/t/question-about-bfloat16-operations-in-amp-and-cuda/206078)
6. **Pascal has no tensor cores**, so TF32 and bf16 acceleration are unavailable; fp16 runs on CUDA cores. [Source: CUDA Programming Guide, compute-capability feature table](https://docs.nvidia.com/cuda/cuda-programming-guide/05-appendices/compute-capabilities.html)
7. **Driver minimums:** cu126 requires Linux driver >= 560.28.03 (GA; >= 560.35.05 for 12.6 Update 3); cu118 requires Linux driver >= 520.61.05. [Source: CUDA 12.6 release notes](https://docs.nvidia.com/cuda/archive/12.6.0/cuda-toolkit-release-notes/index.html), [CUDA 11.8 driver table](https://docs.nvidia.com/cuda/archive/12.6.0/cuda-toolkit-release-notes/index.html)]
8. **Python:** PyTorch 2.7-2.14 all support Python 3.10+; 2.14 requires >= 3.10. Python 3.12 is supported across the whole viable range. [Source: RELEASE.md](https://github.com/pytorch/pytorch/blob/main/RELEASE.md)

## Viable combinations for Pascal + current ComfyUI

| Combination | Pascal support | ComfyUI >= 2.7 | Driver (Linux) | Verdict |
|-------------|---------------|----------------|----------------|---------|
| PyTorch 2.7.x + cu118 | Yes (fact 2) | Yes (minimum) | >= 520.61.05 | Viable |
| PyTorch 2.7.x + cu126 | Yes (facts 2-3) | Yes | >= 560.28.03 | **Recommended** |
| PyTorch 2.8.x - 2.14.x + cu126 | Yes (facts 2-3) | Yes | >= 560.28.03 | Viable (newer features) |
| Any 2.8+ with cu128/cu129/cu130/cu132 | **No** (fact 2) | — | — | Rejected for Pascal |
| Any 2.6 or older + any CUDA | Yes | **No** — below ComfyUI minimum (fact 4) | — | Rejected |
| bf16 anywhere | — | — | — | Impossible on Pascal (fact 5) |

## Options considered

1. **PyTorch 2.7.x + cu126 (recommended)** — the minimum PyTorch line that current ComfyUI supports, on the CUDA line that retains Pascal longest.
   - Pros: satisfies ComfyUI's >= 2.7 requirement with the widest margin; cu126 is the Pascal-retained build across all PyTorch versions (fact 2); driver requirement (560.28.03) is modest.
   - Cons: not the newest PyTorch; some newer ComfyUI optimizations may prefer newer torch (ComfyUI docs note "some features and optimizations might only work on newer versions").
2. **PyTorch 2.14.x + cu126** — newest stable (2026-09-02) with Pascal.
   - Pros: newest features; ComfyUI "strongly recommends" newer versions.
   - Cons: only ~1 month old at time of writing; ComfyUI itself cautions against versions "less than 2 weeks old" only in the context of latest-CUDA pairing — for Pascal, cu126 is mandatory regardless of torch version. **Needs verification** that 2.14+cu126 runs the target models on Pascal (V-003).
3. **PyTorch 2.7.x + cu118** — fallback if the target driver cannot reach 560.28.03.
   - Pros: lower driver bar (520.61.05); cu118 builds of 2.4.1/2.5.1 are community-confirmed to include sm_61.
   - Cons: older CUDA; same torch version as option 1 otherwise.
4. **PyTorch 2.6 or older** — rejected: below ComfyUI's minimum (fact 4).
5. **cu128+ on any version** — rejected: no Pascal (fact 2).

## Decision

**Classification: candidate / documentation-aligned / PENDING REAL PASCAL VALIDATION.** No version combination below has been executed on real GTX 1080 Ti hardware; nothing in this ADR is HARDWARE-VALIDATED.

**PyTorch 2.7.x with the cu126 wheel** (option 1) as the primary pin, with **cu118** (option 3) as the documented driver fallback and **2.14.x+cu126** (option 2) as the documented upgrade path. Python 3.12 (ADR-0005). **fp16 only** — no bf16, no TF32, no tensor-core paths. The exact patch version is pinned in `uv.lock` and marked **NEEDS REAL-GPU VERIFICATION** (V-003): on the target server, run `torch.cuda.is_available()`, `torch.cuda.get_device_capability()` (expect `(6, 1)`), `torch.cuda.get_arch_list()` (expect sm_60/sm_61 coverage), and a small CUDA matmul on each GPU before any model work.

## Consequences

- Easier: a single CUDA line (cu126) works for every PyTorch version from 2.7 to 2.14, so the torch version can be upgraded without changing CUDA; fp16-only simplifies the model registry's precision field.
- Harder: the target driver must be >= 560.28.03 for cu126 (fact 7) — if it is older, either upgrade the driver or use the cu118 fallback (driver >= 520.61.05).
- Must verify later: V-001 (driver/CUDA/OS versions), V-003 (PyTorch pin works on real Pascal GPUs), V-006 (ComfyUI runs on this stack), V-007 (model fits in 11 GB).
