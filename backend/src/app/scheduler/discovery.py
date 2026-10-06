"""GPU discovery: reconcile backend telemetry into ``gpu_nodes``/``gpu_devices``.

Only the Scheduler runs discovery, and it is the only writer of
``gpu_devices.status``. Discovery is split from scheduling so a failing probe
can never take the scheduler loop down.

Device identity is the backend's UUID, not its index: index 0 on a rebooted host
may be a different physical card. The index is stored for display and for the
round-robin ordering only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import CapabilitySource, GpuStatus
from app.infrastructure.logging import get_logger
from app.persistence.models_job import GpuDevice
from app.persistence.repositories.common import utcnow
from app.persistence.repositories.gpu import GpuRepository
from worker.gpu.base import GpuBackend, GpuDeviceInfo

logger = get_logger(__name__)

#: Probes run during discovery. Capability facts come from the backend registry
#: first; a probe refines the value and is recorded with source=PROBE.
DEFAULT_PROBES = ("compute_capability", "capabilities")


def _as_supported(value: Any) -> bool:
    """Capability values arrive as either a bool or a ``{"supported": bool}`` map."""
    if isinstance(value, dict):
        return bool(value.get("supported", True))
    return bool(value)


@dataclass(slots=True)
class DiscoveryReport:
    """Summary of one discovery pass, emitted as a metric and an SSE event."""

    nodes_seen: int = 0
    devices_seen: int = 0
    devices_updated: int = 0
    devices_unhealthy: int = 0
    capabilities_recorded: int = 0
    backend: str = ""
    errors: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes_seen": self.nodes_seen,
            "devices_seen": self.devices_seen,
            "devices_updated": self.devices_updated,
            "devices_unhealthy": self.devices_unhealthy,
            "capabilities_recorded": self.capabilities_recorded,
            "backend": self.backend,
            "errors": self.errors or [],
        }


class DiscoveryService:
    def __init__(
        self,
        session: AsyncSession,
        backend: GpuBackend,
        *,
        probes: tuple[str, ...] = DEFAULT_PROBES,
    ) -> None:
        self._session = session
        self._backend = backend
        self._probes = probes
        self._repo = GpuRepository(session)

    async def discover(self, *, record_probes: bool = True) -> DiscoveryReport:
        report = DiscoveryReport(backend=self._backend.name)
        errors: list[str] = []

        try:
            devices = await self._backend.list_devices()
        except Exception as exc:  # noqa: BLE001 - discovery must never crash the loop
            logger.error("gpu.discovery_failed", error=str(exc), backend=self._backend.name)
            report.errors = [f"list_devices: {exc}"]
            return report

        seen_node_ids = []
        by_node: dict[str, list[GpuDeviceInfo]] = {}
        for device in devices:
            node_key = str(device.extra.get("node_id") or device.extra.get("node_name") or "local")
            by_node.setdefault(node_key, []).append(device)

        for node_key, node_devices in by_node.items():
            report.nodes_seen += 1
            node = await self._repo.upsert_node(
                node_key=node_key,
                name=str(node_devices[0].extra.get("node_name") or node_key),
                metadata={"backend": self._backend.name},
            )
            seen_node_ids.append(node.id)
            report.devices_seen += len(node_devices)

            for info in node_devices:
                try:
                    device = await self._repo.upsert_device(
                        node=node,
                        device_uuid=info.uuid,
                        index=info.index,
                        name=info.name,
                        backend_device_id=info.device_id,
                        backend=self._backend.name,
                        vram_total_mb=info.vram_total_mb,
                        vram_free_mb=info.vram_free_mb,
                        compute_capability=info.compute_capability,
                        driver_version=info.driver_version,
                        utilization_percent=info.utilization_percent,
                        memory_used_mb=info.memory_used_mb,
                        temperature_c=info.temperature_c,
                        power_w=info.power_w,
                        healthy=info.healthy,
                        error_message=info.error_message,
                    )
                    report.devices_updated += 1
                    if not info.healthy:
                        report.devices_unhealthy += 1
                    await self._record_capabilities(device, info, record_probes, report, errors)
                except Exception as exc:  # noqa: BLE001 - one bad device must not abort the pass
                    errors.append(f"{info.device_id}: {exc}")
                    logger.error(
                        "gpu.discovery_device_failed",
                        device_id=info.device_id,
                        error=str(exc),
                    )

        try:
            await self._repo.mark_nodes_offline_not_seen(
                seen_node_ids=seen_node_ids,
                last_seen_before=utcnow(),
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"mark_offline: {exc}")

        report.errors = errors
        logger.info("gpu.discovery", **report.as_dict())
        return report

    async def _record_capabilities(
        self,
        device: GpuDevice,
        info: GpuDeviceInfo,
        record_probes: bool,
        report: DiscoveryReport,
        errors: list[str],
    ) -> None:
        """Registry facts first, then probes refine them (source is recorded)."""
        for capability, value in info.capabilities.items():
            supported = (
                bool(value.get("supported", True)) if isinstance(value, dict) else bool(value)
            )
            await self._repo.record_capability(
                device_id=device.id,
                capability=capability,
                supported=supported,
                source=CapabilitySource.REGISTRY.value,
                value=value if isinstance(value, dict) else {},
            )
            report.capabilities_recorded += 1

        if not record_probes:
            return
        for probe in self._probes:
            try:
                result = await self._backend.run_probe(info.device_id, probe)
            except Exception as exc:  # noqa: BLE001 - a probe failure is a data gap
                errors.append(f"probe {probe} on {info.device_id}: {exc}")
                continue
            if probe == "compute_capability":
                await self._repo.record_capability(
                    device_id=device.id,
                    capability="compute_capability",
                    supported=result.supported,
                    source=CapabilitySource.PROBE.value,
                    value=result.value,
                    probe_message=result.message,
                )
                report.capabilities_recorded += 1
            elif probe == "capabilities":
                for capability, value in dict(result.value.get("capabilities") or {}).items():
                    supported = (
                        bool(value.get("supported", True))
                        if isinstance(value, dict)
                        else bool(value)
                    )
                    await self._repo.record_capability(
                        device_id=device.id,
                        capability=capability,
                        supported=supported,
                        source=CapabilitySource.PROBE.value,
                        value=value if isinstance(value, dict) else {},
                        probe_message=result.message,
                    )
                    report.capabilities_recorded += 1

    async def refresh_device_telemetry(self, device_ids: list[GpuDevice]) -> dict[str, Any]:
        """Poll live metrics for devices a job is using.

        Called by the scheduler heartbeat so the UI shows live utilisation
        without a second discovery process.
        """
        refreshed: dict[str, Any] = {}
        for device in device_ids:
            # The backend handle is persisted at discovery time (see migration
            # a3f81c6d92e4); never derive it from the index or the device name,
            # because each backend namespaces its own device ids.
            handle = device.backend_device_id
            if not handle:
                refreshed[str(device.id)] = {"error": "no backend device id recorded"}
                continue
            metrics = await self._backend.get_metrics(handle)
            if metrics is None:
                refreshed[str(device.id)] = {"error": f"unknown backend device {handle!r}"}
                continue
            device.utilization_percent = metrics.utilization_percent
            device.memory_used_mb = metrics.memory_used_mb
            device.vram_free_mb = max(0, metrics.vram_total_mb - metrics.memory_used_mb)
            device.temperature_c = metrics.temperature_c
            device.power_w = metrics.power_w
            device.last_seen_at = utcnow()
            refreshed[str(device.id)] = {
                "utilization_percent": metrics.utilization_percent,
                "memory_used_mb": metrics.memory_used_mb,
            }
        await self._session.flush()
        return refreshed


def device_is_usable(device: GpuDevice, *, required_vram_mb: int) -> bool:
    """Final gate before reservation. Kept as a function so tests can exercise it."""
    return (
        device.status == GpuStatus.AVAILABLE.value
        and device.health == "OK"
        and device.vram_free_mb >= required_vram_mb
        and device.reservation_id is None
    )


__all__ = [
    "DEFAULT_PROBES",
    "DiscoveryReport",
    "DiscoveryService",
    "device_is_usable",
]
