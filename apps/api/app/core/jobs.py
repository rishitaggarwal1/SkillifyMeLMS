"""Background job queue seam.

Services enqueue work by task name (`jobs.send("enrollments.reconcile_course_org", ...)`) instead of
importing Celery tasks, which keeps modules free of import cycles and lets tests record jobs and run
them inline. Enqueue from `run_after_commit` so a job never runs before the rows it reads exist.
"""

from typing import Protocol


class JobQueue(Protocol):
    def send(self, task: str, *args: str) -> None: ...


class CeleryJobQueue:
    def send(self, task: str, *args: str) -> None:
        from app.worker import celery_app  # noqa: PLC0415 - the API process loads Celery lazily

        celery_app.send_task(task, args=list(args))
