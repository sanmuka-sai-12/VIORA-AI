"""Domain protocols for educational concept extraction persistence and scoped caching."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from ..contracts import ArtifactVersionRef, JobState, Page
from .schemas import (
    ConceptExtractionRecord,
    ExtractedKnowledgeGraph,
    KnowledgePublicationState,
    KnowledgeReviewRecord,
    ReviewDecision,
    StructuredLessonOutline,
)


class ConceptExtractionRepository(Protocol):
    def get_latest_for_version(
        self, tenant_id: UUID, version_id: UUID
    ) -> ConceptExtractionRecord | None: ...

    def get_by_id(
        self, tenant_id: UUID, concept_extraction_id: UUID
    ) -> ConceptExtractionRecord | None: ...

    def list_for_version(
        self, tenant_id: UUID, version_id: UUID, *, limit: int = 20
    ) -> Page[ConceptExtractionRecord]: ...

    def get_publication(
        self, tenant_id: UUID, version_id: UUID
    ) -> KnowledgePublicationState | None: ...

    def record_review(
        self,
        *,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        extraction: ConceptExtractionRecord,
        outline: StructuredLessonOutline,
        decision: ReviewDecision,
        review_notes: str | None,
        approved_concept_ids: tuple[str, ...],
        rejected_concept_ids: tuple[str, ...],
        reviewed_by_user_id: UUID,
        now: datetime,
    ) -> tuple[KnowledgeReviewRecord, KnowledgePublicationState]: ...

    def get_approved_review(
        self, publication: KnowledgePublicationState
    ) -> KnowledgeReviewRecord | None: ...

    def list_reviews(
        self, tenant_id: UUID, version_id: UUID, *, limit: int = 20
    ) -> Page[KnowledgeReviewRecord]: ...

    def set_publication(
        self,
        *,
        tenant_id: UUID,
        version_id: UUID,
        concept_extraction_id: UUID,
        publish: bool,
        actor_id: UUID,
        now: datetime,
    ) -> KnowledgePublicationState: ...

    def enqueue_concept_job(
        self,
        *,
        tenant_id: UUID,
        offering_id: UUID | None,
        source_id: UUID,
        version_id: UUID,
        derived_extraction_id: UUID,
        submitted_by_user_id: UUID,
        input_artifact: ArtifactVersionRef,
        idempotency_key: str | None,
        request_digest: str,
        now: datetime,
    ) -> tuple[UUID, JobState, bool]: ...

    def append_concept_extraction(
        self,
        *,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        derived_extraction_id: UUID,
        extraction_version: int,
        job_id: UUID | None,
        provider: str,
        model: str,
        prompt_version: str,
        schema_version: str,
        chunk_algorithm_version: str,
        input_fingerprint: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        inference_requests: int,
        chunking_ms: float,
        inference_ms: float,
        validation_ms: float,
        cache_hit: bool,
        cache_key: str | None,
        knowledge: ExtractedKnowledgeGraph,
        now: datetime,
    ) -> ConceptExtractionRecord: ...


class ConceptCacheRepository(Protocol):
    def lookup(
        self,
        *,
        tenant_id: UUID,
        cache_key: str,
        scope_key: str,
        input_fingerprint: str,
        now: datetime,
    ) -> ExtractedKnowledgeGraph | None: ...

    def put(
        self,
        *,
        tenant_id: UUID,
        cache_key: str,
        scope_key: str,
        input_fingerprint: str,
        provider: str,
        model: str,
        prompt_version: str,
        schema_version: str,
        chunk_algorithm_version: str,
        source_id: UUID,
        version_id: UUID,
        derived_extraction_id: UUID,
        concept_extraction_id: UUID,
        knowledge: ExtractedKnowledgeGraph,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        inference_requests: int,
        ttl_seconds: int,
        now: datetime,
    ) -> None: ...
