"""Versioned lesson blueprints persisted in ArtifactStore with durable workflow tracking."""

import sqlalchemy as sa
from alembic import op

revision = "0013_lesson_blueprints"
down_revision = "0012_knowledge_retrieval_index"
branch_labels = None
depends_on = None

PREDICATE = "tenant_id = NULLIF(current_setting('viora.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.drop_constraint("ck_workflow_jobs_op", "workflow_jobs", type_="check")
    op.create_check_constraint(
        "ck_workflow_jobs_op",
        "workflow_jobs",
        "operation_kind IN ('source_extract', 'concept_extract', 'knowledge_index', 'lesson_blueprint')",
    )

    op.create_table(
        "lesson_blueprints",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("blueprint_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("derived_extraction_id", sa.Uuid(), nullable=False),
        sa.Column("concept_extraction_id", sa.Uuid(), nullable=False),
        sa.Column("approved_review_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("blueprint_version", sa.Integer(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("outline_version", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        sa.Column("storage_kind", sa.Text(), nullable=False),
        sa.Column("storage_resource_id", sa.Uuid(), nullable=False),
        sa.Column("storage_version_id", sa.Uuid(), nullable=False),
        sa.Column("storage_sha256", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("inference_requests", sa.Integer(), nullable=False),
        sa.Column("generation_ms", sa.Float(), nullable=False),
        sa.Column("validation_ms", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("blueprint_version >= 1", name="ck_lesson_blueprints_ver"),
        sa.CheckConstraint("byte_size >= 1", name="ck_lesson_blueprints_bytes"),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_lesson_blueprints_src_sha"
        ),
        sa.CheckConstraint(
            "storage_sha256 ~ '^[0-9a-f]{64}$'", name="ck_lesson_blueprints_art_sha"
        ),
        sa.CheckConstraint(
            "input_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_lesson_blueprints_fp"
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0",
            name="ck_lesson_blueprints_tokens",
        ),
        sa.CheckConstraint(
            "inference_requests >= 0 AND generation_ms >= 0 AND validation_ms >= 0",
            name="ck_lesson_blueprints_metrics",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_lesson_blueprints_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_lesson_blueprints_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "derived_extraction_id"],
            ["derived_extractions.tenant_id", "derived_extractions.extraction_id"],
            name="fk_lesson_blueprints_derived",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "concept_extraction_id"],
            ["concept_extractions.tenant_id", "concept_extractions.concept_extraction_id"],
            name="fk_lesson_blueprints_concept",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approved_review_id"],
            ["knowledge_reviews.tenant_id", "knowledge_reviews.review_id"],
            name="fk_lesson_blueprints_review",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "blueprint_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "version_id",
            "blueprint_version",
            name="uq_lesson_blueprints_ver_num",
        ),
    )
    op.create_index(
        "ix_lesson_blueprints_lookup",
        "lesson_blueprints",
        ["tenant_id", "version_id", "blueprint_version"],
    )
    op.create_index(
        "uq_lesson_blueprints_job",
        "lesson_blueprints",
        ["tenant_id", "job_id"],
        unique=True,
        postgresql_where=sa.text("job_id IS NOT NULL"),
    )

    op.execute("ALTER TABLE lesson_blueprints ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE lesson_blueprints FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY lesson_blueprints_read ON lesson_blueprints FOR SELECT USING ({PREDICATE})"
    )
    op.execute(
        f"CREATE POLICY lesson_blueprints_append ON lesson_blueprints FOR INSERT WITH CHECK ({PREDICATE})"
    )

    op.create_table(
        "lesson_blueprint_publications",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("blueprint_id", sa.Uuid(), nullable=False),
        sa.Column("concept_extraction_id", sa.Uuid(), nullable=False),
        sa.Column("approved_review_id", sa.Uuid(), nullable=False),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        sa.Column(
            "approval_status",
            sa.Text(),
            server_default="pending_review",
            nullable=False,
        ),
        sa.Column(
            "published_to_students",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_lbp_sha256"
        ),
        sa.CheckConstraint(
            "approval_status IN ('pending_review', 'approved', 'changes_requested', 'rejected')",
            name="ck_lbp_status",
        ),
        sa.CheckConstraint(
            "(NOT published_to_students) OR (approval_status = 'approved' AND published_at IS NOT NULL)",
            name="ck_lbp_requires_approval",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_lbp_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_lbp_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "blueprint_id"],
            ["lesson_blueprints.tenant_id", "lesson_blueprints.blueprint_id"],
            name="fk_lbp_blueprint",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "concept_extraction_id"],
            ["concept_extractions.tenant_id", "concept_extractions.concept_extraction_id"],
            name="fk_lbp_concept",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approved_review_id"],
            ["knowledge_reviews.tenant_id", "knowledge_reviews.review_id"],
            name="fk_lbp_review",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"], ["users.user_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "version_id"),
    )
    op.execute("ALTER TABLE lesson_blueprint_publications ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE lesson_blueprint_publications FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON lesson_blueprint_publications USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY tenant_isolation ON lesson_blueprint_publications")
    op.execute("ALTER TABLE lesson_blueprint_publications NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE lesson_blueprint_publications DISABLE ROW LEVEL SECURITY")
    op.drop_table("lesson_blueprint_publications")

    op.execute("DROP POLICY lesson_blueprints_append ON lesson_blueprints")
    op.execute("DROP POLICY lesson_blueprints_read ON lesson_blueprints")
    op.execute("ALTER TABLE lesson_blueprints NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE lesson_blueprints DISABLE ROW LEVEL SECURITY")
    op.drop_index("uq_lesson_blueprints_job", table_name="lesson_blueprints")
    op.drop_index("ix_lesson_blueprints_lookup", table_name="lesson_blueprints")
    op.drop_table("lesson_blueprints")

    op.drop_constraint("ck_workflow_jobs_op", "workflow_jobs", type_="check")
    op.create_check_constraint(
        "ck_workflow_jobs_op",
        "workflow_jobs",
        "operation_kind IN ('source_extract', 'concept_extract', 'knowledge_index')",
    )
