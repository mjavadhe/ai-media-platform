"""GPU cluster endpoints (``/api/v1/gpus``) — read-mostly by design.

Every mutation of ``gpu_devices.status`` and ``gpu_reservations.state`` belongs to
the Scheduler. The only status writes exposed here are Scheduler-initiated admin
actions (``quarantine``/``release``), which call the Scheduler service rather than
touching the rows directly, so the single-writer invariant still holds.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import AppSettings, DbSession, require_permissions
from app.api.v1.schemas import (
    ClusterSummaryOut,
    DeviceListOut,
    DeviceStatusUpdate,
    GpuDeviceDetailOut,
    GpuDeviceOut,
    GpuNodeOut,
    NodeListOut,
)
from app.domain.enums import GpuStatus, PermissionCode
from app.infrastructure.logging import get_logger
from app.persistence.repositories.gpu import GpuRepository
from app.persistence.repositories.jobs import JobRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/gpus", tags=["gpus"])

#: Read-only cluster access, reused by the top-level alias routes.
ClusterRead = require_permissions(PermissionCode.GPU_READ.value)


@router.get("/nodes", response_model=NodeListOut)
async def list_nodes(
    session: DbSession,
    principal: require_permissions(PermissionCode.GPU_READ.value),
) -> NodeListOut:
    nodes = await GpuRepository(session).list_nodes()
    return NodeListOut(items=[GpuNodeOut.model_validate(node) for node in nodes])


@router.get("/devices", response_model=DeviceListOut)
async def list_devices(
    session: DbSession,
    principal: require_permissions(PermissionCode.GPU_READ.value),
    node_id: uuid.UUID | None = None,
    status: str | None = None,
    health: str | None = None,
    min_free_vram_mb: int | None = Query(default=None, ge=0),
    capability: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> DeviceListOut:
    """Filtered device inventory — the placement query the UI and API share."""
    try:
        status_enum = GpuStatus(status.upper()) if status else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    page = await GpuRepository(session).list_devices(
        node_id=node_id,
        status=status_enum,
        health=health,
        min_free_vram_mb=min_free_vram_mb,
        capability=capability,
        limit=limit,
        offset=offset,
    )
    return DeviceListOut(
        items=[GpuDeviceOut.model_validate(device) for device in page.items],
        total=page.total,
    )


@router.get("/cluster", response_model=ClusterSummaryOut)
async def cluster_summary(
    session: DbSession,
    settings: AppSettings,
    principal: require_permissions(PermissionCode.GPU_READ.value),
) -> ClusterSummaryOut:
    """Everything the operations page needs in a single round trip."""
    gpu = GpuRepository(session)
    jobs = JobRepository(session)
    nodes = await gpu.list_nodes()
    devices = (await gpu.list_devices(limit=500)).items
    details = [GpuDeviceDetailOut.model_validate(device) for device in devices]
    by_status: dict[str, int] = {}
    for device in devices:
        by_status[device.status] = by_status.get(device.status, 0) + 1
    return ClusterSummaryOut(
        nodes=[GpuNodeOut.model_validate(node) for node in nodes],
        devices=details,
        by_status=by_status,
        queued_jobs=await jobs.count_queued(),
        jobs_by_status=await jobs.count_by_status(),
        scheduler={
            "loop_interval_seconds": settings.scheduler_loop_interval_seconds,
            "reconcile_interval_seconds": settings.scheduler_reconcile_interval_seconds,
            "lease_ttl_seconds": settings.lease_ttl_seconds,
            "heartbeat_missed_threshold": settings.heartbeat_missed_threshold,
            "lease_advisory_key": settings.scheduler_lease_advisory_key,
            "backend": settings.gpu_backend,
        },
    )


@router.get("/devices/{device_id}", response_model=GpuDeviceDetailOut)
async def get_device(
    device_id: uuid.UUID,
    session: DbSession,
    principal: require_permissions(PermissionCode.GPU_READ.value),
) -> GpuDeviceDetailOut:
    device = await GpuRepository(session).get_device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="gpu device not found")
    return GpuDeviceDetailOut.model_validate(device)


@router.get("/reservations")
async def list_reservations(
    session: DbSession,
    principal: require_permissions(PermissionCode.GPU_READ.value),
    state: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    rows = await GpuRepository(session).list_reservations(state=state, limit=limit)
    return [
        {
            "id": str(row.id),
            "job_id": str(row.job_id),
            "gpu_device_id": str(row.gpu_device_id),
            "node_id": str(row.node_id),
            "state": row.state,
            "placement_policy": row.placement_policy,
            "required_vram_mb": row.required_vram_mb,
            "allocated_vram_mb": row.allocated_vram_mb,
            "release_requested_at": row.release_requested_at.isoformat()
            if row.release_requested_at
            else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


# --- admin actions -------------------------------------------------------
@router.post("/devices/{device_id}/status", response_model=GpuDeviceOut)
async def update_device_status(
    device_id: uuid.UUID,
    payload: DeviceStatusUpdate,
    session: DbSession,
    settings: AppSettings,
    principal: require_permissions(PermissionCode.GPU_ADMIN.value),
) -> GpuDeviceOut:
    """Quarantine/release a device via the Scheduler, never by direct row update."""
    from app.scheduler.service import SchedulerService
    from worker.gpu.registry import build_gpu_backend

    backend = build_gpu_backend(settings.gpu_backend)
    scheduler = SchedulerService(session, backend, settings)
    try:
        target = GpuStatus(payload.status.upper())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if target not in {GpuStatus.QUARANTINED, GpuStatus.AVAILABLE}:
        raise HTTPException(
            status_code=422,
            detail="only QUARANTINED and AVAILABLE may be set manually",
        )
    device = await scheduler.admin_set_device_status(
        device_id=device_id, status=target, reason=payload.reason, actor=str(principal.user.id)
    )
    logger.info(
        "gpu.device_status_changed",
        gpu_device_id=str(device_id),
        status=device.status,
        reason=payload.reason,
        actor_id=str(principal.user.id),
    )
    await session.commit()
    return GpuDeviceOut.model_validate(device)


@router.post("/devices/{device_id}/release", response_model=GpuDeviceOut)
async def force_release(
    device_id: uuid.UUID,
    session: DbSession,
    settings: AppSettings,
    principal: require_permissions(PermissionCode.GPU_ADMIN.value),
    reason: str | None = None,
) -> GpuDeviceOut:
    """Force-complete a stuck reservation so the device returns to the pool."""
    from app.scheduler.service import SchedulerService
    from worker.gpu.registry import build_gpu_backend

    backend = build_gpu_backend(settings.gpu_backend)
    scheduler = SchedulerService(session, backend, settings)
    device = await scheduler.admin_force_release(
        device_id=device_id, reason=reason, actor=str(principal.user.id)
    )
    logger.warning(
        "gpu.device_force_released",
        gpu_device_id=str(device_id),
        reason=reason,
        actor_id=str(principal.user.id),
    )
    await session.commit()
    return GpuDeviceOut.model_validate(device)


#: Contract paths ``GET /api/v1/nodes`` and ``GET /api/v1/gpus`` sit outside the
#: ``/gpus`` prefix, so they get their own router bound to the same handlers.
cluster_router = APIRouter(tags=["gpus"])


@cluster_router.get("/nodes", response_model=NodeListOut)
async def nodes_root(session: DbSession, principal: ClusterRead) -> NodeListOut:
    return await list_nodes(session=session, principal=principal)


@cluster_router.get("/gpus", response_model=DeviceListOut)
async def gpus_root(
    session: DbSession,
    principal: ClusterRead,
    node_id: uuid.UUID | None = None,
    status: str | None = None,
    health: str | None = None,
    min_free_vram_mb: int | None = Query(default=None, ge=0),
    capability: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> DeviceListOut:
    return await list_devices(
        session,
        principal,
        node_id=node_id,
        status=status,
        health=health,
        min_free_vram_mb=min_free_vram_mb,
        capability=capability,
        limit=limit,
        offset=offset,
    )


__all__ = ["cluster_router", "router"]
