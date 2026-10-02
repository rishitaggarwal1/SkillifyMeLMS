from datetime import datetime

from pydantic import BaseModel, Field


class PlatformSummary(BaseModel):
    """Counts for the platform admin's landing page."""

    organizations: dict[str, int] = Field(description="By status (active, archived)")
    users: dict[str, int] = Field(description="By account status (invited, active, disabled)")
    users_by_role: dict[str, int] = Field(
        description="Distinct users holding each role in an active organization. Platform "
        "admins are a Keycloak realm role and aren't counted."
    )
    courses: dict[str, int] = Field(
        description='By status (active, archived), plus "published": active with a version'
    )
    enrollments: dict[str, int] = Field(description="By status (active, revoked)")
    active_today: int = Field(
        description="Distinct students who opened a lesson since midnight IST (learning "
        "activity; sign-ins aren't counted)"
    )
    active_since: datetime
    generated_at: datetime
