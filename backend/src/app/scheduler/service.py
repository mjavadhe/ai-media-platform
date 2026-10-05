"""The Scheduler: GPU placement, worker-claim execution, release Tx2, recovery.

Ownership summary (contract sections 5.1 / 6):

===============================  ==============================================
Responsibility                   Actor
===============================  ==============================================
``gpu_devices.status``           Scheduler only (this module)
Reservation / ``RESERVED``       Scheduler
Worker claim -> ``STARTING``     Scheduler (the claim is only the trigger)
Expired lease / lost worker      Scheduler (it detected the problem)
Release Tx2 (``RELEASED``)       Scheduler
Request release (Tx1)            Orchestrator
``RUNNING``/``COMPLETED``/...    Orchestrator
===============================  ==============================================

Every transaction boundary is explicit. Nothing in this module writes a job
state it is not permitted to write — the state machine rejects it.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import (
    FailureClass,
    GpuStatus,
    JobStatus,
    ReservationState,
    SchedulingPolicy,
    WorkerStatus,
)
from app.domain.retry import decide_retry
from app.infrastructure.config import Settings
from app.infrastructure.logging import get_logger
from app.persistence.models_job import GpuDevice, Job
from app.persistence.repositories.gpu import GpuRepository, ReservationConflictError
from app.persistence.repositories.jobs import JobRepository, StartConflictError
from app.persistence.repositories.workers import WorkerRepository
from app.scheduler.discovery import DiscoveryService

logger = get_logger(__name__)


class NoCapacityError(Exception):
    """No GPU currently satisfies the job's requirements."""


@dataclass(slots=True)
class LoopStats:
    """Per-iteration counters, surfaced through /health and metrics."""

    iterations: int = 0
    scheduled: int = 0
    started: int = 0
    released: int = 0
    recovered: int = 0
    cancelled: int = 0
    discovered: int = 0
    unreservable: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "iterations": self.iterations,
            "scheduled": self.scheduled,
            "started": self.started,
            "released": self.released,
            "recovered": self.recovered,
            "cancelled": self.cancelled,
            "discovered": self.discovered,
            "unreservable": self.unreservable,
            "errors": self.errors[-10:],
        }


