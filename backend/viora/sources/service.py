"""Educational resource ingestion, version management, and extraction orchestration."""

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from ..contracts import ArtifactVersionRef, ResourceRef, TenantContext
from ..perf.public import OperationTracker
from ..security import Access, AccessError, TransactionalAccess
from ..storage.public import ArtifactStore, StorageError
from .document_contracts import ExtractedDocument
from .extraction import (
    AdaptiveDocumentExtractor,
    validate_and_sniff_text_input,
    validate_and_sniff_upload,
)
from .ports import (
    ConcurrentUniqueConflict,
    DocumentExtractor,
    SourceIngestionRepository,
    SourceRepository,
)
from .schemas import (
    SourceIngestionLimits,
    SourceInputFormat,
    SourceProcessingStatus,
    SourceRegistrationResult,
    SourceSummary,
    SourceVisibility,
    TextInputFormat,
)

logger = logging.getLogger("viora.sources")


def read_source(
    access: Access, repository: SourceRepository, token: str, tenant: UUID, source: UUID
) -> SourceSummary:
    """Preserve legacy Access-based service entrypoint for domain boundary tests."""
    context = access.require(
        token, "source.read", ResourceRef(tenant_id=tenant, kind="source", resource_id=source)
    )
    found = repository.find(context.tenant_id, source)
    if found is None:
        raise AccessError(404, "source_missing", "Source unavailable")
    return found


def build_active_dedupe_key(
    offering_id: UUID,
    origin_kind: str,
    owner_user_id: UUID,
    content_sha256: str,
) -> str:
    scope = "shared" if origin_kind == "teacher_resource" else str(owner_user_id)
    return f"{offering_id}:{origin_kind}:{scope}:{content_sha256}"


def _canonical_request_digest(**fields: object) -> str:
    encoded = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_initial_visibility(
    origin_kind: str, requested_visibility: SourceVisibility | None
) -> SourceVisibility:
    if origin_kind == "teacher_resource":
        vis: SourceVisibility = requested_visibility or "teacher_only"
        if vis not in {"teacher_only", "offering_published"}:
            raise AccessError(
                422,
                "invalid_visibility",
                "Teacher resource visibility must be teacher_only or offering_published",
            )
        return vis
    if origin_kind == "student_submission":
        vis = requested_visibility or "owner_private"
        if vis not in {"owner_private", "teacher_review"}:
            raise AccessError(
                422,
                "invalid_visibility",
                "Student submission visibility must be owner_private or teacher_review",
            )
        return vis
    raise AccessError(422, "invalid_origin", "Unsupported source origin_kind")


def _extract_and_validate_document(
    *,
    extractor: DocumentExtractor,
    tenant_id: UUID,
    source_id: UUID,
    version_id: UUID,
    source_version: int,
    source_sha256: str,
    input_format: SourceInputFormat,
    title: str,
    raw_bytes: bytes,
    limits: SourceIngestionLimits,
) -> tuple[ExtractedDocument, SourceProcessingStatus, float, dict[str, str | int | float | bool]]:
    stage_tracker = OperationTracker("source.extract", tenant_id=tenant_id)
    with stage_tracker.stage("adaptive_extract", input_bytes=len(raw_bytes)) as stage:
        doc = extractor.extract(
            tenant_id=tenant_id,
            source_id=source_id,
            version_id=version_id,
            source_version=source_version,
            source_sha256=source_sha256,
            input_format=input_format,
            title=title,
            raw_bytes=raw_bytes,
            limits=limits,
        )
        stage.output_bytes = len(doc.normalized_text.encode("utf-8"))
    metrics = stage_tracker.finish()
    extract_ms = metrics.total_duration_ms
    if len(doc.normalized_text) > limits.max_text_chars:
        raise AccessError(
            413,
            "text_limit_exceeded",
            f"Normalized text length {len(doc.normalized_text)} exceeds limit of {limits.max_text_chars}",
        )
    if not doc.quality.usable_for_digital_ingestion:
        if doc.quality.overall_status == "ocr_required":
            raise AccessError(
                422,
                "ocr_required",
                "Uploaded PDF contains no extractable digital text; scanned/OCR processing is deferred to Batch 2.3",
            )
        raise AccessError(
            422,
            "empty_input",
            "Source did not yield enough readable digital text",
        )
    status: SourceProcessingStatus = (
        "partial_extraction" if doc.quality.warning_count > 0 else "extracted"
    )
    meta: dict[str, str | int | float | bool] = {
        "overall_quality": doc.quality.overall_status,
        "digital_text_pages": doc.quality.digital_text_pages,
        "pages_needing_layout": doc.quality.pages_needing_layout,
        "pages_needing_table": doc.quality.pages_needing_table,
        "pages_needing_ocr": doc.quality.pages_needing_ocr,
        "pages_with_visuals": doc.quality.pages_with_visuals,
        "pages_uncertain": doc.quality.pages_uncertain,
        "reading_order_verified": doc.quality.reading_order_verified,
        "block_count": len(doc.text_blocks),
        "section_count": len(doc.sections),
        "figure_count": len(doc.figures),
        "table_count": len(doc.tables),
        "formula_count": len(doc.formulas),
    }
    return doc, status, extract_ms, meta


