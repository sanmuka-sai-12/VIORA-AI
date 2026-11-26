"""PostgreSQL repositories for concept extractions and authorization-scoped AI cache."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..contracts import ArtifactVersionRef, JobState, Page
from ..security import AccessError
from ..sources.public import DerivedExtractionRecord, build_scope_key
from ..workflow.public import SqlWorkflowRepository, WorkflowJobSubmit
from .models import (
    ConceptExtraction,
    ConceptExtractionCacheEntry,
    KnowledgePublication,
    KnowledgeReview,
)
from .schemas import (
    ConceptCandidate,
    ConceptEvidenceBinding,
    ConceptExtractionRecord,
    ConceptRelationshipCandidate,
    ExtractedKnowledgeGraph,
    KnowledgePublicationState,
    KnowledgeReviewRecord,
    ReviewDecision,
    StructuredLessonOutline,
)


def build_concept_input_fingerprint(
    *,
    derived: DerivedExtractionRecord,
    provider: str,
    model: str,
    prompt_version: str,
    schema_version: str,
    chunk_algorithm_version: str,
    chunk_config_digest: str | None = None,
) -> str:
    """Deterministic SHA-256 fingerprint of derived text, evidence bindings, and AI/chunk versions."""
    evidence_digest = hashlib.sha256(
        json.dumps(
            [
                {
                    "evidence_id": ev.evidence_id,
                    "version_id": str(ev.version_id),
                    "sha256": ev.source_sha256,
                    "page": ev.page_number,
                    "section": ev.section_id,
                    "block": ev.block_id,
                    "excerpt": ev.excerpt,
                }
                for ev in derived.document.evidence_references
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    payload = json.dumps(
        {
            "derived_extraction_id": str(derived.extraction_id),
            "version_id": str(derived.version_id),
            "source_sha256": derived.source_sha256,
            "input_format": derived.document.input_format,
            "normalized_text_sha256": hashlib.sha256(
                derived.document.normalized_text.encode("utf-8")
            ).hexdigest(),
            "evidence_digest": evidence_digest,
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "chunk_algorithm_version": chunk_algorithm_version,
            "chunk_config_digest": chunk_config_digest or "default",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_concept_cache_key(
    *,
    scope_key: str,
    input_fingerprint: str,
) -> str:
    payload = json.dumps(
        {"scope_key": scope_key, "input_fingerprint": input_fingerprint},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_knowledge_scope_key(
    *,
    offering_id: UUID | None,
    origin_kind: str,
    owner_user_id: UUID | None,
) -> str:
    """Reuse strict per-offering / per-student isolation scope for AI concept caching."""
    return build_scope_key(
        offering_id=offering_id,
        origin_kind=origin_kind,
        owner_user_id=owner_user_id,
    )


def _rebind_items(
    items: tuple[Any, ...],
    *,
    source_id: UUID,
    version_id: UUID,
    derived_extraction_id: UUID,
    source_sha256: str,
) -> tuple[Any, ...]:
    return tuple(
        item.model_copy(
            update={
                "evidence_bindings": tuple(
                    ConceptEvidenceBinding(
                        evidence_id=b.evidence_id,
                        source_id=source_id,
                        version_id=version_id,
                        source_sha256=source_sha256,
                        extraction_id=derived_extraction_id,
                        page_number=b.page_number,
                        section_id=b.section_id,
                        block_id=b.block_id,
                        verified=b.verified,
                        excerpt=b.excerpt,
                    )
                    for b in item.evidence_bindings
                )
            }
        )
        for item in items
    )


def rebind_cached_knowledge_graph(
    cached: ExtractedKnowledgeGraph,
    *,
    source_id: UUID,
    version_id: UUID,
    derived_extraction_id: UUID,
    source_sha256: str,
    title: str,
    created_at: datetime,
) -> ExtractedKnowledgeGraph:
    """Rebind cached concepts and evidence references strictly to target derived extraction IDs."""
    rebound_concepts = _rebind_items(
        cached.concepts,
        source_id=source_id,
        version_id=version_id,
        derived_extraction_id=derived_extraction_id,
        source_sha256=source_sha256,
    )
    rebound_rels = _rebind_items(
        cached.relationships,
        source_id=source_id,
        version_id=version_id,
        derived_extraction_id=derived_extraction_id,
        source_sha256=source_sha256,
    )
    assert all(isinstance(c, ConceptCandidate) for c in rebound_concepts)
    assert all(isinstance(r, ConceptRelationshipCandidate) for r in rebound_rels)
    return cached.model_copy(
        update={
            "source_id": source_id,
            "version_id": version_id,
            "derived_extraction_id": derived_extraction_id,
            "source_sha256": source_sha256,
            "title": title,
            "published_to_students": False,
            "concepts": rebound_concepts,
            "relationships": rebound_rels,
            "created_at": created_at,
        }
    )


def _record_dto(row: ConceptExtraction) -> ConceptExtractionRecord:
    return ConceptExtractionRecord(
        concept_extraction_id=row.concept_extraction_id,
        tenant_id=row.tenant_id,
        source_id=row.source_id,
        version_id=row.version_id,
        derived_extraction_id=row.derived_extraction_id,
        extraction_version=row.extraction_version,
        concept_version=row.concept_version,
        job_id=row.job_id,
        provider=row.provider,
        model=row.model,
        prompt_version=row.prompt_version,
        schema_version=row.schema_version,
        chunk_algorithm_version=row.chunk_algorithm_version,
        input_fingerprint=row.input_fingerprint,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        total_tokens=row.total_tokens,
        inference_requests=row.inference_requests,
        chunking_ms=row.chunking_ms,
        inference_ms=row.inference_ms,
        validation_ms=row.validation_ms,
        cache_hit=row.cache_hit,
        published_to_students=row.published_to_students,
        knowledge=ExtractedKnowledgeGraph.model_validate(row.knowledge),
        created_at=row.created_at,
    )


class SqlConceptExtractionRepository:
    """Tenant-scoped append-only store and workflow job enqueueing for concept extractions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_latest_for_version(
        self, tenant_id: UUID, version_id: UUID
    ) -> ConceptExtractionRecord | None:
        row = self.session.scalar(
            select(ConceptExtraction)
            .where(
                ConceptExtraction.tenant_id == tenant_id,
                ConceptExtraction.version_id == version_id,
            )
            .order_by(ConceptExtraction.concept_version.desc())
            .limit(1)
        )
        return _record_dto(row) if row is not None else None

    def get_by_id(
        self, tenant_id: UUID, concept_extraction_id: UUID
    ) -> ConceptExtractionRecord | None:
        row = self.session.scalar(
            select(ConceptExtraction).where(
                ConceptExtraction.tenant_id == tenant_id,
                ConceptExtraction.concept_extraction_id == concept_extraction_id,
            )
        )
        return _record_dto(row) if row is not None else None

    def get_for_job(
        self, tenant_id: UUID, job_id: UUID
    ) -> ConceptExtractionRecord | None:
        row = self.session.scalar(
            select(ConceptExtraction).where(
                ConceptExtraction.tenant_id == tenant_id,
                ConceptExtraction.job_id == job_id,
            )
        )
        return _record_dto(row) if row is not None else None

    def list_for_version(
        self, tenant_id: UUID, version_id: UUID, *, limit: int = 20
    ) -> Page[ConceptExtractionRecord]:
        rows = self.session.scalars(
            select(ConceptExtraction)
            .where(
                ConceptExtraction.tenant_id == tenant_id,
                ConceptExtraction.version_id == version_id,
            )
            .order_by(ConceptExtraction.concept_version.desc())
            .limit(limit + 1)
        ).all()
        has_more = len(rows) > limit
        items = tuple(_record_dto(r) for r in rows[:limit])
        next_cursor = str(items[-1].concept_extraction_id) if has_more else None
        return Page(items=items, next_cursor=next_cursor)

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
    ) -> tuple[UUID, JobState, bool]:
        _ = derived_extraction_id
        wf = SqlWorkflowRepository(self.session)
        job, replay = wf.enqueue(
            WorkflowJobSubmit(
                tenant_id=tenant_id,
                offering_id=offering_id,
                source_id=source_id,
                version_id=version_id,
                submitted_by_user_id=submitted_by_user_id,
                operation_kind="concept_extract",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                input_artifact=input_artifact,
            ),
            now=now,
        )
        return job.job_id, job.state, replay

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
    ) -> ConceptExtractionRecord:
        if job_id is not None:
            existing_job = self.get_for_job(tenant_id, job_id)
            if existing_job is not None:
                return existing_job

        prev = self.session.scalar(
            select(ConceptExtraction)
            .where(
                ConceptExtraction.tenant_id == tenant_id,
                ConceptExtraction.version_id == version_id,
            )
            .order_by(ConceptExtraction.concept_version.desc())
            .limit(1)
        )
        next_ver = (prev.concept_version + 1) if prev is not None else 1
        row = ConceptExtraction(
            tenant_id=tenant_id,
            concept_extraction_id=uuid4(),
            source_id=source_id,
            version_id=version_id,
            derived_extraction_id=derived_extraction_id,
            extraction_version=extraction_version,
            concept_version=next_ver,
            job_id=job_id,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            schema_version=schema_version,
            chunk_algorithm_version=chunk_algorithm_version,
            input_fingerprint=input_fingerprint,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            inference_requests=inference_requests,
            chunking_ms=chunking_ms,
            inference_ms=inference_ms,
            validation_ms=validation_ms,
            cache_hit=cache_hit,
            cache_key=cache_key,
            published_to_students=False,
            source_sha256=knowledge.source_sha256,
            knowledge=knowledge.model_copy(
                update={"published_to_students": False}
            ).model_dump(mode="json"),
            created_at=now,
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            sqlstate = getattr(exc.orig, "sqlstate", None)
            if sqlstate == "23505" and job_id is not None:
                existing = self.get_for_job(tenant_id, job_id)
                if existing is not None:
                    return existing
            if sqlstate == "23505":
                raise AccessError(
                    409,
                    "concurrent_concept_extraction",
                    "Concurrent concept extraction conflict",
                ) from None
            raise
        return _record_dto(row)

    @staticmethod
    def _review_dto(row: KnowledgeReview) -> KnowledgeReviewRecord:
        return KnowledgeReviewRecord(
            review_id=row.review_id,
            tenant_id=row.tenant_id,
            source_id=row.source_id,
            version_id=row.version_id,
            concept_extraction_id=row.concept_extraction_id,
            review_version=row.review_version,
            decision=row.decision,  # type: ignore[arg-type]
            review_notes=row.review_notes,
            approved_concept_ids=tuple(row.approved_concept_ids),
            rejected_concept_ids=tuple(row.rejected_concept_ids),
            reviewed_by_user_id=row.reviewed_by_user_id,
            created_at=row.created_at,
        )

    @staticmethod
    def _publication_dto(row: KnowledgePublication) -> KnowledgePublicationState:
        outline = StructuredLessonOutline.model_validate(row.outline)
        return KnowledgePublicationState(
            tenant_id=row.tenant_id,
            source_id=row.source_id,
            version_id=row.version_id,
            concept_extraction_id=row.concept_extraction_id,
            concept_version=row.concept_version,
            source_sha256=row.source_sha256,
            review_status=row.review_status,  # type: ignore[arg-type]
            latest_review_id=row.latest_review_id,
            approved_review_id=row.approved_review_id,
            teacher_approved=row.review_status == "approved"
            and row.approved_review_id is not None,
            published_to_students=row.published_to_students,
            published_by_user_id=row.published_by_user_id,
            published_at=row.published_at,
            updated_at=row.updated_at,
            learning_request_only=outline.learning_request_only,
            all_evidence_verified=outline.all_evidence_verified,
            outline=outline,
        )

    def get_publication(
        self, tenant_id: UUID, version_id: UUID
    ) -> KnowledgePublicationState | None:
        row = self.session.scalar(
            select(KnowledgePublication).where(
                KnowledgePublication.tenant_id == tenant_id,
                KnowledgePublication.version_id == version_id,
            )
        )
        return self._publication_dto(row) if row is not None else None

    def list_reviews(
        self, tenant_id: UUID, version_id: UUID, *, limit: int = 20
    ) -> Page[KnowledgeReviewRecord]:
        rows = self.session.scalars(
            select(KnowledgeReview)
            .where(
                KnowledgeReview.tenant_id == tenant_id,
                KnowledgeReview.version_id == version_id,
            )
            .order_by(KnowledgeReview.created_at.desc(), KnowledgeReview.review_version.desc())
            .limit(limit + 1)
        ).all()
        has_more = len(rows) > limit
        items = tuple(self._review_dto(r) for r in rows[:limit])
        next_cursor = str(items[-1].review_id) if has_more else None
        return Page(items=items, next_cursor=next_cursor)

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
    ) -> tuple[KnowledgeReviewRecord, KnowledgePublicationState]:
        last_rev = self.session.scalar(
            select(KnowledgeReview)
            .where(
                KnowledgeReview.tenant_id == tenant_id,
                KnowledgeReview.concept_extraction_id == extraction.concept_extraction_id,
            )
            .order_by(KnowledgeReview.review_version.desc())
            .limit(1)
        )
        review_id = uuid4()
        rev_row = KnowledgeReview(
            tenant_id=tenant_id,
            review_id=review_id,
            source_id=source_id,
            version_id=version_id,
            concept_extraction_id=extraction.concept_extraction_id,
            review_version=(last_rev.review_version + 1) if last_rev else 1,
            decision=decision,
            review_notes=review_notes.strip() if review_notes and review_notes.strip() else None,
            approved_concept_ids=list(approved_concept_ids),
            rejected_concept_ids=list(rejected_concept_ids),
            reviewed_by_user_id=reviewed_by_user_id,
            created_at=now,
        )
        self.session.add(rev_row)

        existing_pub = self.session.scalar(
            select(KnowledgePublication)
            .where(
                KnowledgePublication.tenant_id == tenant_id,
                KnowledgePublication.version_id == version_id,
            )
            .with_for_update()
        )
        approved_id = review_id if decision == "approved" else None
        outline_json = outline.model_dump(mode="json")
        if existing_pub is None:
            pub_row = KnowledgePublication(
                tenant_id=tenant_id,
                version_id=version_id,
                source_id=source_id,
                concept_extraction_id=extraction.concept_extraction_id,
                concept_version=extraction.concept_version,
                source_sha256=extraction.knowledge.source_sha256,
                review_status=decision,
                latest_review_id=review_id,
                approved_review_id=approved_id,
                published_to_students=False,
                published_by_user_id=None,
                published_at=None,
                outline=outline_json,
                updated_at=now,
            )
            self.session.add(pub_row)
        else:
            prev_extraction_id = existing_pub.concept_extraction_id
            existing_pub.concept_extraction_id = extraction.concept_extraction_id
            existing_pub.concept_version = extraction.concept_version
            existing_pub.source_sha256 = extraction.knowledge.source_sha256
            existing_pub.review_status = decision
            existing_pub.latest_review_id = review_id
            existing_pub.approved_review_id = approved_id
            # Changing review decision to non-approved or switching extraction unpublishes immediately
            if decision != "approved" or prev_extraction_id != extraction.concept_extraction_id:
                existing_pub.published_to_students = False
                existing_pub.published_by_user_id = None
                existing_pub.published_at = None
            existing_pub.outline = outline_json
            existing_pub.updated_at = now
            pub_row = existing_pub

        self.session.flush()
        return self._review_dto(rev_row), self._publication_dto(pub_row)

    def set_publication(
        self,
        *,
        tenant_id: UUID,
        version_id: UUID,
        concept_extraction_id: UUID,
        publish: bool,
        actor_id: UUID,
        now: datetime,
    ) -> KnowledgePublicationState:
        pub_row = self.session.scalar(
            select(KnowledgePublication)
            .where(
                KnowledgePublication.tenant_id == tenant_id,
                KnowledgePublication.version_id == version_id,
            )
            .with_for_update()
        )
        if pub_row is None or pub_row.concept_extraction_id != concept_extraction_id:
            raise AccessError(
                409,
                "teacher_approval_required",
                "Target concept extraction version must be reviewed and approved before publishing",
            )
        if publish and (
            pub_row.review_status != "approved" or pub_row.approved_review_id is None
        ):
            raise AccessError(
                409,
                "teacher_approval_required",
                "Educational knowledge requires explicit teacher approval before publication",
            )
        pub_row.published_to_students = publish
        pub_row.published_by_user_id = actor_id if publish else None
        pub_row.published_at = now if publish else None
        pub_row.updated_at = now
        self.session.flush()
        return self._publication_dto(pub_row)


