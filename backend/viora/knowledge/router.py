"""HTTP API router for educational concept extraction and retrieval."""

import contextlib
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, Query, Security, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..ai.public import AIGateway, build_ai_gateway_from_settings
from ..config import Settings
from ..contracts import Page
from ..db import Database
from ..security import Access, AccessError, TransactionalAccess
from ..sources.public import clamp_source_limits
from ..storage.public import ArtifactStore, local_artifact_store
from .embeddings import TextEmbedder, build_embedder_from_settings
from .repository import SqlConceptExtractionRepository
from .schemas import (
    ConceptExtractionRecord,
    ConceptExtractionTriggerRequest,
    ConceptExtractionTriggerResult,
    FlashcardProgressUpdateRequest,
    KnowledgeIndexStatus,
    KnowledgeIndexTriggerRequest,
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
    LessonBlueprintTriggerRequest,
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
    PracticeQuizAttemptRecord,
    PracticeQuizSubmitRequest,
    PublishedLessonBlueprint,
    PublishedLessonPackage,
)
from .service import ConceptKnowledgeService


def _token(bearer: HTTPAuthorizationCredentials | None) -> str | None:
    return bearer.credentials if bearer else None


def _read_bounded_upload(upload: UploadFile, max_bytes: int) -> bytes:
    """Read an uploaded stream in fixed chunks and enforce byte limits."""
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = upload.file.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise AccessError(
                    413,
                    "payload_too_large",
                    f"Uploaded file exceeds maximum byte limit of {max_bytes}",
                )
            chunks.append(chunk)
    finally:
        with contextlib.suppress(OSError, ValueError):
            upload.file.close()
    return b"".join(chunks)


