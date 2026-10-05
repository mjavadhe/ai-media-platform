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
| V-011 | Models | SD 1.5 (fp16, 512×512) fits and runs within 11 GB on a 1080 Ti | Spike: record resolution, precision, VRAM, time per image | TODO | Estimates in docs/design/09 are unconfirmed until this passes |
| V-012 | Models | SDXL (fp16, 1024×1024) feasibility on 11 GB (with/without --lowvram) | Spike: record settings that work or fail | TODO | Borderline per ADR-0006 feasibility table |
| V-013 | Models | At least one image-to-video model (AnimateDiff-class) runs within 11 GB | Spike after MVP image generation is stable | TODO | Video is post-MVP per spec §99 |
| V-014 | Queue | Phase 1-3 queue implementation (Postgres SKIP LOCKED per ADR-0002) meets spec §64 features | Integration test run on target server | TODO | Provisional choice — owner confirms ADR-0002 first |
| V-015 | Storage | Storage abstraction works with the target server's disk/MinIO (if provided) | Upload/download/delete + path traversal rejection test | TODO | |
| V-016 | Realtime | SSE streams survive the target deployment's proxy (if any) | Deploy behind proxy, verify no buffering | TODO | ADR-0003 consequence |
| V-017 | ComfyUI | ComfyUI runs on the ADR-0006 pin (PyTorch 2.7.x+cu126); ComfyUI requires torch >= 2.7 | Check ComfyUI requirements on target | TODO | Blocks Phase 5 if incompatible |
| V-018 | Environment | PostgreSQL version on target is supported by the pinned stack | Run migrations against target Postgres | TODO | ADR-0008 |
