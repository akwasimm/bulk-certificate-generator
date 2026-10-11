"""HTTP endpoints for certificate jobs and certificates."""
import logging
import re
import tempfile
import zipfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import Certificate, CertificateJob, CertificateStatus
from ..schemas import CertificateJobCreate, CertificateJobOut, CertificateOut
from ..security import require_api_key
from ..services.tasks import process_job_task
from ..services.validation import clean_recipient
from ..utils import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1",
    tags=["certificates"],
    dependencies=[Depends(require_api_key)],
)


def _get_job_or_404(db: Session, job_id: UUID) -> CertificateJob:
    job = db.get(CertificateJob, str(job_id))
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Certificate job not found.")
    return job


def _get_certificate_or_404(db: Session, certificate_id: UUID) -> Certificate:
    certificate = db.get(Certificate, str(certificate_id))
    if certificate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Certificate not found.")
    return certificate


def _certificate_filename(certificate: Certificate) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", certificate.full_name or "recipient").strip("-")
    return f"certificate-{certificate.recipient_index + 1:03d}-{safe or 'recipient'}.pdf"


def _iter_file(fileobj, chunk_size: int = 64 * 1024):
    try:
        while chunk := fileobj.read(chunk_size):
            yield chunk
    finally:
        fileobj.close()


@router.post(
    "/certificate-jobs",
    response_model=CertificateJobOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a bulk certificate generation job",
)
def create_certificate_job(
    payload: CertificateJobCreate,
    response: Response,
    db: Session = Depends(get_db),
) -> CertificateJob:
    job = CertificateJob(
        course_name=payload.course_name,
        issued_by=payload.issued_by,
        issued_on=payload.issued_on or utcnow().date(),
        total_recipients=len(payload.recipients),
    )
    db.add(job)
    db.flush()

    for index, recipient in enumerate(payload.recipients):
        name, error = clean_recipient(recipient)
        certificate = Certificate(
            job_id=job.id,
            recipient_index=index,
            full_name=name or (recipient.full_name or "").strip()[:200] or None,
            email=(recipient.email or "").strip()[:320] or None,
        )
        if error is not None:
            certificate.status = CertificateStatus.FAILED
            certificate.error = error
            job.failed_recipients += 1
            job.processed_recipients += 1
        db.add(certificate)

    db.commit()
    db.refresh(job)

    process_job_task.delay(job.id)

    response.headers["Location"] = f"/api/v1/certificate-jobs/{job.id}"
    return job


@router.get(
    "/certificate-jobs/{job_id}",
    response_model=CertificateJobOut,
    summary="Check job status / progress",
)
def get_certificate_job(job_id: UUID, db: Session = Depends(get_db)) -> CertificateJob:
    return _get_job_or_404(db, job_id)


@router.get(
    "/certificate-jobs/{job_id}/certificates",
    response_model=list[CertificateOut],
    summary="List per-recipient results of a job",
)
def list_job_certificates(
    job_id: UUID,
    status_filter: CertificateStatus | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
) -> list[Certificate]:
    _get_job_or_404(db, job_id)
    statement = (
        select(Certificate)
        .where(Certificate.job_id == str(job_id))
        .order_by(Certificate.recipient_index)
    )
    if status_filter is not None:
        statement = statement.where(Certificate.status == status_filter)
    return list(db.scalars(statement).all())


@router.get(
    "/certificates/{certificate_id}/download",
    summary="Download one generated certificate (PDF)",
    responses={409: {"description": "Certificate not generated yet."}},
)
def download_certificate(certificate_id: UUID, db: Session = Depends(get_db)):
    certificate = _get_certificate_or_404(db, certificate_id)
    if certificate.status is not CertificateStatus.GENERATED or not certificate.file_path:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Certificate file is not available (status: {certificate.status.value}).",
        )
    path = Path(get_settings().storage_dir) / certificate.file_path
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Certificate file is missing from storage.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=_certificate_filename(certificate),
    )


@router.get(
    "/certificate-jobs/{job_id}/download",
    summary="Download every generated certificate of a job as one ZIP",
    responses={409: {"description": "No generated certificates available yet."}},
)
def download_job_zip(job_id: UUID, db: Session = Depends(get_db)):
    _get_job_or_404(db, job_id)
    certificates = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == str(job_id), Certificate.status == CertificateStatus.GENERATED)
        .order_by(Certificate.recipient_index)
    ).all()

    # Streamed from a spooled temp file so large batches never sit in memory.
    archive = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    included = 0
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for certificate in certificates:
            path = Path(get_settings().storage_dir) / certificate.file_path
            if path.is_file():
                bundle.write(path, arcname=_certificate_filename(certificate))
                included += 1
            else:
                logger.warning("Certificate %s file missing from storage: %s", certificate.id, path)

    if included == 0:
        archive.close()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="No generated certificates are available yet.")

    archive.seek(0)
    return StreamingResponse(
        _iter_file(archive),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="certificates-{job_id}.zip"'},
    )
