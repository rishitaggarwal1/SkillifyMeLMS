"""Domain events published by the identity module (schemas documented in docs/events.md).

Batch membership changes are the signal Phase 2 uses to enroll/unenroll students. Events are keyed
by batch id, so every change to one batch is delivered in order.
"""

from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.outbox import add_outbox_event

BATCH_MEMBER_AGGREGATE = "batch_member"
BATCH_MEMBER_ADDED = "batch_member_added"
BATCH_MEMBER_REMOVED = "batch_member_removed"
SCHEMA_VERSION = 1

AddReason = Literal["added", "invitation", "import"]
RemoveReason = Literal["removed", "left_organization", "invitation_revoked"]


def _emit(
    session: AsyncSession,
    event_type: str,
    *,
    organization_id: UUID,
    batch_id: UUID,
    user_ids: Sequence[UUID],
    actor_user_id: UUID | None,
    reason: str,
) -> None:
    for user_id in user_ids:
        add_outbox_event(
            session,
            aggregate_type=BATCH_MEMBER_AGGREGATE,
            aggregate_id=batch_id,
            event_type=event_type,
            organization_id=organization_id,
            payload={
                "batch_id": str(batch_id),
                "user_id": str(user_id),
                "organization_id": str(organization_id),
                "actor_user_id": str(actor_user_id) if actor_user_id else None,
                "reason": reason,
            },
            headers={"version": SCHEMA_VERSION},
        )


def batch_members_added(
    session: AsyncSession,
    *,
    organization_id: UUID,
    batch_id: UUID,
    user_ids: Sequence[UUID],
    actor_user_id: UUID | None,
    reason: AddReason = "added",
) -> None:
    _emit(
        session,
        BATCH_MEMBER_ADDED,
        organization_id=organization_id,
        batch_id=batch_id,
        user_ids=user_ids,
        actor_user_id=actor_user_id,
        reason=reason,
    )


def batch_members_removed(
    session: AsyncSession,
    *,
    organization_id: UUID,
    batch_id: UUID,
    user_ids: Sequence[UUID],
    actor_user_id: UUID | None,
    reason: RemoveReason = "removed",
) -> None:
    _emit(
        session,
        BATCH_MEMBER_REMOVED,
        organization_id=organization_id,
        batch_id=batch_id,
        user_ids=user_ids,
        actor_user_id=actor_user_id,
        reason=reason,
    )
