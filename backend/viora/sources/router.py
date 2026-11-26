"""HTTP API router for versioned source ingestion, metadata, and extraction retrieval."""

import contextlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, Query, Response, Security, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import Settings
from ..contracts import Page, ResourceRef
from ..institution.public import offering_actor_access
from ..security import Access, AccessError, TransactionalAccess
from ..storage.public import ArtifactStore, local_artifact_store
from ..workflow.public import (
    DurableWorkflowService,
    ExtractionTriggerRequest,
    SqlWorkflowRepository,
    WorkflowJobDetail,
    WorkflowJobSummary,
)
from .document_contracts import DerivedExtractionRecord
from .repository import SqlSourceRepository
from .schemas import (
    OfferingSourcePolicyInfo,
    OfferingSourcePolicyUpdateRequest,
    SourceInputFormat,
    SourceLifecycleState,
    SourceMetadataUpdateRequest,
    SourceOriginKind,
    SourceRegistrationResult,
    SourceSummary,
    SourceTextRegisterRequest,
    SourceVersionDetail,
    SourceVersionSummary,
    SourceVersionTextRequest,
    SourceVisibility,
    clamp_source_limits,
)
from .service import SourceIngestionService, read_source


def _token(bearer: HTTPAuthorizationCredentials | None) -> str | None:
    return bearer.credentials if bearer else None


def _resolve_idempotency_key(
    body_key: str | None, header_key: str | None
) -> str | None:
    h_clean = header_key.strip() if header_key is not None else None
    if h_clean == "":
        h_clean = None
    b_clean = body_key.strip() if body_key is not None else None
    if b_clean == "":
        b_clean = None
    if h_clean is not None and b_clean is not None and h_clean != b_clean:
        raise AccessError(
            422,
            "idempotency_key_mismatch",
            "Conflicting idempotency keys in header and request payload",
        )
    chosen = h_clean if h_clean is not None else b_clean
    if chosen is not None and len(chosen) > 128:
        raise AccessError(
            422,
            "invalid_idempotency_key",
            "Idempotency key must not exceed 128 characters",
        )
    return chosen