class SqlConceptExtractionCache:
    """Tenant-bound RLS-enforced cache for verified educational concept extractions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def lookup(
        self,
        *,
        tenant_id: UUID,
        cache_key: str,
        scope_key: str,
        input_fingerprint: str,
        now: datetime,
    ) -> tuple[ExtractedKnowledgeGraph, int, int, int, int] | None:
        row = self.session.scalar(
            select(ConceptExtractionCacheEntry).where(
                ConceptExtractionCacheEntry.tenant_id == tenant_id,
                ConceptExtractionCacheEntry.cache_key == cache_key,
                ConceptExtractionCacheEntry.scope_key == scope_key,
                ConceptExtractionCacheEntry.input_fingerprint == input_fingerprint,
                ConceptExtractionCacheEntry.invalidated_at.is_(None),
                ConceptExtractionCacheEntry.expires_at > now,
            )
        )
        if row is None:
            return None
        self.session.execute(
            update(ConceptExtractionCacheEntry)
            .where(
                ConceptExtractionCacheEntry.tenant_id == tenant_id,
                ConceptExtractionCacheEntry.cache_key == cache_key,
            )
            .values(
                hit_count=ConceptExtractionCacheEntry.hit_count + 1,
                last_hit_at=now,
            )
        )
        return (
            ExtractedKnowledgeGraph.model_validate(row.knowledge),
            row.input_tokens,
            row.output_tokens,
            row.total_tokens,
            row.inference_requests,
        )

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
    ) -> None:
        expires_at = now + timedelta(seconds=ttl_seconds)
        payload = knowledge.model_copy(update={"published_to_students": False}).model_dump(
            mode="json"
        )
        stmt = (
            pg_insert(ConceptExtractionCacheEntry)
            .values(
                tenant_id=tenant_id,
                cache_key=cache_key,
                scope_key=scope_key,
                input_fingerprint=input_fingerprint,
                provider=provider,
                model=model,
                prompt_version=prompt_version,
                schema_version=schema_version,
                chunk_algorithm_version=chunk_algorithm_version,
                source_id=source_id,
                version_id=version_id,
                derived_extraction_id=derived_extraction_id,
                concept_extraction_id=concept_extraction_id,
                knowledge=payload,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                inference_requests=inference_requests,
                hit_count=0,
                last_hit_at=None,
                expires_at=expires_at,
                invalidated_at=None,
                created_at=now,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "cache_key"],
                set_={
                    "concept_extraction_id": concept_extraction_id,
                    "derived_extraction_id": derived_extraction_id,
                    "source_id": source_id,
                    "version_id": version_id,
                    "knowledge": payload,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens,
                    "inference_requests": inference_requests,
                    "expires_at": expires_at,
                    "invalidated_at": None,
                },
            )
        )
        self.session.execute(stmt)

    def invalidate_source(
        self, *, tenant_id: UUID, source_id: UUID, now: datetime
    ) -> int:
        res = self.session.execute(
            update(ConceptExtractionCacheEntry)
            .where(
                ConceptExtractionCacheEntry.tenant_id == tenant_id,
                ConceptExtractionCacheEntry.source_id == source_id,
                ConceptExtractionCacheEntry.invalidated_at.is_(None),
            )
            .values(invalidated_at=now)
        )
        return int(res.rowcount or 0)
