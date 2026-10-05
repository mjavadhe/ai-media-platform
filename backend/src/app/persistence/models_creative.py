"""Creative-entity models: projects, sequences, shots, assets, media, storage.

Mirrors `docs/design/21-asset-project-shot-data-model.md`. Media bytes are never
stored here — only metadata plus a storage key behind the storage abstraction
(doc 05 principle 2).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

# `PgUUID` avoids shadowing `uuid.UUID`; the uppercase import is deliberate.
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import (
    ApprovalStatus,
    AssetType,
    MediaType,
    ProjectStatus,
    ShotStatus,
    StorageOwnerType,
)
from app.persistence.base import Base, TimestampMixin, uuid_pk

if TYPE_CHECKING:
    from app.persistence.models_job import Job


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id = uuid_pk()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default=ProjectStatus.ACTIVE.value, nullable=False
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sequences: Mapped[list[Sequence]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    assets: Mapped[list[Asset]] = relationship(back_populates="project")
    # ``jobs`` is a *generic* job table (it is not FK-coupled to the creative
    # tables), so the reverse view is declared with an explicit join and is
    # read-only: nothing here may cascade or delete a job.
    jobs: Mapped[list[Job]] = relationship(
        "Job",
        primaryjoin="foreign(Project.id) == Job.project_id",
        viewonly=True,
        lazy="raise",
    )

    __table_args__ = (
        Index("ix_projects_status", "status"),
        Index("ix_projects_created_by", "created_by"),
    )


class Sequence(Base, TimestampMixin):
    __tablename__ = "sequences"

    id = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="sequences")
    shots: Mapped[list[Shot]] = relationship(
        back_populates="sequence", cascade="all, delete-orphan", order_by="Shot.position"
    )
    jobs: Mapped[list[Job]] = relationship(
        "Job",
        primaryjoin="foreign(Sequence.id) == Job.sequence_id",
        viewonly=True,
        lazy="raise",
    )

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_sequences_project_name"),
        Index("ix_sequences_project_id_position", "project_id", "position"),
    )


class Shot(Base, TimestampMixin):
    __tablename__ = "shots"

    id = uuid_pk()
    sequence_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sequences.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=ShotStatus.DRAFT.value, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)

    prompt: Mapped[str | None] = mapped_column(Text)
    negative_prompt: Mapped[str | None] = mapped_column(Text)
    camera: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    lighting: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    sequence: Mapped[Sequence] = relationship(back_populates="shots")
    media: Mapped[list[GeneratedMedia]] = relationship(
        back_populates="shot", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[Job]] = relationship(
        "Job",
        primaryjoin="foreign(Shot.id) == Job.shot_id",
        viewonly=True,
        lazy="raise",
    )

    __table_args__ = (
        Index("ix_shots_sequence_id", "sequence_id"),
        Index("ix_shots_status", "status"),
        Index("ix_shots_project_id", "project_id"),
    )


class Asset(Base, TimestampMixin):
    """Polymorphic asset. ``project_id IS NULL`` means a global asset."""

    __tablename__ = "assets"

    id = uuid_pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )

    project: Mapped[Project | None] = relationship(back_populates="assets")
    versions: Mapped[list[AssetVersion]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", order_by="AssetVersion.version"
    )
    references: Mapped[list[ReferenceMedia]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_assets_project_id_type", "project_id", "type"),
        Index("ix_assets_type", "type"),
        Index("ix_assets_name", "name"),
    )


class AssetVersion(Base, TimestampMixin):
    """Immutable asset version (doc 21)."""

    __tablename__ = "asset_versions"

    id = uuid_pk()
    asset_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(String(128))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    asset: Mapped[Asset] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint("asset_id", "version", name="uq_asset_versions_asset_version"),
    )


class ReferenceMedia(Base):
    """Links an asset to a storage object used as generation input."""

    __tablename__ = "reference_media"

    id = uuid_pk()
    asset_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    storage_object_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("storage_objects.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), default="reference", nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=None, nullable=False
    )

    asset: Mapped[Asset] = relationship(back_populates="references")

    __table_args__ = (
        UniqueConstraint("asset_id", "storage_object_id", name="uq_reference_media_pair"),
    )


class StorageObject(Base, TimestampMixin):
    """Media metadata. Bytes live behind the storage abstraction, never in the DB."""

    __tablename__ = "storage_objects"

    id = uuid_pk()
    backend: Mapped[str] = mapped_column(String(16), nullable=False)
    bucket: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128))
    owner_type: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(String(64))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("backend", "bucket", "key", name="uq_storage_objects_location"),
        Index("ix_storage_objects_owner", "owner_type", "owner_id"),
    )


class GeneratedMedia(Base, TimestampMixin):
    """A generation result attached to a shot (doc 21 / spec section 46)."""

    __tablename__ = "generated_media"

    id = uuid_pk()
    shot_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("shots.id", ondelete="CASCADE")
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    asset_role: Mapped[str] = mapped_column(String(32), default="output", nullable=False)
    current_version_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    approval_status: Mapped[str] = mapped_column(
        String(16), default=ApprovalStatus.PENDING.value, nullable=False
    )
    prompt: Mapped[str | None] = mapped_column(Text)
    negative_prompt: Mapped[str | None] = mapped_column(Text)
    reproducibility: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    shot: Mapped[Shot | None] = relationship(back_populates="media")
    versions: Mapped[list[GeneratedMediaVersion]] = relationship(
        back_populates="media",
        cascade="all, delete-orphan",
        order_by="GeneratedMediaVersion.version_no",
    )

    __table_args__ = (
        # idempotency guard: one media row per (attempt, role) — a duplicate
        # completion report can therefore never register the same asset twice
        UniqueConstraint("attempt_id", "asset_role", name="uq_generated_media_attempt_role"),
        Index("ix_generated_media_shot_id", "shot_id"),
        Index("ix_generated_media_job_id", "job_id"),
        Index("ix_generated_media_project_id", "project_id"),
    )


class GeneratedMediaVersion(Base, TimestampMixin):
    __tablename__ = "generated_media_versions"

    id = uuid_pk()
    media_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("generated_media.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_object_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("storage_objects.id", ondelete="RESTRICT"), nullable=False
    )
    prompt: Mapped[str | None] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    reproducibility: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    media: Mapped[GeneratedMedia] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint("media_id", "version_no", name="uq_generated_media_versions_no"),
    )


class PromptTemplate(Base, TimestampMixin):
    """Versioned, composable prompt template (spec sections 27-28)."""

    __tablename__ = "prompt_templates"

    id = uuid_pk()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="prompt", nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    __table_args__ = (
        UniqueConstraint("name", "version", "category", name="uq_prompt_templates_name_version"),
        Index("ix_prompt_templates_category", "category"),
    )


__all__ = [
    "Asset",
    "AssetType",
    "AssetVersion",
    "GeneratedMedia",
    "GeneratedMediaVersion",
    "MediaType",
    "Project",
    "PromptTemplate",
    "ReferenceMedia",
    "Sequence",
    "Shot",
    "StorageObject",
    "StorageOwnerType",
]
