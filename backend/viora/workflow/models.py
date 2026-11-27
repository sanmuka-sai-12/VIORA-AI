"""SQLAlchemy model for durable, lease-fenced PostgreSQL workflow jobs."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class WorkflowJob(Base):
    __tablename__ = "workflow_jobs"
    __table_args__ = (
        CheckConstraint(
            "operation_kind IN ('source_extract', 'concept_extract', 'knowledge_index', 'lesson_blueprint')",
            name="ck_workflow_jobs_op",
        ),
        CheckConstraint(
            "state IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')",
            name="ck_workflow_jobs_state",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_workflow_jobs_attempts"),
        CheckConstraint("max_attempts >= 1", name="ck_workflow_jobs_max_attempts"),
        CheckConstraint("fencing_token >= 0", name="ck_workflow_jobs_fence"),
        CheckConstraint(
            "idempotency_key IS NULL OR (btrim(idempotency_key) <> '' AND length(idempotency_key) <= 128)",
            name="ck_workflow_jobs_idemp_key",
        ),
        CheckConstraint(
            "request_digest IS NULL OR request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_jobs_req_digest",
        ),
        CheckConstraint(
            "input_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_jobs_artifact_sha",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "offering_id"],
            ["subject_offerings.tenant_id", "subject_offerings.id"],
            name="fk_workflow_jobs_offering",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_workflow_jobs_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_workflow_jobs_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "submitted_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_workflow_jobs_actor",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_workflow_jobs_idempotency",
            "tenant_id",
            "submitted_by_user_id",
            "operation_kind",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "uq_workflow_jobs_active_version",
            "tenant_id",
            "version_id",
            "operation_kind",
            unique=True,
            postgresql_where=text("state IN ('queued', 'running', 'retry_wait')"),
        ),
        Index(
            "ix_workflow_jobs_claim",
            "state",
            "next_run_at",
            "lease_expires_at",
            "created_at",
        ),
        Index(
            "ix_workflow_jobs_source",
            "tenant_id",
            "source_id",
            "created_at",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("institutions.tenant_id", ondelete="RESTRICT"), primary_key=True
    )
    job_id: Mapped[UUID] = mapped_column(primary_key=True)
    offering_id: Mapped[UUID | None] = mapped_column(default=None)
    source_id: Mapped[UUID]
    version_id: Mapped[UUID]
    submitted_by_user_id: Mapped[UUID]
    operation_kind: Mapped[str] = mapped_column(Text, server_default="source_extract")
    idempotency_key: Mapped[str | None] = mapped_column(Text, default=None)
    request_digest: Mapped[str | None] = mapped_column(Text, default=None)
    input_artifact_kind: Mapped[str] = mapped_column(Text)
    input_artifact_resource_id: Mapped[UUID]
    input_artifact_version_id: Mapped[UUID]
    input_artifact_sha256: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default="queued")
    stage: Mapped[str] = mapped_column(Text, server_default="queued")
    progress_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    checkpoint_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    attempt_count: Mapped[int] = mapped_column(Integer, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="3")
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    lease_owner: Mapped[str | None] = mapped_column(Text, default=None)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    fencing_token: Mapped[int] = mapped_column(BigInteger, server_default="0")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, server_default="false")
    result_extraction_id: Mapped[UUID | None] = mapped_column(default=None)
    result_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    last_error_code: Mapped[str | None] = mapped_column(Text, default=None)
    last_error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
