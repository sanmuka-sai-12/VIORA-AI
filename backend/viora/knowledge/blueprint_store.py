"""PostgreSQL + ArtifactStore persistence for versioned lesson blueprints."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..contracts import ArtifactVersionRef, JobState, Page, TenantContext
from ..security import AccessError
from ..storage.public import ArtifactStore
from ..workflow.public import SqlWorkflowRepository, WorkflowJobSubmit
from .models import LessonBlueprintPublicationModel, LessonBlueprintRecordModel
from .schemas import (
    LessonBlueprint,
    LessonBlueprintApprovalState,
    LessonBlueprintRecord,
    LessonBlueprintSummary,
    PublicationReviewStatus,
    ReviewDecision,
)


def build_blueprint_input_fingerprint(
    *,
    concept_extraction_id: UUID,
    approved_review_id: UUID,
    source_sha256: str,
    outline_version: str,
    provider: str,
    model: str,
    prompt_version: str,
    schema_version: str,
) -> str:
    payload = json.dumps(
        {
            "concept_extraction_id": str(concept_extraction_id),
            "approved_review_id": str(approved_review_id),
            "source_sha256": source_sha256,
            "outline_version": outline_version,
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class SqlLessonBlueprintRepository:
    """Tenant-scoped append-only store for lesson blueprints backed by ArtifactStore."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _artifact_ref(row: LessonBlueprintRecordModel) -> ArtifactVersionRef:
        return ArtifactVersionRef(
            tenant_id=row.tenant_id,
            kind=row.storage_kind,
            resource_id=row.storage_resource_id,
            version_id=row.storage_version_id,
            sha256=row.storage_sha256,
        )

    @classmethod
    def _summary_dto(cls, row: LessonBlueprintRecordModel) -> LessonBlueprintSummary:
        return LessonBlueprintSummary(
            blueprint_id=row.blueprint_id,
            tenant_id=row.tenant_id,
            source_id=row.source_id,
            version_id=row.version_id,
            concept_extraction_id=row.concept_extraction_id,
            approved_review_id=row.approved_review_id,
            blueprint_version=row.blueprint_version,
            job_id=row.job_id,
            provider=row.provider,
            model=row.model,
            artifact_ref=cls._artifact_ref(row),
            byte_size=row.byte_size,
            created_at=row.created_at,
        )

    @classmethod
    def _record_dto(
        cls,
        row: LessonBlueprintRecordModel,
        *,
        artifact_store: ArtifactStore,
        context: TenantContext,
    ) -> LessonBlueprintRecord:
        ref = cls._artifact_ref(row)
        raw_bytes = artifact_store.read(context, ref)
        blueprint = LessonBlueprint.model_validate_json(raw_bytes)
        return LessonBlueprintRecord(
            blueprint_id=row.blueprint_id,
            tenant_id=row.tenant_id,
            source_id=row.source_id,
            version_id=row.version_id,
            derived_extraction_id=row.derived_extraction_id,
            concept_extraction_id=row.concept_extraction_id,
            approved_review_id=row.approved_review_id,
            blueprint_version=row.blueprint_version,
            job_id=row.job_id,
            provider=row.provider,
            model=row.model,
            prompt_version=row.prompt_version,
            schema_version=row.schema_version,
            outline_version=row.outline_version,
            input_fingerprint=row.input_fingerprint,
            source_sha256=row.source_sha256,
            artifact_ref=ref,
            byte_size=row.byte_size,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            total_tokens=row.total_tokens,
            inference_requests=row.inference_requests,
            generation_ms=row.generation_ms,
            validation_ms=row.validation_ms,
            blueprint=blueprint,
            created_at=row.created_at,
        )

    def enqueue_blueprint_job(
        self,
        *,
        tenant_id: UUID,
        offering_id: UUID | None,
        source_id: UUID,
        version_id: UUID,
        submitted_by_user_id: UUID,
        input_artifact: ArtifactVersionRef,
        idempotency_key: str | None,
        request_digest: str,
        now: datetime,
    ) -> tuple[UUID, JobState, bool]:
        job, replay = SqlWorkflowRepository(self.session).enqueue(
            WorkflowJobSubmit(
                tenant_id=tenant_id,
                offering_id=offering_id,
                source_id=source_id,
                version_id=version_id,
                submitted_by_user_id=submitted_by_user_id,
                operation_kind="lesson_blueprint",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                input_artifact=input_artifact,
            ),
            now=now,
        )
        return job.job_id, job.state, replay

    def get_latest_for_version(
        self,
        tenant_id: UUID,
        version_id: UUID,
        *,
        artifact_store: ArtifactStore,
        context: TenantContext,
        approved_review_id: UUID | None = None,
    ) -> LessonBlueprintRecord | None:
        stmt = select(LessonBlueprintRecordModel).where(
            LessonBlueprintRecordModel.tenant_id == tenant_id,
            LessonBlueprintRecordModel.version_id == version_id,
        )
        if approved_review_id is not None:
            stmt = stmt.where(
                LessonBlueprintRecordModel.approved_review_id == approved_review_id
            )
        row = self.session.scalar(
            stmt.order_by(LessonBlueprintRecordModel.blueprint_version.desc()).limit(1)
        )
        return (
            self._record_dto(row, artifact_store=artifact_store, context=context)
            if row is not None
            else None
        )

    def get_latest_summary(
        self, tenant_id: UUID, version_id: UUID
    ) -> LessonBlueprintSummary | None:
        row = self.session.scalar(
            select(LessonBlueprintRecordModel)
            .where(
                LessonBlueprintRecordModel.tenant_id == tenant_id,
                LessonBlueprintRecordModel.version_id == version_id,
            )
            .order_by(LessonBlueprintRecordModel.blueprint_version.desc())
            .limit(1)
        )
        return self._summary_dto(row) if row is not None else None

    def get_for_job(
        self,
        tenant_id: UUID,
        job_id: UUID,
        *,
        artifact_store: ArtifactStore,
        context: TenantContext,
    ) -> LessonBlueprintRecord | None:
        row = self.session.scalar(
            select(LessonBlueprintRecordModel).where(
                LessonBlueprintRecordModel.tenant_id == tenant_id,
                LessonBlueprintRecordModel.job_id == job_id,
            )
        )
        return (
            self._record_dto(row, artifact_store=artifact_store, context=context)
            if row is not None
            else None
        )

    def get_by_id(
        self,
        tenant_id: UUID,
        blueprint_id: UUID,
        *,
        artifact_store: ArtifactStore,
        context: TenantContext,
    ) -> LessonBlueprintRecord | None:
        row = self.session.scalar(
            select(LessonBlueprintRecordModel).where(
                LessonBlueprintRecordModel.tenant_id == tenant_id,
                LessonBlueprintRecordModel.blueprint_id == blueprint_id,
            )
        )
        return (
            self._record_dto(row, artifact_store=artifact_store, context=context)
            if row is not None
            else None
        )

    @staticmethod
    def _approval_dto(row: LessonBlueprintPublicationModel) -> LessonBlueprintApprovalState:
        status: PublicationReviewStatus = row.approval_status  # type: ignore[assignment]
        return LessonBlueprintApprovalState(
            tenant_id=row.tenant_id,
            source_id=row.source_id,
            version_id=row.version_id,
            blueprint_id=row.blueprint_id,
            concept_extraction_id=row.concept_extraction_id,
            approved_review_id=row.approved_review_id,
            approval_status=status,
            teacher_approved=status == "approved",
            published_to_students=bool(row.published_to_students and status == "approved"),
            reviewed_by_user_id=row.reviewed_by_user_id,
            review_notes=row.review_notes,
            reviewed_at=row.reviewed_at,
            published_by_user_id=row.published_by_user_id,
            published_at=row.published_at,
            updated_at=row.updated_at,
        )

    def get_publication_state(
        self, tenant_id: UUID, version_id: UUID
    ) -> LessonBlueprintApprovalState | None:
        row = self.session.scalar(
            select(LessonBlueprintPublicationModel).where(
                LessonBlueprintPublicationModel.tenant_id == tenant_id,
                LessonBlueprintPublicationModel.version_id == version_id,
            )
        )
        return self._approval_dto(row) if row is not None else None

    def review_and_publish(
        self,
        *,
        tenant_id: UUID,
        source_id: UUID,
        version_id: UUID,
        blueprint_id: UUID,
        concept_extraction_id: UUID,
        approved_review_id: UUID,
        source_sha256: str,
        decision: ReviewDecision,
        publish: bool,
        review_notes: str | None,
        actor_id: UUID,
        now: datetime,
    ) -> LessonBlueprintApprovalState:
        bp_row = self.session.scalar(
            select(LessonBlueprintRecordModel).where(
                LessonBlueprintRecordModel.tenant_id == tenant_id,
                LessonBlueprintRecordModel.blueprint_id == blueprint_id,
            )
        )
        if (
            bp_row is None
            or bp_row.source_id != source_id
            or bp_row.version_id != version_id
        ):
            raise AccessError(
                404,
                "lesson_blueprint_not_found",
                "Lesson blueprint version not found for this source version",
            )
        if (
            bp_row.concept_extraction_id != concept_extraction_id
            or bp_row.approved_review_id != approved_review_id
            or bp_row.source_sha256 != source_sha256
        ):
            raise AccessError(
                409,
                "blueprint_curriculum_mismatch",
                "Lesson blueprint does not match the currently approved curriculum version",
            )
        if publish and decision != "approved":
            raise AccessError(
                409,
                "teacher_approval_required",
                "Generated lesson blueprint requires teacher approval before publishing to students",
            )
        clean_notes = review_notes.strip() if review_notes and review_notes.strip() else None
        existing = self.session.scalar(
            select(LessonBlueprintPublicationModel)
            .where(
                LessonBlueprintPublicationModel.tenant_id == tenant_id,
                LessonBlueprintPublicationModel.version_id == version_id,
            )
            .with_for_update()
        )
        pub_flag = bool(publish and decision == "approved")
        if existing is None:
            row = LessonBlueprintPublicationModel(
                tenant_id=tenant_id,
                version_id=version_id,
                source_id=source_id,
                blueprint_id=blueprint_id,
                concept_extraction_id=concept_extraction_id,
                approved_review_id=approved_review_id,
                source_sha256=source_sha256,
                approval_status=decision,
                published_to_students=pub_flag,
                reviewed_by_user_id=actor_id,
                review_notes=clean_notes,
                reviewed_at=now,
                published_by_user_id=actor_id if pub_flag else None,
                published_at=now if pub_flag else None,
                updated_at=now,
            )
            self.session.add(row)
        else:
            existing.blueprint_id = blueprint_id
            existing.concept_extraction_id = concept_extraction_id
            existing.approved_review_id = approved_review_id
            existing.source_sha256 = source_sha256
            existing.approval_status = decision
            existing.published_to_students = pub_flag
            existing.reviewed_by_user_id = actor_id
            existing.review_notes = clean_notes
            existing.reviewed_at = now
            existing.published_by_user_id = actor_id if pub_flag else None
            existing.published_at = now if pub_flag else None
            existing.updated_at = now
            row = existing
        self.session.flush()
        return self._approval_dto(row)

    def revoke_publications_for_version(
        self, *, tenant_id: UUID, version_id: UUID, now: datetime
    ) -> None:
        row = self.session.scalar(
            select(LessonBlueprintPublicationModel)
            .where(
                LessonBlueprintPublicationModel.tenant_id == tenant_id,
                LessonBlueprintPublicationModel.version_id == version_id,
            )
            .with_for_update()
        )
        if row is not None and row.published_to_students:
            row.published_to_students = False
            row.published_by_user_id = None
            row.published_at = None
            row.updated_at = now
            self.session.flush()

    def list_for_version(
        self, tenant_id: UUID, version_id: UUID, *, limit: int = 20
    ) -> Page[LessonBlueprintSummary]:
        rows = self.session.scalars(
            select(LessonBlueprintRecordModel)
            .where(
                LessonBlueprintRecordModel.tenant_id == tenant_id,
                LessonBlueprintRecordModel.version_id == version_id,
            )
            .order_by(LessonBlueprintRecordModel.blueprint_version.desc())
            .limit(limit + 1)
        ).all()
        has_more = len(rows) > limit
        items = tuple(self._summary_dto(r) for r in rows[:limit])
        next_cursor = str(items[-1].blueprint_id) if has_more else None
        return Page(items=items, next_cursor=next_cursor)

    def list_storage_keys(self, tenant_id: UUID) -> set[tuple[UUID, UUID]]:
        rows = self.session.execute(
            select(
                LessonBlueprintRecordModel.storage_resource_id,
                LessonBlueprintRecordModel.storage_version_id,
            ).where(LessonBlueprintRecordModel.tenant_id == tenant_id)
        ).all()
        return {(row[0], row[1]) for row in rows}

    def append_blueprint(
        self,
        *,
        context: TenantContext,
        artifact_store: ArtifactStore,
        source_id: UUID,
        version_id: UUID,
        derived_extraction_id: UUID,
        concept_extraction_id: UUID,
        approved_review_id: UUID,
        job_id: UUID | None,
        provider: str,
        model: str,
        prompt_version: str,
        schema_version: str,
        outline_version: str,
        input_fingerprint: str,
        blueprint: LessonBlueprint,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        inference_requests: int,
        generation_ms: float,
        validation_ms: float,
        now: datetime,
    ) -> LessonBlueprintRecord:
        if job_id is not None:
            existing = self.get_for_job(
                context.tenant_id,
                job_id,
                artifact_store=artifact_store,
                context=context,
            )
            if existing is not None:
                return existing

        blueprint_id = uuid4()
        payload_bytes = blueprint.model_dump_json().encode("utf-8")
        stored = artifact_store.put_bytes(
            context,
            payload_bytes,
            kind="lesson_blueprint",
            resource_id=blueprint_id,
            content_type="application/json",
            artifact_id=blueprint_id,
            version_id=blueprint_id,
        )

        prev = self.session.scalar(
            select(LessonBlueprintRecordModel)
            .where(
                LessonBlueprintRecordModel.tenant_id == context.tenant_id,
                LessonBlueprintRecordModel.version_id == version_id,
            )
            .order_by(LessonBlueprintRecordModel.blueprint_version.desc())
            .limit(1)
        )
        next_ver = (prev.blueprint_version + 1) if prev is not None else 1
        row = LessonBlueprintRecordModel(
            tenant_id=context.tenant_id,
            blueprint_id=blueprint_id,
            source_id=source_id,
            version_id=version_id,
            derived_extraction_id=derived_extraction_id,
            concept_extraction_id=concept_extraction_id,
            approved_review_id=approved_review_id,
            job_id=job_id,
            blueprint_version=next_ver,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            schema_version=schema_version,
            outline_version=outline_version,
            input_fingerprint=input_fingerprint,
            source_sha256=blueprint.source_sha256,
            storage_kind=stored.metadata.ref.kind,
            storage_resource_id=stored.metadata.ref.resource_id,
            storage_version_id=stored.metadata.ref.version_id,
            storage_sha256=stored.metadata.ref.sha256,
            byte_size=stored.metadata.byte_size,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            inference_requests=inference_requests,
            generation_ms=generation_ms,
            validation_ms=validation_ms,
            created_at=now,
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            artifact_store.delete_uncommitted(context, stored.metadata.ref)
            sqlstate = getattr(exc.orig, "sqlstate", None)
            if sqlstate == "23505" and job_id is not None:
                existing = self.get_for_job(
                    context.tenant_id,
                    job_id,
                    artifact_store=artifact_store,
                    context=context,
                )
                if existing is not None:
                    return existing
            if sqlstate == "23505":
                raise AccessError(
                    409,
                    "concurrent_lesson_blueprint",
                    "Concurrent lesson blueprint conflict",
                ) from None
            raise
        except Exception:
            artifact_store.delete_uncommitted(context, stored.metadata.ref)
            raise
        return self._record_dto(row, artifact_store=artifact_store, context=context)
