"""Authorized service and router workflows for educational concept extraction."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from ..contracts import Page, ResourceRef
from ..security import AccessError, TransactionalAccess
from ..sources.public import SqlSourceRepository
from ..workflow.public import ConcurrentJobConflict
from .curriculum import generate_lesson_outline
from .ports import ConceptExtractionRepository
from .schemas import (
    ConceptExtractionRecord,
    ConceptExtractionTriggerResult,
    KnowledgePublicationState,
    KnowledgePublishRequest,
    KnowledgeReviewRecord,
    KnowledgeReviewRequest,
    PublishedLessonPackage,
)


def _source_ref(tenant_id: UUID, source_id: UUID) -> ResourceRef:
    return ResourceRef(tenant_id=tenant_id, kind="source", resource_id=source_id)


def _concept_request_digest(
    *,
    source_id: UUID,
    version_id: UUID,
    derived_extraction_id: UUID,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "op": "concept_extract",
                "source_id": str(source_id),
                "version_id": str(version_id),
                "derived_extraction_id": str(derived_extraction_id),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class ConceptKnowledgeService:
    """Orchestrates authorized triggering and retrieval of versioned concept graphs."""

    def __init__(
        self,
        repo_factory: Callable[[Session], ConceptExtractionRepository],
        source_repo_factory: Callable[[Session], SqlSourceRepository] = SqlSourceRepository,
    ) -> None:
        self.repo_factory = repo_factory
        self.source_repo_factory = source_repo_factory

    def trigger_concept_extraction(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        *,
        idempotency_key: str | None = None,
    ) -> ConceptExtractionTriggerResult:
        clean_key = (
            idempotency_key.strip()
            if idempotency_key and idempotency_key.strip()
            else None
        )
        for attempt in range(2):
            try:
                with access.transaction(
                    bearer, "source.extract", _source_ref(tenant_id, source_id)
                ) as scope:
                    src_repo = self.source_repo_factory(scope.session)
                    src_info = src_repo.get_source_access(tenant_id, source_id)
                    ver = src_repo.get_version(tenant_id, source_id, version_id)
                    derived = src_repo.get_latest_derived_extraction(
                        tenant_id, source_id, version_id
                    )
                    if src_info is None or ver is None:
                        raise AccessError(
                            404, "version_not_found", "Source version not found"
                        )
                    if src_info.lifecycle_state != "active":
                        raise AccessError(
                            409,
                            "source_archived",
                            "Archived sources cannot be analyzed for concepts",
                        )
                    if derived is None:
                        raise AccessError(
                            409,
                            "extraction_required",
                            "Derived document extraction must complete before concept extraction",
                        )
                    repo = self.repo_factory(scope.session)
                    digest = _concept_request_digest(
                        source_id=source_id,
                        version_id=version_id,
                        derived_extraction_id=derived.extraction_id,
                    )
                    now = datetime.now(UTC)
                    job_id, job_state, replay = repo.enqueue_concept_job(
                        tenant_id=tenant_id,
                        offering_id=src_info.offering_id,
                        source_id=source_id,
                        version_id=version_id,
                        derived_extraction_id=derived.extraction_id,
                        submitted_by_user_id=scope.context.actor_id,
                        input_artifact=ver.storage_ref,
                        idempotency_key=clean_key,
                        request_digest=digest,
                        now=now,
                    )
                    existing = repo.get_latest_for_version(tenant_id, version_id)
                    if not replay:
                        scope.audit.change(
                            scope.context,
                            "source.concepts.queued",
                            _source_ref(tenant_id, source_id),
                        )
                    return ConceptExtractionTriggerResult(
                        job_id=job_id,
                        job_state=job_state,
                        derived_extraction_id=derived.extraction_id,
                        concept_extraction_id=(
                            existing.concept_extraction_id if existing else None
                        ),
                        idempotent_replay=replay,
                    )
            except ConcurrentJobConflict:
                if attempt == 0:
                    continue
                raise AccessError(
                    409,
                    "concurrent_concept_job",
                    "Concurrent concept extraction request collided; retry",
                ) from None
        raise AccessError(409, "concurrent_concept_job", "Failed to schedule job")

    def get_latest_concepts(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
    ) -> ConceptExtractionRecord:
        with access.transaction(
            bearer, "source.concepts.read", _source_ref(tenant_id, source_id)
        ) as scope:
            src_repo = self.source_repo_factory(scope.session)
            ver = src_repo.get_version(tenant_id, source_id, version_id)
            if ver is None:
                raise AccessError(404, "version_not_found", "Source version not found")
            rec = self.repo_factory(scope.session).get_latest_for_version(
                tenant_id, version_id
            )
            if rec is None:
                raise AccessError(
                    404,
                    "concepts_not_ready",
                    "Concept extraction not yet available for this version",
                )
            return rec

    def list_concept_versions(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        *,
        limit: int = 20,
    ) -> Page[ConceptExtractionRecord]:
        with access.transaction(
            bearer, "source.concepts.read", _source_ref(tenant_id, source_id)
        ) as scope:
            src_repo = self.source_repo_factory(scope.session)
            ver = src_repo.get_version(tenant_id, source_id, version_id)
            if ver is None:
                raise AccessError(404, "version_not_found", "Source version not found")
            return self.repo_factory(scope.session).list_for_version(
                tenant_id, version_id, limit=limit
            )

    def get_lesson_outline(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
    ) -> KnowledgePublicationState:
        with access.transaction(
            bearer, "source.concepts.read", _source_ref(tenant_id, source_id)
        ) as scope:
            src_repo = self.source_repo_factory(scope.session)
            ver = src_repo.get_version(tenant_id, source_id, version_id)
            if ver is None:
                raise AccessError(404, "version_not_found", "Source version not found")
            repo = self.repo_factory(scope.session)
            latest = repo.get_latest_for_version(tenant_id, version_id)
            if latest is None:
                raise AccessError(
                    404,
                    "concepts_not_ready",
                    "Concept extraction not yet available for this version",
                )
            pub = repo.get_publication(tenant_id, version_id)
            if pub is not None and pub.concept_extraction_id == latest.concept_extraction_id:
                return pub
            outline = generate_lesson_outline(latest.knowledge)
            return KnowledgePublicationState(
                tenant_id=tenant_id,
                source_id=source_id,
                version_id=version_id,
                concept_extraction_id=latest.concept_extraction_id,
                concept_version=latest.concept_version,
                source_sha256=latest.knowledge.source_sha256,
                review_status="pending_review",
                latest_review_id=None,
                approved_review_id=None,
                teacher_approved=False,
                published_to_students=False,
                published_by_user_id=None,
                published_at=None,
                updated_at=latest.created_at,
                learning_request_only=outline.learning_request_only,
                all_evidence_verified=outline.all_evidence_verified,
                outline=outline,
            )

    def review_concepts(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        req: KnowledgeReviewRequest,
    ) -> KnowledgePublicationState:
        with access.transaction(
            bearer, "source.concepts.review", _source_ref(tenant_id, source_id)
        ) as scope:
            src_repo = self.source_repo_factory(scope.session)
            src_info = src_repo.get_source_access(tenant_id, source_id)
            ver = src_repo.get_version(tenant_id, source_id, version_id)
            if src_info is None or ver is None:
                raise AccessError(404, "version_not_found", "Source version not found")
            if src_info.lifecycle_state != "active":
                raise AccessError(
                    409, "source_archived", "Archived sources cannot be reviewed"
                )
            repo = self.repo_factory(scope.session)
            ext = repo.get_by_id(tenant_id, req.concept_extraction_id)
            if ext is None or ext.version_id != version_id or ext.source_id != source_id:
                raise AccessError(
                    404,
                    "concept_extraction_not_found",
                    "Concept extraction version not found for this source version",
                )
            if (
                ext.knowledge.input_format == "topic_description"
                and req.decision == "approved"
                and not req.acknowledge_learning_request
            ):
                raise AccessError(
                    409,
                    "topic_request_acknowledgement_required",
                    "Topic description extractions contain learning requests, not factual evidence; set acknowledge_learning_request=true to approve curriculum scope",
                )
            valid_cids = {c.concept_id for c in ext.knowledge.concepts}
            unknown = (set(req.approved_concept_ids) | set(req.rejected_concept_ids)) - valid_cids
            if unknown:
                raise AccessError(
                    422,
                    "unknown_concept_id",
                    "Review references unknown concept_id for this extraction",
                )
            outline = generate_lesson_outline(ext.knowledge)
            rev, pub = repo.record_review(
                tenant_id=tenant_id,
                source_id=source_id,
                version_id=version_id,
                extraction=ext,
                outline=outline,
                decision=req.decision,
                review_notes=req.review_notes,
                approved_concept_ids=req.approved_concept_ids,
                rejected_concept_ids=req.rejected_concept_ids,
                reviewed_by_user_id=scope.context.actor_id,
                now=datetime.now(UTC),
            )
            scope.audit.change(
                scope.context,
                f"source.concepts.review.{rev.decision}",
                _source_ref(tenant_id, source_id),
            )
            return pub

    def list_reviews(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        *,
        limit: int = 20,
    ) -> Page[KnowledgeReviewRecord]:
        with access.transaction(
            bearer, "source.concepts.read", _source_ref(tenant_id, source_id)
        ) as scope:
            ver = self.source_repo_factory(scope.session).get_version(
                tenant_id, source_id, version_id
            )
            if ver is None:
                raise AccessError(404, "version_not_found", "Source version not found")
            return self.repo_factory(scope.session).list_reviews(
                tenant_id, version_id, limit=limit
            )

    def publish_concepts(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        req: KnowledgePublishRequest,
    ) -> KnowledgePublicationState:
        with access.transaction(
            bearer, "source.concepts.publish", _source_ref(tenant_id, source_id)
        ) as scope:
            src_repo = self.source_repo_factory(scope.session)
            src_info = src_repo.get_source_access(tenant_id, source_id)
            ver = src_repo.get_version(tenant_id, source_id, version_id)
            if src_info is None or ver is None:
                raise AccessError(404, "version_not_found", "Source version not found")
            if src_info.lifecycle_state != "active":
                raise AccessError(
                    409, "source_archived", "Archived sources cannot be published"
                )
            if req.publish and src_info.visibility != "offering_published":
                raise AccessError(
                    409,
                    "source_not_published",
                    "Set source visibility to offering_published before publishing educational lessons to students",
                )
            pub = self.repo_factory(scope.session).set_publication(
                tenant_id=tenant_id,
                version_id=version_id,
                concept_extraction_id=req.concept_extraction_id,
                publish=req.publish,
                actor_id=scope.context.actor_id,
                now=datetime.now(UTC),
            )
            scope.audit.change(
                scope.context,
                "source.concepts.published" if req.publish else "source.concepts.unpublished",
                _source_ref(tenant_id, source_id),
            )
            return pub

    def get_published_lesson(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
    ) -> PublishedLessonPackage:
        with access.transaction(
            bearer,
            "source.published_knowledge.read",
            _source_ref(tenant_id, source_id),
        ) as scope:
            src_repo = self.source_repo_factory(scope.session)
            src_info = src_repo.get_source_access(tenant_id, source_id)
            ver = src_repo.get_version(tenant_id, source_id, version_id)
            if (
                src_info is None
                or ver is None
                or src_info.lifecycle_state != "active"
                or src_info.visibility != "offering_published"
            ):
                raise AccessError(
                    404,
                    "published_knowledge_not_found",
                    "No active offering-published educational lesson exists for this version",
                )
            repo = self.repo_factory(scope.session)
            pub = repo.get_publication(tenant_id, version_id)
            if (
                pub is None
                or not pub.published_to_students
                or not pub.teacher_approved
                or pub.approved_review_id is None
                or pub.published_at is None
            ):
                raise AccessError(
                    404,
                    "published_knowledge_not_found",
                    "No approved educational lesson has been published for this version",
                )
            ext = repo.get_by_id(tenant_id, pub.concept_extraction_id)
            if ext is None:
                raise AccessError(
                    404,
                    "published_knowledge_not_found",
                    "Approved concept extraction record unavailable",
                )
            return PublishedLessonPackage(
                tenant_id=tenant_id,
                source_id=source_id,
                version_id=version_id,
                derived_extraction_id=ext.derived_extraction_id,
                concept_extraction_id=ext.concept_extraction_id,
                concept_version=ext.concept_version,
                approved_review_id=pub.approved_review_id,
                source_sha256=pub.source_sha256,
                input_format=ext.knowledge.input_format,
                title=ext.knowledge.title,
                teacher_approved=True,
                published_to_students=True,
                learning_request_only=pub.learning_request_only,
                all_evidence_verified=pub.all_evidence_verified,
                published_at=pub.published_at,
                knowledge=ext.knowledge.model_copy(
                    update={"published_to_students": True}
                ),
                outline=pub.outline,
            )
