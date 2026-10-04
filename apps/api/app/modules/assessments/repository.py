"""Assessment-owned DB queries. Runtime writes remain guarded until step 3."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, text, union, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import CursorParams, paginate_by_id
from app.modules.assessments.models import (
    Question,
    QuestionBank,
    QuestionKey,
    QuestionSkill,
    Quiz,
    QuizVersion,
    QuizVersionKey,
    QuizVersionQuestion,
)

QuestionData = tuple[Question, QuestionKey, QuestionBank]
MAX_PUBLICATION_POOL = 1000


class AuthorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save(self, row: Any) -> None:
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)

    async def database_time(self) -> datetime:
        return (await self.session.execute(select(func.now()))).scalar_one()

    async def bank(self, bank_id: UUID) -> QuestionBank | None:
        return await self.session.get(QuestionBank, bank_id, populate_existing=True)

    async def banks(
        self, org: UUID, params: CursorParams, query: str | None
    ) -> tuple[list[QuestionBank], str | None]:
        stmt = select(QuestionBank).where(
            QuestionBank.organization_id == org, QuestionBank.archived_at.is_(None)
        )
        if query:
            stmt = stmt.where(
                func.lower(QuestionBank.name).contains(query.lower(), autoescape=True)
            )
        return await paginate_by_id(self.session, stmt, QuestionBank.id, params)

    async def bump_bank(self, bank_id: UUID, expected: int) -> QuestionBank | None:
        return await self.session.scalar(
            update(QuestionBank)
            .where(
                QuestionBank.id == bank_id,
                QuestionBank.revision == expected,
                QuestionBank.archived_at.is_(None),
            )
            .values(revision=QuestionBank.revision + 1, updated_at=func.now())
            .returning(QuestionBank)
            .execution_options(populate_existing=True)
        )

    async def lock_banks(self, bank_ids: Sequence[UUID]) -> None:
        if bank_ids:
            await self.session.execute(
                select(QuestionBank.id)
                .where(QuestionBank.id.in_(bank_ids))
                .order_by(QuestionBank.id)
                .with_for_update()
            )

    async def question(self, question_id: UUID) -> Question | None:
        return await self.session.get(Question, question_id, populate_existing=True)

    async def keys(self, ids: Sequence[UUID]) -> dict[UUID, QuestionKey]:
        if not ids:
            return {}
        return {
            k.question_id: k
            for k in await self.session.scalars(
                select(QuestionKey).where(QuestionKey.question_id.in_(ids))
            )
        }

    async def tags(self, ids: Sequence[UUID]) -> dict[UUID, list[UUID]]:
        result: dict[UUID, list[UUID]] = {}
        if ids:
            rows = await self.session.execute(
                select(QuestionSkill.question_id, QuestionSkill.skill_id)
                .where(QuestionSkill.question_id.in_(ids))
                .order_by(QuestionSkill.skill_id)
            )
            for qid, sid in rows:
                result.setdefault(qid, []).append(sid)
        return result

    async def replace_tags(self, question: Question, ids: Sequence[UUID]) -> None:
        await self.session.execute(
            delete(QuestionSkill).where(QuestionSkill.question_id == question.id)
        )
        self.session.add_all(
            [
                QuestionSkill(
                    question_id=question.id, skill_id=sid, organization_id=question.organization_id
                )
                for sid in ids
            ]
        )
        await self.session.flush()

    @staticmethod
    def _question_query(
        bank_id: UUID, query: str | None, kind: str | None, skill_ids: Sequence[UUID]
    ) -> Any:
        stmt = select(Question).where(Question.bank_id == bank_id, Question.archived_at.is_(None))
        if query:
            stmt = stmt.where(func.lower(Question.prompt).contains(query.lower(), autoescape=True))
        if kind:
            stmt = stmt.where(Question.question_type == kind)
        for sid in skill_ids:
            stmt = stmt.where(
                select(QuestionSkill.question_id)
                .where(QuestionSkill.question_id == Question.id, QuestionSkill.skill_id == sid)
                .exists()
            )
        return stmt

    async def questions(
        self,
        bank_id: UUID,
        params: CursorParams,
        query: str | None,
        kind: str | None,
        skill_ids: Sequence[UUID],
    ) -> tuple[list[Question], str | None]:
        return await paginate_by_id(
            self.session, self._question_query(bank_id, query, kind, skill_ids), Question.id, params
        )

    async def question_data(self, ids: Sequence[UUID]) -> list[QuestionData]:
        if not ids:
            return []
        rows = await self.session.execute(
            select(Question, QuestionKey, QuestionBank)
            .join(QuestionKey, QuestionKey.question_id == Question.id)
            .join(QuestionBank, QuestionBank.id == Question.bank_id)
            .where(
                Question.id.in_(ids),
                Question.archived_at.is_(None),
                QuestionBank.archived_at.is_(None),
            )
        )
        return [(q, k, b) for q, k, b in rows]

    async def pool_data(self, filters: Sequence[tuple[UUID, Sequence[UUID]]]) -> list[QuestionData]:
        if not filters:
            return []
        selections = []
        for bank_id, skill_ids in filters:
            eligible = self._question_query(bank_id, None, None, skill_ids).with_only_columns(
                Question.id
            )
            bounded = eligible.order_by(Question.id).limit(MAX_PUBLICATION_POOL + 1).subquery()
            selections.append(select(bounded.c.id))
        ids = selections[0] if len(selections) == 1 else union(*selections)
        rows = await self.session.execute(
            select(Question, QuestionKey, QuestionBank)
            .join(QuestionKey, QuestionKey.question_id == Question.id)
            .join(QuestionBank, QuestionBank.id == Question.bank_id)
            .where(Question.id.in_(ids), QuestionBank.archived_at.is_(None))
        )
        return [(q, k, b) for q, k, b in rows]

    async def referenced_banks(self, question_ids: Sequence[UUID]) -> list[UUID]:
        if not question_ids:
            return []
        return list(
            await self.session.scalars(
                select(Question.bank_id).where(Question.id.in_(question_ids)).distinct()
            )
        )

    async def quizzes(self, lesson_ids: Sequence[UUID]) -> list[Quiz]:
        if not lesson_ids:
            return []
        return list(
            await self.session.scalars(
                select(Quiz)
                .where(Quiz.lesson_id.in_(lesson_ids))
                .execution_options(populate_existing=True)
            )
        )

    async def published_versions(
        self, version_id: UUID, lesson_ids: Sequence[UUID]
    ) -> list[QuizVersion]:
        if not lesson_ids:
            return []
        return list(
            await self.session.scalars(
                select(QuizVersion).where(
                    QuizVersion.course_version_id == version_id,
                    QuizVersion.lesson_id.in_(lesson_ids),
                )
            )
        )

    async def published_questions(self, version_ids: Sequence[UUID]) -> list[QuizVersionQuestion]:
        if not version_ids:
            return []
        return list(
            await self.session.scalars(
                select(QuizVersionQuestion)
                .where(QuizVersionQuestion.quiz_version_id.in_(version_ids))
                .order_by(QuizVersionQuestion.position)
            )
        )

    async def published_keys(self, question_ids: Sequence[UUID]) -> dict[UUID, QuizVersionKey]:
        if not question_ids:
            return {}
        return {
            k.question_id: k
            for k in await self.session.scalars(
                select(QuizVersionKey).where(QuizVersionKey.question_id.in_(question_ids))
            )
        }


async def permitted_solutions(
    session: AsyncSession, attempt_id: UUID
) -> Sequence[Mapping[str, Any]]:
    """The DB function enforces current access, submitted state, mode and timing."""
    rows = await session.execute(
        text("SELECT * FROM app.quiz_attempt_solutions(:attempt)"), {"attempt": attempt_id}
    )
    return [dict(row) for row in rows.mappings()]
