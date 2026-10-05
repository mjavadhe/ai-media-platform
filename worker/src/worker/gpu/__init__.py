"""GPU backends for the worker.

The port (:mod:`worker.gpu.base`) intentionally has **no** allocation API so a
worker can never allocate a GPU (ADR-0007). Placement is the Scheduler's job.
"""

from worker.gpu.base import (
    Capability,
    GpuBackend,
    GpuDeviceInfo,
    GpuMetrics,
    ProbeResult,
    assert_no_allocation_api,
)
from worker.gpu.mock_backend import MockGpuBackend, MockGpuError, MockGpuOomError, MockGpuSpec
from worker.gpu.nvml_backend import CAPABILITY_REGISTRY, NvmlGpuBackend, capabilities_for
from worker.gpu.registry import build_gpu_backend

__all__ = [
    "CAPABILITY_REGISTRY",
    "Capability",
    "GpuBackend",
    "GpuDeviceInfo",
    "GpuMetrics",
    "MockGpuBackend",
    "MockGpuError",
    "MockGpuOomError",
    "MockGpuSpec",
    "NvmlGpuBackend",
    "ProbeResult",
    "assert_no_allocation_api",
    "build_gpu_backend",
    "capabilities_for",
]
