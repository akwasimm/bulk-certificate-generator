"""Celery task that dispatches to the synchronous job processor."""
from ..celery_app import celery_app
from .processor import process_job


@celery_app.task(name="certificates.process_job")
def process_job_task(job_id: str) -> None:
    process_job(job_id)
