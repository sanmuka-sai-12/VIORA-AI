"""Durable workflow jobs, separated derived extractions, and scoped extraction caching."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_workflow_extraction_cache"
down_revision = "0008_source_ingestion"
branch_labels = None
depends_on = None

PREDICATE = "tenant_id = NULLIF(current_setting('viora.tenant_id', true), '')::uuid"
WORKER_PREDICATE = "NULLIF(current_setting('viora.worker_id', true), '') IS NOT NULL"


def upgrade() -> None:
    # 1. Extend immutable source_versions to accept PNG, JPEG, scanned PDFs, and async pending status
    op.drop_constraint("ck_source_versions_format", "source_versions", type_="check")
    op.create_check_constraint(
        "ck_source_versions_format",
        "source_versions",
        "input_format IN ('plain_text', 'typed_notes', 'topic_description', 'digital_pdf', 'scanned_pdf', 'png', 'jpeg')",
    )
    op.drop_constraint("ck_source_versions_status", "source_versions", type_="check")
    op.create_check_constraint(
        "ck_source_versions_status",
        "source_versions",
        "processing_status IN ('pending', 'extracted', 'partial_extraction', 'ocr_required', 'unreadable', 'failed', 'cancelled')",
    )
    op.alter_column(
        "source_versions",
        "extracted_document",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
    )

    # 2. Separate append-only derived_extractions table
    op.create_table(
        "derived_extractions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("extraction_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        sa.Column("extraction_version", sa.Integer(), nullable=False),
        sa.Column("supersedes_extraction_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column(
            "cache_hit", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("cache_key", sa.Text(), nullable=True),
        sa.Column("processing_status", sa.Text(), nullable=False),
        sa.Column("extractor_id", sa.Text(), nullable=False),
        sa.Column("extractor_version", sa.Text(), nullable=False),
        sa.Column("contract_version", sa.Text(), nullable=False),
        sa.Column("extraction_ms", sa.Float(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column(
            "processing_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "document",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_derived_extractions_sha256"
        ),
        sa.CheckConstraint(
            "extraction_version >= 1", name="ck_derived_extractions_ver"
        ),
        sa.CheckConstraint(
            "processing_status IN ('extracted', 'partial_extraction', 'ocr_required', 'unreadable')",
            name="ck_derived_extractions_status",
        ),
        sa.CheckConstraint("extraction_ms >= 0", name="ck_derived_extractions_ms"),
        sa.CheckConstraint("char_count >= 0", name="ck_derived_extractions_chars"),
        sa.CheckConstraint("page_count >= 1", name="ck_derived_extractions_pages"),
        sa.CheckConstraint(
            "warning_count >= 0", name="ck_derived_extractions_warnings"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_derived_extractions_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_derived_extractions_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "extraction_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "version_id",
            "extraction_version",
            name="uq_derived_extractions_ver_num",
        ),
    )
    op.create_index(
        "ix_derived_extractions_ver",
        "derived_extractions",
        ["tenant_id", "version_id", "extraction_version"],
    )
    op.create_index(
        "uq_derived_extractions_job",
        "derived_extractions",
        ["tenant_id", "job_id"],
        unique=True,
        postgresql_where=sa.text("job_id IS NOT NULL"),
    )

    # 3. Durable lease-fenced PostgreSQL workflow_jobs
    op.create_table(
        "workflow_jobs",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("offering_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("submitted_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "operation_kind",
            sa.Text(),
            server_default="source_extract",
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("request_digest", sa.Text(), nullable=True),
        sa.Column("input_artifact_kind", sa.Text(), nullable=False),
        sa.Column("input_artifact_resource_id", sa.Uuid(), nullable=False),
        sa.Column("input_artifact_version_id", sa.Uuid(), nullable=False),
        sa.Column("input_artifact_sha256", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="queued", nullable=False),
        sa.Column("stage", sa.Text(), server_default="queued", nullable=False),
        sa.Column(
            "progress_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "checkpoint_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "attempt_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "max_attempts", sa.Integer(), server_default="3", nullable=False
        ),
        sa.Column(
            "next_run_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fencing_token", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column(
            "cancel_requested", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("result_extraction_id", sa.Uuid(), nullable=True),
        sa.Column(
            "result_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "operation_kind IN ('source_extract')", name="ck_workflow_jobs_op"
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')",
            name="ck_workflow_jobs_state",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_workflow_jobs_attempts"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_workflow_jobs_max_attempts"),
        sa.CheckConstraint("fencing_token >= 0", name="ck_workflow_jobs_fence"),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR (btrim(idempotency_key) <> '' AND length(idempotency_key) <= 128)",
            name="ck_workflow_jobs_idemp_key",
        ),
        sa.CheckConstraint(
            "request_digest IS NULL OR request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_jobs_req_digest",
        ),
        sa.CheckConstraint(
            "input_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_jobs_artifact_sha",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "offering_id"],
            ["subject_offerings.tenant_id", "subject_offerings.id"],
            name="fk_workflow_jobs_offering",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_workflow_jobs_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_workflow_jobs_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "submitted_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_workflow_jobs_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "job_id"),
    )
    op.create_index(
        "uq_workflow_jobs_idempotency",
        "workflow_jobs",
        ["tenant_id", "submitted_by_user_id", "operation_kind", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "uq_workflow_jobs_active_version",
        "workflow_jobs",
        ["tenant_id", "version_id", "operation_kind"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued', 'running', 'retry_wait')"),
    )
    op.create_index(
        "ix_workflow_jobs_claim",
        "workflow_jobs",
        ["state", "next_run_at", "lease_expires_at", "created_at"],
    )
    op.create_index(
        "ix_workflow_jobs_source",
        "workflow_jobs",
        ["tenant_id", "source_id", "created_at"],
    )

    # 4. Version-aware scoped extraction cache
    op.create_table(
        "extraction_cache",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("cache_key", sa.Text(), nullable=False),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("algorithm_version", sa.Text(), nullable=False),
        sa.Column("config_digest", sa.Text(), nullable=False),
        sa.Column("preprocessing_version", sa.Text(), nullable=False),
        sa.Column("input_format", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("derived_extraction_id", sa.Uuid(), nullable=False),
        sa.Column(
            "document",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("hit_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cache_key ~ '^[0-9a-f]{64}$'", name="ck_extraction_cache_key"
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_extraction_cache_sha256"
        ),
        sa.CheckConstraint("hit_count >= 0", name="ck_extraction_cache_hits"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_extraction_cache_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_extraction_cache_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "derived_extraction_id"],
            ["derived_extractions.tenant_id", "derived_extractions.extraction_id"],
            name="fk_extraction_cache_extraction",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "cache_key"),
    )
    op.create_index(
        "ix_extraction_cache_source",
        "extraction_cache",
        ["tenant_id", "source_id", "version_id"],
    )
    op.create_index(
        "ix_extraction_cache_expiry",
        "extraction_cache",
        ["tenant_id", "invalidated_at", "expires_at"],
    )

    # 5. Scoped per-page OCR cache
    op.create_table(
        "page_ocr_cache",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("page_cache_key", sa.Text(), nullable=False),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("page_image_sha256", sa.Text(), nullable=False),
        sa.Column("ocr_engine_id", sa.Text(), nullable=False),
        sa.Column("ocr_engine_version", sa.Text(), nullable=False),
        sa.Column(
            "ocr_result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "page_cache_key ~ '^[0-9a-f]{64}$'", name="ck_page_ocr_cache_key"
        ),
        sa.CheckConstraint(
            "page_image_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_page_ocr_cache_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "page_cache_key"),
    )

    # 6. Forced RLS on all new tenant-bearing tables
    op.execute("ALTER TABLE derived_extractions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE derived_extractions FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY derived_extractions_read ON derived_extractions FOR SELECT USING ({PREDICATE})"
    )
    op.execute(
        f"CREATE POLICY derived_extractions_append ON derived_extractions FOR INSERT WITH CHECK ({PREDICATE})"
    )

    for table in ("extraction_cache", "page_ocr_cache"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
        )

    op.execute("ALTER TABLE workflow_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_jobs FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON workflow_jobs USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
    )
    op.execute(
        f"CREATE POLICY worker_claim_select ON workflow_jobs FOR SELECT USING ({WORKER_PREDICATE})"
    )
    op.execute(
        f"CREATE POLICY worker_claim_update ON workflow_jobs FOR UPDATE USING ({WORKER_PREDICATE}) WITH CHECK ({WORKER_PREDICATE})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY worker_claim_update ON workflow_jobs")
    op.execute("DROP POLICY worker_claim_select ON workflow_jobs")
    op.execute("DROP POLICY tenant_isolation ON workflow_jobs")
    op.execute("ALTER TABLE workflow_jobs NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_jobs DISABLE ROW LEVEL SECURITY")

    for table in ("page_ocr_cache", "extraction_cache"):
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY derived_extractions_append ON derived_extractions")
    op.execute("DROP POLICY derived_extractions_read ON derived_extractions")
    op.execute("ALTER TABLE derived_extractions NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE derived_extractions DISABLE ROW LEVEL SECURITY")

    op.drop_table("page_ocr_cache")
    op.drop_index("ix_extraction_cache_expiry", table_name="extraction_cache")
    op.drop_index("ix_extraction_cache_source", table_name="extraction_cache")
    op.drop_table("extraction_cache")

    op.drop_index("ix_workflow_jobs_source", table_name="workflow_jobs")
    op.drop_index("ix_workflow_jobs_claim", table_name="workflow_jobs")
    op.drop_index("uq_workflow_jobs_active_version", table_name="workflow_jobs")
    op.drop_index("uq_workflow_jobs_idempotency", table_name="workflow_jobs")
    op.drop_table("workflow_jobs")

    op.drop_index("uq_derived_extractions_job", table_name="derived_extractions")
    op.drop_index("ix_derived_extractions_ver", table_name="derived_extractions")
    op.drop_table("derived_extractions")

    op.execute(
        "UPDATE source_versions SET extracted_document = '{}'::jsonb WHERE extracted_document IS NULL"
    )
    op.alter_column(
        "source_versions",
        "extracted_document",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
    )
    op.drop_constraint("ck_source_versions_status", "source_versions", type_="check")
    op.create_check_constraint(
        "ck_source_versions_status",
        "source_versions",
        "processing_status IN ('extracted', 'partial_extraction')",
    )
    op.drop_constraint("ck_source_versions_format", "source_versions", type_="check")
    op.create_check_constraint(
        "ck_source_versions_format",
        "source_versions",
        "input_format IN ('plain_text', 'typed_notes', 'topic_description', 'digital_pdf')",
    )
