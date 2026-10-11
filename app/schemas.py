"""Pydantic request and response schemas."""
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from .config import get_settings
from .models import CertificateStatus, JobStatus


class RecipientInput(BaseModel):
    # Lenient on purpose: a bad value is flagged on its own certificate row
    # instead of failing the whole request.
    full_name: str | None = None
    email: str | None = None


class CertificateJobCreate(BaseModel):
    course_name: str = Field(..., min_length=1, max_length=200)
    issued_by: str | None = Field(default=None, max_length=160)
    issued_on: date | None = None
    recipients: list[RecipientInput] = Field(..., min_length=1)

    @field_validator("course_name", "issued_by")
    @classmethod
    def _strip_and_reject_blank(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def _enforce_recipient_limit(self) -> "CertificateJobCreate":
        limit = get_settings().max_recipients_per_job
        if len(self.recipients) > limit:
            raise ValueError(
                f"A single job may contain at most {limit} recipients (got {len(self.recipients)})."
            )
        return self


class CertificateJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: JobStatus
    course_name: str
    issued_by: str | None
    issued_on: date
    total_recipients: int
    processed_recipients: int
    succeeded_recipients: int
    failed_recipients: int
    created_at: datetime
    completed_at: datetime | None

    @computed_field
    @property
    def progress_percent(self) -> float:
        if self.total_recipients == 0:
            return 0.0
        return round(self.processed_recipients / self.total_recipients * 100, 1)

    @computed_field
    @property
    def certificates_url(self) -> str:
        return f"/api/v1/certificate-jobs/{self.id}/certificates"

    @computed_field
    @property
    def download_url(self) -> str:
        return f"/api/v1/certificate-jobs/{self.id}/download"


class CertificateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    recipient_index: int
    full_name: str | None
    email: str | None
    status: CertificateStatus
    error: str | None
    generated_at: datetime | None

    @computed_field
    @property
    def download_url(self) -> str | None:
        if self.status is CertificateStatus.GENERATED:
            return f"/api/v1/certificates/{self.id}/download"
        return None
