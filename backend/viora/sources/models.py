"""SQLAlchemy models for tenant-scoped sources, immutable versions, and policies."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("btrim(title) <> ''", name="ck_sources_title"),
        CheckConstraint(
            "origin_kind IN ('teacher_resource', 'student_submission', 'unscoped')",
            name="ck_sources_origin_kind",
        ),
        CheckConstraint(
            "visibility IN ('restricted', 'teacher_only', 'offering_published', 'owner_private', 'teacher_review')",
            name="ck_sources_visibility",
        ),
        CheckConstraint(
            "lifecycle_state IN ('active', 'archived')",
            name="ck_sources_lifecycle_state",
        ),
        CheckConstraint(
            "("
            "(origin_kind = 'unscoped' AND offering_id IS NULL AND visibility = 'restricted')"
            " OR "
            "(origin_kind = 'teacher_resource' AND offering_id IS NOT NULL "
            "AND creator_user_id IS NOT NULL AND owner_user_id IS NOT NULL "
            "AND visibility IN ('teacher_only', 'offering_published', 'restricted'))"
            " OR "
            "(origin_kind = 'student_submission' AND offering_id IS NOT NULL "
            "AND creator_user_id IS NOT NULL AND owner_user_id IS NOT NULL "
            "AND visibility IN ('owner_private', 'teacher_review', 'restricted'))"
            ")",
            name="ck_sources_origin_visibility_integrity",
        ),
        CheckConstraint(
            "latest_version_number >= 0",
            name="ck_sources_latest_version_number",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "offering_id"],
            ["subject_offerings.tenant_id", "subject_offerings.id"],
            name="fk_sources_offering",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "creator_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_sources_creator",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "owner_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_sources_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "active_dedupe_key",
            name="uq_sources_active_dedupe",
        ),
        Index(
            "ix_sources_offering",
            "tenant_id",
            "offering_id",
            "origin_kind",
            "lifecycle_state",
            "created_at",
        ),
        Index(
            "ix_sources_owner",
            "tenant_id",
            "owner_user_id",
            "offering_id",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("institutions.tenant_id", ondelete="RESTRICT"), primary_key=True
    )
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String)
    offering_id: Mapped[UUID | None] = mapped_column(default=None)
    creator_user_id: Mapped[UUID | None] = mapped_column(default=None)
    owner_user_id: Mapped[UUID | None] = mapped_column(default=None)
    origin_kind: Mapped[str] = mapped_column(Text, server_default="unscoped")
    visibility: Mapped[str] = mapped_column(Text, server_default="restricted")
    lifecycle_state: Mapped[str] = mapped_column(Text, server_default="active")
    latest_version_number: Mapped[int] = mapped_column(Integer, server_default="0")
    latest_version_id: Mapped[UUID | None] = mapped_column(default=None)
    active_dedupe_key: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OfferingSourcePolicy(Base):
    __tablename__ = "offering_source_policies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "offering_id"],
            ["subject_offerings.tenant_id", "subject_offerings.id"],
            name="fk_offering_source_policy_offering",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_offering_source_policy_actor",
            ondelete="RESTRICT",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("institutions.tenant_id", ondelete="RESTRICT"), primary_key=True
    )
    offering_id: Mapped[UUID] = mapped_column(primary_key=True)
    student_submissions_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default="true"
    )
    teachers_can_view_student_submissions: Mapped[bool] = mapped_column(
        Boolean, server_default="true"
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SourceVersion(Base):
    __tablename__ = "source_versions"
    __table_args__ = (
        CheckConstraint("version_number >= 1", name="ck_source_versions_number"),
        CheckConstraint(
            "input_format IN ('plain_text', 'typed_notes', 'topic_description', 'digital_pdf')",
            name="ck_source_versions_format",
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_source_versions_sha256",
        ),
        CheckConstraint("size_bytes > 0", name="ck_source_versions_size"),
        CheckConstraint(
            "storage_sha256 = content_sha256",
            name="ck_source_versions_storage_sha",
        ),
        CheckConstraint(
            "processing_status IN ('extracted', 'partial_extraction')",
            name="ck_source_versions_status",
        ),
        CheckConstraint("extraction_ms >= 0", name="ck_source_versions_ms"),
        CheckConstraint("char_count >= 0", name="ck_source_versions_chars"),
        CheckConstraint("page_count >= 1", name="ck_source_versions_pages"),
        CheckConstraint("warning_count >= 0", name="ck_source_versions_warnings"),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_source_versions_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_source_versions_creator",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "version_number",
            name="uq_source_versions_number",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "content_sha256",
            name="uq_source_versions_digest",
        ),
        Index(
            "ix_source_versions_source_num",
            "tenant_id",
            "source_id",
            "version_number",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("institutions.tenant_id", ondelete="RESTRICT"), primary_key=True
    )
    version_id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID]
    version_number: Mapped[int] = mapped_column(Integer)
    created_by_user_id: Mapped[UUID]
    input_format: Mapped[str] = mapped_column(Text)
    original_filename: Mapped[str | None] = mapped_column(Text, default=None)
    content_sha256: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_kind: Mapped[str] = mapped_column(Text)
    storage_resource_id: Mapped[UUID]
    storage_version_id: Mapped[UUID]
    storage_sha256: Mapped[str] = mapped_column(Text)
    processing_status: Mapped[str] = mapped_column(Text)
    extractor_id: Mapped[str] = mapped_column(Text)
    contract_version: Mapped[str] = mapped_column(Text)
    extraction_ms: Mapped[float] = mapped_column(Float)
    char_count: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer)
    warning_count: Mapped[int] = mapped_column(Integer)
    processing_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB)
    extracted_document: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SourceIdempotencyRecord(Base):
    __tablename__ = "source_idempotency"
    __table_args__ = (
        CheckConstraint(
            "operation_kind IN ('create_source', 'create_version')",
            name="ck_source_idemp_op",
        ),
        CheckConstraint(
            "btrim(idempotency_key) <> '' AND length(idempotency_key) <= 128",
            name="ck_source_idemp_key",
        ),
        CheckConstraint(
            "request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_source_idemp_digest",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "offering_id"],
            ["subject_offerings.tenant_id", "subject_offerings.id"],
            name="fk_source_idemp_offering",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_source_idemp_actor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_source_idemp_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_source_idemp_version",
            ondelete="RESTRICT",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("institutions.tenant_id", ondelete="RESTRICT"), primary_key=True
    )
    offering_id: Mapped[UUID] = mapped_column(primary_key=True)
    actor_user_id: Mapped[UUID] = mapped_column(primary_key=True)
    target_scope_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation_kind: Mapped[str] = mapped_column(Text, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_digest: Mapped[str] = mapped_column(Text)
    source_id: Mapped[UUID]
    version_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