def _read_bounded_upload(upload: UploadFile, max_bytes: int) -> bytes:
    """Read an uploaded stream in fixed chunks and abort immediately if limits are exceeded."""
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
    access: Access, repository: Callable[[], Iterator[SqlSourceRepository]]
) -> APIRouter:
    """Preserve Phase 0 unit router contract for isolated Access testing."""
    router = APIRouter(tags=["sources"])
    bearer_dep = Security(HTTPBearer(auto_error=False))

    @router.get("/tenants/{tenant_id}/sources/{source_id}", operation_id="readSource")
    def get_source(
        tenant_id: UUID,
        source_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SourceSummary:
        with repository() as repo:
            return read_source(
                access, repo, _token(bearer) or "", tenant_id, source_id
            )

    return router


def build_protected_router(
    access: TransactionalAccess,
    *,
    settings: Settings | None = None,
    artifact_store: ArtifactStore | None = None,
) -> APIRouter:
    router = APIRouter(tags=["sources"])
    bearer_dep = Security(HTTPBearer(auto_error=False))
    limits = (
        clamp_source_limits(
            max_bytes=settings.source_max_bytes,
            max_pdf_pages=settings.source_max_pdf_pages,
            max_text_chars=settings.source_max_text_chars,
            min_text_chars=settings.source_min_text_chars,
        )
        if settings is not None
        else clamp_source_limits()
    )
    store = artifact_store or local_artifact_store(
        settings.media_storage_dir if settings is not None else "./storage",
        max_artifact_bytes=limits.max_bytes,
    )
    ingestion = SourceIngestionService(store, limits)

    @router.post(
        "/tenants/{tenant_id}/offerings/{offering_id}/sources",
        status_code=201,
        operation_id="registerTextSource",
    )
    def register_text_source(
        tenant_id: UUID,
        offering_id: UUID,
        body: SourceTextRegisterRequest,
        response: Response,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        idempotency_header: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ) -> SourceRegistrationResult:
        idemp_key = _resolve_idempotency_key(body.idempotency_key, idempotency_header)
        result = ingestion.register_text_source(
            access=access,
            repo_factory=SqlSourceRepository,
            bearer=_token(bearer),
            tenant_id=tenant_id,
            offering_id=offering_id,
            title=body.title,
            input_format=body.input_format,
            content=body.content,
            origin_kind=body.origin_kind,
            visibility=body.visibility,
            original_filename=body.original_filename,
            idempotency_key=idemp_key,
        )
        response.status_code = 200 if result.idempotent_replay else 201
        return result

    @router.post(
        "/tenants/{tenant_id}/offerings/{offering_id}/sources/files",
        status_code=201,
        operation_id="registerFileSource",
    )
    def register_file_source(
        tenant_id: UUID,
        offering_id: UUID,
        title: Annotated[str, Form(min_length=1, max_length=300)],
        file: Annotated[UploadFile, File()],
        response: Response,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        origin_kind: Annotated[SourceOriginKind, Form()] = "teacher_resource",
        visibility: Annotated[SourceVisibility | None, Form()] = None,
        input_format: Annotated[SourceInputFormat | None, Form()] = None,
        idempotency_key: Annotated[str | None, Form(max_length=128)] = None,
        idempotency_header: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ) -> SourceRegistrationResult:
        clean_title = title.strip()
        if not clean_title:
            raise AccessError(422, "invalid_title", "Source title cannot be blank")
        idemp_key = _resolve_idempotency_key(idempotency_key, idempotency_header)
        result = ingestion.register_file_source(
            access=access,
            repo_factory=SqlSourceRepository,
            bearer=_token(bearer),
            tenant_id=tenant_id,
            offering_id=offering_id,
            title=clean_title,
            origin_kind=origin_kind,
            visibility=visibility,
            read_bounded_bytes=lambda max_b: _read_bounded_upload(file, max_b),
            declared_format=input_format,
            original_filename=file.filename,
            claimed_content_type=file.content_type,
            idempotency_key=idemp_key,
        )
        response.status_code = 200 if result.idempotent_replay else 201
        return result

    @router.get(
        "/tenants/{tenant_id}/offerings/{offering_id}/sources",
        operation_id="listOfferingSources",
    )
    def list_offering_sources(
        tenant_id: UUID,
        offering_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        origin_kind: SourceOriginKind | None = None,
        lifecycle_state: SourceLifecycleState | None = None,
        cursor: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> Page[SourceSummary]:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="offering", resource_id=offering_id
        )
        with access.transaction(_token(bearer), "source.offering.list", ref) as scope:
            repo = SqlSourceRepository(scope.session)
            actor_access = offering_actor_access(
                scope.session, tenant_id, scope.context.actor_id, offering_id
            )
            policy_cfg = repo.get_offering_policy(tenant_id, offering_id)
            return repo.list_offering_sources(
                tenant_id,
                offering_id,
                actor_id=scope.context.actor_id,
                is_assigned_teacher=actor_access.is_assigned_teacher,
                is_enrolled_student=actor_access.is_enrolled_student,
                teachers_can_view_student_submissions=policy_cfg.teachers_can_view_student_submissions,
                origin_kind=origin_kind,
                lifecycle_state=lifecycle_state,
                cursor=cursor,
                limit=limit,
            )

    @router.get("/tenants/{tenant_id}/sources/{source_id}", operation_id="readSource")
    def get_source(
        tenant_id: UUID,
        source_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SourceSummary:
        resource = ResourceRef(tenant_id=tenant_id, kind="source", resource_id=source_id)
        with access.transaction(_token(bearer), "source.read", resource) as scope:
            result = SqlSourceRepository(scope.session).find(tenant_id, source_id)
            if result is None:
                raise AccessError(404, "source_missing", "Source unavailable")
            return result

    @router.patch(
        "/tenants/{tenant_id}/sources/{source_id}",
        operation_id="updateSourceMetadata",
    )
    def update_source_metadata(
        tenant_id: UUID,
        source_id: UUID,
        body: SourceMetadataUpdateRequest,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SourceSummary:
        if body.title is None and body.visibility is None and body.lifecycle_state is None:
            raise AccessError(
                422, "empty_update", "At least one metadata field must be provided"
            )
        action = (
            "source.lifecycle"
            if (
                body.lifecycle_state is not None
                and body.title is None
                and body.visibility is None
            )
            else "source.update"
        )
        resource = ResourceRef(tenant_id=tenant_id, kind="source", resource_id=source_id)
        with access.transaction(_token(bearer), action, resource) as scope:
            repo = SqlSourceRepository(scope.session)
            repo.get_source_access(tenant_id, source_id, lock=True)
            scope.recheck(action, resource)
            updated = repo.update_source_metadata(
                tenant_id=tenant_id,
                source_id=source_id,
                title=body.title,
                visibility=body.visibility,
                lifecycle_state=body.lifecycle_state,
                now=datetime.now(UTC),
            )
            if updated is None:
                raise AccessError(404, "source_missing", "Source unavailable")
            scope.audit.change(scope.context, f"{action}.updated", resource)
            return updated

    @router.get(
        "/tenants/{tenant_id}/sources/{source_id}/versions",
        operation_id="listSourceVersions",
    )
    def list_source_versions(
        tenant_id: UUID,
        source_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        cursor: Annotated[int | None, Query(ge=1)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> Page[SourceVersionSummary]:
        resource = ResourceRef(tenant_id=tenant_id, kind="source", resource_id=source_id)
        with access.transaction(_token(bearer), "source.version.read", resource) as scope:
            return SqlSourceRepository(scope.session).list_versions(
                tenant_id, source_id, cursor=cursor, limit=limit
            )

    @router.get(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}",
        operation_id="readSourceVersion",
    )
    def read_source_version(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> SourceVersionDetail:
        resource = ResourceRef(tenant_id=tenant_id, kind="source", resource_id=source_id)
        with access.transaction(_token(bearer), "source.version.read", resource) as scope:
            detail = SqlSourceRepository(scope.session).get_version(
                tenant_id, source_id, version_id
            )
            if detail is None:
                raise AccessError(
                    404, "version_missing", "Source version unavailable"
                )
            return detail

    @router.post(
        "/tenants/{tenant_id}/sources/{source_id}/versions",
        status_code=201,
        operation_id="createTextSourceVersion",
    )
    def create_text_source_version(
        tenant_id: UUID,
        source_id: UUID,
        body: SourceVersionTextRequest,
        response: Response,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        idempotency_header: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ) -> SourceRegistrationResult:
        idemp_key = _resolve_idempotency_key(body.idempotency_key, idempotency_header)
        result = ingestion.create_text_version(
            access=access,
            repo_factory=SqlSourceRepository,
            bearer=_token(bearer),
            tenant_id=tenant_id,
            source_id=source_id,
            input_format=body.input_format,
            content=body.content,
            original_filename=body.original_filename,
            idempotency_key=idemp_key,
        )
        response.status_code = 200 if result.idempotent_replay else 201
        return result

    @router.post(
        "/tenants/{tenant_id}/sources/{source_id}/versions/files",
        status_code=201,
        operation_id="createFileSourceVersion",
    )
    def create_file_source_version(
        tenant_id: UUID,
        source_id: UUID,
        file: Annotated[UploadFile, File()],
        response: Response,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        input_format: Annotated[SourceInputFormat | None, Form()] = None,
        idempotency_key: Annotated[str | None, Form(max_length=128)] = None,
        idempotency_header: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ) -> SourceRegistrationResult:
        idemp_key = _resolve_idempotency_key(idempotency_key, idempotency_header)
        result = ingestion.create_file_version(
            access=access,
            repo_factory=SqlSourceRepository,
            bearer=_token(bearer),
            tenant_id=tenant_id,
            source_id=source_id,
            read_bounded_bytes=lambda max_b: _read_bounded_upload(file, max_b),
            declared_format=input_format,
            original_filename=file.filename,
            claimed_content_type=file.content_type,
            idempotency_key=idemp_key,
        )
        response.status_code = 200 if result.idempotent_replay else 201
        return result

    @router.get(
        "/tenants/{tenant_id}/offerings/{offering_id}/source-policy",
        operation_id="readOfferingSourcePolicy",
    )
    def read_offering_source_policy(
        tenant_id: UUID,
        offering_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> OfferingSourcePolicyInfo:
        ref = ResourceRef(
            tenant_id=tenant_id, kind="offering", resource_id=offering_id
        )
        with access.transaction(_token(bearer), "source.policy.read", ref) as scope:
            return SqlSourceRepository(scope.session).get_offering_policy(
                tenant_id, offering_id
            )

    @router.patch(
        "/tenants/{tenant_id}/offerings/{offering_id}/source-policy",
        operation_id="updateOfferingSourcePolicy",
    )
    def update_offering_source_policy(
        tenant_id: UUID,
        offering_id: UUID,
        body: OfferingSourcePolicyUpdateRequest,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> OfferingSourcePolicyInfo:
        if (
            body.student_submissions_enabled is None
            and body.teachers_can_view_student_submissions is None
        ):
            raise AccessError(
                422, "empty_update", "At least one policy setting must be provided"
            )
        ref = ResourceRef(
            tenant_id=tenant_id, kind="offering", resource_id=offering_id
        )
        with access.transaction(_token(bearer), "source.policy.update", ref) as scope:
            scope.recheck("source.policy.update", ref)
            updated = SqlSourceRepository(scope.session).upsert_offering_policy(
                tenant_id,
                offering_id,
                actor_id=scope.context.actor_id,
                student_submissions_enabled=body.student_submissions_enabled,
                teachers_can_view_student_submissions=body.teachers_can_view_student_submissions,
            )
            scope.audit.change(scope.context, "source.policy.updated", ref)
            return updated

    @router.get(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/extraction",
        operation_id="readLatestDerivedExtraction",
    )
    def read_latest_derived_extraction(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> DerivedExtractionRecord:
        resource = ResourceRef(tenant_id=tenant_id, kind="source", resource_id=source_id)
        with access.transaction(_token(bearer), "source.version.read", resource) as scope:
            rec = SqlSourceRepository(scope.session).get_latest_derived_extraction(
                tenant_id, source_id, version_id
            )
            if rec is None:
                raise AccessError(
                    404,
                    "extraction_missing",
                    "No derived extraction is available yet for this source version",
                )
            return rec

    @router.get(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/extractions",
        operation_id="listDerivedExtractions",
    )
    def list_derived_extractions(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> Page[DerivedExtractionRecord]:
        resource = ResourceRef(tenant_id=tenant_id, kind="source", resource_id=source_id)
        with access.transaction(_token(bearer), "source.version.read", resource) as scope:
            return SqlSourceRepository(scope.session).list_derived_extractions(
                tenant_id, source_id, version_id, limit=limit
            )

    @router.post(
        "/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/extract",
        status_code=202,
        operation_id="triggerVersionExtraction",
    )
    def trigger_version_extraction(
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        body: ExtractionTriggerRequest | None = None,
        idempotency_header: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ) -> WorkflowJobDetail:
        idemp_key = _resolve_idempotency_key(
            body.idempotency_key if body is not None else None, idempotency_header
        )
        return ingestion.trigger_extraction(
            access=access,
            repo_factory=SqlSourceRepository,
            bearer=_token(bearer),
            tenant_id=tenant_id,
            source_id=source_id,
            version_id=version_id,
            idempotency_key=idemp_key,
        )

    @router.get(
        "/tenants/{tenant_id}/sources/{source_id}/jobs",
        operation_id="listSourceJobs",
    )
    def list_source_jobs(
        tenant_id: UUID,
        source_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> Page[WorkflowJobSummary]:
        return DurableWorkflowService.list_source_jobs(
            access,
            SqlWorkflowRepository,
            bearer=_token(bearer),
            tenant_id=tenant_id,
            source_id=source_id,
            limit=limit,
        )

    @router.get(
        "/tenants/{tenant_id}/jobs/{job_id}",
        operation_id="readWorkflowJob",
    )
    def read_workflow_job(
        tenant_id: UUID,
        job_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> WorkflowJobDetail:
        return DurableWorkflowService.get_job(
            access,
            SqlWorkflowRepository,
            bearer=_token(bearer),
            tenant_id=tenant_id,
            job_id=job_id,
        )

    @router.post(
        "/tenants/{tenant_id}/jobs/{job_id}/cancel",
        operation_id="cancelWorkflowJob",
    )
    def cancel_workflow_job(
        tenant_id: UUID,
        job_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> WorkflowJobDetail:
        return DurableWorkflowService.cancel_job(
            access,
            SqlWorkflowRepository,
            bearer=_token(bearer),
            tenant_id=tenant_id,
            job_id=job_id,
        )

    @router.post(
        "/tenants/{tenant_id}/jobs/{job_id}/retry",
        operation_id="retryWorkflowJob",
    )
    def retry_workflow_job(
        tenant_id: UUID,
        job_id: UUID,
        bearer: Annotated[HTTPAuthorizationCredentials | None, bearer_dep],
    ) -> WorkflowJobDetail:
        return DurableWorkflowService.retry_job(
            access,
            SqlWorkflowRepository,
            bearer=_token(bearer),
            tenant_id=tenant_id,
            job_id=job_id,
        )

    return router
