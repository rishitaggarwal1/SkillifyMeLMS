"""Celery application. Start a worker with: `celery -A app.worker worker --loglevel=INFO`.

Task modules register themselves under app/modules/<module>/tasks.py and are autodiscovered.
Tasks must be idempotent (Celery with acks_late can redeliver) and must set tenant context
explicitly; there is no request.
"""

from celery import Celery
from celery.signals import setup_logging

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.validation import configure_email_validation

settings = get_settings()
configure_email_validation(settings)

celery_app = Celery("skillifyme", broker=settings.celery_broker)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
)
celery_app.conf.beat_schedule = {
    "video-progress-flush": {
        "task": "enrollments.flush_video_progress",
        "schedule": settings.progress_flush_interval_seconds,
    },
    "identity-expire-invitations": {
        "task": "identity.expire_invitations",
        "schedule": 3600.0,  # hourly
    },
}
celery_app.autodiscover_tasks(
    ["app.modules.identity", "app.modules.enrollments", "app.modules.media"], related_name="tasks"
)


@setup_logging.connect
def _setup_logging(**_kwargs: object) -> None:
    configure_logging(settings.log_level, json_logs=settings.log_json)