def build_router(
    access: Access | TransactionalAccess,
    db: Database | None = None,
    *,
    settings: Settings | None = None,
    embedder: TextEmbedder | None = None,
    ai_gateway: AIGateway | None = None,
    artifact_store: ArtifactStore | None = None,
) -> APIRouter:
    _ = db
    bearer_scheme = HTTPBearer(auto_error=False)
    resolved_embedder = (
        embedder
        if embedder is not None
        else (build_embedder_from_settings(settings) if settings is not None else None)
    )
    resolved_gateway = (
        ai_gateway
        if ai_gateway is not None
        else (build_ai_gateway_from_settings(settings) if settings is not None else None)
    )
    resolved_store = artifact_store or local_artifact_store(
        settings.media_storage_dir if settings is not None else "./storage"
    )
    resolved_limits = clamp_source_limits(
        max_bytes=settings.source_max_bytes if settings is not None else 10 * 1024 * 1024,
        max_pdf_pages=settings.source_max_pdf_pages if settings is not None else 100,
        max_text_chars=settings.source_max_text_chars if settings is not None else 200_000,
        min_text_chars=settings.source_min_text_chars if settings is not None else 3,
    )
    resolved_ocr = settings.worker_enable_ocr if settings is not None else True
    service = ConceptKnowledgeService(
        SqlConceptExtractionRepository,
        embedder=resolved_embedder,
        ai_gateway=resolved_gateway,
        artifact_store=resolved_store,
        source_limits=resolved_limits,
        enable_ocr=resolved_ocr,
    )
    router = APIRouter(tags=["knowledge"])

    @router.post(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts/extract",
        status_code=202,
        response_model=ConceptExtractionTriggerResult,
    )
    def trigger_concepts(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        body: ConceptExtractionTriggerRequest | None = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> ConceptExtractionTriggerResult:
        key = (
            body.idempotency_key
            if (body and body.idempotency_key)
            else idempotency_key
        )
        return service.trigger_concept_extraction(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            source_id,
            version_id,
            idempotency_key=key,
        )

    @router.get(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts",
        response_model=ConceptExtractionRecord,
    )
    def get_latest_concepts(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> ConceptExtractionRecord:
        return service.get_latest_concepts(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            source_id,
            version_id,
        )

    @router.get(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts/versions",
        response_model=Page[ConceptExtractionRecord],
    )
    def list_concept_versions(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> Page[ConceptExtractionRecord]:
        return service.list_concept_versions(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            source_id,
            version_id,
            limit=limit,
        )

    @router.get(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts/outline",
        response_model=KnowledgePublicationState,
    )
    def get_lesson_outline(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> KnowledgePublicationState:
        return service.get_lesson_outline(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            source_id,
            version_id,
        )

    @router.post(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts/review",
        response_model=KnowledgePublicationState,
    )
    def review_concepts(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        body: KnowledgeReviewRequest,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> KnowledgePublicationState:
        return service.review_concepts(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            source_id,
            version_id,
            body,
        )

    @router.get(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts/reviews",
        response_model=Page[KnowledgeReviewRecord],
    )
    def list_reviews(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> Page[KnowledgeReviewRecord]:
        return service.list_reviews(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            source_id,
            version_id,
            limit=limit,
        )

    @router.post(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts/publish",
        response_model=KnowledgePublicationState,
    )
    def publish_concepts(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        body: KnowledgePublishRequest,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> KnowledgePublicationState:
        return service.publish_concepts(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            source_id,
            version_id,
            body,
        )

    @router.get(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/concepts/published",
        response_model=PublishedLessonPackage,
    )
    def get_published_lesson(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> PublishedLessonPackage:
        return service.get_published_lesson(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            source_id,
            version_id,
        )

    @router.post(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/index",
        status_code=202,
        response_model=KnowledgeIndexTriggerResult,
    )
    def trigger_knowledge_index(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        body: KnowledgeIndexTriggerRequest | None = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> KnowledgeIndexTriggerResult:
        key = (
            body.idempotency_key
            if (body and body.idempotency_key)
            else idempotency_key
        )
        return service.trigger_knowledge_indexing(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            source_id,
            version_id,
            idempotency_key=key,
        )

    @router.get(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/index",
        response_model=KnowledgeIndexStatus,
    )
    def get_knowledge_index_status(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> KnowledgeIndexStatus:
        return service.get_knowledge_index_status(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            source_id,
            version_id,
        )

    @router.post(
        "/tenants/{tenant_id}/knowledge/retrieve",
        response_model=KnowledgeRetrievalResult,
    )
    def retrieve_knowledge(
        tenant_id: UUID,
        body: KnowledgeRetrieveRequest,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> KnowledgeRetrievalResult:
        return service.retrieve_knowledge(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            body,
        )

    @router.post(
        "/tenants/{tenant_id}/knowledge/qa",
        response_model=KnowledgeQAResult,
    )
    def answer_knowledge_question(
        tenant_id: UUID,
        body: KnowledgeQARequest,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> KnowledgeQAResult:
        return service.answer_question(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            body,
        )

    @router.post(
        "/tenants/{tenant_id}/learning/attachments/file",
        status_code=201,
        response_model=PersonalLearningAttachment,
        operation_id="uploadPersonalLearningFile",
    )
    def upload_personal_learning_file(
        tenant_id: UUID,
        file: Annotated[UploadFile, File(...)],
        title: Annotated[str | None, Form(max_length=200)] = None,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> PersonalLearningAttachment:
        return service.upload_personal_learning_file(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            read_bounded_bytes=lambda max_b: _read_bounded_upload(file, max_b),
            original_filename=file.filename,
            claimed_content_type=file.content_type,
            title=title,
        )

    @router.post(
        "/tenants/{tenant_id}/learning/attachments/text",
        status_code=201,
        response_model=PersonalLearningAttachment,
        operation_id="uploadPersonalLearningText",
    )
    def upload_personal_learning_text(
        tenant_id: UUID,
        body: PersonalLearningAttachmentTextRequest,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> PersonalLearningAttachment:
        return service.upload_personal_learning_text(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            body,
        )

    @router.get(
        "/tenants/{tenant_id}/learning/materials",
        response_model=Page[PersonalLearningAttachment],
        operation_id="listPersonalLearningMaterials",
    )
    def list_personal_learning_materials(
        tenant_id: UUID,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> Page[PersonalLearningAttachment]:
        return service.list_personal_learning_materials(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            limit=limit,
        )

    @router.delete(
        "/tenants/{tenant_id}/learning/materials/{attachment_id}",
        response_model=PersonalLearningDeleteResult,
        operation_id="deletePersonalLearningMaterial",
    )
    def delete_personal_learning_material(
        tenant_id: UUID,
        attachment_id: UUID,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> PersonalLearningDeleteResult:
        return service.delete_personal_learning_material(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            attachment_id,
        )

    @router.get(
        "/tenants/{tenant_id}/learning/sessions",
        response_model=Page[PersonalLearningSessionSummary],
        operation_id="listPersonalLearningSessions",
    )
    def list_personal_learning_sessions(
        tenant_id: UUID,
        limit: Annotated[int, Query(ge=1, le=100)] = 40,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> Page[PersonalLearningSessionSummary]:
        return service.list_personal_learning_sessions(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            limit=limit,
        )

    @router.post(
        "/tenants/{tenant_id}/learning/sessions",
        status_code=201,
        response_model=PersonalLearningSessionDetail,
        operation_id="createPersonalLearningSession",
    )
    def create_personal_learning_session(
        tenant_id: UUID,
        body: PersonalLearningSessionCreateRequest,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> PersonalLearningSessionDetail:
        return service.create_personal_learning_session(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            body,
        )

    @router.get(
        "/tenants/{tenant_id}/learning/sessions/{session_id}",
        response_model=PersonalLearningSessionDetail,
        operation_id="getPersonalLearningSession",
    )
    def get_personal_learning_session(
        tenant_id: UUID,
        session_id: UUID,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> PersonalLearningSessionDetail:
        return service.get_personal_learning_session(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            session_id,
        )

    @router.patch(
        "/tenants/{tenant_id}/learning/sessions/{session_id}",
        response_model=PersonalLearningSessionDetail,
        operation_id="updatePersonalLearningSession",
    )
    def update_personal_learning_session(
        tenant_id: UUID,
        session_id: UUID,
        body: PersonalLearningSessionUpdateRequest,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> PersonalLearningSessionDetail:
        return service.update_personal_learning_session(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            session_id,
            body,
        )

    @router.delete(
        "/tenants/{tenant_id}/learning/sessions/{session_id}",
        response_model=PersonalLearningDeleteResult,
        operation_id="deletePersonalLearningSession",
    )
    def delete_personal_learning_session(
        tenant_id: UUID,
        session_id: UUID,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> PersonalLearningDeleteResult:
        return service.delete_personal_learning_session(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            session_id,
        )

    @router.post(
        "/tenants/{tenant_id}/learning/ask",
        response_model=PersonalLearningResponse,
        operation_id="askPersonalLearning",
    )
    def ask_personal_learning(
        tenant_id: UUID,
        body: PersonalLearningRequest,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> PersonalLearningResponse:
        return service.ask_personal_learning(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            body,
        )

    @router.post(
        "/tenants/{tenant_id}/learning/resources/generate",
        response_model=PersonalLearningStudyResource,
        operation_id="generatePersonalLearningResource",
    )
    def generate_personal_learning_resource(
        tenant_id: UUID,
        body: PersonalLearningResourceGenerateRequest,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> PersonalLearningStudyResource:
        return service.generate_personal_learning_resource(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            body,
        )

    @router.get(
        "/tenants/{tenant_id}/learning/sessions/{session_id}/resources",
        response_model=Page[PersonalLearningStudyResource],
        operation_id="listPersonalLearningSessionResources",
    )
    def list_personal_learning_resources(
        tenant_id: UUID,
        session_id: UUID,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> Page[PersonalLearningStudyResource]:
        return service.list_personal_learning_resources(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            session_id,
        )

    @router.delete(
        "/tenants/{tenant_id}/learning/resources/{resource_id}",
        response_model=PersonalLearningDeleteResult,
        operation_id="deletePersonalLearningResource",
    )
    def delete_personal_learning_resource(
        tenant_id: UUID,
        resource_id: UUID,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> PersonalLearningDeleteResult:
        return service.delete_personal_learning_resource(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            resource_id,
        )

    @router.patch(
        "/tenants/{tenant_id}/learning/resources/{resource_id}/flashcards",
        response_model=PersonalLearningStudyResource,
        operation_id="updatePersonalLearningFlashcards",
    )
    def update_personal_learning_flashcards(
        tenant_id: UUID,
        resource_id: UUID,
        body: FlashcardProgressUpdateRequest,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> PersonalLearningStudyResource:
        return service.update_personal_learning_flashcards(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            resource_id,
            body,
        )

    @router.post(
        "/tenants/{tenant_id}/learning/resources/{resource_id}/quiz/submit",
        response_model=PracticeQuizAttemptRecord,
        operation_id="submitPersonalLearningQuiz",
    )
    def submit_personal_learning_quiz(
        tenant_id: UUID,
        resource_id: UUID,
        body: PracticeQuizSubmitRequest,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> PracticeQuizAttemptRecord:
        return service.submit_personal_learning_quiz(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            resource_id,
            body,
        )

    @router.post(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/blueprint/generate",
        status_code=202,
        response_model=LessonBlueprintTriggerResult,
    )
    def trigger_lesson_blueprint(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        body: LessonBlueprintTriggerRequest | None = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> LessonBlueprintTriggerResult:
        key = (
            body.idempotency_key
            if (body and body.idempotency_key)
            else idempotency_key
        )
        return service.trigger_lesson_blueprint(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            source_id,
            version_id,
            idempotency_key=key,
        )

    @router.get(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/blueprint",
        response_model=LessonBlueprintRecord,
    )
    def get_latest_blueprint(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> LessonBlueprintRecord:
        return service.get_latest_blueprint(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            source_id,
            version_id,
        )

    @router.get(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/blueprint/versions",
        response_model=Page[LessonBlueprintSummary],
    )
    def list_blueprint_versions(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> Page[LessonBlueprintSummary]:
        return service.list_blueprint_versions(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            source_id,
            version_id,
            limit=limit,
        )

    @router.post(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/blueprint/review",
        response_model=LessonBlueprintApprovalState,
    )
    def review_lesson_blueprint(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        body: LessonBlueprintApprovalRequest,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> LessonBlueprintApprovalState:
        return service.review_lesson_blueprint(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            source_id,
            version_id,
            body,
        )

    @router.get(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/blueprint/approval",
        response_model=LessonBlueprintApprovalState,
    )
    def get_blueprint_approval_state(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> LessonBlueprintApprovalState:
        return service.get_blueprint_approval_state(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            source_id,
            version_id,
        )

    @router.get(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/blueprint/published",
        response_model=PublishedLessonBlueprint,
    )
    def get_published_blueprint(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        creds: Annotated[
            HTTPAuthorizationCredentials | None, Security(bearer_scheme)
        ] = None,
    ) -> PublishedLessonBlueprint:
        return service.get_published_blueprint(
            access,  # type: ignore[arg-type]
            _token(creds),
            tenant_id,
            source_id,
            version_id,
        )

    return router
