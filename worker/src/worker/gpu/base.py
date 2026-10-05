"""GPU backend interface (contract section 17, doc 06).

Deliberately **no** allocation API. The port exposes discovery, inspection and
execution only; GPU placement/reservation/release belongs exclusively to the
Scheduler (ADR-0007, contract section 5.1). Keeping `allocate`/`reserve`/
`release` off this interface is the code-level guarantee that a worker can never
allocate a GPU.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Capability(StrEnum):
    """Capability keys consumed by the registry + probes (AGENTS.md: data-driven)."""

    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"
    INPAINTING = "inpainting"
    CONTROLNET = "controlnet"
    LORA = "lora"
    VIDEO = "video"
    UPSCALE = "upscale"
    FP16 = "fp16"
    BF16 = "bf16"
    ATTENTION_SLICE = "attention_slicing"
    VAE_TILING = "vae_tiling"


@dataclass(frozen=True, slots=True)
class GpuDeviceInfo:
    """A device as reported by a backend (facts, never placement)."""

    device_id: str
    uuid: str
    index: int
    name: str
    vram_total_mb: int
    vram_free_mb: int
    compute_capability: str | None = None
    driver_version: str | None = None
    utilization_percent: float = 0.0
    memory_used_mb: int = 0
    temperature_c: float | None = None
    power_w: float | None = None
    healthy: bool = True
    error_message: str | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def supports(self, capability: str) -> bool:
        value = self.capabilities.get(capability)
        if isinstance(value, bool):
            return value
        if isinstance(value, dict):
            return bool(value.get("supported", True))
        return str(capability) in self.capabilities


@dataclass(frozen=True, slots=True)
class GpuMetrics:
    device_id: str
    utilization_percent: float
    memory_used_mb: int
    vram_total_mb: int
    temperature_c: float | None = None
    power_w: float | None = None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Outcome of a capability probe — recorded with ``source=PROBE``."""

    capability: str
    supported: bool
    value: dict[str, Any] = field(default_factory=dict)
    message: str | None = None


class GpuBackend(abc.ABC):
    """Discovery / metrics / probe / execute. **Never** allocation."""

    name: str = "abstract"

    @abc.abstractmethod
    async def list_devices(self) -> list[GpuDeviceInfo]:
        """All devices visible to this backend."""

    async def get_device(self, device_id: str) -> GpuDeviceInfo | None:
        for device in await self.list_devices():
            if device.device_id == device_id or device.uuid == device_id:
                return device
        return None

    @abc.abstractmethod
    async def get_metrics(self, device_id: str) -> GpuMetrics | None: ...

    async def get_capabilities(self, device_id: str) -> dict[str, Any]:
        device = await self.get_device(device_id)
        return dict(device.capabilities) if device else {}

    @abc.abstractmethod
    async def run_probe(self, device_id: str, probe: str) -> ProbeResult: ...

    @abc.abstractmethod
    async def execute(
        self,
        device_id: str,
        payload: dict[str, Any],
        *,
        on_progress: Any = None,
    ) -> dict[str, Any]:
        """Run a workload on a device and return produced artifact descriptors.

        ``on_progress`` is an optional async callback ``(percent, message)``.
        """

    async def health(self) -> dict[str, Any]:
        """Backend-level health probe used by /ready and node status."""
        devices = await self.list_devices()
        return {"backend": self.name, "ok": True, "device_count": len(devices)}

    async def close(self) -> None:
        """Release backend resources (NVML shutdown, HTTP clients, ...)."""
        return None


def assert_no_allocation_api(backend: GpuBackend) -> None:
    """Guard used by tests: the port must never grow allocation methods."""
    forbidden = {"allocate", "reserve", "release", "free", "deallocate", "assign"}
    present = forbidden & {name for name in dir(backend) if not name.startswith("_")}
    if present:
        raise AssertionError(f"GpuBackend must not expose allocation API, found: {sorted(present)}")
