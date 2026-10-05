"""GPU/cluster repository — **the only** writer of ``gpu_devices.status``.

Ownership contract (section 5.1 / ADR-0007):

* ``gpu_devices.status`` may only be changed through :class:`GpuRepository`
  methods, and those methods are called exclusively by the Scheduler.
* ``gpu_reservations.state`` transitions to ``RELEASE_PENDING`` are performed by
  :meth:`GpuRepository.request_release`, which the Orchestrator calls in Tx 1.
  Only :meth:`GpuRepository.complete_release` (Scheduler, Tx 2) may write
  ``RELEASED`` or move the device back to ``AVAILABLE``.

Every method here is deliberately split along that Tx1/Tx2 boundary so a caller
cannot accidentally perform the Scheduler's half of the work.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import GpuStatus, NodeStatus, ReservationState
from app.persistence.models_job import (
    GpuCapability,
    GpuDevice,
    GpuNode,
    GpuReservation,
    Job,
)
from app.persistence.repositories.common import utcnow


class ReservationConflictError(Exception):
    """The reservation is no longer in a state that allows the requested change.

    Raised in the normal path when a second actor races the Scheduler; callers
    treat it as "someone else already did this" rather than as a fatal error.
    """


@dataclass(frozen=True, slots=True)
class DevicePage:
    """A page of devices plus the unpaginated total."""

    items: list[GpuDevice]
    total: int


@dataclass(frozen=True, slots=True)
class DeviceCandidate:
    """A schedulable GPU, as returned by the placement query."""

    device_id: uuid.UUID
    node_id: uuid.UUID
    index: int
    name: str
    vram_total_mb: int
    vram_free_mb: int
    compute_capability: str | None
    status: GpuStatus
    capabilities: dict[str, Any]
    node_status: NodeStatus
    score: float = 0.0


class GpuRepository:
    """Scheduler-only writer for physical GPU state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- discovery (Scheduler) -------------------------------------------
    async def upsert_node(
        self,
        *,
        node_key: str,
        name: str,
        endpoint: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GpuNode:
        """Create or refresh a node row. Nodes are keyed by a stable backend id."""
        node = await self._session.scalar(
            select(GpuNode).where(GpuNode.node_key == node_key).with_for_update()
        )
        now = utcnow()
        if node is None:
            node = GpuNode(
                node_key=node_key,
                name=name,
                endpoint=endpoint,
                status=NodeStatus.HEALTHY.value,
                last_seen_at=now,
                metadata_=metadata or {},
            )
            self._session.add(node)
            await self._session.flush()
            return node
        node.name = name
        node.endpoint = endpoint
        node.status = NodeStatus.HEALTHY.value
        node.last_seen_at = now
        if metadata:
            node.metadata_ = {**(node.metadata_ or {}), **metadata}
        await self._session.flush()
        return node

    async def upsert_device(
        self,
        *,
        node: GpuNode,
        device_uuid: str,
        index: int,
        name: str,
        backend_device_id: str | None = None,
        vram_total_mb: int,
        vram_free_mb: int,
        compute_capability: str | None = None,
        driver_version: str | None = None,
        utilization_percent: float = 0.0,
        memory_used_mb: int = 0,
        temperature_c: float | None = None,
        power_w: float | None = None,
        healthy: bool = True,
        error_message: str | None = None,
    ) -> GpuDevice:
        """Refresh a device row from backend telemetry (Scheduler discovery)."""
        device = await self._session.scalar(
            select(GpuDevice)
            .where(GpuDevice.node_id == node.id, GpuDevice.uuid == device_uuid)
            .with_for_update()
        )
        now = utcnow()
        if device is None:
            device = GpuDevice(
                node_id=node.id,
                uuid=device_uuid,
                index=index,
                name=name,
                vram_total_mb=vram_total_mb,
                vram_free_mb=vram_free_mb,
                status=GpuStatus.AVAILABLE.value,
            )
            self._session.add(device)
        device.name = name
        if backend_device_id:
            device.backend_device_id = backend_device_id
        device.vram_total_mb = vram_total_mb
        device.vram_free_mb = vram_free_mb
        device.compute_capability = compute_capability
        device.driver_version = driver_version
        device.utilization_percent = utilization_percent
        device.memory_used_mb = memory_used_mb
        device.temperature_c = temperature_c
        device.power_w = power_w
        device.health = "OK" if healthy else "ERROR"
        device.error_message = error_message
        device.last_seen_at = now
        # A device that vanished from the backend while it was reserved is the
        # Scheduler's problem to resolve; here we only refuse to *revive* an
        # unhealthy device into the schedulable pool.
        if not healthy and device.status == GpuStatus.AVAILABLE.value:
            device.status = GpuStatus.UNHEALTHY.value
        elif healthy and device.status == GpuStatus.UNHEALTHY.value:
            device.status = GpuStatus.AVAILABLE.value
        await self._session.flush()
        return device

    async def record_capability(
        self,
        *,
        device_id: uuid.UUID,
        capability: str,
        supported: bool,
        source: str,
        value: dict[str, Any] | None = None,
        probe_message: str | None = None,
    ) -> GpuCapability:
        """Upsert a data-driven capability record (registry + probes)."""
        row = await self._session.scalar(
            select(GpuCapability)
            .where(GpuCapability.device_id == device_id, GpuCapability.capability == capability)
            .with_for_update()
        )
        if row is None:
            row = GpuCapability(device_id=device_id, capability=capability)
            self._session.add(row)
        row.supported = supported
        row.source = source
        row.value = value or {}
        row.message = probe_message
        row.checked_at = utcnow()
        await self._session.flush()
        return row

    async def get_device(self, device_id: uuid.UUID, *, lock: bool = False) -> GpuDevice | None:
        stmt = select(GpuDevice).where(GpuDevice.id == device_id)
        if lock:
            stmt = stmt.with_for_update()
        return await self._session.scalar(stmt)

    async def mark_nodes_offline_not_seen(
        self, *, seen_node_ids: list[uuid.UUID], last_seen_before: datetime
    ) -> int:
        """Drain nodes absent from a discovery pass (Scheduler only).

        Nodes still holding a live reservation are left alone — their GPU is
        accounted for by ``complete_release``/recovery, and flipping the node
        offline here would race that path.
        """
        if not seen_node_ids:
            return 0
        busy_node_ids = select(GpuReservation.node_id).where(
            GpuReservation.state.in_(
                [
                    ReservationState.RESERVED.value,
                    ReservationState.BUSY.value,
                    ReservationState.RELEASE_PENDING.value,
                ]
            )
        )
        result = await self._session.execute(
            update(GpuNode)
            .where(
                GpuNode.id.notin_(seen_node_ids),
                GpuNode.last_seen_at.isnot(None),
                GpuNode.last_seen_at < last_seen_before,
                GpuNode.id.notin_(busy_node_ids),
            )
            .values(status=NodeStatus.OFFLINE.value)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    async def set_device_status(
        self, device_id: uuid.UUID, status: GpuStatus, *, reason: str | None = None
    ) -> None:
        """Explicit Scheduler status write (drain/maintenance/offline/health)."""
        device = await self.get_device(device_id, lock=True)
        if device is None:
            return
        device.status = status.value
        if reason:
            device.error_message = reason
        await self._session.flush()

    # --- placement (Scheduler) -------------------------------------------
    async def find_candidate(
        self,
        *,
        required_vram_mb: int,
        required_capabilities: list[str] | None = None,
        preferred_node_ids: list[uuid.UUID] | None = None,
        preferred_gpu_uuids: list[str] | None = None,
        policy: str = "AUTO",
    ) -> DeviceCandidate | None:
        """Pick a GPU for a job. Selection is a data query, not a GPU-name check.

        Only devices in ``AVAILABLE`` on a ``HEALTHY`` node with enough free VRAM
        and every required capability are considered. Preference and policy only
        affect the *ordering* of an otherwise valid candidate set.
        """
        capabilities = required_capabilities or []
        stmt = (
            select(GpuDevice, GpuNode)
            .join(GpuNode, GpuNode.id == GpuDevice.node_id)
            .where(
                GpuDevice.status == GpuStatus.AVAILABLE.value,
                GpuNode.status == NodeStatus.HEALTHY.value,
                GpuDevice.vram_free_mb >= required_vram_mb,
                GpuDevice.health == "OK",
            )
        )
        rows = (await self._session.execute(stmt)).all()
        candidates: list[DeviceCandidate] = []
        for device, node in rows:
            if preferred_gpu_uuids and device.uuid not in preferred_gpu_uuids:
                continue
            if not await self._device_supports(device.id, capabilities):
                continue
            candidates.append(
                DeviceCandidate(
                    device_id=device.id,
                    node_id=node.id,
                    index=device.index,
                    name=device.name,
                    vram_total_mb=device.vram_total_mb,
                    vram_free_mb=device.vram_free_mb,
                    compute_capability=device.compute_capability,
                    status=GpuStatus(device.status),
                    capabilities=await self._capability_map(device.id),
                    node_status=NodeStatus(node.status),
                    score=self._score(
                        device, node, required_vram_mb, capabilities, preferred_node_ids or []
                    ),
                )
            )
        if not candidates:
            return None
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        if policy.upper() == "ROUND_ROBIN":
            # deterministic rotation keeps load spread without relying on
            # wall-clock time or randomness (reproducible in tests)
            rotated = sorted(candidates, key=lambda c: (c.index, str(c.device_id)))
            return rotated[_round_robin_offset(len(rotated))]
        return candidates[0]

    def _score(
        self,
        device: GpuDevice,
        node: GpuNode,
        required_vram_mb: int,
        capabilities: list[str],
        preferred_node_ids: list[uuid.UUID],
    ) -> float:
        """AUTO policy: prefer an explicit pin, then least waste, then most free VRAM."""
        score = 0.0
        if node.id in preferred_node_ids:
            score += 1000.0
        total = max(1, device.vram_total_mb)
        score += 100.0 * (1.0 - (device.vram_total_mb - required_vram_mb) / total)
        score += min(50.0, device.vram_free_mb / 200.0)
        score += len(capabilities) * 1.0
        return score

    async def _device_supports(self, device_id: uuid.UUID, capabilities: list[str]) -> bool:
        if not capabilities:
            return True
        rows = await self._session.execute(
            select(GpuCapability.capability, GpuCapability.supported).where(
                GpuCapability.device_id == device_id
            )
        )
        known = dict(rows.all())
        return all(bool(known.get(name, False)) for name in capabilities)

    async def _capability_map(self, device_id: uuid.UUID) -> dict[str, Any]:
        rows = await self._session.execute(
            select(GpuCapability.capability, GpuCapability.supported, GpuCapability.value).where(
                GpuCapability.device_id == device_id
            )
        )
        return {
            name: {"supported": supported, **(value or {})} for name, supported, value in rows.all()
        }

    async def reserve(
        self,
        *,
        job: Job,
        device: GpuDevice,
        node: GpuNode,
        policy: str,
        scores: dict[str, Any] | None = None,
    ) -> GpuReservation:
        """Create the reservation and move the device to ``RESERVED`` (Scheduler Tx1).

        Idempotent per job: if the job already holds a live reservation the same
        reservation is returned instead of allocating a second GPU.
        """
        existing = await self._session.scalar(
            select(GpuReservation)
            .where(
                GpuReservation.job_id == job.id,
                GpuReservation.state.in_(
                    [ReservationState.RESERVED.value, ReservationState.BUSY.value]
                ),
            )
            .with_for_update()
        )
        if existing is not None:
            return existing
        reservation = GpuReservation(
            job_id=job.id,
            node_id=node.id,
            gpu_device_id=device.id,
            policy=policy,
            scores=scores or {},
            state=ReservationState.RESERVED.value,
        )
        self._session.add(reservation)
        await self._session.flush()
        device.reservation_id = reservation.id
        device.status = GpuStatus.RESERVED.value
        job.gpu_device_id = device.id
        job.node_id = node.id
        await self._session.flush()
        return reservation

    async def set_reservation_busy(
        self, reservation_id: uuid.UUID, *, busy: bool
    ) -> GpuReservation:
        """Scheduler-side RESERVED<->BUSY flip on start."""
        reservation = await self._session.get(GpuReservation, reservation_id, with_for_update=True)
        if reservation is None:
            raise ReservationConflictError(f"reservation {reservation_id} not found")
        target = ReservationState.BUSY if busy else ReservationState.RESERVED
        if reservation.state in {
            ReservationState.RELEASED.value,
            ReservationState.RELEASE_PENDING.value,
        }:
            raise ReservationConflictError(
                f"reservation {reservation_id} is {reservation.state}, cannot become {target.value}"
            )
        reservation.state = target.value
        await self._session.flush()
        return reservation

    async def attach_worker(self, reservation_id: uuid.UUID, worker_id: uuid.UUID) -> None:
        reservation = await self._session.get(GpuReservation, reservation_id, with_for_update=True)
        if reservation is None:
            raise ReservationConflictError(f"reservation {reservation_id} not found")
        reservation.worker_id = worker_id
        await self._session.flush()

    # --- release: Tx1 (Orchestrator request) vs Tx2 (Scheduler completion) -
    async def request_release(self, reservation_id: uuid.UUID) -> bool:
        """Tx1: request a release. Idempotent; never touches the device status.

        Returns True when this call created the ``RELEASE_PENDING`` request,
        False when it was already pending or already released (so a duplicate
        completion report is a no-op rather than a double free).
        """
        result = await self._session.execute(
            update(GpuReservation)
            .where(
                GpuReservation.id == reservation_id,
                GpuReservation.state.in_(
                    [ReservationState.RESERVED.value, ReservationState.BUSY.value]
                ),
            )
            .values(state=ReservationState.RELEASE_PENDING.value, release_requested_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)

    async def complete_release(self, reservation_id: uuid.UUID) -> bool:
        """Tx2: free the device and mark the reservation ``RELEASED``.

        The ``gpu_devices.reservation_id IS DISTINCT FROM :id`` guard plus the
        partial unique index ``uq_gpu_reservations_live_device`` make this
        exactly-once: a late or duplicated Tx2 cannot free a GPU that has already
        been handed to a different job.
        """
        reservation = await self._session.get(GpuReservation, reservation_id, with_for_update=True)
        if reservation is None:
            return False
        if reservation.state == ReservationState.RELEASED.value:
            return False  # idempotent: already freed
        if reservation.state != ReservationState.RELEASE_PENDING.value:
            raise ReservationConflictError(
                f"reservation {reservation_id} is {reservation.state}; release not requested"
            )
        device = await self.get_device(reservation.gpu_device_id, lock=True)
        now = utcnow()
        reservation.state = ReservationState.RELEASED.value
        reservation.released_at = now
        if device is not None:
            # The guard: only free the device if it still points at *this*
            # reservation. If it points elsewhere, another job legitimately owns
            # the GPU now and we must not clobber its status.
            if device.reservation_id == reservation.id:
                device.status = GpuStatus.AVAILABLE.value
                device.reservation_id = None
                device.vram_free_mb = device.vram_total_mb
                device.memory_used_mb = 0
                device.utilization_percent = 0.0
            else:
                notes = dict(reservation.scores or {})
                notes["release_note"] = "device already re-reserved; status left untouched"
                reservation.scores = notes
        await self._session.flush()
        return True

    async def complete_release_for_job(self, job_id: uuid.UUID) -> bool:
        """Complete Tx2 for whatever live reservation a job still holds.

        Used by the Scheduler's *recovery* paths, where the Scheduler is the actor
        that detected the problem, so it may finish the release itself instead of
        leaving a ``RELEASE_PENDING`` request for the reconciler. Returns False
        when the job holds no live reservation (already released).
        """
        reservation = await self.get_reservation_for_job(job_id, lock=True)
        if reservation is None:
            return False
        # Recovery is the detecting actor, so it may skip the wait for Tx1's
        # handshake — but the state must still move through RELEASE_PENDING,
        # otherwise `complete_release` (correctly) refuses to free a live
        # reservation. The row is already locked, so this cannot race Tx1.
        if reservation.state == ReservationState.RELEASE_PENDING.value:
            return await self.complete_release(reservation.id)
        reservation.state = ReservationState.RELEASE_PENDING.value
        reservation.release_requested_at = utcnow()
        await self._session.flush()
        return await self.complete_release(reservation.id)

    async def get_pending_releases(
        self, *, older_than_seconds: float = 0.0, limit: int = 200
    ) -> list[GpuReservation]:
        """Reservations awaiting Scheduler Tx2 (release reconciliation).

        ``skip_locked`` lets several Scheduler iterations overlap safely, and the
        grace period keeps Tx2 out of the critical path of Tx1.
        """
        threshold = utcnow() - timedelta(seconds=max(0.0, older_than_seconds))
        rows = await self._session.scalars(
            select(GpuReservation)
            .where(
                GpuReservation.state == ReservationState.RELEASE_PENDING.value,
                GpuReservation.release_requested_at.isnot(None),
                GpuReservation.release_requested_at <= threshold,
            )
            .order_by(GpuReservation.release_requested_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(rows.all())

    async def list_devices(
        self,
        *,
        node_id: uuid.UUID | None = None,
        status: str | None = None,
        health: str | None = None,
        min_free_vram_mb: int | None = None,
        capability: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> DevicePage:
        """Filtered device inventory.

        The same predicate set backs the API's ``GET /gpus/devices``, the
        operations UI and (partly) placement, so filtering is expressed once here
        instead of being re-derived per caller. ``capability`` filters on the
        data-driven capability rows, never on a device name.
        """
        stmt = select(GpuDevice)
        if node_id is not None:
            stmt = stmt.where(GpuDevice.node_id == node_id)
        if status:
            stmt = stmt.where(GpuDevice.status == status)
        if health:
            stmt = stmt.where(GpuDevice.health == health)
        if min_free_vram_mb is not None:
            stmt = stmt.where(GpuDevice.vram_free_mb >= min_free_vram_mb)
        if capability:
            stmt = stmt.where(
                GpuDevice.id.in_(
                    select(GpuCapability.device_id).where(GpuCapability.name == capability)
                )
            )
        total = await self._session.scalar(select(func.count()).select_from(stmt.subquery()))
        stmt = stmt.order_by(GpuDevice.node_id, GpuDevice.index)
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        rows = await self._session.scalars(stmt)
        return DevicePage(items=list(rows.all()), total=int(total or 0))

    async def list_nodes(self) -> list[GpuNode]:
        return list((await self._session.scalars(select(GpuNode).order_by(GpuNode.name))).all())

    async def list_reservations(
        self, *, state: str | None = None, limit: int = 100
    ) -> list[GpuReservation]:
        """Reservations for the operations view, newest first."""
        stmt = select(GpuReservation)
        if state:
            stmt = stmt.where(GpuReservation.state == state)
        stmt = stmt.order_by(GpuReservation.created_at.desc()).limit(limit)
        return list((await self._session.scalars(stmt)).all())

    async def get_reservation_for_job(
        self, job_id: uuid.UUID, *, lock: bool = False
    ) -> GpuReservation | None:
        stmt = (
            select(GpuReservation)
            .where(
                GpuReservation.job_id == job_id,
                GpuReservation.state.in_(
                    [
                        ReservationState.RESERVED.value,
                        ReservationState.BUSY.value,
                        ReservationState.RELEASE_PENDING.value,
                    ]
                ),
            )
            .order_by(GpuReservation.created_at.desc())
        )
        if lock:
            stmt = stmt.with_for_update()
        return await self._session.scalar(stmt)


_round_robin_counter = 0


def _round_robin_offset(size: int) -> int:
    """Next offset in a stable rotation (process-local, test-visible)."""
    global _round_robin_counter
    if size <= 0:
        return 0
    _round_robin_counter = (_round_robin_counter + 1) % size
    return _round_robin_counter
