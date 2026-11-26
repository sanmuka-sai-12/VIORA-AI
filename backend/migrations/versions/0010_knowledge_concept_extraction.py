"""Educational concept extractions, scoped AI cache, and workflow concept_extract operation."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_knowledge_concepts"
down_revision = "0009_workflow_extraction_cache"
branch_labels = None
depends_on = None

PREDICATE = "tenant_id = NULLIF(current_setting('viora.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.drop_constraint("ck_workflow_jobs_op", "workflow_jobs", type_="check")
    op.create_check_constraint(
        "ck_workflow_jobs_op",
        "workflow_jobs",
        "operation_kind IN ('source_extract', 'concept_extract')",
    )

    op.create_table(
        "concept_extractions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("concept_extraction_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("derived_extraction_id", sa.Uuid(), nullable=False),
        sa.Column("extraction_version", sa.Integer(), nullable=False),
        sa.Column("concept_version", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("chunk_algorithm_version", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("inference_requests", sa.Integer(), nullable=False),
        sa.Column("chunking_ms", sa.Float(), nullable=False),
        sa.Column("inference_ms", sa.Float(), nullable=False),
        sa.Column("validation_ms", sa.Float(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("cache_key", sa.Text(), nullable=True),
        sa.Column(
            "published_to_students", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        sa.Column(
            "knowledge",
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
            "source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_concept_extractions_sha256"
        ),
        sa.CheckConstraint(
            "input_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_concept_extractions_fingerprint",
        ),
        sa.CheckConstraint("concept_version >= 1", name="ck_concept_extractions_ver"),
        sa.CheckConstraint(
            "extraction_version >= 1", name="ck_concept_extractions_ext_ver"
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0",
            name="ck_concept_extractions_tokens",
        ),
        sa.CheckConstraint(
            "inference_requests >= 0 AND chunking_ms >= 0 AND inference_ms >= 0 AND validation_ms >= 0",
            name="ck_concept_extractions_metrics",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_concept_extractions_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_concept_extractions_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "derived_extraction_id"],
            ["derived_extractions.tenant_id", "derived_extractions.extraction_id"],
            name="fk_concept_extractions_derived",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "concept_extraction_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "version_id",
            "concept_version",
            name="uq_concept_extractions_ver_num",
        ),
    )
    op.create_index(
        "ix_concept_extractions_ver",
        "concept_extractions",
        ["tenant_id", "version_id", "concept_version"],
    )
    op.create_index(
        "uq_concept_extractions_job",
        "concept_extractions",
        ["tenant_id", "job_id"],
        unique=True,
        postgresql_where=sa.text("job_id IS NOT NULL"),
    )

    op.create_table(
        "concept_extraction_cache",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("cache_key", sa.Text(), nullable=False),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("chunk_algorithm_version", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("derived_extraction_id", sa.Uuid(), nullable=False),
        sa.Column("concept_extraction_id", sa.Uuid(), nullable=False),
        sa.Column(
            "knowledge",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("inference_requests", sa.Integer(), nullable=False),
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
            "cache_key ~ '^[0-9a-f]{64}$'", name="ck_concept_cache_key"
        ),
        sa.CheckConstraint(
            "input_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_concept_cache_fp"
        ),
        sa.CheckConstraint("hit_count >= 0", name="ck_concept_cache_hits"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_concept_cache_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_concept_cache_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "derived_extraction_id"],
            ["derived_extractions.tenant_id", "derived_extractions.extraction_id"],
            name="fk_concept_cache_derived",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "concept_extraction_id"],
            ["concept_extractions.tenant_id", "concept_extractions.concept_extraction_id"],
            name="fk_concept_cache_concept",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "cache_key"),
    )
    op.create_index(
        "ix_concept_cache_source",
        "concept_extraction_cache",
        ["tenant_id", "source_id", "version_id"],
    )

    op.execute("ALTER TABLE concept_extractions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE concept_extractions FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY concept_extractions_read ON concept_extractions FOR SELECT USING ({PREDICATE})"
    )
    op.execute(
        f"CREATE POLICY concept_extractions_append ON concept_extractions FOR INSERT WITH CHECK ({PREDICATE})"
    )

    op.execute("ALTER TABLE concept_extraction_cache ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE concept_extraction_cache FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON concept_extraction_cache USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY tenant_isolation ON concept_extraction_cache")
    op.execute("ALTER TABLE concept_extraction_cache NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE concept_extraction_cache DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY concept_extractions_append ON concept_extractions")
    op.execute("DROP POLICY concept_extractions_read ON concept_extractions")
    op.execute("ALTER TABLE concept_extractions NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE concept_extractions DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_concept_cache_source", table_name="concept_extraction_cache")
    op.drop_table("concept_extraction_cache")

    op.drop_index("uq_concept_extractions_job", table_name="concept_extractions")
    op.drop_index("ix_concept_extractions_ver", table_name="concept_extractions")
    op.drop_table("concept_extractions")

    op.drop_constraint("ck_workflow_jobs_op", "workflow_jobs", type_="check")
    op.create_check_constraint(
        "ck_workflow_jobs_op",
        "workflow_jobs",
        "operation_kind IN ('source_extract')",
    )
