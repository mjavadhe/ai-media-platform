"""Worker agent process (``app-worker``).

The agent is deliberately dumb about policy: it registers, heartbeats, asks for
work, executes it on the backend it was handed, and reports the outcome. It never
decides placement, retries or cancellation — those belong to the Scheduler and
Orchestrator.

The heartbeat loop is the critical part. While a job runs, the agent renews the
lease on a fixed cadence; if a renewal fails or the API rejects the credential,
the agent abandons the job instead of finishing work whose lease has expired,
because the Scheduler may already have re-queued and re-assigned it.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import platform
import signal
import time
from pathlib import Path
from typing import Any

from app.infrastructure.config import Settings, get_settings
from app.infrastructure.logging import get_logger
from worker.client import (
    ApiClient,
    ApiUnavailableError,
    ClaimedJob,
    CredentialRejectedError,
    StaleClaimError,
)
from worker.gpu.base import GpuBackend
from worker.gpu.mock_backend import MockGpuError, MockGpuOomError
from worker.gpu.registry import build_gpu_backend

logger = get_logger("worker")

#: Credential cache, so a restart does not need a new registration token.
CREDENTIAL_FILE = Path(os.environ.get("APP_WORKER_STATE_DIR", ".var/worker")) / "credential.json"

#: Failure classes the Orchestrator understands (spec section 14).
FAILURE_OOM = "OOM"
FAILURE_INFRASTRUCTURE = "INFRASTRUCTURE"
FAILURE_VALIDATION = "VALIDATION"
FAILURE_UNKNOWN = "UNKNOWN"


class WorkerAgent:
    """One agent process: register, poll, execute, report."""

    def __init__(
        self,
        client: ApiClient,
        backend: GpuBackend,
        settings: Settings,
        *,
        registration_token: str | None = None,
        worker_name: str | None = None,
    ) -> None:
        self._client = client
        self._backend = backend
        self._settings = settings
        self._registration_token = registration_token
        self._worker_name = worker_name or settings.worker_name
        self._worker_id: str | None = None
        self._stop = asyncio.Event()
        self._current: ClaimedJob | None = None
        self._abort = asyncio.Event()

    # --- lifecycle --------------------------------------------------------
    @property
    def worker_id(self) -> str | None:
        return self._worker_id

    async def start(self) -> None:
        await self._ensure_registered()
        logger.info(
            "worker.started",
            worker_id=self._worker_id,
            backend=self._backend.name,
            base_url=self._settings.worker_base_url,
        )
        heartbeat = asyncio.create_task(self._heartbeat_loop(), name="worker-heartbeat")
        try:
            while not self._stop.is_set():
                try:
                    job = await self._client.claim()
                except CredentialRejectedError:
                    logger.error("worker.credential_rejected")
                    raise
                except (ApiUnavailableError, StaleClaimError) as exc:
                    logger.warning("worker.claim_failed", error=str(exc))
                    await self._sleep(self._settings.worker_poll_interval_seconds)
                    continue
                if job is None:
                    await self._sleep(self._settings.worker_poll_interval_seconds)
                    continue
                await self._execute(job)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    def stop(self) -> None:
        self._stop.set()
        self._abort.set()

    # --- registration -----------------------------------------------------
    async def _ensure_registered(self) -> None:
        """Adopt an existing credential if there is one, otherwise register.

        Preference order: a credential handed to the client, then the on-disk
        cache, then a fresh registration. A cached credential is only trusted
        until the API rejects it; after that the agent needs the token again.
        """
        existing = self._client.credential or self._load_credential()
        if existing:
            self._client.set_credential(existing)
            try:
                self._worker_id = str((await self._client.me())["id"])
                logger.info("worker.credential_adopted", worker_id=self._worker_id)
                return
            except CredentialRejectedError:
                self._client.set_credential("")
                logger.warning("worker.cached_credential_rejected")
            except ApiUnavailableError as exc:
                # Do not discard a good credential over a network blip.
                raise RuntimeError(f"cannot verify worker credential: {exc}") from exc

        if not self._registration_token:
            raise RuntimeError(
                "no registration token available; set APP_WORKER_REGISTRATION_TOKEN or "
                "provide a cached credential"
            )
        capabilities = await self._describe_capabilities()
        payload = await self._client.register(
            registration_token=self._registration_token,
            name=self._worker_name,
            version=_version(),
            platform=f"{platform.system()} {platform.release()}",
            capabilities=capabilities,
        )
        self._worker_id = payload["worker_id"]
        self._save_credential(payload["credential"])
        logger.info("worker.registered", worker_id=self._worker_id)

    async def _describe_capabilities(self) -> dict[str, Any]:
        """What this node can do, as data. Never a GPU-name conditional."""
        health = await self._backend.health()
        devices = []
        for device in await self._backend.list_devices():
            devices.append(
                {
                    "device_id": device.device_id,
                    "name": device.name,
                    "vram_total_mb": device.vram_total_mb,
                    "compute_capability": device.compute_capability,
                    "capabilities": dict(device.capabilities),
                }
            )
        return {"backend": self._backend.name, "health": health, "devices": devices}

    # --- heartbeat --------------------------------------------------------
    async def _heartbeat_loop(self) -> None:
        current: ClaimedJob | None = None
        while not self._stop.is_set():
            interval = (
                current.heartbeat_interval_seconds
                if current
                else self._settings.heartbeat_interval_seconds
            )
            await self._sleep(interval)
            if self._stop.is_set():
                break
            claims = [current.claim_id] if current else []
            try:
                payload = await self._client.heartbeat(
                    status="BUSY" if current else "IDLE",
                    active_job_count=1 if current else 0,
                    claim_ids=claims,
                )
            except CredentialRejectedError:
                logger.error("worker.credential_rejected_while_busy")
                self._abort.set()
                return
            except (ApiUnavailableError, StaleClaimError) as exc:
                # Transient: the lease may still be alive, so keep going and let
                # the Scheduler's expiry recovery be the source of truth.
                logger.warning("worker.heartbeat_failed", error=str(exc))
                continue
            if current and current.claim_id in payload.get("lost_claim_ids", []):
                logger.error("worker.lease_lost", job_id=current.job_id)
                self._abort.set()
            current = self._current

    # --- execution --------------------------------------------------------
    async def _execute(self, job: ClaimedJob) -> None:
        """Execute one claimed job and report exactly one terminal outcome."""
        self._current = job
        self._abort = asyncio.Event()
        started = time.perf_counter()
        logger.info("worker.job_started", job_id=job.job_id, attempt_no=job.attempt_no)
        try:
            result = await self._run_backend(job)
        except _AbortedError:
            logger.warning("worker.job_abandoned", job_id=job.job_id, reason="lease lost")
        except MockGpuOomError as exc:
            await self._report_failure(job, FAILURE_OOM, str(exc), started, retryable=True)
        except MockGpuError as exc:
            await self._report_failure(
                job, FAILURE_INFRASTRUCTURE, str(exc), started, retryable=True
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("worker.job_crashed", job_id=job.job_id)
            await self._report_failure(
                job, FAILURE_UNKNOWN, str(exc)[:500], started, retryable=True
            )
        else:
            await self._report_success(job, result, started)
        finally:
            self._current = None

    async def _run_backend(self, job: ClaimedJob) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "prompt": job.prompt,
            "negative_prompt": job.negative_prompt,
            "job_type": job.job_type,
            # The reservation size travels with the claim: a real engine needs it
            # to pre-allocate, and it lets the backend fail fast instead of OOMing
            # halfway through a diffusion run.
            "required_vram_mb": job.required_vram_mb,
            **job.params,
        }
        last_percent = 0.0

        async def on_progress(percent: float, message: str) -> None:
            nonlocal last_percent
            if self._abort.is_set():
                raise _AbortedError("lease lost during execution")
            # Throttle: progress is for humans and for liveness, not a firehose.
            if percent - last_percent < 5.0 and percent < 100.0:
                return
            last_percent = percent
            try:
                await self._client.progress(job, percent=percent, message=message)
            except StaleClaimError as exc:
                raise _AbortedError(str(exc)) from exc
            except ApiUnavailableError as exc:
                logger.warning("worker.progress_report_failed", error=str(exc))

        return await self._backend.execute(
            job.gpu_backend_device_id, payload, on_progress=on_progress
        )

    async def _report_success(
        self, job: ClaimedJob, result: dict[str, Any], started: float
    ) -> None:
        outputs = [_encode_output(item) for item in result.get("outputs", [])]
        runtime_info = {
            "backend": self._backend.name,
            "agent": _version(),
            **(result.get("runtime_info") or {}),
        }
        try:
            await self._client.complete(
                job,
                outputs=outputs,
                runtime_info=runtime_info,
                duration_seconds=round(time.perf_counter() - started, 3),
            )
        except StaleClaimError as exc:
            logger.warning("worker.complete_rejected", job_id=job.job_id, error=str(exc))
            return
        logger.info("worker.job_completed", job_id=job.job_id, artifacts=len(outputs))

    async def _report_failure(
        self,
        job: ClaimedJob,
        failure_class: str,
        message: str,
        started: float,
        *,
        retryable: bool | None,
    ) -> None:
        logger.warning(
            "worker.job_failed",
            job_id=job.job_id,
            failure_class=failure_class,
            error=message,
        )
        with contextlib.suppress(StaleClaimError, ApiUnavailableError):
            await self._client.fail(
                job,
                failure_class=failure_class,
                message=message,
                error={"message": message, "duration_seconds": time.perf_counter() - started},
                retryable=retryable,
            )

    # --- helpers ----------------------------------------------------------
    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=max(0.01, seconds))

    def _load_credential(self) -> str | None:
        try:
            return CREDENTIAL_FILE.read_text().strip() or None
        except OSError:
            return None

    def _save_credential(self, credential: str) -> None:
        with contextlib.suppress(OSError):
            CREDENTIAL_FILE.parent.mkdir(parents=True, exist_ok=True)
            CREDENTIAL_FILE.write_text(credential)
            CREDENTIAL_FILE.chmod(0o600)


class _AbortedError(Exception):
    """Internal: the lease disappeared, so the worker must stop work."""


def _encode_output(item: dict[str, Any]) -> dict[str, Any]:
    """Turn a backend artifact into the API's wire shape.

    Bytes go over the wire base64-encoded; the API decodes and hands them to the
    storage backend. Large artifacts should use ``kind="url"`` instead.
    """
    payload: dict[str, Any] = {
        "kind": item.get("kind", "file"),
        "mime_type": item.get("mime_type", "application/octet-stream"),
        "filename": item.get("filename"),
        "asset_role": item.get("asset_role"),
        "metadata": dict(item.get("metadata") or {}),
    }
    if item.get("url"):
        payload["url"] = item["url"]
        payload["kind"] = "url"
        return payload
    content = item.get("content")
    if isinstance(content, bytes):
        payload["content_base64"] = base64.b64encode(content).decode("ascii")
    elif isinstance(content, str):
        payload["content_base64"] = base64.b64encode(content.encode()).decode("ascii")
    return payload


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("ai-media-platform")
    except PackageNotFoundError:  # pragma: no cover - editable install always has it
        return "0.0.0"


async def run() -> None:
    """Process entry point."""
    settings = get_settings()
    backend = build_gpu_backend(
        settings.gpu_backend,
        mock_nodes=settings.mock_gpu_nodes,
        mock_gpus_per_node=settings.mock_gpus_per_node,
        mock_vram_mb=settings.mock_gpu_vram_mb,
    )
    client = ApiClient(settings.worker_base_url)
    agent = WorkerAgent(
        client,
        backend,
        settings,
        registration_token=os.environ.get("APP_WORKER_REGISTRATION_TOKEN"),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    task = asyncio.create_task(agent.start(), name="worker-agent")
    await stop.wait()
    logger.info("worker.stopping")
    agent.stop()
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await asyncio.wait_for(task, timeout=10)
    await backend.close()
    await client.close()


def main() -> None:  # pragma: no cover - process entry point
    asyncio.run(run())


__all__ = ["WorkerAgent", "main", "run"]
