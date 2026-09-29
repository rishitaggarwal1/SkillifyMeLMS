"""Celery tasks for the courses module (autodiscovered by app.worker). All are idempotent."""

import asyncio

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.modules.courses.jobs import REVALIDATE_CATALOG
from app.worker import celery_app

logger = get_logger(__name__)
CATALOG_TAG = "catalog"  # the tag the web app's catalog fetches use


class RevalidationError(RuntimeError):
    pass


async def revalidate_catalog(settings: Settings, http: httpx.AsyncClient) -> bool:
    """Ask the web app to revalidate its statically generated catalog pages. Returns False when
    revalidation isn't configured (the pages still refresh on their time-based revalidation).
    Raises RevalidationError on a failed call, so Celery retries it."""
    if not settings.web_internal_url or settings.revalidate_secret is None:
        logger.info("catalog_revalidation_skipped", reason="not configured")
        return False
    url = f"{settings.web_internal_url.rstrip('/')}/api/revalidate"
    try:
        response = await http.post(
            url,
            json={"tags": [CATALOG_TAG]},
            headers={"x-revalidate-secret": settings.revalidate_secret.get_secret_value()},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        raise RevalidationError(str(exc)) from exc
    if response.status_code != httpx.codes.OK:
        msg = f"revalidation failed with HTTP {response.status_code}"
        raise RevalidationError(msg)
    logger.info("catalog_revalidated", tags=[CATALOG_TAG])
    return True


async def _revalidate() -> bool:
    async with httpx.AsyncClient() as http:
        return await revalidate_catalog(get_settings(), http)


@celery_app.task(
    name=REVALIDATE_CATALOG,
    autoretry_for=(RevalidationError,),
    retry_backoff=2,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=8,
)
def revalidate_catalog_task() -> bool:
    return asyncio.run(_revalidate())
