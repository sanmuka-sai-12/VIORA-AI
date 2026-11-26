"""Versioned educational source ownership, immutable versions, policy, and RLS."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_source_ingestion"
down_revision = "0007_actor_institution_discovery"
branch_labels = None
depends_on = None

PREDICATE = "tenant_id = NULLIF(current_setting('viora.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.add_column("sources", sa.Column("offering_id", sa.Uuid(), nullable=True))
    op.add_column("sources", sa.Column("creator_user_id", sa.Uuid(), nullable=True))
    op.add_column("sources", sa.Column("owner_user_id", sa.Uuid(), nullable=True))
    op.add_column(
        "sources",
        sa.Column(
            "origin_kind", sa.Text(), server_default="unscoped", nullable=False
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "visibility", sa.Text(), server_default="restricted", nullable=False
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "lifecycle_state", sa.Text(), server_default="active", nullable=False
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "latest_version_number", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column("sources", sa.Column("latest_version_id", sa.Uuid(), nullable=True))
    op.add_column("sources", sa.Column("active_dedupe_key", sa.Text(), nullable=True))
    op.add_column(
        "sources",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_check_constraint("ck_sources_title", "sources", "btrim(title) <> ''")
    op.create_check_constraint(
        "ck_sources_origin_kind",
        "sources",
        "origin_kind IN ('teacher_resource', 'student_submission', 'unscoped')",
    )
    op.create_check_constraint(
        "ck_sources_visibility",
        "sources",
        "visibility IN ('restricted', 'teacher_only', 'offering_published', 'owner_private', 'teacher_review')",
    )
    op.create_check_constraint(
        "ck_sources_lifecycle_state",
        "sources",
        "lifecycle_state IN ('active', 'archived')",
    )
    op.create_check_constraint(
        "ck_sources_origin_visibility_integrity",
        "sources",
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
    )
    op.create_check_constraint(
        "ck_sources_latest_version_number",
        "sources",
        "latest_version_number >= 0",
    )
    op.create_foreign_key(
        "fk_sources_offering",
        "sources",
        "subject_offerings",
        ["tenant_id", "offering_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_sources_creator",
        "sources",
        "memberships",
        ["tenant_id", "creator_user_id"],
        ["tenant_id", "user_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_sources_owner",
        "sources",
        "memberships",
        ["tenant_id", "owner_user_id"],
        ["tenant_id", "user_id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_sources_active_dedupe",
        "sources",
        ["tenant_id", "active_dedupe_key"],
    )
    op.create_index(
        "ix_sources_offering",
        "sources",
        ["tenant_id", "offering_id", "origin_kind", "lifecycle_state", "created_at"],
    )
    op.create_index(
        "ix_sources_owner",
        "sources",
        ["tenant_id", "owner_user_id", "offering_id"],
    )

    op.create_table(
        "offering_source_policies",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("offering_id", sa.Uuid(), nullable=False),
        sa.Column(
            "student_submissions_enabled",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
        sa.Column(
            "teachers_can_view_student_submissions",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "offering_id"],
            ["subject_offerings.tenant_id", "subject_offerings.id"],
            name="fk_offering_source_policy_offering",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_offering_source_policy_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "offering_id"),
    )

    op.create_table(
        "source_versions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("input_format", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_kind", sa.Text(), nullable=False),
        sa.Column("storage_resource_id", sa.Uuid(), nullable=False),
        sa.Column("storage_version_id", sa.Uuid(), nullable=False),
        sa.Column("storage_sha256", sa.Text(), nullable=False),
        sa.Column("processing_status", sa.Text(), nullable=False),
        sa.Column("extractor_id", sa.Text(), nullable=False),
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
            "extracted_document",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_source_versions_number"),
        sa.CheckConstraint(
            "input_format IN ('plain_text', 'typed_notes', 'topic_description', 'digital_pdf')",
            name="ck_source_versions_format",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_source_versions_sha256",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_source_versions_size"),
        sa.CheckConstraint(
            "storage_sha256 = content_sha256",
            name="ck_source_versions_storage_sha",
        ),
        sa.CheckConstraint(
            "processing_status IN ('extracted', 'partial_extraction')",
            name="ck_source_versions_status",
        ),
        sa.CheckConstraint("extraction_ms >= 0", name="ck_source_versions_ms"),
        sa.CheckConstraint("char_count >= 0", name="ck_source_versions_chars"),
        sa.CheckConstraint("page_count >= 1", name="ck_source_versions_pages"),
        sa.CheckConstraint("warning_count >= 0", name="ck_source_versions_warnings"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_source_versions_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_source_versions_creator",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "version_id"),
        sa.UniqueConstraint(
            "tenant_id", "source_id", "version_number", name="uq_source_versions_number"
        ),
        sa.UniqueConstraint(
            "tenant_id", "source_id", "content_sha256", name="uq_source_versions_digest"
        ),
    )
    op.create_index(
        "ix_source_versions_source_num",
        "source_versions",
        ["tenant_id", "source_id", "version_number"],
    )

    op.create_table(
        "source_idempotency",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("offering_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("target_scope_id", sa.Uuid(), nullable=False),
        sa.Column("operation_kind", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_digest", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "operation_kind IN ('create_source', 'create_version')",
            name="ck_source_idemp_op",
        ),
        sa.CheckConstraint(
            "btrim(idempotency_key) <> '' AND length(idempotency_key) <= 128",
            name="ck_source_idemp_key",
        ),
        sa.CheckConstraint(
            "request_digest ~ '^[0-9a-f]{64}$'",
            name="ck_source_idemp_digest",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "offering_id"],
            ["subject_offerings.tenant_id", "subject_offerings.id"],
            name="fk_source_idemp_offering",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_source_idemp_actor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.source_id"],
            name="fk_source_idemp_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["source_versions.tenant_id", "source_versions.version_id"],
            name="fk_source_idemp_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "offering_id",
            "actor_user_id",
            "target_scope_id",
            "operation_kind",
            "idempotency_key",
        ),
    )

    for table in ("offering_source_policies", "source_idempotency"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
        )

    op.execute("ALTER TABLE source_versions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE source_versions FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY source_versions_read ON source_versions FOR SELECT USING ({PREDICATE})"
    )
    op.execute(
        f"CREATE POLICY source_versions_append ON source_versions FOR INSERT WITH CHECK ({PREDICATE})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY source_versions_append ON source_versions")
    op.execute("DROP POLICY source_versions_read ON source_versions")
    op.execute("ALTER TABLE source_versions NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE source_versions DISABLE ROW LEVEL SECURITY")
    for table in ("source_idempotency", "offering_source_policies"):
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("source_idempotency")
    op.drop_index("ix_source_versions_source_num", table_name="source_versions")
    op.drop_table("source_versions")
    op.drop_table("offering_source_policies")

    op.drop_index("ix_sources_owner", table_name="sources")
    op.drop_index("ix_sources_offering", table_name="sources")
    op.drop_constraint("uq_sources_active_dedupe", "sources", type_="unique")
    op.drop_constraint("fk_sources_owner", "sources", type_="foreignkey")
    op.drop_constraint("fk_sources_creator", "sources", type_="foreignkey")
    op.drop_constraint("fk_sources_offering", "sources", type_="foreignkey")
    op.drop_constraint("ck_sources_latest_version_number", "sources", type_="check")
    op.drop_constraint("ck_sources_origin_visibility_integrity", "sources", type_="check")
    op.drop_constraint("ck_sources_lifecycle_state", "sources", type_="check")
    op.drop_constraint("ck_sources_visibility", "sources", type_="check")
    op.drop_constraint("ck_sources_origin_kind", "sources", type_="check")
    op.drop_constraint("ck_sources_title", "sources", type_="check")
    for column in (
        "updated_at",
        "created_at",
        "active_dedupe_key",
        "latest_version_id",
        "latest_version_number",
        "lifecycle_state",
        "visibility",
        "origin_kind",
        "owner_user_id",
        "creator_user_id",
        "offering_id",
    ):
        op.drop_column("sources", column)
