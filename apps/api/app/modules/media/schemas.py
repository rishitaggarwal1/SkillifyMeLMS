"""Media API models."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

VideoStatusName = Literal["created", "processing", "ready", "failed"]


class VideoCreate(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class VideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    provider: str
    status: VideoStatusName
    duration_seconds: int | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class UploadTicketOut(BaseModel):
    protocol: Literal["s3_put", "tus"] = Field(
        description="s3_put: PUT the file to `url` with `headers`. "
        "tus: TUS upload to `url` with `headers`."
    )
    url: str
    fields: dict[str, str]
    headers: dict[str, str]
    expires_at: datetime


class VideoUploadOut(BaseModel):
    video: VideoOut
    upload: UploadTicketOut


class PlaybackOut(BaseModel):
    url: str
    kind: Literal["mp4", "hls"]
    expires_at: datetime
