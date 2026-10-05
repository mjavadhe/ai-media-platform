"""GPU backend registry — pluggable discovery/execution backend (ADR-0007).

``auto`` prefers NVML when a driver is visible and falls back to the mock, which
is what lets the dev machine (no NVIDIA GPU) run the whole stack. No hard-coded
GPU names, counts or paths: everything comes from configuration (AGENTS.md).
"""

from __future__ import annotations

from worker.gpu.base import GpuBackend
from worker.gpu.mock_backend import MockGpuBackend, MockGpuSpec
from worker.gpu.nvml_backend import NvmlGpuBackend, capabilities_for


def build_gpu_backend(
    backend: str,
    *,
    mock_nodes: int = 1,
    mock_gpus_per_node: int = 2,
    mock_vram_mb: int = 11_000,
    extra_devices: list[MockGpuSpec] | None = None,
) -> GpuBackend:
    """Instantiate a backend by name.

    ``backend`` is one of ``auto`` | ``mock`` | ``nvml``.
    """
    kind = backend.strip().lower()
    if kind == "mock":
        return MockGpuBackend(
            node_count=mock_nodes,
            gpus_per_node=mock_gpus_per_node,
            vram_mb=mock_vram_mb,
            extra_devices=extra_devices,
        )
    if kind == "nvml":
        return NvmlGpuBackend()
    if kind == "auto":
        nvml = NvmlGpuBackend()
        if nvml.available:
            return nvml
        return MockGpuBackend(
            node_count=mock_nodes,
            gpus_per_node=mock_gpus_per_node,
            vram_mb=mock_vram_mb,
            extra_devices=extra_devices,
        )
    raise ValueError(f"unknown GPU backend {backend!r} (expected auto, mock or nvml)")


__all__ = [
    "GpuBackend",
    "MockGpuBackend",
    "MockGpuSpec",
    "NvmlGpuBackend",
    "build_gpu_backend",
    "capabilities_for",
]
