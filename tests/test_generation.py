"""Unit tests for the renderer, recipient validation and the background processor."""
import io
from datetime import date

from pypdf import PdfReader

from app.models import Certificate, CertificateJob, CertificateStatus, JobStatus
from app.schemas import RecipientInput
from app.services.generator import render_certificate_pdf
from app.services.validation import clean_recipient


def _render(name: str = "Ada Lovelace", course: str = "Advanced Python Programming",
            issuer: str | None = "Arena Academy", cert_id: str = "test-certificate-id") -> bytes:
    return render_certificate_pdf(
        certificate_id=cert_id,
        recipient_name=name,
        course_name=course,
        issued_by=issuer,
        issued_on=date(2026, 10, 11),
    )


def test_pdf_is_valid_single_page_with_expected_text():
    pdf_bytes = _render()

    assert pdf_bytes.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) == 1

    text = reader.pages[0].extract_text()
    assert "Ada Lovelace" in text
    assert "Advanced Python Programming" in text
    assert "Arena Academy" in text
    assert "11 October 2026" in text
    assert "test-certificate-id" in text


def test_very_long_recipient_name_is_shrunk_to_fit_but_kept_intact():
    long_name = "Bartholomew Montgomery-Fitzgerald de Winterbourne-Whitmore III"

    pdf_bytes = _render(name=long_name)

    text = PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text()
    assert long_name in text


def test_certificate_without_issuer_still_renders():
    pdf_bytes = _render(issuer=None)
    assert pdf_bytes.startswith(b"%PDF")


def test_clean_recipient_normalises_whitespace():
    name, error = clean_recipient(RecipientInput(full_name="   Ada    Lovelace  "))
    assert (name, error) == ("Ada Lovelace", None)


def test_clean_recipient_allows_missing_email():
    name, error = clean_recipient(RecipientInput(full_name="Alan Turing", email=None))
    assert (name, error) == ("Alan Turing", None)


def test_clean_recipient_rejects_bad_values():
    bad_rows = [
        RecipientInput(full_name=None),
        RecipientInput(full_name="   "),
        RecipientInput(full_name="x" * 121),
        RecipientInput(full_name="Bad", email="not-an-email"),
        RecipientInput(full_name="Bad", email="two@at@signs"),
    ]
    for row in bad_rows:
        name, error = clean_recipient(row)
        assert name is None
        assert error and error.startswith("Rejected:")


def test_process_job_persists_progress_incrementally(db, monkeypatch):
    """While the first certificate is still rendering, the job must already be
    'processing' with processed_recipients == 0, proving progress is written per
    certificate rather than once at the end."""
    from app.database import SessionLocal
    from app.services import processor

    session = SessionLocal()
    job = CertificateJob(
        course_name="Concurrency 101",
        issued_by="Acme Corp",
        issued_on=date(2026, 10, 11),
        total_recipients=2,
    )
    session.add(job)
    session.flush()
    job_id = job.id
    session.add(Certificate(job_id=job_id, recipient_index=0, full_name="Ada Lovelace"))
    session.add(Certificate(job_id=job_id, recipient_index=1, full_name="Grace Hopper"))
    session.commit()

    observations = []
    real_render = processor.render_certificate_pdf

    def spy_render(**kwargs):
        if kwargs["recipient_name"] == "Ada Lovelace":
            probe = SessionLocal()
            try:
                current = probe.get(CertificateJob, job_id)
                observations.append((current.status, current.processed_recipients))
            finally:
                probe.close()
        return real_render(**kwargs)

    monkeypatch.setattr(processor, "render_certificate_pdf", spy_render)

    processor.process_job(job_id)

    session.expire_all()
    final = session.get(CertificateJob, job_id)
    certificates = sorted(final.certificates, key=lambda c: c.recipient_index)

    assert observations == [(JobStatus.PROCESSING, 0)]
    assert final.status is JobStatus.COMPLETED
    assert final.processed_recipients == 2
    assert final.succeeded_recipients == 2
    assert all(c.status is CertificateStatus.GENERATED for c in certificates)
    assert all(c.file_path for c in certificates)
    session.close()
