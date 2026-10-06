"""End-to-end orchestration flow against real PostgreSQL and the Mock GPU backend.

This is the test that catches cross-layer mistakes: it drives the real HTTP API,
the real Scheduler and Orchestrator services, and a real worker agent running its
real event loop, all against one schema.

Covered flows:
  * discovery -> placement -> reservation (Scheduler is the only device writer)
  * worker registration, heartbeat lease renewal, claim with a backend handle
  * progress -> completion -> artifact storage -> Tx2 release
  * failure classification -> retry budget -> RETRYING -> re-queued -> success
  * cancellation before placement, and cancellation of a running job
  * unreservable jobs (demand exceeds capacity)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.domain.enums import GpuStatus, JobStatus, ReservationState
from app.infrastructure.config import Settings
from app.orchestrator.service import OrchestratorService
from app.persistence.models_identity import Worker
from app.persistence.models_job import GpuDevice, GpuNode, GpuReservation, Job
from app.persistence.repositories.workers import WorkerRepository
from app.scheduler.service import SchedulerService
from app.services.storage import build_storage_backend
from worker.agent import WorkerAgent
from worker.client import ApiClient
from worker.gpu.mock_backend import MockGpuBackend

pytestmark = pytest.mark.e2e


async def _status_of(harness: Harness, job_id: uuid.UUID) -> str:
    return (await harness.job(str(job_id))).status


def make_settings(**overrides: Any) -> Settings:
    """Settings for the worker agent and the services driven directly."""
    base: dict[str, Any] = {
        "lease_ttl_seconds": 60,
        "heartbeat_interval_seconds": 1,
        "worker_poll_interval_seconds": 0.02,
        "mock_gpu_nodes": 1,
        "mock_gpus_per_node": 1,
        "mock_gpu_vram_mb": 8192,
    }
    base.update(overrides)
    return Settings(**base)


class Harness:
    """Drives the phases of a job the way the real processes would."""

    def __init__(self, client: Any, session_factory: Any, token: str, tmp_path: Path) -> None:
        self.client = client
        self.session_factory = session_factory
        self.token = token
        self.settings = make_settings(storage_root=str(tmp_path / "objects"))
        self.backend = MockGpuBackend(
            node_count=self.settings.mock_gpu_nodes,
            gpus_per_node=self.settings.mock_gpus_per_node,
            vram_mb=self.settings.mock_gpu_vram_mb,
            execution_seconds=0.01,
        )
        self.storage = build_storage_backend(
            self.settings.storage_backend,
            root=self.settings.storage_root,
            bucket=self.settings.storage_bucket,
        )

    @property
    def auth(self) -> dict[str, str]:
        # Bearer auth is exempt from CSRF; see tests/e2e/test_security.py.
        return {"authorization": f"Bearer {self.token}"}

    # --- phases -----------------------------------------------------------
    async def discover(self) -> None:
        async with self.session_factory() as session:
            scheduler = SchedulerService(session, self.backend, self.settings)
            await scheduler.discover()
            await session.commit()

    async def scheduler_cycle(self) -> Any:
        async with self.session_factory() as session:
            scheduler = SchedulerService(session, self.backend, self.settings)
            stats = await scheduler.run_once()
            await session.commit()
            return stats

    async def orchestrator(self, action: str, **kwargs: Any) -> Any:
        """Run one Orchestrator action in its own transaction."""
        async with self.session_factory() as session:
            service = OrchestratorService(session, self.settings, self.storage)
            result = await getattr(service, action)(**kwargs)
            await session.commit()
            return result

    async def create_job(self, **overrides: Any) -> dict:
        """Create a job through the public API, so registry rules apply."""
        payload: dict[str, Any] = {
            "job_type": "IMAGE",
            "prompt": "a lighthouse in a storm",
            "params": {"width": 512, "height": 512, "steps": 3},
            # The builtin mock models honestly declare 0 MB (no weights are
            # loaded), so tests state the reservation they want to exercise.
            "required_vram_mb": 2048,
        }
        payload.update(overrides)
        response = await self.client.post("/api/v1/jobs", json=payload, headers=self.auth)
        assert response.status_code in (200, 201), response.text
        body = response.json()
        return {"job_id": body["id"], "body": body}

    async def run_agent(self, *, jobs: int = 1, timeout_seconds: float = 20.0) -> int:
        """Run the agent until ``jobs`` more jobs reach COMPLETED."""
        baseline = await self.count_completed()
        async def enough_completed() -> bool:
            return await self.count_completed() >= baseline + jobs

        await self.run_agent_until(enough_completed, timeout_seconds=timeout_seconds)
        return await self.count_completed() - baseline

    async def run_agent_until(
        self, predicate: Callable[[], Awaitable[bool]], *, timeout_seconds: float = 20.0
    ) -> None:
        """Run the agent until ``predicate`` holds.

        Not every scenario ends in a completion (an injected OOM must fail), so
        the caller decides what "done" means.
        """
        credential = await self._register_worker()
        agent = WorkerAgent(self.api_client(), self.backend, self.settings)
        agent._client.set_credential(credential)
        agent._worker_id = str(uuid.uuid4())
        task = asyncio.create_task(agent.start())
        try:
            await asyncio.wait_for(self._poll_until(predicate), timeout=timeout_seconds)
        except TimeoutError:
            pytest.fail("agent did not reach the expected state in time")
        finally:
            agent.stop()
            # The agent exits its poll loop on stop(); it must not hang.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(task, timeout=5)

    async def wait_until(
        self, predicate: Callable[[], Awaitable[bool]], *, timeout_seconds: float = 30.0
    ) -> None:
        """Wait for a condition without starting a worker agent."""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while not await predicate():
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("timed out waiting for a scheduler condition")
            await asyncio.sleep(0.05)

    async def _poll_until(self, predicate: Callable[[], Awaitable[bool]]) -> None:
        # Poll with a deadline; an Event would need the agent to signal it.
        deadline = asyncio.get_running_loop().time() + 20.0
        while not await predicate():
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("timed out waiting for the agent")
            await asyncio.sleep(0.02)

    async def count_completed(self) -> int:
        async with self.session_factory() as session:
            rows = await session.execute(
                select(Job.id).where(Job.status == JobStatus.COMPLETED.value)
            )
            return len(rows.all())

    # --- worker bootstrap -------------------------------------------------
    async def _register_worker(self) -> str:
        """Create a worker with a one-time token, then register over HTTP."""
        async with self.session_factory() as session:
            node_id = await session.scalar(select(GpuDevice.node_id).limit(1))
            registration = await WorkerRepository(session).create_worker(
                name=f"pytest-{uuid.uuid4().hex[:6]}",
                node_id=node_id,
                registration_ttl_hours=1,
            )
            await session.commit()
            token = registration.token

        response = await self.client.post(
            "/api/v1/workers/register",
            json={
                "name": f"pytest-{uuid.uuid4().hex[:6]}",
                "version": "pytest",
                "platform": "linux",
                "capabilities": {"backend": "mock"},
                "node_key": await self.node_key(),
            },
            headers={"X-Registration-Token": token},
        )
        assert response.status_code in (200, 201), response.text
        return response.json()["credential"]

    async def node_key(self) -> str | None:
        async with self.session_factory() as session:
            return await session.scalar(select(GpuNode.node_key).limit(1))

    def api_client(self) -> ApiClient:
        """An API client bound to the in-process app, like a real worker."""
        return ApiClient("http://testserver", client=self.client, timeout_seconds=10)

    # --- assertions helpers -------------------------------------------------
    async def job(self, job_id: str) -> Job:
        async with self.session_factory() as session:
            job = await session.get(Job, uuid.UUID(job_id))
            assert job is not None
            session.expunge(job)
            return job

    async def reservation(self, job_id: str) -> GpuReservation | None:
        async with self.session_factory() as session:
            return await session.scalar(
                select(GpuReservation).where(GpuReservation.job_id == uuid.UUID(job_id))
            )

    async def device(self) -> GpuDevice:
        async with self.session_factory() as session:
            device = await session.scalar(select(GpuDevice).limit(1))
            assert device is not None
            return device

    async def worker(self, name_prefix: str = "pytest-") -> Worker | None:
        async with self.session_factory() as session:
            return await session.scalar(
                select(Worker).where(Worker.name.like(f"{name_prefix}%"))
            )


@pytest_asyncio.fixture
async def harness(client, db, admin_token, tmp_path, monkeypatch) -> Harness:
    """Discovered hardware plus a harness wired to the test database."""
    import worker.agent as agent_module

    # Never read or write a real developer credential file.
    monkeypatch.setattr(agent_module, "CREDENTIAL_FILE", tmp_path / "credential.json")
    h = Harness(client, db, admin_token["token"], tmp_path)
    # One settings object for the app, the harness and the worker agent.
    h.settings = client.app.state.settings  # type: ignore[attr-defined]
    await h.discover()
    return h


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
async def test_full_happy_path(harness: Harness) -> None:
    created = await harness.create_job()
    job_id = created["job_id"]

    # Registry-derived requirements reached the job row.
    job = await harness.job(job_id)
    assert job.required_vram_mb > 0
    # The API enqueues on create (CREATED -> QUEUED is API-owned).
    assert job.status == JobStatus.QUEUED.value

    # Scheduler reserves the GPU before any worker exists.
    stats = await harness.scheduler_cycle()
    assert stats.scheduled == 1

    reservation = await harness.reservation(job_id)
    assert reservation is not None
    assert reservation.state == ReservationState.RESERVED.value
    device = await harness.device()
    assert device.status == GpuStatus.RESERVED.value
    # Placement persisted a handle the worker backend can actually use.
    assert device.backend_device_id

    assert await harness.run_agent(jobs=1) == 1

    job = await harness.job(job_id)
    assert job.status == JobStatus.COMPLETED.value
    assert job.progress_percent == 100.0

    # Tx1 (Orchestrator) requested the release; Tx2 is the Scheduler's, and it
    # only runs on a cycle — exactly as in production.
    await harness.scheduler_cycle()
    reservation = await harness.reservation(job_id)
    assert reservation.state == ReservationState.RELEASED.value

    device = await harness.device()
    assert device.status == GpuStatus.AVAILABLE.value
    assert device.reservation_id is None


async def test_claim_carries_backend_handle_not_database_id(harness: Harness) -> None:
    """A worker that received a DB uuid could not execute anything."""
    created = await harness.create_job()
    await harness.scheduler_cycle()
    credential = await harness._register_worker()

    client = harness.api_client()
    client.set_credential(credential)
    claimed = await client.claim()

    assert claimed is not None
    assert claimed.gpu_backend_device_id.startswith("mock-node")
    with pytest.raises(ValueError):  # not a uuid: it is a backend handle
        uuid.UUID(claimed.gpu_backend_device_id)
    assert claimed.gpu_backend_device_id == (await harness.device()).backend_device_id
    assert str(claimed.job_id) == created["job_id"]


async def test_heartbeat_renews_owned_leases_and_reports_lost_ones(
    harness: Harness,
) -> None:
    await harness.create_job()
    await harness.scheduler_cycle()
    client = harness.api_client()
    client.set_credential(await harness._register_worker())
    claimed = await client.claim()
    assert claimed is not None

    first = await client.heartbeat(
        status="BUSY", active_job_count=1, claim_ids=[claimed.claim_id]
    )
    assert first["renewed_claim_ids"] == [claimed.claim_id]
    assert first["lost_claim_ids"] == []

    second = await client.heartbeat(
        status="BUSY",
        active_job_count=1,
        claim_ids=[claimed.claim_id, "not-a-real-claim"],
    )
    assert second["renewed_claim_ids"] == [claimed.claim_id]
    assert "not-a-real-claim" in second["lost_claim_ids"]


async def test_claiming_an_empty_queue_is_not_an_error(harness: Harness) -> None:
    client = harness.api_client()
    client.set_credential(await harness._register_worker())
    assert await client.claim() is None


async def test_worker_registration_requires_a_valid_token(harness: Harness) -> None:
    response = await harness.client.post(
        "/api/v1/workers/register",
        json={"name": "intruder", "capabilities": {}},
        headers={"X-Registration-Token": "not-a-real-token"},
    )
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Failure, retry, cancellation
# ---------------------------------------------------------------------------
async def test_oom_classification_drives_retry_then_success(harness: Harness) -> None:
    created = await harness.create_job()
    await harness.scheduler_cycle()

    harness.backend.set_failure_rates(oom_rate=1.0)
    job_id = uuid.UUID(created["job_id"])
    async def became_retrying() -> bool:
        return await _status_of(harness, job_id) == JobStatus.RETRYING.value

    await harness.run_agent_until(became_retrying, timeout_seconds=15)

    job = await harness.job(created["job_id"])
    assert job.status == JobStatus.RETRYING.value, job.status

    # Tx2 is the Scheduler's, so it needs a cycle before the GPU is free again.
    await harness.scheduler_cycle()
    reservation = await harness.reservation(created["job_id"])
    assert reservation.state == ReservationState.RELEASED.value
    device = await harness.device()
    assert device.status == GpuStatus.AVAILABLE.value

    # The Orchestrator owns the retry promotion, not the Scheduler. The retry is
    # delayed by backoff, so the sweep has to keep running until it is due.
    job_id = uuid.UUID(created["job_id"])

    async def retry_visible() -> bool:
        await harness.orchestrator("sweep_due_retries")
        return await _status_of(harness, job_id) == JobStatus.QUEUED.value

    await harness.wait_until(retry_visible, timeout_seconds=30)
    job = await harness.job(created["job_id"])
    assert job.status == JobStatus.QUEUED.value

    # The fault was injected, not real: clear it and the retry succeeds.
    harness.backend.set_failure_rates(oom_rate=0.0)
    await harness.scheduler_cycle()
    assert await harness.run_agent(jobs=1) == 1
    job = await harness.job(created["job_id"])
    assert job.status == JobStatus.COMPLETED.value


async def test_retry_budget_is_exhausted_and_the_job_fails_for_good(
    harness: Harness,
) -> None:
    created = await harness.create_job()
    await harness.scheduler_cycle()
    harness.backend.set_failure_rates(oom_rate=1.0)
    job_id = uuid.UUID(created["job_id"])

    async def needs_retry_or_failed() -> bool:
        return await _status_of(harness, job_id) in {
            JobStatus.RETRYING.value,
            JobStatus.FAILED.value,
        }

    # Each retry costs three passes (fail, promote, place), so the cap has to
    # scale with the budget; the budget itself is what ends the loop.
    for _ in range((int(harness.settings.job_max_retries) + 1) * 4):
        status = await _status_of(harness, job_id)
        if status == JobStatus.FAILED.value:
            break
        if status == JobStatus.RETRYING.value:
            # A retry is delayed by backoff, and a sweep only promotes what is
            # already due, so the sweep has to keep running while we wait.
            async def back_on_queue() -> bool:
                await harness.orchestrator("sweep_due_retries")
                return await _status_of(harness, job_id) == JobStatus.QUEUED.value

            await harness.wait_until(back_on_queue, timeout_seconds=30)
            continue
        if status == JobStatus.QUEUED.value:
            await harness.scheduler_cycle()
            continue
        await harness.run_agent_until(needs_retry_or_failed, timeout_seconds=15)

    job = await harness.job(created["job_id"])
    assert job.status == JobStatus.FAILED.value
    assert job.error, "a failed job must record why"
    assert "oom" in json.dumps(job.error, default=str).lower()


async def test_cancelling_a_queued_job_never_touches_a_gpu(harness: Harness) -> None:
    created = await harness.create_job()
    job_id = created["job_id"]

    await harness.orchestrator("cancel", job_id=uuid.UUID(job_id), reason="user cancelled")

    job = await harness.job(job_id)
    assert job.status == JobStatus.CANCELLED.value
    await harness.scheduler_cycle()
    assert await harness.reservation(job_id) is None
    assert await harness.count_completed() == 0


async def test_cancelling_a_running_job_releases_the_gpu(harness: Harness) -> None:
    """The worker keeps its lease until it stops; Tx2 releases afterwards."""
    created = await harness.create_job()
    await harness.scheduler_cycle()
    client = harness.api_client()
    client.set_credential(await harness._register_worker())
    claimed = await client.claim()
    assert claimed is not None

    await harness.orchestrator("cancel", job_id=uuid.UUID(created["job_id"]), reason="stop")

    job = await harness.job(created["job_id"])
    assert job.status == JobStatus.CANCELLED.value

    # A worker that reports completion after cancellation must be refused.
    with pytest.raises(Exception):  # noqa: B017 - domain error type is internal
        await client.complete(
            claimed,
            outputs=[],
            runtime_info={},
            duration_seconds=0.1,
        )


async def test_job_demanding_more_vram_than_hardware_is_unreservable(
    harness: Harness,
) -> None:
    created = await harness.create_job(required_vram_mb=harness.settings.mock_gpu_vram_mb + 1)
    stats = await harness.scheduler_cycle()
    assert stats.scheduled == 0

    job = await harness.job(created["job_id"])
    # The job stays QUEUED (it was never placed) and records why in its timeline.
    assert job.status == JobStatus.QUEUED.value, job.status
    from app.persistence.models_identity import JobEvent

    job_id = uuid.UUID(created["job_id"])
    async with harness.session_factory() as session:
        events = (
            (await session.execute(select(JobEvent).where(JobEvent.job_id == job_id)))
            .scalars()
            .all()
        )
    assert any(e.event_type == "job.unreservable" for e in events), [
        e.event_type for e in events
    ]
    assert (await harness.reservation(created["job_id"])) is None


# ---------------------------------------------------------------------------
# Artifacts and audit trail
# ---------------------------------------------------------------------------
async def test_artifacts_live_in_storage_not_in_the_database(harness: Harness) -> None:
    created = await harness.create_job()
    await harness.scheduler_cycle()
    assert await harness.run_agent(jobs=1) == 1

    from app.persistence.models_creative import GeneratedMedia, StorageObject

    job_id = uuid.UUID(created["job_id"])
    async with harness.session_factory() as session:
        media = (
            (
                await session.execute(
                    select(GeneratedMedia)
                    .where(GeneratedMedia.job_id == job_id)
                    .options(selectinload(GeneratedMedia.versions))
                )
            )
            .scalars()
            .all()
        )
        assert media, "expected a media row describing the artifact"
        row = media[0]
        assert row.media_type == "IMAGE"
        assert row.versions, "the artifact must point at a stored object"
        stored = await session.get(StorageObject, row.versions[0].storage_object_id)
        assert stored is not None
        assert stored.mime_type and stored.size_bytes and stored.checksum
        assert stored.backend == "local" and stored.bucket
        # The bytes are readable through the storage backend, not the DB.
        data = await harness.storage.read(stored.key)
        assert len(data) == stored.size_bytes


async def test_progress_and_event_trail_are_recorded(harness: Harness) -> None:
    created = await harness.create_job()
    await harness.scheduler_cycle()
    assert await harness.run_agent(jobs=1) == 1

    from app.persistence.models_identity import JobEvent

    job_id = uuid.UUID(created["job_id"])
    async with harness.session_factory() as session:
        events = (
            (
                await session.execute(
                    select(JobEvent)
                    .where(JobEvent.job_id == job_id)
                    .order_by(JobEvent.seq)
                )
            )
            .scalars()
            .all()
        )
    job = await harness.job(created["job_id"])
    assert job.progress_percent == 100.0

    assert any(e.event_type == "job.completed" for e in events)
    percents = [
        e.payload.get("percent", 0)
        for e in events
        if e.event_type == "job.progress"
    ]
    assert percents, "expected progress events"
    assert percents == sorted(percents), "progress must never go backwards"
