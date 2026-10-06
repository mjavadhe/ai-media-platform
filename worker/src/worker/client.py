"""HTTP client a worker agent uses to talk to the API.

Deliberately thin and explicit about failure modes, because every one of them is
a lease/heartbeat risk:

* **401** → the credential was revoked/rotated: stop, do not retry forever.
* **409** → a stale lease or a start conflict: abandon the job locally.
* **5xx / transport error** → transient: retry with backoff, because the lease
  may still be alive and the Scheduler needs the eventual report.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx


class ApiError(Exception):
    """Non-retryable API error (4xx other than 408/429)."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class ApiUnavailableError(Exception):
    """Transient API failure: the transport or the server is unavailable."""


class CredentialRejectedError(ApiError):
    """The worker credential is no longer valid; the agent must re-register."""


class StaleClaimError(Exception):
    """The lease is gone (409): the worker must stop work immediately."""


@dataclass(slots=True)
class ClaimedJob:
    """A job the Scheduler has started for this worker."""

    job_id: str
    attempt_id: str
    attempt_no: int
    claim_id: str
    lease_expires_at: str
    lease_ttl_seconds: int
    heartbeat_interval_seconds: int
    gpu_device_id: str
    #: Opaque backend handle (e.g. "mock-node-0-gpu-0"), used for execution.
    gpu_backend_device_id: str
    node_id: str
    node_key: str | None
    backend: str | None
    required_vram_mb: int | None
    job_type: str
    params: dict[str, Any]
    prompt: str | None
    negative_prompt: str | None
    model_version_id: str | None
    workflow_version_id: str | None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ClaimedJob:
        return cls(
            job_id=payload["job_id"],
            attempt_id=payload["attempt_id"],
            attempt_no=int(payload["attempt_no"]),
            claim_id=payload["claim_id"],
            lease_expires_at=payload["lease_expires_at"],
            lease_ttl_seconds=int(payload["lease_ttl_seconds"]),
            heartbeat_interval_seconds=int(payload["heartbeat_interval_seconds"]),
            gpu_device_id=payload["gpu_device_id"],
            gpu_backend_device_id=payload["gpu_backend_device_id"],
            node_id=payload["node_id"],
            node_key=payload.get("node_key"),
            backend=payload.get("backend"),
            required_vram_mb=payload.get("required_vram_mb"),
            job_type=payload["job_type"],
            params=payload.get("params") or {},
            prompt=payload.get("prompt"),
            negative_prompt=payload.get("negative_prompt"),
            model_version_id=payload.get("model_version_id"),
            workflow_version_id=payload.get("workflow_version_id"),
        )


class ApiClient:
    """Typed wrapper over the worker-facing API endpoints."""

    def __init__(
        self,
        base_url: str,
        *,
        credential: str | None = None,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._credential = credential
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout_seconds,
            headers={"accept": "application/json"},
        )

    # --- plumbing ---------------------------------------------------------
    def set_credential(self, credential: str) -> None:
        """Set/rotate the worker credential after registration or rotation."""
        self._credential = credential

    @property
    def credential(self) -> str | None:
        return self._credential

    def _headers(self, *, json_body: bool = True) -> dict[str, str]:
        headers = {"accept": "application/json"}
        if json_body:
            headers["content-type"] = "application/json"
        if self._credential:
            headers["authorization"] = f"Worker {self._credential}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        retries: int = 2,
    ) -> httpx.Response:
        attempt = 0
        while True:
            try:
                response = await self._client.request(
                    method,
                    path,
                    json=json,
                    params=params,
                    headers=headers or self._headers(json_body=json is not None),
                )
            except httpx.HTTPError as exc:
                attempt += 1
                if attempt > retries:
                    raise ApiUnavailableError(f"{method} {path}: {exc}") from exc
                await asyncio.sleep(min(2**attempt * 0.2, 2.0))
                continue
            if response.status_code == 401:
                raise CredentialRejectedError(401, _detail(response))
            if response.status_code == 409:
                raise StaleClaimError(_detail(response))
            if response.status_code >= 500 and attempt <= retries:
                attempt += 1
                await asyncio.sleep(min(2**attempt * 0.2, 2.0))
                continue
            if response.status_code >= 400:
                raise ApiError(response.status_code, _detail(response))
            return response

    # --- registration -----------------------------------------------------
    async def register(
        self,
        *,
        registration_token: str,
        name: str,
        version: str,
        platform: str,
        capabilities: dict[str, Any],
        node_key: str | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/api/v1/workers/register",
            json={
                "name": name,
                "version": version,
                "platform": platform,
                "capabilities": capabilities,
                "node_key": node_key,
            },
            headers=self._headers(),
        )
        payload = response.json()
        self._credential = payload["credential"]
        return payload

    async def me(self) -> dict[str, Any]:
        """Identity of the credential in use; also a cheap credential probe."""
        return (await self._request("GET", "/api/v1/workers/me")).json()

    # --- liveness ---------------------------------------------------------
    async def heartbeat(
        self, *, status: str | None, active_job_count: int, claim_ids: list[str]
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/api/v1/workers/heartbeat",
            json={
                "status": status,
                "active_job_count": active_job_count,
                "claim_ids": claim_ids,
            },
        )
        return response.json()

    # --- work -------------------------------------------------------------
    async def claim(self, *, job_id: str | None = None) -> ClaimedJob | None:
        response = await self._request(
            "POST", "/api/v1/workers/jobs/claim", json={"job_id": job_id}
        )
        if response.status_code == 204 or not response.content:
            return None
        return ClaimedJob.from_payload(response.json())

    async def progress(
        self,
        job: ClaimedJob,
        *,
        percent: float,
        step: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            f"/api/v1/jobs/{job.job_id}/progress",
            params={"claim_id": job.claim_id},
            json={
                "percent": round(percent, 2),
                "step": step,
                "total": total,
                "message": message,
            },
        )
        return response.json()

    async def complete(
        self,
        job: ClaimedJob,
        *,
        outputs: list[dict[str, Any]],
        runtime_info: dict[str, Any],
        duration_seconds: float,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            f"/api/v1/jobs/{job.job_id}/complete",
            params={"claim_id": job.claim_id},
            json={
                "outputs": outputs,
                "runtime_info": runtime_info,
                "duration_seconds": duration_seconds,
            },
        )
        return response.json()

    async def fail(
        self,
        job: ClaimedJob,
        *,
        failure_class: str,
        message: str,
        error: dict[str, Any],
        retryable: bool | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            f"/api/v1/jobs/{job.job_id}/fail",
            params={"claim_id": job.claim_id},
            json={
                "failure_class": failure_class,
                "message": message,
                "error": error,
                "retryable": retryable,
            },
        )
        return response.json()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _detail(response: httpx.Response) -> str:
    """Pull the message out of the API's uniform error envelope."""
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or payload)[:300]
        return str(payload.get("detail") or payload)[:300]
    return str(payload)[:300]


__all__ = [
    "ApiClient",
    "ApiError",
    "ApiUnavailableError",
    "ClaimedJob",
    "CredentialRejectedError",
    "StaleClaimError",
]
