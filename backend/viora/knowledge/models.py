"""SQLAlchemy models for persisted educational concept extractions and scoped AI cache."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from ..db import Base


class _Vector384Type(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **_kw: object) -> str:
        return "vector(384)"


class ConceptExtraction(Base):
    """Append-only derived concept graph extracted from an immutable derived_extractions record."""

    __tablename__ = "concept_extractions"
    __table_args__ = (
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_concept_extractions_sha256",
        ),
        CheckConstraint(
            "input_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_concept_extractions_fingerprint",
        ),
        CheckConstraint(
            "concept_version >= 1",
            name="ck_concept_extractions_ver",
        ),
        CheckConstraint(
            "extraction_version >= 1",
            name="ck_concept_extractions_ext_ver",
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0",
            name="ck_concept_extractions_tokens",
        ),
        CheckConstraint(
            "inference_requests >= 0 AND chunking_ms >= 0 AND inference_ms >= 0 AND validation_ms >= 0",
            name="ck_concept_extractions_metrics",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_concept_extractions_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_concept_extractions_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "derived_extraction_id"],
            ["derived_extractions.tenant_id", "derived_extractions.extraction_id"],
            name="fk_concept_extractions_derived",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "version_id",
            "concept_version",
            name="uq_concept_extractions_ver_num",
        ),
        Index(
            "ix_concept_extractions_ver",
            "tenant_id",
            "version_id",
            "concept_version",
        ),
        Index(
            "uq_concept_extractions_job",
            "tenant_id",
            "job_id",
            unique=True,
            postgresql_where=text("job_id IS NOT NULL"),
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("institutions.tenant_id", ondelete="RESTRICT"), primary_key=True
    )
    concept_extraction_id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID]
    version_id: Mapped[UUID]
    derived_extraction_id: Mapped[UUID]
    extraction_version: Mapped[int] = mapped_column(Integer)
    concept_version: Mapped[int] = mapped_column(Integer)
    job_id: Mapped[UUID | None] = mapped_column(default=None)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(Text)
    chunk_algorithm_version: Mapped[str] = mapped_column(Text)
    input_fingerprint: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    total_tokens: Mapped[int] = mapped_column(Integer)
    inference_requests: Mapped[int] = mapped_column(Integer)
    chunking_ms: Mapped[float] = mapped_column(Float)
    inference_ms: Mapped[float] = mapped_column(Float)
    validation_ms: Mapped[float] = mapped_column(Float)
    cache_hit: Mapped[bool] = mapped_column(Boolean, server_default="false")
    cache_key: Mapped[str | None] = mapped_column(Text, default=None)
    published_to_students: Mapped[bool] = mapped_column(Boolean, server_default="false")
    source_sha256: Mapped[str] = mapped_column(Text)
    knowledge: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ConceptExtractionCacheEntry(Base):
    """Tenant-, offering-, and actor-scoped cache for completed AI concept extractions."""

    __tablename__ = "concept_extraction_cache"
    __table_args__ = (
        CheckConstraint(
            "cache_key ~ '^[0-9a-f]{64}$'", name="ck_concept_cache_key"
        ),
        CheckConstraint(
            "input_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_concept_cache_fp"
        ),
        CheckConstraint("hit_count >= 0", name="ck_concept_cache_hits"),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_concept_cache_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_concept_cache_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "derived_extraction_id"],
            ["derived_extractions.tenant_id", "derived_extractions.extraction_id"],
            name="fk_concept_cache_derived",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "concept_extraction_id"],
            ["concept_extractions.tenant_id", "concept_extractions.concept_extraction_id"],
            name="fk_concept_cache_concept",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_concept_cache_source",
            "tenant_id",
            "source_id",
            "version_id",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("institutions.tenant_id", ondelete="RESTRICT"), primary_key=True
    )
    cache_key: Mapped[str] = mapped_column(Text, primary_key=True)
    scope_key: Mapped[str] = mapped_column(Text)
    input_fingerprint: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(Text)
    chunk_algorithm_version: Mapped[str] = mapped_column(Text)
    source_id: Mapped[UUID]
    version_id: Mapped[UUID]
    derived_extraction_id: Mapped[UUID]
    concept_extraction_id: Mapped[UUID]
    knowledge: Mapped[dict[str, Any]] = mapped_column(JSONB)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    total_tokens: Mapped[int] = mapped_column(Integer)
    inference_requests: Mapped[int] = mapped_column(Integer)
    hit_count: Mapped[int] = mapped_column(Integer, server_default="0")
    last_hit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeReview(Base):
    """Append-only versioned teacher review log for extracted educational knowledge."""

    __tablename__ = "knowledge_reviews"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved', 'changes_requested', 'rejected')",
            name="ck_knowledge_reviews_decision",
        ),
        CheckConstraint("review_version >= 1", name="ck_knowledge_reviews_ver"),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_knowledge_reviews_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_knowledge_reviews_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "concept_extraction_id"],
            ["concept_extractions.tenant_id", "concept_extractions.concept_extraction_id"],
            name="fk_knowledge_reviews_concept",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "concept_extraction_id",
            "review_version",
            name="uq_knowledge_reviews_ver",
        ),
        Index(
            "ix_knowledge_reviews_lookup",
            "tenant_id",
            "version_id",
            "review_version",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("institutions.tenant_id", ondelete="RESTRICT"), primary_key=True
    )
    review_id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID]
    version_id: Mapped[UUID]
    concept_extraction_id: Mapped[UUID]
    review_version: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(Text)
    review_notes: Mapped[str | None] = mapped_column(Text, default=None)
    approved_concept_ids: Mapped[list[str]] = mapped_column(JSONB)
    rejected_concept_ids: Mapped[list[str]] = mapped_column(JSONB)
    reviewed_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.user_id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgePublication(Base):
    """Tenant-scoped lesson outline, approval state, and student publication binding per version."""

    __tablename__ = "knowledge_publications"
    __table_args__ = (
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_knowledge_pub_sha256",
        ),
        CheckConstraint("concept_version >= 1", name="ck_knowledge_pub_ver"),
        CheckConstraint(
            "review_status IN ('pending_review', 'approved', 'changes_requested', 'rejected')",
            name="ck_knowledge_pub_status",
        ),
        CheckConstraint(
            "(NOT published_to_students) OR (review_status = 'approved' AND approved_review_id IS NOT NULL)",
            name="ck_knowledge_pub_requires_approval",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_knowledge_pub_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_knowledge_pub_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "concept_extraction_id"],
            ["concept_extractions.tenant_id", "concept_extractions.concept_extraction_id"],
            name="fk_knowledge_pub_concept",
            ondelete="RESTRICT",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("institutions.tenant_id", ondelete="RESTRICT"), primary_key=True
    )
    version_id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID]
    concept_extraction_id: Mapped[UUID]
    concept_version: Mapped[int] = mapped_column(Integer)
    source_sha256: Mapped[str] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(Text, server_default="pending_review")
    latest_review_id: Mapped[UUID | None] = mapped_column(default=None)
    approved_review_id: Mapped[UUID | None] = mapped_column(default=None)
    published_to_students: Mapped[bool] = mapped_column(Boolean, server_default="false")
    published_by_user_id: Mapped[UUID | None] = mapped_column(default=None)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    outline: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeIndexChunk(Base):
    """Versioned, tenant- and scope-isolated searchable chunk + embedding index."""

    __tablename__ = "knowledge_index_chunks"
    __table_args__ = (
        CheckConstraint("index_version >= 1", name="ck_kic_index_ver"),
        CheckConstraint("chunk_index >= 0", name="ck_kic_chunk_idx"),
        CheckConstraint(
            "page_start >= 1 AND page_end >= page_start", name="ck_kic_pages"
        ),
        CheckConstraint("estimated_tokens >= 1", name="ck_kic_tokens"),
        CheckConstraint("embedding_dim = 384", name="ck_kic_embed_dim"),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_kic_content_sha256"
        ),
        CheckConstraint(
            "embedding_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_kic_embed_fp"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_kic_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_kic_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "derived_extraction_id"],
            ["derived_extractions.tenant_id", "derived_extractions.extraction_id"],
            name="fk_kic_derived",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "concept_extraction_id"],
            ["concept_extractions.tenant_id", "concept_extractions.concept_extraction_id"],
            name="fk_kic_concept",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "version_id",
            "index_version",
            "chunk_id",
            name="uq_kic_ver_chunk",
        ),
        Index(
            "ix_kic_active_lookup",
            "tenant_id",
            "version_id",
            "is_active",
            "chunk_index",
        ),
        Index(
            "ix_kic_scope_fp",
            "tenant_id",
            "scope_key",
            "embedding_fingerprint",
        ),
        Index(
            "ix_kic_section_lookup",
            "tenant_id",
            "derived_extraction_id",
            "section_id",
            "chunk_index",
        ),
        Index(
            "ix_kic_search_tsv",
            "search_tsv",
            postgresql_using="gin",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("institutions.tenant_id", ondelete="RESTRICT"), primary_key=True
    )
    chunk_record_id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID]
    version_id: Mapped[UUID]
    derived_extraction_id: Mapped[UUID]
    concept_extraction_id: Mapped[UUID | None] = mapped_column(default=None)
    job_id: Mapped[UUID | None] = mapped_column(default=None)
    index_version: Mapped[int] = mapped_column(Integer)
    chunk_id: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer)
    section_id: Mapped[str] = mapped_column(Text)
    section_path: Mapped[list[str]] = mapped_column(JSONB)
    heading_title: Mapped[str | None] = mapped_column(Text, default=None)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    estimated_tokens: Mapped[int] = mapped_column(Integer)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB)
    block_ids: Mapped[list[str]] = mapped_column(JSONB)
    rendered_text: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(Text)
    chunk_algorithm_version: Mapped[str] = mapped_column(Text)
    scope_key: Mapped[str] = mapped_column(Text)
    embedding_model: Mapped[str] = mapped_column(Text)
    embedding_model_version: Mapped[str] = mapped_column(Text)
    embedding_dim: Mapped[int] = mapped_column(Integer)
    embedding_fingerprint: Mapped[str] = mapped_column(Text)
    search_tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(heading_title, '') || ' ' || rendered_text)",
            persisted=True,
        ),
    )
    embedding: Mapped[Any] = mapped_column(_Vector384Type(), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
