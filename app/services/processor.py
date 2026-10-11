"""Background processing of certificate jobs.

Render PDFs on a small thread pool. Each certificate is committed on its own so
pollers see live progress, and a failure of one render never affects the others.
Worker threads receive only an immutable RenderTask and return bytes; all
database access stays on the orchestrating thread.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select

from ..config import get_settings
from ..database import SessionLocal
from ..models import Certificate, CertificateJob, CertificateStatus, JobStatus
from ..utils import utcnow
from .generator import render_certificate_pdf

logger = logging.getLogger(__name__)
settings = get_settings()

# One bounded pool shared by every job, so concurrent submissions cannot spawn an
# unbounded number of threads. Created lazily so it can be recreated after the app
# lifespan shuts it down (relevant to tests that start and stop the app repeatedly).
_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=max(1, settings.max_workers),
                thread_name_prefix="certificate",
            )
        return _executor


def shutdown_executor() -> None:
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=True)
            _executor = None


@dataclass(frozen=True)
class RenderTask:
    certificate_id: str
    recipient_name: str
    course_name: str
    issued_by: str | None
    issued_on: date


def _render_one(task: RenderTask) -> bytes:
    return render_certificate_pdf(
        certificate_id=task.certificate_id,
        recipient_name=task.recipient_name,
        course_name=task.course_name,
        issued_by=task.issued_by,
        issued_on=task.issued_on,
    )


def process_job(job_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(CertificateJob, job_id)
        # Only a freshly queued job is processed, so a duplicate task is a no-op.
        if job is None or job.status is not JobStatus.QUEUED:
            if job is not None:
                logger.warning("Job %s skipped: status is %s", job_id, job.status.value)
            return

        job.status = JobStatus.PROCESSING
        job.updated_at = utcnow()
        db.commit()

        job_dir = Path(settings.storage_dir) / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        pending = db.scalars(
            select(Certificate)
            .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING)
            .order_by(Certificate.recipient_index)
        ).all()
        cert_by_id = {cert.id: cert for cert in pending}

        tasks = [
            RenderTask(
                certificate_id=cert.id,
                recipient_name=cert.full_name or "",
                course_name=job.course_name,
                issued_by=job.issued_by,
                issued_on=job.issued_on,
            )
            for cert in pending
        ]

        futures = {_get_executor().submit(_render_one, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            certificate = cert_by_id[task.certificate_id]
            try:
                pdf_bytes = future.result()
                (job_dir / f"{certificate.id}.pdf").write_bytes(pdf_bytes)
                certificate.status = CertificateStatus.GENERATED
                certificate.file_path = f"{job_id}/{certificate.id}.pdf"
                certificate.generated_at = utcnow()
                certificate.error = None
                job.succeeded_recipients += 1
            except Exception:
                # Generic message to callers; the full traceback goes to the logs.
                logger.exception("Certificate %s (%s) failed", certificate.id, task.recipient_name)
                certificate.status = CertificateStatus.FAILED
                certificate.error = "Generation failed."
                job.failed_recipients += 1

            job.processed_recipients += 1
            job.updated_at = utcnow()
            db.commit()

        job.status = (
            JobStatus.COMPLETED_WITH_FAILURES if job.failed_recipients else JobStatus.COMPLETED
        )
        job.completed_at = utcnow()
        job.updated_at = utcnow()
        db.commit()
        logger.info(
            "Job %s finished: %s generated, %s failed",
            job_id, job.succeeded_recipients, job.failed_recipients,
        )

    except Exception:
        db.rollback()
        logger.exception("Job %s crashed, marking as failed", job_id)
        try:
            failed_job = db.get(CertificateJob, job_id)
            if failed_job is not None:
                failed_job.status = JobStatus.FAILED
                failed_job.updated_at = utcnow()
                db.commit()
        except Exception:
            logger.exception("Job %s: could not persist FAILED status", job_id)
    finally:
        db.close()
