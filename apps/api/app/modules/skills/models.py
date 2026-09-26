"""Skills taxonomy (global in this phase): DSA > Arrays > Two Pointers.

`path` is an ltree of slugs (`dsa.arrays.two_pointers`), so "everything under DSA" is one
index-backed query: `path <@ 'dsa'`. Lessons (and, from Phase 3, questions and problems) are tagged
with skills.
"""

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import LTree


class Skill(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("path", name="uq_skills_path"),
        Index("ix_skills_path_gist", "path", postgresql_using="gist"),
        Index("ix_skills_parent_id", "parent_id"),
    )

    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("skills.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(63))  # one ltree label: [a-z0-9_]
    path: Mapped[str] = mapped_column(LTree())
    description: Mapped[str] = mapped_column(String(500), server_default="")
