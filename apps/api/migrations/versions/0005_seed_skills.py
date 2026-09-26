"""Starter skills taxonomy for placement training (reference data, present in every environment;
Phase 3 tags questions against it).

Ids are derived from the path (uuid5), so every environment has the same ids. Existing paths are
left untouched, so re-running or editing skills later is safe.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (path, name), parents before children.
SKILLS: tuple[tuple[str, str], ...] = (
    ("aptitude", "Aptitude"),
    ("aptitude.quantitative", "Quantitative"),
    ("aptitude.logical_reasoning", "Logical Reasoning"),
    ("aptitude.verbal", "Verbal"),
    ("programming_fundamentals", "Programming Fundamentals"),
    ("oop", "Object-Oriented Programming"),
    ("dsa", "Data Structures & Algorithms"),
    ("dsa.arrays", "Arrays"),
    ("dsa.strings", "Strings"),
    ("dsa.linked_lists", "Linked Lists"),
    ("dsa.stacks_queues", "Stacks & Queues"),
    ("dsa.trees", "Trees"),
    ("dsa.graphs", "Graphs"),
    ("dsa.dynamic_programming", "Dynamic Programming"),
    ("dsa.sorting_searching", "Sorting & Searching"),
    ("core_cs", "Core CS"),
    ("core_cs.operating_systems", "Operating Systems"),
    ("core_cs.dbms", "DBMS"),
    ("core_cs.computer_networks", "Computer Networks"),
)

_NAMESPACE = uuid.UUID("6f1b8f3c-2f5e-4c1a-9d59-7c3a1f0b6a10")


def skill_id(path: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, path)


def upgrade() -> None:
    insert = sa.text(
        """
        INSERT INTO skills (id, parent_id, name, slug, path)
        VALUES (:id, :parent_id, :name, :slug, CAST(:path AS ltree))
        ON CONFLICT (path) DO NOTHING
        """
    )
    for path, name in SKILLS:
        parent_path, _, slug = path.rpartition(".")
        op.get_bind().execute(
            insert,
            {
                "id": skill_id(path),
                "parent_id": skill_id(parent_path) if parent_path else None,
                "name": name,
                "slug": slug,
                "path": path,
            },
        )


def downgrade() -> None:
    delete = sa.text("DELETE FROM skills WHERE id = :id")
    for path, _ in reversed(SKILLS):
        op.get_bind().execute(delete, {"id": skill_id(path)})
