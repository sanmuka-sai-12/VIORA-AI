"""PostgreSQL repositories for durable lease-fenced workflow jobs."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..contracts import ArtifactVersionRef, JobState, Page
from ..security import AccessError
from .models import WorkflowJob
from .ports import ConcurrentJobConflict, JobCancelledError, StaleWorkerFenceError
from .schemas import (
    ClaimedWorkflowJob,
    LeaseRenewalResult,
    WorkflowJobDetail,
    WorkflowJobSubmit,
    WorkflowJobSummary,
)

_SENSITIVE_ERROR_RE = re.compile(
    r"(?:[A-Za-z]:\\|/(?:home|Users|var|etc|tmp|app|srv)/|\bpassword\b|\btoken\b|\bsecret\b|\bpostgresql\+psycopg://)",
    re.IGNORECASE,
)


def sanitize_error_message(raw: str | None) -> str:
    """Return a bounded, non-sensitive diagnostic message safe for API/DB storage."""
    if not raw:
        return "Processing failed"
    single = re.sub(r"\s+", " ", raw).strip()
    if _SENSITIVE_ERROR_RE.search(single):
        return "Processing failed due to an internal or access condition"
    return single[:256]


def _artifact_ref(row: WorkflowJob) -> ArtifactVersionRef:
    return ArtifactVersionRef(
        tenant_id=row.tenant_id,
        kind=row.input_artifact_kind,
        resource_id=row.input_artifact_resource_id,
        version_id=row.input_artifact_version_id,
        sha256=row.input_artifact_sha256,
    )


def _job_summary_dto(row: WorkflowJob) -> WorkflowJobSummary:
    return WorkflowJobSummary(
        job_id=row.job_id,
        tenant_id=row.tenant_id,
        offering_id=row.offering_id,
        source_id=row.source_id,
        version_id=row.version_id,
        submitted_by_user_id=row.submitted_by_user_id,
        operation_kind=row.operation_kind,  # type: ignore[arg-type]
        state=JobState(row.state),
        stage=row.stage,
        progress_metadata=dict(row.progress_metadata or {}),
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        next_run_at=row.next_run_at,
        cancel_requested=row.cancel_requested,
        result_extraction_id=row.result_extraction_id,
        result_metadata=dict(row.result_metadata or {}),
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        updated_at=row.updated_at,
    )


def _job_detail_dto(
    row: WorkflowJob, *, idempotent_replay: bool = False
) -> WorkflowJobDetail:
    summary = _job_summary_dto(row)
    return WorkflowJobDetail(
        **summary.model_dump(),
        fencing_token=row.fencing_token,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        heartbeat_at=row.heartbeat_at,
        input_artifact=_artifact_ref(row),
        idempotent_replay=idempotent_replay,
    )


class SqlWorkflowDirectory:
    """Authorization directory lookup executed inside tenant-bound READ COMMITTED scopes."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def job_source_id(self, tenant_id: UUID, job_id: UUID) -> UUID | None:
        return self.session.scalar(
            select(WorkflowJob.source_id).where(
                WorkflowJob.tenant_id == tenant_id,
                WorkflowJob.job_id == job_id,
            )
        )


