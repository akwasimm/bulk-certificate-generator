"""Celery application that runs certificate jobs outside the web process.

Late acknowledgements plus rejection-on-worker-loss mean a job that dies with
its worker is re-delivered instead of being lost, so jobs survive restarts.
"""
from celery import Celery

from .config import get_settings

settings = get_settings()

celery_app = Celery(
    "bulk_certificates",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.services.tasks"],
)

celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
)
