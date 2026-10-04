"""Assessment-owned DB queries. Runtime writes remain guarded until step 3."""

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def permitted_solutions(
    session: AsyncSession, attempt_id: UUID
) -> Sequence[Mapping[str, Any]]:
    """The DB function enforces current access, submitted state, mode and timing."""
    rows = await session.execute(
        text("SELECT * FROM app.quiz_attempt_solutions(:attempt)"), {"attempt": attempt_id}
    )
    return [dict(row) for row in rows.mappings()]