class SqlWorkflowRepository:
    """Tenant-scoped job submission, inspection, cancellation, and retry repository."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def lookup_idempotency(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        operation_kind: str,
        idempotency_key: str,
        request_digest: str | None,
    ) -> WorkflowJobDetail | None:
        existing = self.session.scalar(
            select(WorkflowJob).where(
                WorkflowJob.tenant_id == tenant_id,
                WorkflowJob.submitted_by_user_id == actor_id,
                WorkflowJob.operation_kind == operation_kind,
                WorkflowJob.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            return None
        if (
            request_digest is not None
            and existing.request_digest is not None
            and existing.request_digest != request_digest
        ):
            raise AccessError(
                409,
                "idempotency_conflict",
                "Idempotency key already used for a different workflow request",
            )
        return _job_detail_dto(existing, idempotent_replay=True)

    def find_active_for_version(
        self,
        *,
        tenant_id: UUID,
        version_id: UUID,
        operation_kind: str,
    ) -> WorkflowJobDetail | None:
        active = self.session.scalar(
            select(WorkflowJob).where(
                WorkflowJob.tenant_id == tenant_id,
                WorkflowJob.version_id == version_id,
                WorkflowJob.operation_kind == operation_kind,
                WorkflowJob.state.in_(("queued", "running", "retry_wait")),
            )
        )
        return _job_detail_dto(active, idempotent_replay=True) if active is not None else None

    def enqueue(
        self, request: WorkflowJobSubmit, *, now: datetime
    ) -> tuple[WorkflowJobDetail, bool]:
        if request.idempotency_key is not None:
            replay = self.lookup_idempotency(
                tenant_id=request.tenant_id,
                actor_id=request.submitted_by_user_id,
                operation_kind=request.operation_kind,
                idempotency_key=request.idempotency_key,
                request_digest=request.request_digest,
            )
            if replay is not None:
                return replay, True

        active = self.find_active_for_version(
            tenant_id=request.tenant_id,
            version_id=request.version_id,
            operation_kind=request.operation_kind,
        )
        if active is not None:
            return active, True

        job = WorkflowJob(
            tenant_id=request.tenant_id,
            job_id=uuid4(),
            offering_id=request.offering_id,
            source_id=request.source_id,
            version_id=request.version_id,
            submitted_by_user_id=request.submitted_by_user_id,
            operation_kind=request.operation_kind,
            idempotency_key=request.idempotency_key,
            request_digest=request.request_digest,
            input_artifact_kind=request.input_artifact.kind,
            input_artifact_resource_id=request.input_artifact.resource_id,
            input_artifact_version_id=request.input_artifact.version_id,
            input_artifact_sha256=request.input_artifact.sha256,
            state="queued",
            stage="queued",
            progress_metadata={"stage": "queued"},
            checkpoint_data=None,
            attempt_count=0,
            max_attempts=request.max_attempts,
            next_run_at=now,
            fencing_token=0,
            cancel_requested=False,
            result_metadata={},
            created_at=now,
            updated_at=now,
        )
        self.session.add(job)
        try:
            self.session.flush()
        except IntegrityError as exc:
            sqlstate = getattr(exc.orig, "sqlstate", None)
            if sqlstate == "23505":
                raise ConcurrentJobConflict() from None
            raise
        return _job_detail_dto(job, idempotent_replay=False), False

    def get_job(self, tenant_id: UUID, job_id: UUID) -> WorkflowJobDetail | None:
        row = self.session.scalar(
            select(WorkflowJob)
            .where(WorkflowJob.tenant_id == tenant_id, WorkflowJob.job_id == job_id)
            .execution_options(populate_existing=True)
        )
        return _job_detail_dto(row) if row is not None else None

    def get_latest_job_for_version(
        self, tenant_id: UUID, version_id: UUID
    ) -> WorkflowJobSummary | None:
        row = self.session.scalar(
            select(WorkflowJob)
            .where(
                WorkflowJob.tenant_id == tenant_id,
                WorkflowJob.version_id == version_id,
            )
            .order_by(WorkflowJob.created_at.desc())
            .limit(1)
        )
        return _job_summary_dto(row) if row is not None else None

    def get_latest_jobs_for_versions(
        self, tenant_id: UUID, version_ids: list[UUID]
    ) -> dict[UUID, WorkflowJobSummary]:
        if not version_ids:
            return {}
        rows = self.session.scalars(
            select(WorkflowJob)
            .ext(distinct_on(WorkflowJob.version_id))
            .where(
                WorkflowJob.tenant_id == tenant_id,
                WorkflowJob.version_id.in_(version_ids),
            )
            .order_by(WorkflowJob.version_id, WorkflowJob.created_at.desc())
        ).all()
        return {row.version_id: _job_summary_dto(row) for row in rows}

    def list_source_jobs(
        self,
        tenant_id: UUID,
        source_id: UUID,
        *,
        limit: int = 25,
    ) -> Page[WorkflowJobSummary]:
        rows = self.session.scalars(
            select(WorkflowJob)
            .where(
                WorkflowJob.tenant_id == tenant_id,
                WorkflowJob.source_id == source_id,
            )
            .order_by(WorkflowJob.created_at.desc())
            .limit(limit + 1)
        ).all()
        has_more = len(rows) > limit
        items = tuple(_job_summary_dto(r) for r in rows[:limit])
        next_cursor = str(items[-1].job_id) if has_more else None
        return Page(items=items, next_cursor=next_cursor)

    def request_cancel(
        self, tenant_id: UUID, job_id: UUID, *, now: datetime
    ) -> WorkflowJobDetail | None:
        row = self.session.scalar(
            select(WorkflowJob)
            .where(WorkflowJob.tenant_id == tenant_id, WorkflowJob.job_id == job_id)
            .with_for_update()
        )
        if row is None:
            return None
        if row.state == "cancelled":
            return _job_detail_dto(row, idempotent_replay=True)
        if row.state in {"succeeded", "failed"}:
            raise AccessError(
                409,
                "job_not_cancellable",
                f"Cannot cancel job in terminal state '{row.state}'",
            )
        row.cancel_requested = True
        row.updated_at = now
        if row.state in {"queued", "retry_wait"}:
            row.state = "cancelled"
            row.stage = "cancelled"
            row.completed_at = now
            row.lease_owner = None
            row.lease_expires_at = None
        self.session.flush()
        return _job_detail_dto(row)

    def request_retry(
        self, tenant_id: UUID, job_id: UUID, *, now: datetime
    ) -> WorkflowJobDetail | None:
        row = self.session.scalar(
            select(WorkflowJob)
            .where(WorkflowJob.tenant_id == tenant_id, WorkflowJob.job_id == job_id)
            .with_for_update()
        )
        if row is None:
            return None
        if row.state in {"queued", "running", "retry_wait"}:
            raise AccessError(
                409,
                "job_already_active",
                f"Job is already active in state '{row.state}'",
            )
        if row.state == "succeeded":
            raise AccessError(
                409,
                "job_already_succeeded",
                "Completed job cannot be retried; trigger a new extraction if needed",
            )
        active = self.find_active_for_version(
            tenant_id=tenant_id,
            version_id=row.version_id,
            operation_kind=row.operation_kind,
        )
        if active is not None:
            raise AccessError(
                409,
                "job_already_active",
                "Another active extraction job already exists for this source version",
            )
        row.state = "queued"
        row.stage = "queued"
        row.cancel_requested = False
        row.attempt_count = 0
        row.next_run_at = now
        row.lease_owner = None
        row.lease_expires_at = None
        row.heartbeat_at = None
        row.last_error_code = None
        row.last_error_message = None
        row.completed_at = None
        row.progress_metadata = {"stage": "queued", "manual_retry": True}
        row.updated_at = now
        self.session.flush()
        return _job_detail_dto(row)


class SqlWorkflowWorkerStore:
    """Low-level lease claiming, heartbeat renewal, and fenced state transitions."""

    @staticmethod
    def claim_next_job(
        session: Session,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime,
    ) -> ClaimedWorkflowJob | None:
        session.execute(
            text("SELECT set_config('viora.worker_id', :wid, true)"),
            {"wid": worker_id},
        )
        for _ in range(16):
            row = session.scalar(
                select(WorkflowJob)
                .where(
                    or_(
                        and_(
                            WorkflowJob.state.in_(("queued", "retry_wait")),
                            WorkflowJob.next_run_at <= now,
                            WorkflowJob.cancel_requested.is_(False),
                        ),
                        and_(
                            WorkflowJob.state == "running",
                            WorkflowJob.lease_expires_at.is_not(None),
                            WorkflowJob.lease_expires_at <= now,
                        ),
                    )
                )
                .order_by(WorkflowJob.next_run_at.asc(), WorkflowJob.created_at.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            if row.cancel_requested:
                row.state = "cancelled"
                row.stage = "cancelled"
                row.fencing_token += 1
                row.lease_owner = None
                row.lease_expires_at = None
                row.completed_at = now
                row.updated_at = now
                session.flush()
                continue
            if row.state == "running" and row.attempt_count >= row.max_attempts:
                row.state = "failed"
                row.stage = "failed"
                row.fencing_token += 1
                row.lease_owner = None
                row.lease_expires_at = None
                row.completed_at = now
                row.last_error_code = "lease_expired"
                row.last_error_message = (
                    "Worker lease expired after maximum retry attempts"
                )
                row.updated_at = now
                session.flush()
                continue

            row.state = "running"
            row.stage = "claimed"
            row.attempt_count += 1
            row.fencing_token += 1
            row.lease_owner = worker_id
            row.heartbeat_at = now
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            if row.started_at is None:
                row.started_at = now
            row.updated_at = now
            session.flush()
            return ClaimedWorkflowJob(
                job_id=row.job_id,
                tenant_id=row.tenant_id,
                offering_id=row.offering_id,
                source_id=row.source_id,
                version_id=row.version_id,
                submitted_by_user_id=row.submitted_by_user_id,
                operation_kind=row.operation_kind,  # type: ignore[arg-type]
                attempt_count=row.attempt_count,
                max_attempts=row.max_attempts,
                fencing_token=row.fencing_token,
                lease_owner=worker_id,
                lease_expires_at=row.lease_expires_at,
                input_artifact=_artifact_ref(row),
                checkpoint_data=dict(row.checkpoint_data)
                if row.checkpoint_data is not None
                else None,
                created_at=row.created_at,
                started_at=row.started_at,
            )
        return None

    @staticmethod
    def renew_lease(
        session: Session,
        *,
        tenant_id: UUID,
        job_id: UUID,
        worker_id: str,
        fencing_token: int,
        lease_seconds: int,
        stage: str,
        progress_metadata: dict[str, str | int | float | bool],
        checkpoint_data: dict[str, object] | None,
        now: datetime,
    ) -> LeaseRenewalResult:
        row = session.scalar(
            select(WorkflowJob)
            .where(
                WorkflowJob.tenant_id == tenant_id,
                WorkflowJob.job_id == job_id,
                WorkflowJob.state == "running",
                WorkflowJob.lease_owner == worker_id,
                WorkflowJob.fencing_token == fencing_token,
            )
            .with_for_update()
        )
        if row is None:
            return LeaseRenewalResult(renewed=False, cancel_requested=False)
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
        row.heartbeat_at = now
        row.stage = stage
        row.progress_metadata = dict(progress_metadata)
        if checkpoint_data is not None:
            row.checkpoint_data = dict(checkpoint_data)
        row.updated_at = now
        session.flush()
        return LeaseRenewalResult(
            renewed=True, cancel_requested=bool(row.cancel_requested)
        )

    @staticmethod
    def finalize_succeeded(
        session: Session,
        *,
        tenant_id: UUID,
        job_id: UUID,
        worker_id: str,
        fencing_token: int,
        extraction_id: UUID,
        result_metadata: dict[str, str | int | float | bool],
        progress_metadata: dict[str, str | int | float | bool],
        now: datetime,
    ) -> None:
        """Atomically finalize job ONLY when lease_owner, fencing_token, and non-cancelled hold."""
        res = session.execute(
            update(WorkflowJob)
            .where(
                WorkflowJob.tenant_id == tenant_id,
                WorkflowJob.job_id == job_id,
                WorkflowJob.state == "running",
                WorkflowJob.lease_owner == worker_id,
                WorkflowJob.fencing_token == fencing_token,
                WorkflowJob.cancel_requested.is_(False),
            )
            .values(
                state="succeeded",
                stage="completed",
                result_extraction_id=extraction_id,
                result_metadata=dict(result_metadata),
                progress_metadata=dict(progress_metadata),
                lease_owner=None,
                lease_expires_at=None,
                completed_at=now,
                updated_at=now,
            )
        )
        if res.rowcount != 1:
            cancel_flag = session.scalar(
                select(WorkflowJob.cancel_requested).where(
                    WorkflowJob.tenant_id == tenant_id,
                    WorkflowJob.job_id == job_id,
                    WorkflowJob.lease_owner == worker_id,
                    WorkflowJob.fencing_token == fencing_token,
                )
            )
            if cancel_flag is True:
                raise JobCancelledError("Job was cancelled prior to finalization")
            raise StaleWorkerFenceError(
                "Worker lost lease or fencing token changed before finalization"
            )

    @staticmethod
    def finalize_cancelled(
        session: Session,
        *,
        tenant_id: UUID,
        job_id: UUID,
        worker_id: str,
        fencing_token: int,
        now: datetime,
    ) -> bool:
        res = session.execute(
            update(WorkflowJob)
            .where(
                WorkflowJob.tenant_id == tenant_id,
                WorkflowJob.job_id == job_id,
                WorkflowJob.state == "running",
                WorkflowJob.lease_owner == worker_id,
                WorkflowJob.fencing_token == fencing_token,
            )
            .values(
                state="cancelled",
                stage="cancelled",
                lease_owner=None,
                lease_expires_at=None,
                completed_at=now,
                updated_at=now,
            )
        )
        return res.rowcount == 1

    @staticmethod
    def finalize_failure_or_retry(
        session: Session,
        *,
        tenant_id: UUID,
        job_id: UUID,
        worker_id: str,
        fencing_token: int,
        error_code: str,
        error_message: str,
        retryable: bool,
        backoff_seconds: float,
        now: datetime,
    ) -> str:
        row = session.scalar(
            select(WorkflowJob)
            .where(
                WorkflowJob.tenant_id == tenant_id,
                WorkflowJob.job_id == job_id,
                WorkflowJob.state == "running",
                WorkflowJob.lease_owner == worker_id,
                WorkflowJob.fencing_token == fencing_token,
            )
            .with_for_update()
        )
        if row is None:
            raise StaleWorkerFenceError(
                "Worker lost lease before recording failure/retry"
            )
        if row.cancel_requested:
            row.state = "cancelled"
            row.stage = "cancelled"
            row.lease_owner = None
            row.lease_expires_at = None
            row.completed_at = now
            row.updated_at = now
            session.flush()
            return "cancelled"

        sanitized = sanitize_error_message(error_message)
        if retryable and row.attempt_count < row.max_attempts:
            row.state = "retry_wait"
            row.stage = "retry_wait"
            row.next_run_at = now + timedelta(seconds=max(0.1, backoff_seconds))
            row.lease_owner = None
            row.lease_expires_at = None
            row.last_error_code = error_code[:64]
            row.last_error_message = sanitized
            row.updated_at = now
            session.flush()
            return "retry_wait"

        row.state = "failed"
        row.stage = "failed"
        row.lease_owner = None
        row.lease_expires_at = None
        row.completed_at = now
        row.last_error_code = error_code[:64]
        row.last_error_message = sanitized
        row.updated_at = now
        session.flush()
        return "failed"
