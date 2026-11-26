"""SQLAlchemy repositories for source directory lookups and versioned ingestion."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..contracts import ArtifactVersionRef, Page
from ..security import AccessError
from .document_contracts import ExtractedDocument
from .models import OfferingSourcePolicy, Source, SourceIdempotencyRecord, SourceVersion
from .ports import ConcurrentUniqueConflict
from .schemas import (
    OfferingSourcePolicyInfo,
    SourceAccessInfo,
    SourceInputFormat,
    SourceProcessingStatus,
    SourceRegistrationResult,
    SourceSummary,
    SourceVersionDetail,
    SourceVersionSummary,
)


def build_active_dedupe_key(
    offering_id: UUID,
    origin_kind: str,
    owner_user_id: UUID,
    content_sha256: str,
) -> str:
    """Scope deduplication cleanly: per-offering for official resources, per-owner for student work."""
    scope = "shared" if origin_kind == "teacher_resource" else str(owner_user_id)
    return f"{offering_id}:{origin_kind}:{scope}:{content_sha256}"


def _source_dto(row: Source, latest: SourceVersion | None = None) -> SourceSummary:
    return SourceSummary(
        source_id=row.source_id,
        title=row.title,
        tenant_id=row.tenant_id,
        offering_id=row.offering_id,
        creator_user_id=row.creator_user_id,
        owner_user_id=row.owner_user_id,
        origin_kind=row.origin_kind,
        visibility=row.visibility,
        lifecycle_state=row.lifecycle_state,
        latest_version_number=row.latest_version_number,
        latest_version_id=row.latest_version_id,
        latest_input_format=latest.input_format if latest else None,
        latest_digest=latest.content_sha256 if latest else None,
        latest_size_bytes=latest.size_bytes if latest else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _version_summary_dto(row: SourceVersion) -> SourceVersionSummary:
    return SourceVersionSummary(
        tenant_id=row.tenant_id,
        source_id=row.source_id,
        version_id=row.version_id,
        version_number=row.version_number,
        created_by_user_id=row.created_by_user_id,
        input_format=row.input_format,  # type: ignore[arg-type]
        original_filename=row.original_filename,
        content_sha256=row.content_sha256,
        size_bytes=row.size_bytes,
        storage_ref=ArtifactVersionRef(
            tenant_id=row.tenant_id,
            kind=row.storage_kind,
            resource_id=row.storage_resource_id,
            version_id=row.storage_version_id,
            sha256=row.storage_sha256,
        ),
        processing_status=row.processing_status,  # type: ignore[arg-type]
        extractor_id=row.extractor_id,
        contract_version=row.contract_version,
        extraction_ms=row.extraction_ms,
        char_count=row.char_count,
        page_count=row.page_count,
        warning_count=row.warning_count,
        processing_metadata=dict(row.processing_metadata),
        created_at=row.created_at,
    )


class SqlSourceDirectory:
    """Internal authorization reader executed inside the tenant-bound READ COMMITTED scope."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def source_access_info(
        self, tenant_id: UUID, source_id: UUID
    ) -> SourceAccessInfo | None:
        row = self.session.execute(
            select(
                Source.tenant_id,
                Source.source_id,
                Source.offering_id,
                Source.creator_user_id,
                Source.owner_user_id,
                Source.origin_kind,
                Source.visibility,
                Source.lifecycle_state,
            ).where(Source.tenant_id == tenant_id, Source.source_id == source_id)
        ).one_or_none()
        if row is None:
            return None
        return SourceAccessInfo(
            tenant_id=row.tenant_id,
            source_id=row.source_id,
            offering_id=row.offering_id,
            creator_user_id=row.creator_user_id,
            owner_user_id=row.owner_user_id,
            origin_kind=row.origin_kind,
            visibility=row.visibility,
            lifecycle_state=row.lifecycle_state,
        )

    def offering_source_policy(
        self, tenant_id: UUID, offering_id: UUID
    ) -> OfferingSourcePolicyInfo:
        row = self.session.execute(
            select(
                OfferingSourcePolicy.tenant_id,
                OfferingSourcePolicy.offering_id,
                OfferingSourcePolicy.student_submissions_enabled,
                OfferingSourcePolicy.teachers_can_view_student_submissions,
                OfferingSourcePolicy.updated_by_user_id,
                OfferingSourcePolicy.updated_at,
            ).where(
                OfferingSourcePolicy.tenant_id == tenant_id,
                OfferingSourcePolicy.offering_id == offering_id,
            )
        ).one_or_none()
        if row is None:
            return OfferingSourcePolicyInfo(
                tenant_id=tenant_id,
                offering_id=offering_id,
                student_submissions_enabled=True,
                teachers_can_view_student_submissions=True,
            )
        return OfferingSourcePolicyInfo(
            tenant_id=row.tenant_id,
            offering_id=row.offering_id,
            student_submissions_enabled=row.student_submissions_enabled,
            teachers_can_view_student_submissions=row.teachers_can_view_student_submissions,
            updated_by_user_id=row.updated_by_user_id,
            updated_at=row.updated_at,
        )


class SqlSourceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find(self, tenant_id: UUID, source_id: UUID) -> SourceSummary | None:
        row = self.session.scalar(
            select(Source)
            .where(Source.tenant_id == tenant_id, Source.source_id == source_id)
            .execution_options(populate_existing=True)
        )
        if row is None:
            return None
        latest = None
        if row.latest_version_id is not None:
            latest = self.session.scalar(
                select(SourceVersion).where(
                    SourceVersion.tenant_id == tenant_id,
                    SourceVersion.version_id == row.latest_version_id,
                )
            )
        return _source_dto(row, latest)

    def get_source_access(
        self, tenant_id: UUID, source_id: UUID, *, lock: bool = False
    ) -> SourceAccessInfo | None:
        stmt = select(Source).where(
            Source.tenant_id == tenant_id, Source.source_id == source_id
        )
        if lock:
            stmt = stmt.with_for_update()
        row = self.session.scalar(stmt.execution_options(populate_existing=True))
        if row is None:
            return None
        return SourceAccessInfo(
            tenant_id=row.tenant_id,
            source_id=row.source_id,
            offering_id=row.offering_id,
            creator_user_id=row.creator_user_id,
            owner_user_id=row.owner_user_id,
            origin_kind=row.origin_kind,
            visibility=row.visibility,
            lifecycle_state=row.lifecycle_state,
        )

    def get_version(
        self, tenant_id: UUID, source_id: UUID, version_id: UUID
    ) -> SourceVersionDetail | None:
        row = self.session.scalar(
            select(SourceVersion).where(
                SourceVersion.tenant_id == tenant_id,
                SourceVersion.source_id == source_id,
                SourceVersion.version_id == version_id,
            )
        )
        if row is None:
            return None
        summary = _version_summary_dto(row)
        return SourceVersionDetail(
            **summary.model_dump(),
            extracted_document=ExtractedDocument.model_validate(row.extracted_document),
        )

    def list_versions(
        self,
        tenant_id: UUID,
        source_id: UUID,
        *,
        cursor: int | None,
        limit: int,
    ) -> Page[SourceVersionSummary]:
        stmt = select(SourceVersion).where(
            SourceVersion.tenant_id == tenant_id,
            SourceVersion.source_id == source_id,
        )
        if cursor is not None:
            stmt = stmt.where(SourceVersion.version_number < cursor)
        rows = self.session.scalars(
            stmt.order_by(SourceVersion.version_number.desc()).limit(limit + 1)
        ).all()
        has_more = len(rows) > limit
        slice_rows = rows[:limit]
        next_cursor = str(slice_rows[-1].version_number) if has_more else None
        return Page(
            items=tuple(_version_summary_dto(item) for item in slice_rows),
            next_cursor=next_cursor,
        )

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
    ) -> Page[SourceSummary]:
        access_clauses = []
        if is_assigned_teacher:
            access_clauses.append(Source.origin_kind == "teacher_resource")
            if teachers_can_view_student_submissions:
                access_clauses.append(
                    and_(
                        Source.origin_kind == "student_submission",
                        Source.visibility == "teacher_review",
                        Source.lifecycle_state == "active",
                    )
                )
        if is_enrolled_student:
            access_clauses.append(
                and_(
                    Source.origin_kind == "teacher_resource",
                    Source.visibility == "offering_published",
                    Source.lifecycle_state == "active",
                )
            )
            access_clauses.append(
                and_(
                    Source.origin_kind == "student_submission",
                    Source.owner_user_id == actor_id,
                )
            )
        if not access_clauses:
            return Page(items=(), next_cursor=None)

        stmt = select(Source).where(
            Source.tenant_id == tenant_id,
            Source.offering_id == offering_id,
            Source.origin_kind != "unscoped",
            or_(*access_clauses),
        )
        if origin_kind is not None:
            stmt = stmt.where(Source.origin_kind == origin_kind)
        if lifecycle_state is not None:
            stmt = stmt.where(Source.lifecycle_state == lifecycle_state)
        if cursor is not None:
            stmt = stmt.where(Source.source_id > cursor)
        rows = self.session.scalars(
            stmt.order_by(Source.source_id.asc()).limit(limit + 1)
        ).all()
        has_more = len(rows) > limit
        slice_rows = rows[:limit]
        version_ids = [r.latest_version_id for r in slice_rows if r.latest_version_id is not None]
        versions_by_id: dict[UUID, SourceVersion] = {}
        if version_ids:
            for ver in self.session.scalars(
                select(SourceVersion).where(
                    SourceVersion.tenant_id == tenant_id,
                    SourceVersion.version_id.in_(version_ids),
                )
            ).all():
                versions_by_id[ver.version_id] = ver
        next_cursor = str(slice_rows[-1].source_id) if has_more else None
        return Page(
            items=tuple(
                _source_dto(
                    r,
                    versions_by_id.get(r.latest_version_id)
                    if r.latest_version_id
                    else None,
                )
                for r in slice_rows
            ),
            next_cursor=next_cursor,
        )

    def get_offering_policy(
        self, tenant_id: UUID, offering_id: UUID
    ) -> OfferingSourcePolicyInfo:
        return SqlSourceDirectory(self.session).offering_source_policy(
            tenant_id, offering_id
        )

    def upsert_offering_policy(
        self,
        tenant_id: UUID,
        offering_id: UUID,
        *,
        actor_id: UUID,
        student_submissions_enabled: bool | None,
        teachers_can_view_student_submissions: bool | None,
    ) -> OfferingSourcePolicyInfo:
        row = self.session.scalar(
            select(OfferingSourcePolicy)
            .where(
                OfferingSourcePolicy.tenant_id == tenant_id,
                OfferingSourcePolicy.offering_id == offering_id,
            )
            .with_for_update()
        )
        now = datetime.now(UTC)
        if row is None:
            row = OfferingSourcePolicy(
                tenant_id=tenant_id,
                offering_id=offering_id,
                student_submissions_enabled=(
                    True
                    if student_submissions_enabled is None
                    else student_submissions_enabled
                ),
                teachers_can_view_student_submissions=(
                    True
                    if teachers_can_view_student_submissions is None
                    else teachers_can_view_student_submissions
                ),
                updated_by_user_id=actor_id,
                updated_at=now,
            )
            self.session.add(row)
        else:
            if student_submissions_enabled is not None:
                row.student_submissions_enabled = student_submissions_enabled
            if teachers_can_view_student_submissions is not None:
                row.teachers_can_view_student_submissions = (
                    teachers_can_view_student_submissions
                )
            row.updated_by_user_id = actor_id
            row.updated_at = now
        self.session.flush()
        return OfferingSourcePolicyInfo(
            tenant_id=row.tenant_id,
            offering_id=row.offering_id,
            student_submissions_enabled=row.student_submissions_enabled,
            teachers_can_view_student_submissions=row.teachers_can_view_student_submissions,
            updated_by_user_id=row.updated_by_user_id,
            updated_at=row.updated_at,
        )

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
    ) -> SourceRegistrationResult | None:
        rec = self.session.scalar(
            select(SourceIdempotencyRecord).where(
                SourceIdempotencyRecord.tenant_id == tenant_id,
                SourceIdempotencyRecord.offering_id == offering_id,
                SourceIdempotencyRecord.actor_user_id == actor_id,
                SourceIdempotencyRecord.target_scope_id == target_scope_id,
                SourceIdempotencyRecord.operation_kind == operation_kind,
                SourceIdempotencyRecord.idempotency_key == idempotency_key,
            )
        )
        if rec is None:
            return None
        if rec.request_digest != request_digest:
            raise AccessError(
                409,
                "idempotency_conflict",
                "Idempotency key already used for a different source payload",
            )
        src_row = self.session.scalar(
            select(Source).where(
                Source.tenant_id == tenant_id, Source.source_id == rec.source_id
            )
        )
        ver_row = self.session.scalar(
            select(SourceVersion).where(
                SourceVersion.tenant_id == tenant_id,
                SourceVersion.version_id == rec.version_id,
            )
        )
        if src_row is None or ver_row is None:
            raise AccessError(
                409,
                "idempotency_unavailable",
                "Idempotent source registration state could not be resolved",
            )
        doc = ExtractedDocument.model_validate(ver_row.extracted_document)
        return SourceRegistrationResult(
            source=_source_dto(src_row, ver_row),
            version=_version_summary_dto(ver_row),
            quality=doc.quality,
            idempotent_replay=True,
            orphans_cleaned=0,
            registration_ms=0.0,
        )

    def has_active_dedupe_key(
        self, *, tenant_id: UUID, active_dedupe_key: str
    ) -> bool:
        found = self.session.scalar(
            select(Source.source_id).where(
                Source.tenant_id == tenant_id,
                Source.active_dedupe_key == active_dedupe_key,
            )
        )
        return found is not None

    def has_version_digest(
        self, *, tenant_id: UUID, source_id: UUID, content_sha256: str
    ) -> bool:
        found = self.session.scalar(
            select(SourceVersion.version_id).where(
                SourceVersion.tenant_id == tenant_id,
                SourceVersion.source_id == source_id,
                SourceVersion.content_sha256 == content_sha256,
            )
        )
        return found is not None

    def has_version_id(self, *, tenant_id: UUID, version_id: UUID) -> bool:
        found = self.session.scalar(
            select(SourceVersion.version_id).where(
                SourceVersion.tenant_id == tenant_id,
                SourceVersion.version_id == version_id,
            )
        )
        return found is not None

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
        extracted_document: ExtractedDocument,
        now: datetime,
        idempotency_key: str | None,
        request_digest: str | None,
    ) -> tuple[SourceSummary, SourceVersionSummary]:
        source = Source(
            tenant_id=tenant_id,
            source_id=source_id,
            title=title,
            offering_id=offering_id,
            creator_user_id=actor_id,
            owner_user_id=actor_id,
            origin_kind=origin_kind,
            visibility=visibility,
            lifecycle_state="active",
            latest_version_number=1,
            latest_version_id=version_id,
            active_dedupe_key=active_dedupe_key,
            created_at=now,
            updated_at=now,
        )
        version = SourceVersion(
            tenant_id=tenant_id,
            version_id=version_id,
            source_id=source_id,
            version_number=1,
            created_by_user_id=actor_id,
            input_format=input_format,
            original_filename=original_filename,
            content_sha256=content_sha256,
            size_bytes=size_bytes,
            storage_kind=storage_ref.kind,
            storage_resource_id=storage_ref.resource_id,
            storage_version_id=storage_ref.version_id,
            storage_sha256=storage_ref.sha256,
            processing_status=processing_status,
            extractor_id=extractor_id,
            contract_version=contract_version,
            extraction_ms=extraction_ms,
            char_count=char_count,
            page_count=page_count,
            warning_count=warning_count,
            processing_metadata=dict(processing_metadata),
            extracted_document=extracted_document.model_dump(mode="json"),
            created_at=now,
        )
        self.session.add(source)
        try:
            self.session.flush()
            self.session.add(version)
            self.session.flush()
            if idempotency_key is not None and request_digest is not None:
                self.session.add(
                    SourceIdempotencyRecord(
                        tenant_id=tenant_id,
                        offering_id=offering_id,
                        actor_user_id=actor_id,
                        target_scope_id=offering_id,
                        operation_kind="create_source",
                        idempotency_key=idempotency_key,
                        request_digest=request_digest,
                        source_id=source_id,
                        version_id=version_id,
                        created_at=now,
                    )
                )
                self.session.flush()
        except IntegrityError as exc:
            sqlstate = getattr(exc.orig, "sqlstate", None)
            if sqlstate == "23505":
                if idempotency_key is not None and request_digest is not None:
                    raise ConcurrentUniqueConflict() from None
                raise AccessError(
                    409,
                    "duplicate_source",
                    "Duplicate active source content or idempotency key conflict",
                ) from None
            raise
        return _source_dto(source, version), _version_summary_dto(version)

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
        extracted_document: ExtractedDocument,
        now: datetime,
        idempotency_key: str | None,
        request_digest: str | None,
    ) -> tuple[SourceSummary, SourceVersionSummary]:
        source = self.session.scalar(
            select(Source)
            .where(Source.tenant_id == tenant_id, Source.source_id == source_id)
            .with_for_update()
        )
        if source is None:
            raise AccessError(404, "source_missing", "Source unavailable")
        if source.lifecycle_state != "active":
            raise AccessError(
                409,
                "source_archived",
                "Cannot create a new version on an archived source",
            )
        next_num = source.latest_version_number + 1
        version = SourceVersion(
            tenant_id=tenant_id,
            version_id=version_id,
            source_id=source_id,
            version_number=next_num,
            created_by_user_id=actor_id,
            input_format=input_format,
            original_filename=original_filename,
            content_sha256=content_sha256,
            size_bytes=size_bytes,
            storage_kind=storage_ref.kind,
            storage_resource_id=storage_ref.resource_id,
            storage_version_id=storage_ref.version_id,
            storage_sha256=storage_ref.sha256,
            processing_status=processing_status,
            extractor_id=extractor_id,
            contract_version=contract_version,
            extraction_ms=extraction_ms,
            char_count=char_count,
            page_count=page_count,
            warning_count=warning_count,
            processing_metadata=dict(processing_metadata),
            extracted_document=extracted_document.model_dump(mode="json"),
            created_at=now,
        )
        source.latest_version_number = next_num
        source.latest_version_id = version_id
        source.active_dedupe_key = active_dedupe_key
        source.updated_at = now
        self.session.add(version)
        try:
            self.session.flush()
            if idempotency_key is not None and request_digest is not None:
                self.session.add(
                    SourceIdempotencyRecord(
                        tenant_id=tenant_id,
                        offering_id=offering_id,
                        actor_user_id=actor_id,
                        target_scope_id=source_id,
                        operation_kind="create_version",
                        idempotency_key=idempotency_key,
                        request_digest=request_digest,
                        source_id=source_id,
                        version_id=version_id,
                        created_at=now,
                    )
                )
                self.session.flush()
        except IntegrityError as exc:
            sqlstate = getattr(exc.orig, "sqlstate", None)
            if sqlstate == "23505":
                raise AccessError(
                    409,
                    "duplicate_version",
                    "Identical version digest or concurrent version conflict",
                ) from None
            raise
        return _source_dto(source, version), _version_summary_dto(version)

    def update_source_metadata(
        self,
        *,
        tenant_id: UUID,
        source_id: UUID,
        title: str | None,
        visibility: str | None,
        lifecycle_state: str | None,
        now: datetime,
    ) -> SourceSummary | None:
        source = self.session.scalar(
            select(Source)
            .where(Source.tenant_id == tenant_id, Source.source_id == source_id)
            .with_for_update()
        )
        if source is None:
            return None
        if title is not None:
            source.title = title
        if visibility is not None:
            if source.origin_kind == "teacher_resource" and visibility not in {
                "teacher_only",
                "offering_published",
            }:
                raise AccessError(
                    422,
                    "invalid_visibility",
                    "Teacher resource visibility must be teacher_only or offering_published",
                )
            if source.origin_kind == "student_submission" and visibility not in {
                "owner_private",
                "teacher_review",
            }:
                raise AccessError(
                    422,
                    "invalid_visibility",
                    "Student submission visibility must be owner_private or teacher_review",
                )
            source.visibility = visibility
        if lifecycle_state is not None and lifecycle_state != source.lifecycle_state:
            source.lifecycle_state = lifecycle_state
            if lifecycle_state == "archived":
                source.active_dedupe_key = None
            elif (
                source.offering_id is not None
                and source.owner_user_id is not None
                and source.latest_version_id is not None
            ):
                sha = self.session.scalar(
                    select(SourceVersion.content_sha256).where(
                        SourceVersion.tenant_id == tenant_id,
                        SourceVersion.version_id == source.latest_version_id,
                    )
                )
                if sha is not None:
                    source.active_dedupe_key = build_active_dedupe_key(
                        source.offering_id,
                        source.origin_kind,
                        source.owner_user_id,
                        sha,
                    )
        source.updated_at = now
        try:
            self.session.flush()
        except IntegrityError as exc:
            sqlstate = getattr(exc.orig, "sqlstate", None)
            if sqlstate == "23505":
                raise AccessError(
                    409,
                    "duplicate_source",
                    "Cannot reactivate source because active duplicate content already exists",
                ) from None
            raise
        latest = None
        if source.latest_version_id is not None:
            latest = self.session.scalar(
                select(SourceVersion).where(
                    SourceVersion.tenant_id == tenant_id,
                    SourceVersion.version_id == source.latest_version_id,
                )
            )
        return _source_dto(source, latest)
