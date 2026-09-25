"""Model registry: import every module's models here so `Base.metadata` is complete for Alembic."""

from app.db.base import Base
from app.db.outbox import OutboxEvent

__all__ = ["Base", "OutboxEvent"]
