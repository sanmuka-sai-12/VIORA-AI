"""Ports for source persistence, authorization directory lookup, and extraction."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from ..contracts import ArtifactVersionRef, Page
from .document_contracts import (
    DerivedExtractionRecord,
    ExtractedDocument,
    SourceDocumentFormat,
)
from .schemas import (
    OfferingSourcePolicyInfo,
    SourceAccessInfo,
    SourceIngestionLimits,
    SourceInputFormat,
    SourceProcessingStatus,
    SourceRegistrationResult,
    SourceSummary,
    SourceVersionDetail,
    SourceVersionSummary,
)


class ConcurrentUniqueConflict(Exception):
    """Signals a 23505 unique conflict so the service can resolve concurrent idempotency outside the rolled-back tx."""


class Sources(Protocol):
    def find(self, tenant_id: UUID, source_id: UUID) -> SourceSummary | None: ...


SourceRepository = Sources


class SourceDirectory(Protocol):
    """Internal read contract consumed by centralized authorization policy."""

    def source_access_info(
        self, tenant_id: UUID, source_id: UUID
    ) -> SourceAccessInfo | None: ...

    def offering_source_policy(
        self, tenant_id: UUID, offering_id: UUID
    ) -> OfferingSourcePolicyInfo: ...


class SourceIngestionRepository(Protocol):
    """Transactional persistence contract executed inside an authorized DB scope."""

    def find(self, tenant_id: UUID, source_id: UUID) -> SourceSummary | None: ...

    def get_source_access(
        self, tenant_id: UUID, source_id: UUID, *, lock: bool = False
    ) -> SourceAccessInfo | None: ...

    def get_version(
        self, tenant_id: UUID, source_id: UUID, version_id: UUID
    ) -> SourceVersionDetail | None: ...

    def get_latest_derived_extraction(
        self, tenant_id: UUID, source_id: UUID, version_id: UUID
    ) -> DerivedExtractionRecord | None: ...

    def list_derived_extractions(
        self,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        *,
        limit: int = 25,
    ) -> Page[DerivedExtractionRecord]: ...

    def list_versions(
        self,
        tenant_id: UUID,
        source_id: UUID,
        *,
        cursor: int | None,
        limit: int,
    ) -> Page[SourceVersionSummary]: ...

    def list_offering_sources(
        self,
        tenant_id: UUID,
        offering_id: UUID,
        *,
        actor_id: UUID,
        is_assigned_teacher: bool,
        is_enrolled_student: bool,
        teachers_can_view_student_submissions: bool,
        origin_kind: str | None,
        lifecycle_state: str | None,
        cursor: UUID | None,
        limit: int,
    ) -> Page[SourceSummary]: ...

    def get_offering_policy(
        self, tenant_id: UUID, offering_id: UUID
    ) -> OfferingSourcePolicyInfo: ...

    def upsert_offering_policy(
        self,
        tenant_id: UUID,
        offering_id: UUID,
        *,
        actor_id: UUID,
        student_submissions_enabled: bool | None,
        teachers_can_view_student_submissions: bool | None,
    ) -> OfferingSourcePolicyInfo: ...

    def lookup_idempotency(
        self,
        *,
        tenant_id: UUID,
        offering_id: UUID,
        actor_id: UUID,
        target_scope_id: UUID,
        operation_kind: str,
        idempotency_key: str,
        request_digest: str,
    ) -> SourceRegistrationResult | None: ...

    def has_active_dedupe_key(
        self, *, tenant_id: UUID, active_dedupe_key: str
    ) -> bool: ...

    def has_version_digest(
        self, *, tenant_id: UUID, source_id: UUID, content_sha256: str
    ) -> bool: ...

    def has_version_id(self, *, tenant_id: UUID, version_id: UUID) -> bool: ...

    def create_source_and_version(
        self,
        *,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        offering_id: UUID,
        actor_id: UUID,
        title: str,
        origin_kind: str,
        visibility: str,
        input_format: SourceInputFormat,
        original_filename: str | None,
        content_sha256: str,
        size_bytes: int,
        active_dedupe_key: str,
        storage_ref: ArtifactVersionRef,
        processing_status: SourceProcessingStatus,
        extractor_id: str,
        contract_version: str,
        extraction_ms: float,
        char_count: int,
        page_count: int,
        warning_count: int,
        processing_metadata: dict[str, str | int | float | bool],
        extracted_document: ExtractedDocument | None,
        now: datetime,
        idempotency_key: str | None,
        request_digest: str | None,
        enqueue_extraction_job: bool = False,
        job_max_attempts: int = 3,
    ) -> tuple[SourceSummary, SourceVersionSummary, UUID | None]: ...

    def append_version(
        self,
        *,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        offering_id: UUID,
        actor_id: UUID,
        input_format: SourceInputFormat,
        original_filename: str | None,
        content_sha256: str,
        size_bytes: int,
        active_dedupe_key: str | None,
        storage_ref: ArtifactVersionRef,
        processing_status: SourceProcessingStatus,
        extractor_id: str,
        contract_version: str,
        extraction_ms: float,
        char_count: int,
        page_count: int,
        warning_count: int,
        processing_metadata: dict[str, str | int | float | bool],
        extracted_document: ExtractedDocument | None,
        now: datetime,
        idempotency_key: str | None,
        request_digest: str | None,
        enqueue_extraction_job: bool = False,
        job_max_attempts: int = 3,
    ) -> tuple[SourceSummary, SourceVersionSummary, UUID | None]: ...

    def update_source_metadata(
        self,
        *,
        tenant_id: UUID,
        source_id: UUID,
        title: str | None,
        visibility: str | None,
        lifecycle_state: str | None,
        now: datetime,
    ) -> SourceSummary | None: ...


class DocumentExtractor(Protocol):
    """Extractor-independent interface for adaptive multi-format normalization & triage."""

    def extract(
        self,
        *,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        source_version: int,
        source_sha256: str,
        input_format: SourceDocumentFormat,
        title: str,
        raw_bytes: bytes,
        limits: SourceIngestionLimits,
    ) -> ExtractedDocument: ...
