"""Indexes for the platform-admin screens (Phase 2.5): organization name search, the user and
course status filters, and the "active today" count. No policy changes: platform admins already
read these tables through the existing `PLATFORM_ADMIN` branches.

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_organizations_name_trgm ON organizations "
        "USING gin (lower(name) gin_trgm_ops)"
    )
    op.create_index("ix_users_status", "users", ["status"])
    op.create_index("ix_courses_status", "courses", ["status"])
    op.create_index("ix_enrollments_last_accessed_at", "enrollments", ["last_accessed_at"])


def downgrade() -> None:
    op.drop_index("ix_enrollments_last_accessed_at", table_name="enrollments")
    op.drop_index("ix_courses_status", table_name="courses")
    op.drop_index("ix_users_status", table_name="users")
    op.drop_index("ix_organizations_name_trgm", table_name="organizations")