class SourceIngestionService:
    """Coordinates bounded validation, adaptive extraction, ArtifactStore I/O, and DB persistence."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        limits: SourceIngestionLimits,
        extractor: DocumentExtractor | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.limits = limits
        self.extractor: DocumentExtractor = extractor or AdaptiveDocumentExtractor()

    def _safe_compensate_uncommitted(
        self,
        *,
        access: TransactionalAccess,
        repo_factory: Callable[[Any], SourceIngestionRepository],
        bearer: str | None,
        tenant_id: UUID,
        version_id: UUID,
        verify_action: str,
        verify_ref: ResourceRef,
        stored_context: TenantContext,
        stored_ref: ArtifactVersionRef,
        persist_attempted: bool,
    ) -> int:
        if persist_attempted:
            try:
                with access.transaction(bearer, verify_action, verify_ref) as verify_scope:
                    verify_repo = repo_factory(verify_scope.session)
                    if verify_repo.has_version_id(
                        tenant_id=tenant_id, version_id=version_id
                    ):
                        logger.warning(
                            "Skipping artifact compensation because version exists in DB: tenant=%s version=%s",
                            tenant_id,
                            version_id,
                        )
                        return 0
            except Exception:  # noqa: BLE001 - fail-safe: never delete artifact on ambiguous DB verification
                logger.warning(
                    "Skipping artifact compensation after ambiguous DB commit outcome: tenant=%s version=%s",
                    tenant_id,
                    version_id,
                )
                return 0
        try:
            if self.artifact_store.delete_uncommitted(stored_context, stored_ref):
                return 1
        except (StorageError, OSError):
            logger.warning(
                "Failed compensating artifact cleanup for tenant=%s source=%s version=%s",
                tenant_id,
                stored_ref.resource_id,
                stored_ref.version_id,
            )
        return 0

    def register_text_source(
        self,
        *,
        access: TransactionalAccess,
        repo_factory: Callable[[Any], SourceIngestionRepository],
        bearer: str | None,
        tenant_id: UUID,
        offering_id: UUID,
        title: str,
        input_format: TextInputFormat,
        content: str,
        origin_kind: str,
        visibility: SourceVisibility | None,
        original_filename: str | None,
        idempotency_key: str | None,
        before_db_persist_hook: Callable[[], None] | None = None,
        after_db_commit_hook: Callable[[], None] | None = None,
    ) -> SourceRegistrationResult:
        action = (
            "source.teacher.create"
            if origin_kind == "teacher_resource"
            else "source.student.submit"
        )
        offering_ref = ResourceRef(
            tenant_id=tenant_id, kind="offering", resource_id=offering_id
        )
        resolved_visibility = _resolve_initial_visibility(origin_kind, visibility)

        def prepare() -> tuple[SourceInputFormat, bytes, str, str | None]:
            return validate_and_sniff_text_input(
                input_format=input_format,
                content=content,
                original_filename=original_filename,
                limits=self.limits,
            )

        return self._register_new_source_core(
            access=access,
            repo_factory=repo_factory,
            bearer=bearer,
            tenant_id=tenant_id,
            offering_id=offering_id,
            offering_ref=offering_ref,
            action=action,
            title=title,
            origin_kind=origin_kind,
            visibility=resolved_visibility,
            idempotency_key=idempotency_key,
            prepare_payload=prepare,
            before_db_persist_hook=before_db_persist_hook,
            after_db_commit_hook=after_db_commit_hook,
        )

    def register_file_source(
        self,
        *,
        access: TransactionalAccess,
        repo_factory: Callable[[Any], SourceIngestionRepository],
        bearer: str | None,
        tenant_id: UUID,
        offering_id: UUID,
        title: str,
        origin_kind: str,
        visibility: SourceVisibility | None,
        read_bounded_bytes: Callable[[int], bytes],
        declared_format: SourceInputFormat | None,
        original_filename: str | None,
        claimed_content_type: str | None,
        idempotency_key: str | None,
        before_db_persist_hook: Callable[[], None] | None = None,
        after_db_commit_hook: Callable[[], None] | None = None,
    ) -> SourceRegistrationResult:
        action = (
            "source.teacher.create"
            if origin_kind == "teacher_resource"
            else "source.student.submit"
        )
        offering_ref = ResourceRef(
            tenant_id=tenant_id, kind="offering", resource_id=offering_id
        )
        resolved_visibility = _resolve_initial_visibility(origin_kind, visibility)

        def prepare() -> tuple[SourceInputFormat, bytes, str, str | None]:
            raw_bytes = read_bounded_bytes(self.limits.max_bytes)
            return validate_and_sniff_upload(
                raw_bytes=raw_bytes,
                declared_format=declared_format,
                original_filename=original_filename,
                claimed_content_type=claimed_content_type,
                limits=self.limits,
            )

        return self._register_new_source_core(
            access=access,
            repo_factory=repo_factory,
            bearer=bearer,
            tenant_id=tenant_id,
            offering_id=offering_id,
            offering_ref=offering_ref,
            action=action,
            title=title,
            origin_kind=origin_kind,
            visibility=resolved_visibility,
            idempotency_key=idempotency_key,
            prepare_payload=prepare,
            before_db_persist_hook=before_db_persist_hook,
            after_db_commit_hook=after_db_commit_hook,
        )

    def _register_new_source_core(
        self,
        *,
        access: TransactionalAccess,
        repo_factory: Callable[[Any], SourceIngestionRepository],
        bearer: str | None,
        tenant_id: UUID,
        offering_id: UUID,
        offering_ref: ResourceRef,
        action: str,
        title: str,
        origin_kind: str,
        visibility: SourceVisibility,
        idempotency_key: str | None,
        prepare_payload: Callable[[], tuple[SourceInputFormat, bytes, str, str | None]],
        before_db_persist_hook: Callable[[], None] | None,
        after_db_commit_hook: Callable[[], None] | None,
    ) -> SourceRegistrationResult:
        tracker = OperationTracker("source.register", tenant_id=tenant_id)
        stored_context: TenantContext | None = None
        stored_ref: ArtifactVersionRef | None = None
        attempted_version_id: UUID | None = None
        captured_actor_id: UUID | None = None
        captured_req_digest: str | None = None
        persist_attempted = False
        committed = False
        orphans_cleaned = 0

        try:
            with access.transaction(bearer, action, offering_ref) as scope:
                repo = repo_factory(scope.session)
                actor_id = scope.context.actor_id
                captured_actor_id = actor_id

                with tracker.stage("validate_and_sniff") as st_val:
                    resolved_format, raw_bytes, content_sha256, safe_filename = (
                        prepare_payload()
                    )
                    st_val.input_bytes = len(raw_bytes)
                    st_val.output_bytes = len(raw_bytes)

                req_digest = _canonical_request_digest(
                    operation="create_source",
                    offering_id=str(offering_id),
                    origin_kind=origin_kind,
                    visibility=visibility,
                    title=title,
                    input_format=resolved_format,
                    filename=safe_filename,
                    content_sha256=content_sha256,
                )
                captured_req_digest = req_digest
                with tracker.stage("idempotency_and_dedupe"):
                    if idempotency_key is not None:
                        replay = repo.lookup_idempotency(
                            tenant_id=tenant_id,
                            offering_id=offering_id,
                            actor_id=actor_id,
                            target_scope_id=offering_id,
                            operation_kind="create_source",
                            idempotency_key=idempotency_key,
                            request_digest=req_digest,
                        )
                        if replay is not None:
                            return replay

                    dedupe_key = build_active_dedupe_key(
                        offering_id, origin_kind, actor_id, content_sha256
                    )
                    if repo.has_active_dedupe_key(
                        tenant_id=tenant_id, active_dedupe_key=dedupe_key
                    ):
                        raise AccessError(
                            409,
                            "duplicate_source",
                            "Identical active source content already exists in this scope",
                        )

                source_id = uuid4()
                version_id = uuid4()
                attempted_version_id = version_id
                extracted_doc, proc_status, extract_ms, proc_meta = (
                    _extract_and_validate_document(
                        extractor=self.extractor,
                        tenant_id=tenant_id,
                        source_id=source_id,
                        version_id=version_id,
                        source_version=1,
                        source_sha256=content_sha256,
                        input_format=resolved_format,
                        title=title,
                        raw_bytes=raw_bytes,
                        limits=self.limits,
                    )
                )

                content_type = (
                    "application/pdf"
                    if resolved_format == "digital_pdf"
                    else "text/plain; charset=utf-8"
                )
                with tracker.stage("artifact_store_put", input_bytes=len(raw_bytes)) as st_put:
                    try:
                        stored = self.artifact_store.put_bytes(
                            scope.context,
                            raw_bytes,
                            kind="source_input",
                            resource_id=source_id,
                            content_type=content_type,
                            artifact_id=source_id,
                            version_id=version_id,
                            max_bytes=self.limits.max_bytes,
                        )
                    except StorageError:
                        logger.warning(
                            "Artifact storage rejected source write for tenant=%s",
                            tenant_id,
                        )
                        raise AccessError(
                            503,
                            "storage_unavailable",
                            "Failed to store source artifact safely",
                        ) from None
                    stored_context = scope.context
                    stored_ref = stored.metadata.ref
                    st_put.output_bytes = stored.metadata.byte_size

                if before_db_persist_hook is not None:
                    before_db_persist_hook()

                now = datetime.now(UTC)
                with tracker.stage("db_persist"):
                    scope.recheck(action, offering_ref)
                    persist_attempted = True
                    src_summary, ver_summary = repo.create_source_and_version(
                        tenant_id=tenant_id,
                        source_id=source_id,
                        version_id=version_id,
                        offering_id=offering_id,
                        actor_id=actor_id,
                        title=title,
                        origin_kind=origin_kind,
                        visibility=visibility,
                        input_format=resolved_format,
                        original_filename=safe_filename,
                        content_sha256=content_sha256,
                        size_bytes=len(raw_bytes),
                        active_dedupe_key=dedupe_key,
                        storage_ref=stored.metadata.ref,
                        processing_status=proc_status,
                        extractor_id=extracted_doc.provenance.extractor_id,
                        contract_version=extracted_doc.schema_version,
                        extraction_ms=extract_ms,
                        char_count=len(extracted_doc.normalized_text),
                        page_count=max(1, extracted_doc.quality.total_pages),
                        warning_count=extracted_doc.quality.warning_count,
                        processing_metadata=proc_meta,
                        extracted_document=extracted_doc,
                        now=now,
                        idempotency_key=idempotency_key,
                        request_digest=req_digest if idempotency_key else None,
                    )
                    scope.audit.change(
                        scope.context,
                        f"{action}.registered",
                        ResourceRef(tenant_id=tenant_id, kind="source", resource_id=source_id),
                    )
            if after_db_commit_hook is not None:
                after_db_commit_hook()
            committed = True
        except ConcurrentUniqueConflict:
            if (
                stored_ref is not None
                and stored_context is not None
                and attempted_version_id is not None
            ):
                orphans_cleaned += self._safe_compensate_uncommitted(
                    access=access,
                    repo_factory=repo_factory,
                    bearer=bearer,
                    tenant_id=tenant_id,
                    version_id=attempted_version_id,
                    verify_action="source.offering.list",
                    verify_ref=offering_ref,
                    stored_context=stored_context,
                    stored_ref=stored_ref,
                    persist_attempted=True,
                )
            if (
                idempotency_key is not None
                and captured_actor_id is not None
                and captured_req_digest is not None
            ):
                with access.transaction(bearer, action, offering_ref) as replay_scope:
                    replay = repo_factory(replay_scope.session).lookup_idempotency(
                        tenant_id=tenant_id,
                        offering_id=offering_id,
                        actor_id=captured_actor_id,
                        target_scope_id=offering_id,
                        operation_kind="create_source",
                        idempotency_key=idempotency_key,
                        request_digest=captured_req_digest,
                    )
                    if replay is not None:
                        return replay.model_copy(
                            update={"orphans_cleaned": orphans_cleaned}
                        )
            raise AccessError(
                409,
                "duplicate_source",
                "Duplicate active source content or idempotency key conflict",
            ) from None
        except Exception:
            if (
                stored_ref is not None
                and stored_context is not None
                and attempted_version_id is not None
                and not committed
            ):
                orphans_cleaned += self._safe_compensate_uncommitted(
                    access=access,
                    repo_factory=repo_factory,
                    bearer=bearer,
                    tenant_id=tenant_id,
                    version_id=attempted_version_id,
                    verify_action="source.offering.list",
                    verify_ref=offering_ref,
                    stored_context=stored_context,
                    stored_ref=stored_ref,
                    persist_attempted=persist_attempted,
                )
            raise

        op_metrics = tracker.finish()
        logger.info("source.register", extra=tracker.to_log_dict())
        return SourceRegistrationResult(
            source=src_summary,
            version=ver_summary,
            quality=extracted_doc.quality,
            idempotent_replay=False,
            orphans_cleaned=orphans_cleaned,
            registration_ms=op_metrics.total_duration_ms,
        )

    def create_text_version(
        self,
        *,
        access: TransactionalAccess,
        repo_factory: Callable[[Any], SourceIngestionRepository],
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        input_format: TextInputFormat,
        content: str,
        original_filename: str | None,
        idempotency_key: str | None,
        before_db_persist_hook: Callable[[], None] | None = None,
        after_db_commit_hook: Callable[[], None] | None = None,
    ) -> SourceRegistrationResult:
        def prepare() -> tuple[SourceInputFormat, bytes, str, str | None]:
            return validate_and_sniff_text_input(
                input_format=input_format,
                content=content,
                original_filename=original_filename,
                limits=self.limits,
            )

        return self._create_version_core(
            access=access,
            repo_factory=repo_factory,
            bearer=bearer,
            tenant_id=tenant_id,
            source_id=source_id,
            idempotency_key=idempotency_key,
            prepare_payload=prepare,
            before_db_persist_hook=before_db_persist_hook,
            after_db_commit_hook=after_db_commit_hook,
        )

    def create_file_version(
        self,
        *,
        access: TransactionalAccess,
        repo_factory: Callable[[Any], SourceIngestionRepository],
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        read_bounded_bytes: Callable[[int], bytes],
        declared_format: SourceInputFormat | None,
        original_filename: str | None,
        claimed_content_type: str | None,
        idempotency_key: str | None,
        before_db_persist_hook: Callable[[], None] | None = None,
        after_db_commit_hook: Callable[[], None] | None = None,
    ) -> SourceRegistrationResult:
        def prepare() -> tuple[SourceInputFormat, bytes, str, str | None]:
            raw_bytes = read_bounded_bytes(self.limits.max_bytes)
            return validate_and_sniff_upload(
                raw_bytes=raw_bytes,
                declared_format=declared_format,
                original_filename=original_filename,
                claimed_content_type=claimed_content_type,
                limits=self.limits,
            )

        return self._create_version_core(
            access=access,
            repo_factory=repo_factory,
            bearer=bearer,
            tenant_id=tenant_id,
            source_id=source_id,
            idempotency_key=idempotency_key,
            prepare_payload=prepare,
            before_db_persist_hook=before_db_persist_hook,
            after_db_commit_hook=after_db_commit_hook,
        )

    def _create_version_core(
        self,
        *,
        access: TransactionalAccess,
        repo_factory: Callable[[Any], SourceIngestionRepository],
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        idempotency_key: str | None,
        prepare_payload: Callable[[], tuple[SourceInputFormat, bytes, str, str | None]],
        before_db_persist_hook: Callable[[], None] | None,
        after_db_commit_hook: Callable[[], None] | None,
    ) -> SourceRegistrationResult:
        tracker = OperationTracker("source.version.create", tenant_id=tenant_id)
        source_ref = ResourceRef(
            tenant_id=tenant_id, kind="source", resource_id=source_id
        )
        stored_context: TenantContext | None = None
        stored_ref: ArtifactVersionRef | None = None
        attempted_version_id: UUID | None = None
        persist_attempted = False
        committed = False
        orphans_cleaned = 0

        try:
            with access.transaction(bearer, "source.version.create", source_ref) as scope:
                repo = repo_factory(scope.session)
                actor_id = scope.context.actor_id
                locked = repo.get_source_access(tenant_id, source_id, lock=True)
                if locked is None or locked.offering_id is None:
                    raise AccessError(404, "source_missing", "Source unavailable")
                if locked.lifecycle_state != "active":
                    raise AccessError(
                        409,
                        "source_archived",
                        "Cannot create a new version on an archived source",
                    )

                source_summary = repo.find(tenant_id, source_id)
                if source_summary is None:
                    raise AccessError(404, "source_missing", "Source unavailable")

                with tracker.stage("validate_and_sniff") as st_val:
                    resolved_format, raw_bytes, content_sha256, safe_filename = (
                        prepare_payload()
                    )
                    st_val.input_bytes = len(raw_bytes)
                    st_val.output_bytes = len(raw_bytes)

                req_digest = _canonical_request_digest(
                    operation="create_version",
                    source_id=str(source_id),
                    input_format=resolved_format,
                    filename=safe_filename,
                    content_sha256=content_sha256,
                )
                with tracker.stage("idempotency_and_dedupe"):
                    if idempotency_key is not None:
                        replay = repo.lookup_idempotency(
                            tenant_id=tenant_id,
                            offering_id=locked.offering_id,
                            actor_id=actor_id,
                            target_scope_id=source_id,
                            operation_kind="create_version",
                            idempotency_key=idempotency_key,
                            request_digest=req_digest,
                        )
                        if replay is not None:
                            return replay

                    if repo.has_version_digest(
                        tenant_id=tenant_id,
                        source_id=source_id,
                        content_sha256=content_sha256,
                    ):
                        raise AccessError(
                            409,
                            "duplicate_version",
                            "This source already has an immutable version with identical content",
                        )

                    next_dedupe_key = build_active_dedupe_key(
                        locked.offering_id,
                        locked.origin_kind,
                        locked.owner_user_id or actor_id,
                        content_sha256,
                    )
                    if repo.has_active_dedupe_key(
                        tenant_id=tenant_id, active_dedupe_key=next_dedupe_key
                    ):
                        raise AccessError(
                            409,
                            "duplicate_source",
                            "Another active source in this scope already matches this content",
                        )

                version_id = uuid4()
                attempted_version_id = version_id
                next_ver_num = source_summary.latest_version_number + 1
                extracted_doc, proc_status, extract_ms, proc_meta = (
                    _extract_and_validate_document(
                        extractor=self.extractor,
                        tenant_id=tenant_id,
                        source_id=source_id,
                        version_id=version_id,
                        source_version=next_ver_num,
                        source_sha256=content_sha256,
                        input_format=resolved_format,
                        title=source_summary.title,
                        raw_bytes=raw_bytes,
                        limits=self.limits,
                    )
                )

                content_type = (
                    "application/pdf"
                    if resolved_format == "digital_pdf"
                    else "text/plain; charset=utf-8"
                )
                with tracker.stage("artifact_store_put", input_bytes=len(raw_bytes)) as st_put:
                    try:
                        stored = self.artifact_store.put_bytes(
                            scope.context,
                            raw_bytes,
                            kind="source_input",
                            resource_id=source_id,
                            content_type=content_type,
                            artifact_id=source_id,
                            version_id=version_id,
                            max_bytes=self.limits.max_bytes,
                        )
                    except StorageError:
                        logger.warning(
                            "Artifact storage rejected version write for tenant=%s",
                            tenant_id,
                        )
                        raise AccessError(
                            503,
                            "storage_unavailable",
                            "Failed to store source artifact safely",
                        ) from None
                    stored_context = scope.context
                    stored_ref = stored.metadata.ref
                    st_put.output_bytes = stored.metadata.byte_size

                if before_db_persist_hook is not None:
                    before_db_persist_hook()

                now = datetime.now(UTC)
                with tracker.stage("db_persist"):
                    scope.recheck("source.version.create", source_ref)
                    persist_attempted = True
                    src_summary, ver_summary = repo.append_version(
                        tenant_id=tenant_id,
                        source_id=source_id,
                        version_id=version_id,
                        offering_id=locked.offering_id,
                        actor_id=actor_id,
                        input_format=resolved_format,
                        original_filename=safe_filename,
                        content_sha256=content_sha256,
                        size_bytes=len(raw_bytes),
                        active_dedupe_key=next_dedupe_key,
                        storage_ref=stored.metadata.ref,
                        processing_status=proc_status,
                        extractor_id=extracted_doc.provenance.extractor_id,
                        contract_version=extracted_doc.schema_version,
                        extraction_ms=extract_ms,
                        char_count=len(extracted_doc.normalized_text),
                        page_count=max(1, extracted_doc.quality.total_pages),
                        warning_count=extracted_doc.quality.warning_count,
                        processing_metadata=proc_meta,
                        extracted_document=extracted_doc,
                        now=now,
                        idempotency_key=idempotency_key,
                        request_digest=req_digest if idempotency_key else None,
                    )
                    scope.audit.change(
                        scope.context,
                        "source.version.created",
                        source_ref,
                    )
            if after_db_commit_hook is not None:
                after_db_commit_hook()
            committed = True
        except Exception:
            if (
                stored_ref is not None
                and stored_context is not None
                and attempted_version_id is not None
                and not committed
            ):
                orphans_cleaned += self._safe_compensate_uncommitted(
                    access=access,
                    repo_factory=repo_factory,
                    bearer=bearer,
                    tenant_id=tenant_id,
                    version_id=attempted_version_id,
                    verify_action="source.read",
                    verify_ref=source_ref,
                    stored_context=stored_context,
                    stored_ref=stored_ref,
                    persist_attempted=persist_attempted,
                )
            raise

        op_metrics = tracker.finish()
        logger.info("source.version.create", extra=tracker.to_log_dict())
        return SourceRegistrationResult(
            source=src_summary,
            version=ver_summary,
            quality=extracted_doc.quality,
            idempotent_replay=False,
            orphans_cleaned=orphans_cleaned,
            registration_ms=op_metrics.total_duration_ms,
        )
