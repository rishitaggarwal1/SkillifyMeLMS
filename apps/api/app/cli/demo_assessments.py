"""Guarded demo-only fixture preparation. Never imported by the API or workers.

Only time travel/reset lives here; scoring, submission and completion always use
the public services. Both the attempt ID and its demo enrollment must match.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import demo_progress


async def prepare_expired_attempt(
    session: AsyncSession, attempt_id: UUID, enrollment_id: UUID
) -> None:
    demo_progress._require_seed()
    await session.execute(
        text(
            "UPDATE quiz_attempts SET started_at = clock_timestamp() - interval '601 seconds', "
            "expires_at = clock_timestamp() - interval '1 second' "
            "WHERE id = :attempt AND enrollment_id = :enrollment AND state = 'in_progress'"
        ),
        {"attempt": attempt_id, "enrollment": enrollment_id},
    )
