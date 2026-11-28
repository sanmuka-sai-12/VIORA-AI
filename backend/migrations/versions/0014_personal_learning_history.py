"""Persistent student-private Personal Learning sessions, history, and library."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_personal_learning_history"
down_revision = "0013_lesson_blueprints"
branch_labels = None
depends_on = None

STUDENT_PRIVATE_PREDICATE = (
    "tenant_id = NULLIF(current_setting('viora.tenant_id', true), '')::uuid "
    "AND owner_user_id = NULLIF(current_setting('viora.actor_id', true), '')::uuid"
)

TABLES = (
    "personal_learning_materials",
    "personal_learning_sessions",
    "personal_learning_messages",
)


def upgrade() -> None:
    op.create_table(
        "personal_learning_materials",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("attachment_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("input_format", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("status_message", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("preview_excerpt", sa.Text(), nullable=True),
        sa.Column("doc_sha256", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('pdf', 'image', 'text')", name="ck_pl_materials_kind"),
        sa.CheckConstraint(
            "status IN ('ready', 'partial', 'unreadable')",
            name="ck_pl_materials_status",
        ),
        sa.CheckConstraint(
            "doc_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_pl_materials_doc_sha",
        ),
        sa.CheckConstraint(
            "page_count >= 0 AND chunk_count >= 0 AND char_count >= 0",
            name="ck_pl_materials_counts",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.user_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "attachment_id"),
    )
    op.create_index(
        "ix_pl_materials_owner_recent",
        "personal_learning_materials",
        ["tenant_id", "owner_user_id", "created_at"],
    )

    op.create_table(
        "personal_learning_sessions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), server_default="question", nullable=False),
        sa.Column(
            "active_attachment_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('question', 'explain')", name="ck_pl_sessions_mode"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.user_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("tenant_id", "session_id"),
    )
    op.create_index(
        "ix_pl_sessions_owner_recent",
        "personal_learning_sessions",
        ["tenant_id", "owner_user_id", "updated_at"],
    )

    op.create_table(
        "personal_learning_messages",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), server_default="question", nullable=False),
        sa.Column(
            "attached_titles",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "sources",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "follow_up_prompts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("truncated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_pl_messages_role"),
        sa.CheckConstraint("mode IN ('question', 'explain')", name="ck_pl_messages_mode"),
        sa.CheckConstraint("sequence_no >= 1", name="ck_pl_messages_seq"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["institutions.tenant_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.user_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["personal_learning_sessions.tenant_id", "personal_learning_sessions.session_id"],
            name="fk_pl_messages_session",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "message_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "session_id",
            "sequence_no",
            name="uq_pl_messages_session_seq",
        ),
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY student_private_isolation ON {table} "
            f"USING ({STUDENT_PRIVATE_PREDICATE}) WITH CHECK ({STUDENT_PRIVATE_PREDICATE})"
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY student_private_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("personal_learning_messages")
    op.drop_index("ix_pl_sessions_owner_recent", table_name="personal_learning_sessions")
    op.drop_table("personal_learning_sessions")
    op.drop_index("ix_pl_materials_owner_recent", table_name="personal_learning_materials")
    op.drop_table("personal_learning_materials")
