"""Skills API models."""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SkillSlug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,63}$")]
SkillPath = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,63}(\.[a-z0-9_]{1,63}){0,9}$")]
SkillName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class SkillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    parent_id: UUID | None
    name: str
    slug: str
    path: str
    description: str


class SkillCreate(BaseModel):
    parent_id: UUID | None = None
    name: SkillName
    slug: SkillSlug = Field(description="One path label: lowercase letters, digits, underscores.")
    description: str = Field(default="", max_length=500)


class SkillUpdate(BaseModel):
    name: SkillName | None = None
    description: str | None = Field(default=None, max_length=500)
