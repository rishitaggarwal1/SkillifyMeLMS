"""Model registry: import every module's models here so `Base.metadata` is complete for Alembic."""

from app.db.base import Base
from app.db.outbox import OutboxEvent
from app.modules.audit.models import AuditLog
from app.modules.identity.models import (
    Batch,
    BatchMember,
    ImportJob,
    ImportJobError,
    Invitation,
    Membership,
    Organization,
    User,
)

__all__ = [
    "AuditLog",
    "Base",
    "Batch",
    "BatchMember",
    "ImportJob",
    "ImportJobError",
    "Invitation",
    "Membership",
    "Organization",
    "OutboxEvent",
    "User",
]