class SchedulerService:
    """One iteration of every Scheduler responsibility.

    Exposed as ``run_once`` so tests can drive the loop deterministically without
    sleeping, and so recovery can be triggered on demand.
    """

    def __init__(self, session: AsyncSession, backend: Any, settings: Settings) -> None:
        self._session = session
        self._backend = backend
        self._settings = settings
        self._jobs = JobRepository(session)
        self._gpu = GpuRepository(session)
        self._workers = WorkerRepository(session)
        self._discovery = DiscoveryService(session, backend)
        self.stats = LoopStats()

    # ------------------------------------------------------------------
    # 1. Discovery
    # ------------------------------------------------------------------
    async def discover(self) -> None:
        report = await self._discovery.discover()
        self.stats.discovered = report.devices_updated
        if report.errors:
            self.stats.errors.extend(report.errors)

    # ------------------------------------------------------------------
    # 2. Release Tx2 (highest priority: never leak a GPU)
    # ------------------------------------------------------------------
    async def complete_pending_releases(self) -> int:
        """Finish releases requested by the Orchestrator in Tx1.

        Runs first in every iteration so a crashed Tx1 cannot hold a GPU longer
        than one iteration. Idempotent by construction: ``complete_release`` is a
        no-op for an already-released reservation and refuses to free a device
        that has been re-reserved for another job.
        """
        released = 0
        reservations = await self._gpu.get_pending_releases(
            older_than_seconds=self._settings.scheduler_release_pending_grace_seconds
        )
        for reservation in reservations:
            try:
                if await self._gpu.complete_release(reservation.id):
                    released += 1
                    logger.info(
                        "gpu.released",
                        reservation_id=str(reservation.id),
                        gpu_device_id=str(reservation.gpu_device_id),
                    )
            except ReservationConflictError as exc:
                self.stats.errors.append(f"release {reservation.id}: {exc}")
        self.stats.released += released
        return released

    # ------------------------------------------------------------------
    # 3. Scheduling claim + reservation
    # ------------------------------------------------------------------
    async def schedule_one(self) -> bool:
        """Claim one ``QUEUED`` job and reserve a GPU for it.

        Returns True when a job was reserved. A job that cannot be placed is put
        back on the queue with a short delay rather than failing, so a temporary
        capacity shortage does not burn the retry budget.
        """
        claimed = await self._jobs.claim_next_queued()
        if claimed is None:
            return False
        job = claimed.job
        policy = job.policy or SchedulingPolicy.AUTO.value
        try:
            candidate = await self._gpu.find_candidate(
                required_vram_mb=job.required_vram_mb,
                required_capabilities=list(job.required_capabilities or []),
                preferred_node_ids=[uuid.UUID(str(n)) for n in (job.preferred_node_ids or [])],
                preferred_gpu_uuids=list(job.preferred_gpu_ids or []),
                policy=policy,
            )
        except ValueError as exc:
            # A malformed pin is a user error, not a capacity problem.
            await self._fail_job(job, failure_class=FailureClass.VALIDATION_ERROR, reason=str(exc))
            return False

        if candidate is None:
            self.stats.unreservable += 1
            await self._jobs.mark_unreservable(
                job,
                reason="no GPU satisfies the job's VRAM/capability requirements",
            )
            logger.info(
                "job.no_capacity",
                job_id=str(job.id),
                required_vram_mb=job.required_vram_mb,
                required_capabilities=list(job.required_capabilities or []),
            )
            return False

        device = await self._gpu.get_device(candidate.device_id, lock=True)
        if device is None:  # pragma: no cover - candidate came from the same tx
            await self._jobs.mark_unreservable(job, reason="device vanished during reservation")
            return False

        reservation = await self._gpu.reserve(
            job=job,
            device=device,
            node=await self._node(candidate.node_id),
            policy=policy,
            scores={
                "score": candidate.score,
                "vram_total_mb": candidate.vram_total_mb,
                "compute_capability": candidate.compute_capability,
                "node_id": str(candidate.node_id),
                "policy": policy,
            },
        )
        await self._jobs.mark_reserved(job, reservation=reservation, policy=policy)
        self.stats.scheduled += 1
        logger.info(
            "job.reserved",
            job_id=str(job.id),
            gpu_device_id=str(device.id),
            node_id=str(candidate.node_id),
            policy=policy,
            score=candidate.score,
        )
        return True

    async def _node(self, node_id: uuid.UUID) -> Any:
        from app.persistence.models_job import GpuNode

        node = await self._session.get(GpuNode, node_id)
        if node is None:  # pragma: no cover - FK guarantees existence
            raise NoCapacityError(f"node {node_id} not found")
        return node

    async def _fail_job(self, job: Job, *, failure_class: FailureClass, reason: str) -> None:
        decision = decide_retry(
            failure_class,
            attempt_no=job.retry_count + 1,
            max_retries=job.max_retries,
            base_seconds=self._settings.job_retry_base_seconds,
            max_seconds=self._settings.job_retry_max_seconds,
        )
        if decision.should_retry:
            await self._jobs.requeue_for_recovery(
                job,
                attempt=None,
                reason=reason,
                failure_class=failure_class.value,
                visible_delay_seconds=decision.delay_seconds,
            )
        else:
            await self._jobs.fail_from_recovery(
                job,
                attempt=None,
                reason=reason,
                failure_class=failure_class.value,
            )

    # ------------------------------------------------------------------
    # 4. Worker claim (Scheduler-executed start transaction)
    # ------------------------------------------------------------------
    async def start_for_worker(
        self,
        *,
        worker_id: uuid.UUID,
        job_id: uuid.UUID | None = None,
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute the worker claim. This is the only path to ``STARTING``.

        The worker chooses nothing: it asks "give me work" (or names the job it
        was woken for) and the Scheduler validates the reservation, opens the
        attempt, creates the lease and flips both the reservation and the device
        to ``BUSY``.
        """
        worker = await self._workers.get(worker_id)
        if worker is None:
            raise StartConflictError(f"worker {worker_id} not found")
        if worker.disabled_at is not None:
            raise StartConflictError("worker is disabled")

        target = await self._jobs.get(job_id, lock=True) if job_id else None
        if target is None:
            target = await self._next_reserved_for(worker)
        if target is None:
            return None

        reservation = await self._gpu.get_reservation_for_job(target.id, lock=True)
        if reservation is None:
            # The GPU side vanished (e.g. already released). Put the job back.
            await self._jobs.requeue_for_recovery(
                target,
                attempt=None,
                reason="reservation missing at start time",
                failure_class=FailureClass.INFRASTRUCTURE.value,
                increment_retry=False,
            )
            raise StartConflictError("no live reservation for this job")

        if reservation.node_id != worker.node_id and worker.node_id is not None:
            # Worker on a different node: it cannot own this GPU. Requeue rather
            # than start work it is not equipped to run.
            await self._jobs.requeue_for_recovery(
                target,
                attempt=None,
                reason="reservation is on another node",
                failure_class=FailureClass.INFRASTRUCTURE.value,
                increment_retry=False,
            )
            raise StartConflictError("reservation belongs to a different node")

        started = await self._jobs.start_for_worker(
            target.id,
            worker=worker,
            reservation=reservation,
            lease_ttl_seconds=self._settings.lease_ttl_seconds,
            capabilities=capabilities,
        )
        device = await self._gpu.get_device(reservation.gpu_device_id, lock=True)
        if device is not None:
            # Scheduler is the only writer of gpu_devices.status: RESERVED -> BUSY.
            device.status = GpuStatus.BUSY.value
        reservation.state = ReservationState.BUSY.value
        await self._session.flush()

        self.stats.started += 1
        logger.info(
            "job.started",
            job_id=str(target.id),
            attempt_id=str(started.attempt.id),
            worker_id=str(worker.id),
            claim_id=started.claim_id,
            lease_expires_at=started.lease_expires_at.isoformat(),
        )
        return {
            "job_id": str(target.id),
            "attempt_id": str(started.attempt.id),
            "attempt_no": started.attempt.attempt_no,
            "claim_id": started.claim_id,
            "lease_expires_at": started.lease_expires_at,
            "lease_ttl_seconds": self._settings.lease_ttl_seconds,
            "heartbeat_interval_seconds": self._settings.heartbeat_interval_seconds,
            "gpu_device_id": str(reservation.gpu_device_id),
            "node_id": str(reservation.node_id),
            "job_type": target.job_type,
            "params": dict(target.params or {}),
            "prompt": target.prompt,
            "negative_prompt": target.negative_prompt,
            "model_version_id": str(target.model_version_id) if target.model_version_id else None,
            "workflow_version_id": (
                str(target.workflow_version_id) if target.workflow_version_id else None
            ),
        }

    async def _next_reserved_for(self, worker: Any) -> Job | None:
        from sqlalchemy import select

        stmt = (
            select(Job)
            .where(Job.status == JobStatus.RESERVED.value)
            .order_by(Job.priority_rank.desc(), Job.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if worker.node_id is not None:
            stmt = stmt.where(Job.node_id == worker.node_id)
        return await self._session.scalar(stmt)

    # ------------------------------------------------------------------
    # 5. Recovery: expired leases
    # ------------------------------------------------------------------
    async def recover_expired_leases(self) -> int:
        """Requeue jobs whose worker lease expired, in one transaction per job.

        Everything the Scheduler needs is done here in the *same* transaction:
        requeue the job, mark the attempt interrupted, release the lease and
        free the GPU. That avoids depending on the reconciler for the common
        case; the reconciler only exists to cover an Orchestrator Tx1 that
        committed without its Tx2.
        """
        claims = await self._jobs.find_expired_leases()
        recovered = 0
        for claim in claims:
            job = await self._jobs.get(claim.job_id, lock=True)
            if job is None:
                await self._jobs.release_claim(claim.job_id, reason="job_missing")
                continue
            if JobStatus(job.status) in {JobStatus.QUEUED, JobStatus.CREATED}:
                await self._jobs.release_claim(job.id, reason="already_requeued")
                continue

            attempt = await self._jobs.get_open_attempt(job.id)
            decision = decide_retry(
                FailureClass.LEASE_EXPIRED,
                attempt_no=job.retry_count + 1,
                max_retries=job.max_retries,
                base_seconds=self._settings.job_retry_base_seconds,
                max_seconds=self._settings.job_retry_max_seconds,
            )
            if decision.should_retry:
                await self._jobs.requeue_for_recovery(
                    job,
                    attempt=attempt,
                    reason=f"lease expired for worker {claim.worker_id}",
                    failure_class=FailureClass.LEASE_EXPIRED.value,
                    visible_delay_seconds=decision.delay_seconds,
                )
                await self._jobs.release_claim(job.id, reason="lease_expired")
            else:
                await self._jobs.fail_from_recovery(
                    job,
                    attempt=attempt,
                    reason="lease expired and retry budget exhausted",
                    failure_class=FailureClass.LEASE_EXPIRED.value,
                )
                await self._jobs.release_claim(job.id, reason="lease_expired")

            # Same-transaction GPU release: the recovery actor is the Scheduler,
            # so it completes Tx2 itself instead of leaving a pending request.
            await self._gpu.complete_release_for_job(job.id)
            recovered += 1
            logger.info(
                "job.recovered_lease_expired",
                job_id=str(job.id),
                worker_id=str(claim.worker_id),
                retry=decision.should_retry,
            )
        self.stats.recovered += recovered
        return recovered

    # ------------------------------------------------------------------
    # 6. Recovery: silent workers
    # ------------------------------------------------------------------
    async def recover_lost_workers(self) -> int:
        """Flip workers that missed heartbeats to OFFLINE and release their jobs."""
        grace = (
            self._settings.heartbeat_interval_seconds * self._settings.heartbeat_missed_threshold
        )
        workers = await self._workers.find_stale(heartbeat_grace_seconds=grace)
        recovered = 0
        for worker in workers:
            await self._workers.set_status(worker.id, WorkerStatus.OFFLINE)
            logger.warning("worker.offline", worker_id=str(worker.id), grace_seconds=grace)
            from sqlalchemy import select

            jobs = await self._session.scalars(
                select(Job).where(
                    Job.worker_id == worker.id,
                    Job.status.in_(
                        [
                            JobStatus.STARTING.value,
                            JobStatus.RUNNING.value,
                            JobStatus.RESERVED.value,
                        ]
                    ),
                )
            )
            for job in jobs.all():
                await self._requeue_lost_job(job, reason=f"worker {worker.name} went offline")
                recovered += 1
        self.stats.recovered += recovered
        return recovered

    async def _requeue_lost_job(self, job: Job, *, reason: str) -> None:
        attempt = await self._jobs.get_open_attempt(job.id)
        decision = decide_retry(
            FailureClass.WORKER_CRASH,
            attempt_no=job.retry_count + 1,
            max_retries=job.max_retries,
            base_seconds=self._settings.job_retry_base_seconds,
            max_seconds=self._settings.job_retry_max_seconds,
        )
        # Same-transaction GPU release: the recovery actor is the Scheduler, so it
        # completes Tx2 itself instead of leaving a pending request for the
        # reconciler. `complete_release_for_job` is a no-op when nothing is held.
        await self._gpu.complete_release_for_job(job.id)
        await self._jobs.release_claim(job.id, reason="worker_lost")
        if decision.should_retry:
            await self._jobs.requeue_for_recovery(
                job,
                attempt=attempt,
                reason=reason,
                failure_class=FailureClass.WORKER_CRASH.value,
                visible_delay_seconds=decision.delay_seconds,
            )
        else:
            await self._jobs.fail_from_recovery(
                job,
                attempt=attempt,
                reason=f"{reason}; retry budget exhausted",
                failure_class=FailureClass.WORKER_CRASH.value,
            )

    # ------------------------------------------------------------------
    # 7. Recovery: devices whose node vanished
    # ------------------------------------------------------------------
    async def recover_orphan_devices(self) -> int:
        """Release reservations held by devices that went OFFLINE or unhealthy."""
        from sqlalchemy import select

        from app.persistence.models_job import GpuReservation

        rows = await self._session.execute(
            select(GpuReservation, GpuDevice)
            .join(GpuDevice, GpuDevice.id == GpuReservation.gpu_device_id)
            .where(
                GpuReservation.state.in_(
                    [ReservationState.RESERVED.value, ReservationState.BUSY.value]
                ),
                GpuDevice.status.in_([GpuStatus.OFFLINE.value, GpuStatus.UNHEALTHY.value]),
            )
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        recovered = 0
        for reservation, device in rows.all():
            await self._gpu.set_device_status(
                device.id, GpuStatus.UNHEALTHY, reason="orphaned reservation on unhealthy device"
            )
            # Scheduler is the detecting actor here, so it may finish Tx2 itself —
            # but only through the guarded path that moves the reservation
            # through RELEASE_PENDING. A direct complete_release() on a live
            # reservation is (correctly) rejected.
            try:
                await self._gpu.complete_release_for_job(reservation.job_id)
            except ReservationConflictError as exc:
                self.stats.errors.append(f"orphan release {reservation.id}: {exc}")
                continue
            job = await self._jobs.get(reservation.job_id, lock=True)
            if job is not None and JobStatus(job.status) not in {
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }:
                await self._jobs.requeue_for_recovery(
                    job,
                    attempt=await self._jobs.get_open_attempt(job.id),
                    reason="allocated GPU became unavailable",
                    failure_class=FailureClass.INFRASTRUCTURE.value,
                )
            recovered += 1
            logger.warning(
                "gpu.orphan_released",
                job_id=str(reservation.job_id),
                gpu_device_id=str(device.id),
            )
        self.stats.recovered += recovered
        return recovered

    # ------------------------------------------------------------------
    # 8. Telemetry refresh
    # ------------------------------------------------------------------
    async def refresh_busy_device_metrics(self) -> int:
        from sqlalchemy import select

        devices = await self._session.scalars(
            select(GpuDevice).where(GpuDevice.status == GpuStatus.BUSY.value).limit(64)
        )
        busy = list(devices.all())
        if busy:
            await self._discovery.refresh_device_telemetry(busy)
        return len(busy)

    # ------------------------------------------------------------------
    # Admin actions (still Scheduler-owned writes)
    # ------------------------------------------------------------------
    async def admin_set_device_status(
        self, *, device_id: uuid.UUID, status: GpuStatus, reason: str | None, actor: str
    ) -> Any:
        """Quarantine or return a device to the pool.

        Exposed as a Scheduler method (not an API row update) so the single-writer
        rule on ``gpu_devices.status`` keeps holding, and so any reservation that
        depends on the device is reconciled in the same transaction.
        """
        await self._gpu.set_device_status(device_id, status, reason=reason)
        if status is not GpuStatus.AVAILABLE:
            # A quarantined device must not keep holding a job hostage.
            await self.recover_orphan_devices()
        device = await self._gpu.get_device(device_id)
        if device is None:
            raise NoCapacityError(f"gpu device {device_id} not found")
        return device

    async def admin_force_release(
        self, *, device_id: uuid.UUID, reason: str | None, actor: str
    ) -> Any:
        """Force a stuck reservation to ``RELEASED`` and free the device."""
        from sqlalchemy import select

        from app.persistence.models_job import GpuReservation

        rows = await self._session.scalars(
            select(GpuReservation)
            .where(
                GpuReservation.gpu_device_id == device_id,
                GpuReservation.state.in_(
                    [ReservationState.RESERVED.value, ReservationState.BUSY.value]
                ),
            )
            .with_for_update(skip_locked=True)
        )
        reservations = list(rows.all())
        for reservation in reservations:
            await self._gpu.complete_release_for_job(reservation.job_id)
            job = await self._jobs.get(reservation.job_id, lock=True)
            if job is not None and not self._jobs.is_terminal(job):
                await self._jobs.requeue_for_recovery(
                    job,
                    attempt=await self._jobs.get_open_attempt(job.id),
                    reason=reason or "device forcibly released by an administrator",
                    failure_class=FailureClass.INFRASTRUCTURE.value,
                )
        device = await self._gpu.get_device(device_id)
        if device is None:
            raise NoCapacityError(f"gpu device {device_id} not found")
        return device

    # ------------------------------------------------------------------
    # Iteration
    # ------------------------------------------------------------------
    async def run_once(self) -> LoopStats:
        """One full pass. Each step is idempotent and independently safe."""
        self.stats.iterations += 1
        for step in (
            self.discover,
            self.complete_pending_releases,
            self.recover_expired_leases,
            self.recover_lost_workers,
            self.recover_orphan_devices,
            self.refresh_busy_device_metrics,
        ):
            try:
                await step()
            except Exception as exc:  # noqa: BLE001 - one failing step must not stop the loop
                # A failed statement poisons the surrounding transaction, so the
                # rollback must happen here or every later step fails too.
                await self._session.rollback()
                self.stats.errors.append(f"{step.__name__}: {exc}")
                logger.error("scheduler.step_failed", step=step.__name__, error=str(exc))

        # Fill capacity: keep claiming while a job can be placed.
        for _ in range(self._settings.scheduler_batch_size):
            try:
                if not await self.schedule_one():
                    break
            except Exception as exc:  # noqa: BLE001
                await self._session.rollback()
                self.stats.errors.append(f"schedule_one: {exc}")
                logger.error("scheduler.schedule_failed", error=str(exc))
                break
        return self.stats


async def scheduler_loop(
    session_factory: async_sessionmaker[AsyncSession],
    backend_factory: Any,
    settings: Settings,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run the Scheduler until ``stop_event`` is set.

    Each iteration gets a fresh session so a failure rolls back only that
    iteration's work.
    """
    backend = backend_factory()
    stop = stop_event or asyncio.Event()
    interval = settings.scheduler_loop_interval_seconds
    logger.info("scheduler.loop_started", interval=interval, gpu_backend=backend.name)
    while not stop.is_set():
        started = asyncio.get_running_loop().time()
        async with session_factory() as session:
            service = SchedulerService(session, backend, settings)
            try:
                await service.run_once()
                await session.commit()
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                logger.error("scheduler.iteration_failed", error=str(exc))
        elapsed = asyncio.get_running_loop().time() - started
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.01, interval - elapsed))
        except TimeoutError:
            continue
    await backend.close()
    logger.info("scheduler.loop_stopped")


__all__ = ["LoopStats", "NoCapacityError", "SchedulerService", "scheduler_loop"]
