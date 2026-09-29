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


# ============================================================================ files

FileKindName = Literal["pdf", "image"]
FileStatusName = Literal["pending", "ready", "rejected"]
# The only image types accepted; everything else (SVG, BMP, TIFF, HEIC...) is refused.
ImageContentType = Literal["image/png", "image/jpeg", "image/webp", "image/gif"]
# Shown to users and used in the download's Content-Disposition: no control characters, quotes,
# backslashes or path separators.
FileName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=255, pattern=r'^[^\x00-\x1f\x7f"\/]+$'
    ),
]


class FileCreate(BaseModel):
    kind: FileKindName
    file_name: FileName
    content_type: Literal["application/pdf"] | ImageContentType = Field(
        description="application/pdf for kind=pdf; image/png, image/jpeg, image/webp or "
        "image/gif for kind=image. Storage only accepts an upload with exactly this Content-Type."
    )


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: FileKindName
    file_name: str
    content_type: str
    size_bytes: int | None
    status: FileStatusName
    created_at: datetime
    updated_at: datetime


class PresignedPostOut(BaseModel):
    url: str
    fields: dict[str, str] = Field(
        description="Send these as form fields, then the file as the last field named `file`."
    )
    max_bytes: int
    expires_at: datetime


class FileUploadOut(BaseModel):
    file: FileOut
    upload: PresignedPostOut


class FileDownloadOut(BaseModel):
    url: str
    file_name: str
    expires_at: datetime
