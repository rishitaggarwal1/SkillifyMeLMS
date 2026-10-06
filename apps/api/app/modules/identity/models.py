"""Identity tables: organizations, users, memberships, batches, batch members, invitations and CSV
import jobs. The schema (constraints, RLS policies, helper functions) is defined in migration 0002;
these mappings must stay in sync with it."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OrgRole(StrEnum):
    ORG_ADMIN = "org_admin"
    INSTRUCTOR = "instructor"
    LAB_AUTHOR = "lab_author"
    STUDENT = "student"


STAFF_ROLES: frozenset[OrgRole] = frozenset(
    {OrgRole.ORG_ADMIN, OrgRole.INSTRUCTOR, OrgRole.LAB_AUTHOR}
)


class OrganizationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class UserStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    DISABLED = "disabled"


class BatchStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class ImportJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (
        Index("ix_organizations_status", "status"),
        # The publishers' organization directory orders and pages by name (migration 0009).
        Index("ix_organizations_lower_name_id", func.lower(text("name")), "id"),
        # Platform admins search organizations by name (migration 0010).
        Index(
            "ix_organizations_name_trgm",
            func.lower(text("name")),
            postgresql_using="gin",
            postgresql_ops={"lower(name)": "gin_trgm_ops"},
        ),
    )

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    is_content_publisher: Mapped[bool] = mapped_column(Boolean, server_default=false())
    status: Mapped[str] = mapped_column(String(20), server_default=OrganizationStatus.ACTIVE)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Global (not tenant-owned): one person can belong to several organizations."""

    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_email_lower", func.lower(text("email")), unique=True),
        Index(
            "ix_users_email_trgm",
            func.lower(text("email")),
            postgresql_using="gin",
            postgresql_ops={"lower(email)": "gin_trgm_ops"},
        ),
        Index(
            "ix_users_full_name_trgm",
            func.lower(text("full_name")),
            postgresql_using="gin",
            postgresql_ops={"lower(full_name)": "gin_trgm_ops"},
        ),
        Index("ix_users_status", "status"),
    )

    keycloak_sub: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(320))
    full_name: Mapped[str] = mapped_column(String(200), server_default="")
    status: Mapped[str] = mapped_column(String(20), server_default=UserStatus.INVITED)
    last_login_at: Mapped[datetime | None]


class Membership(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", "role", name="uq_memberships_user_org_role"),
        Index("ix_memberships_organization_id_role", "organization_id", "role"),
        Index("ix_memberships_created_by", "created_by"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(20))
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Batch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "batches"
    __table_args__ = (
        # Composite-FK target: lets other tables guarantee a batch belongs to a given org.
        UniqueConstraint("id", "organization_id", name="uq_batches_id_organization_id"),
        Index("ix_batches_organization_id_status", "organization_id", "status"),
        Index("ix_batches_created_by", "created_by"),
        Index("uq_batches_org_name", "organization_id", func.lower(text("name")), unique=True),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(1000), server_default="")
    status: Mapped[str] = mapped_column(String(20), server_default=BatchStatus.ACTIVE)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class BatchMember(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "batch_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id", "organization_id"],
            ["batches.id", "batches.organization_id"],
            ondelete="CASCADE",
            name="fk_batch_members_batch_org",
        ),
        UniqueConstraint("batch_id", "user_id", name="uq_batch_members_batch_user"),
        Index("ix_batch_members_batch_org", "batch_id", "organization_id"),  # composite FK
        Index("ix_batch_members_user_org", "user_id", "organization_id"),
        Index("ix_batch_members_added_by", "added_by"),
    )

    batch_id: Mapped[UUID] = mapped_column(Uuid)
    organization_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    added_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Invitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "invitations"
    __table_args__ = (
        Index("ix_invitations_organization_id_status", "organization_id", "status"),
        Index("ix_invitations_user_id", "user_id"),
        Index("ix_invitations_invited_by", "invited_by"),
        Index(
            "ix_invitations_expires_at_pending",
            "expires_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "uq_invitations_pending_email",
            "organization_id",
            func.lower(text("email")),
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    email: Mapped[str] = mapped_column(String(320))
    roles: Mapped[list[str]] = mapped_column(ARRAY(Text))
    batch_ids: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid), server_default="{}")
    status: Mapped[str] = mapped_column(String(20), server_default=InvitationStatus.PENDING)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    invited_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime]


class ImportJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "import_jobs"
    __table_args__ = (
        Index("ix_import_jobs_org_status", "organization_id", "status"),
        # The migration adds `ON DELETE SET NULL (batch_id)` (PG15+ column list): only batch_id
        # is nulled, organization_id stays. SQLAlchemy can't express the column list.
        ForeignKeyConstraint(
            ["batch_id", "organization_id"],
            ["batches.id", "batches.organization_id"],
            name="fk_import_jobs_batch_org",
            ondelete="SET NULL",
        ),
        Index("ix_import_jobs_organization_id", "organization_id", "id"),
        Index("ix_import_jobs_batch_org", "batch_id", "organization_id"),  # composite FK
        Index("ix_import_jobs_created_by", "created_by"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    batch_id: Mapped[UUID | None] = mapped_column(Uuid)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(30), server_default=ImportJobStatus.QUEUED)
    file_key: Mapped[str] = mapped_column(String(500))
    file_name: Mapped[str] = mapped_column(String(255), server_default="")
    total_rows: Mapped[int] = mapped_column(Integer, server_default="0")
    processed_rows: Mapped[int] = mapped_column(Integer, server_default="0")
    created_count: Mapped[int] = mapped_column(Integer, server_default="0")
    skipped_count: Mapped[int] = mapped_column(Integer, server_default="0")
    error_count: Mapped[int] = mapped_column(Integer, server_default="0")
    error_message: Mapped[str | None] = mapped_column(String(1000))
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class ImportJobError(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "import_job_errors"
    __table_args__ = (
        Index("ix_import_job_errors_job_row", "import_job_id", "row_number"),
        Index("ix_import_job_errors_organization_id", "organization_id"),
    )

    import_job_id: Mapped[UUID] = mapped_column(ForeignKey("import_jobs.id", ondelete="CASCADE"))
    organization_id: Mapped[UUID] = mapped_column(Uuid)
    row_number: Mapped[int] = mapped_column(Integer)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    code: Mapped[str] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(String(500))
