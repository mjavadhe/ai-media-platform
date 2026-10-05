"""Mock GPU backend — the mandatory CI/dev backend (ADR-0007, contract §18).

Simulates N nodes × M GPUs with configurable VRAM, utilization, health,
injected failures and OOM. Deterministic when seeded, which is what makes the
scheduler/queue/worker test suite reproducible without NVIDIA hardware.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import uuid
from dataclasses import dataclass, field
from typing import Any

from worker.gpu.base import (
    Capability,
    GpuBackend,
    GpuDeviceInfo,
    GpuMetrics,
    ProbeResult,
)


class MockGpuError(RuntimeError):
    """Injected execution failure."""


class MockGpuOomError(MockGpuError):
    """Injected out-of-memory failure — retryable in the retry taxonomy."""


@dataclass
class MockGpuSpec:
    """Declarative description of one simulated device."""

    device_id: str
    node_id: str
    node_name: str
    index: int
    name: str
    vram_total_mb: int
    compute_capability: str = "6.1"
    capabilities: dict[str, Any] = field(default_factory=dict)
    healthy: bool = True

    def uuid(self) -> str:
        digest = hashlib.sha1(self.device_id.encode()).hexdigest()  # noqa: S324
        return f"GPU-{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


DEFAULT_CAPABILITIES: dict[str, Any] = {
    Capability.TEXT_TO_IMAGE.value: True,
    Capability.IMAGE_TO_IMAGE.value: True,
    Capability.INPAINTING.value: True,
    Capability.CONTROLNET.value: True,
    Capability.LORA.value: True,
    Capability.UPSCALE.value: True,
    Capability.FP16.value: True,
    # bf16 is deliberately False: Pascal (sm_61) has no bf16 support (ADR-0006).
    Capability.BF16.value: False,
    Capability.ATTENTION_SLICE.value: True,
    Capability.VAE_TILING.value: True,
    Capability.VIDEO.value: False,
}


class MockGpuBackend(GpuBackend):
    """Fully functional simulated GPU cluster."""

    name = "mock"

    def __init__(
        self,
        *,
        node_count: int = 1,
        gpus_per_node: int = 2,
        vram_mb: int = 11_000,
        device_name: str = "MockGPU",
        seed: int | None = 1337,
        failure_rate: float = 0.0,
        oom_rate: float = 0.0,
        execution_seconds: float = 0.0,
        compute_capability: str = "6.1",
        extra_devices: list[MockGpuSpec] | None = None,
    ) -> None:
        # Seeded PRNG: fault injection must be reproducible, not cryptographically
        # secure, so `random` is the correct tool here (ruff S311).
        self._rng = random.Random(seed)  # noqa: S311
        self._failure_rate = failure_rate
        self._oom_rate = oom_rate
        self._execution_seconds = execution_seconds
        self._devices: dict[str, MockGpuSpec] = {}
        self._utilization: dict[str, float] = {}
        self._memory_used: dict[str, int] = {}
        self._calls: dict[str, int] = {}

        for spec in extra_devices or []:
            self._register(spec)
        for node_index in range(node_count):
            node_id = f"mock-node-{node_index}"
            node_name = f"mock-node-{node_index}"
            for gpu_index in range(gpus_per_node):
                # vary VRAM slightly so scheduling has a real choice to make
                vram = vram_mb - (gpu_index * 512 if gpus_per_node > 1 else 0)
                self._register(
                    MockGpuSpec(
                        device_id=f"{node_id}-gpu-{gpu_index}",
                        node_id=node_id,
                        node_name=node_name,
                        index=gpu_index,
                        name=f"{device_name} {gpu_index}",
                        vram_total_mb=vram,
                        compute_capability=compute_capability,
                        capabilities=dict(DEFAULT_CAPABILITIES),
                    )
                )

    # --- lifecycle -------------------------------------------------------
    def _register(self, spec: MockGpuSpec) -> None:
        self._devices[spec.device_id] = spec
        self._utilization[spec.device_id] = 0.0
        self._memory_used[spec.device_id] = 0
        self._calls[spec.device_id] = 0

    @property
    def devices(self) -> list[MockGpuSpec]:
        return list(self._devices.values())

    def node_ids(self) -> list[str]:
        seen: list[str] = []
        for spec in self._devices.values():
            if spec.node_id not in seen:
                seen.append(spec.node_id)
        return seen

    # --- discovery -------------------------------------------------------
    async def list_devices(self) -> list[GpuDeviceInfo]:
        return [
            GpuDeviceInfo(
                device_id=spec.device_id,
                uuid=spec.uuid(),
                index=spec.index,
                name=spec.name,
                vram_total_mb=spec.vram_total_mb,
                vram_free_mb=spec.vram_total_mb - self._memory_used[spec.device_id],
                compute_capability=spec.compute_capability,
                driver_version="mock-driver-0.0.0",
                utilization_percent=self._utilization[spec.device_id],
                memory_used_mb=self._memory_used[spec.device_id],
                temperature_c=42.0,
                power_w=120.0,
                healthy=spec.healthy,
                error_message=None if spec.healthy else "mock: device marked unhealthy",
                capabilities=dict(spec.capabilities),
                extra={"node_id": spec.node_id, "node_name": spec.node_name},
            )
            for spec in self._devices.values()
        ]

    async def get_metrics(self, device_id: str) -> GpuMetrics | None:
        spec = self._devices.get(device_id)
        if spec is None:
            return None
        return GpuMetrics(
            device_id=device_id,
            utilization_percent=self._utilization[device_id],
            memory_used_mb=self._memory_used[device_id],
            vram_total_mb=spec.vram_total_mb,
            temperature_c=42.0,
            power_w=120.0,
        )

    async def run_probe(self, device_id: str, probe: str) -> ProbeResult:
        spec = self._devices.get(device_id)
        if spec is None:
            return ProbeResult(capability=probe, supported=False, message="unknown device")
        if probe == "compute_capability":
            return ProbeResult(
                capability=probe,
                supported=True,
                value={"compute_capability": spec.compute_capability},
            )
        if probe == "capabilities":
            return ProbeResult(
                capability=probe,
                supported=True,
                value={"capabilities": dict(spec.capabilities)},
            )
        if probe == "memory_bandwidth":
            return ProbeResult(capability=probe, supported=True, value={"gb_per_second": 336.0})
        return ProbeResult(capability=probe, supported=False, message=f"unknown probe {probe!r}")

    # --- test helpers ----------------------------------------------------
    def set_healthy(self, device_id: str, healthy: bool) -> None:
        self._devices[device_id].healthy = healthy

    def set_capability(self, device_id: str, capability: str, value: Any) -> None:
        self._devices[device_id].capabilities[capability] = value

    def set_failure_rates(
        self, *, failure_rate: float | None = None, oom_rate: float | None = None
    ) -> None:
        if failure_rate is not None:
            self._failure_rate = failure_rate
        if oom_rate is not None:
            self._oom_rate = oom_rate

    def set_execution_seconds(self, seconds: float) -> None:
        self._execution_seconds = seconds

    def call_count(self, device_id: str | None = None) -> int:
        if device_id is not None:
            return self._calls[device_id]
        return sum(self._calls.values())

    # --- execution -------------------------------------------------------
    async def execute(
        self,
        device_id: str,
        payload: dict[str, Any],
        *,
        on_progress: Any = None,
    ) -> dict[str, Any]:
        spec = self._devices.get(device_id)
        if spec is None:
            raise MockGpuError(f"unknown mock device {device_id!r}")
        if not spec.healthy:
            raise MockGpuError(f"device {device_id!r} is unhealthy")

        self._calls[device_id] += 1
        required_vram = int(payload.get("required_vram_mb", 0) or 0)
        if required_vram > spec.vram_total_mb:
            raise MockGpuOomError(
                f"mock OOM: job needs {required_vram} MB, device has {spec.vram_total_mb} MB"
            )

        if self._oom_rate and self._rng.random() < self._oom_rate:
            raise MockGpuOomError("mock OOM (injected)")
        if self._failure_rate and self._rng.random() < self._failure_rate:
            raise MockGpuError("mock execution failure (injected)")

        self._memory_used[device_id] = min(required_vram, spec.vram_total_mb)
        self._utilization[device_id] = 95.0
        steps = max(1, int(payload.get("steps", 4)))
        try:
            for step in range(1, steps + 1):
                if on_progress is not None:
                    await on_progress(step / steps * 100.0, f"mock step {step}/{steps}")
                if self._execution_seconds:
                    await asyncio.sleep(self._execution_seconds / steps)
        finally:
            self._memory_used[device_id] = 0
            self._utilization[device_id] = 0.0

        seed = payload.get("seed")
        if seed is None:
            seed = int.from_bytes(
                hashlib.sha256(f"{device_id}:{payload.get('prompt', '')}".encode()).digest()[:4],
                "big",
            )

        outputs: list[dict[str, Any]] = []
        for index, mime in enumerate(self._output_mime_types(payload)):
            outputs.append(
                {
                    "kind": "file",
                    "mime_type": mime,
                    "filename": f"mock-{uuid.uuid4().hex[:12]}-{index}.{mime.rsplit('/', 1)[-1]}",
                    "content": self._placeholder_bytes(mime, seed, index),
                    "metadata": {
                        "device_id": device_id,
                        "device_name": spec.name,
                        "compute_capability": spec.compute_capability,
                        "seed": seed,
                        "steps": steps,
                        "backend": "mock",
                        "deterministic": True,
                    },
                }
            )

        return {
            "outputs": outputs,
            "runtime_info": {
                "backend": "mock",
                "device_id": device_id,
                "device_name": spec.name,
                "compute_capability": spec.compute_capability,
                "seed": seed,
                "steps": steps,
            },
        }

    @staticmethod
    def _output_mime_types(payload: dict[str, Any]) -> list[str]:
        job_type = str(payload.get("job_type", "IMAGE")).upper()
        count = int(payload.get("output_count", 1))
        if job_type == "VIDEO":
            mime = "video/mp4"
        elif job_type == "AUDIO":
            mime = "audio/wav"
        else:
            mime = str(payload.get("output_mime_type", "image/png"))
        return [mime] * max(1, count)

    @staticmethod
    def _placeholder_bytes(mime: str, seed: int, index: int) -> bytes:
        """Deterministic placeholder payload (no real image encoding required)."""
        header = f"MOCK-MEDIA\nmime={mime}\nseed={seed}\nindex={index}\n".encode()
        filler = hashlib.sha256(f"{seed}:{index}".encode()).digest() * 64
        return header + filler
