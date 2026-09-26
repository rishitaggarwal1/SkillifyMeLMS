"""Object storage (S3; MinIO locally). boto3 is synchronous, so the async helpers run its calls in a
worker thread; Celery tasks may use the sync methods directly."""

import asyncio
from functools import cached_property
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config

from app.core.config import Settings

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client


class ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bucket = settings.s3_bucket

    @cached_property
    def client(self) -> "S3Client":
        s = self.settings
        return boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
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
