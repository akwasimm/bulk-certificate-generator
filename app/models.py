"""SQLAlchemy models."""
import enum
import uuid

from sqlalchemy import Column, Date, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .database import Base
from .utils import utcnow


def _enum(enum_cls) -> Enum:
    # Stored as VARCHAR + CHECK constraint so SQLite and PostgreSQL read alike.
    return Enum(
        enum_cls,
        native_enum=False,
        values_callable=lambda cls: [member.value for member in cls],
    )


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    FAILED = "failed"


class CertificateStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATED = "generated"
    FAILED = "failed"


class CertificateJob(Base):
    __tablename__ = "certificate_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    status = Column(_enum(JobStatus), default=JobStatus.QUEUED, nullable=False, index=True)

    course_name = Column(String(200), nullable=False)
    issued_by = Column(String(160), nullable=True)
    issued_on = Column(Date, nullable=False)

    total_recipients = Column(Integer, nullable=False, default=0)
    processed_recipients = Column(Integer, nullable=False, default=0)
    succeeded_recipients = Column(Integer, nullable=False, default=0)
    failed_recipients = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    certificates = relationship("Certificate", back_populates="job", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<CertificateJob {self.id} {self.status.value} {self.processed_recipients}/{self.total_recipients}>"


class Certificate(Base):
    __tablename__ = "certificates"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(
        String(36),
        ForeignKey("certificate_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recipient_index = Column(Integer, nullable=False)

    full_name = Column(String(200), nullable=True)
    email = Column(String(320), nullable=True)

    status = Column(_enum(CertificateStatus), default=CertificateStatus.PENDING, nullable=False)
    error = Column(Text, nullable=True)
    file_path = Column(String(512), nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    job = relationship("CertificateJob", back_populates="certificates")

    def __repr__(self) -> str:
        return f"<Certificate {self.id} {self.full_name!r} {self.status.value}>"
