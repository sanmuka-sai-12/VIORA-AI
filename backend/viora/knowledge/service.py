"""Authorized service and router workflows for educational concept extraction."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from ..ai.public import AIError, AIGateway, AIResourceLimitError
from ..contracts import Page, ResourceRef
from ..security import AccessError, TransactionalAccess
from ..sources.public import (
    SourceIngestionLimits,
    SqlSourceRepository,
    clamp_source_limits,
)
from ..storage.public import ArtifactStore
from ..workflow.public import ConcurrentJobConflict
from .blueprint import project_published_blueprint
from .blueprint_store import SqlLessonBlueprintRepository
from .curriculum import generate_lesson_outline
from .embeddings import TextEmbedder
from .indexing import SqlKnowledgeIndexRepository
from .personal_learning import (
    DirectAcademicLearning,
    PersonalLearningAttachmentService,
)
from .personal_learning_store import SqlPersonalLearningRepository
from .ports import ConceptExtractionRepository
from .publication import publication_complete, reviewed_graph
from .qa import GroundedKnowledgeQA
from .retrieval import HybridKnowledgeRetriever
from .schemas import (
    ConceptExtractionRecord,
    ConceptExtractionTriggerResult,
    FlashcardProgressUpdateRequest,
    KnowledgeIndexStatus,
    KnowledgeIndexTriggerResult,
    KnowledgePublicationState,
    KnowledgePublishRequest,
    KnowledgeQARequest,
    KnowledgeQAResult,
    KnowledgeRetrievalResult,
    KnowledgeRetrieveRequest,
    KnowledgeReviewRecord,
    KnowledgeReviewRequest,
    LessonBlueprintApprovalRequest,
    LessonBlueprintApprovalState,
    LessonBlueprintRecord,
    LessonBlueprintSummary,
    LessonBlueprintTriggerResult,
    PersonalLearningAttachment,
    PersonalLearningAttachmentTextRequest,
    PersonalLearningDeleteResult,
    PersonalLearningRequest,
    PersonalLearningResourceGenerateRequest,
    PersonalLearningResponse,
    PersonalLearningSessionCreateRequest,
    PersonalLearningSessionDetail,
    PersonalLearningSessionSummary,
    PersonalLearningSessionUpdateRequest,
    PersonalLearningStudyResource,
    PracticeExamActiveAttempt,
    PracticeExamAttemptRecord,
    PracticeExamProgressUpdateRequest,
    PracticeExamSubmitRequest,
    PracticeQuizAttemptRecord,
    PracticeQuizSubmitRequest,
    PublishedLessonBlueprint,
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
        embedder: TextEmbedder | None = None,
        ai_gateway: AIGateway | None = None,
        artifact_store: ArtifactStore | None = None,
        source_limits: SourceIngestionLimits | None = None,
        enable_ocr: bool = True,
    ) -> None:
        self.repo_factory = repo_factory
        self.source_repo_factory = source_repo_factory
        self.embedder: TextEmbedder | None = embedder
        self.ai_gateway: AIGateway | None = ai_gateway
        self.artifact_store: ArtifactStore | None = artifact_store
        self.source_limits = source_limits or clamp_source_limits()
        self.enable_ocr = enable_ocr

    def _personal_learning_attachments(self) -> PersonalLearningAttachmentService:
        if self.artifact_store is None:
            raise AccessError(
                503,
                "attachment_storage_unavailable",
                "Private study attachment storage is not configured",
            )
        return PersonalLearningAttachmentService(
            self.artifact_store,
            limits=self.source_limits,
            enable_ocr=self.enable_ocr,
        )

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
            reviewed = reviewed_graph(ext.knowledge, req.rejected_concept_ids)
            if req.decision == "approved" and not publication_complete(reviewed):
                raise AccessError(
                    409, "incomplete_publication", "A complete lesson is required for approval"
                )
            outline = generate_lesson_outline(reviewed)
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
            review = repo.get_approved_review(pub)
            if (
                ext is None or review is None
                or pub.source_id != source_id
                or ext.source_id != source_id or ext.version_id != version_id
                or ext.concept_version != pub.concept_version
                or ext.knowledge.source_sha256 != pub.source_sha256
            ):
                raise AccessError(
                    404,
                    "published_knowledge_not_found",
                    "Approved concept extraction record unavailable",
                )
            graph = reviewed_graph(ext.knowledge, review.rejected_concept_ids)
            if not publication_complete(graph):
                raise AccessError(
                    404, "published_knowledge_not_found", "Approved lesson is incomplete"
                )
            outline = generate_lesson_outline(graph)
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
                learning_request_only=outline.learning_request_only,
                all_evidence_verified=outline.all_evidence_verified,
                published_at=pub.published_at,
                knowledge=graph.model_copy(
                    update={"published_to_students": True}
                ),
                outline=outline,
            )

    def trigger_knowledge_indexing(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        *,
        idempotency_key: str | None = None,
    ) -> KnowledgeIndexTriggerResult:
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
                            "Archived sources cannot be indexed",
                        )
                    if derived is None:
                        raise AccessError(
                            409,
                            "extraction_required",
                            "Derived document extraction must complete before indexing",
                        )
                    concept_rec = self.repo_factory(
                        scope.session
                    ).get_latest_for_version(tenant_id, version_id)
                    concept_ext_id = (
                        concept_rec.concept_extraction_id
                        if concept_rec is not None
                        and concept_rec.derived_extraction_id == derived.extraction_id
                        else None
                    )
                    digest = hashlib.sha256(
                        json.dumps(
                            {
                                "op": "knowledge_index",
                                "source_id": str(source_id),
                                "version_id": str(version_id),
                                "derived_extraction_id": str(derived.extraction_id),
                                "concept_extraction_id": str(concept_ext_id)
                                if concept_ext_id
                                else None,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    idx_repo = SqlKnowledgeIndexRepository(scope.session)
                    job_id, job_state, replay = idx_repo.enqueue_index_job(
                        tenant_id=tenant_id,
                        offering_id=src_info.offering_id,
                        source_id=source_id,
                        version_id=version_id,
                        submitted_by_user_id=scope.context.actor_id,
                        input_artifact=ver.storage_ref,
                        idempotency_key=clean_key,
                        request_digest=digest,
                        now=datetime.now(UTC),
                    )
                    status = idx_repo.get_index_status(tenant_id, source_id, version_id)
                    if not replay:
                        scope.audit.change(
                            scope.context,
                            "source.knowledge.index_queued",
                            _source_ref(tenant_id, source_id),
                        )
                    return KnowledgeIndexTriggerResult(
                        job_id=job_id,
                        job_state=job_state,
                        derived_extraction_id=derived.extraction_id,
                        concept_extraction_id=concept_ext_id,
                        index_version=status.index_version,
                        idempotent_replay=replay,
                    )
            except ConcurrentJobConflict:
                if attempt == 0:
                    continue
                raise AccessError(
                    409,
                    "concurrent_index_job",
                    "Concurrent knowledge indexing request collided; retry",
                ) from None
        raise AccessError(409, "concurrent_index_job", "Failed to schedule indexing job")

    def get_knowledge_index_status(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
    ) -> KnowledgeIndexStatus:
        with access.transaction(
            bearer, "source.version.read", _source_ref(tenant_id, source_id)
        ) as scope:
            ver = self.source_repo_factory(scope.session).get_version(
                tenant_id, source_id, version_id
            )
            if ver is None:
                raise AccessError(404, "version_not_found", "Source version not found")
            return SqlKnowledgeIndexRepository(scope.session).get_index_status(
                tenant_id, source_id, version_id
            )

    def retrieve_knowledge(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        req: KnowledgeRetrieveRequest,
    ) -> KnowledgeRetrievalResult:
        if req.offering_id is not None:
            action = "offering.read"
            ref = ResourceRef(
                tenant_id=tenant_id, kind="offering", resource_id=req.offering_id
            )
        else:
            action = "knowledge.retrieve"
            ref = ResourceRef(
                tenant_id=tenant_id, kind="institution", resource_id=tenant_id
            )
        with access.transaction(bearer, action, ref) as scope:
            return HybridKnowledgeRetriever(scope.session, self.embedder).retrieve(
                scope.context, req
            )

    def answer_question(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        req: KnowledgeQARequest,
    ) -> KnowledgeQAResult:
        if req.offering_id is not None:
            action = "offering.read"
            ref = ResourceRef(
                tenant_id=tenant_id, kind="offering", resource_id=req.offering_id
            )
        else:
            action = "knowledge.retrieve"
            ref = ResourceRef(
                tenant_id=tenant_id, kind="institution", resource_id=tenant_id
            )
        with access.transaction(bearer, action, ref) as scope:
            if self.ai_gateway is None:
                raise AccessError(
                    503,
                    "ai_gateway_unavailable",
                    "Grounded Q&A requires an active AI gateway",
                )
            scoped_context = scope.context
            retrieve_req = KnowledgeRetrieveRequest(
                query=req.question,
                offering_id=req.offering_id,
                mode=req.mode,
                top_k=req.top_k,
                candidate_k=req.candidate_k,
                max_context_tokens=req.max_context_tokens,
                include_student_submissions=req.include_student_submissions,
                published_only=req.published_only,
                max_graph_hops=req.max_graph_hops,
            )
            retriever = HybridKnowledgeRetriever(scope.session, self.embedder)
            retrieval = retriever.retrieve(scoped_context, retrieve_req)
            evidence = retriever.evidence_snapshot
        try:
            answer = GroundedKnowledgeQA(self.ai_gateway).answer(
                context=scoped_context,
                request=req,
                retrieval=retrieval,
            )
        except AIResourceLimitError as exc:
            raise AccessError(422, exc.code, exc.message) from None
        except AIError as exc:
            raise AccessError(503, exc.code, exc.message) from None
        with access.transaction(bearer, action, ref) as scope:
            if scope.context != scoped_context:
                raise AccessError(
                    403, "access_changed", "Knowledge access changed during inference"
                )
            HybridKnowledgeRetriever(scope.session).recheck_evidence(
                scope.context, retrieve_req, evidence
            )
            return answer

    def upload_personal_learning_file(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        *,
        read_bounded_bytes: Callable[[int], bytes],
        original_filename: str | None,
        claimed_content_type: str | None,
        title: str | None = None,
    ) -> PersonalLearningAttachment:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            scoped_context = scope.context
            attachments_svc = self._personal_learning_attachments()
        raw_bytes = read_bounded_bytes(self.source_limits.max_bytes)
        attachment = attachments_svc.ingest_file(
            context=scoped_context,
            raw_bytes=raw_bytes,
            original_filename=original_filename,
            claimed_content_type=claimed_content_type,
            title=title,
        )
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.context != scoped_context:
                raise AccessError(
                    403, "access_changed", "Institution access changed during upload"
                )
            if scope.session is not None:
                return SqlPersonalLearningRepository(scope.session).register_material(
                    scoped_context, attachment
                )
            return attachment

    def upload_personal_learning_text(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        req: PersonalLearningAttachmentTextRequest,
    ) -> PersonalLearningAttachment:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            scoped_context = scope.context
            attachments_svc = self._personal_learning_attachments()
        attachment = attachments_svc.ingest_text(
            context=scoped_context,
            title=req.title,
            content=req.content,
        )
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.context != scoped_context:
                raise AccessError(
                    403, "access_changed", "Institution access changed during upload"
                )
            if scope.session is not None:
                return SqlPersonalLearningRepository(scope.session).register_material(
                    scoped_context, attachment
                )
            return attachment

    def import_classroom_material(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
    ) -> PersonalLearningAttachment:
        from ..sources.public import read_source_material_bytes

        source_ref = ResourceRef(tenant_id=tenant_id, kind="source", resource_id=source_id)
        with access.transaction(bearer, "source.read", source_ref) as scope:
            scoped_context = scope.context
            attachments_svc = self._personal_learning_attachments()
            if scope.session is None:
                raise AccessError(404, "source_missing", "Source material unavailable")
            material = read_source_material_bytes(
                scope.session,
                scoped_context,
                attachments_svc.artifact_store,
                tenant_id,
                source_id,
            )
            if material is None:
                raise AccessError(404, "source_missing", "Source material unavailable")
            title, input_format, original_filename, raw_bytes = material

        if input_format in {"plain_text", "typed_notes", "topic_description"}:
            attachment = attachments_svc.ingest_text(
                context=scoped_context,
                title=title,
                content=raw_bytes.decode("utf-8", errors="replace"),
            )
        else:
            media_type = {
                "digital_pdf": "application/pdf",
                "scanned_pdf": "application/pdf",
                "png": "image/png",
                "jpeg": "image/jpeg",
            }.get(input_format, "application/octet-stream")
            ext = {
                "digital_pdf": ".pdf",
                "scanned_pdf": ".pdf",
                "png": ".png",
                "jpeg": ".jpg",
            }.get(input_format, ".bin")
            attachment = attachments_svc.ingest_file(
                context=scoped_context,
                raw_bytes=raw_bytes,
                original_filename=original_filename or f"{title}{ext}",
                claimed_content_type=media_type,
                title=title,
            )

        with access.transaction(bearer, "source.read", source_ref) as scope:
            if scope.context != scoped_context:
                raise AccessError(
                    403, "access_changed", "Access changed during classroom import"
                )
            if scope.session is not None:
                return SqlPersonalLearningRepository(scope.session).register_material(
                    scoped_context, attachment, origin_source_id=source_id
                )
            return attachment

    def list_personal_learning_materials(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        *,
        limit: int = 50,
    ) -> Page[PersonalLearningAttachment]:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.session is None:
                return Page(items=())
            items = SqlPersonalLearningRepository(scope.session).list_materials(
                scope.context, limit=limit
            )
            return Page(items=items)

    def delete_personal_learning_material(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        attachment_id: UUID,
    ) -> PersonalLearningDeleteResult:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.session is None:
                raise AccessError(
                    503,
                    "persistence_unavailable",
                    "Personal learning library requires database persistence",
                )
            SqlPersonalLearningRepository(scope.session).delete_material(
                scope.context, attachment_id
            )
            return PersonalLearningDeleteResult(deleted=True)

    def list_personal_learning_sessions(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        *,
        limit: int = 40,
    ) -> Page[PersonalLearningSessionSummary]:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.session is None:
                return Page(items=())
            items = SqlPersonalLearningRepository(scope.session).list_sessions(
                scope.context, limit=limit
            )
            return Page(items=items)

    def create_personal_learning_session(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        req: PersonalLearningSessionCreateRequest,
    ) -> PersonalLearningSessionDetail:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.session is None:
                raise AccessError(
                    503,
                    "persistence_unavailable",
                    "Personal learning sessions require database persistence",
                )
            return SqlPersonalLearningRepository(scope.session).create_session(
                scope.context,
                title=req.title,
                mode=req.mode,
                attachment_ids=req.attachment_ids,
            )

    def get_personal_learning_session(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        session_id: UUID,
    ) -> PersonalLearningSessionDetail:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.session is None:
                raise AccessError(
                    404,
                    "learning_session_not_found",
                    "Learning conversation was not found",
                )
            return SqlPersonalLearningRepository(scope.session).get_session(
                scope.context, session_id
            )

    def update_personal_learning_session(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        session_id: UUID,
        req: PersonalLearningSessionUpdateRequest,
    ) -> PersonalLearningSessionDetail:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.session is None:
                raise AccessError(
                    503,
                    "persistence_unavailable",
                    "Personal learning sessions require database persistence",
                )
            return SqlPersonalLearningRepository(scope.session).update_session(
                scope.context, session_id, req
            )

    def delete_personal_learning_session(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        session_id: UUID,
    ) -> PersonalLearningDeleteResult:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.session is None:
                raise AccessError(
                    503,
                    "persistence_unavailable",
                    "Personal learning sessions require database persistence",
                )
            SqlPersonalLearningRepository(scope.session).delete_session(
                scope.context, session_id
            )
            return PersonalLearningDeleteResult(deleted=True)

    def _resolve_learning_inputs(
        self,
        scope,
        *,
        session_id: UUID | None,
        history: tuple,
        attachments: tuple,
        attachment_ids: tuple[UUID, ...],
        include_conversation: bool = True,
    ):
        resolved_refs = list(attachments)
        effective_history = history
        active_ids: list[UUID] = [
            *attachment_ids,
            *(att.attachment_id for att in attachments),
        ]
        dedup_active_ids = list(dict.fromkeys(active_ids))[:5]
        active_titles: list[str] = []
        session_title: str | None = None

        if scope.session is not None:
            pl_repo = SqlPersonalLearningRepository(scope.session)
            revocation_check_ids = list(dedup_active_ids)
            if session_id is not None:
                detail = pl_repo.get_session(scope.context, session_id)
                session_title = detail.title
                if include_conversation and not effective_history:
                    effective_history = pl_repo.load_recent_turns(
                        scope.context, session_id
                    )
                if not dedup_active_ids and detail.attachments:
                    dedup_active_ids = [
                        att.attachment_id for att in detail.attachments[:5]
                    ]
                if include_conversation and detail.attachments:
                    revocation_check_ids.extend(
                        att.attachment_id for att in detail.attachments
                    )
            if revocation_check_ids:
                for origin_src_id in pl_repo.origin_source_ids_for_materials(
                    scope.context, revocation_check_ids
                ):
                    try:
                        scope.recheck(
                            "source.read",
                            ResourceRef(
                                tenant_id=scope.context.tenant_id,
                                kind="source",
                                resource_id=origin_src_id,
                            ),
                        )
                    except AccessError:
                        raise AccessError(
                            403,
                            "material_access_revoked",
                            "Imported classroom material is no longer accessible after withdrawal or unenrolment",
                        ) from None
            if dedup_active_ids:
                db_refs = pl_repo.resolve_material_refs(scope.context, dedup_active_ids)
                seen_att_ids = {r.attachment_id for r in resolved_refs}
                for dref in db_refs:
                    if dref.attachment_id not in seen_att_ids:
                        resolved_refs.append(dref)
                        seen_att_ids.add(dref.attachment_id)
                mat_rows = pl_repo._load_materials_map(scope.context, dedup_active_ids)
                active_titles = [m.title for m in mat_rows]

        return (
            tuple(resolved_refs[:5]),
            effective_history,
            dedup_active_ids,
            active_titles,
            session_title,
        )

    def ask_personal_learning(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        req: PersonalLearningRequest,
    ) -> PersonalLearningResponse:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if self.ai_gateway is None:
                raise AccessError(
                    503,
                    "ai_gateway_unavailable",
                    "Personal learning AI service is not configured",
                )
            scoped_context = scope.context
            (
                resolved_refs,
                effective_history,
                dedup_active_ids,
                active_titles,
                _,
            ) = self._resolve_learning_inputs(
                scope,
                session_id=req.session_id,
                history=req.history,
                attachments=req.attachments,
                attachment_ids=req.attachment_ids,
            )
            attachments_svc = (
                self._personal_learning_attachments() if resolved_refs else None
            )

        effective_req = req.model_copy(
            update={
                "history": effective_history,
                "attachments": resolved_refs,
            }
        )
        try:
            response = DirectAcademicLearning(
                self.ai_gateway,
                attachment_service=attachments_svc,
            ).ask(
                context=scoped_context,
                request=effective_req,
            )
        except AIResourceLimitError as exc:
            raise AccessError(422, exc.code, exc.message) from None
        except AIError as exc:
            raise AccessError(503, exc.code, exc.message) from None

        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.context != scoped_context:
                raise AccessError(
                    403, "access_changed", "Institution access changed during inference"
                )
            if scope.session is not None:
                titles_for_msg = active_titles or list(
                    dict.fromkeys(src.title for src in response.sources)
                )
                saved_session_id, saved_title = SqlPersonalLearningRepository(
                    scope.session
                ).append_exchange(
                    scoped_context,
                    session_id=req.session_id,
                    prompt_text=req.prompt.strip(),
                    mode=req.mode,
                    attached_titles=titles_for_msg,
                    assistant_content=response.answer,
                    sources=response.sources,
                    follow_up_prompts=response.follow_up_prompts,
                    truncated=response.truncated,
                    active_attachment_ids=dedup_active_ids,
                )
                return response.model_copy(
                    update={
                        "session_id": saved_session_id,
                        "session_title": saved_title,
                    }
                )
            return response

    def generate_personal_learning_resource(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        req: PersonalLearningResourceGenerateRequest,
    ) -> PersonalLearningStudyResource:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if self.ai_gateway is None:
                raise AccessError(
                    503,
                    "ai_gateway_unavailable",
                    "Personal learning AI service is not configured",
                )
            scoped_context = scope.context
            (
                resolved_refs,
                effective_history,
                dedup_active_ids,
                active_titles,
                session_title,
            ) = self._resolve_learning_inputs(
                scope,
                session_id=req.session_id,
                history=req.history,
                attachments=req.attachments,
                attachment_ids=req.attachment_ids,
                include_conversation=req.include_conversation,
            )
            attachments_svc = (
                self._personal_learning_attachments() if resolved_refs else None
            )

        effective_req = req.model_copy(
            update={
                "history": effective_history,
                "attachments": resolved_refs,
            }
        )
        try:
            draft = DirectAcademicLearning(
                self.ai_gateway,
                attachment_service=attachments_svc,
            ).generate_study_resource(
                context=scoped_context,
                request=effective_req,
                session_title=session_title,
            )
        except AIResourceLimitError as exc:
            raise AccessError(422, exc.code, exc.message) from None
        except AIError as exc:
            raise AccessError(503, exc.code, exc.message) from None

        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.context != scoped_context:
                raise AccessError(
                    403,
                    "access_changed",
                    "Institution access changed during generation",
                )
            if scope.session is None:
                raise AccessError(
                    503,
                    "persistence_unavailable",
                    "Personal learning resources require database persistence",
                )
            fallback_title = (
                (req.topic or "").strip()
                or (active_titles[0] if active_titles else "")
                or (effective_history[-1].content if effective_history else "Study Notes")
            )
            return SqlPersonalLearningRepository(scope.session).save_study_resource(
                scoped_context,
                session_id=req.session_id,
                replace_resource_id=req.replace_resource_id,
                fallback_title=fallback_title,
                active_attachment_ids=dedup_active_ids,
                tool_kind=draft["tool_kind"],
                source_scope=draft["source_scope"],
                title=draft["title"],
                focus_topic=draft["focus_topic"],
                content=draft["content"],
                structured_data=draft.get("structured_data"),
                sources=draft["sources"],
                model=draft["model"],
                provider=draft["provider"],
                usage_tokens=draft["usage_tokens"],
                truncated=draft["truncated"],
            )

    def list_personal_learning_resources(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        session_id: UUID,
    ) -> Page[PersonalLearningStudyResource]:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.session is None:
                return Page(items=())
            items = SqlPersonalLearningRepository(scope.session).list_session_resources(
                scope.context, session_id
            )
            return Page(items=items)

    def delete_personal_learning_resource(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        resource_id: UUID,
    ) -> PersonalLearningDeleteResult:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.session is None:
                raise AccessError(
                    503,
                    "persistence_unavailable",
                    "Personal learning resources require database persistence",
                )
            SqlPersonalLearningRepository(scope.session).delete_study_resource(
                scope.context, resource_id
            )
            return PersonalLearningDeleteResult(deleted=True)

    def update_personal_learning_flashcards(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        resource_id: UUID,
        req: FlashcardProgressUpdateRequest,
    ) -> PersonalLearningStudyResource:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.session is None:
                raise AccessError(
                    503,
                    "persistence_unavailable",
                    "Personal learning resources require database persistence",
                )
            return SqlPersonalLearningRepository(
                scope.session
            ).update_flashcard_progress(scope.context, resource_id, req.statuses)

    def submit_personal_learning_quiz(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        resource_id: UUID,
        req: PracticeQuizSubmitRequest,
    ) -> PracticeQuizAttemptRecord:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.session is None:
                raise AccessError(
                    503,
                    "persistence_unavailable",
                    "Personal learning quizzes require database persistence",
                )
            return SqlPersonalLearningRepository(
                scope.session
            ).submit_practice_quiz(scope.context, resource_id, req.answers)

    def start_personal_learning_exam(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        resource_id: UUID,
    ) -> PracticeExamActiveAttempt:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.session is None:
                raise AccessError(
                    503,
                    "persistence_unavailable",
                    "Personal learning exams require database persistence",
                )
            return SqlPersonalLearningRepository(
                scope.session
            ).start_practice_exam(scope.context, resource_id)

    def save_personal_learning_exam_progress(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        resource_id: UUID,
        req: PracticeExamProgressUpdateRequest,
    ) -> PracticeExamActiveAttempt:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.session is None:
                raise AccessError(
                    503,
                    "persistence_unavailable",
                    "Personal learning exams require database persistence",
                )
            return SqlPersonalLearningRepository(
                scope.session
            ).save_practice_exam_progress(
                scope.context,
                resource_id,
                attempt_id=req.attempt_id,
                answers=req.answers,
                marked_for_review=req.marked_for_review,
            )

    def submit_personal_learning_exam(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        resource_id: UUID,
        req: PracticeExamSubmitRequest,
    ) -> PracticeExamAttemptRecord:
        ref = ResourceRef(tenant_id=tenant_id, kind="institution", resource_id=tenant_id)
        with access.transaction(bearer, "knowledge.retrieve", ref) as scope:
            if scope.session is None:
                raise AccessError(
                    503,
                    "persistence_unavailable",
                    "Personal learning exams require database persistence",
                )
            return SqlPersonalLearningRepository(
                scope.session
            ).submit_practice_exam(
                scope.context,
                resource_id,
                attempt_id=req.attempt_id,
                answers=req.answers,
                auto_submitted=req.auto_submitted,
            )

    def trigger_lesson_blueprint(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        *,
        idempotency_key: str | None = None,
    ) -> LessonBlueprintTriggerResult:
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
                            "Archived sources cannot generate lesson blueprints",
                        )
                    if src_info.origin_kind != "teacher_resource":
                        raise AccessError(
                            409,
                            "teacher_resource_required",
                            "Only teacher resources support approved lesson blueprints",
                        )
                    if derived is None:
                        raise AccessError(
                            409,
                            "extraction_required",
                            "Derived extraction must complete before blueprint generation",
                        )
                    repo = self.repo_factory(scope.session)
                    pub = repo.get_publication(tenant_id, version_id)
                    if (
                        pub is None
                        or not pub.teacher_approved
                        or pub.approved_review_id is None
                    ):
                        raise AccessError(
                            409,
                            "teacher_approval_required",
                            "Approved curriculum review is required before generating a lesson blueprint",
                        )
                    ext = repo.get_by_id(tenant_id, pub.concept_extraction_id)
                    review = repo.get_approved_review(pub)
                    if (
                        ext is None
                        or review is None
                        or ext.source_id != source_id
                        or ext.version_id != version_id
                        or ext.derived_extraction_id != derived.extraction_id
                        or ext.knowledge.source_sha256 != pub.source_sha256
                    ):
                        raise AccessError(
                            409,
                            "approved_curriculum_mismatch",
                            "Approved curriculum records are incomplete or mismatched",
                        )
                    reviewed = reviewed_graph(
                        ext.knowledge, review.rejected_concept_ids
                    )
                    if not publication_complete(reviewed):
                        raise AccessError(
                            409,
                            "incomplete_publication",
                            "A complete approved curriculum is required for blueprint generation",
                        )
                    digest = hashlib.sha256(
                        json.dumps(
                            {
                                "op": "lesson_blueprint",
                                "source_id": str(source_id),
                                "version_id": str(version_id),
                                "concept_extraction_id": str(ext.concept_extraction_id),
                                "approved_review_id": str(pub.approved_review_id),
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    now = datetime.now(UTC)
                    bp_repo = SqlLessonBlueprintRepository(scope.session)
                    job_id, job_state, replay = bp_repo.enqueue_blueprint_job(
                        tenant_id=tenant_id,
                        offering_id=src_info.offering_id,
                        source_id=source_id,
                        version_id=version_id,
                        submitted_by_user_id=scope.context.actor_id,
                        input_artifact=ver.storage_ref,
                        idempotency_key=clean_key,
                        request_digest=digest,
                        now=now,
                    )
                    latest_summary = bp_repo.get_latest_summary(tenant_id, version_id)
                    if not replay:
                        scope.audit.change(
                            scope.context,
                            "source.lesson_blueprint.queued",
                            _source_ref(tenant_id, source_id),
                        )
                    return LessonBlueprintTriggerResult(
                        job_id=job_id,
                        job_state=job_state,
                        concept_extraction_id=ext.concept_extraction_id,
                        approved_review_id=pub.approved_review_id,
                        blueprint_id=(
                            latest_summary.blueprint_id if latest_summary else None
                        ),
                        idempotent_replay=replay,
                    )
            except ConcurrentJobConflict:
                if attempt == 0:
                    continue
                raise AccessError(
                    409,
                    "concurrent_blueprint_job",
                    "Concurrent lesson blueprint request collided; retry",
                ) from None
        raise AccessError(409, "concurrent_blueprint_job", "Failed to schedule blueprint job")

    def get_latest_blueprint(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
    ) -> LessonBlueprintRecord:
        with access.transaction(
            bearer, "source.concepts.read", _source_ref(tenant_id, source_id)
        ) as scope:
            if self.artifact_store is None:
                raise AccessError(
                    503,
                    "artifact_store_unavailable",
                    "Lesson blueprint artifact storage is not configured",
                )
            ver = self.source_repo_factory(scope.session).get_version(
                tenant_id, source_id, version_id
            )
            if ver is None:
                raise AccessError(404, "version_not_found", "Source version not found")
            rec = SqlLessonBlueprintRepository(scope.session).get_latest_for_version(
                tenant_id,
                version_id,
                artifact_store=self.artifact_store,
                context=scope.context,
            )
            if rec is None:
                raise AccessError(
                    404,
                    "lesson_blueprint_not_found",
                    "Lesson blueprint not yet available for this version",
                )
            return rec

    def list_blueprint_versions(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        *,
        limit: int = 20,
    ) -> Page[LessonBlueprintSummary]:
        with access.transaction(
            bearer, "source.concepts.read", _source_ref(tenant_id, source_id)
        ) as scope:
            ver = self.source_repo_factory(scope.session).get_version(
                tenant_id, source_id, version_id
            )
            if ver is None:
                raise AccessError(404, "version_not_found", "Source version not found")
            return SqlLessonBlueprintRepository(scope.session).list_for_version(
                tenant_id, version_id, limit=limit
            )

    def review_lesson_blueprint(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        req: LessonBlueprintApprovalRequest,
    ) -> LessonBlueprintApprovalState:
        action = "source.concepts.publish" if req.publish else "source.concepts.review"
        with access.transaction(bearer, action, _source_ref(tenant_id, source_id)) as scope:
            src_repo = self.source_repo_factory(scope.session)
            src_info = src_repo.get_source_access(tenant_id, source_id)
            ver = src_repo.get_version(tenant_id, source_id, version_id)
            if src_info is None or ver is None:
                raise AccessError(404, "version_not_found", "Source version not found")
            if src_info.lifecycle_state != "active":
                raise AccessError(
                    409,
                    "source_archived",
                    "Archived sources cannot approve or publish lesson blueprints",
                )
            if src_info.origin_kind != "teacher_resource":
                raise AccessError(
                    409,
                    "teacher_resource_required",
                    "Only teacher resources support lesson blueprint approval",
                )
            repo = self.repo_factory(scope.session)
            pub = repo.get_publication(tenant_id, version_id)
            if (
                pub is None
                or not pub.teacher_approved
                or pub.approved_review_id is None
            ):
                raise AccessError(
                    409,
                    "teacher_approval_required",
                    "Curriculum review approval is required before approving a lesson blueprint",
                )
            if req.publish:
                if src_info.visibility != "offering_published":
                    raise AccessError(
                        409,
                        "source_not_published",
                        "Set source visibility to offering_published before publishing a lesson blueprint to students",
                    )
                if not pub.published_to_students:
                    raise AccessError(
                        409,
                        "concept_publication_required",
                        "Publish approved curriculum concepts before publishing a generated lesson blueprint",
                    )
            state = SqlLessonBlueprintRepository(scope.session).review_and_publish(
                tenant_id=tenant_id,
                source_id=source_id,
                version_id=version_id,
                blueprint_id=req.blueprint_id,
                concept_extraction_id=pub.concept_extraction_id,
                approved_review_id=pub.approved_review_id,
                source_sha256=pub.source_sha256,
                decision=req.decision,
                publish=req.publish,
                review_notes=req.review_notes,
                actor_id=scope.context.actor_id,
                now=datetime.now(UTC),
            )
            scope.audit.change(
                scope.context,
                f"source.lesson_blueprint.review.{req.decision}",
                _source_ref(tenant_id, source_id),
            )
            return state

    def get_blueprint_approval_state(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
    ) -> LessonBlueprintApprovalState:
        with access.transaction(
            bearer, "source.concepts.read", _source_ref(tenant_id, source_id)
        ) as scope:
            ver = self.source_repo_factory(scope.session).get_version(
                tenant_id, source_id, version_id
            )
            if ver is None:
                raise AccessError(404, "version_not_found", "Source version not found")
            bp_repo = SqlLessonBlueprintRepository(scope.session)
            state = bp_repo.get_publication_state(tenant_id, version_id)
            if state is not None:
                return state
            latest = bp_repo.get_latest_summary(tenant_id, version_id)
            if latest is None:
                raise AccessError(
                    404,
                    "lesson_blueprint_not_found",
                    "Lesson blueprint not yet available for this version",
                )
            return LessonBlueprintApprovalState(
                tenant_id=tenant_id,
                source_id=source_id,
                version_id=version_id,
                blueprint_id=latest.blueprint_id,
                concept_extraction_id=latest.concept_extraction_id,
                approved_review_id=latest.approved_review_id,
                approval_status="pending_review",
                teacher_approved=False,
                published_to_students=False,
                updated_at=latest.created_at,
            )

    def get_published_blueprint(
        self,
        access: TransactionalAccess,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
    ) -> PublishedLessonBlueprint:
        with access.transaction(
            bearer,
            "source.published_knowledge.read",
            _source_ref(tenant_id, source_id),
        ) as scope:
            if self.artifact_store is None:
                raise AccessError(
                    503,
                    "artifact_store_unavailable",
                    "Lesson blueprint artifact storage is not configured",
                )
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
                    "published_blueprint_not_found",
                    "No active offering-published lesson blueprint exists for this version",
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
                    "published_blueprint_not_found",
                    "No approved lesson has been published for this version",
                )
            bp_repo = SqlLessonBlueprintRepository(scope.session)
            bp_pub = bp_repo.get_publication_state(tenant_id, version_id)
            if (
                bp_pub is None
                or not bp_pub.teacher_approved
                or not bp_pub.published_to_students
                or bp_pub.blueprint_id is None
                or bp_pub.published_at is None
                or bp_pub.concept_extraction_id != pub.concept_extraction_id
                or bp_pub.approved_review_id != pub.approved_review_id
            ):
                raise AccessError(
                    404,
                    "published_blueprint_not_found",
                    "No teacher-approved lesson blueprint has been published for this version",
                )
            rec = bp_repo.get_by_id(
                tenant_id,
                bp_pub.blueprint_id,
                artifact_store=self.artifact_store,
                context=scope.context,
            )
            if (
                rec is None
                or rec.source_id != source_id
                or rec.version_id != version_id
                or rec.approved_review_id != pub.approved_review_id
                or rec.concept_extraction_id != pub.concept_extraction_id
                or rec.source_sha256 != pub.source_sha256
                or rec.blueprint.approved_review_id != pub.approved_review_id
                or rec.blueprint.concept_extraction_id != pub.concept_extraction_id
            ):
                raise AccessError(
                    404,
                    "published_blueprint_not_found",
                    "No lesson blueprint matching the published curriculum version is available",
                )
            return project_published_blueprint(rec, published_at=bp_pub.published_at)
