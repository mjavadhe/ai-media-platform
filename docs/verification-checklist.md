# Real-GPU Verification Checklist

Items that cannot be verified on the dev machine (no NVIDIA GPU) and must be run on the target server.
Each item references the code/feature marked "NEEDS REAL-GPU VERIFICATION".

Status values: TODO | PASSED | FAILED | BLOCKED

| ID | Area | What to verify | How | Status | Notes |
|----|------|----------------|-----|--------|-------|
| V-001 | Environment | Driver/CUDA/OS versions recorded in docs/hardware.md section B | Run commands listed in hardware.md | TODO | |
| V-002 | Environment | Docker + nvidia-container-toolkit work (`docker run --gpus all ... nvidia-smi`) | Run on target | TODO | |
| V-003 | PyTorch | Chosen PyTorch/CUDA build supports Pascal (compute capability 6.1) | Install pinned version, run `torch.cuda.is_available()` and a small matmul on each GPU | TODO | See ADR on PyTorch/CUDA versions |
| V-004 | GPU discovery | Real NVML/nvidia-smi backend reports all GPUs with UUID, VRAM, compute cap | Run worker discovery, compare with `nvidia-smi` | TODO | |
| V-005 | GPU metrics | Utilization/VRAM/temperature/power values match nvidia-smi | Compare under idle and load | TODO | |
| V-006 | Engine | ComfyUI starts and runs a basic image workflow on one 1080 Ti | Manual spike before Phase 5 | TODO | |
| V-007 | Models | At least one image model fits and runs within 11 GB VRAM | Spike; record resolution, precision, time | TODO | |
| V-008 | OOM handling | A deliberately oversized job produces a classified OOM failure and is not retried forever | Run oversized job | TODO | |
| V-009 | Multi-GPU | Two parallel jobs run on two different GPUs without interference | Run 2 concurrent jobs | TODO | |
| V-010 | Recovery | Killing the worker marks the node OFFLINE and re-queues retryable jobs | Kill worker mid-job | TODO | |
