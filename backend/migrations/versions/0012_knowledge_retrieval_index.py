"""Knowledge retrieval chunk & versioned embedding index with pgvector + FTS."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_knowledge_retrieval_index"
down_revision = "0011_knowledge_curriculum_review"
branch_labels = None
depends_on = None

PREDICATE = "tenant_id = NULLIF(current_setting('viora.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.drop_constraint("ck_workflow_jobs_op", "workflow_jobs", type_="check")
    op.create_check_constraint(
        "ck_workflow_jobs_op",
        "workflow_jobs",
        "operation_kind IN ('source_extract', 'concept_extract', 'knowledge_index')",
    )

    op.create_table(
        "knowledge_index_chunks",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_record_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("derived_extraction_id", sa.Uuid(), nullable=False),
        sa.Column("concept_extraction_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("index_version", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Text(), nullable=False),
        sa.Column(
            "section_path",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("heading_title", sa.Text(), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "evidence_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "block_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("rendered_text", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("chunk_algorithm_version", sa.Text(), nullable=False),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("embedding_model_version", sa.Text(), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column("embedding_fingerprint", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("index_version >= 1", name="ck_kic_index_ver"),
        sa.CheckConstraint("chunk_index >= 0", name="ck_kic_chunk_idx"),
        sa.CheckConstraint(
            "page_start >= 1 AND page_end >= page_start", name="ck_kic_pages"
        ),
        sa.CheckConstraint("estimated_tokens >= 1", name="ck_kic_tokens"),
        sa.CheckConstraint("embedding_dim = 384", name="ck_kic_embed_dim"),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_kic_content_sha256"
        ),
        sa.CheckConstraint(
            "embedding_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_kic_embed_fp"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_kic_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_kic_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "derived_extraction_id"],
            ["derived_extractions.tenant_id", "derived_extractions.extraction_id"],
            name="fk_kic_derived",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "concept_extraction_id"],
            ["concept_extractions.tenant_id", "concept_extractions.concept_extraction_id"],
            name="fk_kic_concept",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "chunk_record_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "version_id",
            "index_version",
            "chunk_id",
            name="uq_kic_ver_chunk",
        ),
    )
    op.execute(
        "ALTER TABLE knowledge_index_chunks ADD COLUMN search_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(heading_title, '') || ' ' || rendered_text)) STORED"
    )
    op.execute(
        "CREATE INDEX ix_kic_search_tsv ON knowledge_index_chunks USING gin (search_tsv)"
    )
    op.create_index(
        "ix_kic_active_lookup",
        "knowledge_index_chunks",
        ["tenant_id", "version_id", "is_active", "chunk_index"],
    )
    op.create_index(
        "ix_kic_scope_fp",
        "knowledge_index_chunks",
        ["tenant_id", "scope_key", "embedding_fingerprint"],
    )
    op.create_index(
        "ix_kic_section_lookup",
        "knowledge_index_chunks",
        ["tenant_id", "derived_extraction_id", "section_id", "chunk_index"],
    )

    op.execute(
        """
        DO $$
        DECLARE
            vec_schema text;
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
                CREATE EXTENSION IF NOT EXISTS vector;
                SELECT n.nspname INTO vec_schema
                FROM pg_extension e
                JOIN pg_namespace n ON n.oid = e.extnamespace
                WHERE e.extname = 'vector';
                EXECUTE format(
                    'ALTER TABLE knowledge_index_chunks ADD COLUMN embedding %I.vector(384)',
                    vec_schema
                );
                EXECUTE format(
                    'CREATE INDEX ix_kic_embedding_hnsw ON knowledge_index_chunks USING hnsw (embedding %I.vector_cosine_ops) WITH (m = 16, ef_construction = 64)',
                    vec_schema
                );
            ELSE
                EXECUTE 'ALTER TABLE knowledge_index_chunks ADD COLUMN embedding double precision[]';
            END IF;
        END
        $$;
        """
    )

    op.execute("ALTER TABLE knowledge_index_chunks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_index_chunks FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON knowledge_index_chunks USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY tenant_isolation ON knowledge_index_chunks")
    op.execute("ALTER TABLE knowledge_index_chunks NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_index_chunks DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_kic_section_lookup", table_name="knowledge_index_chunks")
    op.drop_index("ix_kic_scope_fp", table_name="knowledge_index_chunks")
    op.drop_index("ix_kic_active_lookup", table_name="knowledge_index_chunks")
    op.execute("DROP INDEX IF EXISTS ix_kic_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_kic_search_tsv")
    op.drop_table("knowledge_index_chunks")

    op.drop_constraint("ck_workflow_jobs_op", "workflow_jobs", type_="check")
    op.create_check_constraint(
        "ck_workflow_jobs_op",
        "workflow_jobs",
        "operation_kind IN ('source_extract', 'concept_extract')",
    )
