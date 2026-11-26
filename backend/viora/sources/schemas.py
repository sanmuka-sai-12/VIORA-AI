"""Domain contracts for versioned educational source ingestion and retrieval."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints

from ..contracts import ArtifactVersionRef, Contract, Digest, JobState
from .document_contracts import ExtractedDocument, ExtractionQualityReport

SourceInputFormat = Literal[
    "plain_text",
    "typed_notes",
    "topic_description",
    "digital_pdf",
    "scanned_pdf",
    "png",
    "jpeg",
]

TextInputFormat = Literal[
    "plain_text",
    "typed_notes",
    "topic_description",
]

SourceOriginKind = Literal["teacher_resource", "student_submission"]

SourceVisibility = Literal[
    "teacher_only",
    "offering_published",
    "owner_private",
    "teacher_review",
    "restricted",
]

TeacherMutableVisibility = Literal["teacher_only", "offering_published"]
StudentMutableVisibility = Literal["owner_private", "teacher_review"]

SourceLifecycleState = Literal["active", "archived"]
SourceProcessingStatus = Literal[
    "pending",
    "extracted",
    "partial_extraction",
    "ocr_required",
    "unreadable",
    "failed",
    "cancelled",
]

SourceTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
]

IdempotencyKeyStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]

HARD_MAX_SOURCE_BYTES = 50 * 1024 * 1024  # 50 MiB hard ceiling
HARD_MAX_PDF_PAGES = 500
HARD_MAX_TEXT_CHARS = 1_000_000
HARD_MIN_TEXT_CHARS = 1

DEFAULT_MAX_SOURCE_BYTES = 10 * 1024 * 1024  # 10 MiB default
DEFAULT_MAX_PDF_PAGES = 100
DEFAULT_MAX_TEXT_CHARS = 200_000
DEFAULT_MIN_TEXT_CHARS = 3


class SourceIngestionLimits(Contract):
    """Hard-clamped ingestion limits preventing memory/CPU exhaustion."""

    max_bytes: int = Field(default=DEFAULT_MAX_SOURCE_BYTES, ge=1024, le=HARD_MAX_SOURCE_BYTES)
    max_pdf_pages: int = Field(default=DEFAULT_MAX_PDF_PAGES, ge=1, le=HARD_MAX_PDF_PAGES)
    max_text_chars: int = Field(default=DEFAULT_MAX_TEXT_CHARS, ge=10, le=HARD_MAX_TEXT_CHARS)
    min_text_chars: int = Field(default=DEFAULT_MIN_TEXT_CHARS, ge=HARD_MIN_TEXT_CHARS, le=100)


def clamp_source_limits(
    *,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    max_pdf_pages: int = DEFAULT_MAX_PDF_PAGES,
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
    min_text_chars: int = DEFAULT_MIN_TEXT_CHARS,
) -> SourceIngestionLimits:
    """Clamp configured source limits to strict safety ceilings."""
    return SourceIngestionLimits(
        max_bytes=max(1024, min(max_bytes, HARD_MAX_SOURCE_BYTES)),
        max_pdf_pages=max(1, min(max_pdf_pages, HARD_MAX_PDF_PAGES)),
        max_text_chars=max(10, min(max_text_chars, HARD_MAX_TEXT_CHARS)),
        min_text_chars=max(HARD_MIN_TEXT_CHARS, min(min_text_chars, 100)),
    )


class SourceAccessInfo(Contract):
    """Lightweight authorization metadata evaluated inside policy transactions."""

    tenant_id: UUID
    source_id: UUID
    offering_id: UUID | None
    creator_user_id: UUID | None
    owner_user_id: UUID | None
    origin_kind: str
    visibility: str
    lifecycle_state: str
    title: str = ""
    latest_version_number: int = 0


class OfferingSourcePolicyInfo(Contract):
    tenant_id: UUID
    offering_id: UUID
    student_submissions_enabled: bool = True
    teachers_can_view_student_submissions: bool = True
    updated_by_user_id: UUID | None = None
    updated_at: datetime | None = None


class OfferingSourcePolicyUpdateRequest(Contract):
    student_submissions_enabled: bool | None = None
    teachers_can_view_student_submissions: bool | None = None


class SourceTextRegisterRequest(Contract):
    title: SourceTitle
    input_format: TextInputFormat
    content: str = Field(min_length=1, max_length=HARD_MAX_TEXT_CHARS)
    origin_kind: SourceOriginKind = "teacher_resource"
    visibility: SourceVisibility | None = None
    original_filename: str | None = Field(default=None, max_length=255)
    idempotency_key: IdempotencyKeyStr | None = None


class SourceVersionTextRequest(Contract):
    input_format: TextInputFormat
    content: str = Field(min_length=1, max_length=HARD_MAX_TEXT_CHARS)
    original_filename: str | None = Field(default=None, max_length=255)
    idempotency_key: IdempotencyKeyStr | None = None


class SourceMetadataUpdateRequest(Contract):
    title: SourceTitle | None = None
    visibility: SourceVisibility | None = None
    lifecycle_state: SourceLifecycleState | None = None


class SourceSummary(Contract):
    """Backward-compatible source metadata view."""

    source_id: UUID
    title: str
    tenant_id: UUID | None = None
    offering_id: UUID | None = None
    creator_user_id: UUID | None = None
    owner_user_id: UUID | None = None
    origin_kind: str | None = None
    visibility: str | None = None
    lifecycle_state: str | None = None
    latest_version_number: int = 0
    latest_version_id: UUID | None = None
    latest_input_format: str | None = None
    latest_digest: Digest | None = None
    latest_size_bytes: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SourceVersionSummary(Contract):
    tenant_id: UUID
    source_id: UUID
    version_id: UUID
    version_number: int = Field(ge=1)
    created_by_user_id: UUID
    input_format: SourceInputFormat
    original_filename: str | None = None
    content_sha256: Digest
    size_bytes: int = Field(ge=1)
    storage_ref: ArtifactVersionRef
    processing_status: SourceProcessingStatus
    extractor_id: str
    contract_version: str
    extraction_ms: float = Field(ge=0.0)
    char_count: int = Field(ge=0)
    page_count: int = Field(ge=1)
    warning_count: int = Field(ge=0)
    processing_metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
    latest_extraction_id: UUID | None = None
    latest_extraction_version: int = Field(default=0, ge=0)
    latest_job_id: UUID | None = None
    latest_job_state: JobState | None = None
    created_at: datetime


class SourceVersionDetail(SourceVersionSummary):
    extracted_document: ExtractedDocument | None = None


class SourceRegistrationResult(Contract):
    source: SourceSummary
    version: SourceVersionSummary
    quality: ExtractionQualityReport | None = None
    job_id: UUID | None = None
    job_state: JobState | None = None
    idempotent_replay: bool = False
    orphans_cleaned: int = 0
    registration_ms: float = Field(ge=0.0)
