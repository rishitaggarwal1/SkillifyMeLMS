"""Append-only audit trail of admin actions (UPDATE/DELETE are revoked from the app role)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, Index, String, Uuid, false, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_organization_id_id", "organization_id", "id"),
        Index("ix_audit_log_actor_user_id", "actor_user_id"),
        Index("ix_audit_log_target", "target_type", "target_id"),
        Index("ix_audit_log_action", "action"),
    )

    # No FKs on purpose: audit rows must outlive the orgs and users they describe.
    organization_id: Mapped[UUID | None] = mapped_column(Uuid)
    actor_user_id: Mapped[UUID | None] = mapped_column(Uuid)
    actor_is_platform_admin: Mapped[bool] = mapped_column(Boolean, server_default=false())
    action: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(100))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(INET)
    request_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
