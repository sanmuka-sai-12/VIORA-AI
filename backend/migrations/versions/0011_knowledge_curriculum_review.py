"""Versioned teacher review, lesson outline state, and student publication control."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_knowledge_curriculum_review"
down_revision = "0010_knowledge_concepts"
branch_labels = None
depends_on = None

PREDICATE = "tenant_id = NULLIF(current_setting('viora.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "knowledge_reviews",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("concept_extraction_id", sa.Uuid(), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column(
            "approved_concept_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "rejected_concept_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'changes_requested', 'rejected')",
            name="ck_knowledge_reviews_decision",
        ),
        sa.CheckConstraint("review_version >= 1", name="ck_knowledge_reviews_ver"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_knowledge_reviews_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_knowledge_reviews_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "concept_extraction_id"],
            ["concept_extractions.tenant_id", "concept_extractions.concept_extraction_id"],
            name="fk_knowledge_reviews_concept",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"], ["users.user_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "review_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "concept_extraction_id",
            "review_version",
            name="uq_knowledge_reviews_ver",
        ),
    )
    op.create_index(
        "ix_knowledge_reviews_lookup",
        "knowledge_reviews",
        ["tenant_id", "version_id", "review_version"],
    )

    op.create_table(
        "knowledge_publications",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("concept_extraction_id", sa.Uuid(), nullable=False),
        sa.Column("concept_version", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        sa.Column(
            "review_status",
            sa.Text(),
            server_default="pending_review",
            nullable=False,
        ),
        sa.Column("latest_review_id", sa.Uuid(), nullable=True),
        sa.Column("approved_review_id", sa.Uuid(), nullable=True),
        sa.Column(
            "published_to_students",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column("published_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "outline",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_knowledge_pub_sha256"
        ),
        sa.CheckConstraint("concept_version >= 1", name="ck_knowledge_pub_ver"),
        sa.CheckConstraint(
            "review_status IN ('pending_review', 'approved', 'changes_requested', 'rejected')",
            name="ck_knowledge_pub_status",
        ),
        sa.CheckConstraint(
            "(NOT published_to_students) OR (review_status = 'approved' AND approved_review_id IS NOT NULL)",
            name="ck_knowledge_pub_requires_approval",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_knowledge_pub_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_knowledge_pub_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "concept_extraction_id"],
            ["concept_extractions.tenant_id", "concept_extractions.concept_extraction_id"],
            name="fk_knowledge_pub_concept",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "version_id"),
    )

    op.execute("ALTER TABLE knowledge_reviews ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_reviews FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY knowledge_reviews_read ON knowledge_reviews FOR SELECT USING ({PREDICATE})"
    )
    op.execute(
        f"CREATE POLICY knowledge_reviews_append ON knowledge_reviews FOR INSERT WITH CHECK ({PREDICATE})"
    )

    op.execute("ALTER TABLE knowledge_publications ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_publications FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON knowledge_publications USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY tenant_isolation ON knowledge_publications")
    op.execute("ALTER TABLE knowledge_publications NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_publications DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY knowledge_reviews_append ON knowledge_reviews")
    op.execute("DROP POLICY knowledge_reviews_read ON knowledge_reviews")
    op.execute("ALTER TABLE knowledge_reviews NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_reviews DISABLE ROW LEVEL SECURITY")

    op.drop_table("knowledge_publications")
    op.drop_index("ix_knowledge_reviews_lookup", table_name="knowledge_reviews")
    op.drop_table("knowledge_reviews")
