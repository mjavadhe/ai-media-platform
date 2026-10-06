"""Creative structure endpoints (``/api/v1/projects`` and friends).

Projects → sequences → shots → assets/versions → generated media. These are
plain CRUD over the catalogue tables; they never touch job state, which is the
Orchestrator's/Scheduler's job.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select

from app.api.deps import (
    AppSettings,
    CsrfChecked,
    DbSession,
    RegistryDep,
    StorageDep,
    require_permissions,
)
from app.api.v1.schemas import (
    AssetCreate,
    AssetOut,
    GeneratedMediaOut,
    MediaVersionOut,
    ProjectCreate,
    ProjectOut,
    SequenceCreate,
    SequenceOut,
    ShotBatchCreate,
    ShotBatchOut,
    ShotCreate,
    ShotOut,
)
from app.domain.enums import PermissionCode
from app.domain.jobs.state_machine import JobOwner
from app.infrastructure.logging import get_logger
from app.persistence.models_creative import (
    Asset,
    GeneratedMedia,
    GeneratedMediaVersion,
    Project,
    Sequence,
    Shot,
    StorageObject,
)
from app.persistence.repositories.common import utcnow
from app.persistence.repositories.jobs import JobRepository

logger = get_logger(__name__)

router = APIRouter(tags=["creative"])


# --- projects ------------------------------------------------------------
@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(
    session: DbSession,
    principal: require_permissions(PermissionCode.PROJECT_READ.value),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ProjectOut]:
    rows = await session.scalars(
        select(Project).order_by(Project.created_at.desc(), Project.id).limit(limit).offset(offset)
    )
    return [ProjectOut.model_validate(project) for project in rows.all()]


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    session: DbSession,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.PROJECT_WRITE.value),
) -> ProjectOut:
    if payload.slug:
        exists = await session.scalar(select(Project.id).where(Project.slug == payload.slug))
        if exists is not None:
            raise HTTPException(status_code=409, detail=f"slug {payload.slug!r} already in use")
    project = Project(
        name=payload.name,
        slug=payload.slug or _slugify(payload.name),
        description=payload.description,
        status="ACTIVE",
        created_by=principal.user.id,
        # The ORM attribute is ``metadata_``; ``extra`` is not a valid kwarg.
        metadata_=payload.metadata_ or {},
    )
    session.add(project)
    await session.flush()
    logger.info("project.created", project_id=str(project.id), actor_id=str(principal.user.id))
    await session.commit()
    return ProjectOut.model_validate(project)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: uuid.UUID,
    session: DbSession,
    principal: require_permissions(PermissionCode.PROJECT_READ.value),
) -> ProjectOut:
    return ProjectOut.model_validate(await _get_project(session, project_id))


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    session: DbSession,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.PROJECT_DELETE.value),
) -> Response:
    project = await _get_project(session, project_id)
    await session.delete(project)
    await session.commit()
    logger.info("project.deleted", project_id=str(project_id), actor_id=str(principal.user.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- sequences -----------------------------------------------------------
@router.get("/projects/{project_id}/sequences", response_model=list[SequenceOut])
async def list_sequences(
    project_id: uuid.UUID,
    session: DbSession,
    principal: require_permissions(PermissionCode.PROJECT_READ.value),
) -> list[SequenceOut]:
    await _get_project(session, project_id)
    rows = await session.scalars(
        select(Sequence)
        .where(Sequence.project_id == project_id)
        .order_by(Sequence.position, Sequence.created_at)
    )
    return [SequenceOut.model_validate(row) for row in rows.all()]


@router.post(
    "/projects/{project_id}/sequences",
    response_model=SequenceOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_sequence(
    project_id: uuid.UUID,
    payload: SequenceCreate,
    session: DbSession,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.PROJECT_WRITE.value),
) -> SequenceOut:
    await _get_project(session, project_id)
    sequence = Sequence(
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        position=payload.position,
        created_by=principal.user.id,
    )
    session.add(sequence)
    await session.flush()
    await session.commit()
    return SequenceOut.model_validate(sequence)


# --- shots ---------------------------------------------------------------
@router.get("/sequences/{sequence_id}/shots", response_model=list[ShotOut])
async def list_shots(
    sequence_id: uuid.UUID,
    session: DbSession,
    principal: require_permissions(PermissionCode.PROJECT_READ.value),
) -> list[ShotOut]:
    rows = await session.scalars(
        select(Shot).where(Shot.sequence_id == sequence_id).order_by(Shot.position, Shot.created_at)
    )
    return [ShotOut.model_validate(shot) for shot in rows.all()]


@router.post(
    "/sequences/{sequence_id}/shots",
    response_model=ShotOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_shot(
    sequence_id: uuid.UUID,
    payload: ShotCreate,
    session: DbSession,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.PROJECT_WRITE.value),
) -> ShotOut:
    sequence = await session.get(Sequence, sequence_id)
    if sequence is None:
        raise HTTPException(status_code=404, detail="sequence not found")
    shot = Shot(
        project_id=sequence.project_id,
        sequence_id=sequence_id,
        title=payload.title,
        code=payload.code,
        position=payload.position,
        status="DRAFT",
        prompt=payload.prompt,
        negative_prompt=payload.negative_prompt,
        camera=payload.camera,
        lighting=payload.lighting,
        params=payload.params,
        created_by=principal.user.id,
    )
    session.add(shot)
    await session.flush()
    await session.commit()
    return ShotOut.model_validate(shot)


# --- assets --------------------------------------------------------------
@router.get("/assets", response_model=list[AssetOut])
async def list_all_assets(
    session: DbSession,
    principal: require_permissions(PermissionCode.ASSET_READ.value),
    project_id: uuid.UUID | None = None,
    asset_type: str | None = Query(default=None, alias="type"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AssetOut]:
    """Contract path: unfiltered asset list across projects."""
    stmt = select(Asset)
    if project_id:
        stmt = stmt.where(Asset.project_id == project_id)
    if asset_type:
        stmt = stmt.where(Asset.type == asset_type)
    rows = await session.scalars(stmt.order_by(Asset.created_at.desc()).limit(limit))
    return [AssetOut.model_validate(asset) for asset in rows.all()]


@router.get("/projects/{project_id}/assets", response_model=list[AssetOut])
async def list_assets(
    project_id: uuid.UUID,
    session: DbSession,
    principal: require_permissions(PermissionCode.ASSET_READ.value),
    asset_type: str | None = Query(default=None, alias="type"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AssetOut]:
    stmt = select(Asset).where(Asset.project_id == project_id)
    if asset_type:
        stmt = stmt.where(Asset.type == asset_type)
    rows = await session.scalars(stmt.order_by(Asset.created_at).limit(limit))
    return [AssetOut.model_validate(asset) for asset in rows.all()]


@router.post("/assets", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
async def create_asset(
    payload: AssetCreate,
    session: DbSession,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.ASSET_WRITE.value),
) -> AssetOut:
    asset = Asset(
        name=payload.name,
        type=payload.type,
        description=payload.description,
        project_id=payload.project_id,
        tags=payload.tags,
        created_by=principal.user.id,
    )
    session.add(asset)
    await session.flush()
    await session.commit()
    return AssetOut.model_validate(asset)


@router.post(
    "/sequences/{sequence_id}/shots/batch",
    response_model=ShotBatchOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_shots_batch(
    sequence_id: uuid.UUID,
    payload: ShotBatchCreate,
    session: DbSession,
    settings: AppSettings,
    registry: RegistryDep,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.PROJECT_WRITE.value),
) -> ShotBatchOut:
    """Create N shots from one template and optionally queue a job per shot.

    One transaction for the whole batch: a partially created storyboard is worse
    than a rejected batch, and the caller gets one error instead of a partial
    list.
    """
    sequence = await session.get(Sequence, sequence_id)
    if sequence is None:
        raise HTTPException(status_code=404, detail="sequence not found")
    shots: list[Shot] = []
    job_ids: list[uuid.UUID] = []
    jobs = JobRepository(session)
    for index, item in enumerate(payload.shots):
        title = item.title.format(index=index, code=item.code or index)
        shot = Shot(
            project_id=sequence.project_id,
            sequence_id=sequence_id,
            title=title,
            code=item.code,
            position=item.position + index,
            status="DRAFT",
            prompt=_render(item.prompt, payload.prompt_template, index, title),
            negative_prompt=item.negative_prompt,
            camera=item.camera,
            lighting=item.lighting,
            params=item.params,
            created_by=principal.user.id,
        )
        session.add(shot)
        await session.flush()
        shots.append(shot)
        if payload.auto_create_jobs:
            job = await jobs.create(
                job_type="IMAGE",
                created_by=principal.user.id,
                prompt=shot.prompt,
                negative_prompt=shot.negative_prompt,
                project_id=shot.project_id,
                sequence_id=sequence_id,
                shot_id=shot.id,
                params=payload.job_params,
            )
            await jobs.enqueue(job, owner=JobOwner.API, visible_at=utcnow())
            job_ids.append(job.id)
    logger.info(
        "shots.batch_created",
        sequence_id=str(sequence_id),
        count=len(shots),
        jobs=len(job_ids),
        actor_id=str(principal.user.id),
    )
    await session.commit()
    return ShotBatchOut(
        shots=[ShotOut.model_validate(shot) for shot in shots],
        job_ids=job_ids,
    )


def _render(prompt: str | None, template: str | None, index: int, title: str) -> str | None:
    """Substitute ``{index}``/``{code}``/``{title}`` without eval/format tricks."""
    text = template or prompt
    if not text:
        return None
    return text.replace("{index}", str(index)).replace("{code}", title).replace(
        "{title}", title
    )


# --- generated media -----------------------------------------------------
@router.get("/media", response_model=list[GeneratedMediaOut])
async def list_media(
    session: DbSession,
    principal: require_permissions(PermissionCode.ASSET_READ.value),
    project_id: uuid.UUID | None = None,
    shot_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[GeneratedMediaOut]:
    stmt = select(GeneratedMedia)
    if project_id:
        stmt = stmt.where(GeneratedMedia.project_id == project_id)
    if shot_id:
        stmt = stmt.where(GeneratedMedia.shot_id == shot_id)
    if job_id:
        stmt = stmt.where(GeneratedMedia.job_id == job_id)
    rows = await session.scalars(stmt.order_by(GeneratedMedia.created_at.desc()).limit(limit))
    return [GeneratedMediaOut.model_validate(media) for media in rows.all()]


@router.get("/media/{media_id}/versions", response_model=list[MediaVersionOut])
async def list_media_versions(
    media_id: uuid.UUID,
    session: DbSession,
    principal: require_permissions(PermissionCode.ASSET_READ.value),
) -> list[MediaVersionOut]:
    """Versions are the pointer chain: DB row -> storage object -> bytes."""
    rows = await session.scalars(
        select(GeneratedMediaVersion)
        .where(GeneratedMediaVersion.media_id == media_id)
        .order_by(GeneratedMediaVersion.version_no)
    )
    versions = list(rows.all())
    # Key, mime type, size and checksum live on the StorageObject, reached via
    # the version's FK; the version row carries none of them.
    objects = {
        obj.id: obj
        for obj in (
            await session.scalars(
                select(StorageObject).where(
                    StorageObject.id.in_([v.storage_object_id for v in versions])
                )
            )
        ).all()
    } if versions else {}
    return [
        MediaVersionOut(
            version_no=version.version_no,
            storage_key=objects[version.storage_object_id].key,
            mime_type=objects[version.storage_object_id].mime_type,
            size_bytes=objects[version.storage_object_id].size_bytes,
            checksum=objects[version.storage_object_id].checksum,
        )
        for version in versions
        if version.storage_object_id in objects
    ]


@router.get("/media/{media_id}/current")
async def download_current(
    media_id: uuid.UUID,
    session: DbSession,
    storage: StorageDep,
    principal: require_permissions(PermissionCode.STORAGE_READ.value),
) -> Response:
    """Stream the current version's bytes through the storage abstraction.

    Large media never touches PostgreSQL; this only ever reads from the
    configured backend.
    """
    media = await session.get(GeneratedMedia, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="media not found")
    version = await session.scalar(
        select(GeneratedMediaVersion)
        .where(
            GeneratedMediaVersion.media_id == media_id,
            GeneratedMediaVersion.version_no == media.current_version_no,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="media has no current version")
    # A version points at a StorageObject row via ``storage_object_id``; the
    # object holds the storage key and the checksum (there is no
    # ``version.storage_key``, which made this endpoint raise AttributeError).
    object_row = await session.get(StorageObject, version.storage_object_id)
    if object_row is None:
        raise HTTPException(status_code=404, detail="storage object missing for this version")
    # ``exists`` is a coroutine; calling it without await silently skipped the
    # existence check and always passed.
    if not await storage.exists(object_row.key):
        raise HTTPException(status_code=410, detail="artifact bytes are no longer stored")
    data = await storage.read(object_row.key)
    return Response(
        content=data,
        media_type=object_row.mime_type or "application/octet-stream",
        headers={
            "content-length": str(len(data)),
            "x-checksum-sha256": object_row.checksum or "",
            "x-version-no": str(version.version_no),
        },
    )


async def _get_project(session: DbSession, project_id: uuid.UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def _slugify(value: str) -> str:
    out = "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "project"


__all__ = ["router"]
