"""Indexes for assessment authoring filters.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_questions_bank_archived_id", "questions", ["bank_id", "archived_at", "id"])
    op.create_index(
        "ix_question_banks_name_search",
        "question_banks",
        [sa.text("lower(name) gin_trgm_ops")],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_questions_prompt_search",
        "questions",
        [sa.text("lower(prompt) gin_trgm_ops")],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_questions_prompt_search", table_name="questions")
    op.drop_index("ix_question_banks_name_search", table_name="question_banks")
    op.drop_index("ix_questions_bank_archived_id", table_name="questions")
