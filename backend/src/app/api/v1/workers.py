"""Worker endpoints (``/api/v1/workers``).

The claim endpoint is the crux of the ownership contract: the worker's request
*triggers* a Scheduler-executed transaction. No worker request ever writes
``jobs.status`` directly — :meth:`SchedulerService.start_for_worker` does, and the
state machine would reject anyone else.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status
from sqlalchemy import select

from app.api.deps import (
    AppSettings,
    CsrfChecked,
    CurrentWorker,
    DbSession,
    StorageDep,
    require_permissions,
)
from app.api.v1.schemas import (
    ClaimOut,
    ClaimRequest,
    CompleteRequest,
    FailRequest,
    HeartbeatOut,
    HeartbeatRequest,
    OutputDescriptor,
    ProgressOut,
    ProgressRequest,
    TerminalOut,
    WorkerCreatedOut,
    WorkerListOut,
    WorkerOut,
    WorkerRegisterRequest,
    WorkerRegistrationOut,
)
from app.domain.enums import PermissionCode, WorkerStatus
from app.infrastructure.events import Event, get_event_bus
from app.infrastructure.logging import get_logger
from app.orchestrator.service import OrchestratorService, StaleLeaseError, WrongStateError
from app.persistence.models_job import GpuNode
from app.persistence.repositories.common import utcnow
from app.persistence.repositories.jobs import JobRepository, LeaseExpiredError
from app.persistence.repositories.workers import WorkerAuthError, WorkerRepository
from app.scheduler.service import SchedulerService
from worker.gpu.registry import build_gpu_backend

logger = get_logger(__name__)

router = APIRouter(prefix="/workers", tags=["workers"])
#: Contract path for worker reports: ``POST /api/v1/jobs/{id}/{progress,complete,fail}``.
job_router = APIRouter(prefix="/jobs", tags=["jobs"])


def _orchestrator(session, settings: AppSettings, storage) -> OrchestratorService:
    return OrchestratorService(session, settings, storage)


def _scheduler(session, settings: AppSettings) -> SchedulerService:
    """A SchedulerService bound to the worker's own backend.

    The claim transaction only touches the database; the backend instance is used
    for nothing but interface symmetry, so the configured backend is safe to
    construct here without a GPU present.
    """
    backend = build_gpu_backend(
        settings.gpu_backend,
        mock_nodes=settings.mock_gpu_nodes,
        mock_gpus_per_node=settings.mock_gpus_per_node,
        mock_vram_mb=settings.mock_gpu_vram_mb,
    )
    return SchedulerService(session, backend, settings)


#: One-time registration token header, presented once by a fresh agent.
REGISTRATION_HEADER = "X-Registration-Token"


@router.post("/register", response_model=WorkerRegistrationOut)
async def register(
    payload: WorkerRegisterRequest,
    session: DbSession,
    settings: AppSettings,
    registration_token: Annotated[str | None, Header(alias=REGISTRATION_HEADER)] = None,
) -> WorkerRegistrationOut:
    """Exchange a one-time registration token for a long-lived credential.

    The registration token itself is issued by an admin via
    ``POST /api/v1/workers`` (authenticated, ``worker:admin``) and is single-use:
    it is cleared in the same transaction that issues the credential.
    """
    if not registration_token:
        raise HTTPException(
            status_code=401, detail=f"{REGISTRATION_HEADER} header is required for registration"
        )
    node_id = None
    if payload.node_key:
        node_id = await session.scalar(
            select(GpuNode.id).where(GpuNode.node_key == payload.node_key)
        )
    repo = WorkerRepository(session)
    try:
        credential = await repo.complete_registration(
            registration_token=registration_token,
            name=payload.name,
            version=payload.version,
            platform=payload.platform,
            capabilities=payload.capabilities,
            node_id=node_id,
            credential_ttl_hours=settings.worker_credential_ttl_hours,
        )
    except WorkerAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    logger.info("worker.registered", worker_id=str(credential.worker_id), name=payload.name)
    await session.commit()
    return WorkerRegistrationOut(
        worker_id=credential.worker_id,
        credential=credential.credential,
        prefix=credential.prefix,
        expires_at=credential.expires_at,
    )


# --- admin: create a worker and issue its registration token ------------
admin_router = APIRouter(prefix="/workers", tags=["workers"])
#: Contract path for worker reports: ``POST /api/v1/jobs/{id}/{progress,complete,fail}``.


@admin_router.post("", response_model=WorkerCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_worker(
    payload: WorkerRegisterRequest,
    session: DbSession,
    settings: AppSettings,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.WORKER_ADMIN.value),
) -> WorkerCreatedOut:
    """Create a worker and print its one-time registration token."""
    repo = WorkerRepository(session)
    registration = await repo.create_worker(
        name=payload.name,
        registration_ttl_hours=1,
    )
    logger.info(
        "worker.created", worker_id=str(registration.worker_id), actor_id=str(principal.user.id)
    )
    await session.commit()
    return WorkerCreatedOut(
        worker_id=registration.worker_id,
        registration_token=registration.token,
        expires_at=registration.expires_at,
    )


# --- worker-authenticated endpoints --------------------------------------
@router.get("/me", response_model=WorkerOut)
async def worker_me(worker: CurrentWorker) -> WorkerOut:
    return WorkerOut.model_validate(worker)


async def _heartbeat(
    payload: HeartbeatRequest,
    worker,
    session: DbSession,
    settings: AppSettings,
) -> HeartbeatOut:
    """Liveness + lease renewal + cache reporting.

    A heartbeat that does not renew the lease it is reporting on is pointless:
    every claimed job is renewed here, and a claim whose lease has already
    expired is reported as *not* renewed so the worker stops work immediately
    instead of writing results into a lease that no longer exists.
    """
    repo = WorkerRepository(session)
    status_enum = None
    if payload.status:
        try:
            status_enum = WorkerStatus(payload.status.upper())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    await repo.record_heartbeat(
        worker,
        status=status_enum,
        active_job_count=payload.active_job_count,
        cache_state=payload.cache_state,
    )
    jobs = JobRepository(session)
    renewed: list[str] = []
    lost: list[str] = []
    for claim_id in payload.claim_ids:
        job = await jobs.get_by_claim_id(claim_id)
        if job is None:
            lost.append(claim_id)
            continue
        try:
            await jobs.renew_lease(
                job,
                claim_id=claim_id,
                worker_id=worker.id,
                lease_ttl_seconds=settings.lease_ttl_seconds,
            )
        except LeaseExpiredError:
            lost.append(claim_id)
        else:
            renewed.append(claim_id)
    await session.commit()
    result = HeartbeatOut(
        worker_id=worker.id,
        server_time=utcnow(),
        lease_ttl_seconds=settings.lease_ttl_seconds,
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
        renewed_claim_ids=renewed,
        lost_claim_ids=lost,
    )
    if lost:
        logger.warning("worker.leases_lost", worker_id=str(worker.id), claim_ids=lost)
    return result


@router.post("/heartbeat", response_model=HeartbeatOut)
async def heartbeat(
    payload: HeartbeatRequest,
    worker: CurrentWorker,
    session: DbSession,
    settings: AppSettings,
) -> HeartbeatOut:
    return await _heartbeat(payload, worker, session, settings)


@router.post("/{worker_id}/heartbeat", response_model=HeartbeatOut)
async def heartbeat_by_id(
    worker_id: uuid.UUID,
    payload: HeartbeatRequest,
    worker: CurrentWorker,
    session: DbSession,
    settings: AppSettings,
) -> HeartbeatOut:
    """Contract path. The worker may only heartbeat for itself."""
    if worker.id != worker_id:
        raise HTTPException(status_code=403, detail="a worker may only heartbeat for itself")
    return await _heartbeat(payload, worker, session, settings)


@router.post("/jobs/claim", response_model=ClaimOut)
@router.post("/{worker_id}/jobs/claim", response_model=ClaimOut)
async def claim_job(
    payload: ClaimRequest,
    worker: CurrentWorker,
    session: DbSession,
    settings: AppSettings,
    worker_id: uuid.UUID | None = None,
) -> ClaimOut:
    """Worker claim = start trigger. All writes are performed by the Scheduler.

    Returns 204 when there is nothing to claim, which is the normal idle case and
    not an error.
    """
    if worker_id is not None and worker.id != worker_id:
        raise HTTPException(status_code=403, detail="a worker may only claim for itself")
    scheduler = _scheduler(session, settings)
    try:
        started = await scheduler.start_for_worker(
            worker_id=worker.id,
            job_id=payload.job_id,
            capabilities=payload.capabilities,
        )
    except Exception as exc:
        await session.rollback()
        logger.warning("worker.claim_conflict", worker_id=str(worker.id), error=str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if started is None:
        await session.commit()
        raise HTTPException(status_code=204, detail="no claimable job")
    await WorkerRepository(session).record_heartbeat(
        worker, status=WorkerStatus.BUSY, active_job_count=1
    )
    await session.commit()
    logger.info(
        "worker.claimed",
        worker_id=str(worker.id),
        job_id=started["job_id"],
        attempt_id=started["attempt_id"],
        claim_id=started["claim_id"],
    )
    return ClaimOut(
        job_id=uuid.UUID(started["job_id"]),
        attempt_id=uuid.UUID(started["attempt_id"]),
        attempt_no=started["attempt_no"],
        claim_id=started["claim_id"],
        lease_expires_at=started["lease_expires_at"],
        lease_ttl_seconds=started["lease_ttl_seconds"],
        heartbeat_interval_seconds=started["heartbeat_interval_seconds"],
        gpu_device_id=uuid.UUID(started["gpu_device_id"]),
        gpu_backend_device_id=started["gpu_backend_device_id"],
        required_vram_mb=started.get("required_vram_mb"),
        node_id=uuid.UUID(started["node_id"]),
        job_type=started["job_type"],
        params=started["params"],
        prompt=started["prompt"],
        negative_prompt=started["negative_prompt"],
        model_version_id=started["model_version_id"],
        workflow_version_id=started["workflow_version_id"],
    )


@router.post("/jobs/{job_id}/progress", response_model=ProgressOut)
@job_router.post("/{job_id}/progress", response_model=ProgressOut)
async def report_progress(
    job_id: uuid.UUID,
    payload: ProgressRequest,
    claim_id: str,
    worker: CurrentWorker,
    session: DbSession,
    settings: AppSettings,
    storage: StorageDep,
) -> ProgressOut:
    """Progress report. The first one transitions ``STARTING -> RUNNING``."""
    orchestrator = _orchestrator(session, settings, storage)
    try:
        result = await orchestrator.record_progress(
            job_id=job_id,
            claim_id=claim_id,
            worker_id=worker.id,
            step=payload.step,
            total=payload.total,
            percent=payload.percent,
            message=payload.message,
        )
    except StaleLeaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WrongStateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await get_event_bus().publish(
        _job_event(
            job_id,
            "job.running" if result["started"] else "job.progress",
            {
                "progress_percent": result["progress_percent"],
                "status": result["status"],
                "step": payload.step,
                "total": payload.total,
                "message": payload.message,
            },
        )
    )
    return ProgressOut(
        job_id=job_id,
        status=result["status"],
        progress_percent=result["progress_percent"],
        started=result["started"],
    )


@router.post("/jobs/{job_id}/complete", response_model=TerminalOut)
@job_router.post("/{job_id}/complete", response_model=TerminalOut)
async def complete_job(
    job_id: uuid.UUID,
    payload: CompleteRequest,
    claim_id: str,
    worker: CurrentWorker,
    session: DbSession,
    settings: AppSettings,
    storage: StorageDep,
) -> TerminalOut:
    """Completion report (Tx1): job -> COMPLETED, media stored, release requested."""
    orchestrator = _orchestrator(session, settings, storage)
    try:
        result = await orchestrator.complete(
            job_id=job_id,
            claim_id=claim_id,
            worker_id=worker.id,
            outputs=[_decode_output(item) for item in payload.outputs],
            runtime_info=payload.runtime_info,
            duration_seconds=payload.duration_seconds,
        )
    except StaleLeaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WrongStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await get_event_bus().publish(
        _job_event(
            job_id,
            "job.completed",
            {"status": result.status.value, "artifacts": result.artifacts},
        )
    )
    return TerminalOut(
        job_id=job_id,
        status=result.status.value,
        attempt_id=result.attempt_id,
        artifacts=result.artifacts,
        release_requested=result.release_requested,
    )


@router.post("/jobs/{job_id}/fail", response_model=TerminalOut)
@job_router.post("/{job_id}/fail", response_model=TerminalOut)
async def fail_job(
    job_id: uuid.UUID,
    payload: FailRequest,
    claim_id: str,
    worker: CurrentWorker,
    session: DbSession,
    settings: AppSettings,
    storage: StorageDep,
) -> TerminalOut:
    """Failure report (Tx1): the Orchestrator decides retry vs fail."""
    orchestrator = _orchestrator(session, settings, storage)
    error = dict(payload.error)
    if payload.message:
        error["message"] = payload.message
    try:
        result = await orchestrator.fail(
            job_id=job_id,
            claim_id=claim_id,
            worker_id=worker.id,
            failure_class=payload.failure_class,
            error=error,
            retryable=payload.retryable,
        )
    except StaleLeaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WrongStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await get_event_bus().publish(
        _job_event(
            job_id,
            "job.retrying" if result.retry and result.retry.should_retry else "job.failed",
            {
                "status": result.status.value,
                "failure_class": result.retry.failure_class.value if result.retry else None,
            },
        )
    )
    return TerminalOut(
        job_id=job_id,
        status=result.status.value,
        attempt_id=result.attempt_id,
        release_requested=result.release_requested,
        retry=result.retry.as_dict() if result.retry else None,
    )


@router.post("/claims/{claim_id}/renew", response_model=HeartbeatOut)
@router.post("/{worker_id}/claims/{claim_id}/renew", response_model=HeartbeatOut)
async def renew_lease(
    claim_id: str,
    worker: CurrentWorker,
    session: DbSession,
    settings: AppSettings,
    worker_id: uuid.UUID | None = None,
) -> HeartbeatOut:
    """Extend a single lease without a full heartbeat."""
    if worker_id is not None and worker.id != worker_id:
        raise HTTPException(status_code=403, detail="a worker may only renew its own leases")
    repo = JobRepository(session)
    job = await repo.get_by_claim_id(claim_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown claim")
    try:
        claim = await repo.renew_lease(
            job,
            claim_id=claim_id,
            worker_id=worker.id,
            lease_ttl_seconds=settings.lease_ttl_seconds,
        )
    except LeaseExpiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return HeartbeatOut(
        worker_id=worker.id,
        server_time=claim.expires_at,
        lease_ttl_seconds=settings.lease_ttl_seconds,
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
        renewed_claim_ids=[claim_id],
    )


@router.post("/credentials")
@router.post("/{worker_id}/credentials")
async def rotate_own_credential(
    worker: CurrentWorker,
    session: DbSession,
    settings: AppSettings,
    worker_id: uuid.UUID | None = None,
) -> dict[str, str]:
    """Worker-initiated credential rotation (e.g. after a suspected leak)."""
    if worker_id is not None and worker.id != worker_id:
        raise HTTPException(status_code=403, detail="a worker may only rotate its own credential")
    credential = await WorkerRepository(session).rotate_credential(
        worker.id, ttl_hours=settings.worker_credential_ttl_hours
    )
    logger.info("worker.credential_self_rotated", worker_id=str(worker.id))
    await session.commit()
    return {
        "worker_id": str(credential.worker_id),
        "credential": credential.credential,
        "prefix": credential.prefix,
        "expires_at": str(credential.expires_at),
    }


# --- reads (user-authenticated) -----------------------------------------
read_router = APIRouter(prefix="/workers", tags=["workers"])
#: Contract path for worker reports: ``POST /api/v1/jobs/{id}/{progress,complete,fail}``.


@read_router.get("", response_model=WorkerListOut)
async def list_workers(
    session: DbSession,
    principal: require_permissions(PermissionCode.WORKER_READ.value),
    status_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> WorkerListOut:
    page = await WorkerRepository(session).list(status=status_filter, limit=limit, offset=offset)
    return WorkerListOut(
        items=[WorkerOut.model_validate(worker) for worker in page.items],
        total=page.total,
    )


@read_router.get("/{worker_id}", response_model=WorkerOut)
async def get_worker(
    worker_id: uuid.UUID,
    session: DbSession,
    principal: require_permissions(PermissionCode.WORKER_READ.value),
) -> WorkerOut:
    worker = await WorkerRepository(session).get(worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="worker not found")
    return WorkerOut.model_validate(worker)


@read_router.post("/{worker_id}/disable", response_model=WorkerOut)
async def disable_worker(
    worker_id: uuid.UUID,
    session: DbSession,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.WORKER_ADMIN.value),
    disabled: bool = True,
) -> WorkerOut:
    repo = WorkerRepository(session)
    await repo.set_disabled(worker_id, disabled=disabled)
    worker = await repo.get(worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="worker not found")
    logger.info("worker.disabled_toggled", worker_id=str(worker_id), disabled=disabled)
    await session.commit()
    return WorkerOut.model_validate(worker)


@read_router.post("/{worker_id}/rotate-credential")
async def rotate_credential(
    worker_id: uuid.UUID,
    session: DbSession,
    settings: AppSettings,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.WORKER_ADMIN.value),
) -> dict[str, str]:
    """Rotate a worker credential. The old one stops working immediately."""
    credential = await WorkerRepository(session).rotate_credential(
        worker_id, ttl_hours=settings.worker_credential_ttl_hours
    )
    logger.info("worker.credential_rotated", worker_id=str(worker_id))
    await session.commit()
    return {
        "worker_id": str(credential.worker_id),
        "credential": credential.credential,
        "prefix": credential.prefix,
        "expires_at": str(credential.expires_at),
    }


def _decode_output(item: OutputDescriptor) -> dict:
    """Turn a wire output descriptor into the media service's dict form."""
    payload: dict = {
        "kind": item.kind,
        "mime_type": item.mime_type,
        "filename": item.filename,
        "asset_role": item.asset_role,
        "metadata": dict(item.metadata),
    }
    if item.kind == "url":
        payload["url"] = item.url
        return payload
    if item.content_base64 is not None:
        try:
            payload["content"] = base64.b64decode(item.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"invalid base64 payload: {exc}") from exc
    return payload


def _job_event(job_id: uuid.UUID, event_type: str, payload: dict) -> Event:
    return Event(event_type=event_type, job_id=job_id, payload=payload)


__all__ = ["REGISTRATION_HEADER", "admin_router", "job_router", "read_router", "router"]
