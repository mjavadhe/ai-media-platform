"""NVML GPU backend — the real backend (contract section 17, doc 17).

Two independent code paths, both behind :class:`GpuBackend`:

* ``pynvml`` when the binding is installed and a driver is present,
* ``nvidia-smi --query-gpu`` parsing as a dependency-free fallback.

The backend is import-safe on machines without NVIDIA hardware: constructing it
never fails, it simply reports zero devices and ``available == False``. It also
never allocates — placement is the Scheduler's job (ADR-0007).

NEEDS REAL-GPU VERIFICATION (V-004, V-005): metric field names, power readings
and probe results can only be confirmed against real Pascal hardware.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from typing import Any

from worker.gpu.base import (
    Capability,
    GpuBackend,
    GpuDeviceInfo,
    GpuMetrics,
    ProbeResult,
)

#: registry mapping device properties to capability keys — data-driven, never
#: an if-statement on a GPU name (AGENTS.md).
CAPABILITY_REGISTRY: dict[str, dict[str, Any]] = {
    "6.1": {  # Pascal (GTX 1080 Ti class)
        Capability.TEXT_TO_IMAGE.value: True,
        Capability.FP16.value: True,
        Capability.BF16.value: False,
        Capability.ATTENTION_SLICE.value: True,
        Capability.VAE_TILING.value: True,
    },
    "7.0": {  # Volta
        Capability.TEXT_TO_IMAGE.value: True,
        Capability.FP16.value: True,
        Capability.BF16.value: False,
    },
    "7.5": {  # Turing
        Capability.TEXT_TO_IMAGE.value: True,
        Capability.FP16.value: True,
        Capability.BF16.value: False,
        Capability.CONTROLNET.value: True,
    },
    "8.0": {  # Ampere
        Capability.TEXT_TO_IMAGE.value: True,
        Capability.FP16.value: True,
        Capability.BF16.value: True,
        Capability.CONTROLNET.value: True,
    },
    "8.6": {  # Ampere consumer
        Capability.TEXT_TO_IMAGE.value: True,
        Capability.FP16.value: True,
        Capability.BF16.value: True,
    },
    "9.0": {  # Hopper
        Capability.TEXT_TO_IMAGE.value: True,
        Capability.FP16.value: True,
        Capability.BF16.value: True,
        Capability.CONTROLNET.value: True,
    },
}


def capabilities_for(compute_capability: str | None) -> dict[str, Any]:
    """Resolve capabilities from a compute capability, defaulting conservatively."""
    if not compute_capability:
        return {}
    entry = CAPABILITY_REGISTRY.get(compute_capability.strip())
    return dict(entry) if entry else {}


def _safe(default: Any, func: Any, *args: Any, **kwargs: Any) -> Any:
    """Best-effort NVML/driver call.

    NVML surfaces a wide and version-dependent exception surface (RuntimeError,
    NVMLError subclasses, OSError). Rather than enumerate them — which would be
    a moving target across driver versions — a failure degrades to ``default``
    and the field is simply reported as unavailable.
    """
    try:
        return func(*args, **kwargs)
    except Exception:  # noqa: BLE001 - see docstring
        return default


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


class NvmlGpuBackend(GpuBackend):
    """Real GPU backend. Degrades to empty discovery when NVML is unavailable."""

    name = "nvml"

    def __init__(self, *, prefer_nvml: bool = True) -> None:
        self._pynvml: Any = None
        self._nvml_ok = False
        self._handles: dict[str, Any] = {}
        self._smi_path = shutil.which("nvidia-smi")
        if prefer_nvml:
            self._try_init_nvml()

    def _try_init_nvml(self) -> None:
        try:
            import pynvml  # type: ignore[import-not-found]

            pynvml.nvmlInit()
        except Exception:  # noqa: BLE001 - no driver / no binding / no permission
            self._pynvml = None
            self._nvml_ok = False
        else:
            self._pynvml = pynvml
            self._nvml_ok = True

    @property
    def available(self) -> bool:
        return self._nvml_ok or self._smi_path is not None

    async def list_devices(self) -> list[GpuDeviceInfo]:
        if self._nvml_ok:
            return await asyncio.to_thread(self._list_devices_nvml)
        if self._smi_path:
            return await asyncio.to_thread(self._list_devices_smi)
        return []

    def _list_devices_nvml(self) -> list[GpuDeviceInfo]:
        assert self._pynvml is not None
        nvml = self._pynvml
        devices: list[GpuDeviceInfo] = []
        count = _safe(0, nvml.nvmlDeviceGetCount)
        if not isinstance(count, int) or count <= 0:
            return []
        for index in range(count):
            try:
                handle = nvml.nvmlDeviceGetHandleByIndex(index)
                self._handles[str(index)] = handle
                name = _decode(nvml.nvmlDeviceGetName(handle))
                uuid_str = _decode(nvml.nvmlDeviceGetUUID(handle))
                memory = nvml.nvmlDeviceGetMemoryInfo(handle)
                capability = _safe(None, nvml.nvmlDeviceGetCudaComputeCapability, handle)
                compute_capability = f"{capability[0]}.{capability[1]}" if capability else None
                utilization = _safe(None, nvml.nvmlDeviceGetUtilizationRates, handle)
                util_pct = float(getattr(utilization, "gpu", 0) or 0)
                temperature = _safe(
                    None,
                    lambda h=handle: float(
                        nvml.nvmlDeviceGetTemperature(h, nvml.NVML_TEMPERATURE_GPU)
                    ),
                )
                power_mw = _safe(None, nvml.nvmlDeviceGetPowerUsage, handle)
                power = power_mw / 1000.0 if power_mw is not None else None
                driver_version = _decode(nvml.nvmlSystemGetDriverVersion()) if index == 0 else None
                devices.append(
                    GpuDeviceInfo(
                        device_id=str(index),
                        uuid=uuid_str,
                        index=index,
                        name=str(name),
                        vram_total_mb=int(memory.total) // (1024 * 1024),
                        vram_free_mb=int(memory.free) // (1024 * 1024),
                        compute_capability=compute_capability,
                        driver_version=driver_version,
                        utilization_percent=util_pct,
                        memory_used_mb=int(memory.used) // (1024 * 1024),
                        temperature_c=temperature,
                        power_w=power,
                        healthy=True,
                        capabilities=capabilities_for(compute_capability),
                        extra={"source": "nvml"},
                    )
                )
            except Exception:  # noqa: BLE001,S112 - one bad device must not hide the rest
                continue
        return devices

    def _list_devices_smi(self) -> list[GpuDeviceInfo]:
        import subprocess

        fields = (
            "index,uuid,name,memory.total,memory.used,memory.free,"
            "utilization.gpu,temperature.gpu,power.draw,compute_cap"
        )
        result = _safe(
            None,
            lambda: subprocess.run(  # noqa: S603
                [str(self._smi_path), f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            ),
        )
        if result is None or result.returncode != 0:
            return []
        driver_version = self._smi_driver_version()
        devices: list[GpuDeviceInfo] = []
        for line in result.stdout.strip().splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 11:
                continue
            try:
                index = int(float(parts[0]))
                compute_capability = parts[10]
                devices.append(
                    GpuDeviceInfo(
                        device_id=str(index),
                        uuid=parts[1],
                        index=index,
                        name=parts[2],
                        vram_total_mb=int(float(parts[3])),
                        vram_free_mb=int(float(parts[5])),
                        memory_used_mb=int(float(parts[4])),
                        utilization_percent=float(parts[6]),
                        temperature_c=float(parts[7]),
                        power_w=float(parts[8]) if parts[8] not in {"", "[N/A]"} else None,
                        compute_capability=compute_capability,
                        driver_version=driver_version,
                        healthy=True,
                        capabilities=capabilities_for(compute_capability),
                        extra={"source": "nvidia-smi"},
                    )
                )
            except (ValueError, IndexError):
                continue
        return devices

    def _smi_driver_version(self) -> str | None:
        import subprocess

        if not self._smi_path:
            return None
        result = _safe(
            None,
            lambda: subprocess.run(  # noqa: S603
                [str(self._smi_path), "--query-gpu=driver_version", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            ),
        )
        if result is None:
            return None
        lines = result.stdout.strip().splitlines()
        return lines[0] if lines else None

    async def get_metrics(self, device_id: str) -> GpuMetrics | None:
        device = await self.get_device(device_id)
        if device is None:
            return None
        return GpuMetrics(
            device_id=device.device_id,
            utilization_percent=device.utilization_percent,
            memory_used_mb=device.memory_used_mb,
            vram_total_mb=device.vram_total_mb,
            temperature_c=device.temperature_c,
            power_w=device.power_w,
        )

    async def run_probe(self, device_id: str, probe: str) -> ProbeResult:
        device = await self.get_device(device_id)
        if device is None:
            return ProbeResult(capability=probe, supported=False, message="unknown device")
        if probe == "compute_capability":
            return ProbeResult(
                capability=probe,
                supported=True,
                value={"compute_capability": device.compute_capability},
            )
        if probe == "capabilities":
            return ProbeResult(
                capability=probe, supported=True, value={"capabilities": device.capabilities}
            )
        if probe == "cuda_matmul" and self._nvml_ok:
            return ProbeResult(
                capability=probe,
                supported=True,
                value={"note": "requires torch; see docs/hardware.md verification checklist"},
            )
        return ProbeResult(
            capability=probe, supported=False, message=f"probe {probe!r} not available"
        )

    async def execute(
        self,
        device_id: str,
        payload: dict[str, Any],
        *,
        on_progress: Any = None,
    ) -> dict[str, Any]:
        """Real execution requires an engine adapter (ComfyUI/diffusers).

        The NVML backend itself only manages the device; it never loads a model.
        NEEDS REAL-GPU VERIFICATION (V-003/V-006).
        """
        device = await self.get_device(device_id)
        if device is None:
            raise RuntimeError(f"unknown device {device_id!r}")
        if on_progress is not None:
            await on_progress(0.0, "nvml backend delegates execution to an engine adapter")
        raise RuntimeError(
            "NvmlGpuBackend.execute requires an engine adapter "
            "(ComfyUI/diffusers). Configure the worker with "
            "APP_WORKER_EXECUTION_BACKEND=comfyui for real inference. "
            "NEEDS REAL-GPU VERIFICATION."
        )

    async def health(self) -> dict[str, Any]:
        devices = await self.list_devices()
        return {
            "backend": self.name,
            "ok": bool(devices),
            "available": self.available,
            "nvml": self._nvml_ok,
            "nvidia_smi": self._smi_path is not None,
            "device_count": len(devices),
            "detail": None
            if devices
            else json.dumps({"reason": "no NVIDIA driver visible on this host"}),
        }

    async def close(self) -> None:
        if self._nvml_ok and self._pynvml is not None:
            with contextlib.suppress(Exception):
                self._pynvml.nvmlShutdown()
            self._nvml_ok = False
        self._handles.clear()
