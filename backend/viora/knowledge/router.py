"""HTTP API router for educational concept extraction and retrieval."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..ai.public import AIGateway, build_ai_gateway_from_settings
from ..config import Settings
from ..contracts import Page
from ..db import Database
from ..security import Access, TransactionalAccess
from ..storage.public import ArtifactStore, local_artifact_store
from .embeddings import TextEmbedder, build_embedder_from_settings
from .repository import SqlConceptExtractionRepository
from .schemas import (
    ConceptExtractionRecord,
    ConceptExtractionTriggerRequest,
    ConceptExtractionTriggerResult,
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
    PublishedLessonBlueprint,
    PublishedLessonPackage,
)
from .service import ConceptKnowledgeService


def _token(bearer: HTTPAuthorizationCredentials | None) -> str | None:
    return bearer.credentials if bearer else None


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
    service = ConceptKnowledgeService(
        SqlConceptExtractionRepository,
        embedder=resolved_embedder,
        ai_gateway=resolved_gateway,
        artifact_store=resolved_store,
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
