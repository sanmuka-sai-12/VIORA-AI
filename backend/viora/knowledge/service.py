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
from .ports import ConceptExtractionRepository
from .publication import publication_complete, reviewed_graph
from .qa import GroundedKnowledgeQA
from .retrieval import HybridKnowledgeRetriever
from .schemas import (
    ConceptExtractionRecord,
    ConceptExtractionTriggerResult,
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
    PersonalLearningRequest,
    PersonalLearningResponse,
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
        return attachments_svc.ingest_file(
            context=scoped_context,
            raw_bytes=raw_bytes,
            original_filename=original_filename,
            claimed_content_type=claimed_content_type,
            title=title,
        )

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
        return attachments_svc.ingest_text(
            context=scoped_context,
            title=req.title,
            content=req.content,
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
            attachments_svc = (
                self._personal_learning_attachments() if req.attachments else None
            )
        try:
            response = DirectAcademicLearning(
                self.ai_gateway,
                attachment_service=attachments_svc,
            ).ask(
                context=scoped_context,
                request=req,
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
            return response

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
