"""Object storage (S3; MinIO locally). boto3 is synchronous, so the async helpers run its calls in a
worker thread; Celery tasks may use the sync methods directly."""

import asyncio
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import Settings

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client


@dataclass(frozen=True, slots=True)
class PresignedPost:
    url: str
    fields: dict[str, str]


class ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bucket = settings.s3_bucket

    @cached_property
    def client(self) -> "S3Client":
        return self._client(self.settings.s3_endpoint_url)

    @cached_property
    def browser_client(self) -> "S3Client":
        """Signs URLs for the browser: the signature covers the host, so it must be the public
        endpoint (MinIO's host-published port locally), not the in-network one."""
        return self._client(self.settings.s3_browser_endpoint_url)

    def _client(self, endpoint_url: str | None) -> "S3Client":
        s = self.settings
        return boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key_id.get_secret_value() if s.s3_access_key_id else None,
            aws_secret_access_key=(
                s.s3_secret_access_key.get_secret_value() if s.s3_secret_access_key else None
            ),
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    async def aput_bytes(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self.put_bytes, key, data, content_type)

    async def aget_bytes(self, key: str) -> bytes:
        return await asyncio.to_thread(self.get_bytes, key)

    # ------------------------------------------------------------------ browser uploads/downloads

    def presigned_post(
        self, key: str, *, content_type: str, max_bytes: int, expires_in: int
    ) -> PresignedPost:
        """A browser form upload that S3 itself restricts to one key, one content type and a
        size range (a presigned PUT can't enforce size)."""
        post = self.browser_client.generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", 1, max_bytes],
            ],
            ExpiresIn=expires_in,
        )
        return PresignedPost(url=post["url"], fields=dict(post["fields"]))

    def presigned_get(self, key: str, *, expires_in: int, download_name: str | None = None) -> str:
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if download_name is not None:
            params["ResponseContentDisposition"] = f'inline; filename="{download_name}"'
        return self.browser_client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires_in
        )

    def presigned_put(self, key: str, *, content_type: str, expires_in: int) -> str:
        return self.browser_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_in,
        )

    # ------------------------------------------------------------------ server-side inspection

    def size(self, key: str) -> int | None:
        """Object size in bytes, or None if it doesn't exist."""
        try:
            return int(self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise

    def read_range(self, key: str, start: int, length: int) -> bytes:
        end = start + length - 1
        body = self.client.get_object(Bucket=self.bucket, Key=key, Range=f"bytes={start}-{end}")
        return body["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)
