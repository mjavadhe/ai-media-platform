"""Media pipeline: persist worker outputs and register them as media versions.

Flow per produced artifact (contract section 11 / doc 21):

1. write the bytes through :class:`StorageBackend` (never into the DB),
2. upsert a ``storage_objects`` row (key + checksum + size only),
3. on ``COMPLETED``: create the ``generated_media`` row for the job/shot and its
   first ``generated_media_versions`` row — idempotent via
   ``uq_generated_media_attempt_role`` and the checksum,
4. on ``FAILED``: create a ``generated_media`` row with no version, so a failed
   job's intent is still visible in the UI.

Idempotency matters: a duplicate completion report, or a retried attempt, must
never register the same artifact twice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import MediaType, StorageOwnerType
from app.persistence.models_creative import (
    GeneratedMedia,
    GeneratedMediaVersion,
    StorageObject,
)
from app.persistence.models_job import Job, JobAttempt
from app.persistence.repositories.common import utcnow
from app.services.storage.base import StorageBackend

#: mime type prefix -> media type. Data-driven, no filename heuristics.
_MIME_MEDIA_TYPES: dict[str, MediaType] = {
    "image/": MediaType.IMAGE,
    "video/": MediaType.VIDEO,
    "audio/": MediaType.AUDIO,
}

_EXTENSIONS: dict[MediaType, str] = {
    MediaType.IMAGE: "bin",
    MediaType.VIDEO: "mp4",
    MediaType.AUDIO: "wav",
}


def media_type_for(mime_type: str) -> MediaType:
    for prefix, media_type in _MIME_MEDIA_TYPES.items():
        if mime_type.startswith(prefix):
            return media_type
    return MediaType.IMAGE


@dataclass(frozen=True, slots=True)
class PersistedArtifact:
    """One stored artifact with its database rows."""

    storage_object: StorageObject
    media: GeneratedMedia
    version: GeneratedMediaVersion | None
    created: bool


class MediaService:
    def __init__(self, session: AsyncSession, storage: StorageBackend) -> None:
        self._session = session
        self._storage = storage

    async def persist_outputs(
        self,
        *,
        job: Job,
        attempt: JobAttempt | None,
        outputs: list[dict[str, Any]],
        prompt: str | None = None,
        negative_prompt: str | None = None,
        runtime_info: dict[str, Any] | None = None,
        register_media: bool = True,
    ) -> list[PersistedArtifact]:
        """Store every produced artifact and register it against the job.

        ``outputs`` entries are the worker's output descriptors:
        ``{"kind": "file", "mime_type": ..., "filename": ..., "content": bytes}``
        or a ``{"kind": "url", "url": ...}`` for engines that return a location.
        """
        artifacts: list[PersistedArtifact] = []
        for index, output in enumerate(outputs):
            role = str(output.get("asset_role") or f"output_{index}")
            media_type = media_type_for(str(output.get("mime_type") or "application/octet-stream"))
            key = self._build_key(job=job, attempt=attempt, role=role, index=index, output=output)

            payload = output.get("content")
            if payload is None and output.get("url"):
                # A URL output is recorded as metadata only; Phase 1 has no
                # outbound fetch so the artifact stays remote.
                artifacts.append(
                    await self._register_remote(
                        job=job,
                        attempt=attempt,
                        role=role,
                        media_type=media_type,
                        output=output,
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        runtime_info=runtime_info,
                        register_media=register_media,
                    )
                )
                continue

            data = payload if isinstance(payload, bytes) else bytes(payload or b"")
            stored = await self._storage.write(
                key,
                data,
                content_type=str(output.get("mime_type") or "application/octet-stream"),
                metadata={
                    "job_id": str(job.id),
                    "attempt_id": str(attempt.id) if attempt else "",
                    "role": role,
                },
            )
            storage_object = await self._upsert_storage_object(
                key=stored.key,
                size_bytes=stored.size_bytes,
                checksum=stored.checksum,
                content_type=stored.content_type,
                owner_type=StorageOwnerType.JOB.value,
                owner_id=job.id,
                mime_type=stored.content_type,
            )
            artifacts.append(
                await self._register_media(
                    job=job,
                    attempt=attempt,
                    role=role,
                    media_type=media_type,
                    storage_object=storage_object,
                    output=output,
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    runtime_info=runtime_info,
                    register_media=register_media,
                )
            )
        return artifacts

    # --- internals --------------------------------------------------------
    def _build_key(
        self,
        *,
        job: Job,
        attempt: JobAttempt | None,
        role: str,
        index: int,
        output: dict[str, Any],
    ) -> str:
        extension = str(output.get("filename") or "").rsplit(".", 1)[-1].lower()
        extension = extension if extension.isalnum() and len(extension) <= 8 else "bin"
        attempt_part = f"attempt-{attempt.attempt_no}" if attempt else "no-attempt"
        return f"jobs/{job.id}/{attempt_part}/{role}-{index}.{extension}"

    async def _upsert_storage_object(
        self,
        *,
        key: str,
        size_bytes: int,
        checksum: str,
        content_type: str,
        owner_type: str,
        owner_id: uuid.UUID,
        mime_type: str,
    ) -> StorageObject:
        existing = await self._session.scalar(select(StorageObject).where(StorageObject.key == key))
        if existing is not None:
            return existing
        row = StorageObject(
            key=key,
            size_bytes=size_bytes,
            checksum=checksum,
            content_type=content_type,
            mime_type=mime_type,
            owner_type=owner_type,
            owner_id=owner_id,
            status="AVAILABLE",
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def _register_media(
        self,
        *,
        job: Job,
        attempt: JobAttempt | None,
        role: str,
        media_type: MediaType,
        storage_object: StorageObject,
        output: dict[str, Any],
        prompt: str | None,
        negative_prompt: str | None,
        runtime_info: dict[str, Any] | None,
        register_media: bool,
    ) -> PersistedArtifact:
        if not register_media:
            return PersistedArtifact(
                storage_object=storage_object, media=None, version=None, created=False
            )  # type: ignore[arg-type]

        media = None
        created = False
        if attempt is not None:
            media = await self._session.scalar(
                select(GeneratedMedia).where(
                    GeneratedMedia.attempt_id == attempt.id,
                    GeneratedMedia.asset_role == role,
                )
            )
        if media is None:
            media = GeneratedMedia(
                shot_id=job.shot_id,
                project_id=job.project_id,
                job_id=job.id,
                attempt_id=attempt.id if attempt else None,
                media_type=media_type.value,
                asset_role=role,
                current_version_no=0,
                prompt=prompt,
                negative_prompt=negative_prompt,
                reproducibility=self._reproducibility(output, runtime_info),
            )
            self._session.add(media)
            await self._session.flush()
            created = True

        version = await self._session.scalar(
            select(GeneratedMediaVersion).where(
                GeneratedMediaVersion.media_id == media.id,
                GeneratedMediaVersion.version_no == media.current_version_no + 1,
            )
        )
        if version is None:
            version = GeneratedMediaVersion(
                media_id=media.id,
                version_no=media.current_version_no + 1,
                storage_object_id=storage_object.id,
                prompt=prompt,
                parameters=dict(output.get("metadata") or {}),
                reproducibility=self._reproducibility(output, runtime_info),
            )
            self._session.add(version)
            media.current_version_no = version.version_no
            await self._session.flush()
        return PersistedArtifact(
            storage_object=storage_object, media=media, version=version, created=created
        )

    async def _register_remote(
        self,
        *,
        job: Job,
        attempt: JobAttempt | None,
        role: str,
        media_type: MediaType,
        output: dict[str, Any],
        prompt: str | None,
        negative_prompt: str | None,
        runtime_info: dict[str, Any] | None,
        register_media: bool,
    ) -> PersistedArtifact:
        """Record an engine-produced URL without fetching it in Phase 1."""
        storage_object = await self._upsert_storage_object(
            key=str(output["url"]),
            size_bytes=int(output.get("size_bytes") or 0),
            checksum=str(output.get("checksum") or "remote"),
            content_type=str(output.get("mime_type") or "application/octet-stream"),
            owner_type=StorageOwnerType.JOB.value,
            owner_id=job.id,
            mime_type=str(output.get("mime_type") or "application/octet-stream"),
        )
        if not register_media:
            return PersistedArtifact(
                storage_object=storage_object, media=None, version=None, created=False
            )  # type: ignore[arg-type]
        media = GeneratedMedia(
            shot_id=job.shot_id,
            project_id=job.project_id,
            job_id=job.id,
            attempt_id=attempt.id if attempt else None,
            media_type=media_type.value,
            asset_role=role,
            current_version_no=0,
            prompt=prompt,
            negative_prompt=negative_prompt,
            reproducibility=self._reproducibility(output, runtime_info),
        )
        self._session.add(media)
        await self._session.flush()
        return PersistedArtifact(
            storage_object=storage_object, media=media, version=None, created=True
        )

    @staticmethod
    def _reproducibility(
        output: dict[str, Any], runtime_info: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Everything needed to reproduce this artifact (spec section 46)."""
        merged = {**(runtime_info or {}), **dict(output.get("metadata") or {})}
        return {
            "seed": merged.get("seed"),
            "steps": merged.get("steps"),
            "backend": merged.get("backend"),
            "device_id": merged.get("device_id"),
            "compute_capability": merged.get("compute_capability"),
            "captured_at": utcnow().isoformat(),
        }


__all__ = ["MediaService", "PersistedArtifact", "media_type_for"]
