"""Uploaded learning media owned by an organization: videos (behind a VideoProvider) and files
(PDFs in S3). Students never read these tables directly; playback/download URLs are issued by the
media service after checking they can read a course that uses the asset."""

from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class VideoStatus(StrEnum):
    CREATED = "created"  # upload ticket issued
    PROCESSING = "processing"  # uploaded, provider transcoding
    READY = "ready"
    FAILED = "failed"


class FileStatus(StrEnum):
    PENDING = "pending"  # presigned upload issued, not yet confirmed
    READY = "ready"
    REJECTED = "rejected"


class FileKind(StrEnum):
    PDF = "pdf"  # pdf lessons
    IMAGE = "image"  # images in notes lessons


class VideoAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "video_assets"
    __table_args__ = (
        UniqueConstraint("provider", "provider_video_id", name="uq_video_assets_provider_video"),
        CheckConstraint(
            "status IN ('created', 'processing', 'ready', 'failed')", name="ck_video_assets_status"
        ),
        Index("ix_video_assets_organization_id_status", "organization_id", "status"),
        Index("ix_video_assets_created_by", "created_by"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(String(20))
    provider_video_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), server_default=VideoStatus.CREATED)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class StoredFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_files_storage_key"),
        CheckConstraint("status IN ('pending', 'ready', 'rejected')", name="ck_files_status"),
        CheckConstraint("kind IN ('pdf', 'image')", name="ck_files_kind"),
        Index("ix_files_organization_id_status", "organization_id", "status"),
        Index("ix_files_organization_id_kind", "organization_id", "kind"),
        Index("ix_files_created_by", "created_by"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(20))  # FileKind
    storage_key: Mapped[str] = mapped_column(String(500))
    file_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), server_default=FileStatus.PENDING)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
